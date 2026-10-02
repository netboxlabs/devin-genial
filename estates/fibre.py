"""The provider PoP plant: cage or cabinet, panels, aggregation, history and cable policy.

DESIGN.md §3 (build/lived-in-design, final locked revision, v0.18) is the
spec; build/footprint-design §§2 and 5 (v0.17) set the cable policy kept here.

Tier (``timeline.of(w).tier``): a core PoP (NOC handoff, transit or IX port)
is a carrier-hotel cage (site -> suite -> cage) with two installed APC AR3100
42U cabinets, R01 (side A) and R02 (side B). An edge PoP is one cabinet
(site -> suite -> ``Cabinet <facility id>``) holding both sides. No reserved
cabinet is modelled: the cage location's description carries the contract
("Cage contract: 4 cabinet positions; 2 installed; expansion by change order").

Stratigraphy (K4). Each cabinet is filled top-down in install order, from the
frozen timeline (estates/timeline.py): the launch kit, then every later event
(original aggregation -> ACX5048, original console server -> EX3400 +
OM2216-L, cold spare, MX80 -> MX204 refresh, the planned MX304 successor, the
staged DDoS appliance). A removed device keeps its units as a gap that is never
reused: the append-only ledger ``provider-cabinet-u/<rack>`` records each
item's position once, so growth appends below the lowest used unit and never
moves an existing one. Gaps are blanked (exclude_from_utilization) and each
removed device has a dated rack journal ("Removed: …, CHG…"). Hygiene: a 2U
cable manager under each 48-port panel, 1U under each 24-port panel and under
each router (pair).

Cable policy: every PoP cable carries a label (site code, cabinet and
ordinal, or the carrier's cross-connect ID), a medium, a colour by function
and a length. Owned fibre (customer access tails, owned dark-fibre spans and
owned NOC links) lands on our OSP panel; a carrier's circuit (leased waves,
transit, NOC private lines) is a cross-connect from the meet-me room onto the
colo's demarc panel, its xconnect_id on the circuit termination and on the
cable label. Panel front ports carry TIA-606-style labels
``<facility_id>.<U>:<port>`` (style only; no standard compliance is claimed).
Every circuit termination stays site-scoped (VE WAN-map limitation). Nothing
here claims a route, light level or patch order.

Device keys (WP-C wires the service devices the plant racks):

    pe-a/pe-b, agg-a/agg-b, mgmt-01, console-01    in service
    ntp-01      Meinberg M300 (NOC-handoff PoPs), R01, active
    ddos-01     Arbor TMS (MX304 PoPs with transit and DIA), R01, staged
    pe-a2/pe-b2 MX304 successor pair, planned or staged, no power
    legacy-pe-a/legacy-pe-b   MX80 relics, decommissioning, uncabled
    agg-spare   cold-spare aggregation chassis, inventory, uncabled (R02)

Interface for the services work package (WP-C), which owns the home-side
ledger ``provider-agg-home/<pop>`` and the per-side UNI ledger:

    attachment(w, pop, side, ordinal) -> dict(site, agg, uni, agg_lag, pe, pe_lag)
        The AGG UNI port (``uni_ports[ordinal]``, ordinal 0..39) on the home
        side ``side`` ("a"/"b") and the PE LAG ``ae1``, parent of that
        attachment's PE subinterface. Raises past 40 UNIs per side (80 per
        PoP); the stated growth path is MX304 or hub/access PoPs.
    carry(w, pop, side, vlan)
        Tag a service VLAN on that side's AGG ae0 and PE ae1 (home LAG only).
    subinterface(w, pop, side, vid, vlan, *, description, vrf=None) -> key
        The PE unit ``ae1.<vid>`` (virtual, parent ae1, access, untagged
        vlan); it also carries ``vlan`` on the home LAG.
    nid_management(w, pop, side) -> (vlan, network, gateway_interface)
        The side's NID-management VLAN (4001 A / 4002 B) and /25 in Carrier
        Management, gatewayed .1 on that side's PE ``ae1.<vid>``.
    nid_host(w, pop, side, owner) -> host offset in that /25 (2..126),
        from the append-only ledger ``provider-nid-hosts/<pop>/<side>``.
    Pass the UNI as the PoP-side port to provider._circuit: it lands the
    access circuit's PoP termination on that side's OSP panel.

Circuit-type keys the plant emits unless the registry already has them:
``circuit-type/cellular-oob`` and ``circuit-type/noc-access``.
"""

import ipaddress

from . import timeline
from .blocks import device_type
from .model import DesignError
from .naming import CABLE_COLORS, ROLE_COLORS, titleize


# Catalog aliases (WP-A, catalog/README.md "Regional-carrier footprint aliases"
# and "Lived-in carrier aliases").
AGGREGATION = "aggregation"            # Juniper ACX5448-M
AGGREGATION_LEGACY = "aggregation-legacy"   # Juniper ACX5048-AC
PE = "provider-edge"                   # Juniper MX204
PE_LEGACY = "provider-edge-legacy"     # Juniper MX80 (relic)
PE_SUCCESSOR = "provider-edge-successor"    # Juniper MX304 (planned/staged)
DDOS = "ddos-mitigation"               # Arbor TMS HD 1000
TIMING = "time-server"                 # Meinberg LANTIME M300
POP_MANAGEMENT = "pop-mgmt"            # Juniper EX3400-24T, no PoE
POP_OOB = "oob-server"                 # Opengear OM2216-L
POP_PDU = "pdu-switched"               # APC AP8941, 0U, 208 V 30 A, networked
OSP_PANEL = "osp-panel"                # CommScope FMS-K2BI-L1A1-48-SP, 1U
COLO_PANEL = "demarc-panel"            # Generic LC-24 fibre panel, 1U
CABLE_MANAGER_1U = "cable-manager-1u"
CABLE_MANAGER_2U = "cable-manager-2u"
BLANKING_1U = "blanking-1u"            # exclude_from_utilization
BLANKING_2U = "blanking-2u"
NOC_DEMARC_PANEL = "patch-panel"       # pinned Panduit 24-port Cat 6 panel
AGG_MODELS = {"acx5048": AGGREGATION_LEGACY, "acx5448m": AGGREGATION}

