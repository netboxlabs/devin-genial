"""Retail chain demand: a store fleet, distribution centres and shared commerce DCs.

Store, warehouse and headquarters demand is authored planning policy. Shared
builders allocate every device, port, address, rack and cable; independent checks
inspect the finished graph. Sizing thresholds are fictional assumptions, never
vendor performance ratings, measured transaction volume or surveyed RF coverage.
"""

from decimal import Decimal
import ipaddress
import math

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation
from .model import DesignError, World, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps", "headquarters_staff",
          "naming", "site_names", "hardware"}
STORE_NETWORKS = ("backoffice", "pos", "wireless", "security", "guest", "management")
DISTRIBUTION_NETWORKS = ("backoffice", "wireless", "security", "management")
OFFICE_NETWORKS = ("backoffice", "wireless", "security", "guest", "management")
NETWORKS = ("management", "backoffice", "pos", "wireless", "security", "applications",
            "database", "backup", "wan", "storage", "guest")
# Authored store formats. Counts are installed equipment, not staff rosters,
# shoppers, transactions per hour or measured traffic.
STORES = {"small": dict(workstations=2, pos_terminals=4, aps=2, cameras=4, peak_mbps=20),
          "medium": dict(workstations=4, pos_terminals=8, aps=3, cameras=8, peak_mbps=50),
          "large": dict(workstations=8, pos_terminals=16, aps=5, cameras=12, peak_mbps=100)}
