"""A finite private-L3 provider, composed from persistent physical attachments.

The authored traffic model is directed customer spoke-to-hub demand only.
No routing daemon, BGP session, forwarding or carrier duct survey is simulated.
"""

from collections import Counter, defaultdict, deque
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import ipaddress
import re

from . import datacenter, equipment, ipv6, networking, operations, places, poe, optics
from .blocks import Site, foundation, trunk
from .model import DesignError, World, canonical, resolve_bank_recipe, resolve_demo


COMMON = {"namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool", "reserve_fraction",
          "max_objects", "patching", "reservation_user", "wan_tiers_mbps"}
DEFAULT_POPS = [dict(key="chicago-west",metro="chicago"),dict(key="detroit-south",metro="detroit"),
                dict(key="cleveland-east",metro="cleveland")]
DEFAULT_CUSTOMERS = [dict(key="harbor-logistics",hub_pop="chicago-west",sites=[dict(pop=p["key"],count=1) for p in DEFAULT_POPS])]
CUSTOMER_DEFAULTS = dict(service="private-l3",site_peak_mbps=50,hub_commit_mbps=1000,lan_endpoints=4)
CAPACITY_SCOPE = ("Directed customer spoke-to-hub offered load under normal operation and loss of one inter-PoP span. "
                  "Return, arbitrary peer-to-peer, NOC and external-transit traffic are excluded; this is not total backbone capacity. "
                  "NOC peak tests only local purchased handoff headroom. Router and local-pair failures have a connectivity witness only, "
                  "not a traffic-capacity or single-homed customer-availability guarantee.")


def _key(value,label):
    if not isinstance(value,str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,19}",value):
        raise DesignError(f"{label} must be a lowercase key of 1–20 letters, digits or hyphens, starting with a letter")
    return value


def _integer(value,label,low,high):
    if type(value) is not int or not low <= value <= high:
        raise DesignError(f"{label} must be an integer from {low} through {high}")
    return value


def premises(recipe):
    return [(f"ce-{c['key']}-{entry['pop']}-{n:03}",c,entry["pop"],n)
            for c in recipe["customers"] for entry in c["sites"] for n in range(1,entry["count"]+1)]


def resolve(raw):
    fields = {"profile","demo","topology","pops","customers","noc_pop_a","noc_pop_b","noc_peak_mbps","asn_base"}
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
        _integer(c["lan_endpoints"],f"Customer {key} lan_endpoints",1,12)
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
    r["customers"] = sorted(result,key=lambda c:c["key"])
    blocks = (4294967294-4200000000+1)//1024
    default_asn = 4200000000+(int.from_bytes(sha256(f"{r['namespace']}/provider-asn-block".encode()).digest()[:8],"big")%blocks)*1024
    r["asn_base"] = _integer(raw.get("asn_base",default_asn),"asn_base",4200000000,4294967294-1023)
    if (r["asn_base"]-4200000000)%1024:
        raise DesignError("asn_base must start a 1024-number block aligned from 4200000000; target global collision preflight is still required")
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
            replica_description="independent host and rack lanes; service execution and recovery are not verified"))
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
        for field in ("topology","noc_pop_a","noc_pop_b","noc_peak_mbps","asn_base"):
            if old[field] != recipe[field]:
                raise DesignError(f"Changing {field} requires a new provider baseline")
        current_pops = {p["key"]:p for p in recipe["pops"]}
        if any(current_pops.get(p["key"]) != p for p in old["pops"]):
            raise DesignError("Removing, renaming or moving an existing PoP requires a new baseline")
        current = {c["key"]:c for c in recipe["customers"]}
        for before in old["customers"]:
            after = current.get(before["key"])
            if after is None or any(after[k] != before[k] for k in ("service","hub_pop","site_peak_mbps","hub_commit_mbps")):
                raise DesignError("Removing customers, changing hubs or renewing purchased bandwidth requires a new baseline")
            counts = {e["pop"]:e["count"] for e in after["sites"]}
            if after["lan_endpoints"] < before["lan_endpoints"] or any(counts.get(e["pop"],0) < e["count"] for e in before["sites"]):
                raise DesignError("Reducing customer premises or LAN endpoint demand requires a new baseline")
    return _generate(recipe,previous)