PASSIVE = (OSP_PANEL, COLO_PANEL, CABLE_MANAGER_1U, CABLE_MANAGER_2U, BLANKING_1U, BLANKING_2U)
PLANT_ALIASES = (AGGREGATION, AGGREGATION_LEGACY, PE_LEGACY, PE_SUCCESSOR, DDOS, TIMING,
                 POP_MANAGEMENT, POP_OOB, POP_PDU, *PASSIVE)


ROLES = {PE: "provider-edge", PE_LEGACY: "provider-edge", PE_SUCCESSOR: "provider-edge",
         AGGREGATION: "aggregation", AGGREGATION_LEGACY: "aggregation", POP_MANAGEMENT: "management",
         POP_OOB: "console-server", OSP_PANEL: "patch-panel", COLO_PANEL: "patch-panel",
         CABLE_MANAGER_1U: "cable-management", CABLE_MANAGER_2U: "cable-management",
         BLANKING_1U: "cable-management", BLANKING_2U: "cable-management", POP_PDU: "pdu",
         DDOS: "ddos-mitigation", TIMING: "time-server"}
# The two v0.18 roles have their own colours (K16), never a status colour.
# ponytail: kept here until naming.ROLE_COLORS (WP-C) carries them.
NEW_ROLE_COLORS = {"ddos-mitigation": "d32f2f", "time-server": "00897b"}

RACK_HEIGHT = 42
CAGE_POSITIONS = 4
CAGE_CONTRACT = (f"Cage contract: {CAGE_POSITIONS} cabinet positions; 2 installed; expansion by change order")
EDGE_CABINET = "Leased single cabinet in the carrier hotel; both PoP sides share it"
# The AP8941 inlet is a NEMA L6-30P: its branch circuit is 208 V / 30 A.
POP_FEED = (208, 30)

# Aggregation port plan (DESIGN.md §2): forty UNIs, a four-member LAG to the
# same-side PE. The PE side is MX204 xe-0/1/0-3 into ae1.
UNIS_PER_AGG = 40
AGG_LAG, PE_LAG = "ae0", "ae1"
PE_LAG_MEMBERS = tuple(f"xe-0/1/{n}" for n in range(4))
PE_NOC_PORT = "xe-0/1/4"
NID_VIDS = {"a": 4001, "b": 4002}
# Host ordinals on the PoP management /26: switch irb 1, console server 3,
# PE fxp0 4/5 (provider.FXP0_HOSTS), AGG em0 6/7, then the PDUs 8-11.
OOB_HOST, AGG_EM0_HOSTS, PDU_HOSTS = 3, (6, 7), (8, 9, 10, 11)

# Colour by function (DESIGN.md §5). Data links between our own chassis take
# their medium's colour once optics settle it (smf or mmf).
FUNCTION_COLORS = {"xc": "ffeb3b", "osp": "2196f3", "mgmt": "9e9e9e",
                   "console": CABLE_COLORS["console"], "power-a": CABLE_COLORS["power-a"],
                   "power-b": CABLE_COLORS["power-b"]}
# Authored run lengths, metres: an in-cabinet patch cord (the optics policy's
# 3 m local minimum, catalog optics.local_min_m), a cord from a 0U PDU, the
# feed whip, the hotel's meet-me-room cross-connect to the cage, and our own
# building-entrance fibre from the riser to the cage.
PATCH_M, POWER_M, FEED_M, XC_RUN_M, OSP_RUN_M = 3, 2, 3, 45, 30
LABEL_CAPACITY = 1000

# Legacy (pre-NS-2) hostnames: <facility lower>-<abbreviation><n> (CLLI-style only).
LEGACY_ABBREVIATIONS = {"provider-edge": "rtr", "aggregation": "agg", "time-server": "ntp",
                        "management": "sw", "console-server": "con"}
# What a removed, never-inventoried predecessor was (DESIGN.md §4.1).
ORIGINAL = {"agg": ("the original aggregation switch; not inventoried", 1),
            "oob": ("the original console server with its integrated management ports; not inventoried", 1)}
# Racked but not in service (NetBox's own status meanings).
NOT_IN_SERVICE = ("decommissioning", "planned", "staged", "inventory")


def _stub_models(w):
    """STUB(WP-A): stand-ins until catalog/hardware.json carries the lived-in aliases.

    Remove at integration: every alias below is WP-A's pinned model.
    """
    if all(alias in w.catalog["models"] for alias in (PE_LEGACY, PE_SUCCESSOR, DDOS, TIMING)):
        return
    w.catalog = {**w.catalog, "models": dict(w.catalog["models"])}  # never mutate the shared catalog
    models = w.catalog["models"]
    def clone(alias, base, **changes):
        if alias not in models:
            models[alias] = {**models[base], **changes}
    clone(PE_LEGACY, PE, model="MX80 (stub)", slug="stub-mx80", u_height=2)
    clone(PE_SUCCESSOR, PE, model="MX304 (stub)", slug="stub-mx304", u_height=2, power_ports=[], console_ports=[],
          configured_modules=[], interfaces=[dict(name=f"et-0/0/{n}", type="400gbase-x-qsfpdd") for n in range(16)])
    clone(DDOS, POP_OOB, model="TMS HD 1000 (stub)", slug="stub-tms", u_height=2, console_server_ports=[])
    clone(TIMING, POP_OOB, model="LANTIME M300 (stub)", slug="stub-m300", u_height=1, console_server_ports=[])


