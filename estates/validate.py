"""Independent checks of the finished estate; no imports from profile builders.

Contracts are finite assertions, not a forwarding or network-protocol simulator.
Physical traces cross cables and single-position passive port mappings only.
"""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from ipaddress import ip_interface, ip_network
import json
import math
from pathlib import Path
import re

from .validate_operations import validate as validate_operations
from .validate_networking import validate as validate_networking
from .validate_ipv6 import validate as validate_ipv6
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics
from .validate_wireless_context import validate as validate_wireless_context
from .validate_datacenter import validate as validate_datacenter
from .validate_school import validate as validate_school
from .equipment import validate as validate_equipment


# Independent expectations for the authored bank services. These are demo intent,
# not product deployment instructions or a claim about real bank applications.
BANK_SERVICE_PORTS = {
    "identity": (("identity", "tcp", 443), ("radius", "udp", 1812), ("radius", "udp", 1813)),
    "dns": (("dns", "tcp", 53), ("dns-udp", "udp", 53)),
    "teller-api": (("teller-api", "tcp", 443),),
    "atm-switch": (("atm-switch", "tcp", 443),),
    "ledger-db": (("ledger-db", "tcp", 5432),),
    "monitoring": (("monitoring", "tcp", 443),),
    "backup": (("backup", "tcp", 443),),
    "fraud-analysis": (("fraud-analysis", "tcp", 443),),
}
BANK_BRANCHES_PER_INSTANCE = {
    "identity": 100, "dns": 200, "teller-api": 40, "atm-switch": 60,
    "ledger-db": 100, "monitoring": 100, "backup": 100, "fraud-analysis": 80,
}
# Independent synthetic allowances for the bank's installed DC design. These
# are planning policy, not measured consumption or vendor performance ratings.
BANK_HOST_RESOURCES = {"vcpus": 64, "memory_mb": 262144, "disk_mb": 8000000}
BANK_POWER_WATTS = {"access": 120, "inherited-access": 120, "leaf": 160, "core": 220, "edge": 40,
                    "server": 250, "console-server": 40, "liquid-chassis": 400}
BANK_BRANCH_ENDPOINTS = {"s": (12, 2, 2, 2), "m": (36, 4, 4, 4), "l": (84, 6, 8, 8)}

BRANCH_DESIGNS = {"modern": ("access", 2), "inherited": ("inherited-access", 1), "refreshed": ("access", 2)}


def _catalog():
    return json.loads((Path(__file__).resolve().parent.parent / "catalog/hardware.json").read_text())["models"]


def _medium(kind, port_type):
    if kind in {"power_port", "power_outlet", "power_feed"}:
        return "power"
    if kind == "circuit_termination":
        return None  # The provider handoff does not declare a connector family.
    if kind in {"console_port", "console_server_port"}:
        return "console"
    if "stack" in port_type:
        return "stack"
    if port_type in {"8p8c", "rj-45"} or "base-t" in port_type:
        return "copper"
    if port_type in {"lc", "sc", "st", "mpo", "mpo-12", "mpo-24"} or any(s in port_type for s in ("sfpp", "sfp28", "sfp", "qsfp", "base-x")):
        return "fiber"
    return None


def _speed(port_type):
    match = re.match(r"(\d+)([mgt]?)base", port_type)
    if match:
        return int(match[1]) * {"": 1000, "m": 1000, "g": 1000000, "t": 1000000000}[match[2]]
    return None


