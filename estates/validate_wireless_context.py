"""Independent WLAN dependency, tenant, support and free-address obligations."""

from bisect import bisect_left
from collections import defaultdict
from functools import cache
from ipaddress import ip_interface, ip_network


def validate(plan):
    # Base validation reports malformed records. Optional attrs/refs remain empty
    # mappings, while valid WLAN records retain their independent obligations.
    objects = {obj["key"]: obj for obj in plan.get("objects", [])
               if isinstance(obj, dict) and isinstance(obj.get("key"), str) and isinstance(obj.get("kind"), str)
               and all(isinstance(obj.get(field, {}), dict) for field in ("attrs", "refs", "meta"))}
    kinds, children, assignments = defaultdict(list), defaultdict(list), defaultdict(list)
    networks, addresses, allocated = {}, {}, defaultdict(list)
    prefixes = defaultdict(list)
    findings = []

    def fail(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None

    recipe = plan.get("recipe", {})
    guest_demand = {}
    demand_fields = {"school-district": (("schools", "school"),),
                     "hospital-clinics": (("hospitals", "hospital"), ("clinics", "clinic"))}.get(recipe.get("profile"), ())
    for field, prefix in demand_fields:
        facilities = recipe.get(field, [])
        for facility in facilities if isinstance(facilities, list) else []:
            if not isinstance(facility, dict) or not isinstance(facility.get("key"), str):
                continue
            zones = facility.get("wireless", {})
            if isinstance(zones, dict):
                guest_demand[f"wireless-lan/{prefix}-{facility['key']}/guest"] = sum(
                    zone["guest"] for zone in zones.values() if isinstance(zone, dict) and type(zone.get("guest")) is int and zone["guest"] > 0)

    for key, obj in objects.items():
        kinds[obj["kind"]].append(key)
        for field, value in refs(key).items():
            for target in value if isinstance(value, list) else [value]:
                if isinstance(target, str):
                    children[(field, target)].append(key)
        if obj["kind"] == "contact_assignment" and refs(key).get("role") == "contact-role/operations" and attrs(key).get("priority") == "primary":
            assignments[refs(key).get("object")].append(refs(key).get("contact"))
        try:
            if obj["kind"] == "prefix":
                networks[key] = ip_network(attrs(key).get("prefix"))
                if attrs(key).get("status") == "active":
                    prefixes[(refs(key).get("vlan"), refs(key).get("scope_site"), refs(key).get("tenant"), networks[key].version)].append(key)
            elif obj["kind"] == "ip_address":
                addresses[key] = ip_interface(attrs(key).get("address"))
                if addresses[key].version == 4:
                    allocated[refs(key).get("vrf")].append((int(addresses[key].ip), int(addresses[key].ip)))
            elif obj["kind"] == "ip_range":
                first, last = (ip_interface(attrs(key).get(field)) for field in ("start_address", "end_address"))
                if first.version == last.version == 4 and first.ip <= last.ip:
                    allocated[refs(key).get("vrf")].append((int(first.ip), int(last.ip)))
        except (ValueError, TypeError):
            pass  # Base address checks report malformed inventory independently.

    spans, ends = {}, {}
    for vrf, used in allocated.items():
        merged = []
        for lower, upper in sorted(used):
            if merged and lower <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], upper)
            else:
                merged.append([lower, upper])
        spans[vrf], ends[vrf] = merged, [upper for _, upper in merged]

    signatures = {"dns_tcp": ("tcp", [53]), "dns_udp": ("udp", [53]), "radius_udp": ("udp", [1812, 1813])}

    @cache
    def valid_service(key, tenant, family):
        protocol, ports = signatures[family]
        vm = refs(key).get("virtual_machine")
        host, cluster = refs(vm).get("device"), refs(vm).get("cluster")
        site = refs(host).get("site")
        if (kind(key) != "service" or attrs(key).get("protocol") != protocol or attrs(key).get("ports") != ports or refs(key).get("device") or
                kind(vm) != "virtual_machine" or kind(host) != "device" or kind(cluster) != "cluster" or kind(site) != "site" or
                any(attrs(node).get("status") != "active" or refs(node).get("tenant") != tenant for node in (vm, host, cluster, site)) or
                refs(host).get("role") != "role/server" or refs(host).get("cluster") != cluster or refs(cluster).get("scope_site") != site):
            return False
        expected = [refs(vm).get("primary_ip4")]
        if plan.get("recipe", {}).get("ipv6_pool"):
            expected.append(refs(vm).get("primary_ip6"))
        if refs(key).get("ipaddresses") != expected:
            return False
        for version, address in zip((4, 6), expected):
            interface = refs(address).get("assigned_object")
            vlan, vrf = refs(interface).get("untagged_vlan"), refs(address).get("vrf")
            local_prefixes = prefixes[(vlan, site, tenant, version)]
            if (address not in addresses or addresses[address].version != version or attrs(address).get("status") != "active" or
                    refs(address).get("tenant") != tenant or kind(interface) != "vm_interface" or not attrs(interface).get("enabled") or
                    refs(interface).get("virtual_machine") != vm or kind(vlan) != "vlan" or refs(vlan).get("site") != site or
                    refs(vlan).get("tenant") != tenant or kind(vrf) != "vrf" or refs(interface).get("vrf") != vrf or
                    len(local_prefixes) != 1 or refs(local_prefixes[0]).get("vrf") != vrf or addresses[address].network != networks[local_prefixes[0]]):
                return False
        return True

    @cache
    def required_services(tenant, family):
        candidates = sorted(key for key in kinds["service"] if valid_service(key, tenant, family))
        by_site = {}
        for key in candidates:
            site = refs(refs(key).get("virtual_machine")).get("device")
            by_site.setdefault(refs(site).get("site"), key)
        first = list(by_site.values())
        return (first + [key for key in candidates if key not in first])[:2]

    entries = defaultdict(list)
    fields = {"wlan", "prefix", "technical_contact", "available_ipv4", "dns_tcp", "dns_udp", "radius_udp", "external_required"}
    for contract in plan.get("contracts", []):
        values = contract.get("wireless_services", [])
        if not isinstance(values, list):
            fail("wireless-context-contract", contract.get("site", "plan"), "Wireless service context must be a list of actual WLAN dependency entries.")
            continue
        for entry in values:
            if not isinstance(entry, dict) or set(entry) != fields or not isinstance(entry.get("wlan"), str):
                fail("wireless-context-contract", contract.get("site", "plan"), "Wireless service context has an invalid or incomplete entry.")
                continue
            entries[entry["wlan"]].append((contract.get("site"), entry))
            if kind(entry["wlan"]) != "wireless_lan":
                fail("wireless-context-contract", entry["wlan"], "Dependency context refers to no actual WLAN.")
    for wlan in kinds["wireless_lan"]:
        if len(entries[wlan]) != 1:
            fail("wireless-context-contract", wlan, "Every actual WLAN requires exactly one site dependency entry; missing contracts cannot waive it.")
            continue
        owner, entry = entries[wlan][0]
        site, vlan, tenant = (refs(wlan).get(field) for field in ("scope_site", "vlan", "tenant"))
        prefix, contact = entry["prefix"], entry["technical_contact"]
        local_prefixes = prefixes[(vlan, site, tenant, 4)]
        if (owner != site or kind(site) != "site" or refs(site).get("tenant") != tenant or
                kind(vlan) != "vlan" or refs(vlan).get("site") != site or refs(vlan).get("tenant") != tenant or
                len(local_prefixes) != 1 or prefix != local_prefixes[0] or kind(refs(prefix).get("vrf")) != "vrf" or
                refs(refs(prefix).get("vrf")).get("tenant") != tenant):
            fail("wireless-context-prefix", wlan, "WLAN client capacity must use its actual active IPv4 VLAN prefix in the same site, tenant and VRF.")
        else:
            network, vrf = networks[prefix], refs(prefix)["vrf"]
            first = int(network.network_address) + (network.prefixlen < 31)
            last = int(network.broadcast_address) - (network.prefixlen < 31)
            intervals, used = spans.get(vrf, []), 0
            for index in range(bisect_left(ends.get(vrf, []), first), len(intervals)):
                lower, upper = intervals[index]
                if lower > last:
                    break
                used += min(last, upper) - max(first, lower) + 1
            if type(entry["available_ipv4"]) is not int or entry["available_ipv4"] != last-first+1-used:
                fail("wireless-context-capacity", wlan, "Available IPv4 capacity excludes network/broadcast, distinct allocated addresses and held ranges in the actual VRF.")
            if wlan in guest_demand and last-first+1-used < guest_demand[wlan]:
                fail("wireless-context-capacity", wlan, f"Actual free IPv4 capacity cannot serve the site's {guest_demand[wlan]} planned guest clients; an accurate inventory count does not waive demand.")
        aps = {refs(key).get("device") for key in children[("wireless_lans", wlan)] if kind(key) == "interface"}
        if (kind(contact) != "contact" or kind("contact-role/operations") != "contact_role" or
                "contact-group/operations" not in refs(contact).get("groups", []) or not aps or
                any(assignments[target] != [contact] for target in [site, *aps]) or
                any(refs(ap).get("role") != "role/ap" or refs(ap).get("site") != site or refs(ap).get("tenant") != tenant for ap in aps)):
            fail("wireless-context-support", wlan, "WLAN support must match the actual serving APs' and site's existing primary technical desk in their tenant.")
        external = plan.get("recipe", {}).get("profile") == "regional-bank" and tenant == "tenant/inherited"
        managed = attrs(wlan).get("auth_type") == "wpa-enterprise"
        required_external = (["dns", "radius"] if managed else ["dns"]) if external else []
        if entry["external_required"] != required_external:
            fail("wireless-context-external", wlan, "Independent bank tenants require explicitly unknown external dependencies; owned inventory must not be hidden as external.")
        for family in signatures:
            selected = entry[family]
            needed = not external and (family != "radius_udp" or managed)
            wanted = required_services(tenant, family) if needed else []
            if (not isinstance(selected, list) or len(selected) > 2 or any(not isinstance(key, str) for key in selected) or
                    len(selected) != len(set(selected)) or selected != wanted or (needed and not wanted)):
                fail("wireless-context-service", wlan, f"{family} must bind bounded active same-tenant listener inventory and primary addresses, preferring distinct DCs then replicas; open/external intent cannot borrow authentication.")
        comments = attrs(wlan).get("comments", "")
        facts = [attrs(contact).get("name"), attrs(prefix).get("prefix"), attrs(refs(prefix).get("vrf")).get("name")]
        if external:
            facts += [attrs(tenant).get("name"), "unknown"]
        else:
            for family in signatures:
                for service in entry[family] if isinstance(entry[family], list) else []:
                    facts.extend((attrs(service).get("name"), attrs(refs(service).get("virtual_machine")).get("name")))
        if (not isinstance(comments, str) or any(not isinstance(fact, str) or not fact or fact not in comments for fact in facts) or
                "inventory intent" not in comments or "no configuration, reachability, DHCP service or authentication result" not in comments or
                (not managed and "no captive portal, authentication or isolation enforcement" not in comments)):
            fail("wireless-context-comments", wlan, "Native WLAN comments must name actual support, prefix and service inventory with the execution and external-ownership limits.")
    return findings
