"""Reusable sites, equipment, attachment pools, addressing, patching and power."""

import ipaddress
import math
import re
from datetime import date, timedelta
from decimal import Decimal

from .model import SERIAL_SPACE, DesignError, vendor_serial
from .naming import titleize
from . import naming, places

NETWORKS = ("management", "users", "atm", "wireless", "security", "voice",
            "applications", "database", "backup", "wan", "storage")

# Authored chassis baselines, excluding separately reserved PoE and optics;
# these are not vendor maximums or measured consumption.
# A dual-supply device normally splits its allowance; either inlet can take it
# all during an A/B failover. Endpoint wall power remains outside this scope.
# Selectable vendor lines share their family's authored allowance: the number is
# a planning allocation for that role, never a vendor or measured figure.
PLANNED_WATTS = {"access": 120, "access-juniper": 120, "inherited-access": 120,
                 "leaf": 160, "leaf-juniper": 160,
                 "core": 220, "edge": 40, "server": 250,
                 "console-server": 40, "console-server-48": 40, "liquid-chassis": 400, "provider-edge": 320}

# Equipment-room layout grammar, in metres. Cabinets are bayed contiguously
# along a row (pitch equals the 0.6 m cabinet width); rows are spaced by the
# cabinet depth plus a working aisle; the two equipment zones sit side by side
# across one main aisle. Zone origins are fixed by the reviewed row length, not
# by how many cabinets are installed, so appending a cabinet to either zone
# never moves an existing one.
CABINET_WIDTH_M = 0.6
CABINET_ROW_PITCH_M = 2.4
CABINETS_PER_ROW = 4
ZONE_AISLE_M = 1.5
ROOM_ORIGIN_M = 1.0
ZONE_PITCH_M = CABINETS_PER_ROW * CABINET_WIDTH_M + ZONE_AISLE_M
# One rack lane admits ten devices (see Site.device), each mounted in a single
# rack unit, so a lane can never need more than eleven units of mounting space.
# A 24U cabinet is the honest enclosure for that lane; a 42U cabinet would be
# three quarters empty by construction.
RACK_LANE_DEVICES = 10
RACK_U_HEIGHT = 24
# A single-CE premises (one edge, one switch) gets the small-room kit: a 13U
# Panduit R2P26 two-post rack, one 1U 120 V APC AP9563 on one 120 V / 20 A
# branch circuit and no console server. Both lane devices still fit below it.
SMALL_RACK = (13, "2-post-frame", "Two-post equipment rack")
# Feed electrics by country: US cabinets take 208 V / 20 A single-phase
# circuits (the AP9572 is a 16 A 208/230 V PDU, the 80% continuous load of a
# 20 A breaker); a small US room takes one 120 V / 20 A circuit. Other
# countries keep the 230 V / 16 A single-phase planning feed.
FEED_ELECTRICS = {("US", False): (208, 20), ("US", True): (120, 20)}
DEFAULT_FEED = (230, 16)


def svi_name(w, device, name):
    """The routed-VLAN interface name ``device``'s platform uses for ``VlanN``.

    The catalog platform declares ``svi_format`` where it differs from the
    ``VlanN`` convention (Junos: ``irb.N``). Non-SVI names pass through.
    Exposed for builders outside this module (provider.py keys its own SVIs).
    """
    match = re.fullmatch(r"Vlan(\d+)", name)
    if not match:
        return name
    spec = w.catalog["models"][w.obj(device)["meta"]["hardware"]]
    return spec.get("platform", {}).get("svi_format", "Vlan{vid}").format(vid=match[1])


