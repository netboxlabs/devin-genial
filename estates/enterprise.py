"""Enterprise policy: workload groups and replica placement, not branch counts."""

from copy import deepcopy
import ipaddress
import re

from .blocks import Site, foundation
from . import datacenter, equipment, ipv6, networking, operations, poe, optics
from .model import DesignError, World, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user"}
NETWORKS = ("management", "applications", "database", "backup", "wan", "storage")
DEFAULT_WORKLOADS = [
    dict(key="inventory-api", groups=3, replicas=2, failure_domain="rack",
         listeners=[dict(key="", name="inventory-api", protocol="tcp", ports=[443]),
                    dict(key="metrics", name="inventory-metrics", protocol="tcp", ports=[9090])]),
    dict(key="orders-db", groups=2, replicas=2, failure_domain="rack", network="database",
         vcpus=8, memory_mb=32768, disk_mb=500000,
         listeners=[dict(key="", name="orders-db", protocol="tcp", ports=[5432])]),
    dict(key="build-artifacts", groups=2, replicas=2, failure_domain="host", network="backup", disk_mb=1000000),
]


def resolve(raw):
    allowed = COMMON | {"profile", "data_centers", "wan_peak_mbps", "workloads", "demo"}
    if unknown := raw.keys() - allowed:
        raise DesignError(f"Unknown enterprise recipe fields: {', '.join(sorted(unknown))}; use workloads and WAN demand, not branch counts")
    common = dict(namespace="summit", name="Summit Enterprise Infrastructure", address_pool="10.64.0.0/12")
    common.update({key: value for key, value in raw.items() if key in COMMON})
    # Reuse established scalar/date/private-pool validation; do not carry bank
    # branches, ownership or service policy into the resolved enterprise recipe.
    checked = resolve_bank_recipe(common)
    recipe = {key: checked[key] for key in sorted(COMMON) if key in checked}
    recipe.update(profile="enterprise-data-center", data_centers=raw.get("data_centers", 2),
                  wan_peak_mbps=raw.get("wan_peak_mbps", 1200), demo=raw.get("demo", "baseline"))
    if ipaddress.ip_network(recipe["address_pool"]).prefixlen > 16:
        raise DesignError("Enterprise address_pool must hold /16 site reservations; choose an aligned private /8 through /16")
    for field, low, high in (("data_centers", 1, 8), ("wan_peak_mbps", 1, 16000)):
        if type(recipe[field]) is not int or not low <= recipe[field] <= high:
            raise DesignError(f"{field} must be an integer from {low} through {high}; WAN demand is Mbps per data center")
    if recipe["patching"] != "direct":
        raise DesignError("Enterprise DC currently models direct fabric channels; patching must be 'direct'")
    if recipe["reservation_user"]:
        raise DesignError("Enterprise rack reservations are not implemented; leave reservation_user empty")
    recipe["demo"] = resolve_demo(recipe["demo"])
    workloads = raw.get("workloads", deepcopy(DEFAULT_WORKLOADS))
    if not isinstance(workloads, list) or not 1 <= len(workloads) <= 16:
        raise DesignError("workloads must be a list of 1–16 workload groups; hardware and address capacity are checked separately")
    keys, resolved = set(), []
    fields = {"key", "groups", "replicas", "failure_domain", "network", "vcpus", "memory_mb", "disk_mb", "listeners", "criticality"}
    for item in workloads:
        if not isinstance(item, dict) or "key" not in item or item.keys() - fields:
            raise DesignError("Each workload needs a key and only supported demand, replica, resource and listener fields")
        key = item["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", key) or key in keys:
            raise DesignError("Workload keys must be unique lowercase identifiers of 1–40 characters")
        keys.add(key)
        workload = dict(key=key, groups=1, replicas=2, failure_domain="rack", network="applications",
                        vcpus=4, memory_mb=8192, disk_mb=100000, criticality="tier-2",
                        listeners=[dict(key="", name=key, protocol="tcp", ports=[443])])
        workload.update(deepcopy(item))
        for field, low, high in (("groups", 1, 512), ("replicas", 2, 4), ("vcpus", 1, 64),
                                  ("memory_mb", 1, 262144), ("disk_mb", 1, 8000000)):
            if type(workload[field]) is not int or not low <= workload[field] <= high:
                raise DesignError(f"Workload {key}: {field} must be an integer from {low} through {high}; reserve and host limits are checked during planning")
        if workload["failure_domain"] not in ("host", "rack"):
            raise DesignError(f"Workload {key}: failure_domain must be host or rack; cross-site application recovery is not modeled")
        if workload["network"] not in ("applications", "database", "backup"):
            raise DesignError(f"Workload {key}: network must be applications, database or backup")
        if workload["criticality"] not in ("tier-1", "tier-2"):
            raise DesignError(f"Workload {key}: criticality must be tier-1 or tier-2")
        resolved.append(workload)
    recipe["workloads"] = sorted(resolved, key=lambda item: item["key"])
    return recipe


