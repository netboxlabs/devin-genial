"""Electric utility: control centers and the substations they document.

The estate is one or two control centers — dc-kind sites holding the operations
room and the machine room that carries the shared operational services — plus a
fleet of keyed substations. Each substation is one site: a small control house
(its equipment room) with a station (OT) zone and a minimal corporate (IT)
presence, and a switchyard of permanently positioned bays.

The zone separation is the point of this profile, and it is modeled, not
enforced. The station endpoints — one remote terminal unit and one protection
relay per bay, two station HMIs and one station gateway — sit on their own
`protection`, `telemetry` and `station` segments, in their own routing contexts,
behind their own distribution pair; the only modeled forwarding path from that
pair toward the corporate tier is the `conduit` segment trunked between the two
distribution pairs, with each device's dedicated management port and the control
house's shared serial console server as the two other declared crossings. No
station segment reaches a carrier edge device, and `validate_utility.py` proves
that from the finished graph instead of trusting a contract.

What is deliberately *not* claimed anywhere: this is inventory and intended
boundaries, never enforced security, a firewall policy, an air gap, an electronic
security perimeter or a NERC CIP compliance state. No SCADA or EMS function is
executed, and no utility protocol — DNP3, IEC 61850, Modbus, ICCP, IEC 60870-5 or
any other — is configured, carried or asserted. The remote terminal units,
protection relays, station HMIs and gateways are reference endpoint inventory
with no telemetry point, measurement, control action, protection setting, trip
scheme, certification or firmware. Bays are installed equipment positions: they
are not voltage classes, electrical ratings, bus arrangements, breaker positions,
generation or load figures. The estate is documentation inventory, and no claim
about operating an actual grid follows from it.

Shared builders allocate every device, port, address, rack and cable; the
independent checks in `validate_utility.py` inspect the finished graph.
"""

from copy import deepcopy
from decimal import Decimal
import ipaddress
import math
import re

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation, trunk
from .model import (DesignError, World, hardware_catalog, resolve_bank_recipe, resolve_demo,
                    selected_alias)


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps",
          "naming", "site_names", "hardware"}
# Corporate (IT) substation segments. `wan` addresses the two carrier handoffs
# and carries no client and no gateway SVI, so it is not trunked with these.
IT_NETWORKS = ("management", "office")
# Station (OT) segments. Nothing else may carry them.
OT_NETWORKS = ("protection", "telemetry", "station")
# The modeled routed transit between the two distribution tiers. Its only
# members are the four distribution switches; it never reaches an access
# switch, an endpoint or a carrier edge.
CONDUIT = "conduit"
NETWORKS = ("management", "office", "protection", "telemetry", "station", "conduit",
            "applications", "database", "backup", "wan", "storage")
SUBSTATION_KINDS = ("transmission", "distribution")
SUBSTATION_DEFAULTS = dict(kind="distribution", bays=6)
DEFAULT_SUBSTATIONS = [dict(key="oakridge", kind="transmission", bays=10),
                       dict(key="milldam", kind="transmission", bays=8),
                       dict(key="fairhaven", kind="distribution", bays=6),
                       dict(key="stonebrook", kind="distribution", bays=4)]
MAX_SUBSTATIONS = 24
MIN_CENTERS, MAX_CENTERS = 1, 2
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
SUBSTATION_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
MIN_BAYS, MAX_BAYS = 2, places.MAX_SUBSTATION_BAYS
# Authored per-bay OT inventory density. Installed equipment positions only:
# never a point count, protection scheme, electrical rating or bus arrangement.
BAY_RTUS, BAY_RELAYS = 1, 1
BAY_ENDPOINTS = BAY_RTUS + BAY_RELAYS
# Authored control-house density: two station HMIs and one station gateway.
STATION_HMIS, STATION_GATEWAYS = 2, 1
HOUSE_ENDPOINTS = STATION_HMIS + STATION_GATEWAYS
# Authored corporate presence by substation kind: installed desk positions in
# the control house, never staffing, shift rosters or occupancy.
WORKSTATIONS = {"transmission": 2, "distribution": 1}
# Authored per-substation planning demand, not a measured traffic model.
BASE_MBPS = {"transmission": 20, "distribution": 10}
MBPS_PER_BAY, MBPS_PER_WORKSTATION = 3, 2
# The finite distribution attachment pool every profile shares.
MAX_ACCESS_SWITCHES = 38
# Fictional service sizing. Thresholds count installed inventory and requested
# substations, never telemetry points, scan rates, load or generation.
SERVICES = (
    ("scada-front-end", "applications", 8, 16384, 200000, "rtus", 120, 443),
    ("historian", "database", 8, 32768, 1000000, "ot_endpoints", 400, 5432),
    ("ems-gateway", "applications", 4, 8192, 100000, "substations", 16, 443),
    ("monitoring", "applications", 4, 16384, 200000, "endpoints", 2000, 443),
    ("dns", "applications", 2, 4096, 40000, "sites", 16, 53),
    ("backup", "backup", 4, 16384, 1000000, "substations", 12, 443),
)
TIER_1 = {"scada-front-end", "historian", "ems-gateway", "dns"}


