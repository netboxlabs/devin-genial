"""Bounded operations records connected to the existing bank inventory.

Fields follow the pinned SDK 1.14/plugin 1.17 and NetBox 4.7 audit. This
authored inventory does not create accounts, issue work orders, or run changes.
"""

from collections import defaultdict

from .model import DesignError
from .operations_context import enrich as operational_context


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
                {"name": f"{ns}{label} {code.upper()} private WAN",
                 "account": f"{ns}-{lineage}-{code}",
                 "description": "Retained Birch WAN procurement account" if suffix else "Commercial account for the estate's WAN purchases"},
                {"provider": key, "owner": owner})
    local_sites = {o["refs"]["circuit"]: o["refs"]["termination"]
                   for o in objects if o["kind"] == "circuit_termination" and o["attrs"]["term_side"] == "A"}
    for circuit in (o for o in objects if o["kind"] == "circuit"):
        sid = local_sites[circuit["key"]].removeprefix("site/")
        lineage = "inherited" if bank and w.design_assignments.get(sid) in {"inherited", "refreshed"} else "estate"
        circuit["refs"]["provider_account"] = accounts[(circuit["refs"]["provider"], lineage)]


def enrich(w):
    """Attach operations examples after all physical sites have been built."""
    ns = w.recipe["namespace"]
    by_kind = defaultdict(list)
    for obj in w.objects.values():
        by_kind[obj["kind"]].append(obj)

    def add(kind, key, attrs, refs=None, **meta):
        return w.add(kind, key, attrs, refs, {"operations": True, **meta})

    anchor = "site/dc-01"
    contract = next(c for c in w.contracts if c["site"] == anchor)
    owner_group = add("owner_group", "owner-group/operations", {"name": f"{ns} Infrastructure teams"})
    owner = add("owner", "owner/operations", {"name": f"{ns} Network operations",
                "description": "Accountable team; no authentication users are created"}, {"group": owner_group})
    w.obj(anchor)["refs"]["owner"] = owner
    tenants = add("tenant_group", "tenant-group/banking", {"name": f"{ns} Banking entities", "slug": f"{ns}-banking"}, {"owner": owner})
    for tenant in by_kind["tenant"]:
        tenant["refs"]["group"] = tenants

    _wan_accounts(w, owner)
    group = add("circuit_group", "circuit-group/dc-01/wan-01", {"name": f"{ns} DC01 WAN pair 01", "slug": f"{ns}-dc01-wan-01",
                "description": "Restoration inventory for the first provider pair; priorities do not configure failover"}, {"tenant": "tenant", "owner": owner})
    for side, priority in (("a", "primary"), ("b", "secondary")):
        add("circuit_group_assignment", f"circuit-group-assignment/dc-01/{side}/1", {"priority": priority},
            {"group": group, "member": f"circuit/dc-01/{side}/1"})

    clusters = add("cluster_group", "cluster-group/services", {"name": f"{ns} Regional services", "slug": f"{ns}-regional-services"}, {"owner": owner})
    for cluster in by_kind["cluster"]:
        cluster["refs"]["group"] = clusters
    vm_type = add("virtual_machine_type", "vm-type/services", {"name": f"{ns} Service VM", "slug": f"{ns}-service-vm",
                  "description": "Common service platform; each VM retains its own explicit resource sizing"},
                  {"default_platform": "platform/services", "owner": owner})
    for vm in by_kind["virtual_machine"]:
        vm["refs"]["virtual_machine_type"] = vm_type
        add("virtual_disk", f"virtual-disk/{vm['key']}/disk0", {"name": "disk0", "size": vm["attrs"]["disk"],
            "description": "Provisioned service volume; matches the VM disk budget in MB, not additional storage"},
            {"virtual_machine": vm["key"], "owner": owner})

    racks = add("rack_group", "rack-group/estate", {"name": f"{ns} Estate cabinets", "slug": f"{ns}-estate-cabinets"}, {"owner": owner})
    rack_types = {}
    for height in sorted({rack["attrs"]["u_height"] for rack in by_kind["rack"]}):
        rack_types[height] = add("rack_type", f"rack-type/{height}u", {"model": f"{ns} Reference {height}U cabinet",
            "slug": f"{ns}-reference-{height}u", "u_height": height, "width": 19, "form_factor": "4-post-cabinet",
            "description": "Reference cabinet dimensions; no vendor product or environmental rating is asserted"},
            {"manufacturer": "manufacturer/Devin Reference Designs", "owner": owner})
    for rack in by_kind["rack"]:
        rack["refs"].update(rack_type=rack_types[rack["attrs"]["u_height"]], group=racks)

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
            "description": "Future WAN edge pair reservation", "comments": "Planning reservation only; no equipment purchase or installation has occurred"},
            {"rack": rack["key"], "user": user, "tenant": "tenant", "owner": owner})

    # Bundle only the two A-side service-host fibers, retaining the B-side
    # physical paths outside this example bundle.
    service_hosts = {"device/dc-01/identity-host-01", "device/dc-01/dns-host-01"}
    bundle = add("cable_bundle", "cable-bundle/dc-01/service-hosts-a", {"name": f"{ns} DC01 service hosts A",
                 "description": "Modeled bundle of identity and DNS A-side host fibers; B-side links are separate"}, {"owner": owner})
    bundled = 0
    for cable in by_kind["cable"]:
        endpoints = [w.obj(key) for key in cable["refs"].values() if isinstance(key, str) and key in w.objects]
        devices = {port["refs"].get("device") for port in endpoints if port["kind"] == "interface"}
        if devices & service_hosts and "device/dc-01/compute-01-leaf-a" in devices:
            cable["refs"]["bundle"] = bundle
            bundled += 1
    if bundled != 2:
        raise DesignError("Operations cable bundle requires the two existing DC01 identity/DNS A-side host fibers")

    choices = add("custom_field_choice_set", "custom-field-choices/operations-tier", {"name": f"{ns} Operations tiers",
                  "extra_choices": ["tier-1:Tier 1", "tier-2:Tier 2"], "order_alphabetically": False}, {"owner": owner})
    field_name = f"{ns.replace('-', '_')}_operations_tier"
    field = add("custom_field", "custom-field/operations-tier", {"name": field_name, "label": "Operations tier", "type": "select",
                "object_types": ["dcim.site"], "required": False, "ui_visible": "always", "ui_editable": "yes"},
                {"choice_set": choices, "owner": owner})
    w.obj(anchor)["attrs"].setdefault("custom_fields", {})[field_name] = {"selection": "tier-1"}
    w.obj(anchor)["meta"].setdefault("requires", []).append(field)
    add("custom_link", "custom-link/site-equipment", {"name": f"{ns} Site equipment", "object_types": ["dcim.site"],
        "enabled": True, "link_text": "{% if object.name.startswith('" + ns + "-') %}Site equipment{% endif %}",
        "link_url": "/dcim/devices/?site_id={{ object.pk }}", "button_class": "default", "new_window": False}, {"owner": owner})
    operational_context(w)
    contract["operations"] = {"site": anchor, "custom_field": field, "rack_reservation": reservation,
                              "reservation_dependency": "bound existing username" if reservation else "reservation_user is unset; RackReservation requires an existing user"}
    contract["assumptions"].append("Operations contacts, ownership, account references and journal history are fictional. No user account is created; a rack reservation requires an explicitly bound existing username.")


