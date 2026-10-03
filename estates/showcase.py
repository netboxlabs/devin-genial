"""Showcase sidecar: the first screens of a carrier estate on a NetBox tenant.

Like the geometry, lifecycle and validation sidecars this is a derived artifact
over a frozen ``plan.json``, never a canonical graph change: ``build`` writes
the tour's NetBox-core records bound to the plan's canonical SHA-256,
``check`` recomputes and byte-compares them, ``seed`` writes them over REST
(``SHOWCASE_WRITES=1``) with a private receipt and exact readback, and
``unseed`` removes exactly what a receipt recorded.

It writes three things, all derived from the finished graph:

* shared saved filters (``extras.savedfilter``) for the tour — carrier PoPs,
  customer premises, carrier network devices, backbone and transit circuits,
  internet exchanges, equipment not in service, staged and planned equipment,
  end-of-life hardware (from the catalog's vendor lifecycle dates) and former
  customers. Each records how many plan objects it selects; readback requires
  the target to return exactly that count. Names follow
  ``naming.main_scoped_name`` so ``just retire`` and ``teardown-main`` find
  them (``branch.RETIREMENT_LABELS``).
* three bookmarks for the seeding user (the founding PoP, its first PE, its IX
  port) and that user's home dashboard: a note with the estate's own numbers,
  three object lists and the bookmarks. NetBox exposes only the requesting
  user's dashboard (``/api/extras/dashboard/``) and creates it on that user's
  first Home visit, so the operator opens Home once before seeding; unseed
  restores the prior layout the receipt recorded.
* nothing else: ``BANNER_TOP`` and ``DEFAULT_USER_PREFERENCES`` live in config
  revisions, which NetBox exposes to no REST endpoint, so the artifact carries
  them as documented manual steps.

``first_impression`` is the offline F1-F5 measurement over the plan (the
lived-in design's first-impression acceptance), reported as numbers; it never
claims what a browser renders.
"""

from collections import Counter, defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .model import canonical, digest, hardware_catalog
from .naming import main_scoped_name
from .turbobulk import Client, LoadError, WriteRejected, _write_receipt

RECEIPT_VERSION = 1
WRITER_VERSION = "showcase-1"
NAME_LIMIT = 100
F5_PAGE = 50
F5_TARGET = 0.5
# The saved-filter labels the generator emits; branch.RETIREMENT_LABELS
# retires exactly these (with the "<namespace> " prefix on a shared tenant).
FILTER_LABELS = ("Carrier PoPs", "Customer premises", "Carrier network devices",
                 "Backbone & transit circuits", "Internet exchanges", "Not in service",
                 "Staged & planned equipment", "End-of-life hardware", "Former customers")
ENDPOINTS = {"dcim.site": "/api/dcim/sites/", "dcim.device": "/api/dcim/devices/",
             "circuits.circuit": "/api/circuits/circuits/", "tenancy.tenant": "/api/tenancy/tenants/",
             "extras.journalentry": "/api/extras/journal-entries/"}
UI = {"dcim.site": "/dcim/sites/", "dcim.device": "/dcim/devices/", "circuits.circuit": "/circuits/circuits/"}
# Circuit types are the provider profile's own authored keys.
CARRIER_CIRCUIT_TYPES = ("backbone", "dark-fiber", "transit", "noc-access")
IX_CIRCUIT_TYPE = "ix-port"
PENDING = ("planned", "staged")


class ShowcaseError(RuntimeError):
    """The artifact cannot be built, verified or seeded faithfully."""


# --- derivation ----------------------------------------------------------------

class _Plan:
    def __init__(self, plan):
        self.recipe = plan["recipe"]
        self.objects = {obj["key"]: obj for obj in plan["objects"]}
        self.kinds = defaultdict(list)
        for obj in plan["objects"]:
            self.kinds[obj["kind"]].append(obj)
        self.groups = {}
        for group in self.kinds["site_group"]:
            self.groups[group["key"].rsplit("/", 1)[-1]] = group
        missing = {"pop", "dc", "customer"} - set(self.groups)
        if self.recipe.get("profile") != "provider-backbone" or missing:
            raise ShowcaseError("the showcase sidecar tours a carrier estate: generate the "
                                "provider-backbone profile (missing site groups: "
                                f"{sorted(missing) or 'none'})")
        self.terms = defaultdict(list)
        for term in self.kinds["circuit_termination"]:
            self.terms[term["refs"]["circuit"]].append(self.objects.get(term["refs"].get("termination")))

    def attr(self, key, name):
        return self.objects[key]["attrs"][name]

    def group_of(self, site_key):
        return (self.objects[site_key]["refs"].get("group") or "").rsplit("/", 1)[-1]

    def carrier_site(self, site_key):
        return self.group_of(site_key) in ("pop", "dc")

    def carrier_circuit(self, circuit):
        """A circuit with terminations, none at a customer premises (backbone, transit, IX, NOC)."""
        ends = self.terms[circuit["key"]]
        sites = [end["key"] if end["kind"] == "site" else end["refs"].get("site")
                 for end in ends if end and end["kind"] in ("site", "location")]
        return bool(ends) and not any(self.group_of(site) == "customer" for site in sites if site)

    def type_slug(self, suffix):
        key = f"circuit-type/{suffix}"
        return self.attr(key, "slug") if key in self.objects else None


