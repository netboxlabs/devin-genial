"""Independent scope, chronology and graph-fact checks for operations inventory."""

from collections import defaultdict
from datetime import date
import math
import re

from .model import digest, hardware_catalog
from .naming import dedicated, rate_kbps, titleize


# A CSV export template must be one header line plus exactly one queryset loop,
# so NetBox renders a row per object. The check is structural: nothing here
# imports or executes Jinja2 (the runtime is the standard library only), and a
# column expression must not contain a comma.
_EXPORT_TEMPLATE = re.compile(
    r"(?P<header>[^\n{}]+)\n\{% for (?P<name>[a-z_]+) in queryset %\}"
    r"(?P<row>[^\n]+)\n\{% endfor %\}\Z")
_EXPRESSION = re.compile(r"\{\{(.+?)\}\}")
# core.events in the pinned NetBox 4.7 registers exactly these event types.
_EVENT_TYPES = {"object_created", "object_updated", "object_deleted",
                "job_started", "job_completed", "job_failed", "job_errored"}
# A server list names a small, redundant set of hosts, never the whole workload.
_MAX_ENDPOINT_HOSTS = 2
# Restated, not imported: real metro area codes; the 555-0100..0199 block is
# reserved for fictional use, so no contact can carry a dialable number.
AREA_CODES = {"Chicago": "312", "Detroit": "313", "Cleveland": "216", "Milwaukee": "414"}


def _renders_csv_rows(code):
    """True when this template is a header plus one queryset loop that reads the object."""
    if not isinstance(code, str):
        return False
    match = _EXPORT_TEMPLATE.fullmatch(code)
    if not match or code.count("{%") != 2 or code.count("{{") != code.count("}}"):
        return False
    header = [column.strip() for column in match.group("header").split(",")]
    columns = match.group("row").split(",")
    expressions = _EXPRESSION.findall(match.group("row"))
    loop = match.group("name")
    # Every column must read the loop object, so a row cannot silently render
    # empty or repeat one global value.
    return (len(header) >= 3 and len(header) == len(columns) == len(expressions)
            and all(header) and all(expression.strip().startswith(f"{loop}.")
                                    for expression in expressions))


def _emitted(kinds, content_type):
    """True when the plan emits objects of this native `app.model` type.

    The canonical kind is the model segment; a multi-word model would simply
    find nothing, which fails closed.
    """
    return (isinstance(content_type, str) and "." in content_type
            and bool(kinds.get(content_type.split(".", 1)[1])))


