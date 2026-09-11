"""Independent scope, chronology and graph-fact checks for operations inventory."""

from collections import defaultdict
from datetime import date
import math
import re

from .model import digest, hardware_catalog


def _context(plan, objects, kinds):
    """Derive obligations from inventory, never emitted contracts or metadata."""
    if not plan.get("recipe", {}).get("profile"):
        return []  # Independently authored generic graph fixtures have no profile policy.
    recipe = plan["recipe"]
    ns = recipe.get("namespace", "")
    findings = []

    def fail(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    roles = {"operations": ("Technical escalation", "Operations desk"),
             "facilities": ("Facilities access", "Site facilities"),
             "carrier": ("Carrier escalation", "Carrier support"),
             "service": ("Service support", "Service teams")}
    if recipe.get("profile") == "hospital-clinics":
        roles["biomedical"] = ("Biomedical support", "Biomedical engineering")
    contacts, assignments, notes = {}, {}, {}
    external_handoffs = set()
    infrastructure_roles = {f"role/{role}" for role in (
        "wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
        "ap", "console-server", "stack", "laboratory", "provider-edge", "customer-edge")}

    def expect_contact(key, name, role, scope, mailbox):
        contacts[key] = (name, role, scope, mailbox)
        return key

    def expect_assignment(target, contact, role, suffix=""):
        assignments[f"contact-assignment/{target}{suffix}"] = {
            "object": target, "contact": contact, "role": f"contact-role/{role}"}

    def scheduled(key, event, anchor, minimum, spread):
        try:
            offset = minimum + int(digest([recipe["seed"], key, f"journal-{event}", plan["generator_version"]]), 16) % spread
            return date.fromordinal(date.fromisoformat(anchor).toordinal() - offset).isoformat()
        except (KeyError, TypeError, ValueError):
            return None

    def expect_note(key, event, when, facts):
        notes[f"journal/{key}/{event}"] = (key, event, when, tuple(map(str, facts)))

    for tenant in kinds["tenant"]:
        key = tenant["key"]
        suffix = "" if key == "tenant" else f"/{key}"
        label = "" if key == "tenant" else f" {key.removeprefix('tenant/')}"
        mailbox = "noc" if key == "tenant" else f"{key.removeprefix('tenant/')}.noc"
        expect_contact(f"contact/operations{suffix}", f"{ns}{label} NOC duty desk", "operations", tenant["attrs"].get("name", ""), mailbox)
    for obj in kinds["site"] + kinds["cluster"] + kinds["circuit"] + (kinds["virtual_circuit"] if recipe.get("profile") == "provider-backbone" else []):
        tenant = obj["refs"].get("tenant", "")
        suffix = "" if tenant == "tenant" else f"/{tenant}"
        expect_assignment(obj["key"], f"contact/operations{suffix}", "operations")
    for obj in kinds["device"]:
        if obj["refs"].get("role") not in infrastructure_roles:
            continue
        tenant = obj["refs"].get("tenant", "")
        suffix = "" if tenant == "tenant" else f"/{tenant}"
        expect_assignment(obj["key"], f"contact/operations{suffix}", "operations")
    for obj in kinds["device"] if recipe.get("profile") == "hospital-clinics" else []:
        if obj["refs"].get("role") not in {"role/medical-device", "role/imaging-device"}:
            continue
        site = obj["refs"].get("site", "")
        name = attrs(site).get("name", "")
        contact = expect_contact(f"contact/biomedical/{site}", f"{name} biomedical desk", "biomedical", name,
                                 f"{site.removeprefix('site/')}.biomedical")
        expect_assignment(obj["key"], contact, "biomedical")
    for site in kinds["site"]:
        key, data = site["key"], site["attrs"]
        name = data.get("name", "")
        address = data.get("physical_address", "")
        if not isinstance(address, str):
            fail("operations-journal-facts", key, "Site history needs a textual address from the actual site record.")
            address = ""
        contact_name = f"{name} facilities desk"
        contact = expect_contact(f"contact/{key}", contact_name, "facilities", name, f"{key.removeprefix('site/')}.facilities")
        expect_assignment(key, contact, "facilities", "/facilities")
        expect_note(key, "site-record", scheduled(key, "site-record", recipe.get("as_of"), 150, 31),
                    (name, address.replace("\n", ", "), data.get("time_zone", "")))
        expect_note(key, "access-plan", scheduled(key, "access-plan", recipe.get("as_of"), 60, 31), (name, contact_name))
    terms = defaultdict(list)
    far_terms = defaultdict(list)
    for term in kinds["circuit_termination"]:
        if term["attrs"].get("term_side") == "A":
            terms[term["refs"].get("circuit")].append(term)
        elif term["attrs"].get("term_side") == "Z":
            far_terms[term["refs"].get("circuit")].append(term)
    for circuit in kinds["circuit"]:
        key, data, refs = circuit["key"], circuit["attrs"], circuit["refs"]
        provider = refs.get("provider", "")
        account = objects.get(refs.get("provider_account"), {})
        if (account.get("kind") != "provider_account" or account.get("refs", {}).get("provider") != provider
                or "tenant" in account.get("refs", {})):
            fail("operations-provider-account", key, "Circuit must use an account from its actual provider; account tenancy is not a native field.")
        if recipe.get("profile") != "provider-backbone":
            local = terms[key]
            sid = str(local[0]["refs"].get("termination", "")).removeprefix("site/") if len(local) == 1 else ""
            inherited = recipe.get("profile") == "regional-bank" and plan.get("design_assignments", {}).get(sid) in {"inherited", "refreshed"}
            suffix, lineage = ("/inherited", "inherited") if inherited else ("", "estate")
            if (refs.get("provider_account") != f"provider-account/{provider}{suffix}"
                    or account.get("attrs", {}).get("account") != f"{ns}-{lineage}-{provider.rsplit('/', 1)[-1]}"
                    or account.get("refs", {}).get("owner") != "owner/operations"):
                fail("operations-provider-account", key, "WAN procurement must retain its actual provider and procurement lineage; acquisition does not renew the account.")
        provider_name = attrs(provider).get("name", "")
        contact = expect_contact(f"contact/{provider}", f"{provider_name} support desk", "carrier", provider_name, f"carrier-{provider.removeprefix('provider/')}.support")
        expect_assignment(key, contact, "carrier", "/carrier")
        expect_note(key, "capacity-request", scheduled(key, "capacity-request", data.get("install_date"), 30, 31),
                    (data.get("cid"), provider_name, data.get("commit_rate")))
        local = terms[key]
        if len(local) != 1 or objects.get(local[0]["refs"].get("termination"), {}).get("kind") != "site":
            fail("operations-journal", key, "Handoff history needs one actual A-side site termination.")
        term = local[0] if local else {"attrs": {}, "refs": {}}
        if recipe.get("profile") == "provider-backbone":
            remote = far_terms[key]
            if len(remote) != 1 or objects.get(remote[0]["refs"].get("termination"), {}).get("kind") not in {"site", "provider_network"}:
                fail("operations-journal", key, "Circuit history needs one actual Z-side site or provider-network termination.")
            far = remote[0] if remote else {"attrs": {}, "refs": {}}
            external = objects.get(far["refs"].get("termination"), {}).get("kind") == "provider_network"
            if external:
                external_handoffs.add(key)
            facts = (data.get("cid"), attrs(term["refs"].get("termination")).get("name"),
                     attrs(far["refs"].get("termination")).get("name"), term["attrs"].get("port_speed"))
            expect_note(key, "handoff-plan", data.get("install_date"),
                        facts + (() if external else (far["attrs"].get("port_speed"),)) + (data.get("install_date"),))
        else:
            expect_note(key, "handoff-plan", data.get("install_date"),
                        (data.get("cid"), attrs(term["refs"].get("termination")).get("name"), term["attrs"].get("port_speed"), data.get("install_date")))
    listeners = defaultdict(list)
    for service in kinds["service"]:
        listeners[service["refs"].get("virtual_machine")].append(service)
    anchors = {}
    for vm in kinds["virtual_machine"]:
        key, refs = vm["key"], vm["refs"]
        parts = key.split("/")
        if len(parts) != 4 or parts[0] != "vm" or not parts[3].isdigit():
            fail("operations-contact", key, "A workload contact needs a canonical VM workload and ordinal.")
            continue
        workload, tenant = parts[2], refs.get("tenant", "")
        label = "" if tenant == "tenant" else f" {tenant.removeprefix('tenant/')}"
        contact_name = f"{ns}{label} {workload} service desk"
        mailbox = f"{label.strip()}." if label else ""
        contact = expect_contact(f"contact/service/{tenant}/{workload}", contact_name, "service", f"{workload} within {attrs(tenant).get('name', '')}", f"{mailbox}{workload}.service")
        expect_assignment(key, contact, "service")
        scope = (refs.get("cluster"), workload)
        if scope not in anchors or int(parts[3]) < int(anchors[scope]["key"].rsplit("/", 1)[1]):
            anchors[scope] = vm
    for vm in anchors.values():
        key, data, refs = vm["key"], vm["attrs"], vm["refs"]
        expect_note(key, "resource-plan", scheduled(key, "resource-plan", recipe.get("as_of"), 120, 31),
                    (data.get("name"), attrs(refs.get("device")).get("name"), data.get("vcpus"), data.get("memory"), data.get("disk")))
        entries = []
        for service in sorted(listeners[key], key=lambda obj: obj["key"]):
            service_attrs = service["attrs"]
            entries.append(f"{service_attrs.get('name')}: {service_attrs.get('protocol')}/{','.join(map(str, service_attrs.get('ports', [])))}")
        contact_name = contacts[assignments[f"contact-assignment/{key}"]["contact"]][0]
        expect_note(key, "listener-plan", scheduled(key, "listener-plan", recipe.get("as_of"), 30, 31),
                    (data.get("name"), "; ".join(entries), contact_name))

    equipment_anchors, supplies, interfaces = {}, defaultdict(list), defaultdict(dict)
    catalog_cages = {}
    for model in hardware_catalog()["models"].values():
        catalog_cages[(model["manufacturer"], model["model"])] = next(
            (p["name"] for p in model["interfaces"]
             if p["type"] in {"1000base-x-sfp", "10gbase-x-sfpp", "100gbase-x-qsfp28"}), None)
    optical_ports = {c["refs"].get(side) for c in kinds["cable"] if c["attrs"].get("type") in {"smf", "aoc"}
                     for side in ("a", "b")}
    for port in kinds["interface"]:
        interfaces[port["refs"].get("device")][port["attrs"].get("name")] = port
    for port in kinds["power_port"]:
        if port["refs"].get("module"):
            supplies[port["refs"].get("device")].append(port)
    for device in kinds["device"]:
        refs, data = device["refs"], device["attrs"]
        rack, position = refs.get("rack"), data.get("position")
        if refs.get("role") not in infrastructure_roles or not rack or position is None:
            continue
        if type(position) not in (int, float):
            fail("operations-journal-facts", device["key"], "Equipment history requires an actual numeric rack position.")
            continue
        old = equipment_anchors.get(rack)
        if old is None or (position, device["key"]) < (old["attrs"]["position"], old["key"]):
            equipment_anchors[rack] = device
    for device in equipment_anchors.values():
        key, data, refs = device["key"], device["attrs"], device["refs"]
        rack, room, site = (attrs(refs.get(field)).get("name") for field in ("rack", "location", "site"))
        access = objects.get(refs.get("primary_ip4"), {}).get("refs", {}).get("assigned_object")
        if objects.get(access, {}).get("refs", {}).get("device") != key:
            fail("operations-journal-facts", key, "Installation history must name the device's own primary inventory interface.")
        facilities = attrs(f"contact/{refs.get('site')}").get("name")
        expect_note(key, "equipment-record", scheduled(key, "equipment-record", recipe.get("as_of"), 100, 20),
                    (data.get("name"), attrs(refs.get("device_type")).get("model"), data.get("serial"),
                     site, room, rack, data.get("position"), attrs(access).get("name")))
        expect_note(key, "maintenance-plan", scheduled(key, "maintenance-plan", recipe.get("as_of"), 20, 20),
                    (data.get("name"), site, room, rack, facilities))
        if supplies[key]:
            port = min(supplies[key], key=lambda obj: obj["key"])
            module = objects.get(port["refs"].get("module"), {})
            module_refs = module.get("refs", {})
            bay = objects.get(module_refs.get("module_bay"), {})
            if module.get("kind") != "module" or module_refs.get("device") != key or bay.get("refs", {}).get("device") != key:
                fail("operations-journal-facts", key, "Replacement planning must follow the installed supply through its own module and bay.")
            expect_note(key, "psu-replacement-plan", scheduled(key, "psu-replacement-plan", recipe.get("as_of"), 1, 19),
                        (data.get("name"), attrs(module_refs.get("module_type")).get("model"), module.get("attrs", {}).get("serial"),
                         bay.get("attrs", {}).get("name"), port["attrs"].get("name"), facilities))
        dtype = objects.get(refs.get("device_type"), {})
        maker = attrs(dtype.get("refs", {}).get("manufacturer")).get("name")
        cage = catalog_cages.get((maker, dtype.get("attrs", {}).get("model")))
        port = interfaces[key].get(cage)
        if port and port["key"] in optical_ports:
            module = objects.get(port["refs"].get("module"), {})
            module_refs = module.get("refs", {})
            bay = objects.get(module_refs.get("module_bay"), {})
            module_type = objects.get(module_refs.get("module_type"), {})
            maker = attrs(module_type.get("refs", {}).get("manufacturer")).get("name")
            if (module.get("kind") != "module" or module_refs.get("device") != key
                    or bay.get("kind") != "module_bay" or bay.get("refs", {}).get("device") != key
                    or module_type.get("kind") != "module_type"):
                fail("operations-journal-facts", key, "Optical planning must follow the fixed cage through its own installed module, bay and type.")
            expect_note(key, "optic-replacement-plan", scheduled(key, "optic-replacement-plan", recipe.get("as_of"), 40, 20),
                        (data.get("name"), port["attrs"].get("name"), f"{maker} {module_type.get('attrs', {}).get('model')}",
                         module.get("attrs", {}).get("serial"), bay.get("attrs", {}).get("name"), facilities))

    for role, (title, group) in roles.items():
        for kind, label in (("contact_role", title), ("contact_group", group)):
            key = f"{kind.replace('_', '-')}/{role}"
            name = f"{ns} {label}"
            if (objects.get(key, {}).get("kind") != kind or attrs(key) != {"name": name, "slug": name.lower().replace(" ", "-")}
                    or objects.get(key, {}).get("refs")):
                fail("operations-contact", key, "Contact role/group must retain its unique functional scope and root matching identity.")
    responsibility_forms = {
        "operations": r"Network triage and technical escalation for ([^;\n]+); coordinates site, platform and carrier specialists\.",
        "facilities": r"Equipment-room access, cabinet visits and planned power-work coordination at ([^\n]+)\.",
        "carrier": r"Circuit identifiers, contracted capacity and handoff coordination for ([^;\n]+); customer-side troubleshooting stays with the tenant technical desk\.",
        "service": r"Resource sizing and listener configuration for ([^;\n]+); coordinate host incidents with the cluster technical desk\.",
        "biomedical": r"Medical endpoint inventory and maintenance coordination at ([^;\n]+); network incidents go to the site technical desk\."}
    if recipe.get("profile") == "provider-backbone":
        responsibility_forms["carrier"] = r"Capacity and handoff coordination for ([^\n]+)\. Tenant technical desks handle local troubleshooting\."
    names = set()
    for obj in kinds["contact"]:
        key, data = obj["key"], obj["attrs"]
        expected = contacts.get(key)
        name = data.get("name")
        if not isinstance(name, str) or name in names or len(name) > 100:
            fail("operations-contact", key, "Contact display names must be unique and within the native 100-character bound.")
        if isinstance(name, str):
            names.add(name)
        if expected is None:
            fail("operations-contact", key, "Contact has no tenant, site, provider or workload responsibility in this estate.")
            continue
        expected_name, role, scope, mailbox = expected
        description = data.get("description", "")
        responsibility = re.fullmatch(responsibility_forms[role], description) if isinstance(description, str) else None
        if (name != expected_name or data.get("title") != roles[role][0]
                or data.get("email") != f"{mailbox}@{ns}.example" or len(mailbox) > 64
                or not responsibility or responsibility.group(1) != scope or len(description) > 200
                or set(data) != {"name", "title", "email", "description"}
                or obj["refs"] != {"groups": [f"contact-group/{role}"]}):
            fail("operations-contact", key, "Contact name, safe mailbox, responsibility and group must match its actual scope.")
    for key in contacts:
        if objects.get(key, {}).get("kind") != "contact":
            fail("operations-contact", key, "Required scoped service desk is missing.")
    for obj in kinds["contact_assignment"]:
        if assignments.get(obj["key"]) != obj["refs"] or obj["attrs"] != {"priority": "primary"}:
            fail("operations-contact", obj["key"], "Assignment must use the actual tenant, site, provider or workload desk and its distinct primary responsibility.")
    for key, refs in assignments.items():
        if objects.get(key, {}).get("kind") != "contact_assignment" or objects.get(refs["contact"], {}).get("kind") != "contact":
            fail("operations-contact", key, "Required contact assignment or scoped contact is missing.")

    # Finite note forms admit only these claims. Captured values below are checked
    # against the graph; the emitter and its metadata are not validation inputs.
    forms = {
        "equipment-record": ("Equipment installation record", r"Device: ([^\n]+)\nModel: ([^\n]+)\nSerial: ([^\n]+)\nSite: ([^\n]+)\nRoom: ([^\n]+)\nRack: ([^\n]+) / U ([0-9.]+)\nInventory access interface: ([^\n]+)\nUse this record to identify the chassis and its initial placement\."),
        "maintenance-plan": ("Equipment maintenance plan", r"Device: ([^\n]+)\nSite: ([^\n]+)\nRoom: ([^\n]+)\nRack: ([^\n]+)\nFacilities contact: ([^\n]+)\nArrange equipment-room access with this desk and consult the device's current technical contact before scheduling work\."),
        "psu-replacement-plan": ("PSU replacement preparation", r"Device: ([^\n]+)\nInstalled PSU model: ([^\n]+)\nInstalled PSU serial: ([^\n]+)\nBay: ([^\n]+)\nSupply port: ([^\n]+)\nFacilities contact: ([^\n]+)\nPlan a like-for-like replacement using this installed component record\. Trace current power paths and confirm isolation requirements with the technical owner before scheduling work; no replacement is recorded as executed\."),
        "optic-replacement-plan": ("Optical replacement preparation", r"Device: ([^\n]+)\nInterface: ([^\n]+)\nInstalled part: ([^\n]+)\nInstalled serial: ([^\n]+)\nBay: ([^\n]+)\nFacilities contact: ([^\n]+)\nUse the installed part and current device technical contact to review a like-for-like replacement\. For a captive AOC end, replace the complete assembly\. Preserve the interface and its dependent records; no module deletion, hot-swap or replacement is recorded as executed\."),
        "site-record": ("Site record", r"Site: ([^\n]+)\nAddress: ([^\n]+)\nTime zone: ([^\n]+)\nUse this record when arranging a site visit\."),
        "access-plan": ("Access coordination", r"Site: ([^\n]+)\nFacilities contact: ([^\n]+)\nCoordinate equipment-room access and planned power work with this local desk\."),
        "capacity-request": ("WAN capacity request", r"Circuit: ([^\n]+)\nProvider: ([^\n]+)\nCommitted capacity: ([0-9]+) kbps\nUse the circuit identifier and committed rate when discussing the access order\."),
        "handoff-plan": ("WAN handoff plan", r"Circuit: ([^\n]+)\nCustomer site: ([^\n]+)\nPhysical handoff: ([0-9]+) kbps\nRecorded service date: ([^\n]+)\nThis handoff plan describes the inventory connection; it does not record an acceptance test\."),
        "resource-plan": ("Service resource plan", r"VM: ([^\n]+)\nHost: ([^\n]+)\nCapacity: ([0-9.]+) vCPU; ([0-9]+) MB memory; ([0-9]+) MB disk\nThis is the initial placement and resource budget for this service instance\."),
        "listener-plan": ("Service listener plan", r"VM: ([^\n]+)\nListeners: ([^\n]+)\nSupport contact: ([^\n]+)\nUse the modeled listeners to scope configuration review; no application health check is recorded\.")}
    if recipe.get("profile") == "provider-backbone":
        forms["handoff-plan"] = ("Circuit handoff plan", r"Circuit: ([^\n]+)\nA termination: ([^\n]+)\nZ termination: ([^\n]+)\nA handoff: ([0-9]+) kbps\nZ handoff: ([0-9]+) kbps\nRecorded service date: ([^\n]+)\nUse both termination records to coordinate the local handoffs\.")
    for obj in kinds["journal_entry"]:
        key = obj["key"]
        if key not in notes:
            fail("operations-journal", key, "Journal has no required immutable site, circuit, workload or equipment event.")
            continue
        target, event, when, facts = notes[key]
        title, body = forms[event]
        if event == "handoff-plan" and target in external_handoffs:
            body = (r"Circuit: ([^\n]+)\nA termination: ([^\n]+)\nZ network boundary: ([^\n]+)\n"
                    r"A handoff: ([0-9]+) kbps\nRecorded service date: ([^\n]+)\nRemote interface and owner: unknown\.\n"
                    r"Use the A termination to coordinate the local handoff; the Z record identifies an external network boundary\.")
        comments = obj["attrs"].get("comments", "")
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) — " + title + "\n" + body, comments) if isinstance(comments, str) else None
        if obj["refs"] != {"assigned_object": target} or obj["attrs"].get("kind") != "info" or set(obj["attrs"]) != {"kind", "comments"}:
            fail("operations-journal", key, "Journal must retain its actual subject, information kind and no account binding.")
        if not match or match.groups()[1:] != facts:
            fail("operations-journal-facts", key, "Journal claims must match the subject's actual address, contact, circuit, resource or listener facts.")
        try:
            recorded = date.fromisoformat(match.group(1)) if match else None
            if recorded is None or match.group(1) != when or recorded > date.fromisoformat(recipe["as_of"]):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            fail("operations-journal-date", key, "Authored event date must match its stable seeded chronology and not exceed as_of.")
    for key in notes:
        if objects.get(key, {}).get("kind") != "journal_entry":
            fail("operations-journal", key, "Required bounded lifecycle event is missing.")
    return findings