def _natural(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _select(p, object_type, parameters):
    """Plan objects a saved filter's parameters select (only the lookups this sidecar emits)."""
    kind = object_type.split(".")[1].replace("journalentry", "journal_entry")
    out = []
    for obj in p.kinds[kind]:
        facts = {"status": obj["attrs"].get("status", "active")}
        if kind == "site":
            facts["group"] = p.attr(obj["refs"]["group"], "slug") if obj["refs"].get("group") else None
        elif kind == "device":
            site = p.objects[obj["refs"]["site"]]
            facts.update(site_group=p.attr(site["refs"]["group"], "slug") if site["refs"].get("group") else None,
                         role=p.attr(obj["refs"]["role"], "slug"),
                         device_type=p.attr(obj["refs"]["device_type"], "slug"))
        elif kind == "circuit":
            facts["type"] = p.attr(obj["refs"]["type"], "slug")
        elif kind == "tenant":
            facts["group"] = p.attr(obj["refs"]["group"], "slug") if obj["refs"].get("group") else None
            facts["description"] = obj["attrs"].get("description") or ""
        ok = True
        for name, values in parameters.items():
            if name.endswith("__n"):
                ok &= facts[name[:-3]] not in values
            elif name.endswith("__isw"):
                ok &= any(facts[name[:-5]].lower().startswith(v.lower()) for v in values)
            else:
                ok &= facts[name] in values
        if ok:
            out.append(obj)
    return out


def _filters(p):
    ns = p.recipe["namespace"]
    pop, dc, customer = (p.attr(p.groups[g]["key"], "slug") for g in ("pop", "dc", "customer"))
    carrier_devices = [d for d in p.kinds["device"] if p.carrier_site(d["refs"]["site"])]
    has = defaultdict(set)
    for kind in ("interface", "power_outlet"):
        for obj in p.kinds[kind]:
            has[obj["refs"]["device"]].add(kind)
    # Network roles: devices with interfaces that distribute no power (PDUs,
    # panels and cable managers stay out).
    network_roles = sorted({p.attr(d["refs"]["role"], "slug") for d in carrier_devices
                            if has[d["key"]] == {"interface"}})
    catalog = hardware_catalog()["models"]
    as_of = p.recipe.get("as_of", "")
    end_of_life = sorted({dtype["attrs"]["slug"] for dtype in p.kinds["device_type"]
                          if (life := (catalog.get(dtype["key"].split("/", 1)[1]) or {}).get("lifecycle"))
                          and (life.get("discontinued") or any(life.get(k) and life[k] <= as_of
                                                               for k in ("eol_announced", "last_order")))})
    carrier_types = [s for s in map(p.type_slug, CARRIER_CIRCUIT_TYPES) if s]
    ix = p.type_slug(IX_CIRCUIT_TYPE)
    estate_groups = sorted([pop, dc, customer])
    former = sorted({(t["attrs"].get("description") or "").split(" ")[0] for t in p.kinds["tenant"]
                     if (t["attrs"].get("description") or "").startswith("Former ")})
    customers_group = next((g for g in p.kinds["tenant_group"] if g["key"].endswith("/customers")), None)
    specs = [
        ("Carrier PoPs", "dcim.site", {"group": [pop]}, "Every carrier point of presence"),
        ("Customer premises", "dcim.site", {"group": [customer]}, "Every customer premises the carrier serves"),
        ("Carrier network devices", "dcim.device", {"site_group": sorted([pop, dc]), "role": network_roles},
         "Routers, switches and appliances at the PoPs and the NOC"),
        ("Backbone & transit circuits", "circuits.circuit", {"type": carrier_types},
         "Backbone, dark fibre, transit and NOC circuits"),
        ("Internet exchanges", "circuits.circuit", {"type": [ix]} if ix else None, "Internet exchange ports"),
        ("Not in service", "dcim.device", {"site_group": estate_groups, "status__n": ["active"]},
         "Equipment that is planned, staged, a spare or being withdrawn"),
        ("Staged & planned equipment", "dcim.device", {"site_group": estate_groups, "status": list(PENDING)},
         "Equipment ordered or racked and awaiting installation"),
        ("End-of-life hardware", "dcim.device", {"device_type": end_of_life} if end_of_life else None,
         "Models past their vendor end-of-life announcement or last order date"),
        ("Former customers", "tenancy.tenant",
         {"group": [customers_group["attrs"]["slug"]], "description__isw": former}
         if customers_group and former else None, "Customers whose service has ceased"),
    ]
    filters = []
    for weight, (label, object_type, parameters, description) in enumerate(specs, start=1):
        if not parameters or not all(parameters.values()):
            continue
        selected = _select(p, object_type, parameters)
        if not selected:
            continue
        filters.append({"label": label, "name": main_scoped_name(p.recipe, label),
                        "slug": f"{ns}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}",
                        "object_type": object_type, "parameters": parameters,
                        "description": description, "weight": weight * 10, "count": len(selected)})
    return filters


def _install_day(entry):
    return (entry["attrs"].get("created") or "")[:10]


def _tour(p):
    """The founding PoP (earliest dated journal at a PoP), its first active PE and its IX port."""
    first = {}
    for entry in p.kinds["journal_entry"]:
        target = p.objects.get(entry["refs"].get("assigned_object"))
        site = (target or {}).get("refs", {}).get("site") if target and target["kind"] != "site" else \
            (target or {}).get("key")
        if site and p.group_of(site) == "pop" and _install_day(entry):
            first[site] = min(first.get(site, "9999"), _install_day(entry))
    if not first:
        raise ShowcaseError("no dated journal at any PoP; cannot choose the founding PoP")
    founding = min(first, key=lambda key: (first[key], key))
    pe = sorted((d for d in p.kinds["device"] if d["refs"]["site"] == founding
                 and d["refs"]["role"].endswith("provider-edge") and d["attrs"].get("status", "active") == "active"),
                key=lambda d: _natural(d["attrs"]["name"]))
    ix = p.type_slug(IX_CIRCUIT_TYPE)
    ports = sorted((c for c in p.kinds["circuit"] if ix and p.attr(c["refs"]["type"], "slug") == ix
                    and c["attrs"].get("status", "active") == "active"),
                   key=lambda c: (founding not in [e and e["key"] for e in p.terms[c["key"]]], c["attrs"]["cid"]))
    marks = [{"label": p.attr(founding, "name"), "object_type": "dcim.site",
              "lookup": {"slug": p.attr(founding, "slug")}}]
    if pe:
        marks.append({"label": pe[0]["attrs"]["name"], "object_type": "dcim.device",
                      "lookup": {"name": pe[0]["attrs"]["name"], "site": p.attr(founding, "slug")}})
    if ports:
        marks.append({"label": f"{p.attr(ports[0]['refs']['provider'], 'name')} port {ports[0]['attrs']['cid']}",
                      "object_type": "circuits.circuit", "lookup": {"cid": ports[0]["attrs"]["cid"]}})
    return founding, first[founding], marks


def _numbers(p, founded):
    pops = [s for s in p.kinds["site"] if p.group_of(s["key"]) == "pop"]
    metros = {s["refs"].get("region") for s in pops}
    customers = [t for t in p.kinds["tenant"] if (t["refs"].get("group") or "").endswith("/customers")
                 and not (t["attrs"].get("description") or "").startswith("Former ")]
    premises = [s for s in p.kinds["site"] if p.group_of(s["key"]) == "customer"]
    circuits = [c for c in p.kinds["circuit"] if c["attrs"].get("status", "active") == "active"]
    return {"pops": len(pops), "metros": len(metros), "customers": len(customers),
            "premises": len(premises), "circuits_in_service": len(circuits), "founded": founded[:4]}


def _widget_id(namespace, name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"genial/{namespace}/showcase/{name}"))


