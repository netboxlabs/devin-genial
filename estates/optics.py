"""Install catalog optics in occupied cages; reuse the existing interfaces.

Parts are selected by actual host, named cage, configured rate and cable media.
An AOC is one assembly with two captive ends, not two detachable transceivers.
No component templates or native module-delete lifecycle are implied.
"""

import hashlib
import json
import re

from .model import DesignError


_CAGES = {"1000base-x-sfp": ("sfp", 1000000),
          "10gbase-x-sfpp": ("sfpp", 10000000),
          "100gbase-x-qsfp28": ("qsfp28", 100000000)}


def enrich(world):
    objects, catalog = world.objects, world.catalog
    parts, models = catalog["optics"]["parts"], catalog["models"]
    attached = {}
    for obj in objects.values():
        if obj["kind"] != "cable":
            continue
        for side in ("a", "b"):
            endpoint = obj["refs"][side]
            if endpoint in attached:
                raise DesignError(f"{endpoint}: multiple cables occupy one port")
            attached[endpoint] = obj

    # Index the finite catalog once; a large estate only performs direct lookups.
    selections, bay_types, part_bay_types = {}, {}, {}
    for part_id, part in parts.items():
        supported = set()
        for alias, names in part["compatible_interfaces"].items():
            host = models[alias]
            cages = {p["name"]: p for p in host["interfaces"]}
            for name in names:
                factor = _CAGES[cages[name]["type"]][0]
                bay_type = f"optics-bay-type/{host['manufacturer']}/{factor}"
                supported.add(bay_type)
                bay_types[bay_type] = (host["manufacturer"], factor)
                lookup = (alias, name, part["rate_kbps"], part["medium"])
                if lookup in selections:
                    raise DesignError(f"Optics catalog has ambiguous selection for {lookup}")
                selections[lookup] = part_id
        part_bay_types[part_id] = sorted(supported)
    occupied = []
    for interface in objects.values():
        if interface["kind"] != "interface" or interface["key"] not in attached:
            continue
        attrs, key = interface["attrs"], interface["key"]
        cage = _CAGES.get(attrs.get("type"))
        if cage is None:
            continue
        cable = attached[key]
        device = objects[interface["refs"]["device"]]
        alias = device["refs"]["device_type"].removeprefix("hardware/")
        rate = attrs.get("speed", cage[1])
        part_id = selections.get((alias, attrs["name"], rate, cable["attrs"]["type"]))
        if part_id is None:
            raise DesignError(f"{key}: no reviewed optic for {models[alias]['model']} "
                              f"{attrs['name']} at {rate} kbps over {cable['attrs']['type']}; "
                              "use a supported link design or extend the source-backed catalog")
        occupied.append((interface, device, cable, part_id, cage))

    # Keep the available parts catalog across replacement of the final chassis
    # using a part. Installed modules still follow only occupied cages; shared
    # type definitions follow the profile's fixed hardware-type library.
    for part_id in sorted(key for key, part in parts.items()
                          if any(f"hardware/{alias}" in objects for alias in part["compatible_interfaces"])):
        part, part_bays = parts[part_id], part_bay_types[part_id]
        # Definitions must not depend on which compatible hosts exist today.
        # Otherwise growing the estate would mutate shared ModuleType records.
        for bay_type in part_bays:
            if bay_type not in objects:
                manufacturer, factor = bay_types[bay_type]
                slug = re.sub(r"[^a-z0-9]+", "-", manufacturer.lower()).strip("-")
                world.add("module_bay_type", bay_type,
                          {"name": f"{manufacturer} {factor.upper()} optic cage",
                           "slug": f"{slug}-{factor}-optic", "color": "00838f"},
                          {"manufacturer": f"manufacturer/{manufacturer}"})
        module_type = f"module-type/{part['manufacturer']}/{part['model']}"
        if module_type not in objects:
            profile = "module-profile/installed-optics"
            if profile not in objects:
                fields = {field: {"type": kind} for field, kind in (
                    ("protocol", "string"), ("medium", "string"), ("connector", "string"),
                    ("rate_kbps", "integer"), ("reach_m", "integer"),
                    ("power_reservation_mw", "integer"), ("power_basis", "string"),
                    ("source", "string"))}
                world.add("module_type_profile", profile,
                          {"name": "Devin installed optics inventory",
                           "description": "Source-linked parts and explicit local-link planning reservations",
                           "schema": json.dumps({"type": "object", "properties": fields,
                                                 "required": sorted(fields)}, sort_keys=True)})
            attributes = {field: part[field] for field in (
                "protocol", "medium", "connector", "rate_kbps", "reach_m",
                "power_reservation_mw", "power_basis")}
            attributes["source"] = "\n".join(catalog["sources"][key]["url"] for key in part["source_ids"])
            world.add("module_type", module_type,
                      {"model": part["model"], "attributes": json.dumps(attributes, sort_keys=True)},
                      {"manufacturer": f"manufacturer/{part['manufacturer']}",
                       "profile": profile, "module_bay_types": part_bays})

    for interface, device, cable, part_id, cage in occupied:
        attrs, key = interface["attrs"], interface["key"]
        alias = device["refs"]["device_type"].removeprefix("hardware/")
        part = parts[part_id]
        bay_type = f"optics-bay-type/{models[alias]['manufacturer']}/{cage[0]}"
        bay = world.add("module_bay", f"optics-bay/{key}",
                        {"name": f"Optic {attrs['name']}", "enabled": True,
                         "position": attrs["name"]},
                        {"device": device["key"], "module_bay_types": [bay_type]})
        assembly = part.get("assembly", False)
        identity = cable["key"] if assembly else key
        serial = ("AOC-" if assembly else "OPT-") + hashlib.sha256(
            f"{world.recipe['namespace']}/{identity}".encode()).hexdigest()[:24]
        if assembly:
            cable["attrs"]["comments"] = (f"Assembly serial: {serial}\n"
                "One active optical cable assembly with two captive ends; replace the complete assembly.")
        description = (f"Captive end on {attrs['name']}; replace the complete AOC assembly"
                       if assembly else f"Installed {part['model']} on {attrs['name']}")
        module = world.add("module", f"optics-module/{key}",
                           {"status": "active", "serial": serial, "description": description},
                           {"device": device["key"], "module_bay": bay,
                            "module_type": f"module-type/{part['manufacturer']}/{part['model']}"})
        interface["refs"]["module"] = module
