"""Independent manufacturing demand, path and IT/OT zone obligations.

Recipe policy, plant geometry, endpoint density and service sizing are restated
deliberately instead of importing the manufacturing builder. The base validator
supplies its physical indexes; the shared DC checker inspects the independently
sized corporate services. Emitted contracts can explain, never remove, an
obligation.

The zone checks are the point of this profile. The plant-floor (OT) segments
`process` and `supervisory` must exist on exactly the plant-floor access pair,
its own distribution pair and its own endpoints, and nowhere else: not on a
corporate access switch, not on a carrier edge, not on a cable that reaches
one. That membership is read estate-wide from every interface's actual VLAN
references, so a port anywhere naming a plant-floor VLAN widens the zone and is
reported. The `conduit` segment must be the only modeled trunk between the two
distribution tiers, and the only non-OT VLANs an OT device may carry are that
conduit on the distribution pair and the plant management segment on a device's
own dedicated management port.

Nothing here asserts enforcement. A modeled boundary is inventory: no firewall
policy, access control list, route filter, air gap, Purdue level or IEC 62443
state is checked, claimed or claimable from these findings.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_network
import math
import re

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics
from .model import selected_alias


# Independent restatement of the profile's address, layout and service policy.
IT_NETWORKS = ("management", "office", "logistics", "wireless", "security")
OT_NETWORKS = ("process", "supervisory")
CONDUIT = "conduit"
NETWORK_OFFSETS = {"management": 0, "office": 1, "process": 2, "supervisory": 3, "wireless": 4,
                   "security": 5, "wan": 9, "logistics": 11, "conduit": 12}
DC_NETWORK_OFFSETS = {"management": 0, "applications": 6, "database": 7,
                      "backup": 8, "wan": 9, "storage": 10}
OFFICE_DESKS = 12
MAX_PLANTS, MAX_LINES, MAX_DOCKS, MAX_PODS = 8, 12, 12, 8
MIN_LINES, MIN_DOCKS = 1, 0
MIN_STAFF, MAX_STAFF = 4, MAX_PODS * OFFICE_DESKS
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
PLANT_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
LINE_CONTROLLERS, LINE_PANELS, LINE_FIELD_DROPS = 1, 2, 4
LINE_ENDPOINTS = LINE_CONTROLLERS + LINE_PANELS + LINE_FIELD_DROPS
DOCK_SCANNERS, DOCKS_PER_RADIO = 2, 2
PLANT_BASE_MBPS, MBPS_PER_DESK, MBPS_PER_LINE, MBPS_PER_DOCK = 20, 2, 5, 3
MAX_ACCESS_SWITCHES = 38
CLOSET_ORIGIN = [24, 18, 0]
RECEPTION_ORIGIN = [48, 10, 0]
SERVICE_POLICY = (
    ("mes", "applications", "lines", 24, 4, 16384, 200000, 443),
    ("historian", "database", "ot_endpoints", 480, 8, 32768, 1000000, 5432),
    ("erp-gateway", "applications", "plants", 8, 4, 8192, 100000, 443),
    ("identity", "applications", "endpoints", 2000, 4, 8192, 100000, 443),
    ("dns", "applications", "sites", 16, 2, 4096, 40000, 53),
    ("monitoring", "applications", "endpoints", 3000, 4, 16384, 200000, 443),
    ("backup", "backup", "plants", 6, 4, 16384, 1000000, 443),
)
TIER_1 = {"mes", "historian", "erp-gateway", "identity", "dns"}
INFRASTRUCTURE_ROLES = {"role/distribution", "role/wan-edge", "role/access", "role/management",
                        "role/console-server", "role/pdu", "role/patch-panel"}


def _pods(staff):
    """Authored office-pod layout: ordered (suffix, installed desk positions)."""
    return [(f"pod-{n+1:02}", min(OFFICE_DESKS, staff - OFFICE_DESKS*n))
            for n in range(math.ceil(staff / OFFICE_DESKS))]


def _radios(item):
    return 1 + len(_pods(item["office_staff"])) + math.ceil(item["warehouse_docks"] / DOCKS_PER_RADIO)


def _cameras(item):
    return 1 + item["warehouse_docks"]


def _ot_endpoints(item):
    return LINE_ENDPOINTS * item["production_lines"]


def _it_endpoints(item):
    return (item["office_staff"] + DOCK_SCANNERS * item["warehouse_docks"]
            + _radios(item) + _cameras(item))


def _peak(item):
    return (PLANT_BASE_MBPS + MBPS_PER_DESK * item["office_staff"]
            + MBPS_PER_LINE * item["production_lines"] + MBPS_PER_DOCK * item["warehouse_docks"])


def _mount(role, ordinal, origin):
    if role == "ap":
        row, lane = divmod(ordinal - 1, 2)
        offset, height = (4 + 4*lane, 4 + 4*row), 2.8
    elif role == "camera":
        offset, height = (1 + 8*(ordinal-1), 0), 2.5
    else:
        offset, height = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6)), 0.8
    return [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "manufacturing":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    reserve, tiers = recipe.get("reserve_fraction"), recipe.get("wan_tiers_mbps")
    plants = recipe.get("plants")
    if (not isinstance(plants, list) or not 1 <= len(plants) <= MAX_PLANTS or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("mfg-recipe", "plan", "Manufacturing validation requires 1–8 bounded plants and a bounded reserve.")
        return findings
    keys, codes = set(), set()
    for entry in plants:
        key = entry.get("key") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or not isinstance(key, str) or key in keys or
                not re.fullmatch(PLANT_KEY, key) or key.replace("-", "") in codes or
                type(entry.get("production_lines")) is not int or
                not MIN_LINES <= entry["production_lines"] <= MAX_LINES or
                type(entry.get("warehouse_docks")) is not int or
                not MIN_DOCKS <= entry["warehouse_docks"] <= MAX_DOCKS or
                type(entry.get("office_staff")) is not int or
                not MIN_STAFF <= entry["office_staff"] <= MAX_STAFF):
            report("mfg-recipe", "plan", "Each plant needs a unique hyphen-separated lowercase key of at most 20 "
                                         "characters, also distinct with hyphens removed, 1–12 production lines, "
                                         "0–12 warehouse docks and 4–96 installed office desks.")
            return findings
        keys.add(key)
        codes.add(key.replace("-", ""))
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("mfg-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    plants = sorted(plants, key=lambda entry: entry["key"])
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
        carried = set(refs(port).get("tagged_vlans", []))
        if refs(port).get("untagged_vlan"):
            carried.add(refs(port)["untagged_vlan"])
        return carried

    def active_path(port):
        members = component_members.get(component_of.get(port), [])
        return (bool(peers.get(port)) and bool(members) and
                all(attrs(p).get("enabled", True) for p in members) and
                all(attrs(cable_of[p]).get("status") == "connected" for p in members if p in cable_of))

    premises = [(f"pl-{item['key']}", item) for item in plants]
    expected_sites = {f"site/{sid}" for sid, _ in premises} | {"site/dc-01", "site/dc-02"}
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != expected_sites:
        report("mfg-site-inventory", "plan", "The estate requires exactly one site per requested plant and "
                                             "the two corporate data centers.")

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("mfg-address-allocation", "plan", "Every manufacturing site needs a distinct /16 reservation "
                                                 "inside the private pool.")
        return findings

    access_alias = selected_alias(recipe, "access")
    port_names = catalog.get(access_alias, {}).get("access_ports", [])
    capacity = int(Decimal(len(port_names)) * usable_fraction)
    # Per plant: the devices its zone covers, and the exact device set allowed to
    # name each zone-bearing VLAN. Both are used after the loop for the estate-
    # wide reference sweep that no per-plant check can see.
    ot_zones, vlan_zones = {}, {}

    for sid, item in premises:
        site = f"site/{sid}"
        closet = f"location/{sid}"
        if attrs(site).get("status") != "active":
            report("mfg-site-status", site, "Requested plant must be active.")

        # --- authored ground-floor geometry from the permanent room ledgers ----
        pods = _pods(item["office_staff"])
        ledgers, rooms, pod_rooms, dock_rooms, line_rooms = {}, {}, [], [], []
        broken = False
        for scope, names, origin, bucket, space_type, ceiling in (
                ("plant-pods", [suffix for suffix, _ in pods],
                 lambda slot: [6 + 10*(slot % 4), 2 + 8*(slot // 4), 0], pod_rooms, "office", MAX_PODS),
                ("plant-docks", [f"dock-{n+1:02}" for n in range(item["warehouse_docks"])],
                 lambda slot: [32 + 6*(slot % 4), 28 + 6*(slot // 4), 0], dock_rooms, "loading_dock", MAX_DOCKS),
                ("plant-lines", [f"line-{n+1:02}" for n in range(item["production_lines"])],
                 lambda slot: [6 + 6*(slot % 4), 28 + 6*(slot // 4), 0], line_rooms, "production_line", MAX_LINES)):
            ledger = plan.get("reservations", {}).get(f"{scope}/{sid}", {} if not names else None)
            if (not isinstance(ledger, dict) or set(ledger) != set(names) or
                    any(type(slot) is not int or not 0 <= slot < ceiling for slot in ledger.values()) or
                    sorted(ledger.values()) != list(range(len(names)))):
                report("mfg-room-allocation", site, f"Every {space_type.replace('_', ' ')} room needs one unique "
                                                    "permanent ground-floor position; growth appends positions "
                                                    "and never renumbers.")
                broken = True
                continue
            ledgers[scope] = ledger
            for name in names:
                key = f"location/{sid}/{name}"
                rooms[key] = (space_type, origin(ledger[name]))
                bucket.append(key)
        if broken:
            continue
        rooms[f"location/{sid}/reception"] = ("reception", list(RECEPTION_ORIGIN))
        reception = f"location/{sid}/reception"
        capacities = {f"location/{sid}/{suffix}": desks for suffix, desks in pods}
        actual_rooms = {key for key in children[("site", site)] if kind_of(key) == "location" and
                        meta(key).get("space_type") in {"reception", "office", "loading_dock", "production_line"}}
        if actual_rooms != set(rooms):
            report("mfg-room-inventory", site, "Reception, office pods, loading docks and production line cells "
                                                "must match the authored single-floor plant layout exactly.")
        for room, (space_type, origin) in rooms.items():
            if (kind_of(room) != "location" or meta(room).get("space_type") != space_type or
                    meta(room).get("floor") != 1 or meta(room).get("position_m") != origin or
                    refs(room).get("parent") != f"location/{sid}/floor-01" or
                    attrs(room).get("status") != "active" or
                    (room in capacities and meta(room).get("capacity", {}).get("workstations") != capacities[room])):
                report("mfg-room-placement", room, "Plant rooms must occupy their fixed active ground-floor "
                                                    "positions with their installed desk capacity.")
        if (kind_of(closet) != "location" or meta(closet).get("space_type") != "equipment_room" or
                meta(closet).get("floor") != 1 or meta(closet).get("position_m") != CLOSET_ORIGIN):
            report("mfg-closet-inventory", site, "Each plant needs its single permanent ground-floor equipment room.")

        # --- authored endpoint inventory, separated by zone --------------------
        corporate_expected, process_expected = {}, {}

        def endpoint(bucket, label, role, network, room, ordinal):
            bucket[f"device/{sid}/{label}"] = (role, network, room, ordinal)

        for index in range(item["office_staff"]):
            pod, desk = divmod(index, OFFICE_DESKS)
            endpoint(corporate_expected, f"desk-{index+1:03}", "workstation", "office", pod_rooms[pod], desk + 1)
        endpoint(corporate_expected, "ap-reception", "ap", "wireless", reception, 1)
        endpoint(corporate_expected, "cam-reception", "camera", "security", reception, 1)
        for index, room in enumerate(pod_rooms):
            endpoint(corporate_expected, f"ap-{pods[index][0]}", "ap", "wireless", room, 1)
        for index, room in enumerate(dock_rooms):
            for number in range(DOCK_SCANNERS):
                endpoint(corporate_expected, f"scan-{DOCK_SCANNERS*index+number+1:03}", "scanner",
                         "logistics", room, number + 1)
            endpoint(corporate_expected, f"cam-dock-{index+1:02}", "camera", "security", room, 1)
            if index % DOCKS_PER_RADIO == 0:
                endpoint(corporate_expected, f"ap-dock-{index+1:02}", "ap", "wireless", room, 1)
        for index, room in enumerate(line_rooms):
            endpoint(process_expected, f"plc-{index+1:02}", "plc", "process", room, 1)
            for number in range(LINE_PANELS):
                endpoint(process_expected, f"hmi-{index+1:02}-{number+1}", "hmi", "supervisory", room,
                         LINE_CONTROLLERS + number + 1)
            for number in range(LINE_FIELD_DROPS):
                endpoint(process_expected, f"field-{index+1:02}-{number+1}", "field-device", "process", room,
                         LINE_CONTROLLERS + LINE_PANELS + number + 1)
        expected = corporate_expected | process_expected

        devices = [key for key in children[("site", site)] if kind_of(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        endpoint_roles = {"role/workstation", "role/scanner", "role/ap", "role/camera",
                          "role/plc", "role/hmi", "role/field-device"}
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in endpoint_roles}
        if actual != set(expected):
            report("mfg-endpoint-inventory", site, "Every requested desk, scanner station, coverage radio, camera, "
                                                    "line controller, operator panel and field device must match "
                                                    "the authored plant demand.")
        counted = Counter(role for role, _, _, _ in expected.values())
        for role, wanted in counted.items():
            if len(roles[f"role/{role}"]) != wanted:
                report("mfg-endpoint-demand", site, f"Plant demand requires {wanted} {role} devices; "
                                                     f"found {len(roles[f'role/{role}'])}.")

        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site
                     and c.get("kind") == "plant"] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("mfg-contract-inventory", site, "Each plant needs exactly one explanatory contract.")
        else:
            wanted = dict(workstations=item["office_staff"], aps=_radios(item), cameras=_cameras(item),
                          production_lines=item["production_lines"], warehouse_docks=item["warehouse_docks"],
                          office_pods=len(pods), line_controllers=LINE_CONTROLLERS*item["production_lines"],
                          operator_panels=LINE_PANELS*item["production_lines"],
                          field_devices=LINE_FIELD_DROPS*item["production_lines"],
                          scanner_stations=DOCK_SCANNERS*item["warehouse_docks"], peak_mbps=_peak(item))
            if (any(contracts[0].get("demand", {}).get(field) != value for field, value in wanted.items()) or
                    contracts[0].get("plant") != item["key"]):
                report("mfg-demand-report", site, "Reported plant demand must follow the authored pod, dock, line, "
                                                   "radio, camera and peak policy and name its actual plant.")

        # --- the two access zones, their ledgers and their own switch pairs ----
        zones = {}
        for label, population, prefix, scope_suffix, networks in (
                ("corporate", corporate_expected, "access-", "", IT_NETWORKS),
                ("plant-floor", process_expected, "ot-access-", "/ot", OT_NETWORKS)):
            scope = f"access-endpoints/{sid}/{closet}{scope_suffix}"
            slots = plan.get("reservations", {}).get(scope)
            switches = set()
            if (not capacity or not isinstance(slots, dict) or set(slots) != set(population) or
                    any(type(n) is not int or not 0 <= n < MAX_ACCESS_SWITCHES*capacity for n in slots.values()) or
                    len(set(slots.values())) != len(slots)):
                report("mfg-access-allocation", site, f"Each {label} endpoint needs one unique bounded permanent "
                                                      "access-port slot in its own zone ledger.")
            else:
                count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity))) if slots else 0
                switches = {f"device/{sid}/{prefix}{n+1:02}" for n in range(count)}
                for device, slot in slots.items():
                    pair, offset = divmod(slot, 2*capacity)
                    switch = f"device/{sid}/{prefix}{2*pair+offset % 2+1:02}"
                    if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port_names[offset//2]}":
                        report("mfg-access-allocation", device, "Actual endpoint path must match its reserved "
                                                                 "switch and catalog copper port.")
            zones[label] = dict(population=population, switches=switches, networks=networks)
        corporate_switches = zones["corporate"]["switches"]
        process_switches = zones["plant-floor"]["switches"]
        if set(roles["role/access"]) != corporate_switches | process_switches:
            report("mfg-access-inventory", site, "The equipment room needs complete corporate and plant-floor "
                                                  "access pairs through each zone's last permanent slot, retaining "
                                                  "copper and PoE headroom.")
        if len(roles["role/access"]) > MAX_ACCESS_SWITCHES:
            report("mfg-access-capacity", site, "Both zones share one finite distribution attachment budget; "
                                                 "their access switches exceed it.")

        switch_counts = Counter()
        for device, (role, network, room, ordinal) in expected.items():
            zone = "plant-floor" if device in process_expected else "corporate"
            if (kind_of(device) != "device" or attrs(device).get("status") != "active" or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    not meta(device).get("endpoint") or meta(device).get("network") != network):
                report("mfg-endpoint-placement", device, "Required endpoint identity, active status, segment and "
                                                          "room must match the authored plant layout.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind_of(peer) != "interface" or
                    refs(switch).get("role") != "role/access" or attrs(switch).get("status") != "active" or
                    refs(switch).get("location") != closet or switch not in zones[zone]["switches"] or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("mfg-endpoint-path", device, f"Endpoint needs its own connected enabled VLAN path to an "
                                                     f"active {zone} access switch in the plant equipment room.")
            if role == "ap" and attrs(peer).get("name") not in port_names:
                # PoE reachability is the actual copper path into a catalog
                # access port; the shared PoE analyser checks the supply budget.
                report("mfg-ap-power", device, "Coverage radio must reach a catalog PoE access port on its serving switch.")
            if switch:
                switch_counts[switch] += 1
            mount = _mount(role, ordinal, rooms[room][1])
            route = math.ceil(sum(abs(a-b) for a, b in zip(mount, CLOSET_ORIGIN))+10)
            position = meta(device).get("placement", {})
            if (position.get("room") != room or position.get("floor") != 1 or
                    position.get("cable_origin") != closet or position.get("position_m") != mount or
                    path_lengths.get(port) != route or not 0 < route <= 80):
                report("mfg-endpoint-route", device, "Each endpoint ordinal must retain its fixed mount and actual "
                                                      "copper route inside the 80 m ceiling.")
            address = refs(device).get("primary_ip4")
            if (kind_of(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or refs(address).get("vrf") != f"vrf/{network}"):
                report("mfg-endpoint-address", device, "Installed endpoint requires its active primary address on "
                                                        "eth0 in its own segment routing context.")
        for device in roles["role/access"]:
            if meta(device).get("hardware") != access_alias or switch_counts[device] > capacity:
                report("mfg-access-capacity", device, "Actual attached endpoints must fit this catalog access "
                                                       "switch after reserve.")

        # --- the two distribution tiers ---------------------------------------
        corporate_dist = {f"device/{sid}/dist-{side}" for side in ("a", "b")}
        process_dist = {f"device/{sid}/ot-dist-{side}" for side in ("a", "b")}
        if (set(roles["role/distribution"]) != corporate_dist | process_dist or
                len(roles["role/wan-edge"]) != 2):
            report("mfg-core-inventory", site, "A plant requires a corporate distribution pair, a separate "
                                                "plant-floor distribution pair and two carrier edge devices.")
        for label, members, parents, networks in (
                ("corporate", set(roles["role/wan-edge"]) | corporate_switches, corporate_dist, IT_NETWORKS),
                ("plant-floor", process_switches, process_dist, OT_NETWORKS)):
            required = {f"vlan/{sid}/{name}" for name in networks}
            for device in sorted(members):
                upstreams = set()
                for port in children[("device", device)]:
                    peer = peers.get(port)
                    parent = refs(peer).get("device")
                    if (kind_of(port) == "interface" and kind_of(peer) == "interface" and
                            parent in parents and active_path(port) and
                            all(attrs(node).get("status") == "active" for node in (device, parent)) and
                            required <= vlans(port) and required <= vlans(peer)):
                        upstreams.add(parent)
                if len(upstreams) != 2:
                    report("mfg-uplink-path", device, f"Every {label} switch or carrier edge requires two connected "
                                                       f"active {label} distribution uplinks carrying that zone's "
                                                       "segments.")
        for network, tier in ([(name, corporate_dist) for name in IT_NETWORKS] +
                              [(name, process_dist) for name in OT_NETWORKS] +
                              [(CONDUIT, corporate_dist | process_dist)]):
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for device in sorted(corporate_dist | process_dist):
                for port in children[("device", device)]:
                    if (kind_of(port) == "interface" and attrs(port).get("type") == "virtual" and
                            attrs(port).get("enabled", True) and attrs(device).get("status") == "active" and
                            vlan in vlans(port) and
                            any(kind_of(ip) == "ip_address" and attrs(ip).get("status") == "active"
                                for ip in children[("assigned_object", port)])):
                        gateways.add(device)
                        if (refs(port).get("vrf") != f"vrf/{network}" or
                                any(refs(ip).get("vrf") != f"vrf/{network}"
                                    for ip in children[("assigned_object", port)]
                                    if kind_of(ip) == "ip_address")):
                            report("mfg-zone-isolation", port, "A gateway SVI and its address must stay in their "
                                                                "own segment's routing context; a borrowed one "
                                                                "pools address space across the zone boundary.")
            if gateways != tier:
                report("mfg-gateway-inventory", vlan, "Each segment needs its active addressed gateway SVIs on "
                                                       "exactly its own distribution tier; the conduit needs all four.")

        # --- the IT/OT zone boundary, derived from the finished graph ---------
        ot_vlans = {f"vlan/{sid}/{name}" for name in OT_NETWORKS}
        conduit_vlan = f"vlan/{sid}/{CONDUIT}"
        management_vlan = f"vlan/{sid}/management"
        ot_zone = process_switches | process_dist | set(process_expected)
        ot_zones[sid] = ot_zone
        for vlan in ot_vlans:
            vlan_zones[vlan] = (sid, ot_zone)
        vlan_zones[conduit_vlan] = (sid, corporate_dist | process_dist)
        # Estate-wide, not site-scoped: a port anywhere that names a plant-floor
        # VLAN widens that zone, wherever its own device happens to live.
        carriers = defaultdict(set)
        for port, obj in objects.items():
            if obj["kind"] != "interface":
                continue
            for vlan in vlans(port):
                carriers[vlan].add(refs(port).get("device"))
        for vlan in sorted(ot_vlans):
            if not carriers[vlan] <= ot_zone:
                report("mfg-zone-isolation", vlan, "A plant-floor segment may exist only on its own access pair, "
                                                    "its own distribution pair and its own endpoints.")
            if not process_switches | process_dist <= carriers[vlan]:
                report("mfg-zone-isolation", vlan, "Every plant-floor switch and distribution gateway must carry "
                                                    "both plant-floor segments.")
            if carriers[vlan] & set(roles["role/wan-edge"]):
                report("mfg-zone-isolation", vlan, "No plant-floor segment may reach a carrier edge device.")
        if carriers[conduit_vlan] != corporate_dist | process_dist:
            report("mfg-zone-isolation", conduit_vlan, "The conduit segment joins the two distribution pairs and "
                                                        "nothing else; it never reaches an access switch, an "
                                                        "endpoint or a carrier edge.")
        # Endpoints are included deliberately: a second interface on a line
        # controller is another way to bridge the boundary without any trunk.
        for device in sorted(ot_zone):
            allowed = set(ot_vlans) | ({conduit_vlan} if device in process_dist else set())
            for port in children[("device", device)]:
                if kind_of(port) != "interface":
                    continue
                permitted = {management_vlan} if attrs(port).get("mgmt_only") else allowed
                if not vlans(port) <= permitted:
                    report("mfg-zone-isolation", port, "A plant-floor device carries only its own segments, the "
                                                        "conduit on its distribution pair, and the plant management "
                                                        "segment on its dedicated management port.")
        for device in sorted(process_dist):
            allowed_peers = process_switches | process_dist | corporate_dist | set(roles["role/management"])
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or peer is None:
                    continue
                if kind_of(peer) != "interface" or refs(peer).get("device") not in allowed_peers - {device}:
                    report("mfg-zone-isolation", port, "The plant-floor distribution pair attaches only to its own "
                                                        "access switches, its peer, the corporate distribution pair "
                                                        "and its management switch; no circuit or carrier edge.")
        # Counted, not a set: a duplicated pair is an extra inter-tier link.
        conduit_links = Counter()
        for device in sorted(process_dist):
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or refs(peer).get("device") not in corporate_dist:
                    continue
                conduit_links[(device, refs(peer)["device"])] += 1
                if vlans(port) != {conduit_vlan} or vlans(peer) != {conduit_vlan} or not active_path(port):
                    report("mfg-conduit-path", port, "Every inter-tier trunk must be a connected enabled link "
                                                      "carrying the conduit segment and nothing else.")
        if conduit_links != Counter((near, far) for near in process_dist for far in corporate_dist):
            report("mfg-conduit-path", site, "The conduit must fully mesh the plant-floor and corporate "
                                              "distribution pairs; that is the only modeled path between them.")
        # Allow-list, not deny-list: a VLAN-less cable to the management switch
        # or any other unlisted corporate device is a boundary crossing too.
        management_devices = set(roles["role/management"])
        for device in sorted(process_switches | set(process_expected)):
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or kind_of(peer) != "interface":
                    continue
                peer_device = refs(peer).get("device")
                wanted = management_devices if attrs(port).get("mgmt_only") else ot_zone - {device}
                if peer_device not in wanted:
                    report("mfg-zone-isolation", port, "A plant-floor access switch or endpoint data port stays "
                                                        "inside its own zone; only its dedicated management port "
                                                        "reaches the plant management switch.")
        # The shared serial console server is the third declared crossing:
        # serial CLI, never a forwarding path. Pin it exactly on both sides,
        # reading cables directly — the terminal-peer map drops conflicted ends.
        console_servers = set(roles["role/console-server"])  # roles is already site-scoped
        for key, obj in objects.items():
            if kind_of(key) != "cable":
                continue
            ends = [obj["refs"].get("a"), obj["refs"].get("b")]
            for near, far in (ends, ends[::-1]):
                if kind_of(near) == "console_port" and refs(near).get("device") in ot_zone:
                    if kind_of(far) != "console_server_port" or refs(far).get("device") not in console_servers:
                        report("mfg-zone-isolation", key, "A plant-floor console port terminates only on the "
                                                           "plant's own console server — the declared serial "
                                                           "crossing.")
        for device in sorted(console_servers):
            for port in children[("device", device)]:
                if kind_of(port) == "interface" and not vlans(port) <= {management_vlan}:
                    report("mfg-zone-isolation", port, "The shared console server carries serial CLI and its own "
                                                        "management address only; no plant-floor segment may ride it.")

        # --- addressing --------------------------------------------------------
        if sid not in allocations:
            report("mfg-address-allocation", site, "Requested plant has no persistent reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid]*65536, 16))
            for network, offset in NETWORK_OFFSETS.items():
                prefix = f"prefix/{sid}/{network}"
                wanted = ip_network((int(container.network_address) + offset*256, 24))
                if (attrs(prefix).get("prefix") != str(wanted) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or refs(prefix).get("vrf") != f"vrf/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(offset+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site or
                        refs(f"{prefix}/reservation").get("vrf") != f"vrf/{network}"):
                    report("mfg-prefix-policy", prefix, "Plant role /24 and its reservation container must retain "
                                                         "the fixed offset, site VLAN and segment routing context "
                                                         "inside the reserved /16.")

        # --- carrier attachments ------------------------------------------------
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind_of(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("mfg-wan-inventory", site, "Plant carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers if rate >= (50 if side == "a" else 100) and
                                Decimal(rate)*usable_fraction >= _peak(item)), None)
            if len(circuits[provider]) != 1:
                report("mfg-wan-inventory", site, "Each plant requires exactly one independent attachment to each modeled carrier.")
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
                    report("mfg-wan-capacity", circuit, "Plant commitment and active 1 Gbps edge handoff must "
                                                         "cover the plant peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("mfg-wan-diversity", site, "The two plant carriers must terminate on different active edge devices.")

        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in INFRASTRUCTURE_ROLES:
                report("mfg-equipment-role", device, "Plant infrastructure must serve an explicit network, "
                                                      "management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind_of(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room != closet or attrs(rack).get("status") != "active"):
                report("mfg-equipment-placement", device, "Plant infrastructure must occupy an active rack in the "
                                                           "plant equipment room.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of,
                                       poe_watts=poe_watts, optics_watts=optics_watts))

    # --- nothing outside a zone may name that zone's segments ------------------
    # A per-plant VLAN sweep only sees interfaces. This one sees every reference
    # of every kind, so a WLAN, FHRP group, L2VPN, translation rule or any other
    # record that names a plant-floor or conduit VLAN is reported, as is any
    # record naming two different plants' zone segments.
    for key, obj in objects.items():
        named = {target for value in refs(key).values()
                 for target in (value if isinstance(value, list) else [value])
                 if isinstance(target, str) and target in vlan_zones}
        if not named:
            continue
        owners = {vlan_zones[vlan][0] for vlan in named}
        if len(owners) > 1:
            report("mfg-zone-isolation", key, "No record may join two plants' zone segments.")
            continue
        sid = owners.pop()
        if obj["kind"] == "interface" and all(refs(key).get("device") in vlan_zones[vlan][1]
                                              for vlan in named):
            continue
        if obj["kind"] == "prefix" and refs(key).get("scope_site") == f"site/{sid}":
            continue
        report("mfg-zone-isolation", key, "Only that plant's own zone equipment and its segment prefix may name "
                                           "a plant-floor or conduit VLAN; no other record of any kind may.")
    # Back-ported from the utility profile's review: a service, FHRP assignment,
    # L2VPN termination, tunnel termination or VM interface can reach into a
    # plant-floor zone by naming one of its interfaces or addresses directly,
    # with no VLAN reference at all.
    zone_devices = set().union(*ot_zones.values()) if ot_zones else set()
    ot_records = {key for key, obj in objects.items()
                  if obj["kind"] == "interface" and refs(key).get("device") in zone_devices}
    ot_records |= {key for key, obj in objects.items()
                   if obj["kind"] in {"ip_address", "mac_address"}
                   and refs(key).get("assigned_object") in ot_records}
    for key, obj in objects.items():
        if obj["kind"] == "cable" or key in ot_records or key in zone_devices:
            continue
        if any(target in ot_records for value in refs(key).values()
               for target in (value if isinstance(value, list) else [value]) if isinstance(target, str)):
            report("mfg-zone-isolation", key, "No record outside a plant's floor zone may bind that zone's "
                                              "interfaces or their assigned addresses.")

    # An interface bond, bridge or subinterface is a second way to join two
    # devices that carries no VLAN reference at all.
    zone_of = {device: sid for sid, members in ot_zones.items() for device in members}
    for key, obj in objects.items():
        if obj["kind"] != "interface":
            continue
        for field in ("parent", "bridge", "lag"):
            target = refs(key).get(field)
            if not isinstance(target, str):
                continue
            if zone_of.get(refs(key).get("device")) != zone_of.get(refs(target).get("device")):
                report("mfg-zone-isolation", key, "An interface bond, bridge or subinterface must not join the "
                                                   "plant-floor zone to the corporate tier or to another plant.")

    # --- plant-floor routing contexts are not shared with the corporate tier ---
    # Derived from actual references, not key spelling: only the estate-wide
    # hierarchical container, a plant's own OT segment and its reservation, and
    # records owned by a device inside that plant's OT zone may sit in one.
    ot_vrfs = {f"vrf/{network}": network for network in OT_NETWORKS}
    zone_devices = set().union(*ot_zones.values()) if ot_zones else set()
    for vrf, network in sorted(ot_vrfs.items()):
        if kind_of(vrf) != "vrf" or attrs(vrf).get("enforce_unique") is not True:
            report("mfg-zone-isolation", vrf, "Each plant-floor segment needs its own uniqueness-enforcing "
                                               "routing context.")
    for key, obj in objects.items():
        network = ot_vrfs.get(refs(key).get("vrf"))
        if network is None:
            continue
        if obj["kind"] == "prefix":
            scope = refs(key).get("scope_site")
            if scope is None:
                if attrs(key).get("status") == "container" and not refs(key).get("vlan"):
                    continue  # the estate-wide hierarchical segment reservation
            elif scope in {f"site/{sid}" for sid, _ in premises} and refs(key).get("vlan") in (
                    None, f"vlan/{scope.removeprefix('site/')}/{network}"):
                continue
        elif obj["kind"] in {"interface", "vm_interface"}:
            if refs(key).get("device") in zone_devices:
                continue
        elif obj["kind"] == "ip_address":
            if refs(refs(key).get("assigned_object")).get("device") in zone_devices:
                continue
        elif obj["kind"] == "vrf":
            continue
        report("mfg-zone-isolation", key, "Only a plant's own plant-floor segments and the records owned by that "
                                           "plant's plant-floor equipment may use a plant-floor routing context.")

    # --- independently sized shared corporate services -------------------------
    totals = dict(plants=len(plants), sites=len(plants) + 2,
                  lines=sum(item["production_lines"] for item in plants),
                  docks=sum(item["warehouse_docks"] for item in plants),
                  ot_endpoints=sum(_ot_endpoints(item) for item in plants),
                  endpoints=sum(_ot_endpoints(item) + _it_endpoints(item) for item in plants))
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
                    peak=sum(_peak(item) for item in plants), reserve=reserve, strict_sites=False,
                    network_offsets=DC_NETWORK_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
