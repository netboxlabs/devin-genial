"""One installed provider span offline, with actual paths and exact restoration.

Traffic is the existing directed spoke-to-hub plan, not executed forwarding.
Status-only Diode updates need separate pinned-target qualification.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal

from .model import DesignError, canonical, digest
from .power_scenario import _healthy as _healthy_plan
from .report import _cell, _table
from .scenarios import _changes
from .validate import validate
from .validate_provider import _connected, _loads, _recipe, _tree


SCENARIO = "provider-span-maintenance"
EXECUTION = {"transition": "status-update-unverified", "snapshots": "fresh-target-only-unverified", "applied_to_target": False}
SCOPE = "Declared customer spoke-to-hub peak only; shortest-hop paths with canonical edge-key ties; each full-duplex direction is independent."
LIMITATIONS = [
    "The alternate route is modeled inventory intent, not executed routing, measured throughput or customer availability.",
    "Return, arbitrary peer-to-peer, NOC and transit traffic are excluded; router removal proves backbone connectivity only, not protection for single-homed premises.",
    "A span offline can reduce further-failure protection; only the derived additional-failure witnesses establish that loss.",
    "Carrier interior, duct diversity, optical loss and convergence time are unknown; local circuit handoffs do not prove them.",
    "Carrier-wide failures are not checked. Per-PoP carrier diversity is not guaranteed; two spans can share one provider.",
    "Installed cables, interfaces and optical power remain unchanged. No approval, dated work completion or journal event is fabricated.",
    "Offline restoration is exact. Diode status update, repeated update and same-ID live restoration require separate pinned-target evidence.",
]


def _require(condition, message):
    if not condition:
        raise DesignError(f"Span scenario: {message}")


def _graph(plan):
    """Index the supported direct PE transport and actual service attachments."""
    objects = {o["key"]: o for o in plan["objects"]}
    peers, cables, terms, assignments = {}, {}, defaultdict(list), defaultdict(list)
    routers = {k for k, o in objects.items() if o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge"}
    for key, obj in sorted(objects.items()):
        refs = obj["refs"]
        if obj["kind"] == "cable":
            a, b = refs["a"], refs["b"]
            peers[a], peers[b], cables[a], cables[b] = b, a, key, key
        elif obj["kind"] == "circuit_termination":
            terms[refs["circuit"]].append(key)
        elif obj["kind"] == "contact_assignment":
            assignments[refs["object"]].append(dict(assignment=key, object=refs["object"],
                contact=refs["contact"], role=refs["role"], priority=obj["attrs"].get("priority")))

    def port(key):
        obj = objects.get(key, {})
        owner = objects.get(obj.get("refs", {}).get("device"), {})
        return (obj.get("kind") == "interface" and obj["attrs"].get("type") not in (None, "virtual", "lag", "bridge") and
                obj["attrs"].get("enabled") is True and owner.get("kind") == "device" and owner["attrs"].get("status") == "active")

    def end(term):
        key = peers.get(term)
        _require(port(key), "transport requires direct typed physical handoffs; passive backbone shapes are unsupported")
        obj, iface = objects[term], objects[key]
        device = iface["refs"]["device"]
        site = objects[device]["refs"]["site"]
        _require(obj["refs"]["termination"] == site and objects[cables[term]]["attrs"].get("status") == "connected",
                 "circuit handoff must be connected at its actual device site")
        return dict(termination=term, side=obj["attrs"]["term_side"], site=site, device=device,
                    interface=key, cable=cables[term], interface_type=iface["attrs"]["type"],
                    speed_kbps=iface["attrs"]["speed"], port_speed_kbps=obj["attrs"]["port_speed"])

    circuit_ends, edges = {}, {}
    for circuit, ends in sorted(terms.items()):
        # Opaque transit has no second local owner. It is outside this story.
        if len(ends) != 2 or any(not peers.get(term) for term in ends):
            continue
        sides = sorted((end(term) for term in ends), key=lambda value: value["side"])
        _require([value["side"] for value in sides] == ["A", "Z"], "a circuit needs its actual A/Z handoffs")
        circuit_ends[circuit] = sides
        if all(value["device"] in routers for value in sides):
            obj = objects[circuit]
            _require(obj["attrs"].get("status") == "active" and sides[0]["site"] != sides[1]["site"],
                     "eligible leased spans must be active between different PoPs")
            rate = min([obj["attrs"]["commit_rate"]] + [value[field] for value in sides for field in ("speed_kbps", "port_speed_kbps")])
            edges[circuit] = dict(key=circuit, kind="span", ends=sides, limit_kbps=rate,
                circuit=circuit, provider=obj["refs"]["provider"], provider_account=obj["refs"]["provider_account"],
                commit_rate_kbps=obj["attrs"]["commit_rate"])
    for key, obj in sorted(objects.items()):
        if obj["kind"] != "cable":
            continue
        a, b = obj["refs"]["a"], obj["refs"]["b"]
        if not (port(a) and port(b)):
            continue
        owners = [objects[p]["refs"]["device"] for p in (a, b)]
        if not all(owner in routers for owner in owners):
            continue
        sites = [objects[owner]["refs"]["site"] for owner in owners]
        _require(sites[0] == sites[1] and obj["attrs"].get("status") == "connected", "PE pair must be one connected local cable")
        edge = "pair/" + sites[0].removeprefix("site/")
        _require(edge not in edges, "one actual PE pair cable is supported per PoP")
        ends = [dict(device=objects[p]["refs"]["device"], site=sites[0], interface=p, cable=key,
                     interface_type=objects[p]["attrs"]["type"], speed_kbps=objects[p]["attrs"]["speed"]) for p in (a, b)]
        edges[edge] = dict(key=edge, kind="pair", ends=sorted(ends, key=lambda value: value["device"]),
                           limit_kbps=min(value["speed_kbps"] for value in ends), cable=key)
    adjacency = {router: [] for router in sorted(routers)}
    for key, edge in sorted(edges.items()):
        a, b = (value["device"] for value in edge["ends"])
        adjacency[a].append((key, b, key)); adjacency[b].append((key, a, key))
    for rows in adjacency.values():
        rows.sort()
    return objects, peers, circuit_ends, edges, adjacency, assignments


def _traffic(plan, graph):
    objects, peers, circuits, _, adjacency, _ = graph
    _, customers, demand, _, usable = _recipe(plan["recipe"])
    term_owner = {side["interface"]: (key, side, other) for key, ends in circuits.items()
                  for side, other in (ends, ends[::-1])}
    premises = {}
    for key, obj in sorted(objects.items()):
        if obj["kind"] != "virtual_circuit_termination":
            continue
        iface = obj["refs"]["interface"]
        virtual = objects[iface]
        ce = virtual["refs"]["device"]
        site = objects[ce]["refs"]["site"]
        parent = virtual["refs"]["parent"]
        _require(parent in term_owner, "private-L3 membership needs its actual direct customer access circuit")
        circuit, local, remote = term_owner[parent]
        vc = obj["refs"]["virtual_circuit"]
        tenant = objects[vc]["refs"]["tenant"]
        _require(remote["device"] in adjacency and local["device"] == ce and site not in premises and
                 objects[site]["refs"].get("tenant") == tenant and objects[ce]["refs"].get("tenant") == tenant,
                 "customer service, premise, CE and actual access attachment must share ownership")
        premises[site] = dict(site=site, tenant=tenant, virtual_circuit=vc, membership=key, ce=ce,
            service_interface=iface, access_circuit=circuit, local=local, pe=remote)
    _require(set(premises) == {"site/" + key for key in demand}, "actual private-L3 membership must match every demanded premise")
    rows, flows = [], Counter()
    for customer in sorted(customers, key=lambda value: value["key"]):
        hub = "site/ce-" + customer["key"] + "-" + customer["hub_pop"] + "-001"
        vc = premises[hub]["virtual_circuit"]
        for site in sorted(premises):
            if premises[site]["virtual_circuit"] != vc or site == hub:
                continue
            origin, destination = premises[site]["pe"]["device"], premises[hub]["pe"]["device"]
            peak = customer["site_peak_mbps"] * 1000
            rows.append(dict(site=site, hub=hub, tenant=premises[site]["tenant"], virtual_circuit=vc,
                             from_pe=origin, to_pe=destination, offered_kbps=peak))
            flows[(origin, destination)] += peak
    return premises, rows, flows, usable


def _routes(adjacency, rows, excluded=None):
    trees = {origin: _tree(adjacency, origin, excluded) for origin in sorted({row["from_pe"] for row in rows})}
    result = []
    for row in rows:
        origin, destination = row["from_pe"], row["to_pe"]
        tree, hops = trees[origin], []
        if destination not in tree:
            return None
        while destination != origin:
            previous, edge = tree[destination]
            hops.append(dict(edge=edge, from_pe=previous, to_pe=destination))
            destination = previous
        result.append({**row, "hops": list(reversed(hops))})
    return result


def _directions(edges, before, after, usable, offline):
    return [dict(edge=edge, from_pe=origin, to_pe=destination, limit_kbps=edges[edge]["limit_kbps"],
                 usable_kbps=str(Decimal(edges[edge]["limit_kbps"]) * usable),
                 baseline_state="active", maintenance_state="offline" if edge == offline else "active",
                 baseline_available_kbps=str(Decimal(edges[edge]["limit_kbps"]) * usable),
                 maintenance_available_kbps=str(0 if edge == offline else Decimal(edges[edge]["limit_kbps"]) * usable),
                 baseline_kbps=before[(edge, origin, destination)], maintenance_kbps=after[(edge, origin, destination)],
                 delta_kbps=after[(edge, origin, destination)] - before[(edge, origin, destination)],
                 headroom_kbps=str((0 if edge == offline else Decimal(edges[edge]["limit_kbps"]) * usable) - after[(edge, origin, destination)]))
            for edge, origin, destination in sorted(before.keys() | after.keys())]


def _maintenance(adjacency, edges, flows, span, usable):
    maintained = {router: [row for row in rows if row[2] != span] for router, rows in adjacency.items()}
    loads = _loads(maintained, flows)
    good = _connected(maintained) and loads is not None and all(Decimal(load) <= Decimal(edges[edge]["limit_kbps"]) * usable
                                                           for (edge, _, _), load in (loads or {}).items())
    return maintained, loads, good


def _expected(graph, maintained, flows, span, usable):
    """Predict findings from the actual one-span exclusion, never validate output."""
    objects, _, _, edges, _, _ = graph
    findings = [dict(code="provider-circuit-path", object=span)]
    findings.extend(dict(code="optics-path", object=end["interface"]) for end in edges[span]["ends"]
                    if objects[end["interface"]]["refs"].get("module"))
    resilience = []
    for router in sorted(maintained):
        if not _connected(maintained, removed=router):
            resilience.append(dict(code="provider-router-connectivity", object=router, additional_failure="router"))
    for edge in sorted(set(edges) - {span}):
        if not _connected(maintained, excluded=edge):
            resilience.append(dict(code="provider-link-connectivity", object=edge, additional_failure="link"))
    for removed in (None, *sorted(key for key, edge in edges.items() if edge["kind"] == "span")):
        loads = _loads(maintained, flows, excluded=removed)
        if loads is None:
            resilience.append(dict(code="provider-customer-route", object=removed or "plan", additional_failure="span"))
            continue
        for (edge, origin, destination), load in sorted(loads.items()):
            if Decimal(load) > Decimal(edges[edge]["limit_kbps"]) * usable:
                resilience.append(dict(code="provider-route-capacity", object=edge, additional_failure="span", removed_span=removed,
                                       from_pe=origin, to_pe=destination, load_kbps=load,
                                       usable_kbps=str(Decimal(edges[edge]["limit_kbps"]) * usable)))
    findings.extend(dict(code=row["code"], object=row["object"]) for row in resilience)
    return sorted(findings, key=lambda row: (row["code"], row["object"])), resilience


def _status(plan, span, status):
    result = deepcopy(plan)
    next(obj for obj in result["objects"] if obj["key"] == span)["attrs"]["status"] = status
    return result


def _derive(baseline, changed, span_filter, subject):
    _require(isinstance(baseline, dict) and baseline.get("recipe", {}).get("profile") == "provider-backbone", "a frozen provider-backbone baseline is required")
    _healthy_plan(baseline)
    _require(span_filter is None or isinstance(span_filter, str), "span filter must be a circuit key or omitted")
    graph = _graph(baseline)
    objects, _, _, edges, adjacency, assignments = graph
    premises, rows, flows, usable = _traffic(baseline, graph)
    before = _routes(adjacency, rows)
    _require(before is not None, "baseline declared flow must have actual PE paths")
    affected_counts = Counter(hop["edge"] for row in before for hop in row["hops"] if edges[hop["edge"]]["kind"] == "span")
    candidates = sorted(affected_counts, key=lambda key: (-affected_counts[key], key))
    if span_filter is not None:
        _require(span_filter in candidates, "selected span must be active, used by a declared customer path and have two real PE handoffs")
        candidates = [span_filter]
    selected = next((key for key in candidates if _maintenance(adjacency, edges, flows, key, usable)[2]), None)
    _require(selected is not None, "no used active span retains all modeled customer routes and directed capacity during maintenance")
    _require(subject is None or subject == selected, "subject does not match the frozen span selection")
    span = selected
    exact = _status(baseline, span, "offline")
    if changed is None:
        changed = exact
    _require(canonical(changed) == canonical(exact), "change must set only the selected Circuit status offline, preserving every other record and plan field")
    diff = _changes(baseline, changed)
    _require(diff["counts"] == {"create": 0, "update": 1, "delete": 0}, "exactly one status update is required")
    maintained, after_loads, good = _maintenance(adjacency, edges, flows, span, usable)
    _require(good, "maintenance must retain current modeled connectivity and usable directed capacity")
    after = _routes(maintained, rows)
    directions = _directions(edges, _loads(adjacency, flows), after_loads, usable, span)
    busier = {(row["edge"], row["from_pe"], row["to_pe"]) for row in directions if row["delta_kbps"] > 0}
    expected, resilience = _expected(graph, maintained, flows, span, usable)
    actual = validate(changed)
    _require(Counter((row["code"], row["object"]) for row in actual) == Counter((row["code"], row["object"]) for row in expected),
             f"changed snapshot must contain exactly graph-derived maintenance findings; expected {expected[:5]}, observed {actual[:5]}")
    restored = _status(changed, span, "active")
    _require(canonical(restored) == canonical(baseline), "inverse status update must restore the exact frozen baseline")
    changed_rows = [old for old, new in zip(before, after) if old["hops"] != new["hops"]]
    affected = dict(premises=sorted(row["site"] for row in changed_rows), hubs=sorted({row["hub"] for row in changed_rows}),
                    tenants=sorted({row["tenant"] for row in changed_rows}), virtual_circuits=sorted({row["virtual_circuit"] for row in changed_rows}),
                    unchanged_premises_sharing_increased_load=[old["site"] for old, new in zip(before, after) if old["hops"] == new["hops"] and
                        any((hop["edge"], hop["from_pe"], hop["to_pe"]) in busier for hop in new["hops"])])
    end_sites = {end["site"] for end in edges[span]["ends"]}
    end_devices = {end["device"] for end in edges[span]["ends"]}
    def contacts(targets, role):
        return [row for target in sorted(targets) for row in assignments[target] if row["role"] == role]
    contact_rows = dict(customer=contacts(set(affected["premises"] + affected["hubs"] + affected["virtual_circuits"]), "contact-role/operations"),
                        operator=contacts({span} | end_devices, "contact-role/operations"),
                        carrier=contacts({span}, "contact-role/carrier"), facilities=contacts(end_sites, "contact-role/facilities"))
    _require(all(contact_rows.values()), "actual customer, operator, carrier and PoP facilities assignments are required")
    hashes = dict(baseline=digest(baseline), changed=digest(changed))
    checks = dict(baseline_valid=True, changed_expected_findings=True, one_circuit_status_changed=True,
                  other_objects_unchanged=True, allocations_unchanged=True, modeled_routes_reachable=True,
                  directed_capacity_sufficient=True, exact_inverse_restoration=True, unavailable_spans=1,
                  checked_further_failure_margin_retained=not resilience,
                  validation_findings=len(expected), plan_sha256=hashes)
    inverse = _changes(changed, restored)
    restoration = dict(baseline_sha256=hashes["baseline"], inverse={key: inverse[key] for key in ("create", "update", "delete")},
        offline="Inverse status update restores the exact frozen baseline, including every physical connection and installed module.",
        live="Same-ID Diode status update and restoration remain unqualified until baseline, offline, repeat, active and repeat readback pass on a pinned target.")
    paths = dict(baseline=before, changed=after)
    capacity = dict(scope=SCOPE, reserve_fraction=baseline["recipe"]["reserve_fraction"], directions=directions)
    answers = [dict(id="rerouted-premises", question="Which customer premises take a different modeled path?", answer=affected, evidence=affected["premises"] + [span]),
               dict(id="directed-headroom", question="Do the remaining directions carry the declared spoke-to-hub peak?",
                    answer="Yes, with the exact directed headroom in capacity.directions; excluded traffic remains unknown.", evidence=sorted({row["edge"] for row in capacity["directions"]})),
               dict(id="further-failure-margin", question="What happens to checked further-failure protection?",
                    answer=("Listed additional failures reduce connectivity or declared flow margin." if resilience else
                            "Every checked further failure retains the required connectivity and declared flow capacity.") +
                           " The maintained span and its local optical handoffs remain unavailable; current modeled traffic remains reachable.", evidence=resilience),
               dict(id="restoration-boundary", question="What restoration has been proved?", answer=restoration, evidence=[span])]
    return dict(schema_version=1, scenario=SCENARIO, selection=dict(span_filter=span_filter, span=span), subject=span,
                plans=dict(baseline=baseline, changed=changed), changes=diff, expected_findings=expected,
                edges=edges, premises=premises, paths=paths, affected=affected, contacts=contact_rows, capacity=capacity,
                resilience=resilience, answers=answers, checks=checks, restoration=restoration,
                execution=deepcopy(EXECUTION), limitations=list(LIMITATIONS))


def create(plan, span=None):
    """Select one used span; retain all installed identities and inverse evidence."""
    try:
        return _derive(deepcopy(plan), None, span, None)
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError, StopIteration) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"Span scenario: malformed baseline or unsupported graph ({type(exc).__name__})") from None


def verify(envelope):
    """Recompute all evidence; claimed findings cannot authorize arbitrary defects."""
    try:
        _require(isinstance(envelope, dict) and type(envelope.get("schema_version")) is int and envelope["schema_version"] == 1 and
                 envelope.get("scenario") == SCENARIO, "unsupported scenario envelope")
        plans = envelope["plans"]
        _require(set(plans) == {"baseline", "changed"} and isinstance(plans["changed"], dict) and isinstance(envelope["subject"], str),
                 "scenario requires baseline and changed snapshots and a pinned subject")
        expected = _derive(plans["baseline"], plans["changed"], envelope["selection"]["span_filter"], envelope["subject"])
        _require(set(envelope) - {"plan_files"} == set(expected), "scenario envelope has missing or unsupported claims")
        for key in expected:
            _require(canonical(envelope[key]) == canonical(expected[key]), f"{key} does not match independently derived scenario evidence")
        return deepcopy(expected["checks"])
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError, StopIteration) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"Span scenario: malformed scenario envelope ({type(exc).__name__})") from None


def markdown(envelope):
    """Keep the operator walkthrough bounded; complete rows remain in JSON."""
    verify(envelope)
    objects = {obj["key"]: obj for obj in envelope["plans"]["baseline"]["objects"]}
    def name(key):
        attrs = objects.get(key, {}).get("attrs", {})
        return attrs.get("name", attrs.get("cid", key))
    subject = envelope["subject"]
    protection = ("Further-failure protection is reduced." if envelope["resilience"] else
                  "Further-failure protection is retained for every checked removal in this finite model.")
    lines = ["# Provider span maintenance", "", f"Take **{_cell(name(subject))}** (`{subject}`) out of service: active → offline.", "",
        f"**{len(envelope['affected']['premises'])} customer premises reroute in the declared model.** All modeled spoke-to-hub paths remain reachable within directed usable capacity. {protection}", "",
        "Cables, interfaces and installed optical power remain in place. This is planned maintenance, not executed failover or a completed change.", "", "## Maintained handoffs", ""]
    _table(lines, ["Side / site", "PE", "Interface", "Termination / local cable"],
           [[f"{end['side']}: {name(end['site'])}", name(end["device"]), end["interface"],
             f"{end['termination']} / {end['cable']}"] for end in envelope["edges"][subject]["ends"]])
    lines.extend(["", "## Follow customer paths", ""])
    affected = set(envelope["affected"]["premises"][:12])
    path_rows = {stage: {row["site"]: row for row in rows} for stage, rows in envelope["paths"].items()}
    for site in sorted(affected):
        for stage in ("baseline", "changed"):
            row = path_rows[stage][site]
            sequence = [name(row["from_pe"])]
            for hop in row["hops"]:
                sequence.extend([f"[{name(hop['edge'])}]", name(hop["to_pe"])])
            lines.append(f"- **{_cell(name(row['site']))} → {_cell(name(row['hub']))}**, {stage}, {row['offered_kbps']/1000:g} Mbps: {' → '.join(_cell(item) for item in sequence)}")
    lines.extend(["", f"Showing {len(affected)} of {len(envelope['affected']['premises'])} rerouted premises; all premise/CE/service/handoff witnesses and paths are in scenario.json.", "", "## Directed headroom", "", SCOPE, ""])
    sharing = envelope["affected"]["unchanged_premises_sharing_increased_load"]
    lines.extend([f"{len(sharing)} unchanged premise routes share an increased-load direction. Examples ({min(3,len(sharing))} shown): " +
                  (", ".join(_cell(name(key)) for key in sharing[:3]) or "none") + ".", ""])
    directions = sorted(envelope["capacity"]["directions"], key=lambda row: (Decimal(row["headroom_kbps"]), row["edge"], row["from_pe"], row["to_pe"]))
    _table(lines, ["Link / direction", "State", "Before Mbps", "Maintenance Mbps", "Available Mbps", "Headroom Mbps"],
           [[f"{name(row['edge'])}: {name(row['from_pe'])} → {name(row['to_pe'])}", row["maintenance_state"],
             row["baseline_kbps"]/1000, row["maintenance_kbps"]/1000, Decimal(row["maintenance_available_kbps"])/1000,
             Decimal(row["headroom_kbps"])/1000] for row in directions[:12]])
    lines.extend(["", f"Showing {min(12,len(directions))} of {len(directions)} used or newly unused directions, smallest headroom first. Complete actual limits and endpoint witnesses are in scenario.json.", "", "## Responsible contacts", ""])
    contacts = []
    for scope, rows in envelope["contacts"].items():
        distinct = {}
        for row in rows:
            distinct.setdefault(row["contact"], row)
        samples = list(distinct.values())
        selected = {row["assignment"] for row in samples}
        samples.extend(row for row in rows if row["assignment"] not in selected)
        contacts.extend((scope, row) for row in samples[:3])
    _table(lines, ["Responsibility", "Actual assignment target", "Contact"],
           [[scope, name(row["object"]), name(row["contact"])] for scope, row in contacts])
    total_contacts = sum(len(rows) for rows in envelope["contacts"].values())
    lines.extend(["", f"Showing {len(contacts)} of {total_contacts} actual assignments (at most three per responsibility); complete assignments are in scenario.json.", "", "## Further-failure protection", ""])
    counts = Counter(row["code"] for row in envelope["expected_findings"])
    _table(lines, ["Exact finding code", "Count"], sorted(counts.items()))
    lines.extend(["", "The two local optical handoffs and maintained Circuit remain installed but unavailable. " + protection +
        " Any additional-failure findings describe one further removal during maintenance, not present loss of modeled reachability. The complete code/object multiset and additional-failure witnesses are in scenario.json.", "",
        "Ordinary validation must reject this state; scenario-check requires exactly the independently derived findings, without ignored errors.", "", "## Restore", "", envelope["restoration"]["offline"], "", envelope["restoration"]["live"], "",
        "Fresh-target snapshots and live status transitions require separate qualification; this report does not mark either as applied.", "", "## Boundaries", ""])
    lines.extend("- " + value for value in LIMITATIONS)
    return "\n".join(lines) + "\n"
