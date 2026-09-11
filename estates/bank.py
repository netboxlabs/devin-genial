"""Regional bank profile: demand-sized branches, WAN, and replicated services."""

import math
from copy import deepcopy

from .blocks import Site, foundation
from .model import DesignError, World, resolve_recipe
from . import places, operations, equipment, networking, datacenter, campus, poe, optics

# Authored connected designs. Procurement provenance is fictional, not vendor EOL.
BRANCH_DESIGNS = {
    "modern": dict(access_hardware="access", upstreams=2, label="access-"),
    "inherited": dict(access_hardware="inherited-access", upstreams=1, label="access-"),
    "refreshed": dict(access_hardware="access", upstreams=2, label="access-r"),
}

BRANCHES = {
    "small": dict(workstations=12, atms=2, aps=2, cameras=2, peak_mbps=20),
    "medium": dict(workstations=36, atms=4, aps=4, cameras=4, peak_mbps=50),
    "large": dict(workstations=84, atms=6, aps=8, cameras=8, peak_mbps=100),
}
# Fictional service sizing assumptions, never vendor performance claims.
SERVICES = (
    ("identity", "applications", 4, 8192, 100, 100),
    ("dns", "applications", 2, 4096, 40, 200),
    ("teller-api", "applications", 4, 8192, 100, 40),
    ("atm-switch", "applications", 4, 8192, 100, 60),
    ("ledger-db", "database", 8, 32768, 500, 100),
    ("monitoring", "applications", 4, 16384, 200, 100),
    ("backup", "backup", 4, 16384, 1000, 100),
    ("fraud-analysis", "applications", 8, 16384, 200, 80),
)


def branch(site, demand):
    w = site.w
    places.arrange(site, demand)
    design = BRANCH_DESIGNS[site.design]
    network_roles = ("users", "atm", "wireless", "security", "management")
    compact = w.obj(site.key)["meta"].get("branch_size") == "small"
    architecture = "compact-routed-edge" if compact else "distribution"
    w.obj(site.key)["meta"]["branch_architecture"] = architecture
    dist, edges, upstreams = campus.aggregation(site, network_roles, demand["peak_mbps"], compact=compact)
    endpoints = []
    for label, n, alias, role, segment in (
        ("desk", demand["workstations"], "endpoint", "workstation", "users"),
        ("atm", demand["atms"], "atm", "atm", "atm"),
        ("ap", demand["aps"], "ap", "ap", "wireless"),
        ("cam", demand["cameras"], "endpoint", "camera", "security"),
    ):
        for i in range(n):
            key = site.device(alias, f"{label}-{i+1:03}", role, racked=False,
                              meta={"endpoint": True, "network": segment, "power_scope": "local outlet or PoE"})
            places.place_endpoint(site, key, role, i+1)
            endpoints.append((key, segment))
    access = campus.access(site, endpoints, upstreams, network_roles, design, compact=compact)
    switches, usable, access_count = access["access_devices"], access["access_usable_ports"], access["access_count"]
    site.contract.update(endpoint_count=len(endpoints), demand=demand, branch_design=site.design,
                         branch_architecture=architecture,
                         access_hardware=design["access_hardware"], access_devices=switches,
                         access_usable_ports=usable, required_device_roles={"role/wan-edge": 2, "role/distribution": len(dist), "role/access": access_count})
    site.contract["required_device_roles"].update({"role/workstation": demand["workstations"], "role/atm": demand["atms"],
                                                 "role/ap": demand["aps"], "role/camera": demand["cameras"]})
    site.contract["assumptions"].extend([
        "Inherited access switches have one PSU and one direct upstream each. This accepted design weakness is the refresh scenario's subject."
        if site.design == "inherited" else "Access switches have two PSUs and two distinct direct upstream attachments.",
        "Access cabling is modeled as continuous switch-to-endpoint channels; intermediate passive patching is abstracted."
        if w.recipe["patching"] == "direct" else
        "Passive front/rear patch mappings use the legacy Diode fields; NetBox 4.4.10 is the source-checked target, and 4.5+ is incompatible with this export.",
        "Branch network intent includes independent gateway SVIs; routing policy, firewall enforcement, and protocol convergence are not simulated.",
        "Endpoints are single-homed and distributed across access switches. Full physical port inventories include unused ports.",
        "Carrier domains are separate; last-mile duct diversity is not modeled.",
        "AP demand is carried in a dedicated wireless management segment; RF coverage and PoE electrical budgets are not qualified.",
    ])
    equipment.enrich_site(site)
    if compact:
        site.contract["assumptions"].append(
            "Small branches place VLAN gateways on the two WAN edges. An inter-access VLAN trunk keeps inherited endpoint cohorts in one broadcast domain; "
            "the peer switch provides an indirect edge path. Bridge intent is modeled; firewall performance, STP, and HA are not qualified.")
        site.management(edges, uplinks=[site.interface(edge, "port12") for edge in edges])
    else:
        site.management(dist)
    if site.contract["kind"] == "hq":
        site.contract["assumptions"].append(
            "HQ demand places one AP per occupied office pod and two cameras per floor. Each floor has local access, patching, management and A/B distribution panels. "
            "Direct-terminated fiber risers connect IDFs to the MDF; two upstream devices do not establish separate building routes or facility supplies.")
    site.power()


