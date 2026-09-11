"""Independent school demand and physical-path obligations.

Recipe policy is restated deliberately instead of importing the school builder.
The base validator supplies its physical indexes; the shared DC checker inspects
independently sized district service groups. Contracts cannot remove obligations.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_network
import math
import re

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics


NETWORK_OFFSETS = {"management": 0, "staff": 1, "students": 2,
                   "wireless": 3, "security": 4, "wan": 9}
SERVICE_POLICY = (
    ("identity", "population", 500, 4, 8192, 100000, 443),
    ("dns", "schools", 16, 2, 4096, 40000, 53),
    ("learning-portal", "enrollment", 500, 4, 8192, 100000, 443),
    ("files", "enrollment", 1000, 4, 16384, 500000, 445),
    ("monitoring", "schools", 16, 4, 16384, 200000, 443),
)
METROS = {"Chicago": ("IL", "Illinois", "America/Chicago"),
          "Detroit": ("MI", "Michigan", "America/Detroit"),
          "Cleveland": ("OH", "Ohio", "America/New_York"),
          "Milwaukee": ("WI", "Wisconsin", "America/Chicago")}


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "school-district":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    schools, reserve = recipe.get("schools"), recipe.get("reserve_fraction")
    tiers = recipe.get("wan_tiers_mbps")
    if (not isinstance(schools, list) or not 1 <= len(schools) <= 64 or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("school-recipe", "plan", "School validation requires bounded school demand and reserve.")
        return findings
    if (not isinstance(tiers, list) or not tiers or any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("school-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    for school in schools:
        if (not isinstance(school, dict) or not isinstance(school.get("key"), str) or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,19}", school["key"]) or
                any(type(school.get(field)) is not int or not low <= school[field] <= high
                    for field, low, high in (("classrooms", 1, 32), ("students_per_classroom", 1, 36),
                        ("wired_seats_per_classroom", 0, 12), ("administrative_staff", 0, 48),
                        ("lab_seats", 0, 36), ("wan_peak_mbps", 1, 800)))):
            report("school-recipe", "plan", "School inputs require bounded classrooms, students, wired seats, administration, lab seats and WAN demand.")
            return findings
        if (school["wired_seats_per_classroom"] > school["students_per_classroom"] or
                school["lab_seats"] > school["classrooms"] * (school["students_per_classroom"] - school["wired_seats_per_classroom"]) or
                Decimal(school["wan_peak_mbps"]) > 1000 * (1 - Decimal(str(reserve)))):
            report("school-recipe", "plan", "Installed student seats cannot exceed the bounded enrolled population.")
            return findings
        zones = {f"classroom-{n:03}" for n in range(1, school["classrooms"]+1)}
        zones |= {f"admin-{n+1:02}" for n in range((school["administrative_staff"]+11)//12)}
        if school["lab_seats"]:
            zones.add("computer-lab")
        wireless = school.get("wireless")
        if (not isinstance(wireless, dict) or set(wireless) != zones or
                any(not isinstance(v, dict) or set(v) != {"managed", "guest"} or
                    any(type(n) is not int or not 0 <= n <= 128 for n in v.values()) or sum(v.values()) > 128
                    for v in wireless.values()) or sum(v["guest"] for v in wireless.values()) > 4084):
            report("school-wireless-demand", "plan", "Frozen wireless demand needs exactly its real zones with bounded managed/guest device counts and guest address capacity.")
            return findings
    if len({s["key"] for s in schools}) != len(schools) or sum(s["wan_peak_mbps"] for s in schools) > 16000:
        report("school-recipe", "plan", "School keys must be unique and district WAN demand must fit the bounded DC design.")
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

    sites = {f"site/school-{s['key']}" for s in schools}
    actual_sites = {key for key, obj in objects.items() if obj["kind"] == "site"}
    if actual_sites != sites | {"site/dc-01"}:
        report("school-site-inventory", "plan", "District requires exactly its keyed schools and one service DC.")
    locations, addresses = set(), set()
    for site in sorted(sites | {"site/dc-01"}):
        geography = meta(site).get("geography", {})
        if not isinstance(geography, dict) or not isinstance(geography.get("city"), str):
            report("school-geography", site, "District facilities need an authored metropolitan location.")
            continue
        city = geography["city"]
        state, state_name, zone = METROS.get(city, (None, None, None))
        region = refs(site).get("region")
        if (not state or geography.get("country") != "US" or geography.get("state") != state or
                geography.get("synthetic") is not True or attrs(site).get("time_zone") != zone or
                region != f"region/{recipe.get('namespace')}/us/{str(state).lower()}" or
                kind(region) != "region" or kind(refs(region).get("parent")) != "region"):
            report("school-geography", site, "City, state, region and time zone must describe one coherent authored district location.")
        locations.add((city, state, zone, region))
        address = attrs(site).get("physical_address")
        if not isinstance(address, str) or not address.strip() or f"{city}, {state_name}" not in address:
            report("school-geography", site, "Fictional physical address must agree with the district city and state.")
        if site in sites and isinstance(address, str):
            if address in addresses:
                report("school-geography", site, "Separate school facilities need distinct fictional physical addresses.")
            addresses.add(address)
    if len(locations) != 1:
        report("school-geography", "plan", "All schools and the district service DC must share one metropolitan district.")
    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("school-address-allocation", "plan", "Every school needs a distinct /16 site reservation inside the private pool.")
        return findings

    for school in schools:
        sid = f"school-{school['key']}"
        site = f"site/{sid}"
        if attrs(site).get("status") != "active":
            report("school-site-status", site, "Requested school must be active.")
        classrooms = school["classrooms"]
        floors = (classrooms + 7) // 8
        office_count = (school["administrative_staff"] + 11) // 12
        enrollment = classrooms * school["students_per_classroom"]
        wired = classrooms * school["wired_seats_per_classroom"] + school["lab_seats"]
        staff = classrooms + school["administrative_staff"]
        wireless = school["wireless"]
        network_offsets = NETWORK_OFFSETS | ({"guest": 12} if any(z["guest"] for z in wireless.values()) else {})
        if "guest" not in network_offsets and any(key in objects for key in (f"vlan/{sid}/guest", f"prefix/{sid}/guest")):
            report("school-guest-unrequested", site, "Guest segmentation requires nonzero planned guest demand at this campus.")
        expected_demand = dict(enrollment=enrollment, teaching_staff=classrooms,
            administrative_staff=school["administrative_staff"], staff=staff, wired_student_seats=wired,
            wireless_students=enrollment - wired, wireless_staff=staff, workstations=staff + wired,
            managed_clients=sum(z["managed"] for z in wireless.values()),
            guest_clients=sum(z["guest"] for z in wireless.values()),
            aps=sum(max(1, (z["managed"]+z["guest"]+31)//32) for z in wireless.values()), cameras=2 * floors,
            peak_mbps=school["wan_peak_mbps"])
        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site and c.get("kind") == "school"] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("school-contract-inventory", site, "Each requested school needs exactly one explanatory campus contract.")
        elif contracts[0].get("demand") != expected_demand:
            report("school-demand-report", site, "Reported enrollment, staff, installed seats and wireless cohorts must follow the recipe without adding lab seats to enrollment.")
        rooms = {f"location/{sid}/classroom-{n:03}": ("classroom", (n - 1) // 8 + 1,
                    [6+12*((n-1)%4), 4+24*(((n-1)%8)//4), 4*((n-1)//8)])
                 for n in range(1, classrooms + 1)}
        rooms.update({f"location/{sid}/admin-{n:02}": ("office", 1, [6+12*(n-1),50,0]) for n in range(1, office_count + 1)})
        if school["lab_seats"]:
            rooms[f"location/{sid}/computer-lab"] = ("computer_lab", 1, [6,40,0])
        closets = {floor: f"location/{sid}" + (f"/idf-{floor:02}" if floor > 1 else "") for floor in range(1, floors + 1)}
        devices = [key for key in children[("site", site)] if kind(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        actual_rooms = {key for key in children[("site", site)] if kind(key) == "location" and
                        meta(key).get("space_type") in {"classroom", "office", "computer_lab"}}
        if actual_rooms != set(rooms):
            report("school-room-inventory", site, "Classroom, administration and lab rooms must match the requested demand exactly.")
        for room, (space_type, floor, origin) in rooms.items():
            if (kind(room) != "location" or meta(room).get("space_type") != space_type or meta(room).get("floor") != floor or
                    meta(room).get("position_m") != origin or refs(room).get("parent") != f"location/{sid}/floor-{floor:02}" or attrs(room).get("status") != "active"):
                report("school-room-placement", room, "Teaching, administration and lab rooms must occupy their fixed active floor locations.")
            capacity = {"workstations": school["wired_seats_per_classroom"] + 1, "students": school["students_per_classroom"]}
            if space_type != "classroom":
                capacity = {"workstations": 12 if space_type == "office" else 36}
            if meta(room).get("capacity") != capacity:
                report("school-room-capacity", room, "Room capacity must match the finite classroom, administration or shared-lab demand policy.")
        actual_closets = {key for key in children[("site", site)] if kind(key) == "location" and meta(key).get("space_type") == "equipment_room"}
        if actual_closets != set(closets.values()):
            report("school-closet-inventory", site, "Each occupied teaching floor needs exactly its permanent local equipment room.")
        for floor, room in closets.items():
            if (meta(room).get("floor") != floor or refs(room).get("parent") != f"location/{sid}/floor-{floor:02}" or
                    attrs(room).get("status") != "active"):
                report("school-room-placement", room, "Closets must be active on their own occupied floor.")

        expected = {}

        def endpoint(label, cohort, room, network, role="workstation", ordinal=1):
            expected[f"device/{sid}/{label}"] = (cohort, room, network, role, ordinal)

        def aps(zone, label, cohort, room):
            value = wireless[zone]
            for ordinal in range(1, max(1, (value["managed"]+value["guest"]+31)//32)+1):
                endpoint(label if ordinal == 1 else f"{label}-{ordinal:02}", cohort, room, "wireless", "ap", ordinal)

        for n in range(1, classrooms + 1):
            room = f"location/{sid}/classroom-{n:03}"
            endpoint(f"teacher-{n:03}", "teacher", room, "staff")
            aps(f"classroom-{n:03}", f"ap-classroom-{n:03}", "classroom-ap", room)
            for seat in range(1, school["wired_seats_per_classroom"] + 1):
                endpoint(f"student-{n:03}-{seat:02}", "student", room, "students")
        for n in range(1, school["administrative_staff"] + 1):
            endpoint(f"admin-{n:03}", "administration", f"location/{sid}/admin-{(n - 1) // 12 + 1:02}", "staff")
        for n in range(1, office_count + 1):
            aps(f"admin-{n:02}", f"ap-admin-{n:02}", "office-ap", f"location/{sid}/admin-{n:02}")
        for n in range(1, school["lab_seats"] + 1):
            endpoint(f"lab-{n:03}", "student-lab", f"location/{sid}/computer-lab", "students")
        if school["lab_seats"]:
            aps("computer-lab", "ap-lab-01", "lab-ap", f"location/{sid}/computer-lab")
        for floor in closets:
            for side in ("a", "b"):
                endpoint(f"camera-floor-{floor:02}-{side}", "camera", f"location/{sid}/classroom-{(floor - 1) * 8 + 1:03}", "security", "camera")
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in {"role/workstation", "role/ap", "role/camera"}}
        if actual != set(expected):
            report("school-endpoint-inventory", site, "Every teacher, installed student seat, administration desk, AP and floor camera must match recipe demand.")
        switch_counts, room_endpoints = Counter(), defaultdict(set)
        for device, (cohort, room, network, role, ordinal) in expected.items():
            floor = rooms[room][1]
            closet = closets[floor]
            room_endpoints[closet].add(device)
            if (kind(device) != "device" or attrs(device).get("status") != "active" or refs(device).get("location") != room or
                    refs(device).get("role") != f"role/{role}" or not meta(device).get("endpoint") or
                    meta(device).get("cohort") != cohort or meta(device).get("network") != network):
                report("school-endpoint-placement", device, "Required endpoint identity, active status, cohort, segment and room must match the school recipe.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind(peer) != "interface" or refs(switch).get("role") != "role/access" or
                    attrs(switch).get("status") != "active" or refs(switch).get("location") != closet or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("school-endpoint-path", device, "Endpoint must have its own connected enabled VLAN path to an active access switch in its floor closet.")
            members = component_members.get(component_of.get(port), [])
            if recipe.get("patching") == "panels":
                passive = {refs(p).get("device") for p in members if kind(p) in {"front_port", "rear_port"}}
                outlets = {p for p in passive if meta(p).get("purpose") == "wall-outlet"}
                panels = {p for p in passive if meta(p).get("purpose") == "patch-panel"}
                if (len({cable_of[p] for p in members if p in cable_of}) != 3 or len(passive) != 2 or
                        len(outlets) != 1 or len(panels) != 1 or
                        any(meta(p).get("serves_endpoint") != device or refs(p).get("location") != room for p in outlets) or
                        any(refs(p).get("location") != closet for p in panels)):
                    report("school-endpoint-patching", device, "Panel channels require three actual cables through the endpoint's room outlet and a panel in its serving closet.")
            elif len(members) != 2:
                report("school-endpoint-patching", device, "Direct classroom channels require one cable between the endpoint and access port.")
            if switch:
                switch_counts[switch] += 1
            position = meta(device).get("placement", {})
            if role == "ap":
                origin = rooms[room][2]
                mount = [origin[0]+4+4*((ordinal-1)%2), origin[1]+4+4*((ordinal-1)//2), origin[2]+2.8]
                route = math.ceil(sum(abs(a-b) for a,b in zip(mount,[24,18,4*(floor-1)]))+10)
                if position.get("position_m") != mount or path_lengths.get(port) != route:
                    report("school-ap-mount", device, "Demanded AP ordinal must retain its unique fixed local mount and actual copper route.")
            if (position.get("room") != room or position.get("floor") != floor or position.get("cable_origin") != closet or
                    not 0 < path_lengths.get(port, 0) <= 80):
                report("school-endpoint-route", device, "Endpoint placement and its measured copper route must agree with the fixed floor closet and 80 m ceiling.")
            point, origin = position.get("position_m"), meta(closet).get("position_m")
            if (not isinstance(point, list) or not isinstance(origin, list) or len(point) != 3 or len(origin) != 3 or
                    any(type(n) not in (int, float) or not math.isfinite(n) for n in point + origin) or
                    not (floor - 1) * 4 <= point[2] < floor * 4 or
                    path_lengths.get(port, 0) < sum(abs(a - b) for a, b in zip(point, origin))):
                report("school-endpoint-route", device, "Finite mounting coordinates must fit the declared floor and actual copper route length.")
            address = refs(device).get("primary_ip4")
            if (kind(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or refs(address).get("vrf") != f"vrf/{network}"):
                report("school-endpoint-address", device, "Installed endpoint requires its active primary address on eth0 in the intended segment VRF.")
        capacity = int(Decimal(len(catalog.get("access", {}).get("access_ports", []))) * usable)
        access_by_room = Counter(refs(device).get("location") for device in roles["role/access"])
        for room, members in room_endpoints.items():
            scope = f"access-endpoints/{sid}/{room}"
            slots = plan.get("reservations", {}).get(scope)
            if (not capacity or not isinstance(slots, dict) or set(slots) != members or
                    any(type(n) is not int or not 0 <= n < 38*capacity for n in slots.values()) or
                    len(set(slots.values())) != len(slots)):
                report("school-access-allocation", room, "Each demanded local endpoint needs one unique bounded permanent access-port slot.")
                continue
            count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity)))
            prefix = "" if room == f"location/{sid}" else room.rsplit("/",1)[-1]+"-"
            switches = {f"device/{sid}/{prefix}access-{n+1:02}" for n in range(count)}
            actual_switches = {k for k in roles["role/access"] if refs(k).get("location") == room}
            if actual_switches != switches or access_by_room[room] != count:
                report("school-access-inventory", room, "Each floor needs complete access pairs through its last permanent slot, retaining copper and PoE headroom.")
            for device, slot in slots.items():
                pair, offset = divmod(slot, 2*capacity)
                switch = f"device/{sid}/{prefix}access-{2*pair+offset%2+1:02}"
                port = catalog["access"]["access_ports"][offset//2]
                if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port}":
                    report("school-access-allocation", device, "Actual endpoint path must match its reserved switch and catalog copper port.")
        if len(roles["role/access"]) > 38:
            report("school-access-capacity", site, "Access-switch attachments exceed the finite distribution port budget.")
        required_vlans = {f"vlan/{sid}/{name}" for name in network_offsets if name != "wan"}
        mdf = closets[1]
        if len(roles["role/distribution"]) != 2 or len(roles["role/wan-edge"]) != 2:
            report("school-core-inventory", site, "Campus requires two distribution switches and two carrier edge devices.")
        interconnects = set()
        for device in roles["role/distribution"]:
            for port in children[("device", device)]:
                peer = peers.get(port)
                parent = refs(peer).get("device")
                if (kind(port) == "interface" and kind(peer) == "interface" and
                        parent in roles["role/distribution"] and parent != device and active_path(port) and
                        attrs(device).get("status") == attrs(parent).get("status") == "active" and
                        required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                    interconnects.add(tuple(sorted((port, peer))))
        if len(interconnects) != 1:
            report("school-distribution-interconnect", site, "The distribution pair requires one active connected peer link carrying all campus segments.")
        for role in ("role/access", "role/wan-edge"):
            for device in roles[role]:
                upstreams = set()
                if role == "role/access" and (meta(device).get("hardware") != "access" or switch_counts[device] > capacity):
                    report("school-access-capacity", device, "Actual attached endpoints must fit this catalog access switch after reserve.")
                for port in children[("device", device)]:
                    peer = peers.get(port)
                    parent = refs(peer).get("device")
                    if (kind(port) == "interface" and kind(peer) == "interface" and parent in roles["role/distribution"] and
                            active_path(port) and refs(parent).get("location") == mdf and
                            all(attrs(d).get("status") == "active" for d in (device, parent)) and
                            required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                        upstreams.add(parent)
                if len(upstreams) != 2:
                    report("school-uplink-path", device, "Access and carrier edges require two connected active MDF distribution uplinks carrying every campus segment.")
        for role in ("role/distribution", "role/wan-edge"):
            if any(refs(device).get("location") != mdf or meta(device).get("hardware") !=
                    ("leaf" if role == "role/distribution" else "edge") for device in roles[role]):
                report("school-core-placement", site, "Distribution and carrier-edge equipment must remain in the MDF.")
        for network in network_offsets:
            if network == "wan":
                continue
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for device in roles["role/distribution"]:
                for port in children[("device", device)]:
                    if (kind(port) == "interface" and attrs(port).get("type") == "virtual" and attrs(port).get("enabled", True) and
                            attrs(device).get("status") == "active" and vlan in vlans(port) and
                            any(kind(ip) == "ip_address" and attrs(ip).get("status") == "active" for ip in children[("assigned_object", port)])):
                        gateways.add(device)
            if len(gateways) != 2:
                report("school-gateway-inventory", vlan, "Every campus segment needs two active addressed distribution gateway SVIs.")
        if sid not in allocations:
            report("school-address-allocation", site, "Requested school has no persistent site reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid] * 65536, 16))
            for network, offset in network_offsets.items():
                prefix = f"prefix/{sid}/{network}"
                expected_prefix = ip_network((int(container.network_address) + offset * 4096, 20))
                if (attrs(prefix).get("prefix") != str(expected_prefix) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or refs(prefix).get("vrf") != f"vrf/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(offset+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site):
                    report("school-prefix-policy", prefix, "Campus role /20 must retain its fixed offset, site VLAN and VRF inside the reserved /16.")
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("school-wan-inventory", site, "School carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers
                                if rate >= (50 if side == "a" else 100) and Decimal(rate) * usable >= school["wan_peak_mbps"]), None)
            if len(circuits[provider]) != 1:
                report("school-wan-inventory", site, "Each school requires exactly one independent attachment to each modeled carrier.")
            for term in circuits[provider]:
                circuit = refs(term).get("circuit")
                peer = peers.get(term)
                edge = refs(peer).get("device")
                carrier_edges.add(edge)
                terms = [key for key in children[("circuit", circuit)] if kind(key) == "circuit_termination"]
                remote = [key for key in terms if kind(refs(key).get("termination")) == "provider_network"]
                if (wanted_rate is None or attrs(circuit).get("commit_rate") != wanted_rate * 1000 or
                        attrs(circuit).get("status") != "active" or not active_path(term) or edge not in roles["role/wan-edge"] or
                        attrs(edge).get("status") != "active" or attrs(peer).get("type") != "1000base-t" or
                        attrs(term).get("port_speed") != 1000000 or len(terms) != 2 or len(remote) != 1 or
                        refs(refs(remote[0]).get("termination")).get("provider") != provider or
                        attrs(remote[0]).get("term_side") != "Z" or attrs(term).get("term_side") != "A"):
                    report("school-wan-capacity", circuit, "School carrier commitment and active 1 Gbps edge handoff must cover recipe peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("school-wan-diversity", site, "The two school carriers must terminate on different active edge devices.")
        # Exclude only demanded endpoints and the actual catalog outlet type.
        # Descriptive endpoint/purpose flags cannot erase power obligations.
        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in {"role/distribution", "role/wan-edge", "role/access", "role/management",
                    "role/console-server", "role/pdu", "role/patch-panel"}:
                report("school-equipment-role", device, "Campus infrastructure must serve an explicit network, management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room not in closets.values() or attrs(rack).get("status") != "active"):
                report("school-equipment-placement", device, "Campus infrastructure must occupy an active rack in an occupied floor closet.")
            model = catalog.get(meta(device).get("hardware"), {})
            management_ports = {p["name"] for p in model.get("interfaces", []) if p.get("mgmt_only")}
            management_vlan = f"vlan/{sid}/management"
            if management_ports:
                address = refs(device).get("primary_ip4")
                primary = refs(address).get("assigned_object")
                valid_primary = (kind(address) == "ip_address" and attrs(address).get("status") == "active" and
                    refs(address).get("vrf") == "vrf/management" and refs(primary).get("device") == device and
                    kind(primary) == "interface" and attrs(primary).get("enabled", True) and vlans(primary) == {management_vlan})
                if not valid_primary:
                    report("school-management-address", device, "Infrastructure management requires an active primary address on its own enabled management interface.")
                svi_managed = attrs(primary).get("type") == "virtual" and refs(device).get("role") in {"role/distribution", "role/management"}
                if not svi_managed:
                    peer = peers.get(primary)
                    manager = refs(peer).get("device")
                    if (not active_path(primary) or attrs(primary).get("name") not in management_ports or
                            manager not in roles["role/management"] or refs(manager).get("location") != room or
                            attrs(manager).get("status") != "active" or vlans(peer) != {management_vlan}):
                        report("school-management-path", device, "Dedicated management must reach an active local closet management switch on a connected management-only path.")
            if device in roles["role/management"]:
                upstreams = []
                for port in children[("device", device)]:
                    peer = peers.get(port)
                    parent = refs(peer).get("device")
                    if (kind(port) == "interface" and parent in roles["role/distribution"] and
                            active_path(port) and vlans(port) == vlans(peer) == {management_vlan} and
                            attrs(parent).get("status") == "active" and refs(parent).get("location") == mdf):
                        upstreams.append(parent)
                if len(upstreams) != 1:
                    report("school-management-uplink", device, "Each management switch requires its active management-only backbone to the MDF distribution pair.")
            if device not in roles["role/management"]:
                for specification in model.get("console_ports", []):
                    if specification.get("type") != "rj-45":
                        continue
                    port = f"{device}/console_port/{specification['name']}"
                    peer = peers.get(port)
                    server = refs(peer).get("device")
                    if (kind(peer) != "console_server_port" or not active_path(port) or
                            server not in roles["role/console-server"] or refs(server).get("location") != room or
                            attrs(server).get("status") != "active"):
                        report("school-console-path", device, "Primary equipment console requires a connected path to an active local closet console server.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of, poe_watts=poe_watts, optics_watts=optics_watts))

    counts = dict(schools=len(schools), staff=sum(s["classrooms"] + s["administrative_staff"] for s in schools),
                  enrollment=sum(s["classrooms"] * s["students_per_classroom"] for s in schools))
    counts["population"] = counts["staff"] + counts["enrollment"]
    workloads = []
    for key, metric, threshold, vcpus, memory, disk, port in SERVICE_POLICY:
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        workloads.append(dict(key=key, groups=max(1, (counts[metric] + threshold - 1) // threshold), replicas=2,
            failure_domain="rack", network="applications", vcpus=vcpus, memory_mb=memory, disk_mb=disk,
            listeners=listeners, criticality="tier-1" if key in {"identity", "dns", "learning-portal"} else "tier-2"))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=workloads,
                    peak=sum(s["wan_peak_mbps"] for s in schools), reserve=reserve, strict_sites=False, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