def _automation(objects, kinds, ns, fail, solo=False):
    """Check the estate's automation inventory against its own graph.

    Config-context server lists must be addresses this estate's own service
    listeners bind; scope references must exist; the event rule must name the
    emitted webhook and stay inert. Emitted contracts and metadata are not
    inputs.
    """
    # Reading a defaultdict would insert empty kinds the coverage check reads.
    listed = lambda kind: kinds.get(kind, [])
    hosts = defaultdict(lambda: defaultdict(list))
    for service in listed("service"):
        vm = service["refs"].get("virtual_machine", "")
        parts = vm.split("/") if isinstance(vm, str) else []
        if len(parts) != 4:
            continue
        for key in service["refs"].get("ipaddresses", []):
            address = objects.get(key, {}).get("attrs", {}).get("address", "")
            if isinstance(address, str) and address:
                hosts[parts[2]][vm].append(address.split("/")[0])
    bound = {workload: {address for served in served_by.values() for address in served}
             for workload, served_by in hosts.items()}

    # The three Branching-exempt records land on main and `just retire` matches
    # them by exact "<namespace> " prefix, so they keep it. Config contexts are
    # branch-scoped and carry an authored, namespace-free display name.
    for obj in (listed("config_context") + listed("export_template")
                + listed("webhook") + listed("event_rule")):
        name = obj["attrs"].get("name", "")
        description = obj["attrs"].get("description", "")
        main_scoped = obj["kind"] != "config_context"
        if (not isinstance(name, str) or len(name) > 100
                or name.startswith(f"{ns} ") is not (main_scoped and not solo)
                or not isinstance(description, str) or not 1 <= len(description) <= 200
                or objects.get(obj["refs"].get("owner"), {}).get("kind") != "owner"):
            fail("automation-record", obj["key"],
                 "Main-scoped automation records carry the namespace on a shared tenant only and "
                 "branch-scoped config contexts never do; all need a native-length name, description and the estate's owner.")

    contexts = {obj["key"]: obj for obj in listed("config_context")}
    weights = {}
    for key in ("config-context/global", "config-context/switching"):
        obj = contexts.get(key)
        if obj is None or obj["attrs"].get("is_active") is not True:
            fail("automation-context", key, "Estate automation needs this active config context.")
            continue
        weight = obj["attrs"].get("weight")
        if type(weight) is not int or not 0 <= weight <= 32767:
            fail("automation-context", key, "Config-context weight must be a native positive small integer.")
            continue
        weights[key] = weight
    if len(weights) == 2 and not weights["config-context/switching"] > weights["config-context/global"]:
        fail("automation-context", "config-context/switching",
             "The role-scoped context must outweigh the global one for its narrower scope to win.")
    for key, obj in contexts.items():
        if key not in {"config-context/global", "config-context/switching"}:
            fail("automation-context", key, "Config context has no declared estate scope.")

    context = contexts.get("config-context/global")
    data = context["attrs"].get("data") if context else None
    if context is not None:
        endpoints = data.get("service_endpoints") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or data.get("domain") != f"{ns}.example"
                or not isinstance(endpoints, dict)
                or set(data) - {"domain", "service_endpoints", "dns_servers"}):
            fail("automation-context", context["key"],
                 "The global context carries this estate's domain and its own service endpoints, nothing else.")
        else:
            if {workload.replace("_", "-") for workload in endpoints} != set(bound):
                fail("automation-context-facts", context["key"],
                     "Service endpoints must name exactly the workloads this estate serves.")
            for workload, addresses in sorted(endpoints.items()):
                served_by = hosts.get(workload.replace("_", "-"), {})
                available = bound.get(workload.replace("_", "-"), set())
                if (not isinstance(addresses, list) or not addresses
                        or len(set(addresses)) != len(addresses)
                        or any(address not in available for address in addresses)):
                    fail("automation-context-facts", context["key"],
                         f"Every {workload} endpoint must be an address that workload's own listeners bind.")
                    continue
                # A short, redundant server list: the addresses of a couple of
                # hosts (both families when the estate is dual-stack), never a
                # roster of every instance.
                named = {vm for vm, served in served_by.items()
                         if set(served) & set(addresses)}
                if len(named) > _MAX_ENDPOINT_HOSTS:
                    fail("automation-context-facts", context["key"],
                         f"The {workload} endpoint list must name at most "
                         f"{_MAX_ENDPOINT_HOSTS} serving hosts.")
            resolvers = data.get("dns_servers")
            if resolvers != endpoints.get("dns"):
                fail("automation-context-facts", context["key"],
                     "The resolver list must be exactly this estate's own DNS listener addresses.")

    scoped = contexts.get("config-context/switching")
    if scoped is not None:
        roles = scoped["refs"].get("roles")
        if (not isinstance(roles, list) or not roles
                or any(objects.get(role, {}).get("kind") != "device_role" for role in roles)):
            fail("automation-context", scoped["key"],
                 "The role-scoped context must reference existing device roles.")
        if not isinstance(scoped["attrs"].get("data"), dict) or not scoped["attrs"]["data"]:
            fail("automation-context", scoped["key"], "A config context must carry data.")

    templates = {obj["key"]: obj for obj in listed("export_template")}
    for key in ("export-template/device-inventory", "export-template/cable-report"):
        obj = templates.get(key)
        if obj is None:
            fail("automation-template", key, "Estate automation needs this export template.")
            continue
        types = obj["attrs"].get("object_types")
        if (not isinstance(types, list) or not types
                or any(not _emitted(kinds, name) for name in types)):
            fail("automation-template", key,
                 "An export template must apply to an object type this estate actually emits.")
        if (not _renders_csv_rows(obj["attrs"].get("template_code"))
                or obj["attrs"].get("mime_type") != "text/csv"
                or obj["attrs"].get("file_extension") != "csv"
                or obj["attrs"].get("as_attachment") is not True):
            fail("automation-template", key,
                 "A CSV export template must be a header plus one queryset loop whose columns all read the object.")

    webhooks = {obj["key"]: obj for obj in listed("webhook")}
    hook = webhooks.get("webhook/netops")
    if hook is None or len(webhooks) != 1:
        fail("automation-event-rule", "webhook/netops", "Estate automation needs exactly one demo webhook.")
    else:
        url = hook["attrs"].get("payload_url", "")
        host = url.split("//", 1)[-1].split("/", 1)[0] if isinstance(url, str) else ""
        if (not isinstance(url, str) or not url.startswith("https://") or not host.endswith(".invalid")
                or hook["attrs"].get("http_method") != "POST"
                or hook["attrs"].get("ssl_verification") is not True):
            fail("automation-event-rule", hook["key"],
                 "The demo webhook must stay inert: an https reserved .invalid endpoint that nothing can reach.")
    rules = {obj["key"]: obj for obj in listed("event_rule")}
    rule = rules.get("event-rule/device-change")
    if rule is None or len(rules) != 1:
        fail("automation-event-rule", "event-rule/device-change",
             "Estate automation needs exactly one demo event rule.")
    else:
        types = rule["attrs"].get("object_types")
        events = rule["attrs"].get("event_types")
        if (objects.get(rule["refs"].get("action_object"), {}).get("kind") != "webhook"
                or rule["attrs"].get("action_type") != "webhook"):
            fail("automation-event-rule", rule["key"],
                 "The event rule must fire the estate's own webhook through the webhook action.")
        if rule["attrs"].get("enabled") is not False:
            fail("automation-event-rule", rule["key"],
                 "The demo event rule ships disabled; an enabled rule would emit requests the estate does not model.")
        if (not isinstance(types, list) or not types
                or any(not _emitted(kinds, name) for name in types)
                or not isinstance(events, list) or not events or set(events) - _EVENT_TYPES
                or len(set(events)) != len(events)):
            fail("automation-event-rule", rule["key"],
                 "The event rule must watch an emitted object type on native event types.")


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
    contacts, assignments, notes, priorities = {}, {}, {}, {}
    external_handoffs = set()
    infrastructure_roles = {f"role/{role}" for role in (
        "wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
        "ap", "console-server", "stack", "laboratory", "provider-edge", "customer-edge")}

    def expect_contact(key, name, role, scope, mailbox, area=None):
        contacts[key] = (name, role, scope, mailbox, area)
        return key

    def site_area(site):
        # The metro is read back from the site's own postal address line.
        lines = attrs(site).get("physical_address", "")
        city = lines.split("\n")[1].split(",")[0] if isinstance(lines, str) and lines.count("\n") >= 1 else None
        return AREA_CODES.get(city)

    def expect_assignment(target, contact, role, suffix="", priority="primary"):
        assignments[f"contact-assignment/{target}{suffix}"] = {
            "object": target, "contact": contact, "role": f"contact-role/{role}"}
        priorities[f"contact-assignment/{target}{suffix}"] = priority

    def scheduled(key, event, anchor, minimum, spread):
        try:
            offset = minimum + int(digest([recipe["seed"], key, f"journal-{event}", plan["generator_version"]]), 16) % spread
            return date.fromordinal(date.fromisoformat(anchor).toordinal() - offset).isoformat()
        except (KeyError, TypeError, ValueError):
            return None

    def expect_note(key, event, when, facts, kind="info"):
        notes[f"journal/{key}/{event}"] = (key, event, when, tuple(map(str, facts)), kind)

    for tenant in kinds["tenant"]:
        key = tenant["key"]
        suffix = "" if key == "tenant" else f"/{key}"
        label = "" if key == "tenant" else f" {key.removeprefix('tenant/')}"
        mailbox = "noc" if key == "tenant" else f"{key.removeprefix('tenant/')}.noc"
        expect_contact(f"contact/operations{suffix}", f"{tenant['attrs'].get('name', '')} NOC duty desk", "operations", tenant["attrs"].get("name", ""), mailbox)
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
                                 f"{site.removeprefix('site/')}.biomedical", site_area(site))
        expect_assignment(obj["key"], contact, "biomedical")
        expect_assignment(site, contact, "biomedical", "/biomedical", "tertiary")
    for site in kinds["site"]:
        key, data = site["key"], site["attrs"]
        name = data.get("name", "")
        address = data.get("physical_address", "")
        if not isinstance(address, str):
            fail("operations-journal-facts", key, "Site history needs a textual address from the actual site record.")
            address = ""
        contact_name = f"{name} facilities desk"
        contact = expect_contact(f"contact/{key}", contact_name, "facilities", name, f"{key.removeprefix('site/')}.facilities",
                                 site_area(key))
        expect_assignment(key, contact, "facilities", "/facilities", "secondary")
        expect_note(key, "access-plan", scheduled(key, "access-plan", recipe.get("as_of"), 60, 31), (contact_name,))
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
        # A provider-backbone third-party carrier answers from its own domain.
        own_domain = recipe.get("profile") == "provider-backbone" and provider != "provider/operator"
        contact = expect_contact(f"contact/{provider}", f"{provider_name} support desk", "carrier", provider_name,
                                 None if own_domain else f"carrier-{provider.removeprefix('provider/')}.support")
        expect_assignment(key, contact, "carrier", "/carrier", "secondary")
        if "commit_rate" in data or recipe.get("profile") != "provider-backbone":  # owned fiber purchases nothing
            expect_note(key, "capacity-request", scheduled(key, "capacity-request", data.get("install_date"), 30, 31),
                        (_rate(data.get("commit_rate")), provider_name, data.get("cid")))
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
                     attrs(far["refs"].get("termination")).get("name"), _rate(term["attrs"].get("port_speed")))
            expect_note(key, "handoff-plan", data.get("install_date"),
                        facts + (() if external else (_rate(far["attrs"].get("port_speed")),)) + (data.get("install_date"),))
        else:
            expect_note(key, "handoff-plan", data.get("install_date"),
                        (provider_name, attrs(term["refs"].get("termination")).get("name"), _rate(term["attrs"].get("port_speed"))),
                        "success")
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
        mailbox = f"{label.strip()}." if label else ""
        # The tenant's authored display name is clipped in prose so the
        # description stays inside the native 200-character bound.
        tenant_label = str(attrs(tenant).get("name", ""))[:40].rstrip()
        contact_name = f"{tenant_label} {titleize(workload)} service desk"
        contact = expect_contact(f"contact/service/{tenant}/{workload}", contact_name, "service", f"{titleize(workload)} within {tenant_label}", f"{mailbox}{workload}.service")
        expect_assignment(key, contact, "service")
        expect_assignment(key, f"contact/operations{'' if tenant == 'tenant' else f'/{tenant}'}", "operations",
                          "/operations", "secondary")
        scope = (refs.get("cluster"), workload)
        if scope not in anchors or int(parts[3]) < int(anchors[scope]["key"].rsplit("/", 1)[1]):
            anchors[scope] = vm
    for vm in anchors.values():
        key, refs = vm["key"], vm["refs"]
        expect_note(key, "resource-plan", scheduled(key, "resource-plan", recipe.get("as_of"), 120, 31),
                    (attrs(refs.get("device")).get("name"),), "success")

    equipment_anchors, supplies, interfaces = {}, defaultdict(list), defaultdict(dict)
    catalog_cages = {}
    for model in hardware_catalog()["models"].values():
        catalog_cages[(model["manufacturer"], model["model"])] = next(
            (p["name"] for p in model["interfaces"]
             if p["type"] in {"1000base-x-sfp", "10gbase-x-sfpp", "25gbase-x-sfp28",
                              "100gbase-x-qsfp28"}), None)
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
        expect_note(key, "equipment-record", scheduled(key, "equipment-record", recipe.get("as_of"), 100, 20),
                    (attrs(refs.get("device_type")).get("model"), data.get("serial"),
                     room, rack, data.get("position"), attrs(access).get("name")), "success")
        if supplies[key]:
            port = min(supplies[key], key=lambda obj: obj["key"])
            module = objects.get(port["refs"].get("module"), {})
            module_refs = module.get("refs", {})
            bay = objects.get(module_refs.get("module_bay"), {})
            if module.get("kind") != "module" or module_refs.get("device") != key or bay.get("refs", {}).get("device") != key:
                fail("operations-journal-facts", key, "Replacement planning must follow the installed supply through its own module and bay.")
            expect_note(key, "psu-replacement-plan", scheduled(key, "psu-replacement-plan", recipe.get("as_of"), 1, 19),
                        (attrs(module_refs.get("module_type")).get("model"), bay.get("attrs", {}).get("name"),
                         module.get("attrs", {}).get("serial")), "warning")
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
            assembly = any(part.get("manufacturer") == maker and part.get("model") == module_type.get("attrs", {}).get("model")
                           and part.get("assembly") for part in hardware_catalog()["optics"]["parts"].values())
            expect_note(key, "optic-replacement-plan", scheduled(key, "optic-replacement-plan", recipe.get("as_of"), 40, 20),
                        (port["attrs"].get("name"), bay.get("attrs", {}).get("name"),
                         f"{maker} {module_type.get('attrs', {}).get('model')}", module.get("attrs", {}).get("serial"),
                         "the whole cable assembly" if assembly else "the transceiver"))

    for role, (title, group) in roles.items():
        # The role carries an authored display name with a namespaced slug; the
        # group keeps the namespace in its name because its canonical slug is
        # derived from it and omitted on the wire for the auto-slug matcher.
        for kind, label in (("contact_role", title), ("contact_group", group)):
            key = f"{kind.replace('_', '-')}/{role}"
            if kind == "contact_group":
                name = f"{ns} {label}"
                expected = {"name": name, "slug": name.lower().replace(" ", "-")}
            else:
                expected = {"name": label, "slug": f"{ns}-{label.lower().replace(' ', '-')}"}
            if (objects.get(key, {}).get("kind") != kind or attrs(key) != expected
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
        expected_name, role, scope, mailbox, area = expected
        phone = re.fullmatch(r"\+1 (\d{3})-555-01\d\d", data["phone"]) if isinstance(data.get("phone"), str) else None
        if not phone or phone.group(1) not in AREA_CODES.values() or (area is not None and phone.group(1) != area):
            fail("operations-contact", key, "Contact phone must be a fictional 555-0100..0199 line in the area code of the metro it serves.")
        description = data.get("description", "")
        responsibility = re.fullmatch(responsibility_forms[role], description) if isinstance(description, str) else None
        if (name != expected_name or data.get("title") != roles[role][0]
                or (data.get("email") != f"{mailbox}@{ns}.example" or len(mailbox) > 64 if mailbox is not None else
                    not (own := re.fullmatch(r"support@([a-z0-9-]{1,40})\.example", str(data.get("email")))) or own.group(1) == ns)
                or not responsibility or responsibility.group(1) != scope or len(description) > 200
                or set(data) != {"name", "title", "phone", "email", "description"}
                or obj["refs"] != {"groups": [f"contact-group/{role}"]}):
            fail("operations-contact", key, "Contact name, safe mailbox, responsibility and group must match its actual scope.")
    for key in contacts:
        if objects.get(key, {}).get("kind") != "contact":
            fail("operations-contact", key, "Required scoped service desk is missing.")
    for obj in kinds["contact_assignment"]:
        if assignments.get(obj["key"]) != obj["refs"] or obj["attrs"] != {"priority": priorities.get(obj["key"])}:
            fail("operations-contact", obj["key"], "Assignment must use the actual tenant, site, provider or workload desk at its "
                 "priority: technical desk primary, local or commercial desk secondary, specialist tertiary.")
    for key, refs in assignments.items():
        if objects.get(key, {}).get("kind") != "contact_assignment" or objects.get(refs["contact"], {}).get("kind") != "contact":
            fail("operations-contact", key, "Required contact assignment or scoped contact is missing.")

    # Finite note forms admit only these claims. Captured values below are checked
    # against the graph; the emitter and its metadata are not validation inputs.
    # One short, human operational line per event; the record itself already
    # shows its fields, so a note states only what happened or what to do.
    forms = {
        "equipment-record": ("Installed", r"([^\n]+) serial ([^\n]+) racked in ([^\n]+), cabinet ([^\n]+) at U([0-9.]+); managed through ([^\n]+)\."),
        "psu-replacement-plan": ("Keep a spare PSU", r"Confirm a like-for-like ([^\n]+) is on hand for ([^\n]+) \(installed serial ([^\n]+)\) before the next maintenance window\."),
        "optic-replacement-plan": ("Optic replacement note", r"([^\n]+) \(([^\n]+)\) holds ([^\n]+) serial ([^\n]+); if it fails, swap in a like-for-like part and replace (the whole cable assembly|the transceiver)\."),
        "access-plan": ("Site access", r"Equipment-room visits are booked through ([^\n]+); give two working days' notice and flag any planned power work\."),
        "capacity-request": ("Order placed", r"Ordered ([^\n]+) from ([^\n]+); quote ([^\n]+) on every call to the carrier\."),
        "handoff-plan": ("In service", r"([^\n]+) handed the circuit over at ([^\n]+) on a ([^\n]+) port\."),
        "resource-plan": ("First instance placed", r"Placed on ([^\n]+); later replicas follow the same sizing\.")}
    if recipe.get("profile") == "provider-backbone":
        forms["handoff-plan"] = ("Circuit handoff plan", r"Circuit: ([^\n]+)\nA termination: ([^\n]+)\nZ termination: ([^\n]+)\nA handoff: ([^\n]+)\nZ handoff: ([^\n]+)\nRecorded service date: ([^\n]+)\nUse both termination records to coordinate the local handoffs\.")
    for obj in kinds["journal_entry"]:
        key = obj["key"]
        if key not in notes:
            fail("operations-journal", key, "Journal has no required immutable site, circuit, workload or equipment event.")
            continue
        target, event, when, facts, expected_kind = notes[key]
        title, body = forms[event]
        if event == "handoff-plan" and target in external_handoffs:
            body = (r"Circuit: ([^\n]+)\nA termination: ([^\n]+)\nZ network boundary: ([^\n]+)\n"
                    r"A handoff: ([^\n]+)\nRecorded service date: ([^\n]+)\nRemote side: upstream carrier network\.\n"
                    r"Use the A termination to coordinate the local handoff; the Z record identifies an external network boundary\.")
        comments = obj["attrs"].get("comments", "")
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) — " + title + "\n" + body, comments) if isinstance(comments, str) else None
        if obj["refs"] != {"assigned_object": target} or obj["attrs"].get("kind") != expected_kind or set(obj["attrs"]) != {"kind", "comments"}:
            fail("operations-journal", key, "Journal must retain its actual subject, its event's kind (completed events success, "
                 "open spares warning, otherwise info) and no account binding.")
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
    _automation(objects, kinds, ns, fail, dedicated(recipe))
    return findings


