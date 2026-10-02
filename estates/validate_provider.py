"""Independent finite provider inventory, physical path and offered-load checks.

The authored policy is restated here; construction helpers and explanatory
contracts are deliberately not validation authorities. Routing is inventory
intent, never an executed reachability or convergence test.

v0.17 regional-carrier footprint (build/footprint-design/DESIGN.md §§2-5, §7):
every PoP is a carrier-hotel cage of two installed 42U cabinets holding a PE,
an aggregation switch, an operator OSP panel and the colo's demarc panel, with
the management switch and cellular console server in R01; every customer
premises is a NID-terminated access circuit into one aggregation side, with a
carrier CE in an MPOE wall cabinet where the service is managed. Every gate
below re-derives its obligation from the recipe, the ledgers and the graph.

v0.18 lived-in carrier (build/lived-in-design/DESIGN.md §§2-4, §7): the
provider timeline is restated from its frozen ``provider-timeline`` ledgers
(estates/timeline.py is never imported), and each cabinet's install history
from it: a core PoP's cage of R01/R02 or an edge PoP's one cabinet, units
top-down from ``provider-cabinet-u``, blanked never-reused gaps with dated
rack journals, MX80 relics, the MX304 successor, the cold spare, the time
server and the DDoS appliance, exchanges and former customers.
"""

from collections import Counter, defaultdict, deque
from datetime import date, timedelta
from decimal import Decimal
from ipaddress import ip_interface, ip_network
import math
import re

from .validate_datacenter import validate_power, validate_resolved
from .validate_poe import analyze as analyze_poe
from .validate_optics import analyze as analyze_optics
from . import __version__
from .model import digest, selected_alias
from .naming import bandwidth, port_speed, role_label, titleize
# Authored address localities (suburbs map to their metro); geography data, not builder policy.
from .places import ADDRESS_STREETS, LOCALITIES, MILWAUKEE_COUNTY, carrier_suite


METROS = {"chicago": ("Chicago", "IL", "Illinois", "America/Chicago"),
          "detroit": ("Detroit", "MI", "Michigan", "America/Detroit"),
          "cleveland": ("Cleveland", "OH", "Ohio", "America/New_York"),
          "milwaukee": ("Milwaukee", "WI", "Wisconsin", "America/Chicago")}
DC_OFFSETS = {"management": 0, "applications": 6, "database": 7, "backup": 8, "storage": 10}
SERVICE_POLICY = (("identity", "premises", 128, 4, 8192, 100000, 443),
                  ("dns", "pops", 16, 2, 4096, 40000, 53),
                  ("monitoring", "pops", 16, 4, 16384, 200000, 443),
                  ("provisioning", "premises", 128, 4, 8192, 100000, 443))
SPAN_KEY = re.compile(r"circuit/backbone/([a-z][a-z0-9-]{0,19})-([ab])/([a-z][a-z0-9-]{0,19})-([ab])")
# Metro centres (lat, lon), restated from the authored geography.
METRO_POINTS = {"chicago": (41.8781, -87.6298), "detroit": (42.3314, -83.0458),
                "cleveland": (41.4993, -81.6944), "milwaukee": (43.0389, -87.9065)}
# Carrier-owned public space (RFC 5737, re-packed in 0.17) and AS numbers
# (RFC 5398), restated. The backbone takes the first half of 192.0.2.0/24;
# customer DIA takes every remaining documentation block except the upstreams'
# /28s, and managed-DIA CE links a /26 of it.
PUBLIC_POOLS = {"loopbacks": ip_network("192.0.2.0/27"), "pair": ip_network("192.0.2.32/27"),
                "backbone": ip_network("192.0.2.64/26")}
PUBLIC_AGGREGATES = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
DIA_POOLS = ("192.0.2.128/25", "198.51.100.0/25", "198.51.100.128/26", "203.0.113.0/24")
DIA_LINK_POOL = ip_network("203.0.113.192/26")
DIA_NETWORKS = [net for pool in DIA_POOLS for net in ip_network(pool).subnets(new_prefix=29)
                if not net.overlaps(DIA_LINK_POOL)]
# Each upstream's own transit assignment: outside the operator's tenancy.
UPSTREAM_POOLS = {"a": ip_network("198.51.100.224/28"), "b": ip_network("198.51.100.240/28")}
# The cellular out-of-band carrier's RFC 6598 /30 per PoP console server.
OOB_POOL = ip_network("100.64.0.0/24")
DOCUMENTATION_ASNS = range(64496, 64512)
LOOPBACK_SLOTS = 30
ROUTE_FACTOR = 1.3
# A premises sits in its serving PoP's area: within this distance, and nearer
# it than any other same-metro PoP allocated before the premises; premises
# stand on a street grid at least this far apart.
PREMISES_KM = 25
PREMISES_SPACING_M = 300
LEASED_COMMIT = 100000000
CARRIER_NAMES = ("transport-a", "transport-b", "transit-a", "transit-b", "oob")
CORE, MANAGEMENT, OOB = None, "vrf/provider", "vrf/oob"
HUB_RT, SPOKE_RT = 9000, 9001
FXP0_HOSTS, AGG_EM0_HOSTS, PDU_HOSTS, OOB_HOST = (4, 5), (6, 7), (8, 9, 10, 11), 3
TIME_SERVER_HOST, DDOS_HOST = 20, 21  # PoP services on the management /26, clear of the plant's 1-11
# Services, tiers and the access layer, restated (DESIGN §§2-3).
SERVICES = ("private-l3", "dia", "epl")
LARGE_TIERS = (2000, 5000)
UNIS_PER_SIDE, LAG_MEMBERS, LAG_MEMBER_KBPS = 40, 4, 10000000
ATTACHMENTS_PER_POP = 2 * UNIS_PER_SIDE
SERVICE_VLAN_BASE, NID_VIDS = 1001, {"a": 4001, "b": 4002}
EPL_VCID_BASE = 10001
PE_LAG, AGG_LAG = "ae1", "ae0"
PE_LAG_MEMBERS = tuple(f"xe-0/1/{n}" for n in range(4))
PE_NOC_PORT, PE_MGMT_PORT, PE_TRANSIT_PORT = "xe-0/1/4", "xe-0/1/6", "xe-0/1/7"
# Cage and cabinet grammar: four bays in one row at 0.6 m pitch, two installed.
CAGE_BAYS, INSTALLED_BAYS, CABINET_U = 4, 2, 42
RESERVED_CABINET = "Contracted cabinet position, not installed"
# Each installed cabinet, top-down (DESIGN §5 table): U -> (device stem, hardware).
ELEVATION = {"a": {42: ("r01-osp", "osp-panel"), 40: ("r01-cm-40", "cable-manager-2u"),
                   39: ("r01-demarc", "demarc-panel"), 38: ("r01-cm-38", "cable-manager-1u"),
                   37: ("pe-a", "provider-edge"), 36: ("r01-cm-36", "cable-manager-1u"),
                   35: ("agg-a", "aggregation"), 34: ("mgmt-01", "pop-mgmt"), 33: ("console-01", "oob-server")},
             "b": {42: ("r02-osp", "osp-panel"), 40: ("r02-cm-40", "cable-manager-2u"),
                   39: ("r02-demarc", "demarc-panel"), 38: ("r02-cm-38", "cable-manager-1u"),
                   37: ("pe-b", "provider-edge"), 36: ("r02-cm-36", "cable-manager-1u"),
                   35: ("agg-b", "aggregation")}}
# Colour by function at a PoP and the meet-me room's own panel numbering.
MMR_PP = re.compile(r"Meet-me room panel (MMR-\d{2}), port ([1-9]|[1-3]\d|4[0-8])")
# Every PoP cable states these (DESIGN §5 cable policy).
CABLE_POLICY_FIELDS = ("label", "type", "color", "length")
# Generated-object ceiling (DESIGN §3).
OBJECT_CEILING = 40000
ASN_TEXT = 30
# The serving optic of a premises not yet in service: planned (not yet
# received, no serial) or staged (on hand, serialized), in a shut cage.
PENDING_OPTIC = {"planned": "planned", "provisioning": "staged"}
# Customers' own LAN plans, restated: several customers number from the same
# space, each inside its own VRF; a plan overlapping address_pool is skipped.
CUSTOMER_LAN_PLANS = tuple(ip_network(p) for p in ("172.20.0.0/16", "192.168.0.0/16", "172.24.0.0/16", "10.10.0.0/16"))
BGP_KINDS = {"bgp_routing_policy", "bgp_peer_group", "bgp_session"}
BGP_FIELDS = {"bgp_routing_policy": {"name", "weight", "description"},
              "bgp_peer_group": {"name", "description"},
              "bgp_session": {"name", "status", "description"}}
VIRTUAL_CIRCUIT_NOTE = "Each premises' CE-to-PE peering is in the Customer Private L3 BGP peer group."
BGP_POLICIES = {
    "transit-in": ("Transit Import", 100, "Import policy for upstream transit peers"),
    "transit-out": ("Transit Export", 110, "Export policy for upstream transit peers"),
    "customer-in": ("Customer Import", 200, "Import policy for private L3 customer edges"),
    "customer-out": ("Customer Export", 210, "Export policy for private L3 customer edges"),
}
BGP_GROUPS = {
    "ibgp-core": ("iBGP Core", "Internal peerings between provider edge loopbacks", (), (), True),
    "transit": ("Transit Upstream", "External upstream peerings at the backbone transit handoffs",
                ("transit-in",), ("transit-out",), False),
    "customer": ("Customer Private L3", "Customer edge peerings on private-L3 access circuits",
                 ("customer-in",), ("customer-out",), False),
}
SERVICE_LABELS = {"private-l3": "Private L3 VPN", "dia": "Dedicated internet access", "epl": "Ethernet private line"}
CIRCUIT_PHRASES = {"private-l3": "private L3 VPN", "dia": "dedicated internet", "epl": "Ethernet private line"}
CID_CODES = {"private-l3": "PL3", "dia": "DIA", "epl": "EPL"}


# Premises lifecycle, restated from the recipe: an onboarding customer is
# planned; a planned entry under an active customer is provisioning; an entry
# may be decommissioning. Every record the premises owns carries the status
# below, and none of them counts as healthy, in-service capacity.
LIFE = {
    "active": dict(site="active", device="active", circuit="active", cable="connected", rack="active",
                   ip="active", prefix="active", vlan="active", bgp="active"),
    "planned": dict(site="planned", device="planned", circuit="planned", cable="planned", rack="planned",
                    ip="reserved", prefix="reserved", vlan="reserved", bgp="planned"),
    "provisioning": dict(site="staging", device="staged", circuit="provisioning", cable="planned", rack="planned",
                         ip="reserved", prefix="reserved", vlan="reserved", bgp="planned"),
    "decommissioning": dict(site="decommissioning", device="decommissioning", circuit="deprovisioning",
                            cable="decommissioning", rack="deprecated", ip="deprecated", prefix="deprecated",
                            vlan="deprecated", bgp="offline"),
}


# --- v0.18 lived-in plant, restated (build/lived-in-design DESIGN §3, §4.1) ---
# Vendor facts (VERIFICATION §1) and authored milestones the history follows.
MX204_AVAILABLE, ACX5048_AVAILABLE, ACX5448M_CUTOVER = date(2018, 1, 1), date(2015, 1, 1), date(2019, 7, 1)
NS2_ADOPTED, AGG_SWAP_START, OOB_SWAP_START = date(2018, 3, 1), date(2016, 3, 1), date(2019, 4, 1)
ACX5048_LAST_ORDER, MX204_EOL_ANNOUNCED, MX304_AVAILABLE = date(2022, 12, 31), date(2026, 6, 15), date(2022, 7, 1)
RELIC_DAYS = 730  # an MX80 pair stays racked "decommissioning" if cut over within 24 months
SFP_PLUS_PORTS = tuple(f"xe-0/1/{n}" for n in range(8))
PE_IX_PORT = "xe-0/1/5"
# Each PoP-plant model's role; the history's aliases.
PLANT_ROLES = {"provider-edge": "provider-edge", "provider-edge-legacy": "provider-edge",
               "provider-edge-successor": "provider-edge", "aggregation": "aggregation",
               "aggregation-legacy": "aggregation", "pop-mgmt": "management", "oob-server": "console-server",
               "osp-panel": "patch-panel", "demarc-panel": "patch-panel", "cable-manager-1u": "cable-management",
               "cable-manager-2u": "cable-management", "blanking-1u": "cable-management",
               "blanking-2u": "cable-management", "pdu-switched": "pdu", "ddos-mitigation": "ddos-mitigation",
               "time-server": "time-server"}
AGG_ALIASES = {"acx5048": "aggregation-legacy", "acx5448m": "aggregation"}
PASSIVE_NAMES = {"osp-panel": "{rack} OSP Panel", "demarc-panel": "{rack} Colo Demarc", "cable-manager-1u": "{rack} CM-{u}",
                 "cable-manager-2u": "{rack} CM-{u}", "blanking-1u": "{rack} Blank-{u}", "blanking-2u": "{rack} Blank-{u}"}
LEGACY_ABBREVIATIONS = {"provider-edge": "rtr", "aggregation": "agg", "time-server": "ntp",
                        "management": "sw", "console-server": "con"}
CAGE_CONTRACT = "Operator cage; Cage contract: 4 cabinet positions; 2 installed; expansion by change order"
EDGE_CABINET = "Leased single cabinet in the carrier hotel; both PoP sides share it"
NOT_IN_SERVICE = ("decommissioning", "planned", "staged", "inventory")
# Internet exchanges (DESIGN §2, K8): one fictional exchange per metro, hosted at
# its founding PoP on PE-A xe-0/1/5; the Milwaukee exchange relocated, so its
# old port at the metro's second PoP is being withdrawn.
IXES = {"chicago": ("Calumet Internet Exchange", "CAL-IX"), "detroit": ("Rouge River IX", "RRIX"),
        "cleveland": ("Erie Shore IX", "ESIX"),
        "milwaukee": ("Cream City Internet Exchange", "Cream City Internet Exchange")}
IX_RELOCATED = "milwaukee"
IX_ASNS = range(65536, 65552)  # RFC 5398 32-bit documentation range
IX_ROUTE_SERVERS = 2
IX_IPV4_NOTE = "No IPv4 peering: the IPv4 blocks are committed to dedicated internet customer assignments."
# Former customers (P0-6): an order ID above every premises serial.
FORMER_ORDER_BASE = 90000
# The detection controller pair exists only beside a TMS (K7).
CONTROLLER_POLICY = ("ddos-detection", 8, 32768, 200000)
LEGACY_NID_BEFORE = date(2016, 1, 1)
ONBOARDING_YEAR_SHARE = 0.15  # DESIGN gate 12 and L16: premises service starts per calendar year
ONBOARDING_MIN_BOOK = 40      # a smaller book is too few for a yearly share to mean anything


def _choose(recipe, key, label, choices):
    """Restated stable local variation: the seed, a subject key, a label and the generator version."""
    return choices[int(digest([recipe["seed"], key, label, __version__]), 16) % len(choices)]


def _timeline(recipe, reservations, pops, order, premises):
    """The frozen provider timeline, re-derived from its ledgers and the recipe.

    Launch and refresh days are read from ``provider-timeline/<event>/<pop>``;
    every other date is the authored rule over them (estates/timeline.py
    restated, never imported). Raises ValueError when a ledger is missing.
    """
    as_of = date.fromisoformat(recipe["as_of"])
    after = lambda pop, label, day, spread, low=0: day + timedelta(days=_choose(recipe, pop, f"timeline-{label}",
                                                                                range(low, low + spread)))

    def ledger(event, pop):
        value = reservations.get(f"provider-timeline/{event}/{pop}")
        if not isinstance(value, dict) or set(value) != {"day"} or not _integer(value["day"], 1, date.max.toordinal()):
            raise ValueError(f"provider-timeline/{event}/{pop} must hold the frozen day of that event.")
        return date.fromordinal(value["day"])

    ordered = sorted(pops, key=order.get)
    tl = dict(as_of=as_of, launch={p: ledger("launch", p) for p in ordered})
    founding = {}
    for pop in ordered:
        founding.setdefault(pops[pop]["metro"], pop)
    noc = {p: tuple(s for s in "ab" if recipe.get(f"noc_pop_{s}") == p) for p in ordered}
    transit = {p: ("a", "b")[i] if i < 2 else None for i, p in enumerate(ordered)}
    dia = Counter(pop for customer, pop, _ in premises.values() if customer["service"] == "dia")
    ix = {p: founding[pops[p]["metro"]] == p for p in ordered}
    tl.update(founding=founding, noc=noc, transit=transit, ix=ix, dia=dia,
              tier={p: "core" if noc[p] or transit[p] or ix[p] else "edge" for p in ordered})
    tl["refresh"] = {p: ledger("refresh", p) if tl["launch"][p] < MX204_AVAILABLE else None for p in ordered}
    if any(f"provider-timeline/refresh/{p}" in reservations for p in ordered if tl["refresh"][p] is None):
        raise ValueError("Only a PoP launched on the MX80 carries a frozen refresh date.")
    tl["relic"] = {p: d is not None and (as_of - d).days <= RELIC_DAYS for p, d in tl["refresh"].items()}
    tl["agg"] = {p: "acx5048" if d < ACX5448M_CUTOVER else "acx5448m" for p, d in tl["launch"].items()}
    tl["agg_swap"] = {p: after(p, "agg-swap", max(AGG_SWAP_START, d + timedelta(days=60)), 240)
                      if d < ACX5048_AVAILABLE else None for p, d in tl["launch"].items()}
    tl["oob_swap"] = {p: after(p, "oob-swap", max(OOB_SWAP_START, d + timedelta(days=60)), 240)
                      if d < OOB_SWAP_START else None for p, d in tl["launch"].items()}
    tl["timing"] = {p: tl["launch"][p] if noc[p] else None for p in ordered}
    tl["spare"] = {p: None for p in ordered}
    for pop in founding.values():
        if tl["agg"][pop] == "acx5048":
            day = min(after(pop, "cold-spare", max(date(2022, 3, 1), tl["launch"][pop]), 240),
                      ACX5048_LAST_ORDER - timedelta(days=14))
        else:
            day = after(pop, "cold-spare", tl["launch"][pop] + timedelta(days=120), 280)
        if day <= as_of - timedelta(days=30):
            tl["spare"][pop] = (tl["agg"][pop], day)
    # The MX204 SFP+ budget: four LAG members and the management uplink, then
    # the NOC, transit and (PE-A) exchange handoffs; eight is the ceiling.
    tl["sfp_plus"] = {(p, s): 5 + (s in noc[p]) + (transit[p] == s) + (ix[p] and s == "a") for p in ordered for s in "ab"}
    tl["mx304"], tl["ddos"] = {}, {}
    for pop in ordered:
        plan = None
        if any(tl["sfp_plus"][(pop, s)] >= len(SFP_PLUS_PORTS) for s in "ab"):
            ordered_on = after(pop, "mx304-ordered", max(MX204_EOL_ANNOUNCED, MX304_AVAILABLE), 30, 10)
            if ordered_on <= as_of - timedelta(days=3):
                received = after(pop, "mx304-received", ordered_on, 31, 60)
                received = received if received <= as_of else None
                plan = dict(ordered=ordered_on, received=received, status="staged" if received else "planned")
        tl["mx304"][pop] = plan
        tl["ddos"][pop] = (min(as_of - timedelta(days=3), after(pop, "ddos-racked", plan["ordered"], 26, 20))
                           if plan and transit[pop] and dia[pop] else None)
    return tl


def _history(tl, pop):
    """One PoP's install ledger, restated: [(rack name, item)] in install order.

    A removed device (``removed`` set) keeps its units as a gap; its alias is None
    for a never-inventoried predecessor (1U).
    """
    core, launch, items = tl["tier"][pop] == "core", tl["launch"][pop], []
    rack = lambda side: "R01" if side == "a" or not core else "R02"

    def add(side, label, alias, kind, day, status="active", removed=None, order=0):
        items.append((day, order, len(items), rack(side), dict(label=label, alias=alias, kind=kind, day=day,
                      status=status, removed=removed)))

    pe0 = "provider-edge-legacy" if tl["refresh"][pop] else "provider-edge"
    for side in ("ab" if core else "a"):
        add(side, f"{rack(side).lower()}-osp", "osp-panel", "panel-48", launch)
        add(side, f"{rack(side).lower()}-demarc", "demarc-panel", "panel-24", launch)
    refresh = tl["refresh"][pop]
    for side in "ab":
        add(side, f"legacy-pe-{side}" if refresh else f"pe-{side}", pe0, "router", launch,
            status="decommissioning" if tl["relic"][pop] else "active",
            removed=refresh if refresh and not tl["relic"][pop] else None)
    if tl["timing"][pop]:
        add("a", "ntp-01", "time-server", None, tl["timing"][pop])
    agg = AGG_ALIASES[tl["agg"][pop]]
    for side in "ab":
        if tl["agg_swap"][pop]:
            add(side, f"original-agg-{side}", None, None, launch, removed=tl["agg_swap"][pop])
        else:
            add(side, f"agg-{side}", agg, None, launch)
    if tl["oob_swap"][pop]:
        add("a", "original-console", None, None, launch, removed=tl["oob_swap"][pop])
    else:
        add("a", "mgmt-01", "pop-mgmt", None, launch)
        add("a", "console-01", "oob-server", None, launch)
    if tl["agg_swap"][pop]:
        for side in "ab":
            add(side, f"agg-{side}", agg, None, tl["agg_swap"][pop])
    if tl["oob_swap"][pop]:
        add("a", "mgmt-01", "pop-mgmt", None, tl["oob_swap"][pop])
        add("a", "console-01", "oob-server", None, tl["oob_swap"][pop])
    if tl["spare"][pop]:
        add("b", "agg-spare", AGG_ALIASES[tl["spare"][pop][0]], None, tl["spare"][pop][1], status="inventory")
    if refresh:
        for side in "ab":
            add(side, f"pe-{side}", "provider-edge", "router", refresh)
    if tl["mx304"][pop]:
        plan = tl["mx304"][pop]
        for side in "ab":
            add(side, f"pe-{side}2", "provider-edge-successor", "router", plan["received"] or plan["ordered"],
                status=plan["status"])
    if tl["ddos"][pop]:
        add("a", "ddos-01", "ddos-mitigation", None, tl["ddos"][pop], status="staged", order=1)
    return [(name, item) for _, _, _, name, item in sorted(items, key=lambda row: row[:3])]


