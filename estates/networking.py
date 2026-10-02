"""Connected, authored IPAM, first-hop, wireless and private-WAN service intent.

These records describe design, not router configuration or measured RF/security.
Numeric global identities are deterministic and collision-checked within an estate;
an unknown target still needs identity preflight/readback before coexistence is claimed.
"""

from collections import defaultdict
from hashlib import sha256
import ipaddress
import math

from .model import DesignError
from . import naming
from .naming import IPAM_ROLES, prefix_role, segment_role, titleize


def _site_display(w, sid):
    """The site's authored display name, when that site exists in the graph."""
    return naming.site_display(w, f"site/{sid}")


def _display(w, key, fallback=""):
    """The display name of an object already in the graph, or ``fallback``."""
    return (w.objects.get(key) or {}).get("attrs", {}).get("name") or fallback


def _number(namespace, key, modulus):
    return int.from_bytes(sha256(f"{namespace}/{key}".encode()).digest()[:8], "big") % modulus


def _physical_peers(objects):
    cables, passive = {}, {}
    for obj in objects.values():
        r = obj["refs"]
        if obj["kind"] == "cable" and obj["attrs"].get("status") == "connected":
            cables[r["a"]], cables[r["b"]] = r["b"], r["a"]
        elif obj["kind"] == "front_port":
            passive[obj["key"]], passive[r["rear_port"]] = r["rear_port"], obj["key"]
    result = {}
    for key, obj in objects.items():
        if obj["kind"] != "interface":
            continue
        current, seen = key, set()
        while current in cables and current not in seen:
            seen.add(current)
            current = cables[current]
            if current not in passive:
                result[key] = current
                break
            current = passive[current]
    return result


def enrich(w):
    """Enrich the completed physical estate without changing equipment allocations."""
    ns = w.recipe["namespace"]
    sites = sorted((o for o in w.objects.values() if o["kind"] == "site"), key=lambda o: o["key"])
    registry(w, sites)
    first_hop(w)
    private_wan(w)
    recovery_overlay(w)
    wireless(w, sites)
    from .ipv6 import enrich as ipv6_enrich
    ipv6_enrich(w)
    macs(w)


def ipam_roles(w):
    """Give every prefix, VLAN and IP range its IPAM role (naming.IPAM_ROLES).

    Runs once the address plan is complete, in every profile, and emits only
    the roles the estate actually uses.
    """
    ns = w.recipe["namespace"]
    chosen = {}
    for obj in w.objects.values():
        refs = obj["refs"]
        if obj["kind"] == "prefix":
            chosen[obj["key"]] = prefix_role(obj["key"], obj["attrs"]["prefix"], refs.get("vrf"), refs.get("vlan"))
        elif obj["kind"] == "vlan":
            chosen[obj["key"]] = segment_role(obj["key"].rsplit("/", 1)[-1])
        elif obj["kind"] == "ip_range":
            chosen[obj["key"]] = ("reserved" if obj["attrs"].get("status") == "reserved" else
                                  "dhcp" if obj["key"].endswith("/dhcp") else None)
    for key in [k for k, role in chosen.items() if role is None]:
        # A static-assignment range shares its segment's own role.
        chosen[key] = chosen["prefix/" + key.removeprefix("ip-range/").rsplit("/", 1)[0]]
    used = set(chosen.values())
    for weight, (role, (name, description)) in enumerate(IPAM_ROLES.items(), 1):
        if role in used:
            w.add("role", f"ip-role/{role}", {"name": name, "slug": f"{ns}-{role}", "weight": 100*weight,
                  "description": description})
    for key, role in chosen.items():
        w.obj(key)["refs"]["role"] = f"ip-role/{role}"


# Site.address allocates endpoint hosts upward from .10 (blocks.py), so the
# addresses between the gateways and .10 are held for infrastructure.
ENDPOINT_FLOOR = 10
# Segments whose endpoints take addresses dynamically (IPAM role keys).
DHCP_ROLES = frozenset({"users", "wireless", "guest", "voice"})
# A DHCP scope takes the upper half of a client segment; the lower half from
# .10 is the static range Site.address fills upward. Guest segments have no
# static clients, so their scope starts at .10.
NO_STATIC_ROLES = frozenset({"guest"})


