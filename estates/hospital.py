"""Synthetic care-unit demand composed from shared campus and DC builders."""

from copy import deepcopy
from decimal import Decimal
import ipaddress
import re

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation
from .model import DesignError, World, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps"}
CAMPUS_NETWORKS = ("clinical", "medical", "imaging", "staff", "wireless", "security", "management")
NETWORKS = ("management", "clinical", "medical", "wireless", "security", "applications",
            "database", "backup", "wan", "storage", "staff", "imaging")
HOSPITAL_DEFAULTS = dict(administrative_desks=12, imaging_rooms=1, wan_peak_mbps=300)
CLINIC_DEFAULTS = dict(exam_rooms=6, administrative_desks=4, imaging_rooms=0, wan_peak_mbps=100)
WARD_DEFAULTS = dict(beds=8, clinical_desks=4)
DEFAULT_HOSPITALS = [dict(key="central", administrative_desks=24, imaging_rooms=2, wan_peak_mbps=600,
                        wards=[dict(key="medical-a", beds=12, clinical_desks=4),
                               dict(key="medical-b", beds=16, clinical_desks=6)])]
DEFAULT_CLINICS = [dict(key="west", exam_rooms=8, administrative_desks=4, imaging_rooms=1, wan_peak_mbps=100),
                   dict(key="north", exam_rooms=12, administrative_desks=8, imaging_rooms=0, wan_peak_mbps=200)]


