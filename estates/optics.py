"""Install catalog optics in occupied cages; reuse the existing interfaces.

Parts are selected by actual host, named cage, configured rate and cable media.
An AOC is one assembly with two captive ends, not two detachable transceivers.
No component templates or native module-delete lifecycle are implied.
"""

import hashlib
import json
import re

from . import naming
from .model import DesignError, vendor_serial


# Datasheet fields a pluggable's module type carries (name, JSON type).
SPEC_FIELDS = (("protocol", "string"), ("medium", "string"), ("connector", "string"),
               ("rate_kbps", "integer"), ("reach_m", "integer"))


def optic_serial(catalog, part, identity):
    """A label-shaped serial from the maker's authored format and a stable hash.

    One serial per identity: a transceiver's interface, or an AOC's cable, so
    both captive ends of one assembly share it.
    """
    formats = catalog["optics"]["serial_formats"]
    fmt = formats.get(part["manufacturer"], formats["Generic"])
    return vendor_serial(fmt, int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big"))


def owned_span_m(objects, endpoint, peers=None):
    """Metres of the operator's own fiber behind a cable end, else 0.

    The end is first traced through 1:1 passive panels (fibre.far_end): an
    aggregation UNI patched through the OSP panel reaches its access circuit.
    A third-party carrier's handoff is local: tracing stops there. Only a
    circuit the estate's own operator provides (provider/operator) carries the
    optic's light end to end — its recorded route `distance`, or, without one,
    the two terminating sites' great-circle distance times the authored route
    factor (the same rule the provider uses for span distances). ``peers``
    (cable end -> other end) may be precomputed by a caller tracing many ends.
    """
    from .fibre import far_end  # lazy: fibre imports the builders that import this module
    from .provider import ROUTE_FACTOR, km  # lazy: provider imports this module
    term = objects.get(far_end(objects, endpoint, peers), {})
    circuit = objects.get(term.get("refs", {}).get("circuit"), {})
    if term.get("kind") != "circuit_termination" or circuit.get("refs", {}).get("provider") != "provider/operator":
        return 0
    attrs = circuit["attrs"]
    if attrs.get("distance") and attrs.get("distance_unit") == "km":
        return round(attrs["distance"] * 1000)
    targets = [objects.get(objects.get(f"{circuit['key']}/{side}", {}).get("refs", {}).get("termination"), {}) for side in "AZ"]
    ends = [(objects.get(t["refs"]["site"], {}) if t.get("kind") == "location" else t).get("attrs", {}) for t in targets]
    points = [(e["latitude"], e["longitude"]) for e in ends if "latitude" in e and "longitude" in e]
    return round(km(*points) * ROUTE_FACTOR * 1000) if len(points) == 2 else 0


# Device types an estate keeps after their last device is replaced: the bank's
# acquired-branch line survives an access refresh (types are never deleted by
# a refresh). While an estate carries that lineage, the part definitions both
# it and the access line a refresh installs can take are kept too, so the
# refresh snapshot neither adds nor deletes a shared module type.
RETAINED = frozenset({"inherited-access"})


def retained_hosts(world):
    """Host aliases whose part definitions stay whether or not a cage holds one."""
    if not any(f"hardware/{alias}" in world.objects for alias in RETAINED):
        return frozenset()
    return RETAINED | {world.hardware_alias("access")}

_CAGES = {"1000base-x-sfp": ("sfp", 1000000),
          "10gbase-x-sfpp": ("sfpp", 10000000),
          "25gbase-x-sfp28": ("sfp28", 25000000),
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
                supported.add((bay_type, alias))
                bay_types[bay_type] = (host["manufacturer"], factor)
                lookup = (alias, name, part["rate_kbps"], part["medium"])
                if any(parts[other]["reach_m"] == part["reach_m"] for other in selections.get(lookup, ())):
                    raise DesignError(f"Optics catalog has ambiguous selection for {lookup}")
                selections.setdefault(lookup, []).append(part_id)
        part_bay_types[part_id] = sorted(supported)

    def cage_lookup(key, medium):
        """(alias, cage name, rate, medium) for an optical interface, else None."""
        interface = objects.get(key, {})
        cage = _CAGES.get(interface.get("attrs", {}).get("type")) if interface.get("kind") == "interface" else None
        if cage is None:
            return None
        alias = objects[interface["refs"]["device"]]["refs"]["device_type"].removeprefix("hardware/")
        return alias, interface["attrs"]["name"], interface["attrs"].get("speed", cage[1]), medium

    # Reach follows the run, not the cage: a single-mode jumper between two
    # cages in one room, short enough for multimode and with a reviewed
    # short-reach part on both ends, is OM4 multimode carrying SR/SR4 optics.
    # Building backbones, carrier handoffs and owned spans keep single-mode;
    # so does a link whose cage has no reviewed multimode part (FortiGate).
    # ponytail: direct jumpers only; an LC patch-panel channel stays single-mode
    # until the passive lane model covers MPO.
    mmf_reach = min(p["reach_m"] for p in parts.values() if p["medium"] == "mmf")
    for cable in {id(c): c for c in attached.values()}.values():
        a, b = cable["refs"]["a"], cable["refs"]["b"]
        rooms = {objects.get(objects.get(end, {}).get("refs", {}).get("device"), {}).get("refs", {}).get("location")
                 for end in (a, b)}
        if (cable["attrs"].get("type") == "smf" and cable["attrs"].get("length_unit") == "m"
                and cable["attrs"].get("length", mmf_reach + 1) <= mmf_reach
                and len(rooms) == 1 and None not in rooms
                and all(selections.get(cage_lookup(end, "mmf")) for end in (a, b))):
            cable["attrs"]["type"] = "mmf"
    peers = {end: cable["refs"]["b" if cable["refs"]["a"] == end else "a"] for end, cable in attached.items()}
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
        span = owned_span_m(objects, peers[key], peers)
        # The shortest reviewed reach that covers the owned span plus the local
        # cords the check adds to it; local channels (span 0) keep the catalog's
        # short-reach part. ponytail: the cords are bounded by the local-channel
        # maximum (100 m) rather than summed along the path; summing would pick
        # a longer part only for a span within 100 m of a reach boundary.
        part_id = min((p for p in selections.get((alias, attrs["name"], rate, cable["attrs"]["type"]), ())
                       if parts[p]["reach_m"] >= (span + catalog["optics"]["local_max_m"] if span else 0)),
                      key=lambda p: parts[p]["reach_m"], default=None)
        if part_id is None:
            raise DesignError(f"{key}: no reviewed optic for {models[alias]['model']} "
                              f"{attrs['name']} at {rate} kbps over {cable['attrs']['type']}"
                              + (f" reaching {span / 1000:g} km" if span else "") +
                              "; use a supported link design or extend the source-backed catalog")
        occupied.append((interface, device, cable, part_id, cage, span))

    # Bay-type fit follows the device types the finished estate keeps: those a
    # device uses, plus RETAINED lineage types (operations._prune keeps the
    # same set), so a cage form factor no kept type carries is not published.
    held = {obj["refs"].get("device_type") for obj in objects.values() if obj["kind"] == "device"}
    library = {alias for alias in models if f"hardware/{alias}" in objects
               and (f"hardware/{alias}" in held or alias in RETAINED)}
    installed_parts = {entry[3] for entry in occupied}
    kept = retained_hosts(world) & library
    for part_id in sorted(key for key, part in parts.items()
                          if key in installed_parts or set(part["compatible_interfaces"]) & kept):
        part = parts[part_id]
        part_bays = sorted({bay_type for bay_type, alias in part_bay_types[part_id] if alias in library})
        for bay_type in part_bays:
            if bay_type not in objects:
                manufacturer, factor = bay_types[bay_type]
                slug = re.sub(r"[^a-z0-9]+", "-", manufacturer.lower()).strip("-")
                world.add("module_bay_type", bay_type,
                          {"name": f"{manufacturer} {naming.OPTIC_CAGE_LABELS.get(factor, factor.upper())} optic cage",
                           "slug": f"{slug}-{factor}-optic", "color": naming.PALETTE.get(bay_type, "")},
                          {"manufacturer": f"manufacturer/{manufacturer}"})
        module_type = f"module-type/{part['manufacturer']}/{part['model']}"
        if module_type not in objects:
            profile = "module-profile/installed-optics"
            if profile not in objects:
                fields = {field: {"type": kind} for field, kind in SPEC_FIELDS}
                world.add("module_type_profile", profile,
                          {"name": "Installed pluggable optic",
                           "description": "Field-replaceable pluggable transceiver",
                           "schema": json.dumps({"type": "object", "properties": fields,
                                                 "required": sorted(fields)}, sort_keys=True)})
            # Datasheet facts only. Provenance (source URLs) and the planning
            # power reservation stay in catalog/hardware.json, where the checks
            # read them; a module type in NetBox reads like a vendor spec sheet.
            attributes = {field: part[field] for field, _ in SPEC_FIELDS}
            world.add("module_type", module_type,
                      {"model": part["model"], "attributes": json.dumps(attributes, sort_keys=True)},
                      {"manufacturer": f"manufacturer/{part['manufacturer']}",
                       "profile": profile, "module_bay_types": part_bays})

    for interface, device, cable, part_id, cage, span in occupied:
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
        serial = optic_serial(catalog, part, f"{world.recipe['namespace']}/{identity}")
        if assembly:
            cable["attrs"]["comments"] = (f"Assembly serial: {serial}\n"
                "One active optical cable assembly with two captive ends; replace the complete assembly.")
        description = (f"Captive end on {attrs['name']}; replace the complete AOC assembly"
                       if assembly else f"Installed {part['model']} on {attrs['name']}"
                       + (f" for a {span / 1000:.1f} km owned fiber run" if span else "")
                       + (f"; {part['installation_note']}" if span and part.get("installation_note") else ""))
        module = world.add("module", f"optics-module/{key}",
                           {"status": "active", "serial": serial, "description": description},
                           {"device": device["key"], "module_bay": bay,
                            "module_type": f"module-type/{part['manufacturer']}/{part['model']}"})
        interface["refs"]["module"] = module
