"""Reusable sites, equipment, attachment pools, addressing, patching and power."""

import hashlib
import ipaddress
import math
import re
from datetime import date, timedelta
from decimal import Decimal

from .model import DesignError
from . import places

NETWORKS = ("management", "users", "atm", "wireless", "security", "voice",
            "applications", "database", "backup", "wan", "storage")

# Authored chassis baselines, excluding separately reserved PoE and optics;
# these are not vendor maximums or measured consumption.
# A dual-supply device normally splits its allowance; either inlet can take it
# all during an A/B failover. Endpoint wall power remains outside this scope.
PLANNED_WATTS = {"access": 120, "inherited-access": 120, "leaf": 160,
                 "core": 220, "edge": 40, "server": 250,
                 "console-server": 40, "liquid-chassis": 400, "provider-edge": 320}


def trunk(site, interfaces, networks):
    vlans = [site.network(n)[0] for n in dict.fromkeys(networks)]
    for key in interfaces:
        site.w.obj(key)["attrs"]["mode"] = "tagged"
        site.w.obj(key)["refs"]["tagged_vlans"] = vlans


def foundation(w, *, industry="bank", inherited=True, networks=NETWORKS,
               device_roles=None, hardware_aliases=None, site_kinds=None, include_carriers=True):
    ns = w.recipe["namespace"]
    w.add("tenant", "tenant", {"name": f"{ns} estate", "slug": ns, "description": w.recipe['name']})
    if inherited:
        w.add("tenant", "tenant/inherited", {"name": f"{ns} Birch Bank", "slug": f"{ns}-birch",
              "description": "Acquisition candidate with retained naming, addressing and carrier contracts"})
    w.add("tag", "tag/estate", {"name": f"{ns}: generated", "slug": f"{ns}-generated", "color": "607d8b"})
    places.foundation(w, site_kinds=site_kinds)
    colors = {"wan-edge": "e65100", "distribution": "6a1b9a", "access": "1565c0",
              "spine": "4a148c", "leaf": "7b1fa2", "server": "2e7d32", "management": "546e7a",
              "patch-panel": "78909c", "pdu": "c62828", "workstation": "00838f",
              "atm": "f9a825", "ap": "00acc1", "camera": "795548", "wall-outlet": "78909c",
              "medical-device": "d81b60", "imaging-device": "8e24aa",
              "provider-edge": "5e35b1", "customer-edge": "00838f"}
    for role in device_roles if device_roles is not None else ("wan-edge", "distribution", "access", "spine", "leaf", "server", "management", "patch-panel",
                 "pdu", "workstation", "atm", "ap", "camera", "wall-outlet"):
        w.add("device_role", f"role/{role}", {"name": f"{ns} {role}", "slug": f"{ns}-{role}", "color": colors[role]})
    for role, color in (("application", "2e7d32"), ("database", "6a1b9a"), ("backup-service", "546e7a")):
        w.add("device_role", f"role/{role}", {"name": f"{ns} {role}", "slug": f"{ns}-{role}",
              "color": color, "vm_role": True})
    w.add("platform", "platform/services", {"name": f"{ns} Service Linux", "slug": f"{ns}-service-linux",
          "description": "Linux service baseline; distribution and version are unspecified"})
    for group in ("network", "compute"):
        w.add("rack_role", f"rack-role/{group}", {"name": f"{ns} {group}", "slug": f"{ns}-{group}",
              "color": "1565c0" if group == "network" else "2e7d32"})
    manufacturers = set()
    for alias, spec in w.catalog["models"].items():
        if hardware_aliases is not None and alias not in hardware_aliases:
            continue
        manufacturer = spec["manufacturer"]
        if manufacturer not in manufacturers:
            w.add("manufacturer", f"manufacturer/{manufacturer}", {"name": manufacturer,
                  "slug": manufacturer.lower().replace(" ", "-")})
            manufacturers.add(manufacturer)
        w.add("device_type", f"hardware/{alias}",
              {k: spec[k] for k in ("model", "slug", "u_height", "is_full_depth", "subdevice_role", "cooling_method") if k in spec},
              {"manufacturer": f"manufacturer/{manufacturer}"})
    for name in networks:
        w.add("vrf", f"vrf/{name}", {"name": f"{ns}-{name}", "enforce_unique": True}, {"tenant": "tenant"})
        # A one-site pool is already represented by the scoped site reservation.
        # Emitting an equal global root would duplicate the same VRF/prefix.
        if w.pool.prefixlen < w.site_prefixlen:
            w.add("prefix", f"root/{name}", {"prefix": w.recipe["address_pool"], "status": "container",
                  "description": f"{ns} {name}: hierarchical site reservations"}, {"vrf": f"vrf/{name}", "tenant": "tenant"})
    for side, provider in (("a", "Northstar Transit"), ("b", "Meridian Carrier")) if include_carriers else ():
        w.add("provider", f"provider/{side}", {"name": f"{ns} {provider}", "slug": f"{ns}-carrier-{side}",
              "comments": f"Minimum private access commitment {50 if side == 'a' else 100} Mbps. "
                          "Separate modeled provider domains do not establish diverse ducts."})
        w.add("provider_network", f"carrier/{side}", {"name": f"{ns}-private-wan-{side}",
              "description": "Opaque managed L3 WAN; provider interior intentionally abstracted"}, {"provider": f"provider/{side}"})
    if include_carriers:
        w.add("circuit_type", "circuit-type/wan", {"name": f"{ns} Private WAN access", "slug": f"{ns}-private-wan"})
    w.add("cluster_type", "cluster-type", {"name": f"{ns} Virtualization", "slug": f"{ns}-virtualization"})


