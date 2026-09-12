"""Independent small REST shapes: distinguish normalization from lost intent."""

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lab.verify import ENDPOINTS, fetch_inventory, main, verify_plan


def fixture():
    def obj(key, kind, attrs, refs=None):
        return {"key": key, "kind": kind, "attrs": attrs, "refs": refs or {}, "meta": {}}
    plan = {"objects": [
        obj("s", "site", {"name": "Bank DC", "status": "active"}),
        obj("c", "cluster", {"name": "Compute"}, {"scope_site": "s"}),
        obj("v", "vlan", {"vid": 10, "name": "Server"}, {"site": "s"}),
        obj("vm", "virtual_machine", {"name": "Ledger", "disk": 500000, "vcpus": 4},
            {"cluster": "c", "primary_ip4": "ip"}),
        obj("p", "vm_interface", {"name": "eth0", "enabled": True, "mode": "access"},
            {"virtual_machine": "vm", "untagged_vlan": "v"}),
        obj("ip", "ip_address", {"address": "10.0.0.2/24"}, {"assigned_object": "p"}),
        obj("svc", "service", {"name": "DNS", "protocol": "udp", "ports": [53]},
            {"virtual_machine": "vm"}),
    ]}
    inventory = {
        "site": [{"id": 1, "name": "Bank DC", "status": {"value": "active", "label": "Active"}}],
        "cluster": [{"id": 2, "name": "Compute", "scope_type": "dcim.site", "scope_id": 1, "group": None}],
        "vlan": [{"id": 3, "vid": 10, "name": "Server", "site": {"id": 1}, "group": None}],
        "virtual_machine": [{"id": 4, "name": "Ledger", "disk": 500000, "vcpus": "4.00",
                             "cluster": {"id": 2}, "tenant": None, "primary_ip4": {"id": 6}}],
        "vm_interface": [{"id": 5, "name": "eth0", "enabled": True,
                          "mode": {"value": "access", "label": "Access"},
                          "virtual_machine": {"id": 4}, "untagged_vlan": {"id": 3}}],
        "ip_address": [{"id": 6, "address": "10.0.0.2/24", "vrf": None,
                        "assigned_object_type": "virtualization.vminterface", "assigned_object_id": 5}],
        "service": [{"id": 7, "name": "DNS", "port_mappings": ["udp/53"],
                     "parent_object_type": "virtualization.virtualmachine", "parent_object_id": 4}],
    }
    return plan, inventory