def address_ranges(w):
    """Infrastructure, static, DHCP and headroom ranges inside every VLAN-bound IPv4 LAN.

    A client segment reads gateway, held infrastructure (to .9), static
    assignments from .10, then the DHCP scope and its held headroom to the
    top: no unexplained gap. Boundaries are quarter marks of the prefix and
    the recipe's reserve fraction; a held range is emitted only while no
    assigned address occupies it.
    """
    fraction = float(w.recipe["reserve_fraction"])
    taken = defaultdict(set)
    for obj in w.objects.values():
        if obj["kind"] == "ip_address":
            address = ipaddress.ip_interface(obj["attrs"]["address"])
            if address.version == 4:
                taken[obj["refs"].get("vrf")].add(int(address.ip))
    prefixes = sorted((o for o in w.objects.values() if o["kind"] == "prefix" and o["attrs"].get("status") == "active"
                       and o["refs"].get("vlan") and ipaddress.ip_network(o["attrs"]["prefix"]).version == 4),
                      key=lambda o: o["key"])
    for prefix in prefixes:
        net = ipaddress.ip_network(prefix["attrs"]["prefix"])
        size, base = net.num_addresses, int(net.network_address)
        if size < 64:
            continue
        refs = {name: prefix["refs"][name] for name in ("vrf", "tenant") if name in prefix["refs"]}
        used = {value - base for value in taken[refs.get("vrf")] if base < value < base + size - 1}
        stem = prefix["key"].removeprefix("prefix/")
        purpose = naming.segment_purpose(prefix["refs"]["vlan"].rsplit("/", 1)[-1]).lower()

        def held(key, first, last, description, status="reserved"):
            if last - first < 1 or (status == "reserved" and any(first <= host <= last for host in used)):
                return
            w.add("ip_range", f"ip-range/{stem}/{key}", {"start_address": f"{net[first]}/{net.prefixlen}",
                  "end_address": f"{net[last]}/{net.prefixlen}", "status": status, "mark_populated": False,
                  "description": description}, dict(refs))

        gateways = max((host for host in used if host < ENDPOINT_FLOOR), default=0)
        held("infrastructure", gateways + 1, ENDPOINT_FLOOR - 1, "Held for network infrastructure below the endpoint range")
        role = naming.segment_role(prefix["refs"]["vlan"].rsplit("/", 1)[-1])
        if role in DHCP_ROLES:
            # The scope starts at the first quarter mark (from half) clear of
            # every static assignment, so it never overlaps one.
            # ponytail: growth that pushes static clients past that mark moves
            # the boundary up a quarter; a dense-static segment past 3/4 has
            # no DHCP scope at all.
            top = size - 2
            static_top = max((host for host in used if host >= ENDPOINT_FLOOR), default=0)
            start = (ENDPOINT_FLOOR if role in NO_STATIC_ROLES else
                     next((mark for mark in (size // 2, 3 * size // 4) if mark > static_top), top + 1))
            if start > ENDPOINT_FLOOR:
                held("static", ENDPOINT_FLOOR, min(start - 1, top), f"Static assignments for {purpose}, allocated upward",
                     status="active")
            if start > top:
                continue
            headroom = max(2, math.ceil((top - start + 1) * fraction))
            held("dhcp", start, top - headroom, f"DHCP scope for {purpose}", status="active")
            held("headroom", top - headroom + 1, top,
                 f"Held back from the DHCP scope as {fraction:.0%} growth headroom")


def registry(w, sites):
    ns = w.recipe["namespace"]
    rir = w.add("rir", "rir/private", {"name": "Private registry", "slug": f"{ns}-private",
                "is_private": True, "description": "Local private-address and private-ASN registry"})
    base = 4200000000 + _number(ns, "asn-block", 1000000)*64
    w.add("asn_range", "asn-range/private", {"name": "Routing domains", "slug": f"{ns}-routing",
          "start": base, "end": base+63, "description": "Private 32-bit ASNs for the enterprise routing domains"}, {"rir": rir})
    # NetBox's ASN model carries no name, so the visualization layer labels an
    # AS node from its description. Name the party that actually holds the AS —
    # a carrier AS reading "carrier-a" contradicted the provider named on every
    # other record in the estate.
    holders = {"bank": _display(w, "tenant"), "birch": _display(w, "tenant/inherited"),
               "carrier-a": _display(w, "provider/a"), "carrier-b": _display(w, "provider/b")}
    for i, label in enumerate(("bank", "birch", "carrier-a", "carrier-b")):
        if label == "birch" and not any(o["meta"].get("lineage") == "birch" for o in sites):
            continue
        holder = holders[label] or titleize(label)
        w.add("asn", f"asn/{label}", {"asn": base+i, "description": f"{holder} routing domain"}, {"rir": rir})
    for side in ("a", "b"):
        w.obj(f"provider/{side}")["refs"]["asns"] = [f"asn/carrier-{side}"]
    pools = {w.recipe["address_pool"]}
    if any(o["meta"].get("lineage") == "birch" for o in sites):
        pools.add("172.16.0.0/12")
    for network in ipaddress.collapse_addresses(ipaddress.ip_network(pool) for pool in pools):
        pool = str(network)
        # Aggregates have no name either; the description is the rendered label.
        w.add("aggregate", f"aggregate/{pool}", {"prefix": pool,
              "description": f"{holders['bank'] or w.recipe['name']} private address allocation"}, {"rir": rir})
    for site in sites:
        sid, tenant = site["key"].split("/", 1)[1], site["refs"]["tenant"]
        # Retained routing identity survives acquisition and access refresh.
        site["refs"]["asns"] = ["asn/birch" if site["meta"].get("lineage") == "birch" else "asn/bank"]
        w.add("vlan_group", f"vlan-group/{sid}", {"name": _site_display(w, sid) or titleize(sid), "slug": f"{ns}-{sid}",
              "description": "Site-local VLAN allocation"}, {"scope_site": site["key"], "tenant": tenant})
    for obj in list(w.objects.values()):
        if obj["kind"] == "vlan":
            sid = obj["refs"]["site"].split("/", 1)[1]
            obj["refs"]["group"] = f"vlan-group/{sid}"
    for vrf in sorted((o for o in w.objects.values() if o["kind"] == "vrf"), key=lambda o: o["key"]):
        target = f"route-target/{vrf['key']}"
        number = w.reserve("route-targets", vrf["key"], 65534)+1
        w.add("route_target", target, {"name": f"{base}:{number}", "description": f"Import/export domain for {vrf['attrs']['name']}"})
        vrf["refs"].update(import_targets=[target], export_targets=[target])


def first_hop(w):
    ns = w.recipe["namespace"]
    members = defaultdict(list)
    for obj in w.objects.values():
        if obj["kind"] == "interface" and obj["attrs"].get("type") == "virtual" and obj["refs"].get("untagged_vlan") == "vlan/dc-01/applications":
            members[obj["refs"].get("untagged_vlan")].append(obj["key"])
    used = set()
    for vlan, ports in sorted(members.items()):
        if not vlan or len(ports) != 2:
            raise DesignError(f"{vlan}: first-hop blueprint requires exactly two gateway VLAN interfaces")
        vlan_obj = w.obj(vlan)
        site = vlan_obj["refs"]["site"]
        sid = site.split("/", 1)[1]
        network_role = vlan.rsplit("/", 1)[1]
        prefix = w.obj(f"prefix/{sid}/{network_role}")
        net = ipaddress.ip_network(prefix["attrs"]["prefix"])
        key = f"fhrp/{sid}/{network_role}"
        group_id = 1 + _number(ns, key, 255)
        if group_id in used:
            raise DesignError("FHRP global numeric identity collision; choose a new namespace")
        used.add(group_id)
        w.add("fhrp_group", key, {"name": f"{_site_display(w, sid) or titleize(sid)} {titleize(network_role)} Gateway",
              "protocol": "vrrp3", "group_id": group_id,
              "description": "VRRPv3 virtual gateway with two physical owners"})
        for i, port in enumerate(sorted(ports)):
            w.add("fhrp_group_assignment", f"{key}/member/{i+1}", {"priority": 110-i*10}, {"group": key, "interface": port})
        w.add("ip_address", f"ip/{key}", {"address": f"{net[254]}/{net.prefixlen}", "status": "active", "role": "vip",
              "description": f"Shared gateway for {vlan_obj['attrs']['name']}"},
              {"assigned_object": key, "vrf": prefix["refs"]["vrf"], "tenant": w.obj(site)["refs"]["tenant"]})


def private_wan(w):
    ns = w.recipe["namespace"]
    w.add("virtual_circuit_type", "virtual-circuit-type/private-l3", {"name": "Managed L3 VPN", "slug": f"{ns}-managed-l3", "color": "e65100"})
    for side in ("a", "b"):
        w.add("virtual_circuit", f"virtual-circuit/{side}", {"cid": f"{ns}-private-{side}", "status": "active",
              "description": "Carrier-private routed bank WAN service"},
              {"provider_network": f"carrier/{side}", "type": "virtual-circuit-type/private-l3"})
    peers = _physical_peers(w.objects)
    for port, peer in sorted(peers.items()):
        term = w.obj(peer)
        if term["kind"] != "circuit_termination":
            continue
        circuit = w.obj(term["refs"]["circuit"])
        side = circuit["refs"]["provider"].rsplit("/", 1)[1]
        device = w.obj(w.obj(port)["refs"]["device"])
        role = "hub" if device["refs"]["site"].startswith("site/dc-") else "spoke"
        service = w.add("interface", f"{device['key']}/if/PrivateWAN",
                        {"name": "PrivateWAN", "type": "virtual", "enabled": True, "description": "Managed routed service over physical WAN access"},
                        {"device": device["key"], "parent": port, "vrf": w.obj(port)["refs"]["vrf"]})
        w.add("virtual_circuit_termination", f"virtual-circuit-termination/{port}",
              {"role": role, "description": "Managed routing attachment over this physical access circuit"},
              {"virtual_circuit": f"virtual-circuit/{side}", "interface": service})


def recovery_overlay(w):
    ns = w.recipe["namespace"]
    w.add("ike_proposal", "ike-proposal/recovery", {"name": "Recovery IKE", "authentication_method": "certificates",
          "encryption_algorithm": "aes-256-cbc", "authentication_algorithm": "hmac-sha256", "group": "14", "sa_lifetime": 86400})
    w.add("ike_policy", "ike-policy/recovery", {"name": "Recovery IKEv2", "version": 2}, {"proposals": ["ike-proposal/recovery"]})
    w.add("ip_sec_proposal", "ipsec-proposal/recovery", {"name": "Recovery ESP", "encryption_algorithm": "aes-256-cbc",
          "authentication_algorithm": "hmac-sha256", "sa_lifetime_seconds": 3600})
    w.add("ip_sec_policy", "ipsec-policy/recovery", {"name": "Recovery IPsec", "pfs_group": "14"}, {"proposals": ["ipsec-proposal/recovery"]})
    w.add("ip_sec_profile", "ipsec-profile/recovery", {"name": "Recovery Protection", "mode": "esp"},
          {"ike_policy": "ike-policy/recovery", "ipsec_policy": "ipsec-policy/recovery"})
    w.add("tunnel_group", "tunnel-group/recovery", {"name": "Recovery", "slug": f"{ns}-recovery"})
    w.add("tunnel", "tunnel/recovery", {"name": "DC Recovery", "status": "planned", "encapsulation": "ipsec-tunnel",
          "description": "Planned protected DC recovery transport"},
          {"group": "tunnel-group/recovery", "ipsec_profile": "ipsec-profile/recovery", "tenant": "tenant"})
    w.add("vrf", "vrf/recovery", {"name": "Recovery", "enforce_unique": True}, {"tenant": "tenant"})
    base = w.obj("asn/bank")["attrs"]["asn"]
    target = w.add("route_target", "route-target/recovery", {"name": f"{base}:65535", "description": "Planned DC recovery broadcast domain"})
    w.obj("vrf/recovery")["refs"].update(import_targets=[target], export_targets=[target])
    net = ipaddress.ip_network((int(w.site_network("dc-01").network_address)+15*256, 31))
    w.add("prefix", "prefix/recovery", {"prefix": str(net), "status": "active", "description": "Planned DC overlay transit; addresses reserved for both ends"},
          {"vrf": "vrf/recovery", "tenant": "tenant"})
    w.add("l2vpn", "l2vpn/recovery", {"name": "Recovery Segment", "slug": f"{ns}-recovery-segment",
          "type": "vxlan", "identifier": 1+_number(ns, "recovery-vni", 16777214), "status": "planned",
          "description": "Planned data center recovery segment"},
          {"tenant": "tenant", "import_targets": [target], "export_targets": [target]})
    for i in range(2):
        sid = f"dc-{i+1:02}"
        device = f"device/{sid}/edge-001-a"
        outside = f"{device}/if/wan1"
        port = f"{device}/if/Recovery0"
        w.add("interface", port, {"name": "Recovery0", "type": "virtual", "enabled": True,
              "description": "Reserved routed endpoint for planned IPsec transport"}, {"device": device, "parent": outside, "vrf": "vrf/recovery"})
        w.add("ip_address", f"ip/{port}", {"address": f"{net[i]}/31", "status": "reserved"}, {"assigned_object": port, "vrf": "vrf/recovery", "tenant": "tenant"})
        w.add("tunnel_termination", f"tunnel/recovery/{sid}", {"role": "peer"},
              {"tunnel": "tunnel/recovery", "termination": port, "outside_ip": f"ip/{outside}"})
        label = _site_display(w, sid) or titleize(sid)
        vlan = w.add("vlan", f"vlan/{sid}/recovery", {"name": "Recovery", "vid": 3900+i, "status": "reserved",
                     "description": "Planned recovery segment"},
                     {"site": f"site/{sid}", "group": f"vlan-group/{sid}", "tenant": "tenant"})
        policy = w.add("vlan_translation_policy", f"vlan-translation/{sid}", {"name": f"{label} Recovery", "description": "Planned local-to-peer recovery VLAN translation"})
        w.add("vlan_translation_rule", f"vlan-translation/{sid}/rule", {"local_vid": 3900+i, "remote_vid": 3901-i}, {"policy": policy})
        service = w.add("interface", f"{device}/if/RecoveryLAN", {"name": "RecoveryLAN", "type": "virtual", "enabled": True,
                        "mode": "access", "description": "Planned recovery attachment"},
                        {"device": device, "parent": port, "untagged_vlan": vlan, "vlan_translation_policy": policy})
        w.add("l2vpn_termination", f"l2vpn/recovery/{sid}", {}, {"l2vpn": "l2vpn/recovery", "assigned_object": service})


# Authored non-overlapping channel plans per radio band. These are planning
# assignments, not an RF survey: no coverage, interference or reuse distance is
# claimed. 2.4 GHz offers only three non-overlapping 22 MHz channels, so the
# diagnostic hop there shares one with a WLAN radio instead of adding a fourth.
RADIO_CHANNELS = {"5g": ("5g-36-5180-20", "5g-44-5220-20", "5g-157-5785-20"),
                  "2.4g": ("2.4g-1-2412-22", "2.4g-6-2437-22", "2.4g-11-2462-22")}
DIAGNOSTIC_CHANNELS = {"5g": "5g-149-5745-20", "2.4g": "2.4g-11-2462-22"}


def wireless(w, sites, *, lan_roles=(("staff", "users", "wlan0"),), diagnostic=True, stable_channels=False, guest_sites=()):
    """Attach explicit WLAN roles to existing AP radios and wired access paths.

    ``lan_roles`` contains (unique WLAN label, local network role, radio name)
    tuples; several WLANs may share a radio. ``guest_sites`` selects canonical
    site keys with existing APs and guest VLANs, using wlan0 without changing
    base group identities or radio channels. Stable channels use each AP identity
    so adding a school room cannot reroll existing radios. RF remains unqualified.
    """
    ns = w.recipe["namespace"]
    aps = defaultdict(list)
    for obj in w.objects.values():
        if obj["kind"] == "device" and obj["refs"].get("role") == "role/ap":
            aps[obj["refs"]["site"]].append(obj["key"])
    sites = [site for site in sites if aps.get(site["key"])]
    if isinstance(guest_sites, str):
        raise DesignError("guest_sites must contain canonical site keys, not one string")
    try:
        guest_sites = set(guest_sites)
    except TypeError as exc:
        raise DesignError("guest_sites must contain canonical site keys") from exc
    if any(not isinstance(key, str) for key in guest_sites) or guest_sites - {site["key"] for site in sites}:
        raise DesignError("guest_sites must be a subset of the supplied sites with APs")
    if not sites:
        return
    lan_roles = tuple(lan_roles)
    labels, radios = [role[0] for role in lan_roles], list(dict.fromkeys(role[2] for role in lan_roles))
    if not lan_roles or len(set(labels)) != len(labels) or (guest_sites and "guest" in labels):
        raise DesignError("WLAN roles require distinct labels, including optional guest service")
    if diagnostic and "wlan1" in radios:
        raise DesignError("The diagnostic hop requires wlan1; disable it when that radio serves a WLAN")
    radio_slots = {radio: index for index, radio in enumerate(radios)}
    if guest_sites:
        radio_slots.setdefault("wlan0", len(radio_slots))
    roles_by_site = {site["key"]: lan_roles + ((("guest", "guest", "wlan0"),) if site["key"] in guest_sites else ()) for site in sites}
    peers = _physical_peers(w.objects)

    def band(device, radio_name):
        """Declared band of this AP model's radio; channels follow the hardware."""
        bands = w.catalog["models"][w.obj(device)["meta"]["hardware"]].get("radio_bands", {})
        if bands.get(radio_name) not in RADIO_CHANNELS:
            raise DesignError(f"{device}: {radio_name} has no reviewed channel plan for its catalog band")
        return bands[radio_name]

    # Check prerequisites before adding groups or modifying the wired/radio paths.
    for site in sites:
        sid = site["key"].split("/", 1)[1]
        for label, network, radio_name in roles_by_site[site["key"]]:
            if w.objects.get(f"vlan/{sid}/{network}", {}).get("kind") != "vlan":
                raise DesignError(f"{sid}: {label} WLAN requires its local {network} VLAN")
            for device in aps[site["key"]]:
                radio = w.objects.get(f"{device}/if/{radio_name}", {})
                if radio.get("kind") != "interface" or not str(radio["attrs"].get("type", "")).startswith("ieee802.11"):
                    raise DesignError(f"{device}: {label} WLAN requires an existing {radio_name} radio")
                ethernet = f"{device}/if/eth0"
                if any(w.objects.get(key, {}).get("kind") != "interface" for key in (ethernet, peers.get(ethernet))):
                    raise DesignError(f"{device}: {label} WLAN requires a real wired access path")
    group_label = "staff" if len(lan_roles) == 1 else "campus"
    group = w.add("wireless_lan_group", f"wireless-group/{group_label}", {"name": titleize(group_label), "slug": f"{ns}-{group_label}"})
    for site in sites:
        devices = sorted(aps[site["key"]])
        sid, tenant = site["key"].split("/", 1)[1], site["refs"]["tenant"]
        site_group = w.add("wireless_lan_group", f"wireless-group/{sid}",
                           {"name": _site_display(w, sid) or titleize(sid), "slug": f"{ns}-{sid}"}, {"parent": group})
        memberships, vlans = defaultdict(list), set()
        for label, network, radio_name in roles_by_site[site["key"]]:
            vlan = f"vlan/{sid}/{network}"
            description = "Staff WLAN; user VLAN separate from AP management" if network == "users" else f"{label.title()} WLAN; {network} VLAN separate from AP management"
            attrs = {"ssid": f"{ns}-{label}", "status": "active", "auth_type": "wpa-enterprise",
                     "auth_cipher": "aes", "description": description}
            if label == "guest" and site["key"] in guest_sites:
                attrs.update(auth_type="open")
                attrs.pop("auth_cipher")
            wlan = w.add("wireless_lan", f"wireless-lan/{sid}/{label}", attrs,
                         {"group": site_group, "vlan": vlan, "scope_site": site["key"], "tenant": tenant})
            memberships[radio_name].append(wlan)
            vlans.add(vlan)
        for ordinal, device in enumerate(devices):
            channel_slot = w.choose(device, "wireless-channel", range(3)) if stable_channels else ordinal
            for radio_name, wlans in memberships.items():
                radio = w.obj(f"{device}/if/{radio_name}")
                channels = RADIO_CHANNELS[band(device, radio_name)]
                radio["attrs"].update(rf_role="ap", rf_channel=channels[(channel_slot + radio_slots[radio_name]) % len(channels)])
                radio["attrs"].pop("mode", None)
                radio["refs"].pop("untagged_vlan", None)
                radio["refs"].pop("tagged_vlans", None)
                radio["refs"]["wireless_lans"] = sorted(wlans)
            ethernet = f"{device}/if/eth0"
            for key in (ethernet, peers[ethernet]):
                w.obj(key)["attrs"]["mode"] = "tagged"
                tags = w.obj(key)["refs"].get("tagged_vlans", [])
                w.obj(key)["refs"]["tagged_vlans"] = sorted(set(tags) | vlans)
        if not diagnostic or len(devices) < 2:
            continue
        # ponytail: a single authored indoor diagnostic hop per site, not an RF planner.
        # It is routed and separate from wired forwarding, so no Ethernet loop is implied.
        prefix = w.obj(f"prefix/{sid}/wireless")
        site_base = ipaddress.ip_network(w.obj(f"prefix/{sid}/reservation")["attrs"]["prefix"])
        net = ipaddress.ip_network((int(site_base.network_address)+11*256, 31))
        w.add("prefix", f"prefix/{sid}/radio-transit", {"prefix": str(net), "status": "active", "description": "Routed indoor diagnostic radio hop"},
              {"vrf": prefix["refs"]["vrf"], "scope_site": site["key"], "tenant": tenant})
        ports = []
        for i, device in enumerate(devices[:2]):
            port = f"{device}/if/wlan1"
            w.obj(port)["attrs"].update(rf_role="ap" if i == 0 else "station",
                                        rf_channel=DIAGNOSTIC_CHANNELS[band(device, "wlan1")],
                                        description="Routed diagnostic radio hop")
            w.obj(port)["refs"]["vrf"] = prefix["refs"]["vrf"]
            w.add("ip_address", f"ip/{port}", {"address": f"{net[i]}/31", "status": "active"},
                  {"assigned_object": port, "vrf": prefix["refs"]["vrf"], "tenant": tenant})
            ports.append(port)
        points = [w.obj(device)["meta"]["placement"]["position_m"] for device in devices[:2]]
        w.add("wireless_link", f"wireless-link/{sid}/diagnostic", {"ssid": f"{ns}-{sid}-diag"[:32], "status": "connected",
              "auth_type": "wpa-enterprise", "auth_cipher": "aes", "distance": max(1, math.ceil(math.dist(*points))), "distance_unit": "m",
              "description": "Indoor diagnostic radio hop"},
              {"interface_a": ports[0], "interface_b": ports[1], "tenant": tenant})


MAC_TAIL_MULTIPLIER = 0x5BD1E9  # odd, so slot -> tail is a bijection mod 2**24


def mac_oui(catalog, w, port):
    """The public IEEE OUI for this interface's maker, or None (locally administered).

    VM interfaces take the conventional QEMU/KVM prefix; makers the catalog
    does not list (generic endpoints, lab simulators) keep a locally
    administered address rather than borrowing someone else's block.
    """
    ouis = catalog["mac_ouis"]
    obj = w.obj(port)
    if obj["kind"] == "vm_interface":
        return ouis["virtual_machine"]
    device_type = w.obj(w.obj(obj["refs"]["device"])["refs"]["device_type"])
    return ouis["manufacturers"].get(w.obj(device_type["refs"]["manufacturer"])["attrs"]["name"])


def mac_tail(namespace, prefix, slot):
    """A stable, scattered 24-bit tail for one ledger slot under one OUI."""
    offset = int.from_bytes(sha256(f"{namespace}/{prefix}/mac-tail".encode()).digest()[:3], "big")
    value = (slot * MAC_TAIL_MULTIPLIER + offset) % (1 << 24)
    return ":".join(f"{(value >> shift) & 255:02X}" for shift in (16, 8, 0))


def macs(w):
    ns = w.recipe["namespace"]
    used = set()
    assigned = {o["refs"].get("assigned_object") for o in w.objects.values() if o["kind"] == "ip_address"}
    for port in sorted(assigned):
        obj = w.obj(port)
        if obj["kind"] not in {"interface", "vm_interface"} or obj["attrs"].get("type") in {"virtual", "bridge"}:
            continue
        oui = mac_oui(w.catalog, w, port)
        if oui:
            # An append-only ledger per OUI keeps every tail unique and stable
            # under growth; a bare 24-bit hash would collide at estate scale.
            mac = f"{oui}:{mac_tail(ns, oui, w.reserve(f'mac/{oui}', port, 1 << 24))}"
        else:
            raw = bytearray(sha256(f"{ns}/{port}/mac".encode()).digest()[:6])
            raw[0] = (raw[0] | 2) & 254
            mac = ":".join(f"{v:02X}" for v in raw)
        if mac in used:
            raise DesignError("Generated MAC collision; choose a new namespace")
        used.add(mac)
        key = w.add("mac_address", f"mac/{port}", {"mac_address": mac}, {"assigned_object": port})
        obj["refs"]["primary_mac_address"] = key
