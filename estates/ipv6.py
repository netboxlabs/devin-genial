"""Optional IPv6 inventory over the existing, explicitly supported IPv4 graph.

Physical owners and service identities come from the completed estate. Separate
append-only reservations keep IPv6 independent of each profile's IPv4 geometry.
This models address inventory, not router advertisements or forwarding execution.
"""

from collections import defaultdict
from ipaddress import IPv6Network, ip_interface, ip_network

from .model import DesignError


_DOCUMENTATION = (IPv6Network("2001:db8::/32"), IPv6Network("3fff::/20"))
# These finite ceilings exceed the link/loopback count possible within the
# supported two-million-object budget, and exclude reserved high anycast IIDs.
_INFRA_CAPACITY = 1_000_000


def resolve_pool(value):
    """Accept an aligned /32–/40 documentation allocation; return canonical text."""
    message = "ipv6_pool must be an aligned IPv6 documentation network /32 through /40 within 2001:db8::/32 or 3fff::/20"
    if not isinstance(value, str) or not value:
        raise DesignError(message)
    try:
        network = IPv6Network(value, strict=True)
    except (ValueError, TypeError) as exc:
        raise DesignError(message) from exc
    if not 32 <= network.prefixlen <= 40 or not any(network.subnet_of(pool) for pool in _DOCUMENTATION):
        raise DesignError(message)
    return str(network)


def _network(obj, field):
    try:
        parser = ip_interface if field == "address" else ip_network
        return parser(obj["attrs"][field])
    except (KeyError, ValueError, TypeError) as exc:
        raise DesignError(f"IPv6 enrichment: {obj['key']} needs a valid IPv4 {field}") from exc


