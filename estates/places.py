"""Authored Great Lakes geography and finite, fictional building layouts.

Time-zone labels follow IANA tzdb northamerica and zone1970.tab:
https://data.iana.org/time-zones/tzdb/northamerica
https://data.iana.org/time-zones/tzdb/zone1970.tab
Street addresses, rooms, dimensions and routes are design inputs, not surveyed
properties. Authored naming emits metro-centered synthetic coordinate offsets
for the map view — never a claim about a real premises location — and no
RF/electrical compliance is invented.
"""

import hashlib
import math

from .model import DesignError


METROS = (
    ("Chicago", "IL", "Illinois", "America/Chicago", 41.8781, -87.6298),
    ("Detroit", "MI", "Michigan", "America/Detroit", 42.3314, -83.0458),
    ("Cleveland", "OH", "Ohio", "America/New_York", 41.4993, -81.6944),
    ("Milwaukee", "WI", "Wisconsin", "America/Chicago", 43.0389, -87.9065),
)
# Authored display-name pools ("naming = 'authored'"). Streets are real metro
# geography; every composed site name stays a fictional design assumption.
STREETS = {
    "Chicago": ("Wabash", "Halsted", "Clark", "Ashland", "Damen", "Kedzie", "Montrose", "Archer"),
    "Detroit": ("Woodward", "Gratiot", "Cass", "Livernois", "Vernor", "Bagley", "Jefferson", "Mack"),
    "Cleveland": ("Euclid", "Superior", "Lorain", "Prospect", "Carnegie", "Payne", "Clifton", "Denison"),
    "Milwaukee": ("Brady", "Kilbourn", "Wells", "Vliet", "Locust", "Greenfield", "Mitchell", "Burleigh"),
}
CAMPUSES = {
    "Chicago": ("Elk Grove", "Cermak", "Fulton Market", "Ravenswood"),
    "Detroit": ("Corktown", "Rivertown", "Highland Park", "New Center"),
    "Cleveland": ("Flats East", "Midtown", "Lakewood Edge", "University Circle"),
    "Milwaukee": ("Third Ward", "Menomonee Valley", "Walkers Point", "Bay View"),
}
GROUPS = {"branch": "Retail branches", "hq": "Headquarters", "dc": "Data centers", "school": "Schools",
          "hospital": "Hospitals", "clinic": "Outpatient clinics", "pop": "Provider PoPs", "customer": "Customer premises",
          "store": "Retail stores", "distribution": "Distribution centers",
          "academic": "Academic buildings", "residence": "Residence halls", "library": "Libraries"}
MAX_CHANNEL_M = 80
FLOOR_HEIGHT_M = 4
OFFICE_DESKS = 12
# Authored university building grammar. Eight rooms per academic or library
# floor, fifty residence rooms per floor, and eight floors per building: the
# same ceiling the shared eight-block management uplink pool supports.
ACADEMIC_ROOMS_PER_FLOOR = 8
DORM_ROOMS_PER_FLOOR = 50
LAB_SEATS_PER_ROOM = 24
READING_SEATS_PER_ROOM = 24
MAX_BUILDING_FLOORS = 8
SPACE_DESCRIPTIONS = {
    "building": "Banking and business operations premises",
    "floor": "Customer or staff floor with assigned equipment-room service",
    "equipment_room": "Restricted network equipment and cable termination",
    "atm_lobby": "Public self-service banking lobby",
    "teller_hall": "Customer-facing teller and service counters",
    "office": "Private staff work area with twelve desk positions",
    "reception": "Visitor reception and building entrance",
    "classroom": "Teaching room with declared student seats and a teacher position",
    "computer_lab": "Shared student computer lab; seats do not add to enrollment",
    "patient_room": "Two bed stations with reference networked monitoring endpoints; no clinical equipment certification",
    "nurse_station": "Clinical workstation area supporting the assigned ward",
    "exam_room": "Outpatient examination room with an installed clinical workstation",
    "imaging_room": "Reference imaging modality and diagnostic workstation; no clinical function is executed",
    "corridor": "Circulation space with access-point and security-camera locations",
    "sales_floor": "Customer sales floor with point-of-sale lane positions",
    "back_office": "Store back office with staff workstation positions",
    "stockroom": "Receiving and stock storage area",
    "warehouse_floor": "Distribution warehouse floor with handheld scanner positions",
    "shipping_dock": "Shipping and receiving dock",
    "lecture_hall": "Teaching room with an installed instructor position and coverage radio",
    "teaching_lab": "Instructional and research computing lab with installed workstation seats",
    "dorm_room": "Residence room with installed wired data ports; no resident-owned device is represented",
    "reading_room": "Library reading area with installed study workstation positions",
}


def _site_display(site):
    """The emitted display name (authored, overridden, or legacy)."""
    return site.w.obj(site.key)["attrs"]["name"]


def _ordinal(number):
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"


