"""Compliance-policy sidecar for the NetBox Labs Validation plugin.

Like the geometry and lifecycle sidecars, this is a derived artifact over a
frozen ``plan.json``, never a canonical graph change: ``build`` writes curated
validation policies bound to the plan's canonical SHA-256, ``check``
recomputes and byte-compares them, and ``seed`` creates the policies and rules
over REST (``VALIDATION_WRITES=1``), runs them, and compares the engine's
actual failing checks with the artifact's prediction.

Policies are curated so their premises match the estate: every parameter is
read from the finished graph (VLAN ranges per site group, interface-name
patterns per platform, required config-context keys from the estate's own
contexts, point-to-point mask sizes the plan uses, required roles per site
kind) and checks whose premise the estate does not share are excluded with a
recorded reason instead of left to fail. Each rule carries a prediction of the
subjects it should fail on and the graph cause of each. Results, findings and
compliance scores are engine output only: nothing here writes them.
"""

from collections import Counter, defaultdict
from pathlib import Path
import argparse
import hashlib
import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.request

from .model import canonical, digest
from .turbobulk import Client, LoadError, _write_receipt

RECEIPT_VERSION = 1
WRITER_VERSION = "validation-1"
PLUGIN = "netbox_validation"
API = "/api/plugins/validation/"
NAME_LIMIT = 100
TERMINAL = {"passed", "failed", "error"}
RUN_TIMEOUT = 1800
# Interface types the engine accepts uncabled by default (live: "virtual/LAG").
UNCABLED_TYPES = {"virtual", "lag", "bridge"}
# Optional device fields asset_documentation_complete may require; a field is
# required only when every device in the plan carries it.
DOCUMENT_FIELDS = (("serial", "attrs"), ("asset_tag", "attrs"), ("platform", "refs"), ("tenant", "refs"))

# Checks every estate is expected to pass; not individually modeled here —
# the estate's own validators enforce the underlying graph properties, so a
# failure is recorded as engine drift, never hidden.
ASSUMED = (
    ("no_duplicate_ips", "addressing", "critical", {}),
    ("no_orphan_ips", "addressing", "medium", {}),
    ("ip_vrf_consistency", "addressing", "high", {}),
    ("ip_prefix_utilization", "addressing", "medium", {"max_utilization_pct": 90}),
    ("loopback_has_host_route", "addressing", "medium", {}),
    ("prefix_role_assigned", "standards", "low", {}),
    ("consistent_platform_per_role", "standards", "medium", {}),
    ("vlan_assignments_complete", "completeness", "low", {}),
    ("circuit_terminations_complete", "completeness", "medium", {}),
    ("contact_assigned_to_site", "completeness", "low", {}),
    ("lag_min_members", "redundancy", "medium", {"min_members": 2}),
    ("virtual_chassis_member_count", "redundancy", "medium", {"min_members": 2}),
    ("cable_trace_complete", "topology", "high", {}),
    ("symmetric_cabling", "topology", "high", {}),
    ("mtu_consistency_across_link", "topology", "medium", {}),
    ("rear_port_mapping_complete", "topology", "medium", {}),
    ("no_plaintext_secrets_in_context", "security_intent", "critical", {}),
)

# Built-in checks deliberately not installed, each with the premise it does
# not share with a generated estate (observed on a live 1.14.1 tenant).
EXCLUDED = {
    "consistent_device_naming": "flags every A/B redundant pair (pe-a vs pe-b) as a naming "
                                "inconsistency; pattern_by_role does not override the peer heuristic",
    "shared_failure_domain": "counts both A and B panels as shared domains of two dual-corded "
                             "devices in one room, so every correctly dual-fed pair fails",
    "device_single_point_of_failure": "flags each site's management switch as cutting off 66-186 "
                                      "devices, more than its site holds; the count describes the "
                                      "engine's graph, not a single point in this topology",
    "site_redundant_paths": "reports 0 devices with external uplinks at every site, PoPs with eight "
                            "circuits included; inter-site paths here are circuits",
    "dual_homed_circuits": "reports 0 circuits on customer edges whose WAN port is cabled to the "
                           "circuit termination, and evaluates switches and PDUs too; "
                           "site_connectivity_redundancy states single-homing per site",
    "management_vrf_enforced": "matches interface names, so unaddressed spare mgmt ports and "
                               "in-band customer management in the customer VRF read as violations",
    "ntp_syslog_configured": "the estate authors no NTP or syslog context; a failure on every "
                             "device would restate an omission, not a finding",
    "required_context_structure": "config_context_required_keys checks the estate's own keys; "
                                  "a second schema would only restate them",
    "cabled_interfaces_have_ips": "layer-2 access and trunk ports are cabled without addresses by design",
    "power_redundancy": "per-device restatement of power_feed_blast_radius; one finding per feed reads better",
    "power_panel_blast_radius": "at a one-panel premises the panel and its one feed are the same "
                                "fact; power_feed_blast_radius states it",
    "concurrent_maintainability": "restates power_feed_blast_radius once PDUs are out of scope",
    "power_feed_capacity": "reports 'no capacity data' for every feed although feeds carry voltage, "
                           "amperage and max utilisation, so it would pass vacuously",
    "power_three_phase_balance": "every feed is single-phase",
    "min_bgp_sessions": "the engine's BGP session source is unverified on this plugin version",
    "bgp_asn_range_consistent": "the engine's per-device ASN source is unverified on this plugin version",
    "config-engine and routing checks": "require rendered configurations; the estate renders none",
}