def _registry(w):
    ns,r = w.recipe["namespace"],w.recipe
    w.add("rir","rir/private",dict(name=f"{ns} Private allocations",slug=f"{ns}-private",is_private=True))
    w.add("asn_range","asn-range/private",dict(name=f"{ns} Provider routing domains",slug=f"{ns}-routing",
          start=r["asn_base"],end=r["asn_base"]+1023,description="Private 32-bit ASN reservation; no BGP sessions are configured"),{"rir":"rir/private"})
    for key,offset in (("operator",0),("transit-a",1),("transit-b",2)):
        w.add("asn",f"asn/{key}",dict(asn=r["asn_base"]+offset,description=f"{ns} {key} routing identity"),{"rir":"rir/private"})
    for label,name in (("operator",r["name"]),("transport-a","Northstar Transport"),("transport-b","Meridian Transport"),
                       ("transit-a","Atlas Upstream"),("transit-b","Orion Upstream")):
        refs = {"asns":[f"asn/{'operator' if label == 'operator' else label}"]} if not label.startswith("transport-") else {}
        display = f"{ns} {name}"
        # Retain ordinary branding; reserve thirteen characters for support-desk
        # names. A digest preserves identity when the full display exceeds87.
        if len(display) > 87:
            display = display[:70].rstrip()+"-"+sha256(display.encode()).hexdigest()[:16]
        attrs = dict(name=display,slug=f"{ns}-{label}")
        if label == "operator":
            attrs["comments"] = f"Private-L3 operator for {r['name']}; routing and commercial inventory only."
        w.add("provider",f"provider/{label}",attrs,refs)
        if label.startswith("transit-"):
            w.add("provider_network",f"provider-network/transit/{label[-1]}",dict(name=f"{ns}-{label}",description="External transit interior and remote interface owner are unknown"),{"provider":f"provider/{label}"})
        if label != "operator":
            _account(w,f"provider-account/provider/{label}",f"provider/{label}",label)
    w.add("provider_network","provider-network/operator",dict(name=f"{ns}-private-l3",description="Routed private service across actual modeled PoPs; control plane is not executed"),{"provider":"provider/operator"})
    _account(w,"provider-account/operator/noc","provider/operator","noc")
    for name in ("backbone","access","transit"):
        w.add("circuit_type",f"circuit-type/{name}",dict(name=f"{ns} {name.title()}",slug=f"{ns}-{name}"))
    w.add("virtual_circuit_type","virtual-circuit-type/private-l3",dict(name=f"{ns} Private L3",slug=f"{ns}-private-l3",color="e65100"))
    for c in r["customers"]:
        key = c["key"]; slot = w.reserve("provider-customers",key,256)
        tenant = w.add("tenant",f"tenant/cust-{key}",dict(name=f"{ns} {key}",slug=f"{ns}-cust-{key}",description="Private-L3 service customer"))
        w.add("asn",f"asn/customer/{key}",dict(asn=r["asn_base"]+256+slot,description=f"{ns} {key} customer routing identity"),{"rir":"rir/private"})
        target = w.add("route_target",f"route-target/customer/{key}",dict(name=f"{r['asn_base']}:{slot+1}",description=f"{key} private routing import/export domain"),{"tenant":tenant})
        vrf = w.add("vrf",f"vrf/customer/{key}",dict(name=f"{ns}-customer-{key}",enforce_unique=True),
                    dict(tenant=tenant,import_targets=[target],export_targets=[target]))
        w.add("prefix",f"root/customer/{key}",dict(prefix=r["address_pool"],status="container",description="Customer scoped private allocation space"),dict(vrf=vrf,tenant=tenant))
        account = _account(w,f"provider-account/customer/{key}","provider/operator",f"customer-{key}")
        w.add("virtual_circuit",f"virtual-circuit/customer/{key}",dict(cid=f"{ns}-private-{key}",status="active",
              description="Customer private-L3 membership; authored spoke-to-hub traffic demand",
              comments="Peer membership is service inventory, not a declared all-to-all traffic matrix or configured BGP sessions."),
              dict(provider_network="provider-network/operator",type="virtual-circuit-type/private-l3",tenant=tenant,provider_account=account))