def _dashboard(p, numbers, marks, filters):
    ns, estate = p.recipe["namespace"], p.recipe.get("name") or p.recipe["namespace"]
    links = " · ".join(f"[{m['label']}]({UI[m['object_type']]}?{urllib.parse.urlencode(m['lookup'])})"
                       for m in marks)
    by_label = {f["label"]: f for f in filters}
    note = (f"**{estate}** runs {numbers['pops']} PoPs in {numbers['metros']} metros and a NOC, serving "
            f"{numbers['customers']} customers at {numbers['premises']} premises over "
            f"{numbers['circuits_in_service']} circuits in service. In service since {numbers['founded']}.\n\n"
            f"Start here: {links}")
    widgets = [
        ("note", "extras.NoteWidget", f"{estate}", "green", 0, 0, 6, 4, {"content": note}),
        ("bookmarks", "extras.BookmarksWidget", "Start here", "orange", 6, 0, 6, 4,
         {"order_by": "name", "object_types": sorted({m["object_type"] for m in marks})}),
    ]
    lists = [("pops", "Carrier PoPs", "dcim.site", 0, 4, 6, 6),
             ("not-in-service", "Not in service", "dcim.device", 6, 4, 6, 6)]
    for key, label, model, x, y, w, h in lists:
        if label in by_label:
            widgets.append((key, "extras.ObjectListWidget", label, None, x, y, w, h,
                            {"model": model, "page_size": 12, "url_params": by_label[label]["parameters"]}))
    widgets.append(("journal", "extras.ObjectListWidget", "Recent journal entries", None, 0, 10, 12, 6,
                    {"model": "extras.journalentry", "page_size": 10, "url_params": {"ordering": ["-created"]}}))
    layout, config = [], {}
    for key, cls, title, color, x, y, w, h, settings in widgets:
        ident = _widget_id(ns, key)
        layout.append({"id": ident, "x": x, "y": y, "w": w, "h": h})
        config[ident] = {"class": cls, "title": title, "color": color, "config": settings}
    return {"layout": layout, "config": config}