def ensure_models(w):
    """Register the PoP plant's catalog device types (catalog/hardware.json carries every alias)."""
    _stub_models(w)
    for alias in (*PLANT_ALIASES, PE):
        if alias in w.catalog["models"]:  # STUB(WP-A): drop the guard with the stubs
            device_type(w, alias)


def _role(w, role):
    key = f"role/{role}"
    if key not in w.objects:
        w.add("device_role", key, {"name": titleize(role), "slug": f"{w.recipe['namespace']}-{role}",
                                   "color": ROLE_COLORS.get(role) or NEW_ROLE_COLORS[role]})
    return key


def circuit_type(w, kind, name):
    """A circuit type the plant needs, unless the provider registry emitted it."""
    key = f"circuit-type/{kind}"
    if key not in w.objects:
        w.add("circuit_type", key, {"name": name, "slug": f"{w.recipe['namespace']}-{kind}"})
    return key


def _blanking(occupied):
    """Blanking panels filling every gap strictly inside the occupied band.

    ``occupied`` is the set of units holding equipment or a removed device's
    ledgered position. Each gap takes 2U panels from its top, then one 1U.
    Returns [(position, alias)].
    """
    fill, gap = [], []
    for u in range(max(occupied), min(occupied) - 1, -1):
        if u not in occupied:
            gap.append(u)
            continue
        while gap:
            if len(gap) >= 2:
                fill.append((gap[1], BLANKING_2U)); gap = gap[2:]
            else:
                fill.append((gap[0], BLANKING_1U)); gap = []
    return fill


def history(w, pop):
    """One PoP's install ledger: [(rack name, item)] in install order.

    An item is dict(label, alias, kind, day, status, removed, text, legacy):
    ``kind`` drives the hygiene rule ("panel-48", "panel-24", "router" or
    None); ``removed`` is the removal date of a device that is gone (its units
    stay a gap); ``text`` names a removed predecessor.
    """
    tl = timeline.of(w)
    core, launch = tl.tier[pop] == "core", tl.launch[pop]
    # STUB(WP-A): ACX5048 builds as the ACX5448-M until its catalog entry and optics land.
    agg_alias = lambda model: AGG_MODELS[model] if AGG_MODELS[model] in w.catalog["models"] else AGGREGATION
    rack = lambda side: "R01" if side == "a" or not core else "R02"
    items = []

    def add(side, label, alias, kind, day, *, status="active", removed=None, text=None, order=0):
        items.append((day, order, len(items), rack(side), dict(label=label, alias=alias, kind=kind, day=day,
                      status=status, removed=removed, text=text, legacy=tl.legacy(day))))

    sides = "ab"
    pe0 = PE_LEGACY if tl.pe_at_launch[pop] == "mx80" else PE
    for side in (sides if core else "a"):
        add(side, f"{rack(side).lower()}-osp", OSP_PANEL, "panel-48", launch)
        add(side, f"{rack(side).lower()}-demarc", COLO_PANEL, "panel-24", launch)
    for side in sides:
        relic = tl.refresh[pop] is not None
        add(side, f"legacy-pe-{side}" if relic else f"pe-{side}", pe0, "router", launch,
            status="decommissioning" if relic and tl.relic[pop] else "active",
            removed=tl.refresh[pop] if relic and not tl.relic[pop] else None)
    if tl.timing[pop]:
        add("a", "ntp-01", TIMING, None, tl.timing[pop])
    for side in sides:
        if tl.agg_swap[pop]:
            add(side, f"original-agg-{side}", None, None, launch, removed=tl.agg_swap[pop], text=ORIGINAL["agg"][0])
        else:
            add(side, f"agg-{side}", agg_alias(tl.agg[pop]), None, launch)
    if tl.oob_swap[pop]:
        add("a", "original-console", None, None, launch, removed=tl.oob_swap[pop], text=ORIGINAL["oob"][0])
    else:
        add("a", "mgmt-01", POP_MANAGEMENT, None, launch)
        add("a", "console-01", POP_OOB, None, launch)
    # Later events, each a dated programme; same-day items keep this order.
    if tl.agg_swap[pop]:
        for side in sides:
            add(side, f"agg-{side}", agg_alias(tl.agg[pop]), None, tl.agg_swap[pop])
    if tl.oob_swap[pop]:
        add("a", "mgmt-01", POP_MANAGEMENT, None, tl.oob_swap[pop])
        add("a", "console-01", POP_OOB, None, tl.oob_swap[pop])
    if tl.spare[pop]:
        model, day = tl.spare[pop]
        add("b", "agg-spare", agg_alias(model), None, day, status="inventory")
    if tl.refresh[pop]:
        for side in sides:
            add(side, f"pe-{side}", PE, "router", tl.refresh[pop])
    if tl.mx304[pop]:
        plan = tl.mx304[pop]
        for side in sides:
            add(side, f"pe-{side}2", PE_SUCCESSOR, "router", plan["received"] or plan["ordered"], status=plan["status"])
    if tl.ddos[pop]:
        add("a", "ddos-01", DDOS, None, tl.ddos[pop], status="staged", order=1)
    return [(name, item) for _, _, _, name, item in sorted(items, key=lambda row: row[:3])]


