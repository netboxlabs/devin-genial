"""Deterministic acquisition/refresh snapshots and graph-derived demo answers.

This describes changes; it never submits them. Removing a canonical record is
not a Diode deletion instruction, and changing tenant can change target identity.
"""

from collections import defaultdict
from copy import deepcopy
from ipaddress import ip_interface, ip_network

from .bank import generate
from .model import DesignError, digest
from .validate import validate


def _index(plan):
    return {obj["key"]: obj for obj in plan["objects"]}


def _physical(plan, site_key):
    """Trace actual cables and passive mappings, without traversing switches."""
    objects = _index(plan)
    by_device = defaultdict(list)
    cables, mappings = {}, {}
    for key, obj in objects.items():
        refs = obj["refs"]
        if "device" in refs:
            by_device[refs["device"]].append(key)
        if obj["kind"] == "cable":
            a, b = refs["a"], refs["b"]
            cables[a], cables[b] = (b, key), (a, key)
        elif obj["kind"] == "front_port":
            rear = refs["rear_port"]
            mappings[key], mappings[rear] = rear, key

    def trace(start):
        visited, path = set(), []
        current = start
        while current not in visited and current in cables:
            visited.add(current)
            current, cable = cables[current]
            path.append(cable)
            if objects[current]["kind"] not in {"front_port", "rear_port"}:
                return current, path
            current = mappings.get(current)
        return None, path

    devices = {key: obj for key, obj in objects.items()
               if obj["kind"] == "device" and obj["refs"].get("site") == site_key}
    access = {key: obj for key, obj in devices.items() if obj["refs"].get("role") == "role/access"}
    endpoints = {key: obj for key, obj in devices.items() if obj["meta"].get("endpoint")}
    attachments = {}
    addresses = defaultdict(list)
    for key, obj in objects.items():
        if obj["kind"] == "ip_address":
            owner = objects.get(obj["refs"].get("assigned_object"), {}).get("refs", {}).get("device")
            if owner in endpoints:
                addresses[owner].append({"key": key, "address": obj["attrs"]["address"],
                                         "dns_name": obj["attrs"].get("dns_name"),
                                         "interface": obj["refs"]["assigned_object"],
                                         "vrf": obj["refs"].get("vrf")})
    for endpoint in sorted(endpoints):
        attached = []
        for port in sorted(by_device[endpoint]):
            if objects[port]["kind"] != "interface":
                continue
            peer, path = trace(port)
            target = objects.get(peer, {})
            if target.get("kind") == "interface" and target["refs"].get("device") in access:
                attached.append({"access_device": target["refs"]["device"], "access_interface": peer,
                                 "endpoint_interface": port, "cables": path})
        attachments[endpoint] = attached
    inventory = {}
    for key, obj in sorted(access.items()):
        uplinks, peer_trunks, supply_paths = [], [], []
        for port in sorted(by_device[key]):
            kind = objects[port]["kind"]
            peer, path = trace(port)
            target = objects.get(peer, {})
            if kind == "interface" and target.get("kind") == "interface":
                parent = target["refs"].get("device")
                if objects.get(parent, {}).get("refs", {}).get("role") in {"role/distribution", "role/wan-edge"}:
                    uplinks.append({"interface": port, "upstream": parent,
                                    "upstream_interface": peer, "cables": path})
                elif parent in access:
                    peer_trunks.append({"interface": port, "access_peer": parent,
                                        "peer_interface": peer, "cables": path})
            elif kind == "power_port" and target.get("kind") == "power_outlet":
                inlet = target["refs"].get("power_port")
                feed, upstream_path = trace(inlet)
                if objects.get(feed, {}).get("kind") == "power_feed":
                    supply_paths.append({"power_port": port, "outlet": peer,
                                         "pdu": target["refs"]["device"], "feed": feed,
                                         "panel": objects[feed]["refs"]["power_panel"],
                                         "cables": path + upstream_path})
        device_type = objects[obj["refs"]["device_type"]]
        manufacturer = objects[device_type["refs"]["manufacturer"]]
        inventory[key] = {"name": obj["attrs"]["name"], "model": device_type["attrs"]["model"],
                          "manufacturer": manufacturer["attrs"]["name"], "uplinks": uplinks, "peer_trunks": peer_trunks,
                          "power_ports": sorted(p for p in by_device[key] if objects[p]["kind"] == "power_port"),
                          "supply_paths": supply_paths,
                          "affected_endpoints": sorted(endpoint for endpoint, links in attachments.items()
                                                       if any(link["access_device"] == key for link in links))}
    return {"access": inventory, "attachments": attachments,
            "endpoint_names": {key: obj["attrs"]["name"] for key, obj in sorted(endpoints.items())},
            "endpoint_addresses": {key: sorted(addresses[key], key=lambda value: value["key"])
                                   for key in sorted(endpoints)}}


