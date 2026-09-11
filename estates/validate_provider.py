"""Independent finite provider inventory, physical path and offered-load checks.

The authored policy is restated here; construction helpers and explanatory
contracts are deliberately not validation authorities. Routing is inventory
intent, never an executed reachability or convergence test.
"""

from collections import Counter, defaultdict, deque
from datetime import date
from decimal import Decimal
from ipaddress import ip_interface, ip_network
import math
import re

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics


METROS = {"chicago": ("Chicago", "IL", "Illinois", "America/Chicago"),
          "detroit": ("Detroit", "MI", "Michigan", "America/Detroit"),
          "cleveland": ("Cleveland", "OH", "Ohio", "America/New_York"),
          "milwaukee": ("Milwaukee", "WI", "Wisconsin", "America/Chicago")}
DC_OFFSETS = {"management": 0, "applications": 6, "database": 7, "backup": 8, "storage": 10}
SERVICE_POLICY = (("identity", "premises", 128, 4, 8192, 100000, 443),
                  ("dns", "pops", 16, 2, 4096, 40000, 53),
                  ("monitoring", "pops", 16, 4, 16384, 200000, 443),
                  ("provisioning", "premises", 128, 4, 8192, 100000, 443))


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _key(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,19}", value) is not None


def _recipe(recipe):
    """Bound saved inputs before deriving potentially large obligations."""
    reserve = recipe.get("reserve_fraction")
    if (type(reserve) not in (int, float) or not math.isfinite(reserve) or not 0.1 <= reserve <= 0.4 or
            recipe.get("topology") != "incremental-mesh" or recipe.get("patching") not in ("direct", "panels") or
            recipe.get("reservation_user") != "" or recipe.get("demo") not in ("baseline", "loss-of-power-diversity", "provider-span-maintenance")):
        raise ValueError("Provider policy requires bounded reserve, incremental-mesh topology and a supported baseline/power/span demo.")
    if (not isinstance(recipe.get("namespace"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,18}[a-z0-9]", recipe["namespace"]) or
            not isinstance(recipe.get("name"), str) or not 1 <= len(recipe["name"]) <= 80 or
            not _integer(recipe.get("seed"), 0, 2**63 - 1)):
        raise ValueError("Provider namespace, display name and seed must retain their supported bounds.")
    date.fromisoformat(recipe.get("as_of", ""))
    pool = ip_network(recipe.get("address_pool", ""), strict=True)
    if (pool.version != 4 or not 8 <= pool.prefixlen <= 12 or
            not any(pool.subnet_of(ip_network(p)) for p in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))):
        raise ValueError("Provider site and infrastructure reservations require an aligned RFC1918 /8 through /12.")
    tiers = recipe.get("wan_tiers_mbps")
    if (not isinstance(tiers, list) or not tiers or any(not _integer(t, 1, 1000) for t in tiers) or
            tiers != sorted(set(tiers)) or tiers[-1] != 1000):
        raise ValueError("Customer commitments need strictly increasing integer tiers ending at 1000 Mbps.")
    raw_pops, customers = recipe.get("pops"), recipe.get("customers")
    if not isinstance(raw_pops, list) or not 3 <= len(raw_pops) <= 64:
        raise ValueError("Provider demand needs 3–64 distinct keyed PoPs.")
    pops = {}
    for item in raw_pops:
        if (not isinstance(item, dict) or not _key(item.get("key")) or item["key"] in pops or
                not isinstance(item.get("metro"), str) or item["metro"] not in METROS):
            raise ValueError("Each PoP requires one unique bounded key and an authored metro.")
        pops[item["key"]] = item
    if len({item["metro"] for item in pops.values()}) < 3:
        raise ValueError("The regional backbone requires at least three authored metros.")
    noc_a, noc_b, noc_peak = (recipe.get(field) for field in ("noc_pop_a", "noc_pop_b", "noc_peak_mbps"))
    usable = Decimal(1) - Decimal(str(reserve))
    if (not isinstance(noc_a, str) or not isinstance(noc_b, str) or noc_a not in pops or noc_b not in pops or noc_a == noc_b or
            not _integer(noc_peak, 1, 800) or Decimal(noc_peak) > 1000 * usable):
        raise ValueError("The NOC needs two distinct requested PoPs and headroom on each 1 Gbps handoff.")
    if not isinstance(customers, list) or not 1 <= len(customers) <= 256:
        raise ValueError("Provider demand needs 1–256 keyed private-L3 customers.")
    if (not _integer(recipe.get("asn_base"), 4200000000, 4294967294 - 1023) or
            (recipe["asn_base"] - 4200000000) % 1024):
        raise ValueError("The complete 1024-ASN block must remain in the private 32-bit range.")
    demand, seen, occupancy = {}, set(), Counter((noc_a, noc_b))
    for item in customers:
        if (not isinstance(item, dict) or not _key(item.get("key")) or item["key"] in seen or
                item.get("service") != "private-l3" or not isinstance(item.get("hub_pop"), str) or item["hub_pop"] not in pops or
                not _integer(item.get("site_peak_mbps"), 1, 800) or not _integer(item.get("lan_endpoints"), 1, 12) or
                type(item.get("hub_commit_mbps")) is not int or item["hub_commit_mbps"] not in tiers or
                not isinstance(item.get("sites"), list) or not 1 <= len(item["sites"]) <= len(pops)):
            raise ValueError("Customer keys, service, fixed hub commitment and installed LAN demand must be bounded.")
        seen.add(item["key"])
        entries, premises = set(), []
        for entry in item["sites"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("pop"), str) or entry["pop"] not in pops or
                    entry["pop"] in entries or not _integer(entry.get("count"), 1, 12)):
                raise ValueError("Customer site entries need distinct valid PoPs and 1–12 premises each.")
            entries.add(entry["pop"])
            occupancy[entry["pop"]] += entry["count"]
            for ordinal in range(1, entry["count"] + 1):
                sid = f"ce-{item['key']}-{entry['pop']}-{ordinal:03}"
                if sid in demand:
                    raise ValueError("Composed customer site identities collide; choose unambiguous customer/PoP keys.")
                demand[sid] = (item, entry["pop"], ordinal)
                premises.append(sid)
        if len(entries) < 2 or item["hub_pop"] not in entries:
            raise ValueError("Every private service needs a real hub and premises at two or more PoPs.")
        if Decimal(len(premises) - 1) * item["site_peak_mbps"] > Decimal(item["hub_commit_mbps"]) * usable:
            raise ValueError("Purchased hub commitment cannot cover the declared customer spoke-to-hub peak after reserve.")
        if Decimal(item["site_peak_mbps"]) > 1000 * usable:
            raise ValueError("Customer peak cannot fit the physical 1 Gbps handoff after reserve.")
    if any(count > 12 for count in occupancy.values()):
        raise ValueError("Combined customer and NOC attachments exceed the actual twelve service ports at a PoP.")
    return pops, customers, demand, pool, usable