def data_center(site, total_branches, total_peak):
    """Resolve bank business demand before invoking shared physical construction."""
    workloads = []
    for slot, (name, network, cpus, memory, disk, branches_per_instance) in enumerate(SERVICES):
        listeners = [{"key": "", "name": name, "protocol": "tcp",
                      "ports": [5432 if name == "ledger-db" else 53 if name == "dns" else 443]}]
        if name == "dns":
            listeners.append({"key": "udp", "name": "dns-udp", "protocol": "udp", "ports": [53]})
        elif name == "identity":
            listeners.append({"key": "radius", "name": "radius", "protocol": "udp", "ports": [1812, 1813]})
        workloads.append(dict(key=name, slot=slot, network=network,
                              instances=max(2, math.ceil(total_branches / branches_per_instance)),
                              vcpus=cpus, memory_mb=memory, disk_mb=disk * 1000,
                              listeners=listeners,
                              criticality="tier-1" if name in ("identity", "dns", "ledger-db", "atm-switch") else "tier-2",
                              replica_description="paired with the other DC"))
    datacenter.build(site, workloads=workloads, wan_peak_mbps=total_peak, assumptions=[
        "Each DC WAN budget covers all declared branch peak demand independently; this is a modeled capacity assumption, not a throughput benchmark.",
        "Shared services are deployed in both DCs; application replication and recovery protocols are not simulated.",
        "Reference servers provide a declared 64-vCPU/256-GiB/8-TB budget with reserved headroom; these are fictional sizing assumptions.",
        "Spine/leaf paths represent L2/L3 intent; no routing convergence or firewall reachability is asserted.",
    ])

def assign_designs(w, branch_ids, previous, transition):
    """Resolve the small design grammar before allocating physical objects."""
    r = w.recipe
    active = {key for key, _ in branch_ids}
    known = active | w.design_assignments.keys()
    if unknown := (r["site_designs"].keys() | set(r["acquired_sites"])) - known:
        raise DesignError(f"Unknown branch IDs in design inputs: {', '.join(sorted(unknown))}")
    changed_site = None
    if transition is not None:
        if not previous or not isinstance(transition, dict) or transition.keys() != {"site", "operation"}:
            raise DesignError("A transition requires a previous plan and exactly site/operation")
        changed_site, operation = transition["site"], transition["operation"]
        if changed_site not in active or previous.get("design_assignments", {}).get(changed_site) != "inherited":
            raise DesignError("Acquisition/refresh requires an existing inherited branch")
        expected = deepcopy(previous["recipe"])
        if operation == "acquire" and changed_site not in expected["acquired_sites"]:
            expected["acquired_sites"].append(changed_site)
        elif operation == "refresh" and changed_site in expected["acquired_sites"]:
            expected["site_designs"][changed_site] = "refreshed"
        else:
            raise DesignError("Acquire an independent inherited branch, then refresh the acquired branch")
        if r != resolve_recipe(expected):
            raise DesignError("Transition recipe must change only the selected acquisition or refresh; unrelated changes are rejected")
    for key, _ in branch_ids:
        existing = w.design_assignments.get(key)
        if existing:
            selected = r["site_designs"].get(key, existing)
            was_acquired = existing == "refreshed" or key in previous["recipe"]["acquired_sites"]
            acquired = selected == "refreshed" or key in r["acquired_sites"]
            if key != changed_site and (selected != existing or acquired != was_acquired):
                raise DesignError(f"{key}: changing an existing design or ownership requires an explicit scenario or rebaseline")
        elif key in r["site_designs"]:
            selected = r["site_designs"][key]
        else:
            choice = w.choose(key, "branch-design", range(sum(r["design_mix"].values())))
            for name, weight in r["design_mix"].items():
                choice -= weight
                if choice < 0:
                    selected = name
                    break
        if key in r["acquired_sites"] and selected == "modern":
            raise DesignError(f"{key}: acquired_sites requires inherited lineage")
        w.design_assignments[key] = selected


