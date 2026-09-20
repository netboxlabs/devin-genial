"""Compose one customer demo: recipe, artifact, feature packs and a cheat sheet.

Twenty minutes before a call an SE knows three things — the industry, the
vendor in the racks, and which product story is being sold. This turns those
three into a loaded, verified branch and a DEMO.md to read from.

It orchestrates and never reimplements. Every step is the same entry point the
Justfile recipe invokes (`estates.__main__.main`, `estates.load.main`,
`estates.branch.main`), called in-process with the exact argv that recipe would
build, so generation, validation, the TurboBulk contract check, the load
preflight, the strict readback and every receipt behave identically whether an
operator typed `just …` or `just demo`. No gate is skipped, softened or
duplicated here: a non-zero exit from any of them stops the compose.

Offline by default. Target writes happen only when `--target` and `--branch`
are both supplied, and then only through the loader's own gated path.
"""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from importlib import import_module
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import tomllib

from . import __version__
from .model import (DesignError, resolve_hardware as model_resolve_hardware,
                    resolve_recipe as model_resolve_recipe)
from .report import _cell, _table


# Same DNS-label rule the recipe resolver enforces (estates/model.py); checked
# here so a derived namespace fails at the flag, not after template assembly.
_NAMESPACE = re.compile(r"[a-z][a-z0-9-]{0,18}[a-z0-9]")

FEATURES = ("assurance", "automation", "scenario")

# `[hardware]` families, not vendors-in-general: the recipe key selects a line
# for exactly `access`, `leaf` and `ap`. "juniper" has no AP line, "aruba" has
# no switch line, so each shorthand moves the families it actually covers.
VENDORS = {
    "default": ({}, "catalog defaults (Cisco access, Arista leaf, reference AP)"),
    "juniper": ({"access": "juniper", "leaf": "juniper"},
                "Juniper EX3400-24P access and QFX5120-48Y-AFO2 leaf; the AP stays the reference line"),
    "aruba": ({"ap": "aruba"},
              "HPE Aruba AP-505 radios; access and leaf stay on the catalog defaults"),
}

# Site-name pools, DNS names and device names all key off the namespace, so a
# namespace-derived seed makes two composes for one customer agree without the
# operator having to remember a number. Kept inside 0 <= seed < 2**63.
def seed_for(namespace):
    return int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], "big") >> 1


def namespace_for(name):
    """Derive the DNS-label namespace from a customer name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"^[^a-z]+", "", slug)[:20].rstrip("-")
    if not _NAMESPACE.fullmatch(slug):
        raise DesignError(
            f"cannot derive a namespace from name {name!r}: a namespace is a 2–20 character "
            "DNS label (lowercase letters, digits or hyphens, starting with a letter and "
            "ending with a letter or digit). Pass --namespace explicitly.")
    return slug


# The drift twin selects an access switch and its endpoint ports, so it needs a
# profile that models campus access. The enterprise data center models fabric
# only, and estates/drift.py refuses it by name.
NO_DRIFT = ("enterprise-data-center",)


# One authored demand template per profile. Moderate on purpose: believable
# enough to walk for twenty minutes, small enough to load in minutes. Measured
# sizes are in docs/demo.md; seed and vendor line move them by dozens, not
# thousands.
class _Template:
    def __init__(self, label, size, hook, body, step):
        self.label, self.size = label, size
        self.hook, self.body, self.step = hook, body, step


PROFILES = {
    "regional-bank": _Template(
        "Regional bank",
        "8 sites: two data centers, a headquarters and five branches (three modern, "
        "one inherited from an acquisition, one refreshed)",
        "Two data centers carry the shared services; five branches consume them, and one "
        "of those branches still runs the acquired bank's equipment under its old brand.",
        """\
headquarters = 1
headquarters_staff = 48
data_centers = 2

# Branch mix. Sizes carry fixed authored demand: small 12 workstations / 2 ATMs,
# medium 36 / 4, large 84 / 6. Counts may grow; sizes may not change.
[branches]
small = 3
medium = 1
large = 1

# The weighted pool for branches added later; the explicit assignments below
# guarantee all three designs are on screen for this demo.
[design_mix]
modern = 60
inherited = 25
refreshed = 15

[site_designs]
br-s0001 = "modern"
br-s0002 = "inherited"
br-s0003 = "refreshed"
""",
        ("**Show the merger.** The inherited branch {inherited} renders under the authored "
         "predecessor brand *Birch* — device names, DNS, VRFs, route targets, its own tenant "
         "and WAN accounts. Filter devices by that site and read the names. That lineage is "
         "the acquisition story's substance, and it survives an equipment refresh.")),

    "enterprise-data-center": _Template(
        "Enterprise data center",
        "2 data centers carrying four workloads (36 VMs, rack-diverse replicas)",
        "Every workload replica is placed on an explicit host and rack lane, so 'are these two "
        "replicas actually independent?' has an answer you can trace rather than assert.",
        """\
data_centers = 2
wan_peak_mbps = 1200

# Each workload is a named application shard set. Rename these to the
# customer's own applications before the call: the key drives VM names, DNS,
# listener names and the service records, so it is the cheapest realism there is.
[[workloads]]
key = "payments"
groups = 3
replicas = 2
failure_domain = "rack"
network = "applications"
criticality = "tier-1"

[[workloads]]
key = "ledger-db"
groups = 2
replicas = 2
failure_domain = "rack"
network = "database"
criticality = "tier-1"

[[workloads]]
key = "reporting"
groups = 3
replicas = 2
network = "applications"

[[workloads]]
key = "backup"
groups = 1
replicas = 2
network = "backup"
""",
        ("**Prove the failure domain.** Open two replicas of the same `payments` group and "
         "compare their cluster, host and rack. `failure_domain = \"rack\"` also separates the "
         "paired fabric and WAN devices they depend on. The reserve is exact decimal headroom, "
         "not a promise that a spare host exists to recover onto.")),

    "school-district": _Template(
        "School district",
        "3 sites: one district data center and two campuses (20 classrooms, a computer lab, "
        "two admin pods)",
        "Every classroom has persistent endpoint ports in a named switch pair, so adding "
        "classrooms next year never reroutes a seat that already exists.",
        """\
[[schools]]
key = "oak"
classrooms = 12
administrative_staff = 12
lab_seats = 24

[[schools]]
key = "ridge"
classrooms = 8
administrative_staff = 12
""",
        ("**Open one classroom.** Pick a `classroom-0NN` room, then its serving access pair. "
         "The wired student seats, the teaching device and the AP mount all resolve to "
         "permanent ports. AP channels come from stable per-AP keys, so growth never rerolls "
         "them — there is no RF survey or association evidence behind any of it.")),

    "hospital-clinics": _Template(
        "Hospital and clinics",
        "4 sites: one data center, a hospital with three wards and two imaging rooms, and two clinics",
        "Wards, imaging and clinical desks keep distinct roles and segments, and the biomedical "
        "responsibility is its own site-scoped contact rather than a note in a description.",
        """\
[[hospitals]]
key = "central"
administrative_desks = 12
imaging_rooms = 2

[[hospitals.wards]]
key = "medical"

[[hospitals.wards]]
key = "surgical"

[[hospitals.wards]]
key = "intensive-care"

[[clinics]]
key = "west"
exam_rooms = 6