class ValidationError(RuntimeError):
    """The artifact cannot be built, verified or seeded faithfully."""


# --- derivation ----------------------------------------------------------------

class _Graph:
    def __init__(self, plan):
        self.objects = {obj["key"]: obj for obj in plan["objects"]}
        self.kinds = defaultdict(list)
        for obj in plan["objects"]:
            self.kinds[obj["kind"]].append(obj)
        self.peer = {}
        for cable in self.kinds["cable"]:
            a, b = cable["refs"].get("a"), cable["refs"].get("b")
            if a and b:
                self.peer[a], self.peer[b] = b, a
        self.children = defaultdict(lambda: defaultdict(list))
        for kind in ("interface", "console_port", "power_port", "power_outlet"):
            for obj in self.kinds[kind]:
                self.children[obj["refs"].get("device")][kind].append(obj)
        self.addresses = defaultdict(list)
        for ip in self.kinds["ip_address"]:
            if ip["refs"].get("assigned_object"):
                self.addresses[ip["refs"]["assigned_object"]].append(ip)
        self.devices = sorted(self.kinds["device"], key=lambda d: d["attrs"]["name"])

    def ref(self, obj, name):
        return self.objects.get(obj["refs"].get(name)) if obj["refs"].get(name) else None

    def group(self, device):
        site = self.ref(device, "site")
        return site["refs"].get("group") if site else None

    def slug(self, key):
        return self.objects[key]["attrs"]["slug"]

    def name(self, key):
        return self.objects[key]["attrs"]["name"]

    def power_paths(self, device):
        """Feeds reached by each cabled power port: port -> feed, directly or through one PDU."""
        feeds = []
        for port in self.children[device["key"]]["power_port"]:
            end = self.objects.get(self.peer.get(port["key"]))
            if end and end["kind"] == "power_outlet":
                end = self.objects.get(self.peer.get(end["refs"].get("power_port")))
            if end and end["kind"] == "power_feed":
                feeds.append(end["key"])
        return feeds

    def site_circuits(self, site_key):
        circuits = {}
        for term in self.kinds["circuit_termination"]:
            target = term["refs"].get("termination")
            # A termination in a room counts for that room's site, as NetBox's
            # own _site cache does (assumed, not verified against the plugin).
            if self.objects.get(target, {}).get("kind") == "location":
                target = self.objects[target]["refs"].get("site")
            if target == site_key:
                circuit = self.objects[term["refs"]["circuit"]]
                peer = self.peer.get(term["key"], "")
                circuits[circuit["key"]] = (circuit, peer.split("/if/")[0] if "/if/" in peer else None)
        return [circuits[key] for key in sorted(circuits)]


def _shape(name):
    """Generalise one interface name into a regex alternative: digit runs become \\d+."""
    return re.sub(r"\d+", r"\\d+", re.escape(name))


def _leaves(data, prefix=""):
    """Dotted leaf paths of a config-context dict (lists and scalars are leaves)."""
    out = []
    for key in sorted(data):
        path = f"{prefix}{key}"
        out += _leaves(data[key], path + ".") if isinstance(data[key], dict) and data[key] else [path]
    return out


def _rule(name, check, category, severity, parameters=None, *, engine="intent", roles=(), platforms=(),
          derivation, model="derived", expected=()):
    if len(name) > NAME_LIMIT:
        raise ValidationError(f"rule name {name!r} exceeds {NAME_LIMIT} characters")
    return {"name": name, "check_name": check, "category": category, "severity": severity,
            "engine": engine, "parameters": parameters or {}, "roles": sorted(roles),
            "platforms": sorted(platforms), "derivation": derivation, "model": model,
            "expected": sorted(expected, key=lambda e: (e["subject"], e["cause"]))}


def _uncabled(g, device):
    names = [i["attrs"]["name"] for i in g.children[device["key"]]["interface"]
             if i["attrs"].get("enabled", True) is not False and i["key"] not in g.peer
             and i["attrs"].get("type") not in UNCABLED_TYPES
             and not str(i["attrs"].get("type", "")).startswith("ieee802.11")]
    return sorted(names)