def _elevation(w, rack_key, items, heights):
    """Top-down positions for one cabinet's install ledger, frozen in ``provider-cabinet-u/<rack>``.

    Returns (placed, gaps): placed [(position, item-or-manager)] for devices
    to create, gaps [(position, item)] for removed devices.
    """
    ledger = w.reservations.setdefault(f"provider-cabinet-u/{rack_key}", {})
    floor = min(ledger.values(), default=RACK_HEIGHT + 1)
    placed, gaps = [], []

    def mount(label, height):
        nonlocal floor
        if label not in ledger:
            position = floor - height
            if position < 1:
                raise DesignError(f"{rack_key}: cabinet stack exceeds {RACK_HEIGHT}U; add a cabinet by change order")
            ledger[label] = position
        floor = min(floor, ledger[label])
        return ledger[label]

    for i, item in enumerate(items):
        height = heights[item["alias"]] if item["alias"] else ORIGINAL["agg" if "agg" in item["label"] else "oob"][1]
        position = mount(item["label"], height)
        (gaps if item["removed"] else placed).append((position, height, item))
        after = items[i + 1] if i + 1 < len(items) else None
        manager = {"panel-48": CABLE_MANAGER_2U, "panel-24": CABLE_MANAGER_1U}.get(item["kind"])
        # A router pair racked together shares one manager below the pair.
        if item["kind"] == "router" and not (after and after["kind"] == "router" and after["day"] == item["day"]):
            manager = CABLE_MANAGER_1U
        if manager:
            cm = dict(label=f"cm-under-{item['label']}", alias=manager, kind=None, day=item["day"],
                      status="active", removed=None, text=None, legacy=False)
            placed.append((mount(cm["label"], heights[manager]), heights[manager], cm))
    return placed, gaps


def _legacy_name(w, site, alias, label):
    facility = (w.obj(site.key)["attrs"].get("facility") or site.code).lower()
    n = 2 if label.endswith("-b") else 1
    return f"{facility}-{LEGACY_ABBREVIATIONS[ROLES[alias]]}{n}"


def colo_tenant(w, site):
    """The metro's carrier-hotel operator, tenant of its demarc panels."""
    from .operations_context import COLOCATION
    city = w.obj(site.key)["meta"]["geography"]["city"]
    name = COLOCATION[city][0]
    key = f"tenant/colo/{city.lower()}"
    if key not in w.objects:
        group = "tenant-group/colocation"
        if group not in w.objects:
            w.add("tenant_group", group, {"name": "Colocation providers", "slug": f"{w.recipe['namespace']}-colocation",
                  "description": "Carrier hotels whose demarcation panels stand in the operator's cages"})
        w.add("tenant", key, {"name": name, "slug": f"{w.recipe['namespace']}-colo-{city.lower()}",
              "description": "Carrier-hotel operator; owns the meet-me room and its demarcation panels"},
              {"group": group})
    return key, name


def _passive_text(alias, colo_name):
    return {OSP_PANEL: "Operator OSP fibre panel: access tails, owned dark fibre and owned NOC links",
            COLO_PANEL: f"{colo_name} demarcation panel: carrier cross-connects from the meet-me room",
            BLANKING_1U: "Blanking panel", BLANKING_2U: "Blanking panel"}.get(alias, "Horizontal cable manager")


def _journal(w, target, event, day, title, body, kind="info"):
    """A dated plant-history journal entry (P0-8 shape; business hours UTC)."""
    key = f"journal/{target}/{event}"
    return w.add("journal_entry", key, {"kind": kind, "comments": timeline.entry(title, day.isoformat(), body),
                 "created": timeline.created(day.isoformat(), key)}, {"assigned_object": target}, {"history": True})