[[clinics]]
key = "north"
exam_rooms = 4
""",
        ("**Follow the biomedical split.** Imaging and clinical equipment carry distinct roles "
         "and segments, and a separate biomedical contact from the network desk. Beds and desks "
         "are installed capacity — never patient throughput or staffing.{guest}")),

    "provider-backbone": _Template(
        "Provider backbone",
        "9 sites: three PoPs across three metros plus five customer premises for two accounts",
        "Every customer premises reaches its hub over a real two-site circuit through finite "
        "physical PoP ports — not a cloud drawn as a cloud.",
        """\
[[pops]]
key = "chicago-west"
metro = "chicago"

[[pops]]
key = "detroit-east"
metro = "detroit"

[[pops]]
key = "cleveland-central"
metro = "cleveland"

[[customers]]
key = "harbor-logistics"
hub_pop = "chicago-west"

[[customers.sites]]
pop = "chicago-west"

[[customers.sites]]
pop = "detroit-east"

[[customers.sites]]
pop = "cleveland-central"

[[customers]]
key = "granite-foods"
hub_pop = "detroit-east"

[[customers.sites]]
pop = "detroit-east"

[[customers.sites]]
pop = "cleveland-central"
""",
        ("**Trace one customer to its hub.** Start at a `ce-harbor-logistics-…` premises, follow "
         "its access circuit into the PoP, through the PE to the hub. Offered kbps is a finite "
         "declared flow model per spoke, not total backbone traffic, and span providers are shown "
         "per PoP without any promise of carrier diversity. [The profile guide]"
         "(@ROOT@/profiles/provider-backbone.md#planned-span-maintenance) has the "
         "planned-span-maintenance scenario if you want an outage story.")),

    "retail-chain": _Template(
        "Retail chain",
        "10 sites: two commerce data centers, a support centre, a distribution centre and six stores "
        "across three formats",
        "Store formats repeat — the same lane, radio and camera grammar at every one — so the "
        "fleet question 'what does a medium store look like' has exactly one answer.",
        """\
headquarters = 1
headquarters_staff = 36
distribution_centers = 1

# Formats carry fixed authored demand: small 2 workstations / 4 lanes, medium
# 4 / 8, large 8 / 16, distribution centre 12 / 24 scanners.
[stores]
small = 3
medium = 2
large = 1
""",
        ("**Compare two formats side by side.** Open a `st-s…` store and a `st-l…` store and read "
         "the same segment list at different sizes: `pos`, `backoffice`, `wireless`, `security`, "
         "`guest`. Lanes are installed capacity, never transaction volume.")),

    "university-campus": _Template(
        "University campus",
        "5 sites in one metro: a campus data center, two academic buildings, a residence hall and "
        "the library",
        "One campus is one estate — buildings, the hall and the library are separate sites inside "
        "a single authored metro, all served by one campus data center.",
        """\
# Frozen during growth while appended buildings raise the required peak, so it
# is sized with headroom rather than to today's exact demand.
wan_peak_mbps = 4000

[[buildings]]
key = "science"
classrooms = 8
lab_seats = 48
offices = 24

[[buildings]]
key = "humanities"
classrooms = 6
lab_seats = 0
offices = 24

[[residences]]
key = "aspen"
rooms = 100

[library]
reading_seats = 120
aps = 6
""",
        ("**Open a residence hall floor.** Every room holds a permanent reserved position and its "
         "wired port is *installed capacity* — no resident-owned device is modeled. Identity and "
         "WLAN records use eduroam-style naming only; no authentication protocol is configured "
         "anywhere, so do not let the room infer 802.1X from an SSID name.")),

    "msp": _Template(
        "Managed service provider",
        "6 sites: one NOC and five managed customer offices across three accounts",
        "Each account is its own NetBox tenant owning its own sites, addresses and VLANs; the "
        "provider appears only as the operator. Nothing joins two customers.",
        """\
[[customers]]
key = "summit-legal"
offices = 2
staff = 24

[[customers]]
key = "harbor-dental"
offices = 2
staff = 12

[[customers]]
key = "brightline-media"
offices = 1
staff = 36
""",
        ("**Separate ownership from operation.** The API proofs below are the point of this "
         "profile — see [Ownership versus operation](@ROOT@/profiles/msp.md#ownership-versus-operation). "
         "There is no modeled path of any kind between the NOC and a customer office: managed is "
         "expressed by contacts, ownership and the local management segment, never by reachability.")),

    "manufacturing": _Template(
        "Manufacturing",
        "4 sites: two corporate data centers and two plants with separated plant-floor (OT) and "
        "corporate (IT) zones",
        "The plant floor has its own segments, routing contexts, access pair and distribution "
        "pair; exactly one modeled forwarding path — the conduit — reaches the corporate tier.",
        """\
[[plants]]
key = "riverbend"
production_lines = 6
warehouse_docks = 6
office_staff = 48

[[plants]]
key = "kestrel-forge"
production_lines = 4
warehouse_docks = 4
office_staff = 24
""",
        ("**Walk the three crossings.** See [The IT/OT boundary, precisely]"
         "(@ROOT@/profiles/manufacturing.md#the-itot-boundary-precisely). Say the caveats out "
         "loud: the separation is *modeled*, never enforced — no firewall rule, ACL, route filter "
         "or data diode exists; both distribution tiers share one equipment room; no Purdue level, "
         "IEC 62443 state or industrial protocol is configured or claimed.")),

    "utility": _Template(
        "Electric utility",
        "4 sites: one control center and three substations (24 switchyard bays total)",
        "A substation control house carries a station (OT) zone and a minimal corporate tier, "
        "joined by one modeled conduit — the same zone grammar as the plant, in a different industry.",
        """\
control_centers = 1

[[substations]]
key = "oakridge"
kind = "transmission"
bays = 10

[[substations]]
key = "milldam"
kind = "transmission"
bays = 8

[[substations]]
key = "fairhaven"
kind = "distribution"
bays = 6
""",
        ("**Walk the three crossings, then read the boundary list aloud.** See [The "
         "station/corporate boundary, precisely]"
         "(@ROOT@/profiles/utility.md#the-stationcorporate-boundary-precisely). Nothing here "
         "supports a NERC CIP, electronic security perimeter, SCADA/EMS, protection-setting or "
         "grid-topology claim; bays are installed equipment positions, not voltage classes. No "
         "service listens on 102, 502, 2404 or 20000, and a test asserts it.")),
}


# --------------------------------------------------------------------------
# Flags


def _vendor_note(resolved_hardware):
    """Describe a recipe's [hardware] selection the way the flag shorthands do."""
    defaults = model_resolve_hardware({})
    moved = {family: line for family, line in sorted(resolved_hardware.items())
             if line != defaults.get(family)}
    if not moved:
        return VENDORS["default"][1]
    return "from the recipe's [hardware] table: " + ", ".join(
        f"{family} = {line}" for family, line in moved.items())