def _baseline(g):
    rules = [_rule(check.replace("_", " ").capitalize(), check, category, severity, parameters,
                   derivation="estate-wide hygiene the generator's own validators enforce",
                   model="assumed")
             for check, category, severity, parameters in ASSUMED]
    expected = []
    for device in g.devices:
        names = _uncabled(g, device)
        if names:
            expected.append({"subject": device["attrs"]["name"],
                             "cause": f"{len(names)} enabled, uncabled port(s): {', '.join(names[:6])}"
                                      + (" …" if len(names) > 6 else "")})
    rules.append(_rule("Enabled ports are cabled", "no_unconnected_active_interfaces", "topology", "low",
                       derivation="interfaces the plan leaves enabled without a cable",
                       model="predicted", expected=expected))
    fields = [field for field, where in DOCUMENT_FIELDS
              if g.devices and all(d[where].get(field) for d in g.devices)]
    if fields:
        rules.append(_rule("Devices carry their documentation fields", "asset_documentation_complete",
                           "completeness", "low", {"required_fields": fields},
                           derivation="device fields populated on every device in the plan"))
    # Point-to-point sizing: only roles whose every addressed physical
    # interface is a routed link — the engine reads any addressed physical
    # port (management LANs included) as point-to-point.
    masks, routed, mixed = set(), set(), set()
    for device in g.devices:
        for interface in g.children[device["key"]]["interface"]:
            if interface["attrs"].get("type") in UNCABLED_TYPES:
                continue
            for ip in g.addresses[interface["key"]]:
                net = ipaddress.ip_interface(ip["attrs"]["address"])
                if net.version != 4:
                    continue
                if net.network.prefixlen >= 30 and not interface["attrs"].get("mgmt_only"):
                    masks.add(net.network.prefixlen)
                    routed.add(device["refs"]["role"])
                else:
                    mixed.add(device["refs"]["role"])
    if routed - mixed:
        rules.append(_rule("Routed links use point-to-point masks", "point_to_point_subnet_sizing",
                           "addressing", "medium", {"allowed_masks": sorted(masks | {31})},
                           roles=[g.slug(r) for r in routed - mixed],
                           derivation="masks the plan uses on routed links; roles whose addressed "
                                      "physical ports are all routed links"))
    # Required config-context keys: the leaf paths of the estate's unscoped
    # contexts here; role-scoped contexts are checked by each site kind.
    keys = _context_keys(g, None)
    if keys:
        rules.append(_rule("Every device renders the global context keys", "config_context_required_keys",
                           "completeness", "medium", {"required_keys": keys},
                           derivation="every leaf key of the estate's unscoped config contexts"))
    return rules


def _context_keys(g, roles):
    """Leaf keys of active contexts scoped to exactly no roles (roles=None) or to any of ``roles``."""
    keys = set()
    for context in g.kinds["config_context"]:
        scoped = set(context["refs"].get("roles", []))
        if (set(context["refs"]) - {"owner", "roles"} or not context["attrs"].get("is_active", True)
                or (roles is None) != (not scoped) or (roles is not None and not scoped & roles)):
            continue  # ponytail: global or role-scoped contexts only; other scopes need their own rule scoping
        keys.update(_leaves(context["attrs"].get("data") or {}))
    return sorted(keys)


def _naming(g, platform, devices):
    """One pattern per interface type: the name shapes this platform's hardware carries."""
    shapes = defaultdict(set)
    for device in devices:
        for interface in g.children[device["key"]]["interface"]:
            if interface["attrs"].get("type"):
                shapes[interface["attrs"]["type"]].add(_shape(interface["attrs"]["name"]))
    patterns = {kind: "^(?:" + "|".join(sorted(found)) + ")$" for kind, found in sorted(shapes.items())}
    return [_rule(f"Interface names follow {g.name(platform)} conventions"[:NAME_LIMIT],
                  "interface_naming_consistent", "standards", "low", {"pattern_by_type": patterns},
                  derivation="per interface type, the name shapes this platform's catalog hardware carries")]


