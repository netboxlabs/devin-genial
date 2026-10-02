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
CUSTOMER_DEFAULTS = dict(service="private-l3",site_peak_mbps=50,hub_commit_mbps=1000,lan_endpoints=4)
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
PUBLIC_POOLS = {"loopbacks": "192.0.2.0/24", "pair": "198.51.100.0/25", "backbone": "203.0.113.0/24"}
PUBLIC_AGGREGATES = ("192.0.2.0/24", "198.51.100.0/25", "203.0.113.0/24")
# Each upstream numbers its transit /31 from its own space: these blocks are
# recorded as the upstream's assignment, outside every operator aggregate.
UPSTREAM_POOLS = {"transit-a": "198.51.100.224/28", "transit-b": "198.51.100.240/28"}
PUBLIC_LINK_SCOPES = {"pair": "provider-pair-links", "backbone": "provider-span-links", "transit": "provider-transit-links"}
# The out-of-band broadband ISP hands each PoP console server a /30 from RFC
# 6598 shared address space; it lives in its own routing context, never the core.
OOB_POOL = "100.64.0.0/24"
# Host ordinals on the PoP management /26: switch SVI 1, console NET1 3, PE fxp0 4/5.
FXP0_HOSTS = (4, 5)
LOOPBACK_CAPACITY = 254


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