def dns_label(name):
    """An interface name as one DNS label: xe-0/1/1 -> xe-0-1-1."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def trunk(site, interfaces, networks):
    vlans = [site.network(n)[0] for n in dict.fromkeys(networks)]
    for key in interfaces:
        site.w.obj(key)["attrs"]["mode"] = "tagged"
        site.w.obj(key)["refs"]["tagged_vlans"] = vlans


def foundation(w, *, industry="bank", inherited=True, networks=NETWORKS,
               device_roles=None, hardware_aliases=None, site_kinds=None, include_carriers=True):
    ns = w.recipe["namespace"]
    w.add("tenant", "tenant", {"name": w.recipe["name"], "slug": ns, "description": "Network owner and operator"})
    if inherited:
        w.add("tenant", "tenant/inherited", {"name": "Birch Bank", "slug": f"{ns}-birch",
              "description": "Acquisition candidate with retained naming, addressing and carrier contracts"})
    # Tags are graph-derived once the estate is finished (operations.finalize):
    # a blanket "everything is managed" tag carried no information.
    places.foundation(w, site_kinds=site_kinds)
    # Every candidate role is declared here; operations.finalize drops the ones
    # nothing references, so an estate's role list is exactly what it uses.
    for role in device_roles if device_roles is not None else ("wan-edge", "distribution", "access", "spine", "leaf", "server", "management", "patch-panel",
                 "pdu", "workstation", "atm", "ap", "camera", "wall-outlet"):
        w.add("device_role", f"role/{role}", {"name": titleize(role), "slug": f"{ns}-{role}", "color": naming.ROLE_COLORS[role]})
    for role in ("application", "database", "backup-service"):
        w.add("device_role", f"role/{role}", {"name": titleize(role), "slug": f"{ns}-{role}",
              "color": naming.ROLE_COLORS[role], "vm_role": True})
    w.add("platform", "platform/services", {"name": "Service Linux", "slug": f"{ns}-service-linux",
          "description": "Linux server build for service VMs"})
    for group in ("network", "compute"):
        w.add("rack_role", f"rack-role/{group}", {"name": titleize(group), "slug": f"{ns}-{group}",
              "color": "1565c0" if group == "network" else "2e7d32"})
    # Callers name role families; the recipe's selected vendor line decides which
    # catalog model that family actually emits.
    selected = None if hardware_aliases is None else {w.hardware_alias(a) for a in hardware_aliases}
    if selected is not None and "console-server" in selected:
        # The equipment builder sizes each room's console server from its own
        # serial demand, so both Opengear sizes belong to any estate that has one.
        selected.add("console-server-48")
    if selected is not None and "provider-edge" in selected:
        # Provider customer premises take the small-room 120 V PDU; PoP cages
        # carry a fibre enclosure per PE cabinet.
        selected |= {"pdu-120", "fibre-panel"}
    manufacturers = set()
    for alias, spec in w.catalog["models"].items():
        if selected is not None and alias not in selected:
            continue
        manufacturer = spec["manufacturer"]
        if manufacturer not in manufacturers:
            w.add("manufacturer", f"manufacturer/{manufacturer}", {"name": manufacturer,
                  "slug": manufacturer.lower().replace(" ", "-")})
            manufacturers.add(manufacturer)
        if "platform" in spec and f"platform/{spec['platform']['slug']}" not in w.objects:
            # Same identity grammar as the Service Linux platform: an authored
            # vendor OS name, a namespaced slug, linked to its manufacturer.
            w.add("platform", f"platform/{spec['platform']['slug']}",
                  {"name": spec["platform"]["name"], "slug": f"{ns}-{spec['platform']['slug']}"},
                  {"manufacturer": f"manufacturer/{manufacturer}"})
        w.add("device_type", f"hardware/{alias}",
              {k: spec[k] for k in ("model", "slug", "part_number", "u_height", "is_full_depth", "subdevice_role",
                                   "cooling_method", "airflow", "weight", "weight_unit") if k in spec},
              {"manufacturer": f"manufacturer/{manufacturer}"})
    # Optic makers need a manufacturer row even when no chassis shares it
    # (generic third-party server optics in an otherwise vendor-only estate).
    for part in w.catalog["optics"]["parts"].values():
        if (part["manufacturer"] not in manufacturers and
                any(f"hardware/{alias}" in w.objects for alias in part["compatible_interfaces"])):
            w.add("manufacturer", f"manufacturer/{part['manufacturer']}", {"name": part["manufacturer"],
                  "slug": part["manufacturer"].lower().replace(" ", "-")})
            manufacturers.add(part["manufacturer"])
    for name in networks:
        w.add("vrf", f"vrf/{name}", {"name": titleize(name), "enforce_unique": True}, {"tenant": "tenant"})
    # One global container for the whole pool, directly under its aggregate.
    # A global container parents prefixes in every VRF, so repeating the same
    # pool once per VRF only multiplied identical rows. A one-site pool is
    # already represented by the scoped site reservation.
    if w.pool.prefixlen < w.site_prefixlen:
        w.add("prefix", "root/pool", {"prefix": w.recipe["address_pool"], "status": "container",
              "description": "Site allocation pool"}, {"tenant": "tenant"})
    for side, provider in (("a", "Northstar Transit"), ("b", "Meridian Carrier")) if include_carriers else ():
        w.add("provider", f"provider/{side}", {"name": provider, "slug": f"{ns}-carrier-{side}",
              "comments": f"Minimum private access commitment {50 if side == 'a' else 100} Mbps."})
        w.add("provider_network", f"carrier/{side}", {"name": f"Private WAN {side.upper()}",
              "description": "Carrier-managed private L3 WAN"}, {"provider": f"provider/{side}"})
    if include_carriers:
        w.add("circuit_type", "circuit-type/wan", {"name": "Private WAN access", "slug": f"{ns}-private-wan"})
    w.add("cluster_type", "cluster-type", {"name": "Virtualization", "slug": f"{ns}-virtualization"})


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
        self.racks, self.rack_members, self.devices, self.nets = {}, {}, [], {}
        self.rack_grid = (kind == "dc" and w.recipe["profile"] in {"enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone", "retail-chain", "university-campus", "msp", "manufacturing", "utility"}
                          or kind == "pop" and w.recipe["profile"] == "provider-backbone")
        self.rack_domains = self.rack_grid
        # A provider customer premises is one CE and one switch: no redundant
        # pair needs a second feed, PDU or console server (SMALL_RACK).
        self.small_kit = kind == "customer"
        self.network_prefixlen = {"regional-bank": 24, "enterprise-data-center": 20, "school-district": 20,
                                  "hospital-clinics": 20, "provider-backbone": 20 if kind == "dc" else 26,
                                  "retail-chain": 20 if kind == "dc" else 24,
                                  # Residence halls address every installed room
                                  # data port, so campus buildings need a /22.
                                  "university-campus": 20 if kind == "dc" else 22,
                                  "msp": 20 if kind == "dc" else 24,
                                  "manufacturing": 20 if kind == "dc" else 24,
                                  "utility": 20 if kind == "dc" else 24}[w.recipe["profile"]]
        self.network_offsets = {name: index for index, name in enumerate(NETWORKS)}
        if w.recipe["profile"] == "school-district":
            self.network_offsets.pop("users")
            self.network_offsets.pop("atm")
            self.network_offsets.update(staff=1, students=2, guest=12)
        elif w.recipe["profile"] == "hospital-clinics":
            self.network_offsets = dict(management=0, clinical=1, medical=2, wireless=3, security=4,
                                        applications=5, database=6, backup=7, wan=8, storage=9,
                                        staff=10, imaging=11, guest=12)
        elif w.recipe["profile"] == "retail-chain":
            self.network_offsets = dict(management=0, backoffice=1, pos=2, wireless=3, security=4,
                                        applications=5, database=6, backup=7, wan=8, storage=9,
                                        guest=12)
        elif w.recipe["profile"] == "university-campus":
            self.network_offsets = dict(management=0, staff=1, students=2, research=3, wireless=4,
                                        security=5, applications=6, database=7, backup=8, wan=9,
                                        storage=10, guest=12)
        elif w.recipe["profile"] == "msp":
            self.network_offsets = dict(management=0, staff=1, wireless=3, security=4,
                                        applications=5, database=6, backup=7, wan=8, storage=9,
                                        guest=12)
        elif w.recipe["profile"] == "manufacturing":
            # `process` and `supervisory` are the plant-floor (OT) zones;
            # `conduit` is the modeled routed transit between the two tiers.
            self.network_offsets = dict(management=0, office=1, process=2, supervisory=3,
                                        wireless=4, security=5, applications=6, database=7,
                                        backup=8, wan=9, storage=10, logistics=11, conduit=12)
        elif w.recipe["profile"] == "utility":
            # `protection`, `telemetry` and `station` are the station (OT) zone;
            # `conduit` is the modeled routed transit between the two tiers.
            self.network_offsets = dict(management=0, office=1, protection=2, telemetry=3,
                                        station=4, applications=6, database=7, backup=8,
                                        wan=9, storage=10, conduit=12)
        self.links = set()
        self.contract = dict(site=self.key, kind=kind, required_device_roles={}, redundant_uplinks=[],
                             required_connections=[], compute=[], assumptions=[])
        w.contracts.append(self.contract)
        w.add("site", self.key, {"name": self.name, "slug": self.name, "status": "active",
              "description": description}, {"tenant": self.tenant})
        self.equipment_location = places.locate(self)

    @property
    def display(self):
        """The authored site display name places.locate() settled on.

        ``self.name`` is the namespaced *slug* stem; every human-facing label
        built from a site reads this instead (estates/naming.py).
        """
        return naming.site_display(self.w, self.key)

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
        # Single resolution point: builders name a role family, the recipe's
        # selected vendor line names the catalog model that is actually built.
        alias = self.w.hardware_alias(alias)
        spec = self.w.catalog["models"][alias]
        key = f"device/{self.id}/{label}"
        inherited = self.lineage == "birch" and not (role == "access" and self.design == "refreshed")
        attrs = dict(name=self.display_name(label, inherited), status="active",
                     serial=vendor_serial(spec["serial_format"], self.w.choose(f"{self.w.recipe['namespace']}/{key}", "serial", SERIAL_SPACE)),
                     # Reference the emitted display name (authored or legacy);
                     # device identities themselves stay keyed on stable ids.
                     description=f"{naming.role_label(role)} at {self.w.obj(self.key)['attrs']['name']}")
        refs = dict(site=self.key, device_type=f"hardware/{alias}", role=f"role/{role}", tenant=self.tenant)
        if "platform" in spec:
            refs["platform"] = f"platform/{spec['platform']['slug']}"
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
            rack_no, unit = divmod(slot, RACK_LANE_DEVICES)
            if rack_domain is not None:
                rack_no = 4 * rack_no + rack_domain
            rack = self.rack(group, rack_no, location)
            # Lane members mount contiguously from the bottom rail: real
            # installs bay single-unit equipment together rather than leaving a
            # spare unit between every device.
            attrs.update(position=unit + 1, face="front")
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
        # Passive positions pair the catalog's front and rear ports by index;
        # names and media (8P8C jack, 110 punchdown) come from the pinned source.
        for position, (front, back) in enumerate(zip(spec.get("front_ports", []), spec.get("rear_ports", [])), 1):
            rear = self.w.add("rear_port", f"{key}/rear/{position}", dict(back, positions=1), {"device": key})
            self.w.add("front_port", f"{key}/front/{position}", dict(front, rear_port_position=1),
                       {"device": key, "rear_port": rear})
        return key

    def rack(self, group, ordinal, location=None):
        location = location or self.equipment_location
        room = self.room_prefix(location)
        key = f"rack/{self.id}/{room}{group}-{ordinal+1:02}"
        if key not in self.racks:
            if self.rack_grid and ordinal >= 64:
                raise DesignError(f"{self.id}: data hall supports 64 rack positions per equipment zone; extend the reviewed layout")
            row, column = divmod(ordinal, CABINETS_PER_ROW) if self.rack_grid else (0, ordinal)
            # facility_id is the room-scoped cabinet code stencilled on the
            # cabinet: room tag ("DH", "MDF", "IDF02", the cage "C12"), row,
            # zone letter and bay. Unique per room, stable because lanes are.
            room_name = self.w.obj(location)["attrs"]["name"]
            tag = room_name.removeprefix("Cage ") if room_name.startswith("Cage ") else "".join(
                word if word.isupper() or word[0].isdigit() else word[0].upper() for word in room_name.split())
            # Asset tags are short and sequential in purchase order: the
            # namespace (at most 20 characters) is the cross-estate separator
            # NetBox's global uniqueness needs, the permanent ledger the number.
            asset_tag = f"{self.w.recipe['namespace'].upper()}-{self.w.reserve('asset-tags', key, 100000) + 1:05}"
            # Enclosed four-post 24U cabinet (APC AR3104) everywhere a room
            # holds rack lanes; a single-CE premises takes the small-room kit.
            # Form factor and width live on the rack type (operations._racks).
            height, _, description = (SMALL_RACK if self.small_kit else
                                      (RACK_U_HEIGHT, "4-post-cabinet", f"{group.title()} equipment cabinet"))
            self.w.add("rack", key, {"name": f"{'N' if group == 'network' else 'C'}{ordinal+1:02}", "u_height": height,
                  "status": "active", "asset_tag": asset_tag,
                  "facility_id": f"{tag}-{row+1:02}-{group[0].upper()}{column+1:02}",
                  "description": description},
                  {"site": self.key, "location": location, "tenant": self.tenant,
                   "role": f"rack-role/{group}"})
            self.racks[key] = True
            self.rack_members[key] = []
            if self.rack_grid:
                # Two bounded cabinet zones share a synthetic data hall. Lane
                # ordinals retain positions during growth; unused lanes are space.
                zone = 0 if group == "network" else 1
                # Rounded so a coordinate is exact in the plan, in the
                # independent checks and after the geometry sidecar's
                # metre-to-centimetre conversion.
                self.w.obj(key)["meta"]["position_m"] = [
                    round(ROOM_ORIGIN_M + ZONE_PITCH_M*zone + CABINET_WIDTH_M*column, 3),
                    round(ROOM_ORIGIN_M + CABINET_ROW_PITCH_M*row, 3), 0]
        return key

    def interface(self, device, name):
        key = f"{device}/if/{name}"
        if key not in self.w.objects:
            raise DesignError(f"Missing hardware interface: {key}")
        return key

    def virtual_interface(self, device, name, network):
        key = f"{device}/if/{name}"
        vlan, _ = self.network(network)
        # Builders key gateway interfaces "VlanN"; the emitted name follows the
        # device's own platform (Junos routes a VLAN on irb.N, EOS/IOS XE on
        # VlanN). The key stays stable so growth and checks address one record.
        self.w.add("interface", key, {"name": svi_name(self.w, device, name), "type": "virtual", "enabled": True, "mode": "access"},
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
                description=f"{self.w.obj(rooms[0])['attrs']['name']} to {self.w.obj(rooms[1])['attrs']['name']} building backbone")
        elif self.rack_grid:
            racks = []
            for end in (a, b):
                node = self.w.obj(end)
                owner = self.w.obj(node["refs"]["device"]) if "device" in node["refs"] else node
                racks.append(owner["refs"].get("rack"))
            if all(racks) and racks[0] != racks[1]:
                points = [self.w.obj(rack)["meta"]["position_m"] for rack in racks]
                self.w.obj(key)["attrs"].update(length=math.ceil(sum(abs(x-y) for x,y in zip(*points)) + 3),
                    description="Inter-rack run with 3 m service slack")
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
        # A site's routing domain may be one VRF for every segment (provider) or
        # a per-role family named with a literal "{role}" placeholder (MSP, whose
        # customer tenants each own their own segment VRFs). Values without the
        # placeholder are returned unchanged.
        if self.routing_domain is not None:
            return self.routing_domain.replace("{role}", role)
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
            self.w.add("vrf", vrf, {"name": f"{self.display} {titleize(role)}", "enforce_unique": True,
                       "description": "Retained Birch site routing context"}, {"tenant": self.tenant})
        # One global site block parents this site's segments in every VRF;
        # repeating it per routing context only multiplied identical rows.
        if f"prefix/{self.id}/reservation" not in self.w.objects:
            self.w.add("prefix", f"prefix/{self.id}/reservation", {"prefix": str(container), "status": "container",
                  "description": f"{self.display} site block"}, {"tenant": self.tenant, "scope_site": self.key})
        # NetBox holds VLAN names unique per VLAN group (unique_group_name), and
        # every VLAN joins its site's group, so the short segment name suffices.
        vlan = self.w.add("vlan", f"vlan/{self.id}/{role}", {"name": titleize(role), "vid": 10 * (index+1),
              "status": "active", "description": naming.segment_purpose(role)}, {"site": self.key, "tenant": self.tenant})
        # `wan` carries no gateway SVI and the conduit holds four, not two:
        # the reservation sentence belongs only to ordinary client segments.
        gateways = "" if role in ("wan", "conduit") else "; .1 and .2 reserved for gateway SVIs"
        self.w.add("prefix", f"prefix/{self.id}/{role}", {"prefix": str(net), "status": "active",
              "description": f"{naming.segment_purpose(role)} at {self.display}{gateways}"},
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
        # No description: the assigned interface and VRF already say what it
        # is. The DNS name is final here only for a primary; operations
        # .finalize rewrites every other one to its interface-qualified form
        # once all primaries in the estate are known.
        dns_name = self.w.obj(owner)["attrs"]["name"] if owner else self.code
        if not primary and node["kind"] == "interface":
            dns_name = f"{dns_label(node['attrs']['name'])}.{dns_name}"
        attrs = dict(address=f"{net.network_address + host}/{net.prefixlen}", status="active",
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
        elif not dc and self.w.recipe["profile"] == "retail-chain":
            cohort = "chain-standard"
        elif not dc and self.w.recipe["profile"] == "university-campus":
            cohort = "campus-standard"
        elif not dc and self.w.recipe["profile"] == "msp":
            cohort = "managed-standard"
        elif not dc and self.w.recipe["profile"] == "manufacturing":
            cohort = "plant-standard"
        elif not dc and self.w.recipe["profile"] == "utility":
            cohort = "substation-standard"
        ages = {"dc-aggregation": range(1460, 1826), "retained-birch": range(2190, 2921),
                "cedar-standard": range(365, 1096), "district-standard": range(365, 1096),
                "health-system-standard": range(365, 1096), "chain-standard": range(365, 1096),
                "campus-standard": range(365, 1096), "managed-standard": range(365, 1096),
                "plant-standard": range(365, 1096), "substation-standard": range(365, 1096)}
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
                             "description": f"{naming.bandwidth(rate)} private WAN on a 1G handoff; carrier {side.upper()}",
                             "comments": f"{naming.COHORT_LABELS[cohort]} order: {reason[0].lower()}{reason[1:]}."},
                            {"provider": f"provider/{side}", "type": "circuit-type/wan", "tenant": self.tenant},
                            {"procurement": {"cohort": cohort, "minimum_commit_mbps": 1000 if dc else floor,
                                             "planned_peak_mbps": peak, "handoff_mbps": 1000,
                                             "selection_reason": reason}})
        for term, target in (("A", self.key), ("Z", f"carrier/{side}")):
            self.w.add("circuit_termination", f"{circuit}/{term}", {"term_side": term, "port_speed": 1000000,
                  "description": "Copper customer handoff" if term == "A" else "Carrier network side"},
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
        # One cabled management port per device: the first mgmt_only port in
        # catalog order (an Opengear's NET2 stays a spare, as in a real install).
        managed = [(key, ports[0]) for key in self.devices
                   if not self.w.obj(key)["refs"].get("primary_ip4")
                   for ports in [[p["name"] for p in self.w.catalog["models"][self.w.obj(key)["meta"]["hardware"]]["interfaces"]
                                  if p.get("mgmt_only")]] if ports]
        spec = self.w.hardware("access")
        access_ports, uplink_ports = spec["access_ports"], spec["uplink_ports"]
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
                    parent_port = self.interface(parents[parent_slot % 2],
                                                 self.w.hardware("leaf")["fabric_ports"][40 + parent_slot//2])
                copper = self.w.obj(parent_port)["attrs"]["type"] == "1000base-t"
                uplink = self.interface(switch, access_ports[-1] if copper else uplink_ports[0])
                self.cable(uplink, parent_port, "cat6" if copper else "smf")
                vlan, _ = self.network("management")
                for p in (uplink, parent_port):
                    self.w.obj(p)["attrs"]["mode"] = "tagged"
                    self.w.obj(p)["refs"]["tagged_vlans"] = [vlan]
                vi = self.virtual_interface(switch, "Vlan10", "management")
                self.address(vi, "management", primary=True, device=switch)
            switch = switches[group]
            source = self.interface(switch, access_ports[port])
            target = self.interface(device, port_name)
            self.cable(source, target)
            vlan, _ = self.network("management")
            for p in (source, target):
                self.w.obj(p)["attrs"]["mode"] = "access"
                self.w.obj(p)["refs"]["untagged_vlan"] = vlan
            self.address(target, "management", primary=True, device=device)

    def power(self):
        self.contract["power_redundancy"] = []
        # A single-CE premises is one branch circuit; every other room keeps
        # separate A and B panels, feeds and PDUs.
        sides = ("a",) if self.small_kit else ("a", "b")
        country = self.w.obj(self.key)["meta"].get("geography", {}).get("country")
        voltage, amperage = FEED_ELECTRICS.get((country, self.small_kit), DEFAULT_FEED)
        pdu_alias = "pdu-120" if self.small_kit and country == "US" else "pdu"
        pdu_spec = self.w.hardware(pdu_alias)
        for location in self.contract["placement"]["equipment_locations"].values():
            room = self.room_prefix(location)
            for side in sides:
                # Panel names are unique per site, so the room prefix and side
                # are enough: the namespaced site stem only truncated to
                # indistinguishable node labels in the visualization layer.
                self.w.add("power_panel", f"panel/{self.id}/{room}{side}",
                           {"name": f"{titleize(room.rstrip('-')) + ' ' if room else ''}"
                                    f"{'Panel' if len(sides) == 1 else 'Supply ' + side.upper()}"},
                           {"site": self.key, "location": location})
        for rack, members in self.rack_members.items():
            location = self.w.obj(rack)["refs"]["location"]
            room = self.room_prefix(location)
            outlets = {}
            for side in sides:
                label = f"pdu-{rack.split('/')[-1]}-{side}"
                pdu = self.device(pdu_alias, label, "pdu", racked=False)
                self.w.obj(pdu)["refs"].update(rack=rack, location=location)
                if pdu_spec["u_height"]:
                    # The 1U horizontal PDU takes the top unit, above the lane.
                    self.w.obj(pdu)["attrs"].update(position=self.w.obj(rack)["attrs"]["u_height"], face="front")
                self.w.obj(pdu)["meta"]["failure_domain"] = f"{self.id}-{room}supply-{side}"
                # NetBox keys a feed on (panel, name) and a panel is already
                # per site, room and side, so the cabinet label and side are
                # enough. Same reason as the panel above: the namespaced site
                # stem truncated to indistinguishable node labels.
                stem = titleize(rack.split('/')[-1].removeprefix(room))
                feed = self.w.add("power_feed", f"feed/{rack}/{side}",
                                 {"name": stem if len(sides) == 1 else f"{stem} {side.upper()}", "status": "active",
                                  "type": "primary" if side == "a" else "redundant", "supply": "ac", "phase": "single-phase",
                                  "voltage": voltage, "amperage": amperage, "max_utilization": 80},
                                 {"power_panel": f"panel/{self.id}/{room}{side}", "rack": rack})
                inlet = f"{pdu}/power/{pdu_spec['power_ports'][0]['name']}"
                self.cable(feed, inlet, "power")
                outlets[side] = []
                for port in pdu_spec["power_outlets"]:
                    outlet = self.w.add("power_outlet", f"{pdu}/outlet/{port['name']}", dict(port), {"device": pdu, "power_port": inlet})
                    outlets[side].append(outlet)
            for device in members:
                # Rack slots persist across growth; emission order need not.
                # One outlet per supply, indexed by the member's mounting unit:
                # with one PDU a member's supplies take adjacent outlets.
                i = self.w.obj(device)["attrs"]["position"] - 1
                ports = self.w.catalog["models"][self.w.obj(device)["meta"]["hardware"]]["power_ports"]
                allowance = PLANNED_WATTS.get(self.w.obj(device)["meta"]["hardware"], 0)
                for j, port in enumerate(ports):
                    side = sides[j % len(sides)]
                    index = i if len(sides) == 2 else len(ports) * i + j
                    if index >= len(outlets[side]):
                        raise DesignError(f"{rack}: {pdu_alias} has no outlet left for {device}; extend the reviewed room kit")
                    inlet = f"{device}/power/{port['name']}"
                    self.w.obj(inlet)["attrs"].update(allocated_draw=allowance // len(ports), maximum_draw=allowance,
                         description="Draw split across the A and B feeds; maximum covers single-feed failover" if len(sides) == 2
                         else "Draw split across both supplies on the one PDU; maximum covers single-supply failover")
                    self.cable(outlets[side][index], inlet, "power")
                if ports:
                    self.w.obj(device)["meta"]["planned_watts"] = allowance
                    self.contract["power_redundancy"].append(dict(device=device, min_distinct_pdus=min(len(sides), len(ports)), planned_watts=allowance))
        self.contract["assumptions"].append(
            "Power checks cover racked infrastructure on one modeled branch circuit; a single-CE premises has no second feed."
            if self.small_kit else
            "Power checks cover racked infrastructure and separate modeled A/B panels; facility independence and endpoint wall power are not claimed.")
        self.contract["assumptions"].append("Inlet draws are authored chassis planning allocations plus connected PD reservations with a 1.25 upstream AC allowance, split in normal operation with full per-device failover allowance on each supply. Endpoint wall power is excluded; no measured or vendor-certified consumption is claimed.")