def validate(plan):
    """Return stable ``{code, object, message}`` findings; never mutate the plan."""
    findings = []

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    if not isinstance(plan, dict) or not isinstance(plan.get("objects"), list):
        report("plan-format", "plan", "Expected an object with an objects list.")
        return findings
    recipe = plan.get("recipe", {})
    if not isinstance(recipe, dict):
        report("plan-profile", "plan", "recipe must be an object with a supported profile.")
        return findings
    generated = "generator_version" in plan or "hardware_digest" in plan or "profile" in recipe
    if generated and recipe.get("profile") not in ("regional-bank", "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone"):
        report("plan-profile", "plan", "Generated plans require a supported bank, data center, school, hospital or provider profile; missing or unsupported profiles cannot disable domain validation.")
        return findings
    objects = {}
    for obj in plan["objects"]:
        if not isinstance(obj, dict) or not isinstance(obj.get("key"), str) or not isinstance(obj.get("kind"), str):
            report("object-format", "plan", "Every object needs string key and kind fields.")
            continue
        key = obj["key"]
        if any(not isinstance(obj.get(field, {}), dict) for field in ("attrs", "refs", "meta")):
            report("object-format", key, "attrs, refs, and meta must be objects.")
            continue
        if key in objects:
            report("duplicate-key", key, "Object keys must be unique.")
            continue
        objects[key] = obj

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def meta(key):
        return objects.get(key, {}).get("meta", {}) if isinstance(key, str) else {}

    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None

    by_kind = defaultdict(list)
    children = defaultdict(list)
    scalar_refs = {"device", "virtual_machine", "site", "scope_site", "location", "rack", "role", "parent", "bridge", "cluster", "vrf", "group", "manufacturer", "device_type", "rear_port", "assigned_object", "primary_ip4", "primary_ip6", "termination", "circuit", "provider", "vlan", "untagged_vlan", "power_port", "power_panel", "a", "b", "tenant", "platform", "region", "site_group", "type", "interface", "virtual_circuit", "provider_network", "provider_account"}
    for key, obj in objects.items():
        by_kind[obj["kind"]].append(key)
        for field, value in refs(key).items():
            if field in scalar_refs and not isinstance(value, str):
                report("invalid-reference", key, f"{field} must be one object key.")
            values = value if isinstance(value, list) else [value]
            for target in values:
                if not isinstance(target, str):
                    report("invalid-reference", key, f"{field} must contain object keys.")
                elif target not in objects:
                    report("missing-reference", key, f"{field} references missing object {target}.")
                else:
                    children[(field, target)].append(key)
        for field in ("u_height", "position", "speed", "port_speed", "commit_rate", "positions", "rear_port_position", "vid", "vcpus", "memory", "disk", "length", "allocated_draw", "maximum_draw", "voltage", "amperage", "max_utilization"):
            value = attrs(key).get(field)
            if field == "position" and obj["kind"] == "module_bay":
                if not isinstance(value, str):
                    report("object-field", key, "Module bay position must be a string.")
                continue
            if value is not None and (type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or value < 0):
                report("object-field", key, f"{field} must be a nonnegative number.")
        for field in ("name", "type", "prefix", "address", "face"):
            if field in attrs(key) and not isinstance(attrs(key)[field], str):
                report("object-field", key, f"{field} must be a string.")
        if "hardware" in meta(key) and not isinstance(meta(key)["hardware"], str):
            report("object-field", key, "hardware metadata must be a catalog alias string.")
        resources = meta(key).get("resources", {})
        if not isinstance(resources, dict) or any(type(value) not in (int, float) or value < 0 for value in resources.values()):
            report("object-field", key, "resources metadata must contain nonnegative numbers.")
    if any(f["code"] in {"invalid-reference", "object-field"} for f in findings):
        return sorted(findings, key=lambda item: (item["code"], str(item["object"]), item["message"]))

    # The pinned NetBox4.7 OwnerSerializer requires group on create. The shared
    # estate policy supplies a real group rather than relying on omitted/null.
    for key in by_kind["owner"]:
        if kind(refs(key).get("group")) != "owner_group":
            report("owner-group", key, "An infrastructure owner must reference its owner group for the pinned REST target.")

    # NetBox v4.7.0 dcim.models.racks.Rack.asset_tag: max_length=50, unique=True.
    rack_tags = {}
    for key in by_kind["rack"]:
        tag = attrs(key).get("asset_tag")
        if tag is None or tag == "":
            continue
        if not isinstance(tag, str) or len(tag) > 50:
            report("rack-asset-tag", key, "Rack asset_tag must be a string of at most 50 characters for the pinned target.")
        elif tag in rack_tags:
            report("rack-asset-tag", key, f"Rack asset_tag must be globally unique; duplicates {rack_tags[tag]}.")
        else:
            rack_tags[tag] = key

    # Scope lookup follows only containment relationships, never arbitrary graph edges.
    site_cache = {}

    def site(key):
        if key in site_cache:
            return site_cache[key]
        seen = set()
        current = key
        while current in objects and current not in seen:
            seen.add(current)
            if kind(current) == "site":
                result = current
                break
            rel = refs(current)
            current = next((rel[f] for f in ("site", "scope_site", "device", "virtual_machine", "rack", "location", "group", "cluster") if isinstance(rel.get(f), str)), None)
        else:
            result = None
        site_cache[key] = result
        return result

    for typ in ("location", "region", "site_group"):
        complete = set()
        for key in by_kind[typ]:
            chain, current = set(), key
            while current and current not in complete:
                if current in chain:
                    report("containment-cycle", key, f"{typ} parent chain contains a cycle.")
                    break
                chain.add(current)
                parent = refs(current).get("parent")
                if parent and kind(parent) != typ:
                    report("containment-parent", current, f"Parent must be another {typ}.")
                    break
                if parent and typ == "location" and site(parent) != site(current):
                    report("location-site", current, "Parent location belongs to a different site.")
                current = parent
            complete.update(chain)
    for key in by_kind["device"] + by_kind["rack"] + by_kind["power_panel"]:
        location = refs(key).get("location")
        if location and (kind(location) != "location" or site(key) != site(location)):
            report("location-site", key, "Location must belong to this object's site.")
        rack = refs(key).get("rack")
        if rack and location and refs(rack).get("location") != location:
            report("rack-location", key, "Racked device and rack must occupy the same location.")

    natural = {}
    components = {"interface", "front_port", "rear_port", "power_port", "power_outlet", "console_port", "console_server_port", "inventory_item", "module_bay", "device_bay", "virtual_device_context", "cooling_intake", "cooling_outflow"}
    for key, obj in objects.items():
        typ, data, rel = obj["kind"], attrs(key), refs(key)
        name = data.get("name")
        if typ in components:
            scope = (rel.get("device"),)
        elif typ in {"vm_interface", "virtual_disk"}:
            scope = (rel.get("virtual_machine"),)
        elif typ == "rack":
            scope = (site(key), rel.get("location"))
        elif typ == "cooling_source":
            scope = (rel.get("site"),)
        elif typ == "cooling_feed":
            scope = (rel.get("cooling_source"),)
        elif typ in {"device", "location", "cluster"}:
            scope = (site(key), rel.get("tenant"), rel.get("parent"))
        elif typ == "virtual_machine":
            scope = (rel.get("cluster"), rel.get("tenant"))
        elif typ in {"device_type", "module_type", "rack_type"}:
            name, scope = data.get("model"), (rel.get("manufacturer"),)
        elif typ == "circuit":
            name, scope = data.get("cid"), (rel.get("provider"),)
        elif typ == "circuit_termination":
            name, scope = data.get("term_side"), (rel.get("circuit"),)
        elif typ == "vlan":
            name, scope = data.get("vid"), (rel.get("group"), site(key))
        elif typ == "service":
            scope = (rel.get("device"), rel.get("virtual_machine"))
        else:
            scope = ()
        if name is not None:
            identity = (typ, scope, str(name))
            if identity in natural:
                report("duplicate-name", key, f"Scoped identity duplicates {natural[identity]}.")
            natural[identity] = key

    try:
        catalog = _catalog()
    except (OSError, ValueError, KeyError) as exc:
        catalog = {}
        report("catalog-unavailable", "catalog", f"Cannot inspect hardware catalog: {exc}")
    poe_findings, poe_watts = analyze_poe(plan, catalog)
    findings.extend(poe_findings)
    optics_findings, optics_watts = analyze_optics(plan, catalog)
    findings.extend(optics_findings)
    for device in by_kind["device"]:
        alias = meta(device).get("hardware")
        model = catalog.get(alias)
        if not model:
            report("hardware-model", device, f"Unknown or missing hardware alias {alias!r}.")
            continue
        device_type = refs(device).get("device_type")
        if device_type and model.get("model") and (kind(device_type) != "device_type" or attrs(device_type).get("model") != model["model"]):
            report("hardware-device-type", device, "Referenced device type differs from the catalog hardware model.")
        for typ, field in (("interface", "interfaces"), ("power_port", "power_ports"), ("power_outlet", "power_outlets")):
            expected = {p["name"]: p for p in model.get(field, [])}
            actual = {}
            for key in children[("device", device)]:
                if kind(key) != typ:
                    continue
                data = attrs(key)
                if typ == "interface" and data.get("type") in {"virtual", "lag", "bridge"}:
                    continue
                name = data.get("name")
                actual[name] = key
                if name not in expected:
                    report("hardware-component", key, f"{name!r} is not in hardware model {alias}.")
                elif data.get("type") != expected[name]["type"]:
                    report("hardware-component-type", key, f"Expected {expected[name]['type']} from hardware model {alias}.")
                elif typ == "interface" and bool(data.get("mgmt_only", False)) != bool(expected[name].get("mgmt_only", False)):
                    report("hardware-management", key, "Management-only flag differs from hardware definition.")
            for missing in expected.keys() - actual.keys():
                report("hardware-inventory", device, f"Missing {typ} {missing} from hardware model {alias}.")
        if count := model.get("passive_ports"):
            for typ, prefix in (("front_port", "F"), ("rear_port", "R")):
                expected = {f"{prefix}{i:02}" for i in range(1, count+1)}
                actual = {attrs(key).get("name") for key in children[("device", device)] if kind(key) == typ}
                if expected != actual:
                    report("passive-inventory", device, f"{alias} requires all {count} catalog {typ}s, including unused positions.")

    # Per-face intervals are checked in sorted order, so large racks stay O(n log n).
    intervals = defaultdict(list)
    for device in by_kind["device"]:
        data, rel = attrs(device), refs(device)
        rack = rel.get("rack")
        if not rack:
            continue
        if kind(rack) != "rack":
            report("rack-reference", device, "rack must reference a rack object.")
            continue
        if rel.get("site") and site(device) != site(rack):
            report("rack-site", device, "Device and rack belong to different sites.")
        model = catalog.get(meta(device).get("hardware"), {})
        height = model.get("u_height", 0)
        position = data.get("position")
        if height == 0:
            if position is not None:
                report("rack-position", device, "Zero-U devices must not consume an elevation position.")
            continue
        if not isinstance(position, (int, float)) or position < 1:
            report("rack-position", device, "Racked equipment needs a positive position.")
            continue
        end = position + height
        if end > attrs(rack).get("u_height", 0) + 1:
            report("rack-bounds", device, "Equipment extends beyond rack height.")
        face = data.get("face")
        if face not in {"front", "rear"}:
            report("rack-face", device, "Racked equipment must declare front or rear face.")
            continue
        for side in ("front", "rear") if model.get("is_full_depth", True) else (face,):
            intervals[(rack, side)].append((position, end, device))
    overlap_pairs = set()
    for entries in intervals.values():
        last_end, last_key = 0, None
        for start, end, key in sorted(entries):
            if start < last_end:
                pair = tuple(sorted((key, last_key)))
                if pair not in overlap_pairs:
                    report("rack-overlap", key, f"Rack elevation overlaps {last_key}.")
                    overlap_pairs.add(pair)
            if end > last_end:
                last_end, last_key = end, key

    graph = defaultdict(set)
    occupied = {}
    term_kinds = {"interface", "front_port", "rear_port", "power_port", "power_outlet", "power_feed", "circuit_termination", "console_port", "console_server_port"}
    for circuit in by_kind["circuit"]:
        terms = [term for term in children[("circuit", circuit)] if kind(term) == "circuit_termination"]
        if sorted(attrs(term).get("term_side", "") for term in terms) != ["A", "Z"]:
            report("circuit-terminations", circuit, "A complete generated circuit needs exactly A and Z terminations.")
        for term in terms:
            target = refs(term).get("termination")
            if kind(target) not in {"site", "provider_network"}:
                report("circuit-attachment", term, "Termination must attach to a site or provider network.")
            elif kind(target) == "provider_network" and refs(target).get("provider") != refs(circuit).get("provider"):
                report("circuit-provider", term, "Provider network and circuit belong to different providers.")
    for cable in by_kind["cable"]:
        rel = refs(cable)
        a, b = rel.get("a"), rel.get("b")
        if not isinstance(a, str) or not isinstance(b, str) or a == b or kind(a) not in term_kinds or kind(b) not in term_kinds:
            report("cable-endpoint", cable, "Cable must join two distinct physical terminations.")
            continue
        for endpoint in (a, b):
            if kind(endpoint) == "interface" and attrs(endpoint).get("type") in {"virtual", "lag", "bridge"}:
                report("cable-endpoint", cable, "Logical interfaces cannot terminate a physical cable.")
            if endpoint in occupied:
                report("cable-occupancy", cable, f"{endpoint} is already occupied by {occupied[endpoint]}.")
            occupied[endpoint] = cable
        for endpoint, peer in ((a, b), (b, a)):
            if kind(endpoint) == "circuit_termination":
                target = refs(endpoint).get("termination")
                if kind(target) == "site" and site(peer) and site(peer) != target:
                    report("circuit-site", cable, "Circuit handoff cable terminates at a different site.")
        ma = _medium(kind(a), attrs(a).get("type", ""))
        mb = _medium(kind(b), attrs(b).get("type", ""))
        if ma and mb and ma != mb:
            report("cable-media", cable, f"Incompatible endpoint media: {ma} and {mb}.")
        if ma == mb == "power" and {kind(a), kind(b)} not in ({"power_port", "power_outlet"}, {"power_port", "power_feed"}):
            report("cable-media", cable, "Power cables must join a power port to an outlet or feed.")
        cable_type = attrs(cable).get("type", "")
        cable_medium = "copper" if cable_type.startswith("cat") else "fiber" if cable_type.startswith(("smf", "mmf", "dac", "aoc")) else None
        if cable_medium and any(m and m != cable_medium for m in (ma, mb)):
            report("cable-media", cable, f"Cable type {cable_type} is incompatible with its terminations.")
        sa = attrs(a).get("speed") or attrs(a).get("port_speed") or _speed(attrs(a).get("type", ""))
        sb = attrs(b).get("speed") or attrs(b).get("port_speed") or _speed(attrs(b).get("type", ""))
        if sa and sb and sa != sb and not (ma == mb == "copper"):
            report("cable-speed", cable, f"Endpoint speeds differ: {sa} and {sb} kbps; declare the intended interface speeds.")
        graph[a].add(b)
        graph[b].add(a)
    mapped_rears = {}
    for front in by_kind["front_port"]:
        rear = refs(front).get("rear_port")
        if kind(rear) != "rear_port" or refs(front).get("device") != refs(rear).get("device"):
            report("passive-mapping", front, "Front port must map to a rear port on the same device.")
            continue
        position = attrs(front).get("rear_port_position", 1)
        if not isinstance(position, int) or position < 1 or position > attrs(rear).get("positions", 1):
            report("passive-mapping", front, "Rear-port position is outside the available positions.")
            continue
        identity = (rear, position)
        if identity in mapped_rears:
            report("passive-mapping", front, f"Rear-port position also maps to {mapped_rears[identity]}.")
        mapped_rears[identity] = front
        if attrs(rear).get("positions", 1) != 1:
            report("unsupported-passive-mapping", front, "v1 trace checks support single-position rear ports only.")
            continue
        if _medium("front_port", attrs(front).get("type", "")) != _medium("rear_port", attrs(rear).get("type", "")):
            report("passive-mapping", front, "Front and rear port media differ.")
        graph[front].add(rear)
        graph[rear].add(front)

    # Each passive component is traversed once, rather than tracing afresh per host.
    component_of = {}
    component_members = {}
    device_peers = defaultdict(set)
    terminal_peers = {}
    endpoint_port_counts = Counter()
    path_lengths = {}
    for start in graph:
        if start in component_of:
            continue
        pending, active, members = [start], [], []
        component_of[start] = start
        while pending:
            current = pending.pop()
            members.append(current)
            if kind(current) not in {"front_port", "rear_port"}:
                active.append(current)
            for other in graph[current]:
                if other not in component_of:
                    component_of[other] = start
                    pending.append(other)
        component_members[start] = members
        if len(active) > 2:
            report("physical-fanout", start, "A passive path joins more than two active terminations.")
        if not active and all(len(graph[key]) == 2 for key in members):
            report("passive-loop", start, "Passive cabling forms a closed loop without an active endpoint.")
        if len(active) == 2 and len(members) > 2:
            a, b = active
            ma = _medium(kind(a), attrs(a).get("type", ""))
            mb = _medium(kind(b), attrs(b).get("type", ""))
            sa = attrs(a).get("speed") or attrs(a).get("port_speed") or _speed(attrs(a).get("type", ""))
            sb = attrs(b).get("speed") or attrs(b).get("port_speed") or _speed(attrs(b).get("type", ""))
            if sa and sb and sa != sb and not (ma == mb == "copper"):
                report("path-speed", start, f"Passive path endpoint speeds differ: {sa} and {sb} kbps.")
        if len(active) == 2 and all(kind(k) == "interface" for k in active):
            a, b = (refs(k).get("device") for k in active)
            if a and b and a != b:
                device_peers[a].add(b)
                device_peers[b].add(a)
                if all(_medium("interface", attrs(k).get("type", "")) == "copper" for k in active):
                    if meta(a).get("endpoint"):
                        endpoint_port_counts[b] += 1
                    if meta(b).get("endpoint"):
                        endpoint_port_counts[a] += 1
        if len(active) == 2:
            terminal_peers[active[0]] = active[1]
            terminal_peers[active[1]] = active[0]
            cables = {occupied[key] for key in members if key in occupied}
            lengths = []
            for cable in cables:
                length = attrs(cable).get("length")
                unit = attrs(cable).get("length_unit")
                if length is not None:
                    factor = {"m": 1, "cm": .01, "ft": .3048, "in": .0254, "km": 1000}.get(unit)
                    if factor is None:
                        report("cable-length-unit", cable, "Declared length needs a supported unit.")
                    else:
                        lengths.append(length * factor)
            if len(lengths) == len(cables):
                total = sum(lengths)
                path_lengths[active[0]] = path_lengths[active[1]] = total
                if all(_medium(kind(key), attrs(key).get("type", "")) == "copper" for key in active) and total > 100:
                    report("copper-channel-length", start, f"Copper channel is {total:g} m; this design permits at most 100 m.")

    # Keep planned cables in the structural graph for occupancy/media checks.
    # Availability is a separate index: inspect each complete passive path once.
    connected_components = set()
    for component, members in component_members.items():
        cables = {occupied[key] for key in members if key in occupied}
        devices = {refs(key).get("device") for key in members} - {None}
        if (cables and all(attrs(key).get("status", None if generated else "connected") == "connected" for key in cables) and
                all(attrs(key).get("status", None if generated else "active") == "active" for key in devices)):
            connected_components.add(component)
    available_peers = {port: peer for port, peer in terminal_peers.items()
                       if component_of.get(port) in connected_components and
                       all(attrs(key).get("enabled", True) for key in (port, peer))}

    prefixes = defaultdict(dict)
    for prefix in by_kind["prefix"]:
        try:
            network = ip_network(attrs(prefix)["prefix"], strict=True)
        except (ValueError, KeyError, TypeError):
            report("prefix-format", prefix, "Prefix must be a canonical IPv4 or IPv6 network.")
            continue
        scope = (refs(prefix).get("vrf"), network.version)
        identity = (network.prefixlen, int(network.network_address))
        if identity in prefixes[scope]:
            report("prefix-duplicate", prefix, f"Network duplicates {prefixes[scope][identity]} in the same VRF.")
        prefixes[scope][identity] = prefix
    lengths = {scope: sorted({length for length, _ in networks}, reverse=True) for scope, networks in prefixes.items()}
    addresses = {}
    address_versions = {}
    usable_addresses = set()
    for address in by_kind["ip_address"]:
        try:
            value = ip_interface(attrs(address)["address"])
        except (ValueError, KeyError, TypeError):
            report("ip-format", address, "Address must be a valid IPv4 or IPv6 interface address.")
            continue
        vrf = refs(address).get("vrf")
        address_versions[address] = value.version
        identity = (vrf, value.version, int(value.ip))
        if identity in addresses:
            report("ip-duplicate", address, f"Address duplicates {addresses[identity]} in the same VRF.")
        addresses[identity] = address
        scope = (vrf, value.version)
        owner = refs(address).get("assigned_object")
        # RFC1122 host-bit exclusions, with RFC3021 /31 and host /32 preserved.
        # An unassigned reserved record may document a subnet boundary; an
        # assignment, primary reference or listener makes it a host obligation.
        boundary = value.version == 4 and value.network.prefixlen < 31 and value.ip in (
            value.network.network_address, value.network.broadcast_address)
        reserved_only = (attrs(address).get("status") == "reserved" and not owner and
                         not any(children[(field, address)] for field in ("primary_ip4", "primary_ip6", "ipaddresses")))
        if boundary and not reserved_only:
            report("ip-host-address", address, "IPv4 network/broadcast addresses cannot serve hosts with this mask; use a usable host address (/31 and /32 are supported).")
        elif not boundary:
            usable_addresses.add(address)
        if owner and kind(owner) not in {"interface", "vm_interface", "fhrp_group"}:
            report("ip-assignment", address, "Address must be assigned to an interface, VM interface or FHRP group.")
        if refs(owner).get("vrf") and refs(owner)["vrf"] != vrf:
            report("ip-vrf", address, "Address and assigned interface use different VRFs.")
        containing = None
        for length in lengths.get(scope, []):
            network_integer = (int(value.ip) >> (value.max_prefixlen - length)) << (value.max_prefixlen - length)
            containing = prefixes[scope].get((length, network_integer))
            if containing and attrs(containing).get("status") != "container":
                break
            containing = None
        if not containing:
            report("ip-outside-prefix", address, "No containing prefix exists in the address VRF.")
        elif owner:
            if generated and refs(address).get("tenant") != refs(containing).get("tenant"):
                report("ip-tenant", address, "Assigned address and its containing segment must have the same tenant.")
            if generated and kind(owner) == "vm_interface" and refs(address).get("tenant") != refs(refs(owner).get("virtual_machine")).get("tenant"):
                report("ip-tenant", address, "A generated VM's address must belong to its actual VM tenant.")
            if site(containing) and site(owner) and site(containing) != site(owner):
                report("ip-site", address, "Containing prefix and assigned interface belong to different sites.")
            vlan = refs(containing).get("vlan")
            owner_vlans = set(refs(owner).get("tagged_vlans", []))
            if refs(owner).get("untagged_vlan"):
                owner_vlans.add(refs(owner)["untagged_vlan"])
            if vlan and (owner_vlans or attrs(owner).get("mode") in {"access", "tagged"}) and vlan not in owner_vlans:
                report("ip-vlan", address, "Containing prefix VLAN is not carried by the assigned interface.")
            if generated and vlan in owner_vlans and value.network != ip_network(attrs(containing)["prefix"]):
                report("ip-vlan-mask", address, "A generated VLAN interface address must use its containing segment's subnet mask; membership alone does not establish an on-link gateway.")
    for owner in by_kind["device"] + by_kind["virtual_machine"]:
        for field, version in (("primary_ip4", 4), ("primary_ip6", 6)):
            address = refs(owner).get(field)
            if not address:
                continue
            interface = refs(address).get("assigned_object")
            assigned_owner = refs(interface).get("device") or refs(interface).get("virtual_machine")
            if kind(address) != "ip_address" or address_versions.get(address) != version or assigned_owner != owner:
                report("primary-ip", owner, f"{field} must be an IPv{version} address assigned to this object's interface.")
    for service in by_kind["service"]:
        owner = refs(service).get("virtual_machine") or refs(service).get("device")
        for address in refs(service).get("ipaddresses", []):
            interface = refs(address).get("assigned_object")
            actual_owner = refs(interface).get("virtual_machine") or refs(interface).get("device")
            if kind(address) != "ip_address" or actual_owner != owner:
                report("service-address", service, "Listening address must be assigned to the service's own parent.")
    for interface in by_kind["interface"] + by_kind["vm_interface"]:
        rel = refs(interface)
        # Explicit speed is a configured/negotiated rate in kbps, not a way to
        # upgrade physical hardware. Catalog type identity is checked above.
        nominal = _speed(attrs(interface).get("type", "")) if kind(interface) == "interface" else None
        speed = attrs(interface).get("speed")
        if nominal and speed is not None and speed > nominal:
            report("interface-speed", interface, f"Declared speed exceeds the physical interface type's {nominal} kbps nominal rate.")
        for field in ("bridge", "parent"):
            target = rel.get(field)
            if target and (kind(target) != kind(interface) or target == interface or
                           refs(target).get("device") != rel.get("device") or
                           refs(target).get("virtual_machine") != rel.get("virtual_machine")):
                report("interface-relation", interface, f"{field} must reference a different interface on the same owner.")
            if field == "bridge" and target and attrs(target).get("type") != "bridge":
                report("interface-relation", interface, "Bridge membership must reference an interface with type bridge.")
        if rel.get("parent") and kind(interface) == "interface" and attrs(interface).get("type") != "virtual":
            report("interface-relation", interface, "Only virtual interfaces can declare a parent in this blueprint.")
        vlans = list(rel.get("tagged_vlans", []))
        if rel.get("untagged_vlan"):
            if not attrs(interface).get("mode"):
                report("vlan-mode", interface, "An untagged VLAN needs explicit interface mode; NetBox clears it when mode is blank.")
            vlans.append(rel["untagged_vlan"])
        if rel.get("tagged_vlans") and attrs(interface).get("mode") not in {"tagged", "tagged-all"}:
            report("vlan-mode", interface, "Tagged VLAN references need a tagged interface mode.")
        for vlan in vlans:
            if kind(vlan) != "vlan":
                report("vlan-reference", interface, "VLAN references must target VLAN objects.")
            elif site(vlan) and site(interface) and site(vlan) != site(interface):
                report("vlan-scope", interface, f"VLAN {vlan} belongs to a different site.")

    def carried_vlans(interface):
        carried = set(refs(interface).get("tagged_vlans", []))
        if refs(interface).get("untagged_vlan"):
            carried.add(refs(interface)["untagged_vlan"])
        return carried

    # This is declared VLAN forwarding intent, not STP, routing or ACL simulation.
    # Each per-VLAN edge is inspected once, including links crossing patch panels.
    vlan_graph = defaultdict(lambda: defaultdict(set))
    needed_vlans = defaultdict(set)
    for a, b in terminal_peers.items():
        if a >= b or kind(a) != "interface" or kind(b) != "interface":
            continue
        av, bv = carried_vlans(a), carried_vlans(b)
        if av != bv:
            report("vlan-continuity", a, f"Physical peer {b} carries a different VLAN set.")
        da, db = refs(a).get("device"), refs(b).get("device")
        if not da or not db:
            continue
        if available_peers.get(a) == b:
            for vlan in av & bv:
                vlan_graph[vlan][da].add(db)
                vlan_graph[vlan][db].add(da)
        if meta(da).get("endpoint"):
            needed_vlans[da].update(av)
            needed_vlans[db].update(av)
        if meta(db).get("endpoint"):
            needed_vlans[db].update(bv)
            needed_vlans[da].update(bv)
    for interface in by_kind["vm_interface"]:
        vm = refs(interface).get("virtual_machine")
        host = refs(vm).get("device")
        if host:
            needed_vlans[host].update(carried_vlans(interface))
    gateway_devices = defaultdict(set)
    gateway_interfaces = defaultdict(list)
    for interface in by_kind["interface"]:
        device = refs(interface).get("device")
        vlan = refs(interface).get("untagged_vlan")
        edge_gateway = refs(device).get("role") == "role/wan-edge" and meta(site(device)).get("branch_size") == "small"
        if (not vlan or attrs(interface).get("type") != "virtual" or not attrs(interface).get("enabled", True) or
                attrs(device).get("status", None if generated else "active") != "active" or
                (refs(device).get("role") not in {"role/distribution", "role/leaf"} and not edge_gateway)):
            continue
        if edge_gateway:
            bridge = refs(interface).get("parent")
            carriers = [port for port in children[("bridge", bridge)] if kind(port) == "interface" and
                        refs(port).get("device") == device and port in available_peers and
                        attrs(port).get("enabled", True) and vlan in carried_vlans(port)] if bridge else []
            if (attrs(bridge).get("type") != "bridge" or refs(bridge).get("device") != device or
                    not attrs(bridge).get("enabled", True) or vlan not in carried_vlans(bridge) or not carriers):
                report("branch-gateway-carrier", interface, "Compact gateway needs its enabled local bridge and a cabled bridge member carrying this VLAN.")
                continue
        if vlan and any(address in usable_addresses and address_versions.get(address) == 4 and attrs(address).get("status", None if generated else "active") == "active"
                        for address in children[("assigned_object", interface)]):
            gateway_devices[vlan].add(device)
            gateway_interfaces[(device, vlan)].append(interface)
    gateway_reachable = {}
    forwarding_roles = {"role/access", "role/distribution", "role/leaf", "role/spine", "role/management", "role/wan-edge"}
    for vlan, gateways in gateway_devices.items():
        seen, pending = set(gateways), list(gateways)
        while pending:
            current = pending.pop()
            # A dual-attached server or endpoint is not a switch between uplinks.
            if refs(current).get("role") not in forwarding_roles:
                continue
            if refs(current).get("role") == "role/wan-edge" and meta(site(current)).get("branch_size") != "small":
                continue
            for peer in vlan_graph[vlan].get(current, ()):
                if peer not in seen and site(peer) == site(current):
                    seen.add(peer)
                    pending.append(peer)
        gateway_reachable[vlan] = seen

    role_counts = defaultdict(Counter)
    endpoint_roles = defaultdict(Counter)
    devices_by_site = defaultdict(list)
    for device in by_kind["device"]:
        role = refs(device).get("role")
        role_counts[site(device)][role] += 1
        devices_by_site[site(device)].append(device)
        if meta(device).get("endpoint"):
            endpoint_roles[site(device)][role] += 1
        if attrs(role).get("name"):
            role_counts[site(device)][attrs(role)["name"]] += 1
    scoped_network_objects = defaultdict(list)
    for typ in ("interface", "prefix", "ip_address", "vlan", "rack"):
        for key in by_kind[typ]:
            scoped_network_objects[site(refs(key).get("assigned_object")) if typ == "ip_address" else site(key)].append(key)
    vms_by_host = defaultdict(list)
    vms_by_service = defaultdict(list)
    for vm in by_kind["virtual_machine"]:
        host = refs(vm).get("device")
        if host:
            if kind(host) != "device":
                report("vm-host", vm, "VM device must reference a physical device.")
            elif refs(vm).get("cluster") != refs(host).get("cluster"):
                report("vm-cluster", vm, "VM and assigned host must belong to the same cluster.")
            vms_by_host[host].append(vm)
        vms_by_service[(site(vm), meta(vm).get("service"))].append(vm)
    for feed in by_kind["power_feed"]:
        utilization = attrs(feed).get("max_utilization")
        if utilization is not None and not 0 < utilization <= 100:
            report("power-feed-utilization", feed, "Feed maximum utilization must be a percentage above zero and at most 100.")
        inlet = terminal_peers.get(feed)
        outlets = [key for key in children[("power_port", inlet)] if kind(key) == "power_outlet"]
        loads = [terminal_peers.get(outlet) for outlet in outlets]
        worst_case = sum(attrs(port).get("maximum_draw", 0) for port in loads)
        if worst_case:
            data = attrs(feed)
            budget = data.get("voltage", 0) * data.get("amperage", 0) * data.get("max_utilization", 0) / 100
            if data.get("phase") != "single-phase" or data.get("supply") != "ac":
                report("power-budget-model", feed, "The planning budget currently supports single-phase AC feeds only.")
            elif worst_case > budget:
                report("power-feed-capacity", feed, f"Single-feed failover allocation {worst_case:g} W exceeds {budget:g} W planning budget.")
    contracts = plan.get("contracts", [])
    if not isinstance(contracts, list):
        report("contract-format", "plan", "contracts must be a list.")
        contracts = []
    placed_sites = {entry.get("site") for entry in contracts if isinstance(entry, dict) and entry.get("placement")}
    # Physical infrastructure between equipment rooms needs an actual fiber
    # riser. This checks authored room geometry, not optical reach or duct diversity.
    for cable in by_kind["cable"]:
        a, b = refs(cable).get("a"), refs(cable).get("b")
        if kind(a) != "interface" or kind(b) != "interface":
            continue
        da, db = refs(a).get("device"), refs(b).get("device")
        if not refs(da).get("rack") or not refs(db).get("rack") or not ({site(da), site(db)} & placed_sites):
            continue
        la, lb = refs(da).get("location"), refs(db).get("location")
        if site(da) != site(db):
            report("backbone-site", cable, "Generated equipment-room backbones must remain within one site; inter-site connectivity uses circuits.")
        if la == lb:
            continue
        if not attrs(cable).get("type", "").startswith(("smf", "mmf", "aoc")) or any(
                _medium("interface", attrs(port).get("type", "")) != "fiber" for port in (a, b)):
            report("backbone-media", cable, "Infrastructure connections between equipment rooms require fiber interfaces and fiber cabling.")
        if not all(attrs(port).get("enabled", True) for port in (a, b)):
            report("backbone-enabled", cable, "Equipment-room backbone interfaces must be enabled.")
        points = [meta(room).get("position_m", []) for room in (la, lb)]
        if any(not isinstance(point, list) or len(point) != 3 or any(
                type(n) not in (int, float) or not math.isfinite(n) for n in point) for point in points):
            report("backbone-geometry", cable, "Backbone endpoints need equipment-room coordinates.")
        else:
            minimum = sum(abs(x-y) for x, y in zip(*points))
            length = attrs(cable).get("length", 0)
            if attrs(cable).get("length_unit") != "m" or length <= 0 or length < minimum:
                report("backbone-length", cable, f"Backbone needs a positive length in metres, at least the {minimum:g} m room/riser distance.")
    bank_dc_sites, bank_service_counts, bank_compute_reserve = set(), {}, None
    bank_campuses = {}
    demand_peak = sum(c.get("demand", {}).get("peak_mbps", 0) for c in contracts if isinstance(c, dict) and c.get("kind") in {"branch", "hq"})
    wan_capacity = defaultdict(Counter)
    for circuit in by_kind["circuit"] if recipe.get("profile") != "regional-bank" else []:
        terms = [term for term in children[("circuit", circuit)] if kind(term) == "circuit_termination"]
        provider = refs(circuit).get("provider")
        if not any(kind(refs(term).get("termination")) == "provider_network" and refs(refs(term)["termination"]).get("provider") == provider for term in terms):
            continue
        for term in terms:
            location = refs(term).get("termination")
            peer = terminal_peers.get(term)
            if kind(location) == "site" and kind(peer) == "interface" and site(peer) == location:
                speeds = [attrs(term).get("port_speed") or 0, attrs(circuit).get("commit_rate") or 0, attrs(peer).get("speed") or _speed(attrs(peer).get("type", "")) or 0]
                if attrs(peer).get("enabled", True) and attrs(circuit).get("status", "active") == "active":
                    wan_capacity[location][provider] += min(speeds) / 1000
    if recipe.get("profile") == "regional-bank":
        # Finite bank procurement policy, deliberately independent of circuit
        # metadata and builder contracts. Carrier rates are fictional products.
        tiers = recipe.get("wan_tiers_mbps", [50, 100, 200, 500, 1000])
        if (not isinstance(tiers, list) or not tiers or any(type(t) is not int or t <= 0 for t in tiers) or
                tiers != sorted(set(tiers)) or tiers[-1] != 1000):
            report("wan-tiers", "plan", "WAN tiers must be positive, strictly increasing integer Mbps values ending at 1000.")
            tiers = [50, 100, 200, 500, 1000]
        reserve = recipe.get("reserve_fraction", 0.2)
        if type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve):
            report("wan-recipe", "plan", "WAN reserve must be between 0.1 and 0.4.")
            reserve = 0.2
        usable_fraction = Decimal(1)-Decimal(str(reserve))
        bank_compute_reserve = reserve
        counts = recipe.get("branches", {})
        headquarters, staff = recipe.get("headquarters", 0), recipe.get("headquarters_staff", 180)
        if (not isinstance(counts, dict) or counts.keys() - {"small", "medium", "large"} or
                any(type(n) is not int or not 0 <= n <= 2000 for n in counts.values()) or
                type(headquarters) is not int or not 0 <= headquarters <= 2 or
                type(staff) is not int or not 24 <= staff <= 192):
            report("wan-recipe", "plan", "WAN sizing requires the supported branch counts, HQ count and HQ staff recipe.")
            counts, headquarters, staff = {}, 0, 180
        peaks = {f"site/br-{size[0]}{ordinal:04}": peak for size, peak in (("small", 20), ("medium", 50), ("large", 100))
                 for ordinal in range(1, counts.get(size, 0)+1)}
        peaks.update({f"site/hq-{ordinal:02}": 2*staff for ordinal in range(1, headquarters+1)})
        for location in peaks:
            site_id = location.removeprefix("site/")
            hq = site_id.startswith("hq-")
            demand = (staff, 0, math.ceil(staff/12), 2*math.ceil(staff/48)) if hq else BANK_BRANCH_ENDPOINTS[site_id[3]]
            designs = plan.get("design_assignments", {})
            design = "modern" if hq else designs.get(site_id) if isinstance(designs, dict) else None
            if design not in BRANCH_DESIGNS:
                report("branch-design-selection", location, "Campus health requires a supported persisted access design.")
                design = "modern"
            bank_campuses[location] = dict(kind="hq" if hq else "branch", demand=demand,
                                           design=design, floors=math.ceil(staff/48) if hq else 1,
                                           compact=not hq and site_id[3] == "s")
        aggregate = sum(peaks.values())
        dc_sites = {"site/dc-01", "site/dc-02"}
        bank_dc_sites = dc_sites
        bank_service_counts = {name: max(2, math.ceil(sum(counts.values()) / cohort))
                               for name, cohort in BANK_BRANCHES_PER_INSTANCE.items()}
        expected_sites = set(peaks) | dc_sites
        if set(by_kind["site"]) != expected_sites or recipe.get("data_centers", 2) != 2:
            report("wan-site-inventory", "plan", "WAN sites must match the recipe's branches, headquarters and two data centers.")
        try:
            observed_date = date.fromisoformat(recipe.get("as_of", ""))
        except (TypeError, ValueError):
            observed_date = None
            report("wan-date", "plan", "WAN procurement needs a valid recipe observation date.")
        circuits_by_provider = defaultdict(lambda: defaultdict(list))
        actual_capacity = defaultdict(Counter)
        for circuit in by_kind["circuit"]:
            terms = [term for term in children[("circuit", circuit)] if kind(term) == "circuit_termination"]
            local = [term for term in terms if kind(refs(term).get("termination")) == "site"]
            remote = [term for term in terms if kind(refs(term).get("termination")) == "provider_network"]
            if (len(local) != 1 or len(remote) != 1 or attrs(local[0]).get("term_side") != "A" or
                    attrs(remote[0]).get("term_side") != "Z"):
                report("wan-attachment", circuit, "Each private WAN circuit needs a site A termination and a provider-network Z termination.")
                continue
            location = refs(local[0]).get("termination")
            provider = refs(circuit).get("provider")
            circuits_by_provider[location][provider].append(circuit)
            if provider not in {"provider/a", "provider/b"} or refs(refs(remote[0]).get("termination")).get("provider") != provider:
                report("wan-provider", circuit, "WAN provider and its opaque provider network must agree with carrier A or B.")
                continue
            site_id = location.removeprefix("site/")
            design = plan.get("design_assignments", {}).get(site_id)
            retained = design in {"inherited", "refreshed"} and site_id.startswith("br-")
            peak = aggregate if location in dc_sites else peaks.get(location, 0)
            minimum = max(50 if provider == "provider/a" else 100, 200 if retained else 0)
            expected_rate = 1000 if location in dc_sites else next((tier for tier in tiers if tier >= minimum and Decimal(tier)*usable_fraction >= peak), None)
            rate = attrs(circuit).get("commit_rate")
            if type(rate) is not int or expected_rate is None or rate != expected_rate*1000:
                report("wan-cir-policy", circuit, f"Recipe demand, carrier minimum and retained procurement require {expected_rate} Mbps CIR.")
            if type(rate) is not int or rate not in {tier*1000 for tier in tiers} or not 0 < rate <= 1000000:
                report("wan-cir-tier", circuit, "CIR must be a configured service tier at or below the 1 Gbps physical handoff.")
            peer = terminal_peers.get(local[0])
            edge = refs(peer).get("device")
            speed = attrs(peer).get("speed")
            if speed is None:
                speed = _speed(attrs(peer).get("type", "")) or 0
            active = attrs(circuit).get("status") == "active"
            if not active:
                report("wan-status", circuit, "Baseline WAN circuits must be active.")
            valid_handoff = (kind(peer) == "interface" and site(edge) == location and refs(edge).get("role") == "role/wan-edge" and
                             attrs(edge).get("status") == "active" and attrs(peer).get("name") == "wan1" and attrs(peer).get("enabled", True))
            if not valid_handoff:
                report("wan-handoff", circuit, "Site termination must reach enabled wan1 on an active WAN edge in that site.")
            speeds = [attrs(term).get("port_speed") or 0 for term in terms] + [speed]
            if any(value != 1000000 for value in speeds) or (isinstance(rate, (int, float)) and any(value < rate for value in speeds)):
                report("wan-handoff-speed", circuit, "Both A/Z terminations and the edge handoff must remain 1 Gbps and cover the CIR.")
            path_cables = {occupied[key] for key in component_members.get(component_of.get(local[0]), []) if key in occupied}
            connected = bool(path_cables) and all(attrs(key).get("status") == "connected" for key in path_cables)
            if not connected:
                report("wan-handoff", circuit, "The WAN handoff needs connected physical cables.")
            if active and valid_handoff and connected:
                actual_capacity[location][provider] += min(speeds + [rate if type(rate) is int else 0])/1000
            try:
                installed = date.fromisoformat(attrs(circuit).get("install_date", ""))
            except (TypeError, ValueError):
                installed = None
                report("wan-date", circuit, "Circuit installation date must be a valid ISO calendar date.")
            if installed and observed_date:
                age = (observed_date-installed).days
                low, high = (1460, 1825) if location in dc_sites else (2190, 2920) if retained else (365, 1095)
                if not low <= age <= high:
                    report("wan-date", circuit, f"Fictional procurement date must be {low}–{high} days before the recipe observation date.")
            cohort = "dc-aggregation" if location in dc_sites else "retained-birch" if retained else "cedar-standard"
            procurement = meta(circuit).get("procurement", {})
            if not isinstance(procurement, dict) or procurement.get("cohort") != cohort:
                report("wan-provenance", circuit, "Procurement cohort must agree with actual site purpose and persisted branch lineage.")
            if not isinstance(attrs(circuit).get("comments"), str) or (
                    not attrs(circuit)["comments"].startswith("Procurement record: ") or cohort not in attrs(circuit)["comments"]):
                report("wan-provenance", circuit, "Portable comments must identify the procurement record and its cohort.")
            if any(not isinstance(attrs(circuit).get(field), str) or not attrs(circuit)[field].strip() for field in ("cid", "description")):
                report("wan-provenance", circuit, "WAN service needs a nonempty circuit ID and portable description.")
        for location in expected_sites:
            expected_count = max(1, math.ceil(Decimal(aggregate)/(1000*usable_fraction))) if location in dc_sites else 1
            demand = aggregate if location in dc_sites else peaks[location]
            for provider in ("provider/a", "provider/b"):
                count = len(circuits_by_provider[location][provider])
                if count != expected_count:
                    report("wan-circuit-count", location, f"Need exactly {expected_count} circuits for {provider}; found {count}.")
                available = Decimal(str(actual_capacity[location][provider]))*usable_fraction
                if available < demand:
                    report("wan-carrier-capacity", location, f"{provider} supplies {available:g} usable Mbps from actual handoffs; recipe requires {demand} Mbps.")
        wan_capacity, demand_peak = actual_capacity, aggregate
    # Required campus demand and available paths are independent of explanatory
    # contracts, endpoint markers, and claimed minima. Optional unconnected
    # inventory elsewhere is not globally required to be active.
    for site_key, policy in bank_campuses.items():
        site_id = site_key.removeprefix("site/")
        network_roles = {"role/workstation": "users", "role/atm": "atm", "role/ap": "wireless", "role/camera": "security"}
        vlans = {f"vlan/{site_id}/{segment}" for segment in (*network_roles.values(), "management")}
        alias, minimum = BRANCH_DESIGNS[policy["design"]]
        access = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/access"}
        edges = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/wan-edge"}
        dist = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/distribution"}
        managers = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/management"}
        upstreams = edges if policy["compact"] else dist
        usable = math.floor(len(catalog.get(alias, {}).get("access_ports", [])) * (1-bank_compute_reserve))
        if (len(edges) != 2 or len(dist) != (0 if policy["compact"] else 2) or
                len(managers) != policy["floors"] or len(access) < max(2*policy["floors"], math.ceil(sum(policy["demand"])/max(1, usable)))):
            report("bank-campus-inventory", site_key, "Recipe demand requires its access capacity, floor management switches, WAN pair and selected gateway architecture.")
        for role, required in zip(network_roles, policy["demand"]):
            endpoints = [key for key in devices_by_site[site_key] if refs(key).get("role") == role]
            if len(endpoints) != required:
                report("bank-endpoint-demand", site_key, f"Recipe requires {required} {role} endpoints; found {len(endpoints)}.")
            vlan = f"vlan/{site_id}/{network_roles[role]}"
            endpoint_vlans = {vlan, f"vlan/{site_id}/users"} if role == "role/ap" else {vlan}
            for device in endpoints:
                ports = [key for key in children[("device", device)] if kind(key) == "interface" and attrs(key).get("type") == "1000base-t"]
                if (len(ports) != 1 or refs(available_peers.get(ports[0])).get("device") not in access or
                        refs(ports[0]).get("untagged_vlan") != vlan or carried_vlans(ports[0]) != endpoint_vlans or
                        any(device not in gateway_reachable.get(segment, set()) for segment in endpoint_vlans)):
                    report("bank-endpoint-path", device, "Required endpoint needs an active, enabled, connected access channel carrying its role VLAN to an addressed local gateway.")
        for device in upstreams:
            if any(len(gateway_interfaces[(device, vlan)]) != 1 for vlan in vlans):
                report("bank-gateway-path", device, "Each required gateway must be active with one enabled, active-addressed SVI for every campus VLAN.")
        for device in access | (edges if not policy["compact"] else set()):
            peers = set()
            for port in children[("device", device)]:
                peer = available_peers.get(port)
                parent = refs(peer).get("device")
                if kind(port) == "interface" and parent in upstreams and carried_vlans(port) == carried_vlans(peer) == vlans:
                    peers.add(parent)
            required = minimum if device in access else 2
            if len(peers) != required:
                report("bank-uplink-path", device, f"Required campus infrastructure needs {required} distinct active upstreams through connected, enabled trunks carrying all campus VLANs; found {len(peers)}.")
        pair = access if policy["compact"] else dist
        if not any(refs(available_peers.get(port)).get("device") in pair - {device} and
                   carried_vlans(port) == carried_vlans(available_peers.get(port)) == vlans
                   for device in pair for port in children[("device", device)] if kind(port) == "interface"):
            report("bank-peer-path", site_key, "The authored campus pair requires a connected, active peer trunk carrying all campus VLANs.")
        management_vlan = f"vlan/{site_id}/management"
        for device in devices_by_site[site_key]:
            model = catalog.get(meta(device).get("hardware"), {})
            if meta(device).get("hardware") in {"endpoint", "atm", "ap"} or not model.get("power_ports") or model.get("power_outlets"):
                continue
            primary_address = refs(device).get("primary_ip4")
            primary = refs(primary_address).get("assigned_object")
            manager = refs(available_peers.get(primary)).get("device")
            valid = (primary_address in usable_addresses and attrs(primary_address).get("status") == "active" and
                     attrs(device).get("status") == "active" and attrs(primary).get("enabled", True) and
                     refs(primary).get("device") == device and carried_vlans(primary) == {management_vlan} and
                     device in gateway_reachable.get(management_vlan, set()))
            if attrs(primary).get("type") != "virtual":
                valid = valid and attrs(primary).get("mgmt_only") and manager in managers and refs(manager).get("location") == refs(device).get("location")
            if not valid:
                report("bank-management-path", device, "Required infrastructure needs active primary management addressing and a connected local management path to the campus gateway.")
    # Missing a site contract must not erase independent power obligations.
    # Work on a local list; validation never repairs or mutates the supplied plan.
    contracts = list(contracts)
    contracts_by_site = defaultdict(list)
    for entry in contracts:
        if isinstance(entry, dict) and isinstance(entry.get("site"), str):
            contracts_by_site[entry["site"]].append(entry)
    for campus, policy in sorted(bank_campuses.items()):
        matching = contracts_by_site[campus]
        if len(matching) != 1 or matching[0].get("kind") != policy["kind"]:
            report("bank-campus-contract", campus, "Each recipe campus requires exactly one contract of its declared facility kind.")
        if not matching:
            contracts.append({"site": campus, "kind": policy["kind"]})
    for dc_site in sorted(bank_dc_sites):
        matching = contracts_by_site[dc_site]
        if len(matching) != 1 or matching[0].get("kind") != "dc":
            report("dc-contract", dc_site, "Each bank DC requires exactly one DC contract.")
        if not matching:
            contracts.append({"site": dc_site, "kind": "dc"})
    for contract in contracts:
        if not isinstance(contract, dict):
            report("contract-format", "plan", "Each contract must be an object.")
            continue
        site_key = contract.get("site", "plan")
        bank_dc = site_key in bank_dc_sites
        bank_campus = site_key in bank_campuses
        if kind(site_key) != "site":
            report("contract-site", site_key, "Contract must reference an existing site.")
        if contract.get("kind") in {"branch", "hq", "dc", "school", "hospital", "clinic"}:
            for device in devices_by_site[site_key]:
                for vlan in needed_vlans[device]:
                    if device not in gateway_reachable.get(vlan, set()):
                        report("vlan-gateway", device, f"VLAN {vlan} has no declared cable/VLAN path to a site gateway SVI.")
        if placement := contract.get("placement"):
            equipment = placement.get("equipment_location")
            if kind(equipment) != "location" or meta(equipment).get("space_type") != "equipment_room" or site(equipment) != site_key:
                report("equipment-room", site_key, "Placement contract requires an equipment room at this site.")
            equipment_map = placement.get("equipment_locations", {"1": equipment})
            if not isinstance(equipment_map, dict) or any(not isinstance(floor, str) or not floor.isdecimal() or
                    not isinstance(room, str) for floor, room in equipment_map.items()):
                report("equipment-room-floor", site_key, "Equipment locations must map floor number strings to room keys.")
                equipment_map = {}
            occupied_floors = {str(meta(refs(device).get("location")).get("floor")) for device in devices_by_site[site_key]
                               if meta(device).get("endpoint")}
            staff = plan.get("recipe", {}).get("headquarters_staff", 180)
            if contract.get("kind") == "hq" and (type(staff) is not int or not 24 <= staff <= 192):
                report("building-demand", site_key, "HQ staff recipe must be an integer from 24 through 192.")
                staff = 180
            expected_floors = {str(floor) for floor in range(1, math.ceil(staff/48)+1)} if contract.get("kind") == "hq" else {"1"}
            if contract.get("kind") in {"school", "hospital", "clinic"}:
                # Profile adapters derive occupied floors independently below;
                # keep common physical checks without a one-floor assumption.
                expected_floors = occupied_floors
            if (set(equipment_map) != expected_floors or equipment_map.get("1") != equipment or
                    len(set(equipment_map.values())) != len(equipment_map) or
                    (contract.get("kind") == "hq" and ("equipment_locations" not in placement or occupied_floors != expected_floors))):
                report("equipment-room-floor", site_key, "The equipment-room map must cover exactly the required occupied floors, with MDF on floor 1.")
            equipment_rooms = set(equipment_map.values())
            for floor, room in equipment_map.items():
                if (kind(room) != "location" or meta(room).get("space_type") != "equipment_room" or
                        site(room) != site_key or str(meta(room).get("floor")) != floor):
                    report("equipment-room-floor", room, "Each mapped closet must be an equipment room on its own floor in this site.")
            for room in children[("site", site_key)]:
                if kind(room) != "location" or meta(room).get("space_type") == "building":
                    continue
                parent = refs(room).get("parent")
                floor = meta(room).get("floor")
                point = meta(room).get("position_m", [])
                height = placement.get("floor_height_m")
                if (type(floor) is not int or floor < 1 or type(height) not in (int, float) or height <= 0
                        or not isinstance(point, list) or len(point) != 3 or point[2] != (floor-1)*height):
                    report("location-floor", room, "Room or floor coordinates disagree with its declared level.")
                if meta(room).get("space_type") == "floor":
                    valid_parent = meta(parent).get("space_type") == "building"
                else:
                    valid_parent = meta(parent).get("space_type") == "floor" and meta(parent).get("floor") == floor
                if not valid_parent:
                    report("location-floor", room, "Location parent must agree with the building/floor/room hierarchy and floor number.")
            allowed_rooms = {"role/workstation": {"teller_hall", "office"}, "role/atm": {"atm_lobby"},
                             "role/ap": {"atm_lobby", "teller_hall", "office", "reception"},
                             "role/camera": {"atm_lobby", "teller_hall", "office", "reception"}}
            if contract.get("kind") == "school":
                allowed_rooms = {f"role/{role}": {"classroom", "office", "computer_lab"}
                                 for role in ("workstation", "ap", "camera")}
            elif contract.get("kind") in {"hospital", "clinic"}:
                allowed_rooms = {"role/workstation": {"office", "nurse_station", "exam_room", "imaging_room"},
                                 "role/medical-device": {"patient_room"}, "role/imaging-device": {"imaging_room"},
                                 "role/ap": {"office", "corridor", "imaging_room", "reception"},
                                 "role/camera": {"corridor", "reception"}}
            occupancy = defaultdict(Counter)
            floor_endpoints = Counter()
            for rack in children[("site", site_key)]:
                if kind(rack) == "rack" and refs(rack).get("location") not in equipment_rooms:
                    report("equipment-room", rack, "Generated racks must occupy the designated equipment room.")
            for device in devices_by_site[site_key]:
                location = refs(device).get("location")
                if kind(location) != "location":
                    report("device-placement", device, "Generated devices require an explicit room location.")
                if refs(device).get("rack") and location not in equipment_rooms:
                    report("equipment-room", device, "Racked infrastructure must occupy the designated equipment room.")
                if meta(device).get("purpose") == "wall-outlet":
                    served = meta(device).get("serves_endpoint")
                    if not meta(served).get("endpoint") or refs(served).get("location") != location:
                        report("outlet-room", device, "Wall outlet must share the room of its served endpoint.")
                if meta(device).get("endpoint"):
                    room_type = meta(location).get("space_type")
                    role = refs(device).get("role")
                    if room_type not in allowed_rooms.get(role, set()):
                        report("endpoint-room", device, f"{role} cannot occupy a {room_type} space in this design.")
                    occupancy[location][role] += 1
                    serving_room = equipment_map.get(str(meta(location).get("floor"))) if contract.get("kind") in {"hq", "school", "hospital", "clinic"} else equipment
                    floor_endpoints[serving_room] += 1
                    interfaces = [key for key in children[("device", device)] if kind(key) == "interface"]
                    length = next((path_lengths[key] for key in interfaces if key in path_lengths), None)
                    connected = next((key for key in interfaces if key in terminal_peers), None)
                    switch = refs(terminal_peers.get(connected)).get("device")
                    if refs(switch).get("role") != "role/access" or refs(switch).get("location") != serving_room:
                        report("endpoint-serving-room", device, "Endpoint copper path must terminate on an access switch in its floor's equipment room.")
                    if plan.get("recipe", {}).get("patching") == "panels":
                        members = component_members.get(component_of.get(connected), [])
                        passive = {refs(key).get("device") for key in members if kind(key) in {"front_port", "rear_port"}}
                        outlets = {key for key in passive if meta(key).get("purpose") == "wall-outlet"}
                        patch_panels = {key for key in passive if meta(key).get("purpose") == "patch-panel"}
                        cables = {occupied[key] for key in members if key in occupied}
                        if (len(cables) != 3 or len(passive) != 2 or len(outlets) != 1 or len(patch_panels) != 1
                                or any(meta(outlet).get("serves_endpoint") != device for outlet in outlets)):
                            report("endpoint-patch-path", device, "Panel mode requires three cables through a cabinet panel and the endpoint's own room outlet.")
                        if any(refs(panel).get("location") != serving_room for panel in patch_panels):
                            report("endpoint-panel-room", device, "Cabinet patch panel must share the serving access switch's floor equipment room.")
                    limit = min(100, placement.get("max_access_channel_m", 100))
                    if length is None or not 0 < length <= limit:
                        report("endpoint-channel", device, f"Endpoint requires a measured cable path within {limit} m.")
                    position = meta(device).get("placement", {})
                    point = position.get("position_m", [])
                    origin = meta(serving_room).get("position_m", [])
                    if (position.get("room") != location or position.get("floor") != meta(location).get("floor")
                            or position.get("cable_origin") != serving_room):
                        report("endpoint-geometry", device, "Endpoint placement must agree with its room, floor, and cable origin.")
                    if (not isinstance(point, list) or len(point) != 3 or not isinstance(origin, list) or len(origin) != 3
                            or any(type(n) not in (int, float) or not math.isfinite(n) for n in point + origin)):
                        report("endpoint-geometry", device, "Endpoint and cable origin require finite local x/y/z coordinates.")
                    elif length is not None and length < sum(abs(a-b) for a, b in zip(point, origin)):
                        report("endpoint-route-length", device, "Cable is shorter than the room/riser route between its endpoints.")
                    else:
                        floor = meta(location).get("floor")
                        height = placement.get("floor_height_m")
                        if (type(floor) is not int or floor < 1 or type(height) not in (int, float) or height <= 0
                                or not (floor-1)*height <= point[2] < floor*height):
                            report("endpoint-floor", device, "Endpoint mounting height must be inside its assigned floor.")
            if contract.get("kind") == "hq":
                reserve = plan.get("recipe", {}).get("reserve_fraction", 0.2)
                model = catalog.get("access", {})
                usable = math.floor(len(model.get("access_ports", [])) * (1-reserve))
                access_by_room = Counter(refs(device).get("location") for device in devices_by_site[site_key]
                                         if refs(device).get("role") == "role/access")
                for room in equipment_rooms:
                    if usable < 1 or access_by_room[room] < max(2, math.ceil(floor_endpoints[room] / max(1, usable))):
                        report("building-access-capacity", room, "Each occupied HQ floor needs at least two access switches and enough catalog ports for its own endpoint demand and reserve.")
                for device in devices_by_site[site_key]:
                    if refs(device).get("role") in {"role/distribution", "role/wan-edge"} and refs(device).get("location") != equipment:
                        report("building-core-room", device, "HQ distribution and WAN equipment must remain in the MDF.")
                offices = math.ceil(staff / 12)
                expected = {"workstations": staff, "atms": 0, "aps": offices, "cameras": 2*math.ceil(offices/4), "peak_mbps": 2*staff}
                if contract.get("demand") != expected:
                    report("building-demand", site_key, "HQ demand must follow headquarters_staff: one AP per office pod, two cameras per floor, no ATMs, and 2 Mbps per staff member.")
                actual_floor_roles = defaultdict(Counter)
                for device in devices_by_site[site_key]:
                    if meta(device).get("endpoint"):
                        floor = str(meta(refs(device).get("location")).get("floor"))
                        actual_floor_roles[floor][refs(device).get("role")] += 1
                actual_offices = Counter(str(meta(room).get("floor")) for room in children[("site", site_key)]
                                         if kind(room) == "location" and meta(room).get("space_type") == "office")
                for floor in expected_floors:
                    desks = min(48, staff-(int(floor)-1)*48)
                    pods = math.ceil(desks/12)
                    expected_roles = {"role/workstation": desks, "role/ap": pods, "role/camera": 2}
                    if actual_floor_roles[floor] != expected_roles or actual_offices[floor] != pods:
                        report("building-floor-demand", site_key, f"HQ floor {floor} requires {desks} desks in {pods} office pods, {pods} APs and two cameras.")
            management = {device for device in devices_by_site[site_key] if refs(device).get("role") == "role/management"}
            if recipe.get("profile") == "provider-backbone" and contract.get("kind") == "customer":
                management.update(device for device in devices_by_site[site_key] if refs(device).get("role") == "role/access")
            for device in devices_by_site[site_key]:
                hardware = catalog.get(meta(device).get("hardware"), {})
                if not any(port.get("mgmt_only") for port in hardware.get("interfaces", [])):
                    continue
                primary = refs(refs(device).get("primary_ip4")).get("assigned_object")
                virtual_management_roles = {"role/distribution", "role/leaf", "role/wan-edge", "role/management"}
                if recipe.get("profile") == "provider-backbone":
                    # The independent provider check requires exact loopback/SVI
                    # ownership and real uplinks for this in-band composition.
                    virtual_management_roles.update({"role/provider-edge", "role/customer-edge", "role/access"})
                if attrs(primary).get("type") == "virtual" and refs(device).get("role") in virtual_management_roles:
                    continue
                peer = terminal_peers.get(primary)
                manager = refs(peer).get("device")
                if (not attrs(primary).get("mgmt_only") or manager not in management or
                        refs(manager).get("location") != refs(device).get("location") or
                        not attrs(primary).get("enabled", True) or not attrs(peer).get("enabled", True) or
                        _medium("interface", attrs(peer).get("type", "")) != "copper"):
                    report("management-room", device, "Dedicated management must reach an enabled management switch copper port in the same equipment room.")
                if primary in path_lengths and not 0 < path_lengths[primary] <= 100:
                    report("management-channel", primary, "Dedicated copper management channel must be positive and at most 100 m.")
            if contract.get("kind") == "hq":
                managers_by_room = Counter(refs(device).get("location") for device in management)
                for room in equipment_rooms:
                    if managers_by_room[room] != 1:
                        report("building-management", room, "Each HQ equipment room needs one local management switch.")
                for manager in management:
                    uplinks = []
                    for port in children[("device", manager)]:
                        peer = terminal_peers.get(port)
                        parent = refs(peer).get("device")
                        if refs(parent).get("role") == "role/distribution":
                            uplinks.append((port, peer, parent))
                    vlan = f"vlan/{site_key.removeprefix('site/')}/management"
                    if len(uplinks) != 1 or any(refs(parent).get("location") != equipment or
                            not all(attrs(p).get("enabled", True) and carried_vlans(p) == {vlan} and
                                    _medium("interface", attrs(p).get("type", "")) == "fiber" for p in (port, peer))
                            for port, peer, parent in uplinks):
                        report("building-management-backbone", manager, "Local HQ management requires one enabled fiber management-VLAN uplink to the MDF distribution pair.")
            for location, roles in occupancy.items():
                capacity = meta(location).get("capacity", {}).get("workstations")
                if capacity is not None and roles["role/workstation"] > capacity:
                    report("room-capacity", location, f"Room has {roles['role/workstation']} workstations but capacity {capacity}.")
            geography = meta(site_key).get("geography", {})
            expected_zones = {"Chicago": "America/Chicago", "Detroit": "America/Detroit",
                              "Cleveland": "America/New_York", "Milwaukee": "America/Chicago"}
            region = refs(site_key).get("region")
            if kind(region) != "region" or not refs(region).get("parent") or kind(refs(site_key).get("group")) != "site_group":
                report("site-geography", site_key, "Generated site requires a region hierarchy and functional site group.")
            if attrs(site_key).get("time_zone") != expected_zones.get(geography.get("city")):
                report("site-time-zone", site_key, "Site time zone differs from its authored metropolitan area.")
        for role, count in contract.get("required_device_roles", {}).items():
            if role_counts[site_key][role] < count:
                report("required-role", site_key, f"Need at least {count} devices with role {role}; found {role_counts[site_key][role]}.")
        if "demand" in contract and recipe.get("profile") != "provider-backbone":
            for field, role in (("workstations", "workstation"), ("atms", "atm"), ("aps", "ap"), ("cameras", "camera")):
                expected = contract["demand"].get(field, 0)
                actual = endpoint_roles[site_key][f"role/{role}"]
                if actual != expected:
                    report("endpoint-demand", site_key, f"Demand requests {expected} {field}; found {actual} matching endpoint devices.")
        if "access_usable_ports" in contract:
            for device in devices_by_site[site_key]:
                if refs(device).get("role") == "role/access" and endpoint_port_counts[device] > contract["access_usable_ports"]:
                    report("access-capacity", device, f"{endpoint_port_counts[device]} cabled endpoint ports exceed {contract['access_usable_ports']} usable ports after reserve.")
        if "branch_design" in contract:
            design = contract["branch_design"]
            if design not in BRANCH_DESIGNS:
                report("branch-design", site_key, f"Unsupported branch design {design!r}.")
            else:
                alias, required_paths = BRANCH_DESIGNS[design]
                spec = catalog.get(alias, {})
                site_id = site_key.removeprefix("site/")
                if (contract.get("kind") == "branch" and meta(site_key).get("branch_design") != design) or contract.get("access_hardware") != alias:
                    report("branch-design", site_key, "Site design metadata and contracted access hardware disagree with the selected design.")
                if contract.get("kind") == "branch":
                    selected = plan.get("recipe", {}).get("site_designs", {}).get(site_id, plan.get("design_assignments", {}).get(site_id))
                    if selected != design or plan.get("design_assignments", {}).get(site_id) != design:
                        report("branch-design-selection", site_key, "Actual design differs from recipe override or saved design assignment.")
                    lineage = "birch" if design in {"inherited", "refreshed"} else "cedar"
                    acquired = design == "refreshed" or site_id in plan.get("recipe", {}).get("acquired_sites", [])
                    expected_tenant = "tenant/inherited" if lineage == "birch" and not acquired else "tenant"
                    if meta(site_key).get("lineage") != lineage:
                        report("branch-lineage", site_key, f"Design {design} must preserve {lineage} lineage.")
                    if refs(site_key).get("tenant") != expected_tenant:
                        report("branch-ownership", site_key, "Site ownership differs from the selected acquisition state.")
                    for key in devices_by_site[site_key] + scoped_network_objects[site_key]:
                        if "tenant" in refs(key) and refs(key)["tenant"] != expected_tenant:
                            report("branch-ownership", key, "Object ownership differs from its branch acquisition state.")
                        vrf = refs(key).get("vrf")
                        if vrf and ((lineage == "birch" and not vrf.startswith(f"vrf/inherited/{site_id}/")) or (lineage == "cedar" and vrf.startswith("vrf/inherited/"))):
                            report("branch-vrf-lineage", key, "Routing context does not preserve the branch's authored lineage.")
                access = [key for key in devices_by_site[site_key] if refs(key).get("role") == "role/access"]
                declared = contract.get("access_devices", [])
                if set(declared) != set(access) or len(declared) != len(set(declared)):
                    report("branch-access-inventory", site_key, "Contract access_devices must identify every actual site access switch exactly once.")
                distribution = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/distribution"}
                edges = {key for key in devices_by_site[site_key] if refs(key).get("role") == "role/wan-edge"}
                compact = meta(site_key).get("branch_size") == "small"
                architecture = "compact-routed-edge" if compact else "distribution"
                # Older tiny validation fixtures omit size and retain the two-tier
                # contract; every generated branch/HQ declares its architecture.
                if ("branch_architecture" in contract or "branch_size" in meta(site_key)) and (
                        contract.get("branch_architecture") != architecture or meta(site_key).get("branch_architecture") != architecture):
                    report("branch-architecture", site_key, "Architecture must match the branch footprint, independently of access procurement design.")
                if len(distribution) != (0 if compact else 2) or len(edges) != 2:
                    report("branch-core-inventory", site_key, f"Architecture {architecture} requires {0 if compact else 2} distribution switches and two WAN edges.")
                upstreams = edges if compact else distribution
                access_ports, uplink_ports = set(spec.get("access_ports", [])), set(spec.get("uplink_ports", []))
                reserve = plan.get("recipe", {}).get("reserve_fraction", 0.2)
                usable = math.floor(len(access_ports) * (1 - reserve))
                if not access_ports or not uplink_ports or usable < 1:
                    report("branch-port-catalog", site_key, "Access design needs explicit usable access_ports and uplink_ports in the catalog.")
                else:
                    if contract.get("access_usable_ports") != usable:
                        report("branch-port-budget", site_key, f"Catalog and reserve permit {usable} endpoint ports per switch.")
                    endpoint_total = sum(endpoint_roles[site_key].values())
                    if len(access) < max(2, math.ceil(endpoint_total / usable)):
                        report("branch-access-capacity", site_key, "Actual access switch count cannot satisfy endpoint demand and reserved headroom.")
                if compact:
                    if len(access) != 2:
                        report("branch-compact-capacity", site_key, "Compact routed-edge branches have exactly two access switches and two physical edge attachment ports.")
                    expected_vlans = {f"vlan/{site_id}/{role}" for role in ("users", "atm", "wireless", "security", "management")}
                    peer_ports = []
                    for device in access:
                        ports = [port for port in children[("device", device)] if kind(port) == "interface"]
                        peer_ports.extend(port for port in ports if refs(terminal_peers.get(port)).get("device") in set(access) - {device})
                    third_uplink = spec.get("uplink_ports", [None] * 3)[2] if len(spec.get("uplink_ports", [])) >= 3 else None
                    if (len(peer_ports) != 2 or any(attrs(port).get("name") != third_uplink or
                            not attrs(port).get("enabled", True) or carried_vlans(port) != expected_vlans for port in peer_ports)):
                        report("branch-peer-trunk", site_key, "Compact access switches need one shared enabled trunk on their third catalog uplink carrying all five branch VLANs.")
                    management = {device for device in devices_by_site[site_key] if refs(device).get("role") == "role/management"}
                    management_vlan = f"vlan/{site_id}/management"
                    management_links = []
                    for device in management:
                        for port in children[("device", device)]:
                            peer = terminal_peers.get(port)
                            if refs(peer).get("device") in edges:
                                management_links.append((port, peer))
                    if (len(management) != 1 or len(management_links) != 1 or any(
                            attrs(peer).get("name") != "port12" or not attrs(port).get("enabled", True) or
                            not attrs(peer).get("enabled", True) or carried_vlans(port) != {management_vlan} or
                            carried_vlans(peer) != {management_vlan} for port, peer in management_links)):
                        report("branch-management-path", site_key, "Compact management switch needs one enabled management-VLAN uplink to edge port12.")
                    for device in access:
                        primary = refs(refs(device).get("primary_ip4")).get("assigned_object")
                        peer = terminal_peers.get(primary)
                        if (not attrs(primary).get("mgmt_only") or refs(peer).get("device") not in management or
                                not attrs(primary).get("enabled", True) or not attrs(peer).get("enabled", True) or
                                carried_vlans(primary) != {management_vlan} or carried_vlans(peer) != {management_vlan}):
                            report("branch-management-path", device, "Access primary management IP must use its cabled dedicated port into the management switch.")
                    for edge in edges:
                        edge_ports = {attrs(port).get("name"): port for port in children[("device", edge)] if kind(port) == "interface"}
                        bridges = {refs(edge_ports.get(name)).get("bridge") for name in ("x1", "x2", "port12")}
                        bridge = next(iter(bridges)) if len(bridges) == 1 else None
                        if (not bridge or attrs(bridge).get("type") != "bridge" or refs(bridge).get("device") != edge or
                                not attrs(bridge).get("enabled", True) or carried_vlans(bridge) != expected_vlans):
                            report("branch-edge-bridge", edge, "Compact x1/x2 and management port12 must share the enabled local branch LAN bridge and VLAN set.")
                        for vlan in expected_vlans:
                            gateways = gateway_interfaces[(edge, vlan)]
                            if len(gateways) != 1 or refs(gateways[0]).get("parent") != bridge:
                                report("branch-edge-gateway", edge, f"Need one addressed gateway on the branch bridge for VLAN {vlan}.")
                        primary = refs(refs(edge).get("primary_ip4")).get("assigned_object")
                        if primary not in gateway_interfaces[(edge, management_vlan)]:
                            report("branch-management-path", edge, "Edge primary management IP must belong to its management gateway interface.")
                uplink_contracts = {entry["device"]: entry for entry in contract.get("redundant_uplinks", [])}
                power_contracts = {entry["device"]: entry for entry in contract.get("power_redundancy", [])}
                inherited_upstreams = Counter()
                for device in access:
                    if meta(device).get("hardware") != alias:
                        report("branch-access-hardware", device, f"Design {design} requires catalog hardware {alias}.")
                    ports = [key for key in children[("device", device)] if kind(key) == "interface"]
                    upstream = []
                    for port in ports:
                        peer = terminal_peers.get(port)
                        peer_device = refs(peer).get("device")
                        if peer_device in upstreams:
                            upstream.append(peer_device)
                            if attrs(port).get("name") not in uplink_ports:
                                report("branch-port-role", port, "Upstream attachment must use a catalog uplink port.")
                            if compact and (attrs(peer).get("name") not in {"x1", "x2"} or carried_vlans(port) != expected_vlans or carried_vlans(peer) != expected_vlans):
                                report("branch-edge-attachment", port, "Compact edge attachments must use x1/x2 and carry all five branch VLANs.")
                        if meta(peer_device).get("endpoint") and attrs(port).get("name") not in access_ports:
                            report("branch-port-role", port, "Endpoint attachment must use a catalog access port.")
                    if len(upstream) != required_paths or len(set(upstream)) != required_paths:
                        report("branch-uplinks", device, f"Design {design} requires {required_paths} direct physical uplinks to distinct upstream devices; found {len(upstream)} links to {len(set(upstream))} peers.")
                    uplink_contract = uplink_contracts.get(device, {})
                    if uplink_contract.get("min_distinct_peers") != required_paths or not set(uplink_contract.get("peers", [])) <= upstreams:
                        report("branch-uplink-contract", device, "Uplink contract weakens or misstates the selected branch design.")
                    if design == "inherited":
                        inherited_upstreams.update(set(upstream))
                    psus = [key for key in children[("device", device)] if kind(key) == "power_port"]
                    if len(psus) != len(spec.get("power_ports", [])) or len(psus) != required_paths:
                        report("branch-power-inventory", device, f"Design {design} requires {required_paths} actual catalog power inlets.")
                    if power_contracts.get(device, {}).get("min_distinct_pdus") != required_paths:
                        report("branch-power-contract", device, "Power contract must match the design's installed supply count.")
                if design == "inherited" and upstreams:
                    counts = [inherited_upstreams[device] for device in upstreams]
                    if min(counts) == 0 or max(counts) - min(counts) > 1:
                        report("branch-inherited-distribution", site_key, "Inherited direct single uplinks must alternate across the two upstream devices.")
        if contract.get("kind") == "dc" and recipe.get("profile") in (None, "regional-bank"):
            if contract.get("wan_peak_mbps") != demand_peak:
                report("wan-demand", site_key, f"DC WAN peak must match branch/HQ demand of {demand_peak} Mbps.")
            reserve = contract.get("reserve_fraction", 0)
            for provider in by_kind["provider"]:
                available = Decimal(str(wan_capacity[site_key][provider])) * (1 - Decimal(str(reserve)))
                if available < demand_peak:
                    report("wan-capacity", site_key, f"Provider {provider}: {available:g} usable Mbps from connected, committed circuits; need {demand_peak} Mbps.")
        for connection in contract.get("required_connections", []):
            a, b = connection.get("a"), connection.get("b")
            if not a or not b or a not in component_of or component_of[a] != component_of.get(b):
                report("required-connection", site_key, f"No physical cable path joins {a} and {b}.")
        for uplink in contract.get("redundant_uplinks", []):
            device = uplink["device"]
            peers = device_peers[device] & set(uplink["peers"])
            if bank_dc:
                available_peers = set()
                for port in children[("device", device)]:
                    peer = terminal_peers.get(port)
                    upstream = refs(peer).get("device")
                    if kind(port) != "interface" or kind(peer) != "interface" or upstream not in peers:
                        continue
                    path_cables = {occupied[key] for key in component_members.get(component_of.get(port), []) if key in occupied}
                    if (not path_cables or any(attrs(key).get("status") != "connected" for key in path_cables) or
                            any(not attrs(key).get("enabled", True) for key in (port, peer)) or
                            any(attrs(key).get("status") != "active" for key in (device, upstream))):
                        report("uplink-path-status", port, "Contracted DC uplink needs connected cables, enabled ports and active endpoint devices.")
                        continue
                    available_peers.add(upstream)
                peers = available_peers
            required = uplink.get("min_distinct_peers", 2)
            if len(peers) < required:
                report("uplink-redundancy", device, f"Need {required} distinct permitted upstream devices; found {len(peers)}.")
            if contract.get("kind") in {"branch", "hq", "dc"}:
                for vlan in needed_vlans[device]:
                    vlan_peers = vlan_graph[vlan].get(device, set()) & peers & gateway_reachable.get(vlan, set())
                    if len(vlan_peers) < required:
                        report("uplink-vlan", device, f"VLAN {vlan} must reach {required} contracted upstream peers; found {len(vlan_peers)}.")
        power_checks = contract.get("power_redundancy", [])
        compute_checks = contract.get("compute", [])
        required_services = contract.get("required_services", {})
        if bank_dc or bank_campus:
            # Bank infrastructure requires rack placement independently of its
            # power assertions. Missing placement must not hide a consumer.
            # PDU inputs are traced from consumers, not separate two-supply loads.
            expected_power = []
            for device in devices_by_site[site_key]:
                device_type = refs(device).get("device_type")
                alias = device_type.removeprefix("hardware/") if isinstance(device_type, str) else ""
                model = catalog.get(alias, {})
                if bank_campus and (alias in {"endpoint", "atm", "ap"} or not model.get("power_ports") or model.get("power_outlets")):
                    continue
                rack, location = refs(device).get("rack"), refs(device).get("location")
                if (kind(rack) != "rack" or site(rack) != site_key or kind(location) != "location" or
                        refs(rack).get("location") != location or meta(location).get("space_type") != "equipment_room"):
                    report("dc-device-rack" if bank_dc else "bank-device-rack", device, "Required bank infrastructure must occupy a rack in its local equipment room.")
                if not model.get("power_ports") or model.get("power_outlets"):
                    continue
                expected_power.append({"device": device, "min_distinct_pdus": min(2, len(model["power_ports"])),
                                       "planned_watts": BANK_POWER_WATTS.get(alias, 0) + poe_watts.get(device, 0) + optics_watts.get(device, 0)})
            if (not isinstance(power_checks, list) or any(not isinstance(p, dict) for p in power_checks) or
                    sorted(power_checks, key=lambda p: str(p.get("device"))) != sorted(expected_power, key=lambda p: p["device"])):
                report("dc-power-contract" if bank_dc else "bank-power-contract", site_key, "Bank power assertions must cover every installed consumer with its catalog supply count and independent planning allowance.")
            power_checks = expected_power
        if bank_dc:
            # Inspect actual cluster membership, not the host list emitted by
            # the builder. An unused planned cluster is unrelated inventory.
            expected_compute = []
            for cluster in by_kind["cluster"]:
                if site(cluster) != site_key:
                    continue
                members = children[("cluster", cluster)]
                hosts = sorted(key for key in members if kind(key) == "device")
                if attrs(cluster).get("status") == "planned" and not members:
                    continue
                expected_compute.append({"cluster": cluster, "hosts": hosts, "reserve_fraction": bank_compute_reserve})
                if attrs(cluster).get("status") != "active":
                    report("compute-cluster-status", cluster, "Required DC compute cluster must be active.")
            if len(expected_compute) != 1:
                report("dc-compute-inventory", site_key, "Bank service demand requires one compute cluster per DC.")
            declared_compute = []
            for item in compute_checks if isinstance(compute_checks, list) else []:
                if (isinstance(item, dict) and isinstance(item.get("hosts"), list) and
                        all(isinstance(host, str) for host in item["hosts"])):
                    declared_compute.append(item | {"hosts": sorted(item["hosts"])})
            if (not isinstance(compute_checks, list) or len(declared_compute) != len(compute_checks) or
                    sorted(declared_compute, key=lambda c: str(c.get("cluster"))) != sorted(expected_compute, key=lambda c: c["cluster"])):
                report("dc-compute-contract", site_key, "DC compute assertions must cover actual cluster hosts with the recipe's reserve.")
            compute_checks = expected_compute
            if required_services != bank_service_counts:
                report("dc-service-contract", site_key, "Required DC service counts must follow the bank recipe demand.")
            required_services = bank_service_counts
        for power in power_checks:
            device = power["device"]
            pdus, panels = set(), set()
            for port in children[("device", device)]:
                if kind(port) != "power_port":
                    continue
                outlet = terminal_peers.get(port)
                if kind(outlet) != "power_outlet":
                    report("power-path", port, "Claimed redundant supply must be cabled to a PDU outlet.")
                    continue
                pdu = refs(outlet).get("device")
                inlet = refs(outlet).get("power_port")
                feed = terminal_peers.get(inlet)
                panel = refs(feed).get("power_panel")
                if kind(pdu) != "device" or kind(inlet) != "power_port" or refs(inlet).get("device") != pdu or kind(feed) != "power_feed" or kind(panel) != "power_panel":
                    report("power-path", port, "PDU outlet must trace through its own inlet to a feed and power panel.")
                    continue
                if (bank_dc or bank_campus) and (any(attrs(key).get("status") != "active" for key in (device, pdu, feed)) or
                                any(attrs(occupied.get(key)).get("status") != "connected" for key in (port, inlet))):
                    report("power-path-status", port, "Required bank power path needs active equipment/feed and connected supply cables.")
                    continue
                if contract.get("placement") or bank_campus:
                    rack, location = refs(device).get("rack"), refs(device).get("location")
                    if (not rack or refs(pdu).get("rack") != rack or refs(feed).get("rack") != rack or
                            refs(pdu).get("location") != location or refs(panel).get("location") != location or
                            any(site(key) != site_key for key in (pdu, inlet, feed, panel))):
                        report("power-locality", port, "Supply must stay within its equipment rack, through that rack's PDU/feed and a panel in the same room and site.")
                pdus.add(pdu)
                panels.add(panel)
            minimum = power.get("min_distinct_pdus", 2)
            if len(pdus) < minimum or len(panels) < minimum:
                report("power-redundancy", device, f"Need {minimum} distinct PDUs and upstream panels; found {len(pdus)} PDUs and {len(panels)} panels.")
            if "planned_watts" in power:
                inlets = [key for key in children[("device", device)] if kind(key) == "power_port"]
                total = sum(attrs(key).get("allocated_draw", 0) for key in inlets)
                if total != power["planned_watts"] or total <= 0:
                    report("power-allocation", device, "Normal inlet allocations must sum to the positive device planning allowance.")
                if bank_dc or bank_campus:
                    device_type = refs(device).get("device_type")
                    alias = device_type.removeprefix("hardware/") if isinstance(device_type, str) else ""
                    specs = catalog.get(alias, {}).get("power_ports", [])
                    quotient, remainder = divmod(power["planned_watts"], len(specs) or 1)
                    expected = {spec["name"]: quotient + (n < remainder) for n, spec in enumerate(specs)}
                    if (len(inlets) != len(expected) or {attrs(key).get("name") for key in inlets} != set(expected) or
                            any(type(attrs(key).get("allocated_draw")) is not int or
                                attrs(key).get("allocated_draw") != expected.get(attrs(key).get("name")) for key in inlets)):
                        report("power-allocation", device, "Normal bank inlet draws require the exact catalog-order split of chassis plus PoE and installed-optics allowances.")
                if any(type(attrs(key).get("maximum_draw")) is not int or
                       (attrs(key).get("maximum_draw") != power["planned_watts"] if bank_dc or bank_campus else
                        attrs(key).get("maximum_draw", 0) < power["planned_watts"]) for key in inlets):
                    report("power-failover", device, "Each installed supply must reserve the complete device planning allowance.")
        for compute in compute_checks:
            reserve = compute.get("reserve_fraction", 0)
            if type(reserve) not in (int, float) or not 0 <= reserve < 1:
                report("compute-reserve", site_key, "Compute reserve_fraction must be between zero and one, excluding one.")
                continue
            for host in compute["hosts"]:
                if kind(host) != "device" or refs(host).get("cluster") != compute["cluster"]:
                    report("compute-host", host, "Compute host must belong to the contracted cluster.")
                capacity = meta(host).get("resources", {})
                if bank_dc:
                    if attrs(host).get("status") != "active":
                        report("compute-host-status", host, "Required DC compute host must be active.")
                    if meta(host).get("hardware") != "server" or capacity != BANK_HOST_RESOURCES:
                        report("dc-host-resources", host, "Bank compute hosts require the reference server and its declared planning resources.")
                    capacity = BANK_HOST_RESOURCES if attrs(host).get("status") == "active" else {}
                for resource, field in (("vcpus", "vcpus"), ("memory_mb", "memory"), ("disk_mb", "disk")):
                    available = Decimal(str(capacity.get(resource, 0))) * (Decimal(1) - Decimal(str(reserve)))
                    allocated = sum(attrs(vm).get(field, 0) for vm in vms_by_host[host])
                    if allocated > available:
                        report("compute-capacity", host, f"{resource}: allocated {allocated}, available after reserve {available:g}.")
        # Independently authored legacy contract graphs may omit recipe/profile.
        for service_name, expected_count in required_services.items() if recipe.get("profile") in (None, "regional-bank") else []:
            expected_ports = BANK_SERVICE_PORTS.get(service_name)
            if not expected_ports:
                report("service-policy", site_key, f"No independent service policy exists for {service_name}.")
                continue
            vms = vms_by_service[(site_key, service_name)]
            available_vms = []
            for vm in vms:
                host = refs(vm).get("device")
                interface = refs(refs(vm).get("primary_ip4")).get("assigned_object")
                interface_ready = not bank_dc or (kind(interface) == "vm_interface" and
                    refs(interface).get("virtual_machine") == vm and attrs(interface).get("enabled", True))
                if not interface_ready:
                    report("service-interface", vm, "Required DC service needs its primary address on an enabled VM interface.")
                if interface_ready and (not bank_dc or all(
                        attrs(key).get("status") == "active" for key in (vm, host, refs(vm).get("cluster")))):
                    available_vms.append(vm)
                if kind(host) != "device" or site(host) != site_key or refs(host).get("cluster") != refs(vm).get("cluster"):
                    report("service-placement", vm, "Required service VM must be placed on a host in its contracted site and cluster.")
                services = [key for key in children[("virtual_machine", vm)] if kind(key) == "service"]
                for service in services:
                    if refs(vm).get("primary_ip4") not in refs(service).get("ipaddresses", []):
                        report("service-address", service, "Generated service must explicitly bind its VM's primary address.")
                for name, protocol, port in expected_ports:
                    if not any(attrs(key).get("name") == name and attrs(key).get("protocol") == protocol and isinstance(attrs(key).get("ports"), list) and port in attrs(key)["ports"] and not refs(key).get("device") for key in services):
                        report("service-endpoint", vm, f"Required service {name} must be owned by this VM and expose {protocol}/{port}.")
            if len(available_vms) != expected_count:
                report("service-count", site_key, f"Need {expected_count} available placed {service_name} VMs; found {len(available_vms)}.")
        if "endpoint_count" in contract:
            actual = sum(1 for device in children[("site", site_key)] if kind(device) == "device" and meta(device).get("endpoint"))
            if actual != contract["endpoint_count"]:
                report("endpoint-count", site_key, f"Expected {contract['endpoint_count']} endpoints; found {actual}.")
    findings.extend(validate_operations(plan))
    findings.extend(validate_wireless_context(plan))
    findings.extend(validate_networking(plan, catalog))
    findings.extend(validate_ipv6(plan))
    findings.extend(validate_equipment(plan, catalog))
    findings.extend(validate_datacenter(plan, catalog, poe_watts=poe_watts, optics_watts=optics_watts))
    findings.extend(validate_school(plan, catalog, objects=objects, children=children, peers=terminal_peers,
                    component_of=component_of, component_members=component_members,
                    cable_of=occupied, path_lengths=path_lengths, poe_watts=poe_watts, optics_watts=optics_watts))
    if recipe.get("profile") == "hospital-clinics":
        from .validate_hospital import validate as validate_hospital
        findings.extend(validate_hospital(plan, catalog, objects=objects, children=children, peers=terminal_peers,
                        component_of=component_of, component_members=component_members,
                        cable_of=occupied, path_lengths=path_lengths, poe_watts=poe_watts, optics_watts=optics_watts))
    if recipe.get("profile") == "provider-backbone":
        from .validate_provider import validate as validate_provider
        findings.extend(validate_provider(plan, catalog, objects=objects, children=children, peers=terminal_peers,
                        component_of=component_of, component_members=component_members,
                        cable_of=occupied, path_lengths=path_lengths, poe_watts=poe_watts, optics_watts=optics_watts))
    return sorted(findings, key=lambda item: (item["code"], str(item["object"]), item["message"]))