def resolve(*, profile, vendor, name, namespace, seed, features, sites, out, target, branch,
            recipe=None):
    """Validate the flags and return the frozen compose specification."""
    if recipe is not None:
        # The customer's real shape: everything identity- and demand-shaped
        # comes from the recipe file; only features/out/target/branch are flags.
        shadowed = [flag for flag, moved in (
            ("--profile", profile != "regional-bank"), ("--vendor", vendor != "default"),
            ("--name", name != "Genial Demo Estate"), ("--namespace", bool(namespace)),
            ("--seed", seed is not None), ("--sites", sites is not None)) if moved]
        if shadowed:
            raise DesignError(f"--recipe supplies the estate's identity and shape itself; "
                              f"drop {', '.join(shadowed)} (edit the recipe instead)")
        try:
            source = Path(recipe).read_text()
            resolved = model_resolve_recipe(tomllib.loads(source))
        except OSError as exc:
            raise DesignError(f"--recipe {recipe}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise DesignError(f"--recipe {recipe} is not valid TOML: {exc}") from exc
        spec = resolve(profile=resolved["profile"], vendor="default", name=resolved["name"],
                       namespace=resolved["namespace"], seed=resolved["seed"], features=features,
                       sites=None, out=out, target=target, branch=branch)
        return spec | {"seed_source": "from the recipe", "recipe_path": str(recipe),
                       "recipe_source": source, "site_names": resolved.get("site_names", {}),
                       "vendor_note": _vendor_note(resolved.get("hardware", {}))}
    if profile not in PROFILES:
        raise DesignError(f"unknown --profile {profile!r}; choose one of: " + ", ".join(PROFILES))
    if vendor not in VENDORS:
        raise DesignError(f"unknown --vendor {vendor!r}; choose one of: " + ", ".join(VENDORS))
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        raise DesignError("--name must be 1–80 characters of customer-visible text")
    # A newline here would open a new TOML line in the recipe's header comment
    # and silently set any key the caller pleases.
    if not name.isprintable():
        raise DesignError("--name must not contain control characters; it is written into the "
                          "generated recipe and shown to the customer")
    namespace = namespace or namespace_for(name)
    if not _NAMESPACE.fullmatch(namespace):
        raise DesignError(
            f"--namespace {namespace!r} must be a 2–20 character DNS label: lowercase letters, "
            "digits or hyphens, starting with a letter and ending with a letter or digit")
    chosen = [item.strip() for item in (features or "").split(",") if item.strip()]
    unknown = [item for item in chosen if item not in FEATURES]
    if unknown:
        raise DesignError(f"unknown --features {', '.join(unknown)}; choose from: " + ", ".join(FEATURES))
    if "assurance" in chosen and profile in NO_DRIFT:
        raise DesignError(
            f"--features assurance needs a profile that models campus access; {profile} models "
            "fabric only, so the drift twin has no access switch or endpoint ports to drift. "
            "Drop assurance, or choose one of: "
            + ", ".join(other for other in PROFILES if other not in NO_DRIFT))
    if seed is None:
        seed, seed_source = seed_for(namespace), "derived from the namespace"
    elif not 0 <= seed < 2 ** 63:
        raise DesignError("--seed must be an integer 0 <= seed < 2**63")
    else:
        seed_source = "supplied"
    if bool(target) != bool(branch):
        raise DesignError("--target and --branch go together: supply both to go live in this run, "
                          "or neither to stop after the offline gates and print the go-live commands")
    if target and (target.rstrip("/").endswith("/api") or "?" in target or "#" in target):
        raise DesignError("--target is the NetBox root URL, without /api/, a plugin path, "
                          "credentials, query parameters or a fragment")
    # Checked here, not several minutes of generation later: the loader would
    # otherwise refuse at its own argument parser after the estate is built.
    if target and not os.environ.get("NETBOX_TOKEN"):
        raise DesignError("NETBOX_TOKEN is required to go live. Export it (with "
                          "TURBOBULK_WRITES=1) or put both in .env, which `just demo` sources "
                          "when the token is not already exported. Drop --target/--branch to "
                          "compose offline and print the go-live commands instead.")
    out = out or f"build/demos/{namespace}"
    return {"profile": profile, "vendor": vendor, "name": name, "namespace": namespace,
            "seed": seed, "seed_source": seed_source,
            "features": sorted(set(chosen), key=FEATURES.index),
            "site_names": _site_overrides(sites), "sites_file": str(sites) if sites else None,
            "out": str(out).rstrip("/") or ".", "target": target or None, "branch": branch or None,
            "recipe_path": None, "vendor_note": VENDORS[vendor][1],
            "generator_version": __version__}


def _site_overrides(path):
    """Read a TOML or JSON file of per-site display-name overrides."""
    if not path:
        return {}
    path = Path(path)
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode()) if path.suffix.lower() == ".toml" else json.loads(raw)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DesignError(f"--sites {path}: not readable as "
                          f"{'TOML' if path.suffix.lower() == '.toml' else 'JSON'} ({exc})") from None
    # Accept either the bare mapping or a file that wraps it in [site_names],
    # so an operator can lift the block straight out of a recipe.
    if isinstance(data, dict) and set(data) == {"site_names"}:
        data = data["site_names"]
    if not isinstance(data, dict) or not data:
        raise DesignError(f"--sites {path}: expected a non-empty table of site id -> "
                          '{name = "…", facility = "…"}')
    overrides = {}
    for site, value in data.items():
        if not isinstance(value, dict) or not value or set(value) - {"name", "facility"}:
            raise DesignError(f"--sites {path}: site {site!r} must map to a non-empty table with "
                              "only 'name' and/or 'facility'")
        for field, text in value.items():
            if not isinstance(text, str) or not text.strip():
                raise DesignError(f"--sites {path}: site {site!r} {field} must be non-empty text")
        overrides[str(site)] = dict(sorted(value.items()))
    # Unknown site ids are generation's business: it fails with the real list.
    return dict(sorted(overrides.items()))


# --------------------------------------------------------------------------
# The recipe


def _toml(value):
    """Encode one string as a TOML basic string.

    JSON string escaping is a subset of TOML's, minus `\\/` which json never
    emits, so json.dumps output is always a valid TOML basic string.
    """
    return json.dumps(value)


def recipe_text(spec):
    """Render the deterministic demo recipe; identical inputs give identical bytes."""
    if spec.get("recipe_path"):
        # The customer's own recipe, copied verbatim: the composer adds nothing.
        return spec["recipe_source"]
    template = PROFILES[spec["profile"]]
    lines = [f"# {spec['name']} — {template.label} demo estate, composed by "
             f"`python3 -m estates demo` (Genial {spec['generator_version']}).",
             f"# Size: {template.size}.",
             "#",
             "# An ordinary recipe. Edit it and re-generate; grow it with a previous plan",
             "# (`just generate RECIPE build/next build/this/plan.json`). Keys are documented",
             "# in docs/recipes.md — `namespace`, `name`, `seed` and the vendor line are all",
             "# rebaseline-frozen, so change them only into a fresh output and a fresh branch.",
             f"profile = {_toml(spec['profile'])}",
             f"name = {_toml(spec['name'])}",
             f"namespace = {_toml(spec['namespace'])}",
             f"seed = {spec['seed']}",
             ""]
    lines.extend(template.body.rstrip("\n").split("\n"))
    hardware, _ = VENDORS[spec["vendor"]]
    if hardware:
        lines.extend(["", "# Vendor line per role family. Placed after every top-level scalar:",
                      "# a TOML table header otherwise swallows them.", "[hardware]"])
        lines.extend(f"{family} = {_toml(line)}" for family, line in sorted(hardware.items()))
    if spec["site_names"]:
        lines.extend(["", "# The customer's real site names. Appending entries for new sites is",
                      "# growth; changing or removing an existing entry is a rebaseline."])
        for site, override in spec["site_names"].items():
            lines.append(f"[site_names.{_toml(site)}]")
            lines.extend(f"{field} = {_toml(text)}" for field, text in override.items())
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Running the existing commands


