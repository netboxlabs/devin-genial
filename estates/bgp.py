"""Provider-backbone BGP inventory: routing policies, peer groups and sessions.

Scope, stated once and enforced everywhere below: these are **documentation
records**, exactly like the estate's inert webhook. Genial generates no device
configuration, applies nothing to any device, establishes no session and claims
no protocol state — no convergence, no route exchange, no policy evaluation, no
session establishment. A ``status`` of ``active`` is the plugin's own inventory
status choice for an intended peering, not observed state. The routing policies
deliberately carry no rules: a named policy is inventory; a rule set would read
as configuration.

Why the records exist: the ``netbox_bgp`` plugin's tables and Visual Explorer's
``bgp-topology`` view otherwise render empty against a provider estate that
does have upstreams, customers and an internal core. Every field below is
derived from something the finished graph already asserts — the PE loopback
addresses, the transit and access circuits' own terminations and cables, and
the ASNs the sites already reference. Nothing is invented per site.

Transport: the three ``netbox_bgp`` models have no entity in the pinned Diode
SDK 1.14.0 ``IngestRequest``, so — exactly like the automation pack — they are
``diode.LOADER_ONLY_KINDS``: the wire package omits them and records the
omission in its manifest, and only ``just load`` delivers them, over the
bounded REST create path (``turbobulk.REST_CREATE_KINDS``). They carry
many-to-many policy lists the raw bulk path cannot express, and a plugin model
is not guaranteed to appear in an installed TurboBulk model registry.

iBGP topology: a route-reflector pair rather than a full mesh. The reflectors
are PE A at the first PoP in the permanent ``provider-pop-order`` ledger and PE
A at the first later PoP in a different metro, so one metro's loss cannot take
both; every other PE peers with both, and the two reflectors peer with each
other. That is 2*(2N-2)+1 sessions for N PoPs — linear, so the 64-PoP recipe
ceiling stays bounded (a full mesh would be 8,128 sessions at that ceiling),
and appending a PoP appends sessions without touching any existing one,
because the reflectors are chosen from permanent ordinals.

Dual-stack: when the estate carries ``ipv6_pool``, every peering above gains an
IPv6 twin over the same endpoints' IPv6 companions — loopback /128s for iBGP,
the /127 link addresses for customer eBGP and the /127 prefix for transit.
"""

from collections import defaultdict
from ipaddress import ip_interface

from . import fibre
from .model import DesignError


# Named policy inventory. No rules are recorded: a rule set would read as
# configuration, and this generator configures nothing.
POLICIES = (
    ("transit-in", "Transit Import", 100,
     "Import policy for upstream transit peers"),
    ("transit-out", "Transit Export", 110,
     "Export policy for upstream transit peers"),
    ("customer-in", "Customer Import", 200,
     "Import policy for private L3 customer edges"),
    ("customer-out", "Customer Export", 210,
     "Export policy for private L3 customer edges"),
)
# slug, display name, description, import policies, export policies, internal
GROUPS = (
    ("ibgp-core", "iBGP Core",
     "Internal peerings between provider edge loopbacks", (), (), True),
    ("transit", "Transit Upstream",
     "External upstream peerings at the backbone transit handoffs",
     ("transit-in",), ("transit-out",), False),
    ("customer", "Customer Private L3",
     "Customer edge peerings on private-L3 access circuits",
     ("customer-in",), ("customer-out",), False),
)
PE_ROLE = "role/provider-edge"
CE_ROLE = "role/customer-edge"
# Junos addresses the loopback on logical unit 0 (operations._loopback_units).
LOOPBACK = "lo0.0"


# A customer peering's inventory status follows its access circuit's
# lifecycle: one not yet in service is planned, one being withdrawn offline.
SESSION_STATUS = {"active": "active", "planned": "planned", "provisioning": "planned", "deprovisioning": "offline"}

def _index(world):
    """Finished-graph indexes every derivation below reads from."""
    interfaces, address_of, prefix_of, peers = {}, {4: {}, 6: {}}, {}, {}
    terminations = defaultdict(dict)
    for key, obj in world.objects.items():
        kind, attrs, refs = obj["kind"], obj["attrs"], obj["refs"]
        if kind == "interface":
            interfaces[(refs["device"], attrs["name"])] = key
        elif kind == "ip_address":
            target = refs.get("assigned_object")
            if target is not None:
                address_of[ip_interface(attrs["address"]).version][target] = key
        elif kind == "prefix":
            prefix_of[(refs.get("vrf"), attrs["prefix"])] = key
        elif kind == "cable":
            peers[refs["a"]], peers[refs["b"]] = refs["b"], refs["a"]
        elif kind == "circuit_termination":
            terminations[refs["circuit"]][attrs["term_side"]] = key
    return interfaces, address_of, prefix_of, peers, terminations