def enrich(world):
    """Add one IPv6 peer for each supported segment and addressed interface.

Call once after IPv4 networking and before shared MAC/operational enrichment.
FHRP VIPs and unassigned addresses deliberately remain IPv4-only. No address is
created on a spare interface or an unmodeled far end of a circuit.
"""
    if "ipv6_pool" not in world.recipe:
        return
    pool = IPv6Network(resolve_pool(world.recipe["ipv6_pool"]))
    for scope in world.reservations:
        if scope.startswith("ipv6-") and scope not in {
                "ipv6-sites", "ipv6-routed-links", "ipv6-loopbacks"} and not (
                scope.startswith("ipv6-segments/site/") and scope.removeprefix("ipv6-segments/site/")):
            raise DesignError(f"IPv6 enrichment: unknown reservation scope {scope}; use a reviewed allocation policy")
    by_kind = defaultdict(list)
    for obj in world.objects.values():
        by_kind[obj["kind"]].append(obj)
    sites = {obj["key"]: obj for obj in by_kind["site"]}
    provider = world.recipe["profile"] == "provider-backbone"
    bank = world.recipe["profile"] == "regional-bank"
    routed_prefixes = {f"prefix/link/{key}" for key in world.reservations.get("provider-link-prefixes", {})} if provider else set()
    loop_prefixes = {f"prefix/loopback/{key}" for key in world.reservations.get("provider-loopbacks", {})} if provider else set()
    radio_ports = {obj["refs"][side] for obj in by_kind["wireless_link"]
                   for side in ("interface_a", "interface_b")}
    radio_pairs = {frozenset(obj["refs"][side] for side in ("interface_a", "interface_b"))
                   for obj in by_kind["wireless_link"]}

    # Every assigned address has the exact leaf mask emitted by its builder.
    # Index that relationship once rather than searching every prefix per IP.
    prefix_index, segments = {}, {}
    for obj in sorted(by_kind["prefix"], key=lambda item: item["key"]):
        if obj["attrs"].get("status") == "container":
            continue
        network = _network(obj, "prefix")
        if network.version != 4:
            raise DesignError(f"IPv6 enrichment: {obj['key']} is already IPv6; enrich the IPv4 baseline only once")
        key, refs = obj["key"], obj["refs"]
        site, vlan = refs.get("scope_site"), refs.get("vlan")
        if not refs.get("vrf") or not refs.get("tenant"):
            raise DesignError(f"IPv6 enrichment: {key} needs explicit VRF and tenant ownership")
        if site in sites and vlan and world.objects.get(vlan, {}).get("kind") == "vlan":
            policy = "lan"
        elif bank and site in sites and not vlan and key.endswith("/radio-transit") and network.prefixlen == 31:
            policy = "radio"
        elif not site and not vlan and network.prefixlen == 31 and (
                key in routed_prefixes or bank and key == "prefix/recovery"):
            policy = "routed"
        elif not site and not vlan and network.prefixlen == 32 and key in loop_prefixes:
            policy = "loopback"
        else:
            raise DesignError(f"IPv6 enrichment: unsupported segment {key} ({network}); add an explicit reviewed address policy")
        identity = refs["vrf"], network
        if identity in prefix_index:
            raise DesignError(f"IPv6 enrichment: {key} duplicates the IPv4 leaf identity of {prefix_index[identity]}")
        prefix_index[identity] = key
        segments[key] = obj, network, policy

    addresses, radio_owners = [], defaultdict(list)
    for obj in sorted(by_kind["ip_address"], key=lambda item: item["key"]):
        assigned = obj["refs"].get("assigned_object")
        if not assigned:
            continue
        owner = world.objects.get(assigned)
        if owner and owner["kind"] == "fhrp_group":
            continue
        if not owner or owner["kind"] not in {"interface", "vm_interface"}:
            raise DesignError(f"IPv6 enrichment: {obj['key']} has unsupported assigned owner {assigned}; review its address policy")
        address = _network(obj, "address")
        identity = obj["refs"].get("vrf"), address.network
        prefix = prefix_index.get(identity)
        if address.version != 4 or prefix is None:
            raise DesignError(f"IPv6 enrichment: {obj['key']} needs one IPv4 leaf with the same VRF and exact prefix mask")
        base, network, policy = segments[prefix]
        if obj["refs"].get("tenant") != base["refs"]["tenant"]:
            raise DesignError(f"IPv6 enrichment: {obj['key']} tenant differs from its leaf {prefix}")
        if owner["kind"] == "vm_interface" and obj["refs"].get("tenant") != world.obj(owner["refs"]["virtual_machine"])["refs"].get("tenant"):
            raise DesignError(f"IPv6 enrichment: {obj['key']} tenant differs from its actual VM owner")
        offset = int(address.ip) - int(network.network_address)
        if policy == "lan" and offset == 0:
            raise DesignError(f"IPv6 enrichment: {obj['key']} would consume reserved /64 interface identifier zero")
        if policy == "radio" and assigned not in radio_ports:
            raise DesignError(f"IPv6 enrichment: {obj['key']} diagnostic /64 requires an actual wireless-link endpoint")
        if policy == "radio":
            radio_owners[prefix].append(assigned)
        device = world.objects.get(owner["refs"].get("device"), {})
        if policy == "routed" and provider and (owner["kind"] != "interface" or
                owner["attrs"].get("type") in (None, "virtual", "bridge", "lag") or
                device.get("refs", {}).get("role") not in {
                    "role/provider-edge", "role/customer-edge", "role/management", "role/wan-edge"}):
            raise DesignError(f"IPv6 enrichment: {obj['key']} /127 requires an actual provider routed physical interface")
        if policy == "loopback" and (owner["kind"] != "interface" or
                owner["attrs"].get("name") != "lo0" or owner["attrs"].get("type") != "virtual" or
                device.get("key") != prefix.removeprefix("prefix/loopback/") or
                device.get("refs", {}).get("role") != "role/provider-edge" or
                device.get("refs", {}).get("primary_ip4") != obj["key"]):
            raise DesignError(f"IPv6 enrichment: {obj['key']} /128 requires its reserved PE management loopback")
        addresses.append((obj, prefix, offset + (policy == "radio")))

    for key, (_, _, policy) in segments.items():
        if policy == "radio" and (len(radio_owners[key]) != 2 or frozenset(radio_owners[key]) not in radio_pairs):
            raise DesignError(f"IPv6 enrichment: {key} diagnostic /64 requires both actual wireless-link endpoints")

    site_capacity = (1 << (48 - pool.prefixlen)) - 1
    site_networks = {}
    for site in sorted(sites):
        slot = world.reserve("ipv6-sites", site, site_capacity)
        site_networks[site] = int(pool.network_address) + (slot << 80)
    infrastructure = int(pool.network_address) + (site_capacity << 80)
    namespace = world.recipe["namespace"]
    world.add("rir", "ipv6/rir", {
        "name": f"{namespace} IPv6 documentation registry", "slug": f"{namespace}-ipv6-docs",
        "is_private": False, "description": "Documentation address registry"})
    world.add("aggregate", "ipv6/aggregate", {
        "prefix": str(pool), "description": "IPv6 address allocation"}, {"rir": "ipv6/rir"})

    containers, networks = {}, {}
    for key, (obj, _, policy) in segments.items():
        refs = obj["refs"]
        if policy in {"lan", "radio"}:
            site = refs["scope_site"]
            slot = world.reserve(f"ipv6-segments/{site}", key, 1 << 16)
            network = IPv6Network((site_networks[site] + (slot << 64), 64))
            container = f"ipv6/reservation/{site}/{refs['vrf']}"
            container_network = IPv6Network((site_networks[site], 48))
            container_refs = {name: refs[name] for name in ("vrf", "tenant", "scope_site")}
            container_description = "Stable IPv6 site reservation"
            description = (f"IPv6 {world.obj(refs['vlan'])['attrs']['name']} segment" if policy == "lan"
                           else "IPv6 routed diagnostic radio segment; no RF budget claimed")
        else:
            routed = policy == "routed"
            purpose = "routed" if routed else "loopbacks"
            scope = "ipv6-routed-links" if routed else "ipv6-loopbacks"
            slot = world.reserve(scope, key, _INFRA_CAPACITY)
            base = infrastructure + (0 if routed else 1 << 64)
            network = IPv6Network((base + ((slot + 1) * (2 if routed else 1)), 127 if routed else 128))
            container = f"ipv6/infrastructure/{refs['vrf']}/{purpose}"
            container_network = IPv6Network((base, 64))
            container_refs = {name: refs[name] for name in ("vrf", "tenant")}
            container_description = "Routed IPv6 infrastructure reservation" if routed else "IPv6 loopback reservation"
            description = ("IPv6 point-to-point routed attachment; far end may be explicitly unmodeled" if routed
                           else "IPv6 router inband management loopback")
        container_value = str(container_network), container_refs
        if container in containers and containers[container] != container_value:
            raise DesignError(f"IPv6 enrichment: {key} conflicts with actual ownership of {container}")
        if container not in containers:
            world.add("prefix", container, {"prefix": str(container_network), "status": "container",
                      "description": container_description}, container_refs)
            containers[container] = container_value
        attrs = dict(obj["attrs"], prefix=str(network), description=description)
        world.add("prefix", f"ipv6/{key}", attrs, dict(refs))
        networks[key] = network

    companions = {}
    for obj, prefix, offset in addresses:
        network = networks[prefix]
        address = f"{network.network_address + offset}/{network.prefixlen}"
        companions[obj["key"]] = world.add("ip_address", f"ipv6/{obj['key']}",
                dict(obj["attrs"], address=address), dict(obj["refs"]))
    for kind in ("device", "virtual_machine"):
        for obj in by_kind[kind]:
            if companion := companions.get(obj["refs"].get("primary_ip4")):
                obj["refs"]["primary_ip6"] = companion
    for obj in by_kind["service"]:
        existing = obj["refs"].get("ipaddresses", [])
        extra = [companions[key] for key in existing if key in companions and companions[key] not in existing]
        if extra:
            obj["refs"]["ipaddresses"] = list(dict.fromkeys([*existing, *extra]))