class _Runner:
    """Run Justfile-equivalent entry points in-process, timing each one."""

    def __init__(self, cli, stream):
        self._cli, self._stream = cli, stream
        self.steps = []

    def estates(self, label, argv):
        """One `python3 -m estates …` subcommand, in --json mode."""
        argv = ["--json", *map(str, argv)]
        return self._run(label, ["python3", "-m", "estates", *argv], self._cli, argv)

    def module(self, label, module, argv):
        """One `python3 -m estates.load|branch …` entry point.

        Imported at call time: `estates.load` pulls in the whole TurboBulk
        compiler, which an offline compose without a target never needs.
        """
        entry = import_module(f".{module}", __package__).main
        argv = [str(item) for item in argv]
        return self._run(label, ["python3", "-m", f"estates.{module}", *argv], entry, argv)

    def _run(self, label, shown, entry, argv):
        command = " ".join(shown)
        print(f"demo: {label}", file=self._stream, flush=True)
        captured, problems = io.StringIO(), _Tee(self._stream)
        started = time.monotonic()
        try:
            # stdout is the result; stderr is loader progress the operator wants
            # to watch live *and* the failure text several entry points print
            # there instead of stdout, so it is teed rather than swallowed.
            with redirect_stdout(captured), redirect_stderr(problems):
                try:
                    status = entry(argv)
                except SystemExit as exit:
                    # argparse in a nested entry point: keep the --json failure
                    # contract instead of letting the interpreter unwind.
                    status = exit.code if isinstance(exit.code, int) else 2
        finally:
            self.steps.append({"step": label, "command": command,
                               "seconds": round(time.monotonic() - started, 3)})
        text = captured.getvalue()
        if status != 0:
            raise DesignError(f"{label} failed (exit {status}): "
                              f"{_reason(text, problems.text.getvalue())}\n  command: {command}")
        payload = _payload(text)
        if payload is None:
            raise DesignError(f"{label} returned no JSON result, so the cheat sheet cannot be "
                              f"assembled from it\n  command: {command}\n  output: {text.strip()!r}")
        return payload


class _Tee(io.TextIOBase):
    """Pass writes through to the operator's stream while recording them."""

    def __init__(self, stream):
        self._stream, self.text = stream, io.StringIO()

    def write(self, text):
        self.text.write(text)
        return self._stream.write(text)

    def flush(self):
        self._stream.flush()


def _payload(text):
    """Parse a captured entry point's JSON, indented document or last line."""
    text = text.strip()
    for candidate in ([text] + text.splitlines()[-1:]) if text else []:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _reason(text, errors=""):
    """Pull the underlying message out of a captured failure, wherever it went."""
    payload = _payload(text) or {}
    tail = errors.strip().splitlines()
    return (payload.get("message") or payload.get("verdict") or (tail[-1] if tail else "")
            or text.strip() or "see the message above")


# --------------------------------------------------------------------------
# Real identities out of the finished graph


def _facts(plan):
    """Pull the concrete identities DEMO.md quotes, in permanent key order."""
    objects = sorted(plan["objects"], key=lambda obj: obj["key"])
    index = {obj["key"]: obj for obj in objects}
    counts = Counter(obj["kind"] for obj in objects)
    kinds = {}
    for obj in objects:
        kinds.setdefault(obj["kind"], []).append(obj)
    sites = kinds.get("site", [])
    # The headline site is the first non-data-center one: a branch, plant or
    # campus shows the access/endpoint grammar a data center does not. The MSP's
    # only data-center site is its NOC (docs/recipes.md#common-keys).
    headline = next((site for site in sites
                     if not site["key"].startswith(("site/dc-", "site/noc-"))), sites[0])
    at_site = [obj for obj in kinds.get("device", [])
               if obj["refs"].get("site") == headline["key"]]
    # The trace step wants a switch, not whichever device sorts first: an
    # access port has copper, power and an endpoint at the other end. The
    # enterprise data center has no access layer, so fall back to a leaf.
    subject = next((obj for role in ("role/access", "role/leaf", "role/distribution")
                    for obj in at_site if obj["refs"].get("role") == role),
                   at_site[0] if at_site else None)
    # Whoever is actually assigned to *that* device. Several profiles escalate
    # to a per-account or per-lineage desk, not the shared operations one.
    desks = [index[obj["refs"]["contact"]]["attrs"]["name"]
             for obj in kinds.get("contact_assignment", [])
             if subject is not None and obj["refs"].get("object") == subject["key"]
             and obj["refs"].get("contact") in index]
    rack = next((obj for obj in kinds.get("rack", [])
                 if obj["refs"].get("site") == headline["key"]), None)
    service = next(iter(kinds.get("service", [])), None)
    listener = None
    if service is not None:
        machine = index.get(service["refs"].get("virtual_machine") or "")
        address = next((index[key] for key in service["refs"].get("ipaddresses", [])
                        if key in index), None)
        listener = {"name": service["attrs"]["name"],
                    "protocol": service["attrs"]["protocol"].upper(),
                    "ports": "/".join(str(port) for port in service["attrs"]["ports"]),
                    "host": machine["attrs"]["name"] if machine else "its host",
                    "address": address["attrs"]["address"] if address else None}
    field = next(iter(kinds.get("custom_field", [])), None)
    link = next(iter(kinds.get("custom_link", [])), None)
    return {
        "counts": counts,
        "objects": len(objects),
        "site": {"name": headline["attrs"]["name"], "slug": headline["attrs"]["slug"],
                 "id": headline["key"].removeprefix("site/"),
                 "facility": headline["attrs"].get("facility")},
        "device": subject["attrs"]["name"] if subject else None,
        "devices_at_site": len(at_site),
        "racked_at_site": len([obj for obj in at_site if obj["refs"].get("rack")]),
        # The merger step is a graph fact, not a template assumption: a custom
        # bank recipe without design_mix has no inherited branch to show.
        "inherited_site": next((index[obj["refs"]["site"]]["attrs"]["name"]
                                for obj in kinds.get("device", [])
                                if obj["refs"].get("device_type") == "hardware/inherited-access"
                                and obj["refs"].get("site") in index), None),
        # Wireless claims are graph facts too: guest only when a guest SSID
        # exists, and the second radio only when a WLAN actually rides it.
        "guest_wireless": any("guest" in (obj["attrs"].get("ssid") or "").lower()
                              for obj in kinds.get("wireless_lan", [])),
        "second_radio_assigned": any(obj["attrs"].get("name") == "wlan1"
                                     and obj["refs"].get("wireless_lans")
                                     for obj in kinds.get("interface", [])),
        "wireless": next(({"device": index[obj["refs"]["device"]]["attrs"]["name"],
                           "radio": obj["attrs"]["name"],
                           "channel": obj["attrs"]["rf_channel"],
                           "ssid": index[obj["refs"]["wireless_lans"][0]]["attrs"]["ssid"],
                           "count": len(kinds.get("wireless_lan", []))}
                          for obj in kinds.get("interface", [])
                          if obj["attrs"].get("rf_channel") and obj["refs"].get("wireless_lans")
                          and obj["refs"].get("device") in index
                          and obj["refs"]["wireless_lans"][0] in index), None),
        "rack": rack["attrs"]["name"] if rack else None,
        "listener": listener,
        "desks": desks,
        # Display names, because an internal key finds nothing in the UI.
        "names": {key: obj["attrs"].get("name") for key, obj in index.items()},
        "custom_field": ({"name": field["attrs"]["name"], "label": field["attrs"].get("label")}
                         if field else None),
        "custom_link": link["attrs"]["name"] if link else None,
        "vrfs": [obj["attrs"]["name"] for obj in kinds.get("vrf", [])],
        "customer_tenants": [obj["attrs"]["slug"] for obj in kinds.get("tenant", [])
                             if (obj["refs"].get("group") or "").endswith("/customers")],
        "wlan": _wlan(kinds.get("wireless_lan", [])),
        # `<site>-ot-ds-a`/`-b`: the zone profiles' own distribution pair, at
        # the headline site rather than whichever site sorts first.
        "zone_devices": [obj["attrs"]["name"] for obj in at_site
                         if "-ot-ds-" in obj["attrs"]["name"]][:2],
    }