def resolve(raw):
    fields = {"profile","demo","topology","pops","customers","noc_pop_a","noc_pop_b","noc_peak_mbps","asn_base","discovery_lab"}
    if unknown := raw.keys()-(COMMON|fields):
        raise DesignError(f"Unknown provider fields: {', '.join(sorted(unknown))}; describe PoPs and private-L3 customer demand")
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
    if not isinstance(pops,list) or not 3 <= len(pops) <= 64:
        raise DesignError("pops must contain 3–64 keyed entries")
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
    keys,result,attachment_count,site_ids = set(),[],Counter((r["noc_pop_a"],r["noc_pop_b"])),set()
    for raw_customer in customer_rows:
        if (not isinstance(raw_customer,dict) or not {"key","hub_pop","sites"} <= raw_customer.keys() or
                raw_customer.keys()-(CUSTOMER_DEFAULTS.keys()|{"key","hub_pop","sites"})):
            raise DesignError("Each customer needs key, hub_pop and sites, with only the documented private-L3 demand fields")
        c = CUSTOMER_DEFAULTS|deepcopy(raw_customer)
        key = _key(c["key"],"Customer key")
        if key in keys:
            raise DesignError("Customer keys must be unique")
        keys.add(key)
        if c["service"] != "private-l3":
            raise DesignError("Customer service supports private-l3; Internet and L2VPN are separate future designs")
        _integer(c["site_peak_mbps"],f"Customer {key} site_peak_mbps",1,800)
        _integer(c["lan_endpoints"],f"Customer {key} lan_endpoints",0,12)
        _integer(c["hub_commit_mbps"],f"Customer {key} hub_commit_mbps",1,1000)
        if c["hub_commit_mbps"] not in r["wan_tiers_mbps"]:
            raise DesignError(f"Customer {key}: hub_commit_mbps must be one of wan_tiers_mbps")
        if c["site_peak_mbps"] > 1000*usable:
            raise DesignError(f"Customer {key}: site peak plus reserve exceeds the 1G physical handoff")
        entries = c["sites"]
        if not isinstance(entries,list) or not 2 <= len(entries) <= len(pops):
            raise DesignError(f"Customer {key}: sites must span at least two distinct modeled PoPs")
        seen = set()
        for entry in entries:
            if not isinstance(entry,dict) or entry.keys()-{"pop","count"} or "pop" not in entry:
                raise DesignError(f"Customer {key}: each sites entry requires pop and optional count")
            pop = entry["pop"]
            if not isinstance(pop,str) or pop not in values or pop in seen:
                raise DesignError(f"Customer {key}: attachment PoPs must be unique known keys")
            seen.add(pop)
            entry["count"] = _integer(entry.get("count",1),f"Customer {key} site count",1,12)
            attachment_count[pop] += entry["count"]
            for n in range(1,entry["count"]+1):
                sid = f"ce-{key}-{pop}-{n:03}"
                if sid in site_ids:
                    raise DesignError("Composed customer site identities collide; choose unambiguous customer and PoP keys")
                site_ids.add(sid)
        if not isinstance(c["hub_pop"],str) or c["hub_pop"] not in seen:
            raise DesignError(f"Customer {key}: hub_pop must be one of its attachment PoPs")
        c["sites"] = sorted(entries,key=lambda item:item["pop"])
        count = sum(e["count"] for e in entries)
        if (count-1)*c["site_peak_mbps"] > c["hub_commit_mbps"]*usable:
            raise DesignError(f"Customer {key}: aggregate spoke demand plus reserve exceeds the fixed hub commitment; lower demand or rebaseline its purchase")
        result.append(c)
    if any(count > 12 for count in attachment_count.values()):
        raise DesignError("Combined customer/NOC demand exceeds twelve direct attachments at a PoP; add a PoP or a reviewed aggregation layer")
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
            changed = [k for k in ("service","hub_pop","site_peak_mbps","hub_commit_mbps")
                       if after[k] != before[k]]
            if changed:
                raise DesignError(
                    f"Customer {before['key']}: changing {', '.join(changed)} is a hub or "
                    "purchased-bandwidth change and requires a new baseline")
            counts = {e["pop"]:e["count"] for e in after["sites"]}
            if after["lan_endpoints"] < before["lan_endpoints"] or any(counts.get(e["pop"],0) < e["count"] for e in before["sites"]):
                raise DesignError("Reducing customer premises or LAN endpoint demand requires a new baseline")
            if before["lan_endpoints"] == 0 < after["lan_endpoints"]:
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
        w.add("asn",f"asn/{key}",dict(asn=number,description=f"{held_by[key]} routing identity"),{"rir":"rir/arin"})
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
    for name in ("backbone","dark-fiber","access","transit","out-of-band"):
        w.add("circuit_type",f"circuit-type/{name}",dict(name=titleize(name),slug=f"{ns}-{name}"))
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
        customer = titleize(key)
        tenant = w.add("tenant",f"tenant/cust-{key}",dict(name=customer,slug=f"{ns}-cust-{key}",description="Private L3 VPN customer"))
        w.add("asn",f"asn/customer/{key}",dict(asn=r["asn_base"]+256+slot,description=f"{customer} customer routing identity"),{"rir":"rir/private"})
        # The customer VPN's RD reuses its route target value, <operator ASN>:<n>,
        # numbered by the permanent onboarding slot and stable under growth.
        value = f"{operator_asn}:{1001+slot}"
        target = w.add("route_target",f"route-target/customer/{key}",dict(name=value,description=f"{customer} private L3 VPN import/export"),{"tenant":tenant})
        vrf = w.add("vrf",f"vrf/customer/{key}",dict(name=f"{customer} Private L3",rd=value,enforce_unique=True),
                    dict(tenant=tenant,import_targets=[target,hub],export_targets=[target,spoke]))
        account = _account(w,f"provider-account/customer/{key}","provider/operator",customer,f"{code}-C{slot+1:05d}",
                           f"Private L3 VPN billing for {customer}")
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
              dict(vrf=vrf,tenant=site.tenant,scope_site=site.key))
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
    key = w.add("ip_address",f"ip/{port}",dict(address=f"{net[host]}/{net.prefixlen}",status="active",
          description=f"{w.obj(owner)['attrs']['name']} {w.obj(port)['attrs']['name']}",
          dns_name=f"{w.obj(owner)['attrs']['name']}.{w.recipe['namespace']}.example"),
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
    a,z = (w.obj(f"{key}/{side}")["refs"]["termination"] for side in "AZ")
    if kind == "backbone":
        return f"Backbone span {display(a)} to {display(z)}"
    if kind == "transit":
        return f"Transit handoff to {display(w.obj(key)['refs']['provider'])} at {display(a)}"
    if kind == "noc":
        return f"NOC access link {rest.rsplit('/',1)[-1].upper()} to {display(z)}"
    return f"Access link {display(a)} to {display(z)}"


def _routed_pair(w,key,a,b,vrf=CORE_VRF,tenant="tenant"):
    net = _link_prefix(w,key,vrf,tenant)
    _ip(w,a,net,0,vrf,tenant); _ip(w,b,net,1,vrf,tenant)


def _circuit(w,key,provider,account,kind,a_site,a_port,z_site,z_port,rate_mbps,tenant="tenant",handoff_mbps=None,*,
             cid,installed,description=None,distance_km=None):
    """One circuit; rate_mbps None is owned fibre with no purchased commitment."""
    handoff = handoff_mbps or rate_mbps
    attrs = dict(cid=cid,status="active",install_date=installed.isoformat(),
                 description=description or f"{bandwidth(rate_mbps)} {kind} committed on a {port_speed(handoff)} handoff")
    if rate_mbps is not None:
        attrs["commit_rate"] = rate_mbps*1000
    if distance_km:
        attrs.update(distance=distance_km,distance_unit="km")
    refs = dict(provider=provider,type=f"circuit-type/{kind}",tenant=tenant)
    if account:
        refs["provider_account"] = account
    w.add("circuit",key,attrs,refs,dict(procurement=dict(cohort=f"provider-{kind}",handoff_mbps=handoff)))
    for side,site,port in (("A",a_site,a_port),("Z",z_site,z_port)):
        target = site.key if isinstance(site,Site) else site
        term = w.add("circuit_termination",f"{key}/{side}",dict(term_side=side,port_speed=handoff*1000,
                     description="Local routed handoff" if port else "Upstream carrier handoff"),dict(circuit=key,termination=target))
        if port:
            site.cable(port,term,"cat6" if w.obj(port)["attrs"]["type"] == "1000base-t" else "smf")
            w.obj(port)["attrs"]["speed"] = handoff*1000
            site.contract["required_connections"].append(dict(a=port,b=term))
    return key


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
        # One 1U fibre enclosure per PE cabinet terminates its building fibre.
        site.device("fibre-panel",f"odf-{side}","patch-panel",rack_domain=number)
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
        day = ready[c["hub_pop"]]+timedelta(days=30)
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
    taken, in allocation-slot order, so growth never moves or renames a site.
    Returns sid -> (display name, latitude, longitude, anchor).
    """
    overrides,result = w.recipe.get("site_names",{}),{}
    if w.recipe.get("naming","authored") != "authored":
        return result
    for c in w.recipe["customers"]:
        mine = sorted((w.allocations[sid],sid,pop) for sid,customer,pop,_ in entries if customer["key"] == c["key"])
        taken = Counter()
        for _,sid,pop in mine:
            if sid in overrides:
                continue
            city = next(row[0] for row in places.METROS if row[0].lower() == w.provider_metros[sid])
            jitter = sha256(f"geo/{sid}".encode()).digest()
            point = lambda a:(round(a[2]+(jitter[0]/255-0.5)*2*places.JITTER_LAT,6),
                              round(a[3]+(jitter[1]/255-0.5)*2*places.JITTER_LON,6))
            rivals = [q for q in serving_pops(w,sid,pop) if q != pop]
            eligible = [a for a in places.ANCHORS[city] if len(a) == 4 and km(points[pop],point(a)) <= PREMISES_KM and
                        all(km(points[pop],point(a)) < km(points[q],point(a)) for q in rivals)]
            if not eligible:
                raise DesignError(f"{sid}: no authored {city} anchor lies in the service area of PoP {pop} "
                                  f"(within {PREMISES_KM} km and nearer it than any other {city} PoP)")
            anchor = min(eligible,key=lambda a:(taken[a[0][0]],_hash("premises",sid,a[0][0])))
            taken[anchor[0][0]] += 1
            label = anchor[0][0] + (f" {taken[anchor[0][0]]}" if taken[anchor[0][0]] > 1 else "")
            result[sid] = (f"{titleize(c['key'])} {label}",*point(anchor),anchor)
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


def _service_port(w,pop_sites,pop,target):
    slot = w.reserve(f"provider-service-ports/{pop}",target,12)
    site,routers = pop_sites[pop]
    return site,site.interface(routers[slot%2],f"xe-0/1/{slot//2}")


def _customer(w,sid,c,pop,number,pop_sites,placed,installed):
    key=c["key"]; tenant=f"tenant/cust-{key}"; vrf=f"vrf/customer/{key}"
    site = Site(w,sid,"customer","Private-L3 customer premises and wired office",tenant=tenant,routing_domain=vrf)
    if sid in placed:
        # Named and plotted where the premises is, not after its serving PoP.
        name,latitude,longitude,anchor = placed[sid]
        node = w.obj(site.key)
        lines = node["attrs"]["physical_address"].split("\n")
        lines[:2] = [places.street_address(sid,anchor,latitude,longitude),f"{anchor[1]}, {lines[1].rsplit(', ',1)[-1]}"]
        node["attrs"].update(name=name,latitude=latitude,longitude=longitude,physical_address="\n".join(lines))
    w.obj(site.key)["meta"]["in_service"] = installed.isoformat()
    site.code = premises_code(w,sid,key)
    w.obj(site.key)["refs"]["asns"] = [f"asn/customer/{key}"]
    managed_lan = c["lan_endpoints"] > 0
    _site_network(site,"clients",25,128,20)
    edge = site.device("edge","edge-01","customer-edge")
    a = site.interface(edge,"port1")
    switch = None
    if managed_lan:
        # Seats on a carrier-managed LAN: the CE trunks management and clients
        # to a same-room access switch, which serves the office pod.
        _site_network(site,"management",26,0,10)
        room = places.provider_office(site)
        switch = site.device("access","access-01","access")
        access_ports = w.hardware("access")["access_ports"]
        b = site.interface(switch,access_ports[-1])
        site.cable(a,b); trunk(site,(a,b),("management","clients")); site.contract["required_connections"].append(dict(a=a,b=b))
        for role,name in (("management","Management"),("clients","Clients")):
            vi = site.virtual_interface(edge,name,role); w.obj(vi)["refs"]["parent"] = a
            site.address(vi,role,host=1,primary=role=="management",device=edge)
        vi = site.virtual_interface(switch,"Vlan10","management"); site.address(vi,"management",host=2,primary=True,device=switch)
    else:
        # CE only: port1 hands the customer LAN to customer-owned equipment
        # (not inventoried) and the CE is managed on its own loopback.
        vlan,_ = site.network("clients")
        w.obj(a)["attrs"]["mode"] = "access"; w.obj(a)["refs"]["untagged_vlan"] = vlan
        w.obj(a)["attrs"]["description"] = "Customer LAN handoff to customer-owned equipment"
        vi = site.virtual_interface(edge,"Clients","clients"); w.obj(vi)["refs"]["parent"] = a
        site.address(vi,"clients",host=1,device=edge)
        net = ce_loopback(w.site_network(sid))
        w.add("prefix",f"prefix/{sid}/management",dict(prefix=str(net),status="active",description=f"CE management loopback at {site.display}"),
              dict(vrf=vrf,tenant=tenant,scope_site=site.key))
        loop = w.add("interface",f"{edge}/if/Management",dict(name="Management",type="virtual",enabled=True,
                     description="CE management loopback, exported to Carrier Management"),dict(device=edge))
        _ip(w,loop,net,0,vrf,tenant,True)
    panel = site.device("patch-panel","patch-01","patch-panel") if w.recipe["patching"] == "panels" else None
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
    pop_site,pe_port = _service_port(w,pop_sites,pop,sid)
    hub = pop==c["hub_pop"] and number==1
    usable = 1-Decimal(str(w.recipe["reserve_fraction"]))
    rate = c["hub_commit_mbps"] if hub else next(tier for tier in w.recipe["wan_tiers_mbps"] if tier*usable>=c["site_peak_mbps"])
    port=site.interface(edge,"wan1"); circuit=f"circuit/customer/{sid}"
    _circuit(w,circuit,"provider/operator",f"provider-account/customer/{key}","access",site,port,pop_site,pe_port,rate,tenant,handoff_mbps=1000,
             cid=f"{operator_code(w.recipe['name'])}-PL3-{w.allocations[sid]:05d}",installed=installed)
    _routed_pair(w,circuit,port,pe_port,vrf,tenant)
    vi=w.add("interface",f"{edge}/if/PrivateL3",dict(name="PrivateL3",type="virtual",enabled=True,description="Private L3 VPN attachment over the access circuit"),
             dict(device=edge,parent=port,vrf=vrf))
    w.add("virtual_circuit_termination",f"virtual-circuit-termination/{sid}",dict(role="peer",description=f"{site.display} {'hub' if hub else 'spoke'}"),
          dict(virtual_circuit=f"virtual-circuit/customer/{key}",interface=vi))
    site.contract.update(required_device_roles={"role/customer-edge":1,**({"role/access":1} if managed_lan else {})},endpoint_count=c["lan_endpoints"],
                         demand=dict(lan_endpoints=c["lan_endpoints"],peak_mbps=c["site_peak_mbps"],hub=hub,commit_mbps=rate),access_hardware=w.hardware_alias("access"))
    site.contract["assumptions"].extend(["The private-L3 customer has one physical access circuit and one CE. A CE, access-link or serving-PE failure can isolate this premises.",
        "Twelve fixed office positions and a same-room access switch preserve existing endpoint cables and addresses during growth. Customer LAN is wired; no wireless service is modeled."
        if managed_lan else "The CE hands the customer LAN to customer-owned equipment that is not inventoried; the carrier manages only the CE."])
    if managed_lan:
        _console_management(site,switch)
    else:
        equipment.enrich_site(site,demonstrations=False)
    site.power()
    return dict(site=site.key,customer=key,router=w.obj(pe_port)["refs"]["device"],hub=hub,peak_mbps=c["site_peak_mbps"])


def _capacity(graph,attachments,reserve):
    """Small bounded PE graph: reuse BFS per source per failed span, never per PC."""
    adjacency=defaultdict(list)
    for edge,a,b,rate in graph:
        adjacency[a].append((edge,b)); adjacency[b].append((edge,a))
    for edges in adjacency.values(): edges.sort()
    hubs={a["customer"]:a["router"] for a in attachments if a["hub"]}
    flows=Counter()
    for a in attachments:
        if not a["hub"]: flows[a["router"],hubs[a["customer"]]] += a["peak_mbps"]
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
                load_metric="Maximum directed load on either link direction; directions have independent full-duplex capacity")


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
        device_roles=("provider-edge","customer-edge","wan-edge","access","spine","leaf","server","management","pdu","workstation","patch-panel","wall-outlet"),
        hardware_aliases={"provider-edge","core","leaf","edge","server","access","pdu","console-server","endpoint","patch-panel","wall-outlet"})
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
        if pop==c["hub_pop"] and n==1: return onboarded[c["key"]]
        day=max(onboarded[c["key"]],ready[pop]+timedelta(days=30))+timedelta(days=w.choose(sid,"premises-install",range(7,366)))
        return min(day,as_of-timedelta(days=1))
    placed=_premises_places(w,entries,points)
    attachments=[_customer(w,sid,c,pop,n,pop_sites,placed,installed(sid,c,pop,n)) for sid,c,pop,n in entries]
    dc.contract["provider"]=dict(pop_count=len(pop_sites),customer_count=len(recipe["customers"]),customer_premises=len(entries),
        transport_spans=len(spans),capacity=_capacity(graph,attachments,recipe["reserve_fraction"]),
        management_mode="out-of-band and in-band",transit_remote_ownership="unknown",wireless="omitted; wired private-L3 service scope")
    equipment.enrich(w); optics.enrich(w); poe.enrich(w); ipv6.enrich(w); networking.macs(w); operations.supporting_records(w)
    bgp.enrich(w)
    discovery_lab.add_discovery_lab(w)
    return w.finish()
