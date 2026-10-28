"""NetBox Designs catalogue for one retail store format.

A sidecar over a frozen ``plan.json``, like the drift twin and the geometry
artifact: ``build`` exports the first store of the smallest store format in
permanent allocation order as a NetBox Designs catalogue (one ``dtl:``-pinned
device design per device type, one composed store design, the selected
store's variable values and a manifest), all bound to the plan's canonical
SHA-256; ``check`` re-derives the catalogue byte for byte, validates every
design against the published design schema, expands the composed design with
the recorded variables and proves that the expansion describes exactly the
plan's objects for that store.

Nothing here runs, plans or applies a design. The expansion is Genial's own
reading of the published design reference, not the Designs engine's render,
so it proves fidelity between the catalogue and the plan and nothing about
what Designs itself would do with the catalogue.

No canonical-graph change, no recipe key, no target write, no new dependency:
the YAML writer, the YAML reader and the schema validator are the small
subsets this catalogue needs, implemented on the standard library.
"""

from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import ipaddress
import json
import re
import sys

from . import __version__
from .model import DesignError, canonical, digest, hardware_catalog
from .retail import STORES

ARTIFACT = "netbox-designs-catalogue"
LABEL = "Designs catalogue"
SCHEMA_VERSION = 1
# The 4.7 data model carries the `owner` field every Genial infrastructure
# record declares; a 4.6 target would refuse the design at validation.
NETBOX_VERSIONS = ["4.7.*"]
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "catalog" / "designs" / "design.schema.json"
SCHEMA_URL = "https://netboxlabs.com/docs/designs/reference/design.schema.json"
DTL_RAW = "https://raw.githubusercontent.com/netbox-community/devicetype-library/"
MISSING = object()

# Genial kind -> Designs infra_type. A kind absent here has no Designs object
# type in the reviewed reference (or Designs excludes it) and is never declared.
INFRA_TYPES = {
    "site": "Site", "location": "Location", "rack": "Rack", "rack_type": "RackType",
    "rack_role": "RackRole", "manufacturer": "Manufacturer", "device_type": "DeviceType",
    "device_role": "DeviceRole", "platform": "Platform", "device": "Device",
    "interface": "Interface", "console_port": "ConsolePort",
    "console_server_port": "ConsoleServerPort", "power_port": "PowerPort",
    "power_outlet": "PowerOutlet", "module_bay": "ModuleBay", "module": "Module",
    "module_type": "ModuleType", "cable": "Cable", "vrf": "VRF", "prefix": "Prefix",
    "ip_address": "IPAddress", "ip_range": "IPRange", "vlan_group": "VLANGroup",
    "vlan": "VLAN", "role": "Role", "tenant": "Tenant", "site_group": "SiteGroup",
    "region": "Region", "tag": "Tag", "owner": "Owner", "power_panel": "PowerPanel",
    "power_feed": "PowerFeed", "circuit": "Circuit",
    "circuit_termination": "CircuitTermination", "circuit_type": "CircuitType",
    "provider": "Provider", "provider_account": "ProviderAccount",
    "provider_network": "ProviderNetwork", "wireless_lan_group": "WirelessLANGroup",
    "wireless_lan": "WirelessLAN", "contact": "Contact",
    "contact_assignment": "ContactAssignment", "circuit_group": "CircuitGroup",
    "circuit_group_assignment": "CircuitGroupAssignment",
    "journal_entry": "JournalEntry", "mac_address": "MACAddress",
}
KIND_OF = {value: key for key, value in INFRA_TYPES.items()}
PORT_KINDS = {"Interface": "interface", "ConsolePort": "console_port",
              "ConsoleServerPort": "console_server_port", "PowerPort": "power_port",
              "PowerOutlet": "power_outlet", "FrontPort": "front_port", "RearPort": "rear_port"}
# Natural keys (Designs' object-types reference and designs-overrides), in
# Genial field names. VRF is keyed by `rd` in Designs; Genial VRFs carry no
# rd, so a text reference reaches them by name (key, then name, then slug).
KEYS = {
    "site": ("slug",), "location": ("name", "site"), "rack": ("name", "site"),
    "rack_type": ("manufacturer", "model"), "rack_role": ("slug",), "manufacturer": ("slug",),
    "device_type": ("manufacturer", "model"), "device_role": ("slug",),
    "platform": ("name", "manufacturer"), "device": ("name", "site"),
    "interface": ("name", "device"), "console_port": ("name", "device"),
    "console_server_port": ("name", "device"), "power_port": ("name", "device"),
    "power_outlet": ("name", "device"), "module_bay": ("name", "device"),
    "module": ("module_bay",), "module_type": ("manufacturer", "model"),
    "cable": ("a", "b"), "vrf": ("name",), "prefix": ("prefix", "vrf"),
    "ip_address": ("address", "vrf"), "ip_range": ("start_address", "end_address", "vrf"),
    "vlan_group": ("slug",), "vlan": ("vid", "group"), "role": ("slug",), "tenant": ("slug",),
    "site_group": ("slug",), "region": ("slug",), "tag": ("slug",), "owner": ("name",),
    "power_panel": ("name", "site"), "power_feed": ("name", "power_panel"),
    "circuit": ("cid",), "circuit_termination": ("circuit", "term_side"),
    "circuit_type": ("slug",), "provider": ("slug",), "provider_account": ("account",),
    "provider_network": ("name", "provider"), "wireless_lan_group": ("slug",),
    "wireless_lan": ("ssid", "group"),
}
# Genial reference field -> the Designs field that carries it.
RENAMES = {("prefix", "scope_site"): "site", ("cable", "a"): "termination_a",
           ("cable", "b"): "termination_b", ("ip_address", "assigned_object"): "interface"}
RENAMES_BACK = {(kind, new): old for (kind, old), new in RENAMES.items()}
# Reference expressions for shared records the store does not own: the text
# Designs matches against the key, then the name, then the slug.
SLUG_REFERENCED = {"tenant", "region", "device_role", "rack_role", "platform", "role", "tag",
                   "circuit_type", "provider", "manufacturer", "vlan_group", "wireless_lan_group"}
NAME_REFERENCED = {"owner", "vrf"}

# Whole kinds the catalogue never declares, with the reason the manifest records.
OMITTED_KINDS = {
    "journal_entry": "Designs does not manage JournalEntry (managed-models.yaml excludes it).",
    "mac_address": "A unit's MAC addresses identify one installed device, not the store format.",
    "contact": "Contacts are estate desks; the per-site facilities desk needs a ContactAssignment, "
               "whose generic object reference the design reference does not document.",
    "contact_assignment": "ContactAssignment's generic object reference (object_type/object_id) "
                          "has no documented design syntax.",
    "circuit_group": "The WAN pair's CircuitGroupAssignment member is a generic reference with "
                     "no documented design syntax, so the group would be empty.",
    "circuit_group_assignment": "CircuitGroupAssignment's generic member reference has no "
                                "documented design syntax.",
}
# Fields the catalogue never writes on kinds it does declare.
OMITTED_FIELDS = {
    ("site", "custom_fields"): "Designs does not write custom fields (the site Service class).",
    ("device", "serial"): "A unit serial identifies one installed device, not the store format.",
    ("module", "serial"): "A PSU serial identifies one installed module, not the store format.",
    ("device", "tags"): "Device tags vary per unit of one type; a list-valued template "
                        "variable is not documented, so the device designs carry no tags.",
    ("vlan_group", "scope_site"): "VLANGroup scope has no flattened reference in Designs' "
                                  "model (only Prefix gains `site`).",
    ("wireless_lan", "scope_site"): "WirelessLAN scope has no flattened reference in Designs' model.",
    ("interface", "primary_mac_address"): "MAC addresses are omitted (see MACAddress).",
    ("cable", "comments"): "An AOC assembly serial identifies one installed cable, not the format.",
}
TEMPLATE_PORTS = ("interface", "console_port", "console_server_port", "power_port",
                  "power_outlet", "module_bay")
TEMPLATE_REASON = ("Created by the pinned Device Type Library template; a design must not "
                   "declare a device and the components its type creates in one plan "
                   "(Designs issue #1004), so the store design only references these ports "
                   "from cables and addresses.")
OPTIC_REASON = ("Installed optic in a Genial optics bay; the Device Type Library template "
                "defines no such bay, so neither the bay nor the module can be declared.")
PSU_CABLE_REASON = ("Power cable into a PSU module's inlet; Designs brings in a module type's "
                    "interfaces only, so the inlet cannot be referenced.")
ATTACHED_REASON = "Attached to an omitted record: {}"


class CatalogueError(DesignError):
    """The catalogue cannot be built or verified faithfully."""


