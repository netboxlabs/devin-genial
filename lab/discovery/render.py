"""Render a real-discovery lab from a provider plan.json that carries one.

    python3 lab/discovery/render.py PLAN OUT            # write the lab
    python3 lab/discovery/render.py --check OUT DRYRUN  # diff an orb-agent dry run

The lab records come FROM THE PLAN: a provider recipe with ``discovery_lab =
true`` makes the generator (``estates/discovery_lab.py``) emit a Network Lab
room at the NOC holding Nokia 7220 IXR-D2L lab routers that mirror the wiring
of the first PoP in the permanent ``provider-pop-order`` ledger, independently
checked by ``validate_provider.discovery_lab``. This script only turns those
records into what runs them - the containerlab topology, SR Linux startup
configs and the orb-agent policy - plus ``lab-slice.json``, the plan's own lab
records, which ``--check`` compares with the agent's dry-run output. The only
intended differences are the documented ``drift.json`` items.

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
MANUFACTURER = "Nokia"
LAB_ROLE = "role/lab-router"
USERNAME, PASSWORD = "admin", "NokiaSrl1!"  # containerlab's documented SR Linux default
VAULT_REFERENCE = f"secret//{LAB}/srl/password"  # KV-v2 mount // path / key, seeded by vm.sh
INTERFACE_PATTERNS = [  # discovery defaults that type ports as the 7220 IXR-D2L front panel
    {"match": r"^ethernet-1/(49|5[0-6])$", "type": "100gbase-x-qsfp28"},
    {"match": r"^ethernet-1/5[78]$", "type": "10gbase-x-sfpp"},
    {"match": r"^ethernet-1/\d+$", "type": "25gbase-x-sfp28"},
    {"match": r"^mgmt0$", "type": "1000base-t"},
    {"match": r"^system0$", "type": "virtual"},  # the driver reports this loopback as "other"
]
# Drift addresses stay inside the generator's lab blocks (estates/discovery_lab.py).
LINKS = ipaddress.ip_network("198.19.0.0/24")
LOOPBACKS = ipaddress.ip_network("198.19.255.0/24")
MGMT = ipaddress.ip_network("198.18.0.0/24")


def natural(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def require(condition, message):
    if not condition:
        sys.exit(f"discovery lab: {message}")


# --------------------------------------------------------------------------- read the plan

def lab_objects(plan):
    """The plan's own lab records, exactly as the generator emitted them."""
    return [o for o in plan["objects"] if o["meta"].get("discovery_lab")]


def from_plan(plan):
    """Nodes and links as the renderers below need them, read from the plan's lab records."""
    objects = {o["key"]: o for o in plan["objects"]}
    devices = sorted((o for o in objects.values() if o["kind"] == "device" and o["refs"].get("role") == LAB_ROLE),
                     key=lambda o: o["attrs"]["position"])
    require(devices, "plan has no network lab; set discovery_lab = true in the provider recipe and regenerate")
    ports = {(o["refs"]["device"], o["attrs"]["name"]): o for o in objects.values() if o["kind"] == "interface"}
    address = {o["refs"]["assigned_object"]: o["attrs"]["address"]
               for o in objects.values() if o["kind"] == "ip_address"}
    name = lambda key: objects[key]["attrs"]["name"]
    nodes, by_key = [], {}
    for index, device in enumerate(devices):
        key = device["key"]
        mac = objects[ports[(key, "mgmt0")]["refs"]["primary_mac_address"]]["attrs"]["mac_address"]
        node = {"index": index, "key": key, "name": device["attrs"]["name"],
                "prod": device["meta"]["mirrors"], "prod_name": name(device["meta"]["mirrors"]),
                "mgmt": objects[device["refs"]["primary_ip4"]]["attrs"]["address"],
                "loopback": address[ports[(key, "system0.0")]["key"]],
                "base_mac": mac.lower(), "ports": {}}
        nodes.append(node)
        by_key[key] = node
    cables = sorted((o for o in objects.values() if o["kind"] == "cable"
                     and objects[o["refs"]["a"]]["refs"].get("device") in by_key), key=lambda o: o["attrs"]["label"])
    links = []
    for cable in cables:
        a, b = (objects[cable["refs"][side]] for side in ("a", "b"))
        ends = [(by_key[i["refs"]["device"]], i["attrs"]["name"]) for i in (a, b)]
        for (node, port), (peer, peer_port), iface in ((ends[0], ends[1], a), (ends[1], ends[0], b)):
            node["ports"][port] = {"peer": f"{peer['name']} / {peer_port}",
                                   "ip": address[ports[(node["key"], f"{port}.0")]["key"]],
                                   "description": iface["attrs"]["description"]}
        links.append({"a": (ends[0][0]["name"], ends[0][1]), "b": (ends[1][0]["name"], ends[1][1]),
                      "prefix": str(ipaddress.ip_interface(ends[0][0]["ports"][ends[0][1]]["ip"]).network),
                      "mirrors": cable["meta"]["mirrors"]})
    first = devices[0]["refs"]
    return {"namespace": plan["recipe"]["namespace"], "nodes": nodes, "links": links,
            "site_name": name(first["site"]), "room": name(first["location"]), "role": name(LAB_ROLE)}


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
    return {"config": {"defaults": {"site": lab["site_name"], "location": lab["room"], "role": lab["role"],
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


def render(plan_path, out, clean=False):
    """``clean`` renders the documented state with no drift (the Day-1 seeding route)."""
    plan = json.loads(Path(plan_path).read_text())
    lab = from_plan(plan)
    drifts = [] if clean else drift(lab)
    out = Path(out)
    (out / "configs").mkdir(parents=True, exist_ok=True)
    for node in lab["nodes"]:
        (out / "configs" / f"{node['name']}.cli").write_text(srl_config(node, drifts))
    (out / f"{LAB}.clab.yml").write_text(topology(lab))
    dump = lambda name, value: (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    dump("lab-slice.json", lab_objects(plan))
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
    parser.add_argument("source", help="plan.json (render) or rendered OUT directory (--check)")
    parser.add_argument("target", help="output directory (render) or dry-run output directory (--check)")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.source, args.target)
    render(args.source, args.target, args.clean)
    return 0


if __name__ == "__main__":
    sys.exit(main())