def _items(supplied, label, minimum, maximum, defaults, bounds, extra=()):
    if not isinstance(supplied, list) or not minimum <= len(supplied) <= maximum:
        raise DesignError(f"{label} must be a list of {minimum}–{maximum} keyed entries; combined physical capacity is checked separately")
    keys, codes, result = set(), set(), []
    for raw in supplied:
        if not isinstance(raw, dict) or "key" not in raw or raw.keys() - (defaults.keys() | {"key"} | set(extra)):
            raise DesignError(f"Each {label} entry needs a key and only supported demand fields")
        key = raw["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,19}", key) or key in keys:
            raise DesignError(f"{label} keys must be unique lowercase identifiers of 1–20 characters")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError(f"{label} keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key); codes.add(code)
        item = defaults | deepcopy(raw)
        for field, low, high in bounds:
            if type(item[field]) is not int or not low <= item[field] <= high:
                raise DesignError(f"{label} {key}: {field} must be an integer from {low} through {high}")
        result.append(item)
    return sorted(result, key=lambda item:item["key"])


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "hospitals", "clinics", "demo"}):
        raise DesignError(f"Unknown hospital recipe fields: {', '.join(sorted(unknown))}; describe wards, care rooms and WAN demand")
    common = dict(namespace="lakeshore", name="Lakeshore Health System", address_pool="10.128.0.0/12")
    common.update({key:value for key,value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key:checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="hospital-clinics", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("Hospital address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("Hospital rack-user reservations are not implemented; leave reservation_user empty")
    hospitals = _items(raw.get("hospitals", DEFAULT_HOSPITALS), "hospitals", 1, 8, HOSPITAL_DEFAULTS,
        (("administrative_desks",0,48), ("imaging_rooms",0,8), ("wan_peak_mbps",1,800)), ("wards", "wireless"))
    for item in hospitals:
        item["wards"] = _items(item.get("wards", [dict(key="medical", **WARD_DEFAULTS)]), f"hospital {item['key']} wards", 1, 7,
                               WARD_DEFAULTS, (("beds",4,16), ("clinical_desks",2,8)))
    clinics = _items(raw.get("clinics", DEFAULT_CLINICS), "clinics", 0, 64, CLINIC_DEFAULTS,
        (("exam_rooms",1,24), ("administrative_desks",0,24), ("imaging_rooms",0,4), ("wan_peak_mbps",1,800)), ("wireless",))
    for item in hospitals+clinics:
        if Decimal(item["wan_peak_mbps"]) > 1000*(1-Decimal(str(recipe["reserve_fraction"]))):
            raise DesignError(f"Facility {item['key']}: WAN peak plus reserve exceeds the supported 1 Gbps handoff; extend the reviewed campus edge")
        zones = {f"ward-{ward['key']}": 24 for ward in item.get("wards", [])}
        exams = item.get("exam_rooms", 0)
        zones.update({f"exam-{n+1:03}": 4*min(4, exams-4*n) for n in range((exams+3)//4)})
        zones.update({f"admin-{n+1:02}": min(12, item["administrative_desks"]-12*n)
                      for n in range((item["administrative_desks"]+11)//12)})
        zones.update({f"imaging-{n+1:02}": 4 for n in range(item["imaging_rooms"])})
        zones["reception"] = 4
        item["wireless"] = places.resolve_wireless(zones, item.get("wireless", {}), f"Facility {item['key']}")
    if sum(item["wan_peak_mbps"] for item in hospitals+clinics) > 16000:
        raise DesignError("Health-system WAN peak exceeds the 16,000 Mbps reviewed DC demand limit; extend aggregation before growing further")
    recipe.update(hospitals=hospitals, clinics=clinics)
    return recipe


def demand(item):
    """Installed inventory only: bed stations and desks do not imply headcounts."""
    wards = item.get("wards", [])
    beds = sum(ward["beds"] for ward in wards)
    nurse_desks = sum(ward["clinical_desks"] for ward in wards)
    exams = item.get("exam_rooms", 0)
    imaging = item["imaging_rooms"]
    clinical = nurse_desks+exams+imaging
    admin = item["administrative_desks"]
    floors = len(wards)+1 if wards else (exams+7)//8
    aps = sum(places.wireless_aps(zone, 2 if key.startswith("ward-") else 1)
              for key, zone in item["wireless"].items())
    cameras = 2*floors
    return dict(bed_stations=beds, wards=len(wards), nurse_workstations=nurse_desks, exam_rooms=exams,
                imaging_rooms=imaging, clinical_workstations=clinical, administrative_desks=admin,
                workstations=clinical+admin, medical_devices=beds, imaging_devices=imaging,
                aps=aps, cameras=cameras, floors=floors,
                managed_clients=sum(zone["managed"] for zone in item["wireless"].values()),
                guest_clients=sum(zone["guest"] for zone in item["wireless"].values()),
                endpoints=clinical+admin+beds+imaging+aps+cameras, peak_mbps=item["wan_peak_mbps"])


def workloads(facilities):
    counts = [demand(item) for item in facilities]
    total = lambda field: sum(item[field] for item in counts)
    result = []
    for slot, (key, count, size, cpus, memory, disk, port) in enumerate((
            ("identity",total("workstations"),250,4,8192,100000,443),
            ("dns",len(facilities),16,2,4096,40000,53),
            ("clinical-records",total("clinical_workstations"),100,8,16384,200000,443),
            ("imaging-archive",total("imaging_rooms"),16,8,32768,2000000,11112),
            ("monitoring",total("endpoints"),500,4,16384,200000,443))):
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        elif key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812,1813]))
        result.append(dict(key=key, slot=slot, instances=2*max(1,(count+size-1)//size), replicas=2,
            failure_domain="rack", network="applications", vcpus=cpus, memory_mb=memory, disk_mb=disk,
            listeners=listeners, criticality="tier-1" if key in {"identity","dns","clinical-records"} else "tier-2",
            replica_description="complete health-system service shard; application replication and recovery are not executed"))
    return result


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects","contracts")):
            raise DesignError("Previous hospital plan does not reproduce from its recipe and ledgers; use an intact frozen plan or new baseline")
        for field in ("hospitals","clinics"):
            old = {item["key"]:item for item in previous["recipe"][field]}
            current = {item["key"]:item for item in recipe[field]}
            if old.keys() - current.keys():
                raise DesignError(f"Removing {field} requires a new baseline; Diode replay does not delete retired objects")
            for key,before in old.items():
                after = current[key]
                if any(after[name] < before[name] for name in before.keys()-{"key","wards","wireless"}):
                    raise DesignError(f"Facility {key}: decreasing demand requires a new baseline")
                if after["wan_peak_mbps"] != before["wan_peak_mbps"]:
                    raise DesignError(f"Facility {key}: WAN renewal requires a new baseline; retained procurement is immutable during growth")
                if any(zone not in after["wireless"] or any(after["wireless"][zone][name] < values[name]
                        for name in ("managed", "guest")) for zone, values in before["wireless"].items()):
                    raise DesignError(f"Facility {key}: reducing or removing a wireless zone requires a new baseline")
                if field == "hospitals":
                    wards = {ward["key"]:ward for ward in after["wards"]}
                    for ward in before["wards"]:
                        if ward["key"] not in wards or any(wards[ward["key"]][name] < ward[name] for name in ("beds","clinical_desks")):
                            raise DesignError(f"Hospital {key}: removing wards or reducing ward demand requires a new baseline")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    world.reserve_sites(["dc-01"]+[f"hospital-{item['key']}" for item in recipe["hospitals"]]+
                        [f"clinic-{item['key']}" for item in recipe["clinics"]])
    guest_sites = {f"site/{kind}-{item['key']}" for kind, items in (("hospital",recipe["hospitals"]),("clinic",recipe["clinics"]))
                   for item in items if any(z["guest"] for z in item["wireless"].values())}
    foundation(world, industry="health system", inherited=False, networks=NETWORKS + (("guest",) if guest_sites else ()), site_kinds={"hospital","clinic","dc"},
        device_roles=("wan-edge","distribution","access","spine","leaf","server","management","pdu",
                      "workstation","medical-device","imaging-device","ap","camera","patch-panel","wall-outlet"),
        hardware_aliases={"core","leaf","edge","server","access","pdu","console-server","endpoint","ap","patch-panel","wall-outlet"})
    facilities = recipe["hospitals"]+recipe["clinics"]
    dc = Site(world,"dc-01","dc","Health-system services data center; shared clinical, imaging and operational applications")
    datacenter.build(dc, workloads=workloads(facilities), wan_peak_mbps=sum(item["wan_peak_mbps"] for item in facilities),
        include_equipment=False, assumptions=[
            "Central DC WAN covers the sum of explicit facility peaks under a synthetic centralized-service traffic model; this is not measured imaging throughput.",
            "Identity groups cover 250 installed workstations; clinical-record groups 100 clinical workstations; archive groups 16 imaging rooms; DNS groups 16 facilities; monitoring groups 500 campus endpoints, minimum one each. These are fictional planning thresholds, not clinical performance or retention sizing.",
            "Each complete replica group uses separate hosts and compute racks. No clinical application, DICOM transfer, replication, recovery, routing or authentication is executed.",
            "The synthetic archive listener uses TCP11112, a configurable DICOM transport convention; this is neither a secure clinical deployment nor certification of the reference medical endpoints."])
    campuses = []
    for kind,items in (("hospital",recipe["hospitals"]),("clinic",recipe["clinics"])):
        for item in items:
            site = Site(world,f"{kind}-{item['key']}",kind,
                        "Hospital care units, diagnostics and administration" if kind == "hospital" else "Outpatient examination, diagnostics and administration")
            campuses.append(world.obj(site.key))
            _campus(site,item)
    networking.wireless(world,campuses,lan_roles=(("staff","staff","wlan0"),),diagnostic=False,stable_channels=True,guest_sites=guest_sites)
    equipment.enrich(world)
    optics.enrich(world)
    poe.enrich(world)
    ipv6.enrich(world)
    networking.macs(world)
    operations.supporting_records(world)
    names = set()
    for obj in world.objects.values():
        if obj["kind"] == "device":
            identity = (obj["refs"]["site"],obj["attrs"]["name"])
            if identity in names:
                raise DesignError(f"{obj['key']}: generated device names collide; choose distinct ward keys after device-name abbreviation")
            names.add(identity)
    return world.finish()


def _campus(site,item):
    rooms = places.hospital_rooms(site,item)
    networks = CAMPUS_NETWORKS + (("guest",) if any(z["guest"] for z in item["wireless"].values()) else ())
    dist,edges,upstreams = campus.aggregation(site,networks,item["wan_peak_mbps"])
    endpoints = []

    def add(label,role,segment,room,cohort,ordinal=1):
        key = site.device("ap" if role == "ap" else "endpoint",label,role,racked=False,
                          meta={"endpoint":True,"network":segment,"power_scope":"local outlet or PoE"})
        places.hospital_endpoint(site,key,room,cohort,ordinal)
        endpoints.append((key,segment))

    def aps(zone, label, room, cohort):
        for ordinal in range(1, places.wireless_aps(item["wireless"][zone]) + 1):
            add(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, cohort, ordinal)

    for ward in item.get("wards", []):
        key,space = ward["key"],rooms["wards"][ward["key"]]
        for index in range(ward["beds"]):
            add(f"monitor-{key}-{index+1:03}","medical-device","medical",space["patients"][index//2],"bedside-monitor",index%2+1)
        for index in range(ward["clinical_desks"]):
            add(f"nurse-{key}-{index+1:03}","workstation","clinical",space["nurse"],"nurse-workstation",index+1)
        for index in range(1, places.wireless_aps(item["wireless"][f"ward-{key}"], 2) + 1):
            add(f"ap-ward-{key}-{index:02}","ap","wireless",space["corridor"],"ward-ap",index)
        for index,side in enumerate(("a","b"),1):
            add(f"camera-ward-{key}-{side}","camera","security",space["corridor"],"corridor-camera",index)
    for number,room in enumerate(rooms["exams"],1):
        add(f"exam-{number:03}","workstation","clinical",room,"exam-workstation")
    for index in range((item.get("exam_rooms",0)+3)//4):
        label = f"ap-exam-{index+1:03}"
        for ordinal in range(1, places.wireless_aps(item["wireless"][f"exam-{index+1:03}"]) + 1):
            add(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless",
                rooms["corridors"][index//2+1], "exam-ap", 2*(ordinal-1) + index%2+1)
    for index in range(item["administrative_desks"]):
        add(f"admin-{index+1:03}","workstation","staff",rooms["offices"][index//12],"administration",index%12+1)
    for number,room in enumerate(rooms["offices"],1):
        aps(f"admin-{number:02}", f"ap-admin-{number:02}", room, "office-ap")
    for number,room in enumerate(rooms["imaging"],1):
        add(f"imaging-{number:02}","imaging-device","imaging",room,"imaging-modality")
        add(f"diagnostic-{number:02}","workstation","clinical",room,"diagnostic-workstation",2)
        aps(f"imaging-{number:02}", f"ap-imaging-{number:02}", room, "imaging-ap")
    aps("reception", "ap-reception-01", rooms["reception"], "reception-ap")
    for floor,room in {1:rooms["reception"], **{floor:room for floor,room in rooms["corridors"].items() if floor > 1}}.items():
        for number,side in enumerate(("a","b"),1):
            add(f"camera-{'ground' if floor == 1 else f'floor-{floor:02}'}-{side}","camera","security",room,"corridor-camera",number)
    facts = campus.access(site,endpoints,upstreams,networks,
                          {"access_hardware":"access","upstreams":2,"label":"access-"},stable=True)
    site.contract.update(endpoint_count=len(endpoints),demand=demand(item),access_hardware="access",
        access_devices=facts["access_devices"],access_usable_ports=facts["access_usable_ports"],
        required_device_roles={"role/wan-edge":2,"role/distribution":2,"role/access":facts["access_count"]})
    site.contract["assumptions"].extend([
        "One authored metro contains this health system. Addresses, care units, floor geometry and staffing relationships are fictional, not actual healthcare premises.",
        "Each hospital ward has a permanent floor, two-bed patient rooms, a nurse station and corridor. A clinic has eight exam rooms per floor. Added demand fills permanent room and port slots without moving earlier endpoints.",
        "Each bed station has one reference bedside monitor; each imaging room has one reference modality and one diagnostic workstation. These generic NIC devices have no medical certification or clinical function.",
        "Two coverage APs serve each ward; one serves each four-exam pod, administrative pod, imaging room and reception. Explicit local managed/guest device budgets grow APs at 32 per AP, up to four mounts per zone; these are planning envelopes, not measured RF capacity or people counts.",
        "Cameras are in corridors and reception, never patient, examination or imaging rooms. Desks and bed stations are installed capacity, not staff headcount or daily patient volume.",
        "Separate clinical, medical, imaging, staff and management segments express intended boundaries; no firewall policy, clinical safety or regulatory compliance is demonstrated.",
        "Access pairs retain room-local ports and upstream attachments. Endpoints remain single-homed; separate carriers do not establish diverse ducts or running HA behavior."])
    equipment.enrich_site(site,demonstrations=False)
    site.management(dist)
    site.power()