def _account(w,key,provider,label):
    return w.add("provider_account",key,dict(name=f"{w.recipe['namespace']} {label}",account=f"{w.recipe['namespace']}-{label}",
          description="Commercial inventory account; no credentials or live purchase"),dict(provider=provider))


def _site_network(site,role,prefixlen,offset,vid):
    w = site.w; container = w.site_network(site.id); vrf = site.vrf(role)
    reservation = f"prefix/{site.id}/reservation"
    if reservation not in w.objects:
        w.add("prefix",reservation,dict(prefix=str(container),status="container",description="Stable site reservation"),
              dict(vrf=vrf,tenant=site.tenant,scope_site=site.key))
    net = ipaddress.ip_network((int(container.network_address)+offset,prefixlen))
    vlan = w.add("vlan",f"vlan/{site.id}/{role}",dict(name=f"{site.code}-{role}",vid=vid,status="active",description=f"{role} segment"),
                 dict(site=site.key,tenant=site.tenant))
    w.add("prefix",f"prefix/{site.id}/{role}",dict(prefix=str(net),status="active",description=f"{role} addressed site segment"),
          dict(vrf=vrf,tenant=site.tenant,scope_site=site.key,vlan=vlan))
    site.nets[role] = vlan,net


def _ip(w,port,net,host,vrf,tenant,primary=False):
    owner = w.obj(port)["refs"]["device"]
    key = w.add("ip_address",f"ip/{port}",dict(address=f"{net[host]}/{net.prefixlen}",status="active",
          description=f"{w.obj(owner)['attrs']['name']} {w.obj(port)['attrs']['name']}",
          dns_name=f"{w.obj(owner)['attrs']['name']}.{w.recipe['namespace']}.example"),
          dict(assigned_object=port,vrf=vrf,tenant=tenant))
    w.obj(port)["refs"]["vrf"] = vrf
    if primary: w.obj(owner)["refs"]["primary_ip4"] = key
    return key


def _link_prefix(w,key,vrf,tenant):
    # Globally unique slots also prevent overlapping physical reservations across VRFs.
    slot = w.reserve("provider-link-prefixes",key,16384)
    base = int(w.pool.broadcast_address)-65535
    net = ipaddress.ip_network((base+2*slot,31))
    w.add("prefix",f"prefix/link/{key}",dict(prefix=str(net),status="active",description="Point-to-point routed attachment; far end may be explicitly unmodeled"),
          dict(vrf=vrf,tenant=tenant))
    return net


def _routed_pair(w,key,a,b,vrf="vrf/provider",tenant="tenant"):
    net = _link_prefix(w,key,vrf,tenant)
    _ip(w,a,net,0,vrf,tenant); _ip(w,b,net,1,vrf,tenant)


def _circuit(w,key,provider,account,kind,a_site,a_port,z_site,z_port,rate_mbps,tenant="tenant",handoff_mbps=None):
    handoff = handoff_mbps or rate_mbps
    try:
        installed = (date.fromisoformat(w.recipe["as_of"])-timedelta(days=w.choose(key,"provider-install-age",range(365,1096)))).isoformat()
    except OverflowError as exc:
        raise DesignError("as_of is too early for the authored provider procurement history") from exc
    cid = f"{w.recipe['namespace']}-{key.removeprefix('circuit/').replace('/','-')}"
    w.add("circuit",key,dict(cid=cid,status="active",commit_rate=rate_mbps*1000,install_date=installed,
          description=f"{rate_mbps} Mbps {kind} commitment on {handoff} Mbps local handoff",
          comments="Authored purchased inventory; no forwarding acceptance test or physical duct diversity is claimed."),
          dict(provider=provider,provider_account=account,type=f"circuit-type/{kind}",tenant=tenant),
          dict(procurement=dict(cohort=f"provider-{kind}",handoff_mbps=handoff)))
    for side,site,port in (("A",a_site,a_port),("Z",z_site,z_port)):
        target = site.key if isinstance(site,Site) else site
        term = w.add("circuit_termination",f"{key}/{side}",dict(term_side=side,port_speed=handoff*1000,
                     description="Local routed handoff" if port else "Unmodeled external provider network"),dict(circuit=key,termination=target))
        if port:
            site.cable(port,term,"cat6" if w.obj(port)["attrs"]["type"] == "1000base-t" else "smf")
            w.obj(port)["attrs"]["speed"] = handoff*1000
            site.contract["required_connections"].append(dict(a=port,b=term))
    return key


