"""Readable reports derived from the finished graph, without loading an instance."""

from collections import Counter, defaultdict
from decimal import Decimal
from html import escape
import ipaddress
import json
from pathlib import Path

from .model import digest
from .intent import compatibility_guidance
from .validate import _speed


def type_coverage(plan):
    """Inventory against the pinned schema audit; generated never means live-verified."""
    audit = json.loads((Path(__file__).resolve().parent.parent / "catalog/type-coverage.json").read_text())
    kinds = defaultdict(list)
    for obj in plan["objects"]:
        kinds[obj["kind"]].append(obj["key"])
    rows = []
    for item in audit["types"]:
        keys = sorted(kinds[item["kind"]])
        rows.append({**item, "count": len(keys), "example": keys[0] if keys else None,
                     "state": "external-match" if item["kind"] == "user" and keys else
                              "generated" if keys else "absent"})
    return {"versions": audit["versions"], "sources": audit["sources"], "types": rows,
            "candidate_types": sum(row["category"] == "estate_candidate" for row in rows),
            "generated_candidate_types": sum(row["category"] == "estate_candidate" and bool(row["count"]) for row in rows),
            "missing_candidate_types": [row["kind"] for row in rows if row["category"] == "estate_candidate" and not row["count"]],
            "native_without_sdk": audit["native_without_sdk"], "live_verified": False}


def summary(plan):
    """Return the compact CLI inventory and digest of the exact input plan."""
    return {
        "name": plan["recipe"]["name"],
        "objects": len(plan["objects"]),
        "counts": dict(sorted(Counter(o["kind"] for o in plan["objects"]).items())),
        "sha256": digest(plan),
        "generator_version": plan["generator_version"],
    }