def _group_standards(g, group, devices, consoled, roles_of):
    sites = sorted({d["refs"]["site"] for d in devices})
    rules = []
    present = [roles_of(site) for site in sites]
    required = sorted(set.intersection(*present)) if present else []
    if required:
        rules.append(_rule("Every site carries its role set", "site_has_required_roles", "completeness", "high",
                           {"required_roles": [g.slug(r) for r in required]},
                           derivation="device roles present at every site of this kind"))
    # The 1.14.1 engine attributes every result of a check to the first rule
    # carrying it in a policy, so each policy holds one rule per check: the
    # paired managed role with the smallest per-site minimum stands for the rest.
    counts = Counter((d["refs"]["site"], d["refs"]["role"]) for d in devices)
    paired = sorted((min(counts[(site, role)] for site in sites), g.slug(role), role)
                    for role in {d["refs"]["role"] for d in devices if d["refs"].get("platform")})
    paired = [entry for entry in paired if entry[0] >= 2]
    if paired:
        least, _, role = paired[0]
        rules.append(_rule(f"At least {least} × {g.name(role)} per site", "site_min_devices_by_role",
                           "redundancy", "high", {"role": g.slug(role), "min_count": least},
                           derivation="fewest devices of this paired managed role at any site of this kind"))
    group_roles = {d["refs"]["role"] for d in devices}
    keys = _context_keys(g, group_roles)
    if keys:
        scoped = sorted({r for c in g.kinds["config_context"] for r in c["refs"].get("roles", [])} & group_roles)
        rules.append(_rule("Role contexts render their keys", "config_context_required_keys", "completeness",
                           "medium", {"required_keys": keys}, roles=[g.slug(r) for r in scoped],
                           derivation="every leaf key of the role-scoped config contexts, on those roles"))
    vids = sorted({v["attrs"]["vid"] for v in g.kinds["vlan"]
                   if (v["refs"].get("site") or (g.ref(v, "group") or {"refs": {}})["refs"].get("scope_site"))
                   in sites})
    if vids:
        rules.append(_rule(f"VLAN IDs within {vids[0]}–{vids[-1]}", "vlan_id_range_by_site", "standards", "low",
                           {"min_vid": vids[0], "max_vid": vids[-1]},
                           derivation="lowest and highest VLAN ID the plan allocates at sites of this kind"))
    if any(g.peer.get(p["key"]) for d in devices for p in g.children[d["key"]]["console_port"]):
        roles = sorted(consoled & {d["refs"]["role"] for d in devices})
    else:
        roles = []
    if roles:
        expected = []
        for d in devices:
            ports = g.children[d["key"]]["console_port"]
            if d["refs"]["role"] in roles and ports and not any(g.peer.get(p["key"]) for p in ports):
                expected.append({"subject": d["attrs"]["name"],
                                 "cause": "no console port cabled: "
                                          + ", ".join(sorted(p["attrs"]["name"] for p in ports))})
        rules.append(_rule("Consoled roles reach a console server", "console_connectivity", "topology",
                           "medium", roles=[g.slug(r) for r in roles], model="predicted", expected=expected,
                           derivation="roles the estate cables to a console server anywhere"))
    dual = sorted(role for role in {d["refs"]["role"] for d in devices}
                  if all(len(g.children[d["key"]]["power_port"]) >= 2
                         for d in devices if d["refs"]["role"] == role))
    if dual:
        expected = [{"subject": d["attrs"]["name"],
                     "cause": f"{sum(1 for p in g.children[d['key']]['power_port'] if g.peer.get(p['key']))}"
                              " of its power supplies cabled"}
                    for d in devices if d["refs"]["role"] in dual
                    and sum(1 for p in g.children[d["key"]]["power_port"] if g.peer.get(p["key"])) < 2]
        rules.append(_rule("Dual-supply devices cable both supplies", "redundant_power", "redundancy", "high",
                           {"min_power_feeds": 2}, roles=[g.slug(r) for r in dual],
                           model="predicted", expected=expected,
                           derivation="roles whose hardware has two or more power supplies"))
    return rules


def _group_resilience(g, group, devices):
    """Graph checks; scoped by policy roles because the graph engine ignores rule roles."""
    rules = []
    feed_devices, paths = defaultdict(list), {}
    for d in devices:
        paths[d["key"]] = feeds = g.power_paths(d)
        if len(set(feeds)) == 1:
            feed_devices[feeds[0]].append(d["attrs"]["name"])
    expected = [{"subject": d["attrs"]["name"], "cause": "no power port reaches a feed"}
                for d in devices if g.children[d["key"]]["power_port"] and not paths[d["key"]]]
    rules.append(_rule("Every powered device reaches a feed", "power_path_complete", "power", "high",
                       {"min_complete_paths": 1}, engine="graph", model="predicted", expected=expected,
                       derivation="cabled power paths port → PDU outlet → PDU inlet → feed"))
    expected = []
    for feed, names in sorted(feed_devices.items()):
        obj = g.objects[feed]
        expected.append({"subject": f"{obj['attrs']['name']}: {', '.join(sorted(names))}",
                         "cause": f"every supply of {len(names)} device(s) draws from feed "
                                  f"{obj['attrs']['name']} on panel {g.name(obj['refs']['power_panel'])}"})
    rules.append(_rule("No feed is a single point of power", "power_feed_blast_radius", "power", "high",
                       {"max_unprotected_devices": 0}, engine="graph", model="predicted", expected=expected,
                       derivation="devices whose every power path shares one feed"))
    sites = sorted({d["refs"]["site"] for d in devices})
    circuits = {site: g.site_circuits(site) for site in sites}
    if any(circuits.values()):
        expected, diverse = [], []
        for site in sites:
            if not circuits[site]:
                continue
            providers = sorted({g.name(c["refs"]["provider"]) for c, _ in circuits[site]})
            cids = sorted(c["attrs"]["cid"] for c, _ in circuits[site])
            if len(cids) < 2 or len(providers) < 2:
                expected.append({"subject": g.name(site),
                                 "cause": f"{len(cids)} circuit(s) ({', '.join(cids[:4])}) from "
                                          f"{len(providers)} provider(s): {', '.join(providers)}"})
            if len(cids) >= 2:
                ends = {device for _, device in circuits[site]}
                reasons = (["all circuits from " + providers[0]] if len(providers) < 2 else []) + \
                          (["all circuits on one device"] if len(ends) < 2 else [])
                if reasons:
                    diverse.append({"subject": g.name(site), "cause": "; ".join(reasons)})
        rules.append(_rule("Two circuits from two providers per site", "site_connectivity_redundancy",
                           "infrastructure", "medium", {"min_circuits": 2, "min_providers": 2},
                           engine="graph", model="predicted", expected=expected,
                           derivation="circuit terminations at each site and their providers"))
        if any(len(c) >= 2 for c in circuits.values()):
            rules.append(_rule("Circuits diverse by provider and device", "circuit_path_diversity",
                               "infrastructure", "medium", {"diversity_requirements": ["provider", "device"]},
                               engine="graph", model="predicted", expected=diverse,
                               derivation="providers and terminating devices of each multi-circuit site"))
    rules.append(_rule("A rack failure stays inside the rack", "rack_failure_impact", "infrastructure",
                       "medium", {"max_external_impact": 5}, engine="graph", model="assumed",
                       derivation="racks hold paired devices on separate lanes"))
    if len({d["refs"].get("rack") for d in devices}) > 1:
        rules.append(_rule("No cross-rack cable is a single point", "cable_single_point_of_failure",
                           "topology", "medium", {"cross_rack_only": True}, engine="graph", model="assumed",
                           derivation="paired uplinks between racks"))
    return rules


