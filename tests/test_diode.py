"""Wire/recovery checks; optional SDK parse uses upstream v1.14.0, never a server."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from estates.diode import MAX_ENTITIES, MAX_REQUEST_BYTES, export, verify_export

try:
    from google.protobuf.json_format import ParseDict
    from netboxlabs.diode.sdk import load_dryrun_entities
    from netboxlabs.diode.sdk.diode.v1 import ingester_pb2
except ImportError:
    ParseDict = None


def record(key, kind, attrs, refs=None):
    return {"key": key, "kind": kind, "attrs": attrs, "refs": refs or {}, "meta": {}}


def plan(objects):
    return {"schema_version": 1, "generator_version": "0.1.0",
            "recipe": {"as_of": "2026-09-01"}, "hardware_digest": "fixture",
            "allocations": {}, "objects": objects, "contracts": []}


def connected_plan():
    return plan([
        record("site", "site", {"name": "DC1", "slug": "dc1", "status": "active"}),
        record("tenant", "tenant", {"name": "Bank", "slug": "bank"}),
        record("location", "location", {"name": "Hall A", "slug": "hall-a"}, {"site": "site"}),
        record("rack", "rack", {"name": "R01", "u_height": 42, "width": 19,
                                 "asset_tag": "bank-dc1-r01"}, {"site": "site", "location": "location"}),
        record("maker", "manufacturer", {"name": "Example", "slug": "example"}),
        record("type", "device_type", {"model": "Example switch", "slug": "example-switch", "u_height": 1},
               {"manufacturer": "maker"}),
        record("role", "device_role", {"name": "Access", "slug": "access", "color": "008080"}),
        record("switch", "device", {"name": "dc1-asw01", "position": 40, "face": "front"},
               {"site": "site", "tenant": "tenant", "rack": "rack", "device_type": "type",
                "role": "role", "primary_ip4": "mgmt-ip"}),
        record("port", "interface", {"name": "Ethernet1", "type": "1000base-t", "enabled": True},
               {"device": "switch"}),
        record("vrf", "vrf", {"name": "Management", "rd": "64512:10"}, {"tenant": "tenant"}),
        record("prefix", "prefix", {"prefix": "10.1.0.0/24", "status": "active"},
               {"scope_site": "site", "vrf": "vrf"}),
        record("mgmt-ip", "ip_address", {"address": "10.1.0.2/24", "status": "active"},
               {"vrf": "vrf", "assigned_object": "port"}),
        record("provider", "provider", {"name": "Example Carrier", "slug": "example-carrier"}),
        record("circuit-type", "circuit_type", {"name": "DIA", "slug": "dia"}),
        record("provider-network", "provider_network", {"name": "National IP"}, {"provider": "provider"}),
        record("circuit", "circuit", {"cid": "DIA-001", "status": "active", "commit_rate": 100000,
                                       "install_date": "2025-04-01"},
               {"provider": "provider", "type": "circuit-type"}),
        record("circuit-a", "circuit_termination", {"term_side": "A", "port_speed": 1000000},
               {"circuit": "circuit", "termination": "site"}),
        record("circuit-z", "circuit_termination", {"term_side": "Z", "port_speed": 1000000},
               {"circuit": "circuit", "termination": "provider-network"}),
        record("wan-cable", "cable", {"label": "WAN-001", "type": "cat6", "status": "connected"},
               {"a": "port", "b": "circuit-a"}),
    ])


class DiodeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def requests(self, directory, manifest):
        return [json.loads((directory / entry["path"]).read_text()) for entry in manifest["files"]]

    def test_root_contact_group_uses_native_autoslug_matching(self):
        # Plugin v1.17 matcher.py AutoSlugMatcher requires _auto_slug;
        # transformer.py _set_auto_slugs only adds it when slug is absent.
        # Root name/parent uniqueness otherwise cannot match an omitted parent.
        source = plan([
            record("root", "contact_group", {"name": "Bank Operations desk", "slug": "bank-operations-desk"}),
            record("child", "contact_group", {"name": "Escalations", "slug": "custom-child-slug"}, {"parent": "root"}),
            record("contact", "contact", {"name": "Bank duty team"}, {"groups": ["root", "child"]}),
        ])
        unchanged = deepcopy(source)
        directory = self.root / "contact-groups"
        manifest = export(source, directory)
        entities = [entity for request in self.requests(directory, manifest) for entity in request["entities"]]
        groups = [entity["contact_group"] for entity in entities if "contact_group" in entity]
        self.assertEqual(groups[0], {"name": "Bank Operations desk"})
        self.assertEqual(groups[1]["slug"], "custom-child-slug")
        self.assertEqual(groups[1]["parent"], {"name": "Bank Operations desk"})
        contact = next(entity["contact"] for entity in entities if "contact" in entity)
        self.assertEqual(contact["groups"][0], {"name": "Bank Operations desk"})
        self.assertEqual(contact["groups"][1]["parent"], {"name": "Bank Operations desk"})
        self.assertEqual(source, unchanged)
        if ParseDict:
            verify_export(directory)
        source["objects"][0]["attrs"]["slug"] = "different-slug"
        with self.assertRaisesRegex(ValueError, "name-derived slug"):
            export(source, self.root / "wrong-slug")

    def test_member_backreferences_and_definition_dependencies(self):
        source = connected_plan()
        objects = {obj["key"]: obj for obj in source["objects"]}
        objects["switch"]["refs"].update(virtual_chassis="vc")
        objects["port"]["refs"]["primary_mac_address"] = "mac"
        objects["site"]["meta"]["requires"] = ["cf"]
        source["objects"].extend([
            record("vc", "virtual_chassis", {"name": "Bank stack"}, {"master": "switch"}),
            record("mac", "mac_address", {"mac_address": "02:00:00:00:00:01"}, {"assigned_object": "port"}),
            record("cf", "custom_field", {"name": "bank_tier", "type": "text", "object_types": ["dcim.site"]}),
        ])
        directory = self.root / "references"
        manifest = export(source, directory)
        requests = self.requests(directory, manifest)
        entities = [entity for request in requests for entity in request["entities"]]
        self.assertEqual(manifest["phases"][-1]["purpose"], "back-references")
        chassis = [entity["virtual_chassis"] for entity in entities if "virtual_chassis" in entity]
        self.assertNotIn("master", chassis[0])
        self.assertEqual(chassis[1]["master"]["name"], "dc1-asw01")
        port = [entity["interface"] for entity in entities if "interface" in entity]
        self.assertNotIn("primary_mac_address", port[0])
        self.assertEqual(port[1]["primary_mac_address"]["assigned_object_interface"]["name"], "Ethernet1")
        cf_phase = next(i for i, request in enumerate(requests) if any("custom_field" in e for e in request["entities"]))
        site_phase = next(i for i, request in enumerate(requests) if any("site" in e for e in request["entities"]))
        self.assertLess(cf_phase, site_phase)
        if ParseDict:
            verify_export(directory)

    def test_typed_refs_scoped_identity_and_primary_cycle(self):
        source = connected_plan()
        unchanged = deepcopy(source)
        directory = self.root / "export"
        manifest = export(source, directory)
        self.assertEqual(source, unchanged)
        self.assertEqual(manifest["as_of"], "2026-09-01T00:00:00Z")
        self.assertEqual(manifest["canonical_records"], len(source["objects"]))
        self.assertEqual(manifest["ingestion_entities"], len(source["objects"]) + 1)
        entities = [entity for req in self.requests(directory, manifest) for entity in req["entities"]]
        devices = [entity["device"] for entity in entities if "device" in entity]
        self.assertEqual(len(devices), 2)
        self.assertNotIn("primary_ip4", devices[0])
        self.assertNotIn("device_type", devices[1])
        self.assertEqual(devices[1]["primary_ip4"], {"address": "10.1.0.2/24", "vrf": {"rd": "64512:10"}})
        self.assertEqual(manifest["phases"][-1]["purpose"], "primary-addresses")
        cable = next(entity["cable"] for entity in entities if "cable" in entity)
        interface = cable["a_terminations"][0]["object_interface"]
        self.assertEqual(interface["name"], "Ethernet1")
        self.assertEqual(interface["type"], "1000base-t")
        self.assertEqual(interface["device"]["site"], {"name": "DC1"})
        self.assertEqual(interface["device"]["tenant"], {"name": "Bank"})
        self.assertNotIn("rack", interface["device"])
        circuit_end = cable["b_terminations"][0]["object_circuit_termination"]
        self.assertEqual(circuit_end, {"term_side": "A", "circuit": {
            "cid": "DIA-001", "provider": {"name": "Example Carrier"}}})
        ip = next(entity["ip_address"] for entity in entities if "ip_address" in entity)
        self.assertIn("assigned_object_interface", ip)
        circuit = next(entity["circuit"] for entity in entities if "circuit" in entity)
        self.assertEqual(circuit["install_date"], "2025-04-01T00:00:00Z")

    def test_both_limits_checksums_and_determinism(self):
        # Count bound is independent of byte bound: 1001 tiny sites must split.
        source = plan([record(f"s{i:04d}", "site", {"name": f"S{i}"}) for i in range(MAX_ENTITIES + 1)])
        first, second = self.root / "first", self.root / "second"
        manifest = export(source, first)
        self.assertEqual(export(source, second), manifest)
        self.assertEqual([entry["entities"] for entry in manifest["files"]], [1000, 1])
        for path in first.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
        for entry in manifest["files"]:
            payload = (first / entry["path"]).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(entry["bytes"], len(payload))
            self.assertLessEqual(len(payload), MAX_REQUEST_BYTES)
        # UTF-8 bytes, not Python character count, govern large requests.
        large = plan([record(f"s{i}", "site", {"name": f"S{i}", "comments": "é" * 650_000})
                      for i in range(2)])
        large_manifest = export(large, self.root / "large")
        self.assertEqual([entry["entities"] for entry in large_manifest["files"]], [1, 1])

    def test_bad_references_cycles_and_oversize_leave_no_partial_export(self):
        broken = plan([record("a", "site", {"name": "A"}, {"region": "missing"})])
        with self.assertRaisesRegex(ValueError, "unresolved canonical reference"):
            export(broken, self.root / "broken")
        cyclic = plan([record("a", "region", {"name": "A"}, {"parent": "b"}),
                       record("b", "region", {"name": "B"}, {"parent": "a"})])
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            export(cyclic, self.root / "cyclic")
        huge = plan([record("a", "site", {"name": "A"}),
                     record("b", "site", {"name": "B", "comments": "x" * MAX_REQUEST_BYTES})])
        with self.assertRaisesRegex(ValueError, "one entity exceeds"):
            export(huge, self.root / "huge")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_directory_is_not_replaced(self):
        directory = self.root / "existing"
        directory.mkdir()
        (directory / "user-file").write_text("keep")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            export(connected_plan(), directory)
        self.assertEqual((directory / "user-file").read_text(), "keep")

    def test_passive_mapping_records_explicit_legacy_target_without_rewriting(self):
        source = connected_plan()
        modern = export(source, self.root / "modern")
        self.assertEqual(modern["source_checked_target"]["netbox"], "4.7.0")
        self.assertEqual(modern["known_incompatible_netbox"], [])
        source["objects"].extend([
            record("rear", "rear_port", {"name": "Rear 1", "type": "8p8c", "positions": 1},
                   {"device": "switch"}),
            record("front", "front_port", {"name": "Front 1", "type": "8p8c", "rear_port_position": 1},
                   {"device": "switch", "rear_port": "rear"}),
        ])
        directory = self.root / "legacy"
        manifest = export(source, directory)
        self.assertEqual(manifest["source_checked_target"]["netbox"], "4.4.10")
        self.assertEqual(manifest["known_incompatible_netbox"], [">=4.5.0"])
        self.assertFalse(manifest["source_checked_target"]["live_verified"])
        self.assertTrue(any("PortMapping/rear_ports" in note for note in manifest["compatibility_notes"]))
        entities = [entity for req in self.requests(directory, manifest) for entity in req["entities"]]
        front = next(entity["front_port"] for entity in entities if "front_port" in entity)
        self.assertEqual(front["rear_port"]["name"], "Rear 1")
        self.assertEqual(front["rear_port_position"], 1)

    @unittest.skipUnless(ParseDict is not None, "optional netboxlabs-diode-sdk is not installed")
    def test_sdk_check_rejects_old_root_group_payload_including_nested_refs(self):
        source = plan([
            record("root", "contact_group", {"name": "Bank desk", "slug": "bank-desk"}),
            record("contact", "contact", {"name": "Bank operator"}, {"groups": ["root"]}),
        ])
        for nested in (False, True):
            with self.subTest(nested=nested):
                directory = self.root / ("nested" if nested else "top-level")
                manifest = export(source, directory)
                for entry in manifest["files"]:
                    path = directory / entry["path"]
                    request = json.loads(path.read_text())
                    matched = [entity for entity in request["entities"] if ("contact" if nested else "contact_group") in entity]
                    if not matched:
                        continue
                    group = matched[0]["contact"]["groups"][0] if nested else matched[0]["contact_group"]
                    group["slug"] = "bank-desk"
                    payload = json.dumps(request).encode()
                    path.write_bytes(payload)
                    entry.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                    break
                (directory / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "root ContactGroup.*regenerate"):
                    verify_export(directory)

    @unittest.skipUnless(ParseDict is not None, "optional netboxlabs-diode-sdk is not installed")
    def test_upstream_sdk_parses_every_request_and_actual_protobuf_bound(self):
        # https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/sdk/client.py#L66
        directory = self.root / "sdk"
        manifest = export(connected_plan(), directory)
        receipt = verify_export(directory)
        self.assertEqual(receipt["entities"], manifest["ingestion_entities"])
        self.assertTrue(receipt["descriptor_rules_checked"])
        self.assertFalse(receipt["live_ingestion_verified"])
        self.assertEqual(receipt["source_checked_target"], manifest["source_checked_target"])
        for entry in manifest["files"]:
            path = directory / entry["path"]
            self.assertEqual(len(list(load_dryrun_entities(path))), entry["entities"])
            request = ParseDict(json.loads(path.read_text()), ingester_pb2.IngestRequest())
            self.assertLessEqual(request.ByteSize(), MAX_REQUEST_BYTES)

    @unittest.skipUnless(ParseDict is not None, "optional netboxlabs-diode-sdk is not installed")
    def test_descriptor_rules_reject_wire_valid_enum_and_integer_values(self):
        # These all parse as protobuf, but violate the upstream PGV descriptor.
        for key, field, value, family in (("wan-cable", "type", "not-a-cable", "string.in"),
                                          ("rack", "width", 18, "int64.in"),
                                          ("port", "type", "not-an-interface", "string.in")):
            with self.subTest(key=key):
                source = connected_plan()
                next(obj for obj in source["objects"] if obj["key"] == key)["attrs"][field] = value
                directory = self.root / key
                export(source, directory)
                with self.assertRaisesRegex(ValueError, f"SDK {family} rule"):
                    verify_export(directory)

    @unittest.skipUnless(ParseDict is not None, "optional netboxlabs-diode-sdk is not installed")
    def test_required_timestamp_and_request_count_rules(self):
        for mutation in ("timestamp", "entities"):
            with self.subTest(mutation=mutation):
                directory = self.root / mutation
                manifest = export(plan([record("site", "site", {"name": "DC1"})]), directory)
                entry = manifest["files"][0]
                path = directory / entry["path"]
                request = json.loads(path.read_text())
                if mutation == "timestamp":
                    request["entities"][0].pop("timestamp")
                else:
                    request["entities"] = []
                payload = json.dumps(request).encode()
                path.write_bytes(payload)
                entry.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                (directory / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "SDK (timestamp.required|repeated.min_items) rule"):
                    verify_export(directory)


if __name__ == "__main__":
    unittest.main()