def validate(plan):
    """Check finished records, including unoccupied reservations and disk totals."""
    objects = {o["key"]: o for o in plan["objects"]}
    kinds = defaultdict(list)
    for obj in objects.values():
        kinds[obj["kind"]].append(obj)
    findings = _context(plan, objects, kinds)
    if not any(c.get("operations") for c in plan.get("contracts", [])):
        return sorted(findings, key=lambda f: (f["code"], f["object"]))

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    def related(obj, field):
        return objects.get(obj.get("refs", {}).get(field), {})

    expected = {"circuit_group", "circuit_group_assignment", "cluster_group", "contact", "contact_group", "contact_role",
                "contact_assignment", "provider_account", "rack_type", "rack_group", "tenant_group", "virtual_disk",
                "virtual_machine_type", "custom_field", "custom_field_choice_set", "journal_entry", "custom_link",
                "owner", "owner_group", "cable_bundle"}
    for kind in sorted(expected - kinds.keys()):
        report("operations-coverage", kind, "Operations coverage requires a connected example of this kind.")
    for kind, field, target in (("tenant", "group", "tenant_group"), ("cluster", "group", "cluster_group"),
                                ("rack", "group", "rack_group"), ("owner", "group", "owner_group")):
        for obj in kinds[kind]:
            if related(obj, field).get("kind") != target:
                report("operations-group", obj["key"], f"{kind} must reference its {target}.")
    for circuit in kinds["circuit"]:
        account = related(circuit, "provider_account")
        if account.get("kind") != "provider_account" or account.get("refs", {}).get("provider") != circuit["refs"].get("provider"):
            report("operations-provider-account", circuit["key"], "Circuit and commercial account must belong to the same provider.")
    members = {obj["refs"].get("member"): obj for obj in kinds["circuit_group_assignment"]}
    for side, priority in (("a", "primary"), ("b", "secondary")):
        circuit = f"circuit/dc-01/{side}/1"
        assignment = members.get(circuit, {})
        if assignment.get("attrs", {}).get("priority") != priority or related(assignment, "group").get("kind") != "circuit_group":
            report("operations-circuit-group", circuit, "The DC01 restoration group needs the real A/B pair with distinct inventory priorities.")
    for assignment in kinds["contact_assignment"]:
        if (related(assignment, "object").get("kind") not in {"site", "cluster", "circuit", "virtual_machine", "device"}
                or related(assignment, "contact").get("kind") != "contact" or related(assignment, "role").get("kind") != "contact_role"):
            report("operations-contact", assignment["key"], "Escalation assignment must join real supported infrastructure to a contact and role.")
    for contact in kinds["contact"]:
        groups = contact["refs"].get("groups", [])
        if not groups or any(objects.get(group, {}).get("kind") != "contact_group" for group in groups):
            report("operations-contact", contact["key"], "Duty contact must belong to an actual contact group.")
    disks = defaultdict(list)
    for disk in kinds["virtual_disk"]:
        disks[disk["refs"].get("virtual_machine")].append(disk)
        if related(disk, "virtual_machine").get("kind") != "virtual_machine" or disk["attrs"].get("size", 0) <= 0:
            report("operations-disk", disk["key"], "Disk must have a VM and a positive size in MB.")
    for vm in kinds["virtual_machine"]:
        if not disks[vm["key"]] or sum(d["attrs"].get("size", 0) for d in disks[vm["key"]]) != vm["attrs"].get("disk"):
            report("operations-disk-budget", vm["key"], "Discrete virtual disks must total the existing VM disk budget, not add to it.")
        template = related(vm, "virtual_machine_type")
        if template.get("kind") != "virtual_machine_type" or template.get("refs", {}).get("default_platform") != vm["refs"].get("platform"):
            report("operations-vm-type", vm["key"], "VM type and VM must use the same modeled service platform.")
    occupied = defaultdict(set)
    for device in kinds["device"]:
        position = device["attrs"].get("position")
        if position is not None:
            height = related(device, "device_type").get("attrs", {}).get("u_height", 0)
            occupied[device["refs"].get("rack")].update(range(math.floor(position), math.ceil(position+height)))
    for rack in kinds["rack"]:
        template = related(rack, "rack_type")
        if template.get("kind") != "rack_type" or any(template.get("attrs", {}).get(field) != rack["attrs"].get(field) for field in ("u_height", "width")):
            report("operations-rack-type", rack["key"], "Rack type must match the actual cabinet height and width.")
    username = plan["recipe"].get("reservation_user", "")
    reservations = kinds["rack_reservation"]
    if bool(username) != bool(reservations):
        report("operations-reservation-user", "plan", "Rack reservations require an explicitly bound existing username.")
    reserved = defaultdict(set)
    for reservation in reservations:
        rack, user = related(reservation, "rack"), related(reservation, "user")
        units = reservation["attrs"].get("units", [])
        if (user.get("kind") != "user" or user.get("attrs") != {"username": username}
                or user.get("meta", {}).get("external") is not True):
            report("operations-reservation-user", reservation["key"], "Reservation user is an external match-only dependency; account provisioning is forbidden.")
        if (rack.get("kind") != "rack" or not units or any(type(unit) is not int or not 1 <= unit <= rack.get("attrs", {}).get("u_height", 0) for unit in units)
                or len(set(units)) != len(units) or set(units) & (occupied[rack.get("key")] | reserved[rack.get("key")])):
            report("operations-reservation-space", reservation["key"], "Reserved units must be distinct, in range, and free of equipment or other reservations.")
        reserved[rack.get("key")].update(units)
    for user in kinds["user"]:
        if user.get("meta", {}).get("external") is not True or set(user["attrs"]) != {"username"}:
            report("operations-external-user", user["key"], "Only a username reference to an existing account is allowed.")
    for field in kinds["custom_field"]:
        choices = related(field, "choice_set")
        values = {choice.split(":", 1)[0] for choice in choices.get("attrs", {}).get("extra_choices", [])}
        consumers = [obj for obj in kinds["site"] if field["attrs"].get("name") in obj["attrs"].get("custom_fields", {})]
        if choices.get("kind") != "custom_field_choice_set" or not consumers or "dcim.site" not in field["attrs"].get("object_types", []):
            report("operations-custom-field", field["key"], "Operations field must have real choices and a compatible site consumer.")
        for obj in consumers:
            selected = obj["attrs"]["custom_fields"][field["attrs"]["name"]].get("selection")
            if selected not in values or field["key"] not in obj.get("meta", {}).get("requires", []):
                report("operations-custom-field", obj["key"], "Field value must match a declared choice and depend on its definition before export.")
    for link in kinds["custom_link"]:
        if link["attrs"].get("object_types") != ["dcim.site"] or link["attrs"].get("link_url") != "/dcim/devices/?site_id={{ object.pk }}":
            report("operations-custom-link", link["key"], "Site equipment shortcut must stay on the target's own device inventory.")
    bundles = defaultdict(list)
    for cable in kinds["cable"]:
        if cable["refs"].get("bundle"):
            bundles[cable["refs"]["bundle"]].append(cable)
    for bundle in kinds["cable_bundle"]:
        cables = bundles[bundle["key"]]
        if len(cables) != 2:
            report("operations-cable-bundle", bundle["key"], "The service-host bundle must contain the two real A-side fibers.")
        for cable in cables:
            ports = [related(cable, side) for side in ("a", "b")]
            hosts = {port.get("refs", {}).get("device") for port in ports}
            if (any(port.get("kind") != "interface" for port in ports) or cable["attrs"].get("type") != "smf"
                    or "device/dc-01/compute-01-leaf-a" not in hosts):
                report("operations-cable-bundle", cable["key"], "Bundle members must remain actual A-side optical host connections.")
    return sorted(findings, key=lambda f: (f["code"], f["object"]))