def _first_impression(p, filters, dashboard, marks):
    estate = p.recipe.get("name") or p.recipe["namespace"]
    out = {}
    # F1: the Home dashboard — note names the carrier, three object lists select plan objects,
    # every bookmark resolves to exactly one plan object.
    lists = [w["config"] for w in dashboard["config"].values() if w["class"] == "extras.ObjectListWidget"]
    populated = sum(1 for cfg in lists if cfg["model"] == "extras.journalentry" and p.kinds["journal_entry"]
                    or cfg["model"] != "extras.journalentry"
                    and _select(p, cfg["model"], {k: v for k, v in cfg["url_params"].items()}))
    resolved = 0
    for mark in marks:
        kind = mark["object_type"].split(".")[1]
        hits = [o for o in p.kinds[kind] if all(
            (p.attr(o["refs"]["site"], "slug") if k == "site" else o["attrs"].get(k)) == v
            for k, v in mark["lookup"].items())]
        resolved += len(hits) == 1
    note = next(w["config"]["content"] for w in dashboard["config"].values() if w["class"] == "extras.NoteWidget")
    out["F1"] = {"note_names_carrier": estate in note, "object_lists_populated": populated,
                 "object_lists": len(lists), "bookmarks_resolved": resolved, "bookmarks": len(marks),
                 "pass": estate in note and populated == len(lists) == 3 and resolved == len(marks) == 3}
    # F2: /dcim/sites/ page — carrier rows in the first (PoPs + NOC) rows, by name and by (group, name).
    sites = p.kinds["site"]
    carrier = sum(1 for s in sites if p.carrier_site(s["key"]))
    by_name = sorted(sites, key=lambda s: _natural(s["attrs"]["name"]))
    by_group = sorted(sites, key=lambda s: (_natural(p.attr(s["refs"]["group"], "name")), _natural(s["attrs"]["name"])))
    pops = next((f["count"] for f in filters if f["label"] == "Carrier PoPs"), 0)
    out["F2"] = {"carrier_sites": carrier,
                 "carrier_rows_default_ordering": sum(p.carrier_site(s["key"]) for s in by_name[:carrier]),
                 "carrier_rows_group_ordering": sum(p.carrier_site(s["key"]) for s in by_group[:carrier]),
                 "carrier_pops_filter": pops,
                 "pass": pops == carrier - 1 and sum(p.carrier_site(s["key"]) for s in by_group[:carrier]) == carrier}
    # F3: /tenancy/tenants/ — carrier group first; every customer description names the carrier.
    tenants = p.kinds["tenant"]
    groups = sorted(p.kinds["tenant_group"], key=lambda g: _natural(g["attrs"]["name"]))
    exceptions = [t["attrs"]["name"] for t in tenants if (t["refs"].get("group") or "").endswith("/customers")
                  and not ((t["attrs"].get("description") or "").endswith(f"customer of {estate}"))]
    first = groups[0]["key"] if groups else None
    out["F3"] = {"first_tenant_group": groups[0]["attrs"]["name"] if groups else None,
                 "carrier_group_first": bool(first and first.endswith("/operator")),
                 "customer_description_exceptions": len(exceptions), "exception_sample": exceptions[:5],
                 "former_customers": sum(1 for t in tenants if (t["attrs"].get("description") or "").startswith("Former ")),
                 "pass": bool(first and first.endswith("/operator")) and not exceptions}
    # F4: global search for the carrier's name — kinds whose name or description carries it.
    kinds = Counter(o["kind"] for o in p.objects.values()
                    if estate in str(o["attrs"].get("name") or "") or estate in str(o["attrs"].get("description") or ""))
    out["F4"] = {"kinds": dict(sorted(kinds.items())), "kind_count": len(kinds),
                 "carrier_tenant": any(t["attrs"]["name"] == estate for t in tenants),
                 "pass": len(kinds) >= 5 and any(t["attrs"]["name"] == estate for t in tenants)}
    # F5: /extras/journal-entries/ page 1 — newest 50 by created; share on carrier infrastructure.
    journals = sorted((j for j in p.kinds["journal_entry"] if j["attrs"].get("created")),
                      key=lambda j: (j["attrs"]["created"], j["key"]), reverse=True)[:F5_PAGE]
    # Strict (the design's list): PoP/NOC devices, racks, locations and sites, and
    # backbone, dark-fibre, transit, IX and NOC circuits. Broad adds what an
    # engineer would also read as the carrier's own: NOC virtual machines, the
    # PoP console out-of-band circuits and carrier-circuit terminations.
    strict_types = {f"circuit-type/{t}" for t in CARRIER_CIRCUIT_TYPES + (IX_CIRCUIT_TYPE,)}
    strict = broad = 0
    for entry in journals:
        target = p.objects.get(entry["refs"].get("assigned_object"))
        if target is None:
            continue
        if target["kind"] == "circuit":
            strict += target["refs"]["type"] in strict_types
            broad += p.carrier_circuit(target)
            continue
        if target["kind"] == "circuit_termination":
            broad += p.carrier_circuit(p.objects[target["refs"]["circuit"]])
            continue
        site = target["key"] if target["kind"] == "site" else target["refs"].get("site")
        if target["kind"] == "virtual_machine" and not site:
            cluster = p.objects.get(target["refs"].get("cluster"), {"refs": {}})
            site = cluster["refs"].get("scope_site") or cluster["refs"].get("site")
            broad += bool(site and p.carrier_site(site))
            continue
        hit = bool(site and p.carrier_site(site))
        strict += hit
        broad += hit
    share = strict / len(journals) if journals else 0.0
    out["F5"] = {"page": len(journals), "carrier_rows": strict, "share": round(share, 3),
                 "broad_rows": broad, "broad_share": round(broad / len(journals), 3) if journals else 0.0,
                 "target": F5_TARGET, "pass": share >= F5_TARGET}
    return out


