"""Shared data-center construction from explicit, already-sized workload demand.

Bank/customer policy supplies service identity, stable slots, instance counts,
resources, listeners and narrative. This module allocates their real graph.
"""

import math
import re
from decimal import Decimal

from .blocks import NETWORKS, trunk
from .model import DesignError
from . import equipment


def _per_host(workload, reserve):
    # Decimal reserve preserves exact-fit capacity at operator-specified boundaries.
    usable = Decimal(1) - Decimal(str(reserve))
    return min(int(Decimal(capacity) * usable // workload[field]) for field, capacity in
               (("vcpus", 64), ("memory_mb", 262144), ("disk_mb", 8000000)))


def _host_count(workload, per_host):
    """Size complete replica lanes; a partly filled lane still needs its host."""
    replicas = workload.get("replicas", 1)
    groups = workload["instances"] // replicas
    return replicas * ((groups + per_host - 1) // per_host)


def _workloads(workloads, site):
    """Check resolved policy at the construction boundary, before allocating."""
    reserve = site.w.recipe["reserve_fraction"]
    usable = Decimal(1) - Decimal(str(reserve))
    fields = {"key", "slot", "instances", "network", "vcpus", "memory_mb", "disk_mb",
              "listeners", "criticality", "replica_description"}
    optional = {"replicas", "failure_domain"}
    if not isinstance(workloads, list) or not workloads:
        raise DesignError("DC workloads must be a nonempty list of resolved service demands")
    keys, slots = set(), set()
    host_names, vm_names = set(), set()
    for workload in workloads:
        if (not isinstance(workload, dict) or not fields <= workload.keys()
                or workload.keys() - fields - optional):
            raise DesignError(f"DC workload requires exactly: {', '.join(sorted(fields))}; optional: replicas, failure_domain")
        key = workload["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", key) or key in keys:
            raise DesignError("DC workload keys must be unique lowercase identifiers of 1–40 characters")
        keys.add(key)
        slot = workload["slot"]
        if type(slot) is not int or slot < 0 or slot in slots:
            raise DesignError(f"DC workload {key}: slot must be a unique nonnegative integer")
        slots.add(slot)
        if workload["network"] not in ("applications", "database", "backup"):
            raise DesignError(f"DC workload {key}: network must be applications, database or backup")
        for field in ("instances", "vcpus", "memory_mb", "disk_mb"):
            if type(workload[field]) is not int or workload[field] <= 0:
                raise DesignError(f"DC workload {key}: {field} must be a positive integer")
        replicas, domain = workload.get("replicas", 1), workload.get("failure_domain", "none")
        if type(replicas) is not int or not 1 <= replicas <= 4:
            raise DesignError(f"DC workload {key}: replicas must be an integer from 1 through 4")
        if domain not in ("none", "host", "rack"):
            raise DesignError(f"DC workload {key}: failure_domain must be none, host or rack")
        if domain == "none" and replicas != 1:
            raise DesignError(f"DC workload {key}: replicas above 1 require host or rack failure_domain")
        if domain != "none" and replicas < 2:
            raise DesignError(f"DC workload {key}: host or rack failure_domain requires at least 2 replicas")
        if workload["instances"] % replicas:
            raise DesignError(f"DC workload {key}: total instances must be divisible by replicas, with at least one complete group")
        for field, capacity in (("vcpus", 64), ("memory_mb", 262144), ("disk_mb", 8000000)):
            if workload[field] > Decimal(capacity) * usable:
                raise DesignError(f"DC workload {key}: one VM exceeds the reference host {field} budget after reserve")
        per_host = _per_host(workload, reserve)
        host_count = _host_count(workload, per_host)
        if host_count > 16:
            raise DesignError(f"{site.name} service {key}: exhausted 16-host reservation; extend the compute blueprint")
        for names, labels in ((host_names, (f"{key}-host-{i+1:02}" for i in range(host_count))),
                              (vm_names, (f"{key}-{i+1:03}" for i in range(workload["instances"])))):
            for label in labels:
                name = site.display_name(label)
                if name in names:
                    raise DesignError(f"DC workload {key}: generated name {name!r} collides with another workload; choose distinct workload keys")
                names.add(name)
        for field in ("criticality", "replica_description"):
            if not isinstance(workload[field], str) or not workload[field].strip():
                raise DesignError(f"DC workload {key}: {field} must be explicit nonempty text")
        listeners = workload["listeners"]
        if not isinstance(listeners, list) or not listeners:
            raise DesignError(f"DC workload {key}: listeners must be a nonempty list")
        listener_keys, listener_names = set(), set()
        for listener in listeners:
            if not isinstance(listener, dict) or listener.keys() != {"key", "name", "protocol", "ports"}:
                raise DesignError(f"DC workload {key}: listener requires key, name, protocol and ports")
            suffix = listener["key"]
            if (not isinstance(suffix, str) or not re.fullmatch(r"(?:[a-z][a-z0-9-]{0,39})?", suffix)
                    or suffix in listener_keys):
                raise DesignError(f"DC workload {key}: listener keys must be unique lowercase identifiers or empty")
            listener_keys.add(suffix)
            if not isinstance(listener["name"], str) or not listener["name"].strip():
                raise DesignError(f"DC workload {key}: listener name must be nonempty text")
            if listener["name"] in listener_names:
                raise DesignError(f"DC workload {key}: listener names must be unique within their VM")
            listener_names.add(listener["name"])
            if listener["protocol"] not in ("tcp", "udp"):
                raise DesignError(f"DC workload {key}: listener protocol must be tcp or udp")
            ports = listener["ports"]
            if (not isinstance(ports, list) or not ports or
                    any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or
                    len(set(ports)) != len(ports)):
                raise DesignError(f"DC workload {key}: listener ports must be unique integers from 1 through 65535")
    # Explicit slots keep caller/list ordering from moving host fabric ports.
    # ponytail: each pool reserves 16 hosts; persistent allocation of new pool
    # slots belongs in the recipe planner before accepting dynamic workload edits.
    return sorted(workloads, key=lambda workload: workload["slot"])


def build(site, *, workloads, wan_peak_mbps, assumptions, include_equipment=True, wan_attachment=None):
    """Allocate a DC from resolved workload policy; no industry demand sizing here.

    Instances are total VMs, including replicas. Opt-in host/rack placement
    separates complete shard replicas; it does not implement application recovery.
    Equipment demonstrations can be omitted without losing serial console access.
    """
    workloads = _workloads(workloads, site)
    if type(wan_peak_mbps) is not int or wan_peak_mbps < 0:
        raise DesignError("DC wan_peak_mbps must be a nonnegative integer Mbps demand")
    if not isinstance(assumptions, list) or any(not isinstance(item, str) for item in assumptions):
        raise DesignError("DC assumptions must be a list of explicit planning statements")
    if type(include_equipment) is not bool:
        raise DesignError("DC include_equipment must be a boolean")
    if wan_attachment is not None and not callable(wan_attachment):
        raise DesignError("DC WAN attachment must be a callable construction policy")
    w = site.w
    reserve = w.recipe["reserve_fraction"]
    rack_diversity = any(workload.get("failure_domain") == "rack" for workload in workloads)
    # Replica racks alone are insufficient if a rack loss removes both upstreams.
    spines = [site.device("core", f"spine-{s}", "spine", **({"rack_domain": i} if rack_diversity else {}))
              for i, s in enumerate(("a", "b"))]
    pods = {}

    def pod(key):
        if key not in pods:
            ordinal = w.reserve(f"fabric-pods/{site.id}", key, 16)
            pair = [site.device("leaf", f"{key}-leaf-{s}", "leaf", **({"rack_domain": i} if rack_diversity else {}))
                    for i, s in enumerate(("a", "b"))]
            pods[key] = pair
            for i, leaf in enumerate(pair):
                for j, spine in enumerate(spines):
                    a = site.interface(leaf, f"Ethernet{49+j}/1")
                    b = site.interface(spine, f"Ethernet{ordinal*2+i+1}/1")
                    site.cable(a, b, "smf")
                    trunk(site, [a, b], ("applications", "database", "backup", "storage", "management"))
                site.redundant(leaf, spines)
        return pods[key]

    # Each provider attachment set covers the supplied peak after reserve.
    # The caller owns workload recovery and cross-site capacity assumptions.
    wan_pair_budget = 1000 * (1-Decimal(str(reserve)))
    wan_pairs = max(1, math.ceil(Decimal(str(wan_peak_mbps)) / wan_pair_budget))
    for i in range(wan_pairs):
        leaves = pod(f"wan-{i//20+1:02}")
        for j, side in enumerate(("a", "b")):
            edge = site.device("edge", f"edge-{i+1:03}-{side}", "wan-edge",
                               **({"rack_domain": j} if rack_diversity else {}))
            for k, leaf in enumerate(leaves):
                a, b = site.interface(edge, f"x{k+1}"), site.interface(leaf, f"Ethernet{(i%20)*2+j+1}")
                site.cable(a, b, "smf")
                trunk(site, [a, b], ("applications", "database", "backup", "management"))
            site.redundant(edge, leaves)
            if wan_attachment is None:
                site.wan(edge, side, i+1)
            else:
                wan_attachment(site, edge, side, i+1)
    # The first WAN leaf pair supplies modeled gateway SVIs for the service
    # segments. Addresses express routing intent; no failover protocol is faked.
    for i, gateway in enumerate(pods["wan-01"]):
        for role in ("applications", "database", "backup", "storage", "management"):
            vi = site.virtual_interface(gateway, f"Vlan{10*(NETWORKS.index(role)+1)}", role)
            site.address(vi, role, host=i+1, primary=role == "management", device=gateway)
    cluster = w.add("cluster", f"cluster/{site.id}", {"name": f"{site.name}-compute", "status": "active"},
                    {"type": "cluster-type", "scope_site": site.key, "tenant": "tenant"})
    hosts = []
    site.contract["required_services"] = {}
    for workload in workloads:
        name, network = workload["key"], workload["network"]
        cpus, memory, disk = workload["vcpus"], workload["memory_mb"], workload["disk_mb"]
        instances = workload["instances"]
        replicas, domain = workload.get("replicas", 1), workload.get("failure_domain", "none")
        site.contract["required_services"][name] = instances
        per_host = _per_host(workload, reserve)
        pool = []
        for i in range(_host_count(workload, per_host)):
            stable_slot = workload["slot"]*16+i
            leaves = pod(f"compute-{stable_slot//40+1:02}")
            metadata = {"resources": {"vcpus": 64, "memory_mb": 262144, "disk_mb": 8000000}, "service_pool": name}
            if domain != "none":
                metadata.update(replica_lane=i % replicas, failure_domain=domain)
            host = site.device("server", f"{name}-host-{i+1:02}", "server", group="compute", meta=metadata,
                               **({"rack_domain": i % replicas} if domain == "rack" else {}))
            w.obj(host)["refs"]["cluster"] = cluster
            pool.append(host)
            hosts.append(host)
            for j, leaf in enumerate(leaves):
                a, b = site.interface(host, f"eth{j}"), site.interface(leaf, f"Ethernet{stable_slot%40+1}")
                site.cable(a, b, "smf")
                trunk(site, [a, b], (network, "backup", "storage"))
            site.redundant(host, leaves)
        for i in range(instances):
            metadata = {"service": name, "criticality": workload["criticality"]}
            description = f"{name} service; modeled replica {i+1}, {workload['replica_description']}"
            if domain != "none":
                metadata.update(replica_group=i // replicas + 1, replica_lane=i % replicas,
                                replicas=replicas, failure_domain=domain)
                description = (f"{name} service; group {i // replicas + 1}, replica {i % replicas + 1} of {replicas}; "
                               f"{workload['replica_description']}")
            host_index = replicas * (i // replicas // per_host) + i % replicas
            vm = w.add("virtual_machine", f"vm/{site.id}/{name}/{i+1:03}",
                       {"name": site.display_name(f"{name}-{i+1:03}"), "status": "active", "vcpus": cpus, "memory": memory,
                        "disk": disk, "description": description},
                       {"cluster": cluster, "device": pool[host_index], "tenant": "tenant", "tags": ["tag/estate"],
                        "role": "role/database" if network == "database" else "role/backup-service" if network == "backup" else "role/application",
                        "platform": "platform/services"},
                       metadata)
            vif = w.add("vm_interface", f"{vm}/eth0", {"name": "eth0", "enabled": True, "mode": "access"},
                        {"virtual_machine": vm, "untagged_vlan": site.network(network)[0]})
            address = site.address(vif, network, primary=True, device=vm)
            for listener in workload["listeners"]:
                suffix = f"/{listener['key']}" if listener["key"] else ""
                attrs = {"name": listener["name"], "protocol": listener["protocol"],
                         "ports": list(listener["ports"])}
                if not suffix:
                    attrs["description"] = f"{listener['name']} listener on {w.obj(vm)['attrs']['name']}"
                w.add("service", f"service/{vm}{suffix}", attrs,
                      {"virtual_machine": vm, "ipaddresses": [address]})
        if domain != "none":
            site.contract.setdefault("workload_policies", {})[name] = dict(
                replicas=replicas, failure_domain=domain, groups=instances // replicas,
                per_host=per_host, hosts=pool,
                guarantee=f"Modeled placement retains a complete replica after loss of one {domain}; "
                          "application recovery is not executed or verified. No VM restart, migration or spare-host capacity guarantee.")
    site.contract["compute"].append(dict(cluster=cluster, hosts=hosts, reserve_fraction=reserve))
    site.contract.update(required_device_roles={"role/spine": 2, "role/wan-edge": wan_pairs*2},
                         wan_peak_mbps=wan_peak_mbps, wan_pairs=wan_pairs, wan_usable_mbps=float(wan_pairs*wan_pair_budget),
                         reserve_fraction=reserve)
    site.contract["assumptions"].extend(assumptions)
    if include_equipment:
        equipment.enrich_site(site)
    else:
        equipment.enrich_site(site, demonstrations=False)
    site.management(pods["wan-01"])
    site.power()