def _authored_identity(site, city):
    """Deterministic, unique-by-construction display name for one site.

    Every template embeds a component that is unique within its kind (the
    branch ordinal, the user-authored campus/PoP/customer key, or the fixed
    DC metro), so growth never renames an existing site and collisions cannot
    depend on generation order. Workspace.finish() still enforces global
    uniqueness as the belt.
    """
    w, sid, kind = site.w, site.id, site.contract["kind"]
    profile = w.recipe["profile"]
    # Pool picks hash the site id alone (not the seed): display names embed in
    # journals and descriptions estate-wide, and the declared seed variation
    # stays bounded to serials, dates and design-pool choices.
    pick = lambda pool: pool[int.from_bytes(
        hashlib.sha256(f"display/{sid}".encode()).digest()[:4], "big") % len(pool)]
    if kind == "branch":
        # The address-allocation slot is unique across every site and stable
        # under growth, so street & number can never collide or get renamed.
        # "and", not "&": report tables HTML-escape ampersands, and every name
        # must appear verbatim in the rendered report.
        return f"{pick(STREETS[city])} and {_ordinal(getattr(w, 'allocations', {}).get(sid, 0))} Branch"
    if kind == "store":
        # Same construction as a branch: the address-allocation slot is unique
        # across every site and frozen by growth, so no store can be renamed.
        return f"{pick(STREETS[city])} and {_ordinal(getattr(w, 'allocations', {}).get(sid, 0))} Store"
    if kind == "distribution":
        # The di- ordinal is unique among distribution centers by construction,
        # so two same-metro campuses cannot resolve to one display name.
        return f"{pick(CAMPUSES[city])} Distribution Center {int(sid.rsplit('-', 1)[-1] or 0):02}"
    if kind in {"academic", "residence"}:
        # Building keys are unique within the recipe and frozen by growth, and
        # the two kinds use different suffixes, so one key cannot name two sites.
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} {'Hall' if kind == 'academic' else 'House'}"
    if kind == "library":
        return f"{pick(CAMPUSES[city])} Library"
    if kind == "hq":
        return f"{city} Headquarters"
    if kind == "dc":
        if profile == "provider-backbone":
            return f"{city} NOC Campus"
        if profile == "university-campus":
            return f"{city} Campus Data Center"
        return f"{pick(CAMPUSES[city])} Data Center"
    if kind in {"school", "hospital", "clinic"}:
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} {'School' if kind == 'school' else kind.title()}"
    if kind == "pop":
        # Never drop tokens: PoP keys are globally unique, so full-token
        # titles are too (chicago-east and cleveland-east must not both
        # become "East Exchange").
        tokens = sid.removeprefix("pop-").split("-")
        return f"{' '.join(token.title() for token in tokens)} Exchange"
    if kind == "customer":
        base = sid.removeprefix("ce-")
        stem, ordinal = base.rsplit("-", 1)
        pop_keys = sorted((p.removeprefix("pop-") for p in getattr(w, "provider_metros", {})
                           if p.startswith("pop-")), key=len, reverse=True)
        pop_title = ""
        for pop_key in pop_keys:
            if stem.endswith("-" + pop_key):
                stem = stem.removesuffix("-" + pop_key)
                pop_title = " ".join(token.title() for token in pop_key.split("-"))
                break
        customer = stem.replace("-", " ").title()
        return " ".join(part for part in (customer, "-", pop_title, f"{int(ordinal):02}") if part)
    return f"{sid.replace('-', ' ').title()} Site"


def _display_site(site, node, city, latitude, longitude):
    """Apply the naming policy: authored identity, overrides, facility, geo."""
    w = site.w
    authored = w.recipe.get("naming", "authored") == "authored"
    name = _authored_identity(site, city) if authored else node["attrs"]["name"]
    # Facility codes ride the stable address-allocation slot: short, unique,
    # and unchanged by growth (site ids can exceed the native 50-char limit).
    facility = (f"{city[:3].upper()}{getattr(w, 'allocations', {}).get(site.id, 0):04}"
                if authored else None)
    override = w.recipe.get("site_names", {}).get(site.id)
    if override:
        name = override.get("name", name)
        facility = override.get("facility", facility)
        w.consumed_site_names.add(site.id)
    node["attrs"]["name"] = name
    if facility:
        node["attrs"]["facility"] = facility
    if authored:
        # Metro-centered synthetic offsets: enough spread for the map view,
        # never a claim about a real street address.
        jitter = hashlib.sha256(f"geo/{site.id}".encode()).digest()
        node["attrs"]["latitude"] = round(latitude + (jitter[0] / 255 - 0.5) * 0.24, 6)
        node["attrs"]["longitude"] = round(longitude + (jitter[1] / 255 - 0.5) * 0.24, 6)