def create(plan):
    p = _Plan(plan)
    filters = _filters(p)
    founding, founded, marks = _tour(p)
    numbers = _numbers(p, founded)
    dashboard = _dashboard(p, numbers, marks, filters)
    estate = p.recipe.get("name") or p.recipe["namespace"]
    return {
        "artifact": "showcase", "plan_sha256": digest(plan), "writer_version": WRITER_VERSION,
        "namespace": p.recipe["namespace"], "estate": estate, "numbers": numbers,
        "filters": filters, "bookmarks": marks, "dashboard": dashboard,
        "first_impression": _first_impression(p, filters, dashboard, marks),
        "manual_steps": [
            {"setting": "BANNER_TOP", "where": "Admin → System → Configuration history → new revision",
             "value": f"{estate} — reference carrier estate",
             "reason": "config revisions have no REST endpoint"},
            {"setting": "DEFAULT_USER_PREFERENCES",
             "where": "Admin → System → Configuration history → new revision",
             "value": {"tables": {"SiteTable": {"ordering": ["group", "name"]},
                                  "TenantTable": {"ordering": ["group", "name"]}}},
             "reason": "config revisions have no REST endpoint; without it the Carrier PoPs "
                       "saved filter is the PoP view"},
            {"setting": "dashboard", "where": "open Home once as the token's user before seeding",
             "value": None, "reason": "NetBox creates a user's dashboard on the first Home visit; "
                                      "/api/extras/dashboard/ only updates an existing one"},
        ],
    }


# --- verification --------------------------------------------------------------