def _substations(supplied):
    """Resolve the keyed substation list; physical capacity is checked separately."""
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_SUBSTATIONS:
        raise DesignError(f"substations must be a list of 1–{MAX_SUBSTATIONS} keyed substation sites")
    keys, codes, result = set(), set(), []
    for raw in supplied:
        if not isinstance(raw, dict) or "key" not in raw or raw.keys() - (SUBSTATION_DEFAULTS.keys() | {"key"}):
            raise DesignError("Each substation entry needs a stable key and only supported demand fields: "
                              f"{', '.join(sorted(SUBSTATION_DEFAULTS))}")
        key = raw["key"]
        if not isinstance(key, str) or not re.fullmatch(SUBSTATION_KEY, key) or key in keys:
            raise DesignError("substation keys must be unique hyphen-separated lowercase identifiers of at most 20 "
                              "characters, with no leading, trailing or doubled hyphen")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError("substation keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key)
        codes.add(code)
        item = SUBSTATION_DEFAULTS | deepcopy(raw)
        if item["kind"] not in SUBSTATION_KINDS:
            raise DesignError(f"substation {key}: kind must be 'transmission' or 'distribution'; "
                              "the kind selects the authored corporate presence and planning demand, "
                              "never a voltage class or electrical rating")
        if type(item["bays"]) is not int or not MIN_BAYS <= item["bays"] <= MAX_BAYS:
            raise DesignError(f"substation {key}: bays must be an integer from {MIN_BAYS} through {MAX_BAYS}")
        result.append(item)
    return sorted(result, key=lambda item: item["key"])


def premises(recipe):
    """Ordered (site id, substation) pairs for every requested substation."""
    return [(f"sub-{item['key']}", item) for item in recipe["substations"]]


def centers(recipe):
    """Ordered control-center site ids; the first is the primary."""
    return [f"dc-{n:02}" for n in range(1, recipe["control_centers"] + 1)]


def peak_mbps(item):
    """Authored per-substation planning demand, not a measured traffic model."""
    return (BASE_MBPS[item["kind"]] + MBPS_PER_BAY * item["bays"]
            + MBPS_PER_WORKSTATION * WORKSTATIONS[item["kind"]])


def ot_endpoints(item):
    return BAY_ENDPOINTS * item["bays"] + HOUSE_ENDPOINTS


def it_endpoints(item):
    return WORKSTATIONS[item["kind"]]


def demand(item):
    """Installed inventory only; bays are equipment positions, not ratings."""
    return dict(bays=item["bays"], rtus=BAY_RTUS * item["bays"],
                protection_relays=BAY_RELAYS * item["bays"], station_hmis=STATION_HMIS,
                station_gateways=STATION_GATEWAYS, workstations=WORKSTATIONS[item["kind"]],
                peak_mbps=peak_mbps(item))


def total_peak(recipe):
    return sum(peak_mbps(item) for _, item in premises(recipe))


def counts(recipe):
    """Independent installed-inventory totals used to size the shared services."""
    stations = [item for _, item in premises(recipe)]
    return dict(substations=len(stations), sites=len(stations) + recipe["control_centers"],
                bays=sum(item["bays"] for item in stations),
                rtus=sum(BAY_RTUS * item["bays"] for item in stations),
                ot_endpoints=sum(ot_endpoints(item) for item in stations),
                endpoints=sum(ot_endpoints(item) + it_endpoints(item) for item in stations))


def workloads(recipe):
    """Size the shared services from resolved demand, never emitted inventory."""
    totals = counts(recipe)
    result = []
    for slot, (key, network, vcpus, memory, disk, metric, threshold, port) in enumerate(SERVICES):
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        groups = max(1, math.ceil(totals[metric] / threshold))
        result.append(dict(key=key, slot=slot, instances=2*groups, replicas=2, failure_domain="rack",
                           network=network, vcpus=vcpus, memory_mb=memory, disk_mb=disk,
                           listeners=listeners, criticality="tier-1" if key in TIER_1 else "tier-2",
                           replica_description="complete operational service shard; application replication and recovery are not executed"))
    return result


def access_pairs(count, usable):
    """Access switches a stable room ledger needs for one endpoint population."""
    return max(2, 2*math.ceil(count / (2*usable))) if count else 0


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "substations", "control_centers", "demo"}):
        raise DesignError(f"Unknown utility recipe fields: {', '.join(sorted(unknown))}; "
                          "describe the control centers and the substations with their bay demand")
    common = dict(namespace="northgate", name="Northgate Power and Light", address_pool="10.0.0.0/8")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="utility", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("Utility address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("Utility rack-user reservations are not implemented; leave reservation_user empty")
    supplied = raw.get("control_centers", MAX_CENTERS)
    if type(supplied) is not int or not MIN_CENTERS <= supplied <= MAX_CENTERS:
        raise DesignError("control_centers must be 1 (primary only) or 2 (primary and backup); "
                          "a third operations site is not a reviewed composition")
    recipe["control_centers"] = supplied
    recipe["substations"] = _substations(raw.get("substations", deepcopy(DEFAULT_SUBSTATIONS)))
    # The reviewed bounds cap one substation's own peak at 72 Mbps, inside the
    # 1 Gbps site handoff after the widest supported reserve, and the fleet
    # aggregate at 1,728 Mbps, which the shared control-center aggregation
    # answers with three carrier edge pairs. The corporate and station access
    # tiers share one finite distribution attachment pool, so their switch
    # counts are checked together. Raising any bound needs all three rechecked.
    ports = hardware_catalog()["models"][selected_alias(recipe, "access")]["access_ports"]
    usable = int(Decimal(len(ports)) * (Decimal(1) - Decimal(str(recipe["reserve_fraction"]))))
    for item in recipe["substations"]:
        switches = (access_pairs(it_endpoints(item), usable) + access_pairs(ot_endpoints(item), usable))
        if switches > MAX_ACCESS_SWITCHES:
            raise DesignError(
                f"substation {item['key']}: {it_endpoints(item)} corporate and {ot_endpoints(item)} station "
                f"endpoints need {switches} access switches at {usable} usable ports each, exceeding the "
                f"{MAX_ACCESS_SWITCHES} supported distribution attachments; reduce demand or split the substation")
    return recipe


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous utility plan does not reproduce from its recipe and ledgers; "
                              "use an intact frozen plan or new baseline")
        del reproduced
        if recipe["control_centers"] < previous["recipe"]["control_centers"]:
            raise DesignError("Removing a control center requires a new baseline; "
                              "Diode replay does not delete retired objects")
        current = {item["key"]: item for item in recipe["substations"]}
        for before in previous["recipe"]["substations"]:
            after = current.get(before["key"])
            if after is None:
                raise DesignError(f"Removing substation {before['key']} requires a new baseline; "
                                  "Diode replay does not delete retired objects")
            if after["kind"] != before["kind"]:
                raise DesignError(f"Substation {before['key']}: changing kind requires a new baseline; "
                                  "the kind fixes the authored corporate presence and planning demand")
            if after["bays"] < before["bays"]:
                raise DesignError(f"Substation {before['key']}: decreasing bays requires a new baseline; "
                                  "ordinary growth cannot retire objects")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    stations = premises(r)
    world.reserve_sites(centers(r) + [sid for sid, _ in stations])
    foundation(world, industry="electric utility", inherited=False, networks=NETWORKS,
               site_kinds={"dc", "substation"},
               device_roles=("wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
                             "pdu", "workstation", "rtu", "protection-relay", "hmi", "station-gateway",
                             "patch-panel", "wall-outlet"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server",
                                 "endpoint", "patch-panel", "wall-outlet"})
    peak = total_peak(r)
    for index, sid in enumerate(centers(r)):
        dc = Site(world, sid, "dc",
                  ("Primary control center; operations room and machine room carrying the shared operational services"
                   if index == 0 else
                   "Backup control center; operations room and machine room carrying the same shared operational services"))
        datacenter.build(dc, workloads=workloads(r), wan_peak_mbps=peak, include_equipment=False, assumptions=[
            "Each control center's WAN budget covers every declared substation peak independently; this is a modeled "
            "capacity assumption, not measured telemetry throughput.",
            "The operations room is named in this site's record. Operator consoles, a video wall, shift positions and "
            "any control action are outside this dataset; the machine room's service inventory is what is modeled.",
            "Shared operational services are deployed in every requested control center and each complete replica "
            "group is separated across compute racks. Application replication, failover, control-center transfer and "
            "recovery are not executed, and a second control center is inventory, not a demonstrated failover.",
            "Service groups cover 120 installed remote terminal units for the SCADA front end, 400 installed station "
            "endpoints for the historian, 16 substations for the EMS gateway, 12 for backup, 2,000 installed "
            "endpoints for monitoring and 16 sites for DNS, minimum one each. These are fictional planning "
            "thresholds, not telemetry point counts, scan rates, sample intervals or retention sizing.",
            "The SCADA front end, historian and EMS gateway are inventory for a service family. No telemetry point, "
            "measurement, tag, sample, control action, setpoint, switching command, utility protocol or energy "
            "management function exists in this dataset, and nothing connects to a station endpoint.",
        ])
    for sid, item in stations:
        site = Site(world, sid, "substation",
                    "Substation control house; separated station and corporate zones with a permanently positioned switchyard")
        world.obj(site.key)["meta"]["substation"] = item["key"]
        world.obj(site.key)["meta"]["substation_kind"] = item["kind"]
        _substation(site, item)
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
                raise DesignError(f"{obj['key']}: two devices at this site would emit the same display name; "
                                  "review the substation endpoint labels")
            names.add(identity)
    if previous:
        old_aggregates = {obj["key"] for obj in previous["objects"] if obj["kind"] == "aggregate"}
        if not old_aggregates <= world.objects.keys():
            raise DesignError("Growth would replace an aggregate allocation; explicit rebaseline or an "
                              "aggregate expansion workflow is required")
    return world.finish()