def generate(recipe, previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe", {}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous plan does not reproduce from its recipe and ledgers; use an intact frozen plan or rebaseline")
        old = {item["key"]: item for item in previous["recipe"]["workloads"]}
        current = {item["key"]: item for item in recipe["workloads"]}
        if old.keys() - current.keys() or any(current[key]["groups"] < item["groups"] for key,item in old.items() if key in current):
            raise DesignError("Removing workloads or decreasing groups requires a new baseline; Diode replay does not delete retired objects")
        if any(recipe[field] < previous["recipe"][field] for field in ("data_centers", "wan_peak_mbps")):
            raise DesignError("Decreasing data centers or WAN demand requires a new baseline; ordinary growth cannot retire target objects")
        for item in recipe["workloads"]:
            if item["key"] in old and {k:v for k,v in item.items() if k != "groups"} != {k:v for k,v in old[item["key"]].items() if k != "groups"}:
                raise DesignError(f"Workload {item['key']}: changing replica policy, resources or listeners requires a new baseline; groups can grow in place")
        if any(item["failure_domain"] == "rack" for item in recipe["workloads"]) != any(item["failure_domain"] == "rack" for item in old.values()):
            raise DesignError("Changing network rack-diversity policy requires a new baseline")
    return _generate(recipe, previous)


def _generate(recipe, previous=None):
    world = World(recipe, previous)
    r = world.recipe
    ids = [f"dc-{i+1:02}" for i in range(r["data_centers"])]
    world.reserve_sites(ids)
    foundation(world, industry="enterprise infrastructure", inherited=False, networks=NETWORKS,
               device_roles=("wan-edge", "spine", "leaf", "server", "management", "pdu"),
               hardware_aliases={"core", "leaf", "edge", "server", "access", "pdu", "console-server"}, site_kinds={"dc"})
    workloads = []
    for item in r["workloads"]:
        workload = {key:value for key,value in item.items() if key != "groups"}
        workload.update(slot=world.reserve("workload-slots", item["key"], 16),
                        instances=item["groups"]*item["replicas"],
                        replica_description="complete modeled shard replica at this site; application replication is not executed")
        workloads.append(workload)
    for site_id in ids:
        site = Site(world, site_id, "dc", "Enterprise data center; workload-sized compute and independent carrier attachments")
        datacenter.build(site, workloads=workloads, wan_peak_mbps=r["wan_peak_mbps"], include_equipment=False,
            assumptions=["Each workload group is a synthetic application shard; each replica represents its complete declared resource demand.",
                         "Replica placement is checked across requested host or rack domains; application replication, traffic and recovery execution are not simulated.",
                         "Each site receives the stated workload groups. A second site does not by itself establish cross-site application recovery.",
                         "Each carrier's 1 Gbps attachment pairs cover per-site peak demand plus reserve; carrier interiors and diverse last-mile ducts are abstracted.",
                         "Reference hosts supply fictional 64-vCPU/256-GiB/8-TB planning budgets. Reserve is headroom, not a spare-host migration guarantee.",
                         "Cabinets occupy a bounded synthetic row grid; inter-rack cable routes use Manhattan distance and three metres of service slack."])
    equipment.enrich(world)
    optics.enrich(world)
    poe.enrich(world)
    ipv6.enrich(world)
    networking.macs(world)
    operations.supporting_records(world)
    return world.finish()