def _intrinsic(artifact):
    if artifact.get("artifact") != "showcase":
        raise ShowcaseError("not a showcase artifact")
    names, slugs = set(), set()
    for f in artifact["filters"]:
        if len(f["name"]) > NAME_LIMIT or f["name"] in names or f["slug"] in slugs:
            raise ShowcaseError(f"saved filter {f['name']!r} is duplicated or too long")
        if f["label"] not in FILTER_LABELS or not f["name"].endswith(f["label"]):
            raise ShowcaseError(f"saved filter {f['name']!r} is not a retirable label")
        if not f["slug"].startswith(artifact["namespace"] + "-") or f["count"] < 1:
            raise ShowcaseError(f"saved filter {f['name']!r} lacks its namespace slug or selects nothing")
        names.add(f["name"])
        slugs.add(f["slug"])
    layout = {entry["id"] for entry in artifact["dashboard"]["layout"]}
    if layout != set(artifact["dashboard"]["config"]):
        raise ShowcaseError("dashboard layout and widget config disagree")
    if len({canonical(m["lookup"]) for m in artifact["bookmarks"]}) != len(artifact["bookmarks"]):
        raise ShowcaseError("a bookmark repeats")


def verify(artifact, plan):
    _intrinsic(artifact)
    if artifact.get("plan_sha256") != digest(plan):
        raise ShowcaseError("showcase artifact is not bound to this canonical plan")
    if canonical(artifact) != canonical(create(plan)):
        raise ShowcaseError("showcase artifact differs from the tour recomputed from the bound plan; rebuild it")
    return _summary(artifact)


def _summary(artifact):
    impression = artifact["first_impression"]
    return {"filters": len(artifact["filters"]), "bookmarks": len(artifact["bookmarks"]),
            "widgets": len(artifact["dashboard"]["layout"]),
            "first_impression": {k: v["pass"] for k, v in impression.items()},
            "f5_share": impression["F5"]["share"]}


def build(plan_path, out):
    plan = json.loads(Path(plan_path).read_text())
    artifact = create(plan)
    evidence = verify(artifact, plan)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "showcase.json").write_bytes(canonical(artifact) + b"\n")
    (out / "checks.json").write_bytes(canonical({
        "status": "passed", "scope": "offline showcase tour and first-impression measurement",
        "plan_sha256": artifact["plan_sha256"], **evidence}) + b"\n")
    return {"artifact": "showcase", "output": str(out), **evidence,
            "impression": artifact["first_impression"]}


def check(directory, plan_path):
    directory = Path(directory)
    raw = (directory / "showcase.json").read_bytes()
    artifact = json.loads(raw)
    evidence = verify(artifact, json.loads(Path(plan_path).read_text()))
    if raw != canonical(artifact) + b"\n":
        raise ShowcaseError("showcase.json bytes differ from their canonical form; rebuild")
    try:
        checks = json.loads((directory / "checks.json").read_text())
    except (OSError, ValueError) as exc:
        raise ShowcaseError(f"checks.json is missing or unreadable: {exc}")
    if checks.get("status") != "passed" or checks.get("plan_sha256") != artifact["plan_sha256"]:
        raise ShowcaseError("checks.json does not record a passed check bound to this plan")
    return {"artifact": "showcase", **evidence, "impression": artifact["first_impression"]}


# --- seeding -------------------------------------------------------------------