def build(site):
    """Install one PoP's plant (physical and L2) from its timeline; return (PEs, AGGs) in service.

    The caller (provider._pop) has already created the PoP management /26.
    """
    w, pop = site.w, site.id.removeprefix("pop-")
    tl = timeline.of(w)
    ensure_models(w)
    heights = {alias: w.catalog["models"][alias]["u_height"] for alias in ROLES if alias in w.catalog["models"]}
    colo, colo_name = colo_tenant(w, site)
    core = tl.tier[pop] == "core"
    ledger = history(w, pop)
    room = w.obj(site.equipment_location)
    racks = {}
    for name in ("R01", "R02") if core else ("R01",):
        racks[name] = site.authored_rack(name, len(racks), height=RACK_HEIGHT,
                                         description=f"Side {'AB'[len(racks)]} network cabinet" if core
                                         else "Network cabinet, sides A and B")
    if core:
        room["attrs"]["description"] = f"Operator cage; {CAGE_CONTRACT}"
        room["meta"]["cabinet_positions"] = CAGE_POSITIONS
    else:
        # The edge PoP's room is the cabinet itself.
        room["attrs"].update(name=f"Cabinet {w.obj(racks['R01'])['attrs']['facility_id']}", description=EDGE_CABINET)
    devices = {}
    for name, rack in racks.items():
        rack_name = w.obj(rack)["attrs"]["name"]
        placed, gaps = _elevation(w, rack, [item for r, item in ledger if r == name], heights)
        # Removed devices' units are the gaps the blanking fills.
        occupied = {u for position, height, _ in placed for u in range(position, position + height)}
        blanks = [(position, heights[alias], dict(label=f"blank-{position}", alias=alias, kind=None, status="active",
                   day=None, removed=None, text=None, legacy=False)) for position, alias in _blanking(occupied)]
        for position, _, item in sorted(placed + blanks, key=lambda row: -row[0]):
            alias, label = item["alias"], item["label"]
            if alias in (CABLE_MANAGER_1U, CABLE_MANAGER_2U, BLANKING_1U, BLANKING_2U):
                label = f"{rack_name.lower()}-{'blank' if alias in (BLANKING_1U, BLANKING_2U) else 'cm'}-{position}"
            _role(w, ROLES[alias])
            key = devices[label] = site.device(alias, label, ROLES[alias], rack=rack, position=position)
            node = w.obj(key)
            node["attrs"]["status"] = item["status"]
            if item["status"] in ("decommissioning", "planned", "inventory"):
                # Powered off, uncabled, or not yet here: every port is shut.
                for port in w.catalog["models"][node["meta"]["hardware"]]["interfaces"]:
                    w.obj(f"{key}/if/{port['name']}")["attrs"]["enabled"] = False
            if item["day"]:
                node["meta"]["installed"] = item["day"].isoformat()
            if alias in PASSIVE:
                node["attrs"]["name"] = {OSP_PANEL: f"{rack_name} OSP Panel", COLO_PANEL: f"{rack_name} Colo Demarc"}.get(
                    alias, f"{rack_name} {'Blank' if alias in (BLANKING_1U, BLANKING_2U) else 'CM'}-{position}")
                node["attrs"]["description"] = _passive_text(alias, colo_name)
                if alias in (OSP_PANEL, COLO_PANEL):
                    _panel_labels(w, key, w.obj(rack)["attrs"]["facility_id"], position)
            elif item["legacy"]:
                node["attrs"]["name"] = _legacy_name(w, site, alias, item["label"])
                node["meta"]["legacy_naming"] = True
            if alias == COLO_PANEL:
                node["refs"]["tenant"] = colo
        removed = []
        for position, height, item in sorted(gaps, key=lambda row: -row[0]):
            text = item["text"] or f"Juniper MX80 {_legacy_name(w, site, PE_LEGACY, item['label'])}"
            change = timeline.change(w, f"{rack}/{item['label']}", "-removed")
            units = f"U{position}" if height == 1 else f"U{position}-U{position + height - 1}"
            removed.append(dict(label=item["label"], units=units, text=text, installed=item["day"].isoformat(),
                                removed=item["removed"].isoformat(), change=change))
            _journal(w, rack, f"removed/{item['label']}", item["removed"], "Removed",
                     f"Removed: {text}, {change}. {units} left empty and blanked; units are not reused.")
        w.obj(rack)["meta"]["removed"] = removed
    pes, aggs = [devices["pe-a"], devices["pe-b"]], [devices["agg-a"], devices["agg-b"]]
    for agg in aggs:
        # Unused data ports are shut; provider._circuit enables a UNI it lands on.
        spec = w.catalog["models"][w.obj(agg)["meta"]["hardware"]]
        for port in spec["interfaces"]:
            if not port.get("mgmt_only") and port["name"] not in spec["lag_ports"]:
                w.obj(site.interface(agg, port["name"]))["attrs"]["enabled"] = False
    for side, pe, agg in zip("ab", pes, aggs):
        _lag(site, side, pe, agg)
    site.pdu_alias, site.feed_electrics = POP_PDU, POP_FEED
    site.power()
    _dress_power(site)
    _management(site, devices["mgmt-01"], devices["console-01"], pes, aggs)
    _history_journals(site, devices)
    return pes, aggs


def _panel_labels(w, panel, facility, position):
    """TIA-606-style front-port labels: <facility_id>.<U>:<port> (style only, no compliance claim)."""
    n = 1
    while f"{panel}/front/{n}" in w.objects:
        w.obj(f"{panel}/front/{n}")["attrs"]["label"] = f"{facility}.{position}:{n:02}"
        n += 1


def _history_journals(site, devices):
    """Dated device history: predecessor cut-overs, relic notices, the successor order."""
    w, pop = site.w, site.id.removeprefix("pop-")
    tl = timeline.of(w)
    name = lambda label: w.obj(devices[label])["attrs"]["name"]
    for side in "ab":
        if tl.refresh[pop]:
            old = f"legacy-pe-{side}"
            predecessor = name(old) if old in devices else _legacy_name(w, site, PE_LEGACY, old)
            change = timeline.change(w, devices[f"pe-{side}"], "-cutover")
            _journal(w, devices[f"pe-{side}"], "cut-over", tl.refresh[pop], "Cut over",
                     f"Replaced Juniper MX80 {predecessor} under {change}; services cut over "
                     f"{tl.refresh[pop]:%Y-%m}. Spans re-lit at 100G (the MX80 has only 10G XFP).", "success")
            if old in devices:
                _journal(w, devices[old], "installed", tl.launch[pop], "Installed",
                         f"Racked as the launch PE of {site.display} under {timeline.change(w, devices[old])}.", "success")
                _journal(w, devices[old], "cut-over", tl.refresh[pop], "Cut over",
                         f"Services moved to {name(f'pe-{side}')} under {change}; uncabled and powered off, "
                         "de-rack scheduled.", "warning")
                if timeline.MX80_END_OF_SUPPORT <= tl.as_of:
                    _journal(w, devices[old], "end-of-support", timeline.MX80_END_OF_SUPPORT, "End of support",
                             f"Vendor end of support {timeline.MX80_END_OF_SUPPORT} per vendor notice "
                             f"(end of sale {timeline.MX80_END_OF_SALE}).", "warning")
        if tl.agg_swap[pop]:
            _journal(w, devices[f"agg-{side}"], "replaced", tl.agg_swap[pop], "Replaced predecessor",
                     f"Replaced {ORIGINAL['agg'][0]}, under {timeline.change(w, devices[f'agg-{side}'], '-replace')}; "
                     f"services cut over {tl.agg_swap[pop]:%Y-%m}.", "success")
    if tl.oob_swap[pop]:
        for label in ("mgmt-01", "console-01"):
            _journal(w, devices[label], "replaced", tl.oob_swap[pop], "Replaced predecessor",
                     f"Replaced {ORIGINAL['oob'][0]}, under {timeline.change(w, devices[label], '-replace')}; "
                     f"management cut over {tl.oob_swap[pop]:%Y-%m}.", "success")
    if tl.agg[pop] == "acx5048":
        for side in "ab":
            _journal(w, devices[f"agg-{side}"], "end-of-sale", timeline.ACX5048_LAST_ORDER, "Last order date",
                     f"ACX5048 last order {timeline.ACX5048_LAST_ORDER}, end of support "
                     f"{timeline.ACX5048_END_OF_SUPPORT}, per vendor notice. No aggregation refresh has started.")
    if "agg-spare" in devices:
        model, day = tl.spare[pop]
        bought = (f"bought before the {timeline.ACX5048_LAST_ORDER} last order date, per vendor notice"
                  if model == "acx5048" else "the metro's aggregation model")
        w.obj(devices["agg-spare"])["attrs"]["description"] = f"Cold-spare aggregation chassis, pre-racked and uncabled; {bought}"
        _journal(w, devices["agg-spare"], "racked", day, "Cold spare racked",
                 f"Pre-racked as the metro's cold-spare aggregation chassis under {timeline.change(w, devices['agg-spare'])}; "
                 f"{bought}.")
    if tl.mx304[pop]:
        plan = tl.mx304[pop]
        reason = "; ".join(plan["reasons"])
        for side in "ab":
            successor, current = devices[f"pe-{side}2"], devices[f"pe-{side}"]
            w.obj(successor)["attrs"]["description"] = (f"Planned successor to {name(f'pe-{side}')}: {reason}. "
                                                        "MX304 orderable since 1H2022 per Juniper (authored 2022-07-01)")[:200]
            _journal(w, current, "successor-ordered", plan["ordered"], "Successor ordered",
                     f"MX304 successor {name(f'pe-{side}2')} ordered under {timeline.change(w, successor, '-order')}: {reason}.",
                     "warning")
            _journal(w, successor, "ordered", plan["ordered"], "Ordered",
                     f"Purchase order raised under {timeline.change(w, successor, '-order')}; units reserved in the cabinet.")
            if plan["received"]:
                _journal(w, successor, "received", plan["received"], "Received and staged",
                         "Shipment received; chassis racked and staged, not in service.", "success")


