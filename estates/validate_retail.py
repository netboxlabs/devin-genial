"""Independent retail-chain demand and physical-path obligations.

Recipe policy is restated deliberately instead of importing the retail builder.
The base validator supplies its physical indexes; the shared DC checker inspects
independently sized commerce service groups. Contracts cannot remove obligations.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_network
import math

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics
from .model import selected_alias


# Independent restatement of the profile's address and service policy.
NETWORK_OFFSETS = {"management": 0, "backoffice": 1, "pos": 2, "wireless": 3,
                   "security": 4, "wan": 8, "guest": 12}
DC_NETWORK_OFFSETS = {"management": 0, "applications": 5, "database": 6,
                      "backup": 7, "wan": 8, "storage": 9}
STORES = {"small": dict(workstations=2, pos_terminals=4, aps=2, cameras=4, peak_mbps=20),
          "medium": dict(workstations=4, pos_terminals=8, aps=3, cameras=8, peak_mbps=50),
          "large": dict(workstations=8, pos_terminals=16, aps=5, cameras=12, peak_mbps=100)}
DISTRIBUTION = dict(workstations=12, scanners=24, aps=8, cameras=24, peak_mbps=200)
SERVICE_POLICY = (
    ("commerce-api", "applications", "stores", 40, 4, 8192, 100000, 443),
    ("pos-gateway", "applications", "stores", 30, 4, 8192, 100000, 443),
    ("inventory-db", "database", "stores", 100, 8, 32768, 500000, 5432),
    ("loyalty", "applications", "stores", 120, 4, 8192, 100000, 443),
    ("identity", "applications", "endpoints", 2000, 4, 8192, 100000, 443),
    ("dns", "applications", "sites", 16, 2, 4096, 40000, 53),
    ("monitoring", "applications", "endpoints", 4000, 4, 16384, 200000, 443),
    ("backup", "backup", "stores", 100, 4, 16384, 1000000, 443),
)
TIER_1 = {"commerce-api", "pos-gateway", "inventory-db", "identity", "dns"}


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "retail-chain":
        return []
    access_alias = selected_alias(recipe, "access")
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    stores, reserve = recipe.get("stores"), recipe.get("reserve_fraction")
    tiers, staff = recipe.get("wan_tiers_mbps"), recipe.get("headquarters_staff")
    offices, centres = recipe.get("headquarters"), recipe.get("distribution_centers")
    if (not isinstance(stores, dict) or set(stores) != set(STORES) or
            any(type(n) is not int or not 0 <= n <= 2000 for n in stores.values()) or
            not sum(stores.values()) or
            type(offices) is not int or not 0 <= offices <= 1 or
            type(centres) is not int or not 0 <= centres <= 6 or
            type(staff) is not int or not 24 <= staff <= 192 or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("retail-recipe", "plan", "Retail validation requires bounded store, headquarters, distribution and reserve demand.")
        return findings
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("retail-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    usable = Decimal(1) - Decimal(str(reserve))
    if poe_watts is None:
        _, poe_watts = analyze_poe(plan, catalog)
    if optics_watts is None:
        _, optics_watts = analyze_optics(plan, catalog)

    def attrs(key):
        return objects.get(key, {}).get("attrs", {})

    def refs(key):
        return objects.get(key, {}).get("refs", {})

    def meta(key):
        return objects.get(key, {}).get("meta", {})

    def kind(key):
        return objects.get(key, {}).get("kind")

    def vlans(port):
        return set(refs(port).get("tagged_vlans", [])) | ({refs(port)["untagged_vlan"]} if refs(port).get("untagged_vlan") else set())

    def active_path(port):
        members = component_members.get(component_of.get(port), [])
        return (bool(peers.get(port)) and bool(members) and
                all(attrs(p).get("enabled", True) for p in members) and
                all(attrs(cable_of[p]).get("status") == "connected" for p in members if p in cable_of))

    store_ids = [(f"st-{size[0]}{n+1:04}", size) for size in ("small", "medium", "large")
                 for n in range(stores[size])]
    centre_ids = [f"di-{n+1:02}" for n in range(centres)]
    expected_sites = ({f"site/{sid}" for sid, _ in store_ids} | {f"site/{sid}" for sid in centre_ids} |
                      {f"site/hq-{n+1:02}" for n in range(offices)} | {"site/dc-01", "site/dc-02"})
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != expected_sites:
        report("retail-site-inventory", "plan", "Chain requires exactly its requested stores, distribution centres, headquarters and two commerce data centers.")

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("retail-address-allocation", "plan", "Every retail site needs a distinct /16 reservation inside the private pool.")
        return findings

    campuses = ([(sid, "store", STORES[size]) for sid, size in store_ids] +
                [(sid, "distribution", DISTRIBUTION) for sid in centre_ids])
    for sid, facility, demand in campuses:
        site = f"site/{sid}"
        store = facility == "store"
        if attrs(site).get("status") != "active":
            report("retail-site-status", site, "Requested retail site must be active.")
        network_offsets = {name: offset for name, offset in NETWORK_OFFSETS.items()
                           if store or name not in {"pos", "guest"}}
        for name in ("pos", "guest") if not store else ():
            if any(key in objects for key in (f"vlan/{sid}/{name}", f"prefix/{sid}/{name}")):
                report("retail-segment-unrequested", site, f"A distribution centre has no {name} segment; it has no lanes and no public wireless service.")
        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site and c.get("kind") == facility] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("retail-contract-inventory", site, "Each requested retail site needs exactly one explanatory campus contract.")
        elif contracts[0].get("demand") != demand:
            report("retail-demand-report", site, "Reported store or warehouse demand must follow the authored format without restating installed inventory.")

        suffixes = (("sales-floor", "sales_floor"), ("back-office", "back_office"), ("stockroom", "stockroom")) if store else (
            ("warehouse-floor", "warehouse_floor"), ("office-01", "office"), ("shipping-dock", "shipping_dock"))
        selling, office, storage = (f"location/{sid}/{suffix}" for suffix, _ in suffixes)
        rooms = {f"location/{sid}/{suffix}": (space_type, origin) for (suffix, space_type), origin in
                 zip(suffixes, ([6, 4, 0], [6, 30, 0], [30, 30, 0]))}
        closet = f"location/{sid}"
        actual_rooms = {key for key in children[("site", site)] if kind(key) == "location" and
                        meta(key).get("space_type") in {"sales_floor", "back_office", "stockroom",
                                                        "warehouse_floor", "office", "shipping_dock"}}
        if actual_rooms != set(rooms):
            report("retail-room-inventory", site, "Trading, staff and storage rooms must match the authored single-floor layout exactly.")
        for room, (space_type, origin) in rooms.items():
            if (kind(room) != "location" or meta(room).get("space_type") != space_type or
                    meta(room).get("floor") != 1 or meta(room).get("position_m") != origin or
                    refs(room).get("parent") != f"location/{sid}/floor-01" or attrs(room).get("status") != "active"):
                report("retail-room-placement", room, "Retail rooms must occupy their fixed active ground-floor locations.")
        if (kind(closet) != "location" or meta(closet).get("space_type") != "equipment_room" or
                meta(closet).get("floor") != 1):
            report("retail-closet-inventory", site, "Each retail site needs its permanent ground-floor equipment room.")

        expected = {}

        def endpoint(label, role, network, room, ordinal):
            expected[f"device/{sid}/{label}"] = (role, network, room, ordinal)

        for index in range(demand["workstations"]):
            endpoint(f"desk-{index+1:03}", "workstation", "backoffice", office, index+1)
        lanes = demand["pos_terminals"] if store else demand["scanners"]
        for index in range(lanes):
            endpoint(f"{'pos' if store else 'scan'}-{index+1:03}", "pos-terminal" if store else "scanner",
                     "pos" if store else "backoffice", selling, index+1)
        for index in range(demand["aps"]):
            room, ordinal = (office, 1) if index == 0 else (selling, index)
            endpoint(f"ap-{index+1:03}", "ap", "wireless", room, ordinal)
        front = math.ceil(demand["cameras"]/2)
        for index in range(demand["cameras"]):
            room, ordinal = (selling, index+1) if index < front else (storage, index+1-front)
            endpoint(f"cam-{index+1:03}", "camera", "security", room, ordinal)

        devices = [key for key in children[("site", site)] if kind(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in
                  {"role/workstation", "role/pos-terminal", "role/scanner", "role/ap", "role/camera"}}
        if actual != set(expected):
            report("retail-endpoint-inventory", site, "Every requested workstation, lane or scanner, radio and camera must match the authored format demand.")
        counted = Counter(role for role, _, _, _ in expected.values())
        for role, wanted in counted.items():
            if len(roles[f"role/{role}"]) != wanted:
                report("retail-endpoint-demand", site, f"Format demand requires {wanted} {role} devices; found {len(roles[f'role/{role}'])}.")
        switch_counts, closet_endpoints = Counter(), set()
        for device, (role, network, room, ordinal) in expected.items():
            closet_endpoints.add(device)
            if (kind(device) != "device" or attrs(device).get("status") != "active" or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    not meta(device).get("endpoint") or meta(device).get("network") != network):
                report("retail-endpoint-placement", device, "Required endpoint identity, active status, segment and room must match the retail format.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind(peer) != "interface" or refs(switch).get("role") != "role/access" or
                    attrs(switch).get("status") != "active" or refs(switch).get("location") != closet or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("retail-endpoint-path", device, "Endpoint needs its own connected enabled VLAN path to an active access switch in the site equipment room.")
            if role == "ap":
                # PoE reachability is the actual copper path into a catalog
                # access port; the shared PoE analyser checks the supply budget.
                if attrs(peer).get("name") not in catalog.get(access_alias, {}).get("access_ports", []):
                    report("retail-ap-power", device, "Coverage radio must reach a catalog PoE access port on its serving switch.")
            if switch:
                switch_counts[switch] += 1
            position = meta(device).get("placement", {})
            span, columns = (8, 2) if role == "ap" else (4, 4) if role == "camera" else (1.2, 6)
            height = 2.8 if role == "ap" else 2.5 if role == "camera" else 0.8
            origin = rooms[room][1]
            mount = [origin[0]+1+span*((ordinal-1) % columns), origin[1]+1+span*((ordinal-1)//columns),
                     origin[2]+height]
            route = math.ceil(sum(abs(a-b) for a, b in zip(mount, [24, 18, 0]))+10)
            if (position.get("room") != room or position.get("floor") != 1 or
                    position.get("cable_origin") != closet or position.get("position_m") != mount or
                    path_lengths.get(port) != route or not 0 < route <= 80):
                report("retail-endpoint-route", device, "Each endpoint ordinal must retain its fixed local mount and actual copper route inside the 80 m ceiling.")
            address = refs(device).get("primary_ip4")
            if (kind(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or refs(address).get("vrf") != f"vrf/{network}"):
                report("retail-endpoint-address", device, "Installed endpoint requires its active primary address on eth0 in the intended segment VRF.")
        capacity = int(Decimal(len(catalog.get(access_alias, {}).get("access_ports", []))) * usable)
        scope = f"access-endpoints/{sid}/{closet}"
        slots = plan.get("reservations", {}).get(scope)
        if (not capacity or not isinstance(slots, dict) or set(slots) != closet_endpoints or
                any(type(n) is not int or not 0 <= n < 38*capacity for n in slots.values()) or
                len(set(slots.values())) != len(slots)):
            report("retail-access-allocation", site, "Each requested endpoint needs one unique bounded permanent access-port slot.")
        else:
            count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity)))
            switches = {f"device/{sid}/access-{n+1:02}" for n in range(count)}
            if {key for key in roles["role/access"]} != switches:
                report("retail-access-inventory", site, "The equipment room needs complete access pairs through its last permanent slot, retaining copper and PoE headroom.")
            for device, slot in slots.items():
                pair, offset = divmod(slot, 2*capacity)
                switch = f"device/{sid}/access-{2*pair+offset % 2+1:02}"
                port = catalog[access_alias]["access_ports"][offset//2]
                if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port}":
                    report("retail-access-allocation", device, "Actual endpoint path must match its reserved switch and catalog copper port.")
        if len(roles["role/access"]) > 38:
            report("retail-access-capacity", site, "Access-switch attachments exceed the finite distribution port budget.")
        for device in roles["role/access"]:
            if meta(device).get("hardware") != access_alias or switch_counts[device] > capacity:
                report("retail-access-capacity", device, "Actual attached endpoints must fit this catalog access switch after reserve.")

        required_vlans = {f"vlan/{sid}/{name}" for name in network_offsets if name != "wan"}
        if len(roles["role/distribution"]) != 2 or len(roles["role/wan-edge"]) != 2:
            report("retail-core-inventory", site, "Retail site requires two distribution switches and two carrier edge devices.")
        for role in ("role/access", "role/wan-edge"):
            for device in roles[role]:
                upstreams = set()
                for port in children[("device", device)]:
                    peer = peers.get(port)
                    parent = refs(peer).get("device")
                    if (kind(port) == "interface" and kind(peer) == "interface" and
                            parent in roles["role/distribution"] and active_path(port) and
                            all(attrs(node).get("status") == "active" for node in (device, parent)) and
                            required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                        upstreams.add(parent)
                if len(upstreams) != 2:
                    report("retail-uplink-path", device, "Access and carrier edges require two connected active distribution uplinks carrying every site segment.")
        for network in network_offsets:
            if network == "wan":
                continue
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for device in roles["role/distribution"]:
                for port in children[("device", device)]:
                    if (kind(port) == "interface" and attrs(port).get("type") == "virtual" and
                            attrs(port).get("enabled", True) and attrs(device).get("status") == "active" and
                            vlan in vlans(port) and
                            any(kind(ip) == "ip_address" and attrs(ip).get("status") == "active"
                                for ip in children[("assigned_object", port)])):
                        gateways.add(device)
            if len(gateways) != 2:
                report("retail-gateway-inventory", vlan, "Every retail segment needs two active addressed distribution gateway SVIs.")
        if sid not in allocations:
            report("retail-address-allocation", site, "Requested retail site has no persistent reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid]*65536, 16))
            for network, offset in network_offsets.items():
                prefix = f"prefix/{sid}/{network}"
                wanted = ip_network((int(container.network_address) + offset*256, 24))
                if (attrs(prefix).get("prefix") != str(wanted) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or refs(prefix).get("vrf") != f"vrf/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(offset+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site):
                    report("retail-prefix-policy", prefix, "Site role /24 must retain its fixed offset, site VLAN and VRF inside the reserved /16.")
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("retail-wan-inventory", site, "Retail carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers if rate >= (50 if side == "a" else 100) and
                                Decimal(rate)*usable >= demand["peak_mbps"]), None)
            if len(circuits[provider]) != 1:
                report("retail-wan-inventory", site, "Each retail site requires exactly one independent attachment to each modeled carrier.")
            for term in circuits[provider]:
                circuit = refs(term).get("circuit")
                peer = peers.get(term)
                edge = refs(peer).get("device")
                carrier_edges.add(edge)
                terms = [key for key in children[("circuit", circuit)] if kind(key) == "circuit_termination"]
                remote = [key for key in terms if kind(refs(key).get("termination")) == "provider_network"]
                if (wanted_rate is None or attrs(circuit).get("commit_rate") != wanted_rate*1000 or
                        attrs(circuit).get("status") != "active" or not active_path(term) or
                        edge not in roles["role/wan-edge"] or attrs(edge).get("status") != "active" or
                        attrs(peer).get("type") != "1000base-t" or attrs(term).get("port_speed") != 1000000 or
                        len(terms) != 2 or len(remote) != 1 or
                        refs(refs(remote[0]).get("termination")).get("provider") != provider or
                        attrs(remote[0]).get("term_side") != "Z" or attrs(term).get("term_side") != "A"):
                    report("retail-wan-capacity", circuit, "Retail carrier commitment and active 1 Gbps edge handoff must cover the format's peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("retail-wan-diversity", site, "The two retail carriers must terminate on different active edge devices.")
        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in {"role/distribution", "role/wan-edge", "role/access",
                                                "role/management", "role/console-server", "role/pdu",
                                                "role/patch-panel"}:
                report("retail-equipment-role", device, "Retail infrastructure must serve an explicit network, management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room != closet or attrs(rack).get("status") != "active"):
                report("retail-equipment-placement", device, "Retail infrastructure must occupy an active rack in the site equipment room.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of,
                                       poe_watts=poe_watts, optics_watts=optics_watts))

    totals = dict(stores=sum(stores.values()), sites=sum(stores.values()) + offices + centres)
    totals["endpoints"] = (sum(n*sum(STORES[size][field] for field in
                                     ("workstations", "pos_terminals", "aps", "cameras"))
                               for size, n in stores.items()) +
                           centres*sum(DISTRIBUTION[field] for field in
                                       ("workstations", "scanners", "aps", "cameras")) +
                           offices*(staff + math.ceil(staff/12) + 2*math.ceil(math.ceil(staff/12)/4)))
    peak = (sum(n*STORES[size]["peak_mbps"] for size, n in stores.items()) +
            offices*2*staff + centres*DISTRIBUTION["peak_mbps"])
    services = []
    for key, network, metric, threshold, vcpus, memory, disk, port in SERVICE_POLICY:
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        services.append(dict(key=key, groups=max(1, math.ceil(totals[metric]/threshold)), replicas=2,
                             failure_domain="rack", network=network, vcpus=vcpus, memory_mb=memory,
                             disk_mb=disk, listeners=listeners,
                             criticality="tier-1" if key in TIER_1 else "tier-2"))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01", "site/dc-02"}, workloads=services,
                    peak=peak, reserve=reserve, strict_sites=False, network_offsets=DC_NETWORK_OFFSETS,
                    poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
