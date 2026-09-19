"""Managed service provider: one NOC operating many separate customer tenants.

The estate is deliberately two things at once. The provider runs its own small
operations site — the NOC, which contains its machine room — and it operates the
network at every customer office. Those offices are *owned* by the customer: the
site, its racks, rooms, devices, VLANs, prefixes and addresses all carry that
customer's tenant, and no cable, segment, VLAN or service ever crosses from one
customer to another. What the provider contributes is operation, expressed in the
records that already exist for it: the equipment role and actual tenant on every
direct technical assignment, the per-customer management segment, the provider's
own scoped service desks, the shared infrastructure owner, and the carrier
accounts the provider holds on the customers' behalf.

Office counts, staffing, wireless budgets and service thresholds are authored
planning demand. They are not headcount, seat licences, ticket volume, measured
traffic or a surveyed RF design. Shared builders allocate every device, port,
address, rack and cable; the independent checks in `validate_msp.py` inspect the
finished graph.
"""

from copy import deepcopy
import ipaddress
import math
import re

from . import campus, datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation
from .model import DesignError, World, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps",
          "naming", "site_names", "hardware"}
# Client segments every managed office carries. `guest` is added only where the
# customer asked for visitor wireless; the NOC never offers any of them. Each
# office also addresses its carrier handoff in a `wan` segment, which has no
# gateway SVI and no client.
OFFICE_NETWORKS = ("management", "staff", "wireless", "security")
GUEST_NETWORK = "guest"
# Foundation VRFs belong to the provider's own operations site. Managed-office
# segments live in per-customer routing contexts instead, so no shared routing
# context spans two accounts and no empty global VRF is emitted for them.
NETWORKS = ("management", "applications", "database", "backup", "wan", "storage")
CUSTOMER_DEFAULTS = dict(offices=1, staff=24)
DEFAULT_CUSTOMERS = [dict(key="summit-legal", offices=2, staff=24),
                     dict(key="harbor-dental", offices=3, staff=12),
                     dict(key="brightline-media", offices=1, staff=36),
                     dict(key="cornerstone-realty", offices=2, staff=16)]
MAX_CUSTOMERS, MAX_OFFICES = 24, 4
# Hyphen-separated lowercase key: no leading, trailing or doubled hyphen.
CUSTOMER_KEY = r"(?=.{1,20}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
MIN_STAFF, MAX_STAFF = 4, 4 * places.OFFICE_DESKS
# Authored per-zone concurrent device budgets. These size AP mount counts; they
# are not RF capacity, association evidence, headcount or licence counts.
RECEPTION_MANAGED, RECEPTION_GUEST = 6, 12
DEVICES_PER_DESK = 2
# An office segment is a /24: 254 host addresses less its two gateway SVIs.
GUEST_CAPACITY = 252
# Authored per-office planning demand, not a measured traffic model.
OFFICE_BASE_MBPS, OFFICE_MBPS_PER_DESK = 20, 2
# Fictional service sizing. Thresholds count installed inventory and contracted
# accounts, never technicians, tickets, licences or measured load.
SERVICES = (
    ("monitoring", "applications", 4, 16384, 200000, "endpoints", 1500, 443),
    ("rmm", "applications", 4, 8192, 100000, "endpoints", 800, 443),
    ("helpdesk", "applications", 4, 8192, 100000, "customers", 8, 443),
    ("identity", "applications", 4, 8192, 100000, "endpoints", 2000, 443),
    ("dns", "applications", 2, 4096, 40000, "sites", 16, 53),
    ("inventory-db", "database", 8, 32768, 500000, "offices", 40, 5432),
    ("backup", "backup", 4, 16384, 1000000, "offices", 24, 443),
)
TIER_1 = {"monitoring", "rmm", "identity", "dns"}


def zones(item):
    """Authored wireless zones for one customer's office layout."""
    result = {"reception": dict(managed=RECEPTION_MANAGED, guest=RECEPTION_GUEST)}
    result.update({suffix: dict(managed=DEVICES_PER_DESK * desks, guest=0)
                   for suffix, desks in places.msp_office_pods(item["staff"])})
    return result


