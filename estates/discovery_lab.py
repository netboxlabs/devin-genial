"""Provider-only network lab: a staging replica that a real orb-agent discovers.

Enabled by the ``discovery_lab`` recipe key (off by default). The lab is a
small, clearly labelled slice of the estate — a ``Network Lab`` room and rack
at the provider NOC holding Nokia 7220 IXR-D2L records — that mirrors the
wiring of the first PoP in the permanent ``provider-pop-order`` ledger: its PE
pair plus PE A's first-ordered backbone neighbour (``nodes = 4`` adds PE B's).
``lab/discovery/render.py`` reads these records from the plan and renders the
containerlab topology, SR Linux startup configs and orb-agent policy from them,
so the plan is the single source of truth for what discovery must find.

Why a separate slice rather than the production routers: an SR Linux container
reports a ``7220 IXR-D2L``, ``ethernet-1/N`` ports and a simulator serial.
Matched against the MX204 records it would surface dozens of deviations that
are artefacts of the demo, not drift. So every lab record states what
discovery will truly report — model, serial and platform strings verbatim —
and each device's comments name the production router it mirrors.

Isolation is a contract checked independently by ``validate_provider``: lab
addresses come only from RFC 2544 benchmarking space ``198.18.0.0/15``
(reserved for device-test labs, disjoint from every estate pool), lab devices
carry no VRF, circuit, BGP session or production cable, and they draw no
modeled power. The lab is documentation of a container lab, not a production
claim: no physical hardware, optics or customer traffic.
"""

import hashlib
import ipaddress
import re

from .model import DesignError

ALIAS = "lab-router"
ROLE = "role/lab-router"
MARK = {"discovery_lab": True}
LAB_POOL = ipaddress.ip_network("198.18.0.0/15")
MGMT = ipaddress.ip_network("198.18.0.0/24")
LINKS = ipaddress.ip_network("198.19.0.0/24")
LOOPBACKS = ipaddress.ip_network("198.19.255.0/24")
FAST, SLOW = range(49, 57), range(1, 49)  # D2L QSFP28 and SFP28 cage numbers


def comments(prod_name):
    return ("Software lab: a Nokia SR Linux container emulating this chassis under containerlab. "
            f"It mirrors the wiring of production router {prod_name}, not its hardware or addressing. "
            "No physical hardware, optics or production traffic.")