SHARED_ASSUMPTIONS = [
    "Substation premises, street addresses, control-house geometry, bay positions and equipment counts are fictional "
    "design inputs, not a surveyed utility estate.",
    "Installed remote terminal units, protection relays, station HMIs, station gateways and workstations are "
    "equipment inventory. They are not telemetry points, measurements, protection settings, trip schemes, breaker "
    "positions, switching states, generation, load, outage figures or staffing.",
    "Bays are installed equipment positions in an authored switchyard layout. They are not voltage classes, "
    "electrical ratings, bus arrangements, current or potential transformer ratios, or a one-line diagram. No grid "
    "topology, connectivity, power-flow or state-estimation semantics is represented.",
    "Station endpoints are reference inventory records. No control function, protection logic, relay setting, "
    "coordination study, safety rating, certification, firmware or vendor control platform is represented or claimed.",
    "No utility protocol is configured, carried or asserted anywhere in this dataset — not DNP3, IEC 61850, Modbus, "
    "ICCP, IEC 60870-5 or any other. The endpoints carry ordinary IP inventory records only.",
    "The separated protection, telemetry, station, conduit, office and management segments express intended "
    "boundaries and their own routing contexts. This is modeled separation, not enforced isolation: no firewall "
    "policy, access control list, route filter, data diode or air gap exists, and no electronic security perimeter, "
    "NERC CIP compliance state or security assessment is demonstrated or certified.",
    "The station distribution pair reaches the corporate tier only through the conduit segment trunked between the "
    "two distribution pairs; each station switch additionally reaches the substation management segment through its "
    "own dedicated management port, and its console port reaches the control house's shared console server (serial "
    "CLI, not a forwarding path). Those are the three modeled crossings; no station segment reaches a carrier edge "
    "device.",
    "Both distribution tiers share the one control-house equipment room. The zone boundary is modeled in the routing "
    "and VLAN graph, not by separate rooms, cabinets or enclosures, and no DIN-rail, fanless, hardened, "
    "substation-rated or IEEE 1613 qualified hardware is represented — the station tier installs the same reviewed "
    "catalog models as every other profile.",
    "No physical security is represented: no fence, gate, door controller, badge reader, intrusion detection, camera "
    "or guard position exists in this dataset, and none is implied by any record.",
    "Access switches grow in pairs per zone and retain each endpoint's reserved physical port as demand grows; "
    "endpoints themselves remain single-homed. The two zones keep separate port ledgers and separate upstream pairs.",
    "No wireless coverage of any kind is modeled at a substation: there is no radio, WLAN, SSID or mobile endpoint.",
    "Two modeled carriers and gateways do not establish diverse ducts, separate rights of way, or running routing, "
    "firewall, authentication or HA behaviour. Actual span providers are shown per substation without any carrier "
    "diversity promise.",
    "This estate is documentation inventory for a fictional operator. Nothing here supports a claim about operating "
    "an actual grid, a real substation, or any critical-infrastructure obligation.",
]