def supporting_records(world):
    """Attach operational context to observed infrastructure, without bank anchors."""
    ns = world.recipe["namespace"]
    owner_group = world.add("owner_group", "owner-group/operations", {"name": f"{ns} Infrastructure teams"})
    owner = world.add("owner", "owner/operations", {"name": f"{ns} Infrastructure operations",
        "description": "Accountable infrastructure team"}, {"group": owner_group})
    rir = "rir/private"
    if rir not in world.objects:
        world.add("rir", rir, {"name": f"{ns} Private allocations", "slug": f"{ns}-private", "is_private": True})
    world.add("aggregate", "aggregate/private", {"prefix": world.recipe["address_pool"],
        "description": f"{ns} owned site allocation pool"}, {"rir": rir, "tenant": "tenant"})
    for obj in list(world.objects.values()):
        key = obj["key"]
        if obj["kind"] in {"site", "cluster", "circuit"}:
            obj["refs"]["owner"] = owner
            if obj["kind"] == "site":
                world.add("vlan_group", f"vlan-group/{key}", {"name": obj["attrs"]["name"], "slug": obj["attrs"]["slug"],
                    "description": "Site-local VLAN allocation"}, {"scope_site": key, "tenant": obj["refs"]["tenant"]})
        elif obj["kind"] == "virtual_machine":
            world.add("virtual_disk", f"virtual-disk/{key}/disk0", {"name": "disk0", "size": obj["attrs"]["disk"],
                "description": "Provisioned volume; matches the VM disk budget rather than adding capacity"}, {"virtual_machine": key})
    for obj in world.objects.values():
        if obj["kind"] == "vlan":
            obj["refs"]["group"] = f"vlan-group/{obj['refs']['site']}"
    if world.recipe["profile"] != "provider-backbone":
        _wan_accounts(world, owner)
    operational_context(world)