def _label(site, device, key):
    """Policy label: <site code>-<cabinet>-<ordinal>, from a per-cabinet ledger."""
    w = site.w
    rack = w.obj(device)["refs"].get("rack") if device else None
    name = w.obj(rack)["attrs"]["name"] if rack else "CG"
    code = w.obj(site.key)["attrs"].get("facility") or site.code.upper()
    return f"{code}-{name}-{w.reserve(f'fibre-cable-labels/{site.id}/{name}', key, LABEL_CAPACITY)+1:03}"


def cable(site, a, b, function, kind, *, label=None, length=None, description=None):
    """One PoP cable under the policy: labelled, typed, coloured, lengthed.

    Function "data" leaves the colour to the medium (operations._cables),
    since optics may settle a short single-mode jumper as multimode.
    """
    w = site.w
    owner = next((w.obj(e)["refs"]["device"] for e in (a, b) if "device" in w.obj(e)["refs"]), None)
    key = "cable/" + "--".join(sorted((a, b)))
    key = site.cable(a, b, kind, label=label or _label(site, owner, key))
    attrs = w.obj(key)["attrs"]
    racks = {w.obj(w.obj(e)["refs"]["device"])["refs"].get("rack") for e in (a, b) if "device" in w.obj(e)["refs"]}
    if length is not None:
        attrs["length"] = length
    elif len(racks) == 1 and None not in racks and len([e for e in (a, b) if "device" in w.obj(e)["refs"]]) == 2:
        attrs["length"] = PATCH_M
    if function in FUNCTION_COLORS:
        attrs["color"] = FUNCTION_COLORS[function]
    if description:
        attrs["description"] = description
    return key


def _lag(site, side, pe, agg):
    """Straight intra-rack 4x10G LAG: AGG ae0 on its lag_ports to PE ae1 on xe-0/1/0-3."""
    w = site.w
    vlan, _, _ = nid_management(w, site.id.removeprefix("pop-"), side)
    lags = []
    for device, name, peer in ((agg, AGG_LAG, pe), (pe, PE_LAG, agg)):
        lags.append(w.add("interface", f"{device}/if/{name}", dict(
            name=name, type="lag", enabled=True, mode="tagged",
            description=f"4x10G LAG to {w.obj(peer)['attrs']['name']}; home-side service VLANs and NID management"),
            dict(device=device, tagged_vlans=[vlan])))
    for a_name, b_name in zip(w.catalog["models"][w.obj(agg)["meta"]["hardware"]]["lag_ports"], PE_LAG_MEMBERS, strict=True):
        a, b = site.interface(agg, a_name), site.interface(pe, b_name)
        for port, lag in ((a, lags[0]), (b, lags[1])):
            w.obj(port)["attrs"]["speed"] = 10000000
            w.obj(port)["refs"]["lag"] = lag
        cable(site, a, b, "data", "smf", description="Aggregation LAG member")
        site.contract["required_connections"].append(dict(a=a, b=b))
    # The gateway unit needs ae1, which exists only now.
    _gateway(w, site.id.removeprefix("pop-"), side)


