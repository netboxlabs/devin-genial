"""Scoped service desks and bounded, immutable design records for every profile.

Journal comments are matching identity. Never include growing inventory totals,
current utilization, or an assertion that a scenario change or recovery test was
executed. A historical event cites only what its record cannot show: the change
ticket it ran under and who confirmed it.
"""

from collections import defaultdict
from datetime import date, timedelta

from .automation import enrich as automation_records
from .model import DesignError, digest, redate_serial
from .naming import main_scoped_name, titleize
from .wireless_context import enrich as wireless_context

# Real metro area codes with the 555-0100..0199 block the North American
# Numbering Plan reserves for fictional use, so a directory reads like one
# without ever dialling a real subscriber.
AREA_CODES = {"Chicago": "312", "Detroit": "313", "Cleveland": "216", "Milwaukee": "414"}


SERIAL_SHIFT_WEEKS = 8
# One third-party circuit delivery in this many slipped past its committed date.
SLIP_ONE_IN = 5
# A provider backbone's own provider record: the circuits it sells or runs itself.
OPERATOR = "provider/operator"



# Equipment not yet installed has no installation history to record.
NOT_INSTALLED = frozenset({"planned", "staged", "inventory"})

def _site_of(world, term):
    """The site or provider network a circuit termination stands in, through its room."""
    target = term["refs"]["termination"]
    return world.obj(target)["refs"]["site"] if target.startswith("location/") else target

def timeline(world, kinds, dated):
    """One timeline: service days, device installs and serial date codes.

    A site's service day is its first circuit's install date (a PoP's first
    span, a premises' access circuit): the earliest service any of its
    equipment carries. Every device there is installed 7-37 days before it, so
    a redundant pair arrives together (as_of-relative only for a site with no
    circuit). Each unit was manufactured 30-180 days before it was installed,
    so the date code in its serial (the catalog's {yyww}/{yy}) precedes the
    install; an optic serving a later circuit is made before that circuit
    (its own port's install), else before its host's install.
    Returns (service_day per site, install date per device), ISO strings.
    """
    # A circuit not yet in service has no install date and dates nothing.
    circuit_day = {term["key"]: world.obj(term["refs"]["circuit"])["attrs"]["install_date"]
                   for term in kinds["circuit_termination"]
                   if "install_date" in world.obj(term["refs"]["circuit"])["attrs"]}
    service_day, port_day = {}, {}
    for term in kinds["circuit_termination"]:
        site = term["refs"]["termination"]
        if site.startswith("location/"):  # a handoff in a room serves that room's site
            site = world.obj(site)["refs"]["site"]
        if site.startswith("site/") and term["key"] in circuit_day:
            service_day[site] = min(circuit_day[term["key"]], service_day.get(site, circuit_day[term["key"]]))
    for cable in kinds["cable"]:
        ends = (cable["refs"].get("a"), cable["refs"].get("b"))
        for port, far in (ends, ends[::-1]):
            if far in circuit_day and world.objects.get(port, {}).get("kind") == "interface":
                port_day[port] = min(circuit_day[far], port_day.get(port, circuit_day[far]))
    as_of, installed_on = world.recipe["as_of"], {}
    for device in kinds["device"]:
        key, site = device["key"], device["refs"].get("site")
        anchor = service_day.get(site)
        installed_on[key] = (dated(key, "equipment-record", anchor, 7, 31) if anchor
                             else dated(key, "equipment-record", as_of, 100, 20))

    def made(key, anchor):
        return date.fromisoformat(dated(key, "manufactured", anchor, 30, 151))

    models = world.catalog["models"]
    formats = world.catalog["optics"]["serial_formats"]
    for device in kinds["device"]:
        fmt = models.get(device["meta"].get("hardware"), {}).get("serial_format")
        if fmt and device["attrs"].get("serial"):
            device["attrs"]["serial"] = redate_serial(fmt, device["attrs"]["serial"], made(device["key"], installed_on[device["key"]]))
    # AOC captive ends share one assembly serial: date the assembly once, from
    # its earlier end, keyed by the shared serial.
    groups, assembly_cables = defaultdict(list), defaultdict(list)
    for cable in kinds["cable"]:
        comments = cable["attrs"].get("comments", "")
        if comments.startswith("Assembly serial: "):
            assembly_cables[comments.split("\n", 1)[0].removeprefix("Assembly serial: ")].append(cable)
    for module in kinds["module"]:
        groups[module["attrs"].get("serial")].append(module)
    taken = set()
    for serial, members in sorted(groups.items(), key=lambda item: min(m["key"] for m in item[1])):
        anchors, fmt = [], None
        for module in members:
            device = world.obj(module["refs"]["device"])
            maker = world.obj(world.obj(module["refs"]["module_type"])["refs"]["manufacturer"])["attrs"]["name"]
            fmt = (models.get(device["meta"].get("hardware"), {}).get("module_serial_format")
                   if module["key"].startswith(f"{device['key']}/module/") else formats.get(maker, formats["Generic"]))
            # An optic staged for a handoff not yet in service arrived recently.
            anchors.append(port_day.get(module["key"].removeprefix("optics-module/"))
                           or (dated(module["key"], "staged", as_of, 7, 31) if module["attrs"].get("status") == "staged" else None)
                           or installed_on[device["key"]])
        if fmt and serial:
            when = made(min(m["key"] for m in members), min(anchors))
            redated = redate_serial(fmt, serial, when)
            # Short label formats leave few non-date digits: two modules may
            # differ only in their old placeholder date. Keep detachable
            # serials unique by dating the later key one week earlier.
            # ponytail: first key in sort order wins; a growth-added module that
            # sorts earlier and collides could re-date an existing one (rare).
            for _ in range(SERIAL_SHIFT_WEEKS):
                if redated not in taken:
                    break
                when -= timedelta(days=7)
                redated = redate_serial(fmt, serial, when)
            else:
                raise DesignError(f"{members[0]['key']}: no unique dated serial within {SERIAL_SHIFT_WEEKS} weeks")
            taken.add(redated)
            for module in members:
                module["attrs"]["serial"] = redated
            if len(members) > 1:  # the AOC cable names its assembly serial
                for cable in assembly_cables.get(serial, ()):
                    cable["attrs"]["comments"] = cable["attrs"]["comments"].replace(serial, redated, 1)
    return service_day, installed_on


