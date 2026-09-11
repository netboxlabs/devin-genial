"""Independent bounded checks for connected IPAM, radio and WAN-service records."""

from bisect import bisect_left
from collections import defaultdict
from hashlib import sha256
import ipaddress
import json
import math
from pathlib import Path
import re


def validate(plan, catalog=None):
    recipe = plan.get("recipe", {})
    bank = recipe.get("profile") == "regional-bank"
    school = recipe.get("profile") == "school-district"
    guest_sites = set()
    fields = (("schools", "school"),) if school else (("hospitals", "hospital"), ("clinics", "clinic")) if recipe.get("profile") == "hospital-clinics" else ()
    for field, prefix in fields:
        entries = recipe.get(field, [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
                continue
            zones = entry.get("wireless", {})
            if isinstance(zones, dict) and any(isinstance(zone, dict) and type(zone.get("guest")) is int and zone["guest"] > 0 for zone in zones.values()):
                guest_sites.add(f"site/{prefix}-{entry['key']}")
    objects = {o["key"]: o for o in plan.get("objects", [])}
    by_kind, children = defaultdict(list), defaultdict(list)
    findings = []
    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))
    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}
    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}
    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None
    def site(key):
        seen = set()
        while key in objects and key not in seen:
            seen.add(key)
            if kind(key) == "site":
                return key
            r = refs(key)
            key = next((r[f] for f in ("site", "scope_site", "device", "virtual_machine", "assigned_object", "interface") if isinstance(r.get(f), str)), None)
        return None
    for key, obj in objects.items():
        by_kind[obj["kind"]].append(key)
        for field, value in obj.get("refs", {}).items():
            for target in value if isinstance(value, list) else [value]:
                if isinstance(target, str):
                    children[(field, target)].append(key)
    # Native generic relations have a finite set of meaningful target kinds here.
    required = {
        "asn": {"rir": {"rir"}}, "asn_range": {"rir": {"rir"}}, "aggregate": {"rir": {"rir"}},
        "ip_range": {"vrf": {"vrf"}, "role": {"role"}}, "vlan_group": {"scope_site": {"site"}},
        "fhrp_group_assignment": {"group": {"fhrp_group"}, "interface": {"interface"}},
        "ike_policy": {"proposals": {"ike_proposal"}}, "ip_sec_policy": {"proposals": {"ip_sec_proposal"}},
        "ip_sec_profile": {"ike_policy": {"ike_policy"}, "ipsec_policy": {"ip_sec_policy"}},
        "tunnel": {"group": {"tunnel_group"}, "ipsec_profile": {"ip_sec_profile"}},
        "tunnel_termination": {"tunnel": {"tunnel"}, "termination": {"interface"}, "outside_ip": {"ip_address"}},
        "l2vpn_termination": {"l2vpn": {"l2vpn"}, "assigned_object": {"interface"}},
        "vlan_translation_rule": {"policy": {"vlan_translation_policy"}},
        "virtual_circuit": {"provider_network": {"provider_network"}, "type": {"virtual_circuit_type"}},
        "virtual_circuit_termination": {"virtual_circuit": {"virtual_circuit"}, "interface": {"interface"}},
        "wireless_lan": {"group": {"wireless_lan_group"}, "scope_site": {"site"}, "vlan": {"vlan"}},
        "wireless_link": {"interface_a": {"interface"}, "interface_b": {"interface"}},
        "mac_address": {"assigned_object": {"interface", "vm_interface"}},
    }
    if bank and "rir/private" in objects:
        families = set(required) | {"rir", "role", "route_target", "fhrp_group", "ike_proposal", "ip_sec_proposal", "tunnel_group", "l2vpn", "vlan_translation_policy", "virtual_circuit_type"}
        if not any(refs(key).get("role") == "role/ap" for key in by_kind["device"]):
            families -= {"wireless_lan", "wireless_link"}
        if not any(key.endswith("/users") and refs(key).get("scope_site") for key in by_kind["prefix"]):
            families.discard("ip_range")
        for typ in sorted(families):
            if not by_kind[typ]:
                report("network-family-missing", typ, "The connected bank blueprint requires this infrastructure family.")
    for typ, fields in required.items():
        for key in by_kind[typ]:
            for field, allowed in fields.items():
                value = refs(key).get(field)
                targets = value if isinstance(value, list) else [value]
                if not targets or any(kind(target) not in allowed for target in targets):
                    report("network-reference", key, f"{field} must resolve to {', '.join(sorted(allowed))}.")
    cables, passive = {}, {}
    for key in by_kind["cable"]:
        a, b = refs(key).get("a"), refs(key).get("b")
        if any(str(attrs(port).get("type", "")).startswith("ieee802.11") for port in (a, b)):
            report("radio-cable", key, "A radio interface cannot terminate a physical cable.")
        if attrs(key).get("status") == "connected":
            cables[a], cables[b] = b, a
    for key in by_kind["front_port"]:
        rear = refs(key).get("rear_port")
        passive[key], passive[rear] = rear, key
    peers = {}
    for start in by_kind["interface"]:
        current, seen = start, set()
        while current in cables and current not in seen:
            seen.add(current)
            current = cables[current]
            if current not in passive:
                peers[start] = current
                break
            current = passive[current]
    addresses, prefixes = {}, defaultdict(list)
    for key in by_kind["ip_address"]:
        try:
            addresses[key] = ipaddress.ip_interface(attrs(key).get("address"))
        except ValueError:
            pass  # The base validator reports malformed addresses.
    for key in by_kind["prefix"]:
        try:
            prefixes[refs(key).get("vrf")].append((key, ipaddress.ip_network(attrs(key).get("prefix"))))
        except ValueError:
            pass
    def port_ips(port):
        # Existing FHRP, gateway, radio and recovery policies remain IPv4.
        # The separate IPv6 policy must establish its own required companions.
        return [addresses[key] for key in children[("assigned_object", port)] if key in addresses and addresses[key].version == 4]
    active_prefixes = defaultdict(set)
    for vrf, nets in prefixes.items():
        for key, net in nets:
            if attrs(key).get("status") == "active":
                active_prefixes[(vrf, net.version)].add(net)
    occupied = defaultdict(list)
    for key, value in addresses.items():
        occupied[(refs(key).get("vrf"), value.version)].append(int(value.ip))
    for values in occupied.values():
        values.sort()
    for key in by_kind["ip_range"]:
        try:
            start = ipaddress.ip_interface(attrs(key).get("start_address"))
            end = ipaddress.ip_interface(attrs(key).get("end_address"))
            if start.version != end.version or int(start.ip) > int(end.ip) or start.network != end.network:
                raise ValueError
            if not any(net in active_prefixes[(refs(key).get("vrf"), start.version)] for net in (start.network.supernet(new_prefix=length) for length in range(start.network.prefixlen+1))):
                report("ip-range-containment", key, "Both range ends must belong to an active allocated prefix in the same VRF.")
            values = occupied[(refs(key).get("vrf"), start.version)]
            index = bisect_left(values, int(start.ip))
            if attrs(key).get("status") == "reserved" and index < len(values) and values[index] <= int(end.ip):
                report("ip-range-occupied", key, "Reserved onboarding range overlaps an allocated address.")
        except (ValueError, TypeError):
            report("ip-range-shape", key, "Range needs ordered endpoints with one address family and subnet.")
    seen_asns = set()
    ranges = [(key, attrs(key).get("start"), attrs(key).get("end")) for key in by_kind["asn_range"]]
    for key, start, end in ranges:
        if type(start) is not int or type(end) is not int or not 4200000000 <= start <= end <= 4294967294:
            report("asn-range", key, "Authored routing allocation must be inside the private 32-bit ASN range.")
    for key in by_kind["asn"]:
        number = attrs(key).get("asn")
        if type(number) is not int or number in seen_asns:
            report("asn-identity", key, "ASN must be a unique integer; target matching is global.")
        if type(number) is not int:
            continue
        seen_asns.add(number)
        if not any(type(start) is int and type(end) is int and type(number) is int and start <= number <= end and refs(r).get("rir") == refs(key).get("rir") for r, start, end in ranges):
            report("asn-allocation", key, "ASN is outside its registry's declared allocation.")
        if not children[("asns", key)]:
            report("asn-unattached", key, "Routing domain needs a real site/provider consumer.")
    aggregate_intervals = []
    for key in by_kind["aggregate"]:
        try:
            net = ipaddress.ip_network(attrs(key).get("prefix"))
            aggregate_intervals.append((net.version, int(net.network_address), int(net.broadcast_address), key))
            if not any(child.version == net.version and child.subnet_of(net) for nets in prefixes.values() for _, child in nets):
                report("aggregate-unused", key, "Address aggregate has no emitted prefix allocations.")
        except (ValueError, TypeError):
            report("aggregate-shape", key, "Aggregate prefix is malformed.")
    previous_version, furthest_end, covering_key = None, -1, None
    for version, start, end, key in sorted(aggregate_intervals):
        if version != previous_version:
            furthest_end = -1
        if start <= furthest_end:
            report("aggregate-overlap", key, f"Aggregate overlaps {covering_key}; RIR does not scope native aggregate overlap constraints.")
        if end > furthest_end:
            furthest_end, covering_key = end, key
        previous_version = version
    for key in by_kind["vlan"]:
        if ((bank and "rir/private" in objects) or refs(key).get("group")) and (kind(refs(key).get("group")) != "vlan_group" or site(refs(key).get("group")) != site(key)):
            report("vlan-group-scope", key, "VLAN must belong to its site's allocation group.")
    for key in by_kind["vrf"]:
        if bank and "rir/private" in objects and (not refs(key).get("import_targets") or set(refs(key).get("import_targets", [])) != set(refs(key).get("export_targets", []))):
            report("route-target-domain", key, "Each authored VRF needs symmetric import/export target intent.")
    for key in by_kind["route_target"]:
        if not re.fullmatch(r"\d+:\d+", str(attrs(key).get("name", ""))) or not (children[("import_targets", key)] and children[("export_targets", key)]):
            report("route-target-attachment", key, "Route target needs an ASN:number identity and both import/export consumers.")
    group_ids = set()
    for key in by_kind["fhrp_group"]:
        number = attrs(key).get("group_id")
        if type(number) is not int or not 1 <= number <= 255 or number in group_ids or attrs(key).get("protocol") != "vrrp3":
            report("fhrp-identity", key, "Authored VRRPv3 group needs unique ID 1–255; preflight target-global ID matching.")
        group_ids.add(number)
        members = children[("group", key)]
        ports = [refs(member).get("interface") for member in members if kind(member) == "fhrp_group_assignment"]
        vlans = {refs(port).get("untagged_vlan") for port in ports}
        vrfs = {refs(port).get("vrf") for port in ports}
        if len(ports) != 2 or len({refs(port).get("device") for port in ports}) != 2 or len({site(port) for port in ports}) != 1 or len(vlans) != 1 or None in vlans or len(vrfs) != 1 or any(not attrs(port).get("enabled") or attrs(port).get("type") != "virtual" or refs(refs(port).get("device")).get("role") not in {"role/leaf", "role/distribution", "role/wan-edge"} for port in ports):
            report("fhrp-members", key, "Shared gateway needs two enabled gateway SVIs on distinct same-site devices in one VLAN/VRF.")
        priorities = [attrs(member).get("priority") for member in members]
        if len(priorities) != 2 or len(set(priorities)) != 2 or any(type(p) is not int or not 1 <= p <= 254 for p in priorities):
            report("fhrp-priority", key, "Gateway priorities must be distinct non-owner values 1–254.")
        vips = [ip for ip in children[("assigned_object", key)] if ip in addresses]
        if len(vips) != 1 or addresses[vips[0]].version != 4 or any(not port_ips(port) for port in ports):
            report("fhrp-address", key, "Both members need IPv4 addresses and the group must retain exactly one IPv4 VIP; IPv6 FHRP is not modeled.")
        elif any(refs(vips[0]).get("vrf") != refs(port).get("vrf") or not any(addresses[vips[0]].network == ip.network and addresses[vips[0]].ip != ip.ip for ip in port_ips(port)) for port in ports):
            report("fhrp-subnet", key, "Virtual IP must be distinct from both member IPs in their shared subnet/VRF.")
    serving_radios = {radio for wlan in by_kind["wireless_lan"] for radio in children[("wireless_lans", wlan)]}
    linked_radios = {refs(link).get(field) for link in by_kind["wireless_link"] for field in ("interface_a", "interface_b")
                     if isinstance(refs(link).get(field), str)}
    if serving_radios or linked_radios:
        if catalog is None:
            try:
                catalog = json.loads((Path(__file__).resolve().parent.parent / "catalog/hardware.json").read_text())
            except (OSError, ValueError) as exc:
                report("wireless-radio-catalog", "catalog", f"Cannot inspect radio hardware catalog: {exc}")
                catalog = {}
        models = catalog.get("models", catalog) if isinstance(catalog, dict) else {}
        identities = {(model.get("manufacturer"), model.get("model")): model for model in models.values()
                      if isinstance(model, dict)} if isinstance(models, dict) else {}
        wlan_channels = {"5g-36-5180-20", "5g-44-5220-20", "5g-157-5785-20"}
        for radio in sorted(serving_radios | linked_radios):
            device = refs(radio).get("device")
            device_type = refs(device).get("device_type")
            manufacturer = refs(device_type).get("manufacturer")
            identity = attrs(manufacturer).get("name"), attrs(device_type).get("model")
            model = identities.get(identity, {}) if all(isinstance(value, str) for value in identity) else {}
            name, channel = attrs(radio).get("name"), attrs(radio).get("rf_channel")
            ports = [port for port in model.get("interfaces", []) if isinstance(port, dict) and port.get("name") == name]
            bands = model.get("radio_bands", {})
            if (kind(radio) != "interface" or kind(device) != "device" or kind(device_type) != "device_type" or
                    kind(manufacturer) != "manufacturer" or len(ports) != 1 or
                    not str(ports[0].get("type", "")).startswith("ieee802.11") or attrs(radio).get("type") != ports[0].get("type") or
                    not isinstance(bands, dict) or not isinstance(name, str) or bands.get(name) != "5g" or
                    not isinstance(channel, str) or (radio in serving_radios and channel not in wlan_channels) or
                    (radio in linked_radios and channel != "5g-149-5745-20")):
                report("wireless-radio-band", radio, "Actual device-type manufacturer/model must provide this 5 GHz radio; WLAN channels are 36/44/157 at 20 MHz and the diagnostic hop uses channel 149 at 20 MHz.")
    wlan_identities = set()
    for key in by_kind["wireless_lan"]:
        identity = (refs(key).get("group"), attrs(key).get("ssid"))
        if identity in wlan_identities:
            report("wireless-lan-identity", key, "Diode matches WLAN by group and SSID; site/VLAN do not prevent a merge.")
        wlan_identities.add(identity)
        vlan = refs(key).get("vlan")
        local = refs(key).get("scope_site")
        guest = local in guest_sites and key == f"wireless-lan/{local.split('/', 1)[1]}/guest"
        auth_ok = (attrs(key).get("auth_type") == "open" and not {"auth_cipher", "auth_psk"} & attrs(key).keys()) if guest else (attrs(key).get("auth_type") == "wpa-enterprise" and attrs(key).get("auth_cipher") == "aes" and "auth_psk" not in attrs(key))
        if (site(vlan) != local or attrs(key).get("status") != "active" or (bank and not str(vlan).endswith("/users")) or
                (guest and vlan != f"vlan/{local.split('/', 1)[1]}/guest") or not auth_ok):
            report("wireless-lan-scope", key, "WLAN needs its local client VLAN and active managed enterprise/AES or requested open guest policy; guest cipher/PSK are omitted.")
        radios = children[("wireless_lans", key)]
        if not radios:
            report("wireless-lan-empty", key, "WLAN has no serving AP radio.")
        for radio in radios:
            device = refs(radio).get("device")
            ethernet = next((port for port in children[("device", device)] if kind(port) == "interface" and attrs(port).get("name") == "eth0"), None)
            peer = peers.get(ethernet)
            members = refs(radio).get("wireless_lans", [])
            if (not str(attrs(radio).get("type", "")).startswith("ieee802.11") or not attrs(radio).get("enabled") or
                    attrs(radio).get("rf_role") != "ap" or site(radio) != site(key) or refs(device).get("role") != "role/ap" or
                    attrs(device).get("status") != "active" or "mode" in attrs(radio) or
                    {"untagged_vlan", "tagged_vlans"} & refs(radio).keys() or
                    not isinstance(members, list) or any(not isinstance(member, str) for member in members) or len(members) != len(set(members))):
                report("wireless-radio", radio, "Serving radio must belong to an active local AP, with distinct WLAN memberships and no interface VLAN or mode fields.")
            if any(not attrs(port).get("enabled") or attrs(port).get("mode") != "tagged" or vlan not in refs(port).get("tagged_vlans", []) or refs(port).get("untagged_vlan") == vlan for port in (ethernet, peer)):
                report("wireless-uplink", radio, "Actual wired AP/access path must carry the tagged client VLAN separately from native AP management.")
    if school:
        # Derive WLAN obligations from the recipe and actual APs, never from a
        # completeness marker or the emitted site contract. Campus capacity and
        # AP counts are checked independently by the school validator.
        entries = plan.get("recipe", {}).get("schools", [])
        school_sites = {f"site/school-{entry['key']}" for entry in (entries if isinstance(entries, list) else [])
                        if isinstance(entry, dict) and isinstance(entry.get("key"), str)}
        clients = (("staff", "wlan0"), ("students", "wlan1"))
        clients_by_site = {local: clients + ((("guest", "wlan0"),) if local in guest_sites else ()) for local in school_sites}
        lans_by_site, aps_by_site, group_sites = defaultdict(list), defaultdict(list), defaultdict(set)
        for key in by_kind["wireless_lan"]:
            lans_by_site[site(key)].append(key)
            group_sites[refs(key).get("group")].add(site(key))
            if site(key) not in school_sites:
                report("school-wireless-site", key, "District WLANs belong only to recipe school sites; the district DC has no campus radios.")
        for key in by_kind["device"]:
            if refs(key).get("role") == "role/ap":
                aps_by_site[site(key)].append(key)
                if site(key) not in school_sites:
                    report("school-wireless-site", key, "Campus AP is outside a requested school site.")
        for key in by_kind["wireless_link"]:
            report("school-wireless-diagnostic", key, "Both school AP radios serve client WLANs; no routed diagnostic radio hop is part of this profile.")
        infrastructure = {"role/access", "role/distribution", "role/wan-edge"}
        adjacency = defaultdict(lambda: defaultdict(set))
        for port, peer in peers.items():
            device, other = refs(port).get("device"), refs(peer).get("device")
            local = site(port)
            if local not in school_sites or refs(device).get("role") not in infrastructure or refs(other).get("role") not in infrastructure:
                continue
            expected = {f"vlan/{local.split('/', 1)[1]}/{role}" for role, _ in clients_by_site[local]}
            healthy = site(peer) == local and all(attrs(endpoint).get("enabled") and attrs(endpoint).get("mode") == "tagged" and expected <= set(refs(endpoint).get("tagged_vlans", [])) for endpoint in (port, peer))
            healthy = healthy and all(attrs(node).get("status") == "active" for node in (device, other))
            if not healthy:
                report("school-wireless-trunk", port, "Every campus forwarding trunk must carry staff, student and requested guest VLANs on enabled interfaces between active local devices.")
            else:
                adjacency[local][device].add(other)
        gateways_by_vlan = defaultdict(set)
        for port in by_kind["interface"]:
            device = refs(port).get("device")
            if attrs(port).get("type") == "virtual" and attrs(port).get("enabled") and port_ips(port) and refs(device).get("role") == "role/distribution" and attrs(device).get("status") == "active":
                gateways_by_vlan[(site(port), refs(port).get("untagged_vlan"))].add(device)
        for local in sorted(school_sites):
            sid = local.split("/", 1)[1]
            lans = lans_by_site[local]
            groups = {refs(key).get("group") for key in lans}
            local_clients = clients_by_site[local]
            expected_lans = {f"wireless-lan/{sid}/{role}" for role, _ in local_clients}
            if (set(lans) != expected_lans or groups != {f"wireless-group/{sid}"} or
                    any(kind(group) != "wireless_lan_group" or refs(group).get("parent") != "wireless-group/campus" for group in groups) or
                    kind("wireless-group/campus") != "wireless_lan_group"):
                report("school-wireless-lans", local, "Each school needs exactly its staff, student and requested guest WLANs in its stable campus child group.")
            if any(group_sites[group] != {local} for group in groups):
                report("school-wireless-group", local, "A school's WLAN group must not also scope another site's WLANs.")
            if not aps_by_site[local]:
                report("school-wireless-aps", local, "Requested school has no AP serving its staff and student WLANs.")
            for role, radio_name in local_clients:
                vlan = f"vlan/{sid}/{role}"
                matching = [key for key in lans if refs(key).get("vlan") == vlan]
                if len(matching) != 1 or attrs(matching[0]).get("ssid") != f"{plan['recipe'].get('namespace', '')}-{role}" or attrs(matching[0]).get("status") != "active":
                    report("school-wireless-lan", local, f"The {role} WLAN requires its local VLAN, expected SSID, and active status.")
                gateways = gateways_by_vlan[(local, vlan)]
                reachable, pending = set(gateways), list(gateways)
                while pending:
                    current = pending.pop()
                    for other in adjacency[local][current] - reachable:
                        reachable.add(other)
                        pending.append(other)
                for device in aps_by_site[local]:
                    radio = next((key for key in children[("device", device)] if kind(key) == "interface" and attrs(key).get("name") == radio_name), None)
                    memberships = sorted(f"wireless-lan/{sid}/{label}" for label, name in local_clients if name == radio_name)
                    if (not radio or refs(radio).get("wireless_lans") != memberships or "mode" in attrs(radio) or
                            {"untagged_vlan", "tagged_vlans"} & refs(radio).keys()):
                        report("school-wireless-radio", device, f"Every school AP needs exact local {radio_name} WLAN memberships without radio VLAN/mode fields.")
                    ethernet = next((key for key in children[("device", device)] if kind(key) == "interface" and attrs(key).get("name") == "eth0"), None)
                    peer = peers.get(ethernet)
                    if attrs(device).get("status") != "active" or any(not attrs(key).get("enabled") or refs(key).get("untagged_vlan") != f"vlan/{sid}/wireless" for key in (ethernet, peer)):
                        report("school-wireless-management", device, "Active AP and enabled wired access path must retain a separate local wireless management VLAN.")
                    if refs(peer).get("device") not in reachable:
                        report("school-wireless-gateway", device, f"The AP needs an active wired {role} path through campus trunks to an addressed distribution gateway.")
    for key in by_kind["wireless_link"]:
        ports = [refs(key).get(field) for field in ("interface_a", "interface_b")]
        if len(set(ports)) != 2 or len({refs(port).get("device") for port in ports}) != 2 or len({site(port) for port in ports}) != 1 or any(not str(attrs(port).get("type", "")).startswith("ieee802.11") or not attrs(port).get("enabled") or refs(port).get("bridge") for port in ports):
            report("wireless-link-ends", key, "Diagnostic hop needs two distinct enabled same-site radio devices, without LAN bridging.")
        if {attrs(port).get("rf_role") for port in ports} != {"ap", "station"} or len({attrs(port).get("rf_channel") for port in ports}) != 1:
            report("wireless-link-channel", key, "Radio peers need AP/station roles on one channel.")
        ips = [port_ips(port) for port in ports]
        if any(len(values) != 1 for values in ips) or (all(ips) and (ips[0][0].network != ips[1][0].network or ips[0][0].ip == ips[1][0].ip or ips[0][0].network.prefixlen != 31)):
            report("wireless-link-address", key, "Diagnostic radios need distinct addresses in one routed /31.")
        points = [objects.get(refs(port).get("device"), {}).get("meta", {}).get("placement", {}).get("position_m") for port in ports]
        length = attrs(key).get("distance")
        if type(length) not in (int, float) or length <= 0 or attrs(key).get("distance_unit") != "m" or (all(points) and length < math.dist(*points)):
            report("wireless-link-distance", key, "Diagnostic link distance must cover actual local endpoint geometry in metres.")
    attached_wan, wan_parents = set(), set()
    for key in by_kind["virtual_circuit_termination"]:
        port, vc = refs(key).get("interface"), refs(key).get("virtual_circuit")
        parent = refs(port).get("parent")
        term = peers.get(parent)
        circuit = refs(term).get("circuit")
        remote = [t for t in children[("circuit", circuit)] if attrs(t).get("term_side") == "Z"]
        network = refs(remote[0]).get("termination") if len(remote) == 1 else None
        expected_role = "hub" if str(site(port)).startswith("site/dc-") else "spoke"
        if plan.get("recipe", {}).get("profile") == "provider-backbone":
            # Provider checks independently resolve both physical circuit ends,
            # customer peer membership and the supporting PE transport graph.
            if attrs(port).get("type") != "virtual" or refs(parent).get("device") != refs(port).get("device") or not attrs(parent).get("enabled") or not attrs(port).get("enabled"):
                report("virtual-wan-path", key, "Virtual service interface needs an enabled physical parent on the same device.")
        elif attrs(port).get("type") != "virtual" or refs(parent).get("device") != refs(port).get("device") or attrs(parent).get("name") != "wan1" or refs(refs(parent).get("device")).get("role") != "role/wan-edge" or not attrs(parent).get("enabled") or kind(term) != "circuit_termination" or network != refs(vc).get("provider_network") or attrs(key).get("role") != expected_role:
            report("virtual-wan-path", key, "Virtual routing attachment must follow its enabled wan1 access circuit into the same provider network with correct hub/spoke role.")
        if port in attached_wan:
            report("virtual-wan-duplicate", key, "An interface can terminate only one virtual circuit.")
        attached_wan.add(port)
        wan_parents.add(parent)
    if bank and "rir/private" in objects:
        for port, peer in peers.items():
            if attrs(port).get("name") == "wan1" and kind(peer) == "circuit_termination" and port not in wan_parents:
                report("virtual-wan-missing", port, "Physical WAN access lacks its virtual routed service attachment.")
    for key in by_kind["tunnel"]:
        terms = [t for t in children[("tunnel", key)] if kind(t) == "tunnel_termination"]
        if len(terms) != 2 or len({site(refs(t).get("termination")) for t in terms}) != 2:
            report("tunnel-ends", key, "DC recovery tunnel needs two terminations at distinct sites.")
        for term in terms:
            port, outside = refs(term).get("termination"), refs(term).get("outside_ip")
            physical = refs(outside).get("assigned_object")
            if outside not in addresses or addresses[outside].version != 4 or attrs(port).get("type") != "virtual" or refs(port).get("parent") != physical or refs(port).get("device") != refs(physical).get("device") or kind(peers.get(physical)) != "circuit_termination" or not port_ips(port):
                report("tunnel-outside-ip", term, "Tunnel outside IP must belong to the actual cabled WAN parent on this device, with a reserved inside address.")
        inside = [port_ips(refs(term).get("termination")) for term in terms]
        if len(inside) == 2 and (any(len(values) != 1 for values in inside) or (inside[0][0].network != inside[1][0].network or inside[0][0].ip == inside[1][0].ip or inside[0][0].network.prefixlen != 31)):
            report("tunnel-inside-subnet", key, "Recovery peers need exactly one distinct IPv4 address each in their shared /31 transport subnet.")
        profile = refs(key).get("ipsec_profile")
        if attrs(key).get("encapsulation") != "ipsec-tunnel" or attrs(profile).get("mode") != "esp":
            report("tunnel-security", key, "Recovery intent must reference its ESP/IPsec tunnel profile.")
    for key in by_kind["ike_proposal"]:
        if attrs(key).get("authentication_method") != "certificates" or attrs(key).get("encryption_algorithm") != "aes-256-cbc" or attrs(key).get("authentication_algorithm") != "hmac-sha256":
            report("ike-proposal", key, "Recovery blueprint requires its explicit certificate/AES-256/SHA-256 proposal.")
    for key in by_kind["ip_sec_proposal"]:
        if attrs(key).get("encryption_algorithm") != "aes-256-cbc" or attrs(key).get("authentication_algorithm") != "hmac-sha256" or attrs(key).get("sa_lifetime_seconds") != 3600:
            report("ipsec-proposal", key, "Recovery ESP proposal must preserve its authored algorithms and lifetime.")
    for key in by_kind["l2vpn"]:
        terms = children[("l2vpn", key)]
        ports = [refs(term).get("assigned_object") for term in terms]
        vids = [attrs(refs(port).get("untagged_vlan")).get("vid") for port in ports]
        if len(ports) != 2 or len({site(port) for port in ports}) != 2 or attrs(key).get("status") != "planned":
            report("recovery-l2vpn", key, "Unconfigured recovery segment must remain planned with two distinct DC attachments.")
        for i, port in enumerate(ports):
            parent = refs(port).get("parent")
            policy = refs(port).get("vlan_translation_policy")
            rules = children[("policy", policy)]
            if not children[("termination", parent)] or len(rules) != 1 or len(vids) != 2 or attrs(rules[0]).get("local_vid") != vids[i] or attrs(rules[0]).get("remote_vid") != vids[1-i] or vids[0] == vids[1]:
                report("recovery-vlan-translation", port, "Recovery LAN must attach to the tunnel and translate its actual local VLAN to the peer VLAN.")
    seen_macs = set()
    for key in by_kind["mac_address"]:
        value = str(attrs(key).get("mac_address", ""))
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", value) or value.lower() in seen_macs or int(value[:2], 16) & 3 != 2:
            report("mac-identity", key, "MAC must be unique, locally administered and unicast.")
        seen_macs.add(value.lower())
    for port in by_kind["interface"] + by_kind["vm_interface"]:
        primary = refs(port).get("primary_mac_address")
        if primary and (kind(primary) != "mac_address" or refs(primary).get("assigned_object") != port):
            report("mac-primary-owner", port, "Primary MAC must be assigned to this exact interface.")
    if "rir/private" in objects:
        for key in by_kind["mac_address"]:
            port = refs(key).get("assigned_object")
            if refs(port).get("primary_mac_address") != key:
                report("mac-primary-missing", port, "Generated interface MAC must also be its visible primary MAC.")
    if plan.get("recipe", {}).get("profile"):
        addressed = {refs(key).get("assigned_object") for key in by_kind["ip_address"]}
        eligible = {key for key in addressed if kind(key) in {"interface", "vm_interface"}
                    and attrs(key).get("type") not in {"virtual", "bridge"}}
        for port in eligible:
            key = f"mac/{port}"
            raw = bytearray(sha256(f"{plan['recipe'].get('namespace')}/{port}/mac".encode()).digest()[:6])
            raw[0] = (raw[0] | 2) & 254
            expected = ":".join(f"{value:02X}" for value in raw)
            if (kind(key) != "mac_address" or refs(key).get("assigned_object") != port
                    or attrs(key).get("mac_address") != expected):
                report("mac-identity", port, "Every eligible addressed interface needs its stable, interface-scoped MAC identity.")
            if refs(port).get("primary_mac_address") != key:
                report("mac-primary-missing", port, "The addressed interface must expose its assigned MAC as primary.")
        for key in by_kind["mac_address"]:
            if refs(key).get("assigned_object") not in eligible:
                report("mac-identity", key, "Generated MAC policy covers addressed physical and VM interfaces, excluding bridge and virtual interfaces.")
    return findings