def _rate(kbps):
    """Journals state rates in operator units; a malformed value stays raw and fails the match."""
    return rate_kbps(kbps) if type(kbps) is int and kbps > 0 else kbps


# Restated, not imported from the builder: the tag vocabulary's label scope
# and the switch roles a baseline config context governs.
_TAG_KINDS = {"hub-site": {"site"}, "dual-homed": {"site"}, "acquired": {"site", "device"},
              "route-reflector": {"device"}, "transit-edge": {"device"}, "managed-ce": {"device"},
              "pci-scope": {"device", "vlan", "prefix"}, "clinical": {"device", "vlan", "prefix"},
              "ot-zone": {"device", "vlan", "prefix"},
              "multi-site": {"virtual_machine"}}
_SWITCH_ROLES = {"role/access", "role/leaf"}
_TAXONOMY = ("device_role", "rack_role")


def _shared(plan, objects, kinds, report):
    """Grouping, typing, tagging and taxonomy obligations every estate carries."""
    ns = plan.get("recipe", {}).get("namespace", "")

    def related(obj, field):
        return objects.get(obj.get("refs", {}).get(field), {})

    for kind, field, target in (("tenant", "group", "tenant_group"), ("cluster", "group", "cluster_group"),
                                ("rack", "group", "rack_group"), ("owner", "group", "owner_group")):
        for obj in kinds[kind]:
            if related(obj, field).get("kind") != target:
                report("operations-group", obj["key"], f"{kind} must reference its {target}.")
    # A circuit group is either a real primary/secondary pair or the set of
    # backbone spans; it never mixes the two or carries an invented member.
    members = defaultdict(list)
    for assignment in kinds["circuit_group_assignment"]:
        members[assignment["refs"].get("group")].append(assignment)
        if related(assignment, "member").get("kind") != "circuit":
            report("operations-circuit-group", assignment["key"], "Group members must be actual circuits.")
    for group in kinds["circuit_group"]:
        entries = members[group["key"]]
        priorities = sorted(entry["attrs"].get("priority", "") for entry in entries)
        spans = all(related(entry, "member").get("refs", {}).get("type") == "circuit-type/backbone" for entry in entries)
        pair = (priorities == ["primary", "secondary"]
                and len({entry["refs"].get("member") for entry in entries}) == 2)
        if not entries or not (pair or (spans and priorities == [""] * len(entries))):
            report("operations-circuit-group", group["key"],
                   "A circuit group is one primary/secondary pair of real circuits, or the backbone spans without priorities.")
    for assignment in kinds["contact_assignment"]:
        if (related(assignment, "object").get("kind") not in {"site", "cluster", "circuit", "virtual_machine", "device", "virtual_circuit"}
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
    for rack in kinds["rack"]:
        template = related(rack, "rack_type")
        if template.get("kind") != "rack_type" or any(template.get("attrs", {}).get(field) != rack["attrs"].get(field)
                                                      for field in ("u_height", "width", "form_factor")):
            report("operations-rack-type", rack["key"], "Rack type must match the actual cabinet height, width and form factor.")

    # Service tier: re-derived from the graph (hosted cluster or private-WAN
    # hub; else active circuits from two providers; else one).
    hubs, carriers = set(), defaultdict(set)
    for obj in kinds["cluster"]:
        hubs.add(obj["refs"].get("scope_site"))
    for obj in kinds["virtual_circuit_termination"]:
        if obj["attrs"].get("role") == "hub":
            hubs.add(related(related(obj, "interface"), "device").get("refs", {}).get("site"))
    for term in kinds["circuit_termination"]:
        circuit = related(term, "circuit")
        if str(term["refs"].get("termination", "")).startswith("site/") and circuit.get("attrs", {}).get("status") == "active":
            carriers[term["refs"]["termination"]].add(circuit.get("refs", {}).get("provider"))
    for field in kinds["custom_field"]:
        choices = related(field, "choice_set")
        values = {choice.split(":", 1)[0] for choice in choices.get("attrs", {}).get("extra_choices", [])}
        consumers = [obj for obj in kinds["site"] if field["attrs"].get("name") in obj["attrs"].get("custom_fields", {})]
        if choices.get("kind") != "custom_field_choice_set" or not consumers or "dcim.site" not in field["attrs"].get("object_types", []):
            report("operations-custom-field", field["key"], "Operations field must have real choices and a compatible site consumer.")
        for obj in consumers:
            selected = obj["attrs"]["custom_fields"][field["attrs"]["name"]].get("selection")
            tier = ("tier-1" if obj["key"] in hubs else "tier-2" if len(carriers[obj["key"]]) >= 2 else "tier-3")
            if selected not in values or selected != tier:
                report("operations-custom-field", obj["key"], "Service tier must be the declared choice the site's actual "
                       "hub role and carrier count imply.")
    for link in kinds["custom_link"]:
        if link["attrs"].get("object_types") != ["dcim.site"] or link["attrs"].get("link_url") != "/dcim/devices/?site_id={{ object.pk }}":
            report("operations-custom-link", link["key"], "Site equipment shortcut must stay on the target's own device inventory.")

    # Tags: each lands only on its declared kinds, every emitted tag is used,
    # and none blankets every candidate of its kinds.
    population = defaultdict(int)
    for obj in objects.values():
        population[obj["kind"]] += 1
    usage = defaultdict(lambda: defaultdict(int))
    for obj in objects.values():
        for tag in obj["refs"].get("tags", []) if isinstance(obj["refs"].get("tags"), list) else []:
            slug = tag.removeprefix("tag/")
            if objects.get(tag, {}).get("kind") != "tag" or obj["kind"] not in _TAG_KINDS.get(slug, ()):
                report("operations-tag", obj["key"], f"{tag} is not a reviewed tag for a {obj['kind']}.")
            usage[tag][obj["kind"]] += 1
    for tag in kinds["tag"]:
        counts = usage[tag["key"]]
        if not counts:
            report("operations-tag", tag["key"], "An emitted tag must label at least one object.")
        elif all(counts[kind] >= population[kind] for kind in _TAG_KINDS.get(tag["key"].removeprefix("tag/"), ())):
            report("operations-tag", tag["key"], "A tag on every candidate of its kinds carries no information.")
        if tag["attrs"].get("slug") != f"{ns}-{tag['key'].removeprefix('tag/')}":
            report("operations-tag", tag["key"], "Tag slug must keep the estate namespace.")

    # Taxonomy lists show only what the estate uses, each role in its own colour.
    referenced = {target for obj in objects.values() for value in obj["refs"].values()
                  for target in (value if isinstance(value, list) else [value]) if isinstance(target, str)}
    passive = {(model.get("manufacturer"), model.get("model")) for model in hardware_catalog()["models"].values()
               if model.get("front_ports")}
    for kind in _TAXONOMY + ("device_type",):
        for obj in kinds[kind]:
            if obj["key"] in referenced:
                continue
            # The device-type library is otherwise fixed (growth and scenario
            # snapshots must not delete one); only passive cabling types follow use.
            maker = objects.get(obj["refs"].get("manufacturer"), {}).get("attrs", {}).get("name")
            if kind != "device_type" or (maker, obj["attrs"].get("model")) in passive:
                report("operations-taxonomy", obj["key"], f"Unreferenced {kind} must not be emitted.")
    colours = defaultdict(list)
    for role in kinds["device_role"]:
        colours[role["attrs"].get("color")].append(role["key"])
    for colour, roles in colours.items():
        if len(roles) > 1:
            report("operations-taxonomy", roles[1], f"Device roles {', '.join(roles)} share colour {colour}.")

    # The switch baseline context disables unused ports: an uncabled data port
    # on a governed switch must be shut.
    cabled = {cable["refs"].get(side) for cable in kinds["cable"] for side in ("a", "b")}
    governed = {obj["key"] for obj in kinds["device"] if obj["refs"].get("role") in _SWITCH_ROLES}
    for port in kinds["interface"]:
        if (port["refs"].get("device") in governed and port["key"] not in cabled and port["attrs"].get("enabled") is not False
                and port["attrs"].get("type") not in (None, "virtual", "lag", "bridge")
                and not str(port["attrs"].get("type")).startswith("ieee802.11") and not port["attrs"].get("mgmt_only")):
            report("operations-unused-port", port["key"], "The switch baseline disables unused ports; this uncabled port is still enabled.")


def validate(plan):
    """Check finished records, including unoccupied reservations and disk totals."""
    objects = {o["key"]: o for o in plan["objects"]}
    kinds = defaultdict(list)
    for obj in objects.values():
        kinds[obj["kind"]].append(obj)
    findings = _context(plan, objects, kinds)

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    if "owner/operations" in objects:
        _shared(plan, objects, kinds, report)
    if not any(c.get("operations") for c in plan.get("contracts", [])):
        return sorted(findings, key=lambda f: (f["code"], f["object"]))

    def related(obj, field):
        return objects.get(obj.get("refs", {}).get(field), {})

    expected = {"circuit_group", "circuit_group_assignment", "cluster_group", "contact", "contact_group", "contact_role",
                "contact_assignment", "provider_account", "rack_type", "rack_group", "tenant_group", "virtual_disk",
                "virtual_machine_type", "custom_field", "custom_field_choice_set", "journal_entry", "custom_link",
                "owner", "owner_group", "cable_bundle",
                "config_context", "export_template", "webhook", "event_rule"}
    for kind in sorted(expected - kinds.keys()):
        report("operations-coverage", kind, "Operations coverage requires a connected example of this kind.")
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
    occupied = defaultdict(set)
    for device in kinds["device"]:
        position = device["attrs"].get("position")
        if position is not None:
            height = related(device, "device_type").get("attrs", {}).get("u_height", 0)
            occupied[device["refs"].get("rack")].update(range(math.floor(position), math.ceil(position+height)))
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
        for obj in kinds["site"]:
            if field["attrs"].get("name") in obj["attrs"].get("custom_fields", {}) and field["key"] not in obj.get("meta", {}).get("requires", []):
                report("operations-custom-field", obj["key"], "A field value must depend on its definition before export.")
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