def generate(recipe, previous=None, transition=None):
    """Expand a recipe, checking that reused ledgers reproduce the frozen estate."""
    if previous is not None:
        reproduced = _generate(previous["recipe"], previous)
        if any(reproduced[field] != previous[field] for field in ("objects", "contracts")):
            raise DesignError("Previous plan does not reproduce from its recipe and ledgers; use an intact frozen plan or explicitly rebaseline")
        del reproduced
    return _generate(recipe, previous, transition)


def _generate(recipe, previous=None, transition=None):
    w = World(recipe, previous)
    r = w.recipe
    ids = [f"dc-{i+1:02}" for i in range(r["data_centers"])] + [f"hq-{i+1:02}" for i in range(r["headquarters"])]
    branch_ids = [(f"br-{size[0]}{i+1:04}", size) for size, n in r["branches"].items() for i in range(n)]
    ids.extend(key for key, _ in branch_ids)
    assign_designs(w, branch_ids, previous, transition)
    w.reserve_sites(ids)
    foundation(w, hardware_aliases=("access", "inherited-access", "leaf", "core", "edge", "server",
               "patch-panel", "pdu", "endpoint", "atm", "ap", "wall-outlet", "console-server",
               "liquid-chassis", "liquid-blade"))
    # Authored HQ planning demand: two Mb/s per staff position, one AP per
    # twelve-desk pod and two cameras per occupied floor; no RF/traffic simulation.
    offices = math.ceil(r["headquarters_staff"] / 12)
    hq_demand = dict(workstations=r["headquarters_staff"], atms=0, aps=offices,
                     cameras=2*math.ceil(offices / 4), peak_mbps=2*r["headquarters_staff"])
    total_peak = sum(n*BRANCHES[size]["peak_mbps"] for size, n in r["branches"].items()) + r["headquarters"]*hq_demand["peak_mbps"]
    for i in range(r["data_centers"]):
        data_center(Site(w, f"dc-{i+1:02}", "dc", "Primary / recovery data center; separate modeled site failure domain"), len(branch_ids), total_peak)
    for i in range(r["headquarters"]):
        branch(Site(w, f"hq-{i+1:02}", "hq", "Headquarters campus: operations, finance, security and wireless"),
               hq_demand)
    for key, size in branch_ids:
        site = Site(w, key, "branch", f"{size.title()} retail branch; {BRANCHES[size]['workstations']} modeled staff desks")
        cohort = {"modern": "new-branch", "inherited": "legacy-refresh", "refreshed": "established"}[site.design]
        state = "acquired" if site.acquired else "independent" if site.lineage == "birch" else "native"
        w.obj(site.key)["meta"].update(branch_size=size, lifecycle_cohort=cohort,
                                     branch_design=site.design, lineage=site.lineage, acquisition_state=state)
        w.obj(site.key)["attrs"]["comments"] += f" Design: {site.design}; lineage: {site.lineage}; acquisition state: {state}. Procurement history; no vendor EOL claims."
        branch(site, BRANCHES[size])
    equipment.enrich(w)
    networking.enrich(w)
    optics.enrich(w)
    poe.enrich(w)
    operations.enrich(w)
    if previous:
        old_aggregates = {obj["key"] for obj in previous["objects"] if obj["kind"] == "aggregate"}
        if not old_aggregates <= w.objects.keys():
            raise DesignError("Growth would replace an aggregate allocation; explicit rebaseline or an aggregate expansion workflow is required")
    return w.finish()