def _ledger(reservations, scope, expected, capacity):
    value = reservations.get(scope, {})
    if (not isinstance(value, dict) or set(value) != set(expected) or
            any(not _integer(slot, 0, capacity - 1) for slot in value.values()) or len(set(value.values())) != len(value)):
        raise ValueError(f"{scope} must contain exactly its required unique identities and bounded slots.")
    return value


def _connected(adjacency, excluded=None, removed=None):
    nodes = set(adjacency) - {removed}
    if not nodes:
        return False
    seen, pending = set(), [min(nodes)]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(peer for _, peer, edge in adjacency[node] if peer != removed and edge != excluded and peer not in seen)
    return seen == nodes


def _tree(adjacency, origin, excluded=None):
    """Shortest-hop predecessors; callers supply canonical edge-key ordering."""
    predecessors, pending = {origin: None}, deque([origin])
    while pending:
        node = pending.popleft()
        for _, peer, edge in adjacency.get(node, ()):
            if edge != excluded and peer not in predecessors:
                predecessors[peer] = (node, edge)
                pending.append(peer)
    return predecessors


def _loads(adjacency, flows, excluded=None):
    """Directed shortest-hop customer flow; stable edge-key ties, one BFS/source."""
    loads = Counter()
    sources = defaultdict(list)
    for (origin, destination), peak in flows.items():
        sources[origin].append((destination, peak))
    for origin, destinations in sources.items():
        predecessors = _tree(adjacency, origin, excluded)
        for destination, peak in destinations:
            if destination not in predecessors:
                return None
            while destination != origin:
                previous, edge = predecessors[destination]
                loads[(edge, previous, destination)] += peak
                destination = previous
    return loads


