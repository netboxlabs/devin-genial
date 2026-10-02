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
from .model import selected_alias
from .naming import role_label, titleize
# Authored address localities (suburbs map to their metro); geography data, not builder policy.
from .places import ADDRESS_STREETS, LOCALITIES, MILWAUKEE_COUNTY, carrier_suite


METROS = {"chicago": ("Chicago", "IL", "Illinois", "America/Chicago"),
          "detroit": ("Detroit", "MI", "Michigan", "America/Detroit"),
          "cleveland": ("Cleveland", "OH", "Ohio", "America/New_York"),
          "milwaukee": ("Milwaukee", "WI", "Wisconsin", "America/Chicago")}
DC_OFFSETS = {"management": 0, "applications": 6, "database": 7, "backup": 8, "storage": 10}
SERVICE_POLICY = (("identity", "premises", 128, 4, 8192, 100000, 443),
                  ("dns", "pops", 16, 2, 4096, 40000, 53),
                  ("monitoring", "pops", 16, 4, 16384, 200000, 443),
                  ("provisioning", "premises", 128, 4, 8192, 100000, 443))
# The BGP obligation, restated here rather than read from estates/bgp.py. These
# records document an intended peering: nothing is configured, applied or
# established anywhere in this generator. Records carry operational fields only
# (the limitation lives in docs/modeling.md and the report), and the reviewed
# kind set and field set stay closed: a policy *rule*, a community, a prefix
# list or a session-state field would read as configuration and is refused.
SPAN_KEY = re.compile(r"circuit/backbone/([a-z][a-z0-9-]{0,19})-([ab])/([a-z][a-z0-9-]{0,19})-([ab])")
# Metro centres (lat, lon), restated from the authored geography.
METRO_POINTS = {"chicago": (41.8781, -87.6298), "detroit": (42.3314, -83.0458),
                "cleveland": (41.4993, -81.6944), "milwaukee": (43.0389, -87.9065)}
# Carrier-owned public space (RFC 5737) and AS numbers (RFC 5398), restated.
PUBLIC_POOLS = {"loopbacks": ip_network("192.0.2.0/24"), "pair": ip_network("198.51.100.0/25"),
                "backbone": ip_network("203.0.113.0/24")}
PUBLIC_AGGREGATES = ("192.0.2.0/24", "198.51.100.0/25", "203.0.113.0/24")
# Each upstream's own transit assignment: outside every operator aggregate.
UPSTREAM_POOLS = {"a": ip_network("198.51.100.224/28"), "b": ip_network("198.51.100.240/28")}
# The out-of-band ISP's RFC 6598 /30 per PoP console server, IPv4 only.
OOB_POOL = ip_network("100.64.0.0/24")
DOCUMENTATION_ASNS = range(64496, 64512)
ROUTE_FACTOR = 1.3
# A premises sits in its serving PoP's area: within this distance, and nearer
# it than any other same-metro PoP allocated before the premises.
PREMISES_KM = 25
# Leased inter-metro spans are 100G wavelengths on the 100G port they land on.
LEASED_COMMIT = 100000000
CARRIER_NAMES = ("transport-a", "transport-b", "transit-a", "transit-b", "oob")
# Routing contexts, restated: the core is the global table (no VRF);
# PoP/NOC/CE management is the Carrier Management VRF with a hub-and-spoke
# management extranet to every customer VRF.
CORE, MANAGEMENT, OOB = None, "vrf/provider", "vrf/oob"
HUB_RT, SPOKE_RT = 9000, 9001
FXP0_HOSTS = (4, 5)
BGP_KINDS = {"bgp_routing_policy", "bgp_peer_group", "bgp_session"}
BGP_FIELDS = {"bgp_routing_policy": {"name", "weight", "description"},
              "bgp_peer_group": {"name", "description"},
              "bgp_session": {"name", "status", "description"}}
# The service note must point at the documented CE-to-PE sessions, never deny
# them: the BGP inventory above is emitted for every premises.
VIRTUAL_CIRCUIT_NOTE = "Each premises' CE-to-PE peering is in the Customer Private L3 BGP peer group."
BGP_POLICIES = {
    "transit-in": ("Transit Import", 100,
                   "Import policy for upstream transit peers"),
    "transit-out": ("Transit Export", 110,
                    "Export policy for upstream transit peers"),
    "customer-in": ("Customer Import", 200,
                    "Import policy for private L3 customer edges"),
    "customer-out": ("Customer Export", 210,
                     "Export policy for private L3 customer edges"),
}
BGP_GROUPS = {
    "ibgp-core": ("iBGP Core", "Internal peerings between provider edge loopbacks",
                  (), (), True),
    "transit": ("Transit Upstream", "External upstream peerings at the backbone transit handoffs",
                ("transit-in",), ("transit-out",), False),
    "customer": ("Customer Private L3", "Customer edge peerings on private-L3 access circuits",
                 ("customer-in",), ("customer-out",), False),
}


# Premises lifecycle, restated from the recipe: an onboarding customer is
# planned; a planned entry under an active customer is provisioning; an entry
# may be decommissioning. Every record the premises owns carries the status
# below, and none of them counts as healthy, in-service capacity.
LIFE = {
    "active": dict(site="active", device="active", circuit="active", cable="connected", rack="active",
                   ip="active", prefix="active", vlan="active", bgp="active"),
    "planned": dict(site="planned", device="planned", circuit="planned", cable="planned", rack="planned",
                    ip="reserved", prefix="reserved", vlan="reserved", bgp="planned"),
    "provisioning": dict(site="staging", device="staged", circuit="provisioning", cable="planned", rack="planned",
                         ip="reserved", prefix="reserved", vlan="reserved", bgp="planned"),
    "decommissioning": dict(site="decommissioning", device="decommissioning", circuit="deprovisioning",
                            cable="decommissioning", rack="deprecated", ip="deprecated", prefix="deprecated",
                            vlan="deprecated", bgp="offline"),
}


def _stage(customer, pop):
    if customer.get("status", "active") == "planned":
        return "planned"
    status = next(e.get("status", "active") for e in customer["sites"] if e["pop"] == pop)
    return "provisioning" if status == "planned" else status