def _console_management(site,switch):
    equipment.enrich_site(site,demonstrations=False)
    vlan,_ = site.network("management")
    for device in list(site.devices):
        if site.w.obj(device)["refs"]["device_type"] != "hardware/console-server": continue
        port = site.interface(device,"mgmt0"); peer = site.interface(switch,"GigabitEthernet1/0/23")
        site.cable(port,peer)
        for key in (port,peer):
            site.w.obj(key)["attrs"]["mode"] = "access"
            site.w.obj(key)["refs"]["untagged_vlan"] = vlan
        site.address(port,"management",host=3,primary=True,device=device)
        site.contract["required_connections"].append(dict(a=port,b=peer))


def _pop(w,item):
    site = Site(w,f"pop-{item['key']}","pop","Provider routing, local management and carrier handoffs",routing_domain="vrf/provider")
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
                     description="Inband management loopback; dedicated fxp0 remains unaddressed and uncabled"),dict(device=device,vrf="vrf/provider"))
        slot = w.reserve("provider-loopbacks",device,32768)
        net = ipaddress.ip_network((int(w.pool.broadcast_address)-32767+slot,32))
        w.add("prefix",f"prefix/loopback/{device}",dict(prefix=str(net),status="active",description="Router inband management loopback"),dict(vrf="vrf/provider",tenant="tenant"))
        _ip(w,loop,net,0,"vrf/provider","tenant",True)
    a,b = [site.interface(d,"et-0/0/0") for d in routers]
    site.cable(a,b,"smf"); _routed_pair(w,f"pair/{site.id}",a,b)
    site.contract["required_connections"].append(dict(a=a,b=b))
    switch = site.device("access","mgmt-01","management")
    vi = site.virtual_interface(switch,"Vlan10","management"); site.address(vi,"management",host=1,primary=True,device=switch)
    for i,router in enumerate(routers):
        a = site.interface(switch,f"TenGigabitEthernet1/1/{i+1}"); b = site.interface(router,"xe-0/1/6")
        w.obj(a)["attrs"]["speed"] = 10000000
        site.cable(a,b,"smf"); _routed_pair(w,f"management/{site.id}/{'ab'[i]}",a,b)
        site.contract["required_connections"].append(dict(a=a,b=b))
    _console_management(site,switch)
    site.contract.update(required_device_roles={"role/provider-edge":2,"role/management":1},
                         demand=dict(provider_routers=2,direct_attachment_capacity=12),management_mode="in-band")
    site.contract["assumptions"].extend([
        "Two MX204 chassis occupy separate racks. Exactly three 100G ports per chassis are enabled in the reviewed port-level mode; one is local peer and two are finite transport attachments.",
        "PE lo0 is inband management. fxp0 is present, unaddressed and uncabled. Management-switch SVI and console share a /26 reached through two real routed data-plane /31 uplinks; no independent out-of-band network is modeled.",
        "Installed PSU inventory comes from pinned sources; 320 W chassis planning allowance is synthetic, separate from a PSU output rating."])
    site.power()
    return site,routers


