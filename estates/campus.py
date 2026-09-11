"""Shared campus gateway and room-local access construction.

Profiles supply placed endpoints and segment policy. Stable mode fills pairs of
switches in alternating lanes, preserving endpoint port reservations as demand
grows; legacy bank mode retains the original allocation and canonical output.
"""

import math
from collections import defaultdict
from decimal import Decimal

from .blocks import trunk
from .model import DesignError
from .poe import planning_budget


def _reserve_endpoints(w, scope, members, hardware, usable):
    """Keep occupied ports; fill unused positions only when PoE also fits."""
    items = w.reservations.setdefault(scope, {})
    demand = {}
    for endpoint, _ in members:
        alias = w.obj(endpoint)["refs"]["device_type"].removeprefix("hardware/")
        demand[endpoint] = w.catalog["models"][alias].get("poe_pd", {}).get("pse_reservation_mw", 0)
    if items.keys() - demand.keys():
        raise DesignError(f"{scope}: reserved endpoints cannot be removed or moved; use a new baseline")
    occupied, loads = set(), defaultdict(int)
    ceiling, budget = 38 * usable, planning_budget(hardware)

    def switch(slot):
        pair, lane = divmod(slot, 2 * usable)
        return 2 * pair + lane % 2

    # Preload every incumbent, including those that sort after new demand.
    for endpoint, slot in items.items():
        if type(slot) is not int or not 0 <= slot < ceiling or slot in occupied:
            raise DesignError(f"{scope}: invalid or duplicate physical port reservation")
        occupied.add(slot)
        loads[switch(slot)] += demand[endpoint]
    if any(load > budget for load in loads.values()):
        raise DesignError(f"{scope}: retained PoE reservations exceed the supply-loss planning budget; rebaseline")
    # ponytail: bounded to 38 switches x 24 ports per closet. A larger chassis
    # grammar would warrant indexed free lists; this finite first-fit keeps holes.
    for endpoint, _ in members:
        if endpoint in items:
            continue
        slot = next((slot for slot in range(ceiling) if slot not in occupied
                     and loads[switch(slot)] + demand[endpoint] <= budget), None)
        if slot is None:
            raise DesignError(f"{scope}: no port with sufficient PoE budget for {endpoint}; expand the supported access block")
        items[endpoint] = slot
        occupied.add(slot)
        loads[switch(slot)] += demand[endpoint]
        w._next[scope] = max(w._next.get(scope, 0), slot + 1)
    return dict(items)


def aggregation(site, network_roles, wan_peak_mbps, compact=False):
    """Connect WAN edges, optional distribution pair, and addressed gateways."""
    w = site.w
    dist = [] if compact else [site.device("leaf", f"dist-{side}", "distribution") for side in ("a", "b")]
    edges = [site.device("edge", f"edge-{side}", "wan-edge") for side in ("a", "b")]
    for index, edge in enumerate(edges):
        for side, parent in enumerate(dist):
            a, b = site.interface(edge, f"x{side+1}"), site.interface(parent, f"Ethernet{index+1}")
            site.cable(a, b, "smf")
            trunk(site, [a, b], network_roles)
        if dist:
            site.redundant(edge, dist)
        site.wan(edge, "ab"[index], 1, demand_mbps=wan_peak_mbps)
    if dist:
        a, b = [site.interface(d, "Ethernet49/1") for d in dist]
        site.cable(a, b, "aoc")
        trunk(site, [a, b], network_roles)
    upstreams = edges if compact else dist
    for i, parent in enumerate(upstreams):
        if compact:
            bridge = w.add("interface", f"{parent}/if/branch-lan",
                           {"name": "branch-lan", "type": "bridge", "enabled": True,
                            "description": "Logical branch LAN bridge; forwarding policy is outside this dataset"},
                           {"device": parent})
            trunk(site, [bridge], network_roles)
            for name in ("x1", "x2", "port12"):
                w.obj(site.interface(parent, name))["refs"]["bridge"] = bridge
        for role in network_roles:
            vlan, _ = site.network(role)
            vi = site.virtual_interface(parent, f"Vlan{w.obj(vlan)['attrs']['vid']}", role)
            if compact:
                w.obj(vi)["refs"]["parent"] = bridge
            site.address(vi, role, host=i+1, primary=role == "management", device=parent)
    return dist, edges, upstreams