def enrich(world):
    """Attach the provider's BGP inventory to a finished provider graph."""
    if world.recipe["profile"] != "provider-backbone":
        raise DesignError("BGP inventory is modeled for the provider backbone only")
    objects = world.objects
    interfaces, address_of, prefix_of, peers, terminations = _index(world)

    def obj(key):
        return objects[key]

    families = (4, 6) if "ipv6_pool" in world.recipe else (4,)

    def address(port, what, family=4):
        # A Junos port is addressed on its logical unit 0 (provider._junos_units).
        unit = interfaces.get((obj(port)["refs"].get("device"), f"{obj(port)['attrs'].get('name')}.0"))
        key = address_of[family].get(port) or address_of[family].get(unit)
        if key is None:
            raise DesignError(f"BGP inventory needs an IPv{family} address on {what} ({port})")
        return key

    def suffix(family):
        return "" if family == 4 else "/ipv6"

    def label(family):
        return "" if family == 4 else " IPv6"

    def site_asn(site):
        asns = obj(site)["refs"].get("asns") or []
        if len(asns) != 1:
            raise DesignError(f"BGP inventory needs exactly one routing identity on {site}")
        return asns[0]

    def add(kind, key, attrs, refs=None, mirror=False):
        return world.add(kind, key, attrs, refs or {}, {"bgp": True, **({"mirror": True} if mirror else {})})

    for slug, name, weight, description in POLICIES:
        add("bgp_routing_policy", f"bgp-routing-policy/{slug}",
            {"name": name, "weight": weight, "description": description})

    # Every PE, in the estate's own permanent PoP order, then by device key.
    order = world.reservations.get("provider-pop-order", {})
    routers = []
    for key, entry in objects.items():
        if entry["kind"] != "device" or entry["refs"].get("role") != PE_ROLE:
            continue
        site = entry["refs"]["site"]
        pop = site.removeprefix("site/pop-")
        if pop not in order:
            raise DesignError(f"{key}: provider edge sits outside the permanent PoP order ledger")
        routers.append((order[pop], key, site))
    routers.sort()
    if len(routers) < 4:
        raise DesignError("BGP inventory needs at least two PoPs' worth of provider edges")

    operator_asn = site_asn(routers[0][2])
    for slug, name, description, imports, exports, internal in GROUPS:
        refs = {"local_as": operator_asn}
        if internal:
            refs["remote_as"] = operator_asn
        if imports:
            refs["import_policies"] = [f"bgp-routing-policy/{s}" for s in imports]
        if exports:
            refs["export_policies"] = [f"bgp-routing-policy/{s}" for s in exports]
        add("bgp_peer_group", f"bgp-peer-group/{slug}",
            {"name": name, "description": description}, refs)

    def session(key, local, local_address, remote_as, group, description,
                *, remote_address=None, remote_prefix=None, tenant=None, remote_label, status="active", mirror=False):
        local_site = obj(local)["refs"]["site"]
        refs = {"device": local, "site": local_site, "local_address": local_address,
                "local_as": site_asn(local_site), "remote_as": remote_as,
                "peer_group": f"bgp-peer-group/{group}"}
        if remote_address is not None:
            refs["remote_address"] = remote_address
        else:
            refs["remote_prefix"] = remote_prefix
        if tenant is not None:
            refs["tenant"] = tenant
        add("bgp_session", key,
            {"name": f"{obj(local)['attrs']['name']} to {remote_label}",
             "status": status, "description": description}, refs, mirror)

    def loopback(router, family):
        port = interfaces.get((router, LOOPBACK))
        if port is None:
            raise DesignError(f"{router}: BGP inventory needs the {LOOPBACK} router loopback")
        return port, address(port, f"{router} {LOOPBACK}", family)

    # --- iBGP: reflectors at PE A of two PoPs in different metros. ---
    metro = {p["key"]: p["metro"] for p in world.recipe["pops"]}
    pop_of = lambda site: site.removeprefix("site/pop-")
    first = next(entry for entry in routers if entry[1].endswith("/pe-a"))
    second = next((entry for entry in routers if entry[1].endswith("/pe-a") and
                   metro[pop_of(entry[2])] != metro[pop_of(first[2])]), None)
    if second is None:
        raise DesignError("The route reflectors need provider edges in two different metros")
    reflectors = [first, second]
    clients = [entry for entry in routers if entry not in reflectors]

    def ibgp(local, remote, description, mirror=False):
        for family in families:
            _, local_address = loopback(local, family)
            _, remote_address = loopback(remote, family)
            session(f"bgp-session/ibgp/{local.removeprefix('device/')}/"
                    f"{remote.removeprefix('device/')}{suffix(family)}",
                    local, local_address, site_asn(obj(remote)["refs"]["site"]), "ibgp-core",
                    description, remote_address=remote_address, mirror=mirror,
                    remote_label=f"{obj(remote)['attrs']['name']} iBGP{label(family)}")

    # Every peering is recorded from both ends (the mirror carries meta
    # "mirror"), so each loopback in the BGP view belongs to a named device
    # rather than rendering as a bare address.
    ibgp(reflectors[0][1], reflectors[1][1],
         "Internal peering between the two backbone route reflectors")
    ibgp(reflectors[1][1], reflectors[0][1],
         "Internal peering between the two backbone route reflectors", mirror=True)
    for _, client, _site in clients:
        for _, reflector, reflector_site in reflectors:
            ibgp(client, reflector,
                 "Route-reflector client peering to the backbone reflector at "
                 f"{obj(reflector_site)['attrs']['name']}")
            ibgp(reflector, client,
                 f"Route-reflector peering to client {obj(client)['attrs']['name']}", mirror=True)

    # Passive plant: a circuit lands on a patch panel's rear port and leaves by
    # the front port mapped to it, so a handoff is traced through panels.
    def trace(term):
        port = fibre.far_end(objects, peers[term], peers) if term in peers else None
        return port if objects.get(port, {}).get("kind") == "interface" else None

    # --- eBGP transit: attributed from each circuit's own terminations and cables. ---
    for circuit in sorted(key for key, entry in objects.items() if entry["kind"] == "circuit"):
        entry = obj(circuit)
        cid = entry["attrs"]["cid"]
        if entry["refs"].get("type") == "circuit-type/transit":
            ends = {side: trace(term) for side, term in terminations[circuit].items() if trace(term)}
            if len(ends) != 1:
                raise DesignError(f"{circuit}: transit needs exactly one local handoff")
            port = next(iter(ends.values()))
            local = obj(port)["refs"]["device"]
            upstream = obj(entry["refs"]["provider"])
            asns = upstream["refs"].get("asns") or []
            if len(asns) != 1:
                raise DesignError(f"{circuit}: the upstream provider has no single routing identity")
            for family in families:
                local_address = address(port, f"the {cid} transit handoff", family)
                ip = obj(local_address)
                network = str(ip_interface(ip["attrs"]["address"]).network)
                link = prefix_of.get((ip["refs"].get("vrf"), network))
                if link is None:
                    raise DesignError(f"{circuit}: the transit handoff has no reserved link prefix")
                session(f"bgp-session/{circuit.removeprefix('circuit/')}{suffix(family)}", local, local_address,
                        asns[0], "transit",
                        f"External transit peering over {cid}",
                        remote_prefix=link, remote_label=f"{upstream['attrs']['name']} transit{label(family)}")

    # --- Customer eBGP: one session per private-L3 attachment, all from the
    # finished graph. The CE's VPN subinterface names its access port; that
    # port's /31 names the PE service subinterface holding the other address;
    # the access port's cable reaches the NID whose network port is cabled to
    # the access circuit (its CID and lifecycle status).
    holders = defaultdict(list)
    for key, entry in objects.items():
        if entry["kind"] == "ip_address" and entry["refs"].get("assigned_object"):
            value = ip_interface(entry["attrs"]["address"])
            holders[(entry["refs"].get("vrf"), value.network)].append(key)
    circuit_of = {}
    for circuit, terms in terminations.items():
        for term in terms.values():
            if term in peers and objects[peers[term]]["kind"] == "interface":
                circuit_of.setdefault(obj(peers[term])["refs"]["device"], set()).add(circuit)
    for term in sorted(key for key, entry in objects.items() if entry["kind"] == "virtual_circuit_termination"):
        remote = obj(obj(term)["refs"]["interface"])["refs"].get("parent")
        remote_device = obj(remote)["refs"]["device"] if remote else None
        nid_port = peers.get(remote)
        circuits = circuit_of.get(obj(nid_port)["refs"].get("device"), set()) if nid_port in objects else set()
        if remote_device is None or obj(remote_device)["refs"].get("role") != CE_ROLE or len(circuits) != 1:
            raise DesignError(f"{term}: a VPN attachment needs its CE access port, cabled NID and one access circuit")
        circuit = next(iter(circuits))
        entry = obj(circuit)
        cid = entry["attrs"]["cid"]
        for family in families:
            remote_address = address(remote, f"the {cid} customer handoff", family)
            ip = obj(remote_address)
            others = [k for k in holders[(ip["refs"].get("vrf"), ip_interface(ip["attrs"]["address"]).network)] if k != remote_address]
            local_ports = [obj(k)["refs"]["assigned_object"] for k in others]
            if len(local_ports) != 1 or obj(obj(local_ports[0])["refs"]["device"])["refs"].get("role") != PE_ROLE:
                raise DesignError(f"{circuit}: the customer /31 needs exactly one provider-edge end")
            session(f"bgp-session/{circuit.removeprefix('circuit/')}{suffix(family)}", obj(local_ports[0])["refs"]["device"],
                    others[0], site_asn(obj(remote_device)["refs"]["site"]), "customer",
                    f"Private-L3 customer edge peering over {cid}",
                    remote_address=remote_address, tenant=entry["refs"].get("tenant"),
                    remote_label=f"{obj(remote_device)['attrs']['name']} customer{label(family)}",
                    status=SESSION_STATUS[entry["attrs"]["status"]])
