"""One university campus: academic buildings, residence halls, a library and a campus DC.

Unlike the school district's many small campuses, this profile models a single
large campus. Every building is its own site inside one authored metro; the
established grammar has no sub-site campus container, so buildings *are* sites.

Room counts, radio budgets and service thresholds are authored planning demand.
They are not enrollment, occupancy, measured traffic or a surveyed RF design.
Shared builders allocate every device, port, address, rack and cable; the
independent checks in `validate_university.py` inspect the finished graph.
"""

from copy import deepcopy
from decimal import Decimal
import ipaddress
import math
import re

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation
from .model import DesignError, World, hardware_catalog, resolve_bank_recipe, resolve_demo, selected_alias


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps", "naming", "site_names", "hardware"}
# Every campus building offers staff, student and visitor service; only academic
# buildings carry the instructional/research computing segment.
BUILDING_NETWORKS = ("management", "staff", "students", "wireless", "security", "guest")
ACADEMIC_NETWORKS = BUILDING_NETWORKS + ("research",)
NETWORKS = ("management", "staff", "students", "research", "wireless", "security",
            "applications", "database", "backup", "wan", "storage", "guest")
BUILDING_DEFAULTS = dict(classrooms=8, lab_seats=48, offices=24)
RESIDENCE_DEFAULTS = dict(rooms=120, wired_ports_per_room=1)
LIBRARY_DEFAULTS = dict(reading_seats=160, aps=8)
DEFAULT_BUILDINGS = [dict(key="science", classrooms=8, lab_seats=96, offices=24),
                     dict(key="humanities", classrooms=16, lab_seats=0, offices=40),
                     dict(key="engineering", classrooms=10, lab_seats=120, offices=32),
                     dict(key="business", classrooms=12, lab_seats=24, offices=36)]
DEFAULT_RESIDENCES = [dict(key="aspen", rooms=180), dict(key="willow", rooms=240),
                      dict(key="juniper", rooms=120)]
# Authored per-zone concurrent device budgets. These are planning envelopes for
# AP mount counts, not RF capacity, association evidence or headcounts.
LECTURE_MANAGED, LECTURE_GUEST = 28, 4
LAB_MANAGED, OFFICE_MANAGED = 24, 12
RESIDENCE_PER_ROOM, RESIDENCE_GUEST = 2, 2
# Fictional service sizing. Thresholds count installed inventory, never students.
SERVICES = (
    ("learning-portal", "applications", 4, 8192, 100000, "endpoints", 2000, 443),
    ("identity", "applications", 4, 8192, 100000, "endpoints", 1500, 443),
    ("dns", "applications", 2, 4096, 40000, "sites", 16, 53),
    # Research storage is a data service on the shared DC database segment; the
    # separate site "storage" segment belongs to the DC fabric, not to workloads.
    ("research-storage", "database", 8, 32768, 2000000, "lab_seats", 120, 2049),
    ("monitoring", "applications", 4, 16384, 200000, "endpoints", 4000, 443),
    ("backup", "backup", 4, 16384, 1000000, "sites", 8, 443),
)
TIER_1 = {"learning-portal", "identity", "dns"}
# The guest client segment is one site /22 after its two gateway SVIs.
GUEST_CAPACITY = 1000