def _substation(site, item):
    """Build one substation from the shared room, aggregation and access allocators."""
    rooms = places.substation_rooms(site, item["bays"], WORKSTATIONS[item["kind"]])
    corporate, edges, _ = campus.aggregation(site, IT_NETWORKS, peak_mbps(item))
    uplinks = site.w.hardware("leaf")["uplink_ports"]

    def gateway(device, role, host):
        vlan, _ = site.network(role)
        port = site.virtual_interface(device, f"Vlan{site.w.obj(vlan)['attrs']['vid']}", role)
        site.address(port, role, host=host, device=device)

    # The station (OT) distribution tier: its own pair, its own gateways and no
    # carrier attachment of any kind.
    station = [site.device("leaf", f"ot-dist-{side}", "distribution") for side in ("a", "b")]
    peers = [site.interface(device, uplinks[0]) for device in station]
    site.cable(*peers, "aoc")
    trunk(site, peers, OT_NETWORKS + (CONDUIT,))
    for index, device in enumerate(station):
        for role in OT_NETWORKS:
            gateway(device, role, index + 1)
        # The conduit's station gateways sit above the corporate pair's, so all
        # four addressed transit interfaces live in one modeled segment.
        gateway(device, CONDUIT, index + 3)
    for index, device in enumerate(corporate):
        gateway(device, CONDUIT, index + 1)
    corporate_peers = [site.interface(device, uplinks[0]) for device in corporate]
    trunk(site, corporate_peers, IT_NETWORKS + (CONDUIT,))
    # The conduit itself: a full mesh between the two distribution pairs that
    # carries the conduit segment and nothing else.
    for near, device in enumerate(station):
        for far, parent in enumerate(corporate):
            a, b = site.interface(device, uplinks[far+1]), site.interface(parent, uplinks[near+1])
            site.cable(a, b, "smf")
            trunk(site, [a, b], (CONDUIT,))
        site.redundant(device, corporate)

    corporate_endpoints, station_endpoints = [], []

    def add(bucket, label, role, segment, room, cohort, ordinal):
        key = site.device("endpoint", label, role, racked=False,
                          meta={"endpoint": True, "network": segment, "power_scope": "local outlet"})
        places.substation_endpoint(site, key, room, cohort, ordinal)
        bucket.append((key, segment))

    control = rooms["control"]
    # Control-house ordinals are permanent mount positions inside one room; they
    # never express a control hierarchy, scan order or protection zone.
    for number in range(STATION_HMIS):
        add(station_endpoints, f"hmi-{number+1:02}", "hmi", "station", control, "station-hmi", number + 1)
    add(station_endpoints, "gateway-01", "station-gateway", "telemetry", control, "station-gateway",
        STATION_HMIS + 1)
    for number in range(WORKSTATIONS[item["kind"]]):
        add(corporate_endpoints, f"desk-{number+1:02}", "workstation", "office", control,
            "corporate-desk", STATION_HMIS + STATION_GATEWAYS + number + 1)
    for index, room in enumerate(rooms["bays"]):
        add(station_endpoints, f"rtu-{index+1:02}", "rtu", "telemetry", room, "remote-terminal-unit", 1)
        add(station_endpoints, f"relay-{index+1:02}", "protection-relay", "protection", room,
            "protection-relay", BAY_RTUS + 1)

    corporate_facts = campus.access(site, corporate_endpoints, corporate, IT_NETWORKS,
                                    {"access_hardware": "access", "upstreams": 2, "label": "access-"},
                                    stable=True)
    station_facts = campus.access(site, station_endpoints, station, OT_NETWORKS,
                                  {"access_hardware": "access", "upstreams": 2,
                                   "label": "ot-access-", "zone": "/ot"}, stable=True)
    site.contract.update(endpoint_count=len(corporate_endpoints) + len(station_endpoints),
                         demand=demand(item), substation=item["key"], substation_kind=item["kind"],
                         access_hardware=site.w.hardware_alias("access"),
                         access_devices=corporate_facts["access_devices"] + station_facts["access_devices"],
                         access_usable_ports=corporate_facts["access_usable_ports"],
                         ot_access_devices=station_facts["access_devices"],
                         required_device_roles={"role/wan-edge": 2, "role/distribution": 4,
                                                "role/access": corporate_facts["access_count"]
                                                + station_facts["access_count"]})
    site.contract["assumptions"].append(
        "Each substation holds one authored control house on one ground floor: a control room and up to sixteen "
        "switchyard bay positions, all served by the single control-house equipment room. Every bay keeps a "
        "permanent reserved position, so commissioning a bay appends a position instead of renumbering the "
        "switchyard.")
    site.contract["assumptions"].extend(SHARED_ASSUMPTIONS)
    equipment.enrich_site(site, demonstrations=False)
    site.management(corporate)
    site.power()