def _resolve_zones(item, label):
    defaults = zones(item)
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
                          "office's reserved guest /24; reduce demand or rebaseline a larger plan")
    return resolved


def _customers(supplied):
    """Resolve the keyed customer list; physical capacity is checked separately."""
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_CUSTOMERS:
        raise DesignError(f"customers must be a list of 1–{MAX_CUSTOMERS} keyed managed accounts")
    keys, codes, result = set(), set(), []
    for raw in supplied:
        if not isinstance(raw, dict) or "key" not in raw or raw.keys() - (CUSTOMER_DEFAULTS.keys() | {"key", "wireless"}):
            raise DesignError("Each customer entry needs a stable key and only supported demand fields: "
                              f"{', '.join(sorted(CUSTOMER_DEFAULTS))}, wireless")
        key = raw["key"]
        if not isinstance(key, str) or not re.fullmatch(CUSTOMER_KEY, key) or key in keys:
            raise DesignError("customer keys must be unique hyphen-separated lowercase identifiers of at most 20 "
                              "characters, with no leading, trailing or doubled hyphen")
        code = key.replace("-", "")
        if code in codes:
            raise DesignError("customer keys must also be distinct without hyphens to preserve unique device DNS names")
        keys.add(key)
        codes.add(code)
        item = CUSTOMER_DEFAULTS | deepcopy(raw)
        for field, low, high in (("offices", 1, MAX_OFFICES), ("staff", MIN_STAFF, MAX_STAFF)):
            if type(item[field]) is not int or not low <= item[field] <= high:
                raise DesignError(f"customer {key}: {field} must be an integer from {low} through {high}")
        item["wireless"] = _resolve_zones(item, f"Customer {key}")
        result.append(item)
    return sorted(result, key=lambda item: item["key"])


def premises(recipe):
    """Ordered (site id, customer) pairs for every managed office."""
    return [(f"off-{item['key']}-{n+1:02}", item)
            for item in recipe["customers"] for n in range(item["offices"])]


def peak_mbps(item):
    """Authored per-office planning demand, not a measured traffic model."""
    return OFFICE_MBPS_PER_DESK * item["staff"] + OFFICE_BASE_MBPS


def office_radios(item):
    return sum(places.wireless_aps(zone) for zone in item["wireless"].values())


def office_cameras(item):
    """One camera in reception and one over each staff pod."""
    return 1 + len(places.msp_office_pods(item["staff"]))


def demand(item):
    """Installed inventory only; desks and radios are capacity, not headcount."""
    return dict(staff_desks=item["staff"], office_pods=len(places.msp_office_pods(item["staff"])),
                workstations=item["staff"], aps=office_radios(item), cameras=office_cameras(item),
                managed_clients=sum(zone["managed"] for zone in item["wireless"].values()),
                guest_clients=sum(zone["guest"] for zone in item["wireless"].values()),
                peak_mbps=peak_mbps(item))


def total_peak(recipe):
    return sum(peak_mbps(item) for _, item in premises(recipe))


def counts(recipe):
    """Independent installed-inventory totals used to size the managed services."""
    offices = premises(recipe)
    endpoints = sum(item["staff"] + office_radios(item) + office_cameras(item) for _, item in offices)
    return dict(customers=len(recipe["customers"]), offices=len(offices),
                sites=len(offices) + 1, endpoints=endpoints)


def workloads(recipe):
    """Size the shared managed services from resolved demand, never inventory."""
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
                           replica_description="complete managed-service shard; application replication and recovery are not executed"))
    return result