# A provider's PoPs are cages in carrier hotels: their facilities desk is the
# building operator's remote-hands desk, one invented colocation company per metro.
COLOCATION = {"Chicago": ("Windward Interconnect", "windward-interconnect.example"),
              "Detroit": ("Motorline Data Centers", "motorline-dc.example"),
              "Cleveland": ("Cuyahoga Colocation", "cuyahoga-colo.example"),
              "Milwaukee": ("Kinnickinnic Colocation", "kinnickinnic-colo.example")}


def journal_created(day):
    """A journal entry's ``created`` timestamp: its event day, 15:00 UTC.

    Mid-morning across the US time zones the estates use, so no zone renders
    the entry on the previous calendar day. Rendered the way NetBox's REST
    serializer returns it, so strict readback compares it exactly.
    """
    return f"{day}T15:00:00Z"


def customer_domain(tenant):
    """A provider customer's own mail domain: tenant/cust-acme-bank -> acme-bank.example."""
    return f"{tenant.removeprefix('tenant/cust-')}.example"


def provider_customer(world, tenant):
    return world.recipe["profile"] == "provider-backbone" and tenant.startswith("tenant/cust-")


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
        # clean name would silently change the matching identity. A dedicated
        # tenant holds one estate, so the bare label and its derived slug
        # cannot collide there and the auto-slug matcher still resolves it.
        name = main_scoped_name(world.recipe, groups[key])
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
                   "email": mailbox if "@" in mailbox else f"{mailbox}@{ns}.example", "description": description},
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
        # A provider's customer answers from its own domain, not the carrier's.
        mailbox = ("noc" if tenant == "tenant" else f"noc@{customer_domain(tenant)}" if provider_customer(world, tenant)
                   else f"{tenant.removeprefix('tenant/')}.noc")
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

    service_day, installed_on = timeline(world, kinds, dated)

    def change(target, work=""):
        """The operator's change ticket for one subject's work; stable per subject."""
        return f"CHG{world.choose(target, 'journal-change' + work, range(1000000, 10000000)):07d}"

    def journal(target, event, when, title, body, kind="info"):
        # ``created`` is the event's own date (mid-morning US time), not the
        # load: TurboBulk inserts a supplied value as-is, and a journal whose
        # every entry reads the day it was loaded tells no history.
        add("journal_entry", f"journal/{target}/{event}", {"kind": kind, "comments": f"{when} — {title}\n{body}",
                                                           "created": journal_created(when)}, {"assigned_object": target})

    as_of = world.recipe["as_of"]
    for site in kinds["site"]:
        key, attrs = site["key"], site["attrs"]
        name, mailbox = f"{attrs['name']} facilities desk", f"{key.removeprefix('site/')}.facilities"
        if world.recipe["profile"] == "provider-backbone" and key.startswith("site/pop-"):
            colo, domain = COLOCATION[city[key]]
            name, mailbox = f"{colo} remote hands at {attrs['name']}", f"remote-hands@{domain}"
        elif provider_customer(world, site["refs"].get("tenant", "")):
            mailbox = f"facilities.{(attrs.get('facility') or key.removeprefix('site/'))[:40].lower()}@{customer_domain(site['refs']['tenant'])}"
        desk = contact(f"contact/{key}", name, "facilities", mailbox,
            f"Equipment-room access, cabinet visits and planned power-work coordination at {attrs['name']}.", city[key])
        assign(key, desk, "facilities", "/facilities", "secondary")
        if key in biomedical_desks:
            assign(key, biomedical_desks[key], "biomedical", "/biomedical", "tertiary")
        # Written when the site was readied: before its first equipment
        # arrives (installs lead the service day by at most 37 days).
        journal(key, "access-plan", dated(key, "access-plan", service_day[key], 40, 31) if key in service_day
                else dated(key, "access-plan", as_of, 60, 31), "Site access",
            f"Equipment-room visits are booked through {world.obj(desk)['attrs']['name']}; "
            "give two working days' notice and flag any planned power work.")

    provider_desks = {}
    for circuit in kinds["circuit"]:
        key, attrs, refs = circuit["key"], circuit["attrs"], circuit["refs"]
        provider = refs["provider"]
        name = world.obj(provider)["attrs"]["name"]
        # The operator's own circuits are its products, not purchases: no
        # carrier desk escalates them and no order goes to a supplier.
        own = world.recipe["profile"] == "provider-backbone" and provider == OPERATOR
        if not own and provider not in provider_desks:
            # A third-party carrier's desk answers from its own mail domain.
            domain = world.obj(provider)["meta"].get("support_domain")
            provider_desks[provider] = contact(f"contact/{provider}", f"{name} support desk", "carrier",
                f"support@{domain}" if domain else f"carrier-{provider.removeprefix('provider/')}.support",
                (f"Capacity and handoff coordination for {name}. Tenant technical desks handle local troubleshooting."
                 if world.recipe["profile"] == "provider-backbone" else
                 f"Circuit identifiers, contracted capacity and handoff coordination for {name}; customer-side troubleshooting stays with the tenant technical desk."))
        if not own:
            assign(key, provider_desks[provider], "carrier", "/carrier", "secondary")
        if attrs.get("termination_date"):
            # A circuit being withdrawn carries its disconnect order.
            journal(key, "disconnect-order", dated(key, "disconnect-order", as_of, 3, 25), "Disconnect order",
                f"Disconnect ordered under change {change(key, '-disconnect')}; recover the handoff optics and cabling once the circuit is withdrawn.",
                "warning")
        if "install_date" not in attrs:
            continue  # ordered work not yet in service has no dated history
        # Only what the record cannot show: the change it went live under and
        # who ordered or confirmed it. Its cid, terminations, rates and dates
        # are fields the circuit already holds.
        if "commit_rate" in attrs and own:
            buyer = "the NOC" if refs.get("tenant") == "tenant" else world.obj(refs["tenant"])["attrs"]["name"]
            journal(key, "service-order", dated(key, "service-order", attrs["install_date"], 30, 31), "Service order",
                f"Ordered by {buyer}; provisioning tracked under change {change(key)}.")
        accepted = f"Accepted into service under change {change(key)}."
        if provider != OPERATOR:
            accepted += f" {name} support desk confirmed the handover and closed its ticket."
            # Some carrier deliveries slip: a per-circuit choice keyed by namespace,
            # not seed, so a reseed never adds or drops an event (and growth
            # never moves one), dated before the handover it delayed.
            if int(digest([world.recipe["namespace"], key, "journal-slip"]), 16) % SLIP_ONE_IN == 0:
                journal(key, "delivery-slip", dated(key, "delivery-slip", attrs["install_date"], 5, 16), "Delivery slipped",
                        f"{name} missed the committed handover date; escalated to its support desk.", "warning")
        journal(key, "handover", attrs["install_date"], "Handed over", accepted, "success")

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
        site = world.obj(refs["device"])["refs"]["site"]
        journal(key, "resource-plan", max(dated(key, "resource-plan", as_of, 120, 31), service_day.get(site, "")), "First instance placed",
            f"Placed on {world.obj(refs['device'])['attrs']['name']}; later replicas follow the same sizing.", "success")

    # Permanent U allocation makes this local selection stable when a new
    # workload sorts before existing workloads. New racks receive new stories.
    # One installation event per cabinet, on its lowest-mounted equipment: the
    # change it was racked under and the desk that booked the visit. Model,
    # serial, cabinet, U and modules are fields the device already shows.
    equipment_anchors = {}
    for device in kinds["device"]:
        refs, attrs = device["refs"], device["attrs"]
        rack, position = refs.get("rack"), attrs.get("position")
        if (refs.get("role") not in infrastructure_roles or not rack or position is None
                or attrs.get("status") in NOT_INSTALLED):
            continue
        old = equipment_anchors.get(rack)
        if old is None or (position, device["key"]) < (old["attrs"]["position"], old["key"]):
            equipment_anchors[rack] = device
    for rack_key, device in sorted(equipment_anchors.items()):
        key = device["key"]
        desk = world.obj(f"contact/{device['refs']['site']}")["attrs"]["name"]
        journal(key, "equipment-record", installed_on[key], "Installed",
            f"Racked and cabled under change {change(key)}; the visit was booked through {desk}.", "success")
    wireless_context(world)
    automation_records(world)