class LiveReadbackTests(unittest.TestCase):
    @patch("lab.verify.time.sleep")
    @patch("lab.verify.urllib.request.build_opener")
    def test_inventory_retries_transient_read_disconnect(self, build_opener, sleep):
        page = io.BytesIO(json.dumps({"count": 0, "next": None, "results": []}).encode())
        opener = Mock()
        opener.open.side_effect = [ConnectionResetError(), page]
        build_opener.return_value = opener

        self.assertEqual(fetch_inventory("https://netbox.example", "token", ["site"]),
                         {"site": []})
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_bootstrap_cli_checks_all_endpoints_and_produces_usable_allowlist(self):
        inventory = {kind: [] for kind in ENDPOINTS}
        inventory.update(user=[{"id": 8, "username": "admin"}, {"id": 3, "username": "diode"}],
                         module_type_profile=[{"id": i, "name": name} for i, name in enumerate(
                             ["CPU", "Fan", "GPU", "Hard disk", "Memory", "Power supply", "Expansion card"], 10)])
        before = deepcopy(inventory)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap.json"
            with patch("lab.verify.fetch_inventory", return_value=inventory) as fetch, redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--bootstrap", "--url", "http://localhost:8000", "--receipt", str(path)]), 0)
            self.assertEqual(set(fetch.call_args.args[2]), set(ENDPOINTS))
            receipt = json.loads(path.read_text())
            self.assertTrue(receipt["strict_inventory"])
            self.assertEqual(receipt["matched_objects"], 9)
            self.assertEqual(receipt["target_ids"]["user"], [3, 8])
            plan, loaded = fixture()
            loaded.update({kind: inventory[kind] for kind in ("user", "module_type_profile")})
            self.assertTrue(verify_plan(plan, loaded, strict_inventory=True, allow_existing_receipt=receipt)["success"])
        self.assertEqual(inventory, before)

    def test_bootstrap_rejects_missing_duplicate_and_extra_records(self):
        base = {"user": [{"id": 1, "username": "admin"}, {"id": 2, "username": "diode"}],
                "module_type_profile": [{"id": i, "name": name} for i, name in enumerate(
                    ["CPU", "Fan", "GPU", "Hard disk", "Memory", "Power supply", "Expansion card"], 1)]}
        cases = []
        missing = deepcopy(base)
        missing["module_type_profile"].pop()
        cases.append(missing)
        for kind, record in (("user", {"id": 99, "username": "customer"}),
                             ("module_type_profile", {"id": 99, "name": "CPU"}),
                             ("site", {"id": 99, "name": "Existing estate"})):
            extra = deepcopy(base)
            extra.setdefault(kind, []).append(record)
            cases.append(extra)
        with tempfile.TemporaryDirectory() as temporary:
            for number, inventory in enumerate(cases):
                with self.subTest(number=number):
                    path = Path(temporary) / f"failed-{number}.json"
                    with patch("lab.verify.fetch_inventory", return_value=inventory), redirect_stdout(io.StringIO()):
                        self.assertEqual(main(["--bootstrap", "--url", "http://localhost:8000", "--receipt", str(path)]), 1)
                    receipt = json.loads(path.read_text())
                    self.assertFalse(receipt["success"])
                    with self.assertRaisesRegex(ValueError, "successful"):
                        verify_plan(*fixture(), strict_inventory=True, allow_existing_receipt=receipt)

    def test_bootstrap_rejects_conflicting_inputs_and_existing_receipt_before_gets(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            args = ["--url", "http://localhost:8000", "--receipt", str(path)]
            for conflict in ([], ["--bootstrap", "plan.json"],
                             ["--bootstrap", "--previous-receipt", "prior.json"],
                             ["--bootstrap", "--allow-existing-receipt", "prior.json"]):
                with patch("lab.verify.fetch_inventory") as fetch, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main(args + conflict)
                    fetch.assert_not_called()
                self.assertFalse(path.exists())
            path.write_text("preserve this evidence")
            with patch("lab.verify.fetch_inventory") as fetch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(args + ["--bootstrap"])
                fetch.assert_not_called()
            self.assertEqual(path.read_text(), "preserve this evidence")

    def test_dh_groups_require_native_integer_choice_shape_and_exact_value(self):
        plan = {"objects": [{"key": kind, "kind": kind, "attrs": {"name": kind, field: "14"}, "refs": {}}
                            for kind, field in (("ike_proposal", "group"), ("ip_sec_policy", "pfs_group"))]}
        inventory = {obj["kind"]: [{"id": number, "name": obj["kind"],
                                   next(field for field in obj["attrs"] if field != "name"):
                                   {"value": 14, "label": "Group 14"}}]
                     for number, obj in enumerate(plan["objects"], 1)}
        before = deepcopy((plan, inventory))
        result = verify_plan(plan, inventory, strict_inventory=True)
        self.assertTrue(result["success"], result["mismatches"])
        self.assertEqual(result["coverage"]["attributes_checked"], result["coverage"]["attributes_expected"])
        for kind, field in (("ike_proposal", "group"), ("ip_sec_policy", "pfs_group")):
            for value in ({"value": 15, "label": "Group 15"}, {"value": "14", "label": "Group 14"},
                          {"value": 14.0, "label": "Group 14"}, {"value": True, "label": "Group 14"},
                          {"value": 14}, {"id": 14, "label": "Group 14"},
                          {"value": 14, "label": "Group 14", "id": 14},
                          {"value": 14, "label": None}, 14, None):
                with self.subTest(kind=kind, value=value):
                    changed = deepcopy(inventory)
                    changed[kind][0][field] = value
                    result = verify_plan(plan, changed)
                    self.assertEqual([(item["object"], item["field"], item["code"]) for item in result["mismatches"]],
                                     [(kind, field, "attribute_mismatch")])
        self.assertEqual((plan, inventory), before)

    def test_custom_field_selection_normalization_checks_only_emitted_keys(self):
        fields = {"tier": {"selection": "tier-1"}, "services": {"multiple_selection": ["dns", "ledger"]},
                  "note": {"text": "authored note"}, "count": {"integer": 2}}
        plan = {"objects": [{"key": "site", "kind": "site", "attrs": {"name": "Site", "custom_fields": fields}, "refs": {}}]}
        inventory = {"site": [{"id": 1, "name": "Site", "custom_fields": {
            "tier": {"value": "tier-1", "label": "Tier 1"},
            "services": [{"value": "dns", "label": "DNS"}, {"value": "ledger", "label": "Ledger"}],
            "note": "authored note", "count": 2, "other_estate_field": None}}]}
        before = deepcopy((plan, inventory))
        self.assertTrue(verify_plan(plan, inventory, strict_inventory=True)["success"])
        cases = [("tier", value) for value in (
            {"value": "tier-2", "label": "Tier 2"}, {"value": 1, "label": "Tier 1"},
            {"value": "tier-1"}, {"value": "tier-1", "label": "Tier 1", "id": 2},
            {"value": "tier-1", "label": None}, "tier-1", None)]
        cases.extend(("services", value) for value in (
            ["dns", "ledger"], {"value": ["dns", "ledger"], "label": "Services"},
            [{"value": "dns", "label": "DNS"}],
            [{"value": "dns", "label": "DNS"}, {"value": "wrong", "label": "Ledger"}],
            [{"value": "dns", "label": "DNS"}, {"value": "ledger"}], None))
        cases.extend((("note", {"value": "authored note", "label": "Note"}), ("count", "2")))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                changed = deepcopy(inventory)
                changed["site"][0]["custom_fields"][field] = value
                result = verify_plan(plan, changed)
                self.assertEqual([(item["field"], item["code"]) for item in result["mismatches"]],
                                 [("custom_fields", "attribute_mismatch")])
        changed = deepcopy(inventory)
        del changed["site"][0]["custom_fields"]["tier"]
        self.assertFalse(verify_plan(plan, changed)["success"])
        changed = deepcopy(inventory)
        changed["site"][0]["custom_fields"]["other_estate_field"] = {"value": "other", "label": "Other"}
        self.assertTrue(verify_plan(plan, changed)["success"])
        self.assertEqual((plan, inventory), before)

    def test_native_panel_mapping_tuples_and_port_ids_survive_replay(self):
        plan = {"objects": [
            {"key": "site", "kind": "site", "attrs": {"name": "Site"}, "refs": {}},
            {"key": "device", "kind": "device", "attrs": {"name": "Panel"}, "refs": {"site": "site"}},
            {"key": "rear", "kind": "rear_port", "attrs": {"name": "R01", "positions": 1}, "refs": {"device": "device"}},
            {"key": "front", "kind": "front_port", "attrs": {"name": "F01", "rear_port_position": 1},
             "refs": {"device": "device", "rear_port": "rear"}},
        ]}
        inventory = {
            "site": [{"id": 1, "name": "Site"}],
            "device": [{"id": 2, "name": "Panel", "site": {"id": 1}, "tenant": None}],
            "rear_port": [{"id": 3, "name": "R01", "positions": 1, "device": {"id": 2}}],
            "front_port": [{"id": 4, "name": "F01", "positions": 1, "device": {"id": 2},
                            "rear_ports": [{"position": 1, "rear_port": 3, "rear_port_position": 1}]}],
        }
        before_inventory = deepcopy(inventory)
        first = verify_plan(plan, inventory, strict_inventory=True)
        self.assertTrue(first["success"], first["mismatches"])
        self.assertEqual(first["front_port_mappings"], {
            "front": {"front_port": 4, "rear_ports": [{"position": 1, "rear_port": 3, "rear_port_position": 1}]}})
        self.assertTrue(verify_plan(plan, inventory, first, True)["success"])
        self.assertEqual(inventory, before_inventory)
        self.assertEqual(first["coverage"]["attributes_checked"], first["coverage"]["attributes_expected"])
        self.assertEqual(first["coverage"]["references_checked"], first["coverage"]["references_expected"])
        for mapping in ([], None, [{"position": 1, "rear_port": 99, "rear_port_position": 1}],
                        [{"position": True, "rear_port": 3, "rear_port_position": 1}],
                        [{"position": 1, "rear_port": 3, "rear_port_position": 2}],
                        inventory["front_port"][0]["rear_ports"] * 2):
            with self.subTest(mapping=mapping):
                changed = deepcopy(inventory)
                changed["front_port"][0]["rear_ports"] = mapping
                result = verify_plan(plan, changed, first)
                self.assertFalse(result["success"])
                self.assertIn("front_port_mapping_changed", {f["code"] for f in result["mismatches"]})
        changed = deepcopy(inventory)
        changed["rear_port"][0]["id"] = 99
        changed["front_port"][0]["rear_ports"][0]["rear_port"] = 99
        result = verify_plan(plan, changed, first)
        self.assertIn("identity_changed", {f["code"] for f in result["mismatches"]})
        self.assertIn("front_port_mapping_changed", {f["code"] for f in result["mismatches"]})

    def test_known_rest_normalization_does_not_weaken_field_checks(self):
        plan, inventory = fixture()
        result = verify_plan(plan, inventory, strict_inventory=True)
        self.assertTrue(result["success"], result["mismatches"])
        self.assertEqual(result["coverage"]["attributes_expected"], result["coverage"]["attributes_checked"])
        self.assertEqual(result["coverage"]["references_expected"], result["coverage"]["references_checked"])
        for kind, field, value, code in [
            ("virtual_machine", "disk", 500, "attribute_mismatch"),
            ("vm_interface", "mode", {"value": "", "label": ""}, "attribute_mismatch"),
            ("vm_interface", "untagged_vlan", None, "reference_mismatch"),
            ("virtual_machine", "primary_ip4", None, "reference_mismatch"),
            ("ip_address", "assigned_object_id", 999, "reference_mismatch"),
            ("service", "port_mappings", ["tcp/53"], "attribute_mismatch"),
        ]:
            with self.subTest(kind=kind, field=field):
                changed = deepcopy(inventory)
                changed[kind][0][field] = value
                result = verify_plan(plan, changed)
                self.assertIn(code, {finding["code"] for finding in result["mismatches"]})
        del inventory["vm_interface"][0]["untagged_vlan"]
        self.assertIn("missing_api_field", {f["code"] for f in verify_plan(plan, inventory)["mismatches"]})

    def test_netbox_46_service_fields_are_checked_without_port_mappings(self):
        plan, inventory = fixture()
        inventory["service"][0].pop("port_mappings")
        inventory["service"][0].update(protocol={"value": "udp", "label": "UDP"}, ports=[53])
        self.assertTrue(verify_plan(plan, inventory, strict_inventory=True)["success"])
        inventory["service"][0]["ports"] = [54]
        self.assertIn("attribute_mismatch", {f["code"] for f in verify_plan(plan, inventory)["mismatches"]})

    def test_ambiguous_natural_identity_and_replay_growth_ids(self):
        plan, inventory = fixture()
        first = verify_plan(plan, inventory)
        self.assertTrue(verify_plan(plan, inventory, first)["success"])
        duplicate = deepcopy(inventory)
        duplicate["ip_address"].append(dict(duplicate["ip_address"][0], id=99))
        result = verify_plan(plan, duplicate, first)
        self.assertIn("ambiguous_identity", {f["code"] for f in result["mismatches"]})
        changed = deepcopy(inventory)
        changed["ip_address"][0]["id"] = 99
        changed["virtual_machine"][0]["primary_ip4"] = {"id": 99}
        result = verify_plan(plan, changed, first)
        self.assertIn("identity_changed", {f["code"] for f in result["mismatches"]})
        inventory["site"].append({"id": 100, "name": "Unexplained extra"})
        self.assertIn("unexpected_new_objects", {f["code"] for f in verify_plan(plan, inventory, first)["mismatches"]})
        plan["objects"].append({"key": "new", "kind": "site", "attrs": {"name": "Unexplained extra"}, "refs": {}})
        self.assertTrue(verify_plan(plan, inventory, first)["success"])

    def test_cable_and_power_references_are_exact(self):
        plan = {"objects": [
            {"key": "s", "kind": "site", "attrs": {"name": "Site"}, "refs": {}},
            {"key": "panel", "kind": "power_panel", "attrs": {"name": "A"}, "refs": {"site": "s"}},
            {"key": "feed", "kind": "power_feed", "attrs": {"name": "Feed"}, "refs": {"power_panel": "panel"}},
            {"key": "dev", "kind": "device", "attrs": {"name": "PDU", "asset_tag": "pdu-1"}, "refs": {"site": "s"}},
            {"key": "psu", "kind": "power_port", "attrs": {"name": "Input"}, "refs": {"device": "dev"}},
            {"key": "out", "kind": "power_outlet", "attrs": {"name": "1"}, "refs": {"device": "dev", "power_port": "psu"}},
            {"key": "cable", "kind": "cable", "attrs": {"length": 2}, "refs": {"a": "feed", "b": "psu"}},
        ]}
        inventory = {
            "site": [{"id": 1, "name": "Site"}],
            "power_panel": [{"id": 2, "name": "A", "site": {"id": 1}}],
            "power_feed": [{"id": 3, "name": "Feed", "power_panel": {"id": 2}}],
            "device": [{"id": 4, "name": "PDU", "asset_tag": "pdu-1", "site": {"id": 1}}],
            "power_port": [{"id": 5, "name": "Input", "device": {"id": 4}}],
            "power_outlet": [{"id": 6, "name": "1", "device": {"id": 4}, "power_port": {"id": 5}}],
            "cable": [{"id": 7, "length": "2.00", "a_terminations": [{"object_type": "dcim.powerfeed", "object_id": 3}],
                       "b_terminations": [{"object_type": "dcim.powerport", "object_id": 5}]}],
        }
        self.assertTrue(verify_plan(plan, inventory)["success"])
        inventory["power_outlet"][0]["power_port"] = None
        self.assertIn("reference_mismatch", {f["code"] for f in verify_plan(plan, inventory)["mismatches"]})
        inventory["cable"][0]["b_terminations"][0]["object_id"] = 99
        self.assertIn("missing_object", {f["code"] for f in verify_plan(plan, inventory)["mismatches"]})

    def test_hierarchy_platform_and_service_address_references(self):
        plan, inventory = fixture()
        additions = [
            ("country", "region", {"name": "Example Country"}, {}),
            ("metro", "region", {"name": "Metro"}, {"parent": "country"}),
            ("group", "site_group", {"name": "Branches"}, {}),
            ("subgroup", "site_group", {"name": "Inherited"}, {"parent": "group"}),
            ("maker", "manufacturer", {"name": "Example Maker"}, {}),
            ("os", "platform", {"name": "Example OS"}, {"manufacturer": "maker"}),
            ("rack-role", "rack_role", {"name": "Access"}, {}),
        ]
        for key, kind, attrs, refs in additions:
            plan["objects"].append({"key": key, "kind": kind, "attrs": attrs, "refs": refs})
        plan["objects"][0]["refs"].update(region="metro", group="subgroup")
        next(obj for obj in plan["objects"] if obj["key"] == "svc")["refs"]["ipaddresses"] = ["ip"]
        inventory.update({
            "region": [{"id": 10, "name": "Example Country", "parent": None},
                       {"id": 11, "name": "Metro", "parent": {"id": 10}},
                       {"id": 12, "name": "Metro", "parent": {"id": 99}}],
            "site_group": [{"id": 13, "name": "Branches", "parent": None},
                           {"id": 14, "name": "Inherited", "parent": {"id": 13}}],
            "manufacturer": [{"id": 15, "name": "Example Maker"}],
            "platform": [{"id": 16, "name": "Example OS", "manufacturer": {"id": 15}},
                         {"id": 17, "name": "Example OS", "manufacturer": {"id": 99}}],
            "rack_role": [{"id": 18, "name": "Access"}],
        })
        inventory["site"][0].update(region={"id": 11}, group={"id": 14})
        inventory["service"][0]["ipaddresses"] = [{"id": 6}]
        result = verify_plan(plan, inventory)
        self.assertTrue(result["success"], result["mismatches"])
        self.assertEqual(result["ids"]["metro"]["id"], 11)
        self.assertEqual(result["ids"]["os"]["id"], 16)
        inventory["site"][0]["region"] = {"id": 12}
        self.assertIn("reference_mismatch", {f["code"] for f in verify_plan(plan, inventory)["mismatches"]})

    def test_strict_existing_allowlist_is_limited_to_captured_kind_and_id(self):
        plan, inventory = fixture()
        prior = verify_plan(plan, inventory, strict_inventory=True)
        inventory["site"].append({"id": 99, "name": "Earlier namespace"})
        baseline = {"success": True, "target_ids": {"site": [1, 99]}}
        result = verify_plan(plan, inventory, prior, True, baseline)
        self.assertTrue(result["success"], result["mismatches"])
        self.assertEqual(result["unmatched_target_ids"], {"site": [99]})
        self.assertEqual(result["allowed_existing_target_ids"], {"site": [99]})
        # A new unexpected row is still rejected; IDs are scoped to their kind.
        inventory["site"].append({"id": 100, "name": "Unexplained"})
        inventory["platform"] = [{"id": 99, "name": "Unexplained OS", "manufacturer": None}]
        result = verify_plan(plan, inventory, prior, True, baseline)
        rejected = {f["object"]: f["actual"] for f in result["mismatches"] if f["code"] == "unmatched_target_objects"}
        self.assertEqual(rejected, {"site": [100], "platform": [99]})
        self.assertIn("unexpected_new_objects", {f["code"] for f in result["mismatches"]})
        # The allowlist never suppresses mismatches on a desired shared object.
        inventory["site"][0]["status"] = {"value": "planned"}
        self.assertIn("attribute_mismatch", {f["code"] for f in verify_plan(plan, inventory, strict_inventory=True, allow_existing_receipt=baseline)["mismatches"]})
        with self.assertRaisesRegex(ValueError, "requires strict"):
            verify_plan(plan, inventory, allow_existing_receipt=baseline)
        for invalid in ({"success": False, "target_ids": {}},
                        {"success": True, "target_ids": {"site": [True]}}):
            with self.assertRaises(ValueError):
                verify_plan(plan, inventory, strict_inventory=True, allow_existing_receipt=invalid)

    def test_paged_gets_and_untrusted_next_url(self):
        class FakeOpener:
            def __init__(self, pages):
                self.pages, self.requests = iter(pages), []

            def open(self, request, timeout):
                self.requests.append(request)
                return io.BytesIO(json.dumps(next(self.pages)).encode())

        pages = [{"count": 2, "results": [{"id": 1}], "next": "/api/dcim/sites/?offset=1"},
                 {"count": 2, "results": [{"id": 2}], "next": None}]
        opener = FakeOpener(pages)
        with patch("lab.verify.urllib.request.build_opener", return_value=opener):
            result = fetch_inventory("http://localhost:8000", "fixture-secret", ["site"], "branch-schema")
        self.assertEqual([row["id"] for row in result["site"]], [1, 2])
        self.assertEqual([r.get_method() for r in opener.requests], ["GET", "GET"])
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Token fixture-secret")
        self.assertEqual(opener.requests[0].get_header("X-netbox-branch"), "branch-schema")
        self.assertIn("ordering=id", opener.requests[0].full_url)
        self.assertNotIn("fixture-secret", json.dumps(result))
        opener = FakeOpener([dict(pages[0], next="https://untrusted.example/api/")])
        with patch("lab.verify.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "unsafe"):
                fetch_inventory("http://localhost:8000", "nbt_fixture-secret", ["site"])
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer nbt_fixture-secret")

    def test_prior_receipt_is_never_overwritten(self):
        plan, inventory = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text(json.dumps(plan))
            receipt = root / "receipt.json"
            receipt.write_text("preserved failure evidence")
            with patch("lab.verify.fetch_inventory", return_value=inventory), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main([str(root / "plan.json"), "--url", "http://localhost:8000", "--receipt", str(receipt)])
            self.assertEqual(receipt.read_text(), "preserved failure evidence")

    def test_receipts_from_another_target_are_rejected_before_gets(self):
        plan, inventory = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text(json.dumps(plan))
            previous = verify_plan(plan, inventory)
            previous["target_url"] = "http://another-target:8000"
            (root / "prior.json").write_text(json.dumps(previous))
            for flag in ("--previous-receipt", "--allow-existing-receipt"):
                with patch("lab.verify.fetch_inventory") as fetch, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main([str(root / "plan.json"), "--url", "http://localhost:8000",
                              "--receipt", str(root / "result.json"), "--strict-inventory",
                              flag, str(root / "prior.json")])
                fetch.assert_not_called()
                self.assertFalse((root / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
