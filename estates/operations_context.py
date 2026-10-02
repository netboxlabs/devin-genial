"""Scoped service desks and bounded, immutable design records for every profile.

Journal comments are matching identity. Never include growing inventory totals,
current utilization, or an assertion that a change or recovery test was executed.
"""

from collections import defaultdict
from datetime import date, timedelta

from .automation import enrich as automation_records
from .model import DesignError, digest
from .naming import rate_kbps, titleize
from .wireless_context import enrich as wireless_context

# Real metro area codes with the 555-0100..0199 block the North American
# Numbering Plan reserves for fictional use, so a directory reads like one
# without ever dialling a real subscriber.
AREA_CODES = {"Chicago": "312", "Detroit": "313", "Cleveland": "216", "Milwaukee": "414"}


def enrich(world):
    ns = world.recipe["namespace"]
    objects = list(world.objects.values())
    kinds = defaultdict(list)
    for obj in objects:
        kinds[obj["kind"]].append(obj)

    def add(kind, key, attrs, refs=None):
        return world.add(kind, key, attrs, refs, {"operations": True})

    roles = {"operations": "Technical escalation", "facilities": "Facilities access",
             "carrier": "Carrier escalation", "service": "Service support"}
    groups = {"operations": "Operations desk", "facilities": "Site facilities",
              "carrier": "Carrier support", "service": "Service teams"}
    if world.recipe["profile"] == "hospital-clinics":
        roles["biomedical"] = "Biomedical support"
        groups["biomedical"] = "Biomedical engineering"
    for key, name in roles.items():
        add("contact_role", f"contact-role/{key}", {"name": name, "slug": f"{ns}-{name.lower().replace(' ', '-')}"})
        # ContactGroup keeps the namespace in its display name: its canonical
        # slug is derived from that name and is deliberately omitted on the
        # wire so the pinned plugin's auto-slug matcher can resolve it, so a
        # clean name would silently change the matching identity.
        name = f"{ns} {groups[key]}"
        add("contact_group", f"contact-group/{key}", {"name": name, "slug": name.lower().replace(" ", "-")})

    city = {obj["key"]: obj["meta"]["geography"]["city"] for obj in kinds["site"]}
    # Desks that answer for more than one site (tenant NOC, carrier, service
    # teams) sit in the estate's home metro: its first permanently allocated
    # site, which growth, acquisition and seed-free input order never move.
    first = min(city, key=lambda site: (world.allocations.get(site.removeprefix("site/"), 1 << 62), site))

    def contact(key, name, role, mailbox, description, metro=None):
        area = AREA_CODES[metro or city[first]]
        # ponytail: a stable hash of the contact key picks one of only one
        # hundred fictional lines, so large directories share some numbers the
        # way hunt groups do; a per-area ledger would make them unique.
        line = int(digest(["contact-phone", key]), 16) % 100
        return add("contact", key, {"name": name, "title": roles[role], "phone": f"+1 {area}-555-{100 + line:04}",
                   "email": f"{mailbox}@{ns}.example", "description": description},
                   {"groups": [f"contact-group/{role}"]})

    def assign(target, contact_key, role, suffix="", priority="primary"):
        # Where an object has several desks the technical desk answers first,
        # the local or commercial desk second and a specialist third.
        add("contact_assignment", f"contact-assignment/{target}{suffix}", {"priority": priority},
            {"object": target, "contact": contact_key, "role": f"contact-role/{role}"})

    tenant_desks = {}
    # Keep a directory entry even after the last independent site is acquired:
    # upserts cannot retire the old contact, and other sites must not change.
    for obj in kinds["tenant"]:
        tenant = obj["key"]
        suffix = "" if tenant == "tenant" else f"/{tenant}"
        label = "" if tenant == "tenant" else f" {tenant.removeprefix('tenant/')}"
        mailbox = "noc" if tenant == "tenant" else f"{tenant.removeprefix('tenant/')}.noc"
        # Contacts are branch-scoped and their Diode identity is the name, so
        # the label names the tenant they answer for, not the namespace.
        tenant_desks[tenant] = contact(f"contact/operations{suffix}", f"{obj['attrs']['name']} NOC duty desk", "operations", mailbox,
            f"Network triage and technical escalation for {obj['attrs']['name']}; coordinates site, platform and carrier specialists.")
    infrastructure_roles = {f"role/{role}" for role in (
        "wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
        "ap", "console-server", "stack", "laboratory", "provider-edge", "customer-edge")}
    for obj in objects:
        if (obj["kind"] in {"site", "cluster", "circuit"}
                or (obj["kind"] == "device" and obj["refs"].get("role") in infrastructure_roles)
                or (world.recipe["profile"] == "provider-backbone" and obj["kind"] == "virtual_circuit")):
            assign(obj["key"], tenant_desks[obj["refs"]["tenant"]], "operations")

    biomedical_desks = {}
    for obj in kinds["device"] if world.recipe["profile"] == "hospital-clinics" else []:
        if obj["refs"].get("role") not in {"role/medical-device", "role/imaging-device"}:
            continue
        site = obj["refs"]["site"]
        if site not in biomedical_desks:
            name = world.obj(site)["attrs"]["name"]
            biomedical_desks[site] = contact(f"contact/biomedical/{site}", f"{name} biomedical desk", "biomedical",
                f"{site.removeprefix('site/')}.biomedical",
                f"Medical endpoint inventory and maintenance coordination at {name}; network incidents go to the site technical desk.",
                city[site])
        assign(obj["key"], biomedical_desks[site], "biomedical")

    def dated(target, event, anchor, minimum, spread):
        try:
            return (date.fromisoformat(anchor) - timedelta(days=world.choose(target, f"journal-{event}", range(minimum, minimum + spread)))).isoformat()
        except OverflowError as exc:
            raise DesignError(f"{target}: as_of is too early for the authored operations chronology") from exc

    def journal(target, event, when, title, body, kind="info"):
        add("journal_entry", f"journal/{target}/{event}", {"kind": kind, "comments": f"{when} — {title}\n{body}"}, {"assigned_object": target})

    as_of = world.recipe["as_of"]
    for site in kinds["site"]:
        key, attrs = site["key"], site["attrs"]
        desk = contact(f"contact/{key}", f"{attrs['name']} facilities desk", "facilities", f"{key.removeprefix('site/')}.facilities",
            f"Equipment-room access, cabinet visits and planned power-work coordination at {attrs['name']}.", city[key])
        assign(key, desk, "facilities", "/facilities", "secondary")
        if key in biomedical_desks:
            assign(key, biomedical_desks[key], "biomedical", "/biomedical", "tertiary")
        journal(key, "access-plan", dated(key, "access-plan", as_of, 60, 31), "Site access",
            f"Equipment-room visits are booked through {world.obj(desk)['attrs']['name']}; "
            "give two working days' notice and flag any planned power work.")

    provider_desks = {}
    terms = {obj["refs"]["circuit"]: obj for obj in kinds["circuit_termination"] if obj["attrs"]["term_side"] == "A"}
    far_terms = {obj["refs"]["circuit"]: obj for obj in kinds["circuit_termination"] if obj["attrs"]["term_side"] == "Z"}
    for circuit in kinds["circuit"]:
        key, attrs, refs = circuit["key"], circuit["attrs"], circuit["refs"]
        provider = refs["provider"]
        name = world.obj(provider)["attrs"]["name"]
        if provider not in provider_desks:
            provider_desks[provider] = contact(f"contact/{provider}", f"{name} support desk", "carrier", f"carrier-{provider.removeprefix('provider/')}.support",
                (f"Capacity and handoff coordination for {name}. Tenant technical desks handle local troubleshooting."
                 if world.recipe["profile"] == "provider-backbone" else
                 f"Circuit identifiers, contracted capacity and handoff coordination for {name}; customer-side troubleshooting stays with the tenant technical desk."))
        assign(key, provider_desks[provider], "carrier", "/carrier", "secondary")
        term = terms[key]
        site_name = world.obj(term["refs"]["termination"])["attrs"]["name"]
        journal(key, "capacity-request", dated(key, "capacity-request", attrs["install_date"], 30, 31), "Order placed",
            f"Ordered {rate_kbps(attrs['commit_rate'])} from {name}; quote {attrs['cid']} on every call to the carrier.")
        if world.recipe["profile"] == "provider-backbone":
            far = far_terms[key]
            far_target = world.obj(far["refs"]["termination"])
            far_name = far_target["attrs"]["name"]
            if far_target["kind"] == "provider_network":
                body = (f"Circuit: {attrs['cid']}\nA termination: {site_name}\nZ network boundary: {far_name}\n"
                        f"A handoff: {rate_kbps(term['attrs']['port_speed'])}\nRecorded service date: {attrs['install_date']}\n"
                        "Remote side: upstream carrier network.\n"
                        "Use the A termination to coordinate the local handoff; the Z record identifies an external network boundary.")
            else:
                body = f"Circuit: {attrs['cid']}\nA termination: {site_name}\nZ termination: {far_name}\nA handoff: {rate_kbps(term['attrs']['port_speed'])}\nZ handoff: {rate_kbps(far['attrs']['port_speed'])}\nRecorded service date: {attrs['install_date']}\nUse both termination records to coordinate the local handoffs."
            journal(key, "handoff-plan", attrs["install_date"], "Circuit handoff plan", body)
        else:
            journal(key, "handoff-plan", attrs["install_date"], "In service",
                f"{name} handed the circuit over at {site_name} on a {rate_kbps(term['attrs']['port_speed'])} port.",
                "success")

    service_desks, anchors = {}, {}
    for vm in sorted(kinds["virtual_machine"], key=lambda obj: obj["key"]):
        key, refs = vm["key"], vm["refs"]
        workload = key.split("/")[2]
        scope = (refs["tenant"], workload)
        if scope not in service_desks:
            label = "" if refs["tenant"] == "tenant" else f" {refs['tenant'].removeprefix('tenant/')}"
            mailbox = f"{label.strip()}." if label else ""
            # The estate tenant now carries the recipe's own display name, which
            # the recipe may take up to its full length; clip it in prose so the
            # description stays inside the native 200-character limit.
            tenant_label = world.obj(refs["tenant"])["attrs"]["name"][:40].rstrip()
            service = titleize(workload)
            service_desks[scope] = contact(f"contact/service/{refs['tenant']}/{workload}", f"{tenant_label} {service} service desk", "service", f"{mailbox}{workload}.service",
                f"Resource sizing and listener configuration for {service} within {tenant_label}; coordinate host incidents with the cluster technical desk.")
        assign(key, service_desks[scope], "service")
        assign(key, tenant_desks[refs["tenant"]], "operations", "/operations", "secondary")
        anchors.setdefault((refs["cluster"], workload), vm)
    # ponytail: one record per workload/site, not per replica; append-only VM
    # ordinals keep the anchor stable. A future retirement workflow needs review.
    for vm in anchors.values():
        key, refs = vm["key"], vm["refs"]
        journal(key, "resource-plan", dated(key, "resource-plan", as_of, 120, 31), "First instance placed",
            f"Placed on {world.obj(refs['device'])['attrs']['name']}; later replicas follow the same sizing.", "success")

    # Permanent U allocation makes this local selection stable when a new
    # workload sorts before existing workloads. New racks receive new stories.
    equipment_anchors, supplies, interfaces = {}, defaultdict(list), defaultdict(dict)
    optical_cages = {}
    for model in world.catalog["models"].values():
        optical_cages[(model["manufacturer"], model["model"])] = next(
            (p["name"] for p in model["interfaces"]
             if p["type"] in {"1000base-x-sfp", "10gbase-x-sfpp", "25gbase-x-sfp28",
                              "100gbase-x-qsfp28"}), None)
    optical_ports = {c["refs"][side] for c in kinds["cable"] if c["attrs"].get("type") in {"smf", "aoc"}
                     for side in ("a", "b")}
    for port in kinds["interface"]:
        interfaces[port["refs"]["device"]][port["attrs"]["name"]] = port
    for port in kinds["power_port"]:
        if port["refs"].get("module"):
            supplies[port["refs"]["device"]].append(port)
    for device in kinds["device"]:
        refs, attrs = device["refs"], device["attrs"]
        rack, position = refs.get("rack"), attrs.get("position")
        if refs.get("role") not in infrastructure_roles or not rack or position is None:
            continue
        old = equipment_anchors.get(rack)
        if old is None or (position, device["key"]) < (old["attrs"]["position"], old["key"]):
            equipment_anchors[rack] = device
    for rack_key, device in sorted(equipment_anchors.items()):
        key, refs, attrs = device["key"], device["refs"], device["attrs"]
        rack, room, site = (world.obj(refs[field])["attrs"]["name"] for field in ("rack", "location", "site"))
        model = world.obj(refs["device_type"])["attrs"]["model"]
        access = world.obj(world.obj(refs["primary_ip4"])["refs"]["assigned_object"])["attrs"]["name"]
        journal(key, "equipment-record", dated(key, "equipment-record", as_of, 100, 20), "Installed",
            f"{model} serial {attrs['serial']} racked in {room}, cabinet {rack} at U{attrs['position']}; "
            f"managed through {access}.", "success")
        if supplies[key]:
            port = min(supplies[key], key=lambda obj: obj["key"])
            module = world.obj(port["refs"]["module"])
            bay = world.obj(module["refs"]["module_bay"])["attrs"]["name"]
            model = world.obj(module["refs"]["module_type"])["attrs"]["model"]
            journal(key, "psu-replacement-plan", dated(key, "psu-replacement-plan", as_of, 1, 19), "Keep a spare PSU",
                f"Confirm a like-for-like {model} is on hand for {bay} (installed serial "
                f"{module['attrs']['serial']}) before the next maintenance window.", "warning")
        dtype = world.obj(refs["device_type"])
        manufacturer = world.obj(dtype["refs"]["manufacturer"])["attrs"]["name"]
        # Select a fixed catalog cage before considering occupancy. Later ports
        # becoming occupied cannot replace an earlier immutable journal subject.
        cage = optical_cages.get((manufacturer, dtype["attrs"]["model"]))
        port = interfaces[key].get(cage)
        if port and port["key"] in optical_ports:
            module = world.obj(port["refs"]["module"])
            module_type = world.obj(module["refs"]["module_type"])
            maker = world.obj(module_type["refs"]["manufacturer"])["attrs"]["name"]
            bay = world.obj(module["refs"]["module_bay"])["attrs"]["name"]
            assembly = any(part["manufacturer"] == maker and part["model"] == module_type["attrs"]["model"] and part.get("assembly")
                           for part in world.catalog["optics"]["parts"].values())
            replace = "the whole cable assembly" if assembly else "the transceiver"
            journal(key, "optic-replacement-plan", dated(key, "optic-replacement-plan", as_of, 40, 20), "Optic replacement note",
                f"{port['attrs']['name']} ({bay}) holds {maker} {module_type['attrs']['model']} serial {module['attrs']['serial']}; "
                f"if it fails, swap in a like-for-like part and replace {replace}.")
    wireless_context(world)
    automation_records(world)
