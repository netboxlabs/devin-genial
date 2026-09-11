"""Installed physical inventory, serial access and a fictional liquid-cooled lab.

The vendor console/module inventory comes from the SHA-pinned hardware catalog.
Cooling follows NetBox v4.7 dcim/models/cooling.py and device_components.py:
feed -> rack derives supplied intakes; upstream intake/outflow links are acyclic.
No component templates or invented cable type is needed for the coolant path.
"""

from collections import defaultdict
import json
import math
from pathlib import Path


KINDS = {"console_port", "console_server_port", "device_bay", "inventory_item",
         "inventory_item_role", "module", "module_bay", "module_type",
         "module_type_profile", "module_bay_type", "virtual_chassis",
         "virtual_device_context", "cooling_source", "cooling_feed",
         "cooling_intake", "cooling_outflow"}


def enrich_site(site, *, demonstrations=True):
    """Add equipment before the ordinary management/power builders run."""
    w, ns = site.w, site.w.recipe["namespace"]
    for role in ("console-server", "laboratory", "stack") if demonstrations else ("console-server",):
        if f"role/{role}" not in w.objects:
            w.add("device_role", f"role/{role}",
                  {"name": f"{ns} {role}", "slug": f"{ns}-{role}", "color": "455a64"})
    if demonstrations and site.contract["kind"] == "dc":
        _laboratory(site)
        members = [site.device("access", f"stack-{i:02}", "stack") for i in (1, 2)]
        chassis = w.add("virtual_chassis", f"virtual-chassis/{site.id}",
                        {"name": f"{site.name}-service-stack", "domain": f"{ns}-{site.code}",
                         "description": "Two Cisco access chassis joined by their catalog StackWise ports; auxiliary service stack"},
                        {"master": members[0]})
        for position, device in enumerate(members, 1):
            w.obj(device)["refs"]["virtual_chassis"] = chassis
            w.obj(device)["attrs"].update(vc_position=position, vc_priority=100 - position)
        for port_a, port_b in (("StackPort1/1", "StackPort1/2"), ("StackPort1/2", "StackPort1/1")):
            cable = site.cable(site.interface(members[0], port_a), site.interface(members[1], port_b))
            w.obj(cable)["attrs"].pop("type")
            w.obj(cable)["attrs"]["description"] = "Catalog StackWise ports; cable medium left unspecified by the pinned schema"

    # Preserve each serial attachment's reservation across ordinary growth and
    # hardware refresh; retired slots remain reserved like rack/IP allocations.
    consoles = defaultdict(list)
    for device in list(site.devices):
        obj = w.obj(device)
        for port in w.catalog["models"][obj["meta"]["hardware"]].get("console_ports", []):
            if port["type"] == "rj-45":
                consoles[obj["refs"]["location"]].append(f"{device}/console_port/{port['name']}")
    for room, ports in sorted(consoles.items()):
        servers = {}
        for port in sorted(ports):
            slot = w.reserve(f"console-ports/{site.id}/{site.room_prefix(room)}", port, 960)
            block, position = divmod(slot, 48)
            if block not in servers:
                servers[block] = site.device("console-server", f"{site.room_prefix(room)}console-{block+1:02}",
                                             "console-server", location=room)
            server_port = f"{servers[block]}/console_server_port/Console{position+1:02}"
            w.obj(port)["attrs"]["speed"] = 115200
            cable = site.cable(port, server_port)
            w.obj(cable)["attrs"].pop("type")
            w.obj(cable)["attrs"]["description"] = "RJ45 asynchronous serial console; separate from Ethernet management"
    site.contract["assumptions"].append(
        "Console servers provide serial access to primary network equipment in the same room; spare USB and later management-switch console ports remain uncabled.")


