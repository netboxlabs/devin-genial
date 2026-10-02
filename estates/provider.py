"""A finite private-L3 provider, composed from persistent physical attachments.

The authored traffic model is directed customer spoke-to-hub demand only.
No routing daemon, forwarding or carrier duct survey is simulated. The BGP
records `estates/bgp.py` attaches are documentation inventory in the same
sense: an intended peering is recorded, nothing is configured or established.
"""

from collections import Counter, defaultdict, deque
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
import ipaddress
import math
import re

from . import bgp, datacenter, discovery_lab, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation, trunk
from .model import DesignError, World, canonical, resolve_bank_recipe, resolve_demo
from .naming import bandwidth, port_speed, segment_purpose, titleize


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps",
          "naming", "site_names", "hardware", "tenancy"}
DEFAULT_POPS = [dict(key="chicago-west",metro="chicago"),dict(key="detroit-south",metro="detroit"),
                dict(key="cleveland-east",metro="cleveland")]
DEFAULT_CUSTOMERS = [dict(key="harbor-logistics",hub_pop="chicago-west",sites=[dict(pop=p["key"],count=1) for p in DEFAULT_POPS])]
CUSTOMER_DEFAULTS = dict(service="private-l3",site_peak_mbps=50,hub_commit_mbps=1000,lan_endpoints=4,status="active")
# Per-service recipe fields beyond key, sites, status and an optional authored
# display name. Private L3 keeps its original grammar (and defaults); DIA is one
# premises with a committed rate; an EPL is exactly two premises at two PoPs.
SERVICES = ("private-l3","dia","epl")
SERVICE_DEFAULTS = {"private-l3": dict(site_peak_mbps=50,hub_commit_mbps=1000,lan_endpoints=4,branch="branch"),
                    "dia": dict(commit_mbps=100,managed=False),
                    "epl": dict(rate_mbps=100)}
CIRCUIT_TYPE_NAMES = {"private-l3": "Private L3", "dia": "DIA", "epl": "EPL"}
SERVICE_LABELS = {"private-l3": "Private L3 VPN", "dia": "Dedicated internet access", "epl": "Ethernet private line"}
# Rates past the 1G handoff: an attachment whose rate times (1 + reserve)
# exceeds 1G takes the 10G NID tier, which the 10G handoff bounds.
LARGE_TIERS_MBPS = (2000,5000)
# Catalog aliases (catalog/README.md, regional-carrier footprint aliases).
NID_ALIAS, NID_10G_ALIAS, SMALL_CE_ALIAS, HUB_CE_ALIAS = "nid","nid-10g","ce-small","edge"
MPOE_RACK_TYPE = "mpoe-cabinet"
# Access aggregation: 40 customer UNIs per AGG, two AGGs per PoP; each home
# side's AGG-PE LAG is four 10G members.
UNI_PER_SIDE, LAG_MEMBERS, LAG_MEMBER_MBPS = 40, 4, 10000
ATTACHMENTS_PER_POP = 2*UNI_PER_SIDE
# Per-PoP service VLANs (L3/DIA C-VLAN, EPL S-VLAN), one per attachment.
SERVICE_VLAN_BASE = 1001
# EPL virtual-circuit identifiers: an append-only ledger from 10001.
EPL_VCID_BASE = 10001
# Recipe lifecycle: a customer may be onboarding ("planned"); a premises entry
# may also be "planned" (provisioning under an active customer) or
# "decommissioning". Each premises lifecycle sets every record it owns.
CUSTOMER_STATUSES = ("active","planned")
# Growth may only move a premises forward through its lifecycle.
FORWARD = {("planned","provisioning"),("planned","active"),("provisioning","active"),("active","decommissioning")}
FORWARD_TEXT = "planned to provisioning or active, provisioning to active, and active to decommissioning"
ENTRY_STATUSES = ("active","planned","decommissioning")
LIFECYCLE = {
    "planned": dict(site="planned",device="planned",circuit="planned",cable="planned",rack="planned",
                    feed="planned",ip="reserved",prefix="reserved",vlan="reserved",bgp="planned"),
    "provisioning": dict(site="staging",device="staged",circuit="provisioning",cable="planned",rack="planned",
                         feed="planned",ip="reserved",prefix="reserved",vlan="reserved",bgp="planned"),
    "decommissioning": dict(site="decommissioning",device="decommissioning",circuit="deprovisioning",cable="decommissioning",
                            rack="deprecated",feed="active",ip="deprecated",prefix="deprecated",vlan="deprecated",bgp="offline"),
}
CAPACITY_SCOPE = ("Directed customer spoke-to-hub offered load under normal operation and loss of one inter-PoP span. "
                  "Return, arbitrary peer-to-peer, NOC and external-transit traffic are excluded; this is not total backbone capacity. "
                  "NOC peak tests only local purchased handoff headroom. Router and local-pair failures have a connectivity witness only, "
                  "not a traffic-capacity or single-homed customer-availability guarantee.")
# Third-party carriers: fictional names that collide with no customer, each with
# its own support domain and the circuit-ID shape it prints on an order.
CARRIERS = {"transport-a": ("Ridgeline Lightwave", "ridgeline-lightwave.example", "RLW-WAV-{:06d}", 6),
            "transport-b": ("Ironwood Fiber", "ironwood-fiber.example", "IWT/WAV/{:06d}", 6),
            "transit-a": ("Corvane Global IP", "corvane.example", "CVN-IPT-{:07d}", 7),
            "transit-b": ("Halyard Internet", "halyard-internet.example", "HAL-DIA-{:06d}", 6),
            "oob": ("Brightwire Business Broadband", "brightwire.example", "BWB-{:08d}", 8)}
# RFC 5398 documentation AS numbers for the operator and its two upstreams,
# held under an RIR displayed as ARIN. Customer VPN ASNs stay in the private
# 32-bit asn_base block, which keeps them namespace-separated on one target.
DOCUMENTATION_ASNS = (64496, 64511)
# Carrier-owned address space comes from RFC 5737 documentation IPv4; internal,
# management and customer VPN space stays in the private address_pool.
# Loopbacks take host .1 onward of their /24, never the network address.
# Re-packed (0.17) so customer DIA fits beside the backbone: the three RFC 5737
# blocks are the operator's aggregates; the backbone takes the first half of
# 192.0.2.0/24 and DIA every remaining block except the upstreams' /28s.
PUBLIC_POOLS = {"loopbacks": "192.0.2.0/27", "pair": "192.0.2.32/27", "backbone": "192.0.2.64/26"}
PUBLIC_AGGREGATES = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
# Customer DIA: 64 routed /29s, plus a /26 of 32 public /31s for managed-DIA
# CE links. Beyond either ceiling the build refuses.
DIA_POOLS = ("192.0.2.128/25", "198.51.100.0/25", "198.51.100.128/26", "203.0.113.0/24")
DIA_LINK_POOL = "203.0.113.192/26"
# Each upstream numbers its transit /31 from its own space: these blocks are
# recorded as the upstream's assignment, with no operator tenant.
UPSTREAM_POOLS = {"transit-a": "198.51.100.224/28", "transit-b": "198.51.100.240/28"}
PUBLIC_LINK_SCOPES = {"pair": "provider-pair-links", "backbone": "provider-span-links", "transit": "provider-transit-links"}
# The out-of-band broadband ISP hands each PoP console server a /30 from RFC
# 6598 shared address space; it lives in its own routing context, never the core.
OOB_POOL = "100.64.0.0/24"
# Host ordinals on the PoP management /26: switch SVI 1, console NET1 3, PE fxp0 4/5.
FXP0_HOSTS = (4, 5)
LOOPBACK_CAPACITY = 30


def loopback_network(slot):
    """PE loopback /32 for a permanent slot: host .1 onward, never .0 or .255."""
    return ipaddress.ip_network((int(ipaddress.ip_network(PUBLIC_POOLS["loopbacks"]).network_address)+slot+1,32))


def oob_network(slot):
    """The out-of-band ISP's /30 handoff for one PoP's console server."""
    return ipaddress.ip_network((int(ipaddress.ip_network(OOB_POOL).network_address)+4*slot,30))
# The backbone core (PE loopbacks, PoP pair and span /31s, transit /31s) is the
# global table, as on Junos inet.0. Management of PoPs, the NOC and every CE is
# the Carrier Management VRF; customer VRFs reach it through a hub-and-spoke
# management extranet: they export SPOKE and import HUB.
CORE_VRF = None
MANAGEMENT_VRF = "vrf/provider"
OOB_VRF = "vrf/oob"
MANAGEMENT_HUB_RT, MANAGEMENT_SPOKE_RT = 9000, 9001
# Leased inter-metro transport is a 100G wavelength handed off on the 100G
# port it lands on: the port-based service matches the port it terminates on.
TRANSPORT_TIERS_MBPS = (100000,)
# Authored span route length: great-circle distance times a fibre route factor.
ROUTE_FACTOR = 1.3
# One timeline: PoPs launch in backbone order, spans come up before a PoP
# serves customers, and customers onboard in their permanent slot order.
LAUNCH_EPOCH_DAYS, LAUNCH_STEP_DAYS, LAUNCH_CAP_DAYS = 2555, 45, 1825
# A premises lies in its serving PoP's service area: no farther than this from
# it, and no nearer any other same-metro PoP that existed when it was ordered.
PREMISES_KM = 25


def _key(value,label):
    if not isinstance(value,str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,19}",value):
        raise DesignError(f"{label} must be a lowercase key of 1–20 letters, digits or hyphens, starting with a letter")
    return value


def _integer(value,label,low,high):
    if type(value) is not int or not low <= value <= high:
        raise DesignError(f"{label} must be an integer from {low} through {high}")
    return value


def operator_code(name):
    """Short operator prefix for its own service IDs: Inland Lakes Fiber -> ILF."""
    words = re.findall(r"[A-Za-z]+", name)
    code = "".join(word[0] for word in words).upper()[:4]
    return code if len(code) >= 2 else (words[0][:3].upper() if words else "OPR")


def _hash(*parts):
    return int(sha256("/".join(map(str, parts)).encode()).hexdigest(), 16)


def documentation_asns(namespace):
    """Operator and upstream ASNs: three distinct RFC 5398 numbers per namespace."""
    low, high = DOCUMENTATION_ASNS
    h = _hash(namespace, "documentation-asns")
    return {label: low + (h + offset) % (high - low + 1)
            for label, offset in (("operator", 0), ("transit-a", 5), ("transit-b", 11))}


def carrier_number(namespace, label, ordinal):
    """A carrier order number: namespace-seeded, unique per carrier by ordinal."""
    digits = CARRIERS[label][3]
    low = 10 ** (digits - 1)
    return low + (_hash(namespace, label, "cid") + 37 * ordinal) % (9 * low)


def km(a, b):
    """Great-circle kilometres between two (lat, lon) points."""
    (la1, lo1), (la2, lo2) = ((math.radians(x), math.radians(y)) for x, y in (a, b))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def premises(recipe):
    return [(f"ce-{c['key']}-{entry['pop']}-{n:03}",c,entry["pop"],n)
            for c in recipe["customers"] for entry in c["sites"] for n in range(1,entry["count"]+1)]


def lifecycle(customer,pop):
    """A premises lifecycle: active, planned (onboarding customer), provisioning or decommissioning."""
    if customer.get("status","active") == "planned":
        return "planned"
    status = next(e.get("status","active") for e in customer["sites"] if e["pop"] == pop)
    return "provisioning" if status == "planned" else status


def premises_code(w,sid,customer):
    """Hostname stem for one customer premises: lakeshore-health-cle03.

    Customer key plus the site's own facility code (metro and permanent
    per-metro number), so the hostname matches the facility on the site record,
    is unique across the estate and frozen under growth. Without an authored
    facility code it falls back to the metro and address-allocation slot.
    Device names stay short identities scoped by site and tenant in Diode matching.
    """
    facility = w.obj(f"site/{sid}")["attrs"].get("facility","")
    if re.fullmatch(r"(?:CHI|DET|CLE|MIL)\d{2,}",facility):
        return f"{customer}-{facility.lower()}"
    return f"{customer}-{w.provider_metros[sid][:3]}{w.allocations[sid]:04}"


# Premises stems end in <metro3><digits>; the NOC is dc01. A PoP key of
# either shape could reuse another site's hostnames and DNS names.
_RESERVED_POP_KEY = re.compile(r"dc-?\d+|.*-(?:chi|det|cle|mil)\d{2,}")


def customer_name(customer):
    """A customer's authored display name, else its titled key."""
    return customer.get("name") or titleize(customer["key"])


def anchor_pop(customer):
    """The PoP that dates a customer's onboarding: its VPN hub, else its first premises."""
    return customer.get("hub_pop") or customer["sites"][0]["pop"]


def holder_label(name, limit=30):
    """An AS holder's name cut on a word boundary to fit graph labels (≤ 30)."""
    if len(name) <= limit:
        return name
    cut = name[:limit+1].rsplit(" ",1)[0].rstrip(" ,&-")
    return cut if cut else name[:limit]


def handoff_mbps(rate_mbps, reserve):
    """The physical handoff an attachment needs: 1G, else the 10G tier, else None."""
    need = Decimal(rate_mbps)*(1+Decimal(str(reserve)))
    return 1000 if need <= 1000 else 10000 if need <= 10000 else None


def dia_networks():
    """The 64 documentation /29s DIA allocates from, in permanent ledger order."""
    link = ipaddress.ip_network(DIA_LINK_POOL)
    return [net for pool in DIA_POOLS for net in ipaddress.ip_network(pool).subnets(new_prefix=29)
            if not net.overlaps(link)]


