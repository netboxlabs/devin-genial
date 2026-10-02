"""The provider PoP plant: cage, cabinets, panels, aggregation and cable policy.

DESIGN.md §§2 and 5 (build/footprint-design, final revision) is the spec. One
PoP is a carrier-hotel cage (site -> suite -> cage) holding four cabinet
positions in one row: R01 and R02 are installed APC AR3100 42U cabinets, R03
and R04 are contracted positions with no equipment, feeds or PDUs. Each
installed cabinet is one side of the PoP, top-down:

    U42     our OSP panel (CommScope FMS-48), 48 front <-> 48 rear, 1:1
    U40-41  2U cable manager                  (hygiene rule)
    U39     colo demarc panel (Generic LC-24), the carrier hotel's, 1:1
    U38     1U cable manager                  (hygiene rule)
    U37     PE (Juniper MX204)
    U36     1U cable manager                  (hygiene rule)
    U35     aggregation switch (Juniper ACX5448-M)
    U34     PoP management switch (EX3400-24T)     R01 only
    U33     cellular console server (OM2216-L)     R01 only
    0U      R0n PDU-A, R0n PDU-B (switched AP8941, 208 V 30 A feeds)

Cable policy: every PoP cable carries a label (site code, cabinet and
ordinal, or the carrier's cross-connect ID), a medium, a colour by function
and a length. Owned fibre (customer access tails, owned dark-fibre spans and
owned NOC links) lands on our OSP panel; a carrier's circuit (leased waves,
transit, NOC private lines) is a cross-connect from the meet-me room onto the
colo's demarc panel, its xconnect_id on the circuit termination and on the
cable label. Every circuit termination stays site-scoped (VE WAN-map
limitation). Nothing here claims a route, light level or patch order.

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

from .blocks import device_type
from .model import DesignError
from .naming import CABLE_COLORS, ROLE_COLORS, titleize


# Catalog aliases (WP-A, catalog/README.md "Regional-carrier footprint aliases").
AGGREGATION = "aggregation"            # Juniper ACX5448-M
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

PASSIVE = (OSP_PANEL, COLO_PANEL, CABLE_MANAGER_1U, CABLE_MANAGER_2U, BLANKING_1U, BLANKING_2U)
PLANT_ALIASES = (AGGREGATION, POP_MANAGEMENT, POP_OOB, POP_PDU, *PASSIVE)


def _ports(prefix, names, kind, **extra):
    return [dict(name=f"{prefix}{n}", type=kind, **extra) for n in names]


def _panel(ports, front, rear, model, manufacturer, part, slug):
    names = [f"Port {n}" for n in range(1, ports+1)]
    return dict(manufacturer=manufacturer, model=model, slug=slug, part_number=part, u_height=1,
                is_full_depth=False, is_powered=False, interfaces=[], power_ports=[],
                front_ports=[dict(name=n, type=front) for n in names],
                rear_ports=[dict(name=n, type=rear) for n in names], source_ids=[],
                serial_format="{yy}@######", stub=True)


def _generic(model, slug, height, **extra):
    return dict(manufacturer="Generic", model=model, slug=slug, u_height=height, is_full_depth=False,
                is_powered=False, interfaces=[], power_ports=[], source_ids=[], serial_format="{yy}@######",
                stub=True, **extra)


_JUNOS = dict(name="Juniper Junos", slug="juniper-junos", svi_format="irb.{vid}")

# ponytail: STUB catalog entries mirroring WP-A's published aliases and port
# names, used only while catalog/hardware.json lacks the alias. A real entry
# always wins (ensure_models); delete this table once WP-A's catalog merges.
STUB_MODELS = {
    AGGREGATION: dict(
        manufacturer="Juniper", model="ACX5448-M", slug="juniper-acx5448-m", part_number="ACX5448-M",
        u_height=1, is_full_depth=True, airflow="front-to-rear",
        interfaces=[dict(name="em0", type="1000base-t", mgmt_only=True),
                    *_ports("xe-0/0/", range(44), "10gbase-x-sfpp"),
                    *_ports("et-0/1/", range(6), "100gbase-x-qsfp28")],
        uni_ports=[f"xe-0/0/{n}" for n in range(40)], lag_ports=[f"xe-0/0/{n}" for n in range(40, 44)],
        power_ports=[dict(name="power0", type="iec-60320-c14"), dict(name="power1", type="iec-60320-c14")],
        console_ports=[dict(name="console0", type="rj-45")], source_ids=[],
        serial_format="DK{yyww}######", platform=_JUNOS, stub=True),
    POP_MANAGEMENT: dict(
        manufacturer="Juniper", model="EX3400-24T", slug="juniper-ex3400-24t", part_number="EX3400-24T",
        u_height=1, is_full_depth=True, airflow="front-to-rear",
        interfaces=[*_ports("ge-0/0/", range(24), "1000base-t"), *_ports("xe-0/2/", range(4), "10gbase-x-sfpp"),
                    *_ports("et-0/1/", range(2), "juniper-vcp"), dict(name="me0", type="1000base-t", mgmt_only=True)],
        access_ports=[f"ge-0/0/{n}" for n in range(24)], uplink_ports=[f"xe-0/2/{n}" for n in range(4)],
        power_ports=[dict(name="Power Supply 0", type="iec-60320-c14"), dict(name="Power Supply 1", type="iec-60320-c14")],
        console_ports=[dict(name="Console", type="rj-45")], source_ids=[],
        serial_format="NX{yyww}#####", platform=_JUNOS, stub=True),
    POP_OOB: dict(
        manufacturer="Opengear", model="OM2216-L", slug="opengear-om2216-l", part_number="OM2216-L",
        u_height=1, is_full_depth=False,
        interfaces=[dict(name="eth0", type="1000base-t", mgmt_only=True), dict(name="eth1", type="1000base-t", mgmt_only=True),
                    dict(name="Cellular Interface (LTE)", type="lte")],
        power_ports=[dict(name="PS1", type="iec-60320-c14"), dict(name="PS2", type="iec-60320-c14")],
        console_ports=[dict(name="Console", type="rj-45")],
        console_server_ports=_ports("Port ", range(1, 17), "rj-45"), source_ids=[],
        serial_format="2216{yyww}####", stub=True),
    POP_PDU: dict(
        manufacturer="APC", model="AP8941", slug="apc-ap8941", part_number="AP8941", u_height=0, is_full_depth=False,
        interfaces=[dict(name="Network", type="100base-tx", mgmt_only=True)],
        power_ports=[dict(name="Power Port 1", type="nema-l6-30p")],
        power_outlets=[dict(name=f"Power Outlet {n}", type="iec-60320-c19" if n % 8 == 0 else "iec-60320-c13")
                       for n in range(1, 25)],
        source_ids=[], airflow="passive", serial_format="5A{yyww}E#####", stub=True),
    OSP_PANEL: _panel(48, "lc", "splice", "FMS-K2BI-L1A1-48-SP", "CommScope", "FMS-K2BI-L1A1-48-SP", "commscope-fms-48"),
    COLO_PANEL: _panel(24, "lc", "lc", "LC-24-port Fiber Patch Panel", "Generic", "LC-24", "generic-lc-24"),
    CABLE_MANAGER_1U: _generic("Cable Management Panel 1U", "generic-cable-management-1u", 1),
    CABLE_MANAGER_2U: _generic("Cable Management Panel 2U", "generic-cable-management-2u", 2),
    BLANKING_1U: _generic("Blanking Panel 1U", "generic-blanking-1u", 1, exclude_from_utilization=True),
    BLANKING_2U: _generic("Blanking Panel 2U", "generic-blanking-2u", 2, exclude_from_utilization=True),
}

ROLES = {"provider-edge": "provider-edge", AGGREGATION: "aggregation", POP_MANAGEMENT: "management",
         POP_OOB: "console-server", OSP_PANEL: "patch-panel", COLO_PANEL: "patch-panel",
         CABLE_MANAGER_1U: "cable-management", CABLE_MANAGER_2U: "cable-management",
         BLANKING_1U: "cable-management", BLANKING_2U: "cable-management", POP_PDU: "pdu"}

# The cage: four bays in one row; the first two installed.
RACK_HEIGHT = 42
CAGE_BAYS = 4
INSTALLED_BAYS = 2
RESERVED_DESCRIPTION = "Contracted cabinet position, not installed"
# The AP8941 inlet is a NEMA L6-30P: its branch circuit is 208 V / 30 A.
POP_FEED = (208, 30)

# Aggregation port plan (DESIGN.md §2): forty UNIs, a four-member LAG to the
# same-rack PE. The PE side is MX204 xe-0/1/0-3 into ae1.
UNIS_PER_AGG = 40
AGG_LAG, PE_LAG = "ae0", "ae1"
PE_LAG_MEMBERS = tuple(f"xe-0/1/{n}" for n in range(4))
PE_NOC_PORT = "xe-0/1/4"
NID_VIDS = {"a": 4001, "b": 4002}
# Host ordinals on the PoP management /26: switch irb 1, console server 3,
# PE fxp0 4/5 (provider.FXP0_HOSTS), AGG em0 6/7, then the four PDUs 8-11.
OOB_HOST, AGG_EM0_HOSTS, PDU_HOSTS = 3, (6, 7), (8, 9, 10, 11)

# Colour by function (DESIGN.md §5). Data links between our own chassis take
# their medium's colour once optics settle it (smf or mmf).
FUNCTION_COLORS = {"xc": "ffeb3b", "osp": "2196f3", "mgmt": "9e9e9e",
                   "console": CABLE_COLORS["console"], "power-a": CABLE_COLORS["power-a"],
                   "power-b": CABLE_COLORS["power-b"]}
# Authored run lengths, metres: an in-cabinet patch cord, a cord from a 0U PDU,
# the feed whip, the hotel's meet-me-room cross-connect to the cage, and our
# own building-entrance fibre from the riser to the cage.
PATCH_M, POWER_M, FEED_M, XC_RUN_M, OSP_RUN_M = 2, 2, 3, 45, 30
LABEL_CAPACITY = 1000


def ensure_models(w):
    """Fill only the plant aliases the catalog does not yet carry (see STUB_MODELS)."""
    for alias, spec in STUB_MODELS.items():
        w.catalog["models"].setdefault(alias, spec)
    for alias in (*PLANT_ALIASES, "provider-edge"):
        device_type(w, alias)


def _role(w, role):
    key = f"role/{role}"
    if key not in w.objects:
        w.add("device_role", key, {"name": titleize(role), "slug": f"{w.recipe['namespace']}-{role}",
                                   "color": ROLE_COLORS[role]})
    return key


def circuit_type(w, kind, name):
    """A circuit type the plant needs, unless the provider registry emitted it."""
    key = f"circuit-type/{kind}"
    if key not in w.objects:
        w.add("circuit_type", key, {"name": name, "slug": f"{w.recipe['namespace']}-{kind}"})
    return key


def hygiene(stack, heights, top=RACK_HEIGHT):
    """The deterministic rack-hygiene rule over one cabinet's top-down stack.

    ``stack`` is [(label, alias, kind)] top-down, kind one of "panel-48",
    "panel-24", "pe", "agg" or None. A 2U cable manager goes under each 48-port
    panel, a 1U one under each 24-port panel and between a PE and the
    aggregation switch directly below it. Items mount contiguously down from
    ``top``. Blanking panels fill only 1-2U gaps inside the occupied band.
    Returns [(position, label, alias)] top-down; managers are labelled
    ``cm-<U>`` and blanking panels ``blank-<U>``.
    """
    items = []
    for i, (label, alias, kind) in enumerate(stack):
        items.append((label, alias))
        below = stack[i+1][2] if i+1 < len(stack) else None
        if kind == "panel-48":
            items.append((None, CABLE_MANAGER_2U))
        elif kind == "panel-24" or (kind == "pe" and below == "agg"):
            items.append((None, CABLE_MANAGER_1U))
    placed, unit = [], top
    for label, alias in items:
        position = unit - heights[alias] + 1
        if position < 1:
            raise DesignError(f"Cabinet stack exceeds {top}U")
        placed.append((position, label or f"cm-{position}", alias))
        unit = position - 1
    return sorted(placed + _blanking(placed, heights), key=lambda item: -item[0])


def _blanking(placed, heights):
    """One blanking panel per 1-2U gap strictly inside the occupied band."""
    occupied = {u for position, _, alias in placed for u in range(position, position + heights[alias])}
    fill, gap = [], []
    for u in range(max(occupied), min(occupied) - 1, -1):
        if u not in occupied:
            gap.append(u)
            continue
        if 1 <= len(gap) <= 2:
            fill.append((min(gap), f"blank-{min(gap)}", BLANKING_1U if len(gap) == 1 else BLANKING_2U))
        gap = []
    return fill


def stack(side):
    """Top-down active stack of one installed cabinet, before the hygiene rule."""
    rack = f"r{1 + 'ab'.index(side):02}"
    rows = [(f"{rack}-osp", OSP_PANEL, "panel-48"), (f"{rack}-demarc", COLO_PANEL, "panel-24"),
            (f"pe-{side}", "provider-edge", "pe"), (f"agg-{side}", AGGREGATION, "agg")]
    if side == "a":
        rows += [("mgmt-01", POP_MANAGEMENT, None), ("console-01", POP_OOB, None)]
    return rows


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


def build(site):
    """Install one PoP's cage plant (physical and L2); return (PEs, AGGs).

    The caller (provider._pop) has already created the PoP management /26.
    """
    w = site.w
    ensure_models(w)
    for role in set(ROLES.values()):
        _role(w, role)
    heights = {alias: w.catalog["models"][alias]["u_height"] for alias in ROLES}
    colo, colo_name = colo_tenant(w, site)
    racks = [site.authored_rack(f"R{bay+1:02}", bay, height=RACK_HEIGHT,
                                status="active" if bay < INSTALLED_BAYS else "reserved",
                                description=f"Side {'AB'[bay]} network cabinet" if bay < INSTALLED_BAYS else RESERVED_DESCRIPTION)
             for bay in range(CAGE_BAYS)]
    devices = {}
    for side, rack in zip("ab", racks):
        rack_name = w.obj(rack)["attrs"]["name"]
        for position, label, alias in hygiene(stack(side), heights):
            if label.startswith(("cm-", "blank-")):
                label = f"{rack_name.lower()}-{label}"
            key = devices[label] = site.device(alias, label, ROLES[alias], rack=rack, position=position)
            node = w.obj(key)
            if alias in PASSIVE:
                node["attrs"].pop("serial", None)
                node["attrs"]["name"] = {OSP_PANEL: f"{rack_name} OSP Panel", COLO_PANEL: f"{rack_name} Colo Demarc"}.get(
                    alias, f"{rack_name} {'Blank' if alias in (BLANKING_1U, BLANKING_2U) else 'CM'}-{position}")
                node["attrs"]["description"] = _passive_text(alias, colo_name)
            if alias == COLO_PANEL:
                node["refs"]["tenant"] = colo
    pes, aggs = [devices["pe-a"], devices["pe-b"]], [devices["agg-a"], devices["agg-b"]]
    for side, pe, agg in zip("ab", pes, aggs):
        _lag(site, side, pe, agg)
    site.pdu_alias, site.feed_electrics = POP_PDU, POP_FEED
    site.power()
    _dress_power(site)
    _management(site, devices["mgmt-01"], devices["console-01"], pes, aggs)
    return pes, aggs


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
    for a_name, b_name in zip(w.catalog["models"][AGGREGATION]["lag_ports"], PE_LAG_MEMBERS, strict=True):
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
        w.add("vlan", vlan, dict(name=f"NID Management {side.upper()}", vid=NID_VIDS[side], status="active",
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
    uni = w.catalog["models"][AGGREGATION]["uni_ports"][ordinal]
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
            (console, eth0, OOB_HOST, True), *((pdu, network, host, True) for pdu, host in zip(pdus, PDU_HOSTS, strict=True))]
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


def circuit_behind(objects, endpoint, peers=None):
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
            other = node["refs"]["rear_port"]
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