def _workloads(premises, pops):
    result = []
    for key, measure, threshold, cpus, memory, disk, port in SERVICE_POLICY:
        count = premises if measure == "premises" else pops
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        elif key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        result.append(dict(key=key, groups=max(1, (count + threshold - 1) // threshold), replicas=2,
            failure_domain="rack", network="applications", vcpus=cpus, memory_mb=memory, disk_mb=disk,
            listeners=listeners, criticality="tier-2" if key == "monitoring" else "tier-1"))
    return result


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe, findings = plan.get("recipe", {}), []
    if recipe.get("profile") != "provider-backbone":
        return findings

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    try:
        pops, customers, premises, pool, usable = _recipe(recipe)
    except (ValueError, TypeError, OverflowError) as exc:
        report("provider-recipe", "plan", str(exc))
        return findings

    if poe_watts is None:
        _, poe_watts = analyze_poe(plan, catalog)
    if optics_watts is None:
        _, optics_watts = analyze_optics(plan, catalog)

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None

    def child(field, key, wanted):
        return [item for item in children[(field, key)] if kind(item) == wanted] if isinstance(key, str) else []

    def active_path(port):
        if not isinstance(port, str) or not peers.get(port):
            return False
        members = component_members.get(component_of.get(port), ())
        cables = {cable_of[p] for p in members if p in cable_of}
        devices = {refs(p).get("device") for p in members} - {None}
        return bool(cables) and all(attrs(c).get("status") == "connected" for c in cables) and all(
            attrs(d).get("status") == "active" for d in devices) and all(
            attrs(p).get("enabled") is True for p in (port, peers[port]) if kind(p) == "interface")

    def vlans(port):
        values = refs(port).get("tagged_vlans", [])
        return ({value for value in values if isinstance(value, str)} if isinstance(values, list) else set()) | (
            {refs(port)["untagged_vlan"]} if isinstance(refs(port).get("untagged_vlan"), str) else set())

    def address(port, network, vrf, host=None, tenant=None):
        # This policy still owns the exact IPv4 /31, /32 and site subnets.
        # Required IPv6 companions are checked separately, not counted as extras.
        ips = [key for key in child("assigned_object", port, "ip_address") if key in ipv4_addresses]
        if len(ips) != 1:
            return False
        key = ips[0]
        ip = ipv4_addresses[key]
        good = ip.network == network and (host is None or int(ip.ip) == int(network.network_address) + host)
        return (good and attrs(key).get("status") == "active" and refs(key).get("vrf") == vrf and
                refs(port).get("vrf") == vrf and (tenant is None or refs(key).get("tenant") == tenant))

    def primary(device, port):
        key = refs(device).get("primary_ip4")
        return key in ipv4_addresses and refs(key).get("assigned_object") == port and attrs(key).get("status") == "active"

    def physical(a, b, speed=None):
        return (kind(a) == kind(b) == "interface" and peers.get(a) == b and active_path(a) and
                all(attrs(p).get("type") not in (None, "virtual", "lag") for p in (a, b)) and
                (speed is None or all(attrs(p).get("speed") == speed for p in (a, b))))

    by_kind = defaultdict(set)
    ipv4_addresses = {}
    ips_by_vrf_address = defaultdict(set)
    prefixes_by_vrf_network = defaultdict(list)
    local_cables = defaultdict(set)
    for key, obj in objects.items():
        by_kind[obj["kind"]].add(key)
        if obj["kind"] == "ip_address" and isinstance(attrs(key).get("address"), str):
            try:
                address_value = ip_interface(attrs(key)["address"])
                if address_value.version == 4:
                    ipv4_addresses[key] = address_value
                if isinstance(refs(key).get("vrf"), str):
                    ips_by_vrf_address[(refs(key)["vrf"], address_value.version, int(address_value.ip))].add(key)
            except ValueError:
                pass  # The shared format check reports malformed addresses.
        if obj["kind"] == "prefix" and isinstance(refs(key).get("vrf"), str) and isinstance(attrs(key).get("prefix"), str):
            prefixes_by_vrf_network[(refs(key)["vrf"], attrs(key)["prefix"])].append(key)
        if obj["kind"] == "cable":
            for end in (refs(key).get("a"), refs(key).get("b")):
                owner = refs(end).get("device")
                site = refs(owner).get("site") if owner else refs(end).get("termination")
                if not isinstance(site, str):
                    site = refs(refs(end).get("power_panel")).get("site")
                if isinstance(site, str):
                    local_cables[site].add(key)
    expected_sites = {"site/dc-01"} | {f"site/pop-{key}" for key in pops} | {f"site/{sid}" for sid in premises}
    if by_kind["site"] != expected_sites:
        report("provider-site-inventory", "plan", "Actual PoP, customer and NOC sites must exactly match requested demand.")
    reservations, allocations = plan.get("reservations"), plan.get("allocations")
    try:
        if not isinstance(reservations, dict) or not isinstance(allocations, dict):
            raise ValueError("Provider allocations and reservations must be objects.")
        order = _ledger(reservations, "provider-pop-order", pops, 64)
        if set(order.values()) != set(range(len(pops))):
            raise ValueError("PoP order must retain one contiguous permanent ordinal per requested PoP.")
        customer_slots = _ledger(reservations, "provider-customers", [c["key"] for c in customers], 256)
        if set(allocations) != {site.removeprefix("site/") for site in expected_sites} or allocations.get("dc-01") != 0:
            raise ValueError("All requested sites need exactly one reservation, with NOC dc-01 fixed at /24-unit zero.")
        small_slots = [slot for sid, slot in allocations.items() if sid != "dc-01"]
        if any(not _integer(slot, 256, pool.num_addresses // 256 - 257) for slot in small_slots) or len(set(small_slots)) != len(small_slots):
            raise ValueError("PoP/customer /24s must be distinct and avoid the complete NOC and infrastructure /16s.")
        parents = {}
        for pop, ordinal in order.items():
            if ordinal < 3:
                continue
            for side in ("a", "b"):
                scope = f"provider-parents/{pop}/{side}"
                mapping = reservations.get(scope)
                if not isinstance(mapping, dict) or len(mapping) != 1:
                    raise ValueError(f"{scope} needs exactly one permanent earlier router parent.")
                parent, slot = next(iter(mapping.items()))
                match = re.fullmatch(r"device/pop-([a-z][a-z0-9-]{0,19})/pe-[ab]", parent) if isinstance(parent, str) else None
                if not match or type(slot) is not int or slot != 0 or match[1] not in order or order[match[1]] >= ordinal:
                    raise ValueError(f"{scope} must refer to a real router at an older PoP.")
                parents[(pop, side)] = parent
            if parents[(pop, "a")].split("/")[1] == parents[(pop, "b")].split("/")[1]:
                raise ValueError(f"New PoP {pop} requires parents at two different earlier PoPs.")
    except ValueError as exc:
        report("provider-allocation", "plan", str(exc))
        return findings

    ordered = sorted(pops, key=order.get)
    routers = {f"device/pop-{pop}/pe-{side}" for pop in pops for side in ("a", "b")}
    spans = {}
    for n in range(3):
        spans[f"circuit/backbone/seed-{n+1:02}"] = (f"device/pop-{ordered[n]}/pe-b", f"device/pop-{ordered[(n+1)%3]}/pe-a")
    for pop in ordered[3:]:
        for side in ("a", "b"):
            spans[f"circuit/backbone/{pop}/{side}"] = (f"device/pop-{pop}/pe-{side}", parents[(pop, side)])
    expected_transport = defaultdict(set)
    for circuit, ends in spans.items():
        for router in ends:
            expected_transport[router].add(circuit)
    service_targets = defaultdict(set)
    for side in ("a", "b"):
        service_targets[recipe[f"noc_pop_{side}"]].add(f"noc/{side}")
    for sid, (_, pop, _) in premises.items():
        service_targets[pop].add(sid)
    try:
        transport_ports = {router: _ledger(reservations, f"provider-transport-ports/{router}", expected_transport[router], 2) for router in routers}
        service_ports = {pop: _ledger(reservations, f"provider-service-ports/{pop}", service_targets[pop], 12) for pop in pops}
        links = set(spans) | {f"pair/pop-{pop}" for pop in pops} | {f"management/pop-{pop}/{s}" for pop in pops for s in ("a", "b")}
        links |= {f"circuit/customer/{sid}" for sid in premises} | {f"circuit/noc/{s}" for s in ("a", "b")} | {f"circuit/transit/{s}" for s in ("a", "b")}
        link_slots = _ledger(reservations, "provider-link-prefixes", links, 16384)
        loop_slots = _ledger(reservations, "provider-loopbacks", routers, 32768)
    except ValueError as exc:
        report("provider-allocation", "plan", str(exc))
        return findings
    infra = int(pool.broadcast_address) - 65535
    link_network = {key: ip_network((infra + 2 * slot, 31)) for key, slot in link_slots.items()}

    def routed(link, endpoints, vrf, tenant="tenant"):
        network = link_network[link]
        prefix = f"prefix/link/{link}"
        good = (kind(prefix) == "prefix" and attrs(prefix).get("prefix") == str(network) and
                attrs(prefix).get("status") == "active" and refs(prefix).get("vrf") == vrf and refs(prefix).get("tenant") == tenant)
        for index, port in enumerate(endpoints):
            good &= address(port, network, vrf, index, tenant) and not vlans(port)
        # An opaque transit peer has no native remote address owner.
        actual = ips_by_vrf_address[(vrf, 4, int(network[0]))] | ips_by_vrf_address[(vrf, 4, int(network[1]))]
        expected = {key for port in endpoints for key in child("assigned_object", port, "ip_address") if key in ipv4_addresses}
        if not good or actual != expected:
            report("provider-routed-address", link, "Routed prefix, exact local endpoint ownership, /31 masks and VRF must match the reserved real link; opaque transit has one local owner.")

    adjacency = {router: [] for router in routers}
    capacity = {}

    def edge(key, a, b, rate):
        if a in adjacency and b in adjacency:
            adjacency[a].append((key, b, key))
            adjacency[b].append((key, a, key))
            capacity[key] = rate

    def circuit(key, port_a, port_z, site_a, site_z, provider, speed, commitment, tenant="tenant", account=None):
        terms = child("circuit", key, "circuit_termination")
        sides = {side: [term for term in terms if attrs(term).get("term_side") == side] for side in ("A", "Z")}
        good = (kind(key) == "circuit" and attrs(key).get("status") == "active" and
                attrs(key).get("commit_rate") == commitment and refs(key).get("provider") == provider and
                refs(key).get("tenant") == tenant and len(terms) == 2 and all(len(value) == 1 for value in sides.values()))
        if account is not None:
            good &= refs(key).get("provider_account") == account and refs(account).get("provider") == provider
        for side, port, site in (("A", port_a, site_a), ("Z", port_z, site_z)):
            term = sides[side][0] if len(sides[side]) == 1 else None
            good &= refs(term).get("termination") == site and attrs(term).get("port_speed") == speed
            if port is not None:
                good &= (kind(port) == "interface" and attrs(port).get("type") not in ("virtual", "lag", None) and
                         attrs(port).get("speed") == speed and peers.get(term) == port and active_path(term))
            else:
                good &= kind(site) == "provider_network" and refs(site).get("provider") == provider and not peers.get(term)
        if not good:
            report("provider-circuit-path", key, "Circuit needs its active purchased commitment, correct provider/account/tenant, actual A/Z sites and both local physical handoffs; only transit may have an opaque remote end.")
        return bool(good)

    site_metros = {f"pop-{pop}": item["metro"] for pop, item in pops.items()}
    site_metros.update({sid: pops[pop]["metro"] for sid, (_, pop, _) in premises.items()})
    site_metros["dc-01"] = pops[recipe["noc_pop_a"]]["metro"]
    infrastructure = []
    for site in sorted(expected_sites):
        sid = site.removeprefix("site/")
        customer = premises[sid][0] if sid in premises else None
        tenant = f"tenant/cust-{customer['key']}" if customer else "tenant"
        category = "customer" if customer else "dc" if sid == "dc-01" else "pop"
        city, state_code, state, zone = METROS[site_metros[sid]]
        region = f"region/{recipe['namespace']}/us/{state_code.lower()}"
        group = f"site-group/{recipe['namespace']}/{category}"
        street = {"pop": "Exchange Avenue", "customer": "Business Way", "dc": "Technology Way"}[category]
        expected_address = f"{100 + 4 * allocations[sid]} {street}\n{city}, {state}\nUnited States"
        description = {"pop": "Provider routing, local management and carrier handoffs",
                       "customer": "Private-L3 customer premises and wired office",
                       "dc": "Provider NOC services, inventory and monitoring"}[category]
        if attrs(site).get("description") != description:
            report("provider-scope-text", site, "Facility description must state its modeled role without adding unmodeled availability or execution guarantees.")
        if (kind(site) != "site" or attrs(site).get("status") != "active" or refs(site).get("tenant") != tenant or
                refs(site).get("region") != region or refs(site).get("group") != group or attrs(site).get("time_zone") != zone or
                attrs(site).get("physical_address") != expected_address or kind(region) != "region" or kind(group) != "site_group" or
                refs(region).get("parent") != f"region/{recipe['namespace']}/us/great-lakes"):
            report("provider-site-context", site, "Site ownership, functional group, address, metro/state and time zone must match the actual requested facility.")
        room = f"location/{sid}"
        required_rooms = {f"{room}/building": ("building", 0, [0, 0, 0], None),
                          f"{room}/floor-01": ("floor", 1, [0, 0, 0], f"{room}/building"),
                          room: ("equipment_room", 1, [24, 18, 0], f"{room}/floor-01")}
        if customer:
            required_rooms[f"{room}/office-01"] = ("office", 1, [8, 18, 0], f"{room}/floor-01")
        actual_rooms = set(child("site", site, "location"))
        if actual_rooms != set(required_rooms):
            report("provider-room-inventory", site, "Each bounded facility needs its real building, ground floor, equipment room and requested customer office.")
        for key, (function, floor, point, parent) in required_rooms.items():
            metadata = objects.get(key, {}).get("meta", {})
            if (kind(key) != "location" or attrs(key).get("status") != "active" or refs(key).get("site") != site or
                    refs(key).get("tenant") != tenant or refs(key).get("parent") != parent or
                    metadata.get("space_type") != function or metadata.get("floor") != floor or metadata.get("position_m") != point or
                    (function == "office" and metadata.get("capacity") != {"workstations": 12})):
                report("provider-room-geometry", key, "Facility rooms must retain their fixed local geometry, capacity, ownership and containment.")
        if sid == "dc-01":
            continue
        devices = child("site", site, "device")
        expected = {f"device/{sid}/console-01": ("console-server", "console-server")}
        if customer:
            expected.update({f"device/{sid}/edge-01": ("customer-edge", "edge"),
                             f"device/{sid}/access-01": ("access", "access")})
            expected.update({f"device/{sid}/pc-{n:03}": ("workstation", "endpoint") for n in range(1, customer["lan_endpoints"] + 1)})
        else:
            expected.update({f"device/{sid}/pe-{side}": ("provider-edge", "provider-edge") for side in ("a", "b")})
            expected[f"device/{sid}/mgmt-01"] = ("management", "access")
        for key, (role, hardware) in expected.items():
            location = f"{room}/office-01" if role == "workstation" else room
            if (kind(key) != "device" or attrs(key).get("status") != "active" or refs(key).get("site") != site or
                    refs(key).get("tenant") != tenant or refs(key).get("role") != f"role/{role}" or
                    refs(key).get("device_type") != f"hardware/{hardware}" or refs(key).get("location") != location):
                report("provider-device-inventory", key, "Requested devices need their exact active hardware, role, tenant and local room.")
            if role != "workstation":
                infrastructure.append(key)
                if role in {"provider-edge", "customer-edge"} and attrs(key).get("description") != f"{role} at {attrs(site).get('name')}":
                    report("provider-scope-text", key, "Router description must retain its actual local role; modeled paths do not establish availability or running forwarding.")
                rack = refs(key).get("rack")
                if kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or attrs(rack).get("status") != "active":
                    report("provider-rack-placement", key, "Network and serial equipment must occupy an active rack in their local equipment room.")
        actual = {device for device in devices if refs(device).get("role") not in {"role/pdu", "role/patch-panel", "role/wall-outlet"}}
        if actual != set(expected):
            report("provider-device-inventory", site, "Active non-passive site inventory must exactly match the bounded PoP or customer composition.")
        for device in expected:
            if expected[device][0] in {"workstation", "console-server"}:
                continue
            port = f"{device}/console_port/Console"
            peer = peers.get(port)
            server = refs(peer).get("device")
            if (kind(port) != "console_port" or kind(peer) != "console_server_port" or
                    server != f"device/{sid}/console-01" or not active_path(port)):
                report("provider-console-path", device, "Every PE, CPE and management/access switch requires its own active local serial console path.")

    for pop in ordered:
        sid, site = f"pop-{pop}", f"site/pop-{pop}"
        rack_points = {f"rack/{sid}/network-01": [4, 4, 0], f"rack/{sid}/network-02": [5.2, 4, 0]}
        if set(child("site", site, "rack")) != set(rack_points) or any(
                objects.get(rack, {}).get("meta", {}).get("position_m") != point for rack, point in rack_points.items()):
            report("provider-rack-geometry", site, "The two PoP network cabinets must retain their distinct fixed positions in the local equipment room.")
        for cable in local_cables[site]:
            racks = []
            for end in (refs(cable).get("a"), refs(cable).get("b")):
                owner = refs(end).get("device") or end
                racks.append(refs(owner).get("rack"))
            length = 3
            if all(isinstance(rack, str) and rack in rack_points for rack in racks) and racks[0] != racks[1]:
                length = math.ceil(sum(abs(a-b) for a, b in zip(rack_points[racks[0]], rack_points[racks[1]])) + 3)
            if attrs(cable).get("length_unit") != "m" or attrs(cable).get("length") != length:
                report("provider-cable-geometry", cable, "Local PoP patches require the fixed cabinet distance plus 3m slack; same-rack and opaque circuit tails use the authored 3m allowance.")
        pe_a, pe_b = (f"device/{sid}/pe-{side}" for side in ("a", "b"))
        if refs(pe_a).get("rack") == refs(pe_b).get("rack"):
            report("provider-router-racks", site, "The two real provider routers must occupy different rack lanes.")
        for router in (pe_a, pe_b):
            expected_ports = {p["name"] for p in catalog.get("provider-edge", {}).get("interfaces", [])} | {"lo0"}
            actual_ports = {attrs(port).get("name") for port in child("device", router, "interface")}
            if actual_ports != expected_ports:
                report("provider-port-inventory", router, "PE interfaces must match the pinned chassis and the one in-band loopback.")
            for n in range(4):
                port = f"{router}/if/et-0/0/{n}"
                if (attrs(port).get("type") != "100gbase-x-qsfp28" or attrs(port).get("enabled") is not (n < 3) or
                        (n < 3 and attrs(port).get("speed") != 100000000) or
                        (n == 3 and (peers.get(port) or child("assigned_object", port, "ip_address")))):
                    report("provider-port-mode", port, "The installed MX204 mode exposes three active 100G cages and leaves the fourth unavailable.")
            for n in range(8):
                port = f"{router}/if/xe-0/1/{n}"
                speed = 1000000 if n < 6 else 10000000
                if attrs(port).get("type") != "10gbase-x-sfpp" or attrs(port).get("enabled") is not True or attrs(port).get("speed") != speed:
                    report("provider-port-mode", port, "The PE preserves 10G physical port types with explicit 1G service and 10G infrastructure operating speeds.")
            fxp0, lo = f"{router}/if/fxp0", f"{router}/if/lo0"
            if attrs(lo).get("description") != "Inband management loopback; dedicated fxp0 remains unaddressed and uncabled":
                report("provider-scope-text", lo, "The management descriptor must explicitly retain in-band dependency and the unaddressed dedicated port.")
            if peers.get(fxp0) or child("assigned_object", fxp0, "ip_address") or refs(fxp0).get("vrf") or vlans(fxp0):
                report("provider-management-mode", fxp0, "Dedicated fxp0 remains unaddressed and uncabled; management is explicitly in-band via lo0.")
            loopnet = ip_network((infra + 32768 + loop_slots[router], 32))
            matching_prefixes = prefixes_by_vrf_network[("vrf/provider", str(loopnet))]
            if (attrs(lo).get("type") != "virtual" or attrs(lo).get("enabled") is not True or refs(lo).get("device") != router or
                    not address(lo, loopnet, "vrf/provider", 0, "tenant") or not primary(router, lo) or len(matching_prefixes) != 1 or
                    attrs(matching_prefixes[0]).get("status") != "active"):
                report("provider-loopback", router, "Each PE needs its own active reserved /32 prefix and primary lo0 address in the provider routing domain.")
            supplies = {f"{router}/power/PEM {n}" for n in range(2)}
            if set(child("device", router, "power_port")) != supplies:
                report("provider-psu-inventory", router, "Each MX204 must retain both real populated PEM 0 and PEM 1 supply inlets.")
            pdus, panels = set(), set()
            allowance = 320 + poe_watts.get(router, 0) + optics_watts.get(router, 0)
            quotient, remainder = divmod(allowance, 2)
            for n in range(2):
                port = f"{router}/power/PEM {n}"
                bay, module = f"{router}/module-bay/Power Supply {n}", f"{router}/module/Power Supply {n}"
                module_type = refs(module).get("module_type")
                outlet = peers.get(port)
                pdu, inlet = refs(outlet).get("device"), refs(outlet).get("power_port")
                feed = peers.get(inlet)
                panel = refs(feed).get("power_panel")
                good = (kind(bay) == "module_bay" and attrs(bay).get("position") == f"PEM {n}" and attrs(bay).get("enabled") is True and
                        refs(bay).get("device") == router and kind(module) == "module" and attrs(module).get("status") == "active" and
                        refs(module).get("device") == router and refs(module).get("module_bay") == bay and
                        attrs(module_type).get("model") == "JPSU-650W-AC-AO" and refs(port).get("module") == module and
                        attrs(port).get("type") == "iec-60320-c14" and type(attrs(port).get("allocated_draw")) is int and
                        attrs(port).get("allocated_draw") == quotient + (n < remainder) and
                        type(attrs(port).get("maximum_draw")) is int and attrs(port).get("maximum_draw") == allowance)
                if not good:
                    report("provider-psu-inventory", port, "Each installed AO module must own its matching named C14 inlet, with the exact split of chassis plus PoE/optics allowances and the full total on failover.")
                if (kind(outlet) != "power_outlet" or kind(inlet) != "power_port" or refs(inlet).get("device") != pdu or
                        kind(feed) != "power_feed" or kind(panel) != "power_panel" or not active_path(port) or not active_path(inlet) or
                        any(attrs(item).get("status") != "active" for item in (pdu, feed)) or
                        any(refs(item).get("rack") != refs(router).get("rack") for item in (pdu, feed)) or
                        refs(pdu).get("location") != f"location/{sid}" or refs(panel).get("location") != f"location/{sid}" or refs(panel).get("site") != site):
                    report("provider-power-path", port, "Each PE supply must reach an active PDU/feed in its own rack and a local upstream power panel.")
                pdus.add(pdu); panels.add(panel)
            if None in pdus or None in panels or len(pdus) != 2 or len(panels) != 2:
                report("provider-power-diversity", router, "The PE's populated supplies require two distinct real PDUs and upstream panels.")
        pair = f"pair/{sid}"
        a, b = f"{pe_a}/if/et-0/0/0", f"{pe_b}/if/et-0/0/0"
        routed(pair, (a, b), "vrf/provider")
        if physical(a, b, 100000000):
            edge(pair, pe_a, pe_b, 100000000)
        else:
            report("provider-pair-path", site, "A connected routed 100G local pair cable must join the two actual PE data ports.")
        for side, router, number in (("a", pe_a, 1), ("b", pe_b, 2)):
            a, b = f"device/{sid}/mgmt-01/if/TenGigabitEthernet1/1/{number}", f"{router}/if/xe-0/1/6"
            routed(f"management/{sid}/{side}", (a, b), "vrf/provider")
            if not physical(a, b, 10000000):
                report("provider-management-uplink", site, "PoP management requires two real routed 10G switch uplinks to the separate PE data ports.")

    for ordinal, (key, (a, b)) in enumerate(spans.items()):
        port_a = f"{a}/if/et-0/0/{1 + transport_ports[a][key]}"
        port_b = f"{b}/if/et-0/0/{1 + transport_ports[b][key]}"
        provider = f"provider/transport-{'a' if ordinal % 2 == 0 else 'b'}"
        routed(key, (port_a, port_b), "vrf/provider")
        if circuit(key, port_a, port_b, refs(a).get("site"), refs(b).get("site"), provider, 100000000, 100000000,
                   account=f"provider-account/{provider}"):
            edge(key, a, b, 100000000)

    def local_network(sid, role, vrf, tenant):
        site = f"site/{sid}"
        container = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
        network = ip_network((int(container.network_address) + (128 if role == "clients" else 0), 25 if role == "clients" else 26))
        vlan, prefix = f"vlan/{sid}/{role}", f"prefix/{sid}/{role}"
        if (kind(prefix) != "prefix" or attrs(prefix).get("status") != "active" or attrs(prefix).get("prefix") != str(network) or
                refs(prefix).get("vrf") != vrf or refs(prefix).get("tenant") != tenant or refs(prefix).get("scope_site") != site or refs(prefix).get("vlan") != vlan or
                kind(vlan) != "vlan" or attrs(vlan).get("vid") != (20 if role == "clients" else 10) or attrs(vlan).get("status") != "active" or
                refs(vlan).get("site") != site or refs(vlan).get("tenant") != tenant or
                attrs(f"prefix/{sid}/reservation").get("prefix") != str(container) or refs(f"prefix/{sid}/reservation").get("vrf") != vrf):
            report("provider-site-network", prefix, "Management /26 and customer /25 segments require their fixed site reservation, VLAN, VRF and tenant.")
        return network, vlan

    def svi(port, network, vlan, vrf, host, tenant, parent=None):
        return (kind(port) == "interface" and attrs(port).get("type") == "virtual" and attrs(port).get("enabled") is True and
                vlans(port) == {vlan} and refs(port).get("parent") == parent and address(port, network, vrf, host, tenant))

    def console_management(sid, switch, network, vlan, vrf, tenant):
        device = f"device/{sid}/console-01"
        port, peer = f"{device}/if/mgmt0", f"{switch}/if/GigabitEthernet1/0/23"
        if (not physical(port, peer) or vlans(port) != {vlan} or vlans(peer) != {vlan} or
                not address(port, network, vrf, 3, tenant) or not primary(device, port)):
            report("provider-console-management", device, "The local console server needs its active management address and actual VLAN channel into the routed site switch.")

    used_pe_ports = {router: {f"{router}/if/et-0/0/0", f"{router}/if/xe-0/1/6"} for router in routers}
    for key, ends in spans.items():
        for router in ends:
            used_pe_ports[router].add(f"{router}/if/et-0/0/{1 + transport_ports[router][key]}")
    for pop in pops:
        sid, vrf = f"pop-{pop}", "vrf/provider"
        network, vlan = local_network(sid, "management", vrf, "tenant")
        switch = f"device/{sid}/mgmt-01"
        port = f"{switch}/if/Vlan10"
        if not svi(port, network, vlan, vrf, 1, "tenant") or not primary(switch, port):
            report("provider-management-gateway", switch, "The PoP console subnet requires its actual routed switch SVI and primary management address.")
        console_management(sid, switch, network, vlan, vrf, "tenant")
        dedicated = f"{switch}/if/GigabitEthernet0/0"
        if peers.get(dedicated) or child("assigned_object", dedicated, "ip_address"):
            report("provider-management-mode", dedicated, "The routed management switch must not duplicate its SVI subnet on the dedicated management port.")

    def service_port(pop, target):
        slot = service_ports[pop][target]
        router = f"device/pop-{pop}/pe-{'a' if slot % 2 == 0 else 'b'}"
        port = f"{router}/if/xe-0/1/{slot // 2}"
        used_pe_ports[router].add(port)
        return router, port

    attachments = {}
    for sid, (customer, pop, ordinal) in premises.items():
        tenant, vrf = f"tenant/cust-{customer['key']}", f"vrf/customer/{customer['key']}"
        cpe, switch = f"device/{sid}/edge-01", f"device/{sid}/access-01"
        cpe_wan = f"{cpe}/if/wan1"
        router, port = service_port(pop, sid)
        attachments[sid] = router
        hub = pop == customer["hub_pop"] and ordinal == 1
        rate = customer["hub_commit_mbps"] if hub else next(t for t in recipe["wan_tiers_mbps"] if Decimal(t) * usable >= customer["site_peak_mbps"])
        key = f"circuit/customer/{sid}"
        routed(key, (cpe_wan, port), vrf, tenant)
        circuit(key, cpe_wan, port, f"site/{sid}", f"site/pop-{pop}", "provider/operator", 1000000, rate * 1000,
                tenant, f"provider-account/customer/{customer['key']}")
        management, management_vlan = local_network(sid, "management", vrf, tenant)
        clients, clients_vlan = local_network(sid, "clients", vrf, tenant)
        lan, upstream = f"{cpe}/if/port1", f"{switch}/if/GigabitEthernet1/0/24"
        if (not physical(lan, upstream) or any(vlans(p) != {management_vlan, clients_vlan} or attrs(p).get("mode") != "tagged" for p in (lan, upstream)) or
                not svi(f"{cpe}/if/Management", management, management_vlan, vrf, 1, tenant, lan) or
                not svi(f"{cpe}/if/Clients", clients, clients_vlan, vrf, 1, tenant, lan) or
                not primary(cpe, f"{cpe}/if/Management") or not svi(f"{switch}/if/Vlan10", management, management_vlan, vrf, 2, tenant) or
                not primary(switch, f"{switch}/if/Vlan10")):
            report("provider-customer-gateway", cpe, "Customer LAN requires the real CPE/switch trunk, separate addressed local management/client gateways and the switch management SVI.")
        console_management(sid, switch, management, management_vlan, vrf, tenant)
        for dedicated in (f"{cpe}/if/mgmt", f"{switch}/if/GigabitEthernet0/0"):
            if peers.get(dedicated) or child("assigned_object", dedicated, "ip_address"):
                report("provider-management-mode", dedicated, "Customer management uses the routed local LAN; dedicated management ports remain unused.")
        for n in range(1, customer["lan_endpoints"] + 1):
            device = f"device/{sid}/pc-{n:03}"
            port, access = f"{device}/if/eth0", f"{switch}/if/GigabitEthernet1/0/{n}"
            if (not physical(port, access) or any(vlans(p) != {clients_vlan} for p in (port, access)) or
                    not address(port, clients, vrf, tenant=tenant) or not primary(device, port)):
                report("provider-customer-endpoint", device, "Every requested PC needs its own active access channel, customer VLAN/VRF and actual primary address.")
            room, closet = f"location/{sid}/office-01", f"location/{sid}"
            point = [10 + 2 * ((n-1) % 4), 19 + 2 * ((n-1) // 4), 0.8]
            length = math.ceil(sum(abs(point[i] - (24, 18, 0)[i]) for i in range(3)) + 10)
            metadata = objects.get(device, {}).get("meta", {})
            placement = dict(room=room, function="office", floor=1, position_m=point, cable_origin=closet)
            if (metadata.get("placement") != placement or metadata.get("access_channel_length_m") != length or
                    path_lengths.get(port) != length or not 0 < length <= 80):
                report("provider-customer-route", device, "Customer desk positions and actual local channel lengths must follow the fixed office geometry.")
            members = component_members.get(component_of.get(port), ())
            passive = {refs(p).get("device") for p in members if kind(p) in {"front_port", "rear_port"}}
            if recipe["patching"] == "panels":
                outlets = {d for d in passive if refs(d).get("device_type") == "hardware/wall-outlet"}
                panels = {d for d in passive if refs(d).get("device_type") == "hardware/patch-panel"}
                if (len(passive) != 2 or len(outlets) != 1 or len(panels) != 1 or
                        len({cable_of[p] for p in members if p in cable_of}) != 3 or
                        any(refs(d).get("location") != room for d in outlets) or any(refs(d).get("location") != closet for d in panels)):
                    report("provider-customer-patching", device, "Panel channels must use this customer's office outlet and local equipment-room panel.")
            elif len(members) != 2:
                report("provider-customer-patching", device, "Direct customer access requires exactly one real cable channel.")
        used_copper = {f"{switch}/if/GigabitEthernet1/0/{n}" for n in range(1, customer["lan_endpoints"] + 1)} | {
            f"{switch}/if/GigabitEthernet1/0/23", f"{switch}/if/GigabitEthernet1/0/24"}
        actual_copper = {p for p in child("device", switch, "interface") if attrs(p).get("type") == "1000base-t" and peers.get(p)}
        if actual_copper != used_copper or Decimal(len(used_copper)) > 24 * usable:
            report("provider-customer-port-capacity", switch, "Actual customer access attachments must use the finite fixed ports and retain declared copper-port reserve.")

    noc_edges = set()
    for side in ("a", "b"):
        pop = recipe[f"noc_pop_{side}"]
        if service_ports[pop].get(f"noc/{side}") != 0:
            report("provider-noc-reservation", "site/dc-01", "Each NOC handoff must retain the first reserved service position at its selected PoP.")
        router, pe = service_port(pop, f"noc/{side}")
        device = f"device/dc-01/edge-001-{side}"
        port = f"{device}/if/wan1"
        noc_edges.add(device)
        key = f"circuit/noc/{side}"
        routed(key, (port, pe), "vrf/provider")
        if (not circuit(key, port, pe, "site/dc-01", f"site/pop-{pop}", "provider/operator", 1000000, 1000000,
                        account="provider-account/operator/noc") or refs(device).get("role") != "role/wan-edge" or
                refs(device).get("device_type") != "hardware/edge" or refs(device).get("site") != "site/dc-01" or
                attrs(device).get("status") != "active" or attrs(port).get("type") != "1000base-t" or router not in adjacency):
            report("provider-noc-wan", key, "The NOC needs each distinct real active 1G edge/circuit path to its selected PoP and independently owned provider /31.")
    if len({refs(d).get("rack") for d in noc_edges}) != 2:
        report("provider-noc-diversity", "site/dc-01", "The two NOC provider attachments must retain separate local edge racks.")
    for index, side in enumerate(("a", "b")):
        pop = ordered[index]
        router = f"device/pop-{pop}/pe-{side}"
        port = f"{router}/if/xe-0/1/7"
        used_pe_ports[router].add(port)
        key, provider = f"circuit/transit/{side}", f"provider/transit-{side}"
        if attrs(f"provider-network/transit/{side}").get("description") != "External transit interior and remote interface owner are unknown":
            report("provider-scope-text", f"provider-network/transit/{side}", "External transit must not invent an inspected remote interior or interface owner.")
        routed(key, (port,), "vrf/provider")
        circuit(key, port, None, f"site/pop-{pop}", f"provider-network/transit/{side}", provider, 10000000, 10000000,
                account=f"provider-account/{provider}")
    if by_kind["circuit"] != set(spans) | {f"circuit/customer/{sid}" for sid in premises} | {f"circuit/noc/{s}" for s in ("a", "b")} | {f"circuit/transit/{s}" for s in ("a", "b")}:
        report("provider-circuit-inventory", "plan", "Physical circuit inventory must exactly cover requested backbone, customer, NOC and external transit attachments.")
    for router, required in used_pe_ports.items():
        actual = {p for p in child("device", router, "interface") if peers.get(p)}
        if actual != required:
            report("provider-port-use", router, "Only the exact reserved physical PE ports may be cabled; unused service/transport/transit positions remain free.")

    base = recipe["asn_base"]
    expected_asns = {"asn/operator": base, "asn/transit-a": base + 1, "asn/transit-b": base + 2}
    expected_asns.update({f"asn/customer/{key}": base + 256 + slot for key, slot in customer_slots.items()})
    expected_providers = {"provider/operator", "provider/transport-a", "provider/transport-b", "provider/transit-a", "provider/transit-b"}
    if by_kind["provider"] != expected_providers or by_kind["asn"] != set(expected_asns):
        report("provider-routing-registry", "plan", "Provider and ASN inventories must match actual operator, transport, upstream and customer identities.")
    if (kind("rir/private") != "rir" or attrs("rir/private").get("is_private") is not True or
            kind("asn-range/private") != "asn_range" or attrs("asn-range/private").get("start") != base or
            attrs("asn-range/private").get("end") != base + 1023 or refs("asn-range/private").get("rir") != "rir/private"):
        report("provider-routing-registry", "asn-range/private", "The estate must retain its complete aligned private 32-bit ASN reservation and private registry.")
    for key, number in expected_asns.items():
        if kind(key) != "asn" or attrs(key).get("asn") != number or refs(key).get("rir") != "rir/private":
            report("provider-asn", key, "Each real routing identity must retain its reserved private ASN and registry.")
    for provider, asn in (("provider/operator", "asn/operator"), ("provider/transit-a", "asn/transit-a"), ("provider/transit-b", "asn/transit-b")):
        if refs(provider).get("asns") != [asn]:
            report("provider-asn-consumer", provider, "Provider ASN association must refer to its own actual operator or upstream identity.")
    for site in expected_sites:
        sid = site.removeprefix("site/")
        asn = f"asn/customer/{premises[sid][0]['key']}" if sid in premises else "asn/operator"
        if refs(site).get("asns") != [asn]:
            report("provider-asn-consumer", site, "Site routing ownership must reference its actual customer or operator ASN.")
    if (kind("vrf/provider") != "vrf" or refs("vrf/provider").get("tenant") != "tenant" or
            attrs("vrf/provider").get("enforce_unique") is not True or kind("provider-network/operator") != "provider_network" or
            refs("provider-network/operator").get("provider") != "provider/operator"):
        report("provider-routing-domain", "vrf/provider", "The real backbone requires an operator-owned unique provider routing context and matching service network.")
    expected_vcs, expected_terms = set(), set()
    expected_accounts = {f"provider-account/provider/{family}-{side}" for family in ("transport", "transit") for side in ("a", "b")} | {"provider-account/operator/noc"}
    for account in expected_accounts:
        provider = "provider/operator" if account.endswith("/noc") else account.removeprefix("provider-account/")
        if kind(account) != "provider_account" or refs(account).get("provider") != provider or "tenant" in refs(account):
            report("provider-account", account, "Procurement accounts must reference their actual provider; native accounts have no tenant field.")
    flows = Counter()
    for customer in customers:
        key = customer["key"]
        tenant, vrf, target = f"tenant/cust-{key}", f"vrf/customer/{key}", f"route-target/customer/{key}"
        vc, account = f"virtual-circuit/customer/{key}", f"provider-account/customer/{key}"
        if (attrs(vc).get("description") != "Customer private-L3 membership; authored spoke-to-hub traffic demand" or
                attrs(vc).get("comments") != "Peer membership is service inventory, not a declared all-to-all traffic matrix or configured BGP sessions."):
            report("provider-scope-text", vc, "The service must distinguish peer membership from its finite offered traffic and avoid claiming configured forwarding or availability.")
        expected_vcs.add(vc); expected_accounts.add(account)
        if (kind(tenant) != "tenant" or kind(vrf) != "vrf" or refs(vrf).get("tenant") != tenant or attrs(vrf).get("enforce_unique") is not True or
                refs(vrf).get("import_targets") != [target] or refs(vrf).get("export_targets") != [target] or
                kind(target) != "route_target" or attrs(target).get("name") != f"{base}:{customer_slots[key] + 1}" or refs(target).get("tenant") != tenant):
            report("provider-customer-routing", vrf, "Each private customer needs its own tenant VRF and exact symmetric reserved route target.")
        if (kind(account) != "provider_account" or refs(account).get("provider") != "provider/operator" or "tenant" in refs(account) or
                attrs(account).get("account") != f"{recipe['namespace']}-customer-{key}" or
                kind(vc) != "virtual_circuit" or attrs(vc).get("status") != "active" or refs(vc).get("provider_network") != "provider-network/operator" or
                refs(vc).get("provider_account") != account or refs(vc).get("tenant") != tenant or
                refs(vc).get("type") != "virtual-circuit-type/private-l3"):
            report("provider-customer-service", vc, "The active private-L3 service must belong to the correct customer, operator network and customer procurement account.")
        members = {sid for sid, (item, _, _) in premises.items() if item["key"] == key}
        hub = f"ce-{key}-{customer['hub_pop']}-001"
        for sid in members:
            term = f"virtual-circuit-termination/{sid}"
            expected_terms.add(term)
            port, parent = f"device/{sid}/edge-01/if/PrivateL3", f"device/{sid}/edge-01/if/wan1"
            if attrs(port).get("description") != "Private routed service membership over this actual circuit":
                report("provider-scope-text", port, "The virtual interface describes inventory membership over its actual access circuit, not executed tunneling or routing.")
            if (kind(term) != "virtual_circuit_termination" or refs(term).get("virtual_circuit") != vc or refs(term).get("interface") != port or
                    attrs(term).get("role") != "peer" or kind(port) != "interface" or attrs(port).get("type") != "virtual" or attrs(port).get("enabled") is not True or
                    refs(port).get("device") != f"device/{sid}/edge-01" or refs(port).get("parent") != parent or refs(port).get("vrf") != vrf or
                    not active_path(parent) or len(child("interface", port, "virtual_circuit_termination")) != 1 or child("assigned_object", port, "ip_address")):
                report("provider-virtual-membership", term, "Each requested CPE needs exactly one active virtual peer membership over its actual physical customer handoff and customer VRF.")
            if sid != hub:
                flows[(attachments[sid], attachments[hub])] += customer["site_peak_mbps"] * 1000
    if by_kind["virtual_circuit"] != expected_vcs or by_kind["virtual_circuit_termination"] != expected_terms:
        report("provider-service-inventory", "plan", "Every requested customer and premise must contribute exactly its private-L3 service membership.")
    if by_kind["provider_account"] != expected_accounts:
        report("provider-account-inventory", "plan", "Provider accounts must belong to the actual transport, transit, NOC and customer service obligations.")
    for router in adjacency:
        adjacency[router].sort()
    if not _connected(adjacency):
        report("provider-backbone-connectivity", "plan", "All requested active PEs must be connected through actual pair cables and complete two-sided leased spans.")
    for router in sorted(routers):
        if not _connected(adjacency, removed=router):
            report("provider-router-connectivity", router, "Remaining PEs must remain connected after this single router removal; attached single-homed customers are not protected.")
    for removed in sorted(capacity):
        if not _connected(adjacency, excluded=removed):
            report("provider-link-connectivity", removed, "The actual active backbone must retain connectivity after each single pair-link or inter-PoP-span removal.")
    for removed in (None, *sorted(spans)):
        loads = _loads(adjacency, flows, excluded=removed)
        if loads is None:
            report("provider-customer-route", removed or "plan", "The authored customer spoke-to-hub flow needs a complete real PE path in normal operation and after each inter-PoP span loss.")
            continue
        for (link, origin, destination), load in loads.items():
            if Decimal(load) > Decimal(capacity[link]) * usable:
                report("provider-route-capacity", link, f"Customer spoke-to-hub flow {load/1000:g} Mbps from {origin} to {destination} exceeds purchased usable capacity after {removed or 'no span'} removal; NOC, transit and other traffic are excluded.")
    findings.extend(validate_power(objects, catalog, [d for d in infrastructure if d not in routers], children, peers, cable_of, poe_watts=poe_watts, optics_watts=optics_watts))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=_workloads(len(premises), len(pops)),
                    peak=recipe["noc_peak_mbps"], reserve=recipe["reserve_fraction"], strict_sites=False, network_offsets=DC_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