def _wlan(lans):
    """The WLAN whose comments name the service inventory it depends on."""
    chosen = next((lan for lan in lans if "inventory:" in lan["attrs"].get("comments", "")), None)
    if chosen is None:
        return None
    dependencies = [line for line in chosen["attrs"]["comments"].split("\n") if "inventory:" in line]
    return {"ssid": chosen["attrs"]["ssid"], "dependencies": dependencies}


# --------------------------------------------------------------------------
# DEMO.md


UPSTREAM = "https://github.com/netboxlabs/devin-genial/blob/main/"


def _repo_prefix(out):
    """Link prefix from the demo directory back to the repository's own docs.

    Relative for a demo written inside the repository (the default
    `build/demos/<namespace>`). Anywhere else a relative link would be dead and
    an absolute one would leak a local path into a shared cheat sheet, so the
    links point upstream instead.
    """
    root = Path(__file__).resolve().parents[1]
    out = Path(out).resolve()
    if not out.is_relative_to(root):
        return UPSTREAM
    return Path(os.path.relpath(root, out)).as_posix() + "/"


def _ui(live, path, query=""):
    """Render one UI deep link, branch-activated when the estate is loaded."""
    if not live:
        return f"`{path}{('?' + query) if query else ''}` (on the target, once loaded)"
    separator = "&" if query else "?"
    url = f"{live['origin']}{path}{('?' + query) if query else ''}{separator}_branch={live['schema']}"
    # A markdown link, never an autolink: table cells HTML-escape "&", and a
    # copied "&amp;" silently drops _branch= (HTTP 200 on plain main). The URL
    # inside parentheses survives copy-paste from any rendering.
    return f"[{path}]({url})"