def _km(a, b):
    """Great-circle kilometres (haversine, mean Earth radius)."""
    (la1, lo1), (la2, lo2) = ((math.radians(x), math.radians(y)) for x, y in (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _operator_code(name):
    """Restated: the operator's service-ID prefix is its name's initials."""
    words = re.findall(r"[A-Za-z]+", name)
    code = "".join(word[0] for word in words).upper()[:4]
    return code if len(code) >= 2 else (words[0][:3].upper() if words else "OPR")


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
                not _integer(item.get("site_peak_mbps"), 1, 800) or not _integer(item.get("lan_endpoints"), 0, 12) or
                type(item.get("hub_commit_mbps")) is not int or item["hub_commit_mbps"] not in tiers or
                not isinstance(item.get("sites"), list) or not 1 <= len(item["sites"]) <= len(pops) or
                item.get("status", "active") not in ("active", "planned")):
            raise ValueError("Customer keys, service, fixed hub commitment, installed LAN demand and status must be bounded.")
        seen.add(item["key"])
        entries, premises = set(), []
        for entry in item["sites"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("pop"), str) or entry["pop"] not in pops or
                    entry["pop"] in entries or not _integer(entry.get("count"), 1, 12) or
                    entry.get("status", "active") not in ("active", "planned", "decommissioning") or
                    (item.get("status", "active") == "planned" and entry.get("status", "planned") != "planned")):
                raise ValueError("Customer site entries need distinct valid PoPs, 1–12 premises each and a "
                                 "lifecycle status; every entry of a planned customer is planned.")
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
        if item.get("status", "active") == "active" and _stage(item, item["hub_pop"]) != "active":
            raise ValueError("An active customer's hub premises stays active; its spokes route through it.")
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
    # The recipe declares the vendor line; every port name below follows the
    # resolved catalog entry rather than one vendor's naming.
    access_alias = selected_alias(recipe, "access")
    access_spec = catalog.get(access_alias, {})
    access_ports = access_spec.get("access_ports", [])
    access_uplinks = access_spec.get("uplink_ports", [])
    access_mgmt = next((p["name"] for p in access_spec.get("interfaces", []) if p.get("mgmt_only")), None)

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    if len(access_ports) < 3 or len(access_uplinks) < 2 or access_mgmt is None:
        report("provider-access-catalog", "catalog", "The selected access line must supply its ordered "
               "access ports, at least two uplinks and one management port.")
        return findings

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

    # The lifecycle stage of the premises being checked; "active" elsewhere.
    now = ["active"]

    def life(field):
        return LIFE[now[0]][field]

    def active_path(port):
        """An in-service path; at a non-active premises, the path in its own lifecycle status."""
        if not isinstance(port, str) or not peers.get(port):
            return False
        members = component_members.get(component_of.get(port), ())
        cables = {cable_of[p] for p in members if p in cable_of}
        devices = {refs(p).get("device") for p in members} - {None}
        # The serving PE stays active while a premises it serves is not.
        return bool(cables) and all(attrs(c).get("status") == life("cable") for c in cables) and all(
            attrs(d).get("status") in {"active", life("device")} for d in devices) and (
            now[0] != "active" or all(attrs(d).get("status") == "active" for d in devices)) and all(
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
        return (good and attrs(key).get("status") == life("ip") and refs(key).get("vrf") == vrf and
                refs(port).get("vrf") == vrf and (tenant is None or refs(key).get("tenant") == tenant))

    def primary(device, port):
        key = refs(device).get("primary_ip4")
        return key in ipv4_addresses and refs(key).get("assigned_object") == port and attrs(key).get("status") == life("ip")

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
                if isinstance(refs(key).get("vrf"), (str, type(None))):
                    ips_by_vrf_address[(refs(key).get("vrf"), address_value.version, int(address_value.ip))].add(key)
            except ValueError:
                pass  # The shared format check reports malformed addresses.
        if obj["kind"] == "prefix" and isinstance(refs(key).get("vrf"), (str, type(None))) and isinstance(attrs(key).get("prefix"), str):
            prefixes_by_vrf_network[(refs(key).get("vrf"), attrs(key)["prefix"])].append(key)
        if obj["kind"] == "cable":
            for end in (refs(key).get("a"), refs(key).get("b")):
                owner = refs(end).get("device")
                site = refs(owner).get("site") if owner else refs(end).get("termination")
                if kind(site) == "location":
                    site = refs(site).get("site")
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
        # The backbone span ledger: every key names both PE ends, and the
        # ordinal is the permanent order carriers alternate in.
        span_ledger = reservations.get("provider-backbone-spans")
        if (not isinstance(span_ledger, dict) or not span_ledger or
                sorted(span_ledger.values()) != list(range(len(span_ledger)))):
            raise ValueError("provider-backbone-spans must be a dense permanent sequence of span identities.")
        spans = {}
        for key in sorted(span_ledger, key=span_ledger.get):
            match = SPAN_KEY.fullmatch(key) if isinstance(key, str) else None
            if not match or match[1] not in pops or match[3] not in pops or match[1] == match[3]:
                raise ValueError(f"{key!r} must join PEs at two different requested PoPs.")
            spans[key] = (f"device/pop-{match[1]}/pe-{match[2]}", f"device/pop-{match[3]}/pe-{match[4]}")
        launch = _ledger(reservations, "provider-pop-launch", pops, 64)
        if set(launch.values()) != set(range(len(pops))):
            raise ValueError("PoP launch order must retain one contiguous permanent ordinal per requested PoP.")
        if "provider-span-upgrades" in reservations:
            raise ValueError("Leased spans are 100G wavelengths; the retired provider-span-upgrades ledger must be absent.")
    except ValueError as exc:
        report("provider-allocation", "plan", str(exc))
        return findings

    ordered = sorted(pops, key=order.get)
    routers = {f"device/pop-{pop}/pe-{side}" for pop in pops for side in ("a", "b")}
    pop_of = lambda router: router.split("/")[1].removeprefix("pop-")
    metro_of = lambda router: pops[pop_of(router)]["metro"]
    # Carrier policy, restated: same-metro spans are owned dark fiber; spans
    # between metros alternate the two transport carriers in ledger order.
    span_provider, alternation = {}, Counter()
    for key, (a, b) in spans.items():
        if metro_of(a) == metro_of(b):
            span_provider[key] = "provider/operator"
        else:
            pair = frozenset((metro_of(a), metro_of(b)))
            span_provider[key] = f"provider/transport-{'ab'[alternation[pair] % 2]}"
            alternation[pair] += 1
    # Inter-metro spans may only join neighbouring metros along the lakeshore,
    # derived here by longitude rather than the builder's spanning tree.
    chain = sorted({item["metro"] for item in pops.values()}, key=lambda m: METRO_POINTS[m][1])
    neighbours = {frozenset(pair) for pair in zip(chain, chain[1:])}
    for key, (a, b) in spans.items():
        if metro_of(a) != metro_of(b) and frozenset((metro_of(a), metro_of(b))) not in neighbours:
            report("provider-backbone-geography", key, "An inter-metro span must join neighbouring metros; it may not skip a metro or cross a lake.")
    for pair in neighbours:
        between = [key for key, (a, b) in spans.items() if frozenset((metro_of(a), metro_of(b))) == pair]
        sides = {metro: {end for key in between for end in spans[key] if metro_of(end) == metro} for metro in pair}
        if (len(between) < 2 or {refs(k).get("provider") for k in between} != {"provider/transport-a", "provider/transport-b"} or
                any(len(ends) < 2 for ends in sides.values())):
            report("provider-backbone-diversity", "plan", f"Metros {' and '.join(sorted(pair))} need two spans from two "
                   "different carriers, landing on two different PEs at each end.")
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
        private = {f"management/pop-{pop}/{s}" for pop in pops for s in ("a", "b")}
        private |= {f"circuit/customer/{sid}" for sid in premises} | {f"circuit/noc/{s}" for s in ("a", "b")}
        link_slots = _ledger(reservations, "provider-link-prefixes", private, 16384)
        public = {"pair": ("provider-pair-links", {f"pair/pop-{pop}" for pop in pops}),
                  "backbone": ("provider-span-links", set(spans)),
                  "transit": ("provider-transit-links", {f"circuit/transit/{s}" for s in ("a", "b")})}
        public_slots = {family: _ledger(reservations, scope, keys, 2 if family == "transit" else PUBLIC_POOLS[family].num_addresses // 2)
                        for family, (scope, keys) in public.items()}
        # Loopbacks take host .1 onward of their /24: never .0, never .255.
        loop_slots = _ledger(reservations, "provider-loopbacks", routers, PUBLIC_POOLS["loopbacks"].num_addresses - 2)
        oob_slots = _ledger(reservations, "provider-oob-links", pops, OOB_POOL.num_addresses // 4)
    except ValueError as exc:
        report("provider-allocation", "plan", str(exc))
        return findings
    infra = int(pool.broadcast_address) - 65535
    link_network = {key: ip_network((infra + 2 * slot, 31)) for key, slot in link_slots.items()}
    for family, slots in public_slots.items():
        if family == "transit":
            # Each upstream numbers its /31 from its own assignment.
            link_network.update({key: ip_network((int(UPSTREAM_POOLS[key[-1]].network_address), 31)) for key in slots})
            continue
        base = int(PUBLIC_POOLS[family].network_address)
        link_network.update({key: ip_network((base + 2 * slot, 31)) for key, slot in slots.items()})

    def routed(link, endpoints, vrf, tenant="tenant", hosts=(0, 1)):
        network = link_network[link]
        prefix = f"prefix/link/{link}"
        good = (kind(prefix) == "prefix" and attrs(prefix).get("prefix") == str(network) and
                attrs(prefix).get("status") == life("prefix") and refs(prefix).get("vrf") == vrf and refs(prefix).get("tenant") == tenant)
        for host, port in zip(hosts, endpoints):
            good &= address(port, network, vrf, host, tenant) and not vlans(port)
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

    cross_connects, panel_positions = Counter(), Counter()

    def cross_connect(term, port, site, provider):
        """Carrier handoffs into a PoP record the hotel cross-connect; fibre ones the PE cabinet enclosure position."""
        carrier_pop = port is not None and provider != "provider/operator" and str(site).startswith("site/pop-")
        xc, pp = attrs(term).get("xconnect_id"), attrs(term).get("pp_info")
        device = refs(port).get("device") if port else None
        panel = f"{device[:-4]}odf{device[-2:]}" if device and re.fullmatch(r"device/pop-[^/]+/pe-[ab]", device) else None
        fibre = panel is not None and attrs(port).get("type") != "1000base-t"
        match = re.fullmatch(re.escape(str(attrs(panel).get("name"))) + r", panel ([1-4]), port ([1-9]|1[0-2])", str(pp)) if fibre else None
        if carrier_pop:
            cross_connects[xc] += 1
            if match:
                panel_positions[(panel, match[1], match[2])] += 1
        if ((xc is not None) != carrier_pop or (carrier_pop and not re.fullmatch(r"XC-[1-9]\d{6}", str(xc))) or
                (pp is not None) != (carrier_pop and fibre) or (pp is not None and not match)):
            report("provider-cross-connect", term, "A carrier's handoff into a PoP records its carrier-hotel cross-connect, "
                   "and a fibre handoff its position on the PE cabinet's own fibre enclosure; no other termination carries either.")

    def circuit(key, port_a, port_z, site_a, site_z, provider, speed, commitment, tenant="tenant", account=None):
        terms = child("circuit", key, "circuit_termination")
        sides = {side: [term for term in terms if attrs(term).get("term_side") == side] for side in ("A", "Z")}
        good = (kind(key) == "circuit" and attrs(key).get("status") == life("circuit") and
                attrs(key).get("commit_rate") == commitment and refs(key).get("provider") == provider and
                refs(key).get("tenant") == tenant and len(terms) == 2 and all(len(value) == 1 for value in sides.values()))
        if account is not None:
            good &= refs(key).get("provider_account") == account and refs(account).get("provider") == provider
        for side, port, site in (("A", port_a, site_a), ("Z", port_z, site_z)):
            term = sides[side][0] if len(sides[side]) == 1 else None
            good &= attrs(term).get("port_speed") == speed
            if port is not None:
                # A local handoff terminates in the room its equipment stands in.
                room = refs(refs(port).get("device")).get("location")
                good &= (refs(term).get("termination") == room and kind(room) == "location" and refs(room).get("site") == site and
                         kind(port) == "interface" and attrs(port).get("type") not in ("virtual", "lag", None) and
                         attrs(port).get("speed") == speed and peers.get(term) == port and active_path(term))
                cross_connect(term, port, site, provider)
            else:
                good &= (refs(term).get("termination") == site and kind(site) == "provider_network" and
                         refs(site).get("provider") == provider and not peers.get(term))
                cross_connect(term, None, site, provider)
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
        now[0] = _stage(customer, premises[sid][1]) if customer else "active"
        tenant = f"tenant/cust-{customer['key']}" if customer else "tenant"
        category = "customer" if customer else "dc" if sid == "dc-01" else "pop"
        city, state_code, state, zone = METROS[site_metros[sid]]
        region = f"region/{recipe['namespace']}/us/{state_code.lower()}/{city.lower()}"
        group = f"site-group/{recipe['namespace']}/{category}"
        address_lines = str(attrs(site).get("physical_address")).split("\n")
        locality = address_lines[1].partition(", ")[0] if len(address_lines) == 3 else None
        locality = locality if LOCALITIES.get(locality) == city else city
        # A real street of an anchor in that locality and a positive house
        # number; Chicago's grid may flip a street's North/South or East/West.
        number, _, street = address_lines[0].partition(" ")
        direction, _, rest = street.partition(" ")
        streets = {name for (place, _), names in ADDRESS_STREETS.items() if place == locality for name in names}
        if direction in {"North", "South", "East", "West"} and (locality == "Chicago" or locality in MILWAUKEE_COUNTY):
            street = next((name for name in streets if name.partition(" ")[2] == rest), street)
        good_address = (len(address_lines) == 3 and number.isdecimal() and int(number) > 0 and street in streets and
                        address_lines[1:] == [f"{locality}, {state}", "United States"])
        description = {"pop": "Provider routing, local management and carrier handoffs",
                       "customer": "Private-L3 customer premises and wired office",
                       "dc": "Provider NOC services, inventory and monitoring"}[category]
        if attrs(site).get("description") != description:
            report("provider-scope-text", site, "Facility description must state its modeled role without adding unmodeled availability or execution guarantees.")
        if (kind(site) != "site" or attrs(site).get("status") != life("site") or refs(site).get("tenant") != tenant or
                refs(site).get("region") != region or refs(site).get("group") != group or attrs(site).get("time_zone") != zone or
                not good_address or kind(region) != "region" or kind(group) != "site_group" or
                refs(region).get("parent") != f"region/{recipe['namespace']}/us/{state_code.lower()}"):
            report("provider-site-context", site, "Site ownership, functional group, address, metro/state and time zone must match the actual requested facility.")
        room = f"location/{sid}"
        # Single-level premises: rooms hang from the site; a PoP's cage from
        # its leased carrier-hotel suite.
        required_rooms = {room: ("equipment_room", 1, [24, 18, 0], f"{room}/suite" if category == "pop" else None)}
        if category == "pop":
            required_rooms[f"{room}/suite"] = ("suite", 1, [0, 0, 0], None)
            if [attrs(f"{room}/suite").get("name"), attrs(room).get("name")] != list(carrier_suite(sid)):
                report("provider-room-geometry", room, "A PoP cage and its suite keep their authored carrier-hotel names.")
        if customer and customer["lan_endpoints"]:
            required_rooms[f"{room}/office-01"] = ("office", 1, [8, 18, 0], None)
        actual_rooms = set(child("site", site, "location"))
        if actual_rooms != set(required_rooms):
            report("provider-room-inventory", site, "Each bounded facility needs its real building, ground floor, equipment room and requested customer office.")
        for key, (function, floor, point, parent) in required_rooms.items():
            metadata = objects.get(key, {}).get("meta", {})
            if (kind(key) != "location" or attrs(key).get("status") != life("site") or refs(key).get("site") != site or
                    refs(key).get("tenant") != tenant or refs(key).get("parent") != parent or
                    metadata.get("space_type") != function or metadata.get("floor") != floor or metadata.get("position_m") != point or
                    (function == "office" and metadata.get("capacity") != {"workstations": 12})):
                report("provider-room-geometry", key, "Facility rooms must retain their fixed local geometry, capacity, ownership and containment.")
        if sid == "dc-01":
            continue
        devices = child("site", site, "device")
        expected = {} if customer else {f"device/{sid}/console-01": ("console-server", "console-server")}
        if customer:
            expected[f"device/{sid}/edge-01"] = ("customer-edge", "edge")
            if customer["lan_endpoints"]:
                expected[f"device/{sid}/access-01"] = ("access", access_alias)
            expected.update({f"device/{sid}/pc-{n:03}": ("workstation", "endpoint") for n in range(1, customer["lan_endpoints"] + 1)})
        else:
            expected.update({f"device/{sid}/pe-{side}": ("provider-edge", "provider-edge") for side in ("a", "b")})
            expected[f"device/{sid}/mgmt-01"] = ("management", access_alias)
        for key, (role, hardware) in expected.items():
            location = f"{room}/office-01" if role == "workstation" else room
            if (kind(key) != "device" or attrs(key).get("status") != life("device") or refs(key).get("site") != site or
                    refs(key).get("tenant") != tenant or refs(key).get("role") != f"role/{role}" or
                    refs(key).get("device_type") != f"hardware/{hardware}" or refs(key).get("location") != location):
                report("provider-device-inventory", key, "Requested devices need their exact active hardware, role, tenant and local room.")
            if role != "workstation":
                infrastructure.append(key)
                if role in {"provider-edge", "customer-edge"} and attrs(key).get("description") != f"{role_label(role)} at {attrs(site).get('name')}":
                    report("provider-scope-text", key, "Router description must retain its actual local role; modeled paths do not establish availability or running forwarding.")
                rack = refs(key).get("rack")
                if kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or attrs(rack).get("status") != life("rack"):
                    report("provider-rack-placement", key, "Network and serial equipment must occupy an active rack in their local equipment room.")
        actual = {device for device in devices if refs(device).get("role") not in {"role/pdu", "role/patch-panel", "role/wall-outlet"}}
        if actual != set(expected):
            report("provider-device-inventory", site, "Active non-passive site inventory must exactly match the bounded PoP or customer composition.")
        for device in expected:
            if expected[device][0] in {"workstation", "console-server"} or customer:
                continue
            port = f"{device}/console_port/Console"
            peer = peers.get(port)
            server = refs(peer).get("device")
            if (kind(port) != "console_port" or kind(peer) != "console_server_port" or
                    server != f"device/{sid}/console-01" or not active_path(port)):
                report("provider-console-path", device, "Every PE, CPE and management/access switch requires its own active local serial console path.")
    now[0] = "active"

    for pop in ordered:
        sid, site = f"pop-{pop}", f"site/pop-{pop}"
        # The two cabinets are bayed together on the first row of the network
        # zone: one cabinet width apart, both one metre inside the room.
        rack_points = {f"rack/{sid}/network-01": [1.0, 1.0, 0], f"rack/{sid}/network-02": [1.6, 1.0, 0]}
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
            expected_ports = {p["name"] for p in catalog.get("provider-edge", {}).get("interfaces", [])} | {"lo0", "lo0.0"}
            actual_ports = {attrs(port).get("name") for port in child("device", router, "interface")}
            if actual_ports != expected_ports:
                report("provider-port-inventory", router, "PE interfaces must match the pinned chassis and the one in-band loopback.")
            def in_use(port):
                # Unused ports are shut on every role (operations finalize).
                return bool(peers.get(port) or child("assigned_object", port, "ip_address")
                            or child("parent", port, "interface"))
            for n in range(4):
                port = f"{router}/if/et-0/0/{n}"
                if (attrs(port).get("type") != "100gbase-x-qsfp28" or attrs(port).get("enabled") is not (n < 3 and in_use(port)) or
                        (n < 3 and attrs(port).get("speed") != 100000000) or
                        (n == 3 and (peers.get(port) or child("assigned_object", port, "ip_address")))):
                    report("provider-port-mode", port, "The installed MX204 mode exposes three active 100G cages and leaves the fourth unavailable.")
            for n in range(8):
                port = f"{router}/if/xe-0/1/{n}"
                speed = 1000000 if n < 6 else 10000000
                if attrs(port).get("type") != "10gbase-x-sfpp" or attrs(port).get("enabled") is not in_use(port) or attrs(port).get("speed") != speed:
                    report("provider-port-mode", port, "The PE preserves 10G physical port types with explicit 1G service and 10G infrastructure operating speeds; unused ports are shut.")
            # Junos addresses the loopback on logical unit 0: lo0.0, a child of lo0.
            fxp0, lo, unit = f"{router}/if/fxp0", f"{router}/if/lo0", f"{router}/if/lo0.0"
            if attrs(lo).get("description") != "Backbone router identity loopback":
                report("provider-scope-text", lo, "The loopback descriptor must state its role as the router's backbone identity.")
            loopnet = ip_network((int(PUBLIC_POOLS["loopbacks"].network_address) + loop_slots[router] + 1, 32))
            matching_prefixes = prefixes_by_vrf_network[(CORE, str(loopnet))]
            if (attrs(lo).get("type") != "virtual" or attrs(lo).get("enabled") is not True or refs(lo).get("device") != router or
                    attrs(unit).get("name") != "lo0.0" or attrs(unit).get("type") != "virtual" or refs(unit).get("parent") != lo or
                    refs(unit).get("device") != router or child("assigned_object", lo, "ip_address") or
                    not address(unit, loopnet, CORE, 0, "tenant") or not primary(router, unit) or len(matching_prefixes) != 1 or
                    attrs(matching_prefixes[0]).get("status") != "active" or int(loopnet.network_address) % 256 in (0, 255)):
                report("provider-loopback", router, "Each PE needs its own active reserved /32 prefix and primary lo0.0 address in the global "
                       "table, never the network or broadcast address of a /24.")
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
        routed(pair, (a, b), CORE)
        if physical(a, b, 100000000):
            edge(pair, pe_a, pe_b, 100000000)
        else:
            report("provider-pair-path", site, "A connected routed 100G local pair cable must join the two actual PE data ports.")
        for side, router, number in (("a", pe_a, 1), ("b", pe_b, 2)):
            a, b = f"device/{sid}/mgmt-01/if/{access_uplinks[number-1]}", f"{router}/if/xe-0/1/6"
            routed(f"management/{sid}/{side}", (a, b), MANAGEMENT)
            if not physical(a, b, 10000000):
                report("provider-management-uplink", site, "PoP management requires two real routed 10G switch uplinks to the separate PE data ports.")

    site_points = {site: (attrs(site)["latitude"], attrs(site)["longitude"]) for site in expected_sites
                   if all(type(attrs(site).get(f)) in (int, float) for f in ("latitude", "longitude"))}
    for key, (a, b) in spans.items():
        port_a = f"{a}/if/et-0/0/{1 + transport_ports[a][key]}"
        port_b = f"{b}/if/et-0/0/{1 + transport_ports[b][key]}"
        provider = span_provider[key]
        owned = provider == "provider/operator"
        # Owned fiber is lit at the 100G port and purchases nothing; leased
        # transport commits 10G unless the upgrade ledger names it.
        commit = None if owned else LEASED_COMMIT
        routed(key, (port_a, port_b), CORE)
        if circuit(key, port_a, port_b, refs(a).get("site"), refs(b).get("site"), provider, 100000000, commit,
                   account="provider-account/operator/fiber" if owned else f"provider-account/{provider}"):
            edge(key, a, b, 100000000 if owned else commit)
        if refs(key).get("type") != ("circuit-type/dark-fiber" if owned else "circuit-type/backbone"):
            report("provider-circuit-path", key, "Same-metro spans are owned dark fiber; inter-metro spans are leased transport.")
        ends = [site_points.get(refs(router).get("site")) for router in (a, b)]
        if all(ends) and (attrs(key).get("distance") != round(_km(*ends) * ROUTE_FACTOR, 1) or attrs(key).get("distance_unit") != "km"):
            report("provider-backbone-geography", key, "A span's route length must follow its two PoPs' actual positions.")

    def local_network(sid, role, vrf, tenant):
        site = f"site/{sid}"
        container = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
        network = ip_network((int(container.network_address) + (128 if role == "clients" else 0), 25 if role == "clients" else 26))
        vlan, prefix = f"vlan/{sid}/{role}", f"prefix/{sid}/{role}"
        if (kind(prefix) != "prefix" or attrs(prefix).get("status") != life("prefix") or attrs(prefix).get("prefix") != str(network) or
                refs(prefix).get("vrf") != vrf or refs(prefix).get("tenant") != tenant or refs(prefix).get("scope_site") != site or refs(prefix).get("vlan") != vlan or
                kind(vlan) != "vlan" or attrs(vlan).get("vid") != (20 if role == "clients" else 10) or attrs(vlan).get("status") != life("vlan") or
                refs(vlan).get("site") != site or refs(vlan).get("tenant") != tenant or
                attrs(f"prefix/{sid}/reservation").get("prefix") != str(container) or "vrf" in refs(f"prefix/{sid}/reservation") or
                refs(f"prefix/{sid}/reservation").get("tenant") != tenant):
            report("provider-site-network", prefix, "Management /26 and customer /25 segments require their fixed global site block, VLAN, VRF and tenant.")
        return network, vlan

    def svi(port, network, vlan, vrf, host, tenant, parent=None):
        return (kind(port) == "interface" and attrs(port).get("type") == "virtual" and attrs(port).get("enabled") is True and
                vlans(port) == {vlan} and refs(port).get("parent") == parent and address(port, network, vrf, host, tenant))

    def console_management(sid, switch, network, vlan, vrf, tenant):
        device = f"device/{sid}/console-01"
        # The console server's cabled management port is its first catalog
        # mgmt_only port (Opengear NET1), never a literal name.
        console_mgmt = next((p["name"] for p in catalog.get("console-server", {}).get("interfaces", [])
                             if p.get("mgmt_only")), None)
        port, peer = f"{device}/if/{console_mgmt}", f"{switch}/if/{access_ports[-2]}"
        if (not physical(port, peer) or vlans(port) != {vlan} or vlans(peer) != {vlan} or
                not address(port, network, vrf, 3, tenant) or not primary(device, port)):
            report("provider-console-management", device, "The local console server needs its active management address and actual VLAN channel into the routed site switch.")

    used_pe_ports = {router: {f"{router}/if/et-0/0/0", f"{router}/if/xe-0/1/6", f"{router}/if/fxp0"} for router in routers}
    for key, ends in spans.items():
        for router in ends:
            used_pe_ports[router].add(f"{router}/if/et-0/0/{1 + transport_ports[router][key]}")
    console_net2 = [p["name"] for p in catalog.get("console-server", {}).get("interfaces", []) if p.get("mgmt_only")][1:2]
    for pop in pops:
        sid, vrf = f"pop-{pop}", MANAGEMENT
        network, vlan = local_network(sid, "management", vrf, "tenant")
        switch = f"device/{sid}/mgmt-01"
        port = f"{switch}/if/Vlan10"
        if not svi(port, network, vlan, vrf, 1, "tenant") or not primary(switch, port):
            report("provider-management-gateway", switch, "The PoP console subnet requires its actual routed switch SVI and primary management address.")
        console_management(sid, switch, network, vlan, vrf, "tenant")
        dedicated = f"{switch}/if/{access_mgmt}"
        if peers.get(dedicated) or child("assigned_object", dedicated, "ip_address"):
            report("provider-management-mode", dedicated, "The routed management switch must not duplicate its SVI subnet on the dedicated management port.")
        # Out-of-band: each PE's dedicated fxp0 on the management LAN, on its own switch port.
        for index, side in enumerate(("a", "b")):
            fxp0, peer = f"device/{sid}/pe-{side}/if/fxp0", f"{switch}/if/{access_ports[index]}"
            if (not physical(fxp0, peer) or vlans(fxp0) != {vlan} or vlans(peer) != {vlan} or
                    attrs(peer).get("mode") != "access" or not address(fxp0, network, vrf, FXP0_HOSTS[index], "tenant")):
                report("provider-management-mode", fxp0, "Each PE's dedicated fxp0 needs its own cabled management-switch port and "
                       "Carrier Management address on the PoP management LAN.")
        # Independent console reachability: broadband from a third ISP on NET2.
        key, console = f"circuit/oob/{pop}", f"device/{sid}/console-01"
        net2 = f"{console}/if/{console_net2[0]}" if console_net2 else None
        oob = ip_network((int(OOB_POOL.network_address) + 4 * oob_slots[pop], 30))
        prefix = f"prefix/link/oob/{pop}"
        if (not circuit(key, net2, None, f"site/{sid}", "provider-network/oob", "provider/oob", 1000000, None,
                        account="provider-account/provider/oob") or refs(key).get("type") != "circuit-type/out-of-band" or
                attrs(prefix).get("prefix") != str(oob) or refs(prefix).get("vrf") != OOB or attrs(prefix).get("status") != "active" or
                not address(net2, oob, OOB, 2, "tenant") or primary(console, net2)):
            report("provider-oob", key, "Each PoP console server needs its independent best-effort broadband circuit on its second "
                   "management port, addressed from its reserved /30 in the out-of-band context, never the core or management VRF.")

    def service_port(pop, target):
        slot = service_ports[pop][target]
        router = f"device/pop-{pop}/pe-{'a' if slot % 2 == 0 else 'b'}"
        port = f"{router}/if/xe-0/1/{slot // 2}"
        used_pe_ports[router].add(port)
        return router, port

    attachments, customer_peerings, transit_peerings = {}, {}, {}
    stages = {sid: _stage(customer, pop) for sid, (customer, pop, _) in premises.items()}
    for sid, (customer, pop, ordinal) in premises.items():
        now[0] = stages[sid]
        tenant, vrf = f"tenant/cust-{customer['key']}", f"vrf/customer/{customer['key']}"
        cpe, switch = f"device/{sid}/edge-01", f"device/{sid}/access-01"
        cpe_wan = f"{cpe}/if/wan1"
        router, port = service_port(pop, sid)
        attachments[sid] = router
        hub = pop == customer["hub_pop"] and ordinal == 1
        rate = customer["hub_commit_mbps"] if hub else next(t for t in recipe["wan_tiers_mbps"] if Decimal(t) * usable >= customer["site_peak_mbps"])
        key = f"circuit/customer/{sid}"
        customer_peerings[sid] = (router, port, cpe, cpe_wan, tenant, customer["key"], key)
        routed(key, (cpe_wan, port), vrf, tenant)
        circuit(key, cpe_wan, port, f"site/{sid}", f"site/pop-{pop}", "provider/operator", 1000000, rate * 1000,
                tenant, f"provider-account/customer/{customer['key']}")
        ends = [site_points.get(f"site/{sid}"), site_points.get(f"site/pop-{pop}")]
        route = round(_km(*ends) * ROUTE_FACTOR, 1) if all(ends) else None
        if (attrs(key).get("distance"), attrs(key).get("distance_unit")) != ((route, "km") if route else (None, None)):
            report("provider-access-geography", key, "An access circuit records the route length from its premises to its serving PoP.")
        clients, clients_vlan = local_network(sid, "clients", vrf, tenant)
        lan = f"{cpe}/if/port1"
        if customer["lan_endpoints"]:
            management, management_vlan = local_network(sid, "management", vrf, tenant)
            upstream = f"{switch}/if/{access_ports[-1]}"
            if (not physical(lan, upstream) or any(vlans(p) != {management_vlan, clients_vlan} or attrs(p).get("mode") != "tagged" for p in (lan, upstream)) or
                    not svi(f"{cpe}/if/Management", management, management_vlan, vrf, 1, tenant, lan) or
                    not svi(f"{cpe}/if/Clients", clients, clients_vlan, vrf, 1, tenant, lan) or
                    not primary(cpe, f"{cpe}/if/Management") or not svi(f"{switch}/if/Vlan10", management, management_vlan, vrf, 2, tenant) or
                    not primary(switch, f"{switch}/if/Vlan10")):
                report("provider-customer-gateway", cpe, "Customer LAN requires the real CPE/switch trunk, separate addressed local management/client gateways and the switch management SVI.")
        else:
            # CE only: port1 hands the customer LAN to customer-owned gear and
            # the CE is managed on its own /32 loopback in the customer VRF.
            loop = f"{cpe}/if/Management"
            block = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
            management = ip_network((int(block.network_address) + 1, 32))
            prefix = f"prefix/{sid}/management"
            if (peers.get(lan) or vlans(lan) != {clients_vlan} or attrs(lan).get("mode") != "access" or
                    not svi(f"{cpe}/if/Clients", clients, clients_vlan, vrf, 1, tenant, lan) or
                    kind(loop) != "interface" or attrs(loop).get("type") != "virtual" or refs(loop).get("parent") or vlans(loop) or
                    not address(loop, management, vrf, 0, tenant) or not primary(cpe, loop) or
                    attrs(prefix).get("prefix") != str(management) or refs(prefix).get("vrf") != vrf or
                    refs(prefix).get("vlan") or refs(prefix).get("scope_site") != f"site/{sid}" or
                    kind(f"vlan/{sid}/management") or prefixes_by_vrf_network.get((vrf, str(ip_network((int(block.network_address), 26))))) ):
                report("provider-customer-gateway", cpe, "A CE-only premises hands its customer LAN off untagged on port1 and is managed "
                       "on its own /32 loopback; no management VLAN or switched segment exists without a managed LAN.")
        if objects.get(f"device/{sid}/console-01"):
            report("provider-device-inventory", sid, "A single-CE premises carries no console server.")
        for dedicated in (f"{cpe}/if/mgmt", f"{switch}/if/{access_mgmt}"):
            if peers.get(dedicated) or child("assigned_object", dedicated, "ip_address"):
                report("provider-management-mode", dedicated, "Customer management uses the routed local LAN; dedicated management ports remain unused.")
        for n in range(1, customer["lan_endpoints"] + 1):
            device = f"device/{sid}/pc-{n:03}"
            port, access = f"{device}/if/eth0", f"{switch}/if/{access_ports[n-1]}"
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
        used_copper = {f"{switch}/if/{access_ports[n-1]}" for n in range(1, customer["lan_endpoints"] + 1)} | {
            f"{switch}/if/{access_ports[-1]}"} if customer["lan_endpoints"] else set()
        actual_copper = {p for p in child("device", switch, "interface") if attrs(p).get("type") == "1000base-t" and peers.get(p)}
        if actual_copper != used_copper or Decimal(len(used_copper)) > len(access_ports) * usable:
            report("provider-customer-port-capacity", switch, "Actual customer access attachments must use the finite fixed ports and retain declared copper-port reserve.")

    now[0] = "active"
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
        routed(key, (port, pe), MANAGEMENT)
        # Inside the NOC's own metro the operator runs the access tail; into
        # another metro it leases a 1G private line from a transport carrier.
        local = pops[pop]["metro"] == pops[recipe["noc_pop_a"]]["metro"]
        provider = "provider/operator" if local else f"provider/transport-{side}"
        if (not circuit(key, port, pe, "site/dc-01", f"site/pop-{pop}", provider, 1000000, 1000000,
                        account="provider-account/operator/noc" if local else f"provider-account/{provider}") or
                (not local and not attrs(key).get("description", "").startswith("1G Ethernet private line")) or
                refs(device).get("role") != "role/wan-edge" or
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
        transit_peerings[side] = (router, port, provider, key)
        # The upstream holds the even address of the /31 it assigned.
        routed(key, (port,), CORE, hosts=(1,))
        circuit(key, port, None, f"site/pop-{pop}", f"provider-network/transit/{side}", provider, 10000000, 10000000,
                account=f"provider-account/{provider}")
        container = f"prefix/upstream/transit-{side}"
        if (attrs(container).get("prefix") != str(UPSTREAM_POOLS[side]) or "tenant" in refs(container) or
                any(link_network[key].subnet_of(ip_network(a)) for a in PUBLIC_AGGREGATES)):
            report("provider-public-space", key, "A transit /31 is numbered from its upstream's own recorded assignment, "
                   "which carries no operator tenancy and sits outside every operator aggregate.")
    if by_kind["circuit"] != (set(spans) | {f"circuit/customer/{sid}" for sid in premises} | {f"circuit/noc/{s}" for s in ("a", "b")} |
                              {f"circuit/transit/{s}" for s in ("a", "b")} | {f"circuit/oob/{pop}" for pop in pops}):
        report("provider-circuit-inventory", "plan", "Physical circuit inventory must exactly cover requested backbone, customer, NOC and external transit attachments.")
    for router, required in used_pe_ports.items():
        actual = {p for p in child("device", router, "interface") if peers.get(p)}
        if actual != required:
            report("provider-port-use", router, "Only the exact reserved physical PE ports may be cabled; unused service/transport/transit positions remain free.")

    for value, count in cross_connects.items():
        if count > 1:
            report("provider-cross-connect", "plan", f"Cross-connect {value} is recorded on {count} terminations.")
    for (panel, bay, position), count in panel_positions.items():
        if count > 1:
            report("provider-cross-connect", panel, f"Panel {bay} port {position} carries {count} handoffs.")

    base = recipe["asn_base"]
    public_asns = ("asn/operator", "asn/transit-a", "asn/transit-b")
    expected_asns = {f"asn/customer/{key}": base + 256 + slot for key, slot in customer_slots.items()}
    expected_providers = {"provider/operator", *(f"provider/{label}" for label in CARRIER_NAMES)}
    if kind("provider-network/oob") != "provider_network" or refs("provider-network/oob").get("provider") != "provider/oob":
        report("provider-oob", "provider-network/oob", "The out-of-band ISP needs its own provider network as the far end of every console circuit.")
    if by_kind["provider"] != expected_providers or by_kind["asn"] != set(expected_asns) | set(public_asns):
        report("provider-routing-registry", "plan", "Provider and ASN inventories must match actual operator, transport, upstream and customer identities.")
    if (kind("rir/private") != "rir" or attrs("rir/private").get("is_private") is not True or
            kind("asn-range/private") != "asn_range" or attrs("asn-range/private").get("start") != base or
            attrs("asn-range/private").get("end") != base + 1023 or refs("asn-range/private").get("rir") != "rir/private"):
        report("provider-routing-registry", "asn-range/private", "The estate must retain its complete aligned private 32-bit ASN reservation and private registry.")
    if (kind("rir/arin") != "rir" or attrs("rir/arin").get("name") != "ARIN" or attrs("rir/arin").get("is_private") is not False or
            kind("asn-range/arin") != "asn_range" or refs("asn-range/arin").get("rir") != "rir/arin" or
            (attrs("asn-range/arin").get("start"), attrs("asn-range/arin").get("end")) != (DOCUMENTATION_ASNS[0], DOCUMENTATION_ASNS[-1])):
        report("provider-routing-registry", "rir/arin", "The operator and upstream AS numbers need their public ARIN registry and documentation range.")
    for key, number in expected_asns.items():
        if (kind(key) != "asn" or attrs(key).get("asn") != number or refs(key).get("rir") != "rir/private" or
                refs(key).get("tenant") != f"tenant/cust-{key.rsplit('/', 1)[-1]}"):
            report("provider-asn", key, "Each customer routing identity must retain its reserved private ASN, registry and customer tenant.")
    numbers = [attrs(key).get("asn") for key in public_asns]
    for key, number in zip(public_asns, numbers):
        if (kind(key) != "asn" or number not in DOCUMENTATION_ASNS or numbers.count(number) != 1 or refs(key).get("rir") != "rir/arin" or
                refs(key).get("tenant") != ("tenant" if key == "asn/operator" else None)):
            report("provider-asn", key, "The operator and each upstream hold a distinct documentation AS number under the public registry; "
                   "only the operator's carries the operator tenant.")
    for prefix in PUBLIC_AGGREGATES:
        key = f"aggregate/public/{prefix}"
        if kind(key) != "aggregate" or attrs(key).get("prefix") != prefix or refs(key).get("rir") != "rir/arin":
            report("provider-public-space", key, "Carrier-owned loopback, link and transit space needs its public ARIN aggregate.")
    # Third-party carriers are their own companies: distinct from each other and
    # from every customer, down to the first word of the name.
    first_words = Counter(str(attrs(key).get("name", "")).split(" ")[0] for key in
                          [f"provider/{label}" for label in CARRIER_NAMES] + sorted(by_kind["tenant"]))
    for label in CARRIER_NAMES:
        key = f"provider/{label}"
        if first_words[str(attrs(key).get("name", "")).split(" ")[0]] != 1:
            report("provider-carrier-identity", key, "A carrier's name must not echo another carrier's or a customer's.")
    operator_asn = attrs("asn/operator").get("asn")
    code = _operator_code(recipe["name"])
    for provider, asn in (("provider/operator", "asn/operator"), ("provider/transit-a", "asn/transit-a"), ("provider/transit-b", "asn/transit-b")):
        if refs(provider).get("asns") != [asn]:
            report("provider-asn-consumer", provider, "Provider ASN association must refer to its own actual operator or upstream identity.")
    for site in expected_sites:
        sid = site.removeprefix("site/")
        asn = f"asn/customer/{premises[sid][0]['key']}" if sid in premises else "asn/operator"
        if refs(site).get("asns") != [asn]:
            report("provider-asn-consumer", site, "Site routing ownership must reference its actual customer or operator ASN.")
    hub_rt, spoke_rt = "route-target/management/hub", "route-target/management/spoke"
    if (kind(MANAGEMENT) != "vrf" or refs(MANAGEMENT).get("tenant") != "tenant" or attrs(MANAGEMENT).get("name") != "Carrier Management" or
            attrs(MANAGEMENT).get("enforce_unique") is not True or attrs(MANAGEMENT).get("rd") != f"{operator_asn}:{HUB_RT}" or
            refs(MANAGEMENT).get("import_targets") != [hub_rt, spoke_rt] or refs(MANAGEMENT).get("export_targets") != [hub_rt] or
            attrs(hub_rt).get("name") != f"{operator_asn}:{HUB_RT}" or attrs(spoke_rt).get("name") != f"{operator_asn}:{SPOKE_RT}" or
            kind(hub_rt) != "route_target" or kind(spoke_rt) != "route_target" or
            kind("provider-network/operator") != "provider_network" or
            refs("provider-network/operator").get("provider") != "provider/operator"):
        report("provider-routing-domain", MANAGEMENT, "Carrier Management must import the CE spoke and its own hub target and export only the hub, "
               "with the operator's service network beside it.")
    if kind(OOB) != "vrf" or refs(OOB).get("tenant") != "tenant" or refs(OOB).get("import_targets") or refs(OOB).get("export_targets"):
        report("provider-routing-domain", OOB, "The out-of-band context is the ISP's network: no route target joins it to the carrier.")
    expected_vcs, expected_terms = set(), set()
    expected_accounts = {f"provider-account/provider/{label}" for label in CARRIER_NAMES} | {"provider-account/operator/fiber"}
    if any(refs(f"circuit/noc/{side}").get("provider") == "provider/operator" for side in ("a", "b")):
        expected_accounts.add("provider-account/operator/noc")
    for account in expected_accounts:
        provider = "provider/operator" if account.startswith("provider-account/operator/") else account.removeprefix("provider-account/")
        if kind(account) != "provider_account" or refs(account).get("provider") != provider or "tenant" in refs(account):
            report("provider-account", account, "Procurement accounts must reference their actual provider; native accounts have no tenant field.")
    flows = Counter()
    for customer in customers:
        key = customer["key"]
        tenant, vrf, target = f"tenant/cust-{key}", f"vrf/customer/{key}", f"route-target/customer/{key}"
        vc, account = f"virtual-circuit/customer/{key}", f"provider-account/customer/{key}"
        if (attrs(vc).get("description") != f"{titleize(key)} private L3 VPN, hub at {titleize(customer['hub_pop'])}" or
                attrs(vc).get("comments") != VIRTUAL_CIRCUIT_NOTE):
            report("provider-scope-text", vc, "The service must name its customer and hub and point at its customer BGP peer group.")
        expected_vcs.add(vc); expected_accounts.add(account)
        if (kind(tenant) != "tenant" or kind(vrf) != "vrf" or refs(vrf).get("tenant") != tenant or attrs(vrf).get("enforce_unique") is not True or
                attrs(vrf).get("rd") != f"{operator_asn}:{1001 + customer_slots[key]}" or
                refs(vrf).get("import_targets") != [target, hub_rt] or refs(vrf).get("export_targets") != [target, spoke_rt] or
                kind(target) != "route_target" or attrs(target).get("name") != f"{operator_asn}:{1001 + customer_slots[key]}" or refs(target).get("tenant") != tenant):
            report("provider-customer-routing", vrf, "Each private customer needs its own tenant VRF and exact symmetric reserved route target.")
        if (kind(account) != "provider_account" or refs(account).get("provider") != "provider/operator" or "tenant" in refs(account) or
                attrs(account).get("account") != f"{code}-C{customer_slots[key] + 1:05d}" or
                kind(vc) != "virtual_circuit" or attrs(vc).get("status") != ("planned" if customer.get("status") == "planned" else "active") or
                refs(vc).get("provider_network") != "provider-network/operator" or
                refs(vc).get("provider_account") != account or refs(vc).get("tenant") != tenant or
                refs(vc).get("type") != "virtual-circuit-type/private-l3"):
            report("provider-customer-service", vc, "The private-L3 service (planned while its customer onboards) must belong to the correct customer, operator network and customer procurement account.")
        members = {sid for sid, (item, _, _) in premises.items() if item["key"] == key}
        hub = f"ce-{key}-{customer['hub_pop']}-001"
        for sid in members:
            now[0] = stages[sid]
            term = f"virtual-circuit-termination/{sid}"
            expected_terms.add(term)
            port, parent = f"device/{sid}/edge-01/if/PrivateL3", f"device/{sid}/edge-01/if/wan1"
            if attrs(port).get("description") != "Private L3 VPN attachment over the access circuit":
                report("provider-scope-text", port, "The virtual interface describes inventory membership over its actual access circuit, not executed tunneling or routing.")
            if (kind(term) != "virtual_circuit_termination" or refs(term).get("virtual_circuit") != vc or refs(term).get("interface") != port or
                    attrs(term).get("role") != ("hub" if sid == hub else "spoke") or kind(port) != "interface" or attrs(port).get("type") != "virtual" or attrs(port).get("enabled") is not True or
                    refs(port).get("device") != f"device/{sid}/edge-01" or refs(port).get("parent") != parent or refs(port).get("vrf") != vrf or
                    not active_path(parent) or len(child("interface", port, "virtual_circuit_termination")) != 1 or child("assigned_object", port, "ip_address")):
                report("provider-virtual-membership", term, "Each requested CPE needs exactly one active virtual membership (hub at the customer's hub premises, spoke elsewhere) over its actual physical customer handoff and customer VRF.")
            # Only an in-service premises offers traffic; planned, provisioning
            # and decommissioning paths never count as healthy capacity.
            if sid != hub and stages[sid] == "active":
                flows[(attachments[sid], attachments[hub])] += customer["site_peak_mbps"] * 1000
    now[0] = "active"
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
    # --- One timeline: spans before a PoP's customers, customers in slot order ---
    as_of = date.fromisoformat(recipe["as_of"])

    def in_service(key):
        try:
            return date.fromisoformat(attrs(key).get("install_date"))
        except (TypeError, ValueError):
            return None

    first_span = {}
    for key, ends in spans.items():
        if (day := in_service(key)) is not None:
            for router in ends:
                first_span[pop_of(router)] = min(day, first_span.get(pop_of(router), day))
    pending = {f"circuit/customer/{sid}" for sid, stage in stages.items() if stage in ("planned", "provisioning")}
    for key in sorted(by_kind["circuit"]):
        if key in pending:
            if "install_date" in attrs(key):
                report("provider-timeline", key, "A circuit not yet in service has no install date.")
        elif (day := in_service(key)) is None or day > as_of:
            report("provider-timeline", key, "Every circuit in or leaving service needs a service date on or before as_of.")
    starts = defaultdict(list)
    for sid, (customer, pop, _) in premises.items():
        key = f"circuit/customer/{sid}"
        if (day := in_service(key)) is None:
            continue
        if pop not in first_span or day <= first_span[pop]:
            report("provider-timeline", key, "A customer circuit enters service after its PoP's first backbone span.")
        starts[customer["key"]].append((day, sid))
    previous = None
    for customer in sorted(customers, key=lambda item: customer_slots[item["key"]]):
        if not starts[customer["key"]]:
            continue
        day, first = min(starts[customer["key"]])
        if first != f"ce-{customer['key']}-{customer['hub_pop']}-001" or (previous is not None and day < previous):
            report("provider-timeline", f"provider-account/customer/{customer['key']}",
                   "Customers onboard in their permanent slot order, each starting with its hub circuit.")
        previous = day
    # --- Premises sit across the metro, not on top of their serving PoP ---
    for sid, (_, pop, _) in premises.items():
        here, there = site_points.get(f"site/{sid}"), site_points.get(f"site/pop-{pop}")
        if not here or not there or sid in recipe.get("site_names", {}):
            continue
        # PoPs allocated after this premises cannot reassign its area.
        rivals = [site_points.get(f"site/pop-{q}") for q in pops if q != pop and pops[q]["metro"] == pops[pop]["metro"]
                  and allocations.get(f"pop-{q}", 1 << 62) < allocations[sid]]
        if _km(here, there) > PREMISES_KM or any(p and _km(here, p) <= _km(here, there) for p in rivals):
            report("provider-premises-geography", f"site/{sid}", "Customer premises lie in their serving PoP's area: within "
                   f"{PREMISES_KM} km and nearer it than any other same-metro PoP that existed when they were ordered.")

    # --- BGP inventory: documentation records, never applied configuration ---
    def ipv4_of(port):
        found = [key for key in child("assigned_object", port, "ip_address") if key in ipv4_addresses]
        return found[0] if len(found) == 1 else None

    def name_of(key):
        value = attrs(key).get("name")
        return value if isinstance(value, str) else ""

    if extra_bgp := {k for k in by_kind if k.startswith("bgp_")} - BGP_KINDS:
        report("provider-bgp-inventory", "plan", "Only routing policies, peer groups and sessions "
               "are modeled; " + ", ".join(sorted(extra_bgp)) + " would read as device configuration.")
    if by_kind["bgp_routing_policy"] != {f"bgp-routing-policy/{slug}" for slug in BGP_POLICIES}:
        report("provider-bgp-inventory", "plan", "The estate carries exactly the four authored "
               "named routing policies.")
    if by_kind["bgp_peer_group"] != {f"bgp-peer-group/{slug}" for slug in BGP_GROUPS}:
        report("provider-bgp-inventory", "plan", "The estate carries exactly the three authored "
               "peer groups.")
    for slug, (name, weight, description) in BGP_POLICIES.items():
        key = f"bgp-routing-policy/{slug}"
        expected = {"name": name, "weight": weight, "description": description}
        if kind(key) != "bgp_routing_policy" or attrs(key) != expected or refs(key):
            report("provider-bgp-policy", key, "Each named routing policy must retain its authored "
                   "name, weight and reference-intent description and carry no rule references.")
    for slug, (name, description, imports, exports, internal) in BGP_GROUPS.items():
        key = f"bgp-peer-group/{slug}"
        expected = {"local_as": "asn/operator"}
        if internal:
            expected["remote_as"] = "asn/operator"
        for field, policies in (("import_policies", imports), ("export_policies", exports)):
            if policies:
                expected[field] = [f"bgp-routing-policy/{p}" for p in policies]
        if (kind(key) != "bgp_peer_group" or refs(key) != expected or
                attrs(key) != {"name": name, "description": description}):
            report("provider-bgp-group", key, "Each peer group must retain its authored name, the "
                   "operator's own routing identity and exactly its authored import/export policies.")
    # Reflectors: PE A at the first permanent PoP and PE A at the first later
    # PoP in another metro, so no single metro holds both.
    reflector_pops = [ordered[0], next((pop for pop in ordered if pops[pop]["metro"] != pops[ordered[0]]["metro"]), ordered[0])]
    reflectors = [f"device/pop-{pop}/pe-a" for pop in reflector_pops]
    clients = [f"device/pop-{pop}/pe-{side}" for pop in ordered for side in ("a", "b")
               if f"device/pop-{pop}/pe-{side}" not in reflectors]
    families = (4, 6) if "ipv6_pool" in recipe else (4,)
    expected_sessions = {}

    def address_of(port, family):
        if family == 4:
            return ipv4_of(port)
        found = [key for key in child("assigned_object", port, "ip_address")
                 if isinstance(attrs(key).get("address"), str) and ":" in attrs(key)["address"]]
        return found[0] if len(found) == 1 else None

    def peering(local, remote_label, remote_as, group, description, local_address,
                remote_address=None, remote_prefix=None, tenant=None, status="active"):
        expected = {"device": local, "site": refs(local).get("site"),
                    "local_address": local_address, "local_as": "asn/operator",
                    "remote_as": remote_as, "peer_group": f"bgp-peer-group/{group}"}
        if remote_address is not None:
            expected["remote_address"] = remote_address
        else:
            expected["remote_prefix"] = remote_prefix
        if tenant is not None:
            expected["tenant"] = tenant
        return {"name": f"{name_of(local)} to {remote_label}", "status": status,
                "description": description}, expected

    for family in families:
        tail, label = ("", "") if family == 4 else ("/ipv6", " IPv6")
        expected_sessions[f"bgp-session/ibgp/{reflectors[0].removeprefix('device/')}/"
                          f"{reflectors[1].removeprefix('device/')}{tail}"] = peering(
            reflectors[0], f"{name_of(reflectors[1])} iBGP{label}", "asn/operator", "ibgp-core",
            "Internal peering between the two backbone route reflectors",
            address_of(f"{reflectors[0]}/if/lo0.0", family), remote_address=address_of(f"{reflectors[1]}/if/lo0.0", family))
        for client in clients:
            for reflector in reflectors:
                expected_sessions[f"bgp-session/ibgp/{client.removeprefix('device/')}/"
                                  f"{reflector.removeprefix('device/')}{tail}"] = peering(
                    client, f"{name_of(reflector)} iBGP{label}", "asn/operator", "ibgp-core",
                    "Route-reflector client peering to the backbone reflector at "
                    f"{name_of(refs(reflector).get('site'))}",
                    address_of(f"{client}/if/lo0.0", family), remote_address=address_of(f"{reflector}/if/lo0.0", family))
        for side, (router, port, provider, circuit_key) in sorted(transit_peerings.items()):
            local = address_of(port, family)
            network = ip_interface(attrs(local)["address"]).network if local else None
            candidates = prefixes_by_vrf_network[(CORE, str(network))]
            expected_sessions[f"bgp-session/transit/{side}{tail}"] = peering(
                router, f"{name_of(provider)} transit{label}", f"asn/transit-{side}", "transit",
                f"External transit peering over {attrs(circuit_key).get('cid')}", local,
                remote_prefix=candidates[0] if len(candidates) == 1 else None)
        for sid, (router, port, cpe, cpe_wan, tenant, ckey, circuit_key) in sorted(customer_peerings.items()):
            expected_sessions[f"bgp-session/customer/{sid}{tail}"] = peering(
                router, f"{name_of(cpe)} customer{label}", f"asn/customer/{ckey}", "customer",
                f"Private-L3 customer edge peering over {attrs(circuit_key).get('cid')}",
                address_of(port, family), remote_address=address_of(cpe_wan, family), tenant=tenant,
                status=LIFE[stages[sid]]["bgp"])
    if by_kind["bgp_session"] != set(expected_sessions):
        report("provider-bgp-inventory", "plan", "Sessions must cover exactly the reflector pair, "
               "every other provider edge against both reflectors, each actual transit handoff and "
               "each actual customer access circuit.")
    for key, (expected_attrs, expected_refs) in sorted(expected_sessions.items()):
        if None in expected_refs.values():
            report("provider-bgp-session", key, "The addresses, prefix and endpoints this peering "
                   "record cites must resolve to exactly one real object each.")
            continue
        if kind(key) != "bgp_session" or attrs(key) != expected_attrs or refs(key) != expected_refs:
            report("provider-bgp-session", key, "Each peering record must be attributed from the "
                   "actual loopback, handoff address, routing identity and circuit it documents.")
    for key in sorted(k for kind_name in BGP_KINDS for k in by_kind[kind_name]):
        if extra := set(attrs(key)) - BGP_FIELDS[kind(key)]:
            report("provider-bgp-scope-text", key, "BGP records carry inventory fields only; "
                   + ", ".join(sorted(extra)) + " would read as configured or established session state.")

    serving = [d for d in infrastructure if d not in routers and
                  stages.get(str(refs(d).get("site")).removeprefix("site/"), "active") == "active"]
    findings.extend(validate_power(objects, catalog, serving, children, peers, cable_of,
                                   poe_watts=poe_watts, optics_watts=optics_watts,
                                   single_feed={f"site/{sid}" for sid in premises}))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=_workloads(len(premises), len(pops)),
                    peak=recipe["noc_peak_mbps"], reserve=recipe["reserve_fraction"], strict_sites=False, network_offsets=DC_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings


# The network lab (recipe ``discovery_lab``; built by estates/discovery_lab.py),
# restated here. It is a container lab documented beside the estate, so it is
# validated as its own closed slice and then removed before the production
# checks run: those see exactly the graph they would see with the lab off.
# Membership is decided from roles, references and addresses, never from meta,
# so a production object given the lab role must pass every lab obligation
# below instead of escaping the production checks.
LAB_ROLE = "role/lab-router"
LAB_POOL = ip_network("198.18.0.0/15")
LAB_ALIAS = "lab-router"


def discovery_lab(plan, catalog):
    """Return (lab findings, the production plan without the lab slice)."""
    findings = []

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    objects = {o["key"]: o for o in plan["objects"]}
    setting = plan.get("recipe", {}).get("discovery_lab")
    of = lambda kind: [o for o in objects.values() if o["kind"] == kind]
    devices = {o["key"] for o in of("device") if o["refs"].get("role") == LAB_ROLE}
    components = {o["key"] for o in objects.values() if o["refs"].get("device") in devices}
    interfaces = {k for k in components if objects[k]["kind"] == "interface"}
    owned = {o["key"] for o in objects.values() if o["kind"] in {"mac_address", "ip_address"}
             and o["refs"].get("assigned_object") in interfaces}
    cables = {o["key"] for o in of("cable") if {o["refs"].get("a"), o["refs"].get("b")} & interfaces}
    in_pool = lambda net: net.version == 4 and net.subnet_of(LAB_POOL)
    prefixes = {o["key"] for o in of("prefix") if in_pool(ip_network(o["attrs"]["prefix"], strict=False))}
    used = lambda field: {objects[d]["refs"].get(field) for d in devices} - {None}
    types, platforms, rooms, racks = used("device_type"), used("platform"), used("location"), used("rack")
    lab = devices | components | owned | cables | prefixes | types | platforms | rooms | racks
    lab |= {LAB_ROLE} & objects.keys()
    # A manufacturer leaves with the lab only when no production record uses it.
    makers = {objects.get(k, {}).get("refs", {}).get("manufacturer") for k in types | platforms} - {None} - {
        v for k, o in objects.items() if k not in lab for v in o["refs"].values() if isinstance(v, str)}
    lab |= makers
    production = {k: o for k, o in objects.items() if k not in lab}
    if bool(setting) != bool(lab):
        report("lab-recipe", "plan", "Network lab records exist exactly when recipe discovery_lab enables them.")
    if setting and len(devices) != setting.get("nodes"):
        report("lab-recipe", "plan", f"discovery_lab requests {setting.get('nodes')} lab routers; found {len(devices)}.")

    # Closed slice: no production record may reference a lab record. That also
    # catches a shared platform, room or rack and any BGP session, circuit or
    # VRF bound to a lab port. A manufacturer may be shared with production.
    for key, obj in sorted(production.items()):
        for value in obj["refs"].values():
            for target in value if isinstance(value, list) else [value]:
                if target in lab:
                    report("lab-isolation", key, f"Production record references network lab record {target}.")
    for key in sorted(lab - makers):
        if objects[key]["refs"].get("vrf") or objects[key]["kind"] in {
                "power_port", "power_outlet", "console_port", "module", "module_bay", "circuit_termination"}:
            report("lab-isolation", key, "Network lab records carry no VRF, circuit, modeled power, console or module.")
    for key in sorted(cables):
        if not {objects[key]["refs"].get(side) for side in ("a", "b")} <= interfaces:
            report("lab-isolation", key, "A network lab cable may only join two lab router ports.")

    # Addresses: only RFC 2544 benchmarking space, and that space only in the lab.
    leaves = [ip_network(objects[p]["attrs"]["prefix"]) for p in prefixes if objects[p]["attrs"].get("status") != "container"]
    for ip in of("ip_address"):
        address = ip_interface(ip["attrs"]["address"])
        if (ip["key"] in owned) != in_pool(address.network):
            report("lab-address", ip["key"], "Network lab addresses come from 198.18.0.0/15, and only the lab uses it.")

    # Hardware: what the SR Linux container reports, with every front-panel port.
    model = catalog["models"][LAB_ALIAS]
    panel = {p["name"]: p["type"] for p in model["interfaces"]}
    for device in sorted(devices):
        refs, attrs = objects[device]["refs"], objects[device]["attrs"]
        if (objects.get(refs.get("device_type"), {}).get("attrs", {}).get("model") != model["model"]
                or attrs.get("serial") != model["serial_format"]
                or objects.get(refs.get("platform"), {}).get("attrs", {}).get("name") != model["platform"]["name"]):
            report("lab-hardware", device, "Lab routers carry the model, serial and platform the SR Linux container reports.")
        mine = [objects[k] for k in interfaces if objects[k]["refs"]["device"] == device]
        physical = {i["attrs"]["name"]: i["attrs"].get("type") for i in mine if i["attrs"].get("type") != "virtual"}
        virtual = {i["attrs"]["name"]: i["refs"].get("parent") for i in mine if i["attrs"].get("type") == "virtual"}
        if physical != panel or any(name != "system0" and (not name.endswith(".0") or
                                    objects.get(parent, {}).get("attrs", {}).get("name") != name[:-2])
                                    for name, parent in virtual.items()):
            report("lab-hardware", device, "Lab router interfaces are the 7220 IXR-D2L front panel plus system0 and .0 subinterfaces.")
        macs = [objects.get(i["refs"].get("primary_mac_address"), {}).get("attrs", {}).get("mac_address")
                for i in mine if i["attrs"].get("type") != "virtual"]
        if any(not m or not int(m.split(":")[0], 16) & 2 for m in macs):
            report("lab-hardware", device, "Every lab router port carries its locally administered SR Linux MAC.")
        if any(not any(ip_interface(objects[ip]["attrs"]["address"]) in net for net in leaves)
               for ip in owned if objects[ip]["kind"] == "ip_address"
               and objects[objects[ip]["refs"]["assigned_object"]]["refs"]["device"] == device
               and ip_interface(objects[ip]["attrs"]["address"]).network.prefixlen < 32):
            report("lab-address", device, "Every lab router address sits in an active lab prefix.")

    # Placement: a dedicated room and rack at the NOC holding only lab routers.
    for room in sorted(rooms):
        if objects[room]["refs"].get("site") != "site/dc-01" or objects[room]["attrs"].get("name") != "Network Lab":
            report("lab-placement", room, "Lab routers stand in the NOC's Network Lab room.")
    for rack in sorted(racks):
        if objects[rack]["refs"].get("location") not in rooms:
            report("lab-placement", rack, "The lab rack stands in the Network Lab room.")

    # Mirror: lab-<name> of the first PoP's PE pair plus backbone neighbours, wired
    # exactly as their routed /31 adjacencies in the production graph.
    by_name = {o["attrs"]["name"]: k for k, o in production.items() if o["kind"] == "device"}
    mirrors = {d: by_name.get(objects[d]["attrs"]["name"].removeprefix("lab-")) for d in devices}
    pes = {k for k, o in production.items() if o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge"}
    order = plan.get("reservations", {}).get("provider-pop-order") or {"": 0}
    first = {k for k in pes if production[k]["refs"].get("site") == f"site/pop-{min(order, key=order.get)}"}
    ends = defaultdict(set)
    for ip in production.values():
        owner = production.get(ip["refs"].get("assigned_object"), {}) if ip["kind"] == "ip_address" else {}
        address = ip_interface(ip["attrs"]["address"]) if owner.get("kind") == "interface" else None
        if address and address.version == 4 and address.network.prefixlen == 31:
            ends[address.network].add(owner["refs"]["device"])
    mirrored = set(mirrors.values())
    adjacency = Counter(frozenset(pair) for pair in ends.values() if len(pair) == 2 and pair <= mirrored)
    if devices and (None in mirrored or not mirrored <= pes or not first <= mirrored
                    or any(not any(frozenset((m, f)) in adjacency for f in first) for m in mirrored - first)):
        report("lab-mirror", "plan", "Lab routers mirror the first PoP's PE pair and their backbone neighbours.")
    wired = Counter(frozenset(mirrors.get(objects[objects[c]["refs"][side]]["refs"]["device"]) for side in ("a", "b"))
                    for c in cables if {objects[c]["refs"].get(s) for s in ("a", "b")} <= interfaces)
    if devices and wired != adjacency:
        report("lab-mirror", "plan", "Lab cables follow exactly the mirrored routers' routed /31 adjacencies.")
    return findings, {**plan, "objects": [o for o in plan["objects"] if o["key"] not in lab]}