def natural(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def resolve(raw):
    """``false``/absent, ``true`` (three nodes) or ``{nodes = 3|4}``."""
    if raw is None or raw is False:
        return False
    if raw is True:
        return {"nodes": 3}
    if isinstance(raw, dict) and raw.keys() <= {"nodes"} and raw.get("nodes", 3) in (3, 4) \
            and type(raw.get("nodes", 3)) is int:
        return {"nodes": raw.get("nodes", 3)}
    raise DesignError("discovery_lab must be true, false or a table with nodes = 3 or 4 "
                      "(each SR Linux container needs about 1.8 GB of lab VM memory)")


def select(objects, order, size):
    """``size`` production PEs and the routed /31 links among them, from the finished graph."""
    pes = {k for k, o in objects.items()
           if o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge"}
    pop_of = {k: objects[k]["refs"]["site"].removeprefix("site/pop-") for k in pes}
    # Routed adjacency from the addresses themselves: a /31 with one PE
    # interface on each end, independent of how cables or circuits model it.
    ends = {}
    for o in objects.values():
        if o["kind"] != "ip_address":
            continue
        address = ipaddress.ip_interface(o["attrs"]["address"])
        iface = objects.get(o["refs"].get("assigned_object"), {})
        if address.version == 4 and address.network.prefixlen == 31 and iface.get("kind") == "interface":
            ends.setdefault(address.network, []).append((iface["refs"]["device"], iface["key"]))
    links = sorted((sorted(pair) for pair in ends.values()
                    if len(pair) == 2 and all(d in pes for d, _ in pair)), key=str)

    def rank(dev):
        return order.get(pop_of[dev], 1 << 30), natural(dev)
    first = min(order, key=order.get)
    nodes = sorted((d for d in pes if pop_of[d] == first), key=rank)
    if len(nodes) != 2:
        raise DesignError(f"discovery lab: PoP {first} does not have a PE pair")
    for anchor in list(nodes)[:size - 2]:
        neighbours = sorted({d for pair in links for d, _ in pair
                             if anchor in (pair[0][0], pair[1][0]) and d != anchor and d not in nodes},
                            key=rank)
        if not neighbours:
            raise DesignError(f"discovery lab: {anchor} has no PE neighbour outside its PoP")
        nodes.append(neighbours[0])
    chosen = set(nodes)
    return nodes, [pair for pair in links if pair[0][0] in chosen and pair[1][0] in chosen]


def add_discovery_lab(world):
    """Append the lab slice to a finished provider graph; a no-op unless enabled."""
    setting = world.recipe.get("discovery_lab")
    if not setting:
        return
    if world.recipe["profile"] != "provider-backbone":
        raise DesignError("discovery_lab is modeled for the provider backbone only")
    objects, ns = world.objects, world.recipe["namespace"]
    prod_nodes, prod_links = select(objects, world.reservations["provider-pop-order"], setting["nodes"])
    site = "site/dc-01"
    stem = "dc-01"
    room, rack = f"location/{stem}/network-lab", f"rack/{stem}/network-lab/lab-01"
    spec = world.catalog["models"][ALIAS]
    manufacturer, platform = f"manufacturer/{spec['manufacturer']}", f"platform/{spec['platform']['slug']}"
    salt = int(hashlib.sha256(f"genial-discovery/{ns}".encode()).hexdigest(), 16) % 256

    def add(kind, key, attrs, refs=None, meta=None):
        return world.add(kind, key, attrs, refs or {}, {**MARK, **(meta or {})})

    if manufacturer not in objects:
        add("manufacturer", manufacturer, {"name": spec["manufacturer"], "slug": spec["manufacturer"].lower()})
    add("device_type", f"hardware/{ALIAS}",
        {k: spec[k] for k in ("model", "slug", "part_number", "u_height", "is_full_depth")}
        | {"comments": "Model string as the SR Linux container reports it; front panel per the pinned "
                       "devicetype-library Nokia/7220-IXR-D2L-25-100GE.yaml. Software emulation only."},
        {"manufacturer": manufacturer})
    add("platform", platform, {"name": spec["platform"]["name"], "slug": f"{ns}-{spec['platform']['slug']}",
                               "description": "Nokia SR Linux, named exactly as Orb device discovery reports it"},
        {"manufacturer": manufacturer})
    add("device_role", ROLE, {"name": "Lab Router", "slug": f"{ns}-lab-router", "color": "ff6f00",
                              "description": "Isolated staging router; never carries customer traffic"})
    add("location", room, {"name": "Network Lab", "slug": f"{ns}-{stem}-network-lab", "status": "active",
                           "description": "Isolated software-staging lab; containerised network OS, no production links"},
        {"site": site, "tenant": "tenant"}, {"floor": 1, "space_type": "lab"})
    add("rack", rack, {"name": "L01", "facility_id": "NL-01-L01", "status": "active", "u_height": 24, "width": 19,
                       "form_factor": "4-post-cabinet", "asset_tag": f"{ns.upper()}-{world.reserve('asset-tags', rack, 100000) + 1:05}",
                       "description": "Lab server cabinet hosting the containerlab VM"},
        {"site": site, "location": room, "role": "rack-role/network", "tenant": "tenant"})
    add("prefix", "prefix/lab/pool", {"prefix": str(LAB_POOL), "status": "container",
                                      "description": "RFC 2544 benchmarking space reserved for the network lab"},
        {"tenant": "tenant"})
    add("prefix", "prefix/lab/management", {"prefix": str(MGMT), "status": "active",
                                            "description": "Network lab out-of-band management"},
        {"tenant": "tenant", "role": "ip-role/management"})

    nodes = []
    for index, prod in enumerate(prod_nodes):
        name = "lab-" + objects[prod]["attrs"]["name"]
        if len(name) > 63:
            raise DesignError(f"discovery lab hostname {name} exceeds 63 characters")
        nodes.append({"index": index, "prod": prod, "name": name, "key": f"device/{stem}/network-lab/{name}",
                      "base": bytes([0x1A, salt, index, 0, 0, 0]), "ports": {}})

    # Each mirrored production port takes the next free front-panel cage of the
    # same speed class, in natural production-interface order per node.
    free = {n["prod"]: {"fast": list(FAST), "slow": list(SLOW)} for n in nodes}
    ports = {}
    for node in nodes:
        for iface in sorted((i for pair in prod_links for d, i in pair if d == node["prod"]), key=natural):
            pool = free[node["prod"]]["fast" if (objects[iface]["attrs"].get("speed") or 0) >= 100_000_000 else "slow"]
            if not pool:
                raise DesignError(f"discovery lab: {node['name']} has no free port for {iface}")
            ports[iface] = (node, f"ethernet-1/{pool.pop(0)}")
    links = []
    for number, ((_, a_if), (_, b_if)) in enumerate(prod_links):
        net = list(LINKS.subnets(new_prefix=31))[number]
        (a, a_port), (b, b_port) = ports[a_if], ports[b_if]
        a["ports"][a_port] = {"peer": f"{b['name']} / {b_port}", "ip": f"{net[0]}/31"}
        b["ports"][b_port] = {"peer": f"{a['name']} / {a_port}", "ip": f"{net[1]}/31"}
        links.append((number, net, a, a_port, b, b_port, [a_if, b_if]))

    for node in nodes:
        dev, base = node["key"], node["base"]

        def mac(last, chassis=True):
            return ":".join(f"{b:02X}" for b in base[:3] + bytes([0xFF if chassis else 0, 0, last]))

        def iface(name, attrs, refs=None, mac_address=None):
            key = f"{dev}/if/{name}"
            add("interface", key, {"name": name, **attrs},
                {"device": dev, **(refs or {}), **({"primary_mac_address": f"mac/{key}"} if mac_address else {})})
            if mac_address:
                add("mac_address", f"mac/{key}", {"mac_address": mac_address,
                                                  "description": "SR Linux chassis-derived interface MAC"},
                    {"assigned_object": key})
            return key

        def address(key, cidr):
            add("ip_address", f"ip/{key}", {"address": cidr, "status": "active",
                                            "description": f"{node['name']} {objects[key]['attrs']['name']}"},
                {"assigned_object": key, "tenant": "tenant"})

        prod_name = objects[node["prod"]]["attrs"]["name"]
        add("device", dev, {"name": node["name"], "status": "active", "serial": spec["serial_format"],
                            "face": "front", "position": 1 + node["index"],
                            "description": f"Lab replica of {prod_name} (SR Linux container)",
                            "comments": comments(prod_name)},
            {"device_type": f"hardware/{ALIAS}", "role": ROLE, "platform": platform, "site": site,
             "location": room, "rack": rack, "tenant": "tenant", "tags": ["tag/estate"],
             "primary_ip4": f"ip/{dev}/if/mgmt0.0"},
            {"hardware": ALIAS, "purpose": "lab-router", "mirrors": node["prod"], "lab_index": node["index"]})
        speeds = {}
        for port in spec["interfaces"]:
            if port.get("mgmt_only"):
                mgmt = iface(port["name"], {"type": port["type"], "mgmt_only": True, "enabled": True,
                                            "speed": 1_000_000}, mac_address=mac(0, chassis=False))
                address(iface(f"{port['name']}.0", {"type": "virtual", "enabled": True}, {"parent": mgmt}),
                        f"{MGMT[11 + node['index']]}/{MGMT.prefixlen}")
            else:
                speeds[port["name"]] = port["type"]
        system = iface("system0", {"type": "virtual", "enabled": True, "description": "Router ID loopback"})
        address(iface("system0.0", {"type": "virtual", "enabled": True}, {"parent": system}),
                f"{LOOPBACKS[1 + node['index']]}/32")
        for name, kind in speeds.items():
            wired = node["ports"].get(name)
            attrs = {"type": kind, "enabled": bool(wired)}
            if wired:
                attrs.update(speed={"25gbase-x-sfp28": 25_000_000, "100gbase-x-qsfp28": 100_000_000,
                                    "10gbase-x-sfpp": 10_000_000}[kind], description=f"To {wired['peer']}")
            key = iface(name, attrs, mac_address=mac(int(name.rsplit("/", 1)[1])))
            if wired:
                address(iface(f"{name}.0", {"type": "virtual", "enabled": True}, {"parent": key}), wired["ip"])

    for number, net, a, a_port, b, b_port, mirrors in links:
        add("prefix", f"prefix/lab/link/{net}", {"prefix": str(net), "status": "active",
                                                  "description": "Network lab point-to-point link"},
            {"tenant": "tenant", "role": "ip-role/backbone"}, {"mirrors": mirrors})
        ka, kb = f"{a['key']}/if/{a_port}", f"{b['key']}/if/{b_port}"
        add("cable", f"cable/{ka}--{kb}", {"status": "connected", "label": f"LAB-{number + 1:02d}",
                                           "description": "containerlab veth pair; no physical medium"},
            {"a": ka, "b": kb}, {"mirrors": mirrors})
