"""Independent scope, chronology and graph-fact checks for operations inventory."""

from collections import defaultdict
from datetime import date, timedelta
from itertools import product
import math
import re

from .model import digest, hardware_catalog, serial_date_code
from .naming import bandwidth, dedicated, titleize


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
# Restated: the builder's bounded week shift that keeps detachable serials unique.
SERIAL_SHIFT_WEEKS = 8
# Restated: one third-party circuit delivery in this many slipped (seeded per circuit).
SLIP_ONE_IN = 5
# Restated, not imported: real metro area codes; the 555-0100..0199 block is
# reserved for fictional use, so no contact can carry a dialable number.
AREA_CODES = {"Chicago": "312", "Detroit": "313", "Cleveland": "216", "Milwaukee": "414"}
# Restated: one invented carrier-hotel operator per metro, keyed by the state
# on the PoP's own postal address (each authored metro sits in its own state).
PROVIDER_COLOCATION = {"Illinois": ("Windward Interconnect", "windward-interconnect.example"),
                       "Michigan": ("Motorline Data Centers", "motorline-dc.example"),
                       "Ohio": ("Cuyahoga Colocation", "cuyahoga-colo.example"),
                       "Wisconsin": ("Kinnickinnic Colocation", "kinnickinnic-colo.example")}
# Restated journal time-of-day bands (UTC, [start, end) hours): orders,
# installs and paperwork in US business hours, maintenance at night.
_BANDS = {"business": (14, 22), "night": (4, 9)}
# Restated provider history facts (estates/timeline.py, DESIGN v0.18 §4.1):
# vendor notices are quoted "per vendor notice"; the others are authored.
NS2_ADOPTED = date(2018, 3, 1)
NOTICES_BEGIN = date(2025, 4, 1)
MX80_END_OF_SALE, MX80_END_OF_SUPPORT = date(2021, 6, 30), date(2026, 6, 30)
MX304_AVAILABLE = date(2022, 7, 1)
ACX5048_LAST_ORDER, ACX5048_END_OF_SUPPORT = date(2022, 12, 31), date(2027, 12, 31)
# Restated carrier paperwork policy: one in three leased third-party services
# carries completed maintenance notices, one in two long-lived (five years)
# access or transit circuits carries committed-rate upgrades, and every
# leased term renews every 36 months.
NOTICE_TYPES = frozenset({"circuit-type/backbone", "circuit-type/transit", "circuit-type/noc-access",
                          "circuit-type/cellular-oob", "circuit-type/ix-port"})
NOTICE_ONE_IN, CIR_ONE_IN, CIR_MIN_YEARS, TERM_MONTHS = 3, 2, 5, 36
# Journal events that record a device arriving in its rack (an ordered
# successor holds its reserved units from the order).
_INSTALL_EVENTS = frozenset({"equipment-record", "installed", "replaced", "racked", "ordered", "received"})

# Finite journal forms by event: a bold title and the body each claim may take.
_JOURNAL_FORMS = {
    "equipment-record": ("Installed", r"Racked and cabled under change ([^\n;]+); the visit was booked through ([^\n]+)\."),
    "service-order": ("Service order", r"Ordered by ([^\n;]+); provisioning tracked under change ([^\n.]+)\."),
    "disconnect-order": ("Disconnect order", r"Disconnect ordered under change ([^\n;]+); recover the handoff optics and cabling once the circuit is withdrawn\."),
    "delivery-slip": ("Delivery slipped", r"([^\n;]+) missed the committed handover date; escalated to its support desk\."),
    "handover": ("Handed over", r"Accepted into service under change ([^\n.]+)\.(?: ([^\n]+) support desk confirmed the handover and closed its ticket\.)?"),
    "resource-plan": ("First instance placed", r"Placed on ([^\n]+); later replicas follow the same sizing\."),
    "service-ceased": ("Service ceased", r"Service ceased; NID not recovered — premises access ended\."),
    # Provider paperwork (v0.18 P1-10..12) and the NOC's notice policy.
    "cross-connect-order": ("Cross-connect ordered", r"Letter of authorization ([A-Z]+-[A-Z]+-\d{6}) issued to ([^\n]+) for "
                            r"cross-connect ([^\n ]+)\.(?: Z side: ([^\n]+)\.)? MMR-only jumper; the hotel runs it from the "
                            r"meet-me room to our demarcation panel\."),
    "cir-upgrade": ("Committed rate raised", r"Committed rate raised from ([^\n]+) to ([^\n]+) under change (CHG\d{7}), "
                    r"on the same ([^\n ]+) handoff\."),
    "term-renewal": ("Term renewed", r"([^\n]+) service term renewed for (\d+) months under change (CHG\d{7}); "
                     r"service and handoff unchanged\."),
    "maintenance": ("Provider maintenance completed", r"Provider: ([^\n]+)\nMaintenance ID: ([^\n]+)\nAccount: ([^\n]+)\n"
                    r"Window: ([^\n]+) UTC\nImpact: up to (\d+) hours of interruption inside the window, per provider notice\n"
                    r"Status: completed"),
    "notice-journaling": ("Provider notices journaled", r"From today the NOC journals each completed third-party maintenance "
                          r"notice on the circuit it touched \(provider, maintenance ID, account, window and stated impact, "
                          r"as the provider sent them\) and each PoP's annual cage audit\."),
    "cage-audit": ("Cage audit", r"Annual cage audit with ([^\n]+) remote hands under change (CHG\d{7}): cabinet labels, "
                   r"blanking and power cords walked against this record\."),
    "ix-port": ("IX port turned up", r"([^\n]+) port ([^\n ]+) turned up on ([^\n ]+) under change (CHG\d{7})"
                r"(?:, replacing the ([^\n]+) port after the exchange relocated)?\."),
    "ix-port-shut": ("IX port shut", r"([^\n]+) port ([^\n ]+) shut on ([^\n ]+) after the exchange relocated to ([^\n]+); "
                     r"disconnect ordered under change (CHG\d{7})\."),
    # Provider plant history (v0.18 §3-§4): generations, gaps and spares.
    "installed": ("Installed", r"Racked as the launch PE of ([^\n]+) under (CHG\d{7})\."),
    "cut-over": ("Cut over", r"Replaced Juniper MX80 ([^\n]+) under (CHG\d{7}); services cut over (\d{4}-\d{2})\. "
                 r"Spans re-lit at 100G \(the MX80 has only 10G XFP\)\."),
    "cut-over-relic": ("Cut over", r"Services moved to ([^\n]+) under (CHG\d{7}); uncabled and powered off, de-rack scheduled\."),
    "end-of-support": ("End of support", r"Vendor end of support (\S+) per vendor notice \(end of sale (\S+)\)\."),
    "end-of-sale": ("Last order date", r"ACX5048 last order (\S+), end of support (\S+), per vendor notice\. "
                    r"No aggregation refresh has started\."),
    "replaced": ("Replaced predecessor", r"Replaced ([^\n]+), under (CHG\d{7}); (services|management) cut over (\d{4}-\d{2})\."),
    "removed": ("Removed", r"Removed: ([^\n]+), (CHG\d{7})\. (U\d+(?:-U\d+)?) left empty and blanked; units are not reused\."),
    "spare-racked": ("Cold spare racked", r"Pre-racked as the metro's cold-spare aggregation chassis under (CHG\d{7}); ([^\n]+)\."),
    "racked": ("Racked, awaiting activation", r"Racked under change (CHG\d{7}); activation at the MX304 cut-over: the MX204 "
               r"port budget is exhausted \(PIC0 is limited to 3x100G while all eight 10G SFP\+ ports are in use\)\."),
    "successor-ordered": ("Successor ordered", r"MX304 successor ([^\n ]+) ordered under (CHG\d{7}): ([^\n]+)\."),
    "ordered": ("Ordered", r"Purchase order raised under (CHG\d{7}); units reserved in the cabinet\."),
    "received": ("Received and staged", r"Shipment received; chassis racked and staged, not in service\.")}


