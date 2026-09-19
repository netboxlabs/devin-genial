"""Independent managed-service-provider demand, path and tenancy obligations.

Recipe policy, office geometry, service sizing and the ownership/operation split
are restated deliberately instead of importing the MSP builder. The base
validator supplies its physical indexes; the shared DC checker inspects the
independently sized managed services. Emitted contracts can explain, never
remove, an obligation.

The tenancy checks are the point of this profile. Every object at a managed
office must carry exactly its own customer's tenant, and nothing — no cable,
segment, VLAN, prefix, address, routing context or service — may join two
customers. The provider's relationship shows up only as the shared operations
owner and the per-customer technical desk assigned by equipment role and actual
tenant, which is checked here rather than assumed.
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
NETWORK_OFFSETS = {"management": 0, "staff": 1, "wireless": 3, "security": 4, "wan": 8, "guest": 12}
DC_NETWORK_OFFSETS = {"management": 0, "applications": 5, "database": 6,
                      "backup": 7, "wan": 8, "storage": 9}
OFFICE_DESKS = 12
MAX_CUSTOMERS, MAX_OFFICES = 24, 4
MIN_STAFF, MAX_STAFF = 4, 4 * OFFICE_DESKS
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
CUSTOMER_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
RECEPTION_MANAGED, RECEPTION_GUEST = 6, 12
DEVICES_PER_DESK = 2
GUEST_CAPACITY = 252
OFFICE_BASE_MBPS, OFFICE_MBPS_PER_DESK = 20, 2
SERVICE_POLICY = (
    ("monitoring", "applications", "endpoints", 1500, 4, 16384, 200000, 443),
    ("rmm", "applications", "endpoints", 800, 4, 8192, 100000, 443),
    ("helpdesk", "applications", "customers", 8, 4, 8192, 100000, 443),
    ("identity", "applications", "endpoints", 2000, 4, 8192, 100000, 443),
    ("dns", "applications", "sites", 16, 2, 4096, 40000, 53),
    ("inventory-db", "database", "offices", 40, 8, 32768, 500000, 5432),
    ("backup", "backup", "offices", 24, 4, 16384, 1000000, 443),
)
TIER_1 = {"monitoring", "rmm", "identity", "dns"}
# Fields followed, in order, when resolving the site an object belongs to.
SITE_FIELDS = ("site", "scope_site", "device", "virtual_machine", "rack", "location",
               "assigned_object", "termination", "circuit", "module", "module_bay",
               "power_panel", "power_port", "interface", "object")
# Kinds that must name the owning customer at a managed office.
TENANT_BEARING = {"site", "rack", "location", "device", "vlan", "prefix", "ip_address",
                  "wireless_lan", "vlan_group", "circuit"}


def _pods(staff):
    """Authored pod layout: ordered (suffix, installed desk positions)."""
    return [(f"pod-{n+1:02}", min(OFFICE_DESKS, staff - OFFICE_DESKS*n))
            for n in range(math.ceil(staff / OFFICE_DESKS))]


def _zones(item):
    result = {"reception": dict(managed=RECEPTION_MANAGED, guest=RECEPTION_GUEST)}
    result.update({suffix: dict(managed=DEVICES_PER_DESK*desks, guest=0)
                   for suffix, desks in _pods(item["staff"])})
    return result


def _radios(zone):
    return max(1, (zone["managed"] + zone["guest"] + 31) // 32)


def _peak(item):
    return OFFICE_MBPS_PER_DESK * item["staff"] + OFFICE_BASE_MBPS


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "msp":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    namespace, reserve = recipe.get("namespace"), recipe.get("reserve_fraction")
    tiers, customers = recipe.get("wan_tiers_mbps"), recipe.get("customers")
    if (not isinstance(customers, list) or not 1 <= len(customers) <= MAX_CUSTOMERS or
            not isinstance(namespace, str) or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        report("msp-recipe", "plan", "MSP validation requires 1–24 bounded managed customer accounts and a bounded reserve.")
        return findings
    keys, codes = set(), set()
    for entry in customers:
        key = entry.get("key") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or not isinstance(key, str) or key in keys or
                not re.fullmatch(CUSTOMER_KEY, key) or key.replace("-", "") in codes or
                type(entry.get("offices")) is not int or not 1 <= entry["offices"] <= MAX_OFFICES or
                type(entry.get("staff")) is not int or not MIN_STAFF <= entry["staff"] <= MAX_STAFF):
            report("msp-recipe", "plan", "Each managed customer needs a unique hyphen-separated lowercase key of at "
                                         "most 20 characters, also distinct with hyphens removed, 1–4 offices and "
                                         "4–48 installed staff desks.")
            return findings
        keys.add(key)
        codes.add(key.replace("-", ""))
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        report("msp-recipe", "plan", "WAN tiers must be increasing integer Mbps commitments ending in the 1 Gbps handoff limit.")
        return findings
    customers = sorted(customers, key=lambda entry: entry["key"])
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

    # A purchased circuit reaches its site only through its own A termination.
    circuit_site = {}
    for key, obj in objects.items():
        if (obj["kind"] == "circuit_termination" and obj["attrs"].get("term_side") == "A"
                and kind_of(refs(key).get("termination")) == "site"):
            circuit_site.setdefault(refs(key).get("circuit"), refs(key)["termination"])

    def site_of(key):
        seen = set()
        while isinstance(key, str) and key in objects and key not in seen:
            if kind_of(key) == "site":
                return key
            seen.add(key)
            key = (circuit_site.get(key) if kind_of(key) == "circuit" else
                   next((refs(key)[field] for field in SITE_FIELDS
                         if isinstance(refs(key).get(field), str)), None))
        return None

    premises = [(f"off-{item['key']}-{n+1:02}", item)
                for item in customers for n in range(item["offices"])]
    owner_of = {f"site/{sid}": f"tenant/{item['key']}" for sid, item in premises}
    expected_sites = set(owner_of) | {"site/noc-01"}
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != expected_sites:
        report("msp-site-inventory", "plan", "The estate requires exactly one operations site and one site per "
                                             "requested managed customer office.")

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (address_pool.version != 4 or not 8 <= address_pool.prefixlen <= 16 or not address_pool.is_private or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // 65536
                    for slot in allocations.values()) or len(set(allocations.values())) != len(allocations)):
            raise ValueError("invalid allocation")
    except (ValueError, TypeError, AttributeError):
        report("msp-address-allocation", "plan", "Every MSP site needs a distinct /16 reservation inside the private pool.")
        return findings

    managed_peak = sum(_peak(item) for _, item in premises)
    access_alias = selected_alias(recipe, "access")
    port_names = catalog.get(access_alias, {}).get("access_ports", [])
    capacity = int(Decimal(len(port_names)) * usable_fraction)

    # --- tenancy: one account per customer, grouped, and nothing shared --------
    group = "tenant-group/customers"
    if (kind_of(group) != "tenant_group" or attrs(group).get("name") != f"{namespace} customers"):
        report("msp-tenant-group", group, "Managed accounts need one tenant group named for the provider's customers.")
    customer_tenants = {f"tenant/{item['key']}" for item in customers}
    actual_tenants = {key for key, obj in objects.items() if obj["kind"] == "tenant"}
    if actual_tenants != customer_tenants | {"tenant"}:
        report("msp-tenant-inventory", "plan", "The estate holds exactly the provider's own tenant and one tenant "
                                               "per requested managed customer.")
    for tenant in sorted(customer_tenants):
        if kind_of(tenant) != "tenant" or refs(tenant).get("group") != group:
            report("msp-tenant-group", tenant, "Each managed customer must be its own tenant inside the customer group.")
    # Every customer segment, including the WAN attachment, holds its own unique
    # routing context. A shared or borrowed one would silently pool address space.
    expected_vrfs = set()
    for item in customers:
        guest = isinstance(item.get("wireless"), dict) and any(
            isinstance(zone, dict) and zone.get("guest") for zone in item["wireless"].values())
        for role in NETWORK_OFFSETS:
            if role == "guest" and not guest:
                continue
            vrf = f"vrf/customer/{item['key']}/{role}"
            expected_vrfs.add(vrf)
            if (kind_of(vrf) != "vrf" or refs(vrf).get("tenant") != f"tenant/{item['key']}"
                    or attrs(vrf).get("enforce_unique") is not True):
                report("msp-tenant-isolation", vrf, "Each managed segment needs its own uniqueness-enforcing routing "
                                                    "context owned by that customer alone.")
    stray = {key for key in objects if key.startswith("vrf/customer/")} - expected_vrfs
    for key in sorted(stray):
        report("msp-tenant-isolation", key, "A customer routing context exists for no requested managed segment.")

    # WLAN groups reach a site only through the WLANs inside them, so derive
    # their owner from actual membership before comparing the group hierarchy.
    group_owner = {}
    for key, obj in objects.items():
        if obj["kind"] != "wireless_lan":
            continue
        group, holder = refs(key).get("group"), owner_of.get(site_of(key))
        if not isinstance(group, str) or holder is None:
            continue
        if group_owner.setdefault(group, holder) != holder:
            report("msp-tenant-isolation", group, "One WLAN group must not scope two customers' wireless services.")

    owners = {}

    def owner(key):
        """The managed customer a record belongs to, or None for shared records."""
        if not isinstance(key, str) or key not in objects:
            return None
        if key not in owners:
            owners[key] = None  # Break reference cycles while resolving.
            if key in customer_tenants:
                owners[key] = key
            elif key.startswith("vrf/customer/"):
                candidate = f"tenant/{key.split('/')[2]}"
                owners[key] = candidate if candidate in customer_tenants else None
            elif kind_of(key) == "wireless_lan_group":
                owners[key] = group_owner.get(key)
            else:
                owners[key] = owner_of.get(site_of(key))
        return owners[key]

    for key, obj in objects.items():
        held = owner(key)
        # Any reference that reaches a managed customer must reach this record's
        # own customer: interface bonds, group hierarchies and tenancy alike.
        named = {held} if held else set()
        for value in refs(key).values():
            for target in value if isinstance(value, list) else [value]:
                if reached := owner(target):
                    named.add(reached)
        if len(named) > 1:
            report("msp-tenant-isolation", key, "No record may join two managed customers or borrow another "
                                                "customer's tenant, routing context or equipment.")
        if site_of(key) == "site/noc-01" and refs(key).get("tenant") in customer_tenants:
            report("msp-tenant-isolation", key, "The provider's own operations site carries no customer tenancy.")
        if held is not None and obj["kind"] in TENANT_BEARING and refs(key).get("tenant") != held:
            report("msp-tenant-ownership", key, "Every record at a managed office is owned by that office's customer tenant.")
        if obj["kind"] == "cable":
            ends = {site_of(refs(key).get(side)) for side in ("a", "b")}
            if len(ends) != 1 or None in ends:
                report("msp-tenant-isolation", key, "Every cable stays inside one site; no modeled link joins two customers.")

    # --- managed offices -------------------------------------------------------
    for sid, item in premises:
        site = f"site/{sid}"
        tenant = owner_of[site]
        if attrs(site).get("status") != "active":
            report("msp-site-status", site, "Requested managed office must be active.")
        zone_budgets = item.get("wireless")
        defaults = _zones(item)
        if (not isinstance(zone_budgets, dict) or set(zone_budgets) != set(defaults) or
                any(not isinstance(value, dict) or set(value) != {"managed", "guest"} or
                    any(type(n) is not int or not 0 <= n <= 128 for n in value.values()) or
                    sum(value.values()) > 128 for value in zone_budgets.values()) or
                sum(value["guest"] for value in zone_budgets.values()) > GUEST_CAPACITY):
            report("msp-wireless-demand", site, "Frozen wireless demand needs exactly this office's real zones with "
                                                "bounded managed/guest budgets inside its guest /24.")
            zone_budgets = defaults
        guest = bool(sum(value["guest"] for value in zone_budgets.values()))
        network_offsets = {name: offset for name, offset in NETWORK_OFFSETS.items()
                           if guest or name != "guest"}
        if not guest and any(key in objects for key in (f"vlan/{sid}/guest", f"prefix/{sid}/guest")):
            report("msp-segment-unrequested", site, "An office without visitor wireless demand has no guest segment.")

        # --- authored ground-floor geometry from the permanent pod ledger ------
        pods = _pods(item["staff"])
        ledger = plan.get("reservations", {}).get(f"office-pods/{sid}")
        if (not isinstance(ledger, dict) or set(ledger) != {suffix for suffix, _ in pods} or
                sorted(ledger.values()) != list(range(len(pods)))):
            report("msp-pod-allocation", site, "Every staff pod needs one unique permanent ground-floor position; "
                                                "growth appends positions and never renumbers.")
            continue
        reception = f"location/{sid}/reception"
        rooms = {reception: ("reception", [6, 6, 0], None)}
        pod_rooms = []
        for suffix, desks in pods:
            key = f"location/{sid}/{suffix}"
            rooms[key] = ("staff_office", [8 + 12*ledger[suffix], 18, 0], desks)
            pod_rooms.append(key)
        closet = f"location/{sid}"
        actual_rooms = {key for key in children[("site", site)] if kind_of(key) == "location" and
                        meta(key).get("space_type") in {"reception", "staff_office"}}
        if actual_rooms != set(rooms):
            report("msp-room-inventory", site, "Reception and staff pods must match the authored single-floor office layout exactly.")
        for room, (space_type, origin, desks) in rooms.items():
            if (kind_of(room) != "location" or meta(room).get("space_type") != space_type or
                    meta(room).get("floor") != 1 or meta(room).get("position_m") != origin or
                    refs(room).get("parent") != f"location/{sid}/floor-01" or attrs(room).get("status") != "active" or
                    (desks is not None and meta(room).get("capacity", {}).get("workstations") != desks)):
                report("msp-room-placement", room, "Managed-office rooms must occupy their fixed active ground-floor "
                                                    "positions with their installed desk capacity.")
        if (kind_of(closet) != "location" or meta(closet).get("space_type") != "equipment_room" or
                meta(closet).get("floor") != 1 or meta(closet).get("position_m") != [24, 18, 0]):
            report("msp-closet-inventory", site, "Each managed office needs its permanent ground-floor equipment room.")

        # --- authored endpoint inventory ---------------------------------------
        expected = {}

        def endpoint(label, role, network, room, ordinal):
            expected[f"device/{sid}/{label}"] = (role, network, room, ordinal)

        def radios(zone, label, room):
            for ordinal in range(1, _radios(zone_budgets[zone]) + 1):
                endpoint(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, ordinal)

        for index in range(item["staff"]):
            pod, desk = divmod(index, OFFICE_DESKS)
            endpoint(f"desk-{index+1:03}", "workstation", "staff", pod_rooms[pod], desk + 1)
        radios("reception", "ap-reception", reception)
        endpoint("cam-reception", "camera", "security", reception, 1)
        for index, (suffix, _) in enumerate(pods):
            radios(suffix, f"ap-{suffix}", pod_rooms[index])
            endpoint(f"cam-{suffix}", "camera", "security", pod_rooms[index], 1)

        devices = [key for key in children[("site", site)] if kind_of(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        actual = {key for key in devices if meta(key).get("endpoint") or refs(key).get("role") in
                  {"role/workstation", "role/ap", "role/camera"}}
        if actual != set(expected):
            report("msp-endpoint-inventory", site, "Every requested staff workstation, coverage radio and camera "
                                                   "must match the authored office demand.")
        counted = Counter(role for role, _, _, _ in expected.values())
        for role, wanted in counted.items():
            if len(roles[f"role/{role}"]) != wanted:
                report("msp-endpoint-demand", site, f"Office demand requires {wanted} {role} devices; found {len(roles[f'role/{role}'])}.")
        declared = plan.get("contracts", [])
        contracts = [c for c in declared if isinstance(c, dict) and c.get("site") == site
                     and c.get("kind") == "office"] if isinstance(declared, list) else []
        if len(contracts) != 1:
            report("msp-contract-inventory", site, "Each managed office needs exactly one explanatory contract.")
        else:
            wanted = dict(staff_desks=item["staff"], office_pods=len(pods), workstations=item["staff"],
                          aps=counted["ap"], cameras=counted["camera"],
                          managed_clients=sum(z["managed"] for z in zone_budgets.values()),
                          guest_clients=sum(z["guest"] for z in zone_budgets.values()),
                          peak_mbps=_peak(item))
            if (any(contracts[0].get("demand", {}).get(field) != value for field, value in wanted.items()) or
                    contracts[0].get("managed_customer") != item["key"]):
                report("msp-demand-report", site, "Reported office demand must follow the authored pod, radio, camera "
                                                   "and peak policy and name its actual managed customer.")

        switch_counts, office_endpoints = Counter(), set()
        for device, (role, network, room, ordinal) in expected.items():
            office_endpoints.add(device)
            if (kind_of(device) != "device" or attrs(device).get("status") != "active" or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    not meta(device).get("endpoint") or meta(device).get("network") != network):
                report("msp-endpoint-placement", device, "Required endpoint identity, active status, segment and room "
                                                          "must match the managed-office layout.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind_of(peer) != "interface" or refs(switch).get("role") != "role/access" or
                    attrs(switch).get("status") != "active" or refs(switch).get("location") != closet or
                    refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("msp-endpoint-path", device, "Endpoint needs its own connected enabled VLAN path to an active "
                                                     "access switch in the office equipment room.")
            if role == "ap" and attrs(peer).get("name") not in port_names:
                # PoE reachability is the actual copper path into a catalog
                # access port; the shared PoE analyser checks the supply budget.
                report("msp-ap-power", device, "Coverage radio must reach a catalog PoE access port on its serving switch.")
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
            route = math.ceil(sum(abs(a-b) for a, b in zip(mount, [24, 18, 0]))+10)
            position = meta(device).get("placement", {})
            if (position.get("room") != room or position.get("floor") != 1 or
                    position.get("cable_origin") != closet or position.get("position_m") != mount or
                    path_lengths.get(port) != route or not 0 < route <= 80):
                report("msp-endpoint-route", device, "Each endpoint ordinal must retain its fixed mount and actual "
                                                      "copper route inside the 80 m ceiling.")
            address = refs(device).get("primary_ip4")
            if (kind_of(address) != "ip_address" or attrs(address).get("status") != "active" or
                    refs(address).get("assigned_object") != port or
                    refs(address).get("vrf") != f"vrf/customer/{item['key']}/{network}"):
                report("msp-endpoint-address", device, "Installed endpoint requires its active primary address on eth0 "
                                                        "in its own customer's segment routing context.")

        # --- room-local access pairs -------------------------------------------
        scope = f"access-endpoints/{sid}/{closet}"
        slots = plan.get("reservations", {}).get(scope)
        if (not capacity or not isinstance(slots, dict) or set(slots) != office_endpoints or
                any(type(n) is not int or not 0 <= n < 38*capacity for n in slots.values()) or
                len(set(slots.values())) != len(slots)):
            report("msp-access-allocation", site, "Each office endpoint needs one unique bounded permanent access-port slot.")
        else:
            count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity)))
            switches = {f"device/{sid}/access-{n+1:02}" for n in range(count)}
            if set(roles["role/access"]) != switches:
                report("msp-access-inventory", site, "The office equipment room needs complete access pairs through its "
                                                      "last permanent slot, retaining copper and PoE headroom.")
            for device, slot in slots.items():
                pair, offset = divmod(slot, 2*capacity)
                switch = f"device/{sid}/access-{2*pair+offset % 2+1:02}"
                if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port_names[offset//2]}":
                    report("msp-access-allocation", device, "Actual endpoint path must match its reserved switch and catalog copper port.")
        if len(roles["role/access"]) > 38:
            report("msp-access-capacity", site, "Access-switch attachments exceed the finite distribution port budget.")
        for device in roles["role/access"]:
            if meta(device).get("hardware") != access_alias or switch_counts[device] > capacity:
                report("msp-access-capacity", device, "Actual attached endpoints must fit this catalog access switch after reserve.")

        # --- forwarding, gateways and addressing --------------------------------
        required_vlans = {f"vlan/{sid}/{name}" for name in network_offsets if name != "wan"}
        if len(roles["role/distribution"]) != 2 or len(roles["role/wan-edge"]) != 2:
            report("msp-core-inventory", site, "Managed office requires two distribution switches and two carrier edge devices.")
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
                    report("msp-uplink-path", device, "Access and carrier edges require two connected active "
                                                       "distribution uplinks carrying every office segment.")
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
                report("msp-gateway-inventory", vlan, "Every managed-office segment needs two active addressed distribution gateway SVIs.")
        if sid not in allocations:
            report("msp-address-allocation", site, "Requested managed office has no persistent reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid]*65536, 16))
            for network, offset in network_offsets.items():
                prefix = f"prefix/{sid}/{network}"
                wanted = ip_network((int(container.network_address) + offset*256, 24))
                if (attrs(prefix).get("prefix") != str(wanted) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or
                        refs(prefix).get("vrf") != f"vrf/customer/{item['key']}/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(offset+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site):
                    report("msp-prefix-policy", prefix, "Office role /24 must retain its fixed offset, site VLAN and "
                                                         "own-customer VRF inside the reserved /16.")

        # --- the operating relationship, on records that already carry it -------
        assignment = f"contact-assignment/{site}"
        desk = f"contact/operations/{tenant}"
        if (kind_of(assignment) != "contact_assignment" or refs(assignment).get("contact") != desk or
                refs(assignment).get("role") != "contact-role/operations" or kind_of(desk) != "contact" or
                "contact-group/operations" not in refs(desk).get("groups", [])):
            report("msp-managed-by", site, "A managed office must escalate to the provider's own technical desk for "
                                            "that customer, in the provider's operations group.")
        for device in roles["role/access"] + roles["role/distribution"] + roles["role/wan-edge"]:
            if refs(f"contact-assignment/{device}").get("contact") != desk:
                report("msp-managed-by", device, "Operated equipment must carry the provider's technical desk for its "
                                                  "actual owning customer.")
        comments = attrs(site).get("comments", "")
        segment = attrs(f"prefix/{sid}/management").get("prefix")
        if (not isinstance(comments, str) or not segment or segment not in comments or
                "managed-network contract" not in comments or
                "No service-level commitment, remote-access path or ticketing workflow" not in comments):
            report("msp-managed-by", site, "The office record must state the operating relationship, its management "
                                            "segment and the limits that relationship does not assert.")

        # --- carrier attachments -------------------------------------------------
        circuits = defaultdict(list)
        for term in children[("termination", site)]:
            if kind_of(term) == "circuit_termination":
                circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
        if set(circuits) != {"provider/a", "provider/b"}:
            report("msp-wan-inventory", site, "Managed-office carrier attachments must belong to the two declared providers.")
        carrier_edges = set()
        for side in ("a", "b"):
            provider = f"provider/{side}"
            wanted_rate = next((rate for rate in tiers if rate >= (50 if side == "a" else 100) and
                                Decimal(rate)*usable_fraction >= _peak(item)), None)
            if len(circuits[provider]) != 1:
                report("msp-wan-inventory", site, "Each managed office requires exactly one independent attachment to each modeled carrier.")
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
                    report("msp-wan-capacity", circuit, "Managed commitment and active 1 Gbps edge handoff must cover "
                                                         "the office peak after reserve.")
        if len(carrier_edges) != 2 or None in carrier_edges:
            report("msp-wan-diversity", site, "The two office carriers must terminate on different active edge devices.")

        infrastructure = [key for key in devices if key not in expected and
                          refs(key).get("device_type") != "hardware/wall-outlet"]
        for device in infrastructure:
            if refs(device).get("role") not in {"role/distribution", "role/wan-edge", "role/access",
                                                "role/management", "role/console-server", "role/pdu",
                                                "role/patch-panel"}:
                report("msp-equipment-role", device, "Managed-office infrastructure must serve an explicit network, "
                                                      "management, serial, patching or power role.")
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind_of(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    room != closet or attrs(rack).get("status") != "active"):
                report("msp-equipment-placement", device, "Managed-office infrastructure must occupy an active rack in "
                                                           "the office equipment room.")
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of,
                                       poe_watts=poe_watts, optics_watts=optics_watts))

    # --- independently sized shared managed services ---------------------------
    endpoints = 0
    for _, item in premises:
        zone_budgets = item["wireless"] if isinstance(item.get("wireless"), dict) else _zones(item)
        radios = sum(_radios(zone) for zone in zone_budgets.values()
                     if isinstance(zone, dict) and {"managed", "guest"} <= zone.keys())
        endpoints += item["staff"] + radios + 1 + len(_pods(item["staff"]))
    totals = dict(customers=len(customers), offices=len(premises),
                  sites=len(premises) + 1, endpoints=endpoints)
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
    findings.extend(validate_resolved(plan, catalog, sites={"site/noc-01"}, workloads=services,
                    peak=managed_peak, reserve=reserve, strict_sites=False,
                    network_offsets=DC_NETWORK_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings
