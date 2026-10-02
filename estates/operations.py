"""Operations records connected to the finished estate, for every profile.

Fields follow the pinned SDK 1.14/plugin 1.17 and NetBox 4.7 audit. This
authored inventory does not create accounts, issue work orders, or run changes.
Everything here is derived from the graph the profile builders finished:
groups pair real circuits, tags name properties the graph shows, and the
finalize pass only reads what is there (unused ports, primaries, references).
"""

from collections import defaultdict
import re

from . import naming
from .blocks import (CABINET_ROW_PITCH_M, CABINET_WIDTH_M, CABINETS_PER_ROW, ROOM_ORIGIN_M, ZONE_PITCH_M,
                     dns_label)
from .model import DesignError
from .naming import main_scoped_name
from .networking import _physical_peers, address_ranges, ipam_roles
from .operations_context import enrich as operational_context

# Real enclosure per emitted cabinet height (catalog/README.md, rack type).
RACK_TYPES = {24: ("APC", "AR3104", "4-post-cabinet",
                   "NetShelter SX 24U server rack enclosure, 600 mm wide x 1070 mm deep, with sides"),
              # Pinned devicetype-library rack-types/Panduit/R2P26.yaml: the
              # small-room kit's two-post relay rack (blocks.SMALL_RACK).
              13: ("Panduit", "R2P26", "2-post-frame",
                   "2-Post Rack, 13RU, #12-24 Threaded E-Rails, Aluminum, Black")}

# The group every estate tenant joins, per profile: (key, name, description).
# Customer tenants keep the groups their profile builders already give them.
TENANT_GROUPS = {
    "regional-bank": ("banking", "Banking entities", "The bank and the acquired institution it operates"),
    "enterprise-data-center": ("estate", "Enterprise", "The company that owns and runs these data centers"),
    "school-district": ("estate", "School district", "The district that owns and runs this network"),
    "hospital-clinics": ("estate", "Health system", "The health system that owns and runs this network"),
    "provider-backbone": ("operator", "Network operator", "The carrier that owns and runs the backbone"),
    "retail-chain": ("estate", "Retail company", "The retailer that owns and runs this network"),
    "university-campus": ("estate", "University", "The university that owns and runs this campus network"),
    "msp": ("operator", "Service provider", "The managed service provider that operates customer networks"),
    "manufacturing": ("estate", "Manufacturer", "The manufacturer that owns and runs this network"),
    "utility": ("estate", "Electric utility", "The utility that owns and runs this network"),
}
# Site service tiers, chosen from the finished graph (``_site_facts``).
TIERS = ("tier-1:Tier 1 (hub)", "tier-2:Tier 2 (dual-homed)", "tier-3:Tier 3 (single-homed)")
# Device roles whose switches the "Switch platform baseline" context governs
# (estates/automation.py SWITCH_ROLES): unused ports there are shut.
SWITCH_ROLES = {"role/access", "role/leaf"}
# Roles whose inter-device links are backbone/fabric links and carry jumbo MTU.
CORE_ROLES = {"role/spine", "role/leaf", "role/core", "role/provider-edge", "role/distribution"}
JUMBO_MTU = {"juniper-junos": 9192}
JUMBO_MTU_DEFAULT = 9216
# Native interface type -> line rate in kbps, for cabled ports whose rate is
# set by the hardware rather than negotiated down (copper keeps autoneg).
TYPE_SPEED = {"1000base-x-sfp": 1000000, "10gbase-x-sfpp": 10000000, "25gbase-x-sfp28": 25000000,
              "40gbase-x-qsfpp": 40000000, "100gbase-x-qsfp28": 100000000}
# Taxonomy kinds that exist only to be referenced; unreferenced rows are dropped.
# Device types, platforms and makers stay a fixed library (growth, refresh and
# acquisition snapshots must not delete one); only passive cabling types, which
# the recipe's frozen patching choice alone decides, are dropped when unused.
PRUNABLE = ("device_role", "rack_role")


def _owner(w, name):
    group = w.add("owner_group", "owner-group/operations", {"name": main_scoped_name(w.recipe, "Infrastructure teams")})
    return w.add("owner", "owner/operations", {"name": name, "description": "Accountable infrastructure team"},
                 {"group": group})


def _wan_accounts(w, owner):
    """Purchased WAN accounts; provider service accounts have their own policy."""
    ns = w.recipe["namespace"]
    bank = w.recipe["profile"] == "regional-bank"
    objects = list(w.objects.values())
    accounts = {}
    for provider in (o for o in objects if o["kind"] == "provider"):
        key, code = provider["key"], provider["key"].rsplit("/", 1)[-1]
        # Retain the acquired portfolio's directory even after its final site
        # changes tenant; acquisition does not renew the carrier contract.
        for lineage in ("estate", "inherited") if bank else ("estate",):
            suffix = "/inherited" if lineage == "inherited" else ""
            label = " Birch" if lineage == "inherited" else ""
            accounts[(key, lineage)] = w.add("provider_account", f"provider-account/{key}{suffix}",
                {"name": f"Private WAN {code.upper()}{label}",
                 "account": f"{ns}-{lineage}-{code}",
                 "description": "Retained Birch WAN procurement account" if suffix else "WAN circuit billing account"},
                {"provider": key, "owner": owner})
    local_sites = {o["refs"]["circuit"]: o["refs"]["termination"]
                   for o in objects if o["kind"] == "circuit_termination" and o["attrs"]["term_side"] == "A"}
    for circuit in (o for o in objects if o["kind"] == "circuit"):
        sid = local_sites[circuit["key"]].removeprefix("site/")
        lineage = "inherited" if bank and w.design_assignments.get(sid) in {"inherited", "refreshed"} else "estate"
        circuit["refs"]["provider_account"] = accounts[(circuit["refs"]["provider"], lineage)]


def _site_facts(w):
    """(hub sites, dual-homed sites) as the finished graph shows them.

    A hub hosts a cluster or terminates the hub side of a private-WAN service;
    a dual-homed site has active circuits from two different providers.
    """
    hubs, carriers = set(), defaultdict(set)
    circuits = {o["key"]: o for o in w.objects.values() if o["kind"] == "circuit"}
    for obj in w.objects.values():
        if obj["kind"] == "cluster" and obj["refs"].get("scope_site"):
            hubs.add(obj["refs"]["scope_site"])
        elif obj["kind"] == "virtual_circuit_termination" and obj["attrs"].get("role") == "hub":
            hubs.add(w.obj(w.obj(obj["refs"]["interface"])["refs"]["device"])["refs"]["site"])
        elif obj["kind"] == "circuit_termination" and str(obj["refs"].get("termination", "")).startswith("site/"):
            circuit = circuits[obj["refs"]["circuit"]]
            if circuit["attrs"].get("status") == "active":
                carriers[obj["refs"]["termination"]].add(circuit["refs"]["provider"])
    return hubs, {site for site, providers in carriers.items() if len(providers) >= 2}


def _shared(w, owner):
    """Grouping, typing and IPAM context every profile carries."""
    ns, profile = w.recipe["namespace"], w.recipe["profile"]

    def add(kind, key, attrs, refs=None):
        return w.add(kind, key, attrs, refs, {"operations": True})

    kinds = defaultdict(list)
    for obj in list(w.objects.values()):
        kinds[obj["kind"]].append(obj)

    # Tenant groups: every tenant belongs to one. Customer tenants of a
    # provider backbone join "Customers"; groups a builder already set stay.
    slug, name, description = TENANT_GROUPS[profile]
    estate_group = add("tenant_group", f"tenant-group/{slug}", {"name": name, "slug": f"{ns}-{slug}",
                       "description": description}, {"owner": owner})
    customers = None
    for tenant in sorted(kinds["tenant"], key=lambda o: o["key"]):
        if tenant["refs"].get("group"):
            continue
        if profile == "provider-backbone" and tenant["key"] != "tenant":
            customers = customers or add("tenant_group", "tenant-group/customers", {"name": "Customers",
                "slug": f"{ns}-customers", "description": "Private L3 VPN customers of the backbone"}, {"owner": owner})
            tenant["refs"]["group"] = customers
        else:
            tenant["refs"]["group"] = estate_group

    # Circuit groups pair the real A/B circuits the builders keyed as such
    # (circuit/<scope>/a[/n] beside circuit/<scope>/b[/n]); a provider's
    # backbone spans form one group of their own.
    circuits = {o["key"]: o for o in kinds["circuit"]}
    for key in sorted(circuits):
        parts = key.split("/")
        if "a" not in parts[1:]:
            continue
        index = parts.index("a", 1)
        partner = "/".join(parts[:index] + ["b"] + parts[index + 1:])
        if partner not in circuits:
            continue
        scope, number = parts[1:index], parts[index + 1:]
        site = next((naming.site_display(w, f"site/{prefix}{scope[-1]}") for prefix in ("", "pop-")
                     if naming.site_display(w, f"site/{prefix}{scope[-1]}")), None)
        purpose = "WAN" if len(scope) == 1 else naming.titleize(scope[0]).lower()
        label = (f"{site} {purpose} pair" + (f" {int(number[0]):02}" if number and number[0].isdigit() else "") if site
                 else {"noc": "NOC access pair", "transit": "Upstream transit pair"}.get(
                     "/".join(scope), f"{naming.titleize(' '.join(scope))} pair"))
        # No tenant: a site's tenant moves on acquisition, the carrier pair does not.
        group = add("circuit_group", "circuit-group/" + "/".join(scope + number),
                    {"name": label, "slug": f"{ns}-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-"),
                     "description": "Primary and secondary circuits for one attachment"}, {"owner": owner})
        for member, priority in ((key, "primary"), (partner, "secondary")):
            add("circuit_group_assignment", f"{group.replace('circuit-group/', 'circuit-group-assignment/', 1)}/{member.removeprefix('circuit/')}",
                {"priority": priority}, {"group": group, "member": member})
    spans = sorted(key for key, obj in circuits.items() if obj["refs"].get("type") == "circuit-type/backbone")
    if spans:
        group = add("circuit_group", "circuit-group/backbone", {"name": "Backbone spans", "slug": f"{ns}-backbone-spans",
                    "description": "Leased spans that form the backbone between PoPs"}, {"owner": owner, "tenant": "tenant"})
        for member in spans:
            add("circuit_group_assignment", f"circuit-group-assignment/backbone/{member.removeprefix('circuit/')}", {},
                {"group": group, "member": member})

    # Clusters, VM types (one per VM role) and their discrete disks.
    if kinds["cluster"]:
        clusters = add("cluster_group", "cluster-group/services", {"name": "Regional services" if profile == "regional-bank"
                       else "Shared services", "slug": f"{ns}-regional-services"}, {"owner": owner})
        for cluster in kinds["cluster"]:
            cluster["refs"]["group"] = clusters
    for vm in kinds["virtual_machine"]:
        role = vm["refs"]["role"].removeprefix("role/")
        vm_type = f"vm-type/{role}"
        if vm_type not in w.objects:
            label = {"application": "Application VM", "database": "Database VM", "backup-service": "Backup VM"}.get(
                role, f"{naming.titleize(role)} VM")
            add("virtual_machine_type", vm_type, {"name": label, "slug": f"{ns}-{role}-vm",
                "description": f"Standard Linux {label.removesuffix(' VM').lower()} service VM; sized per workload"},
                {"default_platform": vm["refs"]["platform"], "owner": owner})
        vm["refs"]["virtual_machine_type"] = vm_type
        add("virtual_disk", f"virtual-disk/{vm['key']}/disk0", {"name": "disk0", "size": vm["attrs"]["disk"],
            "description": "Primary VM volume"}, {"virtual_machine": vm["key"], "owner": owner})

    # Rack groups by the kind of room the cabinet stands in; rack types by height.
    kind_of = {contract["site"]: contract["kind"] for contract in w.contracts}
    labels = {"dc": ("data-center", "Data center cabinets"), "pop": ("pop", "PoP cabinets")}
    rack_types = {}
    for rack in sorted(kinds["rack"], key=lambda o: o["key"]):
        slug, label = labels.get(kind_of.get(rack["refs"]["site"]), ("closet", "Wiring closet cabinets"))
        group = f"rack-group/{slug}"
        if group not in w.objects:
            add("rack_group", group, {"name": label, "slug": f"{ns}-{slug}-cabinets"}, {"owner": owner})
        height = rack["attrs"]["u_height"]
        if height not in RACK_TYPES:
            raise DesignError(f"No catalog rack type for a {height}U cabinet; add one before changing rack height")
        if height not in rack_types:
            # APC NetShelter SX 24U AR3104, 600 x 1070 mm: the footprint the
            # cabinet grid (blocks.CABINET_WIDTH_M) is authored around. The pinned
            # library has no 24U SX type, so catalog/README.md cites APC's page.
            maker, model, form, description = RACK_TYPES[height]
            if f"manufacturer/{maker}" not in w.objects:
                add("manufacturer", f"manufacturer/{maker}", {"name": maker, "slug": maker.lower()})
            rack_types[height] = add("rack_type", f"rack-type/{height}u", {"model": model,
                "slug": f"{ns}-{maker.lower()}-{model.lower()}", "u_height": height, "width": 19, "form_factor": form,
                "description": description}, {"manufacturer": f"manufacturer/{maker}", "owner": owner})
        rack["refs"].update(rack_type=rack_types[height], group=group)

    # A site service tier read from the graph, and a shortcut to its equipment.
    hubs, dual = _site_facts(w)
    choices = add("custom_field_choice_set", "custom-field-choices/operations-tier", {"name": main_scoped_name(w.recipe, "Operations tiers"),
                  "extra_choices": list(TIERS), "order_alphabetically": False}, {"owner": owner})
    # Custom-field names are matched EXACTLY by estates/branch.py's retirement
    # (CUSTOM_FIELD_NAMES): adding a field here means extending that tuple too.
    field_name = f"{ns.replace('-', '_')}_operations_tier"
    field = add("custom_field", "custom-field/operations-tier", {"name": field_name, "label": "Service tier", "type": "select",
                "object_types": ["dcim.site"], "required": False, "ui_visible": "always", "ui_editable": "yes",
                "description": "Tier 1 hosts shared services; tier 2 has two carriers; tier 3 has one"},
                {"choice_set": choices, "owner": owner})
    for site in kinds["site"]:
        tier = "tier-1" if site["key"] in hubs else "tier-2" if site["key"] in dual else "tier-3"
        site["attrs"].setdefault("custom_fields", {})[field_name] = {"selection": tier}
        site["meta"].setdefault("requires", []).append(field)
    add("custom_link", "custom-link/site-equipment", {"name": main_scoped_name(w.recipe, "Site equipment"), "object_types": ["dcim.site"],
        "enabled": True, "link_text": "{% if object.name.startswith('" + ns + "-') %}Site equipment{% endif %}",
        "link_url": "/dcim/devices/?site_id={{ object.pk }}", "button_class": "default", "new_window": False}, {"owner": owner})

    address_ranges(w)
    ipam_roles(w)
    operational_context(w)
    w.finishers.append(finalize)
    return field


def enrich(w):
    """The bank's operations records: shared context plus its DC01 examples."""
    ns = w.recipe["namespace"]
    by_kind = defaultdict(list)
    for obj in w.objects.values():
        by_kind[obj["kind"]].append(obj)

    def add(kind, key, attrs, refs=None, **meta):
        return w.add(kind, key, attrs, refs, {"operations": True, **meta})

    anchor = "site/dc-01"
    contract = next(c for c in w.contracts if c["site"] == anchor)
    owner = _owner(w, main_scoped_name(w.recipe, "Network operations"))
    w.obj(anchor)["refs"]["owner"] = owner
    _wan_accounts(w, owner)

    # A real existing account is a binding dependency, never a generated user.
    reservation = None
    if username := w.recipe.get("reservation_user"):
        user = add("user", "external/user/reservation", {"username": username}, external=True)
        rack = w.obj("rack/dc-01/network-01")
        units = [rack["attrs"]["u_height"] - 1, rack["attrs"]["u_height"]]
        for device in by_kind["device"]:
            if device["refs"].get("rack") == rack["key"] and device["attrs"].get("position") is not None:
                start = device["attrs"]["position"]
                height = w.obj(device["refs"]["device_type"])["attrs"]["u_height"]
                if any(start <= unit < start + height for unit in units):
                    raise DesignError("DC01 future WAN reservation overlaps installed equipment; choose a new reviewed reservation")
        reservation = add("rack_reservation", "rack-reservation/dc-01/future-wan", {"units": units, "status": "pending",
            "description": "Future WAN edge pair reservation"},
            {"rack": rack["key"], "user": user, "tenant": "tenant", "owner": owner})

    # Bundle only the two A-side service-host fibers, retaining the B-side
    # physical paths outside this example bundle.
    service_hosts = {"device/dc-01/identity-host-01", "device/dc-01/dns-host-01"}
    bundle = add("cable_bundle", "cable-bundle/dc-01/service-hosts-a", {"name": "DC01 service hosts A",
                 "description": "Identity and DNS A-side host fibers; B-side links run separately"}, {"owner": owner})
    bundled = 0
    for cable in by_kind["cable"]:
        endpoints = [w.obj(key) for key in cable["refs"].values() if isinstance(key, str) and key in w.objects]
        devices = {port["refs"].get("device") for port in endpoints if port["kind"] == "interface"}
        if devices & service_hosts and "device/dc-01/compute-01-leaf-a" in devices:
            cable["refs"]["bundle"] = bundle
            bundled += 1
    if bundled != 2:
        raise DesignError("Operations cable bundle requires the two existing DC01 identity/DNS A-side host fibers")

    field = _shared(w, owner)
    contract["operations"] = {"site": anchor, "custom_field": field, "rack_reservation": reservation,
                              "reservation_dependency": "bound existing username" if reservation else "reservation_user is unset; RackReservation requires an existing user"}
    contract["assumptions"].append("Operations contacts, ownership, account references and journal history are fictional. No user account is created; a rack reservation requires an explicitly bound existing username.")


def supporting_records(world):
    """Attach operational context to observed infrastructure, without bank anchors."""
    ns = world.recipe["namespace"]
    owner = _owner(world, main_scoped_name(world.recipe, "Infrastructure operations"))
    rir = "rir/private"
    if rir not in world.objects:
        world.add("rir", rir, {"name": "Private allocations", "slug": f"{ns}-private", "is_private": True})
    # Aggregates carry no name; the description is the label NetBox renders.
    estate = (world.objects.get("tenant") or {}).get("attrs", {}).get("name") or world.recipe["name"]
    world.add("aggregate", "aggregate/private", {"prefix": world.recipe["address_pool"],
        "description": f"{estate} owned site allocation pool"}, {"rir": rir, "tenant": "tenant"})
    for obj in list(world.objects.values()):
        key = obj["key"]
        if obj["kind"] in {"site", "cluster", "circuit"}:
            obj["refs"]["owner"] = owner
            if obj["kind"] == "site":
                world.add("vlan_group", f"vlan-group/{key}", {"name": obj["attrs"]["name"], "slug": obj["attrs"]["slug"],
                    "description": "Site-local VLAN allocation"}, {"scope_site": key, "tenant": obj["refs"]["tenant"]})
    for obj in world.objects.values():
        if obj["kind"] == "vlan":
            obj["refs"]["group"] = f"vlan-group/{obj['refs']['site']}"
    if world.recipe["profile"] != "provider-backbone":
        _wan_accounts(world, owner)
    _shared(world, owner)


# --- finalize: graph-wide passes over the finished estate -------------------

def finalize(w):
    """Runs from World.finish(), after every builder (BGP, lab) has finished."""
    _planned_cabinets(w)
    _tags(w)
    _ports(w)
    _addresses(w)
    _prune(w)


def _planned_cabinets(w):
    """One planned cabinet at the next compute position of every gridded room.

    The cabinet grid is append-only (blocks.Site.rack): the next compute lane
    always lands on this position, so the estate shows where it grows. The
    key is the one that lane will take, so growth turns this same record
    active instead of adding a second cabinet. Nothing is installed or powered.
    """
    zones = defaultdict(list)
    for obj in w.objects.values():
        if (obj["kind"] == "rack" and obj["refs"].get("role") == "rack-role/compute"
                and isinstance(obj["meta"].get("position_m"), list)):
            zones[obj["refs"]["location"]].append(obj)
    for location, racks in sorted(zones.items()):
        numbered = {int(rack["key"].rsplit("-", 1)[1]): rack for rack in racks}
        ordinal = max(numbered)  # zero-based index of the next lane
        template = numbered[ordinal]
        if ordinal >= 64 or any(rack["attrs"].get("status") != "active" for rack in racks):
            continue
        stem, old = template["key"].rsplit("-", 1)[0], f"{ordinal:02}"
        number = f"{ordinal + 1:02}"
        attrs = dict(template["attrs"], status="planned",
                     name=template["attrs"]["name"].removesuffix(old) + number,
                     facility_id=template["attrs"]["facility_id"].removesuffix(old) + number,
                     description="Next compute cabinet position; not yet installed or powered")
        if template["attrs"]["asset_tag"].endswith(f"-{old}"):
            attrs["asset_tag"] = template["attrs"]["asset_tag"].removesuffix(old) + number
        else:
            del attrs["asset_tag"]  # a digest-shortened tag cannot be extended honestly
        row, column = divmod(ordinal, CABINETS_PER_ROW)
        w.add("rack", f"{stem}-{number}", attrs, dict(template["refs"]),
              {"position_m": [round(ROOM_ORIGIN_M + ZONE_PITCH_M + CABINET_WIDTH_M * column, 3),
                              round(ROOM_ORIGIN_M + CABINET_ROW_PITCH_M * row, 3), 0], "planned": True})


def _tags(w):
    """Replace blanket tagging with the properties the graph actually shows."""
    objects = w.objects
    peers = _physical_peers(objects)
    hubs, dual = _site_facts(w)
    roles = {}
    for obj in objects.values():
        if obj["kind"] == "vlan":
            try:
                roles[obj["key"]] = naming.segment_role(obj["key"].rsplit("/", 1)[-1])
            except ValueError:
                pass
    zone_tags = {"payments": "pci-scope", "clinical": "clinical", "ot": "ot-zone"}
    applied = defaultdict(set)
    for site in hubs:
        applied[site].add("hub-site")
    for site in dual:
        applied[site].add("dual-homed")
    for sid, design in w.design_assignments.items():
        if design in {"inherited", "refreshed"} and f"site/{sid}" in objects:
            applied[f"site/{sid}"].add("acquired")
    devices = {key: obj for key, obj in objects.items() if obj["kind"] == "device"}
    for key, device in devices.items():
        if device["attrs"]["name"].startswith("birch-"):
            applied[key].add("acquired")
        role = device["refs"].get("role")
        if role == "role/customer-edge" or (role == "role/wan-edge" and
                                             objects.get(device["refs"].get("tenant"), {}).get("refs", {}).get("group") == "tenant-group/customers"):
            applied[key].add("managed-ce")
    vrf_role = {}
    for key, role in roles.items():
        if role in zone_tags:
            applied[key].add(zone_tags[role])
    for obj in objects.values():
        if obj["kind"] == "prefix" and obj["refs"].get("vlan") in roles:
            vrf_role[obj["refs"]["vrf"]] = roles[obj["refs"]["vlan"]]
            if roles[obj["refs"]["vlan"]] in zone_tags:
                applied[obj["key"]].add(zone_tags[roles[obj["refs"]["vlan"]]])
    for obj in objects.values():
        refs = obj["refs"]
        if obj["kind"] == "interface" and refs.get("device") in devices:
            for vlan in [refs.get("untagged_vlan")] + list(refs.get("tagged_vlans") or []):
                if roles.get(vlan) in zone_tags:
                    applied[refs["device"]].add(zone_tags[roles[vlan]])
        elif obj["kind"] == "ip_address":
            port = objects.get(refs.get("assigned_object"), {})
            if port.get("kind") == "interface" and vrf_role.get(refs.get("vrf")) in zone_tags:
                applied[port["refs"]["device"]].add(zone_tags[vrf_role[refs["vrf"]]])
        elif obj["kind"] == "bgp_session" and refs.get("peer_group") == "bgp-peer-group/ibgp-core":
            # Clients peer to the reflectors, so a remote iBGP loopback is a reflector's.
            remote = objects.get(refs.get("remote_address"), {})
            port = objects.get(remote.get("refs", {}).get("assigned_object"), {})
            if port.get("kind") == "interface":
                applied[port["refs"]["device"]].add("route-reflector")
    for port, peer in peers.items():
        term = objects.get(peer, {})
        if (term.get("kind") == "circuit_termination"
                and objects[term["refs"]["circuit"]]["refs"].get("type") == "circuit-type/transit"):
            applied[objects[port]["refs"]["device"]].add("transit-edge")
    workloads = defaultdict(set)
    for obj in objects.values():
        if obj["kind"] == "virtual_machine":
            workloads[obj["key"].split("/")[2]].add(obj["refs"]["cluster"])
    for obj in objects.values():
        if obj["kind"] == "virtual_machine" and len(workloads[obj["key"].split("/")[2]]) > 1:
            applied[obj["key"]].add("multi-site")
    # A tag that lands on every candidate of its kinds tells a reader nothing
    # (the old blanket "Managed" problem), so only discriminating tags ship.
    population = defaultdict(int)
    for obj in objects.values():
        population[obj["kind"]] += 1
    tagged = defaultdict(lambda: defaultdict(int))
    for key, slugs in applied.items():
        for slug in slugs:
            tagged[slug][objects[key]["kind"]] += 1
    used = {slug for slug, counts in tagged.items()
            if any(counts[kind] < population[kind] for kind in naming.TAGS[slug][2])}
    for slugs in applied.values():
        slugs &= used
    ns = w.recipe["namespace"]
    for slug, (name, color, kinds, description) in naming.TAGS.items():
        if slug in used:
            w.add("tag", f"tag/{slug}", {"name": name, "slug": f"{ns}-{slug}", "color": color,
                  "description": description})
    for obj in objects.values():
        obj["refs"].pop("tags", None)
    for key, slugs in applied.items():
        if slugs:
            objects[key]["refs"]["tags"] = [f"tag/{slug}" for slug in naming.TAGS if slug in slugs]


def _ports(w):
    """Shut unused switch ports, and set fabric MTU and fixed optical rates."""
    objects = w.objects
    cabled = {}
    for obj in objects.values():
        if obj["kind"] == "cable":
            cabled[obj["refs"]["a"]], cabled[obj["refs"]["b"]] = obj["refs"]["b"], obj["refs"]["a"]
    for obj in objects.values():
        if obj["kind"] != "interface" or not obj["meta"].get("hardware_port"):
            continue
        device = objects[obj["refs"]["device"]]
        attrs = obj["attrs"]
        peer = objects.get(cabled.get(obj["key"]), {})
        if obj["key"] not in cabled:
            # The switch baseline context says unused ports are disabled; the
            # inventory now agrees. A later cable re-enables the port.
            if (device["refs"].get("role") in SWITCH_ROLES and not attrs.get("mgmt_only")
                    and not str(attrs.get("type", "")).startswith("ieee802.11")):
                attrs["enabled"] = False
            continue
        if peer.get("kind") == "interface":
            # Only a like-for-like optical link is fixed at the cage rate.
            if attrs.get("type") in TYPE_SPEED and peer["attrs"].get("type") == attrs["type"] and "speed" not in attrs:
                attrs["speed"] = TYPE_SPEED[attrs["type"]]
            core = objects[peer["refs"]["device"]]["refs"].get("role") in CORE_ROLES
        else:
            # A leased backbone span between two routers is a core link too.
            circuit = objects.get(peer.get("refs", {}).get("circuit"), {})
            core = peer.get("kind") == "circuit_termination" and circuit.get("refs", {}).get("type") == "circuit-type/backbone"
        if (core and device["refs"].get("role") in CORE_ROLES
                and attrs.get("type") in TYPE_SPEED and not attrs.get("mgmt_only")):
            platform = (device["refs"].get("platform") or "").removeprefix("platform/")
            attrs["mtu"] = JUMBO_MTU.get(platform, JUMBO_MTU_DEFAULT)


def _addresses(w):
    """DNS names on primaries and loopbacks only; interface-qualified elsewhere."""
    objects, domain = w.objects, f"{w.recipe['namespace']}.example"
    primaries = {obj.get("refs", {}).get(field) for obj in objects.values()
                 if obj["kind"] in {"device", "virtual_machine"} for field in ("primary_ip4", "primary_ip6")}
    for obj in objects.values():
        if obj["kind"] != "ip_address":
            continue
        port = objects.get(obj["refs"].get("assigned_object"), {})
        if port.get("kind") not in {"interface", "vm_interface"}:
            continue
        owner = objects[port["refs"].get("device") or port["refs"]["virtual_machine"]]["attrs"]["name"]
        loopback = port["attrs"].get("type") == "virtual" and re.match(r"(lo|loopback)\d", port["attrs"]["name"], re.I)
        if loopback:
            obj["attrs"]["role"] = "loopback"
        if obj["key"] in primaries or port["kind"] == "vm_interface":
            obj["attrs"]["dns_name"] = f"{owner}.{domain}"
        else:
            obj["attrs"]["dns_name"] = f"{dns_label(port['attrs']['name'])}.{owner}.{domain}"


def _prune(w):
    """Drop taxonomy nothing references, so lists show only what the estate uses."""
    # A maker stays while anything references it now, so only the maker of a
    # dropped passive type can fall away with it.
    kept_makers = {target for obj in w.objects.values() if obj["kind"] != "device_type"
                   or not w.catalog["models"][obj["key"].removeprefix("hardware/")].get("front_ports")
                   for value in obj["refs"].values()
                   for target in (value if isinstance(value, list) else [value]) if isinstance(target, str)}
    while True:
        referenced = {target for obj in w.objects.values() for value in obj["refs"].values()
                      for target in (value if isinstance(value, list) else [value]) if isinstance(target, str)}
        unused = [key for key, obj in w.objects.items() if key not in referenced and (
            obj["kind"] in PRUNABLE or obj["kind"] == "device_type"
            and w.catalog["models"][key.removeprefix("hardware/")].get("front_ports")
            or obj["kind"] == "manufacturer" and key not in kept_makers)]
        if not unused:
            return
        for key in unused:
            del w.objects[key]