def create(plan):
    g = _Graph(plan)
    estate = plan["recipe"].get("name") or plan["recipe"]["namespace"]
    by_site_role = defaultdict(set)
    for d in g.devices:
        by_site_role[d["refs"]["site"]].add(d["refs"]["role"])
    roles_of = by_site_role.__getitem__
    consoled = {d["refs"]["role"] for d in g.devices
                if any(g.peer.get(p["key"]) for p in g.children[d["key"]]["console_port"])}
    policies = [{"name": f"{estate} · Estate baseline", "site_groups": [], "roles": [], "graph": False,
                 "description": "Addressing, cabling, naming and context hygiene across every device",
                 "rules": _baseline(g)}]
    groups = defaultdict(list)
    for d in g.devices:
        if g.group(d):
            groups[g.group(d)].append(d)
    for group in sorted(groups, key=g.name):
        devices = groups[group]
        label = g.name(group)
        policies.append({"name": f"{estate} · {label} standards", "site_groups": [g.slug(group)], "roles": [],
                         "graph": False, "description": f"Role set, VLAN plan, console and supply "
                                                        f"standards for {label.lower()}",
                         "rules": _group_standards(g, group, devices, consoled, roles_of)})
        # PDUs distribute power through a single inlet by design; the A/B pair
        # is the redundancy, so graph checks scope to roles that draw power.
        drawing = sorted({d["refs"]["role"] for d in devices
                          if g.children[d["key"]]["power_port"] and not g.children[d["key"]]["power_outlet"]})
        if drawing:
            scoped = [d for d in devices if d["refs"]["role"] in drawing]
            policies.append({"name": f"{estate} · {label} resilience", "site_groups": [g.slug(group)],
                             "roles": [g.slug(r) for r in drawing], "graph": True,
                             "description": f"Power and circuit resilience for {label.lower()} "
                                            "(power-drawing roles; PDUs are the distribution layer)",
                             "rules": _group_resilience(g, group, scoped)})
    platforms = defaultdict(list)
    for d in g.devices:
        if d["refs"].get("platform"):
            platforms[d["refs"]["platform"]].append(d)
    for platform in sorted(platforms, key=g.name):
        policies.append({"name": f"{estate} · {g.name(platform)} naming", "site_groups": [], "roles": [],
                         "platforms": [g.slug(platform)], "graph": False,
                         "description": f"Interface naming for {g.name(platform)} devices",
                         "rules": _naming(g, platform, platforms[platform])})
    for policy in policies:
        policy.setdefault("platforms", [])
        if len(policy["name"]) > NAME_LIMIT:
            raise ValidationError(f"policy name {policy['name']!r} exceeds {NAME_LIMIT} characters")
    return {"artifact": "validation", "plan_sha256": digest(plan), "writer_version": WRITER_VERSION,
            "namespace": plan["recipe"]["namespace"], "estate": estate, "policies": policies,
            "excluded": [{"check_name": k, "reason": v} for k, v in sorted(EXCLUDED.items())]}


# --- verification --------------------------------------------------------------

