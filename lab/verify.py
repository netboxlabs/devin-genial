"""Read-only NetBox REST readback of generated intent; never an ingestion receipt.

Identity source: diode-netbox-plugin v1.17.0/docs/matching-criteria-documentation.md.
REST shapes: finite NetBox 4.6/4.7 serializers for IPAM, virtualization, DCIM, circuits.
The finite mappings below cover generator output. Missing API fields fail rather
than being treated as equivalent to a dropped value. The local panel bridge is
read back through native rear_ports tuples; private PortMapping row IDs have no
public REST representation. This normalization performs no target writes.
Source: https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/device_components.py
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


ENDPOINTS = {
    "site": "dcim/sites", "location": "dcim/locations", "rack": "dcim/racks",
    "region": "dcim/regions", "site_group": "dcim/site-groups",
    "rack_role": "dcim/rack-roles", "platform": "dcim/platforms",
    "manufacturer": "dcim/manufacturers", "device_type": "dcim/device-types",
    "device_role": "dcim/device-roles", "device": "dcim/devices",
    "interface": "dcim/interfaces", "cable": "dcim/cables",
    "front_port": "dcim/front-ports", "rear_port": "dcim/rear-ports",
    "power_port": "dcim/power-ports", "power_outlet": "dcim/power-outlets",
    "power_panel": "dcim/power-panels", "power_feed": "dcim/power-feeds",
    "tenant": "tenancy/tenants", "tag": "extras/tags",
    "vrf": "ipam/vrfs", "prefix": "ipam/prefixes", "ip_address": "ipam/ip-addresses",
    "vlan": "ipam/vlans", "service": "ipam/services",
    "provider": "circuits/providers", "provider_network": "circuits/provider-networks",
    "circuit_type": "circuits/circuit-types", "circuit": "circuits/circuits",
    "circuit_termination": "circuits/circuit-terminations",
    "cluster_type": "virtualization/cluster-types", "cluster": "virtualization/clusters",
    "virtual_machine": "virtualization/virtual-machines",
    "vm_interface": "virtualization/interfaces", "virtual_disk": "virtualization/virtual-disks",
}
for _app, _kinds in {
    "dcim": "console_port console_server_port device_bay inventory_item inventory_item_role mac_address module module_bay module_type module_type_profile module_bay_type rack_type rack_group rack_reservation virtual_chassis virtual_device_context cable_bundle cooling_source cooling_feed cooling_intake cooling_outflow",
    "ipam": "asn asn_range aggregate rir ip_range role route_target vlan_group vlan_translation_policy vlan_translation_rule fhrp_group fhrp_group_assignment",
    "circuits": "provider_account circuit_group circuit_group_assignment virtual_circuit virtual_circuit_type virtual_circuit_termination",
    "tenancy": "tenant_group contact contact_group contact_role contact_assignment",
    "virtualization": "cluster_group virtual_machine_type",
    "vpn": "ike_policy ike_proposal ip_sec_policy ip_sec_proposal ip_sec_profile tunnel tunnel_group tunnel_termination l2vpn l2vpn_termination",
    "wireless": "wireless_lan wireless_lan_group wireless_link",
    "extras": "custom_field custom_field_choice_set custom_link journal_entry",
    "users": "owner owner_group user",
}.items():
    for _kind in _kinds.split():
        _slug = _kind.replace("ip_sec", "ipsec").replace("_", "-")
        ENDPOINTS[_kind] = f"{_app}/{_slug[:-1] + 'ies' if _slug.endswith('policy') else _slug + 's'}"
ENDPOINTS.update(mac_address="dcim/mac-addresses", virtual_chassis="dcim/virtual-chassis",
                 journal_entry="extras/journal-entries")
CONTENT_TYPES = {kind: f"{path.split('/')[0]}.{kind.replace('_', '')}"
                 for kind, path in ENDPOINTS.items()}
# These are readback identities, not a reimplementation of every Diode matcher.
IDENTITIES = {kind: ("name",) for kind in ENDPOINTS}
IDENTITIES.update({
    "region": ("name", "parent"), "site_group": ("name", "parent"),
    "platform": ("name", "manufacturer"),
    "location": ("name", "site", "parent"),
    "device_type": ("model", "manufacturer"),
    "device": ("name", "site", "tenant"),
    "rack": ("name", "site", "location"),
    "vrf": ("name", "tenant", "rd"), "vlan": ("vid", "group", "site", "qinq_svlan"),
    "prefix": ("prefix", "vrf"), "ip_address": ("address", "vrf"),
    "provider_network": ("name", "provider"), "circuit": ("cid", "provider"),
    "circuit_termination": ("term_side", "circuit"),
    "power_panel": ("name", "site"), "power_feed": ("name", "power_panel"),
    "cluster": ("name", "scope_type", "scope_id", "group"),
    "virtual_machine": ("name", "cluster", "tenant"),
    "vm_interface": ("name", "virtual_machine"),
    "virtual_disk": ("name", "virtual_machine"),
    "service": ("name", "parent_object_type", "parent_object_id"),
    "cable": ("terminations",),
    "aggregate": ("prefix", "rir"), "asn": ("asn",),
    "ip_range": ("start_address", "end_address", "vrf"),
    "provider_account": ("account", "provider"),
    "rack_type": ("model", "manufacturer"), "rack_reservation": ("rack", "units"),
    "module": ("module_bay",), "module_bay": ("name", "device", "module"),
    "module_type": ("model", "manufacturer"),
    "module_bay_type": ("name", "manufacturer"),
    "inventory_item": ("name", "device", "parent"),
    "virtual_device_context": ("name", "device"),
    "contact_assignment": ("object_type", "object_id", "contact", "role"),
    "fhrp_group": ("group_id",),
    "fhrp_group_assignment": ("interface_type", "interface_id", "group"),
    "l2vpn_termination": ("l2vpn", "assigned_object_type", "assigned_object_id"),
    "tunnel_termination": ("tunnel", "termination_type", "termination_id"),
    "circuit_group_assignment": ("group", "member_type", "member_id"),
    "virtual_circuit": ("cid", "provider_network"),
    "virtual_circuit_termination": ("interface",),
    "vlan_translation_rule": ("policy", "local_vid"),
    "wireless_lan": ("ssid", "group", "vlan"),
    "wireless_link": ("interface_a", "interface_b"),
    "mac_address": ("mac_address", "assigned_object_type", "assigned_object_id"),
    "journal_entry": ("assigned_object_type", "assigned_object_id", "comments"),
    "cooling_source": ("name", "site"), "cooling_feed": ("name", "cooling_source"),
    "cooling_intake": ("name", "device"), "cooling_outflow": ("name", "device"),
    "vlan_group": ("name", "scope_type", "scope_id"),
    "contact_group": ("name", "parent"), "tenant": ("name", "group"),
    "user": ("username",),
})
for _kind in ("interface", "front_port", "rear_port", "power_port", "power_outlet",
              "console_port", "console_server_port", "device_bay"):
    IDENTITIES[_kind] = ("name", "device")


def _generic_stem(obj, field):
    if field.startswith("scope_"):
        return "scope"
    if field in {"assigned_object", "termination", "object", "member", "component"}:
        return field
    if obj["kind"] == "fhrp_group_assignment" and field == "interface":
        return "interface"
    if obj["kind"] == "service" and field in {"virtual_machine", "device"}:
        return "parent_object"


def _value(value):
    if isinstance(value, dict):
        return value.get("id", value.get("value"))
    if isinstance(value, list):
        return tuple(_value(item) for item in value)
    return value


def _ends(row):
    return tuple(sorted((end["object_type"], end["object_id"])
                        for side in ("a_terminations", "b_terminations")
                        for end in row.get(side, [])))


def _identity_fields(obj):
    if obj["kind"] in {"device", "rack"} and obj["attrs"].get("asset_tag"):
        return ("asset_tag",)
    if obj["kind"] == "vrf" and obj["attrs"].get("rd"):
        return ("rd",)
    return IDENTITIES[obj["kind"]]


def _expected_ref(obj, field, target, objects, ids):
    targets = target if isinstance(target, list) else [target]
    values = [ids[key]["id"] for key in targets]
    if _generic_stem(obj, field):
        return (CONTENT_TYPES[objects[targets[0]]["kind"]], values[0])
    if obj["kind"] == "cable" and field in {"a", "b"}:
        return [(CONTENT_TYPES[objects[targets[0]]["kind"]], values[0])]
    return sorted(values) if isinstance(target, list) else values[0]


def _actual_ref(obj, field, row):
    stem = _generic_stem(obj, field)
    if stem:
        return (_value(row[f"{stem}_type"]), row[f"{stem}_id"])
    if obj["kind"] == "cable" and field in {"a", "b"}:
        return sorted((end["object_type"], end["object_id"])
                      for end in row[f"{field}_terminations"])
    value = row[field]
    return sorted(_value(item) for item in value) if isinstance(value, list) else _value(value)


def _desired_identity(obj, objects, ids):
    row = dict(obj["attrs"])
    fields = _identity_fields(obj)
    for field, target in obj["refs"].items():
        stem = _generic_stem(obj, field)
        relevant = (field in fields or (stem and f"{stem}_id" in fields) or
                    (obj["kind"] == "cable" and field in {"a", "b"}))
        if not relevant:
            continue
        value = _expected_ref(obj, field, target, objects, ids)
        if stem:
            row[f"{stem}_type"], row[f"{stem}_id"] = value
        elif obj["kind"] == "cable" and field in {"a", "b"}:
            row[f"{field}_terminations"] = [{"object_type": typ, "object_id": ident}
                                             for typ, ident in value]
        else:
            row[field] = value
    return tuple(_ends(row) if field == "terminations" else _value(row.get(field))
                 for field in fields)


def _equal_scalar(expected, actual):
    if isinstance(expected, (list, dict)):
        return expected == actual
    actual = _value(actual)
    if isinstance(expected, bool):
        return isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)):
        try:
            return not isinstance(actual, bool) and Decimal(str(expected)) == Decimal(str(actual))
        except InvalidOperation:
            return False
    return expected == actual


def _choice_shape(value, value_type):
    return (isinstance(value, dict) and set(value) == {"value", "label"}
            and type(value["value"]) is value_type and isinstance(value["label"], str))


def _equal_attribute(obj, field, expected, actual):
    if (obj["kind"], field) in {("ike_proposal", "group"), ("ip_sec_policy", "pfs_group")}:
        # Installed 4.7 vpn/api/serializers_/crypto.py:26,93 uses integer
        # DHGroupChoices; netbox/api/fields.py:54 emits exactly value + label.
        return _choice_shape(actual, int) and type(expected) is int and expected == actual["value"]
    if field == "custom_fields":
        if not isinstance(actual, dict):
            return False
        normalized = {}
        for name, typed in obj["attrs"][field].items():
            if name not in actual:
                return False
            typ, value = next(iter(typed)), actual[name]
            # extras/models/customfields.py:387 wraps only select/multiselect.
            # extras/api/customfields.py:56 emits all model CF definitions, even
            # unowned ones; assert only the keys this plan actually supplied.
            if typ == "selection":
                if not _choice_shape(value, str):
                    return False
                value = value["value"]
            elif typ == "multiple_selection":
                if not isinstance(value, list) or any(not _choice_shape(item, str) for item in value):
                    return False
                value = [item["value"] for item in value]
            normalized[name] = value
        return expected == normalized
    return _equal_scalar(expected, actual)


def _expected_attribute(obj, field, value):
    # Finite pinned plugin wire conversions; no missing API field is forgiven.
    if field == "custom_fields":
        result = {}
        for name, typed in value.items():
            if not isinstance(typed, dict) or len(typed) != 1:
                raise ValueError(f"{obj['key']}: custom field must have one typed value")
            typ, raw = next(iter(typed.items()))
            if typ not in {"text", "long_text", "integer", "decimal", "boolean", "url", "selection", "multiple_selection"}:
                raise ValueError(f"{obj['key']}: unsupported custom field readback type {typ}")
            result[name] = raw
        return result
    if obj["kind"] == "custom_field_choice_set" and field == "extra_choices":
        return [item.split(":", 1) for item in value]
    if (obj["kind"], field) in {("module_type_profile", "schema"), ("module_type", "attributes"),
                               ("custom_field", "validation_schema")}:
        return json.loads(value)
    if ((obj["kind"], field) in {("ike_proposal", "group"), ("ip_sec_policy", "pfs_group")}
            and isinstance(value, str) and value.isascii() and value.isdecimal()):
        return int(value)
    return value


def verify_plan(plan, inventory, previous_receipt=None, strict_inventory=False, allow_existing_receipt=None):
    """Compare every emitted field; report IDs for replay/growth comparisons.

    Inventory is {canonical_kind: [full REST records]}. A previous successful
    receipt checks ID stability and unexpected new rows. Strict inventory also
    rejects unrelated rows unless their exact kind/ID appears in a successful
    allow-existing receipt. That exception does not assert their current fields.
    """
    allowed_existing = {}
    if allow_existing_receipt is not None:
        if not strict_inventory:
            raise ValueError("allow-existing receipt requires strict inventory")
        if not isinstance(allow_existing_receipt, dict) or allow_existing_receipt.get("success") is not True:
            raise ValueError("allow-existing receipt must be successful")
        captured = allow_existing_receipt.get("target_ids")
        if (not isinstance(captured, dict) or any(not isinstance(values, list)
                or any(type(value) is not int or value <= 0 for value in values)
                or len(values) != len(set(values)) for values in captured.values())):
            raise ValueError("allow-existing receipt must contain valid captured target IDs")
        allowed_existing = {kind: set(values) for kind, values in captured.items()}
    objects = {obj["key"]: obj for obj in plan["objects"]}
    if len(objects) != len(plan["objects"]):
        raise ValueError("duplicate canonical keys")
    findings, ids, indexes, rows_by_id = [], {}, {}, {}
    coverage = Counter(attributes_expected=sum(len(obj["attrs"]) for obj in objects.values()),
                       references_expected=sum(len(obj["refs"]) for obj in objects.values()))

    def issue(key, field, code, expected=None, actual=None):
        findings.append({"object": key, "field": field, "code": code,
                         "expected": expected, "actual": actual})

    for kind, rows in inventory.items():
        rows_by_id[kind] = {row["id"]: row for row in rows}
        if len(rows_by_id[kind]) != len(rows):
            issue(kind, "id", "duplicate_inventory_id")
    unresolved = dict(objects)
    while unresolved:
        progress = False
        for key, obj in list(unresolved.items()):
            kind = obj["kind"]
            if kind not in ENDPOINTS:
                issue(key, "kind", "unsupported_kind", kind)
                del unresolved[key]
                continue
            try:
                identity = _desired_identity(obj, objects, ids)
            except KeyError:
                continue
            fields = _identity_fields(obj)
            index_key = kind, fields
            if index_key not in indexes:
                index = defaultdict(list)
                for row in inventory.get(kind, []):
                    values = tuple(_ends(row) if field == "terminations" else _value(row.get(field))
                                   for field in fields)
                    index[values].append(row["id"])
                indexes[index_key] = index
            matches = indexes[index_key].get(identity, [])
            if len(matches) == 1:
                ids[key] = {"kind": kind, "id": matches[0]}
            else:
                issue(key, "identity", "missing_object" if not matches else "ambiguous_identity",
                      dict(zip(fields, identity)), matches)
            del unresolved[key]
            progress = True
        if not progress:
            for key in unresolved:
                issue(key, "identity", "unresolved_identity_dependency")
            break

    managed = defaultdict(set)
    front_port_mappings = {}
    for key, ident in ids.items():
        pair = ident["kind"], ident["id"]
        if ident["id"] in managed[ident["kind"]]:
            issue(key, "identity", "canonical_identity_collision", actual=pair)
        managed[ident["kind"]].add(ident["id"])
        obj, row = objects[key], rows_by_id[pair[0]][pair[1]]
        if obj["kind"] == "front_port" and "rear_port" in obj["refs"]:
            mapping = row.get("rear_ports")
            fields = {"position", "rear_port", "rear_port_position"}
            if (type(row.get("positions")) is not int or row["positions"] != 1
                    or not isinstance(mapping, list) or len(mapping) != 1
                    or not isinstance(mapping[0], dict) or set(mapping[0]) != fields
                    or any(type(mapping[0][field]) is not int or mapping[0][field] <= 0 for field in fields)
                    or mapping[0]["position"] != 1):
                issue(key, "rear_ports", "invalid_api_mapping", actual=mapping)
            else:
                front_port_mappings[key] = {"front_port": ident["id"], "rear_ports": [dict(mapping[0])]}
                # This exact one-position REST tuple corresponds to the legacy
                # SDK reference and position. Keep the source inventory intact.
                row = {**row, "rear_port": mapping[0]["rear_port"],
                       "rear_port_position": mapping[0]["rear_port_position"]}
        for field, expected in obj["attrs"].items():
            expected = _expected_attribute(obj, field, expected)
            if (obj["kind"] == "service" and field in {"ports", "protocol"}
                    and "port_mappings" in row):
                # NetBox 4.7 stores the legacy SDK pair as combined port mappings.
                expected_mappings = sorted(f"{obj['attrs']['protocol']}/{p}" for p in obj["attrs"]["ports"])
                if sorted(row["port_mappings"]) != expected_mappings:
                    issue(key, "port_mappings", "attribute_mismatch", expected_mappings, row["port_mappings"])
                coverage["attributes_checked"] += 1
                continue
            if field not in row:
                issue(key, field, "missing_api_field", expected)
            else:
                coverage["attributes_checked"] += 1
                if not _equal_attribute(obj, field, expected, row[field]):
                    issue(key, field, "attribute_mismatch", expected, row[field])
        for field, target in obj["refs"].items():
            try:
                expected = _expected_ref(obj, field, target, objects, ids)
            except KeyError:
                issue(key, field, "unresolved_reference", target)
                continue
            try:
                actual = _actual_ref(obj, field, row)
            except KeyError:
                issue(key, field, "missing_api_field", expected)
                continue
            coverage["references_checked"] += 1
            if expected != actual:
                issue(key, field, "reference_mismatch", expected, actual)

    target_ids = {kind: sorted(row["id"] for row in rows) for kind, rows in sorted(inventory.items())}
    if previous_receipt:
        if not previous_receipt.get("success"):
            raise ValueError("previous receipt must be successful")
        for key, previous in previous_receipt["ids"].items():
            if key not in objects:
                issue(key, "identity", "canonical_object_removed", previous)
            elif ids.get(key) != previous:
                issue(key, "id", "identity_changed", previous, ids.get(key))
        for key, previous in previous_receipt.get("front_port_mappings", {}).items():
            if front_port_mappings.get(key) != previous:
                issue(key, "rear_ports", "front_port_mapping_changed", previous, front_port_mappings.get(key))
        for kind, current in target_ids.items():
            added = set(current) - set(previous_receipt["target_ids"].get(kind, []))
            expected_added = {ref["id"] for key, ref in ids.items()
                              if ref["kind"] == kind and key not in previous_receipt["ids"]}
            unexplained = added - expected_added - allowed_existing.get(kind, set())
            if unexplained:
                issue(kind, "id", "unexpected_new_objects", [], sorted(unexplained))
    unmatched = {kind: sorted(set(values) - managed[kind]) for kind, values in target_ids.items()
                 if set(values) - managed[kind]}
    if strict_inventory:
        for kind, values in unmatched.items():
            unexpected = sorted(set(values) - allowed_existing.get(kind, set()))
            if unexpected:
                issue(kind, "id", "unmatched_target_objects", [], unexpected)
    allowed_unmatched = {kind: sorted(set(values) & allowed_existing.get(kind, set()))
                         for kind, values in unmatched.items()
                         if set(values) & allowed_existing.get(kind, set())}
    return {"success": not findings, "target_contract": "Genial finite NetBox REST normalization",
            "canonical_objects": len(objects), "matched_objects": len(ids),
            "mismatch_count": len(findings), "mismatches": findings, "coverage": dict(coverage),
            "ids": ids, "target_ids": target_ids, "unmatched_target_ids": unmatched,
            "front_port_mappings": front_port_mappings,
            "allowed_existing_target_ids": allowed_unmatched,
            "strict_inventory": strict_inventory,
            "limits": ["REST state comparison does not establish Diode reconciliation status.",
                       "Only kinds present in the canonical plan are inventoried.",
                       "Only emitted fields are asserted; meta/contracts require the offline validator.",
                       "REST exposes mapping tuples and port IDs, but cannot detect recreation of identical private PortMapping rows.",
                       "Allowed existing IDs remain visible but require their own plan readback to establish preservation.",
                       "Without strict inventory, unrelated existing rows are reported, not rejected."]}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_inventory(url, token, kinds, branch=None):
    """Fetch full paginated inventories; never follow credential-bearing redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("NetBox URL must be an HTTP(S) base URL without credentials, query, or fragment")
    if not token or any(char.isspace() for char in token):
        raise ValueError("NetBox token is empty or contains whitespace")
    base = url.rstrip("/") + "/api/"
    origin = parsed.scheme, parsed.netloc
    opener = urllib.request.build_opener(_NoRedirect())
    inventory = {}
    for kind in sorted(set(kinds)):
        if kind not in ENDPOINTS:
            continue  # verify_plan reports unsupported kinds explicitly.
        next_url = base + ENDPOINTS[kind] + "/?limit=1000&ordering=id"
        seen, records, count = set(), [], None
        while next_url:
            target = urllib.parse.urlsplit(next_url)
            if (target.scheme, target.netloc) != origin or next_url in seen:
                raise ValueError(f"{kind}: unsafe or repeated pagination URL")
            seen.add(next_url)
            prefix = "Bearer " if token.startswith("nbt_") else "Token "
            headers = {"Authorization": prefix + token, "Accept": "application/json"}
            if branch:
                headers["X-NetBox-Branch"] = branch
            request = urllib.request.Request(next_url, headers=headers)
            try:
                with opener.open(request, timeout=60) as response:
                    page = json.load(response)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"{kind}: NetBox GET failed with HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                raise RuntimeError(f"{kind}: NetBox GET connection failed") from None
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                raise ValueError(f"{kind}: expected paginated NetBox REST response")
            if count is not None and count != page.get("count"):
                raise ValueError(f"{kind}: target inventory changed during pagination; retry after reconciliation")
            count = page.get("count")
            records.extend(page["results"])
            next_url = urllib.parse.urljoin(next_url, page["next"]) if page.get("next") else None
        if count != len(records):
            raise ValueError(f"{kind}: pagination count disagrees with returned records")
        inventory[kind] = records
    return inventory