def _items(supplied, label, minimum, maximum, defaults, bounds):
    """Resolve a keyed building list; physical capacity is checked separately."""
    if not isinstance(supplied, list) or not minimum <= len(supplied) <= maximum:
        raise DesignError(f"{label} must be a list of {minimum}–{maximum} keyed buildings")
    keys, codes, result = set(), set(), []
    for raw in supplied:
        if not isinstance(raw, dict) or "key" not in raw or raw.keys() - (defaults.keys() | {"key", "wireless"}):
            raise DesignError(f"Each {label} entry needs a stable key and only supported demand fields: "
                              f"{', '.join(sorted(defaults))}, wireless")
        key = raw["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,19}", key) or key in keys:
            raise DesignError(f"{label} keys must be unique lowercase identifiers of 1–20 characters")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError(f"{label} keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key)
        codes.add(code)
        item = defaults | deepcopy(raw)
        for field, low, high in bounds:
            if type(item[field]) is not int or not low <= item[field] <= high:
                raise DesignError(f"{label} {key}: {field} must be an integer from {low} through {high}")
        result.append(item)
    return sorted(result, key=lambda item: item["key"])


def zones(item, kind):
    """Authored wireless zones for one building; explicit local device budgets."""
    if kind == "residence":
        floors = places.university_floors(item, kind)
        return {f"floor-{n+1:02}": dict(
            managed=RESIDENCE_PER_ROOM * min(places.DORM_ROOMS_PER_FLOOR,
                                             item["rooms"] - places.DORM_ROOMS_PER_FLOOR*n),
            guest=RESIDENCE_GUEST) for n in range(floors)}
    if kind == "library":
        return {}
    result = {f"lecture-{n+1:03}": dict(managed=LECTURE_MANAGED, guest=LECTURE_GUEST)
              for n in range(item["classrooms"])}
    result.update({f"lab-{n+1:02}": dict(managed=LAB_MANAGED, guest=0)
                   for n in range(math.ceil(item["lab_seats"] / places.LAB_SEATS_PER_ROOM))})
    result.update({f"office-{n+1:02}": dict(managed=OFFICE_MANAGED, guest=0)
                   for n in range(math.ceil(item["offices"] / places.OFFICE_DESKS))})
    return result


def _resolve_zones(item, kind, label):
    defaults = zones(item, kind)
    if not defaults:
        raise DesignError(f"{label}: a campus building needs at least one wireless zone; add rooms or seats")
    supplied = item.get("wireless", {})
    if not isinstance(supplied, dict) or supplied.keys() - defaults.keys():
        raise DesignError(f"{label}: wireless must map existing zones to managed/guest counts; zones: "
                          f"{', '.join(sorted(defaults))}. Removing zones requires a new baseline")
    for key, value in supplied.items():
        if not isinstance(value, dict) or value.keys() - {"managed", "guest"}:
            raise DesignError(f"{label} wireless {key}: only managed and guest device counts are supported")
    # resolve_wireless takes one managed default per zone; carry the authored
    # guest default through by pre-seeding every zone the recipe left alone.
    seeded = {key: value | supplied.get(key, {}) for key, value in defaults.items()}
    resolved = places.resolve_wireless({key: value["managed"] for key, value in defaults.items()},
                                       seeded, label)
    if sum(zone["guest"] for zone in resolved.values()) > GUEST_CAPACITY:
        raise DesignError(f"{label}: planned guest devices exceed the {GUEST_CAPACITY} addresses in this "
                          "building's reserved guest /22; reduce demand or rebaseline a larger plan")
    return resolved


def demand(item, kind):
    """Installed inventory only; seats and ports are capacity, not headcount."""
    floors = places.university_floors(item, kind)
    radios = item["aps"] if kind == "library" else sum(places.wireless_aps(zone) for zone in item["wireless"].values())
    if kind == "residence":
        workstations = item["rooms"] * item["wired_ports_per_room"]
        counts = dict(residence_rooms=item["rooms"], data_ports=workstations)
    elif kind == "library":
        workstations = item["reading_seats"]
        counts = dict(reading_seats=item["reading_seats"])
    else:
        workstations = item["classrooms"] + item["lab_seats"] + item["offices"]
        counts = dict(lecture_halls=item["classrooms"], lab_seats=item["lab_seats"],
                      office_desks=item["offices"])
    reported = dict(counts, workstations=workstations, aps=radios, cameras=2*floors,
                    floors=floors, peak_mbps=peak_mbps(item, kind))
    # The library sizes radios from an explicit mount count, so it has no zone
    # budget to report; a zero would wrongly read as "no wireless demand".
    if kind != "library":
        reported.update(managed_clients=sum(zone["managed"] for zone in item["wireless"].values()),
                        guest_clients=sum(zone["guest"] for zone in item["wireless"].values()))
    return reported


def peak_mbps(item, kind):
    """Authored per-building planning demand, not a measured traffic model."""
    if kind == "residence":
        return item["rooms"] + 100
    if kind == "library":
        return 2*item["reading_seats"] + 50
    return 4*item["classrooms"] + 2*item["lab_seats"] + 2*item["offices"] + 10


def access_switches(item, kind, reserve, alias="access"):
    """Restate the shared room-local access pair arithmetic before allocating."""
    ports = len(hardware_catalog()["models"][alias]["access_ports"])
    usable = int(ports * (1 - Decimal(str(reserve))))
    if usable < 1:
        raise DesignError("reserve_fraction leaves no usable access ports; lower it or rebaseline")
    total, floors = 0, places.university_floors(item, kind)
    for floor in range(1, floors + 1):
        endpoints = floor_endpoints(item, kind, floor)
        total += max(2, 2*math.ceil(endpoints / (2*usable)))
    return total


def floor_endpoints(item, kind, floor):
    """Endpoints served by one floor's equipment room under the authored layout."""
    radios = 0
    if kind == "residence":
        rooms = min(places.DORM_ROOMS_PER_FLOOR,
                    item["rooms"] - places.DORM_ROOMS_PER_FLOOR*(floor-1))
        wired = rooms * item["wired_ports_per_room"]
        radios = places.wireless_aps(item["wireless"][f"floor-{floor:02}"])
    elif kind == "library":
        first = (floor-1) * places.ACADEMIC_ROOMS_PER_FLOOR
        rooms = min(places.ACADEMIC_ROOMS_PER_FLOOR,
                    math.ceil(item["reading_seats"] / places.READING_SEATS_PER_ROOM) - first)
        wired = sum(min(places.READING_SEATS_PER_ROOM,
                        item["reading_seats"] - places.READING_SEATS_PER_ROOM*(first+n))
                    for n in range(rooms))
        radios = len(_library_mounts(item, floor))
    else:
        wired = 0
        for _, _, seats, zone, room_floor in _academic_rooms(item):
            if room_floor == floor:
                wired += seats
                radios += places.wireless_aps(item["wireless"][zone])
    return wired + radios + 2


def _academic_rooms(item):
    """Ordered (bucket, ordinal, wired seats, wireless zone, floor) room slots."""
    rooms = [("halls", n+1, 1, f"lecture-{n+1:03}") for n in range(item["classrooms"])]
    rooms += [("labs", n+1, min(places.LAB_SEATS_PER_ROOM, item["lab_seats"] - places.LAB_SEATS_PER_ROOM*n),
               f"lab-{n+1:02}") for n in range(math.ceil(item["lab_seats"] / places.LAB_SEATS_PER_ROOM))]
    rooms += [("offices", n+1, min(places.OFFICE_DESKS, item["offices"] - places.OFFICE_DESKS*n),
               f"office-{n+1:02}") for n in range(math.ceil(item["offices"] / places.OFFICE_DESKS))]
    return [(bucket, ordinal, seats, zone, index // places.ACADEMIC_ROOMS_PER_FLOOR + 1)
            for index, (bucket, ordinal, seats, zone) in enumerate(rooms)]


def _library_mounts(item, floor=None):
    """Explicit library radio mounts: entrance first, then reading rooms."""
    rooms = math.ceil(item["reading_seats"] / places.READING_SEATS_PER_ROOM)
    mounts = [("reception", 0, 1, 1)]
    for index in range(item["aps"] - 1):
        room = index % rooms
        mounts.append(("reading", room, index // rooms + 1,
                       room // places.ACADEMIC_ROOMS_PER_FLOOR + 1))
    return [m for m in mounts if floor is None or m[3] == floor]


def resolve(raw, growth=False):
    if unknown := raw.keys() - (COMMON | {"profile", "buildings", "residences", "library",
                                          "wan_peak_mbps", "demo"}):
        raise DesignError(f"Unknown university recipe fields: {', '.join(sorted(unknown))}; "
                          "describe academic buildings, residence halls, the library and campus WAN demand")
    common = dict(namespace="lakemont", name="Lakemont University", address_pool="10.0.0.0/8")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="university-campus", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("University address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("University rack-user reservations are not implemented; leave reservation_user empty")
    buildings = _items(raw.get("buildings", deepcopy(DEFAULT_BUILDINGS)), "buildings", 1, 16,
                       BUILDING_DEFAULTS,
                       (("classrooms", 0, 40), ("lab_seats", 0, 200), ("offices", 0, 60)))
    residences = _items(raw.get("residences", deepcopy(DEFAULT_RESIDENCES)), "residences", 0, 16,
                        RESIDENCE_DEFAULTS,
                        (("rooms", 10, 400), ("wired_ports_per_room", 0, 2)))
    library = LIBRARY_DEFAULTS | deepcopy(raw.get("library", {}))
    if not isinstance(raw.get("library", {}), dict) or library.keys() - LIBRARY_DEFAULTS.keys():
        raise DesignError("library accepts only reading_seats and aps")
    for field, low, high in (("reading_seats", 24, 300), ("aps", 1, 16)):
        if type(library[field]) is not int or not low <= library[field] <= high:
            raise DesignError(f"library: {field} must be an integer from {low} through {high}")
    rooms = math.ceil(library["reading_seats"] / places.READING_SEATS_PER_ROOM)
    if library["aps"] > 1 + 4*rooms:
        raise DesignError(f"library: {library['aps']} radios exceed the entrance mount plus four mounts in each "
                          f"of its {rooms} reading rooms; add reading seats or reduce aps")
    for item in buildings:
        if not item["classrooms"] + item["lab_seats"] + item["offices"]:
            raise DesignError(f"Building {item['key']}: an academic building needs lecture halls, "
                              "lab seats or faculty offices")
    for item, kind, label in ([(b, "academic", f"Building {b['key']}") for b in buildings] +
                              [(h, "residence", f"Residence {h['key']}") for h in residences]):
        item["wireless"] = _resolve_zones(item, kind, label)
    for item, kind, label in ([(b, "academic", f"Building {b['key']}") for b in buildings] +
                              [(h, "residence", f"Residence {h['key']}") for h in residences] +
                              [(library, "library", "library")]):
        floors = places.university_floors(item, kind)
        if floors > places.MAX_BUILDING_FLOORS:
            raise DesignError(f"{label}: {floors} floors exceed the reviewed {places.MAX_BUILDING_FLOORS}-floor "
                              "campus building layout; split the demand across buildings")
        peak = peak_mbps(item, kind)
        if Decimal(peak) > 1000*(1 - Decimal(str(recipe["reserve_fraction"]))):
            raise DesignError(f"{label}: {peak} Mbps peak plus reserve exceeds the supported 1 Gbps building "
                              "handoff; reduce demand or extend the reviewed campus edge")
        switches = access_switches(item, kind, recipe["reserve_fraction"],
                                   selected_alias(recipe, "access"))
        if switches > 38:
            raise DesignError(f"{label}: its floors need {switches} access switches, over the 38 supported "
                              "distribution attachments; reduce installed ports per building or add a building")
    recipe.update(buildings=buildings, residences=residences, library=library)
    campus_peak = total_peak(recipe)
    wan = raw.get("wan_peak_mbps", 4000)
    if type(wan) is not int or not 1 <= wan <= 16000:
        raise DesignError("wan_peak_mbps must be an integer between 1 and 16000 for the campus edge")
    if wan < campus_peak:
        # During growth the frozen campus edge cannot be raised: name the real exit.
        hint = ("the grown campus exceeds the frozen campus edge, and campus WAN renewal requires "
                "a new baseline — regenerate without the previous plan, sizing wan_peak_mbps with "
                "headroom for future growth" if growth
                else "raise the campus edge (leave headroom for future growth; it is frozen after "
                     "the baseline) or reduce building demand")
        raise DesignError(f"wan_peak_mbps {wan} does not cover the {campus_peak} Mbps campus peak "
                          f"(academic buildings, residence halls and the library); {hint}")
    recipe["wan_peak_mbps"] = wan
    return recipe


def total_peak(recipe):
    return (sum(peak_mbps(item, "academic") for item in recipe["buildings"])
            + sum(peak_mbps(item, "residence") for item in recipe["residences"])
            + peak_mbps(recipe["library"], "library"))


def counts(recipe):
    """Independent installed-inventory totals used to size campus services."""
    facilities = ([(item, "academic") for item in recipe["buildings"]]
                  + [(item, "residence") for item in recipe["residences"]]
                  + [(recipe["library"], "library")])
    demands = [demand(item, kind) for item, kind in facilities]
    return dict(sites=len(facilities),
                lab_seats=sum(item["lab_seats"] for item in recipe["buildings"]),
                endpoints=sum(d["workstations"] + d["aps"] + d["cameras"] for d in demands))


def workloads(recipe):
    """Size shared campus services from resolved demand, never from inventory."""
    totals = counts(recipe)
    result = []
    for slot, (key, network, vcpus, memory, disk, metric, threshold, port) in enumerate(SERVICES):
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        elif key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        groups = max(1, math.ceil(totals[metric] / threshold))
        result.append(dict(key=key, slot=slot, instances=2*groups, replicas=2, failure_domain="rack",
                           network=network, vcpus=vcpus, memory_mb=memory, disk_mb=disk,
                           listeners=listeners, criticality="tier-1" if key in TIER_1 else "tier-2",
                           replica_description="complete campus service shard; application replication and recovery are not executed"))
    return result


def generate(recipe, previous=None):
    recipe = resolve(recipe, growth=previous is not None)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous university plan does not reproduce from its recipe and ledgers; "
                              "use an intact frozen plan or new baseline")
        del reproduced
        old_recipe = previous["recipe"]
        if recipe["wan_peak_mbps"] != old_recipe["wan_peak_mbps"]:
            raise DesignError("Campus WAN renewal requires a new baseline; retained procurement is immutable during growth")
        for field, grow in (("buildings", ("classrooms", "lab_seats", "offices")),
                            ("residences", ("rooms",))):
            old = {item["key"]: item for item in old_recipe[field]}
            current = {item["key"]: item for item in recipe[field]}
            if old.keys() - current.keys():
                raise DesignError(f"Removing {field} requires a new baseline; Diode replay does not delete retired objects")
            for key, before in old.items():
                after = current[key]
                if any(after[name] < before[name] for name in grow):
                    raise DesignError(f"{field} {key}: decreasing demand requires a new baseline; ordinary growth cannot retire objects")
                if field == "residences" and after["wired_ports_per_room"] != before["wired_ports_per_room"]:
                    raise DesignError(f"Residence {key}: changing room port design requires a new baseline; add rooms to grow in place")
                if any(zone not in after["wireless"] or any(after["wireless"][zone][name] < values[name]
                        for name in ("managed", "guest")) for zone, values in before["wireless"].items()):
                    raise DesignError(f"{field} {key}: reducing or removing a wireless zone requires a new baseline")
        for name in ("reading_seats", "aps"):
            if recipe["library"][name] < old_recipe["library"][name]:
                raise DesignError(f"library: decreasing {name} requires a new baseline")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    world.reserve_sites(["dc-01"] + [f"bldg-{item['key']}" for item in r["buildings"]]
                        + [f"hall-{item['key']}" for item in r["residences"]] + ["library-01"])
    foundation(world, industry="university campus", inherited=False, networks=NETWORKS,
               site_kinds={"dc", "academic", "residence", "library"},
               device_roles=("wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
                             "pdu", "workstation", "ap", "camera", "patch-panel", "wall-outlet"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server",
                                 "endpoint", "ap", "patch-panel", "wall-outlet"})
    dc = Site(world, "dc-01", "dc",
              "Campus data center; shared learning, identity, research storage and operations services")
    datacenter.build(dc, workloads=workloads(r), wan_peak_mbps=r["wan_peak_mbps"], include_equipment=False,
                     assumptions=[
        "One campus data center serves the whole campus. Its WAN budget covers the declared building peaks under a "
        "synthetic centralized-service traffic model; this is not measured research or residential throughput.",
        "Each complete replica group is separated across compute racks; application replication, failover and recovery are not executed.",
        "Service groups cover 2,000 installed endpoints for the learning portal, 1,500 for identity, 4,000 for monitoring, "
        "120 installed lab seats for research storage, 16 campus buildings for DNS and 8 for backup, minimum one each. "
        "These are fictional planning thresholds, not enrollment, course load or storage retention sizing.",
        "Identity includes RADIUS UDP1812/1813 inventory for eduroam-style WLAN authentication naming only. No authentication "
        "protocol, realm, roaming federation or directory is configured, running or certified.",
        "The research-storage listener uses TCP2049, a conventional file-service transport number. No file system, export "
        "policy, quota or data-retention behaviour is represented.",
    ])
    campuses = []
    for item in r["buildings"]:
        site = Site(world, f"bldg-{item['key']}", "academic",
                    "Academic building; lecture halls, teaching and research labs and faculty offices")
        campuses.append(world.obj(site.key))
        _building(site, item, "academic")
    for item in r["residences"]:
        site = Site(world, f"hall-{item['key']}", "residence",
                    "Residence hall; installed room data ports and dense floor coverage")
        campuses.append(world.obj(site.key))
        _building(site, item, "residence")
    site = Site(world, "library-01", "library",
                "Campus library; installed study positions and public reading-room coverage")
    campuses.append(world.obj(site.key))
    _building(site, r["library"], "library")
    networking.wireless(world, campuses,
                        lan_roles=(("staff", "staff", "wlan0"), ("students", "students", "wlan1")),
                        diagnostic=False, stable_channels=True,
                        guest_sites={obj["key"] for obj in campuses})
    equipment.enrich(world)
    optics.enrich(world)
    poe.enrich(world)
    ipv6.enrich(world)
    networking.macs(world)
    operations.supporting_records(world)
    names = set()
    for obj in world.objects.values():
        if obj["kind"] == "device":
            identity = (obj["refs"]["site"], obj["attrs"]["name"])
            if identity in names:
                raise DesignError(f"{obj['key']}: generated device names collide; choose distinct building keys")
            names.add(identity)
    return world.finish()


SHARED_ASSUMPTIONS = [
    "One authored metro holds this whole campus. Building premises, street numbers, floor geometry and room counts are "
    "fictional design inputs, not a surveyed university estate.",
    "Installed workstations, lab seats, residence-room data ports, study positions, radios and cameras are equipment "
    "inventory. They are not enrollment, occupancy, staffing, course schedules or measured utilisation.",
    "Each residence-room wired port is one installed endpoint record with its own access port and address reservation. "
    "It represents the installed port, never a resident-owned device.",
    "Separate staff, student, research, wireless, security, guest and management segments express intended boundaries. "
    "No firewall policy, network access control or compliance state is demonstrated.",
    "Identity and WLAN records use eduroam-style naming only. No authentication protocol, RADIUS realm or roaming "
    "federation is configured or asserted anywhere in this dataset.",
    "Access switches grow in pairs per floor equipment room and retain each endpoint's reserved physical port as demand "
    "grows; endpoints themselves remain single-homed.",
    "Wireless zone managed/guest counts are explicit local device budgets that size AP mounts at 32 devices per radio, up "
    "to four mounts per zone. This is an authored equipment envelope, not surveyed RF capacity or observed associations.",
    "Each building attaches through the shared two-carrier edge grammar. A real single campus would interconnect its "
    "buildings over owned fiber; no campus-owned dark fiber, duct diversity or running routing is represented here.",
]


def _building(site, item, kind):
    """Build one campus building from the shared room and access allocators."""
    rooms = places.university_rooms(site, item)
    networks = ACADEMIC_NETWORKS if kind == "academic" else BUILDING_NETWORKS
    dist, edges, upstreams = campus.aggregation(site, networks, peak_mbps(item, kind))
    endpoints = []

    def add(label, role, segment, room, cohort, ordinal=1):
        key = site.device("ap" if role == "ap" else "endpoint", label, role, racked=False,
                          meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
        places.university_endpoint(site, key, room, cohort, ordinal)
        endpoints.append((key, segment))

    def radios(zone, label, room, cohort):
        for ordinal in range(1, places.wireless_aps(item["wireless"][zone]) + 1):
            add(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, cohort, ordinal)

    if kind == "academic":
        for number, room in enumerate(rooms["halls"], 1):
            add(f"instructor-{number:03}", "workstation", "staff", room, "instructor-position")
            radios(f"lecture-{number:03}", f"ap-lecture-{number:03}", room, "lecture-ap")
        for number, room in enumerate(rooms["labs"], 1):
            seats = site.w.obj(room)["meta"]["capacity"]["workstations"]
            for seat in range(1, seats + 1):
                add(f"lab-{number:02}-{seat:02}", "workstation", "research", room, "lab-seat", seat)
            radios(f"lab-{number:02}", f"ap-lab-{number:02}", room, "lab-ap")
        for number, room in enumerate(rooms["offices"], 1):
            desks = site.w.obj(room)["meta"]["capacity"]["workstations"]
            for desk in range(1, desks + 1):
                add(f"office-{number:02}-{desk:02}", "workstation", "staff", room, "faculty-desk", desk)
            radios(f"office-{number:02}", f"ap-office-{number:02}", room, "office-ap")
    elif kind == "residence":
        for number, room in enumerate(rooms["residence"], 1):
            for port in range(1, item["wired_ports_per_room"] + 1):
                add(f"room-{number:03}-{port:02}", "workstation", "students", room, "residence-port", port)
        for floor, corridor in rooms["corridors"].items():
            radios(f"floor-{floor:02}", f"ap-floor-{floor:02}", corridor, "residence-ap")
    else:
        for number, room in enumerate(rooms["reading"], 1):
            seats = site.w.obj(room)["meta"]["capacity"]["workstations"]
            for seat in range(1, seats + 1):
                add(f"study-{number:02}-{seat:02}", "workstation", "students", room, "study-position", seat)
        for index, (bucket, room_index, ordinal, _) in enumerate(_library_mounts(item), 1):
            room = rooms["reception"] if bucket == "reception" else rooms["reading"][room_index]
            add(f"ap-library-{index:02}", "ap", "wireless", room, "library-ap", ordinal)
    for floor, corridor in rooms["corridors"].items():
        for ordinal, side in enumerate(("a", "b"), 1):
            add(f"camera-floor-{floor:02}-{side}", "camera", "security", corridor, "corridor-camera", ordinal)
    facts = campus.access(site, endpoints, upstreams, networks,
                          {"access_hardware": "access", "upstreams": 2, "label": "access-"}, stable=True)
    site.contract.update(endpoint_count=len(endpoints), demand=demand(item, kind), access_hardware=site.w.hardware_alias("access"),
                         access_devices=facts["access_devices"],
                         access_usable_ports=facts["access_usable_ports"],
                         required_device_roles={"role/wan-edge": 2, "role/distribution": 2,
                                                "role/access": facts["access_count"]})
    site.contract["assumptions"].append({
        "academic": "Each academic building holds eight rooms per floor: lecture halls first, then 24-seat teaching and "
                    "research labs, then twelve-desk faculty office pods. One instructor position serves each lecture hall.",
        "residence": "Each residence floor holds fifty rooms along one corridor. Room data ports are installed capacity; "
                     "floor radios and corridor cameras are the only shared endpoints.",
        "library": "The library holds eight 24-seat reading rooms per floor plus a ground-floor entrance. Its radio count "
                   "is an explicit authored mount list, not a zone-budget derivation.",
    }[kind])
    site.contract["assumptions"].extend(SHARED_ASSUMPTIONS)
    equipment.enrich_site(site, demonstrations=False)
    site.management(dist)
    site.power()