def _cell(value):
    return escape(str(value), quote=False).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def _table(lines, headers, rows):
    lines.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
    lines.extend("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)
    lines.append("")


def _rate(value):
    return format(Decimal(str(value)).normalize(), "f")


def _ipv6_walkthrough(plan, objects, kinds):
    """Show bounded examples from actual primary and listener relationships."""
    if not plan["recipe"].get("ipv6_pool"):
        return []
    addresses = {obj["key"]: obj for obj in kinds["ip_address"]}
    families = {key: ipaddress.ip_interface(obj["attrs"]["address"]).version
                for key, obj in addresses.items()}
    prefixes = Counter(ipaddress.ip_network(obj["attrs"]["prefix"]).prefixlen
                       for obj in kinds["prefix"]
                       if ipaddress.ip_network(obj["attrs"]["prefix"]).version == 6)
    primaries = {kind: [obj for obj in kinds[kind]
                        if families.get(obj["refs"].get("primary_ip6")) == 6]
                 for kind in ("device", "virtual_machine")}
    services = [obj for obj in kinds["service"]
                if {families.get(key) for key in obj["refs"].get("ipaddresses", [])} >= {4, 6}]

    def label(key):
        obj = objects.get(key, {})
        name = obj.get("attrs", {}).get("name")
        return f"{name} [{key}]" if name else key or "Unspecified"

    def address(key):
        return addresses.get(key, {}).get("attrs", {}).get("address", "Missing")

    lines = ["## Dual-stack IP inventory", "",
             f"Configured IPv6 pool: `{_cell(plan['recipe']['ipv6_pool'])}`. The graph contains "
             f"{sum(prefixes.values()):,} IPv6 prefixes and {sum(family == 6 for family in families.values()):,} IPv6 addresses; "
             f"{len(primaries['device']):,} devices and {len(primaries['virtual_machine']):,} VMs have primary IPv6 references. "
             f"{len(services):,} service objects list both address families.", "",
             "IPv6 prefix lengths present: " + (", ".join(f"/{length}: {count:,}" for length, count in sorted(prefixes.items())) or "none") + ".", "",
             "Facility reservations use /48s, LANs /64s, routed router links /127s and PE loopbacks /128s. "
             + ("The bank radio diagnostic uses a /64; its FHRP VIPs remain IPv4-only. "
                if plan["recipe"].get("profile") == "regional-bank" else "")
             + "An unknown transit far end stays unknown. This is intended address/listener inventory; "
             "no routing, RA/DHCPv6, application binding or live ingestion is established by this report.", ""]
    rows = []
    for kind, entries in primaries.items():
        if entries:
            # Prefer the actual PE loopback when present; otherwise a stable primary.
            obj = min(entries, key=lambda item: (
                objects.get(addresses[item["refs"]["primary_ip6"]]["refs"].get("assigned_object"), {}).get("attrs", {}).get("name") != "lo0",
                item["key"]))
            ip6 = addresses[obj["refs"]["primary_ip6"]]
            rows.append((kind, label(obj["key"]), label(ip6["refs"].get("assigned_object")),
                         address(obj["refs"].get("primary_ip4")), ip6["attrs"]["address"],
                         label(ip6["refs"].get("vrf"))))
    if rows:
        _table(lines, ["Family", "Primary example", "IPv6 interface", "Primary IPv4", "Primary IPv6", "VRF"], rows)
    if services:
        rows = []
        for obj in sorted(services, key=lambda item: item["key"])[:2]:
            refs, attrs = obj["refs"], obj["attrs"]
            examples = [address(next(key for key in refs["ipaddresses"] if families.get(key) == family))
                        for family in (4, 6)]
            rows.append((label(obj["key"]), label(refs.get("device") or refs.get("virtual_machine")),
                         f"{attrs.get('protocol', '?')}/{','.join(map(str, attrs.get('ports', [])))}", *examples))
        _table(lines, ["Service example", "Owner", "Listener", "IPv4 address", "IPv6 address"], rows)
    return lines


def _operations_walkthrough(objects, kinds):
    """Bound navigation by family/responsibility so volume cannot bury specialists."""
    assignments = defaultdict(list)
    journals = defaultdict(list)
    for item in kinds["contact_assignment"]:
        target = objects.get(item["refs"].get("object"), {})
        assignments[(target.get("kind", "unknown"), item["refs"].get("role", ""))].append(item)
    for item in kinds["journal_entry"]:
        target = objects.get(item["refs"].get("assigned_object"), {})
        journals[target.get("kind", "unknown")].append(item)
    if not assignments and not journals:
        return []

    def label(key):
        obj = objects.get(key, {})
        name = obj.get("attrs", {}).get("name") or obj.get("attrs", {}).get("cid") or key
        # Canonical keys retain scope when two sites use the same display name.
        return f"{name} [{key}]" if name != key else key

    lines = ["## Contacts and journal walkthrough", "",
             f"The graph contains {len(kinds['contact'])} contacts, "
             f"{len(kinds['contact_assignment'])} assignments and {len(kinds['journal_entry'])} journal entries. "
             "These examples show up to three assignments per object family/responsibility and three distinct journal event types per family; "
             "the complete records remain in `plan.json` and the Diode package.", "",
             "Contacts and dated entries are generated planning context. NetBox's journal creation time is ingestion time; "
             "dates in the entry describe the modeled history. They do not establish an executed change or completed test.", ""]
    if assignments:
        rows = []
        for (family, _role), entries in sorted(assignments.items()):
            for entry in sorted(entries, key=lambda obj: obj["key"])[:3]:
                refs = entry["refs"]
                contact = objects.get(refs.get("contact"), {})
                rows.append((family, label(refs.get("object")), label(refs.get("role")),
                             entry["attrs"].get("priority", "unspecified"),
                             contact.get("attrs", {}).get("name", refs.get("contact")),
                             contact.get("attrs", {}).get("email", "No mailbox recorded")))
        _table(lines, ["Family", "Open object", "Responsibility", "Priority", "Contact", "Mailbox"], rows)
    if journals:
        rows = []
        for family, entries in sorted(journals.items()):
            events = {}
            for entry in sorted(entries, key=lambda obj: obj["key"]):
                events.setdefault(entry["key"].rsplit("/", 1)[-1], entry)
            for entry in list(events.values())[:3]:
                rows.append((family, label(entry["refs"].get("assigned_object")),
                             entry["attrs"].get("kind", "info"), entry["attrs"].get("comments", "")))
        _table(lines, ["Family", "Open journal on", "Entry kind", "Recorded context"], rows)
    return lines


def _wireless_walkthrough(plan, objects, kinds):
    """Bounded navigation examples from actual wired APs and WLAN dependencies."""
    aps = [o for o in kinds["device"] if o["refs"].get("role") == "role/ap"]
    if not aps:
        return []
    from .networking import _physical_peers

    peers = _physical_peers(objects)
    models = json.loads((Path(__file__).resolve().parent.parent / "catalog/hardware.json").read_text())["models"]
    loads = Counter()
    for ap in aps:
        alias = ap["refs"].get("device_type", "").removeprefix("hardware/")
        pd = models.get(alias, {}).get("poe_pd", {})
        source = objects.get(peers.get(f"{ap['key']}/if/{pd.get('interface', 'eth0')}"), {})
        loads[source.get("refs", {}).get("device")] += pd.get("pse_reservation_mw", 0)

    def name(key):
        return objects.get(key, {}).get("attrs", {}).get("name", key or "Unresolved")

    lines = ["## Wireless demand, power and support", "",
             f"{len(aps)} installed APs and {len(kinds['wireless_lan'])} WLANs. "
             "AP power follows the actual copper path below. Switch reservations include every attached PD; "
             "the upstream AC planning allowance is included in the normal inlet totals. "
             "These are design reservations, not measured consumption or RF capacity.", ""]
    inlets = defaultdict(list)
    for port in kinds["power_port"]:
        inlets[port["refs"].get("device")].append(port)
    rows = []
    for ap in sorted(aps, key=lambda o: o["key"])[:8]:
        port = objects.get(peers.get(f"{ap['key']}/if/eth0"), {})
        pse = port.get("refs", {}).get("device")
        normal = sum(p["attrs"].get("allocated_draw", 0) for p in inlets[pse])
        rows.append((name(ap["refs"]["site"]), name(ap["key"]), name(pse),
                     port.get("attrs", {}).get("name", "Unresolved"), f"{loads[pse]/1000:g}", normal))
    _table(lines, ["Site", "Open AP eth0 cable trace", "Serving PSE", "Source port", "PSE reserved PD W", "PSE normal AC allowance W"], rows)
    if len(aps) > len(rows):
        lines += [f"Showing {len(rows)} of {len(aps)} APs; all records remain in the plan and Diode package.", ""]
    zones = []
    for family in ("schools", "hospitals", "clinics"):
        for facility in plan["recipe"].get(family, []):
            for zone, demand in facility.get("wireless", {}).items():
                zones.append((facility["key"], zone, demand["managed"], demand["guest"]))
    if zones:
        lines += [f"{len(zones)} local coverage zones plan {sum(z[2] for z in zones)} managed and "
                  f"{sum(z[3] for z in zones)} guest concurrent devices. Each zone uses a 32-device/AP "
                  "equipment planning threshold and retains its coverage anchors, with at most four mounts. "
                  "Local peaks are provisioned independently; no client association or synchronized traffic measurement is implied.", ""]
        _table(lines, ["Facility", "Example zone", "Managed devices", "Guest devices"], zones[:8])
        if len(zones) > 8:
            lines += [f"Showing 8 of {len(zones)} coverage zones; the recipe retains every zone's demand.", ""]
        lines += ["Managed demand sizes AP equipment in aggregate across managed WLANs; it is not allocated per SSID, "
                  "so no per-SSID managed address admission is checked. Guest demand maps to one explicit guest segment "
                  "per facility and is checked against its available IPv4 capacity.", ""]
    entries = [entry for contract in plan.get("contracts", []) for entry in contract.get("wireless_services", [])]
    if entries:
        rows = []
        for entry in sorted(entries, key=lambda e: e["wlan"])[:8]:
            wlan = objects.get(entry["wlan"], {})
            services = list(dict.fromkeys(entry.get("dns_tcp", []) + entry.get("dns_udp", []) + entry.get("radius_udp", [])))
            labels = []
            for service in services:
                obj = objects.get(service, {})
                labels.append(f"{name(obj.get('refs', {}).get('virtual_machine'))}: {obj.get('attrs', {}).get('name', service)}")
            external = entry.get("external_required", [])
            dependency = "; ".join(labels) if not external else "External, unspecified: " + ", ".join(external)
            rows.append((wlan.get("attrs", {}).get("ssid", entry["wlan"]), name(wlan.get("refs", {}).get("scope_site")),
                         objects.get(entry["prefix"], {}).get("attrs", {}).get("prefix", "Unresolved"),
                         entry["available_ipv4"], name(entry["technical_contact"]), dependency))
        _table(lines, ["Open WLAN", "Site", "Client prefix", "Unallocated IPv4 capacity", "Technical support", "Declared service inventory"], rows)
        if len(entries) > len(rows):
            lines += [f"Showing {len(rows)} of {len(entries)} WLAN dependency records; the plan retains the complete inventory.", ""]
        lines += ["WLAN dependencies describe required service inventory. Address headroom is not a DHCP pool or lease count; "
                  "routing, authentication and guest isolation must be configured separately. Open guests have no modeled portal.", ""]
    return lines


def _optics_walkthrough(objects, kinds, cable_peer, passive_peer, cable_at):
    """Inspect installed references and bounded local paths, not descriptive attributes."""
    ports = [p for p in kinds["interface"] if p["refs"].get("module")
             and p["attrs"].get("type") in {"1000base-x-sfp", "10gbase-x-sfpp", "100gbase-x-qsfp28"}]
    if not ports:
        return []
    catalog = json.loads((Path(__file__).resolve().parent.parent / "catalog/hardware.json").read_text())
    parts = {(p["manufacturer"], p["model"]): p for p in catalog["optics"]["parts"].values()}

    def label(key):
        obj = objects.get(key, {})
        return obj.get("attrs", {}).get("name", obj.get("attrs", {}).get("cid", key or "Unresolved"))

    def installed(port):
        module = objects.get(port.get("refs", {}).get("module"), {})
        dtype = objects.get(module.get("refs", {}).get("module_type"), {})
        identity = (label(dtype.get("refs", {}).get("manufacturer")), dtype.get("attrs", {}).get("model", "Unresolved"))
        return module, identity, parts.get(identity, {})

    counts, part_sources = Counter(), {}
    for port in ports:
        _, identity, part = installed(port)
        counts[identity] += 1
        part_sources[identity] = part
    aocs = [c for c in kinds["cable"] if c["attrs"].get("type") == "aoc"]
    lines = ["## Installed optics and local paths", "",
             f"{len(ports)} installed optical ends; {len(aocs)} AOC assemblies. "
             "Each AOC contributes two captive end records and one cable. Parts below are resolved from actual "
             "module-type manufacturer/model references and the reviewed catalog, not free-text module attributes.", ""]
    rows = []
    for identity, count in sorted(counts.items()):
        part = part_sources[identity]
        sources = []
        for key in part.get("source_ids", []):
            url = catalog["sources"][key]["url"]
            sources.append(f"[{key}]({url})" if url.startswith("https://") else f"{key}: {url}")
        rows.append((" ".join(identity), count, part.get("protocol", "Unreviewed"),
                     "captive end; whole assembly SKU" if part.get("assembly") else "independent transceiver",
                     "; ".join(sources) or "No reviewed catalog match"))
    _table(lines, ["Installed part", "Ends", "Protocol", "Inventory meaning", "Part/host evidence and assumptions"], rows)
    contacts = {a["refs"].get("object"): a["refs"].get("contact") for a in kinds["contact_assignment"]
                if a["refs"].get("role") == "contact-role/operations" and a["attrs"].get("priority") == "primary"}
    examples, seen = [], set()
    for port in sorted(ports, key=lambda p: p["key"]):
        module, identity, part = installed(port)
        current, visited, path, cables = port["key"], set(), [], []
        while current in cable_peer and current not in visited and len(visited) < 32:
            visited.add(current)
            cables.append(cable_at[current])
            peer = cable_peer[current]
            path.append(peer)
            if peer not in passive_peer:
                break
            current = passive_peer[peer]
            path.append(current)
        endpoint = objects.get(path[-1], {}) if path else {}
        shape = (identity, endpoint.get("kind"), len(cables) > 1)
        if shape in seen:
            continue
        seen.add(shape)
        device = port["refs"]["device"]
        end_name = (f"{label(endpoint['refs'].get('device'))} / {label(endpoint['key'])}"
                    if endpoint.get("kind") == "interface" else
                    f"{label(endpoint['refs'].get('circuit'))} / {endpoint['attrs'].get('term_side')} handoff; carrier far-end optic unknown"
                    if endpoint.get("kind") == "circuit_termination" else "Unresolved local endpoint")
        panels = list(dict.fromkeys(label(objects[key]["refs"].get("device")) for key in path
                                    if objects.get(key, {}).get("kind") in {"front_port", "rear_port"}))
        route = (" → ".join(panels) + " → " if panels else "") + end_name
        if part.get("assembly") and cables:
            route += f"; cable {cables[0]['attrs'].get('label', cables[0]['key'])}; assembly {module.get('attrs', {}).get('serial', 'Unresolved')}"
        meters = sum(Decimal(str(c["attrs"].get("length", 0))) for c in cables)
        length = f"{meters:g}m / {part.get('reach_m', 'unknown')}m selected part reach" if cables and all(
            c["attrs"].get("length_unit") == "m" for c in cables) else "Local length not available in meters"
        examples.append((f"{label(device)} / {label(port['key'])} [{port['key']}]",
                         f"{' '.join(identity)}; {label(module.get('refs', {}).get('module_bay'))}",
                         route, length, label(contacts.get(device))))
        if len(examples) == 10:
            break
    _table(lines, ["Open interface", "Installed part / bay", "Actual local cable path", "Local length / reach", "Device technical contact"], examples)
    journal_targets = [o["refs"]["assigned_object"] for o in sorted(kinds["journal_entry"], key=lambda o: o["key"])
                       if o["key"].endswith("/optic-replacement-plan")][:3]
    if journal_targets:
        lines += ["Optical preparation journals: " + "; ".join(f"{label(key)} [{key}]" for key in journal_targets) + ". "
                  "These fixed-cage notes are bounded to the rack equipment anchors.", ""]
    lines += ["These bounded examples follow cable and passive-port references only; they do not traverse a switch or carrier network. "
              "The authored local SMF envelope is 3–100m. Catalog reach does not establish an optical loss budget, measured receive power, "
              "FEC configuration or multivendor certification. Indexed-only and reference-host evidence retain their catalog limitations.", "",
              "The installed module owns its interface in NetBox: deleting that module can cascade to the interface. "
              "No module removal, hot-swap or executed replacement is modeled.", ""]
    return lines


def markdown(plan):
    """Render inventory, modeled capacity, placement, and graph-backed stories."""
    objects = {o["key"]: o for o in plan["objects"]}
    kinds, devices_by_site, racks_by_site, devices_by_rack = (defaultdict(list) for _ in range(4))
    devices_by_room, racks_by_room = defaultdict(list), defaultdict(list)
    device_rooms = {}
    interfaces_by_device = defaultdict(list)
    power_ports_by_device = Counter()
    rack_units, rack_watts, free_outlets = Counter(), Counter(), Counter()
    cable_peer, passive_peer, cable_at = {}, {}, {}
    contracts = {c["site"]: c for c in plan.get("contracts", [])}
    for obj in plan["objects"]:
        kind, key, refs = obj["kind"], obj["key"], obj["refs"]
        kinds[kind].append(obj)
        if kind == "device":
            devices_by_site[refs.get("site")].append(obj)
            room = refs.get("location") or objects.get(refs.get("rack"), {}).get("refs", {}).get("location")
            device_rooms[key] = room
            devices_by_room[room].append(obj)
            if refs.get("rack"):
                devices_by_rack[refs["rack"]].append(obj)
                if obj["attrs"].get("position") is not None:
                    rack_units[refs["rack"]] += objects.get(refs.get("device_type"), {}).get("attrs", {}).get("u_height", 0)
        elif kind == "rack":
            racks_by_site[refs.get("site")].append(obj)
            racks_by_room[refs.get("location")].append(obj)
        elif kind == "interface":
            interfaces_by_device[refs.get("device")].append(obj)
        elif kind == "power_port":
            power_ports_by_device[refs.get("device")] += 1
            rack = objects.get(refs.get("device"), {}).get("refs", {}).get("rack")
            rack_watts[rack] += obj["attrs"].get("allocated_draw", 0)
        elif kind == "cable":
            a, b = refs["a"], refs["b"]
            cable_peer[a], cable_peer[b] = b, a
            cable_at[a] = cable_at[b] = obj
        elif kind == "front_port":
            rear = refs.get("rear_port")
            if objects.get(rear, {}).get("attrs", {}).get("positions") == 1:
                passive_peer[key], passive_peer[rear] = rear, key

    def name(key):
        obj = objects.get(key, {})
        return obj.get("attrs", {}).get("name", key or "Unspecified")

    def short_names(keys, limit=6):
        keys = sorted(set(keys))
        text = ", ".join(name(k) for k in keys[:limit])
        return text + (f"; {len(keys)-limit} more" if len(keys) > limit else "")

    def floor_of(device):
        return objects.get(device_rooms.get(device["key"]), {}).get("meta", {}).get("floor", 0)

    # Every traversal follows one physical cable/passive path, never switching
    # or routing inside a device. This supports a bounded endpoint impact story.
    attached = defaultdict(set)
    endpoint_paths = {}
    for device in kinds["device"]:
        if not device["meta"].get("endpoint"):
            continue
        for interface in interfaces_by_device[device["key"]]:
            current, visited, cables, passive = interface["key"], set(), [], set()
            while current not in visited:
                visited.add(current)
                if current in cable_at:
                    cables.append(cable_at[current])
                current = cable_peer.get(current)
                peer = objects.get(current, {})
                if peer.get("kind") == "interface":
                    parent = peer["refs"].get("device")
                    if objects.get(parent, {}).get("meta", {}).get("purpose") == "access":
                        attached[parent].add(device["key"])
                        endpoint_paths[device["key"]] = (parent, cables, passive)
                    break
                if peer.get("kind") in {"front_port", "rear_port"}:
                    passive.add(peer["refs"]["device"])
                current = passive_peer.get(current)
                if current is None:
                    break

    # Coverage follows completed endpoint channels; declared equipment-room maps
    # do not establish that a floor is actually attached to its intended room.
    served_floors, floor_rooms = defaultdict(Counter), defaultdict(Counter)
    free_access_ports = Counter()
    for endpoint, (switch, _, _) in endpoint_paths.items():
        room = device_rooms.get(switch)
        device = objects[endpoint]
        floor = floor_of(device)
        served_floors[room][floor] += 1
        floor_rooms[(device["refs"].get("site"), floor)][room] += 1
    for device in kinds["device"]:
        if device["meta"].get("purpose") == "access":
            free_access_ports[device_rooms.get(device["key"])] += sum(
                port["attrs"].get("type") == "1000base-t" and not port["attrs"].get("mgmt_only")
                and port["key"] not in cable_peer for port in interfaces_by_device[device["key"]])
    for outlet in kinds["power_outlet"]:
        if outlet["key"] not in cable_peer:
            rack = objects.get(outlet["refs"].get("device"), {}).get("refs", {}).get("rack")
            free_outlets[rack] += 1
    backbone, backbone_by_room = [], defaultdict(list)
    for cable in kinds["cable"]:
        a, b = (objects.get(cable["refs"][side], {}) for side in ("a", "b"))
        if a.get("kind") != "interface" or b.get("kind") != "interface":
            continue
        da, db = a["refs"].get("device"), b["refs"].get("device")
        ra, rb = device_rooms.get(da), device_rooms.get(db)
        if (not ra or ra == rb or any(objects.get(room, {}).get("meta", {}).get("space_type") != "equipment_room" for room in (ra, rb))
                or objects[da]["refs"].get("site") != objects[db]["refs"].get("site")):
            continue
        link = {"cable": cable, "site": objects[da]["refs"].get("site"), "rooms": (ra, rb),
                "ends": (f"{name(da)} / {name(a['key'])}", f"{name(db)} / {name(b['key'])}")}
        backbone.append(link)
        for room in (ra, rb):
            backbone_by_room[room].append(link)

    counts = summary(plan)
    recipe = plan["recipe"]
    bank = recipe.get("profile") == "regional-bank"
    is_provider = recipe.get("profile") == "provider-backbone"
    wan_fraction = 1 - Decimal(str(recipe["reserve_fraction"]))
    coverage = type_coverage(plan)
    guidance = compatibility_guidance(plan)
    compatibility = guidance["compatibility"]
    target = compatibility["source_checked_target"]
    lines = [f"# {_cell(counts['name'])}", "", "Generated estate report", "",
             f"{counts['objects']:,} objects; {len(kinds['site'])} sites; {len(kinds['device']):,} devices; "
             f"{len(kinds['rack'])} racks; {len(kinds['virtual_machine'])} virtual machines.", "",
             f"Generator: `{counts['generator_version']}`. Plan SHA-256: `{counts['sha256']}`.",
             f"Synthetic observation date: `{recipe['as_of']}`. Hardware digest: `{plan['hardware_digest']}`.", "",
             f"Access cabling mode: `{recipe.get('patching', 'panels')}`.", "",
             "## Package compatibility", "",
             f"Whole-package source-checked target: NetBox **{target['netbox']}**, Diode SDK **{target['diode_sdk']}**, "
             f"Diode NetBox plugin **{target['diode_netbox_plugin']}**.", "",
             *[note + "\n" for note in compatibility["notes"]],
             f"Type coverage: **{coverage['generated_candidate_types']}/{coverage['candidate_types']}** pinned Diode estate candidate kinds. "
             "`coverage.json` lists every type, an example key, and the native models without SDK support. "
             "Existing-user references are external bindings; cable paths and termination rows are computed by NetBox.", "",
             "## Verification scope", "",
             "This report derives inventory, physical attachments, declared capacity, and placement from the supplied plan. "
             "Rendering it does not run the independent validator or the Diode SDK checks; their results are reported separately. "
             "No live NetBox acceptance, reconciliation, packet forwarding, protocol convergence, or application recovery is established here.", "",
             "Replay uses the recipe's synthetic observation date. Hardware provenance and configured PSU assumptions are recorded in `catalog/README.md`.", "",
             "## Estate topology", "",
             ("PoPs, customer premises and the NOC connect through actual two-site circuit terminations. "
              "Only external transit interiors are abstracted; labels count modeled circuits." if is_provider else
             "Sites attach to the provider networks through the circuit terminations present in the graph. "
             + ("Branch nodes aggregate sites of the same size; edge" if bank else "Edge")
             + " labels count actual modeled circuits. Provider interiors are abstracted."), ""]

    # Graph-derived starting points keep broad type coverage usable in a demo.
    examples = []
    if bank and "contact-assignment/site/dc-01" in objects:
        assignment = objects["contact-assignment/site/dc-01"]["refs"]
        examples.append(("Who operates the primary DC?",
                         f"{name(assignment['object'])} → {name(assignment['contact'])} ({name(assignment['role'])})"))
    vm_key = "vm/dc-01/identity/001"
    if bank and vm_key in objects and f"virtual-disk/{vm_key}/disk0" in objects:
        vm, disk = objects[vm_key], objects[f"virtual-disk/{vm_key}/disk0"]
        examples.append(("Where does identity run and store data?",
                         f"{name(vm_key)} → {disk['attrs']['size']:,} MB disk; {name(vm['refs']['device'])} → {name(vm['refs']['cluster'])}"))
    for port in sorted(kinds["power_port"], key=lambda obj: obj["key"]):
        module_key = port["refs"].get("module")
        if module_key:
            module = objects[module_key]
            examples.append(("Which installed PSU owns this inlet?",
                             f"{name(port['refs']['device'])} / {port['attrs']['name']} → "
                             f"{name(module['refs']['module_bay'])} → {objects[module['refs']['module_type']]['attrs']['model']}"))
            break
    if kinds["wireless_lan"]:
        wlan = sorted(kinds["wireless_lan"], key=lambda obj: obj["key"])[0]
        examples.append(("Which site and VLAN carry the staff WLAN?",
                         f"{wlan['attrs']['ssid']} → {name(wlan['refs']['scope_site'])} → {name(wlan['refs']['vlan'])}"))
    if "tunnel/recovery" in objects:
        tunnel = objects["tunnel/recovery"]
        profile = objects[tunnel["refs"]["ipsec_profile"]]
        examples.append(("What is planned for DC recovery?",
                         f"{name(tunnel['key'])} ({tunnel['attrs']['status']}) → {name(profile['key'])} → "
                         f"{name(profile['refs']['ike_policy'])}; translated recovery VLANs have no production workloads"))
    if "cooling/dc-01/feed" in objects:
        feed = objects["cooling/dc-01/feed"]
        examples.append(("What supplies the analytics blade's coolant?",
                         f"{name(feed['refs']['cooling_source'])} → {feed['attrs']['cooling_capacity']} kW feed → "
                         f"{name(feed['refs']['rack'])}; trace the enclosure and blade intakes"))
    placement_rows = []
    if not bank:
        for assignment in sorted(kinds["contact_assignment"], key=lambda obj: obj["key"]):
            target = assignment["refs"].get("object")
            if objects.get(target, {}).get("kind") == "site":
                examples.append((f"Who operates {name(target)}?",
                                 f"{name(target)} → {name(assignment['refs'].get('contact'))} ({name(assignment['refs'].get('role'))})"))
                break
        workload_vms, listeners, inlets = defaultdict(list), defaultdict(list), defaultdict(list)
        for vm in kinds["virtual_machine"]:
            host = objects.get(vm["refs"].get("device"), {})
            site = host.get("refs", {}).get("site") or objects.get(vm["refs"].get("cluster"), {}).get("refs", {}).get("scope_site")
            workload_vms[(site or "", vm["meta"].get("service", "Unspecified"))].append(vm)
        for service in kinds["service"]:
            listeners[service["refs"].get("virtual_machine")].append(service)
        for inlet in kinds["power_port"]:
            inlets[inlet["refs"].get("device")].append(inlet)
        for (site, workload), vms in sorted(workload_vms.items()):
            vm = min(vms, key=lambda obj: obj["key"])
            host_key = vm["refs"].get("device")
            host = objects.get(host_key, {})
            rack = host.get("refs", {}).get("rack")
            address = objects.get(vm["refs"].get("primary_ip4"), {})
            interface = address.get("refs", {}).get("assigned_object")
            service_text = "; ".join(f"{s['attrs'].get('name', s['key'])}: {s['attrs'].get('protocol', '?')}/"
                                     + ",".join(map(str, s["attrs"].get("ports", [])))
                                     for s in sorted(listeners[vm["key"]], key=lambda obj: obj["key"])) or "No service listeners"
            start = (f"{name(vm['key'])} → {name(interface)} / {address.get('attrs', {}).get('address', 'No primary IP')}; "
                     f"{service_text}; host {name(host_key)} → rack {name(rack)} / cluster {name(vm['refs'].get('cluster'))}")
            examples.append((f"Where does {workload} run at {name(site)}?", start))
            paths = []
            for port in sorted(interfaces_by_device[host_key], key=lambda obj: obj["key"]):
                if port["attrs"].get("mgmt_only") or port["attrs"].get("type") == "virtual":
                    continue
                peer = objects.get(cable_peer.get(port["key"]), {})
                cable = cable_at.get(port["key"], {})
                state = cable.get("attrs", {}).get("status", "uncabled")
                if port["attrs"].get("enabled") is not True or peer.get("attrs", {}).get("enabled") is not True:
                    state += ", interface not enabled"
                paths.append(f"{name(port['key'])} → {name(peer.get('refs', {}).get('device'))} / {name(peer.get('key'))} "
                             f"[{cable.get('attrs', {}).get('label', 'no cable')}: {state}]")
            examples.append((f"Which physical uplinks serve {name(host_key)} at {name(site)}?", "; ".join(paths) or "No physical uplinks"))
            paths = []
            for inlet in sorted(inlets[host_key], key=lambda obj: obj["key"]):
                outlet = objects.get(cable_peer.get(inlet["key"]), {})
                pdu = outlet.get("refs", {}).get("device")
                supply = outlet.get("refs", {}).get("power_port")
                feed = objects.get(cable_peer.get(supply), {})
                panel = feed.get("refs", {}).get("power_panel")
                states = [cable_at.get(key, {}).get("attrs", {}).get("status", "uncabled") for key in (inlet["key"], supply)]
                states.append(f"PDU {objects.get(pdu, {}).get('attrs', {}).get('status', 'missing')}, feed {feed.get('attrs', {}).get('status', 'missing')}")
                paths.append(f"{name(inlet['key'])} → {name(pdu)} / {name(outlet.get('key'))} → {name(feed.get('key'))} → {name(panel)} "
                             f"[{'; '.join(states)}]")
            examples.append((f"Which power paths serve {name(host_key)} at {name(site)}?", "; ".join(paths) or "No power inlet paths"))
            groups = defaultdict(list)
            for replica in vms:
                groups[replica["meta"].get("replica_group")].append(replica)
            survivors = []
            for field in ("device", "rack"):
                counts_outside = []
                for group, replicas in groups.items():
                    domains = Counter()
                    unknown = group is None
                    for replica in replicas:
                        owner = objects.get(replica["refs"].get("device"), {})
                        domain = owner.get("key") if field == "device" else owner.get("refs", {}).get("rack")
                        unknown |= not domain or domain not in objects
                        if replica["attrs"].get("status") == "active" and owner.get("attrs", {}).get("status") == "active" and domain:
                            domains[domain] += 1
                    counts_outside.append(None if unknown else sum(domains.values()) - max(domains.values(), default=0))
                survivors.append("Unspecified" if None in counts_outside else min(counts_outside, default=0))
            hosts = {replica["refs"].get("device") for replica in vms} - {None}
            racks = {objects.get(key, {}).get("refs", {}).get("rack") for key in hosts} - {None}
            placement_rows.append((name(site), workload, len(vms), len(groups) if None not in groups else "Unspecified",
                                   ", ".join(sorted({replica["meta"].get("failure_domain", "Unspecified") for replica in vms})),
                                   len(hosts), len(racks), *survivors))
    if examples:
        # Insert before the topology section, retaining its introduction below.
        offset = lines.index("## Estate topology")
        block = ["## Connected walkthroughs", "", "These are navigation hints from actual graph relationships; planned intent is labeled.", ""]
        _table(block, ("Question", "Starting path"), examples)
        lines[offset:offset] = block

    offset = lines.index("## Estate topology")
    lines[offset:offset] = (_operations_walkthrough(objects, kinds) + _ipv6_walkthrough(plan, objects, kinds)
                            + _wireless_walkthrough(plan, objects, kinds)
                            + _optics_walkthrough(objects, kinds, cable_peer, passive_peer, cable_at))

    site_groups, diagram_labels = {}, {}
    for site in sorted(kinds["site"], key=lambda o: o["key"]):
        key = site["key"]
        if contracts.get(key, {}).get("kind") == "branch":
            group = "branches/" + site["meta"].get("branch_size", "unspecified")
        else:
            group = key
        site_groups[key] = group
        diagram_labels[group] = name(key)
    group_counts = Counter(site_groups.values())
    for group, number in group_counts.items():
        if group.startswith("branches/"):
            diagram_labels[group] = f"{number} {group.split('/')[-1]} branches"
    for provider_network in kinds["provider_network"]:
        diagram_labels[provider_network["key"]] = name(provider_network["key"])
    terms = defaultdict(list)
    for term in kinds["circuit_termination"]:
        terms[term["refs"]["circuit"]].append(term)
    diagram_edges, wan = Counter(), defaultdict(list)
    purchases = []
    for circuit in kinds["circuit"]:
        ends = terms[circuit["key"]]
        targets = [term["refs"].get("termination") for term in ends]
        sites = sorted({t for t in targets if t in site_groups})
        networks = [t for t in targets if objects.get(t, {}).get("kind") == "provider_network"]
        provider = circuit["refs"].get("provider")
        two_site_path = len(sites) == 2 and not networks
        remote_speeds = []
        if two_site_path:
            for end in ends:
                port = objects.get(cable_peer.get(end["key"]), {})
                owner = objects.get(port.get("refs", {}).get("device"), {})
                if (port.get("kind") != "interface" or owner.get("refs", {}).get("site") != end["refs"].get("termination")
                        or port["attrs"].get("enabled") is not True or owner.get("attrs", {}).get("status") != "active"
                        or cable_at.get(end["key"], {}).get("attrs", {}).get("status") != "connected"):
                    two_site_path = False
                remote_speeds.append(port.get("attrs", {}).get("speed") or _speed(port.get("attrs", {}).get("type", "")) or 0)
            diagram_edges[tuple(site_groups[site] for site in sites)] += 1
        for site in sites:
            site_ends = [term for term in ends if term["refs"].get("termination") == site]
            term = site_ends[0]
            cable = cable_at.get(term["key"], {})
            peer = objects.get(cable_peer.get(term["key"]), {})
            interface = peer.get("attrs", {})
            device = objects.get(peer.get("refs", {}).get("device"), {})
            edge_speed = interface.get("speed") or _speed(interface.get("type", "")) or 0
            term_speeds = {end["attrs"].get("term_side"): end["attrs"].get("port_speed", 0) for end in ends}
            physical = Decimal(str(min([edge_speed] + remote_speeds + [end["attrs"].get("port_speed", 0) for end in ends]))) / 1000
            connected = (len(site_ends) == 1 and len(ends) == 2 and set(term_speeds) == {"A", "Z"}
                         and (two_site_path or any(objects[network]["refs"].get("provider") == provider for network in networks))
                         and peer.get("kind") == "interface" and device.get("refs", {}).get("site") == site
                         and interface.get("enabled") is True and cable.get("attrs", {}).get("status") == "connected")
            committed = Decimal(str(circuit["attrs"].get("commit_rate", 0))) / 1000
            available = min(committed, physical) if connected and circuit["attrs"].get("status") == "active" else 0
            purchase = dict(circuit=circuit, site=site, provider=provider, committed=committed,
                            handoff=physical if connected else 0, available=available, connected=connected,
                            term_speeds=term_speeds, edge_speed=edge_speed,
                            attachment=f"{name(device.get('key'))} / {name(peer.get('key'))}" if peer.get("kind") == "interface" else "No cabled interface")
            purchases.append(purchase)
            wan[(site, provider)].append(purchase)
            for network in networks:
                diagram_edges[(site_groups[site], network)] += 1
    node_ids = {key: f"n{i}" for i, key in enumerate(sorted(diagram_labels))}
    lines.extend(["```mermaid", "flowchart LR"])
    for key in sorted(diagram_labels):
        label = escape(diagram_labels[key], quote=True)
        lines.append(f'  {node_ids[key]}["{label}"]')
    for (a, b), number in sorted(diagram_edges.items()):
        lines.append(f'  {node_ids[a]} ---|"{number} circuits"| {node_ids[b]}')
    lines.extend(["```", ""])
    if is_provider:
        lines.extend(["## Provider service walkthrough", "",
            "Open a customer virtual circuit, follow its peer interface to the CE's physical parent, then follow the circuit's A and Z handoffs to the serving PoP port. "
            "The provider's private-L3 network groups service membership; its PoP interior is represented by actual routers and two-site circuits.", "",
            "Customer premises are single-homed. The backbone's router/link survival checks do not make a customer's CE or access circuit redundant. "
            "PE management uses lo0 in-band; fxp0 remains uncabled and unaddressed. NOC services have two physical PoP attachments. "
            "External transit remote interfaces and their owners are unknown.", "",
            "Traffic checks cover declared customer spoke-to-hub flows, with a stable shortest-hop path and each single inter-PoP span loss. "
            "They exclude NOC, transit and background demand; peer membership does not imply an all-to-all traffic matrix. "
            "Carrier-wide failures are outside these checks. Per-PoP carrier diversity is not guaranteed; "
            "the table shows actual inter-PoP span providers, which may share further unmodeled infrastructure. "
            "No BGP session, MPLS forwarding, routing convergence or measured throughput is established.", ""])
        transport_by_site = defaultdict(Counter)
        for circuit in kinds["circuit"]:
            ends = terms[circuit["key"]]
            owners = [objects.get(objects.get(cable_peer.get(end["key"]), {}).get("refs", {}).get("device"), {}) for end in ends]
            sites = {owner.get("refs", {}).get("site") for owner in owners}
            if (len(ends) == 2 and {end["attrs"].get("term_side") for end in ends} == {"A", "Z"}
                    and len(sites) == 2 and None not in sites
                    and all(owner.get("refs", {}).get("role") == "role/provider-edge" for owner in owners)
                    and all(owner["refs"]["site"] == end["refs"].get("termination") for owner, end in zip(owners, ends))):
                for site in sites:
                    transport_by_site[site][circuit["refs"].get("provider")] += 1
        pop_rows = []
        for site in sorted(kinds["site"], key=lambda obj: obj["key"]):
            routers = [d for d in devices_by_site[site["key"]] if d["refs"].get("role") == "role/provider-edge"]
            if not routers:
                continue
            ports = [port for router in routers for port in interfaces_by_device[router["key"]]
                     if port["attrs"].get("name") in {f"xe-0/1/{n}" for n in range(6)}]
            connected = sum(objects.get(cable_peer.get(port["key"]), {}).get("kind") == "circuit_termination"
                            and cable_at[port["key"]]["attrs"].get("status") == "connected"
                            and port["attrs"].get("enabled") is True for port in ports)
            # IP objects have addresses, not names.
            primary = [objects.get(router["refs"].get("primary_ip4"), {}).get("attrs", {}).get("address", "Missing") for router in routers]
            pop_rows.append((name(site["key"]), len(routers), len({d["refs"].get("rack") for d in routers}),
                             connected, sum(port["attrs"].get("enabled") is True for port in ports), ", ".join(primary),
                             "; ".join(f"{name(provider)} ({count} span{'s' if count != 1 else ''})"
                                       for provider, count in sorted(transport_by_site[site["key"]].items())) or "No two-PoP span found"))
        _table(lines, ["PoP", "PE routers", "PE racks", "Cabled service handoffs", "Enabled service ports", "Primary management IPs", "Actual inter-PoP carriers"], pop_rows)
        memberships = defaultdict(list)
        for term in kinds["virtual_circuit_termination"]:
            memberships[term["refs"].get("virtual_circuit")].append(term)
        assignments = defaultdict(list)
        for assignment in kinds["contact_assignment"]:
            assignments[assignment["refs"].get("object")].append(name(assignment["refs"].get("contact")))
        customer_rows = []
        for vc in sorted(kinds["virtual_circuit"], key=lambda obj: obj["key"]):
            interfaces = [objects.get(term["refs"].get("interface"), {}) for term in memberships[vc["key"]]]
            members = {objects.get(interface.get("refs", {}).get("device"), {}).get("refs", {}).get("site") for interface in interfaces}
            vrfs = {interface.get("refs", {}).get("vrf") for interface in interfaces}
            targets = {target for vrf in vrfs for target in objects.get(vrf, {}).get("refs", {}).get("import_targets", [])}
            customer_rows.append((vc["attrs"].get("cid", vc["key"]), name(vc["refs"].get("tenant")),
                len(members - {None}), short_names(members - {None}), short_names(vrfs - {None}), short_names(targets),
                ", ".join(sorted(assignments[vc["key"]])) or "Missing"))
        _table(lines, ["Private service", "Customer", "Member sites", "Actual premises", "VRF", "Import targets", "Service contact"], customer_rows)
        lines.extend(["The tables count actual devices, ports and service references. Circuit procurement below shows the physical handoffs at each site. "
                      "There is no additive WAN budget across backbone links. Customer hub commitments and NOC handoffs are checked separately from the scoped transport flow model. "
                      "Wireless is outside this initial wired provider composition.", ""])
    lines.extend(["## Sites and demand", ""])
    site_rows, endpoint_counts, cohorts = [], {}, defaultdict(list)
    for site in sorted(kinds["site"], key=lambda o: o["key"]):
        key, contract = site["key"], contracts.get(site["key"], {})
        endpoints = Counter(d["meta"].get("purpose") for d in devices_by_site[key] if d["meta"].get("endpoint"))
        endpoint_counts[key] = sum(endpoints.values())
        demand = contract.get("demand", {})
        demand_text = ", ".join(f"{n} {kind}" for kind, n in sorted(demand.items()) if kind != "peak_mbps") or "Shared services"
        if recipe.get("profile") == "school-district" and contract.get("kind") == "school":
            demand_text = f"{demand.get('enrollment', '?')} students; {demand.get('staff', '?')} staff"
        site_rows.append((name(key), contract.get("kind", "Unspecified"), len(devices_by_site[key]), len(racks_by_site[key]),
                          endpoint_counts[key], demand_text, demand.get("peak_mbps", contract.get("wan_peak_mbps", "Unspecified"))))
        if cohort := site["meta"].get("lifecycle_cohort"):
            cohorts[cohort].append(key)
    _table(lines, ["Site", "Kind", "Devices", "Racks", "Actual endpoints", "Declared demand", "Peak Mbps"], site_rows)
    if recipe.get("profile") == "school-district":
        lines.extend(["## School population and access", "",
            "Enrollment and wireless counts are declared planning demand. Wired seats and APs below count actual devices. "
            "Lab seats are shared by enrolled students; they do not add people. Staff wireless demand allows one additional device per staff member. "
            "Radio channels and coverage are synthetic; no RF survey, authentication server or packet forwarding is verified.", ""])
        rows = []
        for item in recipe.get("schools", []):
            key = f"site/school-{item['key']}"
            school_cohorts = Counter(device["meta"].get("cohort") for device in devices_by_site[key])
            d = contracts.get(key, {}).get("demand", {})
            rows.append((name(key), item["classrooms"], d.get("enrollment", "?"), school_cohorts["teacher"],
                school_cohorts["administration"], school_cohorts["student"], school_cohorts["student-lab"], d.get("wireless_students", "?"),
                school_cohorts["classroom-ap"]+school_cohorts["office-ap"]+school_cohorts["lab-ap"]))
        _table(lines, ["School", "Classrooms", "Enrollment", "Teacher desks", "Admin desks", "Classroom student PCs",
                       "Lab PCs", "Planned wireless students", "APs"], rows)
        radius = [service for service in kinds["service"] if service["attrs"].get("protocol") == "udp"
                  and {1812,1813} <= set(service["attrs"].get("ports", []))]
        if radius:
            lines.extend(["**Authentication walkthrough:** inspect the staff and student WLANs, their assigned AP radios, "
                "native management VLAN and tagged client VLANs. The district identity service exposes modeled RADIUS listeners: "
                + "; ".join(f"{name(service['refs'].get('virtual_machine'))} / {name(service['key'])} (UDP1812/1813)" for service in radius)
                + ". This explains authentication intent; a WLAN-to-RADIUS configuration or running session is not represented.", ""])
    if recipe.get("profile") == "hospital-clinics":
        lines.extend(["## Care units and shared services", "",
            "Beds, desks and rooms are declared installed capacity, not patient volume or staff headcount. "
            "The equipment columns below count actual device roles. Reference monitors and modalities have no clinical function or certification. "
            "Staff WLAN intent and segmented VLANs do not establish authentication, firewall enforcement or regulatory compliance.", ""])
        rows = []
        for family, kind in (("hospitals", "hospital"), ("clinics", "clinic")):
            for item in recipe.get(family, []):
                key = f"site/{kind}-{item['key']}"
                roles = Counter(device["refs"].get("role") for device in devices_by_site[key])
                rows.append((name(key), len(item.get("wards", [])), sum(w["beds"] for w in item.get("wards", [])),
                    item.get("exam_rooms", 0), item["imaging_rooms"], roles["role/medical-device"],
                    roles["role/imaging-device"], roles["role/workstation"], roles["role/ap"], roles["role/camera"]))
        _table(lines, ["Facility", "Wards", "Bed stations", "Exam rooms", "Imaging rooms", "Monitors", "Modalities",
                       "Workstations", "APs", "Cameras"], rows)
        lines.extend(["**Care-unit walkthrough:** start with a bedside monitor or imaging modality, open its biomedical contact, "
            "then follow its real cable to the serving floor closet. Inspect the medical or imaging VLAN, the distinct clinical workstation segment, "
            "and the site's carrier circuit and escalation desk. Continue at the service DC's clinical-records and imaging-archive VMs; "
            "their listener, replica, resource and power tables below describe the modeled application placement. Provider interiors remain abstracted.", ""])
        archive = [service for service in kinds["service"] if service["refs"].get("virtual_machine", "").split("/")[2:3] == ["imaging-archive"]]
        if archive:
            lines.extend(["**Archive listener:** " + "; ".join(
                f"{name(service['refs']['virtual_machine'])} / {name(service['key'])}: {service['attrs'].get('protocol')} "
                f"{','.join(map(str, service['attrs'].get('ports', [])))}" for service in sorted(archive, key=lambda obj: obj["key"])[:2])
                + ". These are service inventory endpoints; no image transfer, archive retention or application health is verified.", ""])
    if kinds["region"]:
        lines.extend(["## Geography and rooms", "",
                      "Region and time-zone names describe the authored Great Lakes footprint. Addresses and room geometry are fictional; "
                      "these are local placement and cable-route rules, not surveyed premises.", ""])
        room_counts = Counter(room["refs"].get("site") for room in kinds["location"]
                              if room["meta"].get("space_type") not in {"floor", "building"})
        _table(lines, ["Site", "Region", "City", "Time zone", "Rooms"],
               ((name(s["key"]), name(s["refs"].get("region")), s["meta"].get("geography", {}).get("city", "—"),
                 s["attrs"].get("time_zone", "—"), room_counts[s["key"]]) for s in sorted(kinds["site"], key=lambda o: o["key"])))
        lines.extend(["## Equipment-room coverage", "",
                      "Served floors and endpoint counts follow actual cable paths to access switches in each room. "
                      "Room declarations alone do not establish coverage. Free access ports are uncabled, non-management 1G copper ports on actual access switches.", ""])
        equipment_rooms = sorted((room for room in kinds["location"] if room["meta"].get("space_type") == "equipment_room"),
                                 key=lambda o: (o["refs"].get("site", ""), o["meta"].get("floor", 0), o["key"]))
        _table(lines, ["Site", "Equipment room", "Room floor", "Floors served (endpoints)", "Access switches", "Free access ports", "Racks"],
               ((name(room["refs"].get("site")), name(room["key"]), room["meta"].get("floor", "—"),
                 ", ".join(f"{floor}: {count}" for floor, count in sorted(served_floors[room["key"]].items())) or "No endpoint channels",
                 sum(d["meta"].get("purpose") == "access" for d in devices_by_room[room["key"]]),
                 free_access_ports[room["key"]], len(racks_by_room[room["key"]])) for room in equipment_rooms))
        missing_paths = sum(bool(d["meta"].get("endpoint")) for d in kinds["device"]) - len(endpoint_paths)
        if missing_paths:
            lines.extend([f"{missing_paths} endpoint devices have no complete traced channel to an access switch in this report.", ""])
        if backbone:
            lines.extend(["## Equipment-room backbone", "",
                          "Each row is an actual interface-to-interface cable crossing equipment rooms. Lengths are stored cable lengths "
                          "derived from the authored room geometry. Fiber is directly terminated; ducts, patch shelves, optical loss, "
                          "routing and forwarding are not simulated.", ""])
            _table(lines, ["Site", "Cable", "Room A / termination", "Room B / termination", "Media", "Length"],
                   ((name(link["site"]), link["cable"]["attrs"].get("label", link["cable"]["key"]),
                     f"{name(link['rooms'][0])} / {link['ends'][0]}", f"{name(link['rooms'][1])} / {link['ends'][1]}",
                     link["cable"]["attrs"].get("type", "Unspecified"),
                     f"{link['cable']['attrs'].get('length', 'Unspecified')} {link['cable']['attrs'].get('length_unit', '')}")
                    for link in sorted(backbone, key=lambda link: (link["site"], link["rooms"], link["cable"]["attrs"].get("label", link["cable"]["key"])))))
        if bank or endpoint_paths:
            lines.extend(["## Representative endpoint access paths", "",
                      ("One workstation per site where present, selecting the highest occupied HQ floor. Other endpoint roles are used "
                       "only when no workstation is present. " if bank else
                       "One classroom workstation per school, selecting the highest occupied teaching floor. "
                       if recipe.get("profile") == "school-district" else
                       "One medical endpoint per care facility where present, otherwise a workstation; select the highest occupied care floor. ")
                      + "Paths follow actual cables and front/rear mappings; lengths sum cable records.", ""])
        examples = []
        for site_key, devices in sorted(devices_by_site.items()):
            candidates = [device for device in devices if device["key"] in endpoint_paths]
            if not candidates:
                continue
            hq = contracts.get(site_key, {}).get("kind") == "hq"
            if recipe.get("profile") == "school-district":
                teaching = [d for d in candidates if d["meta"].get("cohort") in {"student", "teacher"}]
                device = min(teaching or candidates, key=lambda d: (-floor_of(d),d["meta"].get("cohort") != "student",d["key"]))
            elif recipe.get("profile") == "hospital-clinics":
                device = min(candidates, key=lambda d: (d["refs"].get("role") != "role/medical-device",
                             d["refs"].get("role") != "role/workstation", -floor_of(d), d["key"]))
            else:
                device = min(candidates, key=lambda d: (d["meta"].get("purpose") != "workstation", -floor_of(d) if hq else 0, d["key"]))
            switch, cables, passive = endpoint_paths[device["key"]]
            length = (f"{sum(c['attrs']['length'] for c in cables):g} m"
                      if all(c["attrs"].get("length_unit") == "m" and "length" in c["attrs"] for c in cables) else "Unspecified / mixed units")
            examples.append((name(site_key), f"{name(device['key'])} ({device['meta'].get('purpose', 'endpoint')})",
                             name(device_rooms.get(device["key"])), f"{name(device_rooms.get(switch))} / {name(switch)}",
                             len(cables), short_names(passive) if passive else "Continuous channel", length))
        if bank or examples:
            _table(lines, ["Site", "Endpoint", "Endpoint room", "Serving room / access switch", "Cables", "Passive devices", "Channel length"], examples)
    if bank:
        lines.extend(["## Branch designs", "",
                  "Equipment, inlet counts, and distinct direct upstream neighbors below are read from the actual graph. "
                  "Small branches use WAN-edge gateways and an inter-access VLAN trunk; other branch footprints retain a distribution pair. "
                  "Inherited branches retain Birch names and scoped address space; refreshed branches retain that lineage with replacement access hardware. "
                  "Procurement history is fictional; inherited does not assert vendor end-of-life status.", ""])
    design_rows = []
    for site in sorted(kinds["site"], key=lambda o: o["key"]):
        key = site["key"]
        if contracts.get(key, {}).get("kind") != "branch":
            continue
        access = [d for d in devices_by_site[key] if d["refs"].get("role") == "role/access"]
        neighbor_counts = set()
        for device in access:
            peers = {objects.get(cable_peer.get(port["key"]), {}).get("refs", {}).get("device")
                     for port in interfaces_by_device[device["key"]]}
            neighbor_counts.add(sum(objects.get(peer, {}).get("refs", {}).get("role") in {"role/distribution", "role/wan-edge"} for peer in peers))
        models = sorted({objects[d["refs"]["device_type"]]["attrs"]["model"] for d in access})
        inlet_counts = {power_ports_by_device[d["key"]] for d in access}
        design_rows.append((name(key), site["meta"].get("branch_design", "Unspecified"), site["meta"].get("branch_architecture", "Unspecified"),
                            name(site["refs"].get("tenant")), ", ".join(models), len(access),
                            ", ".join(map(str, sorted(neighbor_counts))),
                            ", ".join(map(str, sorted(inlet_counts)))))
    if bank:
        _table(lines, ["Site", "Design", "Architecture", "Owner", "Access models", "Switches", "Direct upstream neighbors per switch", "Power inlets per switch"], design_rows)
    lines.extend(["## Circuit procurement", "",
                  "CIR and installation dates below are emitted circuit attributes; the procurement explanation comes from circuit comments. "
                  "Handoff rates show both termination records and the actually cabled edge interface. A 1G handoff does not imply a 1G purchase. "
                  "Providers, service tiers and dates are fictional planning inputs, not carrier offers or verified installation history. "
                  "Separate provider networks do not establish last-mile diversity.", ""])
    ordered_purchases = sorted(purchases, key=lambda p: (p["site"], p["provider"] or "", p["circuit"]["key"]))
    shown = ordered_purchases
    if len(shown) > 80:
        # Keep the detail table readable at scale, while retaining a sample of
        # each footprint/provider/rate/cohort before filling the 80-row limit.
        representatives = {}
        for purchase in shown:
            site, circuit = purchase["site"], purchase["circuit"]
            category = (contracts.get(site, {}).get("kind"), objects[site]["meta"].get("branch_size"),
                        purchase["provider"], purchase["committed"], circuit.get("meta", {}).get("procurement", {}).get("cohort"))
            representatives.setdefault(category, purchase)
        selected = {(p["circuit"]["key"], p["site"]) for p in list(representatives.values())[:80]}
        for purchase in shown:
            if len(selected) >= 80:
                break
            selected.add((purchase["circuit"]["key"], purchase["site"]))
        shown = [p for p in shown if (p["circuit"]["key"], p["site"]) in selected]
        lines.extend([f"Showing {len(shown)} of {len(purchases)} site circuit attachments, sampling footprints, providers, rates and cohorts. "
                      "The plan contains every circuit and site attachment.", ""])
    _table(lines, ["Site", "Provider", "CID", "Type", "CIR Mbps", "Handoff A / Z / interface Mbps", "Cabled edge", "Install date", "Procurement comments"],
           ((name(p["site"]), name(p["provider"]), p["circuit"]["attrs"].get("cid", p["circuit"]["key"]),
             name(p["circuit"]["refs"].get("type")), f"{p['committed']:g}",
             " / ".join(f"{speed/1000:g}" if speed else "Unknown" for speed in
                        (p["term_speeds"].get("A", 0), p["term_speeds"].get("Z", 0), p["edge_speed"])),
             p["attachment"] + (" (not connected/enabled)" if not p["connected"] else ""),
             p["circuit"]["attrs"].get("install_date", "Unspecified"), p["circuit"]["attrs"].get("comments", "Unspecified")) for p in shown))
    wan_metrics = {}
    if not is_provider:
        lines.extend(["## WAN capacity by provider and site", "",
                      "Connected handoff capacity sums the minimum of both termination speeds and the cabled, enabled, same-site interface speed. "
                      "Usable CIR also caps each active circuit at its committed rate and requires a matching provider-network termination and connected cable. "
                      "A missing, inactive or disabled attachment contributes zero usable CIR. Budget applies the recipe's reserve fraction to usable CIR; "
                      "headroom is that budget minus declared site demand. "
                      + ("DC demand includes the full modeled branch and headquarters peak. " if bank else "DC demand is the explicit peak for that site. ")
                      + "Provider budgets are independent planning cases, not additive failover capacity or measured throughput; packet forwarding is not simulated.", ""])
        wan_rows, wan_metrics = [], {}
        for (site, provider), site_purchases in sorted(wan.items()):
            contract = contracts.get(site, {})
            demand = contract.get("wan_peak_mbps", contract.get("demand", {}).get("peak_mbps"))
            committed = sum(p["committed"] for p in site_purchases)
            handoff = sum(p["handoff"] for p in site_purchases)
            available = sum(p["available"] for p in site_purchases)
            budget = Decimal(str(available)) * wan_fraction
            spare = budget-Decimal(str(demand)) if demand is not None else None
            wan_metrics[(site, provider)] = dict(committed=committed, handoff=handoff, available=available,
                                                budget=budget, spare=spare, demand=demand)
            wan_rows.append((name(provider), name(site), len(site_purchases), f"{committed:g}", f"{handoff:g}", f"{available:g}", _rate(budget),
                             demand if demand is not None else "Unspecified", _rate(spare) if spare is not None else "Unspecified"))
        _table(lines, ["Provider", "Site", "Circuits", "Committed Mbps", "Connected handoff Mbps", "Usable CIR Mbps", "Budget Mbps", "Demand Mbps", "Headroom Mbps"], wan_rows)
    lines.extend(["## Active IP plan", "",
                  "Rows come from active prefix objects. Container reservations remain in the plan and allocation ledger.", ""])
    _table(lines, ["Site", "VRF", "Prefix", "VLAN"],
           ((name(p["refs"].get("scope_site")), name(p["refs"].get("vrf")), p["attrs"]["prefix"], name(p["refs"].get("vlan")))
            for p in sorted(kinds["prefix"], key=lambda o: o["key"]) if p["attrs"].get("status") == "active"))
    lines.extend(["## Rack elevations", "",
                  "Positions are shown top to bottom from actual device placement. Unlisted rack units are unoccupied in the plan; "
                  "0U equipment has no rack-unit position.", ""])
    for rack in sorted(kinds["rack"], key=lambda o: o["key"]):
        lines.extend([f"### {_cell(name(rack['refs'].get('site')))} / {_cell(name(rack['refs'].get('location')))} / {_cell(name(rack['key']))} ({rack['attrs']['u_height']}U)", ""])
        rows = []
        for device in sorted(devices_by_rack[rack["key"]], key=lambda o: (-(o["attrs"].get("position") or 0), o["key"])):
            hardware = objects.get(device["refs"].get("device_type"), {}).get("attrs", {})
            height = hardware.get("u_height", 0)
            position = device["attrs"].get("position")
            where = f"U{position:g}" if position is not None else "0U / unpositioned"
            if position is not None and height > 1:
                where = f"U{position:g}–{position+height-1:g}"
            rows.append((where, name(device["key"]), hardware.get("model", "Unspecified"), device["meta"].get("purpose", "Unspecified"), device["attrs"].get("face", "—")))
        _table(lines, ["Position", "Device", "Hardware", "Role", "Face"], rows)
    if kinds["rack"]:
        lines.extend(["## Rack headroom and power", "",
                      "Free rack units subtract installed positioned device heights from rack height; free outlets have no cable. "
                      "These are physical inventory counts, not guarantees of usable power, cooling or policy reserve. "
                      "Synthetic infrastructure allocations only: normal draw sums installed inlet allocations. "
                      "Each dual-supply inlet reserves the whole device allowance for failover. PDU inputs aggregate downstream loads; "
                      "connected PD reservations include a 1.25 upstream AC planning allowance. Endpoint wall power and "
                      "measured/vendor-certified electrical performance are outside this budget.", ""])
        _table(lines, ["Site", "Equipment room", "Rack", "Positioned U", "Free U", "Free PDU outlets", "Infrastructure allocation W"],
               ((name(r["refs"].get("site")), name(r["refs"].get("location")), name(r["key"]), rack_units[r["key"]],
                 r["attrs"]["u_height"] - rack_units[r["key"]], free_outlets[r["key"]], rack_watts[r["key"]])
                for r in sorted(kinds["rack"], key=lambda o: o["key"])))
    lines.extend(["## Service placement", "",
                  "Each row follows the VM's service metadata and its explicit host and cluster references. Replicas are modeled placements; synchronization is not simulated.", ""])
    _table(lines, ["Service", "VM", "Host", "Cluster", "vCPU", "Memory MB", "Disk MB"]
           + ([] if bank else ["Site", "Compute rack", "Replica group", "Replica lane", "Requested domain"]),
           ((vm["meta"].get("service", "Unspecified"), name(vm["key"]), name(vm["refs"].get("device")), name(vm["refs"].get("cluster")),
             vm["attrs"].get("vcpus", "—"), vm["attrs"].get("memory", "—"), vm["attrs"].get("disk", "—"))
            + (() if bank else (name(objects.get(vm["refs"].get("device"), {}).get("refs", {}).get("site")),
                                name(objects.get(vm["refs"].get("device"), {}).get("refs", {}).get("rack")),
                                vm["meta"].get("replica_group", "Unspecified"), vm["meta"].get("replica_lane", "Unspecified"),
                                vm["meta"].get("failure_domain", "Unspecified")))
            for vm in sorted(kinds["virtual_machine"], key=lambda o: (o["meta"].get("service", ""), o["key"]))))
    if placement_rows:
        lines.extend(["## Replica group placement", "",
                      "Groups and requested placement domains come from VM metadata; host and rack counts follow actual references. "
                      "The last two columns count active VMs on active hosts outside the most populated host or compute rack in each group, "
                      "then take the minimum across groups. These are surviving modeled inventory counts after removing one placement domain. "
                      "They do not establish reachable services, network-rack protection, restart capacity, runtime HA or cross-site replication. "
                      "The connected walkthroughs identify concrete VM, uplink and power paths to inspect; independent validation is a separate gate.", ""])
        _table(lines, ["Site", "Workload", "VMs", "Replica groups", "Requested domain", "Hosts", "Compute racks",
                       "Minimum outside one host", "Minimum outside one compute rack"], placement_rows)
    lines.extend(["## Questions to explore", ""])
    if endpoint_counts and (bank or any(endpoint_counts.values())):
        busiest = min(endpoint_counts, key=lambda key: (-endpoint_counts[key], key))
        lines.extend(["**Which site has the most modeled endpoints?**", "", f"{_cell(name(busiest))}: {endpoint_counts[busiest]} endpoint devices.", ""])
    if attached:
        switch = min(attached, key=lambda key: (-len(attached[key]), key))
        lines.extend(["**Which access switch has the largest directly attached endpoint group?**", "",
                      f"{_cell(name(switch))}: {len(attached[switch])} endpoints reached through cable paths (including passive ports in panel mode). "
                      f"Examples: {_cell(short_names(attached[switch]))}. This describes attachment, not application reachability.", ""])
    small_sites = [site for site in site_groups if objects[site]["meta"].get("branch_size") == "small"
                   and contracts.get(site, {}).get("kind") == "branch"]
    hq_sites = [site for site in site_groups if contracts.get(site, {}).get("kind") == "hq"]
    if small_sites and hq_sites:
        small = min(small_sites, key=lambda site: (objects[site]["meta"].get("branch_design") != "modern", site))
        examples = {small, min(hq_sites)}
        lines.extend(["**How do a small branch and headquarters purchase WAN capacity?**", "",
                      "These examples compare actual purchases and connected handoffs with each site's declared peak. "
                      "Procurement comments above explain the selected tier and any retained contract floor.", ""])
        _table(lines, ["Site", "Provider", "Peak Mbps", "Purchased Mbps", "Connected handoff Mbps", "Budget after reserve Mbps"],
               ((name(site), name(provider), m["demand"], f"{m['committed']:g}", f"{m['handoff']:g}", _rate(m["budget"]))
                for (site, provider), m in sorted(wan_metrics.items()) if site in examples))
    retained = [p for p in purchases if p["circuit"].get("meta", {}).get("procurement", {}).get("cohort") == "retained-birch"
                and wan_metrics[(p["site"], p["provider"])]["demand"] is not None]
    if retained:
        purchase = min(retained, key=lambda p: (-(Decimal(str(p["committed"])) - Decimal(str(wan_metrics[(p["site"], p["provider"])]["demand"])) / wan_fraction), p["circuit"]["key"]))
        metric = wan_metrics[(purchase["site"], purchase["provider"])]
        required = Decimal(str(metric["demand"])) / wan_fraction
        extra = Decimal(str(purchase["committed"])) - required
        lines.extend(["**Which retained contract has the most purchased capacity above current planning demand?**", "",
                      f"{_cell(name(purchase['site']))} / {_cell(name(purchase['provider']))}, "
                      f"{_cell(purchase['circuit']['attrs'].get('cid', purchase['circuit']['key']))}: "
                      f"{purchase['committed']:g} Mbps purchased versus {_rate(round(required, 2))} Mbps needed before reserve "
                      f"for a {metric['demand']:g} Mbps peak ({_rate(round(extra, 2))} Mbps difference, rounded to 0.01 Mbps). "
                      f"Its actual connected usable CIR is {purchase['available']:g} Mbps. "
                      f"Recorded explanation: {_cell(purchase['circuit']['attrs'].get('comments', 'Unspecified'))}", ""])
    hq_workstations = [objects[key] for key in endpoint_paths
                       if objects[key]["meta"].get("purpose") == "workstation"
                       and contracts.get(objects[key]["refs"].get("site"), {}).get("kind") == "hq"]
    if hq_workstations:
        workstation = min(hq_workstations, key=lambda d: (-floor_of(d), d["key"]))
        site, floor = workstation["refs"].get("site"), floor_of(workstation)
        rooms = floor_rooms[(site, floor)]
        served = "; ".join(f"{name(room)}: {count} endpoint channels" for room, count in sorted(rooms.items()))
        lines.extend(["**Which equipment room serves the highest occupied HQ floor?**", "",
                      f"{_cell(name(site))}, floor {floor}: {_cell(served)}. "
                      f"The representative path above follows {_cell(name(workstation['key']))} in {_cell(name(device_rooms.get(workstation['key'])))}.", ""])
        placement = contracts.get(site, {}).get("placement", {})
        main_room = placement.get("equipment_locations", {}).get("1", placement.get("equipment_location"))
        if any(room != main_room for room in rooms):
            fibers = {link["cable"]["key"]: link["cable"] for room in rooms for link in backbone_by_room[room]
                      if main_room in link["rooms"] and str(link["cable"]["attrs"].get("type", "")).startswith(("smf", "mmf"))}
            labels = sorted(cable["attrs"].get("label", key) for key, cable in fibers.items())
            examples = ", ".join(labels[:6]) + (f"; {len(labels)-6} more" if len(labels) > 6 else "")
            answer = (f"{len(fibers)} directly terminated fiber cables connect to {name(main_room)}: {examples}. "
                      "The backbone table identifies their ports and stored lengths." if fibers
                      else f"No direct fiber cables to {name(main_room)} are present for those serving rooms in this graph.")
            lines.extend(["**Which fibers connect those rooms to the main equipment room?**", "", _cell(answer), ""])
        lines.extend(["**What rack and access capacity remains in those serving rooms?**", ""])
        for room in sorted(rooms):
            racks = racks_by_room[room]
            free_u = sum(rack["attrs"]["u_height"] - rack_units[rack["key"]] for rack in racks)
            watts = sum(rack_watts[rack["key"]] for rack in racks)
            lines.append(f"{_cell(name(room))}: {free_u:g} free rack units across {len(racks)} {'rack' if len(racks) == 1 else 'racks'}; "
                         f"{free_access_ports[room]} uncabled 1G access ports; {watts:g} W of synthetic infrastructure allocation.")
            lines.append("")
    ledger = [v for v in kinds["virtual_machine"] if v["meta"].get("service") == "ledger-db"]
    if ledger:
        lines.extend(["**Where are the ledger database replicas placed?**", "",
                      f"{len(ledger)} VMs on {_cell(short_names(v['refs'].get('device') for v in ledger))}; "
                      f"clusters: {_cell(short_names(v['refs'].get('cluster') for v in ledger))}.", ""])
    if cohorts:
        lines.extend(["**Which branches carry each lifecycle story?**", "",
                      "Cohorts identify the selected design states shown above. Their procurement history is fictional, not a vendor end-of-life claim.", ""])
        _table(lines, ["Cohort", "Branches", "Examples"], ((cohort, len(keys), short_names(keys)) for cohort, keys in sorted(cohorts.items())))
    lines.extend(["## Explicit assumptions", ""])
    assumptions = guidance["assumptions"]
    lines.extend(f"- {_cell(a)}" for a in assumptions)
    if not assumptions:
        lines.append("No profile assumptions were recorded.")
    if guidance["historical_assumptions"]:
        lines.extend(["", "## Historical mapping evidence", "",
                      "The following statements are retained verbatim in the frozen graph from an earlier blueprint. "
                      "They describe mapping-only source evidence and the unpatched path; they are not the current whole-package target or the local bridge qualification scope stated above.", ""])
        lines.extend(f"- {_cell(text)}" for text in guidance["historical_assumptions"])
    lines.extend(["", "## Object inventory", ""])
    _table(lines, ["Kind", "Count"], counts["counts"].items())
    return "\n".join(lines).rstrip() + "\n"