# One authored distribution-centre format: heavier camera and radio coverage,
# handheld scanners instead of point-of-sale lanes.
DISTRIBUTION = dict(workstations=12, scanners=24, aps=8, cameras=24, peak_mbps=200)
DEFAULT_STORES = {"small": 4, "medium": 2, "large": 1}
# Fictional service sizing assumptions; stores_per_group is a planning threshold.
SERVICES = (
    ("commerce-api", "applications", 4, 8192, 100000, "stores", 40, 443),
    ("pos-gateway", "applications", 4, 8192, 100000, "stores", 30, 443),
    ("inventory-db", "database", 8, 32768, 500000, "stores", 100, 5432),
    ("loyalty", "applications", 4, 8192, 100000, "stores", 120, 443),
    ("identity", "applications", 4, 8192, 100000, "endpoints", 2000, 443),
    ("dns", "applications", 2, 4096, 40000, "sites", 16, 53),
    ("monitoring", "applications", 4, 16384, 200000, "endpoints", 4000, 443),
    ("backup", "backup", 4, 16384, 1000000, "stores", 100, 443),
)


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "stores", "headquarters",
                                          "distribution_centers", "demo"}):
        raise DesignError(f"Unknown retail recipe fields: {', '.join(sorted(unknown))}; "
                          "describe store counts, headquarters and distribution demand")
    common = dict(namespace="harvest", name="Harvest Retail Group", address_pool="10.0.0.0/8")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="retail-chain", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("Retail address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("Retail rack-user reservations are not implemented; leave reservation_user empty")
    stores = raw.get("stores", dict(DEFAULT_STORES))
    if not isinstance(stores, dict) or stores.keys() - set(STORES):
        raise DesignError("stores must contain only small, medium and large counts")
    stores = {size: stores.get(size, 0) for size in STORES}
    if any(type(n) is not int or not 0 <= n <= 2000 for n in stores.values()):
        raise DesignError("Each store count must be an integer between 0 and 2000")
    if not sum(stores.values()):
        raise DesignError("A retail chain needs at least one store; request a small, medium or large count")
    recipe["stores"] = stores
    for key, low, high, default in (("headquarters", 0, 1, 1), ("distribution_centers", 0, 6, 1)):
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise DesignError(f"{key} must be an integer between {low} and {high} for this profile")
        recipe[key] = value
    usable = 1000 * (1 - Decimal(str(recipe["reserve_fraction"])))
    for label, peak in (("Headquarters", 2*recipe["headquarters_staff"] if recipe["headquarters"] else 0),
                        ("Distribution centre", DISTRIBUTION["peak_mbps"] if recipe["distribution_centers"] else 0)):
        if Decimal(peak) > usable:
            raise DesignError(f"{label}: WAN peak plus reserve exceeds the supported 1 Gbps handoff; reduce demand or extend the reviewed site edge")
    if total_peak(recipe) > 16000:
        raise DesignError("Chain WAN peak exceeds the 16,000 Mbps reviewed DC demand limit; extend aggregation before growing further")
    return recipe


def office_demand(recipe):
    """Authored headquarters planning demand: two Mb/s per staff position, one
    AP per twelve-desk pod and two cameras per occupied floor. No RF or traffic
    measurement is represented."""
    staff = recipe["headquarters_staff"]
    pods = math.ceil(staff / 12)
    return dict(workstations=staff, atms=0, aps=pods, cameras=2*math.ceil(pods/4), peak_mbps=2*staff)


def total_peak(recipe):
    return (sum(n*STORES[size]["peak_mbps"] for size, n in recipe["stores"].items())
            + recipe["headquarters"]*office_demand(recipe)["peak_mbps"]
            + recipe["distribution_centers"]*DISTRIBUTION["peak_mbps"])


def counts(recipe):
    """Independent demand totals used to size the shared commerce services."""
    stores = sum(recipe["stores"].values())
    endpoints = sum(n*sum(STORES[size][field] for field in ("workstations", "pos_terminals", "aps", "cameras"))
                    for size, n in recipe["stores"].items())
    endpoints += recipe["distribution_centers"]*sum(
        DISTRIBUTION[field] for field in ("workstations", "scanners", "aps", "cameras"))
    if recipe["headquarters"]:
        demand = office_demand(recipe)
        endpoints += sum(demand[field] for field in ("workstations", "aps", "cameras"))
    return dict(stores=stores, endpoints=endpoints,
                sites=stores + recipe["headquarters"] + recipe["distribution_centers"])


def workloads(recipe):
    """Size shared commerce services from resolved chain demand, never inventory."""
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
                           listeners=listeners,
                           criticality="tier-1" if key in {"commerce-api", "pos-gateway", "inventory-db", "identity", "dns"} else "tier-2",
                           replica_description="complete chain service shard; application replication and recovery are not executed"))
    return result


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous retail plan does not reproduce from its recipe and ledgers; use an intact frozen plan or new baseline")
        del reproduced
        old = previous["recipe"]
        for size, count in old["stores"].items():
            if recipe["stores"][size] < count:
                raise DesignError(f"Removing {size} stores requires a new baseline; Diode replay does not delete retired objects")
        for key in ("headquarters", "distribution_centers"):
            if recipe[key] < old[key]:
                raise DesignError(f"Reducing {key} requires a new baseline; ordinary growth cannot retire objects")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    store_ids = [(f"st-{size[0]}{i+1:04}", size) for size, n in r["stores"].items() for i in range(n)]
    ids = (["dc-01", "dc-02"] + [f"hq-{i+1:02}" for i in range(r["headquarters"])]
           + [f"di-{i+1:02}" for i in range(r["distribution_centers"])] + [key for key, _ in store_ids])
    world.reserve_sites(ids)
    foundation(world, industry="retail chain", inherited=False, networks=NETWORKS,
               site_kinds={"dc", "hq", "store", "distribution"},
               device_roles=("wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
                             "pdu", "workstation", "pos-terminal", "scanner", "ap", "camera",
                             "patch-panel", "wall-outlet"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server",
                                 "endpoint", "ap", "patch-panel", "wall-outlet"})
    peak = total_peak(r)
    for i in range(2):
        dc = Site(world, f"dc-{i+1:02}", "dc",
                  "Commerce services data center; paired trading, inventory and operations applications")
        datacenter.build(dc, workloads=workloads(r), wan_peak_mbps=peak, include_equipment=False, assumptions=[
            "Each DC WAN budget covers all declared store, distribution and headquarters peak demand independently; this is a modeled capacity assumption, not measured trading throughput.",
            "Shared commerce services are deployed in both DCs and each complete replica group is separated across compute racks; application replication, failover and recovery are not executed.",
            "Service groups cover 40 stores for commerce-api, 30 for pos-gateway, 100 for inventory-db and backup, 120 for loyalty, 2,000 installed endpoints for identity, 4,000 for monitoring and 16 sites for DNS, minimum one each. These are fictional planning thresholds, not transaction ratings.",
            "Identity includes RADIUS UDP1812/1813 inventory for staff WLAN authentication intent; no authentication server, payment application or card-data environment is running or certified.",
        ])
    radio_sites, guest_sites = [], set()
    for i in range(r["headquarters"]):
        site = Site(world, f"hq-{i+1:02}", "hq",
                    "Support centre campus: merchandising, commercial and operations staff")
        _office(site, office_demand(r))
        radio_sites.append(world.obj(site.key))
        guest_sites.add(site.key)
    for i in range(r["distribution_centers"]):
        site = Site(world, f"di-{i+1:02}", "distribution",
                    "Distribution centre: warehouse handling, receiving and local administration")
        _campus(site, DISTRIBUTION, DISTRIBUTION_NETWORKS)
        radio_sites.append(world.obj(site.key))
    for key, size in store_ids:
        site = Site(world, key, "store",
                    f"{size.title()}-format retail store; {STORES[size]['pos_terminals']} installed point-of-sale lanes")
        world.obj(site.key)["meta"]["store_format"] = size
        _campus(site, STORES[size], STORE_NETWORKS)
        radio_sites.append(world.obj(site.key))
        guest_sites.add(site.key)
    networking.wireless(world, radio_sites, lan_roles=(("staff", "backoffice", "wlan0"),),
                        diagnostic=False, stable_channels=True,
                        guest_sites=guest_sites & {site["key"] for site in radio_sites})
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
                raise DesignError(f"{obj['key']}: generated device names collide; review the store endpoint labels")
            names.add(identity)
    if previous:
        old_aggregates = {obj["key"] for obj in previous["objects"] if obj["kind"] == "aggregate"}
        if not old_aggregates <= world.objects.keys():
            raise DesignError("Growth would replace an aggregate allocation; explicit rebaseline or an aggregate expansion workflow is required")
    return world.finish()