def resolve(raw):
    if unknown := raw.keys() - (COMMON | {"profile", "customers", "demo"}):
        raise DesignError(f"Unknown MSP recipe fields: {', '.join(sorted(unknown))}; "
                          "describe the managed customer accounts and their office demand")
    common = dict(namespace="arbor", name="Arbor Managed Networks", address_pool="10.0.0.0/8")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="msp", demo=resolve_demo(raw.get("demo", "baseline")))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("MSP address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    if recipe["reservation_user"]:
        raise DesignError("MSP rack-user reservations are not implemented; leave reservation_user empty")
    # The reviewed bounds above (24 accounts, four offices, 48 desks) cap each
    # office's own peak at 116 Mbps — inside the 1 Gbps office handoff after the
    # widest supported reserve — and the aggregate at 11,136 Mbps, which the
    # shared NOC aggregation the DC builder sizes must answer. Raising any of
    # those bounds needs both ceilings rechecked.
    recipe["customers"] = _customers(raw.get("customers", deepcopy(DEFAULT_CUSTOMERS)))
    return recipe


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous MSP plan does not reproduce from its recipe and ledgers; "
                              "use an intact frozen plan or new baseline")
        del reproduced
        current = {item["key"]: item for item in recipe["customers"]}
        for before in previous["recipe"]["customers"]:
            after = current.get(before["key"])
            if after is None:
                raise DesignError(f"Removing customer {before['key']} requires a new baseline; "
                                  "Diode replay does not delete retired objects")
            for field in ("offices", "staff"):
                if after[field] < before[field]:
                    raise DesignError(f"Customer {before['key']}: decreasing {field} requires a new baseline; "
                                      "ordinary growth cannot retire objects")
            if any(zone not in after["wireless"] or any(after["wireless"][zone][name] < values[name]
                    for name in ("managed", "guest")) for zone, values in before["wireless"].items()):
                raise DesignError(f"Customer {before['key']}: reducing or removing a wireless zone requires a new baseline")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    offices = premises(r)
    world.reserve_sites(["noc-01"] + [sid for sid, _ in offices])
    foundation(world, industry="managed service provider", inherited=False, networks=NETWORKS,
               site_kinds={"dc", "office"},
               device_roles=("wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
                             "pdu", "workstation", "ap", "camera", "patch-panel", "wall-outlet"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server",
                                 "endpoint", "ap", "patch-panel", "wall-outlet"})
    _accounts(world)
    noc = Site(world, "noc-01", "dc",
               "Network operations center; its machine room holds the shared managed-service platform")
    datacenter.build(noc, workloads=workloads(r), wan_peak_mbps=total_peak(r), include_equipment=False,
                     assumptions=[
        "One operations site holds both the NOC and its machine room. Its WAN budget covers the declared managed-office "
        "peaks under a synthetic centralized-service traffic model; this is not measured customer throughput.",
        "Each complete replica group is separated across compute racks; application replication, failover and recovery "
        "are not executed.",
        "Service groups cover 1,500 installed managed endpoints for monitoring, 800 for remote management, 2,000 for "
        "identity, 8 contracted accounts for the service desk, 16 sites for DNS, 40 managed offices for the inventory "
        "database and 24 for backup, minimum one each. These are fictional planning thresholds, not ticket volume, "
        "agent counts, licence entitlements or retention sizing.",
        "Identity includes RADIUS UDP1812/1813 inventory for managed staff WLAN authentication intent. No authentication "
        "server, directory, agent, remote-access session or ticketing workflow is configured, running or certified.",
        "The inventory database is named for the provider's own asset records. It does not connect to, synchronise with "
        "or reconcile against this NetBox instance.",
    ])
    managed = []
    guest_sites = set()
    for sid, item in offices:
        site = Site(world, sid, "office",
                    "Managed customer office; access, edge and wireless operated under contract",
                    tenant=f"tenant/{item['key']}",
                    routing_domain=f"vrf/customer/{item['key']}/{{role}}")
        world.obj(site.key)["meta"]["managed_customer"] = item["key"]
        _office(site, item)
        managed.append(world.obj(site.key))
        if sum(zone["guest"] for zone in item["wireless"].values()):
            guest_sites.add(site.key)
    networking.wireless(world, managed, lan_roles=(("staff", "staff", "wlan0"),),
                        diagnostic=False, stable_channels=True, guest_sites=guest_sites)
    equipment.enrich(world)
    optics.enrich(world)
    poe.enrich(world)
    ipv6.enrich(world)
    networking.macs(world)
    operations.supporting_records(world)
    _managed_by(world)
    names = set()
    for obj in world.objects.values():
        if obj["kind"] == "device":
            identity = (obj["refs"]["site"], obj["attrs"]["name"])
            if identity in names:
                raise DesignError(f"{obj['key']}: two devices at this site would emit the same display name; "
                                  "review the office endpoint labels")
            names.add(identity)
    return world.finish()