def demo_markdown(spec, facts, artifacts, live):
    """Render the cheat sheet. Deterministic: timings live in their own section."""
    template = PROFILES[spec["profile"]]
    counts, out = facts["counts"], spec["out"]
    estate = f"{out}/estate"
    if spec.get("recipe_path"):
        # A customer-shaped estate: the template's size prose and hook describe
        # the stock demand, so derive both from the recipe and the graph.
        hook = (f"{counts['site']} sites under the {template.label.lower()} grammar, "
                f"shaped by the customer's own recipe.")
        size = f"custom shape from `{spec['recipe_path']}` — {counts['site']} sites (see the recipe)"
    else:
        hook, size = template.hook, template.size
    lines = [f"# {_cell(spec['name'])} — demo cheat sheet", "",
             f"**{_cell(template.label)}**, composed by Genial {spec['generator_version']}. "
             + ("Loaded and strictly verified on a live branch." if live else
                "Offline only: generated, validated and contract-checked. **Nothing has been "
                "written to a NetBox target.**"), "",
             f"> **{counts['site']} sites, {counts['device']} devices, {counts['cable']} cables "
             "— all connected.**", ">",
             f"> {_cell(hook)}", ""]

    _table(lines, ["", ""], [
        ["Customer", spec["name"]],
        ["Profile", f"`{spec['profile']}` — {template.label}"],
        ["Namespace", f"`{spec['namespace']}`"],
        ["Vendor line", spec["vendor_note"]],
        ["Seed", f"{spec['seed']} ({spec['seed_source']})"],
        ["Size", size],
        ["Objects", f"{facts['objects']:,} ({counts['interface']:,} interfaces, "
                    f"{counts['ip_address']:,} addresses, {counts.get('virtual_machine', 0)} VMs)"],
        ["Recipe", f"`{out}/recipe.toml`"],
        ["Artifact", f"`{estate}` (report: `{estate}/report.md`)"],
        ["Feature packs", ", ".join(spec["features"]) or "none"],
        ["Branch", f"`{live['branch']}`" if live else "not loaded"],
        ["Open at", f"[{live['ui_url']}]({live['ui_url']})" if live else "— (load it first, below)"],
    ])

    lines.extend(["## Before the call", ""])
    if live:
        lines.extend([
            f"Open <{live['ui_url']}>. The `?_branch={live['schema']}` parameter activates the "
            "branch for the session (Branching sets an `active_branch` cookie), so every later "
            "link stays inside the demo and `main` stays clean. You also need a UI login on the "
            "target — the API token above drives the commands, not the screen share.", "",
            f"The load verified {live['objects_matched']} objects with {live['mismatches']} "
            f"mismatches and re-verified clean with zero writes.", ""])
    else:
        lines.extend([
            "Nothing is on a target yet. Every offline gate passed; these are the exact "
            "commands to go live, in order:", "", "```sh",
            "export NETBOX_TOKEN=…            # or put it in .env",
            "export TURBOBULK_WRITES=1        # the write attestation",
            f"just branch https://netbox.example {_shell(spec['namespace'])}",
            f"just load {estate} https://netbox.example {_shell(spec['namespace'])}",
            f"just verify-target {estate} https://netbox.example {_shell(spec['namespace'])}",
            "```", "",
            "Or recompose straight onto the target — same recipe, same seed, same estate:", "",
            "```sh", _compose_command(spec, target="https://netbox.example",
                                      branch=spec["namespace"], out=f"{out}-live"), "```", "",
            f"`just load` prints `ui_url` when it finishes; that is the link this section "
            "would carry. Expect a minute or two per few thousand objects, and a silent "
            "several-minute window while the cable and counter finalizers run — that is normal, "
            "not a hang. See [first target](@ROOT@/docs/first-target.md) for the full runbook; "
            "the composer automates its sections 3 to 6.", ""])

    lines.extend(["## The walkthrough", "",
                  "Real objects from this estate, in the order that builds the story.", ""])
    steps = [
        ("Start with the whole estate.",
         f"{_ui(live, '/dcim/sites/')} — {counts['site']} sites"
         + ". Sites carry authored display names, facility codes and map coordinates"
         + (f" (this one is {_cell(facts['site']['facility'])})" if facts["site"]["facility"] else "")
         + ", so the list reads like an estate rather than a spreadsheet."),
        ("Drop into one site.",
         f"**{_cell(facts['site']['name'])}** (`{facts['site']['id']}`) — "
         + _ui(live, "/dcim/devices/", f"site={facts['site']['slug']}")
         + f" holds {facts['devices_at_site']} devices — {facts['racked_at_site']} racked"
         + (f" (start at **{_cell(facts['rack'])}**)" if facts["rack"] else "")
         + f", the other {facts['devices_at_site'] - facts['racked_at_site']} are room and wall "
           "endpoints in their own locations. Open the rack elevation: every unit position is "
           "deliberate, and its asset tag is unique across the whole estate."),
        ("Trace a path, do not assert one.",
         f"Open **{_cell(facts['device'])}** → *Interfaces* → any cabled port → **Trace**. "
         f"{counts['cable']:,} cables mean the trace lands somewhere real. Then *Power ports* "
         "→ trace to the feed and panel: the power path is modeled end to end, which is what "
         "makes the power-diversity story provable."
         if facts["device"] else
         "Open any device → *Interfaces* → a cabled port → **Trace**."),
        ("Show who owns and operates it.",
         "That device's own contact assignments resolve to "
         + ", ".join(f"**{_cell(desk)}**" for desk in facts["desks"])
         + ". Ownership, operation and escalation are records on the object, not description text."
         if facts["desks"] else
         "Contacts carry ownership, operation and escalation as records on the object, not as "
         "description text."),
    ]
    if facts["listener"]:
        listener = facts["listener"]
        steps.append((
            "Land on something an application owner cares about.",
            f"{_ui(live, '/ipam/services/')} — **{_cell(listener['name'])}** listens on "
            f"{listener['protocol']}/{listener['ports']} on `{_cell(listener['host'])}`"
            + (f" at `{listener['address']}`" if listener["address"] else "")
            + f". {counts.get('service', 0)} service records, each bound to a real interface "
              "address. This is the row the official demo dataset ships empty."))
    if facts.get("wireless"):
        w = facts["wireless"]
        steps.append(("Show the wireless story.",
                      f"{_ui(live, '/wireless/wireless-lans/')} — {w['count']} WLANs. Open "
                      f"**{_cell(w['device'])}** → *Interfaces*: radio `{w['radio']}` carries "
                      f"`{_cell(w['ssid'])}` on channel `{w['channel']}`, and its `eth0` uplink is "
                      "a real tagged trunk (untagged wireless management, tagged client VLANs) — "
                      "segmentation you can trace, not describe."))
    step = template.step
    if "{inherited}" in step:
        if facts.get("inherited_site"):
            step = step.format(inherited=f"**{_cell(facts['inherited_site'])}**")
        else:
            # No merger in this estate: the recipe carries no inherited design.
            step = None
    if step and "{guest}" in step:
        step = step.format(guest=(" Guest wireless is open access intent with no portal and no "
                                  "clinical execution." if facts.get("guest_wireless") else
                                  " Guest wireless is not requested in this recipe (guest demand "
                                  "defaults to zero); the clinical/medical/imaging segmentation "
                                  "carries the story."))
    if step:
        steps.append(("", step))
    for index, (lead, text) in enumerate(steps, start=1):
        lines.append(f"{index}. {('**' + _cell(lead) + '** ') if lead else ''}{text}")
    lines.append("")

    if spec["features"]:
        lines.extend(["## Feature packs", ""])
    if "assurance" in spec["features"]:
        drift = artifacts["drift"]
        lines.extend([
            "### Assurance — the drift twin", "",
            f"`{out}/drift/` is the other half of an Assurance demonstration: a bounded observed "
            f"Diode payload carrying **{drift['items']} deliberate drift items** across "
            f"{drift['observed_records']} observed records at "
            f"**{_cell(facts['names'].get(drift['site'], drift['site']))}**, plus the exact "
            "expected deviation manifest. It re-checked clean against its bound baseline.", "",
            f"**Read `{out}/drift/drift.md` before the call.** It carries the run sequence, the "
            "per-item talk track and the review-versus-auto-apply warning, and it is byte-bound "
            "to the manifest — `just drift-check` re-renders and compares it, so it is never "
            "restated here and never edited by hand. Where its step 1 says "
            f"`<original-baseline-build-dir>`, for this compose that is `{estate}`.", "",
            "Honest line, from that walkthrough: expected deviations are a prediction from the "
            "public Diode changeset contract. **No Assurance-equipped target has seen this "
            "payload.** Do not claim live matching, deviation routing or clearing behaviour.", ""])
    if "automation" in spec["features"]:
        lines.extend(["### Automation — the data the tooling consumes", "",
                      "The records an Ansible, Nautobot-style or ServiceNow talk track leans on "
                      "are in every estate, feature flag or not. What this section adds is where "
                      "to stand when the question comes.", ""])
        rows = [["Source of truth for a playbook",
                 _ui(live, "/dcim/devices/", f"site={facts['site']['slug']}"),
                 "Device, platform, role, primary IP and interface set — the inventory an "
                 "Ansible dynamic inventory plugin reads"]]
        if facts["listener"]:
            rows.append(["Service endpoints a template renders",
                         _ui(live, "/ipam/services/"),
                         f"e.g. `{_cell(facts['listener']['name'])}` on "
                         f"{facts['listener']['protocol']}/{facts['listener']['ports']}"
                         + (f" at `{facts['listener']['address']}`" if facts["listener"]["address"] else "")])
        if facts["custom_field"]:
            rows.append(["A custom field already carrying intent",
                         _ui(live, "/extras/custom-fields/"),
                         f"`{_cell(facts['custom_field']['name'])}` "
                         f"({_cell(facts['custom_field']['label'])}) on sites, with its own choice set"])
        if facts["custom_link"]:
            rows.append(["A custom link already rendering",
                         _ui(live, "/extras/custom-links/"),
                         f"`{_cell(facts['custom_link'])}` — a Jinja template over `object.pk`, "
                         "jumping from a site to its equipment"])
        if facts["wlan"]:
            rows.append(["A record that already resolves its own dependencies",
                         _ui(live, "/wireless/wireless-lans/"),
                         f"`{_cell(facts['wlan']['ssid'])}` comments name the exact DNS and RADIUS "
                         "hosts, protocols and ports it depends on — quoted below"])
        ns = spec["namespace"]
        rows.append(["Config contexts merging onto devices", _ui(live, "/extras/config-contexts/"),
                     f"`{ns} Global service baseline` — its ntp/syslog/dns/service endpoints are "
                     "THIS estate's own service VM addresses — and the role-scoped "
                     f"`{ns} Switch platform baseline` weighted above it. Open any access switch's "
                     "Config Context tab to show the merge"])
        rows.append(["Export templates that render", _ui(live, "/extras/export-templates/"),
                     f"`{ns} Device inventory (CSV)` and `{ns} Cable report (CSV)` — working Jinja "
                     "over dcim.device and dcim.cable; render either live from its object list"])
        rows.append(["Event rule + webhook (inert by design)", _ui(live, "/extras/event-rules/"),
                     f"`{ns} Device change notification` → `{ns} NetOps automation endpoint`: the "
                     "shape of a ServiceNow/ITSM hook, deliberately disabled with an unreachable "
                     "host — nothing fires, and the records say so"])
        _table(lines, ["What", "Where", "What is actually there"], rows)
        if facts["wlan"]:
            lines.extend([
                f"The strongest single artifact is that WLAN's own comments — the estate resolving "
                "its dependencies to named hosts, protocols and ports rather than describing them:",
                "", "```"]
                + [line.rstrip() for line in facts["wlan"]["dependencies"]] + ["```", "",
                "Those hostnames are real VMs in this estate with real service records and real "
                "addresses. That is the join an export template or a playbook would make.", ""])
        lines.extend([
            "**Say this, do not skip it.** Nothing here executes. The event rule is disabled and "
            "its webhook host is unreachable by design; no device is configured, no playbook has "
            "run, no state is reconciled against hardware. Every address, VLAN, context key and "
            "service record is documented inventory — the config context values are provably this "
            "estate's own service addresses, which is exactly the join a playbook would make.", ""])
    if "scenario" in spec["features"]:
        scenario = artifacts["scenario"]
        lines.extend([
            "### Scenario — loss of power diversity", "",
            f"`{out}/scenario/` holds two checked snapshots of this estate — `baseline/` healthy "
            f"and `changed/` with a deliberate defect on "
            f"**{_cell(facts['names'].get(scenario['subject'], scenario['subject']))}** — plus "
            f"`report.md`, the answer key. Verification confirmed the exact expected findings "
            f"({len(scenario['expected_findings'])}) and that reversing the change restores the "
            "baseline byte for byte.", "",
            "Run it as a question, not a reveal: *both cords are plugged in — prove they do not "
            "share an upstream failure domain.* Let the room trace power ports to outlets to "
            "panels before you open `report.md`.", "",
            "**Boundary.** Ordinary validation *must* fail on the changed snapshot; that is the "
            "point, and there is no ignore-errors path. The two snapshots are separate estates: "
            "load them into separate fresh targets, never as a transition on the branch above. "
            "This inspects modeled inventory and executes no failover.", ""])

    if spec["profile"] == "msp":
        first = facts["customer_tenants"][0] if facts["customer_tenants"] else f"{spec['namespace']}-cust-…"
        account = first.split("-cust-", 1)[-1]
        lines.extend([
            "## Prove it from the API", "",
            "The ownership-versus-operation split, from "
            "[the profile guide](@ROOT@/profiles/msp.md#ownership-versus-operation), bound to "
            "this estate:", "", "```sh",
            f"TARGET={live['origin'] if live else 'https://netbox.example'}",
            f"SCHEMA={live['schema'] if live else '<schema id from just branch>'}", "",
            "# 1. Every device at the customer's offices carries their tenant.",
            'curl -s -H "Authorization: Token $NETBOX_TOKEN" \\',
            f'  "$TARGET/api/dcim/devices/?tenant={first}&_branch=$SCHEMA&brief=true&limit=0" | jq .count',
            "# 2. Their operated equipment escalates to the provider's per-account desk.",
            'curl -s -H "Authorization: Token $NETBOX_TOKEN" \\',
            '  "$TARGET/api/tenancy/contact-assignments/?object_type=dcim.device&_branch=$SCHEMA&limit=0" \\',
            f"  | jq '[.results[] | select(.contact.name | contains(\"{account}\"))] | length'",
            "```", "",
            "The first count is every owned device; the second is the operated subset — access, "
            "distribution, WAN edge, APs, management and console server — bound to that account's "
            f"NOC duty desk. Accounts in this estate: "
            + ", ".join(f"`{tenant}`" for tenant in facts["customer_tenants"]) + ".", ""])

    if spec["profile"] in ("manufacturing", "utility"):
        zone = "plant-floor (OT)" if spec["profile"] == "manufacturing" else "station (OT)"
        segments = (["process", "supervisory"] if spec["profile"] == "manufacturing"
                    else ["protection", "telemetry", "station"])
        present = [name for name in facts["vrfs"] if name.rsplit("-", 1)[-1] in set(segments)]
        conduit = next((name for name in facts["vrfs"] if name.endswith("-conduit")), "conduit")
        pair = facts["zone_devices"]
        lines.extend([
            "## The zone boundary, on screen", "",
            f"The {zone} endpoints sit in their own routing contexts behind their own access and "
            "distribution pairs. In this estate those contexts are "
            + ", ".join(f"`{_cell(name)}`" for name in present)
            + (f", and the zone's own distribution pair is "
               + " / ".join(f"`{_cell(name)}`" for name in pair) if pair else "") + ".", "",
            f"Open {_ui(live, '/ipam/vrfs/')}, then "
            + _ui(live, "/ipam/vlans/", f"site={facts['site']['slug']}")
            + f". Three crossings exist and every one is deliberate: the conduit segment "
            f"(`{_cell(conduit)}`) trunked between the two distribution pairs — the only "
            "forwarding path — each zone switch's "
            "dedicated management port, and the room's shared serial console server. The "
            "validator walks each zone VLAN's actual cabled flood domain rather than trusting the "
            "builder's trunk lists.", "",
            "**The separation is modeled, never enforced.** No firewall rule, ACL, route filter "
            "or data diode exists anywhere, both tiers share one equipment room, and this is not "
            "an air gap. The full boundary list is in the profile guide — read it before you "
            "promise anything.", ""])

    lines.extend(["## Repeat, verify, retire", "", "```sh"])
    origin = live["origin"] if live else "https://netbox.example"
    branch = _shell(live["branch"] if live else spec["namespace"])
    lines.extend([
        "# Re-run the full strict readback at any time. Zero writes.",
        f"just verify-target {estate} {origin} {branch}", "",
        "# Grow it without renaming anything. FIRST edit a copy of the recipe (append",
        "# the new demand — running it unedited regenerates this same estate), then the",
        "# full go-live sequence in this order (one namespace = one live branch, so v1",
        "# retires before v2 loads; the preflight names any blocking rows first):",
        f"cp {out}/recipe.toml {out}/recipe-v2.toml   # then edit: append demand",
        f"just generate {out}/recipe-v2.toml {out}-v2 {estate}/plan.json",
        f"just load-check {out}-v2",
        f"just branch {origin} '<v2 branch name>'",
        f"just load-explain {out}-v2 {origin} '<v2 branch name>'",
        f"just retire {origin} {branch} {_shell(spec['namespace'])}",
        f"just load {out}-v2 {origin} '<v2 branch name>'",
        f"just verify-target {out}-v2 {origin} '<v2 branch name>'",
        *([f"just drift {out}-v2/plan.json {out}-v2-drift   # the drift twin binds one plan; regenerate it after growth"]
          if "assurance" in spec["features"] else []),
        *([f"just power-scenario {out}-v2/plan.json {out}-v2-scenario   # the what-if binds one plan; regenerate it after growth"]
          if "scenario" in spec["features"] else []),
        "# The grown estate has no regenerated cheat sheet: this DEMO.md's links die",
        f"# with the v1 branch — narrate the second call from {out}-v2/estate/report.md.",
        "# Close the grown demo out from here, with ITS branch:",
        f"# just retire {origin} '<v2 branch name>' {_shell(spec['namespace'])}",
        "```", "",
        "If you did **not** run the growth block, leave the target exactly as you found "
        "it — the branch AND the namespace's main-scoped rows, which branch deletion "
        "alone does not remove. Retiring with the branch already gone still deletes the "
        "rows, which breaks any other live branch's readback (the command warns).", "",
        "```sh",
        f"just retire {origin} {branch} {_shell(spec['namespace'])}",
        "```", "",
        "One namespace has one verifiable branch at a time. A side-by-side before/after demo "
        "needs two namespaces planned from the start.", "",
        "## Honest lines", "",
    ])
    honesty = [
        "Everything in this estate is fictional documentation inventory. No device was "
        "discovered, configured or measured, and no vendor compatibility is implied.",
        "Offline checks are not live acceptance. What is proven about the target is exactly "
        "what the load receipt and the strict readback record."
        if live else
        "Nothing has been loaded. Every claim above is an offline check; no NetBox target has "
        "seen this estate.",
        "Merging a TurboBulk-loaded branch to `main` is currently blocked upstream. The working "
        "pattern is branch-per-demo, then retire.",
        "Capacity figures are purchased or installed capacity and exact decimal reserve "
        "arithmetic — never measured traffic, throughput or headcount.",
    ]
    if spec["vendor"] == "aruba":
        honesty.append(
            ("`--vendor aruba` declares the AP-505's real 5 GHz + 2.4 GHz split, and a WLAN "
             "rides `wlan1` in this estate, so that SSID carries 2.4 GHz channels — say which "
             "band you are showing."
             if facts.get("second_radio_assigned") else
             "`--vendor aruba` declares the AP-505's real 5 GHz + 2.4 GHz split, but nothing "
             "rides `wlan1` in this profile: the second radio renders as an unused interface "
             "(no channel, no WLAN). Show `wlan0`'s 5 GHz plan; do not click into `wlan1` "
             "expecting a 2.4 GHz story.")
            if counts.get("wireless_lan") else
            "`--vendor aruba` was requested, but this profile models no radio, WLAN or wireless "
            "endpoint of any kind — the AP line changes the hardware digest and nothing on screen.")
    if spec["vendor"] == "juniper":
        honesty.append(
            "`--vendor juniper` moves the access and leaf families only; the AP family has no "
            "Juniper line in the catalog and stays on the reference model. The alternates meet "
            "or beat the models they replace on every port, PSU, PoE and optics quantity.")
    if counts.get("wireless_lan"):
        honesty.append("Wireless coverage is authored zone sizing at a declared 32 devices per AP, "
                       "not an RF survey or association evidence.")
    elif spec["vendor"] != "aruba":
        honesty.append("This profile models no wireless.")
    lines.extend(f"- {line}" for line in honesty)
    lines.extend(["", "Full boundaries: [modeling](@ROOT@/docs/modeling.md), "
                  "[scenarios](@ROOT@/docs/scenarios.md), "
                  "[qualification](@ROOT@/docs/qualification.md). This cheat sheet is "
                  "[docs/demo.md](@ROOT@/docs/demo.md)'s output; regenerating the same compose "
                  "reproduces it byte for byte, timings aside.", ""])
    return "\n".join(lines).replace("@ROOT@/", _repo_prefix(out)) + "\n"


