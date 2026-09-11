"""Independent care demand, clinical segmentation and physical-path checks.

Recipe policy is deliberately restated here. The hospital builder, placement
helpers and emitted contracts are not sources of validation obligations.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_interface, ip_network
import math
import re

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics


NETWORK_OFFSETS = {"management": 0, "clinical": 1, "medical": 2, "wireless": 3,
                   "security": 4, "applications": 5, "database": 6, "backup": 7,
                   "wan": 8, "storage": 9, "staff": 10, "imaging": 11, "guest": 12}
CAMPUS_NETWORKS = ("management", "clinical", "medical", "wireless", "security", "staff", "imaging")
SERVICE_POLICY = (
    ("identity", "workstations", 250, 4, 8192, 100000, 443),
    ("dns", "facilities", 16, 2, 4096, 40000, 53),
    ("clinical-records", "clinical_workstations", 100, 8, 16384, 200000, 443),
    ("imaging-archive", "imaging_rooms", 16, 8, 32768, 2000000, 11112),
    ("monitoring", "endpoints", 500, 4, 16384, 200000, 443),
)
METROS = {"Chicago": ("IL", "Illinois", "America/Chicago"),
          "Detroit": ("MI", "Michigan", "America/Detroit"),
          "Cleveland": ("OH", "Ohio", "America/New_York"),
          "Milwaukee": ("WI", "Wisconsin", "America/Chicago")}


def _recipe(recipe):
    """Bound untrusted saved demand before expanding any expected inventory."""
    hospitals, clinics = recipe.get("hospitals"), recipe.get("clinics")
    reserve, tiers = recipe.get("reserve_fraction"), recipe.get("wan_tiers_mbps")
    if (not isinstance(hospitals, list) or not 1 <= len(hospitals) <= 8 or
            not isinstance(clinics, list) or len(clinics) > 64 or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve)):
        return None
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            sorted(set(tiers)) != tiers or tiers[-1] != 1000):
        return None

    def bounded(item, fields):
        return (isinstance(item, dict) and isinstance(item.get("key"), str) and
                re.fullmatch(r"[a-z][a-z0-9-]{0,19}", item["key"]) is not None and
                all(type(item.get(field)) is int and low <= item[field] <= high for field, low, high in fields))

    facilities = []
    for category, entries in (("hospital", hospitals), ("clinic", clinics)):
        keys = set()
        for item in entries:
            fields = (("administrative_desks", 0, 48 if category == "hospital" else 24),
                      ("imaging_rooms", 0, 8 if category == "hospital" else 4), ("wan_peak_mbps", 1, 800))
            if category == "clinic":
                fields += (("exam_rooms", 1, 24),)
            if not bounded(item, fields) or item["key"].replace("-", "") in keys:
                return None
            keys.add(item["key"].replace("-", ""))
            if Decimal(item["wan_peak_mbps"]) > 1000 * (1 - Decimal(str(reserve))):
                return None
            if category == "hospital":
                wards = item.get("wards")
                if not isinstance(wards, list) or not 1 <= len(wards) <= 7:
                    return None
                ward_keys = set()
                for ward in wards:
                    if not bounded(ward, (("beds", 4, 16), ("clinical_desks", 2, 8))) or ward["key"].replace("-", "") in ward_keys:
                        return None
                    ward_keys.add(ward["key"].replace("-", ""))
            zones = {f"ward-{ward['key']}" for ward in item.get("wards", [])}
            zones |= {f"exam-{n+1:03}" for n in range((item.get("exam_rooms",0)+3)//4)}
            zones |= {f"admin-{n+1:02}" for n in range((item["administrative_desks"]+11)//12)}
            zones |= {f"imaging-{n+1:02}" for n in range(item["imaging_rooms"])}
            zones.add("reception")
            wireless = item.get("wireless")
            if (not isinstance(wireless, dict) or set(wireless) != zones or
                    any(not isinstance(v, dict) or set(v) != {"managed", "guest"} or
                        any(type(n) is not int or not 0 <= n <= 128 for n in v.values()) or sum(v.values()) > 128
                        for v in wireless.values()) or sum(v["guest"] for v in wireless.values()) > 4084):
                return None
            facilities.append((category, item))
    if sum(item["wan_peak_mbps"] for _, item in facilities) > 16000:
        return None
    return facilities


def _demand(category, item):
    wards = item.get("wards", []) if category == "hospital" else []
    beds = sum(ward["beds"] for ward in wards)
    clinical = sum(ward["clinical_desks"] for ward in wards) + item.get("exam_rooms", 0) + item["imaging_rooms"]
    floors = len(wards) + 1 if category == "hospital" else (item["exam_rooms"] + 7) // 8
    aps = sum(max(2 if key.startswith("ward-") else 1, (v["managed"]+v["guest"]+31)//32)
              for key,v in item["wireless"].items())
    workstations = clinical + item["administrative_desks"]
    return dict(bed_stations=beds, wards=len(wards), nurse_workstations=sum(w["clinical_desks"] for w in wards),
                exam_rooms=item.get("exam_rooms", 0), imaging_rooms=item["imaging_rooms"],
                clinical_workstations=clinical, administrative_desks=item["administrative_desks"], workstations=workstations,
                medical_devices=beds, imaging_devices=item["imaging_rooms"], aps=aps, cameras=2 * floors, floors=floors,
                managed_clients=sum(v["managed"] for v in item["wireless"].values()),
                guest_clients=sum(v["guest"] for v in item["wireless"].values()),
                endpoints=workstations + beds + item["imaging_rooms"] + aps + 2 * floors,
                peak_mbps=item["wan_peak_mbps"])


def _layout(category, item, sid, reservations):
    """Reconstruct fixed room obligations from demand and bounded ward slots."""
    rooms, endpoints, closets = {}, {}, {1: f"location/{sid}"}

    def room(suffix, function, floor, x, y, capacity=None):
        key = f"location/{sid}" + (f"/{suffix}" if suffix else "")
        rooms[key] = dict(space_type=function, floor=floor, position_m=[x, y, max(0, floor - 1) * 4],
                          parent=None if function == "building" else f"location/{sid}/building" if function == "floor" else f"location/{sid}/floor-{floor:02}")
        if capacity:
            rooms[key]["capacity"] = capacity
        return key

    def floor(number):
        room(f"floor-{number:02}", "floor", number, 0, 0)
        if number > 1:
            closets[number] = room(f"idf-{number:02}", "equipment_room", number, 24, 18)

    def endpoint(label, cohort, location, network, role="workstation", ordinal=1):
        endpoints[f"device/{sid}/{label}"] = (cohort, location, network, role, ordinal)

    def aps(zone, label, cohort, location):
        value = item["wireless"][zone]
        for ordinal in range(1, max(1, (value["managed"]+value["guest"]+31)//32)+1):
            endpoint(label if ordinal == 1 else f"{label}-{ordinal:02}", cohort, location, "wireless", "ap", ordinal)

    room("building", "building", 0, 0, 0)
    floor(1)
    room("", "equipment_room", 1, 24, 18)
    reception = room("reception", "reception", 1, 24, 36)
    aps("reception", "ap-reception-01", "reception-ap", reception)
    for number, side in enumerate(("a", "b"), 1):
        endpoint(f"camera-ground-{side}", "corridor-camera", reception, "security", "camera", number)
    if category == "hospital":
        slots = reservations.get(f"healthcare-wards/{sid}") if isinstance(reservations, dict) else None
        if (not isinstance(slots, dict) or set(slots) != {ward["key"] for ward in item["wards"]} or
                any(type(slot) is not int or not 0 <= slot < 7 for slot in slots.values()) or len(set(slots.values())) != len(slots)):
            return None
        for ward in item["wards"]:
            key, level = ward["key"], slots[ward["key"]] + 2
            floor(level)
            patients = [room(f"ward-{key}/patient-{n+1:02}", "patient_room", level, 6 + 12 * (n % 4), 4 + 24 * (n // 4), {"bed_stations": 2})
                        for n in range((ward["beds"] + 1) // 2)]
            nurse = room(f"ward-{key}/nurse-station", "nurse_station", level, 6, 44, {"workstations": 8})
            corridor = room(f"ward-{key}/corridor", "corridor", level, 24, 40)
            for n in range(ward["beds"]):
                endpoint(f"monitor-{key}-{n+1:03}", "bedside-monitor", patients[n // 2], "medical", "medical-device", n % 2 + 1)
            for n in range(ward["clinical_desks"]):
                endpoint(f"nurse-{key}-{n+1:03}", "nurse-workstation", nurse, "clinical", ordinal=n + 1)
            value = item["wireless"][f"ward-{key}"]
            for n in range(1, max(2, (value["managed"]+value["guest"]+31)//32)+1):
                endpoint(f"ap-ward-{key}-{n:02}", "ward-ap", corridor, "wireless", "ap", n)
            for n, side in enumerate(("a", "b"), 1):
                endpoint(f"camera-ward-{key}-{side}", "corridor-camera", corridor, "security", "camera", n)
    else:
        corridors = {}
        for level in range(1, (item["exam_rooms"] + 7) // 8 + 1):
            floor(level)
            corridors[level] = room(f"corridor-floor-{level:02}", "corridor", level, 24, 40)
            if level > 1:
                for n, side in enumerate(("a", "b"), 1):
                    endpoint(f"camera-floor-{level:02}-{side}", "corridor-camera", corridors[level], "security", "camera", n)
        for n in range(item["exam_rooms"]):
            location = room(f"exam-{n+1:03}", "exam_room", n // 8 + 1, 6 + 12 * (n % 4), 4 + 24 * ((n % 8) // 4), {"workstations": 1})
            endpoint(f"exam-{n+1:03}", "exam-workstation", location, "clinical")
        for n in range((item["exam_rooms"] + 3) // 4):
            label, value = f"ap-exam-{n+1:03}", item["wireless"][f"exam-{n+1:03}"]
            for ordinal in range(1, max(1, (value["managed"]+value["guest"]+31)//32)+1):
                endpoint(label if ordinal == 1 else f"{label}-{ordinal:02}", "exam-ap", corridors[n//2+1],
                         "wireless", "ap", 2*(ordinal-1)+n%2+1)
    for n in range((item["administrative_desks"] + 11) // 12):
        office = room(f"admin-{n+1:02}", "office", 1, 6 + 12 * n, 60 if category == "clinic" else 50, {"workstations": 12})
        aps(f"admin-{n+1:02}", f"ap-admin-{n+1:02}", "office-ap", office)
    for n in range(item["administrative_desks"]):
        endpoint(f"admin-{n+1:03}", "administration", f"location/{sid}/admin-{n//12+1:02}", "staff", ordinal=n % 12 + 1)
    for n in range(item["imaging_rooms"]):
        imaging = room(f"imaging-{n+1:02}", "imaging_room", 1, 6 + 12 * (n % 4), 44 if category == "clinic" else 4 + 24 * (n // 4), {"modalities": 1, "workstations": 1})
        endpoint(f"imaging-{n+1:02}", "imaging-modality", imaging, "imaging", "imaging-device")
        endpoint(f"diagnostic-{n+1:02}", "diagnostic-workstation", imaging, "clinical", ordinal=2)
        aps(f"imaging-{n+1:02}", f"ap-imaging-{n+1:02}", "imaging-ap", imaging)
    return rooms, endpoints, closets


def _position(origin, closet, role, ordinal, cohort):
    height = 2.8 if role == "ap" else 2.5 if role == "camera" else 0.8
    if role == "ap":
        row, lane = divmod(ordinal-1, 2)
        x = 4 + (8 if cohort in {"ward-ap", "exam-ap"} else 4)*lane
        y = 4 + (-8 if cohort == "ward-ap" else 4)*row
    elif role == "camera":
        x, y = 1 + 8 * (ordinal - 1), 0
    else:
        x, y = 1 + 1.2 * ((ordinal - 1) % 6), 1 + 1.2 * ((ordinal - 1) // 6)
    point = [origin[0] + x, origin[1] + y, origin[2] + height]
    return point, math.ceil(sum(abs(point[n] - closet[n]) for n in range(3)) + 10)


def _wan(site, item, tiers, usable, objects, children, peers, active_path, roles, report):
    attrs = lambda key: objects.get(key, {}).get("attrs", {})
    refs = lambda key: objects.get(key, {}).get("refs", {})
    kind = lambda key: objects.get(key, {}).get("kind")
    circuits, edges = defaultdict(list), set()
    for term in children[("termination", site)]:
        if kind(term) == "circuit_termination":
            circuits[refs(refs(term).get("circuit")).get("provider")].append(term)
    if set(circuits) != {"provider/a", "provider/b"}:
        report("hospital-wan-inventory", site, "Each care facility must attach to its two declared providers.")
    for side in ("a", "b"):
        provider = f"provider/{side}"
        rate = next((r for r in tiers if r >= (50 if side == "a" else 100) and Decimal(r) * usable >= item["wan_peak_mbps"]), None)
        if len(circuits[provider]) != 1:
            report("hospital-wan-inventory", site, "Each modeled provider needs exactly one site attachment.")
        for term in circuits[provider]:
            circuit, peer = refs(term).get("circuit"), peers.get(term)
            edge = refs(peer).get("device")
            edges.add(edge)
            terms = [key for key in children[("circuit", circuit)] if kind(key) == "circuit_termination"]
            remote = [key for key in terms if kind(refs(key).get("termination")) == "provider_network"]
            if (rate is None or attrs(circuit).get("commit_rate") != rate * 1000 or attrs(circuit).get("status") != "active" or
                    not active_path(term) or edge not in roles["role/wan-edge"] or attrs(edge).get("status") != "active" or
                    attrs(peer).get("type") != "1000base-t" or attrs(term).get("port_speed") != 1000000 or attrs(term).get("term_side") != "A" or
                    len(terms) != 2 or len(remote) != 1 or attrs(remote[0]).get("term_side") != "Z" or
                    refs(refs(remote[0]).get("termination")).get("provider") != provider):
                report("hospital-wan-capacity", circuit, "Active provider commitment and physical 1 Gbps handoff must cover declared facility peak after reserve.")
    if len(edges) != 2 or None in edges:
        report("hospital-wan-diversity", site, "The two care-facility carriers must use distinct active local edge devices.")


def _wireless(site, expected, recipe, guest, objects, children, peers, active_path, report):
    attrs = lambda key: objects.get(key, {}).get("attrs", {})
    refs = lambda key: objects.get(key, {}).get("refs", {})
    kind = lambda key: objects.get(key, {}).get("kind")
    sid = site.removeprefix("site/")
    wlan, vlan = f"wireless-lan/{sid}/staff", f"vlan/{sid}/staff"
    wlans = [wlan] + ([f"wireless-lan/{sid}/guest"] if guest else [])
    vlans = {vlan} | ({f"vlan/{sid}/guest"} if guest else set())
    actual = {key for key in children[("scope_site", site)] if kind(key) == "wireless_lan"}
    group = refs(wlan).get("group")
    if (actual != set(wlans) or attrs(wlan).get("ssid") != f"{recipe['namespace']}-staff" or attrs(wlan).get("status") != "active" or
            attrs(wlan).get("auth_type") != "wpa-enterprise" or attrs(wlan).get("auth_cipher") != "aes" or refs(wlan).get("vlan") != vlan or
            group != f"wireless-group/{sid}" or kind(group) != "wireless_lan_group" or refs(group).get("parent") != "wireless-group/staff"):
        report("hospital-wireless-lan", site, "Each care facility needs its staff WLAN and only its requested guest WLAN, with stable scoped directory identity.")
    if guest:
        key = wlans[1]
        if (attrs(key).get("ssid") != f"{recipe['namespace']}-guest" or attrs(key).get("status") != "active" or
                attrs(key).get("auth_type") != "open" or attrs(key).get("auth_cipher") not in (None, "") or
                attrs(key).get("auth_psk") not in (None, "") or refs(key).get("vlan") != f"vlan/{sid}/guest" or
                refs(key).get("tenant") != refs(site).get("tenant") or refs(key).get("group") != group):
            report("hospital-wireless-lan", key, "Requested guest WLAN needs its actual local VLAN and open-auth intent without claimed portal or RADIUS authentication.")
    for device, (_, _, _, role, _) in expected.items():
        if role != "ap":
            continue
        radio, wire = f"{device}/if/wlan0", f"{device}/if/eth0"
        peer = peers.get(wire)
        if (kind(radio) != "interface" or attrs(radio).get("enabled") is not True or attrs(radio).get("rf_role") != "ap" or
                attrs(radio).get("rf_channel") not in {"5g-36-5180-20", "5g-44-5220-20", "5g-157-5785-20"} or
                refs(radio).get("wireless_lans") != sorted(wlans) or any(f in refs(radio) for f in ("untagged_vlan", "tagged_vlans")) or
                "mode" in attrs(radio) or not active_path(wire) or
                any(not vlans <= set(refs(key).get("tagged_vlans", [])) or refs(key).get("untagged_vlan") != f"vlan/{sid}/wireless" for key in (wire, peer))):
            report("hospital-wireless-path", device, "Every actual AP needs its requested WLAN membership and active wired VLANs, separate from native AP management.")
        second = f"{device}/if/wlan1"
        if refs(second).get("wireless_lans") or refs(second).get("vrf") or children[("interface_a", second)] or children[("interface_b", second)]:
            report("hospital-wireless-scope", device, "Staff and optional guest share wlan0; the second radio must not silently add another WLAN or diagnostic network.")


def _infrastructure(site, devices, closets, roles, catalog, objects, children, peers, active_path, report):
    attrs = lambda key: objects.get(key, {}).get("attrs", {})
    refs = lambda key: objects.get(key, {}).get("refs", {})
    kind = lambda key: objects.get(key, {}).get("kind")
    mdf, vlan = closets[1], f"vlan/{site.removeprefix('site/')}/management"
    allowed = {"role/distribution", "role/wan-edge", "role/access", "role/management", "role/console-server", "role/pdu", "role/patch-panel"}
    for device in devices:
        rack, room = refs(device).get("rack"), refs(device).get("location")
        if refs(device).get("role") not in allowed:
            report("hospital-equipment-role", device, "Care-facility infrastructure needs an explicit network, management, console, patching or power role.")
        if (kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                room not in closets.values() or attrs(rack).get("status") != "active" or attrs(rack).get("u_height") != 42):
            report("hospital-equipment-placement", device, "Infrastructure must occupy an active 42U cabinet in its local floor equipment room.")
        hardware = refs(device).get("device_type", "").removeprefix("hardware/")
        model = catalog.get(hardware, {})
        management = {p["name"] for p in model.get("interfaces", []) if p.get("mgmt_only")}
        if management:
            address = refs(device).get("primary_ip4")
            primary = refs(address).get("assigned_object")
            if (kind(address) != "ip_address" or attrs(address).get("status") != "active" or refs(address).get("vrf") != "vrf/management" or
                    refs(primary).get("device") != device or kind(primary) != "interface" or not attrs(primary).get("enabled", True) or refs(primary).get("untagged_vlan") != vlan):
                report("hospital-management-address", device, "Infrastructure needs an active primary address on its own enabled management interface.")
            svi = attrs(primary).get("type") == "virtual" and refs(device).get("role") in {"role/distribution", "role/management"}
            if not svi:
                peer = peers.get(primary)
                manager = refs(peer).get("device")
                if (not active_path(primary) or attrs(primary).get("name") not in management or manager not in roles["role/management"] or
                        refs(manager).get("location") != room or attrs(manager).get("status") != "active" or refs(peer).get("untagged_vlan") != vlan):
                    report("hospital-management-path", device, "Dedicated management requires a connected active path to its own floor management switch.")
        if device in roles["role/management"]:
            upstreams = []
            for port in children[("device", device)]:
                peer, parent = peers.get(port), refs(peers.get(port)).get("device")
                if (kind(port) == "interface" and parent in roles["role/distribution"] and active_path(port) and
                        refs(port).get("tagged_vlans") == refs(peer).get("tagged_vlans") == [vlan] and
                        attrs(parent).get("status") == "active" and refs(parent).get("location") == mdf):
                    upstreams.append(parent)
            if len(upstreams) != 1:
                report("hospital-management-uplink", device, "Each management switch needs its active management-only uplink to the MDF distribution pair.")
        else:
            for specification in model.get("console_ports", []):
                if specification.get("type") != "rj-45":
                    continue
                port = f"{device}/console_port/{specification['name']}"
                peer = peers.get(port)
                server = refs(peer).get("device")
                if (kind(peer) != "console_server_port" or not active_path(port) or server not in roles["role/console-server"] or
                        refs(server).get("location") != room or attrs(server).get("status") != "active"):
                    report("hospital-console-path", device, "Required serial access must reach a connected active console server in the same equipment room.")


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    """Inspect hospital/clinic inventory using the base validator's path indexes."""
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "hospital-clinics":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    facilities = _recipe(recipe)
    if facilities is None:
        report("hospital-recipe", "plan", "Health-system demand needs bounded hospitals, wards, clinics, installed stations, imaging rooms and WAN peaks; each 1 Gbps handoff must cover peak plus reserve.")
        return findings

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def meta(key):
        return objects.get(key, {}).get("meta", {}) if isinstance(key, str) else {}

    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None

    def vlans(key):
        return set(refs(key).get("tagged_vlans", [])) | ({refs(key)["untagged_vlan"]} if refs(key).get("untagged_vlan") else set())

    def active_path(port):
        members = component_members.get(component_of.get(port), [])
        return (bool(peers.get(port)) and bool(members) and all(attrs(p).get("enabled", True) for p in members) and
                all(attrs(cable_of[p]).get("status") == "connected" for p in members if p in cable_of))

    sites = {f"site/{category}-{item['key']}" for category, item in facilities}
    if {key for key, obj in objects.items() if obj["kind"] == "site"} != sites | {"site/dc-01"}:
        report("hospital-site-inventory", "plan", "The health system needs exactly its requested care facilities and one service DC.")
    site_kinds = {f"site/{category}-{item['key']}": category for category, item in facilities} | {"site/dc-01": "dc"}
    cities, addresses = set(), set()
    ns = recipe.get("namespace", "")
    for site, category in site_kinds.items():
        address = attrs(site).get("physical_address")
        matched = re.fullmatch(r"[^\n]+\n([^,\n]+), ([^\n]+)\nUnited States", address) if isinstance(address, str) else None
        city, state_name = matched.groups() if matched else (None, None)
        state, expected_state, zone = METROS.get(city, (None, None, None))
        region = f"region/{ns}/us/{state.lower()}" if state else None
        group = f"site-group/{ns}/{category}"
        group_name = {"hospital": "Hospitals", "clinic": "Outpatient clinics", "dc": "Data centers"}[category]
        if (not state or state_name != expected_state or attrs(site).get("time_zone") != zone or refs(site).get("region") != region or
                kind(region) != "region" or refs(region).get("parent") != f"region/{ns}/us/great-lakes" or
                attrs(region).get("name") != f"{ns} {expected_state}" or
                meta(site).get("geography") != dict(country="US", state=state, city=city, synthetic=True)):
            report("hospital-geography", site, "Actual address, state region and time zone must describe the same authored health-system metro; metadata cannot override their relationships.")
        if refs(site).get("group") != group or kind(group) != "site_group" or attrs(group).get("name") != f"{ns} {group_name}":
            report("hospital-site-group", site, "The facility must belong to its canonical hospital, outpatient-clinic or service-DC group.")
        if matched:
            if address in addresses:
                report("hospital-geography", site, "Distinct health-system premises need distinct complete street addresses.")
            addresses.add(address)
            cities.add(city)
    if len(cities) != 1:
        report("hospital-geography", "plan", "All care facilities and their service DC must share one coherent authored metro.")
    try:
        pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        if (pool.version != 4 or not 8 <= pool.prefixlen <= 16 or
                not any(pool.subnet_of(ip_network(block)) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")) or
                not isinstance(allocations, dict) or
                any(type(slot) is not int or not 0 <= slot < pool.num_addresses // 65536 for slot in allocations.values()) or
                len(set(allocations.values())) != len(allocations)):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        report("hospital-address-allocation", "plan", "Care facilities need distinct /16 reservations within the private address pool.")
        return findings
    reserve = recipe["reserve_fraction"]
    usable = Decimal(1) - Decimal(str(reserve))
    capacity = int(Decimal(len(catalog.get("access", {}).get("access_ports", []))) * usable)
    if not capacity:
        report("hospital-access-capacity", "plan", "The reviewed access hardware must retain usable endpoint ports after reserve.")
        return findings
    all_demands = []
    if poe_watts is None:
        _, poe_watts = analyze_poe(plan, catalog)
    if optics_watts is None:
        _, optics_watts = analyze_optics(plan, catalog)
    for category, item in facilities:
        sid, site = f"{category}-{item['key']}", f"site/{category}-{item['key']}"
        guest = any(v["guest"] for v in item["wireless"].values())
        networks = CAMPUS_NETWORKS + (("guest",) if guest else ())
        if not guest and any(key in objects for key in (f"vlan/{sid}/guest", f"prefix/{sid}/guest")):
            report("hospital-guest-unrequested", site, "Guest segmentation requires nonzero planned guest demand at this care facility.")
        demand = _demand(category, item)
        all_demands.append(demand)
        if attrs(site).get("status") != "active" or refs(site).get("tenant") != "tenant":
            report("hospital-site-status", site, "A requested care facility must be active in the health-system tenant.")
        layout = _layout(category, item, sid, plan.get("reservations", {}))
        if layout is None:
            report("hospital-ward-allocation", site, "Ward floors need complete distinct permanent slots zero through six; malformed ledgers cannot erase care demand.")
            continue
        rooms, expected, closets = layout
        contracts = plan.get("contracts", [])
        contracts = [c for c in contracts if isinstance(c, dict) and c.get("site") == site and c.get("kind") == category] if isinstance(contracts, list) else []
        if len(contracts) != 1:
            report("hospital-contract-inventory", site, "Each care facility needs one explanatory contract; missing reports do not remove graph obligations.")
        elif contracts[0].get("demand") != demand or contracts[0].get("endpoint_count") != demand["endpoints"]:
            report("hospital-demand-report", site, "Reported beds, installed stations, modalities, APs and cameras must follow care demand without inventing people.")
        actual_rooms = {key for key in children[("site", site)] if kind(key) == "location"}
        if actual_rooms != set(rooms):
            report("hospital-room-inventory", site, "Care, circulation, administration and equipment rooms must exactly follow the fixed facility layout.")
        for room, specification in rooms.items():
            parent = specification["parent"]
            if (kind(room) != "location" or attrs(room).get("status") != "active" or refs(room).get("parent") != parent or
                    refs(room).get("site") != site or refs(room).get("tenant") != "tenant"):
                report("hospital-room-placement", room, "Each required room must be active under its own permanent floor and health-system site.")
            if any(meta(room).get(field) != value for field, value in specification.items() if field != "parent"):
                report("hospital-room-geometry", room, "Room function, floor, finite position and capacity must match the independently derived care layout.")
        devices = [key for key in children[("site", site)] if kind(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        endpoint_roles = {"role/workstation", "role/medical-device", "role/imaging-device", "role/ap", "role/camera"}
        actual = {key for key in devices if refs(key).get("role") in endpoint_roles or
                  refs(key).get("device_type") in {"hardware/endpoint", "hardware/ap"}}
        if actual != set(expected):
            report("hospital-endpoint-inventory", site, "Every requested bedside monitor, clinical/admin station, modality, AP and circulation camera must exist exactly once.")
        switch_counts, room_endpoints = Counter(), defaultdict(set)
        for device, (cohort, room, network, role, ordinal) in expected.items():
            floor = rooms[room]["floor"]
            closet = closets[floor]
            room_endpoints[closet].add(device)
            hardware = "ap" if role == "ap" else "endpoint"
            if (kind(device) != "device" or attrs(device).get("status") != "active" or refs(device).get("site") != site or
                    refs(device).get("location") != room or refs(device).get("role") != f"role/{role}" or
                    refs(device).get("device_type") != f"hardware/{hardware}" or refs(device).get("tenant") != "tenant"):
                report("hospital-endpoint-placement", device, "Required clinical identity, active role, endpoint hardware and actual care/circulation room must match demand.")
            if meta(device).get("cohort") != cohort or meta(device).get("network") != network or meta(device).get("endpoint") is not True:
                report("hospital-endpoint-description", device, "Endpoint labels must agree with independently derived clinical responsibilities and segment.")
            label = f"Reference {cohort.replace('-', ' ')}" if role in {"medical-device", "imaging-device"} else cohort.replace("-", " ").title()
            description = f"{label} in {attrs(room).get('name')} at {attrs(site).get('name')}"
            if role == "ap":
                description += "; staff WLAN on wlan0; RF coverage unverified"
            if attrs(device).get("description") != description:
                report("hospital-endpoint-description", device, "Endpoint prose must describe its actual reference role and location without adding clinical guarantees or certification claims.")
            if role == "camera" and rooms[room]["space_type"] not in {"corridor", "reception"}:
                report("hospital-camera-placement", device, "Cameras belong only in circulation or reception, never care rooms.")
            if role == "camera" and refs(device).get("location") != room:
                report("hospital-camera-placement", device, "This camera must remain in its planned circulation or reception area.")
            port = f"{device}/if/eth0"
            peer = peers.get(port)
            switch = refs(peer).get("device")
            vlan = f"vlan/{sid}/{network}"
            if (not active_path(port) or kind(peer) != "interface" or refs(switch).get("role") != "role/access" or
                    attrs(switch).get("status") != "active" or refs(switch).get("location") != closet or
                    refs(switch).get("site") != site or refs(port).get("untagged_vlan") != vlan or refs(peer).get("untagged_vlan") != vlan):
                report("hospital-endpoint-path", device, "Each endpoint needs its own connected enabled segment path to an active access switch in its serving floor closet.")
            if switch:
                switch_counts[switch] += 1
            members = component_members.get(component_of.get(port), [])
            passive = {refs(p).get("device") for p in members if kind(p) in {"front_port", "rear_port"}}
            if recipe.get("patching") == "panels":
                outlets = {p for p in passive if refs(p).get("device_type") == "hardware/wall-outlet"}
                panels = {p for p in passive if refs(p).get("device_type") == "hardware/patch-panel"}
                if (len({cable_of[p] for p in members if p in cable_of}) != 3 or len(passive) != 2 or len(outlets) != 1 or len(panels) != 1 or
                        any(refs(p).get("location") != room for p in outlets) or any(refs(p).get("location") != closet for p in panels)):
                    report("hospital-endpoint-patching", device, "Panel channels must pass through this endpoint's room outlet and its own floor-closet panel.")
            elif len(members) != 2:
                report("hospital-endpoint-patching", device, "A direct care endpoint channel requires exactly one physical cable.")
            point, route = _position(rooms[room]["position_m"], rooms[closet]["position_m"], role, ordinal, cohort)
            placement = dict(room=room, function=rooms[room]["space_type"], floor=floor, position_m=point, cable_origin=closet)
            if meta(device).get("placement") != placement or meta(device).get("access_channel_length_m") != route or path_lengths.get(port) != route or not 0 < route <= 80:
                report("hospital-endpoint-route", device, "Mounting coordinates and actual copper length must follow the fixed room geometry and local 80 m route ceiling.")
            address = refs(device).get("primary_ip4")
            try:
                valid_prefix = ip_interface(attrs(address).get("address", "")).network == ip_network(attrs(f"prefix/{sid}/{network}").get("prefix", ""))
            except (ValueError, TypeError):
                valid_prefix = False
            if (kind(address) != "ip_address" or attrs(address).get("status") != "active" or refs(address).get("assigned_object") != port or
                    refs(address).get("vrf") != f"vrf/{network}" or not valid_prefix):
                report("hospital-endpoint-address", device, "The actual primary address must belong to this endpoint's required site segment and VRF.")
        access_by_room = Counter(refs(device).get("location") for device in roles["role/access"])
        for room, members in room_endpoints.items():
            slots = plan.get("reservations", {}).get(f"access-endpoints/{sid}/{room}")
            if (not isinstance(slots, dict) or set(slots) != members or
                    any(type(n) is not int or not 0 <= n < 38*capacity for n in slots.values()) or
                    len(set(slots.values())) != len(slots)):
                report("hospital-access-allocation", room, "Each demanded care endpoint needs one unique bounded permanent local access-port slot.")
                continue
            count = max(2, 2*math.ceil((max(slots.values())+1)/(2*capacity)))
            prefix = "" if room == f"location/{sid}" else room.rsplit("/",1)[-1]+"-"
            switches = {f"device/{sid}/{prefix}access-{n+1:02}" for n in range(count)}
            actual_switches = {k for k in roles["role/access"] if refs(k).get("location") == room}
            if actual_switches != switches or access_by_room[room] != count:
                report("hospital-access-inventory", room, "Each occupied floor needs complete pairs through its last permanent slot, retaining copper and PoE headroom.")
            for device, slot in slots.items():
                pair, offset = divmod(slot, 2*capacity)
                switch = f"device/{sid}/{prefix}access-{2*pair+offset%2+1:02}"
                port = catalog["access"]["access_ports"][offset//2]
                if peers.get(f"{device}/if/eth0") != f"{switch}/if/{port}":
                    report("hospital-access-allocation", device, "Actual care endpoint must match its reserved switch and catalog copper port.")
        if len(roles["role/access"]) > 38 or any(switch_counts[key] > capacity for key in roles["role/access"]):
            report("hospital-access-capacity", site, "Actual endpoint attachments must retain per-switch reserve and fit the finite 38-switch distribution limit.")
        mdf = closets[1]
        required_vlans = {f"vlan/{sid}/{network}" for network in networks}
        if len(roles["role/distribution"]) != 2 or len(roles["role/wan-edge"]) != 2:
            report("hospital-core-inventory", site, "Every care campus requires two distribution and two carrier edge devices.")
        interconnects = set()
        for device in roles["role/distribution"]:
            for port in children[("device", device)]:
                peer, parent = peers.get(port), refs(peers.get(port)).get("device")
                if (kind(port) == "interface" and parent in roles["role/distribution"] and parent != device and active_path(port) and
                        attrs(device).get("status") == attrs(parent).get("status") == "active" and required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                    interconnects.add(tuple(sorted((port, peer))))
        if len(interconnects) != 1:
            report("hospital-distribution-interconnect", site, "The distribution pair needs one active connected peer link carrying all care-campus segments.")
        for role in ("role/access", "role/wan-edge"):
            for device in roles[role]:
                upstreams = set()
                for port in children[("device", device)]:
                    peer, parent = peers.get(port), refs(peers.get(port)).get("device")
                    if (kind(port) == "interface" and kind(peer) == "interface" and parent in roles["role/distribution"] and active_path(port) and
                            refs(parent).get("location") == mdf and attrs(device).get("status") == attrs(parent).get("status") == "active" and
                            required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                        upstreams.add(parent)
                if len(upstreams) != 2:
                    report("hospital-uplink-path", device, "Each access or carrier-edge device needs two active local distribution uplinks carrying all campus segments.")
        for role, hardware in (("role/distribution", "leaf"), ("role/wan-edge", "edge"), ("role/access", "access")):
            for device in roles[role]:
                if refs(device).get("device_type") != f"hardware/{hardware}" or (role != "role/access" and refs(device).get("location") != mdf):
                    report("hospital-core-placement", device, "Core and access equipment must retain its actual catalog hardware and correct serving closet.")
        for network in networks:
            vlan, gateways = f"vlan/{sid}/{network}", set()
            for device in roles["role/distribution"]:
                for port in children[("device", device)]:
                    if (kind(port) == "interface" and attrs(port).get("type") == "virtual" and attrs(port).get("enabled", True) and
                            attrs(device).get("status") == "active" and vlan in vlans(port) and
                            any(kind(ip) == "ip_address" and attrs(ip).get("status") == "active" and refs(ip).get("vrf") == f"vrf/{network}" for ip in children[("assigned_object", port)])):
                        gateways.add(device)
            if len(gateways) != 2:
                report("hospital-gateway-inventory", vlan, "Each clinical, medical, imaging, staff or infrastructure segment needs two active addressed distribution gateways.")
        if sid not in allocations:
            report("hospital-address-allocation", site, "Requested care facility has no persistent site reservation.")
        else:
            container = ip_network((int(pool.network_address) + allocations[sid] * 65536, 16))
            for network in networks + ("wan",):
                prefix = f"prefix/{sid}/{network}"
                wanted = str(ip_network((int(container.network_address) + NETWORK_OFFSETS[network] * 4096, 20)))
                if (attrs(prefix).get("prefix") != wanted or attrs(prefix).get("status") != "active" or refs(prefix).get("scope_site") != site or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or refs(prefix).get("vrf") != f"vrf/{network}" or
                        attrs(f"vlan/{sid}/{network}").get("vid") != 10*(NETWORK_OFFSETS[network]+1) or
                        refs(f"vlan/{sid}/{network}").get("site") != site):
                    report("hospital-prefix-policy", prefix, "Each care-campus /20 must occupy its fixed role offset within its own reserved /16, VLAN and VRF.")
        _wan(site, item, recipe["wan_tiers_mbps"], usable, objects, children, peers, active_path, roles, report)
        _wireless(site, expected, recipe, guest, objects, children, peers, active_path, report)
        infrastructure = [key for key in devices if key not in expected and refs(key).get("device_type") != "hardware/wall-outlet"]
        _infrastructure(site, infrastructure, closets, roles, catalog, objects, children, peers, active_path, report)
        findings.extend(validate_power(objects, catalog, infrastructure, children, peers, cable_of, poe_watts=poe_watts, optics_watts=optics_watts))
    counts = {field: sum(item[field] for item in all_demands) for field in ("workstations", "clinical_workstations", "imaging_rooms", "endpoints")}
    counts["facilities"] = len(facilities)
    workloads = []
    for key, metric, threshold, cpus, memory, disk, port in SERVICE_POLICY:
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        if key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        workloads.append(dict(key=key, groups=max(1, (counts[metric] + threshold - 1) // threshold), replicas=2,
            failure_domain="rack", network="applications", vcpus=cpus, memory_mb=memory, disk_mb=disk, listeners=listeners,
            criticality="tier-1" if key in {"identity", "dns", "clinical-records"} else "tier-2"))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=workloads,
        peak=sum(item["wan_peak_mbps"] for _, item in facilities), reserve=reserve, strict_sites=False, poe_watts=poe_watts, optics_watts=optics_watts,
        network_offsets={key: NETWORK_OFFSETS[key] for key in ("management", "applications", "database", "backup", "wan", "storage")}))
    return findings