def nid_management(w, pop, side):
    """Side ``side``'s NID-management VLAN and /25 (built once), and its PE gateway unit key."""
    from .provider import MANAGEMENT_VRF
    sid = f"pop-{pop}"
    vlan = f"vlan/{sid}/{side}/nid-management"
    gateway = f"device/{sid}/pe-{side}/if/{PE_LAG}.{NID_VIDS[side]}"
    slot = w.reserve("provider-nid-management", pop, 64)
    # The second half of the infrastructure /16: provider-link-prefixes holds
    # the first half (16384 /31s), so one /24 per PoP here never collides.
    base = int(w.pool.broadcast_address) - 65535 + 32768 + 256 * slot
    net = ipaddress.ip_network((base + 128 * "ab".index(side), 25))
    if vlan not in w.objects:
        display = w.obj(f"site/{sid}")["attrs"]["name"]
        w.add("vlan", vlan, dict(name=f"NID-Mgmt-{side.upper()}", vid=NID_VIDS[side], status="active",
              description=f"In-band management of customer-premises NIDs homed on aggregation side {side.upper()}"),
              dict(site=f"site/{sid}", tenant="tenant"))
        w.add("prefix", f"prefix/{sid}/{side}/nid-management", dict(prefix=str(net), status="active",
              description=f"NID management, side {side.upper()}, at {display}; gateway PE-{side.upper()} {PE_LAG}.{NID_VIDS[side]}"),
              dict(vrf=MANAGEMENT_VRF, tenant="tenant", scope_site=f"site/{sid}", vlan=vlan))
    return vlan, net, gateway


def _gateway(w, pop, side):
    from .provider import MANAGEMENT_VRF, _ip
    vlan, net, _ = nid_management(w, pop, side)
    unit = subinterface(w, pop, side, NID_VIDS[side], vlan,
                        description=f"NID management gateway, side {side.upper()}", vrf=MANAGEMENT_VRF)
    _ip(w, unit, net, 1, MANAGEMENT_VRF, "tenant")


def nid_host(w, pop, side, owner):
    """A stable host offset (2..126) in the side's NID /25 for ``owner``."""
    return 2 + w.reserve(f"provider-nid-hosts/{pop}/{side}", owner, 125)


def attachment(w, pop, side, ordinal):
    """The home-side UNI and PE LAG for one attachment (see module docstring)."""
    if side not in ("a", "b"):
        raise DesignError(f"PoP {pop}: aggregation side must be a or b")
    if type(ordinal) is not int or not 0 <= ordinal < UNIS_PER_AGG:
        raise DesignError(f"PoP {pop} side {side.upper()}: {UNIS_PER_AGG} UNIs per aggregation switch "
                          f"({2*UNIS_PER_AGG} attachments per PoP) are exhausted. MX204 SFP+ ports are "
                          "exhausted at 8; the next platform is MX304, or split hub and access PoPs")
    sid = f"pop-{pop}"
    agg, pe = f"device/{sid}/agg-{side}", f"device/{sid}/pe-{side}"
    uni = w.catalog["models"][w.obj(agg)["meta"]["hardware"]]["uni_ports"][ordinal]
    return dict(site=f"site/{sid}", agg=agg, uni=f"{agg}/if/{uni}", agg_lag=f"{agg}/if/{AGG_LAG}",
                pe=pe, pe_lag=f"{pe}/if/{PE_LAG}")


def carry(w, pop, side, vlan):
    """Tag ``vlan`` on the home side's two LAG ends only."""
    sid = f"pop-{pop}"
    for lag in (f"device/{sid}/agg-{side}/if/{AGG_LAG}", f"device/{sid}/pe-{side}/if/{PE_LAG}"):
        tagged = w.obj(lag)["refs"].setdefault("tagged_vlans", [])
        if vlan not in tagged:
            tagged.append(vlan)


def subinterface(w, pop, side, vid, vlan, *, description, vrf=None):
    """The PE unit ae1.<vid>: virtual, parent ae1, access on ``vlan`` (carried on the home LAG)."""
    pe = f"device/pop-{pop}/pe-{side}"
    carry(w, pop, side, vlan)
    refs = dict(device=pe, parent=f"{pe}/if/{PE_LAG}", untagged_vlan=vlan)
    if vrf:
        refs["vrf"] = vrf
    return w.add("interface", f"{pe}/if/{PE_LAG}.{vid}", dict(name=f"{PE_LAG}.{vid}", type="virtual", enabled=True,
                 mode="access", description=description), refs)


def _management(site, switch, console, pes, aggs):
    """Copper management LAN, PDU network ports and serial consoles, all into R01."""
    from .provider import FXP0_HOSTS
    w = site.w
    copper = w.catalog["models"][POP_MANAGEMENT]["access_ports"]
    oob = w.catalog["models"][POP_OOB]
    eth0 = next(p["name"] for p in oob["interfaces"] if p.get("mgmt_only"))
    pdus = sorted(d for d in site.devices if w.obj(d)["refs"]["role"] == "role/pdu")
    network = next(p["name"] for p in w.catalog["models"][POP_PDU]["interfaces"] if p.get("mgmt_only"))
    plan = [(pes[0], "fxp0", FXP0_HOSTS[0], False), (pes[1], "fxp0", FXP0_HOSTS[1], False),
            (aggs[0], "em0", AGG_EM0_HOSTS[0], True), (aggs[1], "em0", AGG_EM0_HOSTS[1], True),
            (console, eth0, OOB_HOST, True), *((pdu, network, host, True) for pdu, host in zip(pdus, PDU_HOSTS[:len(pdus)], strict=True))]
    vlan, _ = site.network("management")
    for index, (device, name, host, primary) in enumerate(plan):
        a, b = site.interface(device, name), site.interface(switch, copper[index])
        cable(site, a, b, "mgmt", "cat6", description="PoP management LAN")
        for port in (a, b):
            w.obj(port)["attrs"]["mode"] = "access"
            w.obj(port)["refs"]["untagged_vlan"] = vlan
        site.address(a, "management", host=host, primary=primary, device=device)
        site.contract["required_connections"].append(dict(a=a, b=b))
    servers = [p["name"] for p in oob["console_server_ports"]]
    for index, device in enumerate((*pes, *aggs, switch)):
        port = next(f"{device}/console_port/{p['name']}" for p in
                    w.catalog["models"][w.obj(device)["meta"]["hardware"]]["console_ports"] if p["type"] == "rj-45")
        server_port = f"{console}/console_server_port/{servers[index]}"
        for end in (port, server_port):
            w.obj(end)["attrs"]["speed"] = 115200
        cable(site, port, server_port, "console", "cat6",
              description="RJ45 asynchronous serial console; separate from Ethernet management")


