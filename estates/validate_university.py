"""Independent university-campus demand and physical-path obligations.

Recipe policy, building geometry and service sizing are restated deliberately
instead of importing the university builder. The base validator supplies its
physical indexes; the shared DC checker inspects the independently sized campus
services. Emitted contracts can explain, never remove, an obligation.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_network
import math

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics


# Independent restatement of the profile's address, layout and service policy.
NETWORK_OFFSETS = {"management": 0, "staff": 1, "students": 2, "research": 3,
                   "wireless": 4, "security": 5, "wan": 9, "guest": 12}
DC_NETWORK_OFFSETS = {"management": 0, "applications": 6, "database": 7,
                      "backup": 8, "wan": 9, "storage": 10}
ACADEMIC_ROOMS_PER_FLOOR = 8
DORM_ROOMS_PER_FLOOR = 50
LAB_SEATS_PER_ROOM = 24
READING_SEATS_PER_ROOM = 24
OFFICE_DESKS = 12
MAX_FLOORS = 8
FLOOR_HEIGHT_M = 4
LECTURE_MANAGED, LECTURE_GUEST = 28, 4
LAB_MANAGED, OFFICE_MANAGED = 24, 12
RESIDENCE_PER_ROOM, RESIDENCE_GUEST = 2, 2
GUEST_CAPACITY = 1000
SERVICE_POLICY = (
    ("learning-portal", "applications", "endpoints", 2000, 4, 8192, 100000, 443),
    ("identity", "applications", "endpoints", 1500, 4, 8192, 100000, 443),
    ("dns", "applications", "sites", 16, 2, 4096, 40000, 53),
    ("research-storage", "database", "lab_seats", 120, 8, 32768, 2000000, 2049),
    ("monitoring", "applications", "endpoints", 4000, 4, 16384, 200000, 443),
    ("backup", "backup", "sites", 8, 4, 16384, 1000000, 443),
)
TIER_1 = {"learning-portal", "identity", "dns"}
BUILDING_BOUNDS = (("classrooms", 0, 40), ("lab_seats", 0, 200), ("offices", 0, 60))
RESIDENCE_BOUNDS = (("rooms", 10, 400), ("wired_ports_per_room", 0, 2))


def _radios(zone):
    return max(1, (zone["managed"] + zone["guest"] + 31) // 32)


def _floors(item, kind):
    if kind == "residence":
        return max(1, math.ceil(item["rooms"] / DORM_ROOMS_PER_FLOOR))
    if kind == "library":
        rooms = math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM)
    else:
        rooms = (item["classrooms"] + math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM)
                 + math.ceil(item["offices"] / OFFICE_DESKS))
    return max(1, math.ceil(rooms / ACADEMIC_ROOMS_PER_FLOOR))


def _peak(item, kind):
    if kind == "residence":
        return item["rooms"] + 100
    if kind == "library":
        return 2*item["reading_seats"] + 50
    return 4*item["classrooms"] + 2*item["lab_seats"] + 2*item["offices"] + 10


def _expected_zones(item, kind):
    """Authored zone budgets before any explicit recipe override."""
    if kind == "residence":
        return {f"floor-{n+1:02}": dict(
            managed=RESIDENCE_PER_ROOM * min(DORM_ROOMS_PER_FLOOR, item["rooms"] - DORM_ROOMS_PER_FLOOR*n),
            guest=RESIDENCE_GUEST) for n in range(_floors(item, kind))}
    result = {f"lecture-{n+1:03}": dict(managed=LECTURE_MANAGED, guest=LECTURE_GUEST)
              for n in range(item["classrooms"])}
    result.update({f"lab-{n+1:02}": dict(managed=LAB_MANAGED, guest=0)
                   for n in range(math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM))})
    result.update({f"office-{n+1:02}": dict(managed=OFFICE_MANAGED, guest=0)
                   for n in range(math.ceil(item["offices"] / OFFICE_DESKS))})
    return result


def _academic_spaces(item):
    """Ordered (suffix, space_type, seats, zone) rooms of one academic building."""
    rooms = [(f"lecture-{n+1:03}", "lecture_hall", 1, f"lecture-{n+1:03}")
             for n in range(item["classrooms"])]
    rooms += [(f"lab-{n+1:02}", "teaching_lab",
               min(LAB_SEATS_PER_ROOM, item["lab_seats"] - LAB_SEATS_PER_ROOM*n), f"lab-{n+1:02}")
              for n in range(math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM))]
    rooms += [(f"office-{n+1:02}", "office",
               min(OFFICE_DESKS, item["offices"] - OFFICE_DESKS*n), f"office-{n+1:02}")
              for n in range(math.ceil(item["offices"] / OFFICE_DESKS))]
    return rooms


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "university-campus":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    buildings, residences = recipe.get("buildings"), recipe.get("residences")
    library, reserve = recipe.get("library"), recipe.get("reserve_fraction")
    tiers, campus_wan = recipe.get("wan_tiers_mbps"), recipe.get("wan_peak_mbps")

    def keyed(entries, bounds, low, high):
        if not isinstance(entries, list) or not low <= len(entries) <= high:
            return None
        keys = set()
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or
                    entry["key"] in keys or
                    any(type(entry.get(field)) is not int or not lo <= entry[field] <= hi
                        for field, lo, hi in bounds)):
                return None
            keys.add(entry["key"])
        return sorted(entries, key=lambda entry: entry["key"])

    buildings = keyed(buildings, BUILDING_BOUNDS, 1, 16) if isinstance(buildings, list) else None
    residences = keyed(residences, RESIDENCE_BOUNDS, 0, 16) if isinstance(residences, list) else None
    if (buildings is None or residences is None or not isinstance(library, dict) or
            type(library.get("reading_seats")) is not int or not 24 <= library["reading_seats"] <= 300 or
            type(library.get("aps")) is not int or not 1 <= library["aps"] <= 16 or
            type(campus_wan) is not int or not 1 <= campus_wan <= 16000 or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("university-recipe", "plan", "University validation requires bounded academic, residence, "
                                            "library, campus WAN and reserve demand.")
        return findings
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("university-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    usable_fraction = Decimal(1) - Decimal(str(reserve))
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

    def kind_of(key):
        return objects.get(key, {}).get("kind")

    def vlans(port):
        return set(refs(port).get("tagged_vlans", [])) | ({refs(port)["untagged_vlan"]} if refs(port).get("untagged_vlan") else set())

    def active_path(port):
        members = component_members.get(component_of.get(port), [])
        return (bool(peers.get(port)) and bool(members) and
                all(attrs(p).get("enabled", True) for p in members) and
                all(attrs(cable_of[p]).get("status") == "connected" for p in members if p in cable_of))

    facilities = ([(f"bldg-{item['key']}", "academic", item) for item in buildings] +
                  [(f"hall-{item['key']}", "residence", item) for item in residences] +
                  [("library-01", "library", library)])
    expected_sites = {f"site/{sid}" for sid, _, _ in facilities} | {"site/dc-01"}
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != expected_sites:
        report("university-site-inventory", "plan", "Campus requires exactly its requested academic buildings, "
                                                    "residence halls, one library and one campus data center.")

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("university-address-allocation", "plan", "Every campus site needs a distinct /16 reservation inside the private pool.")
        return findings

    campus_peak = sum(_peak(item, kind) for _, kind, item in facilities)
    if campus_wan < campus_peak:
        report("university-campus-capacity", "plan", f"Campus edge purchase of {campus_wan} Mbps does not cover the "
                                                     f"{campus_peak} Mbps declared building peak.")
    port_names = catalog.get("access", {}).get("access_ports", [])
    capacity = int(Decimal(len(port_names)) * usable_fraction)

    for sid, facility, item in facilities:
        site = f"site/{sid}"
        academic, residence = facility == "academic", facility == "residence"
        if attrs(site).get("status") != "active":
            report("university-site-status", site, "Requested campus site must be active.")
        floors = _floors(item, facility)
        if floors > MAX_FLOORS:
            report("university-building-height", site, "Building exceeds the reviewed eight-floor campus layout.")
            continue
        network_offsets = {name: offset for name, offset in NETWORK_OFFSETS.items()
                           if academic or name != "research"}
        if not academic and any(key in objects for key in (f"vlan/{sid}/research", f"prefix/{sid}/research")):
            report("university-segment-unrequested", site, "Only an academic building carries the instructional "
                                                           "and research computing segment.")

        # --- wireless zone budgets, independently restated -------------------
        if facility == "library":
            rooms_for_aps = math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM)
            if item["aps"] > 1 + 4*rooms_for_aps:
                report("university-wireless-demand", site, "Library radios exceed its entrance mount plus four "
                                                           "mounts in each reading room.")
            zone_budgets = {}
        else:
            zone_budgets = item.get("wireless")
            defaults = _expected_zones(item, facility)
            if (not isinstance(zone_budgets, dict) or set(zone_budgets) != set(defaults) or
                    any(not isinstance(v, dict) or set(v) != {"managed", "guest"} or
                        any(type(n) is not int or not 0 <= n <= 128 for n in v.values()) or
                        sum(v.values()) > 128 for v in zone_budgets.values()) or
                    sum(v["guest"] for v in zone_budgets.values()) > GUEST_CAPACITY):
                report("university-wireless-demand", site, "Frozen wireless demand needs exactly this building's real "
                                                          "zones with bounded managed/guest budgets inside its guest /22.")
                zone_budgets = defaults

        # --- authored room geometry from the permanent room ledger ------------
        if residence:
            spaces = [(f"room-{n+1:03}", "dorm_room", item["wired_ports_per_room"], None)
                      for n in range(item["rooms"])]
        elif academic:
            spaces = _academic_spaces(item)
        else:
            spaces = [(f"reading-{n+1:02}", "reading_room",
                       min(READING_SEATS_PER_ROOM, item["reading_seats"] - READING_SEATS_PER_ROOM*n), None)
                      for n in range(math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM))]
        per_floor = DORM_ROOMS_PER_FLOOR if residence else ACADEMIC_ROOMS_PER_FLOOR
        ledger = plan.get("reservations", {}).get(f"campus-rooms/{sid}")
        if (not isinstance(ledger, dict) or set(ledger) != {suffix for suffix, _, _, _ in spaces} or
                sorted(ledger.values()) != list(range(len(spaces)))):
            report("university-room-allocation", site, "Every requested room needs one unique permanent building "
                                                        "position; growth appends positions and never renumbers.")
            continue
        floors = max(ledger.values()) // per_floor + 1
        rooms, wired_rooms = {}, []
        for suffix, space_type, seats, zone in spaces:
            slot = ledger[suffix]
            floor, place = slot // per_floor + 1, slot % per_floor
            origin = ([2 + 2*(place % 25), 2 + 10*(place//25), (floor-1)*FLOOR_HEIGHT_M] if residence else
                      [6 + 12*(place % 4), 4 + 24*(place//4), (floor-1)*FLOOR_HEIGHT_M])
            key = f"location/{sid}/{suffix}"
            rooms[key] = (space_type, origin, floor)
            wired_rooms.append((key, seats, zone))
        corridors = {}
        for floor in range(1, floors + 1):
            key = f"location/{sid}/corridor-{floor:02}"
            origin = [24, 8 if residence else 40, (floor-1)*FLOOR_HEIGHT_M]
            rooms[key] = ("corridor", origin, floor)
            corridors[floor] = key
        reception = None
        if facility == "library":
            reception = f"location/{sid}/reception"
            rooms[reception] = ("reception", [6, 40, 0], 1)
        serving = {1: f"location/{sid}"}
        for floor in range(2, floors + 1):
            serving[floor] = f"location/{sid}/idf-{floor:02}"

        room_types = {"lecture_hall", "teaching_lab", "office", "dorm_room", "reading_room",
                      "corridor", "reception"}
        actual_rooms = {key for key in children[("site", site)] if kind_of(key) == "location" and
                        meta(key).get("space_type") in room_types}
        if actual_rooms != set(rooms):
            report("university-room-inventory", site, "Teaching, residential, reading, corridor and entrance rooms "
                                                      "must match the authored building layout exactly.")
        for room, (space_type, origin, floor) in rooms.items():
            if (kind_of(room) != "location" or meta(room).get("space_type") != space_type or
                    meta(room).get("floor") != floor or meta(room).get("position_m") != origin or
                    refs(room).get("parent") != f"location/{sid}/floor-{floor:02}" or
                    attrs(room).get("status") != "active"):
                report("university-room-placement", room, "Campus rooms must occupy their fixed active floor positions.")
        for floor, closet in serving.items():
            if (kind_of(closet) != "location" or meta(closet).get("space_type") != "equipment_room" or
                    meta(closet).get("floor") != floor or
                    meta(closet).get("position_m") != [24, 18, (floor-1)*FLOOR_HEIGHT_M]):
                report("university-closet-inventory", site, f"Floor {floor} needs its permanent equipment room.")

        # --- authored endpoint inventory --------------------------------------
        expected = {}

        def endpoint(label, role, network, room, ordinal):
            expected[f"device/{sid}/{label}"] = (role, network, room, ordinal)

        def radios(zone, label, room):
            for ordinal in range(1, _radios(zone_budgets[zone]) + 1):
                endpoint(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, ordinal)

        if academic:
            for index, (room, seats, zone) in enumerate(wired_rooms):
                suffix = room.rsplit("/", 1)[-1]
                if suffix.startswith("lecture-"):
                    endpoint(f"instructor-{suffix.removeprefix('lecture-')}", "workstation", "staff", room, 1)
                    radios(zone, f"ap-{suffix}", room)
                elif suffix.startswith("lab-"):
                    for seat in range(1, seats + 1):
                        endpoint(f"{suffix}-{seat:02}", "workstation", "research", room, seat)
                    radios(zone, f"ap-{suffix}", room)
                else:
                    for desk in range(1, seats + 1):
                        endpoint(f"{suffix}-{desk:02}", "workstation", "staff", room, desk)
                    radios(zone, f"ap-{suffix}", room)
        elif residence:
            for index, (room, ports, _) in enumerate(wired_rooms, 1):
                for port in range(1, ports + 1):
                    endpoint(f"room-{index:03}-{port:02}", "workstation", "students", room, port)
            for floor in range(1, floors + 1):
                radios(f"floor-{floor:02}", f"ap-floor-{floor:02}", corridors[floor])
        else:
            for index, (room, seats, _) in enumerate(wired_rooms, 1):
                for seat in range(1, seats + 1):
                    endpoint(f"study-{index:02}-{seat:02}", "workstation", "students", room, seat)
            reading_rooms = [room for room, _, _ in wired_rooms]
            endpoint("ap-library-01", "ap", "wireless", reception, 1)
            for index in range(item["aps"] - 1):
                room = reading_rooms[index % len(reading_rooms)] if reading_rooms else reception
                endpoint(f"ap-library-{index+2:02}", "ap", "wireless", room, index // max(1, len(reading_rooms)) + 1)
        for floor in range(1, floors + 1):
            for ordinal, side in enumerate(("a", "b"), 1):
                endpoint(f"camera-floor-{floor:02}-{side}", "camera", "security", corridors[floor], ordinal)

        devices = [key for key in children[("site", site)] if kind_of(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in
                  {"role/workstation", "role/ap", "role/camera"}}
        if actual != set(expected):
            report("university-endpoint-inventory", site, "Every requested workstation, lab seat, room data port, "
                                                          "study position, radio and camera must match authored demand.")
        counted = Counter(role for role, _, _, _ in expected.values())
        for role, wanted in counted.items():
            if len(roles[f"role/{role}"]) != wanted:
                report("university-endpoint-demand", site, f"Building demand requires {wanted} {role} devices; found {len(roles[f'role/{role}'])}.")
        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site
                     and c.get("kind") == facility] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("university-contract-inventory", site, "Each campus building needs exactly one explanatory contract.")
        else:
            wanted = dict(workstations=counted["workstation"], aps=counted["ap"], cameras=counted["camera"],
                          floors=floors, peak_mbps=_peak(item, facility))
            if any(contracts[0].get("demand", {}).get(field) != value for field, value in wanted.items()):
                report("university-demand-report", site, "Reported building demand must follow the authored room, "
                                                         "radio, camera, floor and peak policy.")

        switch_counts, room_endpoints = Counter(), defaultdict(set)
        for device, (role, network, room, ordinal) in expected.items():
            floor = rooms[room][2]
            closet = serving[floor]
            room_endpoints[closet].add(device)
            if (kind_of(device) != "device" or attrs(device).get("status") != "active" or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    not meta(device).get("endpoint") or meta(device).get("network") != network):
                report("university-endpoint-placement", device, "Required endpoint identity, active status, segment and room must match the campus layout.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind_of(peer) != "interface" or refs(switch).get("role") != "role/access" or
                    attrs(switch).get("status") != "active" or refs(switch).get("location") != closet or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("university-endpoint-path", device, "Endpoint needs its own connected enabled VLAN path to an "
                                                           "active access switch in its own floor equipment room.")
            if role == "ap" and attrs(peer).get("name") not in port_names:
                # PoE reachability is the actual copper path into a catalog
                # access port; the shared PoE analyser checks the supply budget.
                report("university-ap-power", device, "Coverage radio must reach a catalog PoE access port on its serving switch.")
            if switch:
                switch_counts[switch] += 1
            if role == "ap":
                row, lane = divmod(ordinal - 1, 2)
                offset, height = (4 + 4*lane, 4 + 4*row), 2.8
            elif role == "camera":
                offset, height = (1 + 8*(ordinal-1), 0), 2.5
            else:
                offset, height = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6)), 0.8
            origin = rooms[room][1]
            mount = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
            route = math.ceil(sum(abs(a-b) for a, b in zip(mount, [24, 18, (floor-1)*FLOOR_HEIGHT_M]))+10)
            position = meta(device).get("placement", {})
            if (position.get("room") != room or position.get("floor") != floor or
                    position.get("cable_origin") != closet or position.get("position_m") != mount or
                    path_lengths.get(port) != route or not 0 < route <= 80):
                report("university-endpoint-route", device, "Each endpoint ordinal must retain its fixed mount and "
                                                            "actual copper route inside the 80 m ceiling.")
            address = refs(device).get("primary_ip4")
            if (kind_of(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or refs(address).get("vrf") != f"vrf/{network}"):
                report("university-endpoint-address", device, "Installed endpoint requires its active primary address on eth0 in the intended segment VRF.")

        # --- room-local access pairs ------------------------------------------
        expected_switches = set()
        for floor, closet in serving.items():
            prefix = "" if floor == 1 else f"{closet.rsplit('/', 1)[-1]}-"
            scope = f"access-endpoints/{sid}/{closet}"
            slots = plan.get("reservations", {}).get(scope)
            if (not capacity or not isinstance(slots, dict) or set(slots) != room_endpoints[closet] or
                    any(type(n) is not int or not 0 <= n < 38*capacity for n in slots.values()) or
                    len(set(slots.values())) != len(slots)):
                report("university-access-allocation", closet, "Each floor endpoint needs one unique bounded permanent access-port slot.")
                continue
            count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity)))
            expected_switches |= {f"device/{sid}/{prefix}access-{n+1:02}" for n in range(count)}
            for device, slot in slots.items():
                pair, offset = divmod(slot, 2*capacity)
                switch = f"device/{sid}/{prefix}access-{2*pair+offset % 2+1:02}"
                if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port_names[offset//2]}":
                    report("university-access-allocation", device, "Actual endpoint path must match its reserved switch and catalog copper port.")
        if expected_switches and set(roles["role/access"]) != expected_switches:
            report("university-access-inventory", site, "Every floor equipment room needs complete access pairs through "
                                                        "its last permanent slot, retaining copper and PoE headroom.")
        if len(roles["role/access"]) > 38:
            report("university-access-capacity", site, "Access-switch attachments exceed the finite distribution port budget.")
        for device in roles["role/access"]:
            if meta(device).get("hardware") != "access" or switch_counts[device] > capacity:
                report("university-access-capacity", device, "Actual attached endpoints must fit this catalog access switch after reserve.")

        # --- forwarding, gateways and addressing -------------------------------
        required_vlans = {f"vlan/{sid}/{name}" for name in network_offsets if name != "wan"}
        if len(roles["role/distribution"]) != 2 or len(roles["role/wan-edge"]) != 2:
            report("university-core-inventory", site, "Campus building requires two distribution switches and two carrier edge devices.")
        for role in ("role/access", "role/wan-edge"):
            for device in roles[role]:
                upstreams = set()
                for port in children[("device", device)]:
                    peer = peers.get(port)
                    parent = refs(peer).get("device")
                    if (kind_of(port) == "interface" and kind_of(peer) == "interface" and
                            parent in roles["role/distribution"] and active_path(port) and
                            all(attrs(node).get("status") == "active" for node in (device, parent)) and
                            required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                        upstreams.add(parent)
                if len(upstreams) != 2:
                    report("university-uplink-path", device, "Access and carrier edges require two connected active "
                                                              "distribution uplinks carrying every building segment.")
        for network in network_offsets:
            if network == "wan":
                continue
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for device in roles["role/distribution"]:
                for port in children[("device", device)]:
                    if (kind_of(port) == "interface" and attrs(port).get("type") == "virtual" and
                            attrs(port).get("enabled", True) and attrs(device).get("status") == "active" and
                            vlan in vlans(port) and
                            any(kind_of(ip) == "ip_address" and attrs(ip).get("status") == "active"
                                for ip in children[("assigned_object", port)])):
                        gateways.add(device)
            if len(gateways) != 2:
                report("university-gateway-inventory", vlan, "Every campus segment needs two active addressed distribution gateway SVIs.")
        if sid not in allocations:
            report("university-address-allocation", site, "Requested campus site has no persistent reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid]*65536, 16))
            for network, offset in network_offsets.items():
                prefix = f"prefix/{sid}/{network}"
                wanted = ip_network((int(container.network_address) + offset*1024, 22))
                if (attrs(prefix).get("prefix") != str(wanted) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or refs(prefix).get("vrf") != f"vrf/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(offset+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site):
                    report("university-prefix-policy", prefix, "Building role /22 must retain its fixed offset, site VLAN and VRF inside the reserved /16.")

        # --- carrier attachments ------------------------------------------------
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind_of(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("university-wan-inventory", site, "Campus carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers if rate >= (50 if side == "a" else 100) and
                                Decimal(rate)*usable_fraction >= _peak(item, facility)), None)
            if len(circuits[provider]) != 1:
                report("university-wan-inventory", site, "Each campus building requires exactly one independent attachment to each modeled carrier.")
            for term in circuits[provider]:
                circuit = refs(term).get("circuit")
                peer = peers.get(term)
                edge = refs(peer).get("device")
                carrier_edges.add(edge)
                terms = [key for key in children[("circuit", circuit)] if kind_of(key) == "circuit_termination"]
                remote = [key for key in terms if kind_of(refs(key).get("termination")) == "provider_network"]
                if (wanted_rate is None or attrs(circuit).get("commit_rate") != wanted_rate*1000 or
                        attrs(circuit).get("status") != "active" or not active_path(term) or
                        edge not in roles["role/wan-edge"] or attrs(edge).get("status") != "active" or
                        attrs(peer).get("type") != "1000base-t" or attrs(term).get("port_speed") != 1000000 or
                        len(terms) != 2 or len(remote) != 1 or
                        refs(refs(remote[0]).get("termination")).get("provider") != provider or
                        attrs(remote[0]).get("term_side") != "Z" or attrs(term).get("term_side") != "A"):
                    report("university-wan-capacity", circuit, "Campus commitment and active 1 Gbps edge handoff must cover the building's peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("university-wan-diversity", site, "The two campus carriers must terminate on different active edge devices.")

        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in {"role/distribution", "role/wan-edge", "role/access",
                                                "role/management", "role/console-server", "role/pdu",
                                                "role/patch-panel"}:
                report("university-equipment-role", device, "Campus infrastructure must serve an explicit network, "
                                                             "management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind_of(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room not in set(serving.values()) or attrs(rack).get("status") != "active"):
                report("university-equipment-placement", device, "Campus infrastructure must occupy an active rack in a floor equipment room.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of,
                                       poe_watts=poe_watts, optics_watts=optics_watts))

    totals = dict(sites=len(facilities),
                  lab_seats=sum(item["lab_seats"] for item in buildings))
    endpoints = 0
    for sid, facility, item in facilities:
        building_floors = _floors(item, facility)
        if facility == "residence":
            wired = item["rooms"] * item["wired_ports_per_room"]
            radios = sum(_radios(zone) for zone in _expected_zones(item, facility).values())
        elif facility == "library":
            wired, radios = item["reading_seats"], item["aps"]
        else:
            wired = item["classrooms"] + item["lab_seats"] + item["offices"]
            radios = sum(_radios(zone) for zone in _expected_zones(item, facility).values())
        endpoints += wired + radios + 2*building_floors
    totals["endpoints"] = endpoints
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
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=services,
                    peak=campus_wan, reserve=reserve, strict_sites=False,
                    network_offsets=DC_NETWORK_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