def _mounts(items):
    """Each cabinet item and the hygiene rule's cable managers, in mounting order.

    A 48-port panel takes a 2U manager below it, a 24-port panel and each router
    (or same-day router pair) a 1U manager.
    """
    result = []
    for i, item in enumerate(items):
        result.append(item)
        after = items[i + 1] if i + 1 < len(items) else None
        manager = {"panel-48": "cable-manager-2u", "panel-24": "cable-manager-1u"}.get(item["kind"])
        if item["kind"] == "router" and not (after and after["kind"] == "router" and after["day"] == item["day"]):
            manager = "cable-manager-1u"
        if manager:
            result.append(dict(label=f"cm-under-{item['label']}", alias=manager, kind=None, day=item["day"],
                               status="active", removed=None))
    return result


def _blanks(occupied):
    """Blanking panels filling each gap inside the occupied band: 2U from the gap's top, then 1U."""
    fill, gap = [], []
    for u in range(max(occupied), min(occupied) - 1, -1):
        if u not in occupied:
            gap.append(u)
            continue
        while gap:
            if len(gap) >= 2:
                fill.append((gap[1], "blanking-2u")); gap = gap[2:]
            else:
                fill.append((gap[0], "blanking-1u")); gap = []
    return fill


def _stage(customer, pop):
    if customer.get("status", "active") == "planned":
        return "planned"
    status = next(e.get("status", "active") for e in customer["sites"] if e["pop"] == pop)
    return "provisioning" if status == "planned" else status


