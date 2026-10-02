"""WP-C STUB of the PoP-plant contract that WP-B's ``estates/fibre.py`` provides.

Delete this module at integration once ``fibre.py`` exposes the same four
functions; ``provider._plant()`` prefers ``fibre`` whenever it has them. The
stub builds just enough plant (an aggregation switch per side, its 4x10G LAG to
the PE, a per-side NID-management VLAN and /26) for the services layer to be
generated and tested. It has no OSP panel: ``land`` cables the termination
straight to the UNI.

Contract (all keys are plan keys; ``site`` is the PoP's ``blocks.Site``):

``uni(w, site, side, target)``
    Reserve a customer UNI on AGG-<side> for ``target`` in the append-only
    ledger ``provider-agg-uni/<pop>/<side>`` (40 ports, xe-0/0/0-39) and
    return the interface key. Refuses past 40 with an actionable error.
``land(w, site, term, uni)``
    Physically land the PoP-side circuit termination ``term`` on ``uni``
    (real plant: term -> OSP rear n -> OSP front n -> UNI).
``lag(w, site, side, role)``
    The LAG on the home side: role "pe" is PE-<side> ``ae1`` (the parent of
    every service subinterface ``ae1.<vid>``), role "agg" is AGG-<side> ``ae0``.
    Callers append their service VLANs to both LAGs' ``tagged_vlans``.
``nid_management(w, site, side)``
    (vlan key, IPv4Network, vrf key) of the per-side NID management segment
    (VLAN 4001 for A, 4002 for B), gatewayed on PE-<side> ``ae1.400x`` at .1.
"""

import ipaddress

from .model import DesignError

UNI_PORTS = 40


def _agg(w, site, side):
    device = f"device/{site.id}/agg-{side}"
    if device in w.objects:
        return device
    device = site.device("aggregation", f"agg-{side}", "leaf", rack_domain="ab".index(side))
    pe = f"device/{site.id}/pe-{side}"
    # Stub only: the PoP's PDUs are already cabled, so the stub AGG claims none.
    for port in w.hardware("aggregation")["power_ports"]:
        w.obj(f"{device}/power/{port['name']}")["attrs"]["mark_connected"] = True
    lags = {}
    for owner, name, members in ((device, "ae0", [f"xe-0/0/{n}" for n in range(40, 44)]),
                                 (pe, "ae1", [f"xe-0/1/{n}" for n in range(4)])):
        lags[owner] = w.add("interface", f"{owner}/if/{name}", dict(name=name, type="lag", enabled=True, mode="tagged"),
                            dict(device=owner, tagged_vlans=[]))
        for member in members:
            w.obj(f"{owner}/if/{member}")["refs"]["lag"] = lags[owner]
            w.obj(f"{owner}/if/{member}")["attrs"]["speed"] = 10000000
    for n in range(4):
        site.cable(f"{device}/if/xe-0/0/{40+n}", f"{pe}/if/xe-0/1/{n}", "smf")
    vid = 4001 + "ab".index(side)
    vlan = w.add("vlan", f"vlan/{site.id}/nid-{side}/nid-management",
                 dict(name=f"NID Management {side.upper()}", vid=vid, status="active",
                      description="In-band NID management"), dict(site=site.key, tenant="tenant"))
    net = ipaddress.ip_network((int(w.site_network(site.id).network_address) + 128 + 64 * "ab".index(side), 26))
    w.add("prefix", f"prefix/nid-management/{site.id}/{side}", dict(prefix=str(net), status="active",
          description=f"{site.display} NID management {side.upper()}"),
          dict(vrf="vrf/provider", tenant="tenant"))
    gateway = w.add("interface", f"{pe}/if/ae1.{vid}", dict(name=f"ae1.{vid}", type="virtual", enabled=True, mode="access"),
                    dict(device=pe, parent=lags[pe], untagged_vlan=vlan, vrf="vrf/provider"))
    w.add("ip_address", f"ip/{gateway}", dict(address=f"{net[1]}/{net.prefixlen}", status="active"),
          dict(assigned_object=gateway, vrf="vrf/provider", tenant="tenant"))
    for lag in lags.values():
        w.obj(lag)["refs"]["tagged_vlans"].append(vlan)
    w.obj(device)["meta"]["nid_management"] = (vlan, str(net))
    return device


def uni(w, site, side, target):
    device = _agg(w, site, side)
    slot = w.reserve(f"provider-agg-uni/{site.id.removeprefix('pop-')}/{side}", target, 10**6)
    if slot >= UNI_PORTS:
        raise DesignError(f"{site.id}: AGG-{side.upper()} has {UNI_PORTS} customer UNIs and all are in use; "
                          "add a PoP (the MX304 hub/access design is the reviewed growth path)")
    return site.interface(device, f"xe-0/0/{slot}")


def land(w, site, term, port):
    site.cable(term, port, "smf")


def lag(w, site, side, role):
    _agg(w, site, side)
    return f"device/{site.id}/{'pe' if role == 'pe' else 'agg'}-{side}/if/{'ae1' if role == 'pe' else 'ae0'}"


def nid_management(w, site, side):
    vlan, net = w.obj(_agg(w, site, side))["meta"]["nid_management"]
    return vlan, ipaddress.ip_network(net), "vrf/provider"