# ---------------------------------------------------------------- YAML subset
_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAP_ENTRY = re.compile(r'^("(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_.-]*)\s*:(?:\s+|$)')
_INT = re.compile(r"^-?(0|[1-9][0-9]*)$")
_FLOAT = re.compile(r"^-?[0-9]+\.[0-9]+$")


def _scalar(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    if not isinstance(value, str):
        raise CatalogueError(f"cannot write a {type(value).__name__} as YAML")
    return json.dumps(value, ensure_ascii=False)


def _emit(value, indent, lines):
    pad = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            label = key if _PLAIN_KEY.match(key) else json.dumps(key)
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{label}:")
                _emit(item, indent + 2, lines)
            elif isinstance(item, dict):
                lines.append(f"{pad}{label}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}{label}: []")
            else:
                lines.append(f"{pad}{label}: {_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                sub = []
                _emit(item, indent + 2, sub)
                sub[0] = pad + "- " + sub[0][indent + 2:]
                lines.extend(sub)
            elif isinstance(item, (dict, list)):
                raise CatalogueError("empty mappings and nested lists are not written inside lists")
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        raise CatalogueError("a YAML document must be a mapping")


def dump_yaml(document):
    """Deterministic block YAML: every string double-quoted, lists indented."""
    lines = [f"# yaml-language-server: $schema={SCHEMA_URL}"]
    _emit(document, 0, lines)
    return "\n".join(lines) + "\n"


def _parse_scalar(text):
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise CatalogueError(f"bad double-quoted YAML scalar {text!r}: {exc}")
    if text.startswith("'"):
        if not text.endswith("'") or len(text) < 2:
            raise CatalogueError(f"bad single-quoted YAML scalar {text!r}")
        return text[1:-1].replace("''", "'")
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~", "Null"):
        return None
    if text == "{}":
        return {}
    if text == "[]":
        return []
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


def load_yaml(text):
    """Read the block-style subset ``dump_yaml`` writes (plus plain scalars)."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append([len(raw) - len(raw.lstrip(" ")), stripped])
    position = [0]

    def block():
        indent, text = lines[position[0]]
        return sequence(indent) if text == "-" or text.startswith("- ") else mapping(indent)

    def nested(indent):
        if position[0] < len(lines) and lines[position[0]][0] > indent:
            return block()
        return None

    def mapping(indent):
        result = {}
        while position[0] < len(lines):
            depth, text = lines[position[0]]
            if depth < indent:
                break
            match = _MAP_ENTRY.match(text) if depth == indent else None
            if match is None:
                raise CatalogueError(f"unexpected YAML line {text!r}")
            key = _parse_scalar(match.group(1)) if match.group(1).startswith('"') else match.group(1)
            rest = text[match.end():].strip()
            position[0] += 1
            result[key] = nested(indent) if rest == "" else _parse_scalar(rest)
        return result

    def sequence(indent):
        result = []
        while position[0] < len(lines):
            depth, text = lines[position[0]]
            if depth < indent:
                break
            if depth != indent or not (text == "-" or text.startswith("- ")):
                raise CatalogueError(f"unexpected YAML line {text!r}")
            rest = text[1:].strip()
            if rest == "":
                position[0] += 1
                result.append(nested(indent))
            elif _MAP_ENTRY.match(rest):
                lines[position[0]] = [indent + 2, rest]
                result.append(mapping(indent + 2))
            else:
                position[0] += 1
                result.append(_parse_scalar(rest))
        return result

    if not lines:
        raise CatalogueError("empty YAML document")
    document = block()
    if position[0] != len(lines):
        raise CatalogueError(f"unexpected YAML line {lines[position[0]][1]!r}")
    return document


# ------------------------------------------------------- JSON Schema subset
def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def _is_type(instance, name):
    return {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None}[name](instance)


def validate_schema(instance, schema, root=None, path="$"):
    """Errors for ``instance`` against the draft-2020 subset design.schema.json uses."""
    root = schema if root is None else root
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise CatalogueError(f"unsupported $ref {ref!r}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    errors = []
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else expected
        if not any(_is_type(instance, name) for name in names):
            errors.append(f"{path}: expected {' or '.join(names)}")
            return errors
    for key in schema.get("required", []):
        if not isinstance(instance, dict) or key not in instance:
            errors.append(f"{path}: missing required {key!r}")
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors += validate_schema(value, properties[key], root, f"{path}.{key}")
            elif extra is False:
                errors.append(f"{path}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                errors += validate_schema(value, extra, root, f"{path}.{key}")
    if isinstance(instance, list):
        if "items" in schema:
            for index, value in enumerate(instance):
                errors += validate_schema(value, schema["items"], root, f"{path}[{index}]")
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
    if "pattern" in schema and isinstance(instance, str) and not re.search(schema["pattern"], instance):
        errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")
    if "anyOf" in schema and not any(not validate_schema(instance, option, root, path)
                                     for option in schema["anyOf"]):
        errors.append(f"{path}: matches none of the allowed forms")
    if "oneOf" in schema and sum(not validate_schema(instance, option, root, path)
                                 for option in schema["oneOf"]) != 1:
        errors.append(f"{path}: must match exactly one allowed form")
    if "not" in schema and not validate_schema(instance, schema["not"], root, path):
        errors.append(f"{path}: matches a forbidden form")
    return errors


# ------------------------------------------------------------- plan reading
def _graph(plan):
    objects = {obj["key"]: obj for obj in plan["objects"]}
    referrers = defaultdict(list)
    by_kind = defaultdict(list)
    for key, obj in objects.items():
        by_kind[obj["kind"]].append(key)
        for field, value in obj["refs"].items():
            for target in (value if isinstance(value, list) else [value]):
                referrers[target].append((key, field))
    return objects, referrers, by_kind


def _select(plan, objects):
    """The first store of the smallest store format in permanent allocation order."""
    if plan.get("recipe", {}).get("profile") != "retail-chain":
        raise CatalogueError(f"{LABEL}: the store-format export reads a retail-chain plan; "
                             f"this plan is {plan.get('recipe', {}).get('profile')!r}")
    stores = [(key, obj["meta"]["store_format"]) for key, obj in objects.items()
              if obj["kind"] == "site" and obj["meta"].get("store_format")]
    if not stores:
        raise CatalogueError(f"{LABEL}: the plan has no store site")
    formats = [name for name in STORES if any(fmt == name for _, fmt in stores)]
    smallest = formats[0]
    allocations = plan.get("allocations", {})
    ordered = sorted((key for key, fmt in stores if fmt == smallest),
                     key=lambda key: (allocations.get(key.removeprefix("site/"), float("inf")), key))
    return ordered[0], smallest, ordered


OWNERSHIP_FIELDS = {"site", "location", "rack", "device", "module", "module_bay", "power_panel",
                    "power_port", "interface", "assigned_object", "scope_site", "termination",
                    "circuit", "a", "b", "group", "object", "member"}
PRIVATE_SHARED = {"circuit", "wireless_lan_group", "circuit_group", "contact"}


def _slice(objects, referrers, site_key):
    """Every object the store owns: the forward closure over ownership references,
    shared records referenced only from inside it, and the IP ranges inside
    its prefixes."""
    owned = {site_key}
    prefixes = []
    changed = True
    while changed:
        changed = False
        for key, obj in objects.items():
            if key in owned:
                continue
            for field, value in obj["refs"].items():
                if field not in OWNERSHIP_FIELDS:
                    continue
                targets = value if isinstance(value, list) else [value]
                if any(target in owned for target in targets):
                    owned.add(key)
                    changed = True
                    break
        for key in list(owned):
            for target in {t for value in objects[key]["refs"].values()
                           for t in (value if isinstance(value, list) else [value])}:
                if target in owned or objects[target]["kind"] not in PRIVATE_SHARED:
                    continue
                if objects[target]["kind"] == "circuit" or all(
                        referrer in owned for referrer, _ in referrers[target]):
                    owned.add(target)
                    changed = True
        prefixes = [ipaddress.ip_network(objects[key]["attrs"]["prefix"])
                    for key in owned if objects[key]["kind"] == "prefix"]
        for key, obj in objects.items():
            if obj["kind"] == "ip_range" and key not in owned:
                start = ipaddress.ip_interface(obj["attrs"]["start_address"]).ip
                if any(start in prefix for prefix in prefixes):
                    owned.add(key)
                    changed = True
    return owned


def _natural(key):
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r"(\d+)", key)]


# ---------------------------------------------------------- hardware policy
def _dtl_path(source):
    url = source.get("url", "")
    return url[len(DTL_RAW):].split("/", 1)[1] if url.startswith(DTL_RAW) else None


def _hardware(objects, slice_keys, catalog):
    """How each device type in the slice is expressed: a ``dtl:`` pin, an
    explicit DeviceType, or excluded with its reason."""
    commits = {source["commit"] for source in catalog["sources"].values()
               if source.get("url", "").startswith(DTL_RAW)}
    if len(commits) != 1:
        raise CatalogueError(f"{LABEL}: the catalog pins {len(commits)} devicetype-library commits; one is required")
    commit = commits.pop()
    types = {}
    for key in slice_keys:
        obj = objects[key]
        if obj["kind"] != "device":
            continue
        type_key = obj["refs"]["device_type"]
        if type_key in types:
            continue
        alias = type_key.split("/", 1)[1]
        model = catalog["models"].get(alias)
        if model is None or model["slug"] != objects[type_key]["attrs"]["slug"]:
            raise CatalogueError(f"{LABEL}: device type {type_key} is not the catalog model {alias!r}")
        chassis = [_dtl_path(catalog["sources"][source]) for source in model["source_ids"]
                   if "/device-types/" in catalog["sources"][source].get("url", "")]
        entry = {"alias": alias, "key": type_key, "model": model, "commit": commit,
                 "slug": model["slug"], "dtl": None, "excluded": None, "modules": []}
        if chassis:
            stem = chassis[0].rsplit("/", 1)[1].rsplit(".", 1)[0]
            if stem == model.get("part_number"):
                entry["dtl"] = f"dtl:{chassis[0]}@{commit}"
            else:
                normalised = any(alias in family["lines"].values() and name == "ap"
                                 for name, family in catalog["hardware_lines"].items())
                entry["excluded"] = (
                    f"the pinned source {stem}.yaml is not the {model['model']} the plan names"
                    + (" and the AP line's ports are normalised to eth0/wlan0/wlan1 (a declared "
                       "deviation from the library template)" if normalised else "")
                    + "; a dtl: pin would describe different ports, so the devices, their ports, "
                      "addresses and cables are left out of this slice.")
        for module in model.get("configured_modules", []):
            sources = [_dtl_path(source) for source in catalog["sources"].values()
                       if _dtl_path(source) == f"module-types/{model['manufacturer']}/{module['model']}.yaml"]
            if not sources:
                raise CatalogueError(f"{LABEL}: PSU {module['model']} has no pinned devicetype-library source")
            entry["modules"].append({"bay": module["bay"], "position": module["position"],
                                     "dtl": f"dtl:{sources[0]}@{commit}", "model": module["model"]})
        types[type_key] = entry
    return types


def _omissions(objects, referrers, slice_keys, hardware):
    """Which slice objects the catalogue leaves out, each with its reason."""
    reasons = {}
    for key in sorted(slice_keys):
        obj = objects[key]
        kind = obj["kind"]
        if kind in OMITTED_KINDS:
            reasons[key] = OMITTED_KINDS[kind]
        elif kind not in INFRA_TYPES:
            reasons[key] = f"No Designs object type for Genial kind {kind!r}."
        elif kind == "device" and hardware[obj["refs"]["device_type"]]["excluded"]:
            reasons[key] = hardware[obj["refs"]["device_type"]]["excluded"]
    for key in sorted(slice_keys):
        obj = objects[key]
        kind = obj["kind"]
        if key in reasons:
            continue
        device = obj["refs"].get("device")
        entry = hardware.get(objects[device]["refs"]["device_type"]) if device else None
        if kind in TEMPLATE_PORTS and entry and entry["dtl"]:
            if kind == "interface" and obj["attrs"].get("type") == "virtual":
                continue
            bays = {module["bay"] for module in entry["modules"]}
            if kind == "module_bay" and obj["attrs"]["name"] not in bays:
                reasons[key] = OPTIC_REASON
            else:
                reasons[key] = TEMPLATE_REASON
        elif kind == "module" and entry:
            bay = objects[obj["refs"]["module_bay"]]["attrs"]["name"]
            if bay not in {module["bay"] for module in entry["modules"]}:
                reasons[key] = OPTIC_REASON
        elif kind == "cable":
            for end in ("a", "b"):
                port = objects[obj["refs"][end]]
                if port["kind"] == "power_port" and port["refs"].get("module"):
                    reasons[key] = PSU_CABLE_REASON
    changed = True
    while changed:
        changed = False
        for key in sorted(slice_keys):
            if key in reasons:
                continue
            obj = objects[key]
            for field, value in obj["refs"].items():
                if field not in OWNERSHIP_FIELDS and field != "primary_ip4":
                    continue
                targets = value if isinstance(value, list) else [value]
                # Template-created ports are referenced, never attached to;
                # every other omission takes its dependants with it.
                hit = next((t for t in targets if reasons.get(t) not in (None, TEMPLATE_REASON)), None)
                if hit is not None and field != "primary_ip4":
                    root = reasons[hit]
                    reasons[key] = root if root.startswith("Attached to") else ATTACHED_REASON.format(
                        f"{INFRA_TYPES.get(objects[hit]['kind'], objects[hit]['kind'])} left out because {root[0].lower()}{root[1:]}")
                    changed = True
                    break
    return reasons


# ----------------------------------------------------------------- variables
def _site_variables(objects, slice_keys, site_key):
    site = objects[site_key]
    devices = sorted(key for key in slice_keys if objects[key]["kind"] == "device")
    names = [objects[key]["attrs"]["name"] for key in devices]
    stem = names[0].split("-", 1)[0]
    if any(not name.startswith(stem + "-") for name in names):
        raise CatalogueError(f"{LABEL}: devices at {site_key} do not share one hostname stem")
    domains = set()
    for key in slice_keys:
        obj = objects[key]
        if obj["kind"] == "ip_address" and obj["attrs"].get("dns_name"):
            host = objects[objects[obj["refs"]["assigned_object"]]["refs"]["device"]]["attrs"]["name"]
            if not obj["attrs"]["dns_name"].startswith(host + "."):
                raise CatalogueError(f"{LABEL}: {key} DNS name does not start with its device name")
            domains.add(obj["attrs"]["dns_name"][len(host) + 1:])
    if len(domains) != 1:
        raise CatalogueError(f"{LABEL}: expected one DNS domain at {site_key}, found {sorted(domains)}")
    racks = [key for key in slice_keys if objects[key]["kind"] == "rack"]
    if len(racks) != 1:
        raise CatalogueError(f"{LABEL}: a store format holds one rack; {site_key} has {len(racks)}")
    variables = {"site_name": site["attrs"]["name"], "site_slug": site["attrs"]["slug"],
                 "facility": site["attrs"]["facility"], "physical_address": site["attrs"]["physical_address"],
                 "latitude": site["attrs"]["latitude"], "longitude": site["attrs"]["longitude"],
                 "time_zone": site["attrs"]["time_zone"], "region": objects[site["refs"]["region"]]["attrs"]["slug"],
                 "hostname": stem,
                 "dns_domain": domains.pop(), "rack_asset_tag": objects[racks[0]]["attrs"]["asset_tag"]}
    for key in sorted(k for k in slice_keys if objects[k]["kind"] == "prefix"):
        obj = objects[key]
        network = ipaddress.ip_network(obj["attrs"]["prefix"])
        if obj["attrs"]["status"] == "container":
            variables["site_block"] = obj["attrs"]["prefix"]
        elif network.prefixlen == 24 and network.version == 4:
            segment = key.rsplit("/", 1)[1]
            variables[f"{segment}_net"] = str(network.network_address).rsplit(".", 1)[0]
        else:
            raise CatalogueError(f"{LABEL}: {key} is not a /24 client segment; the store format expects one")
    if "site_block" not in variables:
        raise CatalogueError(f"{LABEL}: {site_key} has no container site block")
    for key in sorted(k for k in slice_keys if objects[k]["kind"] == "circuit"):
        name = f"circuit_{_circuit_letter(key)}_install_date"
        if name in variables or "install_date" not in objects[key]["attrs"]:
            raise CatalogueError(f"{LABEL}: {key} needs one dated install per carrier letter")
        variables[name] = objects[key]["attrs"]["install_date"]
    return variables


def _circuit_letter(key):
    """``circuit/<site>/a/1`` -> ``a``: the carrier letter the recipe's WAN pair uses."""
    return key.split("/")[2]


class _Tokens:
    """Per-store values rewritten into variable expressions, longest first."""

    def __init__(self, variables):
        rules = []
        for name, value in variables.items():
            if name == "site_block":
                rules.append((re.compile(re.escape(value)), name))
            elif name.endswith("_net"):
                rules.append((re.compile(r"(?<![\d.])" + re.escape(value) + r"(?=\.\d)"), name))
            elif name == "hostname":
                rules.append((re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])"), name))
            elif name in ("site_slug", "site_name"):
                rules.append((re.compile(re.escape(value)), name))
        self.rules = sorted(rules, key=lambda rule: -len(rule[0].pattern))
        self.domain = variables["dns_domain"]

    def text(self, value):
        if not isinstance(value, str):
            return value
        for pattern, name in self.rules:
            value = pattern.sub("{{ variables.%s }}" % name, value)
        return value

    def dns(self, value):
        if value.endswith("." + self.domain):
            return self.text(value[:-len(self.domain)]) + "{{ variables.dns_domain }}"
        return self.text(value)


# ----------------------------------------------------------------- emission
def _var(name):
    return "{{ variables.%s }}" % name


def _endpoint(device_name, kind, port):
    return "{{ nbd.lookup.endpoint('%s', '%s', '%s') }}" % (device_name, kind, port)


class _Emitter:
    def __init__(self, objects, slice_keys, site_key, hardware, omitted, tokens):
        self.objects, self.slice, self.site, self.hardware = objects, slice_keys, site_key, hardware
        self.omitted, self.tokens = omitted, tokens
        self.site_expr = _var("site_slug")

    def ref(self, target, parent=None):
        """The reference expression for a plan object: ``{{ parent }}`` for the
        holder, text for shared records (matched by key, name or slug), a
        mapping of key fields otherwise; per-store values are templated."""
        if target == parent:
            return "{{ parent }}"
        obj = self.objects[target]
        kind = obj["kind"]
        attrs = obj["attrs"]
        if target == self.site:
            return self.site_expr
        if kind in SLUG_REFERENCED:
            value = attrs["slug"]
        elif kind in NAME_REFERENCED:
            value = attrs["name"]
        elif kind == "site_group":
            value = {"slug": attrs["slug"]}
        elif kind in ("location", "rack", "power_panel", "device"):
            value = {"name": attrs["name"], "site": self.ref(obj["refs"]["site"])}
        elif kind == "vlan":
            value = {"vid": attrs["vid"], "group": self.ref(obj["refs"]["group"])}
        elif kind == "circuit":
            value = {"cid": attrs["cid"]}
        elif kind == "provider_account":
            value = {"account": attrs["account"]}
        elif kind == "provider_network":
            value = {"name": attrs["name"], "provider": self.ref(obj["refs"]["provider"]),
                     "_type": "ProviderNetwork"}
        elif kind == "interface":
            value = {"name": attrs["name"], "device": self.ref(obj["refs"]["device"])}
        elif kind == "power_feed":
            value = {"name": attrs["name"], "power_panel": self.ref(obj["refs"]["power_panel"]),
                     "_type": "PowerFeed"}
        elif kind == "circuit_termination":
            value = {"circuit": self.ref(obj["refs"]["circuit"]), "term_side": attrs["term_side"],
                     "_type": "CircuitTermination"}
        else:
            raise CatalogueError(f"{LABEL}: no reference expression for {kind} {target}")
        return ({key: self.tokens.text(item) for key, item in value.items()}
                if isinstance(value, dict) else self.tokens.text(value))

    def end(self, target):
        obj = self.objects[target]
        if obj["kind"] in ("interface", "console_port", "console_server_port", "power_port", "power_outlet"):
            device = self.objects[obj["refs"]["device"]]["attrs"]["name"]
            return _endpoint(self.tokens.text(device), INFRA_TYPES[obj["kind"]], obj["attrs"]["name"])
        return self.ref(target)

    def fields(self, key, parent=None, skip=(), nested=None):
        """An object's declared fields: every attribute and reference the plan
        carries, minus the recorded omissions, with per-store values templated."""
        obj = self.objects[key]
        kind = obj["kind"]
        out = {"infra_type": INFRA_TYPES[kind]}
        identity = [field for field in KEYS.get(kind, ()) if field in obj["attrs"]]
        for field in identity + sorted(obj["attrs"].keys() - set(identity)):
            if (kind, field) in OMITTED_FIELDS or field in skip:
                continue
            value = obj["attrs"][field]
            out[field] = self.tokens.dns(value) if field == "dns_name" else self.tokens.text(value)
        for field in sorted(obj["refs"]):
            if (kind, field) in OMITTED_FIELDS or field in skip or field == "primary_ip4":
                continue
            value = obj["refs"][field]
            name = RENAMES.get((kind, field), field)
            if nested and field in nested:
                out[name] = nested[field]
            elif isinstance(value, list):
                out[name] = [self.ref(target, parent) for target in value]
            elif kind == "cable":
                out[name] = self.end(value)
            elif kind == "circuit_termination" and field == "termination":
                target = self.ref(value, parent)
                out[name] = ({"slug": target, "_type": "Site"} if self.objects[value]["kind"] == "site"
                             else target)
            else:
                out[name] = self.ref(value, parent)
        return out


def _device_design(entry, catalog_model):
    """One catalogue design per device type: the Device and, for a type whose
    pinned PSUs sit in template bays, its PSU modules. Nothing else: the
    template creates the ports, and a design must not declare both."""
    variables = {"name": "$USER", "site": "$USER", "role": "$USER", "location": "$USER",
                 "status": "active", "description": "$USER_OPTIONAL", "platform": "$USER_OPTIONAL",
                 "tenant": "$USER_OPTIONAL", "owner": "$USER_OPTIONAL", "rack": "$USER_OPTIONAL",
                 "position": "$USER_OPTIONAL", "face": "$USER_OPTIONAL"}
    device = {"infra_type": "Device", "name": _var("name"), "site": _var("site"), "role": _var("role"),
              "status": _var("status"), "description": _var("description"),
              "platform": _var("platform"), "tenant": _var("tenant"), "owner": _var("owner"),
              "location": {"name": _var("location"), "site": _var("site")},
              "rack": _var("rack"), "position": _var("position"), "face": _var("face")}
    if entry["dtl"]:
        device["device_type"] = entry["dtl"]
        description = f"{catalog_model['manufacturer']} {catalog_model['model']}, pinned to the Device Type Library"
        if entry["modules"]:
            device["modules"] = [
                {"infra_type": "Module", "device": "{{ parent }}",
                 "module_bay": "{{ nbd.nbt.get_module_bay(parent, '%s') }}" % module["position"],
                 "module_type": module["dtl"], "status": "active"}
                for module in entry["modules"]]
            description += f"; {len(entry['modules'])} x {entry['modules'][0]['model']} PSU modules"
    else:
        device["device_type"] = entry["explicit"]
        description = (f"{catalog_model['manufacturer']} {catalog_model['model']}: declared in full "
                       "because the catalog pins no Device Type Library source for it")
        variables.update({"uplink": "$USER", "vrf": "$USER", "address": "$USER", "dns_name": "$USER_OPTIONAL"})
        device["interfaces"] = [
            {"infra_type": "Interface", "name": port["name"], "type": port["type"], "enabled": True,
             "description": _var("uplink"), "device": "{{ parent }}", "vrf": _var("vrf"),
             "ip_addresses": [{"infra_type": "IPAddress", "address": _var("address"),
                               "status": "active", "dns_name": _var("dns_name"),
                               "vrf": _var("vrf"), "tenant": _var("tenant"), "owner": _var("owner"),
                               "interface": "{{ parent }}", "primary": True}]}
            for port in catalog_model["interfaces"]]
        device["power_ports"] = [{"infra_type": "PowerPort", "name": port["name"], "type": port["type"],
                                  "device": "{{ parent }}"} for port in catalog_model.get("power_ports", [])]
    return {"name": entry["slug"], "description": description, "netbox_versions": NETBOX_VERSIONS,
            "variables": variables, "infra": [device]}


def _store_design(emitter, store_format, variables, device_designs):
    objects, slice_keys, site_key = emitter.objects, emitter.slice, emitter.site
    omitted = emitter.omitted

    def keys(kind, predicate=lambda obj: True):
        return sorted((key for key in slice_keys if objects[key]["kind"] == kind
                       and key not in omitted and predicate(objects[key])), key=_natural)

    site = emitter.fields(site_key, skip=("name", "slug", "facility", "physical_address",
                                           "latitude", "longitude", "time_zone", "region"))
    site.update({"name": _var("site_name"), "slug": _var("site_slug"), "facility": _var("facility"),
                 "physical_address": _var("physical_address"), "latitude": _var("latitude"),
                 "longitude": _var("longitude"), "time_zone": _var("time_zone"), "region": _var("region")})
    site = {"infra_type": "Site", "name": site.pop("name"), "slug": site.pop("slug"), **site}
    site["locations"] = [emitter.fields(key, parent=site_key) for key in keys("location")]
    groups = []
    for group_key in keys("vlan_group"):
        group = emitter.fields(group_key, parent=site_key)
        group["vlans"] = [emitter.fields(key, parent=group_key)
                          for key in keys("vlan", lambda obj: obj["refs"]["group"] == group_key)]
        groups.append(group)
    site["vlan_groups"] = groups
    site["prefixes"] = [emitter.fields(key, parent=site_key) for key in keys("prefix")]
    site["ip_ranges"] = [emitter.fields(key, parent=site_key) for key in keys("ip_range")]
    panels = []
    for panel_key in keys("power_panel"):
        panel = emitter.fields(panel_key, parent=site_key)
        panel["power_feeds"] = [emitter.fields(key, parent=panel_key) for key in
                                keys("power_feed", lambda obj: obj["refs"]["power_panel"] == panel_key)]
        panels.append(panel)
    site["power_panels"] = panels
    racks = []
    for rack_key in keys("rack"):
        rack_type = emitter.fields(objects[rack_key]["refs"]["rack_type"])
        rack = emitter.fields(rack_key, parent=site_key, skip=("asset_tag",),
                              nested={"rack_type": rack_type})
        rack["asset_tag"] = _var("rack_asset_tag")
        rack["devices"] = [_include(emitter, key, device_designs, rack_key)
                           for key in keys("device", lambda obj: obj["refs"].get("rack") == rack_key)]
        racks.append(rack)
    site["racks"] = racks
    site["devices"] = [_include(emitter, key, device_designs, None)
                       for key in keys("device", lambda obj: not obj["refs"].get("rack"))]
    declared_devices = {key for key in keys("device")}
    explicit = {key for key in declared_devices if not emitter.hardware[objects[key]["refs"]["device_type"]]["dtl"]}
    svis = []
    for key in keys("interface", lambda obj: obj["refs"]["device"] in declared_devices - explicit):
        interface = emitter.fields(key)
        interface["ip_addresses"] = [_address(emitter, address, key) for address in
                                     keys("ip_address", lambda obj: obj["refs"]["assigned_object"] == key)]
        svis.append(interface)
    site["interfaces"] = svis
    declared_ports = {key for key in slice_keys if objects[key]["kind"] == "interface" and key not in omitted}
    site["ip_addresses"] = [
        _address(emitter, key, None) for key in keys("ip_address", lambda obj: (
            obj["refs"]["assigned_object"] not in declared_ports
            and objects[obj["refs"]["assigned_object"]]["refs"]["device"] in declared_devices - explicit))]
    wireless = []
    for group_key in keys("wireless_lan_group"):
        group = emitter.fields(group_key)
        group["wireless_lans"] = [emitter.fields(key, parent=group_key) for key in
                                  keys("wireless_lan", lambda obj: obj["refs"]["group"] == group_key)]
        wireless.append(group)
    site["wireless_lan_groups"] = wireless
    circuits = []
    for circuit_key in keys("circuit"):
        circuit = emitter.fields(circuit_key, skip=("install_date",))
        circuit["install_date"] = _var(f"circuit_{_circuit_letter(circuit_key)}_install_date")
        circuit["circuit_terminations"] = [emitter.fields(key, parent=circuit_key) for key in
                                           keys("circuit_termination", lambda obj: obj["refs"]["circuit"] == circuit_key)]
        circuits.append(circuit)
    site["circuits"] = circuits
    site["cables"] = [emitter.fields(key) for key in keys("cable")]
    inputs = {"site_name": "$USER", "site_slug": "$USER", "facility": "$USER", "physical_address": "$USER",
              "latitude": "$USER", "longitude": "$USER", "time_zone": "$USER", "region": "$USER",
              "hostname": "$USER",
              "rack_asset_tag": "$USER", "site_block": "$USER",
              "dns_domain": variables["dns_domain"]}
    for name in sorted(variables):
        if name.endswith("_net") or name.endswith("_install_date"):
            inputs[name] = "$USER"
    demand = STORES[store_format]
    return {"name": f"{store_format.title()}-format store",
            "description": (f"One {store_format}-format retail store: {demand['workstations']} back-office "
                            f"workstations, {demand['pos_terminals']} point-of-sale lanes, {demand['cameras']} "
                            f"cameras, their access pair, distribution pair, WAN edge pair, console server, "
                            f"rack PDUs, segments, addressing and WAN circuits. Access points are left out "
                            f"(see manifest.json)."),
            "netbox_versions": NETBOX_VERSIONS, "variables": inputs, "infra": [site]}


def _include(emitter, key, device_designs, rack_key):
    objects, tokens = emitter.objects, emitter.tokens
    obj = objects[key]
    entry = emitter.hardware[obj["refs"]["device_type"]]
    template = {"name": tokens.text(obj["attrs"]["name"]), "site": _var("site_slug"),
                "role": emitter.ref(obj["refs"]["role"]), "status": obj["attrs"]["status"],
                "description": tokens.text(obj["attrs"]["description"]),
                "location": objects[obj["refs"]["location"]]["attrs"]["name"],
                "tenant": emitter.ref(obj["refs"]["tenant"]), "owner": emitter.ref(obj["refs"]["owner"])}
    if "platform" in obj["refs"]:
        template["platform"] = emitter.ref(obj["refs"]["platform"])
    if rack_key:
        template["rack"] = "{{ parent }}"
        for field in ("position", "face"):
            if field in obj["attrs"]:
                template[field] = obj["attrs"][field]
    if not entry["dtl"]:
        ports = [k for k, o in objects.items() if o["kind"] == "interface" and o["refs"].get("device") == key]
        if len(ports) != 1:
            raise CatalogueError(f"{LABEL}: {key} declares {len(ports)} interfaces; the endpoint design holds one")
        port = objects[ports[0]]
        addresses = [k for k, o in objects.items() if o["kind"] == "ip_address"
                     and o["refs"].get("assigned_object") == ports[0]]
        if len(addresses) != 1:
            raise CatalogueError(f"{LABEL}: {ports[0]} carries {len(addresses)} addresses; the endpoint design holds one")
        address = objects[addresses[0]]
        template.update({"uplink": tokens.text(port["attrs"]["description"]),
                         "vrf": emitter.ref(port["refs"]["vrf"]),
                         "address": tokens.text(address["attrs"]["address"])})
        if address["attrs"].get("dns_name"):
            template["dns_name"] = tokens.dns(address["attrs"]["dns_name"])
    return {"design": f"nbd:{device_designs[entry['key']]}", "template_vars": template}


def _address(emitter, key, parent):
    fields = emitter.fields(key, parent=parent)
    device = emitter.objects[emitter.objects[emitter.objects[key]["refs"]["assigned_object"]]["refs"]["device"]]
    if device["refs"].get("primary_ip4") == key:
        fields["primary"] = True
    return fields


def _explicit_type(objects, type_key):
    obj = objects[type_key]
    manufacturer = objects[obj["refs"]["manufacturer"]]
    return {"infra_type": "DeviceType",
            "manufacturer": {"infra_type": "Manufacturer", **{k: manufacturer["attrs"][k] for k in sorted(manufacturer["attrs"])}},
            **{k: obj["attrs"][k] for k in sorted(obj["attrs"])}}


def create(plan):
    """The catalogue as {relative path: text}, plus its manifest and variables."""
    catalog = hardware_catalog()
    objects, referrers, _ = _graph(plan)
    site_key, store_format, same_format = _select(plan, objects)
    slice_keys = _slice(objects, referrers, site_key)
    hardware = _hardware(objects, slice_keys, catalog)
    for entry in hardware.values():
        if not entry["dtl"] and not entry["excluded"]:
            entry["explicit"] = _explicit_type(objects, entry["key"])
    omitted = _omissions(objects, referrers, slice_keys, hardware)
    variables = _site_variables(objects, slice_keys, site_key)
    tokens = _Tokens(variables)
    emitter = _Emitter(objects, slice_keys, site_key, hardware, omitted, tokens)
    device_designs = {key: f"devices/{entry['slug']}" for key, entry in sorted(hardware.items())
                      if not entry["excluded"]}
    designs = {}
    for key, name in device_designs.items():
        designs[f"catalogue/{name}.yaml"] = _device_design(hardware[key], catalog["models"][hardware[key]["alias"]])
    store_name = f"retail/{store_format}_store"
    designs[f"catalogue/{store_name}.yaml"] = _store_design(emitter, store_format, variables, device_designs)
    files = {path: dump_yaml(document) for path, document in designs.items()}
    counts = defaultdict(int)
    for key in slice_keys:
        if key not in omitted:
            counts[INFRA_TYPES[objects[key]["kind"]]] += 1
    groups = defaultdict(list)
    for key, reason in omitted.items():
        groups[(INFRA_TYPES.get(objects[key]["kind"], objects[key]["kind"]), reason)].append(key)
    manifest = {
        "artifact": ARTIFACT, "schema_version": SCHEMA_VERSION, "generator_version": __version__,
        "plan_sha256": digest(plan), "design_schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "netbox_versions": NETBOX_VERSIONS, "devicetype_library_commit": next(iter(hardware.values()))["commit"],
        "store": {"site": site_key, "slug": objects[site_key]["attrs"]["slug"],
                  "name": objects[site_key]["attrs"]["name"], "format": store_format,
                  "selection": "first store of the smallest store format in permanent allocation order",
                  "same_format_sites": same_format},
        "store_design": store_name,
        "device_designs": {name: {"device_type": key, "dtl": hardware[key]["dtl"],
                                  "explicit": hardware[key]["dtl"] is None}
                           for key, name in device_designs.items()},
        "declared": dict(sorted(counts.items())),
        "omitted": [{"infra_type": infra, "count": len(keys), "reason": reason, "examples": sorted(keys, key=_natural)[:3]}
                    for (infra, reason), keys in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))],
        "omitted_fields": [{"infra_type": INFRA_TYPES[kind], "field": field, "reason": reason}
                           for (kind, field), reason in sorted(OMITTED_FIELDS.items())
                           if any(objects[k]["kind"] == kind and field in objects[k]["attrs"] | objects[k]["refs"]
                                  for k in slice_keys if k not in omitted)],
        "referenced_not_declared": sorted({
            f"{INFRA_TYPES[objects[t]['kind']]}: {objects[t]['attrs'].get('name', objects[t]['attrs'].get('model'))}"
            for key in slice_keys if key not in omitted
            for value in objects[key]["refs"].values()
            for t in (value if isinstance(value, list) else [value])
            if t not in slice_keys and objects[t]["kind"] not in ("rack_type", "device_type")}),
        "verification": "offline fidelity of the catalogue to the plan; what Designs plans or applies is not verified",
    }
    files["manifest.json"] = canonical(manifest).decode() + "\n"
    files["deploy-vars.json"] = canonical(variables).decode() + "\n"
    files["README.md"] = _readme(manifest, variables)
    return {"files": files, "manifest": manifest, "variables": variables, "site": site_key,
            "format": store_format, "same_format": same_format}