def foundation(w, *, site_kinds=None):
    """Publish namespaced shared geography before any site references it."""
    ns = w.recipe["namespace"]
    root = f"region/{ns}/us"
    lakes = f"{root}/great-lakes"
    for key, name, slug, parent in ((root, "United States", "us", None),
                                    (lakes, "Great Lakes", "great-lakes", root)):
        w.add("region", key, {"name": f"{ns} {name}", "slug": f"{ns}-{slug}"},
              {"parent": parent} if parent else {})
    for _, code, state, _, _, _ in METROS:
        w.add("region", f"{root}/{code.lower()}",
              {"name": f"{ns} {state}", "slug": f"{ns}-{code.lower()}"}, {"parent": lakes})
    for kind, name in GROUPS.items():
        if site_kinds is None and kind in {"school", "hospital", "clinic", "pop", "customer",
                                           "store", "distribution", "academic", "residence", "library"}:
            continue
        if site_kinds is not None and kind not in site_kinds:
            continue
        w.add("site_group", f"site-group/{ns}/{kind}",
              {"name": f"{ns} {name}", "slug": f"{ns}-{kind}"})


def _location(site, suffix, name, space_type, floor, position, parent=None, capacity=None):
    key = f"location/{site.id}" + (f"/{suffix}" if suffix else "")
    refs = {"site": site.key, "tenant": site.tenant}
    if parent:
        refs["parent"] = parent
    meta = {"space_type": space_type, "floor": floor, "position_m": list(position)}
    if capacity:
        meta["capacity"] = capacity
    description = SPACE_DESCRIPTIONS[space_type]
    if site.w.recipe["profile"] != "regional-bank" and space_type in {"building", "floor"}:
        description = "Network, compute and facilities building" if space_type == "building" else "Equipment and facilities floor"
        if site.w.recipe["profile"] == "school-district":
            description = "Education and district services building" if space_type == "building" else "Teaching, staff and equipment floor"
        elif site.w.recipe["profile"] == "hospital-clinics":
            description = "Care and health-system services building" if space_type == "building" else "Care, clinical staff and equipment floor"
        elif site.w.recipe["profile"] == "university-campus":
            description = "Campus teaching, residential and services building" if space_type == "building" else "Teaching, residential and equipment floor"
    if site.contract["kind"] == "dc" and space_type == "equipment_room":
        description = "Restricted data hall with compute, network and power distribution"
    site.w.add("location", key,
               {"name": name, "slug": f"{site.name}-{suffix.replace('/', '-') or 'equipment'}", "status": "active",
                "description": description}, refs, meta)
    return key


def _floor(site, number):
    key = f"location/{site.id}/floor-{number:02}"
    if key not in site.w.objects:
        _location(site, f"floor-{number:02}", f"Floor {number:02}", "floor", number,
                  (0, 0, (number - 1) * FLOOR_HEIGHT_M), f"location/{site.id}/building")
    return key


def locate(site):
    """Place an existing site and retain location/<site id> for its equipment."""
    if site.w.recipe["profile"] == "provider-backbone":
        return provider_locate(site)
    kind = site.contract["kind"]
    if kind not in GROUPS:
        raise DesignError(f"{site.id}: no building layout for site kind {kind!r}")
    # Site IDs, unlike a global enumeration or RNG, survive mixed estate growth.
    if site.w.recipe["profile"] == "school-district":
        metro_index = site.w.choose("district", "metro", range(len(METROS)))
    elif site.w.recipe["profile"] == "hospital-clinics":
        metro_index = site.w.choose("health-system", "metro", range(len(METROS)))
    elif site.w.recipe["profile"] == "university-campus":
        # One campus, one metro: every building, hall, library and the campus
        # DC share it. This is a single-site estate, not a multi-metro fleet.
        metro_index = site.w.choose("campus", "metro", range(len(METROS)))
    elif site.id in {"dc-01", "dc-02"}:
        metro_index = int(site.id[-2:]) - 1
    else:
        metro_index = int.from_bytes(hashlib.sha256(site.id.encode()).digest()[:4], "big") % len(METROS)
    city, state_code, state, zone, latitude, longitude = METROS[metro_index]
    node = site.w.obj(site.key)
    suffix = "".join(character for character in site.id if character.isdigit())
    number = 100 + 4 * int(suffix or "0")
    if kind in {"school", "hospital", "clinic", "store", "distribution",
                "academic", "residence", "library"}:
        number = 100 + 4 * site.w.allocations[site.id]
    streets = {"br-s": "Market Street", "br-m": "Commerce Drive", "br-l": "Harbor Avenue",
               "hq": "Lakefront Boulevard", "dc": "Technology Way", "school-": "Learning Way",
               "hospital-": "Care Avenue", "clinic-": "Community Way",
               "st-s": "Market Square", "st-m": "Retail Parkway", "st-l": "Galleria Drive",
               "di-": "Distribution Parkway", "bldg-": "University Quadrangle",
               "hall-": "Residence Row", "library-": "Library Green"}
    street = next((name for prefix, name in streets.items() if site.id.startswith(prefix)), "Commerce Way")
    node["attrs"].update(time_zone=zone,
                         physical_address=f"{number} {street}\n{city}, {state}\nUnited States")
    node["refs"].update(region=f"region/{site.w.recipe['namespace']}/us/{state_code.lower()}",
                        group=f"site-group/{site.w.recipe['namespace']}/{kind}")
    node["meta"]["geography"] = {"country": "US", "state": state_code, "city": city, "synthetic": True}
    _display_site(site, node, city, latitude, longitude)
    building = _location(site, "building", "Main building", "building", 0, (0, 0, 0))
    ground = _floor(site, 1)
    equipment = _location(site, "", "Data hall" if kind == "dc" else "MDF",
                          "equipment_room", 1, (24, 18, 0), ground)
    site.equipment_location = equipment
    site.contract["placement"] = {"equipment_location": equipment, "building": building,
                                  "equipment_locations": {"1": equipment},
                                  "max_access_channel_m": MAX_CHANNEL_M, "floor_height_m": FLOOR_HEIGHT_M}
    site.contract["assumptions"].append(
        "Geography names and time zones are real; premises and room geometry are synthetic. "
        "Access routes use local metres and an authored 80 m channel ceiling, not a surveyed cabling or RF design.")
    return equipment


