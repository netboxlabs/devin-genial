"""Independent PoE inventory and capacity analysis; no builder or metadata input.

The supported catalog policy is Type 2 over an existing copper channel. Power
figures are reservations, not measurements, RF coverage or forwarding evidence.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math


_TYPE = "type2-ieee802.3at"
_COPPER = {"cat5e", "cat6", "cat6a", "cat7", "cat7a", "cat8"}
_LENGTH = {"m": 1, "cm": .01, "ft": .3048, "in": .0254, "km": 1000}


def analyze(plan, catalog):
    """Return ``(findings, extra_AC_watts_by_PSE_device)`` without mutation.

    ``catalog`` accepts the full catalog or its models mapping. Existing power
    validators add these independently derived integer watts to their chassis
    allowances. Invalid paths receive findings; remote power owners are never
    inferred from descriptive metadata, contracts, device names or IP addresses.
    """
    findings, extra = [], {}

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    if not isinstance(plan, dict) or not isinstance(plan.get("objects"), list) or not isinstance(catalog, dict):
        report("poe-catalog", "plan", "PoE analysis requires canonical objects and a hardware catalog.")
        return findings, extra
    models = catalog.get("models", catalog)
    if not isinstance(models, dict):
        report("poe-catalog", "catalog", "PoE analysis requires the catalog models mapping.")
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

    def number(value):
        return type(value) is int or type(value) is float and math.isfinite(value)

    kinds, children, cables, passive = (defaultdict(list) for _ in range(4))
    for key, record in objects.items():
        kinds[record["kind"]].append(key)
        device = refs(key).get("device")
        name = attrs(key).get("name")
        if isinstance(device, str) and (name is None or isinstance(name, str)):
            children[(device, record["kind"], name)].append(key)
        if record["kind"] == "cable":
            a, b = refs(key).get("a"), refs(key).get("b")
            if isinstance(a, str) and isinstance(b, str):
                cables[a].append((key, b)); cables[b].append((key, a))
        elif record["kind"] == "front_port":
            rear = refs(key).get("rear_port")
            if isinstance(rear, str):
                passive[key].append(rear); passive[rear].append(key)

    catalog_identity = {(m.get("manufacturer"), m.get("model")): m for m in models.values() if isinstance(m, dict)}
    recipe = plan.get("recipe", {})
    generated = ("generator_version" in plan or "hardware_digest" in plan or
                 isinstance(recipe, dict) and "profile" in recipe)
    flagged_owners = {refs(port).get("device") for port in kinds["interface"]
                      if isinstance(refs(port).get("device"), str) and
                      (attrs(port).get("poe_mode") or attrs(port).get("poe_type"))}
    hardware = {}
    for device in kinds["device"]:
        device_type = refs(device).get("device_type")
        manufacturer = refs(device_type).get("manufacturer")
        identity = attrs(manufacturer).get("name"), attrs(device_type).get("model")
        model = catalog_identity.get(identity) if all(isinstance(v, str) for v in identity) else None
        if kind(device_type) != "device_type" or kind(manufacturer) != "manufacturer" or model is None:
            # Legacy hand-authored generic fixtures predate device-type records.
            # Their metadata is not used to infer PDs or waive generated checks.
            if generated or "device_type" in refs(device) or device in flagged_owners:
                report("poe-hardware", device, "Device type must resolve to an actual catalog manufacturer/model; omitted hardware cannot waive PoE obligations.")
        else:
            hardware[device] = model

    pse, pds, eligible = {}, {}, {}
    for device, model in hardware.items():
        source, powered = model.get("poe_pse"), model.get("poe_pd")
        if source is not None:
            try:
                budgets = source["budget_by_active_supplies_mw"]
                multiplier = Decimal(source["upstream_ac_allowance_multiplier"])
                ports = model["access_ports"]
                max_supplies = len(model.get("configured_modules", [])) if "supply_model" in source else 1
                valid = (isinstance(source, dict) and source["type"] == _TYPE and
                         type(source["per_port_max_mw"]) is int and 0 < source["per_port_max_mw"] <= 30000 and
                         isinstance(budgets, dict) and set(budgets) == {str(n) for n in range(max_supplies + 1)} and
                         budgets["0"] == 0 and all(type(v) is int and v >= 0 for v in budgets.values()) and
                         all(budgets[str(n)] <= budgets[str(n + 1)] for n in range(max_supplies)) and
                         type(source["planning_supply_losses"]) is int and 0 <= source["planning_supply_losses"] < max_supplies and
                         isinstance(source["upstream_ac_allowance_multiplier"], str) and multiplier.is_finite() and multiplier >= 1 and
                         isinstance(ports, list) and ports and all(isinstance(p, str) for p in ports) and len(set(ports)) == len(ports))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                valid = False
            if not valid:
                report("poe-catalog", device, "PSE policy requires finite Type 2 per-port limits, actual supply-count budgets and an explicit AC allowance.")
                continue
            pse[device] = source
            extra[device] = 0
            for name in ports:
                actual = children[(device, "interface", name)]
                if len(actual) != 1:
                    report("poe-port", device, f"PSE requires exactly one catalog access interface named {name}.")
                for port in actual:
                    eligible[port] = "pse"
        if powered is not None:
            valid = (isinstance(powered, dict) and powered.get("required_type") == _TYPE and
                     isinstance(powered.get("interface"), str) and
                     type(powered.get("max_input_mw")) is int and 0 < powered["max_input_mw"] <= 25500 and
                     type(powered.get("pse_reservation_mw")) is int and
                     powered["max_input_mw"] <= powered["pse_reservation_mw"] <= 30000)
            if not valid:
                report("poe-catalog", device, "PD policy requires an explicit Type 2 input, bounded PD envelope and integer PSE reservation.")
                continue
            actual = children[(device, "interface", powered["interface"])]
            if len(actual) != 1:
                report("poe-port", device, "Powered device requires exactly one actual catalog PoE input interface.")
                continue
            pds[actual[0]] = powered
            eligible[actual[0]] = "pd"

    for port in kinds["interface"] + kinds["vm_interface"]:
        expected = eligible.get(port)
        if expected:
            if (attrs(port).get("poe_mode") != expected or attrs(port).get("poe_type") != _TYPE or
                    attrs(port).get("type") != "1000base-t" or attrs(port).get("enabled") is not True or
                    attrs(port).get("mgmt_only", False) or attrs(refs(port).get("device")).get("status") != "active"):
                report("poe-port", port, "Catalog PoE port needs its correct Type 2 mode, enabled 1G copper interface and active owner.")
        elif attrs(port).get("poe_mode") or attrs(port).get("poe_type"):
            report("poe-port", port, "PoE flags are only valid on catalog PSE access ports or the PD input, never management, optical, radio or virtual ports.")

    def copper_peer(start):
        current, seen, length = start, set(), 0
        while current not in seen:
            seen.add(current)
            if len(cables[current]) != 1:
                return None
            cable, peer = cables[current][0]
            value, unit = attrs(cable).get("length"), attrs(cable).get("length_unit")
            medium = attrs(cable).get("type")
            if (attrs(cable).get("status") != "connected" or not isinstance(medium, str) or medium not in _COPPER or
                    not number(value) or value <= 0 or not isinstance(unit, str) or unit not in _LENGTH or
                    value > 80 / _LENGTH[unit]):
                return None
            length += value * _LENGTH[unit]
            if length > 80 or len(cables[peer]) != 1:
                return None
            if kind(peer) == "interface":
                return peer
            if kind(peer) not in {"front_port", "rear_port"} or len(passive[peer]) != 1:
                return None
            mate = passive[peer][0]
            front, rear = (peer, mate) if kind(peer) == "front_port" else (mate, peer)
            owner = refs(front).get("device")
            if (kind(front) != "front_port" or kind(rear) != "rear_port" or len(passive[mate]) != 1 or
                    refs(rear).get("device") != owner or not isinstance(owner, str) or
                    not hardware.get(owner, {}).get("passive_ports") or
                    refs(owner).get("site") != refs(refs(start).get("device")).get("site") or
                    attrs(owner).get("status") != "active" or
                    attrs(front).get("type") != "8p8c" or attrs(rear).get("type") != "8p8c" or
                    attrs(front).get("rear_port_position", 1) != 1 or attrs(rear).get("positions", 1) != 1):
                return None
            current = mate
        return None

    reserved, port_reserved = defaultdict(int), defaultdict(int)
    for port, policy in pds.items():
        peer = copper_peer(port)
        device = refs(peer).get("device")
        if (eligible.get(peer) != "pse" or not isinstance(device, str) or device not in pse or
                refs(refs(port).get("device")).get("site") != refs(device).get("site")):
            report("poe-path", port, "PD requires one unambiguous connected copper channel of at most 80 m to an eligible PSE in its site.")
            continue
        reserved[device] += policy["pse_reservation_mw"]
        port_reserved[peer] += policy["pse_reservation_mw"]
        if port_reserved[peer] > pse[device]["per_port_max_mw"]:
            report("poe-port-budget", peer, "PD reservations exceed this actual PSE port's mW limit.")

    def power_peer(port):
        if len(cables[port]) != 1:
            return None
        cable, peer = cables[port][0]
        return peer if len(cables[peer]) == 1 and attrs(cable).get("type") == "power" and attrs(cable).get("status") == "connected" else None

    def source_panel(port, device):
        outlet = power_peer(port)
        pdu, inlet = refs(outlet).get("device"), refs(outlet).get("power_port")
        feed = power_peer(inlet)
        panel = refs(feed).get("power_panel")
        pdu_model = hardware.get(pdu, {}) if isinstance(pdu, str) else {}
        if (kind(outlet) != "power_outlet" or kind(inlet) != "power_port" or refs(inlet).get("device") != pdu or
                kind(feed) != "power_feed" or kind(panel) != "power_panel" or
                attrs(pdu).get("status") != "active" or attrs(feed).get("status") != "active" or
                attrs(feed).get("supply") != "ac" or attrs(feed).get("phase") != "single-phase" or
                any(not number(attrs(feed).get(f)) or attrs(feed)[f] <= 0 for f in ("voltage", "amperage", "max_utilization")) or
                attrs(feed)["max_utilization"] > 100 or
                any(refs(k).get("rack") != refs(device).get("rack") for k in (pdu, feed)) or
                any(refs(k).get("location") != refs(device).get("location") for k in (pdu, panel)) or
                refs(pdu).get("site") != refs(device).get("site") or
                refs(panel).get("site") != refs(device).get("site") or
                not isinstance(attrs(outlet).get("name"), str) or not isinstance(attrs(inlet).get("name"), str) or
                children[(pdu, "power_outlet", attrs(outlet).get("name"))] != [outlet] or
                children[(pdu, "power_port", attrs(inlet).get("name"))] != [inlet] or
                (attrs(outlet).get("name"), attrs(outlet).get("type")) not in
                    {(p["name"], p["type"]) for p in pdu_model.get("power_outlets", [])} or
                (attrs(inlet).get("name"), attrs(inlet).get("type")) not in
                    {(p["name"], p["type"]) for p in pdu_model.get("power_ports", [])}):
            return None
        return panel

    modules_by_bay = defaultdict(list)
    for module in kinds["module"]:
        if isinstance(refs(module).get("module_bay"), str):
            modules_by_bay[refs(module)["module_bay"]].append(module)
    for device, policy in pse.items():
        total = reserved[device]
        extra[device] = int((Decimal(total) * Decimal(policy["upstream_ac_allowance_multiplier"]) / 1000).to_integral_value(rounding=ROUND_CEILING))
        if not total:
            continue  # Unused PSE chassis health belongs to the existing PSU checks.
        model, panels = hardware[device], []
        configurations = model.get("configured_modules", []) if "supply_model" in policy else [None]
        for config in configurations:
            name = config["power_port"] if config else policy.get("fixed_supply_port")
            ports = children[(device, "power_port", name)]
            valid = len(ports) == 1
            port = ports[0] if valid else None
            expected = next((p for p in model.get("power_ports", []) if p["name"] == name), {})
            valid = valid and attrs(port).get("type") == expected.get("type") and attrs(device).get("status") == "active"
            if config:
                bays = children[(device, "module_bay", config["bay"])]
                modules = [m for b in bays for m in modules_by_bay[b]]
                bay = bays[0] if len(bays) == 1 else None
                module = modules[0] if len(modules) == 1 else None
                module_type = refs(module).get("module_type")
                bay_type = f"module-bay-type/{model['manufacturer']}/{config['model']}"
                valid = (valid and config["model"] == policy["supply_model"] and bay is not None and module is not None and
                         attrs(bay).get("enabled") is True and attrs(bay).get("position") == config["position"] and
                         refs(module).get("device") == device and attrs(module).get("status") == "active" and
                         refs(port).get("module") == module and kind(module_type) == "module_type" and
                         attrs(module_type).get("model") == policy["supply_model"] and
                         kind(refs(module_type).get("manufacturer")) == "manufacturer" and
                         attrs(refs(module_type).get("manufacturer")).get("name") == model["manufacturer"] and
                         refs(bay).get("module_bay_types") == [bay_type] and refs(module_type).get("module_bay_types") == [bay_type] and
                         kind(bay_type) == "module_bay_type" and kind(refs(bay_type).get("manufacturer")) == "manufacturer" and
                         attrs(refs(bay_type).get("manufacturer")).get("name") == model["manufacturer"])
            else:
                valid = valid and not refs(port).get("module")
            panel = source_panel(port, device) if valid else None
            if panel:
                panels.append(panel)
            else:
                report("poe-supply", device, f"PoE source {name} needs its actual active compatible supply, inlet and local powered PDU/feed path.")
        budgets = policy["budget_by_active_supplies_mw"]
        count = len(panels)
        port_limit = len(model["access_ports"]) * policy["per_port_max_mw"]
        available = min(budgets[str(count)], port_limit)
        if total > available:
            report("poe-budget", device, f"Reserved {total} mW exceeds {available} mW of currently available PoE delivery capacity.")
        losses = policy["planning_supply_losses"]
        if losses:
            surviving = min(budgets[str(max(0, count - losses))], port_limit)
            if total > surviving:
                report("poe-redundancy", device, f"Reserved {total} mW exceeds {surviving} mW after the planned supply loss; this is a design-margin deficit, not by itself a current PD outage.")
            if count > 1 and len(set(panels)) != count:
                report("poe-feed-diversity", device, "PoE supplies share an authored panel/feed domain; supply reserve alone cannot establish feed-loss survival.")
    return findings, extra
