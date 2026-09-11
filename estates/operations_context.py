"""Scoped service desks and bounded, immutable design records for every profile.

Journal comments are matching identity. Never include growing inventory totals,
current utilization, or an assertion that a change or recovery test was executed.
"""

from collections import defaultdict
from datetime import date, timedelta

from .model import DesignError
from .wireless_context import enrich as wireless_context


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
        add("contact_role", f"contact-role/{key}", {"name": f"{ns} {name}", "slug": f"{ns}-{name.lower().replace(' ', '-')}"})
        name = f"{ns} {groups[key]}"
        add("contact_group", f"contact-group/{key}", {"name": name, "slug": name.lower().replace(" ", "-")})

    def contact(key, name, role, mailbox, description):
        return add("contact", key, {"name": name, "title": roles[role],
                   "email": f"{mailbox}@{ns}.example", "description": description},
                   {"groups": [f"contact-group/{role}"]})

    def assign(target, contact_key, role, suffix=""):
        add("contact_assignment", f"contact-assignment/{target}{suffix}", {"priority": "primary"},
            {"object": target, "contact": contact_key, "role": f"contact-role/{role}"})

    tenant_desks = {}
    # Keep a directory entry even after the last independent site is acquired:
    # upserts cannot retire the old contact, and other sites must not change.
    for obj in kinds["tenant"]:
        tenant = obj["key"]
        suffix = "" if tenant == "tenant" else f"/{tenant}"
        label = "" if tenant == "tenant" else f" {tenant.removeprefix('tenant/')}"
        mailbox = "noc" if tenant == "tenant" else f"{tenant.removeprefix('tenant/')}.noc"
        tenant_desks[tenant] = contact(f"contact/operations{suffix}", f"{ns}{label} NOC duty desk", "operations", mailbox,
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
                f"Medical endpoint inventory and maintenance coordination at {name}; network incidents go to the site technical desk.")
        assign(obj["key"], biomedical_desks[site], "biomedical")

    def dated(target, event, anchor, minimum, spread):
        try:
            return (date.fromisoformat(anchor) - timedelta(days=world.choose(target, f"journal-{event}", range(minimum, minimum + spread)))).isoformat()
        except OverflowError as exc:
            raise DesignError(f"{target}: as_of is too early for the authored operations chronology") from exc

    def journal(target, event, when, title, body):
        add("journal_entry", f"journal/{target}/{event}", {"kind": "info", "comments": f"{when} — {title}\n{body}"}, {"assigned_object": target})

    as_of = world.recipe["as_of"]
    for site in kinds["site"]:
        key, attrs = site["key"], site["attrs"]
        desk = contact(f"contact/{key}", f"{attrs['name']} facilities desk", "facilities", f"{key.removeprefix('site/')}.facilities",
            f"Equipment-room access, cabinet visits and planned power-work coordination at {attrs['name']}.")
        assign(key, desk, "facilities", "/facilities")
        journal(key, "site-record", dated(key, "site-record", as_of, 150, 31), "Site record",
            f"Site: {attrs['name']}\nAddress: {attrs['physical_address'].replace(chr(10), ', ')}\nTime zone: {attrs['time_zone']}\nUse this record when arranging a site visit.")
        journal(key, "access-plan", dated(key, "access-plan", as_of, 60, 31), "Access coordination",
            f"Site: {attrs['name']}\nFacilities contact: {world.obj(desk)['attrs']['name']}\nCoordinate equipment-room access and planned power work with this local desk.")

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
        assign(key, provider_desks[provider], "carrier", "/carrier")
        term = terms[key]
        site_name = world.obj(term["refs"]["termination"])["attrs"]["name"]
        journal(key, "capacity-request", dated(key, "capacity-request", attrs["install_date"], 30, 31), "WAN capacity request",
            f"Circuit: {attrs['cid']}\nProvider: {name}\nCommitted capacity: {attrs['commit_rate']} kbps\nUse the circuit identifier and committed rate when discussing the access order.")
        if world.recipe["profile"] == "provider-backbone":
            far = far_terms[key]
            far_target = world.obj(far["refs"]["termination"])
            far_name = far_target["attrs"]["name"]
            if far_target["kind"] == "provider_network":
                body = (f"Circuit: {attrs['cid']}\nA termination: {site_name}\nZ network boundary: {far_name}\n"
                        f"A handoff: {term['attrs']['port_speed']} kbps\nRecorded service date: {attrs['install_date']}\n"
                        "Remote interface and owner: unknown.\n"
                        "Use the A termination to coordinate the local handoff; the Z record identifies an external network boundary.")
            else:
                body = f"Circuit: {attrs['cid']}\nA termination: {site_name}\nZ termination: {far_name}\nA handoff: {term['attrs']['port_speed']} kbps\nZ handoff: {far['attrs']['port_speed']} kbps\nRecorded service date: {attrs['install_date']}\nUse both termination records to coordinate the local handoffs."
            journal(key, "handoff-plan", attrs["install_date"], "Circuit handoff plan", body)
        else:
            journal(key, "handoff-plan", attrs["install_date"], "WAN handoff plan",
                f"Circuit: {attrs['cid']}\nCustomer site: {site_name}\nPhysical handoff: {term['attrs']['port_speed']} kbps\nRecorded service date: {attrs['install_date']}\nThis handoff plan describes the inventory connection; it does not record an acceptance test.")

    service_desks, anchors = {}, {}
    listeners = defaultdict(list)
    for service in kinds["service"]:
        if vm := service["refs"].get("virtual_machine"):
            listeners[vm].append(service)
    for vm in sorted(kinds["virtual_machine"], key=lambda obj: obj["key"]):
        key, refs = vm["key"], vm["refs"]
        workload = key.split("/")[2]
        scope = (refs["tenant"], workload)
        if scope not in service_desks:
            label = "" if refs["tenant"] == "tenant" else f" {refs['tenant'].removeprefix('tenant/')}"
            mailbox = f"{label.strip()}." if label else ""
            service_desks[scope] = contact(f"contact/service/{refs['tenant']}/{workload}", f"{ns}{label} {workload} service desk", "service", f"{mailbox}{workload}.service",
                f"Resource sizing and listener configuration for {workload} within {world.obj(refs['tenant'])['attrs']['name']}; coordinate host incidents with the cluster technical desk.")
        assign(key, service_desks[scope], "service")
        anchors.setdefault((refs["cluster"], workload), vm)
    # ponytail: two records per workload/site, not per replica; append-only VM
    # ordinals keep the anchor stable. A future retirement workflow needs review.
    for vm in anchors.values():
        key, attrs, refs = vm["key"], vm["attrs"], vm["refs"]
        journal(key, "resource-plan", dated(key, "resource-plan", as_of, 120, 31), "Service resource plan",
            f"VM: {attrs['name']}\nHost: {world.obj(refs['device'])['attrs']['name']}\nCapacity: {attrs['vcpus']} vCPU; {attrs['memory']} MB memory; {attrs['disk']} MB disk\nThis is the initial placement and resource budget for this service instance.")
        entries = [f"{service['attrs']['name']}: {service['attrs']['protocol']}/{','.join(map(str, service['attrs']['ports']))}" for service in sorted(listeners[key], key=lambda obj: obj["key"])]
        journal(key, "listener-plan", dated(key, "listener-plan", as_of, 30, 31), "Service listener plan",
            f"VM: {attrs['name']}\nListeners: {'; '.join(entries)}\nSupport contact: {world.obj(service_desks[(refs['tenant'], key.split('/')[2])])['attrs']['name']}\nUse the modeled listeners to scope configuration review; no application health check is recorded.")

    # Permanent U allocation makes this local selection stable when a new
    # workload sorts before existing workloads. New racks receive new stories.
    equipment_anchors, supplies, interfaces = {}, defaultdict(list), defaultdict(dict)
    optical_cages = {}
    for model in world.catalog["models"].values():
        optical_cages[(model["manufacturer"], model["model"])] = next(
            (p["name"] for p in model["interfaces"]
             if p["type"] in {"1000base-x-sfp", "10gbase-x-sfpp", "100gbase-x-qsfp28"}), None)
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
        facilities = world.obj(f"contact/{refs['site']}")["attrs"]["name"]
        journal(key, "equipment-record", dated(key, "equipment-record", as_of, 100, 20), "Equipment installation record",
            f"Device: {attrs['name']}\nModel: {model}\nSerial: {attrs['serial']}\nSite: {site}\nRoom: {room}\nRack: {rack} / U {attrs['position']}\nInventory access interface: {access}\nUse this record to identify the chassis and its initial placement.")
        journal(key, "maintenance-plan", dated(key, "maintenance-plan", as_of, 20, 20), "Equipment maintenance plan",
            f"Device: {attrs['name']}\nSite: {site}\nRoom: {room}\nRack: {rack}\nFacilities contact: {facilities}\nArrange equipment-room access with this desk and consult the device's current technical contact before scheduling work.")
        if supplies[key]:
            port = min(supplies[key], key=lambda obj: obj["key"])
            module = world.obj(port["refs"]["module"])
            bay = world.obj(module["refs"]["module_bay"])["attrs"]["name"]
            model = world.obj(module["refs"]["module_type"])["attrs"]["model"]
            journal(key, "psu-replacement-plan", dated(key, "psu-replacement-plan", as_of, 1, 19), "PSU replacement preparation",
                f"Device: {attrs['name']}\nInstalled PSU model: {model}\nInstalled PSU serial: {module['attrs']['serial']}\nBay: {bay}\nSupply port: {port['attrs']['name']}\nFacilities contact: {facilities}\nPlan a like-for-like replacement using this installed component record. Trace current power paths and confirm isolation requirements with the technical owner before scheduling work; no replacement is recorded as executed.")
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
            journal(key, "optic-replacement-plan", dated(key, "optic-replacement-plan", as_of, 40, 20), "Optical replacement preparation",
                f"Device: {attrs['name']}\nInterface: {port['attrs']['name']}\nInstalled part: {maker} {module_type['attrs']['model']}\nInstalled serial: {module['attrs']['serial']}\nBay: {bay}\nFacilities contact: {facilities}\nUse the installed part and current device technical contact to review a like-for-like replacement. For a captive AOC end, replace the complete assembly. Preserve the interface and its dependent records; no module deletion, hot-swap or replacement is recorded as executed.")
    wireless_context(world)