def provider_locate(site):
    """Provider sites use explicit metro attachments; geometry stays local."""
    kind, w = site.contract["kind"], site.w
    metro = w.provider_metros[site.id]
    city, code, state, zone, latitude, longitude = next(row for row in METROS if row[0].lower() == metro)
    street = {"pop": "Exchange Avenue", "customer": "Business Way", "dc": "Technology Way"}[kind]
    node = w.obj(site.key)
    node["attrs"].update(time_zone=zone, physical_address=f"{100+4*w.allocations[site.id]} {street}\n{city}, {state}\nUnited States")
    node["refs"].update(region=f"region/{w.recipe['namespace']}/us/{code.lower()}",
                        group=f"site-group/{w.recipe['namespace']}/{kind}")
    node["meta"]["geography"] = dict(country="US", state=code, city=city, synthetic=True)
    _display_site(site, node, city, latitude, longitude)
    building = _location(site,"building","Main building","building",0,(0,0,0))
    floor = _floor(site,1)
    equipment = _location(site,"","Data hall" if kind == "dc" else "MDF","equipment_room",1,(24,18,0),floor)
    site.contract["placement"] = dict(equipment_location=equipment,building=building,equipment_locations={"1":equipment},
                                      max_access_channel_m=MAX_CHANNEL_M,floor_height_m=FLOOR_HEIGHT_M)
    site.contract["assumptions"].append("Metro and time-zone names are real; addresses, premises and local metre routes are authored. Carrier span routes and duct diversity are unknown.")
    return equipment


def provider_office(site):
    return _location(site,"office-01","Customer office pod","office",1,(8,18,0),_floor(site,1),{"workstations":12})