SHARED_ASSUMPTIONS = [
    "Store, warehouse and support-centre premises, street addresses, room geometry and equipment counts are fictional design inputs, not surveyed retail property.",
    "Installed workstations, lanes, scanners, radios and cameras are equipment inventory. They are not staff rosters, shopper counts, transaction volume or measured utilisation.",
    "Separate back-office, transaction, wireless, security, guest and management segments express intended boundaries. No firewall policy, payment application, card-data scope or compliance state is demonstrated.",
    "Access switches grow in pairs per equipment room and retain each endpoint's reserved physical port as demand grows; endpoints themselves remain single-homed.",
    "One coverage radio serves staff areas and the remainder cover the trading or warehouse floor. Mounting positions are an authored equipment envelope, not surveyed RF capacity or observed associations.",
    "Two modeled carriers and gateways do not establish diverse ducts or running routing, firewall, authentication or HA behaviour.",
]


def _office(site, demand):
    """Support centre: the shared staffed-building grammar, without retail lanes."""
    places.arrange(site, demand)
    dist, edges, upstreams = campus.aggregation(site, OFFICE_NETWORKS, demand["peak_mbps"])
    endpoints = []
    for label, count, alias, role, segment in (
            ("desk", demand["workstations"], "endpoint", "workstation", "backoffice"),
            ("ap", demand["aps"], "ap", "ap", "wireless"),
            ("cam", demand["cameras"], "endpoint", "camera", "security")):
        for index in range(count):
            key = site.device(alias, f"{label}-{index+1:03}", role, racked=False,
                              meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
            places.place_endpoint(site, key, role, index+1)
            endpoints.append((key, segment))
    facts = campus.access(site, endpoints, upstreams, OFFICE_NETWORKS,
                          {"access_hardware": "access", "upstreams": 2, "label": "access-"}, stable=True)
    _contract(site, demand, endpoints, facts)
    site.contract["assumptions"].append(
        "Support-centre staffing places one AP per occupied office pod and two cameras per floor. "
        "Each occupied floor has local access, patching, management and A/B distribution panels; direct-terminated fiber risers reach the MDF.")
    equipment.enrich_site(site, demonstrations=False)
    site.management(dist)
    site.power()


def _campus(site, demand, networks):
    """Store or distribution centre built from the shared campus allocators."""
    rooms = places.retail_rooms(site)
    store = site.contract["kind"] == "store"
    dist, edges, upstreams = campus.aggregation(site, networks, demand["peak_mbps"])
    endpoints = []

    def add(label, alias, role, segment, room, cohort, ordinal):
        key = site.device(alias, label, role, racked=False,
                          meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
        places.retail_endpoint(site, key, room, cohort, ordinal)
        endpoints.append((key, segment))

    for index in range(demand["workstations"]):
        add(f"desk-{index+1:03}", "endpoint", "workstation", "backoffice", rooms["office"],
            "back-office workstation" if store else "distribution office workstation", index+1)
    for index in range(demand["pos_terminals"] if store else demand["scanners"]):
        add(f"{'pos' if store else 'scan'}-{index+1:03}", "endpoint",
            "pos-terminal" if store else "scanner", "pos" if store else "backoffice",
            rooms["selling"], "point-of-sale lane" if store else "warehouse scanner station", index+1)
    # Radio one covers the staffed room; the remainder cover the trading or
    # warehouse floor. Ordinals are permanent mount positions, not RF planning.
    for index in range(demand["aps"]):
        room, ordinal = (rooms["office"], 1) if index == 0 else (rooms["selling"], index)
        add(f"ap-{index+1:03}", "ap", "ap", "wireless", room,
            "staff coverage radio" if index == 0 else "floor coverage radio", ordinal)
    front = math.ceil(demand["cameras"] / 2)
    for index in range(demand["cameras"]):
        room, ordinal = ((rooms["selling"], index+1) if index < front
                         else (rooms["storage"], index+1-front))
        add(f"cam-{index+1:03}", "endpoint", "camera", "security", room, "security camera", ordinal)
    facts = campus.access(site, endpoints, upstreams, networks,
                          {"access_hardware": "access", "upstreams": 2, "label": "access-"}, stable=True)
    _contract(site, demand, endpoints, facts)
    site.contract["assumptions"].append(
        "Each store has one authored sales floor, back office and stockroom; each distribution centre has a warehouse floor, "
        "office and shipping dock. Half of the cameras watch the trading or warehouse floor and half the stock or dock area."
        if store else
        "Each distribution centre has one authored warehouse floor, office and shipping dock. Handheld scanner stations share the "
        "back-office segment; no point-of-sale lane, payment flow or warehouse control system is represented.")
    equipment.enrich_site(site, demonstrations=False)
    site.management(dist)
    site.power()


def _contract(site, demand, endpoints, facts):
    site.contract.update(endpoint_count=len(endpoints), demand=dict(demand), access_hardware=site.w.hardware_alias("access"),
                         access_devices=facts["access_devices"],
                         access_usable_ports=facts["access_usable_ports"],
                         required_device_roles={"role/wan-edge": 2, "role/distribution": 2,
                                                "role/access": facts["access_count"]})
    site.contract["assumptions"].extend(SHARED_ASSUMPTIONS)