def _laboratory(site):
    w = site.w
    parent = site.device("liquid-chassis", "analytics-enclosure-01", "laboratory", group="compute")
    child = site.device("liquid-blade", "analytics-blade-01", "laboratory", racked=False)
    rack, room = (w.obj(parent)["refs"][field] for field in ("rack", "location"))
    w.obj(child)["refs"].update(rack=rack, location=room)
    bay = w.add("device_bay", f"{parent}/bay/Blade1", {"name": "Blade1", "enabled": True},
                {"device": parent, "installed_device": child})
    w.obj(child)["meta"]["powered_by_enclosure"] = parent
    source = w.add("cooling_source", f"cooling/{site.id}/source",
                   {"name": "Lab chiller", "type": "chiller", "status": "active", "fluid_type": "water-glycol",
                    "cooling_capacity": 4, "description": "Reference closed-loop laboratory chiller; 4 kW rated planning capacity"},
                   {"site": site.key, "location": room})
    w.add("cooling_feed", f"cooling/{site.id}/feed",
          {"name": "Analytics loop", "status": "active", "cooling_capacity": 1,
           "max_flow": 12, "max_flow_unit": "lpm", "description": "Reference 1 kW supply-and-return loop to analytics rack"},
          {"cooling_source": source, "rack": rack, "tenant": site.tenant})
    attrs = {"type": "qdc", "diameter": 10, "diameter_unit": "mm"}
    intake = w.add("cooling_intake", f"{parent}/cooling/supply", attrs | {
        "name": "Facility supply", "max_flow": 12, "max_flow_unit": "lpm"}, {"device": parent})
    outflow = w.add("cooling_outflow", f"{parent}/cooling/blade", attrs | {"name": "Blade distribution"},
                    {"device": parent, "cooling_intake": intake})
    w.add("cooling_intake", f"{child}/cooling/supply", attrs | {
        "name": "Cold-plate supply", "max_flow": 6, "max_flow_unit": "lpm"},
        {"device": child, "cooling_outflow": outflow})
    w.add("virtual_device_context", f"{child}/context/diagnostic",
          {"name": "diagnostic", "identifier": 1, "status": "active",
           "description": "Blade diagnostic context; management addressing remains on its physical controller"},
          {"device": child, "tenant": site.tenant})
    role = "inventory-role/cooling"
    if role not in w.objects:
        ns = w.recipe["namespace"]
        w.add("inventory_item_role", role, {"name": f"{ns} Cooling assembly", "slug": f"{ns}-cooling-assembly", "color": "00838f"})
    assembly = w.add("inventory_item", f"{child}/inventory/cold-plate",
                     {"name": "Cold plate assembly", "part_id": "REF-COLDPLATE-01", "status": "active",
                      "description": "Serviceable cold plate; original reference design"},
                     {"device": child, "role": role, "manufacturer": "manufacturer/Devin Reference Designs"})
    w.add("inventory_item", f"{child}/inventory/coupling",
          {"name": "Supply coupling", "part_id": "REF-QDC-10MM", "status": "active"},
          {"device": child, "parent": assembly, "role": role,
           "manufacturer": "manufacturer/Devin Reference Designs", "component": f"{child}/cooling/supply"})
    site.contract["assumptions"].append(
        "One fictional analytics blade demonstrates enclosure power, device bays, replaceable cooling inventory and a rated coolant loop. The 400 W chassis allowance includes its blade; cooling capacities are planning ratings, not telemetry.")


