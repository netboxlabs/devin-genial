"""Independent IPv6 obligations from recipe, reservations and actual IPv4 graph.

No emitter helpers, metadata or contracts establish completeness here. Existing
profile validators still establish the IPv4 and physical obligations; this pass
requires their requested IPv6 companions, including every gateway and listener.
"""

from collections import defaultdict
from ipaddress import IPv6Network, IPv6Address, ip_interface, ip_network


def validate(plan):
    recipe = plan.get("recipe", {})
    if recipe.get("profile") not in {"regional-bank", "enterprise-data-center", "school-district",
                                      "hospital-clinics", "provider-backbone"}:
        return []
    objects = {o["key"]: o for o in plan.get("objects", [])}
    findings = []

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    def attrs(key):
        return objects.get(key, {}).get("attrs", {})

    def refs(key):
        return objects.get(key, {}).get("refs", {})

    def kind(key):
        return objects.get(key, {}).get("kind")

    networks, addresses, six = {}, {}, set()
    for key, obj in objects.items():
        if key.startswith("ipv6/"):
            six.add(key)
        try:
            if obj["kind"] in {"prefix", "aggregate"}:
                value = ip_network(attrs(key).get("prefix"))
                networks[key] = value
            elif obj["kind"] == "ip_address":
                value = ip_interface(attrs(key).get("address"))
                addresses[key] = value
            else:
                continue
            if value.version == 6:
                six.add(key)
        except (ValueError, TypeError):
            pass  # The common validator diagnoses malformed address text.
    ledgers = plan.get("reservations", {})
    if not isinstance(ledgers, dict):
        report("ipv6-reservation", "plan", "Reservations must be a scope-to-slot mapping.")
        return findings
    if "ipv6_pool" not in recipe:
        if six or any(scope.startswith("ipv6-") for scope in ledgers):
            report("ipv6-unrequested", "plan", "IPv6 inventory/reservations require an explicit ipv6_pool baseline.")
        for key, obj in objects.items():
            if obj["kind"] in {"device", "virtual_machine"} and "primary_ip6" in refs(key):
                report("ipv6-unrequested", key, "An IPv4-only recipe cannot assign a primary IPv6 address.")
        return findings
    try:
        raw = recipe["ipv6_pool"]
        pool = ip_network(raw) if isinstance(raw, str) else None
        if (pool is None or pool.version != 6 or not 32 <= pool.prefixlen <= 40 or
                not any(pool.subnet_of(IPv6Network(n)) for n in ("2001:db8::/32", "3fff::/20"))):
            raise ValueError("unsupported pool")
        if str(pool) != raw:
            report("ipv6-pool", "plan", "Frozen ipv6_pool must use canonical compressed network text.")
    except (ValueError, TypeError):
        report("ipv6-pool", "plan", "ipv6_pool requires aligned documentation IPv6 space /32 through /40.")
        return findings

    sites = {k for k, o in objects.items() if o["kind"] == "site"}
    scopes = {"ipv6-sites": (1 << (48-pool.prefixlen))-1,
              "ipv6-routed-links": 1000000, "ipv6-loopbacks": 1000000}
    scopes.update({f"ipv6-segments/{site}": 65536 for site in sites})
    for scope, slots in ledgers.items():
        if not scope.startswith("ipv6-"):
            continue
        if scope not in scopes or not isinstance(slots, dict):
            report("ipv6-reservation", scope, "Unknown IPv6 reservation scope or invalid slot mapping.")
            continue
        values = list(slots.values())
        if (any(not isinstance(key, str) for key in slots) or
                any(type(v) is not int or not 0 <= v < scopes[scope] for v in values) or
                len(set(v for v in values if type(v) is int)) != len(values)):
            report("ipv6-reservation", scope, "IPv6 slots must be unique nonnegative integers within the reserved pool.")

    def slot(scope, key):
        mapping = ledgers.get(scope, {})
        value = mapping.get(key) if isinstance(mapping, dict) else None
        if type(value) is not int or not 0 <= value < scopes[scope]:
            report("ipv6-reservation", key, f"Missing or out-of-range permanent reservation in {scope}.")
            return None
        return value

    site_nets = {}
    for site in sorted(sites):
        value = slot("ipv6-sites", site)
        if value is not None:
            site_nets[site] = IPv6Network((int(pool.network_address)+(value << 80), 48))
    infra = int(pool.network_address)+(((1 << (48-pool.prefixlen))-1) << 80)
    # Store expected identity, subnet and ownership; compare to actual rows below.
    expected = {"ipv6/rir", "ipv6/aggregate"}
    expected_prefixes, companions, ipv4_prefixes = {}, {}, {}
    ns = recipe.get("namespace")
    if (kind("ipv6/rir") != "rir" or attrs("ipv6/rir") !=
            dict(name=f"{ns} IPv6 documentation registry", slug=f"{ns}-ipv6-docs",
                 is_private=False, description="Documentation address registry") or refs("ipv6/rir")):
        report("ipv6-registry", "ipv6/rir", "IPv6 allocation requires the estate's documentation registry identity.")
    if (kind("ipv6/aggregate") != "aggregate" or attrs("ipv6/aggregate").get("prefix") != str(pool) or
            refs("ipv6/aggregate") != {"rir": "ipv6/rir"}):
        report("ipv6-aggregate", "ipv6/aggregate", "IPv6 aggregate must match the requested pool and documentation registry.")

    def require_prefix(key, net, rel, status):
        expected.add(key)
        wanted = (net, rel, status)
        if key in expected_prefixes and expected_prefixes[key] != wanted:
            report("ipv6-prefix-scope", key, "One reservation cannot have conflicting tenant, site or VRF ownership.")
        expected_prefixes[key] = wanted

    provider = recipe["profile"] == "provider-backbone"
    bank = recipe["profile"] == "regional-bank"
    radio_pairs = {frozenset(refs(k).get(side) for side in ("interface_a", "interface_b"))
                   for k, o in objects.items() if o["kind"] == "wireless_link"}
    ipv4_owners = defaultdict(list)
    for key, address in addresses.items():
        if address.version == 4 and refs(key).get("assigned_object"):
            ipv4_owners[(refs(key).get("vrf"), address.network)].append(refs(key)["assigned_object"])
    # The provider validator independently proves these ledger identities and
    # their actual physical paths from recipe demand. A prefix name alone is
    # not sufficient to enroll an arbitrary secondary VM address as a /127.
    source_links = ledgers.get("provider-link-prefixes", {})
    source_loops = ledgers.get("provider-loopbacks", {})
    routed_prefixes = {f"prefix/link/{k}" for k in source_links} if provider and isinstance(source_links, dict) else set()
    loop_prefixes = {f"prefix/loopback/{k}" for k in source_loops} if provider and isinstance(source_loops, dict) else set()
    for key, net4 in networks.items():
        if kind(key) != "prefix" or net4.version != 4 or attrs(key).get("status") == "container":
            continue
        rel = refs(key)
        site, vrf = rel.get("scope_site"), rel.get("vrf")
        tenant = rel.get("tenant")
        if kind(vrf) != "vrf" or kind(tenant) != "tenant":
            report("ipv6-prefix-scope", key, "Dual-stack prefixes require real VRF and tenant ownership.")
        net6, purpose = None, None
        if ((kind(rel.get("vlan")) == "vlan" and site in sites) or
                (bank and site in sites and not rel.get("vlan") and net4.prefixlen == 31)):
            purpose = "segment" if rel.get("vlan") else "radio"
            if purpose == "radio" and (len(ipv4_owners[(vrf, net4)]) != 2 or
                    frozenset(ipv4_owners[(vrf, net4)]) not in radio_pairs):
                report("ipv6-policy", key, "Diagnostic /64 requires the two addressed endpoints of an actual wireless link.")
            n = slot(f"ipv6-segments/{site}", key)
            if n is not None and site in site_nets:
                net6 = IPv6Network((int(site_nets[site].network_address)+(n << 64), 64))
                require_prefix(f"ipv6/reservation/{site}/{vrf}", site_nets[site],
                               {"vrf": vrf, "tenant": tenant, "scope_site": site}, "container")
        elif net4.prefixlen == 31 and not site and not rel.get("vlan") and (key in routed_prefixes or bank and key == "prefix/recovery"):
            purpose = "routed"
            n = slot("ipv6-routed-links", key)
            if n is not None:
                net6 = IPv6Network((infra+2*(n+1), 127))
                require_prefix(f"ipv6/infrastructure/{vrf}/routed", IPv6Network((infra, 64)),
                               {"vrf": vrf, "tenant": tenant}, "container")
        elif key in loop_prefixes and net4.prefixlen == 32 and not site and not rel.get("vlan"):
            purpose = "loopback"
            n = slot("ipv6-loopbacks", key)
            if n is not None:
                net6 = IPv6Network((infra+(1 << 64)+n+1, 128))
                require_prefix(f"ipv6/infrastructure/{vrf}/loopbacks", IPv6Network((infra+(1 << 64), 64)),
                               {"vrf": vrf, "tenant": tenant}, "container")
        else:
            report("ipv6-policy", key, "IPv4 leaf has no reviewed IPv6 segment or routed-link policy.")
        identity = (vrf, net4)
        if identity in ipv4_prefixes:
            report("ipv6-policy", key, "IPv4 leaf identity is ambiguous; one companion cannot resolve duplicate segments.")
        ipv4_prefixes[identity] = (net6, purpose, rel)
        if net6 is not None:
            require_prefix(f"ipv6/{key}", net6, dict(rel), attrs(key).get("status"))

    for key, (net, rel, status) in expected_prefixes.items():
        if (kind(key) != "prefix" or attrs(key).get("prefix") != str(net) or
                attrs(key).get("status") != status or refs(key) != rel):
            report("ipv6-prefix", key, "Missing or incorrect reserved IPv6 prefix, status, VLAN, site, VRF or tenant.")

    for key, value in addresses.items():
        if value.version != 4:
            continue
        rel = refs(key)
        owner = rel.get("assigned_object")
        if not owner:
            continue  # Unassigned inventory is explicitly outside this interface-address pass.
        if kind(owner) == "fhrp_group":
            continue  # Existing FHRP is explicitly IPv4-only, including its VIP.
        if kind(owner) not in {"interface", "vm_interface"}:
            report("ipv6-policy", key, "Address has no reviewed interface-owner policy for an IPv6 companion.")
            continue
        net6, purpose, segment_refs = ipv4_prefixes.get((rel.get("vrf"), value.network), (None, None, {}))
        if net6 is None:
            report("ipv6-address", key, "Address needs an unambiguous reserved IPv6 companion segment.")
            continue
        if (rel.get("tenant") != segment_refs.get("tenant") or
                kind(owner) == "vm_interface" and rel.get("tenant") != refs(refs(owner).get("virtual_machine")).get("tenant")):
            report("ipv6-address-owner", key, "IPv4 and IPv6 address tenants must agree with the actual segment and VM owner.")
        offset = int(value.ip)-int(value.network.network_address)+(1 if purpose == "radio" else 0)
        if purpose in {"segment", "radio"} and not 0 < offset < (1 << 64)-128:
            report("ipv6-address", key, "LAN host ordinals must avoid reserved IPv6 interface identifiers.")
        if purpose == "radio" and not str(attrs(owner).get("type", "")).startswith("ieee802.11"):
            report("ipv6-policy", owner, "The diagnostic /64 policy is only for the actual addressed radios.")
        if purpose == "routed" and provider and (kind(owner) != "interface" or
                attrs(owner).get("type") in (None, "virtual", "bridge", "lag") or
                refs(refs(owner).get("device")).get("role") not in {
                    "role/provider-edge", "role/customer-edge", "role/management", "role/wan-edge"}):
            report("ipv6-policy", owner, "Provider /127 policy requires an actual reserved routed physical interface.")
        if purpose == "loopback" and (attrs(owner).get("name") != "lo0" or attrs(owner).get("type") != "virtual" or
                refs(refs(owner).get("device")).get("role") != "role/provider-edge" or
                refs(refs(owner).get("device")).get("primary_ip4") != key or
                f"prefix/loopback/{refs(owner).get('device')}" not in loop_prefixes):
            report("ipv6-policy", owner, "Provider /128 policy requires the PE's actual virtual lo0 interface.")
        new_key = f"ipv6/{key}"
        expected.add(new_key)
        companions[key] = new_key
        wanted = dict(attrs(key), address=f"{IPv6Address(int(net6.network_address)+offset)}/{net6.prefixlen}")
        if kind(new_key) != "ip_address" or attrs(new_key) != wanted or refs(new_key) != rel:
            report("ipv6-address", new_key, "Missing or incorrect IPv6 address, host ordinal, owner, status, DNS or VRF/tenant.")

    for key, obj in objects.items():
        if obj["kind"] in {"device", "virtual_machine"}:
            required = companions.get(refs(key).get("primary_ip4"))
            if refs(key).get("primary_ip6") != required:
                report("ipv6-primary", key, "primary_ip6 must be the IPv6 companion of this owner's primary IPv4 interface.")
        elif obj["kind"] == "service":
            assigned = refs(key).get("ipaddresses", [])
            if not isinstance(assigned, list):
                report("ipv6-service", key, "Service listening addresses must be an ordered address list.")
                continue
            original = [a for a in assigned if a in addresses and addresses[a].version == 4]
            wanted = original+[companions[a] for a in original if a in companions]
            if not original or assigned != wanted:
                report("ipv6-service", key, "A dual-stack service must retain its IPv4 listeners and their exact IPv6 companions.")
    for key in sorted(six-expected):
        report("ipv6-unexpected", key, "IPv6 record has no requested segment, host or registry obligation; no remote owners or IPv6 FHRP are invented.")
    return findings