def _scopes(objects):
    """Prove site ownership; empty scopes leave shared definitions protected.

    VRFs have no containment field. Infer their ownership from all actual VRF
    consumers, only when every consumer is itself scoped. A global root prefix
    therefore keeps its VRF global even in an estate containing just one site.
    """
    cache = {}
    terms = defaultdict(list)
    prefixes = {(obj["refs"].get("vrf"), ip_network(obj["attrs"]["prefix"])): key
                for key, obj in objects.items() if obj["kind"] == "prefix"}
    for key, obj in objects.items():
        if obj["kind"] == "circuit_termination":
            terms[obj["refs"]["circuit"]].append(obj["refs"].get("termination"))
        elif obj["kind"] == "virtual_circuit_termination":
            terms[obj["refs"]["virtual_circuit"]].append(obj["refs"].get("interface"))
        elif obj["kind"] == "ip_range":
            start = ip_interface(obj["attrs"]["start_address"]).ip
            end = ip_interface(obj["attrs"]["end_address"]).ip
            subnet = ip_network((start, start.max_prefixlen))
            while True:
                prefix = prefixes.get((obj["refs"].get("vrf"), subnet))
                if prefix and end in subnet:
                    terms[key].append(prefix)
                    break
                if subnet.prefixlen == 0:
                    break
                subnet = subnet.supernet()

    def sites(key, visiting=frozenset()):
        if key in cache:
            return cache[key]
        if key not in objects or key in visiting:
            return frozenset()
        obj = objects[key]
        if obj["kind"] == "site":
            result = frozenset([key])
        else:
            fields = ("site", "scope_site", "location", "rack", "device", "virtual_machine",
                      "power_panel", "assigned_object", "termination", "cluster", "group",
                      "interface", "interface_a", "interface_b", "module_bay", "cooling_source")
            targets = [obj["refs"][field] for field in fields if isinstance(obj["refs"].get(field), str)]
            if obj["kind"] == "contact_assignment" and isinstance(obj["refs"].get("object"), str):
                targets.append(obj["refs"]["object"])
            if obj["kind"] == "cable":
                targets.extend([obj["refs"]["a"], obj["refs"]["b"]])
            targets.extend(terms.get(key, []))
            result = frozenset().union(*(sites(target, visiting | {key}) for target in targets))
        cache[key] = result
        return result

    scopes = {key: sites(key) for key in objects}
    vrf_consumers = defaultdict(list)
    for key, obj in objects.items():
        if "vrf" in obj["refs"]:
            vrf_consumers[obj["refs"]["vrf"]].append(scopes[key])
    for key, consumers in vrf_consumers.items():
        if all(consumers):
            scopes[key] = frozenset().union(*consumers)
    return scopes


def _changes(before, after):
    old, new = _index(before), _index(after)
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    deleted = [old[key] for key in sorted(old.keys() - new.keys())]
    changed = [{"key": key, "kind": new[key]["kind"], "before": old[key], "after": new[key]}
               for key in sorted(old.keys() & new.keys()) if old[key] != new[key]]
    tenants = [{"key": change["key"], "before": change["before"]["refs"].get("tenant"),
                "after": change["after"]["refs"].get("tenant"),
                "can_change_natural_identity": change["kind"] in {"device", "virtual_machine", "vrf"}}
               for change in changed
               if change["before"]["refs"].get("tenant") != change["after"]["refs"].get("tenant")]
    return {"create": added, "update": changed, "delete": deleted,
            "counts": {"create": len(added), "update": len(changed), "delete": len(deleted)},
            "tenant_changes": tenants, "requires_target_reconciliation": bool(deleted or tenants),
            "replay_deletes_objects": False}


def _require(condition, check, message):
    if not condition:
        raise DesignError(f"Scenario check {check} failed: {message}")