def _km(a, b):
    """Great-circle kilometres (haversine, mean Earth radius)."""
    (la1, lo1), (la2, lo2) = ((math.radians(x), math.radians(y)) for x, y in (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _operator_code(name):
    """Restated: the operator's service-ID prefix is its name's initials."""
    words = re.findall(r"[A-Za-z]+", name)
    code = "".join(word[0] for word in words).upper()[:4]
    return code if len(code) >= 2 else (words[0][:3].upper() if words else "OPR")


def _holder(name, limit=ASN_TEXT):
    """Restated: an AS holder's name cut on a word boundary to fit graph labels."""
    if len(name) <= limit:
        return name
    cut = name[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,&-") if " " in name[:limit + 1] else ""
    return cut if cut else name[:limit]


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _key(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,19}", value) is not None


def _handoff(rate, usable):
    """Restated: the physical handoff an attachment needs (1G, else the 10G tier)."""
    need = Decimal(rate) * (2 - usable)
    return 1000 if need <= 1000 else 10000 if need <= 10000 else None


def _name(customer):
    return customer.get("name") or titleize(customer["key"])


def _site_peak(customer, pop):
    return next(e for e in customer["sites"] if e["pop"] == pop).get("site_peak_mbps", customer["site_peak_mbps"])


def _recipe(recipe):
    """Bound saved inputs before deriving potentially large obligations."""
    reserve = recipe.get("reserve_fraction")
    if (type(reserve) not in (int, float) or not math.isfinite(reserve) or not 0.1 <= reserve <= 0.4 or
            recipe.get("topology") != "incremental-mesh" or recipe.get("patching") not in ("direct", "panels") or
            recipe.get("reservation_user") != "" or recipe.get("demo") not in ("baseline", "loss-of-power-diversity", "provider-span-maintenance")):
        raise ValueError("Provider policy requires bounded reserve, incremental-mesh topology and a supported baseline/power/span demo.")
    if (not isinstance(recipe.get("namespace"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,18}[a-z0-9]", recipe["namespace"]) or
            not isinstance(recipe.get("name"), str) or not 1 <= len(recipe["name"]) <= 80 or
            not _integer(recipe.get("seed"), 0, 2**63 - 1)):
        raise ValueError("Provider namespace, display name and seed must retain their supported bounds.")
    date.fromisoformat(recipe.get("as_of", ""))
    pool = ip_network(recipe.get("address_pool", ""), strict=True)
    if (pool.version != 4 or not 8 <= pool.prefixlen <= 12 or
            not any(pool.subnet_of(ip_network(p)) for p in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))):
        raise ValueError("Provider site and infrastructure reservations require an aligned RFC1918 /8 through /12.")
    tiers = recipe.get("wan_tiers_mbps")
    if (not isinstance(tiers, list) or not tiers or any(not _integer(t, 1, 1000) for t in tiers) or
            tiers != sorted(set(tiers)) or tiers[-1] != 1000):
        raise ValueError("Customer commitments need strictly increasing integer tiers ending at 1000 Mbps.")
    raw_pops, customers = recipe.get("pops"), recipe.get("customers")
    if not isinstance(raw_pops, list) or not 3 <= len(raw_pops) <= LOOPBACK_SLOTS // 2:
        raise ValueError(f"Provider demand needs 3–{LOOPBACK_SLOTS // 2} distinct keyed PoPs (two PE loopbacks each in the /27).")
    pops = {}
    for item in raw_pops:
        if (not isinstance(item, dict) or not _key(item.get("key")) or item["key"] in pops or
                not isinstance(item.get("metro"), str) or item["metro"] not in METROS):
            raise ValueError("Each PoP requires one unique bounded key and an authored metro.")
        pops[item["key"]] = item
    if len({item["metro"] for item in pops.values()}) < 3:
        raise ValueError("The regional backbone requires at least three authored metros.")
    noc_a, noc_b, noc_peak = (recipe.get(field) for field in ("noc_pop_a", "noc_pop_b", "noc_peak_mbps"))
    usable = Decimal(1) - Decimal(str(reserve))
    if (not isinstance(noc_a, str) or not isinstance(noc_b, str) or noc_a not in pops or noc_b not in pops or noc_a == noc_b or
            not _integer(noc_peak, 1, 800) or Decimal(noc_peak) > 1000 * usable):
        raise ValueError("The NOC needs two distinct requested PoPs and headroom on each 1 Gbps handoff.")
    if not isinstance(customers, list) or not 1 <= len(customers) <= 256:
        raise ValueError("Provider demand needs 1–256 keyed customers.")
    if (not _integer(recipe.get("asn_base"), 4200000000, 4294967294 - 1023) or
            (recipe["asn_base"] - 4200000000) % 1024):
        raise ValueError("The complete 1024-ASN block must remain in the private 32-bit range.")
    demand, seen, names, occupancy = {}, set(), set(), Counter()
    for item in customers:
        service = item.get("service") if isinstance(item, dict) else None
        if (service not in SERVICES or not _key(item.get("key")) or item["key"] in seen or
                item.get("status", "active") not in ("active", "planned") or
                not isinstance(item.get("sites"), list) or
                ("name" in item and (not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 60))):
            raise ValueError("Customer keys, service, status, display name and site entries must be bounded.")
        seen.add(item["key"])
        if _name(item) in names:
            raise ValueError("Customer display names must be distinct.")
        names.add(_name(item))
        low, high = {"private-l3": (2, len(pops)), "dia": (1, 1), "epl": (2, 2)}[service]
        if not low <= len(item["sites"]) <= high:
            raise ValueError("A private-L3 customer spans two or more PoPs, a DIA customer one premises and an EPL two ends.")
        entries = set()
        for entry in item["sites"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("pop"), str) or entry["pop"] not in pops or
                    entry["pop"] in entries or not _integer(entry.get("count"), 1, 12 if service == "private-l3" else 1) or
                    entry.get("status", "active") not in ("active", "planned", "decommissioning") or
                    (item.get("status", "active") == "planned" and entry.get("status", "planned") != "planned") or
                    ("site_peak_mbps" in entry and (service != "private-l3" or not _integer(entry["site_peak_mbps"], 1, 800)))):
                raise ValueError("Customer site entries need distinct valid PoPs, bounded counts and a lifecycle status; "
                                 "every entry of a planned customer is planned.")
            entries.add(entry["pop"])
            occupancy[entry["pop"]] += entry["count"] + (service == "private-l3" and entry["pop"] == item.get("hub_pop"))
            for ordinal in range(1, entry["count"] + 1):
                sid = f"ce-{item['key']}-{entry['pop']}-{ordinal:03}"
                if sid in demand:
                    raise ValueError("Composed customer site identities collide; choose unambiguous customer/PoP keys.")
                demand[sid] = (item, entry["pop"], ordinal)
        if service == "private-l3":
            if (not isinstance(item.get("hub_pop"), str) or item["hub_pop"] not in entries or
                    not _integer(item.get("site_peak_mbps"), 1, 800) or not _integer(item.get("lan_endpoints"), 0, 12) or
                    type(item.get("hub_commit_mbps")) is not int or item["hub_commit_mbps"] not in tiers or
                    not isinstance(item.get("branch"), str) or not re.fullmatch(r"[a-z][a-z ]{1,23}", item["branch"])):
                raise ValueError("A private-L3 customer needs a hub at one of its PoPs, bounded peaks and LAN demand, "
                                 "a tiered hub commitment and a branch noun.")
            if item.get("status", "active") == "active" and _stage(item, item["hub_pop"]) != "active":
                raise ValueError("An active customer's hub premises stays active; its spokes route through it.")
            spokes = sum(_site_peak(item, e["pop"]) * (e["count"] - (e["pop"] == item["hub_pop"])) for e in item["sites"])
            if spokes > item["hub_commit_mbps"] * usable:
                raise ValueError("Purchased hub commitment cannot cover the declared customer spoke-to-hub peak after reserve.")
            if any(_site_peak(item, e["pop"]) > 1000 * usable for e in item["sites"]):
                raise ValueError("Customer peak cannot fit the physical 1 Gbps handoff after reserve.")
        else:
            rate = item.get("commit_mbps" if service == "dia" else "rate_mbps")
            if type(rate) is not int or rate not in (*tiers, *LARGE_TIERS) or _handoff(rate, usable) is None:
                raise ValueError("A DIA commitment or EPL rate is a tier that fits the 1G or 10G handoff after reserve.")
            if service == "dia" and (type(item.get("managed")) is not bool or
                                     (item["managed"] and _handoff(rate, usable) > 1000)):
                raise ValueError("DIA declares managed or not; managed DIA fits the small CE's 1G handoff.")
    if any(count > ATTACHMENTS_PER_POP for count in occupancy.values()):
        raise ValueError(f"Customer attachments exceed the {ATTACHMENTS_PER_POP} aggregation UNIs of a PoP.")
    dia = [c for c in customers if c["service"] == "dia"]
    if len(dia) > len(DIA_NETWORKS) or sum(c["managed"] for c in dia) > DIA_LINK_POOL.num_addresses // 2:
        raise ValueError("DIA demand exceeds the documentation /29s or the managed-DIA public /31s.")
    return pops, customers, demand, pool, usable


def _ledger(reservations, scope, expected, capacity):
    value = reservations.get(scope, {})
    if (not isinstance(value, dict) or set(value) != set(expected) or
            any(not _integer(slot, 0, capacity - 1) for slot in value.values()) or len(set(value.values())) != len(value)):
        raise ValueError(f"{scope} must contain exactly its required unique identities and bounded slots.")
    return value


def _connected(adjacency, excluded=None, removed=None):
    nodes = set(adjacency) - {removed}
    if not nodes:
        return False
    seen, pending = set(), [min(nodes)]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(peer for _, peer, edge in adjacency[node] if peer != removed and edge != excluded and peer not in seen)
    return seen == nodes


def _tree(adjacency, origin, excluded=None):
    """Shortest-hop predecessors; callers supply canonical edge-key ordering."""
    predecessors, pending = {origin: None}, deque([origin])
    while pending:
        node = pending.popleft()
        for _, peer, edge in adjacency.get(node, ()):
            if edge != excluded and peer not in predecessors:
                predecessors[peer] = (node, edge)
                pending.append(peer)
    return predecessors


def _loads(adjacency, flows, excluded=None):
    """Directed shortest-hop customer flow; stable edge-key ties, one BFS/source."""
    loads = Counter()
    sources = defaultdict(list)
    for (origin, destination), peak in flows.items():
        sources[origin].append((destination, peak))
    for origin, destinations in sources.items():
        predecessors = _tree(adjacency, origin, excluded)
        for destination, peak in destinations:
            if destination not in predecessors:
                return None
            while destination != origin:
                previous, edge = predecessors[destination]
                loads[(edge, previous, destination)] += peak
                destination = previous
    return loads


def _workloads(premises, pops, tms=False):
    """NOC service groups; a DDoS detection controller pair only beside a racked TMS (K7)."""
    result = []
    controller = ((CONTROLLER_POLICY[0], "one", 1, *CONTROLLER_POLICY[1:], 443),) if tms else ()
    for key, measure, threshold, cpus, memory, disk, port in (*SERVICE_POLICY, *controller):
        count = premises if measure == "premises" else pops if measure == "pops" else 1
        listeners = [dict(key="", name=key, protocol="tcp", ports=[port])]
        if key == "identity":
            listeners.append(dict(key="radius", name="radius", protocol="udp", ports=[1812, 1813]))
        elif key == "dns":
            listeners.append(dict(key="udp", name="dns-udp", protocol="udp", ports=[53]))
        result.append(dict(key=key, groups=max(1, (count + threshold - 1) // threshold), replicas=2,
            failure_domain="rack", network="applications", vcpus=cpus, memory_mb=memory, disk_mb=disk,
            listeners=listeners, criticality="tier-2" if key in ("monitoring", CONTROLLER_POLICY[0]) else "tier-1"))
    return result


def premises_description(customer, role, rate_mbps, other=None):
    """Restated site description policy: what a premises contains and how it is served."""
    tier = bandwidth(rate_mbps)
    if customer["service"] == "dia":
        return (f"Managed internet, {tier}; NID and carrier CE in the MPOE cabinet" if customer["managed"] else
                f"Dedicated internet demarcation, {tier}; customer firewall not inventoried")
    if customer["service"] == "epl":
        return f"Ethernet private line end to {other}, {tier}; customer Ethernet equipment not inventoried"
    if role == "hub":
        return f"Dual-homed VPN hub, {tier} commit; two NIDs and the CE in the MPOE cabinet"
    if customer["lan_endpoints"] > 0:
        return f"Managed {customer['branch']}, {tier} tier, with a carrier-managed office LAN"
    return f"Managed {customer['branch']}, {tier} tier; NID and CE in the MPOE cabinet"


def validate(plan, catalog, *, objects, children, peers, component_of,
             component_members, cable_of, path_lengths, poe_watts=None, optics_watts=None):
    recipe, findings = plan.get("recipe", {}), []
    if recipe.get("profile") != "provider-backbone":
        return findings

    def report(code, key, message):
        findings.append(dict(code=code, object=key, message=message))

    # The recipe declares the vendor line; every port name below follows the
    # resolved catalog entry rather than one vendor's naming.
    access_alias = selected_alias(recipe, "access")
    access_spec = catalog.get(access_alias, {})
    access_ports = access_spec.get("access_ports", [])
    access_mgmt = next((p["name"] for p in access_spec.get("interfaces", []) if p.get("mgmt_only")), None)
    agg_spec, mgmt_spec = catalog.get("aggregation", {}), catalog.get("pop-mgmt", {})
    unis, agg_lag_ports = agg_spec.get("uni_ports", []), agg_spec.get("lag_ports", [])
    copper, mgmt_uplinks = mgmt_spec.get("access_ports", []), mgmt_spec.get("uplink_ports", [])
    oob_spec = catalog.get("oob-server", {})
    lte = next((p["name"] for p in oob_spec.get("interfaces", []) if p.get("type") == "lte"), None)
    eth0 = next((p["name"] for p in oob_spec.get("interfaces", []) if p.get("mgmt_only") and p.get("type") != "lte"), None)
    pdu_net = next((p["name"] for p in catalog.get("pdu-switched", {}).get("interfaces", []) if p.get("mgmt_only")), None)
    if (len(access_ports) < 3 or access_mgmt is None or len(unis) != UNIS_PER_SIDE or len(agg_lag_ports) != LAG_MEMBERS or
            len(copper) < 9 or len(mgmt_uplinks) < 2 or None in (lte, eth0, pdu_net) or
            any(not catalog.get(a, {}).get(f) for a in ("nid", "nid-10g") for f in ("nni_port", "uni_port")) or
            any(not catalog.get(a, {}).get("wan_ports") for a in ("ce-small", "edge"))):
        report("provider-access-catalog", "catalog", "The selected access line, aggregation switch, PoP management "
               "switch, console server, PDU, NIDs and CEs must supply their ordered service, LAG and management ports.")
        return findings

    try:
        pops, customers, premises, pool, usable = _recipe(recipe)
    except (ValueError, TypeError, OverflowError) as exc:
        report("provider-recipe", "plan", str(exc))
        return findings

    if poe_watts is None:
        _, poe_watts = analyze_poe(plan, catalog)
    if optics_watts is None:
        _, optics_watts = analyze_optics(plan, catalog)

    def attrs(key):
        return objects.get(key, {}).get("attrs", {}) if isinstance(key, str) else {}

    def refs(key):
        return objects.get(key, {}).get("refs", {}) if isinstance(key, str) else {}

    def meta(key):
        return objects.get(key, {}).get("meta", {}) if isinstance(key, str) else {}

    def kind(key):
        return objects.get(key, {}).get("kind") if isinstance(key, str) else None

    def child(field, key, wanted):
        return [item for item in children[(field, key)] if kind(item) == wanted] if isinstance(key, str) else []

    # Premises lifecycle stage of the records being checked ("active" elsewhere).
    now = ["active"]

    def life(field):
        return LIFE[now[0]][field]

    by_kind = defaultdict(set)
    ipv4_addresses = {}
    ips_by_vrf_address = defaultdict(set)
    prefixes_by_vrf_network = defaultdict(list)
    for key, obj in objects.items():
        by_kind[obj["kind"]].add(key)
        if obj["kind"] == "ip_address" and isinstance(attrs(key).get("address"), str):
            try:
                value = ip_interface(attrs(key)["address"])
                if value.version == 4:
                    ipv4_addresses[key] = value
                if isinstance(refs(key).get("vrf"), (str, type(None))):
                    ips_by_vrf_address[(refs(key).get("vrf"), value.version, int(value.ip))].add(key)
            except ValueError:
                pass  # The shared format check reports malformed addresses.
        if obj["kind"] == "prefix" and isinstance(refs(key).get("vrf"), (str, type(None))) and isinstance(attrs(key).get("prefix"), str):
            prefixes_by_vrf_network[(refs(key).get("vrf"), attrs(key)["prefix"])].append(key)

    # --- Passive plant: 1:1 panel mappings and the far end a cable end reaches ---
    # ``peers`` resolves a port's terminal peer through passive paths; these
    # checks need the immediate cable end as well.
    adjacent = {}
    for cable in by_kind["cable"]:
        a, b = refs(cable).get("a"), refs(cable).get("b")
        if isinstance(a, str) and isinstance(b, str):
            adjacent[a], adjacent[b] = b, a
    rear_of, front_of = {}, {}
    for front in by_kind["front_port"]:
        rear = refs(front).get("rear_port")
        if kind(rear) == "rear_port":
            rear_of[front] = rear
            front_of.setdefault(rear, front)

    def walk(end):
        """(far end, [(panel port reached, its mapped mate)]) from one cable end through 1:1 panels."""
        route, seen, current = [], set(), end
        while True:
            other = adjacent.get(current)
            if other is None:
                return None, route
            if kind(other) not in ("front_port", "rear_port"):
                return other, route
            mate = rear_of.get(other) if kind(other) == "front_port" else front_of.get(other)
            route.append((other, mate))
            if mate is None or mate in seen:
                return None, route
            seen.add(mate)
            current = mate

    def path_cables(port):
        members = component_members.get(component_of.get(port), ())
        return {cable_of[p] for p in members if p in cable_of}

    def path_ok(port):
        """An in-service path; at a non-active premises, the path in its own lifecycle status."""
        cables = path_cables(port)
        members = component_members.get(component_of.get(port), ())
        devices = {refs(p).get("device") for p in members} - {None}
        # The serving PoP equipment stays active while a premises it serves is not.
        return (bool(cables) and all(attrs(c).get("status") == life("cable") for c in cables) and
                all(attrs(d).get("status") in {"active", life("device")} for d in devices) and
                (now[0] != "active" or all(attrs(d).get("status") == "active" for d in devices)))

    def vlans(port):
        values = refs(port).get("tagged_vlans", [])
        return ({value for value in values if isinstance(value, str)} if isinstance(values, list) else set()) | (
            {refs(port)["untagged_vlan"]} if isinstance(refs(port).get("untagged_vlan"), str) else set())

    def l3(port):
        """Where a port's addresses live: a PoP Junos device's physical port is
        addressed on its logical unit 0 (<port>.0, a virtual child carrying the
        routing context), never bare; every other port carries its own."""
        device = refs(port).get("device")
        if (refs(device).get("platform") != "platform/juniper-junos" or not str(refs(device).get("site")).startswith("site/pop-")
                or attrs(port).get("type") in (None, "virtual", "lag", "bridge")):
            return port
        units = [u for u in child("parent", port, "interface") if attrs(u).get("name") == f"{attrs(port).get('name')}.0"]
        unit = units[0] if len(units) == 1 else None
        if (unit is None or attrs(unit).get("type") != "virtual" or refs(unit).get("device") != device or
                attrs(unit).get("enabled") is not attrs(port).get("enabled") or refs(port).get("vrf") or
                child("assigned_object", port, "ip_address")):
            return None
        return unit

    def address(port, network, vrf, host=None, tenant=None):
        # This policy owns the exact IPv4 /31, /32 and site subnets; required
        # IPv6 companions are checked separately, not counted as extras.
        port = l3(port)
        ips = [key for key in child("assigned_object", port, "ip_address") if key in ipv4_addresses]
        if len(ips) != 1:
            return False
        key = ips[0]
        ip = ipv4_addresses[key]
        good = ip.network == network and (host is None or int(ip.ip) == int(network.network_address) + host)
        return (good and attrs(key).get("status") == life("ip") and refs(key).get("vrf") == vrf and
                refs(port).get("vrf") == vrf and (tenant is None or refs(key).get("tenant") == tenant))

    def primary(device, port):
        key = refs(device).get("primary_ip4")
        return key in ipv4_addresses and refs(key).get("assigned_object") == l3(port) and attrs(key).get("status") == life("ip")

    def cabled(a, b, speed=None):
        """A direct one-cable link between two physical interfaces."""
        return (kind(a) == kind(b) == "interface" and adjacent.get(a) == b and path_ok(a) and
                all(attrs(p).get("type") not in (None, "virtual", "lag") for p in (a, b)) and
                (speed is None or all(attrs(p).get("speed") == speed for p in (a, b))))

    # --- Sites, ledgers and allocations ---
    targets = {}  # attachment target -> (premises sid, NID ordinal)
    for sid, (customer, pop, ordinal) in premises.items():
        hub = customer["service"] == "private-l3" and pop == customer["hub_pop"] and ordinal == 1
        targets[sid] = (sid, 1)
        if hub:
            targets[f"{sid}/b"] = (sid, 2)
    expected_sites = {"site/dc-01"} | {f"site/pop-{key}" for key in pops} | {f"site/{sid}" for sid in premises}
    if by_kind["site"] != expected_sites:
        report("provider-site-inventory", "plan", "Actual PoP, customer and NOC sites must exactly match requested demand.")
    reservations, allocations = plan.get("reservations"), plan.get("allocations")
    try:
        if not isinstance(reservations, dict) or not isinstance(allocations, dict):
            raise ValueError("Provider allocations and reservations must be objects.")
        order = _ledger(reservations, "provider-pop-order", pops, 64)
        if set(order.values()) != set(range(len(pops))):
            raise ValueError("PoP order must retain one contiguous permanent ordinal per requested PoP.")
        tl = _timeline(recipe, reservations, pops, order, premises)
        customer_slots = _ledger(reservations, "provider-customers", [c["key"] for c in customers], 256)
        if set(allocations) != {site.removeprefix("site/") for site in expected_sites} or allocations.get("dc-01") != 0:
            raise ValueError("All requested sites need exactly one reservation, with NOC dc-01 fixed at /24-unit zero.")
        small_slots = [slot for sid, slot in allocations.items() if sid != "dc-01"]
        if any(not _integer(slot, 256, pool.num_addresses // 256 - 257) for slot in small_slots) or len(set(small_slots)) != len(small_slots):
            raise ValueError("PoP/customer /24s must be distinct and avoid the complete NOC and infrastructure /16s.")
        span_ledger = reservations.get("provider-backbone-spans")
        if (not isinstance(span_ledger, dict) or not span_ledger or
                sorted(span_ledger.values()) != list(range(len(span_ledger)))):
            raise ValueError("provider-backbone-spans must be a dense permanent sequence of span identities.")
        spans = {}
        for key in sorted(span_ledger, key=span_ledger.get):
            match = SPAN_KEY.fullmatch(key) if isinstance(key, str) else None
            if not match or match[1] not in pops or match[3] not in pops or match[1] == match[3]:
                raise ValueError(f"{key!r} must join PEs at two different requested PoPs.")
            spans[key] = (f"device/pop-{match[1]}/pe-{match[2]}", f"device/pop-{match[3]}/pe-{match[4]}")
        launch = _ledger(reservations, "provider-pop-launch", pops, 64)
        if set(launch.values()) != set(range(len(pops))):
            raise ValueError("PoP launch order must retain one contiguous permanent ordinal per requested PoP.")
        for retired in ("provider-span-upgrades", "provider-service-ports"):
            if any(scope == retired or scope.startswith(retired + "/") for scope in reservations):
                raise ValueError(f"The retired {retired} ledger must be absent: access lands on the aggregation layer.")
        routers = {f"device/pop-{pop}/pe-{side}" for pop in pops for side in ("a", "b")}
        expected_transport = defaultdict(set)
        for circuit, ends in spans.items():
            for router in ends:
                expected_transport[router].add(circuit)
        transport_ports = {router: _ledger(reservations, f"provider-transport-ports/{router}", expected_transport[router], 2)
                           for router in routers}
        # Access: the alternating home-side ledger and the per-side UNI ledger.
        at_pop = defaultdict(list)
        for target, (sid, _) in targets.items():
            at_pop[premises[sid][1]].append(target)
        homes = {pop: _ledger(reservations, f"provider-agg-home/{pop}", at_pop[pop], ATTACHMENTS_PER_POP) for pop in pops}
        side_of = {target: "ab"[homes[premises[sid][1]][target] % 2] for target, (sid, _) in targets.items()}
        uni_slots = {}
        for pop in pops:
            for side in ("a", "b"):
                mine = [t for t in at_pop[pop] if side_of[t] == side]
                if mine or f"provider-agg-uni/{pop}/{side}" in reservations:
                    uni_slots.update(_ledger(reservations, f"provider-agg-uni/{pop}/{side}", mine, UNIS_PER_SIDE))
        vid_slots = {}
        for pop in pops:
            if at_pop[pop] or f"provider-service-vlans/{pop}" in reservations:
                vid_slots.update(_ledger(reservations, f"provider-service-vlans/{pop}", at_pop[pop], 4001 - SERVICE_VLAN_BASE))
        nid_slots = _ledger(reservations, "provider-nid-management", pops, 64)
        nid_device = {target: f"device/{sid}/nid-{n:02}" for target, (sid, n) in targets.items()}
        nid_hosts = {}
        for pop in pops:
            for side in ("a", "b"):
                mine = [nid_device[t] for t in at_pop[pop] if side_of[t] == side]
                if mine or f"provider-nid-hosts/{pop}/{side}" in reservations:
                    nid_hosts.update(_ledger(reservations, f"provider-nid-hosts/{pop}/{side}", mine, 125))
        dia = [sid for sid, (c, _, _) in premises.items() if c["service"] == "dia"]
        dia_slots = _ledger(reservations, "provider-dia-29", dia, len(DIA_NETWORKS)) if dia or "provider-dia-29" in reservations else {}
        managed = [sid for sid in dia if premises[sid][0]["managed"]]
        dia_links = (_ledger(reservations, "provider-dia-31", managed, DIA_LINK_POOL.num_addresses // 2)
                     if managed or "provider-dia-31" in reservations else {})
        epl_keys = [c["key"] for c in customers if c["service"] == "epl"]
        vcids = _ledger(reservations, "provider-epl-vcid", epl_keys, 1 << 20) if epl_keys or "provider-epl-vcid" in reservations else {}
        private = {f"management/pop-{pop}/{s}" for pop in pops for s in ("a", "b")} | {f"circuit/noc/{s}" for s in ("a", "b")}
        private |= {f"circuit/customer/{t}" for t, (sid, _) in targets.items() if premises[sid][0]["service"] == "private-l3"}
        link_slots = _ledger(reservations, "provider-link-prefixes", private, 16384)
        lan_slots = {c["key"]: _ledger(reservations, f"provider-customer-lans/{c['key']}",
                                       [sid for sid, (item, _, _) in premises.items() if item["key"] == c["key"]], 255)
                     for c in customers if c["service"] == "private-l3" and not c["lan_endpoints"]}
        if any(scope.startswith("provider-customer-lans/") and scope.removeprefix("provider-customer-lans/") not in lan_slots
               for scope in reservations):
            raise ValueError("Only CE-only private-L3 customers number premises LANs from their own plan.")
        public = {"pair": ("provider-pair-links", {f"pair/pop-{pop}" for pop in pops}),
                  "backbone": ("provider-span-links", set(spans)),
                  "transit": ("provider-transit-links", {f"circuit/transit/{s}" for s in ("a", "b")})}
        public_slots = {family: _ledger(reservations, scope, keys, 2 if family == "transit" else PUBLIC_POOLS[family].num_addresses // 2)
                        for family, (scope, keys) in public.items()}
        loop_slots = _ledger(reservations, "provider-loopbacks", routers, LOOPBACK_SLOTS)
        oob_slots = _ledger(reservations, "provider-oob-links", pops, OOB_POOL.num_addresses // 4)
    except ValueError as exc:
        report("provider-allocation", "plan", str(exc))
        return findings

    ordered = sorted(pops, key=order.get)
    pop_of = lambda router: router.split("/")[1].removeprefix("pop-")
    metro_of = lambda router: pops[pop_of(router)]["metro"]
    infra = int(pool.broadcast_address) - 65535
    link_network = {key: ip_network((infra + 2 * slot, 31)) for key, slot in link_slots.items()}
    for family, slots in public_slots.items():
        if family == "transit":
            # Each upstream numbers its /31 from its own assignment.
            link_network.update({key: ip_network((int(UPSTREAM_POOLS[key[-1]].network_address), 31)) for key in slots})
            continue
        base = int(PUBLIC_POOLS[family].network_address)
        link_network.update({key: ip_network((base + 2 * slot, 31)) for key, slot in slots.items()})

    def routed(link, endpoints, vrf, tenant="tenant", hosts=(0, 1)):
        network = link_network[link]
        prefix = f"prefix/link/{link}"
        good = (kind(prefix) == "prefix" and attrs(prefix).get("prefix") == str(network) and
                attrs(prefix).get("status") == life("prefix") and refs(prefix).get("vrf") == vrf and refs(prefix).get("tenant") == tenant)
        for host, port in zip(hosts, endpoints):
            good &= address(port, network, vrf, host, tenant)
        actual = ips_by_vrf_address[(vrf, 4, int(network[0]))] | ips_by_vrf_address[(vrf, 4, int(network[1]))]
        expected = {key for port in endpoints for key in child("assigned_object", l3(port), "ip_address") if key in ipv4_addresses}
        if not good or actual != expected:
            report("provider-routed-address", link, "Routed prefix, exact local endpoint ownership, /31 masks and VRF must match "
                   "the reserved real link; opaque transit has one local owner.")

    # Carrier policy, restated: same-metro spans are owned dark fiber; spans
    # between metros alternate the two transport carriers in ledger order.
    span_provider, alternation = {}, Counter()
    for key, (a, b) in spans.items():
        if metro_of(a) == metro_of(b):
            span_provider[key] = "provider/operator"
        else:
            pair = frozenset((metro_of(a), metro_of(b)))
            span_provider[key] = f"provider/transport-{'ab'[alternation[pair] % 2]}"
            alternation[pair] += 1
    chain = sorted({item["metro"] for item in pops.values()}, key=lambda m: METRO_POINTS[m][1])
    neighbours = {frozenset(pair) for pair in zip(chain, chain[1:])}
    for key, (a, b) in spans.items():
        if metro_of(a) != metro_of(b) and frozenset((metro_of(a), metro_of(b))) not in neighbours:
            report("provider-backbone-geography", key, "An inter-metro span must join neighbouring metros; it may not skip a metro or cross a lake.")
    for pair in neighbours:
        between = [key for key, (a, b) in spans.items() if frozenset((metro_of(a), metro_of(b))) == pair]
        sides = {metro: {end for key in between for end in spans[key] if metro_of(end) == metro} for metro in pair}
        if (len(between) < 2 or {refs(k).get("provider") for k in between} != {"provider/transport-a", "provider/transport-b"} or
                any(len(ends) < 2 for ends in sides.values())):
            report("provider-backbone-diversity", "plan", f"Metros {' and '.join(sorted(pair))} need two spans from two "
                   "different carriers, landing on two different PEs at each end.")

    # --- Circuit handoffs: site scope, panel landing and the carrier hotel ---
    cross_connects, mmr_positions = Counter(), Counter()
    landed = defaultdict(set)  # panel device -> owning party of the circuits it lands

    def land(term, port, site, provider):
        """A PoP or NOC handoff lands through the right panel in the port's own
        cabinet and reaches ``port``; a premises handoff is one direct cable."""
        far, route = walk(term)
        device = refs(port).get("device")
        carrier = provider != "provider/operator"
        if str(site).startswith("site/pop-"):
            if len(route) != 1 or far != port:
                return False
            rear, front = route[0]
            panel = refs(rear).get("device")
            landed[panel].add("carrier" if carrier else "operator")
            wanted = "hardware/demarc-panel" if carrier else "hardware/osp-panel"
            position = attrs(rear).get("name")
            ledger = reservations.get(f"fibre-panel-positions/{panel}", {})
            return (kind(rear) == "rear_port" and kind(front) == "front_port" and refs(panel).get("device_type") == wanted and
                    refs(panel).get("rack") == refs(device).get("rack") and refs(panel).get("site") == site and
                    attrs(front).get("name") == position and position == f"Port {ledger.get(term, -2) + 1}")
        if site == "site/dc-01":
            if len(route) != 1 or far != port:
                return False
            rear, _ = route[0]
            panel = refs(rear).get("device")
            return refs(panel).get("device_type") == "hardware/patch-panel" and refs(panel).get("rack") == refs(device).get("rack")
        return not route and far == port

    def cross_connect(term, port, site, provider):
        """A carrier's handoff into a PoP records the hotel cross-connect (the cable from the
        colo panel carries it as its label) and, for fibre, its meet-me-room panel position."""
        carrier_pop = port is not None and provider != "provider/operator" and str(site).startswith("site/pop-")
        xc, pp = attrs(term).get("xconnect_id"), attrs(term).get("pp_info")
        fibre = carrier_pop and attrs(port).get("type") != "1000base-t"
        match = MMR_PP.fullmatch(str(pp)) if pp is not None else None
        if carrier_pop:
            cross_connects[xc] += 1
            if match:
                mmr_positions[(site, match[1], match[2])] += 1
        if ((xc is not None) != carrier_pop or (carrier_pop and not re.fullmatch(r"XC-[1-9]\d{6}", str(xc))) or
                (carrier_pop and attrs(cable_of.get(term)).get("label") != xc) or
                (pp is not None) != bool(fibre) or (pp is not None and not match)):
            report("provider-cross-connect", term, "A carrier's handoff into a PoP records its carrier-hotel cross-connect, "
                   "labelled on the cross-connect cable, and a fibre handoff its meet-me-room panel position; no other "
                   "termination carries either.")

    def circuit(key, port_a, port_z, site_a, site_z, provider, speed, commitment, tenant="tenant", account=None,
                circuit_type=None, texts=None):
        terms = child("circuit", key, "circuit_termination")
        sides = {side: [term for term in terms if attrs(term).get("term_side") == side] for side in ("A", "Z")}
        good = (kind(key) == "circuit" and attrs(key).get("status") == life("circuit") and
                attrs(key).get("commit_rate") == commitment and refs(key).get("provider") == provider and
                refs(key).get("tenant") == tenant and len(terms) == 2 and all(len(value) == 1 for value in sides.values()) and
                (circuit_type is None or refs(key).get("type") == circuit_type))
        if account is not None:
            good &= refs(key).get("provider_account") == account and refs(account).get("provider") == provider
        for side, port, site in (("A", port_a, site_a), ("Z", port_z, site_z)):
            term = sides[side][0] if len(sides[side]) == 1 else None
            if port is not None:
                # A local handoff terminates on its site (Visual Explorer maps
                # only site-scoped ends), at its handoff speed, never marked
                # connected, reaches its port through the right panel and
                # names the room its equipment stands in.
                room = attrs(refs(refs(port).get("device")).get("location")).get("name")
                text = (texts or {}).get(side, f"Local routed handoff, {room}")
                good &= (refs(term).get("termination") == site and kind(site) == "site" and
                         attrs(term).get("description") == text and
                         attrs(term).get("port_speed") == speed and attrs(term).get("mark_connected") is not True and
                         kind(port) == "interface" and attrs(port).get("type") not in ("virtual", "lag", None) and
                         attrs(port).get("speed") == speed and land(term, port, site, provider) and path_ok(term))
                cross_connect(term, port, site, provider)
            else:
                good &= (refs(term).get("termination") == site and kind(site) == "provider_network" and
                         refs(site).get("provider") == provider and not adjacent.get(term))
                cross_connect(term, None, site, provider)
        if not good:
            report("provider-circuit-path", key, "Circuit needs its purchased commitment, provider/account/tenant/type, "
                   "site-scoped local ends and complete physical handoffs through the right panel; only transit "
                   "and the cellular service have an opaque remote end.")
        return bool(good)

    # --- Facilities ---
    site_metros = {f"pop-{pop}": item["metro"] for pop, item in pops.items()}
    site_metros.update({sid: pops[pop]["metro"] for sid, (_, pop, _) in premises.items()})
    site_metros["dc-01"] = pops[recipe["noc_pop_a"]]["metro"]

    def rate_of(customer, pop, hub):
        if customer["service"] == "private-l3":
            return customer["hub_commit_mbps"] if hub else next(
                t for t in recipe["wan_tiers_mbps"] if Decimal(t) * usable >= _site_peak(customer, pop))
        return customer["commit_mbps"] if customer["service"] == "dia" else customer["rate_mbps"]

    descriptions = defaultdict(set)
    for site in sorted(expected_sites):
        sid = site.removeprefix("site/")
        customer = premises[sid][0] if sid in premises else None
        now[0] = _stage(customer, premises[sid][1]) if customer else "active"
        tenant = f"tenant/cust-{customer['key']}" if customer else "tenant"
        category = "customer" if customer else "dc" if sid == "dc-01" else "pop"
        city, state_code, state, zone = METROS[site_metros[sid]]
        region = f"region/{recipe['namespace']}/us/{state_code.lower()}/{city.lower()}"
        group = f"site-group/{recipe['namespace']}/{category}"
        address_lines = str(attrs(site).get("physical_address")).split("\n")
        locality = address_lines[1].partition(", ")[0] if len(address_lines) == 3 else None
        locality = locality if LOCALITIES.get(locality) == city else city
        number, _, street = address_lines[0].partition(" ")
        direction, _, rest = street.partition(" ")
        streets = {name for (place, _), names in ADDRESS_STREETS.items() if place == locality for name in names}
        if direction in {"North", "South", "East", "West"} and (locality == "Chicago" or locality in MILWAUKEE_COUNTY):
            street = next((name for name in streets if name.partition(" ")[2] == rest), street)
        good_address = (len(address_lines) == 3 and number.isdecimal() and int(number) > 0 and street in streets and
                        address_lines[1:] == [f"{locality}, {state}", "United States"])
        if customer:
            pop, ordinal = premises[sid][1], premises[sid][2]
            hub = customer["service"] == "private-l3" and pop == customer["hub_pop"] and ordinal == 1
            other = next((titleize(e["pop"]) for e in customer["sites"] if e["pop"] != pop), None)
            description = premises_description(customer, "hub" if hub else "spoke", rate_of(customer, pop, hub), other)
            descriptions[customer["key"]].add(attrs(site).get("description"))
        else:
            description = {"pop": "Provider routing, local management and carrier handoffs",
                           "dc": "Provider NOC services, inventory and monitoring"}[category]
        if attrs(site).get("description") != description:
            report("provider-scope-text", site, "Facility description must state what it contains and how it is served, "
                   "without unmodeled availability or execution guarantees.")
        if (kind(site) != "site" or attrs(site).get("status") != life("site") or refs(site).get("tenant") != tenant or
                refs(site).get("region") != region or refs(site).get("group") != group or attrs(site).get("time_zone") != zone or
                not good_address or kind(region) != "region" or kind(group) != "site_group" or
                refs(region).get("parent") != f"region/{recipe['namespace']}/us/{state_code.lower()}"):
            report("provider-site-context", site, "Site ownership, functional group, address, metro/state and time zone must match the actual requested facility.")
        room = f"location/{sid}"
        required_rooms = {room: ("equipment_room", 1, [24, 18, 0], f"{room}/suite" if category == "pop" else None)}
        if category == "pop":
            required_rooms[f"{room}/suite"] = ("suite", 1, [0, 0, 0], None)
            # A core PoP is a cage; an edge PoP's room is its one leased cabinet,
            # named by that cabinet's facility code (K3).
            suite, cage = carrier_suite(sid)
            core = tl["tier"][sid.removeprefix("pop-")] == "core"
            space = (cage, CAGE_CONTRACT) if core else (f"Cabinet {cage.removeprefix('Cage ')}-01", EDGE_CABINET)
            if ([attrs(f"{room}/suite").get("name"), attrs(room).get("name"), attrs(room).get("description")] != [suite, *space]
                    or meta(room).get("cabinet_positions") != (4 if core else None)):
                report("provider-room-geometry", room, "A core PoP's cage states its cabinet contract and an edge PoP's room is "
                       "its one leased cabinet, inside their authored carrier-hotel suite.")
        elif category == "customer" and attrs(room).get("name") != "MPOE":
            report("provider-room-geometry", room, "A premises' carrier equipment stands in its MPOE room.")
        if customer and customer["service"] == "private-l3" and customer["lan_endpoints"]:
            required_rooms[f"{room}/office-01"] = ("office", 1, [8, 18, 0], None)
        if category != "dc" and set(child("site", site, "location")) != set(required_rooms):
            report("provider-room-inventory", site, "Each bounded facility needs its real equipment room (a PoP's cage inside "
                   "its suite) and requested customer office.")
        for key, (function, floor, point, parent) in required_rooms.items():
            if category == "dc":
                break
            metadata = meta(key)
            if (kind(key) != "location" or attrs(key).get("status") != life("site") or refs(key).get("site") != site or
                    refs(key).get("tenant") != tenant or refs(key).get("parent") != parent or
                    metadata.get("space_type") != function or metadata.get("floor") != floor or metadata.get("position_m") != point or
                    (function == "office" and metadata.get("capacity") != {"workstations": 12})):
                report("provider-room-geometry", key, "Facility rooms must retain their fixed local geometry, capacity, ownership and containment.")
    now[0] = "active"
    # Descriptions vary with what a premises is: a multi-site customer's hub
    # reads differently from its branches, and an EPL's two ends name each other.
    for customer in customers:
        sites = [sid for sid, (c, _, _) in premises.items() if c["key"] == customer["key"]]
        if len(sites) >= 2 and len(descriptions[customer["key"]]) < 2:
            report("provider-scope-text", f"tenant/cust-{customer['key']}", "A multi-site customer's premises descriptions "
                   "distinguish its hub, branches or line ends.")

    # --- PoP plant: stratigraphy, cage or cabinet, panels and history (v0.18) ---
    # Every unit is re-derived from the frozen timeline: each cabinet holds its
    # install ledger top-down, a removed device's units stay a blanked gap with a
    # dated rack journal, and nothing out of service is cabled or powered.
    infrastructure, plant = [], {}
    cables_on = defaultdict(set)  # device -> cables on any of its components
    for cable in by_kind["cable"]:
        for end in (refs(cable).get("a"), refs(cable).get("b")):
            owner = refs(end).get("device")
            if isinstance(owner, str):
                cables_on[owner].add(cable)
    for pop in ordered:
        sid, site, room = f"pop-{pop}", f"site/pop-{pop}", f"location/pop-{pop}"
        city = METROS[pops[pop]["metro"]][0]
        core = tl["tier"][pop] == "core"
        cage = carrier_suite(sid)[1].removeprefix("Cage ")
        facility = str(attrs(site).get("facility", "")).lower()
        history = _history(tl, pop)
        names = ("R01", "R02") if core else ("R01",)
        racks = {name: f"rack/{sid}/{name.lower()}" for name in names}
        if set(child("site", site, "rack")) != set(racks.values()) or by_kind["rack_reservation"]:
            report("provider-rack-geometry", site, "A core PoP's cage holds its two installed cabinets R01/R02 and an edge "
                   "PoP its one cabinet; contracted positions are stated on the cage, never modeled as racks or reservations.")
        expected = {}  # device -> (alias, rack, position, status, item)
        for bay, (name, rack) in enumerate(racks.items()):
            if (kind(rack) != "rack" or attrs(rack).get("name") != name or attrs(rack).get("u_height") != CABINET_U or
                    attrs(rack).get("status") != "active" or refs(rack).get("location") != room or
                    refs(rack).get("rack_type") != "rack-type/42u" or attrs(rack).get("facility_id") != f"{cage}-{bay + 1:02}" or
                    meta(rack).get("position_m") != [round(1.0 + 0.6 * bay, 1), 1.0, 0]):
                report("provider-rack-geometry", rack, "Each installed 42U AR3100 cabinet keeps its name, facility code and "
                       "position at the cabinet pitch.")
            mounts = _mounts([item for r, item in history if r == name])
            ledger = reservations.get(f"provider-cabinet-u/{rack}", {})
            if not isinstance(ledger, dict) or set(ledger) != {m["label"] for m in mounts}:
                report("provider-rack-geometry", rack, "The cabinet's unit ledger records exactly its install history: every "
                       "device ever racked, removed ones included, and the hygiene rule's cable managers.")
                continue
            height = lambda m: catalog.get(m["alias"], {}).get("u_height", 0) if m["alias"] else 1
            # Top-down in install order: the ledger tiles a contiguous band from
            # the top, each item below the one installed before it, and a
            # manager directly under the item it dresses. Gaps are never reused.
            floor, previous = CABINET_U + 1, None
            for m in sorted(mounts, key=lambda m: -ledger[m["label"]] if _integer(ledger[m["label"]], 1, CABINET_U) else 0):
                position = ledger[m["label"]]
                if (not _integer(position, 1, CABINET_U) or position != floor - height(m) or
                        (previous is not None and m["day"] < previous["day"]) or
                        (m["label"].startswith("cm-under-") and previous is not None and
                         m["label"] != f"cm-under-{previous['label']}" and previous["label"].startswith("cm-under-"))):
                    report("provider-rack-geometry", rack, f"{m['label']} at U{position}: the cabinet fills top-down in install "
                           "order with no unledgered gap; a removed device's units are never reused.")
                    break
                floor, previous = position, m
            occupied = {u for m in mounts if not m["removed"] for u in range(ledger[m["label"]], ledger[m["label"]] + height(m))}
            for m in mounts:
                if m["removed"]:
                    continue
                label = (f"{name.lower()}-cm-{ledger[m['label']]}" if m["label"].startswith("cm-under-") else m["label"])
                expected[f"device/{sid}/{label}"] = (m["alias"], rack, ledger[m["label"]], m["status"], m)
            for position, alias in _blanks(occupied) if occupied else ():
                expected[f"device/{sid}/{name.lower()}-blank-{position}"] = (alias, rack, position, "active", None)
            for feed in ("a", "b"):
                expected[f"device/{sid}/pdu-{name.lower()}-{feed}"] = ("pdu-switched", rack, None, "active", None)
            # Each removed device leaves a dated rack journal naming the change.
            for m in mounts:
                journal = f"journal/{rack}/removed/{m['label']}"
                if m["removed"] and (kind(journal) != "journal_entry" or refs(journal).get("assigned_object") != rack or
                                     not str(attrs(journal).get("comments", "")).startswith(f"**Removed** · {m['removed']}\n\nRemoved: ") or
                                     not re.search(r", CHG\d{7}\. ", str(attrs(journal).get("comments", ""))) or
                                     not str(attrs(journal).get("created", "")).startswith(m["removed"].isoformat())):
                    report("provider-rack-journal", rack, f"The gap left by {m['label']} needs its dated removal journal "
                           "naming the predecessor and change ticket.")
            removals = {k for k in child("assigned_object", rack, "journal_entry") if "/removed/" in k}
            if removals != {f"journal/{rack}/removed/{m['label']}" for m in mounts if m["removed"]}:
                report("provider-rack-journal", rack, "Removal journals exist exactly for the devices removed from this cabinet.")
        colo = f"tenant/colo/{city.lower()}"
        for device, (alias, rack, position, status, item) in expected.items():
            rack_name = attrs(rack).get("name", "")
            legacy = bool(item) and alias not in PASSIVE_NAMES and item["day"] < NS2_ADOPTED
            name = (PASSIVE_NAMES[alias].format(rack=rack_name, u=position) if alias in PASSIVE_NAMES else
                    f"{rack_name} PDU-{device[-1].upper()}" if alias == "pdu-switched" else
                    f"{facility}-{LEGACY_ABBREVIATIONS[PLANT_ROLES[alias]]}{2 if item['label'].endswith('-b') else 1}"
                    if legacy else None)
            if (kind(device) != "device" or attrs(device).get("status") != status or refs(device).get("site") != site or
                    refs(device).get("tenant") != (colo if alias == "demarc-panel" else "tenant") or
                    refs(device).get("role") != f"role/{PLANT_ROLES[alias]}" or refs(device).get("device_type") != f"hardware/{alias}" or
                    refs(device).get("location") != room or refs(device).get("rack") != rack or
                    attrs(device).get("position") != position or (position is not None and attrs(device).get("face") != "front") or
                    (name is not None and attrs(device).get("name") != name) or
                    ("tag/legacy-naming" in (refs(device).get("tags") or [])) is not legacy):
                report("provider-device-inventory", device, "Each PoP device holds its exact hardware, role, status, tenant, "
                       "cabinet and unit from the install history; a pre-NS-2 survivor keeps its legacy name and tag.")
            # Out of service: a relic or cold spare has no cable at all; a
            # planned or staged chassis only planned pre-cabling; none is powered.
            if status != "active":
                on = cables_on[device]
                if (any(kind(refs(c).get(s)) in ("power_port", "power_outlet") for c in on for s in ("a", "b")) or
                        (status in ("decommissioning", "inventory") and on) or
                        any(attrs(c).get("status") != "planned" for c in on)):
                    report("provider-out-of-service", device, "A device not in service draws no power; a relic or spare is "
                           "uncabled, and a planned or staged chassis carries only planned cables.")
                if status != "staged" and any(attrs(p).get("enabled") is not False for p in child("device", device, "interface")
                                              if attrs(p).get("type") != "virtual"):
                    report("provider-out-of-service", device, "A decommissioning, planned or spare chassis keeps every port shut.")
            elif alias not in PASSIVE_NAMES and child("device", device, "interface") and not any(
                    attrs(c).get("status") == "connected" and kind(refs(c).get(s)) == "interface" and refs(refs(c).get(s)).get("device") == device
                    for c in cables_on[device] for s in ("a", "b")):
                report("provider-out-of-service", device, "An active device with interfaces has a cabled data or management port.")
            if status == "active" and alias in ("aggregation", "aggregation-legacy", "pop-mgmt", "oob-server", "time-server"):
                infrastructure.append(device)
        if set(child("site", site, "device")) != set(expected):
            report("provider-device-inventory", site, "The PoP inventory is exactly its install history: panels, PEs and their "
                   "relic or successor, aggregation, management, console, time and DDoS equipment, the cold spare, cable "
                   "managers, blanking in the gaps and the PDUs.")
        plant[pop] = expected
        # Fill: per-cabinet rack-unit utilisation, recomputed from each
        # positioned device's type height whatever its status (blanking excluded).
        for name, rack in racks.items():
            want = sum(catalog.get(alias, {}).get("u_height", 0) for alias, r, position, _, _ in expected.values()
                       if r == rack and position is not None and not catalog.get(alias, {}).get("exclude_from_utilization"))
            have = sum(catalog.get(str(refs(d).get("device_type")).removeprefix("hardware/"), {}).get("u_height", 0)
                       for d in child("rack", rack, "device") if attrs(d).get("position") is not None and
                       not catalog.get(str(refs(d).get("device_type")).removeprefix("hardware/"), {}).get("exclude_from_utilization"))
            if have != want:
                report("provider-rack-geometry", rack, f"Cabinet utilisation is the install history's {want}U, recomputed from racked devices.")
        # Panels: every front position maps 1:1 onto the same rear position.
        for panel, (count, front_type) in ((f"device/{sid}/{name.lower()}-{kind_}", spec) for name in names for kind_, spec in
                                           (("osp", (48, "lc")), ("demarc", (24, "lc")))):
            fronts = child("device", panel, "front_port")
            rears = {attrs(r).get("name"): r for r in child("device", panel, "rear_port")}
            if (len(fronts) != count or len(rears) != count or
                    any(rear_of.get(f) != rears.get(attrs(f).get("name")) or attrs(f).get("rear_port_position") != 1 or
                        attrs(f).get("type") != front_type for f in fronts)):
                report("provider-panel-mapping", panel, "Each panel front position maps one-to-one onto the same-numbered rear position.")
            # TIA-606-style front labels: <facility_id>.<U>:<port>.
            unit, fid = attrs(panel).get("position"), attrs(refs(panel).get("rack")).get("facility_id")
            if any(attrs(f).get("label") != f"{fid}.{unit}:{int(str(attrs(f).get('name')).removeprefix('Port ') or 0):02}"
                   for f in fronts if str(attrs(f).get("name", "")).removeprefix("Port ").isdecimal()):
                report("provider-panel-mapping", panel, "Panel front ports carry their <facility>.<U>:<port> labels.")
        # Console paths: each managed chassis' RJ-45 console reaches the console server.
        console = f"device/{sid}/console-01"
        for device in (f"device/{sid}/pe-a", f"device/{sid}/pe-b", f"device/{sid}/agg-a", f"device/{sid}/agg-b", f"device/{sid}/mgmt-01"):
            ports = [p for p in child("device", device, "console_port") if attrs(p).get("type") == "rj-45"]
            if len(ports) != 1 or refs(adjacent.get(ports[0])).get("device") != console or kind(adjacent.get(ports[0])) != "console_server_port":
                report("provider-console-path", device, "Every PE, aggregation and management switch needs its own serial console path to the PoP console server.")

    # --- PEs: ports, loopback, LAG, power ---
    nid_vlan = {(pop, side): f"vlan/pop-{pop}/{side}/nid-management" for pop in pops for side in ("a", "b")}
    homed_vlans = defaultdict(set)  # (pop, side) -> service VLANs homed on that side
    used_pe_ports = {router: {f"{router}/if/et-0/0/0", f"{router}/if/{PE_MGMT_PORT}", f"{router}/if/fxp0",
                              *(f"{router}/if/{p}" for p in PE_LAG_MEMBERS)} for router in routers}
    for key, ends in spans.items():
        for router in ends:
            used_pe_ports[router].add(f"{router}/if/et-0/0/{1 + transport_ports[router][key]}")
    for side in ("a", "b"):
        used_pe_ports[f"device/pop-{recipe[f'noc_pop_{side}']}/pe-{side}"].add(f"device/pop-{recipe[f'noc_pop_{side}']}/pe-{side}/if/{PE_NOC_PORT}")
    for index, side in enumerate(("a", "b")):
        router = f"device/pop-{ordered[index]}/pe-{side}"
        used_pe_ports[router].add(f"{router}/if/{PE_TRANSIT_PORT}")
    # Exchange ports on PE-A xe-0/1/5: each metro's founding PoP, and the
    # relocated exchange's withdrawn port at its metro's second PoP (cabled,
    # shut, its circuit deprovisioning).
    ix_ports = {}  # PE port -> (metro, circuit, in service)
    for metro, host in tl["founding"].items():
        if metro not in IXES:
            continue
        ix_ports[f"device/pop-{host}/pe-a/if/{PE_IX_PORT}"] = (metro, f"circuit/ix/{metro}", True)
        hosts = sorted((p for p in pops if pops[p]["metro"] == metro), key=lambda p: (tl["launch"][p], order[p]))
        if metro == IX_RELOCATED and len(hosts) > 1:
            ix_ports[f"device/pop-{hosts[1]}/pe-a/if/{PE_IX_PORT}"] = (metro, f"circuit/ix/{metro}/former", False)
    for port in ix_ports:
        used_pe_ports[port.rsplit("/if/", 1)[0]].add(port)
    for pop in ordered:
        sid, site = f"pop-{pop}", f"site/pop-{pop}"
        pe_a, pe_b = (f"device/{sid}/pe-{side}" for side in ("a", "b"))
        # Every SFP+ position cabled is the MX304 trigger (DESIGN §3, gate 5).
        free = {s: len(SFP_PLUS_PORTS) - sum(bool(adjacent.get(f"device/{sid}/pe-{s}/if/{n}")) for n in SFP_PLUS_PORTS)
                for s in "ab"}
        exhausted = {s for s in "ab" if free[s] == 0}
        if (exhausted != {s for s in "ab" if tl["sfp_plus"][(pop, s)] >= len(SFP_PLUS_PORTS)} or
                (tl["mx304"][pop] is not None and not exhausted)):
            report("provider-successor", site, "An MX304 successor pair is planned exactly where a PE has no free SFP+ "
                   "position left for its handoffs.")
        ceiling = ("SFP+ positions exhausted, MX304 successor planned" if tl["mx304"][pop]
                   else "MX204 SFP+ ports exhausted at 8, next platform MX304")
        for router in (pe_a, pe_b):
            if attrs(router).get("description") != f"{role_label('provider-edge')} at {attrs(site).get('name')}; {ceiling}":
                report("provider-scope-text", router, "The PE states its role and the MX204 port ceiling with its MX304 growth path.")
            physical = {p["name"] for p in catalog.get("provider-edge", {}).get("interfaces", [])}
            ports = child("device", router, "interface")
            names = {attrs(p).get("name"): p for p in ports}
            logical = {name for name, port in names.items() if name not in physical}
            subifs = {name for name in logical if re.fullmatch(rf"{PE_LAG}\.\d+", name)}
            units = {name for name in logical if name.endswith(".0") and name[:-2] in physical}
            if (not physical <= set(names) or logical - subifs - units != {"lo0", "lo0.0", PE_LAG} or
                    any(refs(names[name]).get("parent") != f"{router}/if/{PE_LAG}" or attrs(names[name]).get("type") != "virtual"
                        for name in subifs)):
                report("provider-port-inventory", router, "PE interfaces are the pinned chassis plus lo0/lo0.0, the ae1 access LAG, "
                       "its ae1.<vid> service units and the .0 units of addressed ports.")

            def in_use(port):
                return bool(adjacent.get(port) or child("assigned_object", l3(port) or port, "ip_address"))
            for n in range(4):
                port = f"{router}/if/et-0/0/{n}"
                if (attrs(port).get("type") != "100gbase-x-qsfp28" or attrs(port).get("enabled") is not (n < 3 and in_use(port)) or
                        (n < 3 and attrs(port).get("speed") != 100000000) or (n == 3 and adjacent.get(port))):
                    report("provider-port-mode", port, "The installed MX204 mode exposes three active 100G cages and leaves the fourth unavailable.")
            speeds = {**{p: 10000000 for p in PE_LAG_MEMBERS}, PE_NOC_PORT: 1000000, PE_MGMT_PORT: 10000000,
                      PE_TRANSIT_PORT: 10000000, PE_IX_PORT: 10000000}
            for n in range(8):
                name = f"xe-0/1/{n}"
                port = f"{router}/if/{name}"
                used = port in used_pe_ports[router]
                live = used and ix_ports.get(port, (None, None, True))[2]
                if (attrs(port).get("type") != "10gbase-x-sfpp" or attrs(port).get("enabled") is not live or
                        (used and attrs(port).get("speed") != speeds.get(name)) or
                        (name in PE_LAG_MEMBERS) != (refs(port).get("lag") == f"{router}/if/{PE_LAG}")):
                    report("provider-port-mode", port, "PE SFP+ ports: xe-0/1/0-3 the 10G ae1 members, xe-0/1/4 the 1G NOC handoff, "
                           "xe-0/1/5 the 10G exchange port, xe-0/1/6 the 10G management uplink, xe-0/1/7 the 10G transit "
                           "handoff; unused and withdrawn ports are shut.")
            actual = {p for p in ports if adjacent.get(p)}
            if actual != used_pe_ports[router]:
                report("provider-port-use", router, "Only the reserved PE ports are cabled: pair, transport, LAG members, NOC, "
                       "management, transit and exchange. No access attachment lands on a PE physical port.")
            lo, unit = f"{router}/if/lo0", f"{router}/if/lo0.0"
            if attrs(lo).get("description") != "Backbone router identity loopback":
                report("provider-scope-text", lo, "The loopback descriptor must state its role as the router's backbone identity.")
            loopnet = ip_network((int(PUBLIC_POOLS["loopbacks"].network_address) + loop_slots[router] + 1, 32))
            matching_prefixes = prefixes_by_vrf_network[(CORE, str(loopnet))]
            if (attrs(lo).get("type") != "virtual" or attrs(lo).get("enabled") is not True or refs(lo).get("device") != router or
                    attrs(unit).get("type") != "virtual" or refs(unit).get("parent") != lo or
                    child("assigned_object", lo, "ip_address") or not address(unit, loopnet, CORE, 0, "tenant") or
                    not primary(router, unit) or len(matching_prefixes) != 1 or attrs(matching_prefixes[0]).get("status") != "active"):
                report("provider-loopback", router, "Each PE needs its own active reserved /32 prefix and primary lo0.0 address "
                       "from the documentation loopback /27, in the global table.")
            supplies = {f"{router}/power/PEM {n}" for n in range(2)}
            if set(child("device", router, "power_port")) != supplies:
                report("provider-psu-inventory", router, "Each MX204 must retain both real populated PEM 0 and PEM 1 supply inlets.")
            pdus, panels = set(), set()
            allowance = 320 + poe_watts.get(router, 0) + optics_watts.get(router, 0)
            quotient, remainder = divmod(allowance, 2)
            for n in range(2):
                port = f"{router}/power/PEM {n}"
                bay, module = f"{router}/module-bay/Power Supply {n}", f"{router}/module/Power Supply {n}"
                outlet = adjacent.get(port)
                pdu, inlet = refs(outlet).get("device"), refs(outlet).get("power_port")
                feed = adjacent.get(inlet)
                panel = refs(feed).get("power_panel")
                good = (kind(bay) == "module_bay" and attrs(bay).get("position") == f"PEM {n}" and attrs(bay).get("enabled") is True and
                        refs(bay).get("device") == router and kind(module) == "module" and attrs(module).get("status") == "active" and
                        refs(module).get("device") == router and refs(module).get("module_bay") == bay and
                        attrs(refs(module).get("module_type")).get("model") == "JPSU-650W-AC-AO" and refs(port).get("module") == module and
                        attrs(port).get("type") == "iec-60320-c14" and type(attrs(port).get("allocated_draw")) is int and
                        attrs(port).get("allocated_draw") == quotient + (n < remainder) and
                        type(attrs(port).get("maximum_draw")) is int and attrs(port).get("maximum_draw") == allowance)
                if not good:
                    report("provider-psu-inventory", port, "Each installed AO module must own its matching named C14 inlet, with the exact "
                           "split of chassis plus PoE/optics allowances and the full total on failover.")
                if (kind(outlet) != "power_outlet" or kind(inlet) != "power_port" or refs(inlet).get("device") != pdu or
                        kind(feed) != "power_feed" or kind(panel) != "power_panel" or not path_ok(port) or not path_ok(inlet) or
                        any(attrs(item).get("status") != "active" for item in (pdu, feed)) or
                        any(refs(item).get("rack") != refs(router).get("rack") for item in (pdu, feed)) or
                        refs(panel).get("site") != site):
                    report("provider-power-path", port, "Each PE supply must reach an active PDU/feed in its own cabinet and a local upstream power panel.")
                pdus.add(pdu)
                panels.add(panel)
            if None in pdus or None in panels or len(pdus) != 2 or len(panels) != 2:
                report("provider-power-diversity", router, "The PE's populated supplies require two distinct real PDUs and upstream panels.")
        pair = f"pair/{sid}"
        a, b = f"{pe_a}/if/et-0/0/0", f"{pe_b}/if/et-0/0/0"
        routed(pair, (a, b), CORE)
        if not cabled(a, b, 100000000):
            report("provider-pair-path", site, "A connected routed 100G inter-cabinet cord must join the two actual PE data ports.")

    # --- Aggregation: straight intra-cabinet 4x10G LAGs and shut spare UNIs ---
    attachments = {}  # target -> dict(pop, side, uni, subif, vlan)
    for target, (sid, n) in targets.items():
        customer, pop, _ = premises[sid]
        side = side_of[target]
        vid = SERVICE_VLAN_BASE + vid_slots[target]
        attachments[target] = dict(pop=pop, side=side, vid=vid, vlan=f"vlan/pop-{pop}/{vid}/customer",
                                   uni=f"device/pop-{pop}/agg-{side}/if/{unis[uni_slots[target]]}",
                                   subif=f"device/pop-{pop}/pe-{side}/if/{PE_LAG}.{vid}")
        homed_vlans[(pop, side)].add(f"vlan/pop-{pop}/{vid}/customer")
    for pop in ordered:
        sid = f"pop-{pop}"
        for side in ("a", "b"):
            agg, pe = f"device/{sid}/agg-{side}", f"device/{sid}/pe-{side}"
            agg_lag, pe_lag = f"{agg}/if/{AGG_LAG}", f"{pe}/if/{PE_LAG}"
            carried = homed_vlans[(pop, side)] | {nid_vlan[(pop, side)]}
            members = [p for p in child("lag", agg_lag, "interface")]
            pe_members = [p for p in child("lag", pe_lag, "interface")]
            pairs = list(zip([f"{agg}/if/{name}" for name in agg_lag_ports], [f"{pe}/if/{name}" for name in PE_LAG_MEMBERS]))
            if (len(members) < 2 or len(members) != LAG_MEMBERS or set(members) != {a for a, _ in pairs} or
                    set(pe_members) != {b for _, b in pairs} or
                    any(not cabled(a, b, LAG_MEMBER_KBPS) or refs(refs(a).get("device")).get("rack") != refs(refs(b).get("device")).get("rack")
                        for a, b in pairs)):
                report("provider-aggregation-lag", agg_lag, "Each side's aggregation switch reaches its own cabinet's PE on a "
                       "straight four-member 10G LAG, member n to member n.")
            for lag in (agg_lag, pe_lag):
                if (attrs(lag).get("type") != "lag" or attrs(lag).get("mode") != "tagged" or attrs(lag).get("enabled") is not True or
                        set(refs(lag).get("tagged_vlans") or []) != carried):
                    report("provider-home-vlans", lag, "A side's LAG carries exactly the service VLANs homed on that side and "
                           "its NID-management VLAN, never the other side's.")
            used = {a["uni"] for a in attachments.values() if a["pop"] == pop and a["side"] == side}
            for name in unis:
                port = f"{agg}/if/{name}"
                if port not in used and (adjacent.get(port) or attrs(port).get("enabled") is not False or vlans(port)):
                    report("provider-port-mode", port, "An aggregation UNI with no attachment is shut, uncabled and carries no VLAN.")

    # --- PoP management LAN, uplinks and the cellular out-of-band service ---
    def local_network(sid, role, vrf, tenant):
        site = f"site/{sid}"
        container = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
        network = ip_network((int(container.network_address) + (128 if role == "clients" else 0), 25 if role == "clients" else 26))
        vlan, prefix = f"vlan/{sid}/{role}", f"prefix/{sid}/{role}"
        if (kind(prefix) != "prefix" or attrs(prefix).get("status") != life("prefix") or attrs(prefix).get("prefix") != str(network) or
                refs(prefix).get("vrf") != vrf or refs(prefix).get("tenant") != tenant or refs(prefix).get("scope_site") != site or refs(prefix).get("vlan") != vlan or
                kind(vlan) != "vlan" or attrs(vlan).get("vid") != (20 if role == "clients" else 10) or attrs(vlan).get("status") != life("vlan") or
                refs(vlan).get("site") != site or refs(vlan).get("tenant") != tenant or
                attrs(f"prefix/{sid}/reservation").get("prefix") != str(container) or "vrf" in refs(f"prefix/{sid}/reservation") or
                refs(f"prefix/{sid}/reservation").get("tenant") != tenant):
            report("provider-site-network", prefix, "Management /26 and customer /25 segments require their fixed global site block, VLAN, VRF and tenant.")
        return network, vlan

    def svi(port, network, vlan, vrf, host, tenant, parent=None):
        return (kind(port) == "interface" and attrs(port).get("type") == "virtual" and attrs(port).get("enabled") is True and
                refs(port).get("parent") == parent and address(port, network, vrf, host, tenant) and
                (vlan is None or vlans(port) == {vlan}))

    for pop in ordered:
        sid, site = f"pop-{pop}", f"site/pop-{pop}"
        network, vlan = local_network(sid, "management", MANAGEMENT, "tenant")
        switch, console = f"device/{sid}/mgmt-01", f"device/{sid}/console-01"
        irb = f"{switch}/if/Vlan10"
        if not svi(irb, network, None, MANAGEMENT, 1, "tenant") or not primary(switch, irb):
            report("provider-management-gateway", switch, "The PoP management subnet requires its routed switch interface and primary management address.")
        pdus = [f"device/{sid}/pdu-r0{n}-{feed}" for n in ((1, 2) if tl["tier"][pop] == "core" else (1,)) for feed in ("a", "b")]
        plan_ports = [(f"device/{sid}/pe-a/if/fxp0", FXP0_HOSTS[0], False), (f"device/{sid}/pe-b/if/fxp0", FXP0_HOSTS[1], False),
                      (f"device/{sid}/agg-a/if/em0", AGG_EM0_HOSTS[0], True), (f"device/{sid}/agg-b/if/em0", AGG_EM0_HOSTS[1], True),
                      (f"{console}/if/{eth0}", OOB_HOST, True),
                      *((f"{pdu}/if/{pdu_net}", host, True) for pdu, host in zip(pdus, PDU_HOSTS))]
        # The PoP services take the next copper ports: the time server's lan1
        # in service, the staged DDoS appliance's Management pre-cabled
        # (planned cable, reserved address, not yet its primary).
        services = [(f"device/{sid}/ntp-01/if/{catalog.get('time-server', {}).get('management_port')}", TIME_SERVER_HOST, True)
                    ] if tl["timing"][pop] else []
        if tl["ddos"][pop]:
            services.append((f"device/{sid}/ddos-01/if/Management", DDOS_HOST, False))
        for index, (port, host, in_service) in enumerate(services, len(plan_ports)):
            peer, device = f"{switch}/if/{copper[index]}", refs(port).get("device")
            ips = [k for k in child("assigned_object", port, "ip_address") if k in ipv4_addresses]
            if (adjacent.get(port) != peer or attrs(cable_of.get(port)).get("status") != ("connected" if in_service else "planned") or
                    vlans(peer) != {vlan} or attrs(peer).get("mode") != "access" or len(ips) != 1 or
                    ipv4_addresses[ips[0]] != ip_interface(f"{network[host]}/{network.prefixlen}") or
                    refs(ips[0]).get("vrf") != MANAGEMENT or attrs(ips[0]).get("status") != ("active" if in_service else "reserved") or
                    (refs(device).get("primary_ip4") == ips[0]) is not in_service):
                report("provider-management-mode", port, "The time server's management port and the staged DDoS appliance's "
                       "are cabled to the next management-switch copper ports and addressed in Carrier Management; only "
                       "the in-service time server's address is active and primary.")
        # The switch port is the access switchport; the host port carries no
        # 802.1Q mode (the shared view lends it its prefix's segment only).
        for index, (port, host, is_primary) in enumerate(plan_ports):
            peer = f"{switch}/if/{copper[index]}"
            device = refs(port).get("device")
            if (not cabled(port, peer) or vlans(peer) != {vlan} or attrs(peer).get("mode") != "access" or not vlans(port) <= {vlan} or attrs(port).get("mode") not in (None, "access") or
                    not attrs(port).get("mgmt_only") or not address(port, network, MANAGEMENT, host, "tenant") or
                    primary(device, port) is not is_primary):
                report("provider-management-mode", port, "Every PE fxp0, aggregation em0, console server and switched PDU has its own "
                       "management-switch copper port, access on the management VLAN, and its Carrier Management address.")
        dedicated = next((f"{switch}/if/{p['name']}" for p in mgmt_spec.get("interfaces", []) if p.get("mgmt_only")), None)
        if dedicated and (adjacent.get(dedicated) or child("assigned_object", dedicated, "ip_address")):
            report("provider-management-mode", dedicated, "The routed management switch must not duplicate its subnet on its dedicated management port.")
        for index, side in enumerate(("a", "b")):
            a, b = f"{switch}/if/{mgmt_uplinks[index]}", f"device/{sid}/pe-{side}/if/{PE_MGMT_PORT}"
            routed(f"management/{sid}/{side}", (a, b), MANAGEMENT)
            if not cabled(a, b, 10000000):
                report("provider-management-uplink", site, "PoP management requires two real routed 10G switch uplinks to the separate PE ports.")
        # Cellular out-of-band: the LTE modem cannot be cabled (NetBox refuses
        # it); it holds the cellular carrier's /30, and the circuit's local end
        # is site-scoped and marked connected. No trace is claimed.
        key, modem = f"circuit/oob/{pop}", f"{console}/if/{lte}"
        oob = ip_network((int(OOB_POOL.network_address) + 4 * oob_slots[pop], 30))
        prefix = f"prefix/link/oob/{pop}"
        terms = {attrs(t).get("term_side"): t for t in child("circuit", key, "circuit_termination")}
        if (attrs(modem).get("type") != "lte" or adjacent.get(modem) or not address(modem, oob, OOB, 2, "tenant") or primary(console, modem) or
                attrs(prefix).get("prefix") != str(oob) or refs(prefix).get("vrf") != OOB or attrs(prefix).get("status") != "active" or
                kind(key) != "circuit" or refs(key).get("type") != "circuit-type/cellular-oob" or refs(key).get("provider") != "provider/oob" or
                refs(key).get("provider_account") != "provider-account/provider/oob" or "commit_rate" in attrs(key) or
                attrs(key).get("status") != "active" or set(terms) != {"A", "Z"} or
                refs(terms["A"]).get("termination") != site or attrs(terms["A"]).get("mark_connected") is not True or adjacent.get(terms["A"]) or
                refs(terms["Z"]).get("termination") != "provider-network/oob" or adjacent.get(terms["Z"])):
            report("provider-oob", key, "Each PoP console server's uncabled LTE modem holds its reserved cellular /30 in the "
                   "out-of-band context; the Cellular OOB circuit ends site-scoped and marked connected, never traced.")
        for term in terms.values():
            cross_connect(term, None, refs(term).get("termination"), "provider/oob")

    # --- Backbone spans, transit and the NOC ---
    adjacency = {router: [] for router in routers}
    capacity = {}

    def edge(key, a, b, rate):
        if a in adjacency and b in adjacency:
            adjacency[a].append((key, b, key))
            adjacency[b].append((key, a, key))
            capacity[key] = rate

    for pop in pops:
        sid = f"pop-{pop}"
        if cabled(f"device/{sid}/pe-a/if/et-0/0/0", f"device/{sid}/pe-b/if/et-0/0/0", 100000000):
            edge(f"pair/{sid}", f"device/{sid}/pe-a", f"device/{sid}/pe-b", 100000000)
    site_points = {site: (attrs(site)["latitude"], attrs(site)["longitude"]) for site in expected_sites
                   if all(type(attrs(site).get(f)) in (int, float) for f in ("latitude", "longitude"))}
    for key, (a, b) in spans.items():
        port_a = f"{a}/if/et-0/0/{1 + transport_ports[a][key]}"
        port_b = f"{b}/if/et-0/0/{1 + transport_ports[b][key]}"
        provider = span_provider[key]
        owned = provider == "provider/operator"
        commit = None if owned else LEASED_COMMIT
        routed(key, (port_a, port_b), CORE)
        if circuit(key, port_a, port_b, refs(a).get("site"), refs(b).get("site"), provider, 100000000, commit,
                   account="provider-account/operator/fiber" if owned else f"provider-account/{provider}",
                   circuit_type="circuit-type/dark-fiber" if owned else "circuit-type/backbone"):
            edge(key, a, b, 100000000 if owned else commit)
        ends = [site_points.get(refs(router).get("site")) for router in (a, b)]
        if all(ends) and (attrs(key).get("distance") != round(_km(*ends) * ROUTE_FACTOR, 1) or attrs(key).get("distance_unit") != "km"):
            report("provider-backbone-geography", key, "A span's route length must follow its two PoPs' actual positions.")
    transit_peerings = {}
    for index, side in enumerate(("a", "b")):
        pop = ordered[index]
        router = f"device/pop-{pop}/pe-{side}"
        port = f"{router}/if/{PE_TRANSIT_PORT}"
        key, provider = f"circuit/transit/{side}", f"provider/transit-{side}"
        transit_peerings[side] = (router, port, provider, key)
        routed(key, (port,), CORE, hosts=(1,))
        circuit(key, port, None, f"site/pop-{pop}", f"provider-network/transit/{side}", provider, 10000000, 10000000,
                account=f"provider-account/{provider}", circuit_type="circuit-type/transit")
        container = f"prefix/upstream/transit-{side}"
        if (attrs(container).get("prefix") != str(UPSTREAM_POOLS[side]) or "tenant" in refs(container) or
                any(link_network[key].subnet_of(ip_network(a)) for a in (str(p) for p in PUBLIC_POOLS.values()))):
            report("provider-public-space", key, "A transit /31 is numbered from its upstream's own recorded assignment, "
                   "which carries no operator tenancy and sits outside the operator's backbone pools.")
    noc_edges = set()
    for side in ("a", "b"):
        pop = recipe[f"noc_pop_{side}"]
        router = f"device/pop-{pop}/pe-{side}"
        pe = f"{router}/if/{PE_NOC_PORT}"
        device = f"device/dc-01/edge-001-{side}"
        port = f"{device}/if/wan1"
        noc_edges.add(device)
        key = f"circuit/noc/{side}"
        routed(key, (port, pe), MANAGEMENT)
        local = pops[pop]["metro"] == pops[recipe["noc_pop_a"]]["metro"]
        provider = "provider/operator" if local else f"provider/transport-{side}"
        if (not circuit(key, port, pe, "site/dc-01", f"site/pop-{pop}", provider, 1000000, 1000000,
                        account="provider-account/operator/noc" if local else f"provider-account/{provider}",
                        circuit_type="circuit-type/noc-access") or
                (not local and not attrs(key).get("description", "").startswith("1G Ethernet private line")) or
                refs(device).get("role") != "role/wan-edge" or refs(device).get("device_type") != "hardware/edge" or
                refs(device).get("site") != "site/dc-01" or attrs(device).get("status") != "active" or
                attrs(port).get("type") != "1000base-t"):
            report("provider-noc-wan", key, "The NOC needs each distinct real active 1G edge/circuit path to its own PE's "
                   "dedicated NOC port, an owned link in the NOC's metro or a leased private line otherwise.")
    if len({refs(d).get("rack") for d in noc_edges}) != 2:
        report("provider-noc-diversity", "site/dc-01", "The two NOC provider attachments must retain separate local edge racks.")

    # --- Customer premises: NID, CE, MPOE cabinet and the attachment ---
    code = _operator_code(recipe["name"])
    stages = {sid: _stage(customer, pop) for sid, (customer, pop, _) in premises.items()}
    customer_peerings, flows, side_load = {}, Counter(), Counter()
    first_attachment = {}
    for target, (sid, ordinal) in sorted(targets.items()):
        customer, pop, number = premises[sid]
        now[0] = stages[sid]
        service, key = customer["service"], customer["key"]
        tenant = f"tenant/cust-{key}"
        hub = service == "private-l3" and pop == customer["hub_pop"] and number == 1
        rate = rate_of(customer, pop, hub)
        handoff = 1000 if service == "private-l3" else _handoff(rate, usable)
        att = attachments[target]
        side, uni, subif, vlan = att["side"], att["uni"], att["subif"], att["vlan"]
        nid = f"device/{sid}/nid-{ordinal:02}"
        # The first NID generation (P1-14) stays where a single-NID 1G service
        # with customer-owned gear behind it entered service before 2016.
        circuit_key = f"circuit/customer/{target}"
        early = str(attrs(circuit_key).get("install_date", "9999")) < LEGACY_NID_BEFORE.isoformat()
        legacy_nid = early and (service == "epl" or (service == "dia" and not customer["managed"]))
        alias = "nid-10g" if handoff > 1000 else "nid-legacy" if legacy_nid else "nid"
        handoff_label = ", 1000BASE-LX handoff" if alias == "nid-legacy" else ""
        spec = catalog.get(alias, {})
        nni = f"{nid}/if/{spec.get('nni_port')}"
        nid_uni = f"{nid}/if/{spec.get('uni_port')}"
        router = f"device/pop-{pop}/pe-{side}"
        if ordinal == 1:
            first_attachment[sid] = router
        circuit_key = f"circuit/customer/{target}"
        # The access circuit: NID NNI -> circuit -> OSP panel -> home-side UNI.
        cid = f"{code}-{CID_CODES[service]}-{allocations[sid]:05d}{'-2' if ordinal == 2 else ''}"
        circuit(circuit_key, nni, uni, f"site/{sid}", f"site/pop-{pop}", "provider/operator", handoff * 1000, rate * 1000,
                tenant, f"provider-account/customer/{key}", circuit_type=f"circuit-type/{service}-access",
                texts={"A": f"Customer demarcation, {attrs(f'location/{sid}').get('name')}",
                       "Z": f"Access fibre landed on AGG-{side.upper()} at {attrs(f'site/pop-{pop}').get('name')}"})
        if attrs(circuit_key).get("cid") != cid:
            report("provider-circuit-path", circuit_key, "An access circuit's ID is the operator code, service and premises allocation.")
        ends = [site_points.get(f"site/{sid}"), site_points.get(f"site/pop-{pop}")]
        route = round(_km(*ends) * ROUTE_FACTOR, 1) if all(ends) else None
        if (attrs(circuit_key).get("distance"), attrs(circuit_key).get("distance_unit")) != ((route, "km") if route else (None, None)):
            report("provider-access-geography", circuit_key, "An access circuit records the route length from its premises to its serving PoP.")
        try:
            ends_on = date.fromisoformat(attrs(circuit_key)["termination_date"]) if "termination_date" in attrs(circuit_key) else None
        except (TypeError, ValueError):
            ends_on = date.min
        if (ends_on is not None) != (stages[sid] == "decommissioning") or (ends_on is not None and ends_on <= date.fromisoformat(recipe["as_of"])):
            report("provider-lifecycle-equipment", circuit_key, "Only a deprovisioning access circuit carries its scheduled disconnect date, after as_of.")
        # Home side: the alternating ledger, the side's UNI, VLAN and PE unit.
        in_service = stages[sid] in ("active", "decommissioning")
        epl = service == "epl"
        mode_ok = ((attrs(uni).get("mode") == "q-in-q" and refs(uni).get("qinq_svlan") == vlan and not refs(uni).get("tagged_vlans"))
                   if epl else (attrs(uni).get("mode") == "tagged" and set(refs(uni).get("tagged_vlans") or []) == {vlan, nid_vlan[(pop, side)]}))
        if (kind(vlan) != "vlan" or attrs(vlan).get("vid") != att["vid"] or refs(vlan).get("site") != f"site/pop-{pop}" or
                refs(vlan).get("tenant") != tenant or attrs(vlan).get("status") != life("vlan") or
                attrs(vlan).get("qinq_role") != ("svlan" if epl else None) or not mode_ok or
                attrs(uni).get("enabled") is not in_service or attrs(uni).get("speed") != handoff * 1000 or
                kind(subif) != "interface" or attrs(subif).get("type") != "virtual" or refs(subif).get("parent") != f"{router}/if/{PE_LAG}" or
                attrs(subif).get("mode") != "access" or refs(subif).get("untagged_vlan") != vlan or
                attrs(subif).get("enabled") is not in_service or
                refs(subif).get("vrf") != (f"vrf/customer/{key}" if service == "private-l3" else None)):
            report("provider-attachment", target, "An attachment is homed on one side: its service VLAN at the PoP, the UNI "
                   "(tagged with the side's NID management, or port-based Q-in-Q for an EPL) on that side's aggregation "
                   "switch and the ae1.<vid> unit on that side's PE; a premises not yet in service keeps both shut.")
        # Physical lifecycle of the serving optic at the UNI.
        module = refs(uni).get("module")
        want = PENDING_OPTIC.get(stages[sid], "active")
        if kind(module) == "module" and (attrs(module).get("status") != want or ("serial" in attrs(module)) == (want == "planned")):
            report("provider-lifecycle-equipment", module, "The serving optic of a premises not yet in service is planned "
                   "without a serial (onboarding customer) or staged (provisioning); otherwise it is installed.")
        # The NID: carrier demarcation, managed in-band from its home side's /25.
        nid_net = ip_network((int(pool.broadcast_address) - 65535 + 32768 + 256 * nid_slots[pop] + 128 * "ab".index(side), 25))
        management = f"{nid}/if/Management"
        racked = service == "private-l3" or (service == "dia" and customer["managed"])
        rack = f"rack/{sid}/network-01"
        if (kind(nid) != "device" or refs(nid).get("role") != "role/nid" or refs(nid).get("tenant") != "tenant" or
                refs(nid).get("device_type") != f"hardware/{alias}" or attrs(nid).get("status") != life("device") or
                refs(nid).get("location") != f"location/{sid}" or (refs(nid).get("rack") == rack) is not racked or
                (not racked and (refs(nid).get("rack") or attrs(nid).get("position") is not None)) or
                attrs(nid).get("description") != f"Carrier demarcation at {attrs(f'site/{sid}').get('name')}, homed on "
                                                  f"{titleize(pop)} AGG-{side.upper()}" or
                attrs(management).get("type") != "virtual" or
                not address(management, nid_net, MANAGEMENT, 2 + nid_hosts.get(nid, -3), "tenant") or not primary(nid, management) or
                any(attrs(p).get("mark_connected") is not True or adjacent.get(p) for p in child("device", nid, "power_port"))):
            report("provider-nid", nid, "Every attachment ends on its own carrier NID (the 10G tier past a 1G handoff), managed "
                   "in-band from its home side's NID /25, on customer power: racked in the MPOE cabinet where a carrier CE "
                   "stands beside it, otherwise unracked at the demarcation.")
        if epl and attrs(management).get("description") != "In-band management carried inside the service S-VLAN":
            report("provider-nid", management, "An EPL NID's management is declared as carried inside the service S-VLAN.")
        # Load on the home side's LAG; only in-service premises offer traffic.
        if stages[sid] == "active":
            side_load[(pop, side)] += rate if (service != "private-l3" or hub) else _site_peak(customer, pop)
        if service == "private-l3":
            vrf = f"vrf/customer/{key}"
            edge_device = f"device/{sid}/edge-01"
            wan = f"{edge_device}/if/{catalog.get('edge' if hub else 'ce-small', {}).get('wan_ports', [None, None])[ordinal - 1]}"
            routed(circuit_key, (wan, subif), vrf, tenant)
            if not cabled(wan, nid_uni):
                report("provider-customer-handoff", wan, "The CE's WAN port is one labelled copper hop to its NID's customer port.")
            customer_peerings[target] = (router, subif, edge_device, wan, tenant, key, circuit_key, stages[sid])
        elif service == "dia":
            block = DIA_NETWORKS[dia_slots[sid]]
            assignment = f"prefix/dia/{sid}"
            if (attrs(assignment).get("prefix") != str(block) or refs(assignment).get("tenant") != tenant or
                    refs(assignment).get("vrf") or attrs(assignment).get("status") != life("prefix")):
                report("provider-dia-address", assignment, "Each DIA premises takes its reserved documentation /29 in the global table.")
            if customer["managed"]:
                link = ip_network((int(DIA_LINK_POOL.network_address) + 2 * dia_links[sid], 31))
                edge_device = f"device/{sid}/edge-01"
                wan = f"{edge_device}/if/{catalog['ce-small']['wan_ports'][0]}"
                lan = f"{edge_device}/if/{catalog['ce-small']['lan_ports'][0]}"
                if (attrs(f"prefix/dia/link/{sid}").get("prefix") != str(link) or not address(subif, link, None, 0, tenant) or
                        not address(wan, link, None, 1, tenant) or not address(lan, block, None, 1, tenant) or
                        attrs(lan).get("mark_connected") is not True or adjacent.get(lan) or not cabled(wan, nid_uni)):
                    report("provider-dia-address", sid, "Managed DIA joins PE and CE on a public /31, routes the /29 to the CE's "
                           "marked-connected LAN handoff, and cables the CE to its NID.")
            elif (not address(subif, block, None, 1, tenant) or attrs(nid_uni).get("mark_connected") is not True or
                  adjacent.get(nid_uni) or attrs(nid_uni).get("label") != f"Customer-owned firewall{handoff_label}"):
                report("provider-dia-address", sid, "Unmanaged DIA routes the /29 on the PE unit (.1) and marks the NID's "
                       "customer port connected to the customer-owned firewall the carrier does not inventory.")
        else:
            if (child("assigned_object", subif, "ip_address") or attrs(nid_uni).get("mark_connected") is not True or
                    adjacent.get(nid_uni) or attrs(nid_uni).get("label") != f"Customer Ethernet equipment{handoff_label}"):
                report("provider-epl", sid, "An EPL end carries no address on its PE unit and marks the NID's customer port "
                       "connected to customer Ethernet equipment the carrier does not inventory.")
    now[0] = "active"
    for (pop, side), load in sorted(side_load.items()):
        for members in (LAG_MEMBERS, LAG_MEMBERS - 1):
            if Decimal(load) > Decimal(members * LAG_MEMBER_KBPS // 1000) * usable:
                report("provider-aggregation-capacity", f"device/pop-{pop}/agg-{side}",
                       f"{load} Mbps of in-service declared access peaks exceed the {members}x10G AGG-PE LAG after reserve "
                       f"({'steady state' if members == LAG_MEMBERS else 'after one member loss'}).")

    # Premises kit: exact devices, MPOE cabinet and the carrier CE.
    for sid, (customer, pop, number) in premises.items():
        now[0] = stages[sid]
        service, key = customer["service"], customer["key"]
        site, tenant = f"site/{sid}", f"tenant/cust-{key}"
        hub = service == "private-l3" and pop == customer["hub_pop"] and number == 1
        nids = {f"device/{sid}/nid-0{n}" for n in ((1, 2) if hub else (1,))}
        ce = f"device/{sid}/edge-01"
        has_ce = service == "private-l3" or (service == "dia" and customer["managed"])
        lan_switch = service == "private-l3" and customer["lan_endpoints"] > 0
        expected = nids | ({ce} if has_ce else set())
        if lan_switch:
            expected |= {f"device/{sid}/access-01"} | {f"device/{sid}/pc-{n:03}" for n in range(1, customer["lan_endpoints"] + 1)}
        actual = {d for d in child("site", site, "device") if refs(d).get("role") not in {"role/patch-panel", "role/wall-outlet"}}
        racks = child("site", site, "rack")
        rack = f"rack/{sid}/network-01"
        if actual != expected:
            report("provider-device-inventory", site, "A premises inventories exactly its kit: NID(s), the carrier CE where the "
                   "service is managed, and a requested managed LAN.")
        for device in sorted(expected):
            room = f"location/{sid}/office-01" if "/pc-" in device else f"location/{sid}"
            if refs(device).get("location") != room or refs(device).get("site") != site:
                report("provider-device-inventory", device, "Premises equipment stands in its MPOE room; workstations in the customer office.")
        if has_ce:
            ce_alias = "edge" if hub else "ce-small"
            if (refs(ce).get("device_type") != f"hardware/{ce_alias}" or refs(ce).get("role") != "role/customer-edge" or
                    refs(ce).get("tenant") != tenant or attrs(ce).get("status") != life("device") or refs(ce).get("rack") != rack or
                    attrs(ce).get("position") is None or
                    any(attrs(p).get("mark_connected") is not True or adjacent.get(p) for p in child("device", ce, "power_port"))):
                report("provider-customer-edge", ce, "A managed premises' carrier CE (the 100F at a hub, the small CE elsewhere) "
                       "stands in the MPOE cabinet on customer power.")
            if (racks != [rack] or attrs(rack).get("name") != "MPOE-1" or refs(rack).get("rack_type") != "rack-type/mpoe-cabinet" or
                    refs(rack).get("tenant") != "tenant" or refs(rack).get("location") != f"location/{sid}" or
                    attrs(rack).get("status") != life("rack") or child("rack", rack, "power_feed") or child("site", site, "power_panel")):
                report("provider-mpoe-cabinet", site, "A premises with two or more carrier devices holds them in one carrier MPOE "
                       "wall cabinet on customer power: no feed, PDU or panel is inventoried.")
        elif racks or child("site", site, "power_panel"):
            report("provider-mpoe-cabinet", site, "A single-NID demarcation stands unracked: no cabinet, feed or panel.")
        if (stages[sid] == "planned") == ("serial" in attrs(ce if has_ce else f"device/{sid}/nid-01")):
            report("provider-lifecycle-equipment", ce if has_ce else f"device/{sid}/nid-01",
                   "Planned premises equipment has not shipped and carries no serial; every other unit does.")
        if service != "private-l3":
            continue
        vrf = f"vrf/customer/{key}"
        lan = f"{ce}/if/{catalog.get('edge' if hub else 'ce-small', {}).get('lan_ports', [None])[0]}"
        if lan_switch:
            switch = f"device/{sid}/access-01"
            clients, clients_vlan = local_network(sid, "clients", vrf, tenant)
            management, management_vlan = local_network(sid, "management", vrf, tenant)
            upstream = f"{switch}/if/{access_ports[-1]}"
            if (not cabled(lan, upstream) or any(vlans(p) != {management_vlan, clients_vlan} or attrs(p).get("mode") != "tagged" for p in (lan, upstream)) or
                    not svi(f"{ce}/if/Management", management, management_vlan, vrf, 1, tenant, lan) or
                    not svi(f"{ce}/if/Clients", clients, clients_vlan, vrf, 1, tenant, lan) or
                    not primary(ce, f"{ce}/if/Management") or not svi(f"{switch}/if/Vlan10", management, management_vlan, vrf, 2, tenant) or
                    not primary(switch, f"{switch}/if/Vlan10")):
                report("provider-customer-gateway", ce, "Customer LAN requires the real CE/switch trunk, separate addressed local "
                       "management/client gateways and the switch management SVI.")
            for n in range(1, customer["lan_endpoints"] + 1):
                device = f"device/{sid}/pc-{n:03}"
                port, access = f"{device}/if/eth0", f"{switch}/if/{access_ports[n-1]}"
                members = component_members.get(component_of.get(port), ())
                terminal = [p for p in members if kind(p) not in ("front_port", "rear_port") and p != port]
                if (terminal != [access] or not path_ok(port) or any(vlans(p) != {clients_vlan} for p in (port, access)) or
                        not address(port, clients, vrf, tenant=tenant) or not primary(device, port)):
                    report("provider-customer-endpoint", device, "Every requested PC needs its own active access channel, customer VLAN/VRF and actual primary address.")
                room, closet = f"location/{sid}/office-01", f"location/{sid}"
                point = [10 + 2 * ((n-1) % 4), 19 + 2 * ((n-1) // 4), 0.8]
                length = math.ceil(sum(abs(point[i] - (24, 18, 0)[i]) for i in range(3)) + 10)
                placement = dict(room=room, function="office", floor=1, position_m=point, cable_origin=closet)
                if (meta(device).get("placement") != placement or meta(device).get("access_channel_length_m") != length or
                        path_lengths.get(port) != length or not 0 < length <= 80):
                    report("provider-customer-route", device, "Customer desk positions and actual local channel lengths must follow the fixed office geometry.")
                passive = {refs(p).get("device") for p in members if kind(p) in {"front_port", "rear_port"}}
                if recipe["patching"] == "panels":
                    outlets = {d for d in passive if refs(d).get("device_type") == "hardware/wall-outlet"}
                    panels = {d for d in passive if refs(d).get("device_type") == "hardware/patch-panel"}
                    if (len(passive) != 2 or len(outlets) != 1 or len(panels) != 1 or
                            any(refs(d).get("location") != room for d in outlets) or any(refs(d).get("location") != closet for d in panels)):
                        report("provider-customer-patching", device, "Panel channels must use this customer's office outlet and local equipment-room panel.")
                elif len(members) != 2:
                    report("provider-customer-patching", device, "Direct customer access requires exactly one real cable channel.")
            dedicated = f"{switch}/if/{access_mgmt}"
            if adjacent.get(dedicated) or child("assigned_object", dedicated, "ip_address"):
                report("provider-management-mode", dedicated, "Customer management uses the routed local LAN; dedicated management ports remain unused.")
        else:
            # CE only: the LAN port routes the customer's own LAN /24, from the
            # customer's own plan, and the CE is managed on its /32 loopback.
            loop = f"{ce}/if/Management"
            block = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
            management = ip_network((int(block.network_address) + 1, 32))
            lan_prefix = f"prefix/{sid}/lan"
            slot = lan_slots.get(key, {}).get(sid)
            lan_plans = [p for p in CUSTOMER_LAN_PLANS if not p.overlaps(pool)]
            routed_lan = (ip_network((int(lan_plans[customer_slots[key] % len(lan_plans)].network_address) + 256 * (slot + 1), 24))
                          if type(slot) is int and lan_plans else None)
            if (routed_lan is None or attrs(lan_prefix).get("prefix") != str(routed_lan) or refs(lan_prefix).get("vrf") != vrf or
                    refs(lan_prefix).get("tenant") != tenant or refs(lan_prefix).get("scope_site") != site or
                    refs(lan_prefix).get("vlan") or attrs(lan_prefix).get("status") != life("prefix") or
                    any(routed_lan.overlaps(ip_network(a)) for a in (*PUBLIC_AGGREGATES, str(pool))) or
                    adjacent.get(lan) or vlans(lan) or attrs(lan).get("mode") or not address(lan, routed_lan, vrf, 1, tenant)):
                report("provider-customer-lan", ce, "A CE-only premises routes its customer's own LAN /24, from the customer's "
                       "plan outside every carrier pool, on its LAN port in the customer VRF; no switchport or VLAN carries it.")
            if any(kind(k) for k in (f"vlan/{sid}/clients", f"prefix/{sid}/clients", f"prefix/{sid}/reservation", f"{ce}/if/Clients",
                                     f"vlan/{sid}/management")):
                report("provider-customer-lan", ce, "A carrier records no segment, VLAN or site block for a customer LAN it does not run.")
            prefix = f"prefix/{sid}/management"
            if (attrs(loop).get("type") != "virtual" or refs(loop).get("parent") or vlans(loop) or
                    not address(loop, management, vrf, 0, tenant) or not primary(ce, loop) or
                    attrs(prefix).get("prefix") != str(management) or refs(prefix).get("vrf") != vrf or
                    refs(prefix).get("vlan") or refs(prefix).get("scope_site") != site):
                report("provider-customer-gateway", ce, "A CE-only premises is managed on its own carrier /32 loopback in the customer VRF.")
    for sid, (customer, pop, _) in premises.items():
        if customer["service"] == "dia" and customer["managed"]:
            now[0] = stages[sid]
            ce, loop = f"device/{sid}/edge-01", f"device/{sid}/edge-01/if/Management"
            block = ip_network((int(pool.network_address) + allocations[sid] * 256, 24))
            management = ip_network((int(block.network_address) + 1, 32))
            if not address(loop, management, MANAGEMENT, 0, "tenant") or not primary(ce, loop):
                report("provider-customer-gateway", ce, "A managed-DIA CE is managed on its own /32 loopback in Carrier Management.")
    now[0] = "active"

    # --- Inventory totals, the cross-connect and panel tenancy ---
    # --- Exchanges (K8): the IX is a provider with one AS, its peering LAN a
    # provider network, our port an IX Port circuit through the colo demarc ---
    as_of = date.fromisoformat(recipe["as_of"])
    exchanges = sorted({metro for metro, _, _ in ix_ports.values()})
    for port, (metro, key, live) in sorted(ix_ports.items()):
        now[0] = "active" if live else "decommissioning"
        router = port.rsplit("/if/", 1)[0]
        lan, provider = f"provider-network/ix/{metro}", f"provider/ix-{metro}"
        circuit(key, port, None, refs(router).get("site"), lan, provider, 10000000, None,
                account=f"provider-account/ix/{metro}", circuit_type="circuit-type/ix-port")
        ends_on = attrs(key).get("termination_date")
        if (IX_IPV4_NOTE not in str(attrs(key).get("comments")) or (ends_on is None) is not live or
                (ends_on is not None and str(ends_on) <= as_of.isoformat())):
            report("provider-exchange", key, "An exchange port declares its IPv6-only peering; only the relocated "
                   "exchange's withdrawn port carries a disconnect date after as_of.")
    now[0] = "active"
    for metro in exchanges:
        name, short = IXES[metro]
        provider, asn, lan = f"provider/ix-{metro}", f"asn/ix/{metro}", f"provider-network/ix/{metro}"
        if (attrs(provider).get("name") != name or refs(provider).get("asns") != [asn] or
                attrs(asn).get("asn") not in IX_ASNS or refs(asn).get("rir") != "rir/arin" or "tenant" in refs(asn) or
                attrs(asn).get("description") != _holder(name) or refs(lan).get("provider") != provider or
                attrs(lan).get("name") != f"{short} peering LAN" or IX_IPV4_NOTE not in str(attrs(lan).get("description")) or
                refs(f"provider-account/ix/{metro}").get("provider") != provider):
            report("provider-exchange", provider, "Each exchange is a provider holding its one route-server AS from the "
                   "exchange range, with its peering LAN and membership account; no tenant holds it.")
    if [attrs(f"asn/ix/{m}").get("asn") for m in exchanges].count(None) or len({attrs(f"asn/ix/{m}").get("asn") for m in exchanges}) != len(exchanges):
        report("provider-exchange", "plan", "Each exchange holds its own distinct AS number.")
    if exchanges and (kind("asn-range/exchanges") != "asn_range" or refs("asn-range/exchanges").get("rir") != "rir/arin" or
                      (attrs("asn-range/exchanges").get("start"), attrs("asn-range/exchanges").get("end")) != (IX_ASNS[0], IX_ASNS[-1])):
        report("provider-routing-registry", "asn-range/exchanges", "Exchange route-server AS numbers need their declared documentation range.")

    # --- Former customers (P0-6): tenant, account and one decommissioned circuit ---
    formers = recipe.get("former_customers") or []
    former_slots = reservations.get("provider-former-customers", {}) if formers else {}
    tenant_users = {value for obj in objects.values() if obj["kind"] not in ("circuit", "provider_account", "journal_entry")
                    for value in obj["refs"].values() if isinstance(value, str) and value.startswith("tenant/cust-")}
    for former in formers:
        key, slot = former.get("key"), former_slots.get(former.get("key")) if isinstance(former_slots, dict) else None
        tenant, account, circuit_key = f"tenant/cust-{key}", f"provider-account/customer/{key}", f"circuit/former/{key}"
        installed, ended = (str(attrs(circuit_key).get(f, "")) for f in ("install_date", "termination_date"))
        if (not _integer(slot, 0, 31) or kind(tenant) != "tenant" or refs(tenant).get("group") != "tenant-group/customers" or
                attrs(tenant).get("name") != former.get("name") or
                not str(attrs(tenant).get("description", "")).startswith("Former ") or
                not str(attrs(tenant).get("description", "")).endswith(f" customer of {recipe['name']}") or
                refs(account).get("provider") != "provider/operator" or attrs(account).get("account") != f"{code}-F{slot + 1:05d}" or
                kind(circuit_key) != "circuit" or attrs(circuit_key).get("status") != "decommissioned" or
                attrs(circuit_key).get("cid") != f"{code}-{CID_CODES.get(former.get('service'))}-{FORMER_ORDER_BASE + slot:05d}" or
                refs(circuit_key).get("provider") != "provider/operator" or refs(circuit_key).get("tenant") != tenant or
                refs(circuit_key).get("provider_account") != account or
                refs(circuit_key).get("type") != f"circuit-type/{former.get('service')}-access" or
                not str(former.get("start")) <= installed[:4] or not installed < ended <= as_of.isoformat() or
                child("circuit", circuit_key, "circuit_termination") or tenant in tenant_users):
            report("provider-former-customer", circuit_key, "A former customer keeps its tenant, closed account and one "
                   "decommissioned circuit with its service dates; no termination, site, device or address remains.")
    if isinstance(former_slots, dict) and set(former_slots) != {f.get("key") for f in formers}:
        report("provider-former-customer", "plan", "provider-former-customers holds exactly the recipe's former customers.")

    expected_circuits = (set(spans) | {f"circuit/customer/{t}" for t in targets} | {f"circuit/noc/{s}" for s in ("a", "b")} |
                         {f"circuit/transit/{s}" for s in ("a", "b")} | {f"circuit/oob/{pop}" for pop in pops} |
                         {key for _, key, _ in ix_ports.values()} | {f"circuit/former/{f.get('key')}" for f in formers})
    if by_kind["circuit"] != expected_circuits:
        report("provider-circuit-inventory", "plan", "Physical circuit inventory must exactly cover requested backbone, customer, "
               "NOC, external transit and cellular out-of-band services.")
    for value, count in cross_connects.items():
        if count > 1:
            report("provider-cross-connect", "plan", f"Cross-connect {value} is recorded on {count} terminations.")
    for (site, bay, position), count in mmr_positions.items():
        if count > 1:
            report("provider-cross-connect", site, f"Meet-me room panel {bay} port {position} carries {count} handoffs.")
    for panel, parties in landed.items():
        colo = refs(panel).get("device_type") == "hardware/demarc-panel"
        city = METROS[site_metros[str(refs(panel).get("site")).removeprefix("site/")]][0] if refs(panel).get("site") else ""
        if parties != {"carrier" if colo else "operator"} or (colo and refs(panel).get("tenant") != f"tenant/colo/{city.lower()}"):
            report("provider-colo-demarc", panel, "The colo's demarc panel, held by that metro's carrier hotel, lands only carrier "
                   "cross-connects; the operator's OSP panel lands only owned fibre.")
    # Every circuit end that is not marked connected has a complete path: its
    # cable reaches an active interface, through 1:1 panels where it lands on one.
    for term in sorted(by_kind["circuit_termination"]):
        if attrs(term).get("mark_connected") is True or kind(refs(term).get("termination")) == "provider_network":
            continue
        far, _ = walk(term)
        if kind(far) != "interface":
            report("provider-path-complete", term, "A local circuit end has a complete cable path to an interface; only a "
                   "marked-connected end or a carrier's remote end stops short.")
    # Visual Explorer's WAN map draws one arc per circuit with two site-scoped ends.
    arcs = sum(1 for key in by_kind["circuit"]
               if {kind(refs(t).get("termination")) for t in child("circuit", key, "circuit_termination")} == {"site"}
               and len(child("circuit", key, "circuit_termination")) == 2)
    if arcs != len(spans) + len(targets) + 2:
        report("provider-wan-arcs", "plan", f"{arcs} circuits end on two sites; the map needs exactly one arc per span, "
               f"access attachment and NOC link ({len(spans) + len(targets) + 2}).")

    # --- Cable policy: every PoP cable is labelled, typed, coloured, lengthed ---
    pop_sites = {f"site/pop-{pop}" for pop in pops}
    for cable in sorted(by_kind["cable"]):
        ends = [refs(cable).get(side) for side in ("a", "b")]
        here = {refs(refs(e).get("device")).get("site") or refs(refs(e).get("power_panel")).get("site") or
                (refs(e).get("termination") if kind(e) == "circuit_termination" else None) for e in ends}
        if here & pop_sites and any(attrs(cable).get(field) in (None, "") for field in CABLE_POLICY_FIELDS):
            report("provider-cable-policy", cable, "Every PoP cable carries a label, a medium, a colour by function and a length.")

    # --- Routing registry ---
    base = recipe["asn_base"]
    public_asns = ("asn/operator", "asn/transit-a", "asn/transit-b")
    l3_customers = [c for c in customers if c["service"] == "private-l3"]
    expected_asns = {f"asn/customer/{c['key']}": base + 256 + customer_slots[c["key"]] for c in l3_customers}
    expected_providers = {"provider/operator", *(f"provider/{label}" for label in CARRIER_NAMES), *(f"provider/ix-{m}" for m in exchanges)}
    exchange_asns = {f"asn/ix/{m}" for m in exchanges}
    if kind("provider-network/oob") != "provider_network" or refs("provider-network/oob").get("provider") != "provider/oob":
        report("provider-oob", "provider-network/oob", "The cellular carrier needs its own provider network as the far end of every out-of-band circuit.")
    if by_kind["provider"] != expected_providers or by_kind["asn"] != set(expected_asns) | set(public_asns) | exchange_asns:
        report("provider-routing-registry", "plan", "Provider and ASN inventories must match actual operator, transport, upstream "
               "and exchange and private-L3 customer identities.")
    if (kind("rir/private") != "rir" or attrs("rir/private").get("is_private") is not True or
            kind("asn-range/private") != "asn_range" or attrs("asn-range/private").get("start") != base or
            attrs("asn-range/private").get("end") != base + 1023 or refs("asn-range/private").get("rir") != "rir/private"):
        report("provider-routing-registry", "asn-range/private", "The estate must retain its complete aligned private 32-bit ASN reservation and private registry.")
    if (kind("rir/arin") != "rir" or attrs("rir/arin").get("name") != "ARIN" or attrs("rir/arin").get("is_private") is not False or
            kind("asn-range/arin") != "asn_range" or refs("asn-range/arin").get("rir") != "rir/arin" or
            (attrs("asn-range/arin").get("start"), attrs("asn-range/arin").get("end")) != (DOCUMENTATION_ASNS[0], DOCUMENTATION_ASNS[-1])):
        report("provider-routing-registry", "rir/arin", "The operator and upstream AS numbers need their public ARIN registry and documentation range.")
    holders = {"asn/operator": recipe["name"], "asn/transit-a": attrs("provider/transit-a").get("name", ""),
               "asn/transit-b": attrs("provider/transit-b").get("name", ""),
               **{f"asn/customer/{c['key']}": _name(c) for c in l3_customers}}
    for key, holder in holders.items():
        text = attrs(key).get("description")
        if not isinstance(text, str) or len(text) > ASN_TEXT or text != _holder(holder):
            report("provider-asn-text", key, f"An AS label names its holder in at most {ASN_TEXT} characters, cut on a word boundary.")
    for key, number in expected_asns.items():
        if (kind(key) != "asn" or attrs(key).get("asn") != number or refs(key).get("rir") != "rir/private" or
                refs(key).get("tenant") != f"tenant/cust-{key.rsplit('/', 1)[-1]}"):
            report("provider-asn", key, "Each private-L3 customer routing identity must retain its reserved private ASN, registry and customer tenant.")
    numbers = [attrs(key).get("asn") for key in public_asns]
    for key, number in zip(public_asns, numbers):
        if (kind(key) != "asn" or number not in DOCUMENTATION_ASNS or numbers.count(number) != 1 or refs(key).get("rir") != "rir/arin" or
                refs(key).get("tenant") != ("tenant" if key == "asn/operator" else None)):
            report("provider-asn", key, "The operator and each upstream hold a distinct documentation AS number under the public registry; "
                   "only the operator's carries the operator tenant.")
    for prefix in PUBLIC_AGGREGATES:
        key = f"aggregate/public/{prefix}"
        if kind(key) != "aggregate" or attrs(key).get("prefix") != prefix or refs(key).get("rir") != "rir/arin":
            report("provider-public-space", key, "Carrier-owned loopback, link and DIA space needs its public ARIN aggregate.")
    # Documentation containment: every operator-public address sits in an
    # operator aggregate; DIA only in its pools; nothing public escapes RFC 5737.
    aggregates = [ip_network(p) for p in PUBLIC_AGGREGATES]
    dia_space = [ip_network(p) for p in DIA_POOLS]
    for key, value in ipv4_addresses.items():
        if value.ip.is_private or value.ip in OOB_POOL or refs(key).get("vrf") is not None:
            continue
        upstream = any(value.network.subnet_of(p) for p in UPSTREAM_POOLS.values())
        if not upstream and not any(value.network.subnet_of(a) for a in aggregates):
            report("provider-public-space", key, "A public address must come from the operator's documentation aggregates or an upstream's assignment.")
        if str(key).startswith("ip/device/pop-") and "ae1." in key and refs(key).get("tenant", "").startswith("tenant/cust-") and not any(
                value.network.subnet_of(p) for p in dia_space):
            report("provider-public-space", key, "A DIA customer's public address comes from the DIA documentation pools.")
    for block in (*DIA_POOLS, str(DIA_LINK_POOL)):
        key = f"prefix/dia/pool/{block}"
        if attrs(key).get("prefix") != block or attrs(key).get("status") != "container" or refs(key).get("vrf"):
            report("provider-public-space", key, "Each DIA documentation pool is a global container.")
    first_words = Counter(str(attrs(key).get("name", "")).split(" ")[0] for key in
                          [f"provider/{label}" for label in CARRIER_NAMES] + sorted(by_kind["tenant"]))
    for label in CARRIER_NAMES:
        key = f"provider/{label}"
        if first_words[str(attrs(key).get("name", "")).split(" ")[0]] != 1:
            report("provider-carrier-identity", key, "A carrier's name must not echo another carrier's or a customer's.")
    operator_asn = attrs("asn/operator").get("asn")
    for provider, asn in (("provider/operator", "asn/operator"), ("provider/transit-a", "asn/transit-a"), ("provider/transit-b", "asn/transit-b")):
        if refs(provider).get("asns") != [asn]:
            report("provider-asn-consumer", provider, "Provider ASN association must refer to its own actual operator or upstream identity.")
    for site in expected_sites:
        sid = site.removeprefix("site/")
        customer = premises[sid][0] if sid in premises else None
        asn = (f"asn/customer/{customer['key']}" if customer and customer["service"] == "private-l3" else
               None if customer else "asn/operator")
        if refs(site).get("asns") != ([asn] if asn else None):
            report("provider-asn-consumer", site, "Site routing ownership references its private-L3 customer's or the operator's ASN; "
                   "a DIA or EPL premises runs no routing identity of its own.")
    hub_rt, spoke_rt = "route-target/management/hub", "route-target/management/spoke"
    if (kind(MANAGEMENT) != "vrf" or refs(MANAGEMENT).get("tenant") != "tenant" or attrs(MANAGEMENT).get("name") != "Carrier Management" or
            attrs(MANAGEMENT).get("enforce_unique") is not True or attrs(MANAGEMENT).get("rd") != f"{operator_asn}:{HUB_RT}" or
            refs(MANAGEMENT).get("import_targets") != [hub_rt, spoke_rt] or refs(MANAGEMENT).get("export_targets") != [hub_rt] or
            attrs(hub_rt).get("name") != f"{operator_asn}:{HUB_RT}" or attrs(spoke_rt).get("name") != f"{operator_asn}:{SPOKE_RT}" or
            kind("provider-network/operator") != "provider_network" or refs("provider-network/operator").get("provider") != "provider/operator"):
        report("provider-routing-domain", MANAGEMENT, "Carrier Management must import the CE spoke and its own hub target and export only the hub, "
               "with the operator's service network beside it.")
    if kind(OOB) != "vrf" or refs(OOB).get("tenant") != "tenant" or refs(OOB).get("import_targets") or refs(OOB).get("export_targets"):
        report("provider-routing-domain", OOB, "The out-of-band context is the cellular carrier's network: no route target joins it to the carrier.")
    expected_accounts = ({f"provider-account/provider/{label}" for label in CARRIER_NAMES} | {"provider-account/operator/fiber"} |
                         {f"provider-account/customer/{c['key']}" for c in [*customers, *formers]} |
                         {f"provider-account/ix/{metro}" for metro in exchanges})
    if any(refs(f"circuit/noc/{side}").get("provider") == "provider/operator" for side in ("a", "b")):
        expected_accounts.add("provider-account/operator/noc")
    for account in expected_accounts:
        provider = ("provider/operator" if account.startswith(("provider-account/operator/", "provider-account/customer/"))
                    else f"provider/ix-{account.rsplit('/', 1)[1]}" if account.startswith("provider-account/ix/")
                    else account.removeprefix("provider-account/"))
        if kind(account) != "provider_account" or refs(account).get("provider") != provider or "tenant" in refs(account):
            report("provider-account", account, "Procurement accounts must reference their actual provider; native accounts have no tenant field.")
    if by_kind["provider_account"] != expected_accounts:
        report("provider-account-inventory", "plan", "Provider accounts must belong to the actual transport, transit, NOC and customer service obligations.")

    # --- Services: private-L3 VPN membership and the EPL ---
    expected_vcs, expected_terms = set(), set()
    for customer in customers:
        key = customer["key"]
        account = f"provider-account/customer/{key}"
        if attrs(account).get("account") != f"{code}-C{customer_slots[key] + 1:05d}":
            report("provider-account", account, "A customer's billing account number follows its permanent onboarding slot.")
        tenant = f"tenant/cust-{key}"
        if customer["service"] == "epl":
            l2vpn = f"l2vpn/epl/{key}"
            terms = child("l2vpn", l2vpn, "l2vpn_termination")
            ends = {refs(t).get("assigned_object") for t in terms}
            want = {attachments[sid]["subif"] for sid, (c, _, _) in premises.items() if c["key"] == key}
            if (kind(l2vpn) != "l2vpn" or attrs(l2vpn).get("type") != "epl" or attrs(l2vpn).get("identifier") != EPL_VCID_BASE + vcids[key] or
                    refs(l2vpn).get("tenant") != tenant or len(terms) != 2 or ends != want or
                    len({refs(e).get("device") for e in ends}) != 2 or
                    attrs(l2vpn).get("status") != ("planned" if customer.get("status") == "planned" else "active")):
                report("provider-epl", l2vpn, "An EPL is one native epl L2VPN with its ledger VC-ID and exactly two terminations, "
                       "on the two ends' PE units.")
            continue
        if customer["service"] != "private-l3":
            continue
        vrf, target = f"vrf/customer/{key}", f"route-target/customer/{key}"
        vc = f"virtual-circuit/customer/{key}"
        if (attrs(vc).get("description") != f"{titleize(key)} private L3 VPN, hub at {titleize(customer['hub_pop'])}" or
                attrs(vc).get("comments") != VIRTUAL_CIRCUIT_NOTE):
            report("provider-scope-text", vc, "The service must name its customer and hub and point at its customer BGP peer group.")
        expected_vcs.add(vc)
        if (kind(tenant) != "tenant" or kind(vrf) != "vrf" or refs(vrf).get("tenant") != tenant or attrs(vrf).get("enforce_unique") is not True or
                attrs(vrf).get("rd") != f"{operator_asn}:{1001 + customer_slots[key]}" or
                refs(vrf).get("import_targets") != [target, hub_rt] or refs(vrf).get("export_targets") != [target, spoke_rt] or
                kind(target) != "route_target" or attrs(target).get("name") != f"{operator_asn}:{1001 + customer_slots[key]}" or refs(target).get("tenant") != tenant):
            report("provider-customer-routing", vrf, "Each private customer needs its own tenant VRF and exact symmetric reserved route target.")
        if (kind(vc) != "virtual_circuit" or attrs(vc).get("status") != ("planned" if customer.get("status") == "planned" else "active") or
                refs(vc).get("provider_network") != "provider-network/operator" or refs(vc).get("provider_account") != account or
                refs(vc).get("tenant") != tenant or refs(vc).get("type") != "virtual-circuit-type/private-l3"):
            report("provider-customer-service", vc, "The private-L3 service must belong to the correct customer, operator network and customer procurement account.")
        members = {sid for sid, (item, _, _) in premises.items() if item["key"] == key}
        hub_sid = f"ce-{key}-{customer['hub_pop']}-001"
        for target, (sid, ordinal) in targets.items():
            if sid not in members:
                continue
            now[0] = stages[sid]
            hub = sid == hub_sid
            port_name = "PrivateL3" if ordinal == 1 else "PrivateL3-2"
            ce = f"device/{sid}/edge-01"
            parent = f"{ce}/if/{catalog.get('edge' if hub else 'ce-small', {}).get('wan_ports', [None, None])[ordinal - 1]}"
            term, port = f"virtual-circuit-termination/{target}", f"{ce}/if/{port_name}"
            expected_terms.add(term)
            if attrs(port).get("description") != "Private L3 VPN attachment over the access circuit":
                report("provider-scope-text", port, "The virtual interface describes inventory membership over its access circuit.")
            if (kind(term) != "virtual_circuit_termination" or refs(term).get("virtual_circuit") != vc or refs(term).get("interface") != port or
                    attrs(term).get("role") != ("hub" if hub else "spoke") or attrs(port).get("type") != "virtual" or
                    refs(port).get("parent") != parent or refs(port).get("vrf") != vrf or not path_ok(parent) or
                    child("assigned_object", port, "ip_address")):
                report("provider-virtual-membership", term, "Each requested CE attachment needs exactly one virtual membership "
                       "(hub at the hub premises) over its actual customer handoff and customer VRF.")
            if not hub and stages[sid] == "active" and stages.get(hub_sid) == "active":
                flows[(first_attachment[sid], first_attachment[hub_sid])] += _site_peak(customer, premises[sid][1]) * 1000
    now[0] = "active"
    if by_kind["virtual_circuit"] != expected_vcs or by_kind["virtual_circuit_termination"] != expected_terms:
        report("provider-service-inventory", "plan", "Every private-L3 customer and attachment contributes exactly its service membership.")

    # --- Backbone connectivity and customer offered load ---
    for router in adjacency:
        adjacency[router].sort()
    if not _connected(adjacency):
        report("provider-backbone-connectivity", "plan", "All PEs must be connected through actual pair cables and complete two-sided spans.")
    for router in sorted(routers):
        if not _connected(adjacency, removed=router):
            report("provider-router-connectivity", router, "Remaining PEs must remain connected after this single router removal; attached single-homed customers are not protected.")
    for removed in sorted(capacity):
        if not _connected(adjacency, excluded=removed):
            report("provider-link-connectivity", removed, "The actual backbone must retain connectivity after each single pair-link or span removal.")
    for removed in (None, *sorted(spans)):
        loads = _loads(adjacency, flows, excluded=removed)
        if loads is None:
            report("provider-customer-route", removed or "plan", "The customer spoke-to-hub flow needs a complete PE path in normal operation and after each span loss.")
            continue
        for (link, origin, destination), load in loads.items():
            if Decimal(load) > Decimal(capacity[link]) * usable:
                report("provider-route-capacity", link, f"Customer spoke-to-hub flow {load/1000:g} Mbps from {origin} to {destination} "
                       f"exceeds purchased usable capacity after {removed or 'no span'} removal.")

    # --- One timeline: spans before a PoP's customers, customers in slot order ---
    as_of = date.fromisoformat(recipe["as_of"])

    def in_service(key):
        try:
            return date.fromisoformat(attrs(key).get("install_date"))
        except (TypeError, ValueError):
            return None

    first_span = {}
    for key, ends in spans.items():
        if (day := in_service(key)) is not None:
            for router in ends:
                first_span[pop_of(router)] = min(day, first_span.get(pop_of(router), day))
    pending = {f"circuit/customer/{t}" for t, (sid, _) in targets.items() if stages[sid] in ("planned", "provisioning")}
    for key in sorted(by_kind["circuit"]):
        if key in pending:
            if "install_date" in attrs(key):
                report("provider-timeline", key, "A circuit not yet in service has no install date.")
        elif (day := in_service(key)) is None or day > as_of:
            report("provider-timeline", key, "Every circuit in or leaving service needs a service date on or before as_of.")
    starts = defaultdict(list)
    for sid, (customer, pop, _) in premises.items():
        key = f"circuit/customer/{sid}"
        if (day := in_service(key)) is None:
            continue
        if pop not in first_span or day <= first_span[pop]:
            report("provider-timeline", key, "A customer circuit enters service after its PoP's first backbone span.")
        starts[customer["key"]].append((day, sid))
    # Onboarding (v0.18): each customer signs on its frozen ledger day, from a
    # year after founding to a week before as_of, starting with its hub (or
    # first) circuit; the book spreads over the years, with no clamp pile-up.
    for customer in customers:
        frozen = reservations.get(f"provider-timeline/onboard/{customer['key']}")
        signed = date.fromordinal(frozen["day"]) if isinstance(frozen, dict) and _integer(frozen.get("day"), 1, date.max.toordinal()) else None
        anchor = customer.get("hub_pop") or customer["sites"][0]["pop"]
        founded = date(as_of.year - 15, as_of.month, min(as_of.day, 28))
        if signed is None or not founded + timedelta(days=365) <= signed <= as_of - timedelta(days=7) or (
                starts[customer["key"]] and min(starts[customer["key"]]) != (signed, f"ce-{customer['key']}-{anchor}-001")):
            report("provider-timeline", f"provider-account/customer/{customer['key']}",
                   "A customer signs on its frozen onboarding day, inside the estate's history, starting with its hub "
                   "(or first) circuit.")
    years = Counter(day.year for days in starts.values() for day, _ in days)
    for year, count in sorted(years.items()):
        if sum(years.values()) >= ONBOARDING_MIN_BOOK and count > ONBOARDING_YEAR_SHARE * sum(years.values()):
            report("provider-timeline", "plan", f"{count} of {sum(years.values())} premises entered service in {year}: "
                   f"no year may hold more than {ONBOARDING_YEAR_SHARE:.0%} of the book.")

    # --- Geography: premises across the metro, on a street grid, apart ---
    placed = []
    for sid, (_, pop, _) in sorted(premises.items(), key=lambda item: allocations[item[0]]):
        here, there = site_points.get(f"site/{sid}"), site_points.get(f"site/pop-{pop}")
        if not here or not there or sid in recipe.get("site_names", {}):
            continue
        rivals = [site_points.get(f"site/pop-{q}") for q in pops if q != pop and pops[q]["metro"] == pops[pop]["metro"]
                  and allocations.get(f"pop-{q}", 1 << 62) < allocations[sid]]
        if _km(here, there) > PREMISES_KM or any(p and _km(here, p) <= _km(here, there) for p in rivals):
            report("provider-premises-geography", f"site/{sid}", "Customer premises lie in their serving PoP's area: within "
                   f"{PREMISES_KM} km and nearer it than any other same-metro PoP that existed when they were ordered.")
        if recipe.get("naming", "authored") == "authored" and any(_km(here, point) * 1000 < PREMISES_SPACING_M for point in placed):
            report("provider-premises-spacing", f"site/{sid}", f"Premises stand at least {PREMISES_SPACING_M} m apart, so "
                   "map pins and labels never stack.")
        placed.append(here)

    # --- Dual-homed means it: two carriers, or attachments into two PEs ---
    carriers, edges = defaultdict(set), defaultdict(set)
    for key in by_kind["circuit"]:
        if attrs(key).get("status") != "active" or refs(key).get("type") == "circuit-type/cellular-oob":
            continue
        for term in child("circuit", key, "circuit_termination"):
            site = refs(term).get("termination")
            if kind(site) != "site":
                continue
            if refs(key).get("provider") != "provider/operator":
                carriers[site].add(refs(key).get("provider"))
    for target, att in attachments.items():
        sid = targets[target][0]
        if attrs(f"circuit/customer/{target}").get("status") == "active":
            edges[f"site/{sid}"].add(f"device/pop-{att['pop']}/pe-{att['side']}")
    for side in ("a", "b"):
        if attrs(f"circuit/noc/{side}").get("status") == "active":
            edges["site/dc-01"].add(f"device/pop-{recipe[f'noc_pop_{side}']}/pe-{side}")
    dual = {site for site in expected_sites if len(carriers[site]) >= 2 or len(edges[site]) >= 2}
    tagged = {site for site in expected_sites if "tag/dual-homed" in (refs(site).get("tags") or [])}
    if tagged != dual and not (not tagged and kind("tag/dual-homed") is None and dual in (set(), expected_sites)):
        for site in sorted(tagged ^ dual):
            report("provider-dual-homed", site, "The dual-homed tag marks exactly the sites with active WAN access from two "
                   "different carriers or attachments homed on two different provider edges.")

    # --- BGP inventory: documentation records, never applied configuration ---
    def name_of(key):
        value = attrs(key).get("name")
        return value if isinstance(value, str) else ""

    if extra_bgp := {k for k in by_kind if k.startswith("bgp_")} - BGP_KINDS:
        report("provider-bgp-inventory", "plan", "Only routing policies, peer groups and sessions are modeled; "
               + ", ".join(sorted(extra_bgp)) + " would read as device configuration.")
    if by_kind["bgp_routing_policy"] != {f"bgp-routing-policy/{slug}" for slug in BGP_POLICIES}:
        report("provider-bgp-inventory", "plan", "The estate carries exactly the four authored named routing policies.")
    if by_kind["bgp_peer_group"] - {"bgp-peer-group/ix-route-servers"} != {f"bgp-peer-group/{slug}" for slug in BGP_GROUPS}:
        report("provider-bgp-inventory", "plan", "The estate carries exactly the three authored peer groups, plus the exchange group.")
    for slug, (name, weight, description) in BGP_POLICIES.items():
        key = f"bgp-routing-policy/{slug}"
        if kind(key) != "bgp_routing_policy" or attrs(key) != {"name": name, "weight": weight, "description": description} or refs(key):
            report("provider-bgp-policy", key, "Each named routing policy must retain its authored name, weight and "
                   "reference-intent description and carry no rule references.")
    for slug, (name, description, imports, exports, internal) in BGP_GROUPS.items():
        key = f"bgp-peer-group/{slug}"
        expected = {"local_as": "asn/operator"}
        if internal:
            expected["remote_as"] = "asn/operator"
        for field, policies in (("import_policies", imports), ("export_policies", exports)):
            if policies:
                expected[field] = [f"bgp-routing-policy/{p}" for p in policies]
        if kind(key) != "bgp_peer_group" or refs(key) != expected or attrs(key) != {"name": name, "description": description}:
            report("provider-bgp-group", key, "Each peer group must retain its authored name, the operator's own routing "
                   "identity and exactly its authored import/export policies.")
    reflector_pops = [ordered[0], next((pop for pop in ordered if pops[pop]["metro"] != pops[ordered[0]]["metro"]), ordered[0])]
    reflectors = [f"device/pop-{pop}/pe-a" for pop in reflector_pops]
    clients = [f"device/pop-{pop}/pe-{side}" for pop in ordered for side in ("a", "b")
               if f"device/pop-{pop}/pe-{side}" not in reflectors]
    families = (4, 6) if "ipv6_pool" in recipe else (4,)
    expected_sessions = {}

    def address_of(port, family):
        found = [key for key in child("assigned_object", l3(port), "ip_address")
                 if isinstance(attrs(key).get("address"), str) and (":" in attrs(key)["address"]) == (family == 6)]
        return found[0] if len(found) == 1 else None

    def peering(local, remote_label, remote_as, group, description, local_address,
                remote_address=None, remote_prefix=None, tenant=None, status="active"):
        expected = {"device": local, "site": refs(local).get("site"), "local_address": local_address,
                    "local_as": "asn/operator", "remote_as": remote_as, "peer_group": f"bgp-peer-group/{group}"}
        if remote_address is not None:
            expected["remote_address"] = remote_address
        else:
            expected["remote_prefix"] = remote_prefix
        if tenant is not None:
            expected["tenant"] = tenant
        return {"name": f"{name_of(local)} to {remote_label}", "status": status, "description": description}, expected

    for family in families:
        tail, label = ("", "") if family == 4 else ("/ipv6", " IPv6")

        def ibgp(local, remote, description):
            expected_sessions[f"bgp-session/ibgp/{local.removeprefix('device/')}/{remote.removeprefix('device/')}{tail}"] = peering(
                local, f"{name_of(remote)} iBGP{label}", "asn/operator", "ibgp-core", description,
                address_of(f"{local}/if/lo0.0", family), remote_address=address_of(f"{remote}/if/lo0.0", family))
        # Every iBGP peering is recorded from both ends, so each loopback in
        # the BGP view belongs to a named device (the mirror rows).
        for local, remote in ((reflectors[0], reflectors[1]), (reflectors[1], reflectors[0])):
            ibgp(local, remote, "Internal peering between the two backbone route reflectors")
        for client in clients:
            for reflector in reflectors:
                ibgp(client, reflector, "Route-reflector client peering to the backbone reflector at "
                     f"{name_of(refs(reflector).get('site'))}")
                ibgp(reflector, client, f"Route-reflector peering to client {name_of(client)}")
        for side, (router, port, provider, circuit_key) in sorted(transit_peerings.items()):
            local = address_of(port, family)
            network = ip_interface(attrs(local)["address"]).network if local else None
            candidates = prefixes_by_vrf_network[(CORE, str(network))]
            expected_sessions[f"bgp-session/transit/{side}{tail}"] = peering(
                router, f"{name_of(provider)} transit{label}", f"asn/transit-{side}", "transit",
                f"External transit peering over {attrs(circuit_key).get('cid')}", local,
                remote_prefix=candidates[0] if len(candidates) == 1 else None)
        for target, (router, subif, ce, wan, tenant, ckey, circuit_key, stage) in sorted(customer_peerings.items()):
            expected_sessions[f"bgp-session/customer/{target}{tail}"] = peering(
                router, f"{name_of(ce)} customer{label}", f"asn/customer/{ckey}", "customer",
                f"Private-L3 customer edge peering over {attrs(circuit_key).get('cid')}",
                address_of(subif, family), remote_address=address_of(wan, family), tenant=tenant, status=LIFE[stage]["bgp"])
    # Exchange route servers (K8): two sessions per in-service IX port, over
    # the IPv6-only peering LAN, to route servers sharing the exchange's AS.
    # The LAN, our member address and the route servers are the exchange's:
    # no tenant, and a route server's address binds no interface of ours.
    # Without ipv6_pool the port stays and the sessions are omitted.
    exchange_group = "bgp-peer-group/ix-route-servers"
    live_ports = sorted((port, metro, key) for port, (metro, key, live) in ix_ports.items() if live)
    for port, metro, key in live_ports if 6 in families else ():
        router = port.rsplit("/if/", 1)[0]
        member = address_of(port, 6)
        lan = ip_interface(attrs(member)["address"]).network if member else None
        lans = prefixes_by_vrf_network[(CORE, str(lan))] if lan else []
        if member is None or refs(member).get("tenant") or len(lans) != 1 or refs(lans[0]).get("tenant") or lan.prefixlen != 64:
            report("provider-exchange", key, "The exchange port holds one member address on the exchange's untenanted "
                   "IPv6 peering /64.")
        name, short = IXES[metro]
        for n in range(1, IX_ROUTE_SERVERS + 1):
            servers = list(ips_by_vrf_address[(CORE, 6, int(lan[n]))]) if lan else []
            server = servers[0] if len(servers) == 1 else None
            if server is not None and (refs(server).get("tenant") or refs(server).get("assigned_object") or
                                       attrs(server).get("description") != f"{short} route server {n}"):
                report("provider-exchange", server, "A route server's address is the exchange's: unassigned, untenanted "
                       "and named for its server.")
            expected_sessions[f"bgp-session/ix/{metro}/rs-{n}"] = peering(
                router, f"{short} route server {n} IPv6", f"asn/ix/{metro}", "ix-route-servers",
                f"Route-server peering on the {name} peering LAN over {attrs(key).get('cid')}", member, remote_address=server)
    want_group = 6 in families and bool(live_ports)
    if (exchange_group in by_kind["bgp_peer_group"]) is not want_group or (want_group and (
            refs(exchange_group) != {"local_as": "asn/operator"} or
            attrs(exchange_group) != {"name": "IX route servers", "description": "Route-server peerings on internet exchange peering LANs"})):
        report("provider-bgp-group", exchange_group, "The IX route servers group exists exactly when an addressed exchange "
               "port peers; it carries the operator's identity and no policies.")
    if by_kind["bgp_session"] != set(expected_sessions):
        report("provider-bgp-inventory", "plan", "Sessions must cover exactly the reflector pair and every client against both "
               "reflectors, each recorded from both ends, each transit handoff and each private-L3 attachment.")
    for key, (expected_attrs, expected_refs) in sorted(expected_sessions.items()):
        if None in expected_refs.values():
            report("provider-bgp-session", key, "The addresses, prefix and endpoints this peering record cites must resolve to exactly one real object each.")
            continue
        if kind(key) != "bgp_session" or attrs(key) != expected_attrs or refs(key) != expected_refs:
            report("provider-bgp-session", key, "Each peering record must be attributed from the actual loopback, handoff address, "
                   "routing identity and circuit it documents.")
    for key in sorted(k for kind_name in BGP_KINDS for k in by_kind[kind_name]):
        if extra := set(attrs(key)) - BGP_FIELDS[kind(key)]:
            report("provider-bgp-scope-text", key, "BGP records carry inventory fields only; "
                   + ", ".join(sorted(extra)) + " would read as configured or established session state.")

    # --- Scale ceiling ---
    if len(objects) > OBJECT_CEILING:
        report("provider-object-ceiling", "plan", f"{len(objects):,} objects exceed the reviewed {OBJECT_CEILING:,} ceiling.")

    # Racked PoP infrastructure runs on modeled A/B power; premises kit on
    # customer power the carrier does not inventory.
    findings.extend(validate_power(objects, catalog, sorted(infrastructure), children, peers, cable_of,
                                   poe_watts=poe_watts, optics_watts=optics_watts))
    findings.extend(validate_resolved(plan, catalog, sites={"site/dc-01"}, workloads=_workloads(len(premises), len(pops), tms=any(
                    refs(d).get("device_type") == "hardware/ddos-mitigation" for d in by_kind["device"])),
                    peak=recipe["noc_peak_mbps"], reserve=recipe["reserve_fraction"], strict_sites=False,
                    network_offsets=DC_OFFSETS, poe_watts=poe_watts, optics_watts=optics_watts))
    return findings


# The network lab (recipe ``discovery_lab``; built by estates/discovery_lab.py),
# restated here. It is a container lab documented beside the estate, so it is
# validated as its own closed slice and then removed before the production
# checks run: those see exactly the graph they would see with the lab off.
# Membership is decided from roles, references and addresses, never from meta,
# so a production object given the lab role must pass every lab obligation
# below instead of escaping the production checks.
LAB_ROLE = "role/lab-router"
LAB_POOL = ip_network("198.18.0.0/15")
LAB_ALIAS = "lab-router"


def discovery_lab(plan, catalog):
    """Return (lab findings, the production plan without the lab slice)."""
    findings = []

    def report(code, key, message):
        findings.append({"code": code, "object": key, "message": message})

    objects = {o["key"]: o for o in plan["objects"]}
    setting = plan.get("recipe", {}).get("discovery_lab")
    of = lambda kind: [o for o in objects.values() if o["kind"] == kind]
    devices = {o["key"] for o in of("device") if o["refs"].get("role") == LAB_ROLE}
    components = {o["key"] for o in objects.values() if o["refs"].get("device") in devices}
    interfaces = {k for k in components if objects[k]["kind"] == "interface"}
    owned = {o["key"] for o in objects.values() if o["kind"] in {"mac_address", "ip_address"}
             and o["refs"].get("assigned_object") in interfaces}
    cables = {o["key"] for o in of("cable") if {o["refs"].get("a"), o["refs"].get("b")} & interfaces}
    in_pool = lambda net: net.version == 4 and net.subnet_of(LAB_POOL)
    prefixes = {o["key"] for o in of("prefix") if in_pool(ip_network(o["attrs"]["prefix"], strict=False))}
    used = lambda field: {objects[d]["refs"].get(field) for d in devices} - {None}
    types, platforms, rooms, racks = used("device_type"), used("platform"), used("location"), used("rack")
    lab = devices | components | owned | cables | prefixes | types | platforms | rooms | racks
    lab |= {LAB_ROLE} & objects.keys()
    # A manufacturer leaves with the lab only when no production record uses it.
    makers = {objects.get(k, {}).get("refs", {}).get("manufacturer") for k in types | platforms} - {None} - {
        v for k, o in objects.items() if k not in lab for v in o["refs"].values() if isinstance(v, str)}
    lab |= makers
    production = {k: o for k, o in objects.items() if k not in lab}
    if bool(setting) != bool(lab):
        report("lab-recipe", "plan", "Network lab records exist exactly when recipe discovery_lab enables them.")
    if setting and len(devices) != setting.get("nodes"):
        report("lab-recipe", "plan", f"discovery_lab requests {setting.get('nodes')} lab routers; found {len(devices)}.")

    # Closed slice: no production record may reference a lab record. That also
    # catches a shared platform, room or rack and any BGP session, circuit or
    # VRF bound to a lab port. A manufacturer may be shared with production.
    for key, obj in sorted(production.items()):
        for value in obj["refs"].values():
            for target in value if isinstance(value, list) else [value]:
                if target in lab:
                    report("lab-isolation", key, f"Production record references network lab record {target}.")
    for key in sorted(lab - makers):
        if objects[key]["refs"].get("vrf") or objects[key]["kind"] in {
                "power_port", "power_outlet", "console_port", "module", "module_bay", "circuit_termination"}:
            report("lab-isolation", key, "Network lab records carry no VRF, circuit, modeled power, console or module.")
    for key in sorted(cables):
        if not {objects[key]["refs"].get(side) for side in ("a", "b")} <= interfaces:
            report("lab-isolation", key, "A network lab cable may only join two lab router ports.")

    # Addresses: only RFC 2544 benchmarking space, and that space only in the lab.
    leaves = [ip_network(objects[p]["attrs"]["prefix"]) for p in prefixes if objects[p]["attrs"].get("status") != "container"]
    for ip in of("ip_address"):
        address = ip_interface(ip["attrs"]["address"])
        if (ip["key"] in owned) != in_pool(address.network):
            report("lab-address", ip["key"], "Network lab addresses come from 198.18.0.0/15, and only the lab uses it.")

    # Hardware: what the SR Linux container reports, with every front-panel port.
    model = catalog["models"][LAB_ALIAS]
    panel = {p["name"]: p["type"] for p in model["interfaces"]}
    for device in sorted(devices):
        refs, attrs = objects[device]["refs"], objects[device]["attrs"]
        if (objects.get(refs.get("device_type"), {}).get("attrs", {}).get("model") != model["model"]
                or attrs.get("serial") != model["serial_format"]
                or objects.get(refs.get("platform"), {}).get("attrs", {}).get("name") != model["platform"]["name"]):
            report("lab-hardware", device, "Lab routers carry the model, serial and platform the SR Linux container reports.")
        mine = [objects[k] for k in interfaces if objects[k]["refs"]["device"] == device]
        physical = {i["attrs"]["name"]: i["attrs"].get("type") for i in mine if i["attrs"].get("type") != "virtual"}
        virtual = {i["attrs"]["name"]: i["refs"].get("parent") for i in mine if i["attrs"].get("type") == "virtual"}
        if physical != panel or any(name != "system0" and (not name.endswith(".0") or
                                    objects.get(parent, {}).get("attrs", {}).get("name") != name[:-2])
                                    for name, parent in virtual.items()):
            report("lab-hardware", device, "Lab router interfaces are the 7220 IXR-D2L front panel plus system0 and .0 subinterfaces.")
        macs = [objects.get(i["refs"].get("primary_mac_address"), {}).get("attrs", {}).get("mac_address")
                for i in mine if i["attrs"].get("type") != "virtual"]
        if any(not m or not int(m.split(":")[0], 16) & 2 for m in macs):
            report("lab-hardware", device, "Every lab router port carries its locally administered SR Linux MAC.")
        if any(not any(ip_interface(objects[ip]["attrs"]["address"]) in net for net in leaves)
               for ip in owned if objects[ip]["kind"] == "ip_address"
               and objects[objects[ip]["refs"]["assigned_object"]]["refs"]["device"] == device
               and ip_interface(objects[ip]["attrs"]["address"]).network.prefixlen < 32):
            report("lab-address", device, "Every lab router address sits in an active lab prefix.")

    # Placement: the NOC's Network Lab room, unracked — a container occupies
    # no rack unit, so a rack or U position would claim hardware.
    for room in sorted(rooms):
        if objects[room]["refs"].get("site") != "site/dc-01" or objects[room]["attrs"].get("name") != "Network Lab":
            report("lab-placement", room, "Lab routers stand in the NOC's Network Lab room.")
    for device in sorted(devices):
        if (objects[device]["refs"].get("rack") or objects[device]["attrs"].get("position") is not None
                or not objects[device]["refs"].get("location")):
            report("lab-placement", device, "A lab router is a container: located in the Network Lab room, never racked.")

    # Mirror: lab-<name> of the first PoP's PE pair plus backbone neighbours, wired
    # exactly as their routed /31 adjacencies in the production graph.
    by_name = {o["attrs"]["name"]: k for k, o in production.items() if o["kind"] == "device"}
    mirrors = {d: by_name.get(objects[d]["attrs"]["name"].removeprefix("lab-")) for d in devices}
    # Only an in-service PE is mirrored: never a decommissioning relic or a
    # planned or staged successor.
    pes = {k for k, o in production.items() if o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge"
           and o["attrs"].get("status") == "active"}
    order = plan.get("reservations", {}).get("provider-pop-order") or {"": 0}
    first = {k for k in pes if production[k]["refs"].get("site") == f"site/pop-{min(order, key=order.get)}"}
    ends = defaultdict(set)
    for ip in production.values():
        owner = production.get(ip["refs"].get("assigned_object"), {}) if ip["kind"] == "ip_address" else {}
        address = ip_interface(ip["attrs"]["address"]) if owner.get("kind") == "interface" else None
        if address and address.version == 4 and address.network.prefixlen == 31:
            ends[address.network].add(owner["refs"]["device"])
    mirrored = set(mirrors.values())
    adjacency = Counter(frozenset(pair) for pair in ends.values() if len(pair) == 2 and pair <= mirrored)
    if devices and (None in mirrored or not mirrored <= pes or not first <= mirrored
                    or any(not any(frozenset((m, f)) in adjacency for f in first) for m in mirrored - first)):
        report("lab-mirror", "plan", "Lab routers mirror the first PoP's PE pair and their backbone neighbours.")
    wired = Counter(frozenset(mirrors.get(objects[objects[c]["refs"][side]]["refs"]["device"]) for side in ("a", "b"))
                    for c in cables if {objects[c]["refs"].get(s) for s in ("a", "b")} <= interfaces)
    if devices and wired != adjacency:
        report("lab-mirror", "plan", "Lab cables follow exactly the mirrored routers' routed /31 adjacencies.")
    return findings, {**plan, "objects": [o for o in plan["objects"] if o["key"] not in lab]}
