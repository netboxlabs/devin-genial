"""Independent utility demand, path and station/corporate zone obligations.

Recipe policy, control-house geometry, endpoint density and service sizing are
restated deliberately instead of importing the utility builder. The base
validator supplies its physical indexes; the shared DC checker inspects the
independently sized control-center services. Emitted contracts can explain,
never remove, an obligation.

The zone checks are the point of this profile. The station (OT) segments
`protection`, `telemetry` and `station` must exist on exactly the station access
pair, its own distribution pair and its own endpoints, and nowhere else: not on a
corporate access switch, not on a carrier edge, not on a cable that reaches one.
That membership is read estate-wide from every interface's actual VLAN
references, so a port anywhere naming a station VLAN widens the zone and is
reported. The `conduit` segment must be the only modeled trunk between the two
distribution tiers, and the only non-OT VLANs a station device may carry are that
conduit on the distribution pair and the substation management segment on a
device's own dedicated management port.

Nothing here asserts enforcement. A modeled boundary is inventory: no firewall
policy, access control list, route filter, air gap, electronic security
perimeter or NERC CIP state is checked, claimed or claimable from these findings.
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
IT_NETWORKS = ("management", "office")
OT_NETWORKS = ("protection", "telemetry", "station")
CONDUIT = "conduit"
NETWORK_OFFSETS = {"management": 0, "office": 1, "protection": 2, "telemetry": 3,
                   "station": 4, "wan": 9, "conduit": 12}
DC_NETWORK_OFFSETS = {"management": 0, "applications": 6, "database": 7,
                      "backup": 8, "wan": 9, "storage": 10}
MAX_SUBSTATIONS, MAX_BAYS, MIN_BAYS = 24, 16, 2
MIN_CENTERS, MAX_CENTERS = 1, 2
SUBSTATION_KINDS = ("transmission", "distribution")
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
SUBSTATION_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
BAY_RTUS, BAY_RELAYS = 1, 1
BAY_ENDPOINTS = BAY_RTUS + BAY_RELAYS
STATION_HMIS, STATION_GATEWAYS = 2, 1
HOUSE_ENDPOINTS = STATION_HMIS + STATION_GATEWAYS
WORKSTATIONS = {"transmission": 2, "distribution": 1}
BASE_MBPS = {"transmission": 20, "distribution": 10}
MBPS_PER_BAY, MBPS_PER_WORKSTATION = 3, 2
MAX_ACCESS_SWITCHES = 38
CLOSET_ORIGIN = [24, 18, 0]
CONTROL_ROOM_ORIGIN = [48, 10, 0]
SERVICE_POLICY = (
    ("scada-front-end", "applications", "rtus", 120, 8, 16384, 200000, 443),
    ("historian", "database", "ot_endpoints", 400, 8, 32768, 1000000, 5432),
    ("ems-gateway", "applications", "substations", 16, 4, 8192, 100000, 443),
    ("monitoring", "applications", "endpoints", 2000, 4, 16384, 200000, 443),
    ("dns", "applications", "sites", 16, 2, 4096, 40000, 53),
    ("backup", "backup", "substations", 12, 4, 16384, 1000000, 443),
)
TIER_1 = {"scada-front-end", "historian", "ems-gateway", "dns"}
INFRASTRUCTURE_ROLES = {"role/distribution", "role/wan-edge", "role/access", "role/management",
                        "role/console-server", "role/pdu", "role/patch-panel"}


def _ot_endpoints(item):
    return BAY_ENDPOINTS * item["bays"] + HOUSE_ENDPOINTS


def _it_endpoints(item):
    return WORKSTATIONS[item["kind"]]


def _peak(item):
    return (BASE_MBPS[item["kind"]] + MBPS_PER_BAY * item["bays"]
            + MBPS_PER_WORKSTATION * WORKSTATIONS[item["kind"]])


def _mount(ordinal, origin):
    """Authored permanent mount: every substation endpoint is a floor position."""
    offset, height = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6)), 0.8
    return [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "utility":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    reserve, tiers = recipe.get("reserve_fraction"), recipe.get("wan_tiers_mbps")
    stations, control_centers = recipe.get("substations"), recipe.get("control_centers")
    if (not isinstance(stations, list) or not 1 <= len(stations) <= MAX_SUBSTATIONS or
            type(control_centers) is not int or not MIN_CENTERS <= control_centers <= MAX_CENTERS or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("utl-recipe", "plan", "Utility validation requires 1 or 2 control centers, 1–24 bounded "
                                     "substations and a bounded reserve.")
        return findings
    keys, codes = set(), set()
    for entry in stations:
        key = entry.get("key") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or not isinstance(key, str) or key in keys or
                not re.fullmatch(SUBSTATION_KEY, key) or key.replace("-", "") in codes or
                entry.get("kind") not in SUBSTATION_KINDS or
                type(entry.get("bays")) is not int or not MIN_BAYS <= entry["bays"] <= MAX_BAYS):
            report("utl-recipe", "plan", "Each substation needs a unique hyphen-separated lowercase key of at "
                                         "most 20 characters, also distinct with hyphens removed, a "
                                         "transmission or distribution kind, and 2–16 installed bay positions.")
            return findings
        keys.add(key)
        codes.add(key.replace("-", ""))
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("utl-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    stations = sorted(stations, key=lambda entry: entry["key"])
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

    premises = [(f"sub-{item['key']}", item) for item in stations]
    center_sites = {f"site/dc-{n:02}" for n in range(1, control_centers + 1)}
    expected_sites = {f"site/{sid}" for sid, _ in premises} | center_sites
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != expected_sites:
        report("utl-site-inventory", "plan", "The estate requires exactly one site per requested substation "
                                             "and one per requested control center.")

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("utl-address-allocation", "plan", "Every utility site needs a distinct /16 reservation "
                                                 "inside the private pool.")
        return findings

    access_alias = selected_alias(recipe, "access")
    port_names = catalog.get(access_alias, {}).get("access_ports", [])
    capacity = int(Decimal(len(port_names)) * usable_fraction)
    # Per substation: the devices its zone covers, and the exact device set
    # allowed to name each zone-bearing VLAN. Both are used after the loop for
    # the estate-wide reference sweep that no per-site check can see.
    ot_zones, vlan_zones = {}, {}

    for sid, item in premises:
        site = f"site/{sid}"
        closet = f"location/{sid}"
        if attrs(site).get("status") != "active":
            report("utl-site-status", site, "Requested substation must be active.")

        # --- authored control-house geometry from the permanent bay ledger ----
        names = [f"bay-{n+1:02}" for n in range(item["bays"])]
        ledger = plan.get("reservations", {}).get(f"substation-bays/{sid}")
        if (not isinstance(ledger, dict) or set(ledger) != set(names) or
                any(type(slot) is not int or not 0 <= slot < MAX_BAYS for slot in ledger.values()) or
                sorted(ledger.values()) != list(range(len(names)))):
            report("utl-room-allocation", site, "Every switchyard bay needs one unique permanent ground-floor "
                                                "position; growth appends positions and never renumbers.")
            continue
        control = f"location/{sid}/control-room"
        rooms = {control: ("control_room", list(CONTROL_ROOM_ORIGIN))}
        bay_rooms = []
        for name in names:
            key = f"location/{sid}/{name}"
            slot = ledger[name]
            rooms[key] = ("switchyard_bay", [6 + 8*(slot % 4), 30 + 8*(slot // 4), 0])
            bay_rooms.append(key)
        capacities = {control: WORKSTATIONS[item["kind"]]}
        actual_rooms = {key for key in children[("site", site)] if kind_of(key) == "location" and
                        meta(key).get("space_type") in {"control_room", "switchyard_bay"}}
        if actual_rooms != set(rooms):
            report("utl-room-inventory", site, "The control room and switchyard bay positions must match the "
                                               "authored single-floor control-house layout exactly.")
        for room, (space_type, origin) in rooms.items():
            if (kind_of(room) != "location" or meta(room).get("space_type") != space_type or
                    meta(room).get("floor") != 1 or meta(room).get("position_m") != origin or
                    refs(room).get("parent") != f"location/{sid}/floor-01" or
                    attrs(room).get("status") != "active" or
                    (room in capacities and meta(room).get("capacity", {}).get("workstations") != capacities[room])):
                report("utl-room-placement", room, "Substation rooms must occupy their fixed active ground-floor "
                                                   "positions with their installed desk capacity.")
        if (kind_of(closet) != "location" or meta(closet).get("space_type") != "equipment_room" or
                meta(closet).get("floor") != 1 or meta(closet).get("position_m") != CLOSET_ORIGIN):
            report("utl-closet-inventory", site, "Each substation needs its single permanent control-house equipment room.")

        # --- authored endpoint inventory, separated by zone --------------------
        corporate_expected, station_expected = {}, {}

        def endpoint(bucket, label, role, network, room, ordinal):
            bucket[f"device/{sid}/{label}"] = (role, network, room, ordinal)

        for number in range(STATION_HMIS):
            endpoint(station_expected, f"hmi-{number+1:02}", "hmi", "station", control, number + 1)
        endpoint(station_expected, "gateway-01", "station-gateway", "telemetry", control, STATION_HMIS + 1)
        for number in range(WORKSTATIONS[item["kind"]]):
            endpoint(corporate_expected, f"desk-{number+1:02}", "workstation", "office", control,
                     STATION_HMIS + STATION_GATEWAYS + number + 1)
        for index, room in enumerate(bay_rooms):
            endpoint(station_expected, f"rtu-{index+1:02}", "rtu", "telemetry", room, 1)
            endpoint(station_expected, f"relay-{index+1:02}", "protection-relay", "protection", room,
                     BAY_RTUS + 1)
        expected = corporate_expected | station_expected

        devices = [key for key in children[("site", site)] if kind_of(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        endpoint_roles = {"role/workstation", "role/rtu", "role/protection-relay", "role/hmi",
                          "role/station-gateway"}
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in endpoint_roles}
        if actual != set(expected):
            report("utl-endpoint-inventory", site, "Every requested remote terminal unit, protection relay, "
                                                   "station HMI, station gateway and corporate workstation must "
                                                   "match the authored substation demand.")
        counted = Counter(role for role, _, _, _ in expected.values())
        for role, wanted in counted.items():
            if len(roles[f"role/{role}"]) != wanted:
                report("utl-endpoint-demand", site, f"Substation demand requires {wanted} {role} devices; "
                                                    f"found {len(roles[f'role/{role}'])}.")

        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site
                     and c.get("kind") == "substation"] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("utl-contract-inventory", site, "Each substation needs exactly one explanatory contract.")
        else:
            wanted = dict(bays=item["bays"], rtus=BAY_RTUS*item["bays"],
                          protection_relays=BAY_RELAYS*item["bays"], station_hmis=STATION_HMIS,
                          station_gateways=STATION_GATEWAYS, workstations=WORKSTATIONS[item["kind"]],
                          peak_mbps=_peak(item))
            if (any(contracts[0].get("demand", {}).get(field) != value for field, value in wanted.items()) or
                    contracts[0].get("substation") != item["key"] or
                    contracts[0].get("substation_kind") != item["kind"]):
                report("utl-demand-report", site, "Reported substation demand must follow the authored bay, "
                                                  "relay, HMI, gateway, desk and peak policy and name its actual "
                                                  "substation and kind.")

        # --- the two access zones, their ledgers and their own switch pairs ----
        zones = {}
        for label, population, prefix, scope_suffix, networks in (
                ("corporate", corporate_expected, "access-", "", IT_NETWORKS),
                ("station", station_expected, "ot-access-", "/ot", OT_NETWORKS)):
            scope = f"access-endpoints/{sid}/{closet}{scope_suffix}"
            slots = plan.get("reservations", {}).get(scope)
            switches = set()
            if (not capacity or not isinstance(slots, dict) or set(slots) != set(population) or
                    any(type(n) is not int or not 0 <= n < MAX_ACCESS_SWITCHES*capacity for n in slots.values()) or
                    len(set(slots.values())) != len(slots)):
                report("utl-access-allocation", site, f"Each {label} endpoint needs one unique bounded permanent "
                                                      "access-port slot in its own zone ledger.")
            else:
                count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity))) if slots else 0
                switches = {f"device/{sid}/{prefix}{n+1:02}" for n in range(count)}
                for device, slot in slots.items():
                    pair, offset = divmod(slot, 2*capacity)
                    switch = f"device/{sid}/{prefix}{2*pair+offset % 2+1:02}"
                    if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port_names[offset//2]}":
                        report("utl-access-allocation", device, "Actual endpoint path must match its reserved "
                                                                "switch and catalog copper port.")
            zones[label] = dict(population=population, switches=switches, networks=networks)
        corporate_switches = zones["corporate"]["switches"]
        station_switches = zones["station"]["switches"]
        if set(roles["role/access"]) != corporate_switches | station_switches:
            report("utl-access-inventory", site, "The control house needs complete corporate and station access "
                                                 "pairs through each zone's last permanent slot, retaining copper "
                                                 "headroom.")
        if len(roles["role/access"]) > MAX_ACCESS_SWITCHES:
            report("utl-access-capacity", site, "Both zones share one finite distribution attachment budget; "
                                                "their access switches exceed it.")

        switch_counts = Counter()
        for device, (role, network, room, ordinal) in expected.items():
            zone = "station" if device in station_expected else "corporate"
            if (kind_of(device) != "device" or attrs(device).get("status") != "active" or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    not meta(device).get("endpoint") or meta(device).get("network") != network):
                report("utl-endpoint-placement", device, "Required endpoint identity, active status, segment and "
                                                         "room must match the authored control-house layout.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind_of(peer) != "interface" or
                    refs(switch).get("role") != "role/access" or attrs(switch).get("status") != "active" or
                    refs(switch).get("location") != closet or switch not in zones[zone]["switches"] or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("utl-endpoint-path", device, f"Endpoint needs its own connected enabled VLAN path to an "
                                                    f"active {zone} access switch in the control-house equipment room.")
            if switch:
                switch_counts[switch] += 1
            mount = _mount(ordinal, rooms[room][1])
            route = math.ceil(sum(abs(a-b) for a, b in zip(mount, CLOSET_ORIGIN))+10)
            position = meta(device).get("placement", {})
            if (position.get("room") != room or position.get("floor") != 1 or
                    position.get("cable_origin") != closet or position.get("position_m") != mount or
                    path_lengths.get(port) != route or not 0 < route <= 80):
                report("utl-endpoint-route", device, "Each endpoint ordinal must retain its fixed mount and actual "
                                                     "copper route inside the 80 m ceiling.")
            address = refs(device).get("primary_ip4")
            if (kind_of(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or refs(address).get("vrf") != f"vrf/{network}"):
                report("utl-endpoint-address", device, "Installed endpoint requires its active primary address on "
                                                       "eth0 in its own segment routing context.")
        for device in roles["role/access"]:
            if meta(device).get("hardware") != access_alias or switch_counts[device] > capacity:
                report("utl-access-capacity", device, "Actual attached endpoints must fit this catalog access "
                                                      "switch after reserve.")

        # --- the two distribution tiers ---------------------------------------
        corporate_dist = {f"device/{sid}/dist-{side}" for side in ("a", "b")}
        station_dist = {f"device/{sid}/ot-dist-{side}" for side in ("a", "b")}
        if (set(roles["role/distribution"]) != corporate_dist | station_dist or
                len(roles["role/wan-edge"]) != 2):
            report("utl-core-inventory", site, "A substation requires a corporate distribution pair, a separate "
                                               "station distribution pair and two carrier edge devices.")
        for label, members, parents, networks in (
                ("corporate", set(roles["role/wan-edge"]) | corporate_switches, corporate_dist, IT_NETWORKS),
                ("station", station_switches, station_dist, OT_NETWORKS)):
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
                    report("utl-uplink-path", device, f"Every {label} switch or carrier edge requires two connected "
                                                      f"active {label} distribution uplinks carrying that zone's "
                                                      "segments.")
        for network, tier in ([(name, corporate_dist) for name in IT_NETWORKS] +
                              [(name, station_dist) for name in OT_NETWORKS] +
                              [(CONDUIT, corporate_dist | station_dist)]):
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for device in sorted(corporate_dist | station_dist):
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
                            report("utl-zone-isolation", port, "A gateway SVI and its address must stay in their "
                                                               "own segment's routing context; a borrowed one "
                                                               "pools address space across the zone boundary.")
            if gateways != tier:
                report("utl-gateway-inventory", vlan, "Each segment needs its active addressed gateway SVIs on "
                                                      "exactly its own distribution tier; the conduit needs all four.")

        # --- the station/corporate zone boundary, derived from the graph ------
        ot_vlans = {f"vlan/{sid}/{name}" for name in OT_NETWORKS}
        conduit_vlan = f"vlan/{sid}/{CONDUIT}"
        management_vlan = f"vlan/{sid}/management"
        ot_zone = station_switches | station_dist | set(station_expected)
        ot_zones[sid] = ot_zone
        for vlan in ot_vlans:
            vlan_zones[vlan] = (sid, ot_zone)
        vlan_zones[conduit_vlan] = (sid, corporate_dist | station_dist)
        # Estate-wide, not site-scoped: a port anywhere that names a station
        # VLAN widens that zone, wherever its own device happens to live.
        carriers = defaultdict(set)
        for port, obj in objects.items():
            if obj["kind"] != "interface":
                continue
            for vlan in vlans(port):
                carriers[vlan].add(refs(port).get("device"))
        for vlan in sorted(ot_vlans):
            if not carriers[vlan] <= ot_zone:
                report("utl-zone-isolation", vlan, "A station segment may exist only on its own access pair, its "
                                                   "own distribution pair and its own endpoints.")
            if not station_switches | station_dist <= carriers[vlan]:
                report("utl-zone-isolation", vlan, "Every station switch and distribution gateway must carry all "
                                                   "three station segments.")
            if carriers[vlan] & set(roles["role/wan-edge"]):
                report("utl-zone-isolation", vlan, "No station segment may reach a carrier edge device.")
        if carriers[conduit_vlan] != corporate_dist | station_dist:
            report("utl-zone-isolation", conduit_vlan, "The conduit segment joins the two distribution pairs and "
                                                       "nothing else; it never reaches an access switch, an "
                                                       "endpoint or a carrier edge.")
        # Endpoints are included deliberately: a second interface on a remote
        # terminal unit is another way to bridge the boundary without a trunk.
        for device in sorted(ot_zone):
            allowed = set(ot_vlans) | ({conduit_vlan} if device in station_dist else set())
            for port in children[("device", device)]:
                if kind_of(port) != "interface":
                    continue
                permitted = {management_vlan} if attrs(port).get("mgmt_only") else allowed
                if not vlans(port) <= permitted:
                    report("utl-zone-isolation", port, "A station device carries only its own segments, the "
                                                       "conduit on its distribution pair, and the substation "
                                                       "management segment on its dedicated management port.")
        for device in sorted(station_dist):
            allowed_peers = station_switches | station_dist | corporate_dist | set(roles["role/management"])
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or peer is None:
                    continue
                if kind_of(peer) != "interface" or refs(peer).get("device") not in allowed_peers - {device}:
                    report("utl-zone-isolation", port, "The station distribution pair attaches only to its own "
                                                       "access switches, its peer, the corporate distribution pair "
                                                       "and its management switch; no circuit or carrier edge.")
        # Counted, not a set: a duplicated pair is an extra inter-tier link.
        conduit_links = Counter()
        for device in sorted(station_dist):
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or refs(peer).get("device") not in corporate_dist:
                    continue
                conduit_links[(device, refs(peer)["device"])] += 1
                if vlans(port) != {conduit_vlan} or vlans(peer) != {conduit_vlan} or not active_path(port):
                    report("utl-conduit-path", port, "Every inter-tier trunk must be a connected enabled link "
                                                     "carrying the conduit segment and nothing else.")
        if conduit_links != Counter((near, far) for near in station_dist for far in corporate_dist):
            report("utl-conduit-path", site, "The conduit must fully mesh the station and corporate distribution "
                                             "pairs; that is the only modeled path between them.")
        # Allow-list, not deny-list: a station access switch or endpoint may
        # only reach this substation's own zone and its own management switch,
        # so a cable to another substation's station zone is reported too.
        for device in sorted(station_switches | set(station_expected)):
            allowed_peers = ot_zone | set(roles["role/management"])
            for port in children[("device", device)]:
                peer = peers.get(port)
                if kind_of(port) != "interface" or peer is None:
                    continue
                if kind_of(peer) != "interface" or refs(peer).get("device") not in allowed_peers - {device}:
                    report("utl-zone-isolation", port, "A station access switch or endpoint attaches only to this "
                                                       "substation's own station zone and its management switch; "
                                                       "never to corporate forwarding equipment, a circuit, or "
                                                       "another substation.")

        # --- addressing --------------------------------------------------------
        if sid not in allocations:
            report("utl-address-allocation", site, "Requested substation has no persistent reservation.")
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
                    report("utl-prefix-policy", prefix, "Substation role /24 and its reservation container must "
                                                        "retain the fixed offset, site VLAN and segment routing "
                                                        "context inside the reserved /16.")

        # --- carrier attachments ------------------------------------------------
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind_of(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("utl-wan-inventory", site, "Substation carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers if rate >= (50 if side == "a" else 100) and
                                Decimal(rate)*usable_fraction >= _peak(item)), None)
            if len(circuits[provider]) != 1:
                report("utl-wan-inventory", site, "Each substation requires exactly one independent attachment to each modeled carrier.")
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
                    report("utl-wan-capacity", circuit, "Substation commitment and active 1 Gbps edge handoff must "
                                                        "cover the substation peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("utl-wan-diversity", site, "The two substation carriers must terminate on different active edge devices.")

        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in INFRASTRUCTURE_ROLES:
                report("utl-equipment-role", device, "Substation infrastructure must serve an explicit network, "
                                                     "management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind_of(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room != closet or attrs(rack).get("status") != "active"):
                report("utl-equipment-placement", device, "Substation infrastructure must occupy an active rack in "
                                                          "the control-house equipment room.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of,
                                       poe_watts=poe_watts, optics_watts=optics_watts))

    # --- nothing outside a zone may name that zone's segments ------------------
    # A per-substation VLAN sweep only sees interfaces. This one sees every
    # reference of every kind, so a WLAN, FHRP group, L2VPN, translation rule or
    # any other record that names a station or conduit VLAN is reported, as is
    # any record naming two different substations' zone segments.
    for key, obj in objects.items():
        named = {target for value in refs(key).values()
                 for target in (value if isinstance(value, list) else [value])
                 if isinstance(target, str) and target in vlan_zones}
        if not named:
            continue
        owners = {vlan_zones[vlan][0] for vlan in named}
        if len(owners) > 1:
            report("utl-zone-isolation", key, "No record may join two substations' zone segments.")
            continue
        sid = owners.pop()
        if obj["kind"] == "interface" and all(refs(key).get("device") in vlan_zones[vlan][1]
                                              for vlan in named):
            continue
        if obj["kind"] == "prefix" and refs(key).get("scope_site") == f"site/{sid}":
            continue
        report("utl-zone-isolation", key, "Only that substation's own zone equipment and its segment prefix may "
                                          "name a station or conduit VLAN; no other record of any kind may.")
    # --- nothing outside a zone may bind that zone's ports or addresses -------
    # The VLAN sweep above sees segment membership. A service, FHRP assignment,
    # L2VPN termination, tunnel termination or VM interface can reach into the
    # zone by naming one of its interfaces or addresses directly, with no VLAN
    # reference at all. Cables are excluded because the peer rules above already
    # constrain every physical attachment.
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
            report("utl-zone-isolation", key, "No record outside a substation's station zone may bind that zone's "
                                              "interfaces or addresses; the control-center services never "
                                              "reference a station endpoint.")

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
                report("utl-zone-isolation", key, "An interface bond, bridge or subinterface must not join the "
                                                  "station zone to the corporate tier or to another substation.")

    # --- station routing contexts are not shared with the corporate tier -------
    # Derived from actual references, not key spelling: only the estate-wide
    # hierarchical container, a substation's own OT segment and its reservation,
    # and records owned by a device inside that substation's OT zone may sit in
    # one.
    ot_vrfs = {f"vrf/{network}": network for network in OT_NETWORKS}
    for vrf, network in sorted(ot_vrfs.items()):
        if kind_of(vrf) != "vrf" or attrs(vrf).get("enforce_unique") is not True:
            report("utl-zone-isolation", vrf, "Each station segment needs its own uniqueness-enforcing "
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
        report("utl-zone-isolation", key, "Only a substation's own station segments and the records owned by that "
                                          "substation's station equipment may use a station routing context.")

    # --- independently sized shared control-center services --------------------
    totals = dict(substations=len(stations), sites=len(stations) + control_centers,
                  bays=sum(item["bays"] for item in stations),
                  rtus=sum(BAY_RTUS * item["bays"] for item in stations),
                  ot_endpoints=sum(_ot_endpoints(item) for item in stations),
                  endpoints=sum(_ot_endpoints(item) + _it_endpoints(item) for item in stations))
    services = []
    for key, network, metric, threshold, vcpus, memory, disk, port in SERVICE_POLICY:
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        services.append(dict(key=key, groups=max(1, math.ceil(totals[metric]/threshold)), replicas=2,
                             failure_domain="rack", network=network, vcpus=vcpus, memory_mb=memory,
                             disk_mb=disk, listeners=listeners,
                             criticality="tier-1" if key in TIER_1 else "tier-2"))
    findings.extend(validate_resolved(plan, catalog, sites=center_sites, workloads=services,
                    peak=sum(_peak(item) for item in stations), reserve=reserve, strict_sites=False,
                    network_offsets=DC_NETWORK_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