def _topology(w,pop_sites):
    order = sorted(w.recipe["pops"],key=lambda p:w.reservations["provider-pop-order"][p["key"]])
    graph = []
    for site,routers in pop_sites.values(): graph.append((f"pair/{site.id}",routers[0],routers[1],100000))
    def span(key,a,b,ordinal):
        sites = [pop_sites[w.obj(d)["refs"]["site"].removeprefix("site/pop-")][0] for d in (a,b)]
        ports = [sites[i].interface(d,f"et-0/0/{1+w.reserve(f'provider-transport-ports/{d}',key,2)}") for i,d in enumerate((a,b))]
        side = "ab"[ordinal%2]
        _circuit(w,key,f"provider/transport-{side}",f"provider-account/provider/transport-{side}","backbone",sites[0],ports[0],sites[1],ports[1],100000)
        _routed_pair(w,key,*ports); graph.append((key,a,b,100000))
    for i in range(3):
        a = pop_sites[order[i]["key"]][1][1]; b = pop_sites[order[(i+1)%3]["key"]][1][0]
        span(f"circuit/backbone/seed-{i+1:02}",a,b,i)
    for index,pop in enumerate(order[3:],3):
        key = pop["key"]; parents=[]
        for lane,side in enumerate(("a","b")):
            scope = f"provider-parents/{key}/{side}"
            existing = w.reservations.get(scope,{})
            if existing:
                if len(existing)!=1 or next(iter(existing.values())) != 0:
                    raise DesignError(f"{scope}: malformed permanent transport parent")
                parent = next(iter(existing))
            else:
                candidates = [d for p in order[:index] for d in pop_sites[p["key"]][1]
                    if len(w.reservations.get(f"provider-transport-ports/{d}",{})) < 2 and
                    all(w.obj(d)["refs"]["site"] != w.obj(other)["refs"]["site"] for other in parents)]
                if not candidates: raise DesignError(f"PoP {key}: no two distinct old PoPs have free transport positions")
                parent = min(candidates,key=lambda d:(
                    places.METROS.index(next(row for row in places.METROS if row[0].lower()==w.provider_metros[w.obj(d)['refs']['site'].removeprefix('site/')])) !=
                    places.METROS.index(next(row for row in places.METROS if row[0].lower()==pop['metro'])),
                    w.choose(f"{key}/{side}/{d}","transport-parent",range(2**32)),d))
                w.reserve(scope,parent,1)
            prior_devices = {d for p in order[:index] for d in pop_sites[p["key"]][1]}
            if parent not in prior_devices or any(w.obj(parent)["refs"]["site"] == w.obj(d)["refs"]["site"] for d in parents):
                raise DesignError(f"{scope}: parent must be a distinct earlier PoP")
            parents.append(parent)
            span(f"circuit/backbone/{key}/{side}",pop_sites[key][1][lane],parent,3+2*(index-3)+lane)
    return graph


def _service_port(w,pop_sites,pop,target):
    slot = w.reserve(f"provider-service-ports/{pop}",target,12)
    site,routers = pop_sites[pop]
    return site,site.interface(routers[slot%2],f"xe-0/1/{slot//2}")


