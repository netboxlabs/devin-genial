"""Render a real-discovery lab from a provider plan.json.

    python3 lab/discovery/render.py PLAN OUT            # write the lab
    python3 lab/discovery/render.py --check OUT DRYRUN  # diff an orb-agent dry run

The lab is a small staging replica of the first PoP in the permanent
``provider-pop-order`` ledger: its PE pair plus the first-ordered backbone
neighbour of PE A (and, with ``--nodes 4``, of PE B), as Nokia SR Linux
containers wired exactly as those routers are wired to each other in the plan.
Real discovery of an SR Linux container must report a Nokia 7220 IXR-D2L, so
the lab is NOT matched against
the Juniper MX204 records it mirrors. It is its own clearly labelled slice -
``lab-slice.json``, written in the plan's own object grammar - in a Network Lab
room at the NOC site, whose every record says what discovery will truly find.
The only intended differences are the documented ``drift.json`` items, which
``--check`` proves against the agent's own dry-run output.

Stdlib only. Nothing here contacts a NetBox target.
"""

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from pathlib import Path

LAB = "genial-discovery"
SRL_IMAGE = "ghcr.io/nokia/srlinux:26.7.2"  # sha256:0096fe3e...b48be8, arm64+amd64
SRL_VERSION = "v26.7.2"
# Reported by the containerised SR Linux itself (``show version``), observed in
# an orb-agent 2.15.0 dry run: these are what discovery will compare.
MODEL, MANUFACTURER, SERIAL = "7220 IXR-D2L", "Nokia", "Sim Serial No."
PLATFORM = f"NOKIA_SRL {SRL_VERSION}"
USERNAME, PASSWORD = "admin", "NokiaSrl1!"  # containerlab's documented SR Linux default
VAULT_REFERENCE = f"secret//{LAB}/srl/password"  # KV-v2 mount // path / key, seeded by vm.sh
# 7220 IXR-D2L front panel, per netbox-community/devicetype-library
# device-types/Nokia/7220-IXR-D2L-25-100GE.yaml: 48x SFP28, 8x QSFP28, 2x SFP+.
PORTS = ([(n, "25gbase-x-sfp28", 25_000_000) for n in range(1, 49)]
         + [(n, "100gbase-x-qsfp28", 100_000_000) for n in range(49, 57)]
         + [(n, "10gbase-x-sfpp", 10_000_000) for n in range(57, 59)])
INTERFACE_PATTERNS = [  # discovery defaults that type ports as the hardware above
    {"match": r"^ethernet-1/(49|5[0-6])$", "type": "100gbase-x-qsfp28"},
    {"match": r"^ethernet-1/5[78]$", "type": "10gbase-x-sfpp"},
    {"match": r"^ethernet-1/\d+$", "type": "25gbase-x-sfp28"},
    {"match": r"^mgmt0$", "type": "1000base-t"},
    {"match": r"^system0$", "type": "virtual"},  # the driver reports this loopback as "other"
]
# RFC 2544 benchmarking space: reserved for network-device test labs and
# disjoint from every estate pool (10.0.0.0/8, 2001:db8::/32).
LAB_POOL = ipaddress.ip_network("198.18.0.0/15")
MGMT = ipaddress.ip_network("198.18.0.0/24")
LINKS = ipaddress.ip_network("198.19.0.0/24")
LOOPBACKS = ipaddress.ip_network("198.19.255.0/24")
ROLE_COLOR = "ff6f00"
HONESTY = ("Software lab: a Nokia SR Linux container emulating this chassis under containerlab. "
           "No physical hardware, optics or production traffic; it mirrors the wiring of the "
           "production routers named in its description, not their hardware or addressing.")


