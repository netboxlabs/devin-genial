"""Manufacturing plants with separated plant-floor (OT) and corporate (IT) zones.

Each plant is one site holding three authored areas on one ground floor — a
production floor of line cells, a warehouse of loading docks, and an office
block — plus the single equipment room that serves them. The corporate estate
adds a paired data center carrying the shared manufacturing services.

The zone separation is the point of this profile, and it is modeled, not
enforced. The plant-floor endpoints sit on their own `process` and
`supervisory` segments, in their own routing contexts, behind their own
distribution pair; the only modeled forwarding path from that pair toward the
corporate tier is the `conduit` segment trunked between the two distribution
pairs, with each device's dedicated management port and the equipment room's
shared serial console server as the two other declared crossings. No `process` or `supervisory` VLAN
reaches a carrier edge device, and `validate_manufacturing.py` proves that from
the finished graph instead of trusting a contract.

What is deliberately *not* claimed anywhere: this is inventory and intended
boundaries, never enforced security, a firewall policy, an air gap, a
Purdue-model level assignment or an IEC 62443 compliance state. No industrial
protocol — Modbus, PROFINET, EtherNet/IP, OPC-UA or any other — is configured,
carried or asserted. The controllers, operator panels and field devices are
reference endpoint inventory with no control function, safety rating,
certification or firmware. Line, dock, desk and service counts are authored
planning envelopes, not throughput, OEE, takt time, output or headcount.

Shared builders allocate every device, port, address, rack and cable; the
independent checks in `validate_manufacturing.py` inspect the finished graph.
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
# Corporate (IT) plant segments. `wan` addresses the two carrier handoffs and
# carries no client and no gateway SVI, so it is not trunked with these.
IT_NETWORKS = ("management", "office", "logistics", "wireless", "security")
# Plant-floor (OT) segments. Nothing else may carry them.
OT_NETWORKS = ("process", "supervisory")
# The modeled routed transit between the two distribution tiers. Its only
# members are the four distribution switches; it never reaches an access
# switch, an endpoint or a carrier edge.
CONDUIT = "conduit"
NETWORKS = ("management", "office", "logistics", "wireless", "security", "conduit",
            "process", "supervisory", "applications", "database", "backup", "wan", "storage")
PLANT_DEFAULTS = dict(production_lines=4, warehouse_docks=4, office_staff=36)
DEFAULT_PLANTS = [dict(key="riverbend", production_lines=6, warehouse_docks=6, office_staff=48),
                  dict(key="kestrel-forge", production_lines=4, warehouse_docks=4, office_staff=24),
                  dict(key="granite-harbor", production_lines=2, warehouse_docks=2, office_staff=12)]
MAX_PLANTS = 8
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
PLANT_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
MIN_LINES, MAX_LINES = 1, places.MAX_PLANT_LINES
MIN_DOCKS, MAX_DOCKS = 0, places.MAX_PLANT_DOCKS
MIN_STAFF, MAX_STAFF = 4, places.MAX_PLANT_PODS * places.OFFICE_DESKS
# Authored per-line OT inventory density. Installed equipment positions only:
# never an I/O count, a control-system design, throughput, OEE or takt time.
LINE_CONTROLLERS, LINE_PANELS, LINE_FIELD_DROPS = 1, 2, 4
LINE_ENDPOINTS = LINE_CONTROLLERS + LINE_PANELS + LINE_FIELD_DROPS
# Authored warehouse density: two wired scanner stations and one camera per
# dock, and one coverage radio for every two docks.
DOCK_SCANNERS, DOCKS_PER_RADIO = 2, 2
# Authored per-plant planning demand, not a measured traffic model.
PLANT_BASE_MBPS, MBPS_PER_DESK, MBPS_PER_LINE, MBPS_PER_DOCK = 20, 2, 5, 3
# The finite distribution attachment pool every profile shares.
MAX_ACCESS_SWITCHES = 38
# Fictional service sizing. Thresholds count installed inventory and requested
# plants, never production output, transaction volume or measured load.
SERVICES = (
    ("mes", "applications", 4, 16384, 200000, "lines", 24, 443),
    ("historian", "database", 8, 32768, 1000000, "ot_endpoints", 480, 5432),
    ("erp-gateway", "applications", 4, 8192, 100000, "plants", 8, 443),
    ("identity", "applications", 4, 8192, 100000, "endpoints", 2000, 443),
    ("dns", "applications", 2, 4096, 40000, "sites", 16, 53),
    ("monitoring", "applications", 4, 16384, 200000, "endpoints", 3000, 443),
    ("backup", "backup", 4, 16384, 1000000, "plants", 6, 443),
)
TIER_1 = {"mes", "historian", "erp-gateway", "identity", "dns"}


def _plants(supplied):
    """Resolve the keyed plant list; physical capacity is checked separately."""
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_PLANTS:
        raise DesignError(f"plants must be a list of 1–{MAX_PLANTS} keyed manufacturing sites")
    keys, codes, result = set(), set(), []
    for raw in supplied:
        if not isinstance(raw, dict) or "key" not in raw or raw.keys() - (PLANT_DEFAULTS.keys() | {"key"}):
            raise DesignError("Each plant entry needs a stable key and only supported demand fields: "
                              f"{', '.join(sorted(PLANT_DEFAULTS))}")
        key = raw["key"]
        if not isinstance(key, str) or not re.fullmatch(PLANT_KEY, key) or key in keys:
            raise DesignError("plant keys must be unique hyphen-separated lowercase identifiers of at most 20 "
                              "characters, with no leading, trailing or doubled hyphen")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError("plant keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key)
        codes.add(code)
        item = PLANT_DEFAULTS | deepcopy(raw)
        for field, low, high in (("production_lines", MIN_LINES, MAX_LINES),
                                 ("warehouse_docks", MIN_DOCKS, MAX_DOCKS),
                                 ("office_staff", MIN_STAFF, MAX_STAFF)):
            if type(item[field]) is not int or not low <= item[field] <= high:
                raise DesignError(f"plant {key}: {field} must be an integer from {low} through {high}")
        result.append(item)
    return sorted(result, key=lambda item: item["key"])


def premises(recipe):
    """Ordered (site id, plant) pairs for every requested plant."""
    return [(f"pl-{item['key']}", item) for item in recipe["plants"]]


def peak_mbps(item):
    """Authored per-plant planning demand, not a measured traffic model."""
    return (PLANT_BASE_MBPS + MBPS_PER_DESK * item["office_staff"]
            + MBPS_PER_LINE * item["production_lines"] + MBPS_PER_DOCK * item["warehouse_docks"])


def office_pods(item):
    return places.plant_pods(item["office_staff"])


def plant_radios(item):
    """One reception radio, one per office pod, one per two loading docks."""
    return 1 + len(office_pods(item)) + math.ceil(item["warehouse_docks"] / DOCKS_PER_RADIO)


def plant_cameras(item):
    """One camera in reception and one over each loading dock."""
    return 1 + item["warehouse_docks"]


def ot_endpoints(item):
    return LINE_ENDPOINTS * item["production_lines"]


def it_endpoints(item):
    return (item["office_staff"] + DOCK_SCANNERS * item["warehouse_docks"]
            + plant_radios(item) + plant_cameras(item))


def demand(item):
    """Installed inventory only; lines, docks and desks are capacity, not output."""
    return dict(workstations=item["office_staff"], aps=plant_radios(item), cameras=plant_cameras(item),
                production_lines=item["production_lines"], warehouse_docks=item["warehouse_docks"],
                office_pods=len(office_pods(item)),
                line_controllers=LINE_CONTROLLERS * item["production_lines"],
                operator_panels=LINE_PANELS * item["production_lines"],
                field_devices=LINE_FIELD_DROPS * item["production_lines"],
                scanner_stations=DOCK_SCANNERS * item["warehouse_docks"],
                peak_mbps=peak_mbps(item))


def total_peak(recipe):
    return sum(peak_mbps(item) for _, item in premises(recipe))


def counts(recipe):
    """Independent installed-inventory totals used to size the shared services."""
    plants = [item for _, item in premises(recipe)]
    return dict(plants=len(plants), sites=len(plants) + 2,
                lines=sum(item["production_lines"] for item in plants),
                docks=sum(item["warehouse_docks"] for item in plants),
                ot_endpoints=sum(ot_endpoints(item) for item in plants),
                endpoints=sum(ot_endpoints(item) + it_endpoints(item) for item in plants))


def workloads(recipe):
    """Size the shared services from resolved demand, never emitted inventory."""
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
                           replica_description="complete manufacturing service shard; application replication and recovery are not executed"))
    return result


def access_pairs(count, usable):
    """Access switches a stable room ledger needs for one endpoint population."""
    return max(2, 2*math.ceil(count / (2*usable))) if count else 0


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "plants", "demo"}):
        raise DesignError(f"Unknown manufacturing recipe fields: {', '.join(sorted(unknown))}; "
                          "describe the plants and their line, dock and office demand")
    common = dict(namespace="ironwood", name="Ironwood Manufacturing", address_pool="10.0.0.0/8")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="manufacturing", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("Manufacturing address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("Manufacturing rack-user reservations are not implemented; leave reservation_user empty")
    recipe["plants"] = _plants(raw.get("plants", deepcopy(DEFAULT_PLANTS)))
    # The reviewed bounds cap one plant's own peak at 308 Mbps, inside the 1 Gbps
    # site handoff after the widest supported reserve, and the fleet aggregate at
    # 2,464 Mbps, which the shared DC aggregation answers with four carrier edge
    # pairs at the default reserve (five at the widest). The corporate and plant-floor access tiers share one finite
    # distribution attachment pool, so their switch counts are checked together.
    # Raising any bound needs all three ceilings rechecked.
    ports = hardware_catalog()["models"][selected_alias(recipe, "access")]["access_ports"]
    usable = int(Decimal(len(ports)) * (Decimal(1) - Decimal(str(recipe["reserve_fraction"]))))
    for item in recipe["plants"]:
        switches = (access_pairs(it_endpoints(item), usable) + access_pairs(ot_endpoints(item), usable))
        if switches > MAX_ACCESS_SWITCHES:
            raise DesignError(
                f"plant {item['key']}: {it_endpoints(item)} corporate and {ot_endpoints(item)} plant-floor "
                f"endpoints need {switches} access switches at {usable} usable ports each, exceeding the "
                f"{MAX_ACCESS_SWITCHES} supported distribution attachments; reduce demand or split the plant")
    return recipe


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous manufacturing plan does not reproduce from its recipe and ledgers; "
                              "use an intact frozen plan or new baseline")
        del reproduced
        current = {item["key"]: item for item in recipe["plants"]}
        for before in previous["recipe"]["plants"]:
            after = current.get(before["key"])
            if after is None:
                raise DesignError(f"Removing plant {before['key']} requires a new baseline; "
                                  "Diode replay does not delete retired objects")
            for field in ("production_lines", "warehouse_docks", "office_staff"):
                if after[field] < before[field]:
                    raise DesignError(f"Plant {before['key']}: decreasing {field} requires a new baseline; "
                                      "ordinary growth cannot retire objects")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    plants = premises(r)
    world.reserve_sites(["dc-01", "dc-02"] + [sid for sid, _ in plants])
    foundation(world, industry="manufacturing", inherited=False, networks=NETWORKS,
               site_kinds={"dc", "plant"},
               device_roles=("wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
                             "pdu", "workstation", "plc", "hmi", "field-device", "scanner", "ap", "camera",
                             "patch-panel", "wall-outlet"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server",
                                 "endpoint", "ap", "patch-panel", "wall-outlet"})
    peak = total_peak(r)
    for index in range(2):
        dc = Site(world, f"dc-{index+1:02}", "dc",
                  "Corporate services data center; paired manufacturing, planning and operations applications")
        datacenter.build(dc, workloads=workloads(r), wan_peak_mbps=peak, include_equipment=False, assumptions=[
            "Each DC WAN budget covers every declared plant peak independently; this is a modeled capacity "
            "assumption, not measured plant throughput.",
            "Shared manufacturing services are deployed in both DCs and each complete replica group is separated "
            "across compute racks; application replication, failover and recovery are not executed.",
            "Service groups cover 24 production lines for the manufacturing execution service, 480 installed "
            "plant-floor endpoints for the historian, 8 plants for the ERP gateway, 6 for backup, 2,000 installed "
            "endpoints for identity, 3,000 for monitoring and 16 sites for DNS, minimum one each. These are "
            "fictional planning thresholds, not production rates, tag counts, sample intervals or retention sizing.",
            "The manufacturing execution and historian services are inventory for a service family. No production "
            "order, recipe, batch record, process tag, sample, industrial protocol or control action exists in "
            "this dataset, and nothing connects to a plant-floor endpoint.",
            "Identity includes RADIUS UDP1812/1813 inventory for staff WLAN authentication intent. No "
            "authentication server, directory or credential is configured, running or certified.",
        ])
    radio_sites = []
    for sid, item in plants:
        site = Site(world, sid, "plant",
                    "Manufacturing plant; separated plant-floor and corporate zones on one authored ground floor")
        world.obj(site.key)["meta"]["plant"] = item["key"]
        _plant(site, item)
        radio_sites.append(world.obj(site.key))
    networking.wireless(world, radio_sites, lan_roles=(("staff", "office", "wlan0"),),
                        diagnostic=False, stable_channels=True)
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
                                  "review the plant endpoint labels")
            names.add(identity)
    if previous:
        old_aggregates = {obj["key"] for obj in previous["objects"] if obj["kind"] == "aggregate"}
        if not old_aggregates <= world.objects.keys():
            raise DesignError("Growth would replace an aggregate allocation; explicit rebaseline or an "
                              "aggregate expansion workflow is required")
    return world.finish()


SHARED_ASSUMPTIONS = [
    "Plant premises, street addresses, floor geometry, line-cell, dock and pod counts and equipment counts are "
    "fictional design inputs, not a surveyed manufacturing estate.",
    "Installed controllers, operator panels, field devices, scanner stations, workstations, radios and cameras are "
    "equipment inventory. They are not production output, throughput, OEE, takt time, cycle counts, shift rosters "
    "or measured utilisation.",
    "Plant-floor endpoints are reference inventory records. No control function, safety integrity level, hazardous-"
    "area rating, certification, firmware or vendor control platform is represented or claimed.",
    "No industrial protocol is configured, carried or asserted anywhere in this dataset — not Modbus, PROFINET, "
    "EtherNet/IP, OPC-UA, or any other. The endpoints carry ordinary IP inventory records only.",
    "The separated process, supervisory, conduit, office, logistics, wireless, security and management segments "
    "express intended boundaries and their own routing contexts. This is modeled separation, not enforced "
    "isolation: no firewall policy, access control list, route filter, data diode or air gap exists, and no "
    "Purdue-model level assignment or IEC 62443 compliance state is demonstrated or certified.",
    "The plant-floor distribution pair reaches the corporate tier only through the conduit segment trunked between "
    "the two distribution pairs; each plant-floor switch additionally reaches the plant management segment through "
    "its own dedicated management port, and its console port reaches the equipment room's shared console server "
    "(serial CLI, not a forwarding path). Those are the three modeled crossings; no process or supervisory segment "
    "reaches a carrier edge device.",
    "Both distribution tiers share the one plant equipment room. The zone boundary is modeled in the routing and "
    "VLAN graph, not by separate rooms, cabinets or enclosures, and no DIN-rail, fanless, hardened or industrially "
    "rated hardware is represented — the plant-floor tier installs the same reviewed catalog models as every other "
    "profile.",
    "Access switches grow in pairs per zone and retain each endpoint's reserved physical port as demand grows; "
    "endpoints themselves remain single-homed. The two zones keep separate port ledgers and separate upstream pairs.",
    "One coverage radio serves reception, one serves each office pod and one serves every two loading docks. "
    "Mounting positions are an authored equipment envelope, not surveyed RF capacity or observed associations, and "
    "no wireless coverage of the production floor is modeled at all.",
    "Scanner stations are installed wired dock positions. No handheld fleet, mobile association, roaming or "
    "warehouse control system is represented.",
    "Two modeled carriers and gateways do not establish diverse ducts or running routing, firewall, authentication "
    "or HA behaviour.",
]


def _plant(site, item):
    """Build one plant from the shared room, aggregation and access allocators."""
    rooms = places.plant_rooms(site, item)
    corporate, edges, _ = campus.aggregation(site, IT_NETWORKS, peak_mbps(item))
    uplinks = site.w.hardware("leaf")["uplink_ports"]

    def gateway(device, role, host):
        vlan, _ = site.network(role)
        port = site.virtual_interface(device, f"Vlan{site.w.obj(vlan)['attrs']['vid']}", role)
        site.address(port, role, host=host, device=device)

    # The plant-floor (OT) distribution tier: its own pair, its own gateways and
    # no carrier attachment of any kind.
    process = [site.device("leaf", f"ot-dist-{side}", "distribution") for side in ("a", "b")]
    peers = [site.interface(device, uplinks[0]) for device in process]
    site.cable(*peers, "aoc")
    trunk(site, peers, OT_NETWORKS + (CONDUIT,))
    for index, device in enumerate(process):
        for role in OT_NETWORKS:
            gateway(device, role, index + 1)
        # The conduit's plant-floor gateways sit above the corporate pair's, so
        # all four addressed transit interfaces live in one modeled segment.
        gateway(device, CONDUIT, index + 3)
    for index, device in enumerate(corporate):
        gateway(device, CONDUIT, index + 1)
    corporate_peers = [site.interface(device, uplinks[0]) for device in corporate]
    trunk(site, corporate_peers, IT_NETWORKS + (CONDUIT,))
    # The conduit itself: a full mesh between the two distribution pairs that
    # carries the conduit segment and nothing else.
    for near, device in enumerate(process):
        for far, parent in enumerate(corporate):
            a, b = site.interface(device, uplinks[far+1]), site.interface(parent, uplinks[near+1])
            site.cable(a, b, "smf")
            trunk(site, [a, b], (CONDUIT,))
        site.redundant(device, corporate)

    corporate_endpoints, process_endpoints = [], []

    def add(bucket, label, alias, role, segment, room, cohort, ordinal):
        key = site.device(alias, label, role, racked=False,
                          meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
        places.plant_endpoint(site, key, room, cohort, ordinal)
        bucket.append((key, segment))

    pods = office_pods(item)
    for index in range(item["office_staff"]):
        pod, desk = divmod(index, places.OFFICE_DESKS)
        add(corporate_endpoints, f"desk-{index+1:03}", "endpoint", "workstation", "office",
            rooms["pods"][pod], "office-desk", desk + 1)
    add(corporate_endpoints, "ap-reception", "ap", "ap", "wireless", rooms["reception"], "reception-ap", 1)
    add(corporate_endpoints, "cam-reception", "endpoint", "camera", "security", rooms["reception"],
        "security-camera", 1)
    for index, (suffix, _) in enumerate(pods):
        add(corporate_endpoints, f"ap-{suffix}", "ap", "ap", "wireless", rooms["pods"][index],
            "office-pod-ap", 1)
    for index, room in enumerate(rooms["docks"]):
        for number in range(DOCK_SCANNERS):
            add(corporate_endpoints, f"scan-{DOCK_SCANNERS*index+number+1:03}", "endpoint", "scanner",
                "logistics", room, "dock-scanner-station", number + 1)
        add(corporate_endpoints, f"cam-dock-{index+1:02}", "endpoint", "camera", "security", room,
            "dock-camera", 1)
        if index % DOCKS_PER_RADIO == 0:
            add(corporate_endpoints, f"ap-dock-{index+1:02}", "ap", "ap", "wireless", room, "dock-ap", 1)
    for index, room in enumerate(rooms["lines"]):
        # Ordinals are permanent mount positions inside one line cell; they never
        # express a control hierarchy, scan order or I/O address.
        add(process_endpoints, f"plc-{index+1:02}", "endpoint", "plc", "process", room, "line-controller", 1)
        for number in range(LINE_PANELS):
            add(process_endpoints, f"hmi-{index+1:02}-{number+1}", "endpoint", "hmi", "supervisory", room,
                "operator-panel", LINE_CONTROLLERS + number + 1)
        for number in range(LINE_FIELD_DROPS):
            add(process_endpoints, f"field-{index+1:02}-{number+1}", "endpoint", "field-device", "process",
                room, "field-device-drop", LINE_CONTROLLERS + LINE_PANELS + number + 1)

    corporate_facts = campus.access(site, corporate_endpoints, corporate, IT_NETWORKS,
                                    {"access_hardware": "access", "upstreams": 2, "label": "access-"},
                                    stable=True)
    process_facts = campus.access(site, process_endpoints, process, OT_NETWORKS,
                                  {"access_hardware": "access", "upstreams": 2,
                                   "label": "ot-access-", "panel_label": "ot-patch-",
                                   "zone": "/ot"}, stable=True)
    site.contract.update(endpoint_count=len(corporate_endpoints) + len(process_endpoints),
                         demand=demand(item), plant=item["key"],
                         access_hardware=site.w.hardware_alias("access"),
                         access_devices=corporate_facts["access_devices"] + process_facts["access_devices"],
                         access_usable_ports=corporate_facts["access_usable_ports"],
                         ot_access_devices=process_facts["access_devices"],
                         required_device_roles={"role/wan-edge": 2, "role/distribution": 4,
                                                "role/access": corporate_facts["access_count"]
                                                + process_facts["access_count"]})
    site.contract["assumptions"].append(
        "Each plant holds one authored ground floor: a reception, up to eight twelve-desk office pods, up to twelve "
        "loading docks and up to twelve production line cells, all served by the single plant equipment room. Every "
        "room keeps a permanent reserved position, so commissioning a line, opening a dock or hiring appends rooms "
        "instead of renumbering the floor.")
    site.contract["assumptions"].extend(SHARED_ASSUMPTIONS)
    equipment.enrich_site(site, demonstrations=False)
    site.management(corporate)
    site.power()
