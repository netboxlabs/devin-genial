"""Authored Great Lakes geography and finite, fictional building layouts.

Time-zone labels follow IANA tzdb northamerica and zone1970.tab:
https://data.iana.org/time-zones/tzdb/northamerica
https://data.iana.org/time-zones/tzdb/zone1970.tab
Street addresses, rooms, dimensions and routes are design inputs, not surveyed
properties. No precise GPS coordinates or RF/electrical compliance are invented.
"""

import hashlib
import math

from .model import DesignError


METROS = (
    ("Chicago", "IL", "Illinois", "America/Chicago"),
    ("Detroit", "MI", "Michigan", "America/Detroit"),
    ("Cleveland", "OH", "Ohio", "America/New_York"),
    ("Milwaukee", "WI", "Wisconsin", "America/Chicago"),
)
GROUPS = {"branch": "Retail branches", "hq": "Headquarters", "dc": "Data centers", "school": "Schools",
          "hospital": "Hospitals", "clinic": "Outpatient clinics", "pop": "Provider PoPs", "customer": "Customer premises"}
MAX_CHANNEL_M = 80
FLOOR_HEIGHT_M = 4
OFFICE_DESKS = 12
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
}


def foundation(w, *, site_kinds=None):
    """Publish namespaced shared geography before any site references it."""
    ns = w.recipe["namespace"]
    root = f"region/{ns}/us"
    lakes = f"{root}/great-lakes"
    for key, name, slug, parent in ((root, "United States", "us", None),
                                    (lakes, "Great Lakes", "great-lakes", root)):
        w.add("region", key, {"name": f"{ns} {name}", "slug": f"{ns}-{slug}"},
              {"parent": parent} if parent else {})
    for _, code, state, _ in METROS:
        w.add("region", f"{root}/{code.lower()}",
              {"name": f"{ns} {state}", "slug": f"{ns}-{code.lower()}"}, {"parent": lakes})
    for kind, name in GROUPS.items():
        if site_kinds is None and kind in {"school", "hospital", "clinic", "pop", "customer"}:
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
    elif site.id in {"dc-01", "dc-02"}:
        metro_index = int(site.id[-2:]) - 1
    else:
        metro_index = int.from_bytes(hashlib.sha256(site.id.encode()).digest()[:4], "big") % len(METROS)
    city, state_code, state, zone = METROS[metro_index]
    node = site.w.obj(site.key)
    suffix = "".join(character for character in site.id if character.isdigit())
    number = 100 + 4 * int(suffix or "0")
    if kind in {"school", "hospital", "clinic"}:
        number = 100 + 4 * site.w.allocations[site.id]
    streets = {"br-s": "Market Street", "br-m": "Commerce Drive", "br-l": "Harbor Avenue",
               "hq": "Lakefront Boulevard", "dc": "Technology Way", "school-": "Learning Way",
               "hospital-": "Care Avenue", "clinic-": "Community Way"}
    street = next((name for prefix, name in streets.items() if site.id.startswith(prefix)), "Commerce Way")
    node["attrs"].update(time_zone=zone,
                         physical_address=f"{number} {street}\n{city}, {state}\nUnited States")
    node["refs"].update(region=f"region/{site.w.recipe['namespace']}/us/{state_code.lower()}",
                        group=f"site-group/{site.w.recipe['namespace']}/{kind}")
    node["meta"]["geography"] = {"country": "US", "state": state_code, "city": city, "synthetic": True}
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
    city, code, state, zone = next(row for row in METROS if row[0].lower() == metro)
    street = {"pop": "Exchange Avenue", "customer": "Business Way", "dc": "Technology Way"}[kind]
    node = w.obj(site.key)
    node["attrs"].update(time_zone=zone, physical_address=f"{100+4*w.allocations[site.id]} {street}\n{city}, {state}\nUnited States")
    node["refs"].update(region=f"region/{w.recipe['namespace']}/us/{code.lower()}",
                        group=f"site-group/{w.recipe['namespace']}/{kind}")
    node["meta"]["geography"] = dict(country="US", state=code, city=city, synthetic=True)
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
    node["attrs"]["description"] = f"{purpose} {ordinal:03} in {space['attrs']['name']} at {site.name}"
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
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {site.name}"
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
    node["attrs"]["description"] = f"{cohort.replace('-', ' ').title()} in {space['attrs']['name']} at {site.name}"
    if role in {"role/medical-device","role/imaging-device"}:
        node["attrs"]["description"] = f"Reference {cohort.replace('-', ' ')} in {space['attrs']['name']} at {site.name}"
    elif role == "role/ap":
        node["attrs"]["description"] += "; staff WLAN on wlan0; RF coverage unverified"
    node["meta"].update(cohort=cohort,placement={"room":room,"function":space["meta"]["space_type"],
        "floor":space["meta"]["floor"],"position_m":point,"cable_origin":serving},access_channel_length_m=route)
