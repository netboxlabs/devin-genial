"""Independent profile obligations and shared DC physical-graph checks.

Obligations come from the recipe and pinned hardware, never emitted contracts.
These checks establish inventory paths and placement, not running HA software.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from ipaddress import ip_interface, ip_network
import math
import re

from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics


# Explicit synthetic reference-host and electrical planning policy. Keep these
# independent of allocator constants so changing an allocation cannot bless it.
# Chassis watts exclude the separately derived PoE and installed-optics extras.
HOST = {"vcpus": 64, "memory_mb": 262144, "disk_mb": 8000000}
WATTS = {"access": 120, "leaf": 160, "core": 220, "edge": 40,
         "server": 250, "console-server": 40, "liquid-chassis": 400}
NETWORK_OFFSETS = {"management": 0, "applications": 6, "database": 7,
                   "backup": 8, "wan": 9, "storage": 10}


def validate(plan, catalog, *, poe_watts=None, optics_watts=None):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") != "enterprise-data-center":
        return []
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    count, peak, reserve = (recipe.get(f) for f in ("data_centers", "wan_peak_mbps", "reserve_fraction"))
    workloads = recipe.get("workloads")
    if (type(count) is not int or not 1 <= count <= 8 or type(peak) is not int or not 1 <= peak <= 16000 or
            type(reserve) not in (int, float) or not 0.1 <= reserve <= 0.4 or not math.isfinite(reserve) or
            not isinstance(workloads, list) or not 1 <= len(workloads) <= 16):
        report("dc-recipe", "plan", "DC validation requires bounded sites, WAN demand, reserve and nonempty workloads.")
        return findings
    for demand in workloads:
        if (not isinstance(demand, dict) or not isinstance(demand.get("key"), str) or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", demand["key"]) or
                any(type(demand.get(f)) is not int or not low <= demand[f] <= high for f, low, high in
                    (("groups", 1, 512), ("replicas", 2, 4), ("vcpus", 1, 64),
                     ("memory_mb", 1, 262144), ("disk_mb", 1, 8000000))) or
                not isinstance(demand.get("failure_domain"), str) or
                demand["failure_domain"] not in {"host", "rack"} or not isinstance(demand.get("network"), str) or
                demand["network"] not in {"applications", "database", "backup"} or
                demand.get("criticality") not in ("tier-1", "tier-2") or
                not isinstance(demand.get("listeners"), list) or not demand["listeners"] or
                any(not isinstance(listener, dict) or not isinstance(listener.get("name"), str) or
                    not listener["name"].strip() or not isinstance(listener.get("key"), str) or
                    not re.fullmatch(r"(?:[a-z][a-z0-9-]{0,39})?", listener["key"]) or
                    not isinstance(listener.get("protocol"), str) or listener["protocol"] not in {"tcp", "udp"} or
                    not isinstance(listener.get("ports"), list) or
                    not listener["ports"] or any(type(p) is not int or not 1 <= p <= 65535 for p in listener["ports"])
                    for listener in demand.get("listeners", []))):
            report("dc-recipe", "plan", "Workloads require explicit groups, replicas, failure domains, resources and listeners.")
            return findings
    if len({d["key"] for d in workloads}) != len(workloads):
        report("dc-recipe", "plan", "Workload identities must be unique.")
        return findings
    return validate_resolved(plan, catalog, sites={f"site/dc-{n:02}" for n in range(1, count + 1)},
                             workloads=workloads, peak=peak, reserve=reserve,
                             poe_watts=poe_watts, optics_watts=optics_watts)


def validate_resolved(plan, catalog, *, sites, workloads, peak, reserve, strict_sites=True,
                      network_offsets=NETWORK_OFFSETS, poe_watts=None, optics_watts=None):
    """Inspect shared DC graph against independently resolved profile obligations.

    Adapters must bound their raw recipes and derive complete workloads without
    importing construction policy. School shares these physical checks while
    supplying its own demand-derived services and leaving campus sites intact.
    """
    recipe, findings = plan.get("recipe", {}), []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    try:
        address_pool = ip_network(recipe.get("address_pool", ""))
        allocations = plan.get("allocations", {})
        slots = list(allocations.values())
        provider = recipe.get("profile") == "provider-backbone"
        unit = 256 if provider else 65536
        if (address_pool.version != 4 or address_pool.prefixlen > (12 if provider else 16) or
                any(type(slot) is not int or not 0 <= slot < address_pool.num_addresses // unit for slot in slots) or
                len(set(slots)) != len(slots) or
                (provider and (allocations.get("dc-01") != 0 or
                 any(not 256 <= slot < address_pool.num_addresses // 256 - 256
                     for key, slot in allocations.items() if key != "dc-01")))):
            raise ValueError("invalid site allocation")
    except (ValueError, AttributeError):
        report("dc-address-allocation", "plan", "DC addressing requires distinct /16 site reservations inside the recipe pool.")
        return findings
    usable = Decimal(1) - Decimal(str(reserve))
    rack_diversity = any(d["failure_domain"] == "rack" for d in workloads)
    pairs = max(1, math.ceil(Decimal(peak) / (1000 * usable)))
    objects = {o["key"]: o for o in plan["objects"] if isinstance(o, dict) and isinstance(o.get("key"), str)
               and isinstance(o.get("kind"), str) and all(isinstance(o.get(f, {}), dict) for f in ("attrs", "refs", "meta"))}
    kinds, children, cable_peer, cable_key = defaultdict(list), defaultdict(list), {}, {}
    addresses = {}

    def attrs(key):
        return objects.get(key, {}).get("attrs", {})

    def refs(key):
        return objects.get(key, {}).get("refs", {})

    def meta(key):
        return objects.get(key, {}).get("meta", {})

    def kind(key):
        return objects.get(key, {}).get("kind")

    for key, obj in objects.items():
        kinds[obj["kind"]].append(key)
        for field, value in refs(key).items():
            for target in value if isinstance(value, list) else [value]:
                if isinstance(target, str):
                    children[(field, target)].append(key)
        if obj["kind"] == "ip_address":
            try:
                addresses[key] = ip_interface(attrs(key).get("address", ""))
            except (ValueError, TypeError):
                pass  # The base validator reports malformed addresses.
        if obj["kind"] == "cable":
            a, b = refs(key).get("a"), refs(key).get("b")
            if isinstance(a, str) and isinstance(b, str):
                cable_peer[a], cable_peer[b] = b, a
                cable_key[a] = cable_key[b] = key

    def active_link(port):
        peer = cable_peer.get(port)
        return (bool(peer) and attrs(cable_key.get(port)).get("status") == "connected" and
                all(attrs(p).get("enabled", True) for p in (port, peer)))

    def vlans(port):
        return set(refs(port).get("tagged_vlans", [])) | ({refs(port)["untagged_vlan"]} if refs(port).get("untagged_vlan") else set())

    def active_address(key, version):
        return key in addresses and addresses[key].version == version and attrs(key).get("status") == "active"

    if not sites <= set(kinds["site"]) or (strict_sites and set(kinds["site"]) != sites):
        report("dc-site-inventory", "plan", "Finished sites must match the recipe's DC count exactly.")
    contracts = plan.get("contracts", [])
    if not isinstance(contracts, list):
        contracts = []
    if Counter(c.get("site") for c in contracts if isinstance(c, dict) and c.get("kind") == "dc") != Counter({s: 1 for s in sites}):
        report("dc-contract-inventory", "plan", "Every requested DC needs exactly one explanatory contract.")
    expected_vms = set()
    for site in sorted(sites):
        sid = site.removeprefix("site/")
        if sid not in allocations:
            report("dc-address-allocation", site, "Requested DC has no persistent site reservation.")
        else:
            container = ip_network((int(address_pool.network_address) + allocations[sid] * 65536, 16))
            for network, offset in network_offsets.items():
                prefix = f"prefix/{sid}/{network}"
                wanted = ip_network((int(container.network_address) + offset * 4096, 20))
                if (attrs(prefix).get("prefix") != str(wanted) or attrs(prefix).get("status") != "active" or
                        refs(prefix).get("scope_site") != site or refs(prefix).get("vrf") != f"vrf/{network}" or
                        refs(prefix).get("vlan") != f"vlan/{sid}/{network}" or
                        attrs(f"{prefix}/reservation").get("prefix") != str(container)):
                    report("dc-prefix-policy", prefix, "Role /20 must occupy its fixed offset in this DC's reserved /16, with local VLAN and VRF scope.")
        devices = [key for key in children[("site", site)] if kind(key) == "device"]
        roles = defaultdict(list)
        for device in devices:
            roles[refs(device).get("role")].append(device)
        if attrs(site).get("status") != "active":
            report("dc-site-status", site, "Requested DC must be active.")
        for role, needed in (("spine", 2), ("wan-edge", pairs * 2)):
            if len(roles[f"role/{role}"]) != needed:
                report("dc-role-inventory", site, f"DC design requires {needed} {role} devices.")
        clusters = [key for key in children[("scope_site", site)] if kind(key) == "cluster"]
        if len(clusters) != 1 or any(attrs(c).get("status") != "active" for c in clusters):
            report("dc-cluster-inventory", site, "Each DC needs exactly one active compute cluster.")
        cluster = clusters[0] if len(clusters) == 1 else None
        for demand in workloads:
            name, replicas = demand["key"], demand["replicas"]
            instances = demand["groups"] * replicas
            expected = [f"vm/{sid}/{name}/{n:03}" for n in range(1, instances + 1)]
            expected_vms.update(expected)
            pool = [key for key in roles["role/server"] if meta(key).get("service_pool") == name]
            per_host = min(int(Decimal(capacity) * usable // demand[field]) for field, capacity in HOST.items())
            needed_hosts = math.ceil(demand["groups"] / per_host) * replicas if per_host else None
            if len(pool) != needed_hosts:
                report("dc-host-inventory", site, f"Workload {name} needs {needed_hosts} hosts for replica separation and reserved resources; found {len(pool)}.")
            for ordinal, vm in enumerate(expected):
                if kind(vm) != "virtual_machine":
                    report("dc-workload-inventory", vm, "Recipe requires this service replica.")
                    continue
                host = refs(vm).get("device")
                if (host not in pool or refs(vm).get("cluster") != cluster or refs(host).get("cluster") != cluster or
                        any(attrs(key).get("status") != "active" for key in (vm, host, cluster))):
                    report("dc-workload-placement", vm, "Replica must be active on an active workload host in its site's active cluster.")
                if (meta(vm).get("service") != name or meta(vm).get("criticality") != demand.get("criticality") or
                        meta(vm).get("failure_domain") != demand["failure_domain"] or meta(vm).get("replicas") != replicas or
                        any(type(meta(vm).get(field)) is not int for field in ("replicas", "replica_group", "replica_lane")) or
                        meta(vm).get("replica_group") != ordinal // replicas + 1 or
                        meta(vm).get("replica_lane") != ordinal % replicas):
                    report("dc-workload-identity", vm, "Service, criticality and replica policy metadata must agree with recipe and stable ordinal.")
                for source, field in (("vcpus", "vcpus"), ("memory_mb", "memory"), ("disk_mb", "disk")):
                    if attrs(vm).get(field) != demand[source]:
                        report("dc-workload-resources", vm, f"{field} must match the explicit per-replica demand.")
                address = refs(vm).get("primary_ip4")
                interface = refs(address).get("assigned_object")
                vlan = f"vlan/{sid}/{demand['network']}"
                if (not active_address(address, 4) or
                        kind(interface) != "vm_interface" or refs(interface).get("virtual_machine") != vm or
                        not attrs(interface).get("enabled", True) or refs(interface).get("untagged_vlan") != vlan or
                        refs(address).get("vrf") != f"vrf/{demand['network']}"):
                    report("dc-workload-address", vm, "Primary address must be active on this VM's enabled interface in its required segment and VRF.")
                try:
                    network = ip_network(attrs(f"prefix/{sid}/{demand['network']}").get("prefix", ""))
                    if ip_interface(attrs(address).get("address", "")).network != network:
                        raise ValueError("wrong segment")
                except ValueError:
                    report("dc-workload-prefix", vm, "Replica address must lie in its site's required workload prefix.")
                services = [key for key in children[("virtual_machine", vm)] if kind(key) == "service"]
                actual = Counter((attrs(s).get("name"), attrs(s).get("protocol"), tuple(attrs(s).get("ports", []))) for s in services)
                wanted = Counter((l["name"], l["protocol"], tuple(l["ports"])) for l in demand["listeners"])
                listener_addresses = [address]
                valid_bindings = True
                if recipe.get("ipv6_pool"):
                    address6 = refs(vm).get("primary_ip6")
                    listener_addresses.append(address6)
                    valid_bindings = (active_address(address6, 6) and refs(address6).get("assigned_object") == interface
                                      and refs(address6).get("vrf") == f"vrf/{demand['network']}")
                if actual != wanted or not valid_bindings or any(refs(s).get("ipaddresses") != listener_addresses or refs(s).get("device") for s in services):
                    report("dc-workload-listener", vm, "Each recipe listener must be owned by this replica and bind exactly its requested primary addresses in IPv4, then IPv6 order.")
            for group in range(demand["groups"]):
                group_vms = expected[group * replicas:(group + 1) * replicas]
                hosts = [refs(vm).get("device") for vm in group_vms]
                domains = hosts if demand["failure_domain"] == "host" else [refs(host).get("rack") for host in hosts]
                if None in domains or len(set(domains)) != replicas:
                    report("dc-replica-diversity", group_vms[0], f"All {replicas} replicas in the group require distinct {demand['failure_domain']} failure domains.")

        for host in roles["role/server"]:
            if meta(host).get("hardware") != "server" or meta(host).get("resources") != HOST or refs(host).get("cluster") != cluster:
                report("dc-host-resources", host, "Compute hosts must use the reference server, fixed synthetic resources and local cluster.")
            vms = [key for key in children[("device", host)] if kind(key) == "virtual_machine"]
            if not vms:
                report("dc-host-unused", host, "Dedicated workload host must serve requested replicas.")
            for resource, field in (("vcpus", "vcpus"), ("memory_mb", "memory"), ("disk_mb", "disk")):
                allocated = sum(attrs(vm).get(field, 0) for vm in vms)
                if allocated > Decimal(HOST[resource]) * usable:
                    report("dc-host-capacity", host, f"{resource} demand exceeds the reference host capacity after reserve.")

        # A deleted/edited redundancy contract cannot remove these physical
        # requirements. This profile deliberately uses direct DC fabric links.
        for role, upstream in (("server", "leaf"), ("wan-edge", "leaf"), ("leaf", "spine")):
            for device in roles[f"role/{role}"]:
                peers = set()
                required_vlans = {f"vlan/{sid}/{n}" for n in ("applications", "database", "backup", "storage", "management")}
                if role == "server":
                    demand = next((d for d in workloads if d["key"] == meta(device).get("service_pool")), None)
                    required_vlans = {f"vlan/{sid}/{n}" for n in (demand["network"], "backup", "storage")} if demand else set()
                elif role == "wan-edge":
                    required_vlans.discard(f"vlan/{sid}/storage")
                for port in children[("device", device)]:
                    peer = cable_peer.get(port)
                    parent = refs(peer).get("device")
                    if (kind(port) == "interface" and kind(peer) == "interface" and parent in roles[f"role/{upstream}"] and
                            active_link(port) and all(attrs(d).get("status") == "active" for d in (device, parent)) and
                            required_vlans <= vlans(port) and required_vlans <= vlans(peer)):
                        peers.add(parent)
                if len(peers) != 2:
                    report("dc-uplink-path", device, f"Requires two active, connected {upstream} peers carrying its service VLANs.")
                if rack_diversity and len({refs(p).get("rack") for p in peers}) != 2:
                    report("dc-uplink-racks", device, "Rack-diverse workloads require separate upstream equipment racks.")

        # The finite model terminates each segment at two addressed gateway
        # SVIs. This is routing intent, without claiming a failover protocol.
        management_gateways = set()
        for network in ("applications", "database", "backup", "storage", "management"):
            vlan = f"vlan/{sid}/{network}"
            gateways = set()
            for leaf in roles["role/leaf"]:
                if attrs(leaf).get("status") != "active":
                    continue
                for port in children[("device", leaf)]:
                    if kind(port) != "interface" or attrs(port).get("type") != "virtual" or not attrs(port).get("enabled", True) or vlan not in vlans(port):
                        continue
                    if any(active_address(ip, 4) for ip in children[("assigned_object", port)]):
                        gateways.add(leaf)
            if len(gateways) != 2 or (rack_diversity and len({refs(g).get("rack") for g in gateways}) != 2):
                report("dc-gateway-inventory", vlan, "Each DC segment requires two active IPv4-addressed gateway leaves, in separate racks when requested.")
            if network == "management":
                management_gateways = gateways

        points = {}
        occupied_points = set()
        for rack in children[("site", site)]:
            if kind(rack) != "rack":
                continue
            point = meta(rack).get("position_m")
            if (not isinstance(point, list) or len(point) != 3 or
                    any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in point)):
                report("dc-rack-geometry", rack, "DC racks require finite nonnegative coordinates in metres.")
                continue
            identity = (refs(rack).get("location"), tuple(point))
            if identity in occupied_points:
                report("dc-rack-geometry", rack, "Two racks cannot occupy the same equipment-room coordinate.")
            occupied_points.add(identity)
            points[rack] = point
        for cable in kinds["cable"]:
            a, b = refs(cable).get("a"), refs(cable).get("b")
            racks = [refs(refs(port).get("device")).get("rack") for port in (a, b)]
            if not all(rack in points for rack in racks):
                continue
            distance = sum(abs(x - y) for x, y in zip(*(points[rack] for rack in racks)))
            if attrs(cable).get("length_unit") != "m" or attrs(cable).get("length", 0) < distance + 3:
                report("dc-cable-geometry", cable, "Cable needs the rack-route distance plus 3 m termination slack.")

        for device in devices:
            model = catalog.get(meta(device).get("hardware"), {})
            rack, room = refs(device).get("rack"), refs(device).get("location")
            if (kind(rack) != "rack" or refs(rack).get("site") != site or refs(rack).get("location") != room or
                    meta(room).get("space_type") != "equipment_room" or attrs(rack).get("status") != "active"):
                report("dc-device-rack", device, "DC equipment must occupy an active rack in its local equipment room.")
            management_ports = {p["name"] for p in model.get("interfaces", []) if p.get("mgmt_only")}
            management_vlan = f"vlan/{sid}/management"
            if management_ports:
                address = refs(device).get("primary_ip4")
                primary = refs(address).get("assigned_object")
                valid_primary = (active_address(address, 4) and
                                 refs(address).get("vrf") == "vrf/management" and kind(primary) == "interface" and
                                 refs(primary).get("device") == device and attrs(primary).get("enabled", True) and
                                 attrs(device).get("status") == "active" and vlans(primary) == {management_vlan})
                svi_managed = (attrs(primary).get("type") == "virtual" and
                               (device in management_gateways or device in roles["role/management"]))
                if not valid_primary:
                    report("dc-management-address", device, "Required management address must be active on this device's enabled management interface.")
                if not svi_managed:
                    peer = cable_peer.get(primary)
                    manager = refs(peer).get("device")
                    if (not valid_primary or attrs(primary).get("name") not in management_ports or not active_link(primary) or
                            kind(peer) != "interface" or manager not in roles["role/management"] or
                            attrs(manager).get("status") != "active" or refs(manager).get("location") != room or
                            vlans(peer) != {management_vlan}):
                        report("dc-management-path", device, "Dedicated management requires a connected enabled path to an active local management switch carrying only management VLAN.")
            if device in roles["role/management"]:
                upstreams = set()
                for port in children[("device", device)]:
                    peer = cable_peer.get(port)
                    gateway = refs(peer).get("device")
                    if (kind(port) == "interface" and kind(peer) == "interface" and gateway in management_gateways and
                            active_link(port) and vlans(port) == vlans(peer) == {management_vlan} and
                            attrs(device).get("status") == "active" and refs(gateway).get("location") == room):
                        upstreams.add(gateway)
                if len(upstreams) != 1:
                    report("dc-management-uplink", device, "Management switch requires its single active connected management-VLAN uplink to a local gateway leaf.")
            # Management switches are built after serial allocation and their
            # console ports are explicitly spare; primary equipment is required.
            if device not in roles["role/management"]:
                for specification in model.get("console_ports", []):
                    if specification.get("type") != "rj-45":
                        continue
                    ports = [p for p in children[("device", device)] if kind(p) == "console_port" and attrs(p).get("name") == specification["name"]]
                    port = ports[0] if len(ports) == 1 else None
                    peer = cable_peer.get(port)
                    server = refs(peer).get("device")
                    if (not port or kind(peer) != "console_server_port" or server not in roles["role/console-server"] or
                            not active_link(port) or refs(server).get("location") != room or
                            any(attrs(d).get("status") != "active" for d in (device, server))):
                        report("dc-console-path", device, "Catalog RJ45 console requires a connected path to an active console server in the same equipment room.")

        if provider:
            # The provider validator owns complete two-site NOC circuit, /31,
            # PE and transport obligations. The checks above still cover every
            # normal fabric, service, replica, management and console obligation;
            # shared power checks run below. This is not a recipe skip flag.
            continue
        capacities, circuits, edges = Counter(), Counter(), defaultdict(set)
        for circuit in kinds["circuit"]:
            terms = [key for key in children[("circuit", circuit)] if kind(key) == "circuit_termination"]
            local = [key for key in terms if refs(key).get("termination") == site]
            if not local:
                continue
            provider = refs(circuit).get("provider")
            remote = [key for key in terms if kind(refs(key).get("termination")) == "provider_network"]
            circuits[provider] += 1
            port = cable_peer.get(local[0])
            edge = refs(port).get("device")
            if (len(terms) != 2 or len(local) != 1 or len(remote) != 1 or
                    provider not in {"provider/a", "provider/b"} or
                    refs(refs(remote[0]).get("termination")).get("provider") != provider or
                    attrs(local[0]).get("term_side") != "A" or attrs(remote[0]).get("term_side") != "Z" or
                    attrs(circuit).get("status") != "active" or attrs(circuit).get("commit_rate") != 1000000 or
                    any(attrs(term).get("port_speed") != 1000000 for term in terms) or not active_link(local[0]) or
                    kind(port) != "interface" or attrs(port).get("type") != "1000base-t" or
                    (attrs(port).get("speed") or 1000000) != 1000000 or attrs(edge).get("status") != "active" or
                    edge not in roles["role/wan-edge"]):
                report("dc-wan-path", circuit, "WAN needs an active 1 Gbps commitment and connected 1 Gbps local edge handoff to its own provider network.")
                continue
            capacities[provider] += 1000
            edges[provider].add(edge)
        for provider in ("provider/a", "provider/b"):
            if circuits[provider] != pairs or len(edges[provider]) != pairs:
                report("dc-wan-inventory", site, f"{provider} requires {pairs} separate edge/circuit attachments.")
            if Decimal(capacities[provider]) * usable < peak:
                report("dc-wan-capacity", site, f"{provider} connected capacity after reserve does not cover {peak} Mbps.")
        if edges["provider/a"] & edges["provider/b"]:
            report("dc-wan-diversity", site, "Carrier A and B attachments must use distinct edge devices.")
        if rack_diversity and ({refs(edge).get("rack") for edge in edges["provider/a"]} &
                               {refs(edge).get("rack") for edge in edges["provider/b"]}):
            report("dc-wan-diversity", site, "Rack-diverse workloads require separate carrier A/B edge racks.")
    if set(kinds["virtual_machine"]) != expected_vms:
        report("dc-workload-inventory", "plan", "VM inventory must exactly match every site's requested workload replicas.")
    findings.extend(validate_power(objects, catalog,
        [key for key in kinds["device"] if refs(key).get("site") in sites], children, cable_peer, cable_key,
        poe_watts=poe_watts, optics_watts=optics_watts))
    return findings


def validate_power(objects, catalog, devices, children, cable_peer, cable_key, *, poe_watts=None, optics_watts=None):
    """Check racked equipment supply paths using the caller's physical indexes.

    Profile adapters select infrastructure independently of contracts; actual
    PD paths and installed optics add separate independently derived allowances.
    """
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    def attrs(key):
        return objects.get(key, {}).get("attrs", {})

    def refs(key):
        return objects.get(key, {}).get("refs", {})

    def kind(key):
        return objects.get(key, {}).get("kind")

    def active_link(port):
        peer = cable_peer.get(port)
        return (bool(peer) and attrs(cable_key.get(port)).get("status") == "connected" and
                all(attrs(p).get("enabled", True) for p in (port, peer)))

    if poe_watts is None:
        _, poe_watts = analyze_poe({"objects": list(objects.values())}, catalog)
    if optics_watts is None:
        _, optics_watts = analyze_optics({"objects": list(objects.values())}, catalog)
    for device in devices:
        device_type = refs(device).get("device_type")
        hardware = device_type.removeprefix("hardware/") if isinstance(device_type, str) else ""
        model = catalog.get(hardware, {})
        rack, room, site = (refs(device).get(field) for field in ("rack", "location", "site"))
        if not model.get("power_ports") or model.get("power_outlets"):
            continue
        ports = [key for key in children[("device", device)] if kind(key) == "power_port"]
        panels, pdus = set(), set()
        allowance = WATTS.get(hardware, 0) + poe_watts.get(device, 0) + optics_watts.get(device, 0)
        quotient, remainder = divmod(allowance, len(model["power_ports"]))
        expected = {spec["name"]: quotient + (n < remainder) for n, spec in enumerate(model["power_ports"])}
        if (len(ports) != len(expected) or {attrs(port).get("name") for port in ports} != set(expected) or
                any(type(attrs(port).get("allocated_draw")) is not int or
                    attrs(port).get("allocated_draw") != expected.get(attrs(port).get("name")) for port in ports) or not allowance):
            report("dc-power-allocation", device, "Normal PSU draws must use the exact catalog-order split of chassis plus independently rounded PoE and installed-optics allowances.")
        for port in ports:
            outlet = cable_peer.get(port)
            pdu, inlet = refs(outlet).get("device"), refs(outlet).get("power_port")
            feed = cable_peer.get(inlet)
            panel = refs(feed).get("power_panel")
            if (kind(outlet) != "power_outlet" or kind(inlet) != "power_port" or refs(inlet).get("device") != pdu or
                    kind(feed) != "power_feed" or kind(panel) != "power_panel" or
                    not active_link(port) or not active_link(inlet) or
                    any(attrs(key).get("status") != "active" for key in (device, pdu, feed)) or
                    any(refs(key).get("rack") != rack for key in (pdu, feed)) or
                    refs(pdu).get("location") != room or refs(panel).get("location") != room or
                    refs(panel).get("site") != site):
                report("dc-power-path", port, "Every supply needs a connected active path through its local PDU/feed to a local panel.")
                continue
            if type(attrs(port).get("maximum_draw")) is not int or attrs(port).get("maximum_draw") != allowance:
                report("dc-power-failover", port, "Every supply must reserve the complete synthetic device allowance.")
            pdus.add(pdu)
            panels.add(panel)
        required = min(2, len(model["power_ports"]))
        if len(pdus) < required or len(panels) < required:
            report("dc-power-diversity", device, f"Installed supplies require {required} distinct PDUs and upstream panels.")
    return findings