def enrich(w):
    """Install the catalog's existing PSU configurations, including late devices."""
    # ModuleType identity is vendor/model globally. Keep these shared descriptive
    # definitions namespace-independent; installed bays/modules are device-scoped.
    profile = "module-profile/installed-psu"
    w.add("module_type_profile", profile, {"name": "Devin installed PSU inventory",
          "description": "Source-linked installed PSU configuration; no inferred power rating",
          "schema": json.dumps({"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]}, sort_keys=True)})
    for device in list(w.objects.values()):
        if device["kind"] != "device":
            continue
        spec = w.catalog["models"][device["meta"]["hardware"]]
        for config in spec.get("configured_modules", []):
            manufacturer = f"manufacturer/{spec['manufacturer']}"
            slug = config["model"].lower()
            module_type = f"module-type/{spec['manufacturer']}/{config['model']}"
            bay_type = f"module-bay-type/{spec['manufacturer']}/{config['model']}"
            if module_type not in w.objects:
                w.add("module_bay_type", bay_type, {"name": f"{config['model']} PSU bay", "slug": f"{slug}-psu", "color": "c62828"},
                      {"manufacturer": manufacturer})
                source_ids = [s for s in spec["source_ids"] if s.endswith("-psu")]
                w.add("module_type", module_type, {"model": config["model"],
                      "attributes": json.dumps({"source": w.catalog["sources"][source_ids[0]]["url"]}, sort_keys=True)},
                      {"manufacturer": manufacturer, "profile": profile, "module_bay_types": [bay_type]})
            key = device["key"]
            bay = w.add("module_bay", f"{key}/module-bay/{config['bay']}",
                        {"name": config["bay"], "position": config["position"], "enabled": True},
                        {"device": key, "module_bay_types": [bay_type]})
            module = w.add("module", f"{key}/module/{config['bay']}",
                           {"status": "active", "serial": f"SYN-PSU-{w.choose(key + config['bay'], 'serial', range(10**10)):010d}"},
                           {"device": key, "module_bay": bay, "module_type": module_type})
            w.obj(f"{key}/power/{config['power_port']}")["refs"]["module"] = module


def validate(plan, catalog=None):
    """Independent physical checks over links/containment, without builder state."""
    objects = {o["key"]: o for o in plan.get("objects", [])
               if isinstance(o, dict) and isinstance(o.get("key"), str) and isinstance(o.get("kind"), str)
               and all(isinstance(o.get(field, {}), dict) for field in ("attrs", "refs", "meta"))}
    if catalog is None:
        catalog = json.loads((Path(__file__).resolve().parent.parent / "catalog/hardware.json").read_text())["models"]
    children, by_kind, cables = defaultdict(list), defaultdict(list), defaultdict(list)
    modules_by_bay = defaultdict(list)
    findings = []

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) else 0

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    for key, obj in objects.items():
        by_kind[obj["kind"]].append(key)
        if isinstance(refs(key).get("device"), str):
            children[refs(key)["device"]].append(key)
        if obj["kind"] == "module":
            modules_by_bay[refs(key).get("module_bay") if isinstance(refs(key).get("module_bay"), str) else None].append(key)
        if obj["kind"] == "cable":
            for field in ("a", "b"):
                endpoint, peer = refs(key).get(field), refs(key).get("b" if field == "a" else "a")
                if isinstance(endpoint, str) and isinstance(peer, str):
                    cables[endpoint].append(peer)
    for device in by_kind["device"]:
        device_type = refs(device).get("device_type")
        model = catalog.get(device_type.removeprefix("hardware/"), {}) if isinstance(device_type, str) else {}
        if not plan.get("recipe", {}).get("profile") and not model:
            model = catalog.get(objects[device].get("meta", {}).get("hardware"), {})
        if plan.get("recipe", {}).get("profile") and (not model or
                objects.get(device_type, {}).get("kind") != "device_type" or
                attrs(device_type).get("model") != model.get("model") or
                objects.get(refs(device_type).get("manufacturer"), {}).get("kind") != "manufacturer" or
                attrs(refs(device_type).get("manufacturer")).get("name") != model.get("manufacturer")):
            report("equipment-hardware", device, "Installed hardware must resolve through its actual device type to the pinned manufacturer and model.")
        for kind in ("console_port", "console_server_port"):
            expected = {(p["name"], p["type"]) for p in model.get(kind + "s", [])}
            actual = {(attrs(p).get("name"), attrs(p).get("type")) for p in children[device] if objects[p]["kind"] == kind}
            if actual != expected:
                report("equipment-console-inventory", device, "Console inventory must match the pinned hardware configuration.")
        for config in model.get("configured_modules", []):
            bays = [b for b in children[device] if objects[b]["kind"] == "module_bay" and attrs(b).get("name") == config["bay"]]
            modules = [m for b in bays for m in modules_by_bay[b]]
            power = [p for p in children[device] if objects[p]["kind"] == "power_port" and attrs(p).get("name") == config["power_port"]]
            if len(bays) != 1 or len(modules) != 1 or len(power) != 1:
                report("equipment-installed-psu", device, "Configured PSU must occupy its bay and own the correct existing inlet.")
                continue
            bay, module, port = bays[0], modules[0], power[0]
            module_type = refs(module).get("module_type")
            bay_type = f"module-bay-type/{model['manufacturer']}/{config['model']}"
            if (refs(module).get("device") != device or refs(port).get("module") != module or
                    objects.get(module_type, {}).get("kind") != "module_type" or
                    attrs(module_type).get("model") != config["model"] or
                    objects.get(refs(module_type).get("manufacturer"), {}).get("kind") != "manufacturer" or
                    attrs(refs(module_type).get("manufacturer")).get("name") != model["manufacturer"] or
                    attrs(module).get("status") != "active" or attrs(bay).get("enabled") is not True or
                    attrs(bay).get("position") != config["position"] or
                    refs(bay).get("module_bay_types") != [bay_type] or
                    refs(module_type).get("module_bay_types") != [bay_type] or
                    objects.get(bay_type, {}).get("kind") != "module_bay_type" or
                    objects.get(refs(bay_type).get("manufacturer"), {}).get("kind") != "manufacturer" or
                    attrs(refs(bay_type).get("manufacturer")).get("name") != model["manufacturer"]):
                report("equipment-installed-psu", device, "Configured PSU needs its active sourced model, enabled compatible bay and actual inlet; descriptive metadata cannot waive these obligations.")
    for port in by_kind["console_server_port"]:
        for peer in cables[port]:
            if (objects.get(peer, {}).get("kind") != "console_port" or
                    refs(refs(port).get("device")).get("location") != refs(refs(peer).get("device")).get("location")):
                report("equipment-console-link", port, "Serial access must connect a device console in the same equipment room.")
    for bay in by_kind["device_bay"]:
        parent, child = refs(bay).get("device"), refs(bay).get("installed_device")
        if child and (attrs(refs(parent).get("device_type")).get("subdevice_role") != "parent" or
                      attrs(refs(child).get("device_type")).get("subdevice_role") != "child" or
                      any(refs(parent).get(f) != refs(child).get(f) for f in ("site", "rack", "location")) or
                      attrs(child).get("position") is not None):
            report("equipment-device-bay", bay, "Blade must be a child device in its parent enclosure's site/rack/room, without a separate U position.")
    for item in by_kind["inventory_item"]:
        for field, target in refs(item).items():
            if field in {"parent", "component"}:
                if refs(target).get("device") != refs(item).get("device"):
                    report("equipment-inventory-parent", item, "Inventory parent and component must belong to the same device.")
    for module in by_kind["module"]:
        bay = refs(module).get("module_bay")
        if refs(bay).get("device") != refs(module).get("device"):
            report("equipment-module-device", module, "Installed module and bay must belong to the same device.")
        allowed = refs(bay).get("module_bay_types", [])
        supported = refs(refs(module).get("module_type")).get("module_bay_types", [])
        allowed = {k for k in allowed if isinstance(k, str)} if isinstance(allowed, list) else set()
        supported = {k for k in supported if isinstance(k, str)} if isinstance(supported, list) else set()
        if not allowed & supported:
            report("equipment-module-fit", module, "Installed module must match one of the bay's allowed form factors.")
    for feed in by_kind["cooling_feed"]:
        source, rack = refs(feed).get("cooling_source"), refs(feed).get("rack")
        if (not rack or any(refs(source).get(f) != refs(rack).get(f) for f in ("site", "location")) or
                number(attrs(feed).get("cooling_capacity")) <= 0):
            report("equipment-cooling-feed", feed, "Rated coolant feed must serve a rack in the source's equipment room.")
    for source in by_kind["cooling_source"]:
        total = sum(number(attrs(f).get("cooling_capacity")) for f in by_kind["cooling_feed"] if refs(f).get("cooling_source") == source)
        if total > number(attrs(source).get("cooling_capacity")):
            report("equipment-cooling-capacity", source, "Allocated feed capacities exceed the source's rated kW capacity.")
    for intake in by_kind["cooling_intake"]:
        rack = refs(refs(intake).get("device")).get("rack")
        if not any(refs(f).get("rack") == rack for f in by_kind["cooling_feed"]):
            report("equipment-cooling-rack", intake, "Liquid-cooled device must occupy a rack served by a coolant feed.")
    for port in by_kind["cooling_intake"] + by_kind["cooling_outflow"]:
        seen, current = set(), port
        while current and current not in seen:
            seen.add(current)
            field = "cooling_outflow" if objects.get(current, {}).get("kind") == "cooling_intake" else "cooling_intake"
            upstream = refs(current).get(field)
            if upstream and field == "cooling_intake" and refs(upstream).get("device") != refs(current).get("device"):
                report("equipment-cooling-parent", current, "An outflow's parent intake must belong to its own device.")
            if upstream and field == "cooling_outflow" and refs(refs(upstream).get("device")).get("rack") != refs(refs(current).get("device")).get("rack"):
                report("equipment-cooling-rack", current, "The modeled coolant chain must remain within its served rack.")
            current = upstream
        if current:
            report("equipment-cooling-cycle", port, "Upstream coolant relationships must be acyclic.")
    for chassis in by_kind["virtual_chassis"]:
        members = [d for d in by_kind["device"] if refs(d).get("virtual_chassis") == chassis]
        positions = [attrs(d).get("vc_position") for d in members]
        if len(members) != 2 or len(set(positions)) != 2:
            report("equipment-stack-members", chassis, "The service stack requires two distinct member positions.")
        for device in members:
            peers = [refs(peer).get("device") for p in children[device] if "stackwise" in attrs(p).get("type", "") for peer in cables[p]]
            if len(peers) != 2 or any(peer not in members or peer == device for peer in peers):
                report("equipment-stack-links", device, "Each member needs two catalog StackWise links to the other member.")
    return findings