def create(plan, site_id):
    """Return before/acquired/refreshed snapshots, reviewed diffs, and answers."""
    site_key = f"site/{site_id}"
    target = _index(plan).get(site_key)
    if not target or target["kind"] != "site":
        raise DesignError(f"Scenario site {site_id!r} is absent from the plan")
    if target["meta"].get("branch_design") != "inherited" or target["meta"].get("acquisition_state") != "independent":
        raise DesignError("Acquisition/refresh requires an inherited, independent branch; choose an eligible site")
    before = deepcopy(plan)
    findings = validate(before)
    if findings:
        raise DesignError(f"Scenario before plan validation failed: {findings[:3]}")
    recipe = deepcopy(before["recipe"])
    recipe["acquired_sites"] = sorted(set(recipe["acquired_sites"]) | {site_id})
    acquired = generate(recipe, previous=before, transition={"site": site_id, "operation": "acquire"})
    recipe = deepcopy(acquired["recipe"])
    recipe["site_designs"][site_id] = "refreshed"
    refreshed = generate(recipe, previous=acquired, transition={"site": site_id, "operation": "refresh"})
    plans = {"before": before, "acquired": acquired, "refreshed": refreshed}
    snapshots = {}
    for stage, snapshot in plans.items():
        if stage != "before":
            findings = validate(snapshot)
            if findings:
                raise DesignError(f"Scenario {stage} plan validation failed: {findings[:3]}")
        snapshots[stage] = _physical(snapshot, site_key)
        _require(snapshots[stage]["access"], "access-inventory", f"{stage} has no access devices")
        _require(snapshots[stage]["attachments"] and
                 all(len(links) == 1 for links in snapshots[stage]["attachments"].values()),
                 "endpoint-connectivity", f"{stage} endpoints must each trace to exactly one access switch")
        _require(all(snapshots[stage]["endpoint_addresses"].values()),
                 "endpoint-addressing", f"{stage} has an endpoint without an assigned IP")
        state = _index(snapshot)[site_key]["meta"]
        expected = {"before": ("inherited", "independent"), "acquired": ("inherited", "acquired"),
                    "refreshed": ("refreshed", "acquired")}[stage]
        _require((state.get("branch_design"), state.get("acquisition_state")) == expected and
                 state.get("lineage") == target["meta"].get("lineage"),
                 "lifecycle-transition", f"{stage} has unexpected design, acquisition state, or lineage")
    for stage in ("acquired", "refreshed"):
        _require(snapshots[stage]["endpoint_names"] == snapshots["before"]["endpoint_names"],
                 "endpoint-name-stability", f"{stage} changes endpoint identities or names")
        _require(snapshots[stage]["endpoint_addresses"] == snapshots["before"]["endpoint_addresses"],
                 "endpoint-address-stability", f"{stage} changes endpoint IPs, DNS, VRFs, or interface assignments")
    _require(snapshots["before"]["access"] == snapshots["acquired"]["access"],
             "acquisition-preserves-hardware", "Acquisition changes access equipment or physical connectivity")
    _require(set(snapshots["before"]["access"]).isdisjoint(snapshots["refreshed"]["access"]),
             "physical-replacement", "Refresh must create new access device identities")
    for key, inventory in snapshots["refreshed"]["access"].items():
        _require(len({link["upstream"] for link in inventory["uplinks"]}) >= 2,
                 "refresh-uplinks", f"{key} lacks two distinct direct upstream neighbors")
        _require(len(inventory["power_ports"]) >= 2 and
                 len(inventory["supply_paths"]) == len(inventory["power_ports"]) and
                 len({path["panel"] for path in inventory["supply_paths"]}) >= 2,
                 "refresh-supplies", f"{key} lacks complete feeds through two distinct supply panels")
    original = _index(before)
    original_scopes = _scopes(original)
    foreign = {key for key, scopes in original_scopes.items() if scopes - {site_key}}
    for stage in ("acquired", "refreshed"):
        current = _index(plans[stage])
        current_scopes = _scopes(current)
        forbidden = [key for key in sorted(original.keys() | current.keys())
                     if original.get(key) != current.get(key) and
                     any(key in objects and scopes[key] != {site_key}
                         for objects, scopes in ((original, original_scopes), (current, current_scopes)))]
        _require(not forbidden, "unrelated-sites-unchanged",
                 f"{stage} changes shared, unscoped, or other-site objects: {forbidden[:5]}")
    changes = {"acquire": _changes(before, acquired), "refresh": _changes(acquired, refreshed)}
    _require(changes["acquire"]["tenant_changes"], "acquisition-ownership", "Acquisition changes no tenant references")
    _require(set(snapshots["before"]["access"]) <= {obj["key"] for obj in changes["refresh"]["delete"]},
             "retired-hardware", "Old physical access devices remain in the refreshed snapshot")
    answers = [
        {"id": "inherited-equipment", "question": "Which access equipment is inherited, and how is it connected?",
         "answer": snapshots["before"]["access"], "evidence": sorted(snapshots["before"]["access"])},
        {"id": "replacement-impact", "question": "Which endpoints depend on each access switch being replaced?",
         "answer": {key: value["affected_endpoints"] for key, value in snapshots["before"]["access"].items()},
         "evidence": snapshots["before"]["attachments"]},
        {"id": "ownership-change", "question": "What ownership changes during acquisition?",
         "answer": changes["acquire"]["tenant_changes"], "evidence": [site_key]},
        {"id": "preserved-addressing", "question": "Which endpoint names and addresses survive acquisition and refresh?",
         "answer": {"names": snapshots["before"]["endpoint_names"],
                    "addresses": snapshots["before"]["endpoint_addresses"]},
         "evidence": sorted(snapshots["before"]["endpoint_names"])},
        {"id": "refreshed-resilience", "question": "What physical uplink and power diversity exists after refresh?",
         "answer": snapshots["refreshed"]["access"], "evidence": sorted(snapshots["refreshed"]["access"])},
    ]
    return {"schema_version": 1, "scenario": "acquire-and-refresh", "site": site_id,
            "site_name": target["attrs"]["name"], "plans": plans, "changes": changes, "answers": answers,
            "checks": {"all_plans_valid": True, "all_endpoints_connected": True,
                       "endpoint_names_and_addresses_preserved": True, "acquisition_preserves_hardware": True,
                       "new_physical_access_devices": True, "refresh_has_two_upstream_neighbors": True,
                       "refresh_has_two_supply_panels": True, "unrelated_sites_unchanged": True,
                       "only_selected_site_changes": True,
                       "foreign_objects_checked": len(foreign),
                       "endpoint_count": len(snapshots["before"]["endpoint_names"]),
                       "plan_sha256": {stage: digest(snapshot) for stage, snapshot in plans.items()}},
            "limitations": [
                "These are complete synthetic snapshots and proposed changes, not executed lifecycle history.",
                "Tenant changes can change natural matching identity; review target reconciliation before applying transitions.",
                "Deletes describe removed canonical records. Diode replay does not delete those target objects.",
                "Loading the refreshed snapshot into an existing tenant is not a verified migration or rollback procedure.",
                "Physical connectivity and separate modeled panels do not prove live failover, facility independence, or application availability.",
                "Small branches also have an inter-access VLAN trunk: inherited switches have one direct edge attachment and an indirect path through the peer switch. Trunk presence does not establish live convergence.",
            ]}