def _customer(w,sid,c,pop,number,pop_sites):
    key=c["key"]; tenant=f"tenant/cust-{key}"; vrf=f"vrf/customer/{key}"
    site = Site(w,sid,"customer","Private-L3 customer premises and wired office",tenant=tenant,routing_domain=vrf)
    w.obj(site.key)["refs"]["asns"] = [f"asn/customer/{key}"]
    _site_network(site,"management",26,0,10); _site_network(site,"clients",25,128,20)
    room = places.provider_office(site)
    edge = site.device("edge","edge-01","customer-edge")
    switch = site.device("access","access-01","access")
    a,b = site.interface(edge,"port1"),site.interface(switch,"GigabitEthernet1/0/24")
    site.cable(a,b); trunk(site,(a,b),("management","clients")); site.contract["required_connections"].append(dict(a=a,b=b))
    for role,name in (("management","Management"),("clients","Clients")):
        vi = site.virtual_interface(edge,name,role); w.obj(vi)["refs"]["parent"] = a
        site.address(vi,role,host=1,primary=role=="management",device=edge)
    vi = site.virtual_interface(switch,"Vlan10","management"); site.address(vi,"management",host=2,primary=True,device=switch)
    panel = site.device("patch-panel","patch-01","patch-panel") if w.recipe["patching"] == "panels" else None
    for i in range(c["lan_endpoints"]):
        device = site.device("endpoint",f"pc-{i+1:03}","workstation",racked=False,
                             meta=dict(endpoint=True,network="clients",power_scope="local outlet"))
        places.provider_endpoint(site,device,room,i+1)
        source,target = site.interface(switch,f"GigabitEthernet1/0/{i+1}"),site.interface(device,"eth0")
        vlan,_ = site.network("clients")
        for port in (source,target):
            w.obj(port)["attrs"]["mode"] = "access"; w.obj(port)["refs"]["untagged_vlan"] = vlan
        site.patch(switch,f"GigabitEthernet1/0/{i+1}",device,"eth0",panel,i+1)
        site.address(target,"clients",primary=True,device=device)
    pop_site,pe_port = _service_port(w,pop_sites,pop,sid)
    hub = pop==c["hub_pop"] and number==1
    usable = 1-Decimal(str(w.recipe["reserve_fraction"]))
    rate = c["hub_commit_mbps"] if hub else next(tier for tier in w.recipe["wan_tiers_mbps"] if tier*usable>=c["site_peak_mbps"])
    port=site.interface(edge,"wan1"); circuit=f"circuit/customer/{sid}"
    _circuit(w,circuit,"provider/operator",f"provider-account/customer/{key}","access",site,port,pop_site,pe_port,rate,tenant,handoff_mbps=1000)
    _routed_pair(w,circuit,port,pe_port,vrf,tenant)
    vi=w.add("interface",f"{edge}/if/PrivateL3",dict(name="PrivateL3",type="virtual",enabled=True,description="Private routed service membership over this actual circuit"),
             dict(device=edge,parent=port,vrf=vrf))
    w.add("virtual_circuit_termination",f"virtual-circuit-termination/{sid}",dict(role="peer",description="Customer service membership; traffic demand is separately directed to its hub"),
          dict(virtual_circuit=f"virtual-circuit/customer/{key}",interface=vi))
    site.contract.update(required_device_roles={"role/customer-edge":1,"role/access":1},endpoint_count=c["lan_endpoints"],
                         demand=dict(lan_endpoints=c["lan_endpoints"],peak_mbps=c["site_peak_mbps"],hub=hub,commit_mbps=rate),access_hardware="access")
    site.contract["assumptions"].extend(["The private-L3 customer has one physical access circuit and one CE. A CE, access-link or serving-PE failure can isolate this premises.",
        "Twelve fixed office positions and a same-room access switch preserve existing endpoint cables and addresses during growth. Customer LAN is wired; no wireless service is modeled."])
    _console_management(site,switch); site.power()
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
    graph=_topology(w,pop_sites)
    ordered=sorted(recipe["pops"],key=lambda p:order[p["key"]])
    for index,side in enumerate(("a","b")):
        site,routers=pop_sites[ordered[index]["key"]]; port=site.interface(routers[index],"xe-0/1/7")
        circuit=f"circuit/transit/{side}"
        _circuit(w,circuit,f"provider/transit-{side}",f"provider-account/provider/transit-{side}","transit",site,port,f"provider-network/transit/{side}",None,10000)
        net=_link_prefix(w,circuit,"vrf/provider","tenant"); _ip(w,port,net,0,"vrf/provider","tenant")
    for side in ("a","b"):
        _service_port(w,pop_sites,recipe[f"noc_pop_{side}"],f"noc/{side}")
    dc=Site(w,"dc-01","dc","Provider NOC services, inventory and monitoring")
    w.obj(dc.key)["refs"]["asns"]=["asn/operator"]
    def noc(site,edge,side,ordinal):
        if ordinal != 1: raise DesignError("Provider NOC has exactly one purchased dual handoff; rebaseline a reviewed larger edge")
        pop,peer=_service_port(w,pop_sites,recipe[f"noc_pop_{side}"],f"noc/{side}")
        port=site.interface(edge,"wan1"); circuit=f"circuit/noc/{side}"
        _circuit(w,circuit,"provider/operator","provider-account/operator/noc","access",site,port,pop,peer,1000)
        _routed_pair(w,circuit,port,peer)
    datacenter.build(dc,workloads=workloads(recipe),wan_peak_mbps=recipe["noc_peak_mbps"],wan_attachment=noc,include_equipment=False,
        assumptions=[CAPACITY_SCOPE,"NOC service groups are authored: identity/provisioning per128 customer premises, DNS/monitoring per16 PoPs, minimum one group each; every group has two rack-separated replicas. These are synthetic inventory budgets, not measured throughput."])
    attachments=[_customer(w,sid,c,pop,n,pop_sites) for sid,c,pop,n in entries]
    dc.contract["provider"]=dict(pop_count=len(pop_sites),customer_count=len(recipe["customers"]),customer_premises=len(entries),
        transport_spans=len(recipe["pops"])*2-3,capacity=_capacity(graph,attachments,recipe["reserve_fraction"]),
        management_mode="in-band",transit_remote_ownership="unknown",wireless="omitted; wired private-L3 service scope")
    equipment.enrich(w); optics.enrich(w); poe.enrich(w); ipv6.enrich(w); networking.macs(w); operations.supporting_records(w)
    return w.finish()