def access(site, endpoints, upstreams, network_roles, design, compact=False, stable=False):
    """Build access using actual catalog ports and each endpoint's serving room.

    Stable allocations alternate endpoints across a pair's two switches, then
    add a new pair after both reach reserved-headroom capacity. The room ledger
    binds endpoint identities to physical switch/port positions. Removing demand
    never recycles slots; profile growth policy must decide whether to reject it.
    """
    w = site.w
    hardware = w.catalog["models"][design["access_hardware"]]
    access_ports, uplink_ports = hardware["access_ports"], hardware["uplink_ports"]
    # Critical endpoint cohorts alternate between access switches; this is branch
    # fault isolation, not a claim that a single-homed endpoint is redundant.
    usable = (int(len(access_ports) * (1-Decimal(str(w.recipe["reserve_fraction"])))) if stable else
              math.floor(len(access_ports) * (1-w.recipe["reserve_fraction"])))
    if usable < 1:
        raise DesignError(f"{site.name}: reserve leaves no usable access ports; lower reserve or select larger hardware")
    areas = defaultdict(list)
    for endpoint, segment in endpoints:
        areas[w.obj(endpoint)["meta"]["placement"]["cable_origin"]].append((endpoint, segment))
    slots = {}
    if stable:
        for room, members in areas.items():
            scope = f"access-endpoints/{site.id}/{room}"
            slots[room] = _reserve_endpoints(w, scope, members, hardware, usable)
        area_counts = {room: max(2, 2*math.ceil((max(slots[room].values())+1) / (2*usable)))
                       for room in areas}
    else:
        area_counts = {room: max(2, math.ceil(len(members) / usable)) for room, members in areas.items()}
    access_count = sum(area_counts.values())
    if compact and access_count != 2:
        raise DesignError(f"{site.name}: compact branches support two access switches; requested reserve needs {access_count}")
    if access_count > 38:
        raise DesignError(f"{site.name}: {access_count} access switches exceed 38 supported distribution attachments")
    switches, area_switches, area_panels = [], {}, {}
    for room, count in area_counts.items():
        area_switches[room], area_panels[room] = [], []
        prefix = site.room_prefix(room)
        for i in range(count):
            switch = site.device(design["access_hardware"], f"{prefix}{design['label']}{i+1:02}", "access", location=room)
            panel = site.device("patch-panel", f"{prefix}patch-{i+1:02}", "patch-panel", location=room) if w.recipe["patching"] == "panels" else None
            switches.append(switch)
            area_switches[room].append(switch)
            area_panels[room].append(panel)
            upstream_slot = w.reserve(f"access-uplinks/{site.id}", switch, 38)
            parents = upstreams if design["upstreams"] == 2 else [upstreams[upstream_slot % 2]]
            for j, parent in enumerate(parents):
                a = site.interface(switch, uplink_ports[j])
                b = site.interface(parent, f"x{i+1}" if compact else f"Ethernet{upstream_slot+3}")
                site.cable(a, b, "smf")
                trunk(site, [a, b], network_roles)
            site.redundant(switch, parents, minimum=design["upstreams"])
    if compact:
        # One shared broadcast domain must remain connected even when inherited
        # switches have only one direct edge attachment apiece. This is an
        # explicit VLAN trunk, not a stack/MLAG or a claim about STP convergence.
        a, b = [site.interface(switch, uplink_ports[2]) for switch in switches]
        site.cable(a, b, "smf")
        trunk(site, [a, b], network_roles)
    for room, members in areas.items():
        for index, (endpoint, segment) in enumerate(members):
            if stable:
                pair, pair_slot = divmod(slots[room][endpoint], 2*usable)
                switch_index, port = 2*pair + pair_slot % 2, pair_slot // 2 + 1
            else:
                switch_index, port = index % area_counts[room], index // area_counts[room] + 1
            switch = area_switches[room][switch_index]
            site.patch(switch, access_ports[port-1], endpoint, "eth0", area_panels[room][switch_index], port)
            vlan = site.network(segment)[0]
            for p in (site.interface(switch, access_ports[port-1]), site.interface(endpoint, "eth0")):
                w.obj(p)["attrs"]["mode"] = "access"
                w.obj(p)["refs"]["untagged_vlan"] = vlan
            site.address(site.interface(endpoint, "eth0"), segment, primary=True, device=endpoint)
    return dict(access_devices=switches, access_usable_ports=usable,
                access_count=access_count, area_counts=area_counts)