def _gate(action):
    if os.environ.get("SHOWCASE_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError(f"{action} requires SHOWCASE_WRITES=1 in the environment")


def _send(client, method, path, payload):
    return client.request(path, method=method, body=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, branch=False)[1]


def _delete(client, path):
    request = urllib.request.Request(client.base + path, headers=client.headers, method="DELETE")
    try:
        with client.opener.open(request, timeout=120):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        raise LoadError(f"DELETE {path} returned HTTP {exc.code}: {exc.read(2000).decode(errors='replace')}") from exc


def _query(parameters):
    return urllib.parse.urlencode(parameters, doseq=True)


def _resolve(client, mark):
    endpoint = ENDPOINTS[mark["object_type"]]
    _, page = client.request(f"{endpoint}?{_query(mark['lookup'])}&brief=1", branch=False)
    if page.get("count") != 1:
        raise LoadError(f"bookmark target {mark['label']!r} resolves to {page.get('count')} objects; "
                        "seed the estate first")
    return page["results"][0]["id"]


def _filter_row(f):
    return {"name": f["name"], "slug": f["slug"], "object_types": [f["object_type"]],
            "description": f["description"], "weight": f["weight"], "enabled": True, "shared": True,
            "parameters": f["parameters"]}


def seed(artifact_dir, *, url, token, receipt_path):
    _gate("writing the showcase tour")
    directory = Path(artifact_dir)
    raw = (directory / "showcase.json").read_bytes()
    artifact = json.loads(raw)
    _intrinsic(artifact)
    client = Client(url, token)
    _, user = client.request("/api/authentication-check/", branch=False)
    binding = {"receipt_version": RECEIPT_VERSION, "writer_version": WRITER_VERSION,
               "artifact": str(directory), "showcase_sha256": hashlib.sha256(raw).hexdigest(),
               "plan_sha256": artifact["plan_sha256"], "target": client.base, "user": user["id"]}
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if receipt is not None:
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; choose a new receipt")
    receipt = receipt or {**binding, "filters": {}, "bookmarks": {}}
    # Resolve every bookmark target before any write, so a missing estate writes nothing.
    targets = {m["label"]: _resolve(client, m) for m in artifact["bookmarks"]}
    # Fresh-only: a saved filter with our slug or name must be one this receipt created.
    ours = {row_id for row_id in receipt["filters"].values()}
    for f in artifact["filters"]:
        for field in ("slug", "name"):
            _, page = client.request(f"/api/extras/saved-filters/?{_query({field: f[field]})}", branch=False)
            foreign = [row for row in page.get("results", []) if row["id"] not in ours]
            if foreign:
                raise LoadError(f"saved filter {f[field]!r} already exists and this receipt did not create "
                                "it; showcase seeding is fresh-only (unseed or retire it first)")
    _write_receipt(receipt_path, receipt)
    for f in artifact["filters"]:
        if f["slug"] not in receipt["filters"]:
            receipt["filters"][f["slug"]] = _send(client, "POST", "/api/extras/saved-filters/", _filter_row(f))["id"]
            _write_receipt(receipt_path, receipt)
    for mark in artifact["bookmarks"]:
        if mark["label"] in receipt["bookmarks"]:
            continue
        object_id = targets[mark["label"]]
        _, page = client.request(f"/api/extras/bookmarks/?object_type={mark['object_type']}"
                                 f"&object_id={object_id}&user_id={user['id']}", branch=False)
        if page.get("count"):
            # The user had bookmarked it already: keep theirs, never delete it.
            receipt["bookmarks"][mark["label"]] = {"id": page["results"][0]["id"], "created": False}
        else:
            row = _send(client, "POST", "/api/extras/bookmarks/",
                        {"object_type": mark["object_type"], "object_id": object_id, "user": user["id"]})
            receipt["bookmarks"][mark["label"]] = {"id": row["id"], "created": True}
        _write_receipt(receipt_path, receipt)
    if "prior_dashboard" not in receipt:
        _, prior = client.request("/api/extras/dashboard/", branch=False)
        receipt["prior_dashboard"] = {"layout": prior.get("layout") or [], "config": prior.get("config") or {}}
        _write_receipt(receipt_path, receipt)
    try:
        _send(client, "PATCH", "/api/extras/dashboard/", artifact["dashboard"])
    except (WriteRejected, LoadError) as exc:
        raise LoadError(f"the dashboard could not be written ({exc}); NetBox creates a user's dashboard on "
                        f"the first Home visit — open Home once as {user.get('username')!r}, then rerun "
                        f"with this receipt") from exc
    receipt["dashboard_written"] = True
    receipt["readback"] = readback(client, artifact, receipt, targets)
    receipt["success"] = not receipt["readback"]["mismatches"]
    _write_receipt(receipt_path, receipt)
    if not receipt["success"]:
        raise LoadError(f"showcase readback found mismatches: {receipt['readback']['mismatches'][:10]}; "
                        f"receipt: {receipt_path}")
    return {"success": True, "receipt": str(receipt_path), "filters": len(receipt["filters"]),
            "bookmarks": len(receipt["bookmarks"]), "dashboard_widgets": len(artifact["dashboard"]["layout"])}


def readback(client, artifact, receipt, targets):
    """Exact readback: filter rows and their live selection counts, bookmarks, dashboard."""
    mismatches = []
    for f in artifact["filters"]:
        _, row = client.request(f"/api/extras/saved-filters/{receipt['filters'][f['slug']]}/", branch=False)
        wanted = _filter_row(f)
        got = {k: row.get(k) for k in wanted}
        if got != wanted:
            mismatches.append(f"saved filter {f['name']}: {sorted(k for k in wanted if got[k] != wanted[k])}")
        _, page = client.request(f"{ENDPOINTS[f['object_type']]}?{_query(f['parameters'])}&brief=1&limit=1",
                                 branch=False)
        if page.get("count") != f["count"]:
            mismatches.append(f"saved filter {f['name']} selects {page.get('count')}, plan {f['count']}")
    for mark in artifact["bookmarks"]:
        _, row = client.request(f"/api/extras/bookmarks/{receipt['bookmarks'][mark['label']]['id']}/", branch=False)
        if row.get("object_id") != targets[mark["label"]] or row.get("object_type") != mark["object_type"]:
            mismatches.append(f"bookmark {mark['label']}")
    _, dashboard = client.request("/api/extras/dashboard/", branch=False)
    if canonical({"layout": dashboard.get("layout"), "config": dashboard.get("config")}) != \
            canonical(artifact["dashboard"]):
        mismatches.append("dashboard")
    return {"mismatches": mismatches, "filters": len(artifact["filters"]),
            "bookmarks": len(artifact["bookmarks"])}


def unseed(receipt_path, *, url, token):
    """Restore the prior dashboard, then delete exactly the bookmarks and filters a receipt created."""
    _gate("removing the showcase tour")
    receipt = json.loads(Path(receipt_path).read_text())
    client = Client(url, token)
    if receipt.get("target") != client.base:
        raise LoadError(f"receipt {receipt_path} belongs to {receipt.get('target')}, not {client.base}")
    _, user = client.request("/api/authentication-check/", branch=False)
    if user["id"] != receipt.get("user"):
        raise LoadError("the dashboard and bookmarks belong to the user that seeded them; "
                        "unseed with that user's token")
    restored = False
    if receipt.get("dashboard_written") and "prior_dashboard" in receipt:
        _send(client, "PATCH", "/api/extras/dashboard/", receipt["prior_dashboard"])
        restored = True
    paths = ([f"/api/extras/bookmarks/{b['id']}/" for b in receipt.get("bookmarks", {}).values() if b["created"]]
             + [f"/api/extras/saved-filters/{i}/" for i in receipt.get("filters", {}).values()])
    for path in paths:
        _delete(client, path)
    survivors = []
    for path in paths:
        endpoint, row_id = path.rstrip("/").rsplit("/", 1)
        if client.request(f"{endpoint}/?id={row_id}", branch=False)[1].get("count"):
            survivors.append(path)
    if survivors:
        raise LoadError(f"showcase rows survived removal: {survivors}")
    return {"success": True, "deleted": len(paths), "dashboard_restored": restored, "receipt": str(receipt_path)}


def default_receipt(artifact_dir, target):
    slug = Path(artifact_dir).name or "showcase"
    suffix = hashlib.sha256(f"{slug}\n{target.rstrip('/')}".encode()).hexdigest()[:12]
    return Path("build/load-receipts") / f"{slug}-showcase-{suffix}.json"


def _report(result):
    lines = [f"showcase: {result['filters']} saved filters, {result['bookmarks']} bookmarks, "
             f"{result['widgets']} dashboard widgets" + (f" -> {result['output']}" if result.get("output") else "")]
    for key, value in result["impression"].items():
        detail = {k: v for k, v in value.items() if k not in ("pass", "exception_sample", "kinds")}
        lines.append(f"  {key} {'pass' if value['pass'] else 'THIN'}: "
                     + ", ".join(f"{k}={v}" for k, v in detail.items()))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Derive, verify and seed the showcase tour records")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="derive the showcase artifact from a frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="recompute a saved showcase artifact from its bound plan")
    p.add_argument("directory", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("seed", help="write saved filters, bookmarks and the dashboard (SHOWCASE_WRITES=1)")
    p.add_argument("directory", type=Path)
    p.add_argument("target")
    p.add_argument("--receipt", type=Path)
    p = sub.add_parser("unseed", help="restore the dashboard and delete what a receipt created (SHOWCASE_WRITES=1)")
    p.add_argument("receipt", type=Path)
    p.add_argument("target")
    args = parser.parse_args(argv)
    try:
        if args.command in ("build", "check"):
            result = build(args.plan, args.out) if args.command == "build" else check(args.directory, args.plan)
            print(_report(result))
        else:
            token = os.environ.get("NETBOX_TOKEN") or parser.error("NETBOX_TOKEN is required")
            if args.command == "unseed":
                print(json.dumps(unseed(args.receipt, url=args.target, token=token), sort_keys=True))
            else:
                receipt = args.receipt or default_receipt(args.directory, args.target)
                print(f"Receipt: {receipt}", flush=True)
                print(json.dumps(seed(args.directory, url=args.target, token=token, receipt_path=receipt),
                                 sort_keys=True))
        return 0
    except (ShowcaseError, LoadError, OSError, ValueError, KeyError) as exc:
        import sys
        print(f"Showcase failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