def _accounts(world):
    """One tenant group, one tenant and one VRF family for each managed account.

    The VRFs exist before any office is built so the shared site builder finds
    them instead of manufacturing a bank-lineage routing context.
    """
    ns = world.recipe["namespace"]
    group = world.add("tenant_group", "tenant-group/customers",
                      {"name": f"{ns} customers", "slug": f"{ns}-customers",
                       "description": "Managed accounts; each customer owns its own sites, equipment and address space"})
    for item in world.recipe["customers"]:
        key = item["key"]
        title = key.replace("-", " ").title()
        tenant = world.add("tenant", f"tenant/{key}",
                           {"name": f"{ns} {title}", "slug": f"{ns}-cust-{key}",
                            "description": f"Managed customer of {world.recipe['name']}; owns its offices, equipment and addressing"},
                           {"group": group})
        guest = bool(sum(zone["guest"] for zone in item["wireless"].values()))
        for role in OFFICE_NETWORKS + (("wan", GUEST_NETWORK) if guest else ("wan",)):
            world.add("vrf", f"vrf/customer/{key}/{role}",
                      {"name": f"{ns}-cust-{key}-{role}", "enforce_unique": True,
                       "description": f"{title} {role} routing context; customer address space is not shared between accounts"},
                      {"tenant": tenant})


def _managed_by(world):
    """Record the operating relationship on records that already exist for it.

    Ownership is the customer tenant on the site and its equipment. Operation is
    the provider's own infrastructure owner plus the per-tenant technical desk the
    shared operations builder already assigned by equipment role and actual
    tenant. This adds no new object kind and no cross-tenant reference.
    """
    ns = world.recipe["namespace"]
    desks = {obj["refs"]["object"]: obj["refs"]["contact"] for obj in world.objects.values()
             if obj["kind"] == "contact_assignment" and obj["refs"].get("role") == "contact-role/operations"}
    for sid, item in premises(world.recipe):
        site = f"site/{sid}"
        desk = desks.get(site)
        if desk is None or world.objects[desk]["key"] != f"contact/operations/tenant/{item['key']}":
            raise DesignError(f"{site}: managed office must carry its own customer technical desk")
        node = world.obj(site)
        node["attrs"]["comments"] = (
            f"Customer-owned premises operated by {world.recipe['name']} under a managed-network contract. "
            f"Escalation runs through {world.obj(desk)['attrs']['name']}; the local management segment "
            f"({world.obj(f'prefix/{sid}/management')['attrs']['prefix']}) carries the operated equipment. "
            "No service-level commitment, remote-access path or ticketing workflow is represented.")
        node["meta"]["operated_by"] = {"provider": ns, "technical_contact": desk,
                                       "management_segment": f"prefix/{sid}/management"}


