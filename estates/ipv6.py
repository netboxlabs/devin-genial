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
# Provider /31 ledgers: private access/management links, then the public
# PoP-pair, inter-PoP span and transit links.
PROVIDER_LINK_SCOPES = ("provider-link-prefixes", "provider-pair-links", "provider-span-links", "provider-transit-links")
# Provider routing contexts IPv6 never reaches: the out-of-band ISP hands off IPv4 only.
PROVIDER_IPV4_ONLY_VRFS = frozenset({"vrf/oob"})
# Routed /127s take one /64 per routing context: the first context keeps the
# infrastructure block's first /64, loopbacks hold the second, later contexts
# follow. The provider's global table (no VRF) is ledgered as "global".
GLOBAL_CONTEXT = "global"


def routed_block(infrastructure, index):
    """Start of the routed /64 for one permanent routing-context ordinal."""
    return infrastructure + ((0 if index == 0 else index + 1) << 64)


def upstream_block(pool, side):
    """The /64 an upstream assigns its transit /127 from, outside the operator's pool.

    Documentation space only (RFC 3849 / RFC 9637): an operator pool in
    2001:db8::/32 sees its upstreams in 3fff:fff::/32, and the reverse.
    """
    root = IPv6Network("3fff:fff::/32") if pool.subnet_of(_DOCUMENTATION[0]) else _DOCUMENTATION[0]
    return IPv6Network((int(root.network_address) + ((1 + "ab".index(side)) << 80), 64))


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
                "ipv6-sites", "ipv6-routed-links", "ipv6-loopbacks", "ipv6-routed-contexts"} and not (
                scope.startswith("ipv6-segments/site/") and scope.removeprefix("ipv6-segments/site/")):
            raise DesignError(f"IPv6 enrichment: unknown reservation scope {scope}; use a reviewed allocation policy")
    by_kind = defaultdict(list)
    for obj in world.objects.values():
        by_kind[obj["kind"]].append(obj)
    sites = {obj["key"]: obj for obj in by_kind["site"]}
    provider = world.recipe["profile"] == "provider-backbone"
    bank = world.recipe["profile"] == "regional-bank"
    routed_prefixes = {f"prefix/link/{key}" for scope in PROVIDER_LINK_SCOPES
                       for key in world.reservations.get(scope, {})} if provider else set()
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
        if provider and refs.get("vrf") in PROVIDER_IPV4_ONLY_VRFS:
            continue
        # The provider's backbone core is the global table: no VRF, but owned.
        if not (refs.get("vrf") or provider) or not refs.get("tenant"):
            raise DesignError(f"IPv6 enrichment: {key} needs explicit VRF and tenant ownership")
        if site in sites and vlan and world.objects.get(vlan, {}).get("kind") == "vlan":
            policy = "lan"
        elif (provider and site in sites and not vlan and network.prefixlen == 32 and
                key == f"prefix/{site.removeprefix('site/')}/management"):
            policy = "ce-loopback"
        elif bank and site in sites and not vlan and key.endswith("/radio-transit") and network.prefixlen == 31:
            policy = "radio"
        elif not site and not vlan and network.prefixlen == 31 and (
                key in routed_prefixes or bank and key == "prefix/recovery"):
            policy = "routed"
        elif not site and not vlan and network.prefixlen == 32 and key in loop_prefixes:
            policy = "loopback"
        else:
            raise DesignError(f"IPv6 enrichment: unsupported segment {key} ({network}); add an explicit reviewed address policy")
        identity = refs.get("vrf"), network
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
        if provider and obj["refs"].get("vrf") in PROVIDER_IPV4_ONLY_VRFS:
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
        if policy == "ce-loopback" and (owner["kind"] != "interface" or owner["attrs"].get("name") != "Management" or
                owner["attrs"].get("type") != "virtual" or owner["refs"].get("parent") or
                device.get("refs", {}).get("role") != "role/customer-edge"):
            raise DesignError(f"IPv6 enrichment: {obj['key']} /128 requires the CE's own management loopback")
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
    # The provider already holds its public space under its ARIN registry.
    registry = "rir/arin" if provider else "ipv6/rir"
    if not provider:
        world.add("rir", "ipv6/rir", {
            "name": "IPv6 allocations", "slug": f"{namespace}-ipv6-docs",
            "is_private": False, "description": "Global IPv6 address allocations"})
    world.add("aggregate", "ipv6/aggregate", {
        "prefix": str(pool), "description": "IPv6 address allocation"}, {"rir": registry})

    containers, networks = {}, {}
    if provider and any(policy == "routed" and not obj["refs"].get("vrf") for obj, _, policy in segments.values()):
        world.reserve("ipv6-routed-contexts", GLOBAL_CONTEXT, 1 << 16)
    for key, (obj, _, policy) in segments.items():
        refs = obj["refs"]
        transit = provider and key.startswith("prefix/link/circuit/transit/")
        if policy in {"lan", "radio", "ce-loopback"}:
            site = refs["scope_site"]
            slot = world.reserve(f"ipv6-segments/{site}", key, 1 << 16)
            network = (IPv6Network((site_networks[site] + (slot << 64) + 1, 128)) if policy == "ce-loopback"
                       else IPv6Network((site_networks[site] + (slot << 64), 64)))
            container = f"ipv6/reservation/{site}/{refs['vrf']}"
            container_network = IPv6Network((site_networks[site], 48))
            container_refs = {name: refs[name] for name in ("vrf", "tenant", "scope_site")}
            container_description = f"{world.obj(site)['attrs']['name']} IPv6 site block"
            # The IPv4 leaf already names its purpose and site; its gateway
            # reservation clause is IPv4-only, so only the first clause carries.
            description = (f"{obj['attrs']['description'].split(';')[0][:193]} (IPv6)" if policy != "radio"
                           else "IPv6 routed diagnostic radio segment")
        elif transit:
            # The upstream assigns the /127 from its own space, as for IPv4.
            world.reserve("ipv6-routed-links", key, _INFRA_CAPACITY)
            side = key.rsplit("/", 1)[1]
            container_network = upstream_block(pool, side)
            network = IPv6Network((int(container_network.network_address), 127))
            container = f"ipv6/upstream/transit-{side}"
            container_refs = {}
            container_description = "Upstream-assigned IPv6 transit interconnect"
            description = f"{obj['attrs']['description'].split(';')[0][:193]} (IPv6)"
        else:
            routed = policy == "routed"
            purpose = "routed" if routed else "loopbacks"
            scope = "ipv6-routed-links" if routed else "ipv6-loopbacks"
            slot = world.reserve(scope, key, _INFRA_CAPACITY)
            context = refs.get("vrf") or GLOBAL_CONTEXT
            base = (routed_block(infrastructure, world.reserve("ipv6-routed-contexts", context, 1 << 16)) if routed
                    else infrastructure + (1 << 64))
            network = IPv6Network((base + ((slot + 1) * (2 if routed else 1)), 127 if routed else 128))
            container = f"ipv6/infrastructure/{context}/{purpose}"
            container_network = IPv6Network((base, 64))
            container_refs = {name: refs[name] for name in ("vrf", "tenant") if name in refs}
            container_description = "Routed IPv6 infrastructure reservation" if routed else "IPv6 loopback reservation"
            description = f"{obj['attrs']['description'].split(';')[0][:193]} (IPv6)"
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
