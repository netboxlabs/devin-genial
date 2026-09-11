"""District demand policy composed from shared campus, DC and facility rules."""

from copy import deepcopy
from decimal import Decimal
import ipaddress
import re

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation
from .model import DesignError, World, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps"}
CAMPUS_NETWORKS = ("staff", "students", "wireless", "security", "management")
NETWORKS = ("management", "staff", "students", "wireless", "security",
            "applications", "database", "backup", "wan", "storage")
DEFAULT_SCHOOLS = [dict(key="oak", classrooms=4, administrative_staff=8, lab_seats=12, wan_peak_mbps=100),
                   dict(key="ridge", classrooms=12, students_per_classroom=28,
                        wired_seats_per_classroom=6, administrative_staff=18, lab_seats=24, wan_peak_mbps=300)]


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "schools", "demo"}):
        raise DesignError(f"Unknown school recipe fields: {', '.join(sorted(unknown))}; describe classrooms, staff and school WAN demand")
    common = dict(namespace="maple", name="Maple School District", address_pool="10.128.0.0/12")
    common.update({key:value for key,value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key:checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="school-district", demo=raw.get("demo", "baseline"))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("School address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("School rack-user reservations are not implemented; leave reservation_user empty")
    recipe["demo"] = resolve_demo(recipe["demo"])
    schools = raw.get("schools", deepcopy(DEFAULT_SCHOOLS))
    if not isinstance(schools, list) or not 1 <= len(schools) <= 64:
        raise DesignError("schools must be a list of 1–64 keyed campuses; address and physical capacity are checked separately")
    keys, codes, resolved = set(), set(), []
    defaults = dict(classrooms=8, students_per_classroom=24, wired_seats_per_classroom=4,
                    administrative_staff=12, lab_seats=0, wan_peak_mbps=200)
    for supplied in schools:
        if not isinstance(supplied, dict) or "key" not in supplied or supplied.keys() - (defaults.keys() | {"key", "wireless"}):
            raise DesignError("Each school needs a stable key and only supported classroom, staffing, lab and WAN fields")
        key = supplied["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,19}", key) or key in keys:
            raise DesignError("School keys must be unique lowercase identifiers of 1–20 characters")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError(f"School {key}: keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key); codes.add(code)
        item = defaults | deepcopy(supplied)
        for field, low, high in (("classrooms",1,32), ("students_per_classroom",1,36),
                                ("wired_seats_per_classroom",0,12), ("administrative_staff",0,48),
                                ("lab_seats",0,36), ("wan_peak_mbps",1,800)):
            if type(item[field]) is not int or not low <= item[field] <= high:
                raise DesignError(f"School {key}: {field} must be an integer from {low} through {high}")
        if item["wired_seats_per_classroom"] > item["students_per_classroom"]:
            raise DesignError(f"School {key}: wired classroom seats cannot exceed enrolled students per classroom")
        if item["lab_seats"] > item["classrooms"]*(item["students_per_classroom"]-item["wired_seats_per_classroom"]):
            raise DesignError(f"School {key}: lab seats must fit remaining enrollment after classroom wired seats; shared labs do not add students")
        if Decimal(item["wan_peak_mbps"]) > 1000*(1-Decimal(str(recipe["reserve_fraction"]))):
            raise DesignError(f"School {key}: WAN peak plus reserve exceeds the supported 1 Gbps handoff; reduce demand or extend the reviewed campus edge")
        zones = {f"classroom-{n:03}": item["students_per_classroom"] - item["wired_seats_per_classroom"] + 1
                 for n in range(1, item["classrooms"] + 1)}
        zones.update({f"admin-{n+1:02}": min(12, item["administrative_staff"] - 12*n)
                      for n in range((item["administrative_staff"] + 11)//12)})
        if item["lab_seats"]:
            zones["computer-lab"] = 0
        item["wireless"] = places.resolve_wireless(zones, item.get("wireless", {}), f"School {key}")
        resolved.append(item)
    if sum(item["wan_peak_mbps"] for item in resolved) > 16000:
        raise DesignError("District WAN peak exceeds the 16,000 Mbps reviewed DC demand limit; extend aggregation before growing further")
    recipe["schools"] = sorted(resolved, key=lambda item:item["key"])
    return recipe


def demand(item):
    """Declared planning counts; wireless clients remain demand, not fake cables."""
    enrollment = item["classrooms"]*item["students_per_classroom"]
    wired = item["classrooms"]*item["wired_seats_per_classroom"]+item["lab_seats"]
    staff = item["classrooms"]+item["administrative_staff"]
    return dict(enrollment=enrollment, teaching_staff=item["classrooms"], administrative_staff=item["administrative_staff"],
                staff=staff, wired_student_seats=wired, wireless_students=enrollment-wired,
                wireless_staff=staff, workstations=staff+wired,
                managed_clients=sum(zone["managed"] for zone in item["wireless"].values()),
                guest_clients=sum(zone["guest"] for zone in item["wireless"].values()),
                aps=sum(places.wireless_aps(zone) for zone in item["wireless"].values()),
                cameras=2*((item["classrooms"]+7)//8), peak_mbps=item["wan_peak_mbps"])


def workloads(schools):
    students = sum(demand(item)["enrollment"] for item in schools)
    staff = sum(demand(item)["staff"] for item in schools)
    result = []
    for slot, (key, count, size, cpus, memory, disk, port) in enumerate((
            ("identity",students+staff,500,4,8192,100000,443),
            ("dns",len(schools),16,2,4096,40000,53),
            ("learning-portal",students,500,4,8192,100000,443),
            ("files",students,1000,4,16384,500000,445),
            ("monitoring",len(schools),16,4,16384,200000,443))):
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        elif key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812,1813]))
        result.append(dict(key=key, slot=slot, instances=2*max(1,(count+size-1)//size), replicas=2,
            failure_domain="rack", network="applications", vcpus=cpus, memory_mb=memory, disk_mb=disk,
            listeners=listeners, criticality="tier-1" if key in {"identity","dns","learning-portal"} else "tier-2",
            replica_description="complete district service shard; application replication and recovery are not executed"))
    return result


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects","contracts")):
            raise DesignError("Previous school plan does not reproduce from its recipe and ledgers; use an intact frozen plan or new baseline")
        old = {item["key"]:item for item in previous["recipe"]["schools"]}
        current = {item["key"]:item for item in recipe["schools"]}
        if old.keys() - current.keys():
            raise DesignError("Removing schools requires a new baseline; Diode replay does not delete retired objects")
        for key, before in old.items():
            after = current[key]
            if any(after[field] != before[field] for field in ("students_per_classroom","wired_seats_per_classroom")):
                raise DesignError(f"School {key}: changing classroom design requires a new baseline; add classrooms to grow in place")
            if any(after[field] < before[field] for field in ("classrooms","administrative_staff","lab_seats","wan_peak_mbps")):
                raise DesignError(f"School {key}: decreasing demand requires a new baseline; ordinary growth cannot retire objects")
            if after["wan_peak_mbps"] != before["wan_peak_mbps"]:
                raise DesignError(f"School {key}: WAN renewal requires a new baseline; retained procurement is immutable during campus growth")
            if any(zone not in after["wireless"] or any(after["wireless"][zone][field] < values[field]
                    for field in ("managed", "guest")) for zone, values in before["wireless"].items()):
                raise DesignError(f"School {key}: reducing or removing a wireless zone requires a new baseline")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    world.reserve_sites(["dc-01"]+[f"school-{item['key']}" for item in recipe["schools"]])
    guest_sites = {f"site/school-{item['key']}" for item in recipe["schools"] if any(z["guest"] for z in item["wireless"].values())}
    foundation(world, industry="school district", inherited=False, networks=NETWORKS + (("guest",) if guest_sites else ()), site_kinds={"school","dc"},
        device_roles=("wan-edge","distribution","access","spine","leaf","server","management","pdu",
                      "workstation","ap","camera","patch-panel","wall-outlet"),
        hardware_aliases={"core","leaf","edge","server","access","pdu","console-server","endpoint","ap","patch-panel","wall-outlet"})
    dc = Site(world,"dc-01","dc","District services data center; shared authentication, learning and operations")
    datacenter.build(dc,workloads=workloads(recipe["schools"]),
        wan_peak_mbps=sum(item["wan_peak_mbps"] for item in recipe["schools"]), include_equipment=False,
        assumptions=["District DC WAN covers the sum of school peaks under a synthetic centralized-service traffic model.",
            "Each complete replica group is separated across hosts and compute racks; routing, application replication and recovery are not executed.",
            "Auth groups cover 500 enrolled students/staff; portal groups 500 students; file groups 1000 students; DNS/monitoring groups 16 schools, minimum one each. These are fictional planning thresholds, not throughput ratings.",
            "Identity includes RADIUS UDP1812/1813 inventory for enterprise WLAN authentication intent; no authentication server or WLAN controller is running."])
    campuses = []
    for item in recipe["schools"]:
        site = Site(world,f"school-{item['key']}","school","School campus; classroom, administration and shared lab demand")
        campuses.append(world.obj(site.key))
        _campus(site,item)
    networking.wireless(world,campuses,lan_roles=(("staff","staff","wlan0"),("students","students","wlan1")),
                       diagnostic=False,stable_channels=True,guest_sites=guest_sites)
    equipment.enrich(world)
    optics.enrich(world)
    poe.enrich(world)
    ipv6.enrich(world)
    networking.macs(world)
    operations.supporting_records(world)
    return world.finish()


def _campus(site,item):
    classrooms,offices,lab = places.school_rooms(site,item)
    networks = CAMPUS_NETWORKS + (("guest",) if any(z["guest"] for z in item["wireless"].values()) else ())
    dist,edges,upstreams = campus.aggregation(site,networks,item["wan_peak_mbps"])
    endpoints = []

    def add(label,role,segment,room,cohort,ordinal=1):
        key = site.device("ap" if role == "ap" else "endpoint",label,role,racked=False,
                          meta={"endpoint":True,"network":segment,"power_scope":"local outlet or PoE"})
        places.school_endpoint(site,key,room,cohort,ordinal)
        endpoints.append((key,segment))

    def aps(zone, label, room, cohort):
        for ordinal in range(1, places.wireless_aps(item["wireless"][zone]) + 1):
            add(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, cohort, ordinal)

    for number,room in enumerate(classrooms,1):
        add(f"teacher-{number:03}","workstation","staff",room,"teacher")
        for seat in range(1,item["wired_seats_per_classroom"]+1):
            add(f"student-{number:03}-{seat:02}","workstation","students",room,"student",seat)
        aps(f"classroom-{number:03}", f"ap-classroom-{number:03}", room, "classroom-ap")
    for index in range(item["administrative_staff"]):
        add(f"admin-{index+1:03}","workstation","staff",offices[index//12],"administration",index%12+1)
    for number,room in enumerate(offices,1):
        aps(f"admin-{number:02}", f"ap-admin-{number:02}", room, "office-ap")
    for index in range(item["lab_seats"]):
        add(f"lab-{index+1:03}","workstation","students",lab,"student-lab",index+1)
    if lab:
        aps("computer-lab", "ap-lab-01", lab, "lab-ap")
    for floor in range(1,(item["classrooms"]+7)//8+1):
        for ordinal,side in enumerate(("a","b"),1):
            add(f"camera-floor-{floor:02}-{side}","camera","security",classrooms[(floor-1)*8],"camera",ordinal)
    facts = campus.access(site,endpoints,upstreams,networks,
                          {"access_hardware":"access","upstreams":2,"label":"access-"},stable=True)
    site.contract.update(endpoint_count=len(endpoints),demand=demand(item),access_hardware="access",
                         access_devices=facts["access_devices"],access_usable_ports=facts["access_usable_ports"],
                         required_device_roles={"role/wan-edge":2,"role/distribution":2,"role/access":facts["access_count"]})
    site.contract["assumptions"].extend([
        "All district campuses and the service DC share one authored metro. School street numbers follow stable site reservations; these are fictional premises.",
        "Eight classrooms fit each authored floor, up to four floors. One wired teacher desk serves each classroom; administration uses separate twelve-desk rooms.",
        "Teaching staff equals classroom count. Administrative staff is additional. Shared lab seats are occupied by enrolled students, never added to enrollment.",
        "Wireless managed/guest counts are explicit local concurrent device budgets, separate from enrollment and installed seats; classroom defaults do not redistribute demand when the shared lab grows. No wireless clients are represented as cabled devices.",
        "Each classroom, administration pod and occupied lab retains a coverage AP; local planned devices grow APs at 32 per AP, up to four fixed mounts. This is an authored equipment envelope, not surveyed RF capacity or observed associations.",
        "Access switches grow in pairs per floor closet. Their reserved endpoint port assignments and direct uplinks stay fixed; endpoints themselves remain single-homed.",
        "Two modeled carriers and gateways do not establish diverse ducts or running routing, firewall, authentication or HA behavior."])
    equipment.enrich_site(site,demonstrations=False)
    site.management(dist)
    site.power()