def site_peak(customer, pop):
    """A private-L3 premises' declared peak: its sites entry's, else the customer's."""
    return next(e for e in customer["sites"] if e["pop"] == pop).get("site_peak_mbps", customer["site_peak_mbps"])


def _resolve_private_l3(c, r, usable):
    key = c["key"]
    _integer(c["site_peak_mbps"],f"Customer {key} site_peak_mbps",1,800)
    _integer(c["lan_endpoints"],f"Customer {key} lan_endpoints",0,12)
    _integer(c["hub_commit_mbps"],f"Customer {key} hub_commit_mbps",1,1000)
    if not isinstance(c["branch"],str) or not re.fullmatch(r"[a-z][a-z ]{1,23}",c["branch"]):
        raise DesignError(f"Customer {key}: branch must be a lowercase noun of 2–24 letters, such as branch, store or clinic")
    if c["hub_commit_mbps"] not in r["wan_tiers_mbps"]:
        raise DesignError(f"Customer {key}: hub_commit_mbps must be one of wan_tiers_mbps")
    entries = c["sites"]
    for entry in entries:
        if site_peak(c,entry["pop"]) > 1000*usable:
            raise DesignError(f"Customer {key}: site peak plus reserve exceeds the 1G physical handoff")
    if not isinstance(c["hub_pop"],str) or c["hub_pop"] not in {e["pop"] for e in entries}:
        raise DesignError(f"Customer {key}: hub_pop must be one of its attachment PoPs")
    if c["status"] == "active" and next(e for e in entries if e["pop"] == c["hub_pop"]).get("status","active") != "active":
        raise DesignError(f"Customer {key}: the hub_pop entry of an active customer must stay active; "
                          "its spokes route through that hub")
    spokes = sum(site_peak(c,e["pop"])*(e["count"]-(e["pop"] == c["hub_pop"])) for e in entries)
    if spokes > c["hub_commit_mbps"]*usable:
        raise DesignError(f"Customer {key}: aggregate spoke demand plus reserve exceeds the fixed hub commitment; lower demand or rebaseline its purchase")


def resolve(raw):
    fields = {"profile","demo","topology","pops","customers","noc_pop_a","noc_pop_b","noc_peak_mbps","asn_base","discovery_lab"}
    if unknown := raw.keys()-(COMMON|fields):
        raise DesignError(f"Unknown provider fields: {', '.join(sorted(unknown))}; describe PoPs and customer service demand")
    base = dict(namespace="lakes-fiber",name="Great Lakes Fiber",address_pool="10.0.0.0/8")
    base.update({k:v for k,v in raw.items() if k in COMMON})
    checked = resolve_bank_recipe(base)
    r = {k:checked[k] for k in sorted(COMMON) if k in checked}
    demo = raw.get("demo", "baseline")
    r.update(profile="provider-backbone",demo=demo if demo == "provider-span-maintenance" else resolve_demo(demo),topology=raw.get("topology","incremental-mesh"))
    if r["topology"] != "incremental-mesh":
        raise DesignError("Provider topology supports incremental-mesh; other grammars need a reviewed physical design")
    if ipaddress.ip_network(r["address_pool"]).prefixlen > 12:
        raise DesignError("Provider address_pool must be an aligned private /8 through /12 for NOC, sites and infrastructure reservations")
    if r["reservation_user"]:
        raise DesignError("Provider rack-user reservations are not implemented; leave reservation_user empty")
    pops = raw.get("pops",DEFAULT_POPS)
    # Two PE loopbacks per PoP from the /27 documentation loopback block.
    if not isinstance(pops,list) or not 3 <= len(pops) <= LOOPBACK_CAPACITY//2:
        raise DesignError(f"pops must contain 3–{LOOPBACK_CAPACITY//2} keyed entries; the documentation loopback /27 "
                          "holds two PE loopbacks per PoP")
    values,codes,result = set(),set(),[]
    for p in pops:
        if not isinstance(p,dict) or p.keys() != {"key","metro"}:
            raise DesignError("Each PoP needs exactly key and metro")
        key = _key(p["key"],"PoP key")
        if _RESERVED_POP_KEY.fullmatch(key):
            raise DesignError(f"PoP key {key!r} is shaped like a NOC or customer premises hostname stem; choose a place name")
        if key in values or key.replace("-","") in codes:
            raise DesignError("PoP keys must be unique, including after removing hyphens for device names")
        if p["metro"] not in ("chicago","detroit","cleveland","milwaukee"):
            raise DesignError("PoP metro must be chicago, detroit, cleveland or milwaukee")
        values.add(key); codes.add(key.replace("-","")); result.append(dict(p))
    r["pops"] = sorted(result,key=lambda p:p["key"])
    if len({p["metro"] for p in result}) < 3:
        raise DesignError("The regional backbone requires at least three distinct authored metros")
    r["noc_pop_a"] = raw.get("noc_pop_a",r["pops"][0]["key"])
    r["noc_pop_b"] = raw.get("noc_pop_b",r["pops"][1]["key"])
    if any(not isinstance(r[k],str) or r[k] not in values for k in ("noc_pop_a","noc_pop_b")) or r["noc_pop_a"] == r["noc_pop_b"]:
        raise DesignError("NOC attachments need two distinct existing PoP keys")
    r["noc_peak_mbps"] = _integer(raw.get("noc_peak_mbps",100),"noc_peak_mbps",1,800)
    usable = 1-Decimal(str(r["reserve_fraction"]))
    if r["noc_peak_mbps"] > 1000*usable:
        raise DesignError("NOC peak plus reserve exceeds the fixed 1G local handoff")
    customer_rows = raw.get("customers",DEFAULT_CUSTOMERS)
    if not isinstance(customer_rows,list) or not 1 <= len(customer_rows) <= 256:
        raise DesignError("customers must contain 1–256 keyed entries")
    keys,names,result,attachment_count,site_ids = set(),set(),[],Counter(),set()
    for raw_customer in customer_rows:
        service = raw_customer.get("service","private-l3") if isinstance(raw_customer,dict) else None
        if service not in SERVICES:
            raise DesignError(f"Customer service must be one of {', '.join(SERVICES)}")
        required = {"key","sites"}|({"hub_pop"} if service == "private-l3" else set())
        allowed = required|{"service","status","name"}|SERVICE_DEFAULTS[service].keys()
        if not required <= raw_customer.keys() or raw_customer.keys()-allowed:
            raise DesignError(f"Each {service} customer needs {', '.join(sorted(required))}, with only these optional fields: "
                              f"{', '.join(sorted(allowed-required))}")
        c = dict(service=service,status="active")|SERVICE_DEFAULTS[service]|deepcopy(raw_customer)
        key = _key(c["key"],"Customer key")
        if key in keys:
            raise DesignError("Customer keys must be unique")
        keys.add(key)
        if "name" in c:
            if not isinstance(c["name"],str) or not 1 <= len(c["name"]) <= 60 or c["name"] != c["name"].strip():
                raise DesignError(f"Customer {key}: name must be 1–60 characters without surrounding spaces")
        if customer_name(c) in names:
            raise DesignError(f"Customer {key}: display name {customer_name(c)!r} is already used by another customer")
        names.add(customer_name(c))
        if c["status"] not in CUSTOMER_STATUSES:
            raise DesignError(f"Customer {key}: status must be one of {', '.join(CUSTOMER_STATUSES)}")
        entries = c["sites"]
        bounds = {"private-l3": (2,len(pops)), "dia": (1,1), "epl": (2,2)}[service]
        if not isinstance(entries,list) or not bounds[0] <= len(entries) <= bounds[1]:
            raise DesignError({"private-l3": f"Customer {key}: sites must span at least two distinct modeled PoPs",
                               "dia": f"Customer {key}: a DIA customer has exactly one sites entry (one premises)",
                               "epl": f"Customer {key}: an EPL has exactly two sites entries, one per end at two distinct PoPs"}[service])
        seen = set()
        entry_fields = {"pop","count","status"}|({"site_peak_mbps"} if service == "private-l3" else set())
        for entry in entries:
            if not isinstance(entry,dict) or entry.keys()-entry_fields or "pop" not in entry:
                raise DesignError(f"Customer {key}: each sites entry requires pop, with only {', '.join(sorted(entry_fields-{'pop'}))} optional")
            if entry.get("status","active") not in ENTRY_STATUSES:
                raise DesignError(f"Customer {key}: a sites entry status must be one of {', '.join(ENTRY_STATUSES)}")
            if c["status"] == "planned" and entry.get("status","planned") != "planned":
                raise DesignError(f"Customer {key}: every premises of a planned customer is planned")
            pop = entry["pop"]
            if not isinstance(pop,str) or pop not in values or pop in seen:
                raise DesignError(f"Customer {key}: attachment PoPs must be unique known keys")
            seen.add(pop)
            entry["count"] = _integer(entry.get("count",1),f"Customer {key} site count",1,12 if service == "private-l3" else 1)
            if "site_peak_mbps" in entry:
                _integer(entry["site_peak_mbps"],f"Customer {key} {pop} site_peak_mbps",1,800)
            # A dual-homed hub takes a second aggregation attachment.
            attachment_count[pop] += entry["count"]+(service == "private-l3" and pop == c["hub_pop"])
            for n in range(1,entry["count"]+1):
                sid = f"ce-{key}-{pop}-{n:03}"
                if sid in site_ids:
                    raise DesignError("Composed customer site identities collide; choose unambiguous customer and PoP keys")
                site_ids.add(sid)
        c["sites"] = sorted(entries,key=lambda item:item["pop"])
        if service == "private-l3":
            _resolve_private_l3(c,r,usable)
        else:
            rate_field = "commit_mbps" if service == "dia" else "rate_mbps"
            rate = c[rate_field]
            if type(rate) is not int or rate not in (*r["wan_tiers_mbps"],*LARGE_TIERS_MBPS):
                raise DesignError(f"Customer {key}: {rate_field} must be one of wan_tiers_mbps or {', '.join(map(str,LARGE_TIERS_MBPS))}")
            if handoff_mbps(rate,r["reserve_fraction"]) is None:
                raise DesignError(f"Customer {key}: {rate_field} plus reserve exceeds the 10G handoff")
            if service == "dia":
                if type(c["managed"]) is not bool:
                    raise DesignError(f"Customer {key}: managed must be true or false")
                if c["managed"] and handoff_mbps(rate,r["reserve_fraction"]) > 1000:
                    raise DesignError(f"Customer {key}: managed DIA plus reserve exceeds the small CE's 1G handoff; "
                                      "large DIA is unmanaged on the 10G NID tier")
        result.append(c)
    over = {pop:count for pop,count in attachment_count.items() if count > ATTACHMENTS_PER_POP}
    if over:
        raise DesignError(f"Customer demand exceeds {ATTACHMENTS_PER_POP} aggregation attachments ({UNI_PER_SIDE} UNIs per AGG) at "
                          f"{', '.join(sorted(over))}; add a PoP (the MX304 hub/access design is the reviewed growth path)")
    dia = [c for c in result if c["service"] == "dia"]
    if len(dia) > len(dia_networks()):
        raise DesignError(f"{len(dia)} DIA customers exceed the {len(dia_networks())} documentation /29s; rebaseline a larger public block")
    if sum(c["managed"] for c in dia) > ipaddress.ip_network(DIA_LINK_POOL).num_addresses//2:
        raise DesignError("Managed DIA customers exceed the 32 public /31 CE links; rebaseline a larger public block")
    # Recipe order is onboarding order: it numbers customer slots, ASNs, RDs
    # and billing accounts, and dates each customer's first circuit.
    r["customers"] = result
    blocks = (4294967294-4200000000+1)//1024
    default_asn = 4200000000+(int.from_bytes(sha256(f"{r['namespace']}/provider-asn-block".encode()).digest()[:8],"big")%blocks)*1024
    r["asn_base"] = _integer(raw.get("asn_base",default_asn),"asn_base",4200000000,4294967294-1023)
    if (r["asn_base"]-4200000000)%1024:
        raise DesignError("asn_base must start a 1024-number block aligned from 4200000000; target global collision preflight is still required")
    r["discovery_lab"] = discovery_lab.resolve(raw.get("discovery_lab"))
    return r