def timings_markdown(steps, started_at):
    """The one section that legitimately differs between two identical composes."""
    lines = ["<!-- timings:start -->", "## What it cost to build", "",
             f"Composed {started_at}. Wall clock, in order:", ""]
    _table(lines, ["Step", "Seconds"],
           [[step["step"], f"{step['seconds']:.1f}"] for step in steps]
           + [["**total**", f"{sum(step['seconds'] for step in steps):.1f}"]])
    lines.extend(["The exact commands are in `compose.json` next to this file.",
                  "", "<!-- timings:end -->"])
    return "\n".join(lines) + "\n"


def _shell(value):
    """Quote a branch or namespace for a copy-pasteable shell command."""
    return value if re.fullmatch(r"[A-Za-z0-9_.:/-]+", value) else "'" + value.replace("'", "'\\''") + "'"


def _compose_command(spec, *, target=None, branch=None, out=None):
    argv = ["python3", "-m", "estates", "demo", "--profile", spec["profile"]]
    if spec["vendor"] != "default":
        argv += ["--vendor", spec["vendor"]]
    argv += ["--name", _shell(spec["name"])]
    if spec["namespace"] != _derived(spec["name"]):
        argv += ["--namespace", spec["namespace"]]
    if spec["seed_source"] == "supplied":
        argv += ["--seed", str(spec["seed"])]
    if spec["features"]:
        argv += ["--features", ",".join(spec["features"])]
    if spec["sites_file"]:
        argv += ["--sites", _shell(spec["sites_file"])]
    argv += ["--out", _shell(out or spec["out"])]
    if target:
        argv += ["--target", target, "--branch", _shell(branch)]
    return " ".join(argv)