def _stamp(day, key, band="business"):
    """Restated: a journal's ``created`` instant inside its band, keyed by the journal."""
    low, high = _BANDS[band]
    n = int(digest(["journal-time", key]), 16)
    return f"{day}T{low + n % (high - low):02}:{(n // 97) % 60:02}:00Z"


def _add_months(day, months):
    year, month = divmod(day.month - 1 + months, 12)
    return date(day.year + year, month + 1, min(day.day, 28))


def _initials(name):
    return "".join(word[0] for word in name.split() if word[0].isalpha()).upper()


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
        elif idle := sorted(set(roles) - {obj["refs"].get("role") for obj in listed("device")}):
            fail("automation-context", scoped["key"],
                 f"The role-scoped context targets roles no device holds: {', '.join(idle)}.")
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

    def site_of(target):
        """A termination target's site, through the room it may name."""
        target = str(target or "")
        return str(objects.get(target, {}).get("refs", {}).get("site", "")) if target.startswith("location/") else target

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    roles = {"operations": ("Technical escalation", "Operations desk"),
             "facilities": ("Facilities access", "Site facilities"),
             "carrier": ("Carrier escalation", "Carrier support"),
             "service": ("Service support", "Service teams")}
    if recipe.get("profile") == "hospital-clinics":
        roles["biomedical"] = ("Biomedical support", "Biomedical engineering")
    contacts, assignments, notes, priorities = {}, {}, {}, {}

    def provider_customer(tenant):
        # A provider's customer desk answers from the customer's own domain.
        return recipe.get("profile") == "provider-backbone" and str(tenant).startswith("tenant/cust-")
    infrastructure_roles = {f"role/{role}" for role in (
        "wan-edge", "distribution", "access", "spine", "leaf", "server", "management",
        "ap", "console-server", "stack", "laboratory", "provider-edge", "customer-edge",
        "aggregation", "ddos-mitigation", "time-server")}

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

    # Each site's service day: its first circuit's install date, read from the
    # graph (never from metadata). Installs lead it; no note predates it.
    service_day = {}
    for term in kinds["circuit_termination"]:
        site = site_of(term["refs"].get("termination"))
        day = attrs(term["refs"].get("circuit")).get("install_date")
        if site.startswith("site/") and isinstance(day, str):
            service_day[site] = min(day, service_day.get(site, day))

    # Every device installs 7-37 days before its site's service day; every
    # serial's date code is a manufacture 30-180 days before the install of the
    # unit it sits in, or before the circuit an optic's own port serves.
    # Re-derived from the graph alone.
    circuit_day = {t["key"]: attrs(t["refs"].get("circuit")).get("install_date") for t in kinds["circuit_termination"]
                   if attrs(t["refs"].get("circuit")).get("install_date")}
    port_day = {}
    for cable in kinds["cable"]:
        ends = (cable["refs"].get("a"), cable["refs"].get("b"))
        for port, far in (ends, ends[::-1]):
            if isinstance(circuit_day.get(far), str) and objects.get(port, {}).get("kind") == "interface":
                port_day[port] = min(circuit_day[far], port_day.get(port, circuit_day[far]))
    # A device that arrived after its site (a provider PoP's later generation,
    # successor or spare) shows its own arrival as a dated install journal; a
    # provider PoP's other plant arrived with the PoP's launch or its PE
    # refresh (the frozen ``provider-timeline`` ledger), or with another
    # arrival journaled at that site. Every other device installs on its
    # site's schedule. ``installed_on`` holds the candidate install days.
    reservations = plan.get("reservations") if isinstance(plan.get("reservations"), dict) else {}

    def ledger_day(event, pop):
        value = reservations.get(f"provider-timeline/{event}/{pop}")
        day = value.get("day") if isinstance(value, dict) else None
        return date.fromordinal(day).isoformat() if type(day) is int and day > 0 else None

    journals_on = defaultdict(dict)
    for obj in kinds["journal_entry"]:
        target = obj["refs"].get("assigned_object")
        if isinstance(target, str) and obj["key"].startswith(f"journal/{target}/"):
            journals_on[target][obj["key"].removeprefix(f"journal/{target}/")] = obj

    def arrivals(device):
        """Install days this device's own journals record (dates read from ``created``).
        Its equipment record is excluded: that date is checked against these."""
        return sorted(str(obj["attrs"].get("created", ""))[:10] for event, obj in journals_on[device].items()
                      if event in _INSTALL_EVENTS - {"equipment-record"}
                      or (event == "cut-over" and obj["attrs"].get("kind") == "success"))

    site_arrivals = defaultdict(set)
    for device in kinds["device"]:
        site_arrivals[device["refs"].get("site")].update(arrivals(device["key"]))
    installed_on = {}
    for device in kinds["device"]:
        site = device["refs"].get("site")
        anchor = service_day.get(site)
        schedule = (scheduled(device["key"], "equipment-record", anchor, 7, 31) if anchor
                    else scheduled(device["key"], "equipment-record", recipe.get("as_of"), 100, 20))
        pop = str(site).removeprefix("site/pop-")
        plant = (recipe.get("profile") == "provider-backbone" and str(site).startswith("site/pop-")
                 and ledger_day("launch", pop))
        own = arrivals(device["key"]) if plant else []
        installed_on[device["key"]] = (own if own else sorted({schedule, ledger_day("launch", pop), ledger_day("refresh", pop),
                                                                  *site_arrivals[site]} - {None}) if plant else [schedule])
    serial_catalog = hardware_catalog()
    host_specs = {(m["manufacturer"], m["model"]): m for m in serial_catalog["models"].values()}
    optic_formats = serial_catalog["optics"]["serial_formats"]

    def host_spec(device):
        dtype = objects.get(objects.get(device, {}).get("refs", {}).get("device_type"), {})
        return host_specs.get((attrs(dtype.get("refs", {}).get("manufacturer")).get("name"), dtype.get("attrs", {}).get("model")), {})

    def check_serial(key, fmt, serial, installed, slack_weeks=0):
        """The printed date code is a manufacture 30-180 days before the install
        (plus the builder's bounded uniqueness shift for detachable modules);
        ``installed`` lists the install days the graph admits."""
        if not fmt or not isinstance(serial, str) or not serial or not installed or not all(installed):
            return
        code = serial_date_code(fmt, serial)

        def fits(day):
            latest = date.fromisoformat(day).toordinal() - 30
            earliest = date.fromisoformat(day).toordinal() - 180 - 7 * slack_weeks
            if code[1] is None:
                # redate_serial prints the ISO year (a manufacture on 30 December
                # 2019 is ISO 2020-W01), so compare ISO years, not calendar years.
                return (date.fromordinal(earliest).isocalendar()[0] <= code[0]
                        <= date.fromordinal(latest).isocalendar()[0])
            try:
                monday = date.fromisocalendar(code[0], code[1], 1).toordinal()
            except ValueError:
                return False
            return earliest - 6 <= monday <= latest
        if code is None or not any(fits(day) for day in installed):
            fail("operations-serial-date", key, "A serial's date code must be a manufacture week 30-180 days before "
                 "the install the graph's own circuit dates imply.")
    for device in kinds["device"]:
        check_serial(device["key"], host_spec(device["key"]).get("serial_format"), device["attrs"].get("serial"),
                     installed_on[device["key"]])
    assemblies = defaultdict(list)
    for module in kinds["module"]:
        assemblies[module["attrs"].get("serial")].append(module)
    for serial, members in assemblies.items():
        anchors, fmt = [], None
        for module in members:
            owner = module["refs"].get("device")
            module_type = objects.get(module["refs"].get("module_type"), {})
            maker = attrs(module_type.get("refs", {}).get("manufacturer")).get("name")
            fmt = (host_spec(owner).get("module_serial_format") if module["key"].startswith(f"{owner}/module/")
                   else optic_formats.get(maker, optic_formats.get("Generic")))
            # An optic staged for a handoff not yet in service arrived recently.
            day = (port_day.get(module["key"].removeprefix("optics-module/"))
                   or (scheduled(module["key"], "staged", recipe.get("as_of"), 7, 31)
                       if module["attrs"].get("status") == "staged" else None))
            anchors.append([day] if day else installed_on.get(owner))
        if all(anchors):
            # The assembly is dated from its earliest end's install.
            check_serial(members[0]["key"], fmt, serial, sorted({min(days) for days in product(*anchors)}), SERIAL_SHIFT_WEEKS)

    def later(when, floor):
        return max(when, floor) if isinstance(when, str) and isinstance(floor, str) else when

    def change(key, work=""):
        """The stable change ticket a subject's work cites (restated seeded choice)."""
        try:
            return f"CHG{1000000 + int(digest([recipe['seed'], key, 'journal-change' + work, plan['generator_version']]), 16) % 9000000:07d}"
        except (KeyError, TypeError):
            return None

    def expect_note(key, event, when, facts, kind="info", band="business", form=None):
        """``when`` is the event's day, the set of days the graph admits for it,
        or an inclusive (first, last) window; ``form`` defaults to the event
        without its ordinal (``term-renewal-2`` -> ``term-renewal``)."""
        notes[f"journal/{key}/{event}"] = (key, form or re.sub(r"-\d+$", "", event), when, tuple(map(str, facts)), kind, band)

    for tenant in kinds["tenant"]:
        key = tenant["key"]
        suffix = "" if key == "tenant" else f"/{key}"
        label = "" if key == "tenant" else f" {key.removeprefix('tenant/')}"
        mailbox = ("noc" if key == "tenant" else f"noc@{key.removeprefix('tenant/cust-')}.example" if provider_customer(key)
                   else f"{key.removeprefix('tenant/')}.noc")
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
        contact_name, mailbox = f"{name} facilities desk", f"{key.removeprefix('site/')}.facilities"
        if recipe.get("profile") == "provider-backbone" and key.startswith("site/pop-"):
            # A PoP cage's facilities desk is its carrier hotel's remote hands.
            lines = address.split("\n")
            colo = PROVIDER_COLOCATION.get(lines[1].rpartition(", ")[2] if len(lines) > 1 else None, ("", ""))
            contact_name, mailbox = f"{colo[0]} remote hands at {name}", f"remote-hands@{colo[1]}"
        elif provider_customer(site["refs"].get("tenant", "")):
            mailbox = (f"facilities.{str(data.get('facility') or key.removeprefix('site/'))[:40].lower()}"
                       f"@{site['refs']['tenant'].removeprefix('tenant/cust-')}.example")
        contact = expect_contact(f"contact/{key}", contact_name, "facilities", name, mailbox, site_area(key))
        expect_assignment(key, contact, "facilities", "/facilities", "secondary")
        # Standing access policy lives in the site's own comments, not a journal.
        policy = (f"**Site access**\n\nEquipment-room visits are booked through {contact_name}; "
                  "give two working days' notice and flag any planned power work.")
        if not str(data.get("comments", "")).endswith(policy) or str(data.get("comments", "")).count("**Site access**") != 1:
            fail("operations-site-access", key, "Site comments must end with one access policy naming the site's own facilities desk.")
    terms, all_terms = defaultdict(list), defaultdict(list)
    for term in kinds["circuit_termination"]:
        all_terms[term["refs"].get("circuit")].append(term)
        if term["attrs"].get("term_side") == "A":
            terms[term["refs"].get("circuit")].append(term)

    def colocation(site):
        """The carrier hotel a provider site stands in, read from its own postal address."""
        lines = str(attrs(site).get("physical_address", "")).split("\n")
        return PROVIDER_COLOCATION.get(lines[1].rpartition(", ")[2] if len(lines) > 1 else None, (None, None))[0]

    def carrier_paperwork(circuit, provider_name):
        """A provider circuit's cross-connect orders, rate upgrades, renewals and
        maintenance notices, from the restated policy and the circuit's own fields."""
        key, data, refs = circuit["key"], circuit["attrs"], circuit["refs"]
        try:
            installed, as_of = date.fromisoformat(data["install_date"]), date.fromisoformat(recipe["as_of"])
        except (KeyError, TypeError, ValueError):
            return

        def pick(label):
            return int(digest([ns, key, label]), 16)
        for term in all_terms[key]:
            side, target = term["attrs"].get("term_side"), str(term["refs"].get("termination", ""))
            colo = colocation(target) if target.startswith("site/") else None
            if not term["attrs"].get("xconnect_id") or colo is None:
                continue
            facts = (f"LOA-{_initials(colo)}-{100000 + pick(f'loa{side}') % 900000}", colo, term["attrs"]["xconnect_id"])
            expect_note(term["key"], "cross-connect-order",
                        scheduled(term["key"], "cross-connect-order", data["install_date"], 14, 17),
                        facts + ((term["attrs"]["pp_info"],) if term["attrs"].get("pp_info") else ()))
        if data.get("status") != "active":
            return
        third_party = refs.get("type") in NOTICE_TYPES and refs.get("provider") != "provider/operator"
        access = (str(refs.get("type", "")).endswith("-access") and refs.get("provider") == "provider/operator"
                  and refs.get("tenant") != "tenant")
        rate = data["commit_rate"] // 1000 if type(data.get("commit_rate")) is int else 0
        upgrades = [event for event in journals_on[key] if re.fullmatch(r"cir-upgrade-\d+", event)]
        if ((access or refs.get("type") == "circuit-type/transit") and rate
                and (as_of - installed).days >= 365 * CIR_MIN_YEARS and pick("cir-upgrade") % CIR_ONE_IN == 0):
            # The lower tiers come from the estate's own ladder and the record
            # keeps today's rate: each raise starts where the previous ended
            # and the last ends at the circuit's committed rate.
            count = len(upgrades)
            if sorted(upgrades) != sorted(f"cir-upgrade-{n}" for n in range(1, count + 1)) or count > 1 + pick("cir-count") % 2:
                fail("operations-journal", key, "Committed-rate upgrades are one or two numbered steps on a chosen long-lived circuit.")
                count = 0
            speeds = {t["attrs"].get("port_speed") for t in terms[key]}
            speed = next(iter(speeds)) if len(speeds) == 1 else None
            handoff = f"{speed // 1000000}G" if type(speed) is int and speed % 1000000 == 0 else None
            steps = []
            for n in range(1, count + 1):
                found = re.search(r"from (\d+) (Mbps|Gbps) to (\d+) (Mbps|Gbps)",
                                  str(journals_on[key][f"cir-upgrade-{n}"]["attrs"].get("comments", "")))
                steps.append(tuple(int(found.group(i)) * (1000 if found.group(i + 1) == "Gbps" else 1) for i in (1, 3))
                             if found else (0, 0))
            chained = (all(0 < low < high for low, high in steps) and all(a[1] == b[0] for a, b in zip(steps, steps[1:]))
                       and all(high == rate for _, high in steps[-1:]))
            span = (as_of - installed).days - 365 - 30
            for n, (low, high) in enumerate(steps, 1):
                expect_note(key, f"cir-upgrade-{n}", (installed + timedelta(days=365 + span * n // (count + 1))).isoformat(),
                            (bandwidth(low), bandwidth(high), change(key, f"-cir-{n}"), handoff) if chained else ("unchained rates",))
        if not third_party:
            return
        n = 1
        while (renewal := _add_months(installed, n * TERM_MONTHS)) < as_of:
            # A leased service renews on each full term's install anniversary.
            expect_note(key, f"term-renewal-{n}", renewal.isoformat(), (provider_name, TERM_MONTHS, change(key, f"-term-{n}")))
            n += 1
        if pick("maintenance") % NOTICE_ONE_IN:
            return
        first = max(NOTICES_BEGIN, installed + timedelta(days=30))
        room = (as_of - first).days - 1
        account = attrs(refs.get("provider_account")).get("account", "not on file")
        count = 1 + pick("maintenance-count") % 2
        for n in range(count if room > 0 else 0):
            when = (first + timedelta(days=room * n // count + pick(f"maintenance-day-{n}") % max(1, room // count))).isoformat()
            event = f"maintenance-{n + 1}"
            # The notice's window opens at the entry's own night-band time.
            hours, minutes = _stamp(when, f"journal/{key}/{event}", "night")[11:16].split(":")
            start = 60 * int(hours) + int(minutes)
            length = 2 + pick(f"maintenance-hours-{n}") % 3
            end = start + 60 * length
            expect_note(key, event, when, (
                provider_name, f"{_initials(provider_name)}-MNT-{when[:4]}-{1000 + pick(f'maintenance-id-{n}') % 9000}", account,
                f"{when} {start // 60:02}:{start % 60:02}–{end // 60 % 24:02}:{end % 60:02}", length), "info", "night")
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
        # A provider backbone's own circuits are its products: no carrier desk
        # escalates them and their order is a service order, never a purchase.
        own = recipe.get("profile") == "provider-backbone" and provider == "provider/operator"
        if not own:
            # A provider-backbone third-party carrier answers from its own domain.
            contact = expect_contact(f"contact/{provider}", f"{provider_name} support desk", "carrier", provider_name,
                                     None if recipe.get("profile") == "provider-backbone"
                                     else f"carrier-{provider.removeprefix('provider/')}.support")
            expect_assignment(key, contact, "carrier", "/carrier", "secondary")
        ceased = data.get("status") == "decommissioned"
        if data.get("termination_date") is not None:
            if data.get("status") not in {"deprovisioning", "decommissioned"}:
                fail("operations-journal", key, "Only a circuit being withdrawn or already withdrawn carries a disconnect date.")
            # Ordered before the earlier of today and the disconnect itself.
            expect_note(key, "disconnect-order", scheduled(key, "disconnect-order",
                                                           min(str(recipe.get("as_of")), str(data["termination_date"])), 3, 25),
                        (change(key, "-disconnect"),), "warning")
        if ceased:
            # A former customer's withdrawn circuit: no terminations remain,
            # and its service ceased on its own termination date.
            if all_terms[key] or not data.get("termination_date") or not data.get("install_date"):
                fail("operations-journal", key, "A decommissioned circuit keeps its install and termination dates and no terminations.")
            expect_note(key, "service-ceased", data.get("termination_date"), ())
        if data.get("status") in {"planned", "provisioning"}:
            if "install_date" in data:
                fail("operations-journal", key, "A circuit not yet in service has no install date.")
            continue  # and no dated order or handoff history yet
        if own and "commit_rate" in data:
            buyer = "the NOC" if refs.get("tenant") == "tenant" else attrs(refs.get("tenant")).get("name")
            expect_note(key, "service-order", scheduled(key, "service-order", data.get("install_date"), 30, 31),
                        (buyer, change(key)))
        local = terms[key]
        if not ceased and (len(local) != 1 or objects.get(site_of(local[0]["refs"].get("termination")), {}).get("kind") != "site"):
            fail("operations-journal", key, "Handoff history needs one actual A-side site termination.")
        expect_note(key, "handover", data.get("install_date"),
                    (change(key),) + ((provider_name,) if provider != "provider/operator" else ()), "success")
        if provider != "provider/operator" and int(digest([ns, key, "journal-slip"]), 16) % SLIP_ONE_IN == 0:
            expect_note(key, "delivery-slip", scheduled(key, "delivery-slip", data.get("install_date"), 5, 16),
                        (provider_name,), "warning")
        if recipe.get("profile") == "provider-backbone":
            carrier_paperwork(circuit, provider_name)
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
        expect_note(key, "resource-plan", later(scheduled(key, "resource-plan", recipe.get("as_of"), 120, 31),
                                                service_day.get(objects.get(refs.get("device"), {}).get("refs", {}).get("site"), "")),
                    (attrs(refs.get("device")).get("name"),), "success")

    def observed(target, event, form):
        """The captured claims of an existing journal in ``form``, or None."""
        obj = journals_on[target].get(event)
        title, body = _JOURNAL_FORMS[form]
        found = re.fullmatch(r"\*\*" + re.escape(title) + r"\*\* · (\d{4}-\d{2}-\d{2})\n\n" + body,
                             str(obj["attrs"].get("comments", ""))) if obj else None
        return found.groups() if found else None

    def provider_history():
        """The provider plant's dated history (v0.18 §3-§4), re-derived from the
        frozen ``provider-timeline`` ledger, device models and rack contents."""
        as_of = str(recipe.get("as_of"))
        if as_of > NOTICES_BEGIN.isoformat() and "site/dc-01" in objects:
            expect_note("site/dc-01", "notice-journaling", NOTICES_BEGIN.isoformat(), ())
        model = lambda device: str(attrs(objects.get(device, {}).get("refs", {}).get("device_type")).get("model", ""))
        units = defaultdict(dict)
        for device in kinds["device"]:
            position = device["attrs"].get("position")
            height = attrs(device["refs"].get("device_type")).get("u_height", 1)
            if type(position) in (int, float):
                for unit in range(math.floor(position), math.ceil(position + height)):
                    units[device["refs"].get("rack")][unit] = device["key"]
        removals = defaultdict(set)
        for rack in kinds["rack"]:
            site = str(rack["refs"].get("site", ""))
            launch = ledger_day("launch", site.removeprefix("site/pop-"))
            for event in [e for e in journals_on[rack["key"]] if e.startswith("removed/")]:
                groups = observed(rack["key"], event, "removed")
                facts = ("not a removal",)
                if groups:
                    first, _, last = groups[3].removeprefix("U").partition("-U")
                    span = range(int(first), int(last or first) + 1)
                    # The gap stays empty and blanked: only blanking panels fill it.
                    blanked = all(model(units[rack["key"]].get(unit)).startswith("Blanking Panel") for unit in span)
                    legacy = re.fullmatch(re.escape(str(attrs(site).get("facility", "")).lower()) + r"-rtr[12]",
                                          groups[1].removeprefix("Juniper MX80 "))
                    if blanked and (legacy and groups[1].startswith("Juniper MX80 ")
                                    or re.fullmatch(r"the original [^\n,;]+; not inventoried", groups[1])):
                        facts = (groups[1], change(f"{rack['key']}/{event.removeprefix('removed/')}", "-removed"), groups[3])
                        removals[site].add((groups[1], groups[0]))
                expect_note(rack["key"], event, (launch, as_of), facts, form="removed")
        for site in kinds["site"]:
            key = site["key"]
            pop = key.removeprefix("site/pop-")
            launch, refresh = ledger_day("launch", pop), ledger_day("refresh", pop)
            if not key.startswith("site/pop-") or not launch:
                continue
            prefix = f"device/{key.removeprefix('site/')}/"
            facility = str(site["attrs"].get("facility", "")).lower()
            colo = colocation(key)
            # The annual cage audit on each launch anniversary since journaling began.
            day = date.fromisoformat(launch)
            for year in range(NOTICES_BEGIN.year, date.fromisoformat(as_of).year + 1):
                when = day.replace(year=year, day=min(day.day, 28))
                if colo and NOTICES_BEGIN <= when < date.fromisoformat(as_of) and when > day:
                    expect_note(key, f"cage-audit-{year}", when.isoformat(), (colo, change(key, f"-audit-{year}")), form="cage-audit")
            for n, side in enumerate("ab", 1):
                pe, relic = f"{prefix}pe-{side}", f"{prefix}legacy-pe-{side}"
                if refresh:
                    # The refresh cut each PE over from its MX80 on the ledger's day.
                    predecessor = attrs(relic).get("name") if relic in objects else f"{facility}-rtr{n}"
                    expect_note(pe, "cut-over", refresh, (predecessor, change(pe, "-cutover"), refresh[:7]), "success")
                if relic in objects:
                    if not refresh or model(relic) != "MX80" or not re.fullmatch(re.escape(facility) + r"-rtr[12]", str(attrs(relic).get("name"))):
                        fail("operations-journal", relic, "A retained predecessor is an MX80 with its legacy name at a refreshed PoP.")
                    expect_note(relic, "installed", launch, (attrs(key).get("name"), change(relic)), "success")
                    expect_note(relic, "cut-over", refresh, (attrs(pe).get("name"), change(pe, "-cutover")), "warning", form="cut-over-relic")
                    if MX80_END_OF_SUPPORT.isoformat() <= as_of:
                        expect_note(relic, "end-of-support", MX80_END_OF_SUPPORT.isoformat(),
                                    (MX80_END_OF_SUPPORT, MX80_END_OF_SALE), "warning")
                successor = f"{prefix}pe-{side}2"
                if successor in objects:
                    ordered = (observed(successor, "ordered", "ordered") or (None,))[0]
                    expect_note(successor, "ordered", (MX304_AVAILABLE.isoformat(), as_of), (change(successor, "-order"),))
                    reasons = (observed(pe, "successor-ordered", "successor-ordered") or (None,) * 4)[3]
                    expect_note(pe, "successor-ordered", ordered or "unordered",
                                (attrs(successor).get("name"), change(successor, "-order"), reasons), "warning")
                    if attrs(successor).get("status") == "staged":
                        expect_note(successor, "received", (ordered, as_of), (), "success")
            for device in [d for d in kinds["device"] if d["refs"].get("site") == key]:
                dkey, role, status = device["key"], device["refs"].get("role"), device["attrs"].get("status")
                if model(dkey).startswith("ACX5048") and status != "inventory":
                    expect_note(dkey, "end-of-sale", ACX5048_LAST_ORDER.isoformat(), (ACX5048_LAST_ORDER, ACX5048_END_OF_SUPPORT))
                if role == "role/aggregation" and status == "inventory":
                    bought = (f"bought before the {ACX5048_LAST_ORDER} last order date, per vendor notice"
                              if model(dkey).startswith("ACX5048") else "the metro's aggregation model")
                    expect_note(dkey, "racked", (launch, as_of), (change(dkey), bought), form="spare-racked")
                if role == "role/ddos-mitigation" and status == "staged":
                    expect_note(dkey, "racked", (launch, as_of), (change(dkey, "-racked"),))
                groups = observed(dkey, "replaced", "replaced")
                if groups:
                    # A replacement retires an original whose rack gap is journaled the same day.
                    scope = "services" if role == "role/aggregation" else "management"
                    expect_note(dkey, "replaced", groups[0] if (groups[1], groups[0]) in removals[key] else "no matching removal",
                                (groups[1], change(dkey, "-replace"), scope, groups[0][:7]), "success")
            replaced = {(groups[1], groups[0]) for d in kinds["device"] if d["refs"].get("site") == key
                        for groups in [observed(d["key"], "replaced", "replaced")] if groups}
            for text, day in removals[key] - replaced:
                if not text.startswith("Juniper MX80 "):
                    fail("operations-journal", key, f"Removing {text} on {day} needs the replacement's own journal.")
        # Exchange ports: turned up on the PE that holds the port, on the
        # circuit's own install day; a relocated exchange's old port shuts on
        # the day its new port turns up.
        peers = {}
        for cable in kinds["cable"]:
            peers[cable["refs"].get("a")], peers[cable["refs"].get("b")] = cable["refs"].get("b"), cable["refs"].get("a")

        def reaches(port, target):
            """Follow cables (through patch-panel front/rear pairs) from a port to a termination."""
            end = peers.get(port)
            for _ in range(8):
                if end == target:
                    return True
                if objects.get(end, {}).get("kind") != "front_port":
                    return False
                end = peers.get(objects[end]["refs"].get("rear_port"))
            return False
        for device in kinds["device"]:
            for event in [e for e in journals_on[device["key"]] if e.startswith("ix-port-")]:
                metro = event.removeprefix("ix-port-").removesuffix("-shut").removesuffix("-former")
                current, former = f"circuit/ix/{metro}", f"circuit/ix/{metro}/former"
                circuit = former if event.endswith(("-shut", "-former")) else current
                network = next((attrs(t["refs"].get("termination")).get("name", "") for t in all_terms[circuit]
                                if str(t["refs"].get("termination", "")).startswith("provider-network/")), "")
                pe_site = next((site_of(t["refs"].get("termination")) for t in terms[circuit]), None)
                port = re.search(r" on (\S+) ", str(journals_on[device["key"]][event]["attrs"].get("comments", "")))
                holds = (pe_site == device["refs"].get("site") and port is not None
                         and any(reaches(f"{device['key']}/if/{port.group(1)}", t["key"]) for t in terms[circuit])
                         and attrs(circuit).get("cid") and network.endswith(" peering LAN"))
                short = network.removesuffix(" peering LAN")
                if not holds:
                    expect_note(device["key"], event, "no exchange port", ("not an exchange port",), form="ix-port")
                elif event.endswith("-shut"):
                    moved_to = next((site_of(t["refs"].get("termination")) for t in terms[current]), None)
                    expect_note(device["key"], event, attrs(current).get("install_date"),
                                (short, attrs(former)["cid"], port.group(1), attrs(moved_to).get("name"),
                                 change(former, "-disconnect")), "warning", form="ix-port-shut")
                else:
                    replacing = (() if circuit == former or former not in objects else
                                 (attrs(next((site_of(t["refs"].get("termination")) for t in terms[former]), None)).get("name"),))
                    expect_note(device["key"], event, attrs(circuit).get("install_date"),
                                (short, attrs(circuit)["cid"], port.group(1), change(circuit, "-turn-up")) + replacing, form="ix-port")
        for circuit in kinds["circuit"]:
            if not circuit["key"].startswith("circuit/ix/"):
                continue
            site = next((site_of(t["refs"].get("termination")) for t in terms[circuit["key"]]), "")
            pe, metro = f"device/{site.removeprefix('site/')}/pe-a", circuit["key"].split("/")[2]
            former = circuit["key"].endswith("/former")
            if former and f"ix-port-{metro}-shut" not in journals_on[pe]:
                fail("operations-journal", circuit["key"], "A relocated exchange port needs its shut journal on the PE that held it.")
            # A port turned up on a router already in the rack is journaled on
            # it; one older than the router moved onto it at its cut-over.
            arrived = (arrivals(pe) or [ledger_day("launch", site.removeprefix("site/pop-"))])[0]
            event = f"ix-port-{metro}" + ("-former" if former else "")
            if arrived and str(circuit["attrs"].get("install_date")) >= arrived and event not in journals_on[pe]:
                fail("operations-journal", circuit["key"], "An exchange port turned up on an installed router needs its turn-up journal.")

    # Legacy names (v0.18 P0-5): a device installed before the NS-2 naming
    # standard that still exists keeps its <facility>-<role><n> name, and the
    # `legacy-naming` tag marks exactly those devices.
    for device in kinds["device"] if recipe.get("profile") == "provider-backbone" else ():
        site = device["refs"].get("site")
        facility = str(attrs(site).get("facility", "")).lower()
        legacy = bool(facility) and re.fullmatch(re.escape(facility) + r"-[a-z]+\d", str(device["attrs"].get("name"))) is not None
        tagged = "tag/legacy-naming" in (device["refs"].get("tags") or [])
        early = bool(installed_on.get(device["key"])) and min(installed_on[device["key"]]) < NS2_ADOPTED.isoformat()
        if tagged != legacy or (legacy and not early):
            fail("operations-tag", device["key"], "The legacy-naming tag marks exactly the devices that keep a pre-NS-2 "
                 "<facility>-<role><n> name, each installed before the standard was adopted.")

    equipment_anchors = {}
    for device in kinds["device"]:
        refs, data = device["refs"], device["attrs"]
        rack, position = refs.get("rack"), data.get("position")
        if (refs.get("role") not in infrastructure_roles or not rack or position is None
                or data.get("status") in {"planned", "staged", "inventory"}):
            continue  # equipment not yet installed carries no installation note
        if type(position) not in (int, float):
            fail("operations-journal-facts", device["key"], "Equipment history requires an actual numeric rack position.")
            continue
        old = equipment_anchors.get(rack)
        if old is None or (position, device["key"]) < (old["attrs"]["position"], old["key"]):
            equipment_anchors[rack] = device
    for device in equipment_anchors.values():
        key = device["key"]
        if "installed" in journals_on[key]:
            continue  # the plant's own dated history already records this install
        # The visit is booked through the site's own facilities desk.
        desk = contacts.get(f"contact/{device['refs'].get('site')}", (None,))[0]
        expect_note(key, "equipment-record", set(installed_on.get(key) or ()), (change(key), desk), "success")
    if recipe.get("profile") == "provider-backbone":
        provider_history()

    for role, (title, group) in roles.items():
        # The role carries an authored display name with a namespaced slug; the
        # group keeps the namespace in its name because its canonical slug is
        # derived from it and omitted on the wire for the auto-slug matcher.
        for kind, label in (("contact_role", title), ("contact_group", group)):
            key = f"{kind.replace('_', '-')}/{role}"
            if kind == "contact_group":
                name = label if recipe.get("tenancy") == "dedicated" else f"{ns} {label}"
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
                or (data.get("email") != (mailbox if "@" in mailbox else f"{mailbox}@{ns}.example") or len(mailbox) > 64
                    if mailbox is not None else
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
    forms = _JOURNAL_FORMS
    as_of_day = str(recipe.get("as_of"))

    def admitted(day, when):
        """A recorded day against an exact day, a set of days or an inclusive window."""
        if isinstance(when, tuple):
            return all(isinstance(bound, str) for bound in when) and when[0] <= day <= when[1]
        return day in when if isinstance(when, (set, frozenset)) else day == when
    for obj in kinds["journal_entry"]:
        key = obj["key"]
        if key not in notes:
            fail("operations-journal", key, "Journal has no required immutable site, circuit, workload or equipment event.")
            continue
        target, form, when, facts, expected_kind, band = notes[key]
        title, body = forms[form]
        comments = obj["attrs"].get("comments", "")
        # A bold title and the event date, a blank line, then the body.
        match = (re.fullmatch(r"\*\*" + re.escape(title) + r"\*\* · (\d{4}-\d{2}-\d{2})\n\n" + body, comments)
                 if isinstance(comments, str) else None)
        if (obj["refs"] != {"assigned_object": target} or obj["attrs"].get("kind") != expected_kind
                or set(obj["attrs"]) != {"kind", "comments", "created"}):
            fail("operations-journal", key, "Journal must retain its actual subject, its event's kind (completed events success, "
                 "open spares warning, otherwise info) and no account binding.")
        if not match or tuple(g for g in match.groups()[1:] if g is not None) != facts:
            fail("operations-journal-facts", key, "Journal claims must match the subject's actual address, contact, circuit, resource or listener facts.")
        try:
            day = match.group(1) if match else None
            if (day is None or not admitted(day, when) or date.fromisoformat(day) > date.fromisoformat(as_of_day)
                    or obj["attrs"].get("created") != _stamp(day, key, band)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            fail("operations-journal-date", key, "Authored event date must match its stable seeded chronology, not exceed "
                 "as_of, and be the entry's own created timestamp at its band's time of day (business hours, or night "
                 "for maintenance).")
    for key in notes:
        if objects.get(key, {}).get("kind") != "journal_entry":
            fail("operations-journal", key, "Required bounded lifecycle event is missing.")
    _automation(objects, kinds, ns, fail, dedicated(recipe))
    return findings



# Restated, not imported from the builder: the tag vocabulary's label scope
# and the switch roles a baseline config context governs.
_TAG_KINDS = {"hub-site": {"site"}, "dual-homed": {"site"}, "acquired": {"site", "device"},
              "route-reflector": {"device"}, "transit-edge": {"device"}, "managed-ce": {"device"},
              "managed-service": {"device", "circuit"},
              "pci-scope": {"device", "vlan", "prefix"}, "clinical": {"device", "vlan", "prefix"},
              "ot-zone": {"device", "vlan", "prefix"},
              "multi-site": {"virtual_machine"}, "legacy-naming": {"device"}}
_TAXONOMY = ("device_role", "rack_role", "region")
# Restated service-class bands: (choice key, ceiling in Mbps; None = above).
_SERVICE_CLASSES = (("essential", 100), ("standard", 500), ("enhanced", 2000),
                    ("aggregation", 10000), ("backbone", None))
# Kinds whose colour is a taxonomy colour; every one in an estate is distinct.
_COLOURED = ("device_role", "rack_role", "tag", "inventory_item_role", "module_bay_type",
             "virtual_circuit_type", "circuit_type")
# Restated jacket colours by cable medium (and power by feed side).
_CABLE_COLOURS = {"smf": "ffeb3b", "mmf": "00bcd4", "aoc": "00bcd4", "cat6": "2196f3"}
_CONSOLE_COLOUR, _POWER_COLOURS = "00e5ff", {"primary": "212121", "redundant": "d50000"}
# Provider PoP colour by function: outside plant blue, carrier cross-connect
# yellow, management grey.
_FUNCTION_COLOURS = {"osp": "2196f3", "xc": "ffeb3b", "mgmt": "9e9e9e"}
_OWNED = ("site", "cluster", "circuit", "device", "rack", "prefix", "ip_address", "vlan")


def _shared(plan, objects, kinds, report):
    """Grouping, typing, tagging and taxonomy obligations every estate carries."""
    ns = plan.get("recipe", {}).get("namespace", "")

    def related(obj, field):
        return objects.get(obj.get("refs", {}).get(field), {})

    for kind, field, target in (("tenant", "group", "tenant_group"), ("cluster", "group", "cluster_group"),
                                ("owner", "group", "owner_group")):
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
        # 4.7 deprecates per-rack form factor and width: they live on the type.
        template = related(rack, "rack_type")
        if (template.get("kind") != "rack_type" or template.get("attrs", {}).get("u_height") != rack["attrs"].get("u_height")
                or {"width", "form_factor"} & rack["attrs"].keys() or "group" in rack["refs"]):
            report("operations-rack-type", rack["key"], "Every rack takes a rack type of its actual height and carries no "
                   "per-rack form factor, width or mirror rack group.")

    # Service class: re-derived from the graph — the summed committed rate of
    # the site's active circuits (a circuit with no commit rate counts its
    # handoff port), banded by the restated _SERVICE_CLASSES ceilings.
    rate = defaultdict(int)
    for term in kinds["circuit_termination"]:
        circuit = related(term, "circuit")
        target = term["refs"].get("termination", "")
        if str(target).startswith("location/"):
            target = objects.get(target, {}).get("refs", {}).get("site")
        if str(target).startswith("site/") and circuit.get("attrs", {}).get("status") == "active":
            value = circuit["attrs"].get("commit_rate") or term["attrs"].get("port_speed") or 0
            rate[target] += value if type(value) is int else 0
    for field in kinds["custom_field"]:
        choices = related(field, "choice_set")
        values = {choice.split(":", 1)[0] for choice in choices.get("attrs", {}).get("extra_choices", [])}
        consumers = [obj for obj in kinds["site"] if field["attrs"].get("name") in obj["attrs"].get("custom_fields", {})]
        if (choices.get("kind") != "custom_field_choice_set" or not consumers or "dcim.site" not in field["attrs"].get("object_types", [])
                or values != {key for key, _ in _SERVICE_CLASSES}
                or field["attrs"].get("label") != "Service class"
                or not str(choices.get("attrs", {}).get("name", "")).endswith("Service classes")):
            report("operations-custom-field", field["key"], "The service-class field needs its labelled choice set and a site consumer.")
        for obj in consumers:
            selected = obj["attrs"]["custom_fields"][field["attrs"]["name"]].get("selection")
            mbps = rate[obj["key"]] / 1000
            expected = next(key for key, ceiling in _SERVICE_CLASSES if ceiling is None or mbps <= ceiling)
            if selected != expected:
                report("operations-custom-field", obj["key"], "Service class must be the band the site's actual "
                       "committed circuit bandwidth falls in.")
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
    # One palette across every coloured taxonomy family: no role, tag, rack
    # role, bay type or circuit type may share a colour with any other.
    colours = defaultdict(list)
    for kind in _COLOURED:
        for obj in kinds[kind]:
            if "color" in obj["attrs"]:
                colours[str(obj["attrs"]["color"]).lower()].append(obj["key"])
    for colour, keys in colours.items():
        if len(keys) > 1:
            report("operations-taxonomy", sorted(keys)[1], f"{', '.join(sorted(keys))} share colour {colour}.")

    # Unused ports are shut on every role: a physical port nothing names (no
    # cable, address, unit, LAG member or termination) and carrying no VLAN or
    # WLAN must be administratively down.
    named = {target for obj in objects.values() if obj["kind"] not in {"mac_address", "journal_entry"}
             for value in obj["refs"].values()
             for target in (value if isinstance(value, list) else [value]) if isinstance(target, str)}
    for port in kinds["interface"]:
        demarcation = bool(port["attrs"].get("mark_connected") and port["attrs"].get("label"))
        if (port["key"] not in named and not demarcation and port["attrs"].get("enabled") is not False
                and port["attrs"].get("type") not in (None, "virtual", "lag", "bridge")
                and not any(port["refs"].get(field) for field in ("untagged_vlan", "tagged_vlans", "wireless_lans"))):
            report("operations-unused-port", port["key"], "Unused ports are disabled on every role; this port is uncabled, "
                   "unaddressed and carries nothing, yet still enabled.")
    cabled = {cable["refs"].get(side) for cable in kinds["cable"] for side in ("a", "b")}
    for port in kinds["interface"]:
        if port["attrs"].get("mark_connected") and (port["key"] in cabled or (port["key"] not in named and not port["attrs"].get("label"))
                                                     or port["attrs"].get("enabled") is False):
            report("operations-unused-port", port["key"], "mark_connected stands for an uninventoried far end of a "
                   "port in service; NetBox refuses it beside a real cable.")

    # Every data cable states its medium in its jacket colour. A feed's own
    # whip is black on the primary feed and red on the redundant one; an
    # equipment cord is either (a cord moved between PDUs keeps its jacket —
    # that is how the power-diversity defect looks on the floor).
    # A provider PoP colours by function instead (DESIGN §5), re-derived here
    # from the graph: a landing panel (one whose rear ports face circuit
    # terminations) is our outside plant (blue) when the operator holds it and
    # a carrier hotel's cross-connect demarc (yellow) otherwise; anything on a
    # PoP management switch is management grey.
    provider = plan.get("recipe", {}).get("profile") == "provider-backbone"
    landing = {}
    for cable in kinds["cable"] if provider else ():
        for near, far in ((cable["refs"].get("a"), cable["refs"].get("b")),
                          (cable["refs"].get("b"), cable["refs"].get("a"))):
            panel = objects.get(objects.get(near, {}).get("refs", {}).get("device"), {})
            if (objects.get(near, {}).get("kind") == "rear_port"
                    and objects.get(far, {}).get("kind") == "circuit_termination"
                    and panel.get("refs", {}).get("role") == "role/patch-panel"):
                landing[panel["key"]] = ("osp" if panel["refs"].get("tenant") == "tenant" else "xc")

    def function(end):
        device = objects.get(end.get("refs", {}).get("device"), {})
        if end.get("kind") in {"front_port", "rear_port"} and device.get("key") in landing:
            return landing[device["key"]]
        if (end.get("kind") == "interface" and device.get("refs", {}).get("role") == "role/management"
                and str(device.get("refs", {}).get("site", "")).startswith("site/pop-")):
            return "mgmt"
        return None

    for cable in kinds["cable"]:
        ends = [objects.get(cable["refs"].get(side), {}) for side in ("a", "b")]
        cable_type, colour = cable["attrs"].get("type"), cable["attrs"].get("color")
        feeds = [end for end in ends if end.get("kind") == "power_feed"]
        functions = {function(end) for end in ends} - {None} if provider else set()
        if len(functions) == 1:
            expected = {_FUNCTION_COLOURS[functions.pop()]}
        elif functions:
            expected = set()  # one cable cannot serve two functions
        elif {end.get("kind") for end in ends} == {"console_port", "console_server_port"}:
            expected = {_CONSOLE_COLOUR} if cable_type == "cat6" else set()
        elif cable_type == "power":
            expected = ({_POWER_COLOURS.get(feeds[0]["attrs"].get("type"))} if feeds
                        else set(_POWER_COLOURS.values()))
        else:
            expected = {_CABLE_COLOURS[cable_type]} if cable_type in _CABLE_COLOURS else None
        if expected is not None and colour not in expected:
            report("operations-cable-colour", cable["key"], "A cable's jacket colour must follow its medium "
                   "(and a power cord its feed side); a console run is typed twisted pair.")

    # One accountable team owns the estate's infrastructure records.
    for kind in _OWNED:
        for obj in kinds[kind]:
            if not obj.get("meta", {}).get("external") and objects.get(obj["refs"].get("owner"), {}).get("kind") != "owner":
                report("operations-owner", obj["key"], f"Every {kind} needs the estate's accountable owner.")


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
                "contact_assignment", "provider_account", "rack_type", "tenant_group", "virtual_disk",
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
            if (any(port.get("kind") != "interface" for port in ports) or cable["attrs"].get("type") not in {"smf", "mmf"}
                    or "device/dc-01/compute-01-leaf-a" not in hosts):
                report("operations-cable-bundle", cable["key"], "Bundle members must remain actual A-side optical host connections.")
    return sorted(findings, key=lambda f: (f["code"], f["object"]))