def _readme(manifest, variables):
    store = manifest["store"]
    lines = [f"# {store['format'].title()}-format store as a NetBox Designs catalogue", "",
             f"Exported from plan `{manifest['plan_sha256'][:12]}` for {store['name']} (`{store['slug']}`), "
             f"the {store['selection']}.", "",
             f"`catalogue/{manifest['store_design']}.yaml` is the composed store design; it includes "
             "one device design per device type:", ""]
    for name, entry in manifest["device_designs"].items():
        lines.append(f"- `catalogue/{name}.yaml`: " + (f"`{entry['dtl']}`" if entry["dtl"] else
                                                       "explicit DeviceType (no pinned library source)"))
    lines += ["", "`deploy-vars.json` holds this store's values for the design's variables; "
              "`manifest.json` records per-type counts, every omitted record with its reason, and the "
              "estate records the design references without declaring.", "",
              "## Declared", ""]
    lines += [f"- {infra}: {count}" for infra, count in manifest["declared"].items()]
    lines += ["", "## Omitted", ""]
    lines += [f"- {entry['infra_type']} x{entry['count']}: {entry['reason']}" for entry in manifest["omitted"]]
    lines += [f"- {entry['infra_type']}.{entry['field']}: {entry['reason']}" for entry in manifest["omitted_fields"]]
    lines += ["", "## Not verified", "",
              "`designs-check` proves this catalogue describes exactly the plan's objects for the store, "
              "using Genial's own reading of the published design reference. Nothing here has been "
              "planned or applied by NetBox Designs; that needs a live catalogue and target.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- expansion
class _Ref:
    def __init__(self, target):
        self.target = target


class _Expanded:
    def __init__(self, infra_type, design, parent):
        self.infra_type, self.design, self.parent = infra_type, design, parent
        self.fields, self.plan_key = {}, None

    def label(self):
        identity = {k: v for k, v in self.fields.items() if not isinstance(v, (dict, list, _Ref))}
        return f"{self.infra_type} {json.dumps(identity, sort_keys=True)[:160]} ({self.design})"


_CALL = re.compile(r"^(nbd\.lookup\.endpoint|nbd\.nbt\.get_module_bay)\((.*)\)$", re.S)


def _args(text, env):
    out, buffer, quote = [], "", None
    for char in text:
        if quote:
            if char == quote:
                out.append(_render_text(buffer, env))
                buffer, quote = "", None
            else:
                buffer += char
        elif char in "'\"":
            if buffer.strip():
                raise CatalogueError(f"text around a quoted argument in {text!r}")
            quote, buffer = char, ""
        elif char == ",":
            if buffer.strip():
                out.append(_evaluate(buffer.strip(), env))
            buffer = ""
        else:
            buffer += char
    if buffer.strip():
        out.append(_evaluate(buffer.strip(), env))
    return out


def _evaluate(expr, env):
    expr = expr.strip()
    call = _CALL.match(expr)
    if call:
        args = _args(call.group(2), env)
        if call.group(1) == "nbd.lookup.endpoint":
            if len(args) != 3 or args[1] not in PORT_KINDS:
                raise CatalogueError(f"bad endpoint lookup {expr!r}")
            return {"_lookup": "endpoint", "device": args[0], "kind": args[1], "port": args[2]}
        if len(args) != 2 or not isinstance(args[0], _Ref):
            raise CatalogueError(f"bad module bay lookup {expr!r}")
        return {"_lookup": "module_bay", "device": args[0], "position": str(args[1])}
    if "|" in expr:
        expr, filter_name = (part.strip() for part in expr.split("|", 1))
    else:
        filter_name = None
    if expr == "parent":
        if env["parent"] is None:
            raise CatalogueError("{{ parent }} used where there is no containing object")
        value = _Ref(env["parent"])
    elif expr.startswith("parent."):
        if env["parent"] is None:
            raise CatalogueError("{{ parent.* }} used where there is no containing object")
        value = env["parent"].fields.get(expr[len("parent."):], MISSING)
    elif expr.startswith("variables."):
        name = expr[len("variables."):]
        if name not in env["variables"]:
            raise CatalogueError(f"design {env['design']!r} uses undeclared variable {name!r}")
        value = env["variables"][name]
    elif expr.startswith("loop."):
        raise CatalogueError("nbd_loop is not used by this catalogue and not expanded by this check")
    else:
        raise CatalogueError(f"unsupported expression {expr!r}")
    if filter_name is None or value is MISSING:
        return value
    if filter_name == "lower":
        return str(value).lower()
    if filter_name == "upper":
        return str(value).upper()
    if filter_name == "slugify":
        return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    if filter_name == "zeropad":
        return f"{int(value):02d}"
    raise CatalogueError(f"unsupported filter {filter_name!r}")


def _expressions(text):
    """(start, end, inner) for each outermost ``{{ }}``; an inner pair inside a
    function argument belongs to the outer expression, as the compute rack's
    cables are written."""
    index = 0
    while True:
        start = text.find("{{", index)
        if start < 0:
            return
        depth, cursor = 0, start
        while cursor < len(text):
            if text.startswith("{{", cursor):
                depth += 1
                cursor += 2
            elif text.startswith("}}", cursor):
                depth -= 1
                cursor += 2
                if depth == 0:
                    break
            else:
                cursor += 1
        if depth != 0:
            raise CatalogueError(f"unbalanced braces in {text!r}")
        yield start, cursor, text[start + 2:cursor - 2]
        index = cursor


def _render_text(text, env):
    pieces, last = [], 0
    for start, end, inner in _expressions(text):
        pieces.append(text[last:start])
        pieces.append(_evaluate(inner, env))
        last = end
    pieces.append(text[last:])
    values = [piece for piece in pieces if not (isinstance(piece, str) and piece == "")]
    if len(values) == 1 and not isinstance(values[0], str):
        return values[0]
    if any(piece is MISSING for piece in values):
        return MISSING
    if any(isinstance(piece, (_Ref, dict, list)) for piece in values):
        raise CatalogueError(f"text around an object or function in {text!r}")
    return "".join(str(piece) for piece in values)


def _render(value, env):
    if isinstance(value, str):
        return _render_text(value, env) if "{{" in value else value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            rendered = _render(item, env)
            if rendered is MISSING:
                return MISSING
            out[key] = rendered
        return out
    if isinstance(value, list):
        out = [_render(item, env) for item in value]
        return MISSING if any(item is MISSING for item in out) else out
    return value


def _variables_for(design, supplied, name):
    declared = design.get("variables") or {}
    variables = {}
    computed = []
    for key, value in declared.items():
        if value == "$USER":
            if key not in supplied:
                raise CatalogueError(f"design {name!r} requires variable {key!r}")
            variables[key] = supplied[key]
        elif value == "$USER_OPTIONAL":
            variables[key] = supplied.get(key, MISSING)
        elif isinstance(value, str) and "{{" in value:
            computed.append((key, value))
        else:
            variables[key] = supplied.get(key, value)
    for extra in supplied.keys() - declared.keys():
        raise CatalogueError(f"design {name!r} does not declare variable {extra!r}")
    for key, value in computed:
        variables[key] = _render_text(value, {"variables": variables, "parent": None, "design": name})
    return variables


def _is_item(value):
    return isinstance(value, dict) and ("infra_type" in value or "design" in value or "nbd_loop" in value)


def expand(catalogue, name, supplied):
    """Flatten design ``name`` with ``supplied`` variables into _Expanded objects."""
    out = []

    def item(entry, env, parent):
        if "nbd_loop" in entry:
            raise CatalogueError("nbd_loop is not used by this catalogue and not expanded by this check")
        local = {**env, "parent": parent}
        if "design" in entry:
            reference = entry["design"]
            if not reference.startswith("nbd:") or reference[4:] not in catalogue:
                raise CatalogueError(f"design {env['design']!r} includes unknown {reference!r}")
            template = {}
            for key, value in (entry.get("template_vars") or {}).items():
                rendered = _render(value, local)
                if rendered is not MISSING:
                    template[key] = rendered
            include(reference[4:], template)
            return None
        obj = _Expanded(entry["infra_type"], env["design"], parent)
        children = []
        for key, value in entry.items():
            if key == "infra_type":
                continue
            if _is_item(value):
                obj.fields[key] = _Ref(item(value, env, obj))
            elif isinstance(value, list) and any(_is_item(v) for v in value):
                children.append(value)
            else:
                rendered = _render(value, local)
                if rendered is not MISSING:
                    obj.fields[key] = rendered
        out.append(obj)
        for group in children:
            for child in group:
                item(child, env, obj)
        return obj

    def include(design_name, template):
        design = catalogue[design_name]
        env = {"variables": _variables_for(design, template, design_name), "parent": None, "design": design_name}
        for entry in design.get("infra") or []:
            item(entry, env, None)

    include(name, supplied)
    return out


# ------------------------------------------------------------ reconciliation
class _Plan:
    def __init__(self, plan):
        self.objects, self.referrers, self.by_kind = _graph(plan)
        self.ref_kinds = defaultdict(set)
        for obj in self.objects.values():
            for field, value in obj["refs"].items():
                for target in (value if isinstance(value, list) else [value]):
                    self.ref_kinds[(obj["kind"], field)].add(self.objects[target]["kind"])

    def resolve(self, value, kinds, what):
        if isinstance(value, _Ref):
            if value.target.plan_key is None:
                raise CatalogueError(f"{what}: refers to {value.target.label()}, which matched no plan object")
            if self.objects[value.target.plan_key]["kind"] not in kinds:
                raise CatalogueError(f"{what}: {value.target.label()} is not one of {sorted(kinds)}")
            return value.target.plan_key
        if isinstance(value, dict) and "_lookup" in value:
            return self._lookup(value, kinds, what)
        if isinstance(value, dict):
            spec = dict(value)
            declared = spec.pop("_type", None)
            if declared is not None:
                if declared not in KIND_OF or KIND_OF[declared] not in kinds:
                    raise CatalogueError(f"{what}: _type {declared!r} is not one of {sorted(kinds)}")
                kinds = {KIND_OF[declared]}
            matches = [key for kind in sorted(kinds) for key in self.by_kind[kind]
                       if self._matches(key, spec, what)]
        elif isinstance(value, str) and value.startswith("dtl:"):
            matches = self._dtl(value, kinds)
        elif isinstance(value, str):
            matches = self._text(value, kinds)
        else:
            raise CatalogueError(f"{what}: {value!r} is not a reference")
        if len(matches) != 1:
            raise CatalogueError(f"{what}: {json.dumps(value, default=str)} matches {len(matches)} plan objects of {sorted(kinds)}")
        return matches[0]

    def _dtl(self, reference, kinds):
        """A pinned library type stands for the plan type the catalog derived
        from that very file: the device type whose catalog entry cites the
        source and whose part number is the file stem, or the module type
        named by the file's manufacturer and model."""
        path, _, commit = reference[4:].partition("@")
        catalog = hardware_catalog()
        sources = {name for name, source in catalog["sources"].items()
                   if _dtl_path(source) == path and source.get("commit") == commit}
        if not sources:
            return []
        stem = path.rsplit("/", 1)[1].rsplit(".", 1)[0]
        if path.startswith("device-types/") and "device_type" in kinds:
            slugs = {model["slug"] for model in catalog["models"].values()
                     if sources & set(model.get("source_ids", [])) and model.get("part_number") == stem}
            return [key for key in self.by_kind["device_type"] if self.objects[key]["attrs"]["slug"] in slugs]
        if path.startswith("module-types/") and "module_type" in kinds:
            maker = path.split("/")[1]
            return [key for key in self.by_kind["module_type"]
                    if self.objects[key]["attrs"]["model"] == stem
                    and self.objects[self.objects[key]["refs"]["manufacturer"]]["attrs"]["name"] == maker]
        return []

    def _text(self, text, kinds):
        for pass_ in ("key", "name", "slug"):
            matches = []
            for kind in sorted(kinds):
                fields = KEYS.get(kind, ())
                field = fields[0] if pass_ == "key" and len(fields) == 1 else pass_
                if pass_ == "key" and len(fields) != 1:
                    continue
                matches += [key for key in self.by_kind[kind]
                            if self.objects[key]["attrs"].get(field) == text]
            if matches:
                return matches
        return []

    def _matches(self, key, spec, what):
        obj = self.objects[key]
        for field, wanted in spec.items():
            if field in obj["refs"]:
                try:
                    if self.resolve(wanted, self.ref_kinds[(obj["kind"], field)], what) != obj["refs"][field]:
                        return False
                except CatalogueError:
                    return False
            elif field in obj["attrs"]:
                if obj["attrs"][field] != wanted and str(obj["attrs"][field]) != str(wanted):
                    return False
            else:
                return False
        return True

    def _lookup(self, value, kinds, what):
        if value["_lookup"] == "endpoint":
            devices = [key for key in self.by_kind["device"] if self.objects[key]["attrs"]["name"] == value["device"]]
            if len(devices) != 1:
                raise CatalogueError(f"{what}: endpoint lookup names {len(devices)} devices called {value['device']!r}")
            kind = PORT_KINDS[value["kind"]]
            if kind not in kinds:
                raise CatalogueError(f"{what}: an endpoint lookup cannot stand for {sorted(kinds)}")
            ports = [key for key in self.by_kind[kind] if self.objects[key]["refs"].get("device") == devices[0]
                     and self.objects[key]["attrs"]["name"] == value["port"]]
            if len(ports) != 1:
                raise CatalogueError(f"{what}: {value['device']} has no {value['kind']} {value['port']!r} in the plan")
            return ports[0]
        device = self.resolve(value["device"], {"device"}, what)
        bays = [key for key in self.by_kind["module_bay"] if self.objects[key]["refs"]["device"] == device
                and str(self.objects[key]["attrs"].get("position")) == value["position"]]
        if len(bays) != 1 or "module_bay" not in kinds:
            raise CatalogueError(f"{what}: {device} has no module bay at position {value['position']!r}")
        return bays[0]


def reconcile(expanded, plan, slice_keys, omitted):
    """Prove the expansion describes exactly the slice: declared fields equal
    the plan's, every slice object is declared or omitted, nothing is extra."""
    graph = _Plan(plan)
    objects = graph.objects
    matched, declared_fields = {}, {}
    primaries = []
    for obj in expanded:
        kind = KIND_OF.get(obj.infra_type)
        if kind is None:
            raise CatalogueError(f"{obj.label()}: unsupported infra_type")
        if kind not in KEYS:
            raise CatalogueError(f"{obj.label()}: {obj.infra_type} has no natural key here")
        resolved = {}
        for field, value in obj.fields.items():
            if field == "primary" and kind == "ip_address":
                if value is True:
                    primaries.append(obj)
                continue
            name = RENAMES_BACK.get((kind, field), field)
            kinds = graph.ref_kinds.get((kind, name))
            if kinds:
                resolved[name] = ([graph.resolve(v, kinds, f"{obj.label()}.{field}") for v in value]
                                  if isinstance(value, list) else graph.resolve(value, kinds, f"{obj.label()}.{field}"))
            else:
                if isinstance(value, (_Ref, dict)):
                    raise CatalogueError(f"{obj.label()}.{field}: a reference where the plan holds no reference field")
                resolved[name] = value
        candidates = []
        for key in graph.by_kind[kind]:
            plan_obj = objects[key]
            if all(resolved.get(field) == plan_obj["attrs"].get(field, plan_obj["refs"].get(field))
                   for field in KEYS[kind]):
                candidates.append(key)
        if len(candidates) != 1:
            raise CatalogueError(f"{obj.label()}: identity {[resolved.get(f) for f in KEYS[kind]]} matches "
                                 f"{len(candidates)} plan objects")
        key = candidates[0]
        if key in matched:
            # Designs reads two declarations under one key as one object; the
            # endpoint design repeats its inline type per included device.
            if declared_fields[key] != resolved:
                raise CatalogueError(f"{obj.label()}: {key} is declared twice with different fields")
            obj.plan_key = key
            continue
        if key in slice_keys and key in omitted:
            raise CatalogueError(f"{obj.label()}: {key} is declared but the manifest lists it as omitted")
        obj.plan_key = key
        matched[key] = obj
        declared_fields[key] = resolved
        plan_obj = objects[key]
        expected = {}
        for field, value in plan_obj["attrs"].items():
            if (kind, field) not in OMITTED_FIELDS:
                expected[field] = value
        for field, value in plan_obj["refs"].items():
            if (kind, field) not in OMITTED_FIELDS and field != "primary_ip4":
                expected[field] = value
        for field in sorted(expected.keys() | resolved.keys()):
            if field not in resolved:
                raise CatalogueError(f"{obj.label()}: plan field {field!r} = {expected[field]!r} is not declared")
            if field not in expected:
                raise CatalogueError(f"{obj.label()}: declares {field!r} = {resolved[field]!r}, which the plan does not carry")
            if resolved[field] != expected[field] and not (
                    isinstance(expected[field], (int, float)) and not isinstance(expected[field], bool)
                    and str(resolved[field]) == str(expected[field])):
                raise CatalogueError(f"{obj.label()}: {field!r} is {resolved[field]!r} in the design but "
                                     f"{expected[field]!r} in the plan")
    for obj in primaries:
        interface = objects[objects[obj.plan_key]["refs"]["assigned_object"]]
        device = objects[interface["refs"]["device"]]
        if device["refs"].get("primary_ip4") != obj.plan_key:
            raise CatalogueError(f"{obj.label()}: marked primary but {device['key']} has primary_ip4 "
                                 f"{device['refs'].get('primary_ip4')!r}")
    for key in sorted(slice_keys):
        if key in omitted:
            continue
        if key not in matched:
            raise CatalogueError(f"plan object {key} ({objects[key]['kind']}) is neither declared nor listed as omitted")
        if objects[key]["kind"] == "device" and objects[key]["refs"].get("primary_ip4"):
            ip = objects[key]["refs"]["primary_ip4"]
            if ip not in omitted and not any(p.plan_key == ip for p in primaries):
                raise CatalogueError(f"{matched[key].label()}: primary address {ip} is not marked primary")
    extra = sorted(key for key in matched if key not in slice_keys and
                   objects[key]["kind"] not in ("rack_type", "device_type", "manufacturer"))
    if extra:
        raise CatalogueError(f"declared objects outside the store: {extra[:5]}")
    return {"declared": sum(key in slice_keys for key in matched),
            "declared_types": sum(key not in slice_keys for key in matched), "primaries": len(primaries)}


# --------------------------------------------------------------- build/check
def _catalogue_from(files):
    catalogue = {}
    for path, text in files.items():
        if path.startswith("catalogue/") and path.endswith(".yaml"):
            catalogue[path[len("catalogue/"):-len(".yaml")]] = load_yaml(text)
    return catalogue


def verify(files, plan):
    """Schema, binding, fidelity for the selected store and every other store
    of the same format, then byte-identical regeneration."""
    try:
        manifest = json.loads(files["manifest.json"])
        variables = json.loads(files["deploy-vars.json"])
    except (KeyError, ValueError) as exc:
        raise CatalogueError(f"{LABEL}: manifest.json or deploy-vars.json is missing or unreadable: {exc}")
    if manifest.get("artifact") != ARTIFACT:
        raise CatalogueError(f"{LABEL}: not a {ARTIFACT} artifact")
    if manifest.get("plan_sha256") != digest(plan):
        raise CatalogueError(f"{LABEL}: the catalogue is not bound to this canonical plan")
    if manifest.get("design_schema_sha256") != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
        raise CatalogueError(f"{LABEL}: the catalogue was checked against another design schema")
    schema = _schema()
    catalogue = _catalogue_from(files)
    if not catalogue:
        raise CatalogueError(f"{LABEL}: no designs under catalogue/")
    for name, design in sorted(catalogue.items()):
        errors = validate_schema(design, schema)
        if errors:
            raise CatalogueError(f"{LABEL}: catalogue/{name}.yaml fails design.schema.json: " + "; ".join(errors[:5]))
        if design.get("name") is None or design.get("netbox_versions") != NETBOX_VERSIONS:
            raise CatalogueError(f"{LABEL}: catalogue/{name}.yaml must name itself and list {NETBOX_VERSIONS}")
        for reference in _dtl_references(design):
            _check_dtl(reference, name)
    store_name = manifest.get("store_design")
    if store_name not in catalogue:
        raise CatalogueError(f"{LABEL}: manifest names store design {store_name!r}, which is not in the catalogue")
    catalog = hardware_catalog()
    objects, referrers, _ = _graph(plan)
    site_key, store_format, same_format = _select(plan, objects)
    if manifest.get("store", {}).get("site") != site_key:
        raise CatalogueError(f"{LABEL}: manifest store {manifest.get('store', {}).get('site')!r} is not the selected {site_key}")
    verified = {}
    for site in same_format:
        slice_keys = _slice(objects, referrers, site)
        hardware = _hardware(objects, slice_keys, catalog)
        omitted = _omissions(objects, referrers, slice_keys, hardware)
        site_variables = variables if site == site_key else _site_variables(objects, slice_keys, site)
        expanded = expand(catalogue, store_name, site_variables)
        try:
            verified[site] = reconcile(expanded, plan, slice_keys, omitted)
        except CatalogueError as exc:
            raise CatalogueError(f"{LABEL}: {site}: {exc}")
    regenerated = create(plan)["files"]
    for path in sorted(regenerated.keys() | files.keys()):
        if regenerated.get(path) != files.get(path):
            raise CatalogueError(f"{LABEL}: {path} differs from its regeneration; rebuild the catalogue")
    return {"store": site_key, "format": store_format, "designs": len(catalogue),
            "declared": verified[site_key]["declared"], "stores_verified": sorted(verified)}


def _dtl_references(value):
    if isinstance(value, str) and value.startswith("dtl:"):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _dtl_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _dtl_references(item)


def _check_dtl(reference, design_name):
    catalog = hardware_catalog()
    path, _, commit = reference[4:].partition("@")
    pinned = {_dtl_path(source): source["commit"] for source in catalog["sources"].values() if _dtl_path(source)}
    if not commit or pinned.get(path) != commit:
        raise CatalogueError(f"{LABEL}: catalogue/{design_name}.yaml pins {reference!r}, which is not a catalog source")


def build(plan_path, out):
    out = Path(out)
    if out.exists():
        raise CatalogueError(f"Output {out} already exists. Choose a new build path; existing catalogues are preserved.")
    plan = json.loads(Path(plan_path).read_text())
    result = create(plan)
    evidence = verify(result["files"], plan)
    out.mkdir(parents=True, exist_ok=False)
    for path, text in result["files"].items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (out / "checks.json").write_bytes(canonical({
        "status": "passed", "scope": "offline catalogue fidelity",
        "plan_sha256": result["manifest"]["plan_sha256"], **evidence}) + b"\n")
    return {"artifact": ARTIFACT, "output": str(out), **evidence}


def _read(directory):
    directory = Path(directory)
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "checks.json":
            files[str(path.relative_to(directory))] = path.read_text()
    return files


def check(directory, plan_path):
    plan = json.loads(Path(plan_path).read_text())
    files = _read(directory)
    evidence = verify(files, plan)
    try:
        checks = json.loads((Path(directory) / "checks.json").read_text())
    except (OSError, ValueError) as exc:
        raise CatalogueError(f"{LABEL}: checks.json is missing or unreadable: {exc}")
    if checks.get("status") != "passed" or checks.get("plan_sha256") != digest(plan):
        raise CatalogueError(f"{LABEL}: checks.json does not record a passed check bound to this plan")
    return {"artifact": ARTIFACT, "checks": "catalogue verified", **evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export one retail store format from a frozen plan as a NetBox Designs catalogue, and verify it")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="export the catalogue from a frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="re-derive and verify a saved catalogue against its bound plan")
    p.add_argument("directory", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.plan, args.out)
            print(f"{result['artifact']}: {result['format']} store {result['store']} -> {result['designs']} designs, "
                  f"{result['declared']} declared objects, verified for {len(result['stores_verified'])} "
                  f"same-format store(s) -> {result['output']}")
        else:
            result = check(args.directory, args.plan)
            print(f"{result['artifact']}: {result['checks']} — {result['format']} store {result['store']}, "
                  f"{result['designs']} designs, {result['declared']} declared objects, "
                  f"{len(result['stores_verified'])} same-format store(s)")
        return 0
    except (DesignError, OSError, ValueError, KeyError) as exc:
        print(f"Designs catalogue failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