def provider_endpoint(site,key,room,ordinal):
    """Twelve fixed desk positions; growing inventory leaves earlier routes intact."""
    if type(ordinal) is not int or not 1 <= ordinal <= 12:
        raise DesignError(f"{site.id}: customer office supports twelve wired desks")
    slot = ordinal-1
    point = [10+2*(slot%4),19+2*(slot//4),0.8]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,(24,18,0)))+10)
    node = site.w.obj(key)
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"Customer office workstation {ordinal:03}"
    node["meta"].update(placement=dict(room=room,function="office",floor=1,position_m=point,cable_origin=site.equipment_location),
                        access_channel_length_m=route,cohort="customer-office")


def arrange(site, demand):
    """Grow office pods from demand without moving earlier pods or desks.

    Retail uses four permanent teller positions, then 12-desk private office
    pods on one floor. HQ uses four pods per floor and a local equipment room
    on each occupied floor, with fiber risers back to the MDF.
    ponytail: the finite footprint holds eight retail pods or four HQ floors;
    extend the reviewed layout before admitting larger per-building demand.
    """
    kind = site.contract["kind"]
    if kind not in {"branch", "hq"}:
        raise DesignError(f"{site.id}: endpoint rooms require a branch or headquarters")
    for field in ("workstations", "atms", "aps", "cameras"):
        if type(demand.get(field)) is not int or demand[field] < 0:
            raise DesignError(f"{site.id}: {field} must be a nonnegative integer")
    teller_desks = min(4, demand["workstations"]) if kind == "branch" else 0
    office_count = math.ceil((demand["workstations"] - teller_desks) / OFFICE_DESKS)
    maximum = 8 if kind == "branch" else 16
    if office_count > maximum or demand["atms"] > 8:
        raise DesignError(f"{site.id}: endpoint demand exceeds the authored building footprint; extend its layout")
    ground = _floor(site, 1)
    reception = kind == "hq" and demand["atms"] == 0
    lobby = _location(site, "reception" if reception else "atm-lobby",
                      "Reception" if reception else "ATM lobby", "reception" if reception else "atm_lobby", 1, (6, 6, 0), ground,
                      {"atms": 8})
    teller = None
    if kind == "branch":
        teller = _location(site, "teller-hall", "Teller hall", "teller_hall", 1, (24, 6, 0), ground,
                           {"workstations": 4})
    offices = []
    for index in range(office_count):
        floor = index // 4 + 1 if kind == "hq" else 1
        column, row = index % 4, 0 if kind == "hq" else index // 4
        offices.append(_location(site, f"office-{index+1:02}", f"Private office pod {index+1:02}",
                                  "office", floor, (8 + 12 * column, 18 + 12 * row, (floor - 1) * FLOOR_HEIGHT_M),
                                  _floor(site, floor), {"workstations": OFFICE_DESKS}))
    if kind == "hq":
        for floor in range(2, math.ceil(office_count / 4) + 1):
            equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room",
                                  floor, (24, 18, (floor - 1) * FLOOR_HEIGHT_M), _floor(site, floor))
            site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    site.places = {"lobby": lobby, "teller": teller, "offices": offices,
                   "teller_desks": teller_desks, "demand": dict(demand)}


def place_endpoint(site, key, role, ordinal):
    """Assign a 1-based endpoint ordinal to an actual room and bounded route."""
    fields = {"workstation": "workstations", "atm": "atms", "ap": "aps", "camera": "cameras"}
    if role not in fields or type(ordinal) is not int or not 1 <= ordinal <= site.places["demand"][fields[role]]:
        raise DesignError(f"{site.id}: invalid {role} endpoint ordinal {ordinal!r}")
    rooms = site.places
    hq = site.contract["kind"] == "hq"
    index = ordinal - 1
    if role == "atm":
        room, slot, purpose = rooms["lobby"], index, "Public self-service ATM"
    elif role == "workstation":
        if index < rooms["teller_desks"]:
            room, slot, purpose = rooms["teller"], index, "Teller customer-service workstation"
        else:
            office, slot = divmod(index - rooms["teller_desks"], OFFICE_DESKS)
            room, purpose = rooms["offices"][office], "Staff workstation"
    else:
        purpose = "Ceiling access point" if role == "ap" else "Security camera"
        slot = index
        if hq:
            if role == "camera" and index == 0:
                room = rooms["lobby"]
            else:
                office = index if role == "ap" else (index // 2) * 4
                if office >= len(rooms["offices"]):
                    raise DesignError(f"{site.id}: {role} {ordinal} has no occupied office zone; review endpoint demand")
                room = rooms["offices"][office]
        elif index < (2 if role == "ap" else 4):
            room = rooms["lobby"] if index < (1 if role == "ap" else 2) else rooms["teller"]
        else:
            office = index - 2 if role == "ap" else (index - 4) // 2
            if office >= len(rooms["offices"]):
                raise DesignError(f"{site.id}: {role} {ordinal} has no private office zone; review endpoint demand")
            room = rooms["offices"][office]
    space = site.w.obj(room)
    origin = space["meta"]["position_m"]
    mounting_height = {"workstation": 0.8, "atm": 1.2, "ap": 2.8, "camera": 2.5}[role]
    position = [origin[0] + 2 + 2 * (slot % 4), origin[1] + 1 + 2 * ((slot // 4) % 3),
                origin[2] + mounting_height]
    origin_room = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    equipment = site.w.obj(origin_room)["meta"]["position_m"]
    route = math.ceil(sum(abs(a - b) for a, b in zip(position, equipment)) + 10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: authored access channel needs {route} m; limit is {MAX_CHANNEL_M} m")
    node = site.w.obj(key)
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{purpose} {ordinal:03} in {space['attrs']['name']} at {_site_display(site)}"
    node["meta"].update(placement={"room": room, "function": space["meta"]["space_type"],
                                  "floor": space["meta"]["floor"], "position_m": position,
                                  "cable_origin": origin_room}, access_channel_length_m=route)


def resolve_wireless(defaults, supplied, label):
    """Freeze bounded concurrent device budgets for existing local zones."""
    if not isinstance(supplied, dict) or supplied.keys() - defaults.keys():
        raise DesignError(f"{label}: wireless must map existing zones to managed/guest counts; zones: {', '.join(sorted(defaults))}. Removing zones requires a new baseline")
    resolved = {}
    for key, managed in sorted(defaults.items()):
        value = supplied.get(key, {})
        if not isinstance(value, dict) or value.keys() - {"managed", "guest"}:
            raise DesignError(f"{label} wireless {key}: only managed and guest device counts are supported")
        value = dict(managed=managed, guest=0) | value
        if any(type(n) is not int or not 0 <= n <= 128 for n in value.values()) or sum(value.values()) > 128:
            raise DesignError(f"{label} wireless {key}: managed/guest must be integers 0–128 with total at most 128; the zone has four AP mounts")
        resolved[key] = value
    if sum(zone["guest"] for zone in resolved.values()) > 4084:
        raise DesignError(f"{label}: planned guest clients exceed 4084 addresses in the reserved /20 client range")
    return resolved


def wireless_aps(zone, minimum=1):
    # Authored inventory planning envelope, not an RF/throughput rating.
    return max(minimum, (zone["managed"] + zone["guest"] + 31) // 32)


def _ap_offset(cohort, ordinal):
    limit = 8 if cohort == "exam-ap" else 4
    if type(ordinal) is not int or not 1 <= ordinal <= limit:
        raise DesignError(f"{cohort}: AP mount ordinal must be 1–{limit}")
    row, lane = divmod(ordinal - 1, 2)
    if cohort == "exam-ap":
        return 4 + 8*lane, 4 + 4*row
    if cohort == "ward-ap":
        return 4 + 8*lane, 4 - 8*row
    return 4 + 4*lane, 4 + 4*row


def school_rooms(site, demand):
    """Finite classroom grammar with permanent room identities and floor closets."""
    classrooms, offices = [], []
    for index in range(demand["classrooms"]):
        floor = index // 8 + 1
        classrooms.append(_location(site, f"classroom-{index+1:03}", f"Classroom {index+1:03}",
            "classroom", floor, (6 + 12*(index % 4), 4 + 24*((index % 8)//4), (floor-1)*FLOOR_HEIGHT_M),
            _floor(site, floor), {"students": demand["students_per_classroom"],
                                  "workstations": demand["wired_seats_per_classroom"]+1}))
    for index in range(math.ceil(demand["administrative_staff"]/12)):
        offices.append(_location(site, f"admin-{index+1:02}", f"Administration {index+1:02}",
            "office", 1, (6+12*index, 50, 0), _floor(site, 1), {"workstations": 12}))
    lab = _location(site, "computer-lab", "Shared computer lab", "computer_lab", 1,
                    (6, 40, 0), _floor(site, 1), {"workstations": 36}) if demand["lab_seats"] else None
    for floor in range(2, math.ceil(demand["classrooms"]/8)+1):
        room = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,
                         (24, 18, (floor-1)*FLOOR_HEIGHT_M), _floor(site, floor))
        site.contract["placement"]["equipment_locations"][str(floor)] = room
    return classrooms, offices, lab


def school_endpoint(site, key, room, cohort, ordinal):
    """Place a wired endpoint; wireless headcounts never enter this allocator."""
    space = site.w.obj(room)
    origin = space["meta"]["position_m"]
    role = site.w.obj(key)["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if cohort == "teacher":
        offset = (8, 0)
    elif role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif role == "role/camera":
        offset = (1+8*(ordinal-1), 0)
    else:
        offset = (1+1.2*((ordinal-1) % 6), 1+1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: school access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node = site.w.obj(key)
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {_site_display(site)}"
    if role == "role/ap":
        node["attrs"]["description"] += "; staff on wlan0, students on wlan1; RF coverage unverified"
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving}, access_channel_length_m=route)


def retail_rooms(site):
    """Permanent single-floor trading or warehouse grammar; no surveyed premises.

    ponytail: one authored floor per store and distribution centre. A multi-level
    store needs a reviewed riser and closet layout before demand can exceed it.
    """
    kind = site.contract["kind"]
    if kind not in {"store", "distribution"}:
        raise DesignError(f"{site.id}: retail rooms require a store or distribution centre")
    ground = _floor(site, 1)
    if kind == "store":
        return dict(
            selling=_location(site, "sales-floor", "Sales floor", "sales_floor", 1, (6, 4, 0), ground),
            office=_location(site, "back-office", "Back office", "back_office", 1, (6, 30, 0), ground,
                             {"workstations": OFFICE_DESKS}),
            storage=_location(site, "stockroom", "Stockroom", "stockroom", 1, (30, 30, 0), ground))
    return dict(
        selling=_location(site, "warehouse-floor", "Warehouse floor", "warehouse_floor", 1, (6, 4, 0), ground),
        office=_location(site, "office-01", "Distribution office", "office", 1, (6, 30, 0), ground,
                         {"workstations": OFFICE_DESKS}),
        storage=_location(site, "shipping-dock", "Shipping dock", "shipping_dock", 1, (30, 30, 0), ground))


def retail_endpoint(site, key, room, cohort, ordinal):
    """Place one store or warehouse endpoint on its bounded local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    # Mount pitches: sparse ceiling grids for radios and cameras, dense lanes
    # for floor equipment. These are authored layouts, not a surveyed plan.
    span, columns = (8, 2) if role == "role/ap" else (4, 4) if role == "role/camera" else (1.2, 6)
    if type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: retail endpoint ordinal must be a positive integer")
    point = [origin[0] + 1 + span*((ordinal-1) % columns), origin[1] + 1 + span*((ordinal-1)//columns),
             origin[2] + height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: retail access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {_site_display(site)}"
    if role == "role/ap":
        node["attrs"]["description"] += "; staff WLAN on wlan0; RF coverage unverified"
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving},
        access_channel_length_m=route)


def _campus_floor(site, floor):
    """Create one building floor and, above ground, the IDF that serves it."""
    parent = _floor(site, floor)
    rooms = site.contract["placement"]["equipment_locations"]
    if floor > 1 and str(floor) not in rooms:
        rooms[str(floor)] = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room",
                                      floor, (24, 18, (floor-1)*FLOOR_HEIGHT_M), parent)
    return parent


def university_floors(item, kind):
    """Authored floor count for one campus building; no surveyed premises."""
    if kind == "residence":
        return max(1, math.ceil(item["rooms"] / DORM_ROOMS_PER_FLOOR))
    if kind == "library":
        rooms = math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM)
    else:
        rooms = (item["classrooms"] + math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM)
                 + math.ceil(item["offices"] / OFFICE_DESKS))
    return max(1, math.ceil(rooms / ACADEMIC_ROOMS_PER_FLOOR))


def university_spaces(item, kind):
    """Ordered (suffix, name, space_type, bucket, capacity) rooms of one building.

    The order is creation order, which is what binds a room to its permanent
    reserved building position. New rooms append; existing rooms never move.
    """
    if kind == "residence":
        return [(f"room-{n+1:03}", f"Residence room {n+1:03}", "dorm_room", "residence",
                 {"workstations": item["wired_ports_per_room"]} if item["wired_ports_per_room"] else None)
                for n in range(item["rooms"])]
    if kind == "library":
        return [(f"reading-{n+1:02}", f"Reading room {n+1:02}", "reading_room", "reading",
                 {"workstations": min(READING_SEATS_PER_ROOM, item["reading_seats"] - READING_SEATS_PER_ROOM*n)})
                for n in range(math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM))]
    spaces = [(f"lecture-{n+1:03}", f"Lecture hall {n+1:03}", "lecture_hall", "halls",
               {"workstations": 1}) for n in range(item["classrooms"])]
    spaces += [(f"lab-{n+1:02}", f"Teaching lab {n+1:02}", "teaching_lab", "labs",
                {"workstations": min(LAB_SEATS_PER_ROOM, item["lab_seats"] - LAB_SEATS_PER_ROOM*n)})
               for n in range(math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM))]
    spaces += [(f"office-{n+1:02}", f"Faculty office pod {n+1:02}", "office", "offices",
                {"workstations": min(OFFICE_DESKS, item["offices"] - OFFICE_DESKS*n)})
               for n in range(math.ceil(item["offices"] / OFFICE_DESKS))]
    return spaces


def university_slot(kind, slot):
    """Map one permanent room reservation to its floor and local position."""
    if kind == "residence":
        floor, place = slot // DORM_ROOMS_PER_FLOOR + 1, slot % DORM_ROOMS_PER_FLOOR
        return floor, (2 + 2*(place % 25), 2 + 10*(place//25), (floor-1)*FLOOR_HEIGHT_M)
    floor, place = slot // ACADEMIC_ROOMS_PER_FLOOR + 1, slot % ACADEMIC_ROOMS_PER_FLOOR
    return floor, (6 + 12*(place % 4), 4 + 24*(place//4), (floor-1)*FLOOR_HEIGHT_M)


def university_rooms(site, item):
    """Finite campus building grammar with permanent rooms, IDFs and corridors.

    Every room holds a reserved building position for the life of the estate, so
    adding lecture halls appends new rooms above the existing labs and offices
    instead of renumbering the building underneath them.

    ponytail: eight rooms per academic or library floor, fifty rooms per
    residence floor, and eight floors per building — the ceiling the shared
    eight-block management uplink pool supports. Raise that reviewed pool
    before admitting a taller building.
    """
    kind = site.contract["kind"]
    if kind not in {"academic", "residence", "library"}:
        raise DesignError(f"{site.id}: campus rooms require an academic, residence or library building")
    per_floor = DORM_ROOMS_PER_FLOOR if kind == "residence" else ACADEMIC_ROOMS_PER_FLOOR
    spaces = university_spaces(item, kind)
    placed = [(space, site.w.reserve(f"campus-rooms/{site.id}", space[0], MAX_BUILDING_FLOORS*per_floor))
              for space in spaces]
    floors = max((slot for _, slot in placed), default=0) // per_floor + 1
    if floors > MAX_BUILDING_FLOORS:
        raise DesignError(f"{site.id}: {floors} floors exceed the reviewed {MAX_BUILDING_FLOORS}-floor "
                          "building layout and its management attachment pool; split the demand across buildings")
    rooms = dict(halls=[], labs=[], offices=[], residence=[], reading=[], corridors={},
                 reception=None, floors=floors)
    for floor in range(1, floors + 1):
        _campus_floor(site, floor)
    for (suffix, name, space_type, bucket, capacity), slot in placed:
        floor, position = university_slot(kind, slot)
        rooms[bucket].append(_location(site, suffix, name, space_type, floor, position,
                                       _floor(site, floor), capacity))
    for floor in range(1, floors + 1):
        origin = (24, 8, (floor-1)*FLOOR_HEIGHT_M) if kind == "residence" else (24, 40, (floor-1)*FLOOR_HEIGHT_M)
        rooms["corridors"][floor] = _location(site, f"corridor-{floor:02}", f"Floor {floor:02} corridor",
                                              "corridor", floor, origin, _floor(site, floor))
    if kind == "library":
        rooms["reception"] = _location(site, "reception", "Library entrance", "reception", 1,
                                       (6, 40, 0), _floor(site, 1))
    return rooms


def university_endpoint(site, key, room, cohort, ordinal):
    """Place one campus endpoint on its permanent mount and floor-local route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: campus endpoint ordinal must be a positive integer")
    elif role == "role/camera":
        offset = (1 + 8*(ordinal-1), 0)
    else:
        offset = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: campus access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {_site_display(site)}"
    if role == "role/ap":
        node["attrs"]["description"] += "; staff on wlan0, students on wlan1; RF coverage unverified"
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving}, access_channel_length_m=route)


def hospital_rooms(site, demand):
    """Permanent ward floors and finite clinical rooms; no surveyed premises."""
    rooms = dict(wards={}, exams=[], offices=[], imaging=[], corridors={})
    ground = _floor(site, 1)
    rooms["reception"] = _location(site, "reception", "Reception", "reception", 1, (24, 36, 0), ground)
    for ward in demand.get("wards", []):
        key = ward["key"]
        floor = 2 + site.w.reserve(f"healthcare-wards/{site.id}", key, 7)
        parent, height = _floor(site, floor), (floor-1)*FLOOR_HEIGHT_M
        prefix = f"ward-{key}"
        patients = [_location(site, f"{prefix}/patient-{index+1:02}", f"{key} patient room {index+1:02}",
            "patient_room", floor, (6+12*(index%4), 4+24*(index//4), height), parent, {"bed_stations":2})
            for index in range((ward["beds"]+1)//2)]
        nurse = _location(site, f"{prefix}/nurse-station", f"{key} nurse station", "nurse_station", floor,
                          (6,44,height), parent, {"workstations":8})
        corridor = _location(site, f"{prefix}/corridor", f"{key} corridor", "corridor", floor, (24,40,height), parent)
        rooms["wards"][key] = dict(patients=patients,nurse=nurse,corridor=corridor)
        equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,
                              (24,18,height), parent)
        site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    for index in range(demand.get("exam_rooms",0)):
        floor = index//8+1
        rooms["exams"].append(_location(site, f"exam-{index+1:03}", f"Exam room {index+1:03}", "exam_room", floor,
            (6+12*(index%4),4+24*((index%8)//4),(floor-1)*FLOOR_HEIGHT_M), _floor(site,floor), {"workstations":1}))
    for floor in range(1,(demand.get("exam_rooms",0)+7)//8+1):
        height, parent = (floor-1)*FLOOR_HEIGHT_M, _floor(site,floor)
        rooms["corridors"][floor] = _location(site, f"corridor-floor-{floor:02}", f"Floor {floor:02} corridor", "corridor",
                                             floor,(24,40,height),parent)
        if floor > 1:
            equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,(24,18,height),parent)
            site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    clinic = site.contract["kind"] == "clinic"
    for index in range((demand["administrative_desks"]+11)//12):
        rooms["offices"].append(_location(site, f"admin-{index+1:02}", f"Administration {index+1:02}", "office",1,
            (6+12*index,60 if clinic else 50,0),ground,{"workstations":12}))
    for index in range(demand["imaging_rooms"]):
        rooms["imaging"].append(_location(site, f"imaging-{index+1:02}", f"Imaging room {index+1:02}", "imaging_room",1,
            (6+12*(index%4),44 if clinic else 4+24*(index//4),0),ground,{"modalities":1,"workstations":1}))
    return rooms


def hospital_endpoint(site, key, room, cohort, ordinal):
    """Assign clinical inventory to its room and bounded local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif role == "role/camera":
        offset = (1+8*(ordinal-1),0)
    else:
        offset = (1+1.2*((ordinal-1)%6),1+1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0],origin[1]+offset[1],origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: clinical access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {_site_display(site)}"
    if role in {"role/medical-device","role/imaging-device"}:
        node["attrs"]["description"] = f"Reference {cohort.replace('-', ' ')} in {space['attrs']['name']} at {_site_display(site)}"
    elif role == "role/ap":
        node["attrs"]["description"] += "; staff WLAN on wlan0; RF coverage unverified"
    node["meta"].update(cohort=cohort,placement={"room":room,"function":space["meta"]["space_type"],
        "floor":space["meta"]["floor"],"position_m":point,"cable_origin":serving},access_channel_length_m=route)