def _dress_power(site):
    """Label, colour and length every power cord in this PoP: A black, B red."""
    w = site.w
    for key, obj in list(w.objects.items()):
        if obj["kind"] != "cable" or obj["attrs"].get("type") != "power":
            continue
        ends = [w.obj(obj["refs"][side]) for side in ("a", "b")]
        pdu = next((w.obj(e["refs"]["device"]) for e in ends if "device" in e["refs"]
                    and w.obj(e["refs"]["device"])["refs"]["role"] == "role/pdu"), None)
        if pdu is None or pdu["refs"]["site"] != site.key:
            continue
        feed = any(e["kind"] == "power_feed" for e in ends)
        side = "b" if pdu["key"].endswith("-b") else "a"
        obj["attrs"].update(label=_label(site, pdu["key"], key), color=FUNCTION_COLORS[f"power-{side}"],
                            length=FEED_M if feed else POWER_M)


def land(site, term, port, *, carrier):
    """Land a circuit termination on its local port through the right panel.

    PoP: an owned circuit through our OSP panel, a carrier's through the colo
    demarc panel (cable label = the termination's xconnect_id), both in the
    port's own cabinet, at the next position of that panel's append-only
    ledger. NOC: through the building's copper demarcation panel beside the
    edge. Returns the cable that reaches ``port``.
    """
    w = site.w
    device = w.obj(port)["refs"]["device"]
    if site.contract["kind"] == "dc":
        panel = _noc_demarc(site, device)
        n = 1 + w.reserve(f"fibre-panel-positions/{panel}", term, len(w.catalog["models"][NOC_DEMARC_PANEL]["front_ports"]))
        entrance = site.cable(f"{panel}/rear/{n}", term, "cat6")
        w.obj(entrance)["attrs"].update(length=OSP_RUN_M, description="Building entrance to the NOC demarcation panel")
        return site.cable(f"{panel}/front/{n}", port, "cat6")
    rack = w.obj(w.obj(device)["refs"]["rack"])["attrs"]["name"]
    panel = f"device/{site.id}/{rack.lower()}-{'demarc' if carrier else 'osp'}"
    alias = COLO_PANEL if carrier else OSP_PANEL
    n = 1 + w.reserve(f"fibre-panel-positions/{panel}", term, len(w.catalog["models"][alias]["front_ports"]))
    function = "xc" if carrier else "osp"
    if carrier:
        cable(site, f"{panel}/rear/{n}", term, function, "smf", label=w.obj(term)["attrs"]["xconnect_id"],
              length=XC_RUN_M, description="Carrier-hotel cross-connect from the meet-me room")
    else:
        cable(site, f"{panel}/rear/{n}", term, function, "smf", length=OSP_RUN_M,
              description="Operator outside-plant fibre from the building entrance")
    return cable(site, f"{panel}/front/{n}", port, function, "smf", length=PATCH_M)


def _noc_demarc(site, edge):
    """The NOC's copper demarcation panel in the edge's own cabinet lane."""
    w = site.w
    domain = (int(w.obj(edge)["refs"]["rack"].rsplit("-", 1)[1]) - 1) % 4
    key = f"device/{site.id}/demarc-{domain+1:02}"
    if key not in w.objects:
        _role(w, "patch-panel")
        site.device(NOC_DEMARC_PANEL, f"demarc-{domain+1:02}", "patch-panel", rack_domain=domain)
        w.obj(key)["attrs"]["description"] = "NOC building demarcation panel for access handoffs"
    return key


def far_end(objects, endpoint, peers=None):
    """The far end a cable end reaches through 1:1 panels (a circuit termination,
    an interface), else the panel port where the path stops.

    Follows front port -> rear port -> cable peer (and rear -> front), so a
    check reading an interface's far end (optics.owned_span_m) sees the
    circuit rather than the panel. ``peers`` (cable end -> other end) may be
    precomputed by a caller tracing many ends.
    """
    if peers is None:
        peers = {}
        for obj in objects.values():
            if obj["kind"] == "cable":
                peers[obj["refs"]["a"]], peers[obj["refs"]["b"]] = obj["refs"]["b"], obj["refs"]["a"]
    seen = set()
    while objects.get(endpoint, {}).get("kind") in ("front_port", "rear_port") and endpoint not in seen:
        seen.add(endpoint)
        node = objects[endpoint]
        if node["kind"] == "front_port":
            other = node["refs"].get("rear_port")  # an unmapped front port stops the path
        else:
            front = endpoint.replace("/rear/", "/front/")
            other = front if objects.get(front, {}).get("refs", {}).get("rear_port") == endpoint else None
        if other is None or other not in peers:
            return endpoint
        endpoint = peers[other]
    return endpoint


def policy_gaps(objects, site_key):
    """Cables touching ``site_key``'s devices missing a label, type, colour or length."""
    gaps = []
    for obj in objects.values():
        if obj["kind"] != "cable":
            continue
        ends = [objects[obj["refs"][side]] for side in ("a", "b")]
        if not any(objects.get(e["refs"].get("device"), {}).get("refs", {}).get("site") == site_key for e in ends):
            continue
        missing = [field for field in ("label", "type", "color", "length") if not obj["attrs"].get(field)]
        if missing:
            gaps.append((obj["key"], missing))
    return gaps