class Site:
    def __init__(self, w, site_id, kind, description, *, tenant=None, routing_domain=None):
        self.w, self.id = w, site_id
        self.key = f"site/{site_id}"
        self.name = f"{w.recipe['namespace']}-{site_id}"
        self.code = site_id.removeprefix("br-").replace("-", "")
        if w.recipe["profile"] == "hospital-clinics" and site_id.startswith("hospital-"):
            self.code = "hosp" + site_id.removeprefix("hospital-").replace("-", "")
        if w.recipe["profile"] == "provider-backbone" and site_id.startswith("ce-"):
            self.code = f"ce{w.allocations[site_id]:05}"
        self.routing_domain = routing_domain
        self.design = w.design_assignments.get(site_id, "modern")
        self.lineage = "birch" if self.design in {"inherited", "refreshed"} else "cedar"
        self.acquired = site_id in w.recipe.get("acquired_sites", []) or self.design == "refreshed"
        self.tenant = tenant or ("tenant/inherited" if self.lineage == "birch" and not self.acquired else "tenant")
        self.equipment_prefix = f"{w.recipe['namespace']}-birch-{site_id}" if self.lineage == "birch" else self.name
        self.racks, self.rack_members, self.devices, self.nets = {}, {}, [], {}
        self.rack_grid = (kind == "dc" and w.recipe["profile"] in {"enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone"}
                          or kind == "pop" and w.recipe["profile"] == "provider-backbone")
        self.rack_domains = self.rack_grid
        self.network_prefixlen = {"regional-bank": 24, "enterprise-data-center": 20, "school-district": 20,
                                  "hospital-clinics": 20, "provider-backbone": 20 if kind == "dc" else 26}[w.recipe["profile"]]
        self.network_offsets = {name: index for index, name in enumerate(NETWORKS)}
        if w.recipe["profile"] == "school-district":
            self.network_offsets.pop("users")
            self.network_offsets.pop("atm")
            self.network_offsets.update(staff=1, students=2, guest=12)
        elif w.recipe["profile"] == "hospital-clinics":
            self.network_offsets = dict(management=0, clinical=1, medical=2, wireless=3, security=4,
                                        applications=5, database=6, backup=7, wan=8, storage=9,
                                        staff=10, imaging=11, guest=12)
        self.links = set()
        self.contract = dict(site=self.key, kind=kind, required_device_roles={}, redundant_uplinks=[],
                             required_connections=[], compute=[], assumptions=[])
        w.contracts.append(self.contract)
        w.add("site", self.key, {"name": self.name, "slug": self.name, "status": "active",
              "description": description,
              "comments": f"Network, compute and facilities inventory for {w.recipe['name']}."}, {"tenant": self.tenant, "tags": ["tag/estate"]})
        self.equipment_location = places.locate(self)

    def display_name(self, label, inherited=False):
        # Names are scoped by the emitted site/tenant; full namespace stays in DNS.
        # NetBox renders name (asset_tag), so duplicating names as tags clips traces.
        abbreviations = {"access": "as", "patch": "pp", "outlet": "jack", "desk": "pc",
                         "edge": "gw", "dist": "ds", "spine": "sp", "leaf": "lf",
                         "compute": "cp", "network": "n", "host": "h"}
        short = "-".join(abbreviations.get(part, part) for part in label.split("-"))
        short = re.sub(r"-(r?)(\d+)(?=-|$)", lambda m: f"{m[1]}{int(m[2]):02}", short)
        return f"{'birch-' if inherited else ''}{self.code}-{short}"

    def room_prefix(self, location):
        return "" if location == self.equipment_location else location.rsplit("/", 1)[-1] + "-"

    def device(self, alias, label, role, group="network", racked=True, meta=None, location=None, rack_domain=None):
        location = location or self.equipment_location
        spec = self.w.catalog["models"][alias]
        key = f"device/{self.id}/{label}"
        inherited = self.lineage == "birch" and not (role == "access" and self.design == "refreshed")
        attrs = dict(name=self.display_name(label, inherited), status="active",
                     serial=f"SYN-{self.w.choose(key, 'serial', range(10**10)):010d}",
                     description=f"{role} at {self.name}")
        refs = dict(site=self.key, device_type=f"hardware/{alias}", role=f"role/{role}", tenant=self.tenant, tags=["tag/estate"])
        metadata = dict(hardware=alias, purpose=role, **(meta or {}))
        if racked and spec["u_height"]:
            if rack_domain is not None and not self.rack_domains:
                if self.racks:
                    raise DesignError("Rack lanes must be selected before allocating equipment; use a new site baseline")
                self.rack_domains = True
            if rack_domain is None and self.rack_domains:
                rack_domain = 0
            scope = f"rack-slots/{self.id}/{self.room_prefix(location)}{group}"
            if rack_domain is not None:
                if type(rack_domain) is not int or not 0 <= rack_domain < 4:
                    raise DesignError("rack_domain must be an integer lane from 0 through 3")
                scope += f"/domain-{rack_domain}"
            slot = self.w.reserve(scope, key, 1000)
            rack_no, unit = divmod(slot, 10)
            if rack_domain is not None:
                rack_no = 4 * rack_no + rack_domain
            rack = self.rack(group, rack_no, location)
            attrs.update(position=unit * 2 + 1, face="front")
            refs.update(rack=rack, location=location)
            self.rack_members[rack].append(key)
        self.w.add("device", key, attrs, refs, metadata)
        self.devices.append(key)
        for port in spec["interfaces"]:
            p = dict(port)
            self.w.add("interface", f"{key}/if/{p['name']}", p | {"enabled": True}, {"device": key}, {"hardware_port": True})
        for port in spec["power_ports"]:
            self.w.add("power_port", f"{key}/power/{port['name']}", dict(port), {"device": key})
        for kind in ("console_port", "console_server_port"):
            for port in spec.get(f"{kind}s", []):
                self.w.add(kind, f"{key}/{kind}/{port['name']}", dict(port), {"device": key})
        for position in range(1, spec.get("passive_ports", 0)+1):
            rear = self.w.add("rear_port", f"{key}/rear/{position}",
                             {"name": f"R{position:02}", "type": "8p8c", "positions": 1}, {"device": key})
            self.w.add("front_port", f"{key}/front/{position}",
                       {"name": f"F{position:02}", "type": "8p8c", "rear_port_position": 1},
                       {"device": key, "rear_port": rear})
        return key

    def rack(self, group, ordinal, location=None):
        location = location or self.equipment_location
        room = self.room_prefix(location)
        key = f"rack/{self.id}/{room}{group}-{ordinal+1:02}"
        if key not in self.racks:
            height = 24 if self.contract["kind"] == "branch" and self.w.obj(self.key)["meta"].get("branch_size") == "small" else 42
            asset_tag = f"{self.name}-{room}{group}-{ordinal+1:02}"
            if len(asset_tag) > 50:
                # Native Rack.asset_tag is globally unique and limited to 50.
                # Keep accepted short tags; retain full room scope in the digest.
                asset_tag = asset_tag[:33] + "-" + hashlib.sha256(asset_tag.encode()).hexdigest()[:16]
            self.w.add("rack", key, {"name": f"{'N' if group == 'network' else 'C'}{ordinal+1:02}", "u_height": height,
                  "width": 19, "status": "active", "asset_tag": asset_tag,
                  "description": "Branch network cabinet" if height == 24 else f"{group.title()} equipment cabinet"},
                  {"site": self.key, "location": location, "tenant": self.tenant,
                   "role": f"rack-role/{group}"})
            self.racks[key] = True
            self.rack_members[key] = []
            if self.rack_grid:
                # Two bounded cabinet zones share a synthetic data hall. Lane
                # ordinals retain positions during growth; unused lanes are space.
                if ordinal >= 64:
                    raise DesignError(f"{self.id}: data hall supports 64 rack positions per equipment zone; extend the reviewed layout")
                row, column = divmod(ordinal, 8)
                self.w.obj(key)["meta"]["position_m"] = [4 + 1.2*column, 4 + 3*row + (30 if group == "compute" else 0), 0]
        return key

    def interface(self, device, name):
        key = f"{device}/if/{name}"
        if key not in self.w.objects:
            raise DesignError(f"Missing hardware interface: {key}")
        return key

    def virtual_interface(self, device, name, network):
        key = f"{device}/if/{name}"
        vlan, _ = self.network(network)
        self.w.add("interface", key, {"name": name, "type": "virtual", "enabled": True, "mode": "access"},
                   {"device": device, "vrf": self.vrf(network), "untagged_vlan": vlan})
        return key

    def cable(self, a, b, cable_type="cat6", label=None):
        if a in self.links or b in self.links:
            raise DesignError(f"Port already occupied: {a} or {b}")
        self.links.update((a, b))
        key = "cable/" + "--".join(sorted((a, b)))
        # ponytail: 10,000 generic cable labels per site exceed this blueprint's
        # physical capacity; raise the reservation ceiling with a larger design.
        cable_label = label or f"{self.code}-C{self.w.reserve(f'cable-labels/{self.id}', key, 10000)+1:03}"
        self.w.add("cable", key, {"label": cable_label,
              "type": cable_type, "status": "connected", "length": 3, "length_unit": "m"}, {"a": a, "b": b})
        rooms = []
        for end in (a, b):
            node = self.w.obj(end)
            owner = self.w.obj(node["refs"]["device"]) if "device" in node["refs"] else node
            rooms.append(owner["refs"].get("location"))
        if all(rooms) and rooms[0] != rooms[1]:
            points = [self.w.obj(room)["meta"]["position_m"] for room in rooms]
            self.w.obj(key)["attrs"].update(
                length=math.ceil(sum(abs(x-y) for x, y in zip(*points)) + 10),
                description=f"{self.w.obj(rooms[0])['attrs']['name']} to {self.w.obj(rooms[1])['attrs']['name']}; modeled building route")
        elif self.rack_grid:
            racks = []
            for end in (a, b):
                node = self.w.obj(end)
                owner = self.w.obj(node["refs"]["device"]) if "device" in node["refs"] else node
                racks.append(owner["refs"].get("rack"))
            if all(racks) and racks[0] != racks[1]:
                points = [self.w.obj(rack)["meta"]["position_m"] for rack in racks]
                self.w.obj(key)["attrs"].update(length=math.ceil(sum(abs(x-y) for x,y in zip(*points)) + 3),
                    description="Inter-rack route: cabinet-grid distance plus three metres of service slack")
        for source, peer in ((a, b), (b, a)):
            obj = self.w.obj(source)
            if obj["kind"] == "interface":
                port = self.w.obj(peer)
                owner = port["refs"].get("device") or port["refs"].get("circuit")
                display = self.w.obj(owner)["attrs"] if owner else port["attrs"]
                obj["attrs"]["description"] = f"To {display.get('name', display.get('cid', 'termination'))} / {port['attrs'].get('name', port['attrs'].get('term_side', ''))}"
        return key

    def redundant(self, device, peers, minimum=2):
        self.contract["redundant_uplinks"].append(dict(device=device, peers=peers, min_distinct_peers=minimum))

    def vrf(self, role):
        if self.routing_domain is not None:
            return self.routing_domain
        return f"vrf/inherited/{self.id}/{role}" if self.lineage == "birch" else f"vrf/{role}"

    def network(self, role):
        if role in self.nets:
            return self.nets[role]
        if role not in self.network_offsets:
            raise DesignError(f"{self.id}: unsupported network role {role!r}; extend the reviewed address plan")
        index = self.network_offsets[role]
        container = self.w.site_network(self.id)
        if self.lineage == "birch":
            # ponytail: the authored Birch plan has 256 /20 slots in 172.16/12.
            # Add a reviewed larger inherited plan before increasing this ceiling.
            slot = self.w.reserve("inherited-site-networks", self.id, 256)
            container = ipaddress.ip_network((int(ipaddress.IPv4Address("172.16.0.0")) + slot * 4096, 20))
        prefixlen = self.network_prefixlen
        net = ipaddress.ip_network((int(container.network_address) + index * (1 << (32-prefixlen)), prefixlen))
        vrf = self.vrf(role)
        if vrf not in self.w.objects:
            self.w.add("vrf", vrf, {"name": f"{self.equipment_prefix}-{role}", "enforce_unique": True,
                       "description": "Retained Birch site routing context; isolation and renumbering are explicit design choices"}, {"tenant": self.tenant})
        self.w.add("prefix", f"prefix/{self.id}/{role}/reservation", {"prefix": str(container), "status": "container",
              "description": f"Stable site reservation for {role}; child allocation fixed by network role"}, {"vrf": vrf, "tenant": self.tenant, "scope_site": self.key})
        vlan = self.w.add("vlan", f"vlan/{self.id}/{role}", {"name": f"{self.name}-{role}", "vid": 10 * (index+1),
              "status": "active", "description": f"{role} segment"}, {"site": self.key, "tenant": self.tenant})
        self.w.add("prefix", f"prefix/{self.id}/{role}", {"prefix": str(net), "status": "active",
              "description": f"{self.name} {role}; .1 and .2 reserved for gateway SVIs"},
              {"vrf": vrf, "vlan": vlan, "scope_site": self.key, "tenant": self.tenant})
        self.nets[role] = vlan, net
        return vlan, net

    def address(self, interface, network, host=None, primary=False, device=None):
        _, net = self.network(network)
        if host is None:
            host = 10 + self.w.reserve(f"addresses/{self.id}/{network}", interface, net.num_addresses - 12)
        if not 0 < host < net.num_addresses-1:
            raise DesignError(f"{self.name} {network}: host {host} outside usable /{net.prefixlen} addresses")
        key = f"ip/{interface}"
        node = self.w.obj(interface)
        owner = device or node["refs"].get("device") or node["refs"].get("virtual_machine")
        dns_name = self.w.obj(owner)["attrs"]["name"] if owner else self.code
        attrs = dict(address=f"{net.network_address + host}/{net.prefixlen}", status="active",
                     description=f"{network.title()} / {dns_name} / {node['attrs']['name']}",
                     dns_name=f"{dns_name}.{self.w.recipe['namespace']}.example")
        self.w.add("ip_address", key, attrs, {"assigned_object": interface, "vrf": self.vrf(network), "tenant": self.tenant})
        if node["kind"] in {"interface", "vm_interface"}:
            node["refs"]["vrf"] = self.vrf(network)
        if primary and device:
            self.w.obj(device)["refs"]["primary_ip4"] = key
        return key

    def wan(self, edge, side, number, demand_mbps=None):
        # Purchased bandwidth differs from the physical handoff. These finite
        # product tiers are fictional planning rules, not real carrier offers.
        dc = self.contract["kind"] == "dc"
        if not dc and (type(demand_mbps) not in (int, float) or demand_mbps <= 0):
            raise DesignError(f"{self.id}: WAN access needs positive site peak demand")
        floor = max(50 if side == "a" else 100, 200 if self.lineage == "birch" else 0)
        usable_fraction = 1 - Decimal(str(self.w.recipe["reserve_fraction"]))
        budget = float(1000 * usable_fraction)
        peak = budget if dc else demand_mbps
        rate = 1000 if dc else next((rate for rate in self.w.recipe["wan_tiers_mbps"]
                                     if rate >= floor and rate * usable_fraction >= Decimal(str(peak))), None)
        if rate is None:
            raise DesignError(f"{self.id}: WAN demand {peak:g} Mbps plus reserve exceeds the supported 1 Gb/s handoff")
        cohort = "dc-aggregation" if dc else "retained-birch" if self.lineage == "birch" else "cedar-standard"
        if not dc and self.w.recipe["profile"] == "school-district":
            cohort = "district-standard"
        elif not dc and self.w.recipe["profile"] == "hospital-clinics":
            cohort = "health-system-standard"
        ages = {"dc-aggregation": range(1460, 1826), "retained-birch": range(2190, 2921),
                "cedar-standard": range(365, 1096), "district-standard": range(365, 1096),
                "health-system-standard": range(365, 1096)}
        key = f"circuit/{self.id}/{side}/{number}"
        try:
            installed = (date.fromisoformat(self.w.recipe["as_of"]) -
                         timedelta(days=self.w.choose(key, "wan-install-age", ages[cohort]))).isoformat()
        except OverflowError as exc:
            raise DesignError(f"{self.id}: as_of is too early for the authored procurement history") from exc
        reason = ("Full-rate DC aggregation; add edge pairs as aggregate demand grows" if dc else
                  f"Smallest tier covering {peak:g} Mbps peak with {Decimal(str(self.w.recipe['reserve_fraction']))*100:g}% reserve "
                  f"and {floor} Mbps contract minimum")
        if cohort == "retained-birch":
            reason += "; retained Birch bulk contract survives ownership and access-hardware changes"
        circuit = self.w.add("circuit", key,
                            {"cid": f"{self.name}-{side.upper()}-{number:03}", "status": "active",
                             "commit_rate": rate * 1000, "install_date": installed,
                             "description": f"{rate} Mb/s private WAN commitment on 1 Gb/s handoff; carrier {side.upper()}",
                             "comments": f"Procurement record: {cohort}. {reason}."},
                            {"provider": f"provider/{side}", "type": "circuit-type/wan", "tenant": self.tenant},
                            {"procurement": {"cohort": cohort, "minimum_commit_mbps": 1000 if dc else floor,
                                             "planned_peak_mbps": peak, "handoff_mbps": 1000,
                                             "selection_reason": reason}})
        for term, target in (("A", self.key), ("Z", f"carrier/{side}")):
            self.w.add("circuit_termination", f"{circuit}/{term}", {"term_side": term, "port_speed": 1000000,
                  "description": "Copper customer handoff" if term == "A" else "Abstract provider network attachment"},
                  {"circuit": circuit, "termination": target})
        port = self.interface(edge, "wan1")
        self.cable(port, f"{circuit}/A")
        self.address(port, "wan", primary=False)
        self.contract["required_connections"].append({"a": port, "b": f"{circuit}/A"})

    def patch(self, switch, port_name, endpoint, endpoint_port, panel, position):
        source, target = self.interface(switch, port_name), self.interface(endpoint, endpoint_port)
        channel_length = self.w.obj(endpoint)["meta"]["access_channel_length_m"]
        room = self.w.obj(self.w.obj(endpoint)["refs"]["location"])["attrs"]["name"]
        equipment = self.w.obj(self.w.obj(switch)["refs"]["location"])["attrs"]["name"]
        endpoint_label = endpoint.rsplit('/', 1)[-1]
        channel_label = self.display_name(endpoint_label)
        if panel is None:
            link = self.cable(source, target, label=f"{channel_label}-D")
            self.w.obj(link)["attrs"].update(length=channel_length, description=f"{equipment} to {room}; continuous access channel")
            self.w.obj(link)["meta"]["representation"] = "Continuous access channel; passive patching abstracted"
            self.contract["required_connections"].append(dict(a=source, b=target))
            return
        rear, front = f"{panel}/rear/{position}", f"{panel}/front/{position}"
        if rear not in self.w.objects or front not in self.w.objects:
            raise DesignError(f"{panel}: patch position {position} exceeds the catalog inventory")
        outlet = self.device("wall-outlet", f"outlet-{endpoint_label}", "wall-outlet", racked=False)
        self.w.obj(outlet)["refs"]["location"] = self.w.obj(endpoint)["refs"]["location"]
        self.w.obj(outlet)["attrs"]["description"] = f"{room} data outlet serving {self.w.obj(endpoint)['attrs']['name']}"
        self.w.obj(outlet)["meta"]["serves_endpoint"] = endpoint
        patch = self.cable(source, front, label=f"{channel_label}-P")
        self.w.obj(patch)["attrs"].update(length=2, description=f"Cabinet patch cord to {self.w.obj(endpoint)['attrs']['name']}")
        link = self.cable(rear, f"{outlet}/rear/1", label=f"{channel_label}-H")
        self.w.obj(link)["attrs"].update(length=channel_length-5, description=f"{equipment} to {room}")
        cord = self.cable(f"{outlet}/front/1", target, label=f"{channel_label}-R")
        self.w.obj(cord)["attrs"].update(length=3, description=f"{room} outlet to endpoint")
        self.contract["required_connections"].append(dict(a=source, b=target))

    def management(self, parents, uplinks=None):
        # ponytail: eight 20-port management blocks per site are bounded by the
        # upstream port pool, independently of /24 or /20 address space. Expand
        # the reviewed physical uplink pool before raising this ceiling.
        # Gateways already managed through an addressed SVI keep their dedicated
        # management port unused instead of joining the same subnet twice.
        managed = [(key, p["name"]) for key in self.devices
                   if not self.w.obj(key)["refs"].get("primary_ip4")
                   for p in self.w.catalog["models"][self.w.obj(key)["meta"]["hardware"]]["interfaces"] if p.get("mgmt_only")]
        switches = {}
        for device, port_name in managed:
            location = self.w.obj(device)["refs"]["location"]
            room = self.room_prefix(location)
            slot = self.w.reserve(f"management-ports/{self.id}" + (f"/{room[:-1]}" if room else ""), device, 160)
            block, port = divmod(slot, 20)
            group = (location, block)
            if group not in switches:
                switch = self.device("access", f"{room}mgmt-{block+1:02}", "management", location=location)
                switches[group] = switch
                parent_slot = self.w.reserve(f"management-uplinks/{self.id}", switch, 8)
                if uplinks is not None:
                    if parent_slot >= len(uplinks):
                        raise DesignError(f"{self.name}: management blocks exceed the supplied parent ports")
                    parent_port = uplinks[parent_slot]
                else:
                    parent_port = self.interface(parents[parent_slot % 2], f"Ethernet{41 + parent_slot//2}")
                copper = self.w.obj(parent_port)["attrs"]["type"] == "1000base-t"
                uplink = self.interface(switch, "GigabitEthernet1/0/24" if copper else "TenGigabitEthernet1/1/1")
                self.cable(uplink, parent_port, "cat6" if copper else "smf")
                vlan, _ = self.network("management")
                for p in (uplink, parent_port):
                    self.w.obj(p)["attrs"]["mode"] = "tagged"
                    self.w.obj(p)["refs"]["tagged_vlans"] = [vlan]
                vi = self.virtual_interface(switch, "Vlan10", "management")
                self.address(vi, "management", primary=True, device=switch)
            switch = switches[group]
            source = self.interface(switch, f"GigabitEthernet1/0/{port+1}")
            target = self.interface(device, port_name)
            self.cable(source, target)
            vlan, _ = self.network("management")
            for p in (source, target):
                self.w.obj(p)["attrs"]["mode"] = "access"
                self.w.obj(p)["refs"]["untagged_vlan"] = vlan
            self.address(target, "management", primary=True, device=device)

    def power(self):
        self.contract["power_redundancy"] = []
        for location in self.contract["placement"]["equipment_locations"].values():
            room = self.room_prefix(location)
            for side in ("a", "b"):
                self.w.add("power_panel", f"panel/{self.id}/{room}{side}", {"name": f"{self.name}-{room}supply-{side.upper()}"},
                           {"site": self.key, "location": location})
        for rack, members in self.rack_members.items():
            location = self.w.obj(rack)["refs"]["location"]
            room = self.room_prefix(location)
            outlets = {}
            for side in ("a", "b"):
                label = f"pdu-{rack.split('/')[-1]}-{side}"
                pdu = self.device("pdu", label, "pdu", racked=False)
                self.w.obj(pdu)["refs"].update(rack=rack, location=location)
                self.w.obj(pdu)["meta"]["failure_domain"] = f"{self.id}-{room}supply-{side}"
                feed = self.w.add("power_feed", f"feed/{rack}/{side}",
                                 {"name": f"{self.name}-{rack.split('/')[-1]}-{side}", "status": "active",
                                  "type": "primary" if side == "a" else "redundant", "supply": "ac", "phase": "single-phase",
                                  "voltage": 230, "amperage": 16, "max_utilization": 80},
                                 {"power_panel": f"panel/{self.id}/{room}{side}", "rack": rack})
                inlet = f"{pdu}/power/Input"
                self.cable(feed, inlet, "power")
                outlets[side] = []
                for port in self.w.catalog["models"]["pdu"]["power_outlets"]:
                    outlet = self.w.add("power_outlet", f"{pdu}/outlet/{port['name']}", dict(port), {"device": pdu, "power_port": inlet})
                    outlets[side].append(outlet)
            for device in members:
                # Rack slots persist across growth; emission order need not.
                i = (self.w.obj(device)["attrs"]["position"] - 1) // 2
                ports = self.w.catalog["models"][self.w.obj(device)["meta"]["hardware"]]["power_ports"]
                allowance = PLANNED_WATTS.get(self.w.obj(device)["meta"]["hardware"], 0)
                for j, port in enumerate(ports):
                    side = "a" if j % 2 == 0 else "b"
                    inlet = f"{device}/power/{port['name']}"
                    self.w.obj(inlet)["attrs"].update(allocated_draw=allowance // len(ports), maximum_draw=allowance,
                         description="Planning allocation; maximum reserves single-feed failover")
                    self.cable(outlets[side][i], inlet, "power")
                if ports:
                    self.w.obj(device)["meta"]["planned_watts"] = allowance
                    self.contract["power_redundancy"].append(dict(device=device, min_distinct_pdus=min(2, len(ports)), planned_watts=allowance))
        self.contract["assumptions"].append("Power checks cover racked infrastructure and separate modeled A/B panels; facility independence and endpoint wall power are not claimed.")
        self.contract["assumptions"].append("Inlet draws are authored chassis planning allocations plus connected PD reservations with a 1.25 upstream AC allowance, split in normal operation with full per-device failover allowance on each supply. Endpoint wall power is excluded; no measured or vendor-certified consumption is claimed.")