def _load_receipt(path, target_url):
    if path is None:
        return None
    receipt = json.loads(path.read_text())
    if not isinstance(receipt, dict) or receipt.get("success") is not True:
        raise ValueError("supplied receipt must be successful")
    if (not isinstance(receipt.get("target_url"), str)
            or receipt["target_url"].rstrip("/") != target_url.rstrip("/")):
        raise ValueError("supplied receipt target URL does not match this NetBox target")
    return receipt


def bootstrap_plan():
    """Expected identities for this repository's pinned, prepared local target.

    NetBox v4.7.0 dcim/migrations/0206_load_module_type_profiles.py loads
    these seven profiles. lab.setup provisions admin; the Diode plugin adds
    diode. Never infer allowable records from an already populated target.
    """
    return {"objects": [
        {"key": f"bootstrap/{kind}/{name}", "kind": kind,
         "attrs": {field: name}, "refs": {}}
        for kind, field, names in (
            ("user", "username", ("admin", "diode")),
            ("module_type_profile", "name", (
                "CPU", "Expansion card", "Fan", "GPU", "Hard disk", "Memory", "Power supply")),
        ) for name in names
    ]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, nargs="?")
    parser.add_argument("--bootstrap", action="store_true",
                        help="capture only the pinned local target's nine bootstrap identities; rejects estate records across all supported endpoints")
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL"))
    parser.add_argument("--token-file", type=Path, help="private file containing only the NetBox token; otherwise NETBOX_TOKEN")
    parser.add_argument("--branch", help="NetBox Branching schema ID for branch-aware readback")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--previous-receipt", type=Path)
    parser.add_argument("--allow-existing-receipt", type=Path,
                        help="successful same-target readback whose captured IDs may coexist with this plan; requires --strict-inventory")
    parser.add_argument("--strict-inventory", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.url:
            raise ValueError("set NETBOX_URL or --url")
        if args.bootstrap:
            if args.plan or args.previous_receipt or args.allow_existing_receipt:
                raise ValueError("bootstrap cannot take a plan, previous receipt, or allow-existing receipt")
        elif args.plan is None:
            raise ValueError("supply a plan or use --bootstrap before loading the pinned local target")
        if args.receipt.exists():
            raise ValueError("receipt already exists; choose a new path to preserve prior evidence")
        previous = _load_receipt(args.previous_receipt, args.url)
        allowed = _load_receipt(args.allow_existing_receipt, args.url)
        if allowed is not None and not args.strict_inventory:
            raise ValueError("allow-existing receipt requires strict inventory")
        token = args.token_file.read_text().strip() if args.token_file else os.environ.get("NETBOX_TOKEN", "")
        plan_bytes = (json.dumps(bootstrap_plan(), sort_keys=True).encode()
                      if args.bootstrap else args.plan.read_bytes())
        plan = json.loads(plan_bytes)
        started_at = datetime.now(timezone.utc).isoformat()
        inventory = fetch_inventory(args.url, token, ENDPOINTS if args.bootstrap
                                    else (obj["kind"] for obj in plan["objects"]), args.branch)
        observed_at = datetime.now(timezone.utc).isoformat()
        result = verify_plan(plan, inventory, previous, args.bootstrap or args.strict_inventory, allowed)
        result.update(plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
                      target_url=args.url.rstrip("/"), readback_started_at=started_at,
                      observed_at=observed_at, branch=args.branch)
        if args.bootstrap:
            result["purpose"] = "pinned-local-bootstrap"
            result["limits"][1] = "All supported readback endpoints were inventoried; only the nine expected bootstrap identities are permitted."
            result["limits"].append("Use this allowlist only before the first load in this database lifecycle; never reuse it after reset.")
        # Preserve earlier evidence (and never overwrite a plan passed by mistake).
        with args.receipt.open("x") as output:
            output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"ids", "target_ids", "unmatched_target_ids", "allowed_existing_target_ids", "front_port_mappings", "mismatches"}}, sort_keys=True))
        for finding in result["mismatches"][:10]:
            print(json.dumps(finding, sort_keys=True))
        return 0 if result["success"] else 1
    except (OSError, ValueError, RuntimeError) as exc:
        # Never print raw network requests/responses or credential values.
        parser.exit(2, f"readback failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
