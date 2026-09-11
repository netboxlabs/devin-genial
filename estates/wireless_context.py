"""WLAN support and service inventory; no native service relationship is invented."""

from bisect import bisect_left
from collections import defaultdict
from functools import cache
from ipaddress import ip_interface, ip_network

from .model import DesignError


def enrich(world):
    objects = world.objects
    wlans = sorted((obj for obj in objects.values() if obj["kind"] == "wireless_lan"), key=lambda obj: obj["key"])
    if not wlans:
        return
    demand_fields = {"school-district": (("schools", "school"),),
                     "hospital-clinics": (("hospitals", "hospital"), ("clinics", "clinic"))}.get(world.recipe["profile"], ())
    guest_demand = {f"wireless-lan/{prefix}-{facility['key']}/guest": sum(zone["guest"] for zone in facility["wireless"].values())
                    for field, prefix in demand_fields for facility in world.recipe[field]}
    prefixes, contacts, occupied, services = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for obj in objects.values():
        attrs, refs = obj["attrs"], obj["refs"]
        if obj["kind"] == "prefix" and attrs.get("status") == "active":
            network = ip_network(attrs["prefix"])
            if network.version == 4:
                prefixes[(refs.get("vlan"), refs.get("scope_site"), refs.get("tenant"))].append(obj)
        elif obj["kind"] == "contact_assignment" and refs.get("role") == "contact-role/operations" and attrs.get("priority") == "primary":
            contacts[refs["object"]].append(refs["contact"])
        elif obj["kind"] in {"ip_address", "ip_range"}:
            start = ip_interface(attrs["address"] if obj["kind"] == "ip_address" else attrs["start_address"])
            end = start if obj["kind"] == "ip_address" else ip_interface(attrs["end_address"])
            if start.version == end.version == 4:
                occupied[refs.get("vrf")].append((int(start.ip), int(end.ip)))
        elif obj["kind"] == "service":
            family = {("tcp", (53,)): "dns_tcp", ("udp", (53,)): "dns_udp", ("udp", (1812, 1813)): "radius_udp"}.get((attrs.get("protocol"), tuple(attrs.get("ports", []))))
            vm = objects.get(refs.get("virtual_machine"), {})
            host = objects.get(vm.get("refs", {}).get("device"), {})
            if family and vm.get("attrs", {}).get("status") == host.get("attrs", {}).get("status") == "active":
                services[(vm["refs"].get("tenant"), family)].append(obj["key"])
    # Merge once per VRF, then seek only the occupied spans intersecting a LAN.
    # Address ranges consume planned capacity even without concrete client rows.
    spans, ends = {}, {}
    for vrf, intervals in occupied.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        spans[vrf], ends[vrf] = merged, [end for _, end in merged]

    @cache
    def selected(tenant, family):
        candidates = sorted(services[(tenant, family)])
        first, remaining, sites = [], [], set()
        for key in candidates:
            vm = objects[objects[key]["refs"]["virtual_machine"]]
            site = objects[vm["refs"]["device"]]["refs"]["site"]
            if site in sites:
                remaining.append(key)
            else:
                sites.add(site)
                first.append(key)
        return (first + remaining)[:2]

    contracts = {contract["site"]: contract for contract in world.contracts}
    entries = defaultdict(list)
    for wlan in wlans:
        key, refs = wlan["key"], wlan["refs"]
        site, tenant = refs["scope_site"], refs["tenant"]
        candidates = prefixes[(refs["vlan"], site, tenant)]
        if len(candidates) != 1 or len(contacts[site]) != 1 or site not in contracts:
            raise DesignError(f"{key}: WLAN context requires one local IPv4 prefix, site technical contact and site contract")
        prefix, contact = candidates[0], contacts[site][0]
        network, vrf = ip_network(prefix["attrs"]["prefix"]), prefix["refs"]["vrf"]
        low, high = int(network.network_address), int(network.broadcast_address)
        if network.prefixlen < 31:
            low, high = low + 1, high - 1
        used = 0
        intervals = spans.get(vrf, [])
        for index in range(bisect_left(ends.get(vrf, []), low), len(intervals)):
            start, end = intervals[index]
            if start > high:
                break
            used += min(end, high) - max(start, low) + 1
        external = world.recipe["profile"] == "regional-bank" and tenant == "tenant/inherited"
        managed = wlan["attrs"]["auth_type"] == "wpa-enterprise"
        entry = dict(wlan=key, prefix=prefix["key"], technical_contact=contact, available_ipv4=high-low+1-used,
                     dns_tcp=[], dns_udp=[], radius_udp=[], external_required=["dns", "radius"] if external and managed else ["dns"] if external else [])
        if key in guest_demand and entry["available_ipv4"] < guest_demand[key]:
            raise DesignError(f"{key}: planned guest demand {guest_demand[key]} exceeds {entry['available_ipv4']} available IPv4 addresses in {network} after allocations and held ranges; reduce demand or revise the address plan")
        if not external:
            for family in ("dns_tcp", "dns_udp") + (("radius_udp",) if managed else ()):
                entry[family] = list(selected(tenant, family))
                if not entry[family]:
                    raise DesignError(f"{key}: no active {family} listener inventory owned by the WLAN tenant")
        entries[site].append(entry)
        lines = [f"Client segment: {network} / {objects[vrf]['attrs']['name']}.",
                 f"Technical support: {objects[contact]['attrs']['name']}."]
        if external:
            lines.append(f"External DNS{' and authentication' if managed else ''} required from {objects[tenant]['attrs']['name']}; service endpoints are unknown in this inventory.")
        else:
            for family, label in (("dns_tcp", "DNS TCP/53"), ("dns_udp", "DNS UDP/53"), ("radius_udp", "RADIUS UDP/1812,1813")):
                if entry[family]:
                    names = [f"{objects[objects[service]['refs']['virtual_machine']]['attrs']['name']} / {objects[service]['attrs']['name']}" for service in entry[family]]
                    lines.append(f"{label} inventory: {'; '.join(names)}.")
        if not managed:
            lines.append("Open guest access planning; no captive portal, authentication or isolation enforcement is modeled.")
        lines.append("Dependencies are inventory intent; no configuration, reachability, DHCP service or authentication result is asserted.")
        wlan["attrs"]["comments"] = "\n".join(lines)
    for site, values in entries.items():
        contracts[site]["wireless_services"] = values