def workloads(recipe):
    count = len(premises(recipe)); result = []
    for slot,(key,number,size,cpus,memory,disk) in enumerate((
        ("identity",count,128,4,8192,100000),("dns",len(recipe["pops"]),16,2,4096,40000),
        ("monitoring",len(recipe["pops"]),16,4,16384,200000),("provisioning",count,128,4,8192,100000))):
        listeners = [dict(key="",name=key,protocol="tcp",ports=[53 if key == "dns" else 443])]
        if key == "dns": listeners.append(dict(key="udp",name="dns-udp",protocol="udp",ports=[53]))
        if key == "identity": listeners.append(dict(key="radius",name="radius",protocol="udp",ports=[1812,1813]))
        result.append(dict(key=key,slot=slot,instances=2*max(1,(number+size-1)//size),replicas=2,failure_domain="rack",
            network="applications",vcpus=cpus,memory_mb=memory,disk_mb=disk,listeners=listeners,
            criticality="tier-2" if key == "monitoring" else "tier-1",
            replica_description="independent host and rack lanes"))
    return result


def generate(recipe,previous=None):
    recipe = resolve(recipe)
    if previous is not None:
        if previous.get("recipe",{}).get("profile") != recipe["profile"]:
            raise DesignError("Changing profile requires a new baseline")
        reproduced = _generate(previous["recipe"],previous)
        if any(canonical(reproduced[k]) != canonical(previous[k]) for k in ("objects","contracts","reservations","allocations")):
            raise DesignError("Previous provider plan does not reproduce from its recipe and ledgers; use an intact frozen plan or rebaseline")
        old = previous["recipe"]
        for field in ("topology","noc_pop_a","noc_pop_b","noc_peak_mbps","asn_base","discovery_lab"):
            if old[field] != recipe[field]:
                raise DesignError(f"Changing {field} requires a new provider baseline")
        current_pops = {p["key"]:p for p in recipe["pops"]}
        if any(current_pops.get(p["key"]) != p for p in old["pops"]):
            raise DesignError("Removing, renaming or moving an existing PoP requires a new baseline")
        current = {c["key"]:c for c in recipe["customers"]}
        for before in old["customers"]:
            after = current.get(before["key"])
            if after is None:
                raise DesignError(f"Removing customer {before['key']} requires a new baseline")
            changed = [k for k in ("service","name","hub_pop","site_peak_mbps","hub_commit_mbps","branch","commit_mbps","managed","rate_mbps")
                       if after.get(k) != before.get(k)]
            changed += [f"{e['pop']} site_peak_mbps" for e in before["sites"]
                        if e.get("site_peak_mbps") != next((x.get("site_peak_mbps") for x in after["sites"] if x["pop"] == e["pop"]),None)]
            if changed:
                raise DesignError(
                    f"Customer {before['key']}: changing {', '.join(changed)} is a service, naming, hub or "
                    "purchased-bandwidth change and requires a new baseline")
            # Lifecycle moves forward only: planned premises go into service,
            # in-service premises may start decommissioning.
            for entry in before["sites"]:
                if entry["pop"] in {e["pop"] for e in after["sites"]}:
                    move = (lifecycle(before,entry["pop"]),lifecycle(after,entry["pop"]))
                    if move[0] != move[1] and move not in FORWARD:
                        raise DesignError(f"Customer {before['key']} at {entry['pop']}: moving premises from {move[0]} "
                                          f"to {move[1]} requires a new baseline; supported growth is {FORWARD_TEXT}")
            counts = {e["pop"]:e["count"] for e in after["sites"]}
            if after.get("lan_endpoints",0) < before.get("lan_endpoints",0) or any(counts.get(e["pop"],0) < e["count"] for e in before["sites"]):
                raise DesignError("Reducing customer premises or LAN endpoint demand requires a new baseline")
            if before.get("lan_endpoints") == 0 < after.get("lan_endpoints",0):
                raise DesignError(f"Customer {before['key']}: adding a managed LAN to CE-only premises moves CE management "
                                  "from its loopback to a switched segment and requires a new baseline")
    return _generate(recipe,previous)


def _registry(w):
    ns,r = w.recipe["namespace"],w.recipe
    code = operator_code(r["name"])
    w.add("rir","rir/private",dict(name="Private allocations",slug=f"{ns}-private",is_private=True))
    w.add("rir","rir/arin",dict(name="ARIN",slug=f"{ns}-arin",is_private=False))
    w.add("asn_range","asn-range/private",dict(name="Customer routing domains",slug=f"{ns}-routing",
          start=r["asn_base"],end=r["asn_base"]+1023,description="Private 32-bit ASNs for customer VPN sites"),{"rir":"rir/private"})
    w.add("asn_range","asn-range/arin",dict(name="Public routing domains",slug=f"{ns}-public-routing",
          start=DOCUMENTATION_ASNS[0],end=DOCUMENTATION_ASNS[1],description="Operator and upstream AS numbers"),{"rir":"rir/arin"})
    for prefix in PUBLIC_AGGREGATES:
        w.add("aggregate",f"aggregate/public/{prefix}",dict(prefix=prefix,description=f"{r['name']} backbone allocation"),
              {"rir":"rir/arin","tenant":"tenant"})
    for purpose,description in (("loopbacks","Backbone router loopbacks"),
                                ("pair","PoP router pair links"),("backbone","Inter-PoP span links")):
        w.add("prefix",f"prefix/public/{purpose}",dict(prefix=PUBLIC_POOLS[purpose],status="container",description=description),
              {"tenant":"tenant"})
    for label,block in UPSTREAM_POOLS.items():
        # The upstream's own assignment: no operator tenant, no aggregate.
        w.add("prefix",f"prefix/upstream/{label}",dict(prefix=block,status="container",
              description=f"{CARRIERS[label][0]} transit interconnect assignment"),{})
    w.add("prefix","prefix/oob/pool",dict(prefix=OOB_POOL,status="container",
          description=f"{CARRIERS['oob'][0]} shared-address out-of-band handoffs"),{"vrf":OOB_VRF})
    # NetBox's ASN model has no name: the visualization layer labels an AS node
    # from its description, so it must name the party that holds the AS.
    provider_names = (("operator",r["name"]),*((label,row[0]) for label,row in CARRIERS.items()))
    held_by = dict(provider_names)
    for key,number in documentation_asns(ns).items():
        # The holder's name alone, at most 30 characters: graph views truncate.
        w.add("asn",f"asn/{key}",dict(asn=number,description=holder_label(held_by[key])),
              {"rir":"rir/arin",**({"tenant":"tenant"} if key == "operator" else {})})
    for label,name in provider_names:
        refs = {"asns":[f"asn/{label}"]} if label == "operator" or label.startswith("transit-") else {}
        display = name
        # Retain ordinary branding; reserve thirteen characters for support-desk
        # names. A digest preserves identity when the full display exceeds87.
        if len(display) > 87:
            display = display[:70].rstrip()+"-"+sha256(display.encode()).hexdigest()[:16]
        attrs = dict(name=display,slug=f"{ns}-{label}")
        if label == "operator":
            attrs["comments"] = (f"Operator of the {r['name']} private L3 VPN service and its backbone, "
                                 "customer access and NOC circuits.")
        # Support desks answer from the provider's own mail domain.
        w.add("provider",f"provider/{label}",attrs,refs,{"support_domain":CARRIERS[label][1]} if label in CARRIERS else {})
        if label.startswith("transit-"):
            w.add("provider_network",f"provider-network/transit/{label[-1]}",dict(name=f"{name} network",
                  description=f"{name} IP transit network"),{"provider":f"provider/{label}"})
        if label == "oob":
            w.add("provider_network","provider-network/oob",dict(name=f"{name} network",
                  description=f"{name} access network for out-of-band console reachability"),{"provider":"provider/oob"})
        if label != "operator":
            service = ("transport","Wavelength transport between metros and NOC private lines") if label.startswith("transport-") else (
                ("out-of-band","Business broadband for PoP out-of-band console access") if label == "oob" else
                ("transit","IP transit at the core PoPs"))
            _account(w,f"provider-account/provider/{label}",f"provider/{label}",f"{name} {service[0]}",
                     str(10**9 + _hash(ns,label,"account") % (9*10**9)),service[1])
    w.add("provider_network","provider-network/operator",dict(name="Private L3",description="Routed private L3 VPN service across the backbone PoPs"),{"provider":"provider/operator"})
    _account(w,"provider-account/operator/noc","provider/operator","NOC access",f"{code}-INT-0001","NOC access circuits")
    _account(w,"provider-account/operator/fiber","provider/operator","Backbone fiber",f"{code}-INT-0002","Owned metro fiber between PoPs")
    # Native classification: one circuit type per service. "access" and
    # "out-of-band" remain only while the NOC and OOB builders still use them.
    services = {c["service"] for c in r["customers"]}
    for name,display in (("backbone","Backbone"),("dark-fiber","Dark Fiber"),("transit","Transit"),
                         ("access",titleize("access")),("out-of-band",titleize("out-of-band")),("cellular-oob","Cellular OOB"),
                         *((f"{s}-access",f"{CIRCUIT_TYPE_NAMES[s]} Access") for s in SERVICES if s in services)):
        w.add("circuit_type",f"circuit-type/{name}",dict(name=display,slug=f"{ns}-{name}"))
    # Customer DIA: the documentation pools are containers under the
    # operator's aggregates; each premises takes one /29, managed CEs one /31.
    for block in (*DIA_POOLS,DIA_LINK_POOL):
        w.add("prefix",f"prefix/dia/pool/{block}",dict(prefix=block,status="container",
              description="Managed internet CE links" if block == DIA_LINK_POOL else "Dedicated internet customer assignments"),
              {"tenant":"tenant"})
    w.add("virtual_circuit_type","virtual-circuit-type/private-l3",dict(name="Private L3",slug=f"{ns}-private-l3",color="e65100"))
    operator_asn = documentation_asns(ns)["operator"]
    # Management extranet: Carrier Management exports HUB and imports SPOKE;
    # every customer VRF exports SPOKE and imports HUB, so the NOC reaches CE
    # management without customers reaching each other.
    hub = w.add("route_target","route-target/management/hub",dict(name=f"{operator_asn}:{MANAGEMENT_HUB_RT}",
                description="Carrier management hub: exported by Carrier Management, imported by every customer VRF"),{"tenant":"tenant"})
    spoke = w.add("route_target","route-target/management/spoke",dict(name=f"{operator_asn}:{MANAGEMENT_SPOKE_RT}",
                  description="CE management spoke: exported by every customer VRF, imported by Carrier Management"),{"tenant":"tenant"})
    w.obj(MANAGEMENT_VRF)["attrs"].update(name="Carrier Management",rd=f"{operator_asn}:{MANAGEMENT_HUB_RT}",
        description="PoP, NOC and CE management; the backbone core is the global table")
    w.obj(MANAGEMENT_VRF)["refs"].update(import_targets=[hub,spoke],export_targets=[hub])
    w.add("vrf",OOB_VRF,dict(name="Out-of-Band Broadband",enforce_unique=True,
          description="Console-server handoffs on the out-of-band ISP, outside the carrier routing domain"),{"tenant":"tenant"})
    for c in r["customers"]:
        key = c["key"]; slot = w.reserve("provider-customers",key,256)
        customer = customer_name(c); label = SERVICE_LABELS[c["service"]]
        if c["service"] == "dia" and c["managed"]:
            label = "Managed internet access"
        tenant = w.add("tenant",f"tenant/cust-{key}",dict(name=customer,slug=f"{ns}-cust-{key}",description=f"{label} customer"))
        account = _account(w,f"provider-account/customer/{key}","provider/operator",customer,f"{code}-C{slot+1:05d}",
                           f"{label} billing for {customer}")
        if c["service"] == "epl":
            # The EPL's virtual-circuit identifier: append-only, never reused.
            vcid = EPL_VCID_BASE + w.reserve("provider-epl-vcid",key,1 << 20)
            w.add("l2vpn",f"l2vpn/epl/{key}",dict(name=f"{customer} EPL",slug=f"{ns}-epl-{key}",type="epl",identifier=vcid,
                  status="planned" if c["status"] == "planned" else "active",
                  description=f"{bandwidth(c['rate_mbps'])} Ethernet private line, VC-ID {vcid}"),dict(tenant=tenant))
        if c["service"] != "private-l3":
            continue
        w.add("asn",f"asn/customer/{key}",dict(asn=r["asn_base"]+256+slot,description=holder_label(customer)),{"rir":"rir/private","tenant":tenant})
        # The customer VPN's RD reuses its route target value, <operator ASN>:<n>,
        # numbered by the permanent onboarding slot and stable under growth.
        value = f"{operator_asn}:{1001+slot}"
        target = w.add("route_target",f"route-target/customer/{key}",dict(name=value,description=f"{customer} private L3 VPN import/export"),{"tenant":tenant})
        vrf = w.add("vrf",f"vrf/customer/{key}",dict(name=f"{customer} Private L3",rd=value,enforce_unique=True),
                    dict(tenant=tenant,import_targets=[target,hub],export_targets=[target,spoke]))
        w.add("virtual_circuit",f"virtual-circuit/customer/{key}",dict(cid=f"{code}-VPN-{slot+1:04d}",status="active",
              description=virtual_circuit_description(c),comments=VIRTUAL_CIRCUIT_NOTE),
              dict(provider_network="provider-network/operator",type="virtual-circuit-type/private-l3",tenant=tenant,provider_account=account))


# Each premises' CE-to-PE peering is emitted by estates/bgp.py as a session in
# the "Customer Private L3" peer group; this note points at those records.
VIRTUAL_CIRCUIT_NOTE = "Each premises' CE-to-PE peering is in the Customer Private L3 BGP peer group."


def virtual_circuit_description(customer):
    return f"{titleize(customer['key'])} private L3 VPN, hub at {titleize(customer['hub_pop'])}"


def _account(w,key,provider,name,number,description):
    # The account number is the matching identity; the name is the label NetBox
    # renders. Both are unique per provider.
    return w.add("provider_account",key,dict(name=name,account=number,description=description),dict(provider=provider))


def _site_network(site,role,prefixlen,offset,vid):
    w = site.w; container = w.site_network(site.id); vrf = site.vrf(role)
    reservation = f"prefix/{site.id}/reservation"
    if reservation not in w.objects:
        w.add("prefix",reservation,dict(prefix=str(container),status="container",description=f"{site.display} site block"),
              dict(tenant=site.tenant,scope_site=site.key))
    net = ipaddress.ip_network((int(container.network_address)+offset,prefixlen))
    # VLAN identity is (vid, group); the name only labels it inside its
    # site-scoped VLAN group, the way an operator names VLANs on a switch.
    vlan = w.add("vlan",f"vlan/{site.id}/{role}",dict(name=titleize(role),vid=vid,status="active",description=segment_purpose(role)),
                 dict(site=site.key,tenant=site.tenant))
    w.add("prefix",f"prefix/{site.id}/{role}",dict(prefix=str(net),status="active",description=f"{segment_purpose(role)} at {site.display}"),
          dict(vrf=vrf,tenant=site.tenant,scope_site=site.key,vlan=vlan))
    site.nets[role] = vlan,net


def _scoped(vrf,**refs):
    """References with the routing context; the global table carries no VRF."""
    return refs if vrf is None else dict(refs,vrf=vrf)


def _ip(w,port,net,host,vrf,tenant,primary=False):
    owner = w.obj(port)["refs"]["device"]
    # No description: the assigned interface already says what it is. DNS
    # names are settled once by operations._addresses.
    key = w.add("ip_address",f"ip/{port}",dict(address=f"{net[host]}/{net.prefixlen}",status="active"),
          _scoped(vrf,assigned_object=port,tenant=tenant))
    if vrf is not None:
        w.obj(port)["refs"]["vrf"] = vrf
    if primary: w.obj(owner)["refs"]["primary_ip4"] = key
    return key


def _link_prefix(w,key,vrf,tenant):
    family = "pair" if key.startswith("pair/") else key.split("/")[1] if key.startswith("circuit/") else None
    if family == "transit":
        # The upstream assigns the transit /31 from its own block.
        w.reserve(PUBLIC_LINK_SCOPES[family],key,2)
        net = ipaddress.ip_network((int(ipaddress.ip_network(UPSTREAM_POOLS[f"transit-{key[-1]}"]).network_address),31))
    elif family in PUBLIC_LINK_SCOPES:
        # Backbone and PoP-pair /31s are carrier-owned public space.
        pool = ipaddress.ip_network(PUBLIC_POOLS[family])
        slot = w.reserve(PUBLIC_LINK_SCOPES[family],key,pool.num_addresses//2)
        net = ipaddress.ip_network((int(pool.network_address)+2*slot,31))
    else:
        # Globally unique slots also prevent overlapping physical reservations across VRFs.
        slot = w.reserve("provider-link-prefixes",key,16384)
        base = int(w.pool.broadcast_address)-65535
        net = ipaddress.ip_network((base+2*slot,31))
    # ponytail: clipped at the native 200; only 100-character site_names overrides reach it.
    attrs = dict(prefix=str(net),status="active",description=_link_description(w,key)[:200])
    if key.startswith("circuit/transit/"):
        attrs["comments"] = (f"Assigned by {CARRIERS[f'transit-{key[-1]}'][0]} from its own address space; "
                             "the far end belongs to the upstream carrier.")
    w.add("prefix",f"prefix/link/{key}",attrs,_scoped(vrf,tenant=tenant))
    return net


def _link_description(w,key):
    """Name both ends of a /31 the way a link would be labelled in IPAM."""
    display = lambda target: w.obj(target)["attrs"]["name"]
    family,_,rest = key.partition("/")
    if family == "pair":
        return f"{display('site/'+rest)} PE-A to PE-B link"
    if family == "management":
        sid,side = rest.rsplit("/",1)
        return f"{display('site/'+sid)} management uplink {side.upper()}"
    kind = rest.split("/",1)[0]
    a,z = (termination_site(w.objects,f"{key}/{side}") for side in "AZ")
    if kind == "backbone":
        return f"Backbone span {display(a)} to {display(z)}"
    if kind == "transit":
        return f"Transit handoff to {display(w.obj(key)['refs']['provider'])} at {display(a)}"
    if kind == "noc":
        return f"NOC access link {rest.rsplit('/',1)[-1].upper()} to {display(z)}"
    return f"Access link {display(a)} to {display(z)}"


def termination_site(objects,term):
    """The site (or provider network) a circuit termination sits in, through its location."""
    target = objects[term]["refs"]["termination"]
    return objects[target]["refs"]["site"] if objects[target]["kind"] == "location" else target


def _routed_pair(w,key,a,b,vrf=CORE_VRF,tenant="tenant"):
    net = _link_prefix(w,key,vrf,tenant)
    _ip(w,a,net,0,vrf,tenant); _ip(w,b,net,1,vrf,tenant)


def _circuit(w,key,provider,account,kind,a_site,a_port,z_site,z_port,rate_mbps,tenant="tenant",handoff_mbps=None,*,
             cid,installed,description=None,distance_km=None):
    """One circuit; rate_mbps None is owned fibre with no purchased commitment."""
    handoff = handoff_mbps or rate_mbps
    # A circuit not yet in service (installed None) records no install date.
    attrs = dict(cid=cid,status="active",description=description or f"{bandwidth(rate_mbps)} {kind} committed on a {port_speed(handoff)} handoff")
    if installed is not None:
        attrs["install_date"] = installed.isoformat()
    if rate_mbps is not None:
        attrs["commit_rate"] = rate_mbps*1000
    if distance_km:
        attrs.update(distance=distance_km,distance_unit="km")
    refs = dict(provider=provider,type=f"circuit-type/{kind}",tenant=tenant)
    if account:
        refs["provider_account"] = account
    w.add("circuit",key,attrs,refs,dict(procurement=dict(cohort=f"provider-{kind}",handoff_mbps=handoff)))
    for side,site,port in (("A",a_site,a_port),("Z",z_site,z_port)):
        # A local handoff terminates on its site and names the room its
        # equipment stands in (PoP cage, premises or NOC equipment room) in
        # the description; a far end NetBox cannot see stays on the carrier's
        # provider network. Site scope, not the Location, because Visual
        # Explorer's WAN map resolves a circuit end only from a dcim.site
        # termination: Location-scoped circuits drew no arcs (docs/modeling.md).
        target = site.key if isinstance(site,Site) else site
        room = w.obj(w.obj(w.obj(port)["refs"]["device"])["refs"]["location"])["attrs"]["name"] if port else None
        attrs = dict(term_side=side,port_speed=handoff*1000,description=f"Local routed handoff, {room}" if port else "Upstream carrier handoff")
        if port and provider != "provider/operator" and site.id.startswith("pop-"):
            attrs.update(_cross_connect(w,f"{key}/{side}",port))
        term = w.add("circuit_termination",f"{key}/{side}",attrs,dict(circuit=key,termination=target))
        if port:
            site.cable(port,term,"cat6" if w.obj(port)["attrs"]["type"] == "1000base-t" else "smf")
            w.obj(port)["attrs"]["speed"] = handoff*1000
            site.contract["required_connections"].append(dict(a=port,b=term))
    return key


# A carrier handoff into a PoP crosses the carrier hotel's meet-me room: the
# hotel's cross-connect order, and for fibre the hotel's own meet-me-room
# patch position (its panel, not ours; NetBox records it as pp_info). The
# operator models no fibre enclosure of its own: one with no ports or cables
# would be a prop, and front/rear port mappings need the local plugin bridge.
MMR_POSITIONS, MMR_PANEL_PORTS = 48 * 99, 48


def _cross_connect(w,term,port):
    slot = w.reserve("provider-cross-connects",term,1 << 20)
    result = dict(xconnect_id=f"XC-{1000000+(_hash(w.recipe['namespace'],'xconnect')+7919*slot)%9000000:07d}")
    device = w.obj(port)["refs"]["device"]
    if w.obj(port)["attrs"]["type"] != "1000base-t" and re.fullmatch(r"device/pop-[^/]+/pe-[ab]",device):
        pop = w.obj(device)["refs"]["site"]
        # The hotel's panel numbering is its own: start each PoP at a stable
        # position inside it, then take consecutive ports by ledger order.
        n = _hash(w.recipe["namespace"],pop,"mmr")%(MMR_POSITIONS//2) + w.reserve(f"provider-mmr-positions/{pop}",term,MMR_POSITIONS//2)
        result["pp_info"] = f"Meet-me room panel MMR-{n//MMR_PANEL_PORTS+1:02d}, port {n%MMR_PANEL_PORTS+1}"
    return result


def _console_management(site,switch):
    equipment.enrich_site(site,demonstrations=False)
    vlan,_ = site.network("management")
    access_ports = site.w.hardware("access")["access_ports"]
    for device in list(site.devices):
        if site.w.obj(device)["refs"]["device_type"] != "hardware/console-server": continue
        mgmt = next(p["name"] for p in site.w.hardware("console-server")["interfaces"] if p.get("mgmt_only"))
        port = site.interface(device,mgmt); peer = site.interface(switch,access_ports[-2])
        site.cable(port,peer)
        for key in (port,peer):
            site.w.obj(key)["attrs"]["mode"] = "access"
            site.w.obj(key)["refs"]["untagged_vlan"] = vlan
        site.address(port,"management",host=3,primary=True,device=device)
        site.contract["required_connections"].append(dict(a=port,b=peer))


def _pop(w,item):
    site = Site(w,f"pop-{item['key']}","pop","Provider routing, local management and carrier handoffs",routing_domain=MANAGEMENT_VRF)
    # Hostname stem: the PoP key itself (chicago-cermak-pe-a), not the
    # hyphen-stripped site id (popchicagocermak-pe-a). resolve() keeps PoP keys
    # disjoint from the NOC and customer premises stems.
    site.code = item["key"]
    w.obj(site.key)["refs"]["asns"] = ["asn/operator"]
    _site_network(site,"management",26,0,10)
    routers = []
    for number,side in enumerate(("a","b")):
        device = site.device("provider-edge",f"pe-{side}","provider-edge",rack_domain=number)
        routers.append(device)
        for n in range(4):
            w.obj(site.interface(device,f"et-0/0/{n}"))["attrs"].update(enabled=n<3,speed=100000000)
        for n in range(8):
            w.obj(site.interface(device,f"xe-0/1/{n}"))["attrs"]["speed"] = 1000000 if n<6 else 10000000
        loop = w.add("interface",f"{device}/if/lo0",dict(name="lo0",type="virtual",enabled=True,
                     description="Backbone router identity loopback"),dict(device=device))
        net = loopback_network(w.reserve("provider-loopbacks",device,LOOPBACK_CAPACITY))
        w.add("prefix",f"prefix/loopback/{device}",dict(prefix=str(net),status="active",description=f"{w.obj(device)['attrs']['name']} router loopback"),dict(tenant="tenant"))
        _ip(w,loop,net,0,CORE_VRF,"tenant",True)
    a,b = [site.interface(d,"et-0/0/0") for d in routers]
    site.cable(a,b,"smf"); _routed_pair(w,f"pair/{site.id}",a,b)
    site.contract["required_connections"].append(dict(a=a,b=b))
    switch = site.device("access","mgmt-01","management")
    vi = site.virtual_interface(switch,"Vlan10","management"); site.address(vi,"management",host=1,primary=True,device=switch)
    vlan,_ = site.network("management")
    access_ports = w.hardware("access")["access_ports"]
    for i,router in enumerate(routers):
        a = site.interface(switch,w.hardware("access")["uplink_ports"][i]); b = site.interface(router,"xe-0/1/6")
        w.obj(a)["attrs"]["speed"] = 10000000
        site.cable(a,b,"smf"); _routed_pair(w,f"management/{site.id}/{'ab'[i]}",a,b,MANAGEMENT_VRF)
        site.contract["required_connections"].append(dict(a=a,b=b))
        # Dedicated out-of-band management: fxp0 on the PoP management LAN.
        fxp0,peer = site.interface(router,"fxp0"),site.interface(switch,access_ports[i])
        site.cable(fxp0,peer)
        for port in (fxp0,peer):
            w.obj(port)["attrs"]["mode"] = "access"; w.obj(port)["refs"]["untagged_vlan"] = vlan
        site.address(fxp0,"management",host=FXP0_HOSTS[i],device=router)
        site.contract["required_connections"].append(dict(a=fxp0,b=peer))
    _console_management(site,switch)
    site.contract.update(required_device_roles={"role/provider-edge":2,"role/management":1},
                         demand=dict(provider_routers=2,direct_attachment_capacity=12),management_mode="out-of-band and in-band")
    site.contract["assumptions"].extend([
        "Two MX204 chassis occupy separate racks. Exactly three 100G ports per chassis are enabled in the reviewed port-level mode; one is local peer and two are finite transport attachments.",
        "PE lo0 is the router identity in the global table. fxp0 is cabled to the PoP management switch and addressed in the Carrier Management /26; "
        "the switch reaches the PEs over two routed /31 uplinks in the same VRF, and the console server has an independent broadband out-of-band circuit.",
        "Installed PSU inventory comes from pinned sources; 320 W chassis planning allowance is synthetic, separate from a PSU output rating."])
    site.power()
    return site,routers


def _points(w):
    """PoP map positions: authored coordinates, else the metro centre (legacy naming)."""
    centres = {row[0].lower():(row[4],row[5]) for row in places.METROS}
    result = {}
    for p in w.recipe["pops"]:
        attrs = w.obj(f"site/pop-{p['key']}")["attrs"]
        result[p["key"]] = (attrs["latitude"],attrs["longitude"]) if "latitude" in attrs else centres[p["metro"]]
    return result


def metro_chain(metros):
    """Inter-metro adjacencies: the nearest-neighbour spanning tree of metro centres.

    For the four authored metros this is the lakeshore chain
    Milwaukee-Chicago-Detroit-Cleveland; no span crosses a lake.
    """
    centres = {row[0].lower():(row[4],row[5]) for row in places.METROS}
    group = {m:m for m in metros}
    def find(m):
        while group[m] != m: m = group[m]
        return m
    chain = []
    for _,a,b in sorted((km(centres[a],centres[b]),a,b) for a,b in combinations(sorted(metros),2)):
        if find(a) != find(b):
            group[find(a)] = find(b); chain.append((a,b))
    return chain


def span_key(a,b):
    """circuit/backbone/<pop>-<side>/<pop>-<side>: both ends are in the identity."""
    return f"circuit/backbone/{a[0]}-{a[1]}/{b[0]}-{b[1]}"


def parse_span(key):
    parts = key.split("/") if isinstance(key,str) else []
    ends = [tuple(part.rsplit("-",1)) for part in parts[2:]]
    if len(parts) != 4 or parts[:2] != ["circuit","backbone"] or any(len(e) != 2 or e[1] not in ("a","b") for e in ends):
        raise DesignError(f"{key!r} is not a backbone span identity")
    return ends


def span_carriers(spans,metro):
    """Same-metro spans are owned dark fiber; inter-metro spans alternate two carriers."""
    seen,result = Counter(),{}
    for key,a,b in spans:
        if metro[a[0]] == metro[b[0]]:
            result[key] = "operator"
        else:
            pair = frozenset((metro[a[0]],metro[b[0]]))
            result[key] = f"transport-{'ab'[seen[pair]%2]}"; seen[pair] += 1
    return result


def _plan_spans(w,points):
    """Ledger the backbone: geographic on a fresh build, append-only under growth.

    Fresh: a nearest-neighbour ring (or pair) inside each metro, then two spans
    per metro-chain adjacency on PoP-diverse ends. Growth: existing spans never
    move; each new PoP dual-homes to its nearest PoPs with a free transport
    port, in its own metro or an adjacent one.
    """
    order = w.reservations["provider-pop-order"]
    pops = sorted(w.recipe["pops"],key=lambda p:order[p["key"]])
    metro = {p["key"]:p["metro"] for p in pops}
    ledger = dict(w.reservations.get("provider-backbone-spans",{}))
    used = Counter()
    for key in ledger:
        for pop,side in parse_span(key):
            if pop not in metro:
                raise DesignError(f"{key}: span end {pop} is not a current PoP")
            used[(pop,side)] += 1
    free = lambda pop: used[(pop,"a")]+used[(pop,"b")] < 4
    def link(a,b):
        ends = []
        for pop in (a,b):
            side = min((s for s in "ab" if used[(pop,s)] < 2),key=lambda s:(used[(pop,s)],s),default=None)
            if side is None:
                raise DesignError(f"PoP {pop}: both PEs' two transport ports are in use; add a PoP or a reviewed transport layer")
            used[(pop,side)] += 1; ends.append((pop,side))
        w.reserve("provider-backbone-spans",span_key(*ends),256)
    chain = metro_chain({p["metro"] for p in pops})
    adjacent = {frozenset(edge) for edge in chain}
    if not ledger:
        members = defaultdict(list)
        for p in pops: members[p["metro"]].append(p["key"])
        for group in members.values():
            if len(group) == 2:
                link(*group)
            elif len(group) > 2:
                tour,rest = [group[0]],group[1:]
                while rest:
                    tour.append(min(rest,key=lambda q:(km(points[tour[-1]],points[q]),order[q]))); rest.remove(tour[-1])
                for i,pop in enumerate(tour): link(pop,tour[(i+1)%len(tour)])
        for x,y in chain:
            pairs = sorted((km(points[a],points[b]),order[a],order[b],a,b) for a in members[x] for b in members[y])
            first = next(p for p in pairs if free(p[3]) and free(p[4])); link(first[3],first[4])
            second = min((p for p in pairs if free(p[3]) and free(p[4])),key=lambda p:((p[3]==first[3])+(p[4]==first[4]),p[:3]))
            link(second[3],second[4])
    else:
        for key in ledger:
            a,b = (metro[pop] for pop,_ in parse_span(key))
            if a != b and frozenset((a,b)) not in adjacent:
                raise DesignError(f"{key}: a new metro between {a} and {b} reroutes existing spans; rebaseline the backbone")
        covered = {pop for key in ledger for pop,_ in parse_span(key)}
        for p in pops:
            if p["key"] in covered: continue
            joined = []
            for _ in "ab":
                ranked = sorted((q for q in covered if free(q) and (metro[q] == p["metro"] or frozenset((metro[q],p["metro"])) in adjacent)),
                                key=lambda q:(metro[q] != p["metro"],km(points[p["key"]],points[q]),order[q]))
                if not ranked:
                    raise DesignError(f"PoP {p['key']}: no PoP in its own or an adjacent metro has a free transport port; add a PoP or rebaseline")
                pick = next((q for q in ranked if q not in joined),ranked[0])
                link(p["key"],pick); joined.append(pick)
            covered.add(p["key"])
    spans = sorted(w.reservations["provider-backbone-spans"].items(),key=lambda item:item[1])
    return [(key,*parse_span(key)) for key,_ in spans]


def _launch(w,spans):
    """PoP launch dates: breadth-first along the backbone from the first PoP.

    The launch ledger is permanent, so growth appends launches and never
    re-dates an existing PoP.
    """
    order = w.reservations["provider-pop-order"]
    if not w.reservations.get("provider-pop-launch"):
        adjacency = defaultdict(set)
        for _,(a,_),(b,_) in spans: adjacency[a].add(b); adjacency[b].add(a)
        start = min(order,key=order.get); queue = deque([start]); w.reserve("provider-pop-launch",start,64)
        while queue:
            for peer in sorted(adjacency[queue.popleft()],key=order.get):
                if peer not in w.reservations["provider-pop-launch"]:
                    w.reserve("provider-pop-launch",peer,64); queue.append(peer)
    for pop in sorted(order,key=order.get): w.reserve("provider-pop-launch",pop,64)
    try:
        epoch = date.fromisoformat(w.recipe["as_of"])-timedelta(days=LAUNCH_EPOCH_DAYS)
        return {pop:epoch+timedelta(days=min(LAUNCH_STEP_DAYS*slot,LAUNCH_CAP_DAYS)+w.choose(pop,"pop-launch",range(15)))
                for pop,slot in w.reservations["provider-pop-launch"].items()}
    except OverflowError as exc:
        raise DesignError("as_of is too early for the authored provider build-out history") from exc


def _transport_text(ends):
    return f"100G wavelength, {ends}, handed off on the 100G port"


def _topology(w,pop_sites,points,spans,launch):
    ns,code = w.recipe["namespace"],operator_code(w.recipe["name"])
    metro = {p["key"]:p["metro"] for p in w.recipe["pops"]}
    launched = w.reservations["provider-pop-launch"]
    carriers = span_carriers(spans,metro)
    graph = []
    for site,routers in pop_sites.values(): graph.append((f"pair/{site.id}",routers[0],routers[1],100000))
    for ordinal,(key,a,b) in enumerate(spans):
        devices = [f"device/pop-{pop}/pe-{side}" for pop,side in (a,b)]
        sites = [pop_sites[pop][0] for pop,_ in (a,b)]
        ports = [sites[i].interface(d,f"et-0/0/{1+w.reserve(f'provider-transport-ports/{d}',key,2)}") for i,d in enumerate(devices)]
        later = max(a[0],b[0],key=launched.get)
        installed = launch[later]-timedelta(days=w.choose(key,"span-install",range(10,26)))
        distance = round(km(points[a[0]],points[b[0]])*ROUTE_FACTOR,1)
        ends = f"{sites[0].display} to {sites[1].display}"
        carrier = carriers[key]
        if carrier == "operator":
            _circuit(w,key,"provider/operator","provider-account/operator/fiber","dark-fiber",sites[0],ports[0],sites[1],ports[1],None,handoff_mbps=100000,
                     cid=f"{code}-DF-{ordinal+1:04d}",installed=installed,description=f"Owned dark fiber, {ends}, lit at 100G",distance_km=distance)
            rate = 100000
        else:
            rate = TRANSPORT_TIERS_MBPS[0]
            _circuit(w,key,f"provider/{carrier}",f"provider-account/provider/{carrier}","backbone",sites[0],ports[0],sites[1],ports[1],rate,
                     handoff_mbps=100000,cid=CARRIERS[carrier][2].format(carrier_number(ns,carrier,ordinal)),installed=installed,
                     description=_transport_text(ends),distance_km=distance)
        _routed_pair(w,key,*ports); graph.append((key,*devices,rate))
    return graph


def _onboarding(w,ready):
    """Customer onboarding dates in permanent slot order; the hub circuit comes first."""
    as_of = date.fromisoformat(w.recipe["as_of"])
    slots = w.reservations["provider-customers"]
    dates,previous = {},None
    for c in sorted(w.recipe["customers"],key=lambda c:slots[c["key"]]):
        day = ready[anchor_pop(c)]+timedelta(days=30)
        # ponytail: the first two dozen customers arrive months apart, later ones
        # days apart, and every date clamps a week before as_of, so a very long
        # list onboards its tail on one day; scale the gap by count if that matters.
        if previous is not None:
            gap = range(60,241) if slots[c["key"]] < 24 else range(3,15)
            day = max(day,previous+timedelta(days=w.choose(c["key"],"onboarding-gap",gap)))
        dates[c["key"]] = previous = min(day,as_of-timedelta(days=7))
    return dates


def serving_pops(w,sid,pop):
    """Same-metro PoPs that existed when this premises was ordered.

    Site allocation slots are one append-only sequence, and new PoPs are
    allocated before new premises, so an earlier slot means an earlier PoP.
    A PoP added later never pulls an existing premises out of its area.
    """
    metro = w.provider_metros[f"pop-{pop}"]
    return [p["key"] for p in w.recipe["pops"] if p["metro"] == metro and
            w.allocations[f"pop-{p['key']}"] < w.allocations[sid]]


def _premises_places(w,entries,points):
    """Place each premises in its serving PoP's service area.

    The recipe homes a premises on a PoP, so placement follows that PoP: an
    authored places anchor no more than PREMISES_KM from it and nearer it than
    any other same-metro PoP of the premises' time. Among those, a hash of the
    site id picks; a customer reuses an anchor only once every eligible one is
    taken. The point is a 150 m street-grid offset from the anchor, at least
    300 m from every earlier premises; all in allocation-slot order, so growth
    never moves or renames a site.
    Returns sid -> (display name, latitude, longitude, anchor).
    """
    overrides,result = w.recipe.get("site_names",{}),{}
    if w.recipe.get("naming","authored") != "authored":
        return result
    # One pass in permanent allocation-slot order across every customer: each
    # premises takes the first free 150 m street-grid point (places.grid_place)
    # at least 300 m from every earlier premises, so growth never moves a site.
    customers,taken,placed = {c["key"]:c for c in w.recipe["customers"]},defaultdict(Counter),[]
    for _,sid,key,pop in sorted((w.allocations[sid],sid,customer["key"],pop) for sid,customer,pop,_ in entries):
        if sid in overrides:
            continue
        c,mine = customers[key],taken[key]
        city = next(row[0] for row in places.METROS if row[0].lower() == w.provider_metros[sid])
        rivals = [q for q in serving_pops(w,sid,pop) if q != pop]
        eligible = [a for a in places.ANCHORS[city] if len(a) == 4 and km(points[pop],a[2:4]) <= PREMISES_KM and
                    all(km(points[pop],a[2:4]) < km(points[q],a[2:4]) for q in rivals)]
        if not eligible:
            raise DesignError(f"{sid}: no authored {city} anchor lies in the service area of PoP {pop} "
                              f"(within {PREMISES_KM} km and nearer it than any other {city} PoP)")
        found = places.grid_place(sid,sorted(eligible,key=lambda a:(mine[a[0][0]],_hash("premises",sid,a[0][0]))),placed)
        if found is None:
            raise DesignError(f"{sid}: every street-grid position {places.MIN_PREMISES_SPACING_M} m from earlier premises is taken "
                              f"in PoP {pop}'s service area; add {city} anchors (places.ANCHORS) or a PoP")
        anchor,point = found
        placed.append(point); mine[anchor[0][0]] += 1
        label = anchor[0][0] + (f" {mine[anchor[0][0]]}" if mine[anchor[0][0]] > 1 else "")
        result[sid] = (f"{customer_name(c)} {label}",*point,anchor)
    return result


def noc_carrier(side):
    """A NOC handoff into another metro leases a private line: A from transport A, B from B."""
    return f"transport-{side}"


# Carrier order-number ordinals for NOC private lines, clear of span ordinals.
NOC_ORDINAL = 4096


def _oob(w,site,pop,launched):
    """Independent console reachability: broadband from a third ISP on NET2."""
    console = f"device/{site.id}/console-01"
    net2 = [p["name"] for p in w.hardware("console-server")["interfaces"] if p.get("mgmt_only")][1]
    port = site.interface(console,net2)
    circuit = f"circuit/oob/{pop}"
    _circuit(w,circuit,"provider/oob","provider-account/provider/oob","out-of-band",site,port,"provider-network/oob",None,None,
             handoff_mbps=1000,cid=CARRIERS["oob"][2].format(carrier_number(w.recipe["namespace"],"oob",w.reservations["provider-pop-order"][pop])),
             installed=launched-timedelta(days=w.choose(circuit,"oob-install",range(20,41))),
             description=f"Business broadband for out-of-band console access at {site.display}; best effort, no committed rate")
    net = oob_network(w.reserve("provider-oob-links",pop,64))
    w.add("prefix",f"prefix/link/oob/{pop}",dict(prefix=str(net),status="active",
          description=f"{CARRIERS['oob'][0]} handoff to the {site.display} console server",
          comments="Assigned by the out-of-band ISP; its gateway address is not inventoried."),dict(vrf=OOB_VRF,tenant="tenant"))
    _ip(w,port,net,2,OOB_VRF,"tenant")


def ce_loopback(block):
    """A CE-only premises' management /32: host .1 of its site /24."""
    return ipaddress.ip_network((int(block.network_address)+1,32))


# A customer brings its own private LAN plan; the carrier records only the
# routed LAN prefix of each premises, in the customer's VRF. Plans repeat
# across customers on purpose (several enterprises number from 172.20/16), and
# each VRF enforces uniqueness only inside itself. A plan inside the carrier's
# own address_pool is skipped, so customer space never sits in its aggregate.
CUSTOMER_LAN_PLANS = ("172.20.0.0/16","192.168.0.0/16","172.24.0.0/16","10.10.0.0/16")


def customer_lan(w,customer,sid):
    """The routed /24 of one CE-only premises, from its customer's own plan.

    The plan follows the customer's permanent onboarding slot; the /24 follows
    an append-only per-customer ledger, so growth never renumbers a premises.
    """
    pool = ipaddress.ip_network(w.recipe["address_pool"])
    plans = [ipaddress.ip_network(p) for p in CUSTOMER_LAN_PLANS if not ipaddress.ip_network(p).overlaps(pool)]
    plan = plans[w.reservations["provider-customers"][customer["key"]]%len(plans)]
    slot = w.reserve(f"provider-customer-lans/{customer['key']}",sid,255)
    return ipaddress.ip_network((int(plan.network_address)+256*(slot+1),24))


CIRCUIT_PHRASES = {"private-l3": "private L3 VPN", "dia": "dedicated internet", "epl": "Ethernet private line"}


def _plant():
    """The PoP plant contract: WP-B's estates/fibre.py, else the WP-C stub."""
    from . import plant_stub
    try:
        from . import fibre
    except ImportError:
        return plant_stub
    return fibre if all(hasattr(fibre,f) for f in ("uni","land","lag","nid_management")) else plant_stub


def _service_port(w,pop_sites,pop,target):
    """A NOC handoff: PE-<side> xe-0/1/4 at its PoP. Customers land on UNIs (_attach)."""
    site,routers = pop_sites[pop]
    return site,site.interface(routers["ab".index(target.rsplit("/",1)[-1])],"xe-0/1/4")


def home_side(w,pop,target):
    """The alternating home-side ledger provider-agg-home/<pop>: A, B, A, ...

    A dual-homed hub reserves its two attachments back to back, so they always
    land on opposite sides. The ledger is append-only: growth never re-homes.
    """
    return "ab"[w.reserve(f"provider-agg-home/{pop}",target,ATTACHMENTS_PER_POP)%2]


def premises_description(customer,role,rate_mbps,other=None):
    """A premises' site description, from what it contains and how it is served."""
    tier = bandwidth(rate_mbps)
    if customer["service"] == "dia":
        return (f"Managed internet, {tier}; NID and carrier CE in the MPOE cabinet" if customer["managed"] else
                f"Dedicated internet demarcation, {tier}; customer firewall not inventoried")
    if customer["service"] == "epl":
        return f"Ethernet private line end to {other}, {tier}; customer Ethernet equipment not inventoried"
    if role == "hub":
        return f"Dual-homed VPN hub, {tier} commit; two NIDs and the CE in the MPOE cabinet"
    branch = customer["branch"]
    if customer["lan_endpoints"] > 0:
        return f"Managed {branch}, {tier} tier, with a carrier-managed office LAN"
    return f"Managed {branch}, {tier} tier; NID and CE in the MPOE cabinet"


def _mpoe_cabinet(w,site,device):
    """Turn the premises' first rack into the carrier's MPOE wall cabinet."""
    rack = w.obj(device)["refs"].get("rack")
    if rack and "rack_type" not in w.obj(rack)["meta"]:
        spec = w.catalog.get("rack_types",{}).get(MPOE_RACK_TYPE,{})
        w.obj(rack)["attrs"].update(name="MPOE-1",u_height=spec.get("u_height",9),
                                    description="Carrier wall-mount cabinet holding the demarcation and edge equipment")
        w.obj(rack)["refs"]["tenant"] = "tenant"
        w.obj(rack)["meta"]["rack_type"] = MPOE_RACK_TYPE


def _customer_power(w,device,where):
    for port in (f"{device}/power/{p['name']}" for p in w.hardware(w.obj(device)["meta"]["hardware"])["power_ports"]):
        w.obj(port)["attrs"].update(mark_connected=True,description=f"Customer-provided power {where}")


def _nid(w,site,label,alias,pop_site,side,racked,in_band=None):
    """A carrier NID at the premises, managed in-band from its home side's /25."""
    device = site.device(alias,label,"nid",racked=racked)
    node = w.obj(device)
    node["refs"]["tenant"] = "tenant"
    node["attrs"]["description"] = f"Carrier demarcation at {site.display}, homed on {titleize(pop_site.id.removeprefix('pop-'))} AGG-{side.upper()}"
    if racked:
        _mpoe_cabinet(w,site,device)
    else:
        node["refs"]["location"] = site.equipment_location
    _customer_power(w,device,"in the MPOE" if racked else "at the demarcation")
    vlan,net,vrf = _plant().nid_management(w,pop_site,side)
    port = f"{device}/if/Management"
    if port not in w.objects:
        w.add("interface",port,dict(name="Management",type="virtual",enabled=True),dict(device=device))
    w.obj(port)["attrs"]["description"] = in_band or "In-band management on the side's NID management VLAN"
    host = 2+w.reserve(f"provider-nid-hosts/{pop_site.id.removeprefix('pop-')}/{side}",device,net.num_addresses-3)
    _ip(w,port,net,host,vrf,"tenant",True)
    return device


def _attach(w,pop_sites,pop,target,nid,premises,c,*,rate,handoff,kind,cid,installed,distance,vrf=None):
    """One access attachment: NID NNI -> circuit -> AGG UNI -> home LAG -> PE ae1.<vid>.

    Returns the PE subinterface and the circuit; the service VLAN is tagged
    only on its home side's LAGs, with the side's NID management VLAN on the UNI.
    """
    plant,(site,routers) = _plant(),pop_sites[pop]
    side = home_side(w,pop,target)
    tenant,name = f"tenant/cust-{c['key']}",customer_name(c)
    uni = plant.uni(w,site,side,target)
    vid = SERVICE_VLAN_BASE+w.reserve(f"provider-service-vlans/{pop}",target,4001-SERVICE_VLAN_BASE)
    epl = c["service"] == "epl"
    label = "EPL S-VLAN" if epl else ("DIA" if c["service"] == "dia" else "VPN")
    vlan = w.add("vlan",f"vlan/{site.id}/{vid}/customer",dict(name=cid,vid=vid,status="active",
                 description=f"{name} {label} at {premises.display}",**({"qinq_role":"svlan"} if epl else {})),
                 dict(site=site.key,tenant=tenant))
    port = w.obj(uni)
    port["attrs"].update(speed=handoff*1000,description=f"{name} UNI, {premises.display}")
    if epl:
        port["attrs"]["mode"] = "q-in-q"; port["refs"]["qinq_svlan"] = vlan
    else:
        mgmt,_,_ = plant.nid_management(w,site,side)
        port["attrs"]["mode"] = "tagged"; port["refs"]["tagged_vlans"] = [vlan,mgmt]
    for role in ("agg","pe"):
        w.obj(plant.lag(w,site,side,role))["refs"].setdefault("tagged_vlans",[]).append(vlan)
    pe = routers["ab".index(side)]
    parent = plant.lag(w,site,side,"pe")
    pe_name = w.obj(parent)["attrs"]["name"]
    subif = w.add("interface",f"{pe}/if/{pe_name}.{vid}",dict(name=f"{pe_name}.{vid}",type="virtual",enabled=True,mode="access",
                  description=f"{name} {label}, {premises.display}; home PE-{side.upper()} via AGG-{side.upper()}"),
                  dict(device=pe,parent=parent,untagged_vlan=vlan,**({"vrf":vrf} if vrf else {})))
    circuit = f"circuit/customer/{target}"
    nni = premises.interface(nid,w.hardware(w.obj(nid)["meta"]["hardware"])["nni_port"])
    stage = lifecycle(c,pop)
    _circuit(w,circuit,"provider/operator",f"provider-account/customer/{c['key']}",kind,premises,nni,site,None,rate,tenant,
             handoff_mbps=handoff,cid=cid,installed=installed if stage in ("active","decommissioning") else None,distance_km=distance,
             description=f"{bandwidth(rate)} {CIRCUIT_PHRASES[c['service']]} committed on a {port_speed(handoff)} handoff")
    w.obj(f"{circuit}/A")["attrs"]["description"] = f"Customer demarcation, {w.obj(premises.equipment_location)['attrs']['name']}"
    w.obj(f"{circuit}/Z")["attrs"]["description"] = f"Access fibre landed on AGG-{side.upper()} at {site.display}"
    plant.land(w,site,f"{circuit}/Z",uni)
    w.provider_service_records[premises.key].extend([uni,subif,vlan])
    return dict(circuit=circuit,subif=subif,vlan=vlan,uni=uni,pe=pe,pop=pop,side=side)


def _customer(w,sid,c,pop,number,pop_sites,placed,installed):
    """One premises: NID(s), any carrier CE, access circuit(s) and the PoP-side service."""
    key=c["key"]; tenant=f"tenant/cust-{key}"; service=c["service"]
    hub = service == "private-l3" and pop == c["hub_pop"] and number == 1
    usable = 1-Decimal(str(w.recipe["reserve_fraction"]))
    if service == "private-l3":
        rate = c["hub_commit_mbps"] if hub else next(tier for tier in w.recipe["wan_tiers_mbps"] if tier*usable >= site_peak(c,pop))
        role,other = ("hub" if hub else "spoke"),None
    else:
        rate = c["commit_mbps"] if service == "dia" else c["rate_mbps"]
        role,other = service,next((titleize(e["pop"]) for e in c["sites"] if e["pop"] != pop),None)
    vrf = f"vrf/customer/{key}" if service == "private-l3" else None
    site = Site(w,sid,"customer",premises_description(c,role,rate,other),tenant=tenant,routing_domain=vrf or MANAGEMENT_VRF)
    if sid in placed:
        # Named and plotted where the premises is, not after its serving PoP.
        name,latitude,longitude,anchor = placed[sid]
        node = w.obj(site.key)
        lines = node["attrs"]["physical_address"].split("\n")
        lines[:2] = [places.street_address(sid,anchor,latitude,longitude),f"{anchor[1]}, {lines[1].rsplit(', ',1)[-1]}"]
        node["attrs"].update(name=name,latitude=latitude,longitude=longitude,physical_address="\n".join(lines))
    stage = lifecycle(c,pop)
    if stage in ("active","decommissioning"):
        w.obj(site.key)["meta"]["in_service"] = installed.isoformat()
    site.code = premises_code(w,sid,key)
    if service == "private-l3":
        w.obj(site.key)["refs"]["asns"] = [f"asn/customer/{key}"]
    pop_site = pop_sites[pop][0]
    here,there = (w.obj(k)["attrs"] for k in (site.key,pop_site.key))
    # The access tail's route length: premises to serving PoP times the route
    # factor, recorded on the circuit so optics read it rather than re-derive it.
    distance = (round(km((here["latitude"],here["longitude"]),(there["latitude"],there["longitude"]))*ROUTE_FACTOR,1)
                if "latitude" in here and "latitude" in there else None)
    code = operator_code(w.recipe["name"])
    kind = f"{service}-access"
    serial = w.allocations[sid]
    result = dict(site=site.key,customer=key,service=service,hub=hub,stage=stage,lags=[])
    w.provider_service_records[site.key]  # every premises owns a (possibly empty) record list
    if service == "private-l3":
        _private_l3(w,site,c,pop,pop_sites,hub,rate,installed,distance,code,kind,serial,result)
    elif service == "dia":
        _dia(w,site,c,pop,pop_sites,rate,installed,distance,code,kind,serial,result)
    else:
        _epl(w,site,c,pop,pop_sites,rate,installed,distance,code,kind,serial,result)
    if not (service == "private-l3" and c["lan_endpoints"] > 0):
        equipment.enrich_site(site,demonstrations=False)
    return result


def _private_l3(w,site,c,pop,pop_sites,hub,rate,installed,distance,code,kind,serial,result):
    key,sid=c["key"],site.id; tenant=f"tenant/cust-{key}"; vrf=f"vrf/customer/{key}"
    managed_lan = c["lan_endpoints"] > 0
    targets = [sid]+([f"{sid}/b"] if hub else [])
    sides = [home_side(w,pop,target) for target in targets]
    pop_site = pop_sites[pop][0]
    # Hub commitments stop at 1G (hub_commit_mbps), so every VPN NID is the 1G tier.
    alias,handoff = NID_ALIAS,1000
    nids = [_nid(w,site,f"nid-{i+1:02}",alias,pop_site,side,True) for i,side in enumerate(sides)]
    ce_alias = HUB_CE_ALIAS if hub else SMALL_CE_ALIAS
    edge = site.device(ce_alias,"edge-01","customer-edge")
    w.obj(edge)["meta"]["managed_service"] = True
    spec = w.hardware(ce_alias)
    lan_port = site.interface(edge,spec["lan_ports"][0])
    switch = None
    if managed_lan:
        _site_network(site,"clients",25,128,20)
        # Seats on a carrier-managed LAN: the CE trunks management and clients
        # to a same-room access switch, which serves the office pod.
        _site_network(site,"management",26,0,10)
        room = places.provider_office(site)
        switch = site.device("access","access-01","access")
        access_ports = w.hardware("access")["access_ports"]
        b = site.interface(switch,access_ports[-1])
        site.cable(lan_port,b); trunk(site,(lan_port,b),("management","clients")); site.contract["required_connections"].append(dict(a=lan_port,b=b))
        for role,name in (("management","Management"),("clients","Clients")):
            vi = site.virtual_interface(edge,name,role); w.obj(vi)["refs"]["parent"] = lan_port
            site.address(vi,role,host=1,primary=role=="management",device=edge)
        vi = site.virtual_interface(switch,"Vlan10","management"); site.address(vi,"management",host=2,primary=True,device=switch)
    _mpoe_cabinet(w,site,edge)
    _customer_power(w,edge,"in the MPOE")
    if not managed_lan:
        # CE only: the LAN port is a routed handoff into the customer's own
        # LAN, numbered from the customer's own address plan (customer_lan);
        # the CE is managed on a carrier loopback, exported to Carrier Management.
        lan = customer_lan(w,c,sid)
        w.obj(lan_port)["attrs"].update(description="Customer LAN handoff to customer-owned equipment",label="Customer LAN")
        w.add("prefix",f"prefix/{sid}/lan",dict(prefix=str(lan),status="active",description=f"{customer_name(c)} LAN at {site.display}",
              comments="Customer-assigned address space routed over the private L3 service."),
              dict(vrf=vrf,tenant=tenant,scope_site=site.key))
        _ip(w,lan_port,lan,1,vrf,tenant)
        net = ce_loopback(w.site_network(sid))
        w.add("prefix",f"prefix/{sid}/management",dict(prefix=str(net),status="active",description=f"CE management loopback at {site.display}"),
              dict(vrf=vrf,tenant=tenant,scope_site=site.key))
        loop = w.add("interface",f"{edge}/if/Management",dict(name="Management",type="virtual",enabled=True,
                     description="CE management loopback, exported to Carrier Management"),dict(device=edge))
        _ip(w,loop,net,0,vrf,tenant,True)
    panel = site.device("patch-panel","patch-01","patch-panel") if managed_lan and w.recipe["patching"] == "panels" else None
    for i in range(c["lan_endpoints"]):
        device = site.device("endpoint",f"pc-{i+1:03}","workstation",racked=False,
                             meta=dict(endpoint=True,network="clients",power_scope="local outlet"))
        places.provider_endpoint(site,device,room,i+1)
        source,target = site.interface(switch,access_ports[i]),site.interface(device,"eth0")
        vlan,_ = site.network("clients")
        for port in (source,target):
            w.obj(port)["attrs"]["mode"] = "access"; w.obj(port)["refs"]["untagged_vlan"] = vlan
        site.patch(switch,access_ports[i],device,"eth0",panel,i+1)
        site.address(target,"clients",primary=True,device=device)
    for i,(target,nid) in enumerate(zip(targets,nids)):
        uni = site.interface(nid,w.hardware(alias)["uni_port"])
        wan = site.interface(edge,spec["wan_ports"][i])
        site.cable(wan,uni)
        site.contract["required_connections"].append(dict(a=wan,b=uni))
        att = _attach(w,pop_sites,pop,target,nid,site,c,rate=rate,handoff=handoff,kind=kind,vrf=vrf,
                      cid=f"{code}-PL3-{serial:05d}{'-2' if i else ''}",installed=installed,distance=distance)
        _routed_pair(w,att["circuit"],wan,att["subif"],vrf,tenant)
        w.obj(att["circuit"])["meta"]["managed_service"] = True
        name = "PrivateL3" if not i else "PrivateL3-2"
        vi=w.add("interface",f"{edge}/if/{name}",dict(name=name,type="virtual",enabled=True,description="Private L3 VPN attachment over the access circuit"),
                 dict(device=edge,parent=wan,vrf=vrf))
        w.add("virtual_circuit_termination",f"virtual-circuit-termination/{target}",dict(role="hub" if hub else "spoke",description=f"{site.display} {'hub' if hub else 'spoke'}"),
              dict(virtual_circuit=f"virtual-circuit/customer/{key}",interface=vi))
        result["lags"].append((pop,att["side"],rate if hub else site_peak(c,pop)))
        if not i:
            result.update(router=att["pe"],peak_mbps=site_peak(c,pop))
    site.contract.update(required_device_roles={"role/customer-edge":1,"role/nid":len(nids),**({"role/access":1} if managed_lan else {})},
                         endpoint_count=c["lan_endpoints"],
                         demand=dict(lan_endpoints=c["lan_endpoints"],peak_mbps=site_peak(c,pop),hub=hub,commit_mbps=rate),access_hardware=w.hardware_alias("access"))
    site.contract["assumptions"].extend(["The hub has two NIDs, one per aggregation side, and two access circuits into both PEs of its PoP; a CE failure can still isolate it."
        if hub else "The private-L3 premises has one NID, one access circuit and one CE. A CE, NID, access-link, aggregation or home-PE failure can isolate it.",
        "Twelve fixed office positions and a same-room access switch preserve existing endpoint cables and addresses during growth. Customer LAN is wired; no wireless service is modeled."
        if managed_lan else "The CE hands the customer LAN to customer-owned equipment that is not inventoried; the carrier manages the NID and CE."])
    if managed_lan:
        _console_management(site,switch)
        _customer_power(w,switch,"in the MPOE")
    if managed_lan:
        site.contract["assumptions"].append("The MPOE cabinet, CE and office switch are on customer power; the building circuit is not inventoried.")
    else:
        site.contract["assumptions"].append("The MPOE wall cabinet is carrier-installed on customer power; the building circuit is not inventoried.")


def _dia(w,site,c,pop,pop_sites,rate,installed,distance,code,kind,serial,result):
    key,sid=c["key"],site.id; tenant=f"tenant/cust-{key}"
    managed = c["managed"]
    pop_site = pop_sites[pop][0]
    alias = NID_10G_ALIAS if handoff_mbps(rate,w.recipe["reserve_fraction"]) > 1000 else NID_ALIAS
    # The NID's home side is decided by the attachment ledger inside _attach;
    # read it first so the NID is managed from the same side.
    side = home_side(w,pop,sid)
    nid = _nid(w,site,"nid-01",alias,pop_site,side,managed)
    spec = w.hardware(alias)
    uni_name = spec["uni_port"]
    if handoff_mbps(rate,w.recipe["reserve_fraction"]) > 1000:
        # The 10G tier hands off on a 10G SFP+ port, not the copper UNI.
        uni_name = next(p["name"] for p in spec["interfaces"] if p["type"] == "10gbase-x-sfpp" and p["name"] != spec["nni_port"])
    uni = site.interface(nid,uni_name)
    att = _attach(w,pop_sites,pop,sid,nid,site,c,rate=rate,handoff=handoff_mbps(rate,w.recipe["reserve_fraction"]),kind=kind,cid=f"{code}-DIA-{serial:05d}",installed=installed,distance=distance)
    block = dia_networks()[w.reserve("provider-dia-29",sid,len(dia_networks()))]
    assignment = w.add("prefix",f"prefix/dia/{sid}",dict(prefix=str(block),status="active",
                       description=f"{customer_name(c)} internet assignment at {site.display}"),dict(tenant=tenant))
    w.provider_service_records[site.key].append(assignment)
    if managed:
        edge = site.device(SMALL_CE_ALIAS,"edge-01","customer-edge")
        w.obj(edge)["meta"]["managed_service"] = True
        w.obj(att["circuit"])["meta"]["managed_service"] = True
        _mpoe_cabinet(w,site,edge); _customer_power(w,edge,"in the MPOE")
        ce = w.hardware(SMALL_CE_ALIAS)
        wan,lan = site.interface(edge,ce["wan_ports"][0]),site.interface(edge,ce["lan_ports"][0])
        site.cable(wan,uni); site.contract["required_connections"].append(dict(a=wan,b=uni))
        # A public /31 joins PE and CE; the /29 is routed to the CE, which
        # holds its first address on the LAN handoff to the customer.
        link = ipaddress.ip_network((int(ipaddress.ip_network(DIA_LINK_POOL).network_address)+
                                     2*w.reserve("provider-dia-31",sid,ipaddress.ip_network(DIA_LINK_POOL).num_addresses//2),31))
        w.add("prefix",f"prefix/dia/link/{sid}",dict(prefix=str(link),status="active",
              description=f"Managed internet link {site.display} to {pop_site.display}"),dict(tenant=tenant))
        _ip(w,att["subif"],link,0,None,tenant); _ip(w,wan,link,1,None,tenant)
        w.obj(lan)["attrs"].update(label="Customer LAN",mark_connected=True,description="Customer LAN handoff to customer-owned equipment")
        _ip(w,lan,block,1,None,tenant)
        net = ce_loopback(w.site_network(sid))
        w.add("prefix",f"prefix/{sid}/management",dict(prefix=str(net),status="active",description=f"CE management loopback at {site.display}"),
              dict(vrf=MANAGEMENT_VRF,tenant="tenant",scope_site=site.key))
        loop = w.add("interface",f"{edge}/if/Management",dict(name="Management",type="virtual",enabled=True,
                     description="CE management loopback in Carrier Management"),dict(device=edge))
        _ip(w,loop,net,0,MANAGEMENT_VRF,"tenant",True)
        roles = {"role/customer-edge":1,"role/nid":1}
    else:
        # Unmanaged: the PE routes the /29 on its subinterface (.1) and the
        # customer's own firewall takes the rest; the NID UNI is the demarcation.
        _ip(w,att["subif"],block,1,None,tenant)
        w.obj(uni)["attrs"].update(mark_connected=True,label="Customer-owned firewall",
                                   description="Demarcation to the customer-owned firewall; not inventoried")
        roles = {"role/nid":1}
    site.contract.update(required_device_roles=roles,demand=dict(commit_mbps=rate,managed=managed))
    site.contract["assumptions"].append("Dedicated internet access over one NID and one access circuit, statically routed on the home PE; "
                                        "a NID, access-link, aggregation or home-PE failure isolates it.")
    result.update(router=att["pe"],peak_mbps=rate)
    result["lags"].append((pop,att["side"],rate))


def _epl(w,site,c,pop,pop_sites,rate,installed,distance,code,kind,serial,result):
    key,sid=c["key"],site.id
    pop_site = pop_sites[pop][0]
    alias = NID_10G_ALIAS if handoff_mbps(rate,w.recipe["reserve_fraction"]) > 1000 else NID_ALIAS
    side = home_side(w,pop,sid)
    # Port-based Q-in-Q cannot also carry the tagged management VLAN, so the
    # EPL NID's management address is recorded as carried in the S-VLAN.
    nid = _nid(w,site,"nid-01",alias,pop_site,side,False,in_band="In-band management carried inside the service S-VLAN")
    spec = w.hardware(alias)
    uni = site.interface(nid,spec["uni_port"])
    w.obj(uni)["attrs"].update(mark_connected=True,label="Customer Ethernet equipment",
                               description="Demarcation to customer Ethernet equipment; not inventoried")
    att = _attach(w,pop_sites,pop,sid,nid,site,c,rate=rate,handoff=handoff_mbps(rate,w.recipe["reserve_fraction"]),kind=kind,cid=f"{code}-EPL-{serial:05d}",installed=installed,distance=distance)
    term = w.add("l2vpn_termination",f"l2vpn/epl/{key}/{sid}",{},dict(l2vpn=f"l2vpn/epl/{key}",assigned_object=att["subif"]))
    w.provider_service_records[site.key].append(term)
    site.contract.update(required_device_roles={"role/nid":1},demand=dict(rate_mbps=rate))
    site.contract["assumptions"].append("One end of a port-based Ethernet private line: one NID and one access circuit; "
                                        "no protection switching is modeled.")
    result.update(router=att["pe"],peak_mbps=rate)
    result["lags"].append((pop,att["side"],rate))
def _apply_lifecycle(w,entries):
    """Give every record a premises owns its lifecycle status (LIFECYCLE).

    Runs once the graph is enriched, so IPv6, optics and MAC records exist;
    the site's own rooms, racks, devices, cables, addresses, segments and
    access circuit move together, and the serving PE port's /31 end with them.
    """
    objects = w.objects
    customers = {c["key"]:c for c in w.recipe["customers"]}
    for c in customers.values():
        if c["status"] == "planned":
            objects[f"virtual-circuit/customer/{c['key']}"]["attrs"]["status"] = "planned"
    stages = {f"site/{sid}":(lifecycle(c,pop),sid) for sid,c,pop,_ in entries if lifecycle(c,pop) != "active"}
    if not stages:
        return
    owner = {}
    for key,obj in objects.items():
        site = obj["refs"].get("site") if obj["kind"] in ("device","rack","location","vlan") else None
        if site in stages: owner[key] = site
    for key,obj in objects.items():
        if obj["refs"].get("device") in owner: owner[key] = owner[obj["refs"]["device"]]
    for site,(_,sid) in stages.items():
        for circuit in (f"circuit/customer/{sid}",f"circuit/customer/{sid}/b"):
            if circuit not in objects: continue
            owner[circuit] = owner[f"{circuit}/A"] = owner[f"{circuit}/Z"] = site
            for prefix in (f"prefix/link/{circuit}",f"ipv6/prefix/link/{circuit}"):
                if prefix in objects: owner[prefix] = site
    for key,obj in objects.items():
        if obj["kind"] == "cable":
            ends = [owner.get(obj["refs"][side]) for side in ("a","b")]
            site = next((s for s in ends if s),None)
            if site: owner[key] = site
            # The PE end of an access /31 belongs to the premises it serves.
            if site and obj["refs"]["b"].startswith("circuit/customer/"):
                owner[obj["refs"]["a"]] = site
        elif obj["kind"] == "prefix" and obj["refs"].get("scope_site") in stages and obj["attrs"]["status"] != "container":
            owner[key] = obj["refs"]["scope_site"]
        elif obj["kind"] == "power_feed" and objects.get(obj["refs"].get("rack"),{}).get("refs",{}).get("site") in stages:
            owner[key] = objects[obj["refs"]["rack"]]["refs"]["site"]
    # The PoP-side service records (AGG UNI, PE subinterface, service VLAN,
    # DIA assignment, EPL termination) move with the premises they serve.
    for site in stages:
        for key in w.provider_service_records.get(site,()):
            owner[key] = site
    for key,obj in objects.items():
        if obj["kind"] == "ip_address" and obj["refs"].get("assigned_object") in owner:
            owner[key] = owner[obj["refs"]["assigned_object"]]
    field = {"site":"site","location":"site","device":"device","rack":"rack","cable":"cable","circuit":"circuit",
             "ip_address":"ip","prefix":"prefix","vlan":"vlan","power_feed":"feed"}
    for key,site in owner.items():
        obj = objects[key]
        if obj["kind"] in field:
            obj["attrs"]["status"] = LIFECYCLE[stages[site][0]][field[obj["kind"]]]
    for site,(stage,_) in stages.items():
        objects[site]["attrs"]["status"] = LIFECYCLE[stage]["site"]
    # Nothing not yet in service looks installed: the serving PE handoff is
    # shut and its optic planned (no serial) or staged on the shelf; a planned
    # CE and its supplies have no serial until the unit is shipped. A
    # withdrawing access circuit carries its scheduled disconnect date.
    as_of = date.fromisoformat(w.recipe["as_of"])
    for key,site in owner.items():
        stage,obj = stages[site][0],objects[key]
        if stage in OPTIC_STAGE and obj["kind"] == "interface" and (objects[obj["refs"]["device"]]["refs"].get("role") == "role/provider-edge"
                                                                     or key in w.provider_service_records.get(site,())):
            obj["attrs"]["enabled"] = False
            if module := obj["refs"].get("module"):
                objects[module]["attrs"]["status"] = OPTIC_STAGE[stage]
                if stage == "planned": objects[module]["attrs"].pop("serial",None)
        elif stage == "planned" and obj["kind"] in ("device","module"):
            obj["attrs"].pop("serial",None)
            if obj["kind"] == "module": obj["attrs"]["status"] = "planned"
        elif stage == "decommissioning" and obj["kind"] == "circuit":
            obj["attrs"]["termination_date"] = (as_of+timedelta(days=w.choose(key,"disconnect",range(21,61)))).isoformat()


def _junos_units(w):
    """Junos addresses an interface on a logical unit: et-0/0/1.0, never bare et-0/0/1.

    Every address on a physical port of a PoP's Junos device (PE data ports,
    fxp0, a Junos management switch's routed uplinks) moves to a virtual
    ``<port>.0`` child that also carries the routing context; the physical
    port keeps its cable, optic, speed and MAC. Address keys are identities
    (BGP sessions name them), so only the assignment moves. lo0.0 follows the
    same rule in operations._loopback_units.
    """
    units = {}
    for ip in [o for o in w.objects.values() if o["kind"] == "ip_address"]:
        port = w.objects.get(ip["refs"].get("assigned_object"),{})
        if port.get("kind") != "interface" or port["attrs"].get("type") in (None,"virtual","lag","bridge"):
            continue
        device = w.objects[port["refs"]["device"]]
        if device["refs"].get("platform") != "platform/juniper-junos" or not device["refs"]["site"].startswith("site/pop-"):
            continue
        if port["key"] not in units:
            units[port["key"]] = w.add("interface",f"{port['key']}.0",
                dict(name=f"{port['attrs']['name']}.0",type="virtual",enabled=port["attrs"].get("enabled",True)),
                dict(device=device["key"],parent=port["key"],**({"vrf":port["refs"]["vrf"]} if port["refs"].get("vrf") else {})))
        ip["refs"]["assigned_object"] = units[port["key"]]
    for key in units:
        w.objects[key]["refs"].pop("vrf",None)  # the routing context is the unit's


# The serving PE optic of a premises not yet in service.
OPTIC_STAGE = {"planned":"planned","provisioning":"staged"}


def _capacity(graph,attachments,reserve):
    """Small bounded PE graph: reuse BFS per source per failed span, never per PC."""
    adjacency=defaultdict(list)
    for edge,a,b,rate in graph:
        adjacency[a].append((edge,b)); adjacency[b].append((edge,a))
    for edges in adjacency.values(): edges.sort()
    # Access aggregation: per PoP side, the declared peaks of every active
    # attachment must fit the AGG-PE LAG with reserve, in steady state and after
    # one member's loss. DIA and EPL load the LAG; only VPN flows cross the core.
    usable=1-Decimal(str(reserve)); sides=Counter()
    for a in attachments:
        if a.get("stage","active") == "active":
            for pop,side,mbps in a.get("lags",()):
                sides[pop,side]+=mbps
    for (pop,side),load in sorted(sides.items()):
        for members in (LAG_MEMBERS,LAG_MEMBERS-1):
            if load > members*LAG_MEMBER_MBPS*usable:
                raise DesignError(f"{pop} AGG-{side.upper()}: {load} Mbps of declared access peaks exceed the {members}x10G "
                                  "AGG-PE LAG with reserve; add a PoP or rebaseline the aggregation design")
    attachments=[a for a in attachments if a.get("service","private-l3") == "private-l3"]
    hubs={a["customer"]:a["router"] for a in attachments if a["hub"] and a.get("stage","active") == "active"}
    flows=Counter()
    for a in attachments:
        # Only an in-service premises offers traffic: planned, provisioning
        # and decommissioning paths are never counted as healthy capacity.
        if not a["hub"] and a.get("stage","active") == "active": flows[a["router"],hubs[a["customer"]]] += a["peak_mbps"]
    worst=Counter(); normal=Counter()
    for failed in [None]+[edge for edge,*_ in graph if edge.startswith("circuit/backbone/")]:
        trees={}; loads=Counter()
        for (start,end),amount in sorted(flows.items()):
            if start==end: continue
            if start not in trees:
                parents={start:None}; queue=deque([start])
                while queue:
                    node=queue.popleft()
                    for edge,peer in adjacency[node]:
                        if edge!=failed and peer not in parents:
                            parents[peer]=(node,edge); queue.append(peer)
                trees[start]=parents
            parents=trees[start]
            if end not in parents: raise DesignError(f"Customer traffic loses its hub path after span {failed}")
            node=end
            while node!=start:
                previous_node,edge=parents[node]
                loads[edge,previous_node,node]+=amount
                node=previous_node
        directional_peaks=Counter()
        for (edge,_,_),load in loads.items():
            directional_peaks[edge]=max(directional_peaks[edge],load)
        for edge,_,_,rate in graph:
            directional_peak=directional_peaks[edge]
            if directional_peak > Decimal(rate)*(1-Decimal(str(reserve))):
                raise DesignError(f"{edge}: customer spoke-to-hub demand exceeds physical capacity plus reserve after span {failed}; expand the reviewed transport design")
            worst[edge]=max(worst[edge],directional_peak)
            if failed is None: normal[edge]=directional_peak
    return dict(scope=CAPACITY_SCOPE,normal_load_mbps=dict(sorted(normal.items())),worst_load_mbps=dict(sorted(worst.items())),
                checked_span_failures=sum(edge.startswith("circuit/backbone/") for edge,*_ in graph),
                load_metric="Maximum directed load on either link direction; directions have independent full-duplex capacity",
                aggregation_load_mbps={f"{pop}/{side}":load for (pop,side),load in sorted(sides.items())})


def _generate(recipe,previous=None):
    w=World(recipe,previous)
    entries=premises(recipe)
    w.reserve_sites(["dc-01"]+[f"pop-{p['key']}" for p in recipe["pops"]]+[sid for sid,*_ in entries])
    for p in recipe["pops"]: w.reserve("provider-pop-order",p["key"],64)
    order=w.reservations["provider-pop-order"]
    if set(order)!= {p["key"] for p in recipe["pops"]} or set(order.values()) != set(range(len(order))):
        raise DesignError("provider-pop-order must be a complete dense immutable sequence of current PoP keys")
    metros={p["key"]:p["metro"] for p in recipe["pops"]}
    w.provider_metros={"dc-01":metros[recipe["noc_pop_a"]],**{f"pop-{k}":v for k,v in metros.items()},**{sid:metros[pop] for sid,_,pop,_ in entries}}
    foundation(w,industry="provider",inherited=False,include_carriers=False,
        networks=("management","applications","database","backup","storage","provider"),site_kinds={"pop","customer","dc"},
        device_roles=("provider-edge","customer-edge","wan-edge","access","spine","leaf","server","management","pdu","workstation","patch-panel","wall-outlet","nid"),
        hardware_aliases={"provider-edge","core","leaf","edge","server","access","pdu","console-server","endpoint","patch-panel","wall-outlet","nid","nid-10g","ce-small","aggregation"})
    _registry(w)
    pop_sites={p["key"]:_pop(w,p) for p in recipe["pops"]}
    points=_points(w)
    spans=_plan_spans(w,points)
    launch=_launch(w,spans)
    graph=_topology(w,pop_sites,points,spans,launch)
    # A PoP serves customers from its launch, once its first span is in service.
    first_span={}
    for key,a,b in spans:
        day=date.fromisoformat(w.obj(key)["attrs"]["install_date"])
        for pop in (a[0],b[0]): first_span[pop]=min(day,first_span.get(pop,day))
    ready={pop:max(day,first_span[pop]) for pop,day in launch.items()}
    for pop,day in ready.items(): w.obj(f"site/pop-{pop}")["meta"]["in_service"]=day.isoformat()
    ordered=sorted(recipe["pops"],key=lambda p:order[p["key"]])
    ns,code=recipe["namespace"],operator_code(recipe["name"])
    for index,side in enumerate(("a","b")):
        pop=ordered[index]["key"]; site,routers=pop_sites[pop]; port=site.interface(routers[index],"xe-0/1/7")
        circuit=f"circuit/transit/{side}"; label=f"transit-{side}"
        _circuit(w,circuit,f"provider/{label}",f"provider-account/provider/{label}","transit",site,port,f"provider-network/transit/{side}",None,10000,
                 cid=CARRIERS[label][2].format(carrier_number(ns,label,0)),installed=launch[pop]-timedelta(days=w.choose(circuit,"transit-install",range(5,21))))
        # The upstream holds the even address of its /31; the operator takes the odd one.
        net=_link_prefix(w,circuit,CORE_VRF,"tenant"); _ip(w,port,net,1,CORE_VRF,"tenant")
    for p in ordered:
        _oob(w,pop_sites[p["key"]][0],p["key"],launch[p["key"]])
    for side in ("a","b"):
        _service_port(w,pop_sites,recipe[f"noc_pop_{side}"],f"noc/{side}")
    dc=Site(w,"dc-01","dc","Provider NOC services, inventory and monitoring")
    w.obj(dc.key)["refs"]["asns"]=["asn/operator"]
    def noc(site,edge,side,ordinal):
        if ordinal != 1: raise DesignError("Provider NOC has exactly one purchased dual handoff; rebaseline a reviewed larger edge")
        pop,peer=_service_port(w,pop_sites,recipe[f"noc_pop_{side}"],f"noc/{side}")
        port=site.interface(edge,"wan1"); circuit=f"circuit/noc/{side}"
        installed=ready[recipe[f"noc_pop_{side}"]]+timedelta(days=w.choose(circuit,"noc-install",range(5,31)))
        target=recipe[f"noc_pop_{side}"]
        if metros[target]==w.provider_metros["dc-01"]:
            _circuit(w,circuit,"provider/operator","provider-account/operator/noc","access",site,port,pop,peer,1000,
                     cid=f"{code}-NOC-{'ab'.index(side)+1:04d}",installed=installed)
        else:
            # Another metro: a leased port-based 1G private line, not an operator
            # access tail stretched across the region on a 10 km optic.
            carrier=noc_carrier(side)
            here,there=(w.obj(k)["attrs"] for k in ("site/dc-01",f"site/pop-{target}"))
            distance=round(km((here["latitude"],here["longitude"]),(there["latitude"],there["longitude"]))*ROUTE_FACTOR,1) if "latitude" in here and "latitude" in there else None
            _circuit(w,circuit,f"provider/{carrier}",f"provider-account/provider/{carrier}","access",site,port,pop,peer,1000,
                     cid=CARRIERS[carrier][2].replace("WAV","EPL").format(carrier_number(ns,carrier,NOC_ORDINAL+"ab".index(side))),installed=installed,
                     description=f"1G Ethernet private line, NOC to {pop.display}",distance_km=distance)
        _routed_pair(w,circuit,port,peer,MANAGEMENT_VRF)
    datacenter.build(dc,workloads=workloads(recipe),wan_peak_mbps=recipe["noc_peak_mbps"],wan_attachment=noc,include_equipment=False,
        assumptions=[CAPACITY_SCOPE,"NOC service groups are authored: identity/provisioning per128 customer premises, DNS/monitoring per16 PoPs, minimum one group each; every group has two rack-separated replicas. These are synthetic inventory budgets, not measured throughput."])
    w.obj(dc.key)["meta"]["in_service"]=min(w.obj(f"circuit/noc/{s}")["attrs"]["install_date"] for s in "ab")
    onboarded=_onboarding(w,ready)
    as_of=date.fromisoformat(recipe["as_of"])
    def installed(sid,c,pop,n):
        if pop==anchor_pop(c) and n==1: return onboarded[c["key"]]
        day=max(onboarded[c["key"]],ready[pop]+timedelta(days=30))+timedelta(days=w.choose(sid,"premises-install",range(7,366)))
        return min(day,as_of-timedelta(days=1))
    w.provider_service_records=defaultdict(list)
    placed=_premises_places(w,entries,points)
    attachments=[_customer(w,sid,c,pop,n,pop_sites,placed,installed(sid,c,pop,n)) for sid,c,pop,n in entries]
    dc.contract["provider"]=dict(pop_count=len(pop_sites),customer_count=len(recipe["customers"]),customer_premises=len(entries),
        transport_spans=len(spans),capacity=_capacity(graph,attachments,recipe["reserve_fraction"]),
        management_mode="out-of-band and in-band",transit_remote_ownership="unknown",wireless="omitted; wired private-L3 service scope")
    equipment.enrich(w); optics.enrich(w); poe.enrich(w); ipv6.enrich(w); networking.macs(w)
    _apply_lifecycle(w,entries)
    _junos_units(w)
    operations.supporting_records(w)
    bgp.enrich(w)
    discovery_lab.add_discovery_lab(w)
    return w.finish()