def _intrinsic(artifact):
    """Plan-free invariants; also run before any seed write."""
    if artifact.get("artifact") != "validation":
        raise ValidationError("not a validation artifact")
    names = [p["name"] for p in artifact["policies"]]
    if len(set(names)) != len(names):
        raise ValidationError("policy names repeat")
    excluded = {e["check_name"] for e in artifact["excluded"]}
    for policy in artifact["policies"]:
        checks = [r["check_name"] for r in policy["rules"]]
        if len(set(checks)) != len(checks):
            # The engine attributes all of a check's results to its first rule.
            raise ValidationError(f"a check repeats in {policy['name']!r}")
        for rule in policy["rules"]:
            if rule["check_name"] in excluded:
                raise ValidationError(f"{rule['check_name']} is both installed and excluded")
            if rule["engine"] == "graph" and not policy["graph"]:
                raise ValidationError(f"graph rule {rule['name']!r} in a policy without the graph engine")
            if rule["engine"] == "graph" and (rule["roles"] or rule["platforms"]):
                raise ValidationError(f"graph rule {rule['name']!r} carries rule roles the engine ignores")
            if rule["model"] != "predicted" and rule["expected"]:
                raise ValidationError(f"rule {rule['name']!r} predicts failures without a model")


def verify(artifact, plan):
    _intrinsic(artifact)
    if artifact.get("plan_sha256") != digest(plan):
        raise ValidationError("validation artifact is not bound to this canonical plan")
    if canonical(artifact) != canonical(create(plan)):
        raise ValidationError("validation artifact differs from the policies recomputed from the "
                              "bound plan; rebuild it")
    return _summary(artifact)


def _summary(artifact):
    rules = [r for p in artifact["policies"] for r in p["rules"]]
    return {"policies": len(artifact["policies"]), "rules": len(rules),
            "predicted_failing_rules": sum(1 for r in rules if r["expected"]),
            "predicted_findings": sum(len(r["expected"]) for r in rules),
            "excluded_checks": len(artifact["excluded"])}


def build(plan_path, out):
    plan = json.loads(Path(plan_path).read_text())
    artifact = create(plan)
    evidence = verify(artifact, plan)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "validation.json").write_bytes(canonical(artifact) + b"\n")
    (out / "checks.json").write_bytes(canonical({
        "status": "passed", "scope": "offline validation policies and prediction",
        "plan_sha256": artifact["plan_sha256"], **evidence}) + b"\n")
    return {"artifact": artifact["artifact"], "output": str(out), **evidence}


