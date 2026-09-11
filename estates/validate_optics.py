"""Independent installed-optic, channel and incremental AC reservation checks.

Catalog identities and actual physical references are the inputs. Module-type
JSON, descriptive metadata and emitted contracts cannot establish compatibility.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import math
import re

from .model import hardware_catalog


_CAGES = {"1000base-x-sfp": ("sfp", 1000000),
          "10gbase-x-sfpp": ("sfpp", 10000000),
          "100gbase-x-qsfp28": ("qsfp28", 100000000)}
_LENGTH = {"m": 1, "cm": .01, "ft": .3048, "in": .0254, "km": 1000}


def analyze(plan, catalog=None):
    """Return findings and integer optical AC allowances keyed by actual device.

    Accept the complete catalog, its models mapping, or the pinned default. Count
    each installed optical module once, including inactive or uncabled inventory;
    health findings do not erase its reservation. The caller adds this separately
    rounded allowance to chassis and PoE allowances, never to a PSU's rating.
    """
    findings, extra = [], {}

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    if catalog is None:
        catalog = hardware_catalog()
    if not isinstance(plan, dict) or not isinstance(plan.get("objects"), list) or not isinstance(catalog, dict):
        report("optics-catalog", "plan", "Optics analysis requires canonical objects and a hardware catalog.")
        return findings, extra
    models = catalog.get("models", catalog)
    recipe = plan.get("recipe", {})
    native_inventory = any(isinstance(o, dict) and (o.get("kind") in {"device_type", "module", "module_bay"} or
        isinstance(o.get("refs"), dict) and ("device_type" in o["refs"] or "module" in o["refs"])) for o in plan["objects"])
    if ("models" not in catalog and isinstance(models, dict) and models and
            all(isinstance(m, dict) and "manufacturer" not in m and "model" not in m for m in models.values()) and
            not native_inventory and "generator_version" not in plan and "hardware_digest" not in plan and
            not (isinstance(recipe, dict) and "profile" in recipe)):
        # Legacy independently authored dimensional fixtures have no native
        # hardware identities or module inventory. They cannot prove optics,
        # and acquiring either native surface makes the normal checks mandatory.
        return findings, extra
    full = catalog if "models" in catalog else hardware_catalog()
    policy, sources = full.get("optics"), full.get("sources", {})
    try:
        parts = policy["parts"]
        multiplier = Decimal(policy["upstream_ac_allowance_multiplier"])
        minimum, maximum = policy["local_min_m"], policy["local_max_m"]
        valid = (isinstance(models, dict) and isinstance(parts, dict) and bool(parts) and
                 isinstance(policy["upstream_ac_allowance_multiplier"], str) and multiplier.is_finite() and
                 1 <= multiplier <= 10 and type(minimum) is int and type(maximum) is int and 0 < minimum <= maximum)
        for part in parts.values():
            valid = valid and (isinstance(part, dict) and
                all(isinstance(part[f], str) and part[f] for f in ("manufacturer", "model", "form_factor", "protocol", "medium", "connector")) and
                all(type(part[f]) is int and part[f] > 0 for f in ("rate_kbps", "reach_m", "power_reservation_mw")) and
                part["form_factor"] in {"sfp", "sfpp", "qsfp28"} and
                part["medium"] in {"smf", "aoc"} and isinstance(part["compatible_interfaces"], dict) and
                bool(part["compatible_interfaces"]) and
                all(alias in models and isinstance(names, list) and names and all(isinstance(n, str) for n in names)
                    for alias, names in part["compatible_interfaces"].items()) and
                (not part.get("assembly") or type(part.get("assembly_length_m")) is int and part["assembly_length_m"] > 0))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        valid = False
    if not valid:
        report("optics-catalog", "catalog", "Optics policy needs finite integer rates, reach and module mW, compatible host ports and an explicit AC allowance.")
        return findings, extra

    objects = {o["key"]: o for o in plan["objects"] if isinstance(o, dict) and isinstance(o.get("key"), str)
               and isinstance(o.get("kind"), str) and all(isinstance(o.get(f, {}), dict) for f in ("attrs", "refs"))}

    def obj(key):
        return objects.get(key, {}) if isinstance(key, str) else {}

    def attrs(key):
        return obj(key).get("attrs", {})

    def refs(key):
        return obj(key).get("refs", {})

    def kind(key):
        return obj(key).get("kind")

    def identity(key, expected_kind):
        manufacturer = refs(key).get("manufacturer")
        name, model = attrs(manufacturer).get("name"), attrs(key).get("model")
        return (name, model) if (kind(key) == expected_kind and kind(manufacturer) == "manufacturer" and
                                 isinstance(name, str) and isinstance(model, str)) else None

    kinds, children, cables, passive, occupancy, bindings = (defaultdict(list) for _ in range(6))
    for key, record in objects.items():
        kinds[record["kind"]].append(key)
        device, name = refs(key).get("device"), attrs(key).get("name")
        if isinstance(device, str) and isinstance(name, str):
            children[(device, record["kind"], name)].append(key)
        if record["kind"] == "cable":
            a, b = refs(key).get("a"), refs(key).get("b")
            if isinstance(a, str) and isinstance(b, str):
                cables[a].append((key, b)); cables[b].append((key, a))
        if record["kind"] == "front_port" and isinstance(refs(key).get("rear_port"), str):
            rear = refs(key)["rear_port"]
            passive[key].append(rear); passive[rear].append(key)
        if record["kind"] == "module" and isinstance(refs(key).get("module_bay"), str):
            occupancy[refs(key)["module_bay"]].append(key)
        if "module" in refs(key) and isinstance(refs(key)["module"], str):
            bindings[refs(key)["module"]].append(key)

    host_ids = {(m.get("manufacturer"), m.get("model")): alias for alias, m in models.items() if isinstance(m, dict)}
    part_ids = {(p["manufacturer"], p["model"]): p for p in parts.values()}
    hardware = {d: host_ids.get(identity(refs(d).get("device_type"), "device_type")) for d in kinds["device"]}
    configured = {(m.get("manufacturer"), c.get("model")) for m in models.values() if isinstance(m, dict)
                  for c in m.get("configured_modules", []) if isinstance(c, dict)}
    catalog_ports = {alias: {p["name"]: p for p in m.get("interfaces", [])}
                     for alias, m in models.items() if isinstance(m, dict)}

    def bay_type_identity(key):
        manufacturer = attrs(refs(key).get("manufacturer")).get("name")
        return (manufacturer, attrs(key).get("name")) if (kind(key) == "module_bay_type" and
                kind(refs(key).get("manufacturer")) == "manufacturer" and isinstance(manufacturer, str) and
                isinstance(attrs(key).get("name"), str)) else None

    module_parts, reserved, checked_types, serials = {}, defaultdict(int), set(), defaultdict(list)
    namespace = recipe.get("namespace") if isinstance(recipe, dict) else None
    for module in kinds["module"]:
        module_type = refs(module).get("module_type")
        actual = identity(module_type, "module_type")
        part = part_ids.get(actual)
        bay, owner = refs(module).get("module_bay"), refs(module).get("device")
        optical_bay = isinstance(attrs(bay).get("name"), str) and attrs(bay)["name"].startswith("Optic ")
        optical_binding = any(kind(p) in {"interface", "vm_interface"} for p in bindings[module])
        if part is None:
            if actual not in configured or optical_bay or optical_binding:
                report("optics-hardware", module, "Installed module must resolve to a reviewed optical part or configured PSU; descriptive module JSON cannot establish its identity.")
            continue
        module_parts[module] = part
        if module_type not in checked_types:
            checked_types.add(module_type)
            fields = ("protocol", "medium", "connector", "rate_kbps", "reach_m", "power_reservation_mw", "power_basis")
            try:
                expected = {f: part[f] for f in fields}
                expected["source"] = "\n".join(sources[s]["url"] for s in part["source_ids"])
                details = json.loads(attrs(module_type).get("attributes", ""))
                truthful = isinstance(details, dict) and all(type(details.get(f)) is type(v) and details.get(f) == v
                                                            for f, v in expected.items())
            except (KeyError, TypeError, ValueError):
                truthful = False
            if not truthful:
                report("optics-part-details", module_type, "Installed part's native JSON must accurately describe catalog protocol, media, reach, integer power reservation, basis and source URLs; those claims do not establish physical fit.")
            expected_bays = set()
            for host, names in part["compatible_interfaces"].items():
                vendor = models[host].get("manufacturer")
                for name in names:
                    cage = _CAGES.get(catalog_ports[host].get(name, {}).get("type"))
                    if cage is None or not isinstance(vendor, str):
                        report("optics-catalog", module_type, "Part compatibility must name real catalog optical cages and their manufacturer.")
                        continue
                    expected_bays.add((vendor, f"{vendor} {cage[0].upper()} optic cage"))
            supported = refs(module_type).get("module_bay_types")
            actual_bays = [bay_type_identity(t) for t in supported] if isinstance(supported, list) else []
            if set(actual_bays) != expected_bays or len(actual_bays) != len(expected_bays):
                report("optics-module", module_type, "Optical module type must advertise exactly its reviewed manufacturer/form-factor bay types, without unknown or duplicated fit claims.")
        serial = attrs(module).get("serial")
        prefix = "AOC" if part.get("assembly") else "OPT"
        if not isinstance(serial, str) or len(serial) > 50 or re.fullmatch(prefix + r"-[0-9a-f]{24}", serial) is None:
            report("optics-serial", module, "Installed optical inventory requires its bounded OPT-/AOC- assembly serial; native serial fields are at most 50 characters.")
        elif (not part.get("assembly") and isinstance(namespace, str) and len(bindings[module]) == 1 and
              serial != "OPT-" + hashlib.sha256(f"{namespace}/{bindings[module][0]}".encode()).hexdigest()[:24]):
            report("optics-serial", module, "Detachable optical serial must retain its actual interface and namespace identity.")
        if isinstance(serial, str):
            serials[serial].append(module)
        if kind(owner) == "device":
            reserved[owner] += part["power_reservation_mw"]
        else:
            report("optics-module", module, "Installed optical module requires an actual device owner for its power reservation.")
        if (kind(bay) != "module_bay" or refs(bay).get("device") != owner or "module" in refs(bay) or
                attrs(bay).get("enabled") is not True or occupancy.get(bay, []) != [module] or
                attrs(module).get("status") != "active" or len(bindings[module]) != 1 or
                kind(bindings[module][0]) != "interface"):
            report("optics-module", module, "Optical module needs one enabled cage directly owned by its chassis without a parent module, unique occupancy, active status and exactly one real interface binding.")

    for owner, mw in reserved.items():
        extra[owner] = int((Decimal(mw) * multiplier / 1000).to_integral_value(rounding=ROUND_CEILING))
    for serial, modules in serials.items():
        if not all(module_parts[m].get("assembly") for m in modules) and len(modules) != 1:
            report("optics-serial", modules[0], "Detachable optics require unique serials; only the two captive ends of one AOC share an assembly serial.")

    installed = {}
    for port in kinds["interface"] + kinds["vm_interface"]:
        owner, name = refs(port).get("device"), attrs(port).get("name")
        alias = hardware.get(owner) if isinstance(owner, str) else None
        spec = catalog_ports.get(alias, {}).get(name) if isinstance(name, str) else None
        actual_type = attrs(port).get("type")
        physical = spec.get("type") if spec else None
        bound = "module" in refs(port)
        relevant = (bound or bool(cables[port]) and (isinstance(actual_type, str) and actual_type in _CAGES or
                    isinstance(physical, str) and physical in _CAGES or
                    any(attrs(c).get("type") in ("smf", "aoc", "dac-active") for c, _ in cables[port])))
        if not relevant:
            continue
        if alias is None:
            report("optics-hardware", port, "Optical interface owner must resolve to its actual catalog manufacturer and model.")
        if (kind(port) != "interface" or spec is None or physical not in _CAGES or actual_type != physical or
                attrs(port).get("enabled") is not True or attrs(port).get("mgmt_only", False) or
                attrs(owner).get("status") != "active" or
                children[(owner, "interface", name)] != [port]):
            report("optics-port", port, "An occupied optical cage must be its unique enabled catalog interface on an active owner; fixed copper, management, radio and virtual ports cannot host optics.")
            continue
        module = refs(port).get("module")
        part = module_parts.get(module) if isinstance(module, str) else None
        if part is None:
            report("optics-module", port, "Every connected optical cage requires exactly one reviewed installed module.")
            continue
        installed[port] = part
        form, default_rate = _CAGES[physical]
        rate = attrs(port).get("speed", default_rate)
        if (type(rate) is not int or rate <= 0 or rate != part["rate_kbps"] or
                name not in part["compatible_interfaces"].get(alias, []) or
                part["form_factor"] != form and (part["form_factor"], form, rate) != ("sfp", "sfpp", 1000000)):
            report("optics-compatibility", port, "Actual module vendor/model must be reviewed for this named host cage, physical form and effective interface speed.")
        bay, module_type = refs(module).get("module_bay"), refs(module).get("module_type")
        vendor = models[alias]["manufacturer"]
        expected_type = vendor, f"{vendor} {form.upper()} optic cage"
        bay_types, supported = refs(bay).get("module_bay_types"), refs(module_type).get("module_bay_types")
        if (refs(module).get("device") != owner or refs(bay).get("device") != owner or
                attrs(bay).get("name") != f"Optic {name}" or
                attrs(bay).get("position") != name or
                children[(owner, "module_bay", f"Optic {name}")] != [bay] or
                not isinstance(bay_types, list) or len(bay_types) != 1 or bay_type_identity(bay_types[0]) != expected_type or
                not isinstance(supported, list) or not any(bay_type_identity(t) == expected_type for t in supported)):
            report("optics-module", port, "Interface, module and cage must share an owner and the unique named, positioned, manufacturer/form-factor bay type with advertised module fit.")

    def channel(start):
        current, seen, length, route = start, set(), 0, []
        site = refs(refs(start).get("device")).get("site")
        if kind(site) != "site":
            return None, route, length, "requires an actual local site owner"
        while current not in seen:
            seen.add(current)
            if len(cables[current]) != 1:
                return None, route, length, "requires exactly one cable at every channel endpoint"
            cable, peer = cables[current][0]
            route.append(cable)
            value, unit = attrs(cable).get("length"), attrs(cable).get("length_unit")
            if (attrs(cable).get("status") != "connected" or len(cables[peer]) != 1 or
                    not (type(value) is int or type(value) is float and math.isfinite(value)) or value <= 0 or
                    not isinstance(unit, str) or unit not in _LENGTH or value > maximum / _LENGTH[unit]):
                return None, route, length, "requires connected, singly occupied cables with finite supported lengths"
            length += value * _LENGTH[unit]
            if length > maximum:
                return None, route, length, "exceeds the complete local-channel length policy"
            if kind(peer) == "interface":
                if refs(refs(peer).get("device")).get("site") != site:
                    return None, route, length, "cannot join different sites with a short local optical channel"
                return peer, route, length, None
            if kind(peer) == "circuit_termination":
                circuit = refs(peer).get("circuit")
                if (refs(peer).get("termination") != site or kind(site) != "site" or
                        kind(circuit) != "circuit" or attrs(circuit).get("status") != "active"):
                    return None, route, length, "requires an active circuit handoff terminating at the local site"
                return peer, route, length, None
            if kind(peer) not in {"front_port", "rear_port"} or len(passive[peer]) != 1:
                return None, route, length, "requires a real peer or an unambiguous passive front/rear mapping"
            mate = passive[peer][0]
            front, rear = (peer, mate) if kind(peer) == "front_port" else (mate, peer)
            owner = refs(front).get("device")
            # ponytail: supported LC channels are simplex mappings (one rear
            # position); MPO breakout needs an explicit lane model before reuse.
            if (kind(front) != "front_port" or kind(rear) != "rear_port" or len(passive[mate]) != 1 or
                    kind(owner) != "device" or refs(rear).get("device") != owner or
                    not models.get(hardware.get(owner), {}).get("passive_ports") or
                    attrs(owner).get("status") != "active" or refs(owner).get("site") != site or
                    attrs(front).get("type") != "lc" or attrs(rear).get("type") != "lc" or
                    type(attrs(front).get("rear_port_position")) is not int or attrs(front)["rear_port_position"] != 1 or
                    type(attrs(rear).get("positions")) is not int or attrs(rear)["positions"] != 1):
                return None, route, length, "requires same-site LC ports with a single owned front/rear position"
            current = mate
        return None, route, length, "contains a loop rather than a terminated channel"

    assemblies = defaultdict(set)
    for port, part in installed.items():
        if not cables[port]:
            if part.get("assembly"):
                report("optics-assembly", port, "An installed captive AOC end requires its complete connected assembly.")
            continue
        peer, route, length, failure = channel(port)
        if failure:
            report("optics-path", port, f"Optical channel {failure}.")
            continue
        other = installed.get(peer)
        if kind(peer) == "interface" and other is None:
            report("optics-path", port, "Local optical peer needs an actual compatible installed module.")
        if (any(attrs(c).get("type") != part["medium"] for c in route) or
                other and (other["medium"] != part["medium"] or other["protocol"] != part["protocol"] or other["rate_kbps"] != part["rate_kbps"]) or
                part["medium"] == "smf" and (part["connector"] != "lc" or other and other["connector"] != "lc") or
                kind(peer) == "circuit_termination" and attrs(peer).get("port_speed") != part["rate_kbps"]):
            report("optics-media", port, "Complete channel media, local endpoint protocol/rate and LC connectors must match; the circuit boundary does not assert a remote optic.")
        if length < minimum or length > min(part["reach_m"], other["reach_m"] if other else part["reach_m"]):
            report("optics-reach", port, "Complete channel length must meet local policy and the smaller installed endpoint reach.")
        if part.get("assembly"):
            module, peer_module = refs(port).get("module"), refs(peer).get("module")
            serial = attrs(module).get("serial")
            if (len(route) != 1 or other is None or not other.get("assembly") or other != part or
                    length != part["assembly_length_m"] or part["connector"] != "captive" or
                    not isinstance(serial, str) or re.fullmatch(r"AOC-[0-9a-f]{24}", serial) is None or
                    attrs(peer_module).get("serial") != serial or module == peer_module or
                    attrs(module).get("asset_tag") and attrs(module).get("asset_tag") == attrs(peer_module).get("asset_tag")):
                report("optics-assembly", port, "AOC requires one exact-length cable, two matching captive modules and one shared assembly serial, with no duplicated asset tag or passive ports.")
            elif isinstance(namespace, str) and serial != "AOC-" + hashlib.sha256(f"{namespace}/{route[0]}".encode()).hexdigest()[:24]:
                report("optics-assembly", port, "AOC assembly serial must be bound to its actual cable identity and namespace.")
            if (attrs(route[0]).get("comments") != f"Assembly serial: {serial}\nOne active optical cable assembly with two captive ends; replace the complete assembly." or
                    any(attrs(refs(endpoint).get("module")).get("description") !=
                        f"Captive end on {attrs(endpoint).get('name')}; replace the complete AOC assembly" for endpoint in (port, peer))):
                report("optics-assembly-details", port, "AOC cable comments and both captive-end descriptions must identify this single serialized assembly and complete-assembly replacement, never independent transceivers.")
            if isinstance(serial, str):
                assemblies[serial].add(route[0])
    for serial, routes in assemblies.items():
        if len(routes) != 1:
            report("optics-assembly", sorted(routes)[0], f"AOC assembly serial {serial} is reused across different cables.")
    return findings, extra