def natural(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def require(condition, message):
    if not condition:
        sys.exit(f"discovery lab: {message}")


# --------------------------------------------------------------------------- selection

def select(plan, size=3):
    """``size`` (3 or 4) production PEs and the /31 links among them, read from the plan."""
    objects = {o["key"]: o for o in plan["objects"]}
    order = plan.get("reservations", {}).get("provider-pop-order")
    require(order, "plan has no provider-pop-order ledger; only the provider profile is supported")
    pes = {k for k, o in objects.items()
           if o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge"}
    pop_of = {k: objects[k]["refs"]["site"].removeprefix("site/pop-") for k in pes}
    # Routed adjacency from the addresses themselves: a /31 with one PE
    # interface on each end, independent of how cables or circuits model it.
    ends = {}
    for o in plan["objects"]:
        if o["kind"] != "ip_address":
            continue
        address = ipaddress.ip_interface(o["attrs"]["address"])
        iface = objects.get(o["refs"].get("assigned_object"), {})
        if address.version == 4 and address.network.prefixlen == 31 and iface.get("kind") == "interface":
            ends.setdefault(address.network, []).append((iface["refs"]["device"], iface["key"]))
    links = sorted((sorted(pair) for pair in ends.values()
                    if len(pair) == 2 and all(d in pes for d, _ in pair)), key=str)

    rank = lambda dev: (order.get(pop_of[dev], 1 << 30), natural(dev))
    first = min(order, key=order.get)
    nodes = sorted((d for d in pes if pop_of[d] == first), key=rank)
    require(len(nodes) == 2, f"PoP {first} does not have a PE pair")
    for anchor in list(nodes)[:size - 2]:
        neighbours = sorted({d for pair in links for d, _ in pair
                             if anchor in (pair[0][0], pair[1][0]) and d != anchor and d not in nodes},
                            key=rank)
        require(neighbours, f"{anchor} has no PE neighbour outside its PoP")
        nodes.append(neighbours[0])
    chosen = set(nodes)
    lab_links = [pair for pair in links if pair[0][0] in chosen and pair[1][0] in chosen]
    return objects, nodes, lab_links


def noc_site(plan, objects):
    """The first site in the data-centre site group: the provider's NOC campus."""
    sites = sorted(k for k, o in objects.items()
                   if o["kind"] == "site" and str(o["refs"].get("group", "")).endswith("/dc"))
    require(sites, "plan has no data-centre (NOC) site")
    site = sites[0]
    rooms = sorted(k for k, o in objects.items()
                   if o["kind"] == "location" and o["refs"].get("site") == site
                   and o["meta"].get("space_type") == "floor")
    require(rooms, f"{site} has no floor location to hold the lab room")
    return site, rooms[0]


# --------------------------------------------------------------------------- model

def build(plan, size=3):
    objects, prod_nodes, prod_links = select(plan, size)
    ns = plan["recipe"]["namespace"]
    site, floor = noc_site(plan, objects)
    site_stem = site.removeprefix("site/")
    salt = int(hashlib.sha256(f"{LAB}/{ns}".encode()).hexdigest(), 16) % 256

    nodes = []
    for index, prod in enumerate(prod_nodes):
        name = "lab-" + objects[prod]["attrs"]["name"]
        require(len(name) <= 63, f"lab hostname {name} exceeds 63 characters")
        nodes.append({"index": index, "prod": prod, "prod_name": objects[prod]["attrs"]["name"],
                      "name": name, "key": f"device/{site_stem}/network-lab/{name}",
                      "mgmt": f"{MGMT[11 + index]}/{MGMT.prefixlen}",
                      "loopback": f"{LOOPBACKS[1 + index]}/32",
                      "base_mac": f"1a:{salt:02x}:{index:02x}:00:00:00", "ports": {}})
    by_prod = {n["prod"]: n for n in nodes}

    # Map each mirrored production port onto the next free front-panel port of
    # the same speed class, in natural production-interface order per node.
    free = {n["name"]: {"fast": list(range(49, 57)), "slow": list(range(1, 49))} for n in nodes}
    ports = {}
    for node in nodes:
        mine = sorted((iface for pair in prod_links for dev, iface in pair if dev == node["prod"]),
                      key=natural)
        for iface in mine:
            speed = objects[iface]["attrs"].get("speed") or 0
            pool = free[node["name"]]["fast" if speed >= 100_000_000 else "slow"]
            require(pool, f"{node['name']} has no free port for {iface}")
            ports[iface] = (node, f"ethernet-1/{pool.pop(0)}")

    links = []
    for number, ((a_dev, a_if), (b_dev, b_if)) in enumerate(prod_links):
        net = list(LINKS.subnets(new_prefix=31))[number]
        (a_node, a_port), (b_node, b_port) = ports[a_if], ports[b_if]
        links.append({"a": (a_node["name"], a_port), "b": (b_node["name"], b_port), "prefix": str(net),
                      "mirrors": [objects[a_if]["key"], objects[b_if]["key"]]})
        a_node["ports"][a_port] = {"peer": f"{b_node['name']} / {b_port}", "ip": f"{net[0]}/31"}
        b_node["ports"][b_port] = {"peer": f"{a_node['name']} / {a_port}", "ip": f"{net[1]}/31"}
    for node in nodes:
        for port in node["ports"].values():
            port["description"] = f"To {port['peer']}"
    return {"namespace": ns, "site": site, "floor": floor, "nodes": nodes, "links": links,
            "site_name": objects[site]["attrs"]["name"]}


def drift(lab):
    """Three on-box changes NetBox was never told about, picked by position only.

    Every item is a field the bundled ``nokia_srl`` driver actually reports
    (orb-agent 2.15.0). Interface descriptions are deliberately absent: the
    driver parses ``show interface all``, which on SR Linux 26.7 prints no
    description, so a description edit would never reach Assurance."""
    require(len(lab["links"]) >= 2, "lab needs at least two links to place drift")
    shut_node, shut_port = lab["links"][-1]["a"]
    renumbered = lab["nodes"][1]
    host = lab["nodes"][2]  # the first neighbour PoP's router
    spare = next(f"ethernet-1/{n}" for n in range(1, 49) if f"ethernet-1/{n}" not in host["ports"])
    extra = list(LINKS.subnets(new_prefix=31))[-1]
    return [
        {"id": "port-shut-on-box", "device": shut_node, "interface": shut_port,
         "field": "enabled", "documented": True, "observed": False,
         "story": "A backbone port was administratively shut during maintenance and never re-enabled; "
                  "NetBox still documents an enabled link."},
        {"id": "loopback-renumbered-on-box", "device": renumbered["name"], "interface": "system0.0",
         "field": "address", "documented": renumbered["loopback"],
         "observed": f"{LOOPBACKS[101 + renumbered['index']]}/32",
         "story": "The router-ID loopback was renumbered on the box. Discovery reports the new address; "
                  "the documented one is simply not observed (Diode carries no deletions)."},
        {"id": "undocumented-test-port", "device": host["name"], "interface": spare,
         "field": "enabled", "documented": False, "observed": True,
         "ip": f"{extra[0]}/31", "description": "Traffic generator - not yet patched",
         "story": "A spare 25G port was enabled and addressed for a traffic generator and never recorded."},
    ]


def slice_objects(lab):
    """The lab in the plan's own object grammar (kind/key/attrs/refs/meta)."""
    ns, site, stem = lab["namespace"], lab["site"], lab["site"].removeprefix("site/")
    room, rack = f"location/{stem}/network-lab", f"rack/{stem}/network-lab/lab-01"
    out = [
        {"kind": "manufacturer", "key": "manufacturer/Nokia", "attrs": {"name": MANUFACTURER, "slug": "nokia"},
         "refs": {}, "meta": {}},
        {"kind": "device_type", "key": "hardware/lab-router", "refs": {"manufacturer": "manufacturer/Nokia"},
         "attrs": {"model": MODEL, "slug": "nokia-7220-ixr-d2l", "part_number": "3HE17645AA", "u_height": 1,
                   "is_full_depth": True,
                   "comments": "Model string as reported by SR Linux `show version`. Ports follow "
                               "netbox-community/devicetype-library Nokia/7220-IXR-D2L-25-100GE.yaml."},
         "meta": {"source": "SR Linux 26.7.2 container; devicetype-library"}},
        {"kind": "platform", "key": "platform/lab-srl", "refs": {"manufacturer": "manufacturer/Nokia"},
         "attrs": {"name": PLATFORM, "slug": "nokia-srl-" + SRL_VERSION.lstrip("v").replace(".", "-"),
                   "description": "Nokia SR Linux; name exactly as Orb device discovery reports it"},
         "meta": {}},
        {"kind": "device_role", "key": "role/lab-router", "refs": {}, "meta": {},
         "attrs": {"name": "Lab Router", "slug": f"{ns}-lab-router", "color": ROLE_COLOR,
                   "description": "Isolated staging router; never carries customer traffic"}},
        {"kind": "location", "key": room, "meta": {"space_type": "lab"},
         "refs": {"site": site, "parent": lab["floor"], "tenant": "tenant"},
         "attrs": {"name": "Network Lab", "slug": f"{ns}-{stem}-network-lab", "status": "active",
                   "description": "Isolated software-staging lab; containerised network OS, no production links"}},
        {"kind": "rack", "key": rack, "meta": {},
         "refs": {"site": site, "location": room, "role": "rack-role/network", "tenant": "tenant"},
         "attrs": {"name": "L01", "facility_id": "L01", "status": "active", "u_height": 24, "width": 19,
                   "form_factor": "4-post-cabinet", "asset_tag": f"{ns}-{stem}-lab-01",
                   "description": "Lab server cabinet hosting the containerlab VM"}},
        {"kind": "prefix", "key": "prefix/lab/pool", "refs": {"tenant": "tenant"}, "meta": {},
         "attrs": {"prefix": str(LAB_POOL), "status": "container",
                   "description": "RFC 2544 benchmarking space reserved for the isolated network lab"}},
        {"kind": "prefix", "key": "prefix/lab/management", "refs": {"tenant": "tenant"}, "meta": {},
         "attrs": {"prefix": str(MGMT), "status": "active", "description": "Network lab out-of-band management"}},
    ]
    for link in lab["links"]:
        out.append({"kind": "prefix", "key": f"prefix/lab/link/{link['prefix']}", "refs": {"tenant": "tenant"},
                    "meta": {"mirrors": link["mirrors"]},
                    "attrs": {"prefix": link["prefix"], "status": "active",
                              "description": "Network lab point-to-point link"}})
    for node in lab["nodes"]:
        dev, base = node["key"], bytes.fromhex(node["base_mac"].replace(":", ""))

        def mac(last, chassis=True):
            raw = base[:3] + bytes([0xFF if chassis else 0, 0, last])
            return ":".join(f"{b:02X}" for b in raw)

        def iface(name, attrs, refs=None, mac_address=None):
            key = f"{dev}/if/{name}"
            out.append({"kind": "interface", "key": key, "meta": {},
                        "refs": {"device": dev, **(refs or {}),
                                 **({"primary_mac_address": f"mac/{key}"} if mac_address else {})},
                        "attrs": {"name": name, **attrs}})
            if mac_address:
                out.append({"kind": "mac_address", "key": f"mac/{key}", "meta": {},
                            "refs": {"assigned_object": key},
                            "attrs": {"mac_address": mac_address,
                                      "description": "SR Linux chassis-derived interface MAC"}})
            return key

        def address(key, cidr, description):
            out.append({"kind": "ip_address", "key": f"ip/{key}", "meta": {},
                        "refs": {"assigned_object": key, "tenant": "tenant"},
                        "attrs": {"address": cidr, "status": "active", "description": description}})

        out.append({"kind": "device", "key": dev, "meta": {"mirrors": node["prod"], "lab_index": node["index"]},
                    "refs": {"device_type": "hardware/lab-router", "role": "role/lab-router",
                             "platform": "platform/lab-srl", "site": site, "location": room, "rack": rack,
                             "tenant": "tenant", "tags": ["tag/estate"],
                             "primary_ip4": f"ip/{dev}/if/mgmt0.0"},
                    "attrs": {"name": node["name"], "status": "active", "serial": SERIAL, "face": "front",
                              "position": 1 + node["index"],
                              "description": f"Lab replica of {node['prod_name']} (SR Linux container)",
                              "comments": HONESTY}})
        mgmt = iface("mgmt0", {"type": "1000base-t", "mgmt_only": True, "enabled": True, "speed": 1_000_000},
                     mac_address=mac(0, chassis=False))
        sub = iface("mgmt0.0", {"type": "virtual", "enabled": True}, {"parent": mgmt})
        address(sub, node["mgmt"], f"{node['name']} mgmt0.0")
        system = iface("system0", {"type": "virtual", "enabled": True, "description": "Router ID loopback"})
        address(iface("system0.0", {"type": "virtual", "enabled": True}, {"parent": system}),
                node["loopback"], f"{node['name']} system0.0")
        for number, kind, speed in PORTS:
            name = f"ethernet-1/{number}"
            wired = node["ports"].get(name)
            attrs = {"type": kind, "enabled": bool(wired)}
            if wired:
                attrs.update(speed=speed, description=wired["description"])
            key = iface(name, attrs, mac_address=mac(number))
            if wired:
                address(iface(f"{name}.0", {"type": "virtual", "enabled": True}, {"parent": key}),
                        wired["ip"], f"{node['name']} {name}.0")
    for link in lab["links"]:
        (a, ap), (b, bp) = link["a"], link["b"]
        ka = f"device/{stem}/network-lab/{a}/if/{ap}"
        kb = f"device/{stem}/network-lab/{b}/if/{bp}"
        out.append({"kind": "cable", "key": f"cable/{ka}--{kb}", "refs": {"a": ka, "b": kb},
                    "meta": {"mirrors": link["mirrors"]},
                    "attrs": {"status": "connected", "label": f"LAB-{lab['links'].index(link) + 1:02d}",
                              "description": "containerlab veth pair; no physical medium"}})
    return out


# --------------------------------------------------------------------------- render

def srl_config(node, drifts):
    """SR Linux CLI startup config: observed state, i.e. the plan plus drift."""
    ports = {name: dict(p) for name, p in node["ports"].items()}
    loopback = node["loopback"]
    for item in drifts:
        if item["device"] != node["name"]:
            continue
        if item["id"] == "port-shut-on-box":
            ports[item["interface"]]["shut"] = True
        elif item["id"] == "loopback-renumbered-on-box":
            loopback = item["observed"]
        else:
            ports[item["interface"]] = {"description": item["description"], "ip": item["ip"]}
    lines = ["set / interface mgmt0 subinterface 0 ipv6 admin-state disable",
             "set / interface system0 admin-state enable",
             "set / interface system0 description \"Router ID loopback\"",
             "set / interface system0 subinterface 0 ipv4 admin-state enable",
             f"set / interface system0 subinterface 0 ipv4 address {loopback}",
             "set / network-instance default type default",
             "set / network-instance default interface system0.0"]
    for name in sorted(ports, key=natural):
        port = ports[name]
        lines += [f"set / interface {name} admin-state {'disable' if port.get('shut') else 'enable'}",
                  f"set / interface {name} description \"{port['description']}\"",
                  f"set / interface {name} subinterface 0 ipv4 admin-state enable",
                  f"set / interface {name} subinterface 0 ipv4 address {port['ip']}",
                  f"set / network-instance default interface {name}.0"]
    return "\n".join(lines) + "\n"


def topology(lab):
    nodes = "\n".join(
        f"    {n['name']}:\n      kind: nokia_srlinux\n      image: {SRL_IMAGE}\n"
        f"      mgmt-ipv4: {n['mgmt'].split('/')[0]}\n      startup-config: configs/{n['name']}.cli\n"
        f"      labels: {{genial.mirrors: {n['prod_name']}, genial.base-mac: \"{n['base_mac']}\"}}"
        for n in lab["nodes"])
    links = "\n".join(
        f"    - endpoints: [\"{l['a'][0]}:{l['a'][1].replace('ethernet-', 'e').replace('/', '-')}\", "
        f"\"{l['b'][0]}:{l['b'][1].replace('ethernet-', 'e').replace('/', '-')}\"]"
        for l in lab["links"])
    return (f"# Rendered by lab/discovery/render.py from {lab['namespace']} plan.json - do not edit.\n"
            f"name: {LAB}\nmgmt:\n  network: {LAB}\n  ipv4-subnet: {MGMT}\n"
            f"topology:\n  nodes:\n{nodes}\n  links:\n{links}\n")


def agent_policy(lab):
    """The device_discovery policy the platform job must reproduce (and the dry run uses).

    The password is the same Vault reference a fleet credential carries; vm.sh
    seeds that path in the lab's dev Vault."""
    return {"config": {"defaults": {"site": lab["site_name"], "location": "Network Lab", "role": "Lab Router",
                                    "interface_patterns": INTERFACE_PATTERNS}},
            "scope": [{"hostname": n["mgmt"].split("/")[0], "username": USERNAME,
                       "password": "${vault://" + VAULT_REFERENCE + "}", "driver": "nokia_srl"}
                      for n in lab["nodes"]]}


def dry_run_agent(lab):
    return {"orb": {"config_manager": {"active": "local"},
                    "backends": {"common": {"diode": {"target": "grpc://127.0.0.1:8080/diode",
                                                      "agent_name": LAB, "dry_run": True,
                                                      "dry_run_output_dir": "/opt/orb/out"}},
                                 "device_discovery": None},
                    "policies": {"device_discovery": {f"{LAB}-dry-run": agent_policy(lab)}}}}


def render(plan_path, out, size=3, clean=False):
    """``clean`` renders the documented state with no drift (the Day-1 seeding route)."""
    plan = json.loads(Path(plan_path).read_text())
    lab = build(plan, size)
    drifts = [] if clean else drift(lab)
    out = Path(out)
    (out / "configs").mkdir(parents=True, exist_ok=True)
    for node in lab["nodes"]:
        (out / "configs" / f"{node['name']}.cli").write_text(srl_config(node, drifts))
    (out / f"{LAB}.clab.yml").write_text(topology(lab))
    dump = lambda name, value: (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    dump("lab-slice.json", slice_objects(lab))
    dump("drift.json", drifts)
    dump("agent.dry-run.json", dry_run_agent(lab))  # YAML is a JSON superset; orb-agent reads it
    dump("manifest.json", {
        "plan": str(Path(plan_path).resolve()), "plan_sha256": hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(),
        "generator_version": plan.get("generator_version"), "image": SRL_IMAGE, "lab": LAB,
        "credential": {"username": USERNAME, "password": PASSWORD, "vault_reference": VAULT_REFERENCE},
        "job_defaults": agent_policy(lab)["config"]["defaults"],
        "nodes": [{k: n[k] for k in ("name", "prod_name", "mgmt", "loopback", "base_mac")} for n in lab["nodes"]],
        "links": [{"a": " ".join(l["a"]), "b": " ".join(l["b"]), "prefix": l["prefix"]} for l in lab["links"]],
    })
    print(f"rendered {len(lab['nodes'])} nodes, {len(lab['links'])} links, {len(drifts)} drift items -> {out}")


# --------------------------------------------------------------------------- check

def _get(entity, *path):
    for part in path:
        entity = entity.get(part) if isinstance(entity, dict) else None
    return entity


def predicted(out, dryrun):
    """Field differences between the dry-run entities and the lab slice."""
    objects = json.loads((Path(out) / "lab-slice.json").read_text())
    by_key = {o["key"]: o for o in objects}
    devices = {o["attrs"]["name"]: o for o in objects if o["kind"] == "device"}
    ifaces = {(by_key[o["refs"]["device"]]["attrs"]["name"], o["attrs"]["name"]): o
              for o in objects if o["kind"] == "interface"}
    ips = {o["attrs"]["address"]: o for o in objects if o["kind"] == "ip_address"}
    prefixes = {o["attrs"]["prefix"] for o in objects if o["kind"] == "prefix"}
    name_of = lambda key, field="name": by_key[key]["attrs"].get(field) if key else None
    diffs = []
    files = sorted(Path(dryrun).glob("*.json"))
    require(files, f"no dry-run output under {dryrun}")
    for path in files:
        for wrapper in json.loads(path.read_text())["entities"]:
            (kind, e), = [(k, v) for k, v in wrapper.items() if k != "timestamp"]
            if kind == "device":
                doc = devices.get(e["name"])
                if doc is None:
                    diffs.append(("create", "device", e["name"], None, None, None)); continue
                want = {"model": name_of(doc["refs"]["device_type"], "model"),
                        "manufacturer": MANUFACTURER, "platform": name_of(doc["refs"]["platform"]),
                        "role": name_of(doc["refs"]["role"]), "serial": doc["attrs"]["serial"],
                        "status": doc["attrs"]["status"], "location": name_of(doc["refs"]["location"]),
                        "primary_ip4": name_of(doc["refs"]["primary_ip4"], "address")}
                got = {"model": _get(e, "device_type", "model"),
                       "manufacturer": _get(e, "device_type", "manufacturer", "name"),
                       "platform": _get(e, "platform", "name"), "role": _get(e, "role", "name"),
                       "serial": e.get("serial"), "status": e.get("status"),
                       "location": _get(e, "location", "name"),
                       "primary_ip4": _get(e, "primary_ip4", "address")}
                ident = e["name"]
            elif kind == "interface":
                ident = (e["device"]["name"], e["name"])
                doc = ifaces.get(ident)
                if doc is None:
                    diffs.append(("create", "interface", " ".join(ident), None, None, None)); continue
                mac = by_key.get(doc["refs"].get("primary_mac_address"), {}).get("attrs", {})
                want = {"type": doc["attrs"]["type"], "enabled": doc["attrs"]["enabled"],
                        "speed": doc["attrs"].get("speed"), "description": doc["attrs"].get("description"),
                        "mac": mac.get("mac_address"), "parent": name_of(doc["refs"].get("parent"))}
                got = {"type": e.get("type"), "enabled": e.get("enabled"),
                       "speed": int(e["speed"]) if e.get("speed") else None,
                       "description": e.get("description"),
                       "mac": _get(e, "primary_mac_address", "mac_address"), "parent": _get(e, "parent", "name")}
                ident = " ".join(ident)
            elif kind == "ip_address":
                doc = ips.get(e["address"])
                where = (_get(e, "assigned_object_interface", "device", "name"),
                         _get(e, "assigned_object_interface", "name"))
                if doc is None:
                    diffs.append(("create", "ip_address", e["address"], None, None, " ".join(where))); continue
                iface = by_key[doc["refs"]["assigned_object"]]
                want = {"interface": (name_of(iface["refs"]["device"]), iface["attrs"]["name"])}
                got, ident = {"interface": where}, e["address"]
            elif kind == "prefix":
                if e["prefix"] not in prefixes:
                    diffs.append(("create", "prefix", e["prefix"], None, None, None))
                continue
            else:
                diffs.append(("unexpected-kind", kind, json.dumps(e)[:80], None, None, None)); continue
            # Discovery omits unset fields; absence is not a change it can express.
            diffs += [("update", kind, ident, f, want[f], got[f])
                      for f in want if got[f] is not None and got[f] != want[f]]
    return sorted(diffs, key=str)


def expected(out):
    """What drift.json says the dry run must differ in - nothing more."""
    items = json.loads((Path(out) / "drift.json").read_text())
    rows = []
    for item in items:
        ident = f"{item['device']} {item['interface']}"
        if item["id"] == "port-shut-on-box":
            rows.append(("update", "interface", ident, "enabled", True, False))
        elif item["id"] == "loopback-renumbered-on-box":
            rows.append(("create", "ip_address", item["observed"], None, None, ident))
        elif item["id"] == "undocumented-test-port":
            # The port has no peer, so it is oper-down and reports no speed.
            rows += [("update", "interface", ident, "enabled", False, True),
                     ("create", "interface", f"{ident}.0", None, None, None),
                     ("create", "ip_address", item["ip"], None, None, f"{ident}.0"),
                     ("create", "prefix", str(ipaddress.ip_interface(item["ip"]).network), None, None, None)]
    return sorted(rows, key=str)


def check(out, dryrun):
    got, want = predicted(out, dryrun), expected(out)
    for row in got:
        print(("  ok   " if row in want else "  EXTRA") + "  " + " | ".join(map(str, row)))
    missing = [row for row in want if row not in got]
    for row in missing:
        print("  MISSING  " + " | ".join(map(str, row)))
    extra = [row for row in got if row not in want]
    print(f"{len(got)} predicted deviations; {len(extra)} unexpected, {len(missing)} missing")
    return 1 if extra or missing else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare OUT's slice with an orb-agent dry run")
    parser.add_argument("--clean", action="store_true",
                        help="render without drift: the lab then matches lab-slice.json exactly")
    parser.add_argument("--nodes", type=int, choices=(3, 4), default=3,
                        help="lab size; each SR Linux container needs ~1.8 GB of VM memory")
    parser.add_argument("source", help="plan.json (render) or rendered OUT directory (--check)")
    parser.add_argument("target", help="output directory (render) or dry-run output directory (--check)")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.source, args.target)
    render(args.source, args.target, args.nodes, args.clean)
    return 0


if __name__ == "__main__":
    sys.exit(main())
