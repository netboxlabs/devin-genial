"""One property-selected wiring defect, with exact evidence and offline restoration.

Diode matches cables by terminations. These are independently loadable snapshots,
not an in-place rewiring or retirement workflow for an already populated target.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal

from . import __version__
from .model import DesignError, canonical, digest, hardware_catalog
from .report import _cell, _table
from .scenarios import _changes
from .validate import validate


SCENARIO = "loss-of-power-diversity"
PROFILES = {"regional-bank", "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone"}
EXECUTION = {"transition": "review-only", "snapshots": "fresh-target-only-unverified", "applied_to_target": False}
LIMITATIONS = [
    "One planted wiring defect can produce multiple independent validation findings.",
    "Separate modeled PDUs/panels do not establish independent facility supplies or running application availability.",
    "The changed cable has a new termination identity. Diode snapshot replay does not retire the old target cable.",
    "Do not replay the changed snapshot into its loaded baseline; no in-place rewiring or rollback is qualified.",
    "Offline inverse restoration recovers the frozen baseline. Live restoration requires a separately qualified fresh target or authorized full reset.",
    "These snapshots do not execute product branching, approval, drift detection, audit history or application failover.",
]


def _require(condition, message):
    if not condition:
        raise DesignError(f"Power scenario: {message}")


def _healthy(plan):
    _require(isinstance(plan, dict) and isinstance(plan.get("recipe"), dict), "baseline must be a frozen plan")
    _require(type(plan.get("schema_version")) is int and plan["schema_version"] == 1 and
             plan.get("generator_version") == __version__, "baseline needs canonical schema 1 and the current generator version; regenerate unsupported frozen plans")
    _require(plan["recipe"].get("profile") in PROFILES, "baseline needs a supported bank, enterprise DC, school, hospital or provider profile")
    _require(plan.get("hardware_digest") == digest(hardware_catalog()), "baseline hardware catalog fingerprint differs from this generator")
    findings = validate(plan)
    _require(not findings, f"baseline must be healthy before planting a defect: {findings[:3]}")


def _graph(plan):
    objects = {obj["key"]: obj for obj in plan["objects"]}
    children, peers, cables = defaultdict(list), {}, {}
    for key, obj in objects.items():
        for field, value in obj["refs"].items():
            for target in value if isinstance(value, list) else [value]:
                children[(field, target)].append(key)
        if obj["kind"] == "cable":
            a, b = obj["refs"]["a"], obj["refs"]["b"]
            peers[a], peers[b] = b, a
            cables[a] = cables[b] = key
    return objects, children, peers, cables


def _paths(graph, device):
    objects, children, peers, cables = graph
    host = objects[device]
    result = []
    for port in sorted(key for key in children[("device", device)] if objects[key]["kind"] == "power_port"):
        outlet = peers.get(port)
        if not outlet or objects[outlet]["kind"] != "power_outlet":
            return []
        pdu, inlet = (objects[outlet]["refs"].get(field) for field in ("device", "power_port"))
        feed = peers.get(inlet)
        panel = objects.get(feed, {}).get("refs", {}).get("power_panel")
        if (not all(key in objects for key in (pdu, inlet, feed, panel)) or
                objects[inlet]["kind"] != "power_port" or objects[inlet]["refs"].get("device") != pdu or
                objects[feed]["kind"] != "power_feed" or objects[panel]["kind"] != "power_panel" or
                any(objects[cables[key]]["attrs"].get("status") != "connected" for key in (port, inlet)) or
                any(objects[key]["attrs"].get("status") != "active" for key in (device, pdu, feed)) or
                any(objects[key]["refs"].get("rack") != host["refs"].get("rack") for key in (pdu, feed)) or
                any(objects[key]["refs"].get("location") != host["refs"].get("location") for key in (pdu, panel)) or
                objects[panel]["refs"].get("site") != host["refs"].get("site")):
            return []
        result.append(dict(device=device, power_port=port, outlet=outlet, pdu=pdu, inlet=inlet,
                           feed=feed, panel=panel, cables=[cables[port], cables[inlet]]))
    return result


def _affected(graph, device):
    objects, children, _, _ = graph
    vms = sorted(key for key in children[("device", device)] if objects[key]["kind"] == "virtual_machine"
                 and objects[key]["attrs"].get("status") == "active")
    services = sorted(key for vm in vms for key in children[("virtual_machine", vm)]
                      if objects[key]["kind"] == "service" and objects[key]["refs"].get("ipaddresses"))
    addresses = sorted({ip for key in services for ip in objects[key]["refs"]["ipaddresses"]})
    return {"virtual_machines": vms, "services": services, "ip_addresses": addresses}


def _choices(graph, site_id):
    objects, children, peers, _ = graph
    free, feed_load = defaultdict(list), defaultdict(Decimal)
    for key, obj in objects.items():
        if obj["kind"] != "power_outlet":
            continue
        if key not in peers:
            free[obj["refs"]["device"]].append(key)
        else:
            feed = peers.get(obj["refs"].get("power_port"))
            feed_load[feed] += Decimal(str(objects[peers[key]]["attrs"].get("maximum_draw", 0)))
    for outlets in free.values():
        outlets.sort()
    # ponytail: sorted canonical host identity is the selection policy. It is
    # stable across emission order; a frozen scenario pins its chosen subject.
    for device in sorted(key for key, obj in objects.items() if obj["kind"] == "device"):
        host = objects[device]
        if (host["kind"] != "device" or host["refs"].get("role") != "role/server" or
                host["attrs"].get("status") != "active" or not host["refs"].get("rack") or
                (site_id is not None and host["refs"].get("site") != f"site/{site_id}")):
            continue
        affected = _affected(graph, device)
        paths = _paths(graph, device)
        if not affected["services"] or len(paths) != 2 or any(len({p[field] for p in paths}) != 2 for field in ("pdu", "feed", "panel")):
            continue
        # Try both directions so a full A-side PDU does not hide an eligible
        # reverse move. The scenario never fabricates a new electrical outlet.
        for keep, move in (paths, paths[::-1]):
            feed = objects[keep["feed"]]["attrs"]
            budget = Decimal(str(feed.get("voltage", 0))) * Decimal(str(feed.get("amperage", 0))) * Decimal(str(feed.get("max_utilization", 0))) / 100
            draw = Decimal(str(objects[move["power_port"]]["attrs"].get("maximum_draw", 0)))
            if feed.get("phase") != "single-phase" or feed.get("supply") != "ac" or feed_load[keep["feed"]] + draw > budget:
                continue
            for spare in free[keep["pdu"]]:
                # Only the source-checked reference host C14 / PDU C13 pair is
                # reviewed here; other connector families need an explicit rule.
                if (objects[move["power_port"]]["attrs"].get("type") == "iec-60320-c14" and
                        objects[spare]["attrs"].get("type") == "iec-60320-c13" and
                        objects[spare]["refs"].get("power_port") == keep["inlet"]):
                    yield device, move["cables"][0], move["outlet"], spare
                    break


def _rewire(baseline, choice):
    device, cable, old_outlet, spare = choice
    changed = deepcopy(baseline)
    record = next(obj for obj in changed["objects"] if obj["key"] == cable)
    record["refs"] = {field: spare if target == old_outlet else target for field, target in record["refs"].items()}
    record["key"] = "cable/" + "--".join(sorted(record["refs"].values()))
    return changed


def _expected(plan, device):
    codes = ["power-redundancy"]
    if plan["recipe"]["profile"] != "regional-bank":
        codes.append("dc-power-diversity")
    return [{"code": code, "object": device} for code in sorted(codes)]


def _pairs(findings):
    return Counter((item["code"], item["object"]) for item in findings)


def _derive(baseline, changed, site_id, device):
    """Recompute the complete explanatory envelope from the two frozen graphs."""
    _healthy(baseline)
    _require(site_id is None or isinstance(site_id, str), "site filter must be a site ID or omitted")
    _require(isinstance(changed, dict), "changed snapshot must be a plan")
    _require(canonical({key: value for key, value in baseline.items() if key != "objects"}) ==
             canonical({key: value for key, value in changed.items() if key != "objects"}),
             "recipe, contracts and allocation/reservation ledgers must stay unchanged")
    graph = _graph(baseline)
    objects = graph[0]
    _require(device in objects, "selected subject is missing from baseline")
    diff = _changes(baseline, changed)
    _require(diff["counts"] == {"create": 1, "update": 0, "delete": 1} and
             all(obj["kind"] == "cable" for obj in diff["create"] + diff["delete"]),
             "the change must replace exactly one cable and preserve every other record")
    old, new = diff["delete"][0], diff["create"][0]
    _require(len(changed.get("objects", [])) == len(baseline["objects"]) and
             len({obj["key"] for obj in changed["objects"]}) == len(changed["objects"]),
             "changed snapshot cannot add duplicate object identities")
    choices = (choice for choice in _choices(graph, site_id) if choice[0] == device and choice[1] == old["key"])
    choice = next((choice for choice in choices if new["refs"] ==
                   {field: choice[3] if target == choice[2] else target for field, target in old["refs"].items()}), None)
    _require(choice is not None, "selected change is not an eligible local spare-outlet move with electrical headroom")
    _require(canonical(changed) == canonical(_rewire(baseline, choice)), "changed snapshot differs from the exact single-cord mutation")
    actual = validate(changed)
    expected = _expected(baseline, device)
    _require(_pairs(actual) == _pairs(expected), f"changed snapshot must contain exactly the expected findings: {expected}; observed {actual[:5]}")
    before_paths, after_paths = _paths(graph, device), _paths(_graph(changed), device)
    _require(len(after_paths) == 2 and all(len({p[field] for p in after_paths}) == 1 for field in ("pdu", "feed", "panel")),
             "both actual supply paths must converge on one PDU, feed and panel")
    restored = deepcopy(changed)
    restored["objects"] = [deepcopy(old) if obj["key"] == new["key"] else obj for obj in restored["objects"]]
    _require(canonical(restored) == canonical(baseline), "inverse cable replacement must restore the exact frozen baseline")
    affected = _affected(graph, device)
    hashes = {"baseline": digest(baseline), "changed": digest(changed)}
    checks = dict(baseline_valid=True, changed_expected_defect=True, one_cable_replaced=True,
                  other_objects_unchanged=True, allocations_unchanged=True, exact_inverse_restoration=True,
                  defects=1, validation_findings=len(expected), plan_sha256=hashes)
    restoration = {"baseline_sha256": hashes["baseline"],
                   "inverse": {"create": [old], "update": [], "delete": [new]},
                   "offline": "Inverse replacement restores the exact frozen baseline.",
                   "live": "Fresh target or authorized full reset required; in-place rewiring and rollback are unqualified."}
    answers = [
        {"id": "shared-failure-domain", "question": "Why do two connected supplies no longer provide power diversity?",
         "answer": "Both cords now terminate on the same PDU and upstream panel.",
         "evidence": after_paths},
        {"id": "exposed-services", "question": "Which modeled workloads and listeners depend on this host?",
         "answer": affected, "evidence": [device] + affected["virtual_machines"] + affected["services"]},
        {"id": "restoration-boundary", "question": "What does restoration prove, and what remains unqualified?",
         "answer": restoration, "evidence": [old["key"], new["key"]]},
    ]
    return dict(schema_version=1, scenario=SCENARIO, selection={"site_filter": site_id}, subject=device,
                plans={"baseline": baseline, "changed": changed}, changes=diff, expected_findings=expected,
                paths={"baseline": before_paths, "changed": after_paths}, affected=affected, answers=answers,
                checks=checks, restoration=restoration, execution=deepcopy(EXECUTION), limitations=list(LIMITATIONS))


def create(plan, site_id=None):
    """Select one eligible service host and return two evidence-backed snapshots."""
    try:
        _healthy(plan)
        _require(site_id is None or isinstance(site_id, str), "site filter must be a site ID or omitted")
        graph = _graph(plan)
        if site_id is not None:
            _require(graph[0].get(f"site/{site_id}", {}).get("kind") == "site", f"site {site_id!r} is absent from the baseline")
        choice = next(_choices(graph, site_id), None)
        _require(choice is not None, "no eligible active service host with two independent supplies and a compatible spare outlet within power budget; choose a DC site or extend the supported estate")
        baseline = deepcopy(plan)
        return _derive(baseline, _rewire(baseline, choice), site_id, choice[0])
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"Power scenario: malformed baseline or unsupported graph ({type(exc).__name__})") from None


def verify(envelope):
    """Verify frozen graph evidence; declared answers/findings cannot waive checks."""
    try:
        _require(isinstance(envelope, dict) and type(envelope.get("schema_version")) is int and envelope["schema_version"] == 1 and
                 envelope.get("scenario") == SCENARIO, "unsupported scenario envelope")
        plans = envelope["plans"]
        _require(set(plans) == {"baseline", "changed"}, "scenario needs exactly baseline and changed snapshots")
        expected = _derive(plans["baseline"], plans["changed"], envelope["selection"]["site_filter"], envelope["subject"])
        _require(set(envelope) - {"plan_files"} == set(expected), "scenario envelope has missing or unsupported claims")
        for key in expected:
            _require(canonical(envelope[key]) == canonical(expected[key]), f"{key} does not match independently derived scenario evidence")
        return deepcopy(expected["checks"])
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"Power scenario: malformed scenario envelope ({type(exc).__name__})") from None


def markdown(envelope):
    """Render an inspectable guide only after independently rechecking evidence."""
    verify(envelope)
    objects = _graph(envelope["plans"]["baseline"])[0]
    device = objects[envelope["subject"]]
    def name(key):
        return objects[key]["attrs"].get("name", key)
    context = f"Rack **{_cell(name(device['refs']['rack']))}** in **{_cell(name(device['refs']['location']))}**; cluster **{_cell(name(device['refs']['cluster']))}**."
    lines = ["# Loss of power diversity", "",
             f"Host **{_cell(device['attrs']['name'])}** at **{_cell(objects[device['refs']['site']]['attrs']['name'])}** has two connected supply cords. The changed snapshot moves one cord onto the PDU already serving the other supply.", "", context, "",
             "The service inventory remains intact. One PDU or panel is now a shared modeled failure domain. This demonstrates a wiring defect, not running application failover.", "",
             "## Inspect the supply paths", ""]
    _table(lines, ["Snapshot", "Supply", "Outlet", "PDU", "Feed", "Panel"],
           [[stage, name(path["power_port"]), name(path["outlet"]), name(path["pdu"]), name(path["feed"]), name(path["panel"])]
            for stage, paths in envelope["paths"].items() for path in paths])
    lines.extend(["", "## Follow the dependent services", ""])
    _table(lines, ["VM", "Listener", "Protocol / ports", "Addresses"],
           [[objects[objects[key]["refs"]["virtual_machine"]]["attrs"]["name"], objects[key]["attrs"]["name"],
             f"{objects[key]['attrs']['protocol']} / {', '.join(str(port) for port in objects[key]['attrs']['ports'])}",
             ", ".join(objects[ip]["attrs"]["address"] for ip in objects[key]["refs"]["ipaddresses"])]
            for key in envelope["affected"]["services"]])
    lines.extend(["", "## Expected findings", "", "One planted defect; the independent validators report:", ""])
    for finding in envelope["expected_findings"]:
        lines.append(f"- `{finding['code']}` on **{_cell(name(finding['object']))}**")
    lines.extend(["", "The scenario check requires exactly this finding set. Ordinary plan checking correctly rejects the changed snapshot as defective.", "",
                  "## Restore the baseline", "", envelope["restoration"]["offline"], "",
                  "**Review-only transition.** Baseline and changed snapshots require separate fresh-target qualification. Live restoration requires a fresh target or authorized full reset. Do not replay either snapshot over the other: Diode does not retire the replaced cable identity, and in-place rewiring or rollback is unqualified.", "",
                  "## Boundaries", ""])
    lines.extend(f"- {limitation}" for limitation in (LIMITATIONS[1], LIMITATIONS[5]))
    return "\n".join(lines) + "\n"