SHARED_ASSUMPTIONS = [
    "Customer premises, street addresses, floor geometry, pod counts and equipment counts are fictional design inputs, "
    "not a surveyed managed estate.",
    "Installed workstations, radios and cameras are equipment inventory. They are not headcount, seat licences, managed "
    "agent counts, ticket volume or measured utilisation.",
    "Each office is owned by its customer tenant and operated by the provider. Ownership is the tenant on the site, its "
    "rooms, racks, equipment, VLANs, prefixes and addresses; operation is the provider's infrastructure owner and the "
    "per-customer technical desk assigned by equipment role and actual tenant. No contract, entitlement, service-level "
    "commitment or authorisation boundary is represented.",
    "Customer estates are separate. No cable, segment, VLAN, prefix, address or service joins two customers, and each "
    "account holds its own segment routing contexts. This is modeled separation, not configured or enforced isolation.",
    "Every managed WLAN names the provider's own shared DNS and RADIUS listener inventory, the one dependency that "
    "crosses from the provider to a customer. It is recorded inventory intent; no reachability, resolution or "
    "authentication result is asserted, and no customer depends on another customer's inventory.",
    "The per-customer management segment carries the operated equipment's addresses. No remote-access path, jump host, "
    "VPN, out-of-band network or monitoring session between the NOC and a customer office is modeled or asserted.",
    "The provider holds the carrier accounts for its customers' access circuits. Purchased capacity is inventory; the "
    "physical handoff, carrier duct diversity and any commercial term are separate and unmodeled.",
    "Access switches grow in pairs per office equipment room and retain each endpoint's reserved physical port as demand "
    "grows; endpoints themselves remain single-homed.",
    "Wireless zone managed/guest counts are explicit local device budgets that size AP mounts at 32 devices per radio, "
    "up to four mounts per zone. This is an authored equipment envelope, not surveyed RF capacity or observed associations.",
    "Two modeled carriers and gateways do not establish diverse ducts or running routing, firewall, authentication or HA behaviour.",
]


def _office(site, item):
    """Build one managed customer office from the shared room and access allocators."""
    rooms = places.msp_office_rooms(site, item["staff"])
    guest = bool(sum(zone["guest"] for zone in item["wireless"].values()))
    networks = OFFICE_NETWORKS + ((GUEST_NETWORK,) if guest else ())
    dist, edges, upstreams = campus.aggregation(site, networks, peak_mbps(item))
    endpoints = []

    def add(label, role, segment, room, cohort, ordinal=1):
        key = site.device("ap" if role == "ap" else "endpoint", label, role, racked=False,
                          meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
        places.msp_endpoint(site, key, room, cohort, ordinal)
        endpoints.append((key, segment))

    def radios(zone, label, room):
        cohort = "reception-ap" if zone == "reception" else "staff-pod-ap"
        for ordinal in range(1, places.wireless_aps(item["wireless"][zone]) + 1):
            add(label if ordinal == 1 else f"{label}-{ordinal:02}", "ap", "wireless", room, cohort, ordinal)

    pods = places.msp_office_pods(item["staff"])
    for index in range(item["staff"]):
        pod, desk = divmod(index, places.OFFICE_DESKS)
        add(f"desk-{index+1:03}", "workstation", "staff", rooms["pods"][pod], "staff-desk", desk + 1)
    radios("reception", "ap-reception", rooms["reception"])
    add("cam-reception", "camera", "security", rooms["reception"], "security-camera", 1)
    for index, (zone, _) in enumerate(pods):
        radios(zone, f"ap-{zone}", rooms["pods"][index])
        add(f"cam-{zone}", "camera", "security", rooms["pods"][index], "security-camera", 1)
    facts = campus.access(site, endpoints, upstreams, networks,
                          {"access_hardware": "access", "upstreams": 2, "label": "access-"}, stable=True)
    site.contract.update(endpoint_count=len(endpoints), demand=demand(item),
                         access_hardware=site.w.hardware_alias("access"),
                         access_devices=facts["access_devices"],
                         access_usable_ports=facts["access_usable_ports"],
                         managed_customer=item["key"],
                         required_device_roles={"role/wan-edge": 2, "role/distribution": 2,
                                                "role/access": facts["access_count"]})
    site.contract["assumptions"].append(
        "Each managed office holds one authored ground floor: a reception and up to four twelve-desk staff pods, each "
        "with its own coverage radio and one camera. Every pod keeps a permanent reserved position, so hiring appends "
        "desks and pods instead of renumbering the floor.")
    site.contract["assumptions"].extend(SHARED_ASSUMPTIONS)
    equipment.enrich_site(site, demonstrations=False)
    site.management(dist)
    site.power()
