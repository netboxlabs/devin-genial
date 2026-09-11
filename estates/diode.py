"""Export canonical estate records as bounded, offline Diode replay requests.

Wire contract: netboxlabs/diode-sdk-python v1.14.0, sdk/client.py and ingester.py.
Identity: netboxlabs/diode-netbox-plugin v1.17.0,
docs/matching-criteria-documentation.md. Full records appear once; nested
references carry only identity. Primary IP updates break the device/IP cycle.
Neither dependency order nor successful replay proves live reconciliation.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import unicodedata


SDK_VERSION = "1.14.0"
MAX_ENTITIES = 1000
MAX_REQUEST_BYTES = 2_500_000
_PRIMARY_IPS = {"primary_ip4", "primary_ip6", "oob_ip"}
# These back-references close real graph cycles after their members exist.
_DEFERRED = {"device": {"primary_ip4", "primary_ip6", "oob_ip"},
             "virtual_machine": {"primary_ip4", "primary_ip6"},
             "virtual_device_context": {"primary_ip4", "primary_ip6"},
             "interface": {"primary_mac_address"}, "vm_interface": {"primary_mac_address"},
             "virtual_chassis": {"master"}, "module_bay": {"installed_module"}}
_DATE_FIELDS = {"install_date", "termination_date", "date_added", "end_of_life"}
_PORTS = {"interface", "front_port", "rear_port", "power_port", "power_outlet",
          "console_port", "console_server_port"}
_TERMINATIONS = _PORTS | {"circuit_termination", "power_feed"}
# Attributes and reference fields sufficient to scope each supported identity.
# Optional fields below are included only when present in the canonical record.
_IDENTITY = {
    "aggregate": (("prefix",), ("rir",)),
    "asn": (("asn",), ()),
    "circuit": (("cid",), ("provider",)),
    "circuit_termination": (("term_side",), ("circuit",)),
    "cluster": (("name",), ("group", "scope_site", "scope_location")),
    "contact": (("name",), ()),
    "device": (("name", "asset_tag"), ("site", "tenant")),
    "device_type": (("model",), ("manufacturer",)),
    "location": (("name",), ("site", "parent")),
    "ip_address": (("address",), ("vrf",)),
    "prefix": (("prefix",), ("vrf",)),
    "power_panel": (("name",), ("site",)),
    "power_feed": (("name",), ("power_panel",)),
    "provider_network": (("name",), ("provider",)),
    "rack": (("name", "asset_tag"), ("site", "location")),
    "rack_type": (("model",), ("manufacturer",)),
    "tenant": (("name",), ("group",)),
    "virtual_machine": (("name",), ("cluster", "device", "tenant")),
    "vm_interface": (("name",), ("virtual_machine",)),
    "virtual_disk": (("name",), ("virtual_machine",)),
    "fhrp_group": (("protocol", "group_id"), ()),
    "wireless_lan": (("ssid",), ("group", "vlan")),
    "asn_range": (("name",), ()),
    "provider_account": (("account",), ("provider",)),
    "ip_range": (("start_address", "end_address"), ("vrf",)),
    "rack_reservation": (("units",), ("rack",)),
    "module": ((), ("module_bay",)),
    "module_bay": (("name",), ("device", "module")),
    "module_type": (("model",), ("manufacturer",)),
    "device_bay": (("name",), ("device",)),
    "inventory_item": (("name", "asset_tag"), ("device", "parent")),
    "virtual_device_context": (("name", "identifier"), ("device",)),
    "contact_assignment": ((), ("object", "contact", "role")),
    "fhrp_group_assignment": ((), ("interface", "group")),
    "l2vpn_termination": ((), ("l2vpn", "assigned_object")),
    "tunnel_termination": ((), ("tunnel", "termination")),
    "circuit_group_assignment": ((), ("group", "member")),
    "virtual_circuit": (("cid",), ("provider_network",)),
    "virtual_circuit_termination": ((), ("interface",)),
    "vlan_translation_rule": (("local_vid",), ("policy",)),
    "wireless_link": ((), ("interface_a", "interface_b")),
    "mac_address": (("mac_address",), ("assigned_object",)),
    "journal_entry": (("comments",), ("assigned_object",)),
    "cooling_source": (("name",), ("site",)),
    "cooling_feed": (("name",), ("cooling_source",)),
    "cooling_intake": (("name",), ("device",)),
    "cooling_outflow": (("name",), ("device",)),
    "user": (("username",), ()),
}
for _kind in _PORTS:
    _IDENTITY[_kind] = (("name",), ("device",))
for _kind in ("interface", "front_port", "rear_port"):
    # PGV constrains even the default "" on these nonoptional type fields.
    # Keep the canonical type in nested references so ValidateAll also accepts them.
    _IDENTITY[_kind] = (("name", "type"), ("device",))
for _kind in ("manufacturer", "platform", "tag", "role", "rack_role",
              "circuit_type", "provider", "cluster_type", "cluster_group",
              "site", "site_group", "region", "vlan_group", "tenant_group",
              "contact_role", "contact_group", "virtual_chassis",
              "wireless_lan_group", "rir"):
    _IDENTITY[_kind] = (("name",), ("parent",))
_IDENTITY["device_role"] = (("name",), ("parent",))
for _kind in ("circuit_group", "virtual_circuit_type", "route_target", "tunnel", "tunnel_group",
              "ike_policy", "ike_proposal", "ip_sec_policy", "ip_sec_proposal", "ip_sec_profile",
              "vlan_translation_policy", "l2vpn", "inventory_item_role", "module_type_profile",
              "module_bay_type", "rack_group", "cable_bundle", "virtual_machine_type",
              "custom_field", "custom_field_choice_set", "custom_link", "owner", "owner_group"):
    _IDENTITY[_kind] = (("name",), ())
_IDENTITY["vlan_group"] = (("name",), ("scope_site", "scope_location"))
_IDENTITY["module_bay_type"] = (("name",), ("manufacturer",))
_IDENTITY["platform"] = (("name",), ("manufacturer",))
# Nonoptional SDK scalars validate even in nested identity messages. Include
# their actual values, never dummy defaults; the remaining payload stays thin.
for _kind, _fields in {
    "ike_policy": ("version",),
    "ike_proposal": ("authentication_method", "encryption_algorithm", "group"),
    "ip_sec_profile": ("mode",), "tunnel": ("status", "encapsulation"),
    "tunnel_termination": ("role",), "virtual_device_context": ("status",),
    "custom_field": ("type",), "cooling_source": ("type",),
}.items():
    _attrs, _refs = _IDENTITY[_kind]
    _IDENTITY[_kind] = (_attrs + _fields, _refs)

_GENERIC_REFS = {
    ("ip_address", "assigned_object"): {"interface", "vm_interface", "fhrp_group"},
    ("mac_address", "assigned_object"): {"interface", "vm_interface"},
    ("circuit_termination", "termination"): {"site", "provider_network"},
    ("fhrp_group_assignment", "interface"): {"interface", "vm_interface"},
    ("l2vpn_termination", "assigned_object"): {"interface", "vlan", "vm_interface"},
    ("tunnel_termination", "termination"): {"interface", "vm_interface"},
    ("circuit_group_assignment", "member"): {"circuit", "virtual_circuit"},
    ("inventory_item", "component"): _PORTS | {"cooling_intake", "cooling_outflow"},
    ("contact_assignment", "object"): None,
    ("journal_entry", "assigned_object"): None,
}


def _deferred_fields(obj):
    return _DEFERRED.get(obj["kind"], set())


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("recipe.as_of must be an ISO date or timestamp")
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(),
                                      timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp must include a timezone")
    except ValueError as exc:
        raise ValueError(f"invalid observation date/timestamp {value!r}: {exc}") from exc
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _keys(ref):
    return ref if isinstance(ref, list) else [ref]


def _index(plan):
    if plan.get("schema_version") != 1:
        raise ValueError("export requires plan schema_version 1")
    objects = {}
    for obj in plan["objects"]:
        key = obj["key"]
        if not isinstance(key, str) or not key or key in objects:
            raise ValueError(f"invalid or duplicate canonical key: {key!r}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", obj["kind"]):
            raise ValueError(f"{key}: invalid Diode kind")
        if not isinstance(obj["attrs"], dict) or not isinstance(obj["refs"], dict):
            raise ValueError(f"{key}: attrs and refs must be dictionaries")
        overlap = obj["attrs"].keys() & obj["refs"].keys()
        if overlap:
            raise ValueError(f"{key}: fields occur in attrs and refs: {sorted(overlap)}")
        objects[key] = obj
    if not objects:
        raise ValueError("cannot export an empty estate")
    for key, obj in objects.items():
        for target in obj.get("meta", {}).get("requires", []):
            if target not in objects or objects[target]["kind"] != "custom_field":
                raise ValueError(f"{key}: requires must reference a custom_field definition")
        for field, ref in obj["refs"].items():
            for target in _keys(ref):
                if not isinstance(target, str) or target not in objects:
                    raise ValueError(f"{key}.{field}: unresolved canonical reference {target!r}")
            if field in _PRIMARY_IPS:
                if isinstance(ref, list) or objects[ref]["kind"] != "ip_address":
                    raise ValueError(f"{key}.{field}: expected one ip_address")
    return objects


def _phases(objects):
    dependencies = {}
    dependents = defaultdict(list)
    for key, obj in objects.items():
        deps = {target for field, ref in obj["refs"].items()
                if field not in _deferred_fields(obj) for target in _keys(ref)}
        deps.update(obj.get("meta", {}).get("requires", []))
        dependencies[key] = len(deps)
        for target in deps:
            dependents[target].append(key)
    ready = sorted(key for key, count in dependencies.items() if not count)
    phases = []
    seen = 0
    while ready:
        phases.append(ready)
        seen += len(ready)
        following = []
        for key in ready:
            for dependent in dependents[key]:
                dependencies[dependent] -= 1
                if not dependencies[dependent]:
                    following.append(dependent)
        ready = sorted(following)
    if seen != len(objects):
        cycle = sorted(key for key, count in dependencies.items() if count)
        raise ValueError(f"non-primary dependency cycle: {', '.join(cycle[:8])}")
    return phases


class _References:
    def __init__(self, objects):
        self.objects = objects
        self.cache = {}
        self.active = set()

    def thin(self, key):
        if key in self.cache:
            return self.cache[key]
        if key in self.active:
            raise ValueError(f"identity reference cycle at {key}")
        self.active.add(key)
        obj = self.objects[key]
        kind, attrs, refs = obj["kind"], obj["attrs"], obj["refs"]
        if kind == "vrf":
            fields, related = (("rd",), ()) if attrs.get("rd") else (("name",), ("tenant",))
        elif kind == "vlan":
            fields = ("vid",)
            related = ("group",) if "group" in refs else ("site",)
        elif kind == "cable":
            fields, related = (), ("a", "b")
        elif kind in _IDENTITY:
            fields, related = _IDENTITY[kind]
        else:
            raise ValueError(f"{key}: no supported thin-reference identity for {kind}")
        data = {field: attrs[field] for field in fields if field in attrs}
        if fields and fields[0] not in data:
            raise ValueError(f"{key}: identity requires attribute {fields[0]}")
        for field in related:
            if field in refs:
                name, value = self.field(obj, field, refs[field])
                data[name] = value
        self.active.remove(key)
        self.cache[key] = data
        return data

    def field(self, obj, field, ref):
        kind = obj["kind"]
        if kind == "cable" and field in {"a", "b"}:
            if isinstance(ref, list) or self.objects[ref]["kind"] not in _TERMINATIONS:
                raise ValueError(f"{obj['key']}.{field}: expected one cable termination")
            target_kind = self.objects[ref]["kind"]
            return f"{field}_terminations", [{f"object_{target_kind}": self.thin(ref)}]
        if (kind, field) in _GENERIC_REFS:
            allowed = _GENERIC_REFS[kind, field]
            if isinstance(ref, list) or (allowed is not None and self.objects[ref]["kind"] not in allowed):
                raise ValueError(f"{obj['key']}.{field}: invalid generic reference type")
            return f"{field}_{self.objects[ref]['kind']}", self.thin(ref)
        value = [self.thin(key) for key in ref] if isinstance(ref, list) else self.thin(ref)
        return field, value

    def entity(self, key, timestamp, primary=False):
        obj = self.objects[key]
        if primary:
            data = dict(self.thin(key))
        else:
            data = {name: _timestamp(value) if name in _DATE_FIELDS else value
                    for name, value in obj["attrs"].items()}
        if obj["kind"] == "contact_group" and "parent" not in obj["refs"]:
            # Plugin 1.17's root ContactGroup has no parent/name matcher.
            # Explicit slug suppresses AutoSlugMatcher and repeated ingestion
            # inserts duplicates. Let native _set_auto_slugs enable that matcher.
            # Source: diode-netbox-plugin v1.17.0 api/{matcher,transformer}.py.
            # This is Django's default ASCII slugify; preserve canonical intent.
            name = unicodedata.normalize("NFKD", data["name"]).encode("ascii", "ignore").decode()
            expected = re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", name.lower())).strip("-_")
            if "slug" in data and data["slug"] != expected:
                raise ValueError(f"{key}: root ContactGroup slug must equal its name-derived slug for plugin 1.17 matching")
            data.pop("slug", None)
        for field, ref in obj["refs"].items():
            if (field in _deferred_fields(obj)) == primary:
                name, value = self.field(obj, field, ref)
                data[name] = value
        return {"timestamp": timestamp, obj["kind"]: data}


def export(plan, output_dir):
    """Write a new/empty directory of deterministic requests; return its manifest.

    JSON byte size bounds protobuf size conservatively for this scalar/object
    format. Optional SDK verification additionally parses each complete request
    and checks its actual ByteSize(); no SDK or network is required here.
    """
    objects = _index(plan)
    phases = [("create", keys) for keys in _phases(objects)]
    deferred = sorted(key for key, obj in objects.items() if _deferred_fields(obj) & obj["refs"].keys())
    if deferred:
        purpose = "primary-addresses" if all(
            (_deferred_fields(objects[key]) & objects[key]["refs"].keys()) <= _PRIMARY_IPS
            for key in deferred) else "back-references"
        phases.append((purpose, deferred))
    timestamp = _timestamp(plan["recipe"]["as_of"])
    version = plan["generator_version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("generator_version must be a numeric major.minor.patch version")
    estate_hash = _sha(_json(sorted(objects.values(), key=lambda obj: obj["key"])))
    plan_hash = _sha(_json(plan))
    manifest = {
        "format_version": 1, "diode_sdk_schema": SDK_VERSION,
        "generator_version": version, "as_of": timestamp,
        "estate_sha256": estate_hash, "plan_sha256": plan_hash,
        "canonical_records": len(objects), "ingestion_entities": len(objects) + len(deferred),
        "counts": dict(sorted(Counter(obj["kind"] for obj in objects.values()).items())),
        "external_references": [{"kind": obj["kind"], "identity": obj["attrs"]}
                                for obj in objects.values() if obj.get("meta", {}).get("external")],
        "limits": {"entities_per_request": MAX_ENTITIES, "json_bytes_per_request": MAX_REQUEST_BYTES},
        "phases": [], "files": [],
        "replay_notes": [
            "Replay preserves these synthetic observation timestamps.",
            "Wait for successful reconciliation of each phase before submitting the next.",
            "Ingest acceptance does not establish applied NetBox state; verify live results.",
            "Replaying a baseline may reverse an active demo scenario; omission does not delete objects.",
        ],
    }
    legacy_mapping = any(obj["kind"] == "front_port" and "rear_port" in obj["refs"]
                         for obj in objects.values())
    expanded = tuple(map(int, version.split("."))) >= (0, 7, 0)
    manifest["source_checked_target"] = {
        "netbox": "4.4.10" if legacy_mapping and not expanded else "4.7.0",
        "diode_sdk": SDK_VERSION, "diode_netbox_plugin": "1.17.0", "live_verified": False,
    }
    manifest["known_incompatible_netbox"] = [">=4.5.0"] if legacy_mapping else []
    manifest["compatibility_notes"] = [
        "The target is checked against tagged source; SDK validation does not establish live NetBox acceptance."
    ]
    manifest["compatibility_sources"] = [
        "https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/sdk/ingester.py",
        "https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/api/supported_models.py",
    ]
    if legacy_mapping:
        if expanded:
            manifest["local_compatibility_required"] = ["front_port_mapping"]
        manifest["compatibility_notes"].append(
            "This estate uses legacy front_port.rear_port mappings supported by the NetBox 4.4.10 model. "
            "NetBox 4.5.0+ uses PortMapping/rear_ports, which SDK 1.14.0 and plugin 1.17.0 cannot ingest. "
            "Do not replay this artifact on those versions: the plugin ignores the old mapping fields "
            "and the resulting passive cable paths are incomplete."
        )
        if expanded:
            manifest["compatibility_notes"].append(
                "Expanded models are source-checked only on NetBox 4.7.0. The whole package has no "
                "4.4.10 compatibility claim; panel qualification requires the explicit local front-port bridge."
            )
        manifest["compatibility_sources"].extend([
            "https://github.com/netbox-community/netbox/blob/v4.4.10/netbox/dcim/models/device_components.py",
            "https://github.com/netbox-community/netbox/blob/v4.5.0/netbox/dcim/api/serializers_/device_components.py",
            "https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/api/compat.py",
            "https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/api/transformer.py",
        ])
    output = Path(output_dir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"export destination must be new or empty: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".diode-export-", dir=output.parent))
    references = _References(objects)
    try:
        for phase_number, (purpose, keys) in enumerate(phases, 1):
            phase = {"phase": phase_number, "purpose": purpose,
                     "requires_completed_phases": list(range(1, phase_number)), "files": []}
            batch = []
            batch_bytes = 0
            part = 1

            def envelope(part_number):
                return {"stream": "latest", "entities": [],
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                            f"devin-generator:{plan_hash}:{phase_number}:{part_number}")),
                        "producer_app_name": "devin-generator", "producer_app_version": version,
                        "sdk_name": "devin-generator", "sdk_version": version}

            def flush():
                request = envelope(part)
                request["entities"] = batch
                payload = _json(request) + b"\n"
                if len(payload) > MAX_REQUEST_BYTES or len(batch) > MAX_ENTITIES:
                    raise ValueError("internal error: request exceeds export bounds")
                filename = f"phase-{phase_number:03d}-part-{part:04d}.json"
                (stage / filename).write_bytes(payload)
                manifest["files"].append({"path": filename, "sha256": _sha(payload),
                                          "bytes": len(payload), "entities": len(batch)})
                phase["files"].append(filename)

            overhead = len(_json(envelope(part))) + 1
            for key in keys:
                entity = references.entity(key, timestamp, purpose in {"primary-addresses", "back-references"})
                size = len(_json(entity))
                if size + overhead > MAX_REQUEST_BYTES:
                    raise ValueError(f"{key}: one entity exceeds {MAX_REQUEST_BYTES} JSON bytes")
                if batch and (len(batch) == MAX_ENTITIES or
                              overhead + batch_bytes + len(batch) + size > MAX_REQUEST_BYTES):
                    flush()
                    part += 1
                    batch, batch_bytes = [], 0
                batch.append(entity)
                batch_bytes += size
            if batch:
                flush()
            manifest["phases"].append(phase)
        (stage / "manifest.json").write_bytes(_json(manifest) + b"\n")
        if output.exists():
            output.rmdir()  # Empty-only: a concurrent writer prevents replacement.
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest


def _descriptor_rules(message, rules_extension, now, path):
    """Check precisely the PGV rule families present in the pinned SDK schema.

    ParseDict alone never evaluates validate.rules. These rules are read from
    the upstream descriptor, not copied enum lists. Unknown rules fail closed.
    Also reject the source- and live-proven root ContactGroup matching pitfall.
    """
    present = {field.name for field, _ in message.ListFields()}
    # SDK-valid historical output can still trigger this proven plugin matcher
    # failure. Reject it here, before any replay submits the original artifact.
    if (message.DESCRIPTOR.full_name == "diode.v1.ContactGroup" and
            "parent" not in present and "slug" in present):
        raise ValueError(f"{path}: root ContactGroup explicit slug duplicates records with plugin 1.17; regenerate this artifact with the current generator")

    def check(value, rules, where):
        for family, limits in rules.ListFields():
            for rule, expected in limits.ListFields():
                name = rule.name
                valid = True
                if name == "in":
                    valid = value in expected
                elif name in {"min_len", "min_items"}:
                    valid = len(value) >= expected
                elif name in {"max_len", "max_items"}:
                    valid = len(value) <= expected
                elif name == "pattern":
                    valid = re.search(expected, value) is not None
                elif name == "uuid":
                    valid = bool(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value))
                elif name == "required":
                    valid = value is not None
                elif name == "lt_now":
                    valid = value is not None and value.ToDatetime(tzinfo=timezone.utc) < now
                elif name == "items":
                    for i, item in enumerate(value):
                        check(item, expected, f"{where}[{i}]")
                else:
                    raise ValueError(f"{where}: unsupported SDK descriptor rule {family.name}.{name}")
                if not valid:
                    raise ValueError(f"{where}: violates SDK {family.name}.{name} rule")

    for field in message.DESCRIPTOR.fields:
        value = getattr(message, field.name)
        supplied = field.name in present
        if field.has_presence and not supplied:
            value = None
        options = field.GetOptions()
        if options.HasExtension(rules_extension):
            rules = options.Extensions[rules_extension]
            # Absent optional scalar fields have no assertion to validate.
            if supplied or not field.has_presence or field.message_type:
                check(value, rules, f"{path}.{field.name}")
        if field.message_type and supplied:
            if field.message_type.GetOptions().map_entry:
                if field.message_type.fields_by_name["value"].message_type:
                    for key, item in value.items():
                        _descriptor_rules(item, rules_extension, now, f"{path}.{field.name}[{key!r}]")
            elif field.is_repeated:
                for i, item in enumerate(value):
                    _descriptor_rules(item, rules_extension, now, f"{path}.{field.name}[{i}]")
            else:
                _descriptor_rules(value, rules_extension, now, f"{path}.{field.name}")


def verify_export(output_dir):
    """Optionally check an export with exact SDK 1.14.0; never contact NetBox.

    Checks checksums, envelope/actual protobuf bounds, types, and all descriptor
    rules present in this pinned schema. NetBox model validation, reference
    matching, and completed ingestion still require live qualification.
    """
    from importlib.metadata import version
    from google.protobuf.json_format import ParseDict
    from netboxlabs.diode.sdk.diode.v1 import ingester_pb2
    from netboxlabs.diode.sdk.validate import validate_pb2

    installed = version("netboxlabs-diode-sdk")
    if installed != SDK_VERSION:
        raise ValueError(f"SDK verification requires {SDK_VERSION}; installed {installed}")
    directory = Path(output_dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    now = datetime.now(timezone.utc)
    total = 0
    maximum = 0
    seen = set()
    for entry in manifest["files"]:
        name = entry["path"]
        if not re.fullmatch(r"phase-\d+-part-\d+\.json", name) or name in seen:
            raise ValueError(f"invalid or duplicate request filename: {name!r}")
        seen.add(name)
        payload = (directory / name).read_bytes()
        if len(payload) != entry["bytes"] or _sha(payload) != entry["sha256"]:
            raise ValueError(f"{name}: manifest checksum/size mismatch")
        request = ParseDict(json.loads(payload), ingester_pb2.IngestRequest())
        _descriptor_rules(request, validate_pb2.rules, now, name)
        size = request.ByteSize()
        if len(payload) > MAX_REQUEST_BYTES or size > MAX_REQUEST_BYTES:
            raise ValueError(f"{name}: JSON or protobuf request exceeds byte limit")
        if len(request.entities) != entry["entities"]:
            raise ValueError(f"{name}: manifest entity count mismatch")
        total += len(request.entities)
        maximum = max(maximum, size)
    if total != manifest["ingestion_entities"]:
        raise ValueError("manifest ingestion entity count mismatch")
    return {"sdk_version": installed, "requests": len(seen), "entities": total,
            "maximum_protobuf_bytes": maximum, "descriptor_rules_checked": True,
            "live_ingestion_verified": False,
            "source_checked_target": manifest["source_checked_target"],
            "known_incompatible_netbox": manifest["known_incompatible_netbox"],
            "compatibility_notes": manifest["compatibility_notes"],
            "compatibility_sources": manifest["compatibility_sources"]}