def markdown(scenario):
    """Render a short walkthrough; complete impacted-object evidence stays in JSON."""
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    answers = {answer["id"]: answer["answer"] for answer in scenario["answers"]}
    lines = [f"# Acquire and refresh {cell(scenario['site_name'])}", "",
             "Three complete candidate snapshots: inherited and independent, acquired, then refreshed. "
             "All counts and affected endpoints below come from the generated graph.", "",
             "## 1. Inspect the inherited branch", "",
             "Trace an endpoint to its access switch, then inspect that switch's direct upstream uplinks and power feeds.", "",
             "| Access device | Model | Direct upstream neighbors | Supply panels | Affected endpoints |",
             "| --- | --- | --- | --- | --- |"]
    for inventory in answers["inherited-equipment"].values():
        lines.append("| " + " | ".join(cell(value) for value in (
            inventory["name"], inventory["model"], len({link["upstream"] for link in inventory["uplinks"]}),
            len({path["panel"] for path in inventory["supply_paths"]}), len(inventory["affected_endpoints"]))) + " |")
    lines.extend(["", "## 2. Review the acquisition", "",
                  f"The acquisition changes {len(scenario['changes']['acquire']['tenant_changes'])} tenant references. "
                  "Access hardware, physical attachments, endpoint names, IP addresses, and DNS names remain stable. "
                  "Review the explicit ownership diff before reconciling an existing target; matching identity can change.", "",
                  "## 3. Review the refresh", "",
                  "Replace the inherited physical access devices and reconnect the same endpoints. "
                  "The new devices have new identities; the original devices are explicitly removed from the candidate snapshot.", "",
                  "| Operation | Create | Update | Delete from candidate |",
                  "| --- | --- | --- | --- |"])
    for operation, change in scenario["changes"].items():
        counts = change["counts"]
        lines.append(f"| {operation} | {counts['create']} | {counts['update']} | {counts['delete']} |")
    lines.extend(["", "| Replacement | Model | Direct upstream neighbors | Supply panels | Endpoints |",
                  "| --- | --- | --- | --- | --- |"])
    for inventory in answers["refreshed-resilience"].values():
        lines.append("| " + " | ".join(cell(value) for value in (
            inventory["name"], inventory["model"], len({link["upstream"] for link in inventory["uplinks"]}),
            len({path["panel"] for path in inventory["supply_paths"]}), len(inventory["affected_endpoints"]))) + " |")
    lines.extend(["", "## Verify the expected answers", "",
                  f"All {scenario['checks']['endpoint_count']} endpoints retain their names and addressing and trace to an access switch. "
                  f"{scenario['checks']['foreign_objects_checked']} objects scoped to other sites are unchanged. "
                  "Shared and unscoped records are unchanged; every candidate change belongs exclusively to this branch. "
                  "The machine-readable answers include every affected endpoint and the exact cable, interface, feed, and panel keys.", "",
                  "## Application boundaries", ""])
    lines.extend(f"- {limitation}" for limitation in scenario["limitations"])
    return "\n".join(lines) + "\n"