def check(directory, plan_path):
    directory = Path(directory)
    raw = (directory / "validation.json").read_bytes()
    artifact = json.loads(raw)
    evidence = verify(artifact, json.loads(Path(plan_path).read_text()))
    if raw != canonical(artifact) + b"\n":
        raise ValidationError("validation.json bytes differ from their canonical form; rebuild")
    try:
        checks = json.loads((directory / "checks.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValidationError(f"checks.json is missing or unreadable: {exc}")
    if checks.get("status") != "passed" or checks.get("plan_sha256") != artifact["plan_sha256"]:
        raise ValidationError("checks.json does not record a passed check bound to this plan")
    return {"artifact": artifact["artifact"], "checks": "policies verified", **evidence}


# --- seeding -------------------------------------------------------------------

def _value(field):
    return field.get("value") if isinstance(field, dict) else field


def _send(client, method, path, payload=None, timeout=120):
    """One plugin write; returns parsed JSON (None for an empty body)."""
    request = urllib.request.Request(client.base + API + path, method=method,
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers={**client.headers, "Content-Type": "application/json"})
    try:
        with client.opener.open(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if method == "DELETE" and exc.code == 404:
            return None
        raise LoadError(f"{method} {API}{path} returned HTTP {exc.code}: "
                        f"{exc.read(2000).decode(errors='replace')}") from exc


def _ids(client, path, slugs, what):
    out = []
    for slug in slugs:
        _, page = client.request(f"/api/{path}/?slug={slug}", branch=False)
        if page.get("count") != 1:
            raise LoadError(f"expected exactly one {what} with slug {slug!r} on the target, found "
                            f"{page.get('count')}; seed the estate first")
        out.append(page["results"][0]["id"])
    return out


def _subject(result):
    """The comparable subject of one engine result, matching a rule's expected subjects."""
    extra = result.get("extra") or {}
    if result.get("device_name"):
        return result["device_name"]
    if "unprotected_devices" in extra:
        names = sorted(d["name"] for d in extra["unprotected_devices"])
        return f"{extra.get('failure_point')}: {', '.join(names)}"
    return extra.get("failure_point") or result.get("message", "")


def compare(rule, results):
    """Engine failing subjects vs the rule's prediction; differences are recorded, not fatal."""
    failing = Counter(_subject(r) for r in results if _value(r["status"]) == "fail")
    predicted = Counter(e["subject"] for e in rule["expected"])
    other = Counter(_value(r["status"]) for r in results)
    return {"check_name": rule["check_name"], "results": dict(sorted(other.items())),
            "failing": sum(failing.values()), "predicted": sum(predicted.values()),
            "unpredicted": sorted((failing - predicted).elements())[:25],
            "unpredicted_count": sum((failing - predicted).values()),
            "missing": sorted((predicted - failing).elements())[:25],
            "missing_count": sum((predicted - failing).values()),
            "sample": [r["message"] for r in results if _value(r["status"]) == "fail"][:3]}


def _run(client, policy_id, timeout=RUN_TIMEOUT):
    run = _send(client, "POST", "runs/", {"policy": policy_id, "trigger": "manual", "status": "pending"})
    try:
        _send(client, "POST", f"runs/{run['id']}/execute/", {}, timeout=timeout)
    except OSError:
        pass  # a slow synchronous execute times out client-side; the poll below decides
    deadline = time.monotonic() + timeout
    while True:
        _, state = client.request(f"{API}runs/{run['id']}/", branch=False)
        if _value(state["status"]) in TERMINAL or time.monotonic() > deadline:
            return state
        time.sleep(5)


def seed(artifact_dir, *, url, token, receipt_path):
    if os.environ.get("VALIDATION_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError("writing validation policies requires VALIDATION_WRITES=1 in the environment")
    directory = Path(artifact_dir)
    raw = (directory / "validation.json").read_bytes()
    artifact = json.loads(raw)
    _intrinsic(artifact)
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    plugin = status.get("plugins", {}).get(PLUGIN)
    if not plugin:
        raise LoadError(f"the target does not run the {PLUGIN} plugin")
    existing = {p["name"] for p in client.all(f"{API}policies/")}
    clash = sorted(existing & {p["name"] for p in artifact["policies"]})
    if clash:
        raise LoadError(f"policies already exist on the target: {clash}; unseed their receipt first")
    receipt = {"receipt_version": RECEIPT_VERSION, "writer_version": WRITER_VERSION,
               "artifact": str(directory), "validation_sha256": hashlib.sha256(raw).hexdigest(),
               "plan_sha256": artifact["plan_sha256"], "target": client.base,
               "plugin_version": plugin, "netbox_version": status.get("netbox-version"),
               "policies": {}, "runs": {}}
    _write_receipt(receipt_path, receipt)
    # Resolve every slug before the first write, so a missing estate writes nothing.
    scopes = {}
    for policy in artifact["policies"]:
        rule_roles = {s for r in policy["rules"] for s in r["roles"]}
        rule_platforms = {s for r in policy["rules"] for s in r["platforms"]} | set(policy["platforms"])
        roles = dict(zip(sorted(set(policy["roles"]) | rule_roles),
                         _ids(client, "dcim/device-roles", sorted(set(policy["roles"]) | rule_roles), "device role")))
        platforms = dict(zip(sorted(rule_platforms),
                             _ids(client, "dcim/platforms", sorted(rule_platforms), "platform")))
        groups = _ids(client, "dcim/site-groups", policy["site_groups"], "site group")
        scopes[policy["name"]] = (roles, platforms, groups)
    for policy in artifact["policies"]:
        roles, platforms, groups = scopes[policy["name"]]
        row = _send(client, "POST", "policies/", {
            "name": policy["name"], "description": policy["description"], "is_active": True,
            "site_groups": groups, "roles": [roles[s] for s in policy["roles"]],
            "platforms": [platforms[s] for s in policy["platforms"]],
            "enable_graph_engine": policy["graph"], "enable_config_engine": False})
        entry = receipt["policies"][policy["name"]] = {"id": row["id"], "rules": {}}
        _write_receipt(receipt_path, receipt)
        for rule in policy["rules"]:
            created = _send(client, "POST", "rules/", {
                "policy": row["id"], "name": rule["name"], "engine": rule["engine"],
                "category": rule["category"], "check_name": rule["check_name"],
                "severity": rule["severity"], "parameters": rule["parameters"], "is_active": True,
                "description": rule["derivation"][:200],
                "roles": [roles[s] for s in rule["roles"]],
                "platforms": [platforms[s] for s in rule["platforms"]]})
            entry["rules"][rule["name"]] = created["id"]
        _write_receipt(receipt_path, receipt)
    receipt["readback"] = readback(client, artifact, receipt, receipt_path)
    _write_receipt(receipt_path, receipt)
    return {"receipt": str(receipt_path), "policies": len(receipt["policies"]),
            "runs": {name: {k: run[k] for k in ("id", "status", "score")}
                     for name, run in receipt["runs"].items()},
            **receipt["readback"]["totals"]}


def readback(client, artifact, receipt, receipt_path=None):
    """Run every seeded policy once and compare its failing subjects to the prediction."""
    comparisons, totals = {}, Counter()
    for policy in artifact["policies"]:
        entry = receipt["policies"][policy["name"]]
        live = {r["id"]: r for r in client.all(f"{API}rules/?policy_id={entry['id']}")}
        for rule in policy["rules"]:
            row = live.get(entry["rules"][rule["name"]])
            if (not row or row["check_name"] != rule["check_name"] or row["parameters"] != rule["parameters"]
                    or _value(row["engine"]) != rule["engine"]):
                raise LoadError(f"rule {rule['name']!r} did not read back as written")
        state = _run(client, entry["id"])
        receipt["runs"][policy["name"]] = run = {
            "id": state["id"], "status": _value(state["status"]), "score": state.get("score"),
            **{k: state.get(k) for k in ("total_checks", "passed_checks", "failed_checks",
                                         "warning_checks", "error_checks", "started_at", "completed_at")}}
        if receipt_path:
            _write_receipt(receipt_path, receipt)
        if run["status"] not in TERMINAL:
            raise LoadError(f"run {run['id']} for {policy['name']!r} did not finish within {RUN_TIMEOUT}s")
        # Grouped by check, not rule id: 1.14.1 files every result of a check
        # under the first rule carrying it, and a policy holds one rule per check.
        results = defaultdict(list)
        for result in client.all(f"{API}results/?run_id={run['id']}"):
            results[result["check_name"]].append(result)
        for rule in policy["rules"]:
            row = compare(rule, results.get(rule["check_name"], []))
            comparisons[f"{policy['name']} / {rule['name']}"] = row
            totals["failing"] += row["failing"]
            totals["predicted"] += row["predicted"]
            totals["unpredicted"] += row["unpredicted_count"]
            totals["missing"] += row["missing_count"]
            totals["rules_matching"] += not (row["unpredicted_count"] or row["missing_count"])
            totals["rules"] += 1
    return {"rules": comparisons, "totals": dict(totals)}


def unseed(receipt_path, *, url, token):
    """Delete exactly the runs and policies a seed receipt recorded (rules and results cascade)."""
    if os.environ.get("VALIDATION_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError("removing validation policies requires VALIDATION_WRITES=1 in the environment")
    receipt = json.loads(Path(receipt_path).read_text())
    client = Client(url, token)
    if receipt.get("target") != client.base:
        raise LoadError(f"receipt {receipt_path} belongs to {receipt.get('target')}, not {client.base}")
    policies = [entry["id"] for entry in receipt.get("policies", {}).values()]
    runs = [run["id"] for run in receipt.get("runs", {}).values()]
    # Runs left behind by a policy outlive it on 1.14.1, so they go first.
    for policy in policies:
        runs += [r["id"] for r in client.all(f"{API}runs/?policy_id={policy}")]
    for run in sorted(set(runs)):
        _send(client, "DELETE", f"runs/{run}/")
    for policy in policies:
        _send(client, "DELETE", f"policies/{policy}/")
    survivors = [f"policies/{p}" for p in policies
                 if client.request(f"{API}policies/?id={p}", branch=False)[1].get("count")]
    if survivors:
        raise LoadError(f"validation policies survived removal: {survivors}")
    return {"deleted_runs": len(set(runs)), "deleted_policies": len(policies),
            "receipt": str(receipt_path), "success": True}


def default_receipt(artifact_dir, target):
    slug = Path(artifact_dir).name or "validation"
    suffix = hashlib.sha256(f"{slug}\n{target.rstrip('/')}".encode()).hexdigest()[:12]
    return Path("build/load-receipts") / f"{slug}-validation-{suffix}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Derive, verify and seed plan-derived policies for NetBox Validation")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="derive the validation artifact from a frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="recompute a saved validation artifact from its bound plan")
    p.add_argument("directory", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("seed", help="create, run and read back the policies (VALIDATION_WRITES=1)")
    p.add_argument("directory", type=Path)
    p.add_argument("target")
    p.add_argument("--receipt", type=Path)
    p = sub.add_parser("unseed", help="delete exactly the policies and runs a receipt recorded (VALIDATION_WRITES=1)")
    p.add_argument("receipt", type=Path)
    p.add_argument("target")
    args = parser.parse_args(argv)
    try:
        if args.command in ("build", "check"):
            result = (build(args.plan, args.out) if args.command == "build"
                      else check(args.directory, args.plan))
            print(f"{result['artifact']}: {result['policies']} policies, {result['rules']} rules, "
                  f"{result['predicted_findings']} predicted failing subjects across "
                  f"{result['predicted_failing_rules']} rules, {result['excluded_checks']} checks excluded"
                  + (f" -> {result['output']}" if args.command == "build" else ""))
        else:
            token = os.environ.get("NETBOX_TOKEN") or parser.error("NETBOX_TOKEN is required")
            if args.command == "unseed":
                print(json.dumps(unseed(args.receipt, url=args.target, token=token), sort_keys=True))
            else:
                receipt = args.receipt or default_receipt(args.directory, args.target)
                print(f"Receipt: {receipt}", flush=True)
                print(json.dumps(seed(args.directory, url=args.target, token=token,
                                      receipt_path=receipt), sort_keys=True))
        return 0
    except (ValidationError, LoadError, OSError, ValueError, KeyError) as exc:
        import sys
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