def _derived(name):
    """The namespace this name would derive, or None when it cannot."""
    try:
        return namespace_for(name)
    except DesignError:
        return None


# --------------------------------------------------------------------------
# The compose


def run(spec, *, cli, stream=None):
    """Compose one demo end to end and return the machine-readable receipt."""
    stream = stream or sys.stderr
    out = Path(spec["out"])
    if out.exists():
        raise DesignError(f"Output {out} already exists. Choose a new --out; existing demos are "
                          "preserved. (A failed compose leaves its partial directory for "
                          "inspection — remove it deliberately.)")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out.mkdir(parents=True)
    recipe = out / "recipe.toml"
    recipe.write_text(recipe_text(spec))
    runner, estate = _Runner(cli, stream), out / "estate"

    runner.estates("generate the estate", ["generate", recipe, "--out", estate])
    runner.estates("validate the finished graph", ["check", estate / "plan.json"])
    contract = runner.module("check the TurboBulk contract offline", "load", [estate, "--load-check"])

    artifacts = {}
    if "assurance" in spec["features"]:
        artifacts["drift"] = runner.estates("build the Assurance drift twin",
                                            ["drift", estate / "plan.json", "--out", out / "drift"])
        runner.estates("re-check the drift twin", ["drift-check", out / "drift"])
    if "scenario" in spec["features"]:
        artifacts["scenario"] = runner.estates(
            "build the power-diversity scenario",
            ["scenario", estate / "plan.json", "--kind", "loss-of-power-diversity",
             "--out", out / "scenario"])
        runner.estates("check the scenario findings",
                       ["scenario-check", out / "scenario" / "scenario.json"])

    live = None
    if spec["target"]:
        created = runner.module("create the branch", "branch", [spec["target"], spec["branch"]])
        loaded = runner.module("load and strictly read back", "load",
                               [estate, spec["target"], "--branch", spec["branch"],
                                "--delivery-policy", "reviewable"])
        runner.module("re-verify with zero writes", "load",
                      [estate, spec["target"], "--branch", spec["branch"], "--verify-only"])
        origin = spec["target"].rstrip("/")
        # Every deep link in the cheat sheet carries `?_branch=<schema>`, and
        # the sheet says main stays clean. Without a schema id those links would
        # silently address main, so refuse rather than write that claim.
        if not created.get("schema_id") or not loaded.get("ui_url"):
            raise DesignError(
                f"branch {spec['branch']!r} reported no schema id, so no branch-activated link "
                "can be written. The estate is at "
                f"{estate}; check the branch on the target and load it with `just load` directly.")
        missing = [key for key in ("objects_matched", "mismatches") if loaded.get(key) is None]
        if missing:
            raise DesignError(
                f"the load returned no strict-readback evidence ({', '.join(missing)}), so a "
                f"'loaded and verified' cheat sheet would overstate it. Receipt: "
                f"{loaded.get('receipt')}")
        live = {"origin": origin, "branch": spec["branch"], "schema": created["schema_id"],
                "branch_id": created.get("id"), "ui_url": loaded["ui_url"],
                "objects_matched": loaded["objects_matched"],
                "mismatches": loaded["mismatches"], "receipt": loaded.get("receipt")}

    plan = json.loads((estate / "plan.json").read_text())
    facts = _facts(plan)
    (out / "DEMO.md").write_text(demo_markdown(spec, facts, artifacts, live)
                                 + "\n" + timings_markdown(runner.steps, started_at))
    receipt = {"schema_version": 1, "artifact": "demo-compose", "composed_at": started_at,
               "spec": {key: value for key, value in spec.items() if key != "site_names"},
               "site_names": spec["site_names"], "steps": runner.steps,
               "turbobulk_verdict": contract.get("verdict"), "artifacts": artifacts,
               "live": live, "counts": dict(sorted(facts["counts"].items())),
               "objects": facts["objects"],
               "compose_command": _compose_command(spec)}
    (out / "compose.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return {"name": spec["name"], "profile": spec["profile"], "namespace": spec["namespace"],
            "out": spec["out"], "demo": str(out / "DEMO.md"), "objects": facts["objects"],
            "sites": facts["counts"]["site"], "devices": facts["counts"]["device"],
            "cables": facts["counts"]["cable"], "features": spec["features"],
            "seconds": round(sum(step["seconds"] for step in runner.steps), 1),
            "loaded": bool(live), "ui_url": live["ui_url"] if live else None,
            "applied_to_target": bool(live)}
