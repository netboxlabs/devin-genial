"""The public loader selects only complete, explicitly enabled transports."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from estates.diode import export
from estates.load import (_diode_manifest, _phase_plan, _probe_endpoints, inspect,
                          _schedule, load, load_diode)
from estates.model import canonical, digest
from estates.turbobulk import LoadError


class Target:
    base = "https://netbox.example"
    token = "token"
    branch_id = "branch_schema"


class LoaderSelectionTests(unittest.TestCase):
    def artifact(self, root, objects):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": objects}
        root.mkdir(exist_ok=True)
        (root / "plan.json").write_bytes(canonical(plan) + b"\n")
        (root / "checks.json").write_text(json.dumps(
            {"status": "passed", "plan_sha256": digest(plan)}))
        return plan

    def selection(self, root, status, manifest, environment, transport="auto"):
        branch = {"id": 1, "name": "Demo", "schema_id": "branch_schema",
                  "status": {"value": "ready"}}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment, clear=True))
            stack.enter_context(patch("estates.load._target", return_value=(Target(), status, branch)))
            stack.enter_context(patch("estates.load._diode_manifest", return_value=((root / "diode", manifest), [])))
            stack.enter_context(patch("estates.load._probe_endpoints", return_value=[]))
            return inspect(root, url=Target.base, token=Target.token, branch="Demo", transport=transport)

    def test_auto_uses_explicitly_enabled_turbobulk_for_covered_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.artifact(root, [{"key": "tag:demo", "kind": "tag",
                                  "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}])
            status = {"netbox-version": "4.7.0", "plugins": {
                "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
            decision = self.selection(root, status, {}, {"TURBOBULK_WRITES": "1"})
            self.assertEqual(decision["selected"], "turbobulk")

    def test_auto_uses_diode_only_with_exact_target_contract_and_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.artifact(root, [{"key": "region:demo", "kind": "region",
                                  "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}])
            status = {"netbox-version": "4.7.0", "plugins": {
                "netbox_diode_plugin": "1.17.0", "netbox_branching": "1.1.2"}}
            manifest = {"source_checked_target": {"netbox": "4.7.0",
                                                   "diode_netbox_plugin": "1.17.0"}}
            env = {"DIODE_TARGET": "grpc://diode.example", "DIODE_CLIENT_ID": "id",
                   "DIODE_CLIENT_SECRET": "secret", "DIODE_MODE": "direct",
                   "DIODE_BRANCH": "branch_schema", "DIODE_WRITES": "1",
                   "DIODE_CONFIG_SOURCE": "https://netbox.example/plugins/diode/settings/",
                   "DIODE_CONFIG_CONFIRMED_AT": "2099-01-01T00:00:00Z"}
            with patch("estates.load._confirmed_at", return_value=True):
                decision = self.selection(root, status, manifest, env)
            self.assertEqual(decision["selected"], "diode")

    def test_explain_returns_every_rejection_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.artifact(root, [{"key": "region:demo", "kind": "region",
                                  "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}])
            status = {"netbox-version": "4.6.8", "plugins": {"netbox_branching": "1.1.2"}}
            manifest = {"source_checked_target": {"netbox": "4.7.0",
                                                   "diode_netbox_plugin": "1.17.0"}}
            with patch("estates.load.inspect", return_value={"selected": None, "candidates": {
                    "turbobulk": {"reasons": ["disabled"]},
                    "diode": {"reasons": ["version mismatch"]},
                    "rest": {"reasons": ["not implemented"]}}}), \
                    patch("estates.load.load_turbobulk") as turbo, \
                    patch("estates.load.load_diode") as diode:
                result = load(root, url=Target.base, token=Target.token, branch="Demo",
                              receipt_path=root / "receipt.json", explain=True)
            self.assertEqual(result["decision"]["selected"], None)
            turbo.assert_not_called()
            diode.assert_not_called()

    def test_phase_plan_removes_only_deferred_cycle_fields(self):
        plan = {"objects": [{"key": "device:one", "kind": "device", "attrs": {"name": "one"},
                             "refs": {"site": "site:one", "primary_ip4": "ip:one"}},
                            {"key": "site:one", "kind": "site", "attrs": {"name": "One"}, "refs": {}},
                            {"key": "ip:one", "kind": "ip_address", "attrs": {"address": "192.0.2.1/32"},
                             "refs": {}}]}
        create = _phase_plan(plan, ["site:one", "ip:one", "device:one"])
        device = next(row for row in create["objects"] if row["key"] == "device:one")
        self.assertEqual(device["refs"], {"site": "site:one"})
        final = _phase_plan(plan, ["site:one", "ip:one", "device:one"], final=True)
        self.assertIn("primary_ip4", next(row for row in final["objects"]
                                          if row["key"] == "device:one")["refs"])

    def test_endpoint_failure_does_not_copy_response_body(self):
        class Missing(Target):
            def request(self, _path):
                from estates.turbobulk import LoadError
                raise LoadError("GET returned HTTP 404: <html>secret session value</html>")

        self.assertEqual(_probe_endpoints(Missing(), {"module_bay_type"}),
                         ["module_bay_type: REST endpoint returned HTTP 404"])

    def test_manifest_checksum_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = self.artifact(root, [{"key": "tag:demo", "kind": "tag",
                                         "attrs": {"name": "Demo"}, "refs": {}}])
            diode = root / "diode"
            diode.mkdir()
            request = diode / "phase-001-part-0001.json"
            request.write_text("{}\n")
            manifest = {"format_version": 1, "plan_sha256": digest(plan),
                        "files": [{"path": request.name, "bytes": request.stat().st_size,
                                   "sha256": "wrong"}],
                        "phases": [{"files": [request.name]}]}
            (diode / "manifest.json").write_text(json.dumps(manifest))
            _package, reasons = _diode_manifest(root / "plan.json", plan)
            self.assertIn("checksum mismatch", reasons[0])

    def test_direct_diode_waits_for_phase_visibility_and_finishes_strict_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = {"schema_version": 1, "generator_version": "0.9.0",
                    "recipe": {"as_of": "2026-09-09"}, "objects": [
                        {"key": "tag:demo", "kind": "tag",
                         "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}]}
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(plan)}))
            export(plan, root / "diode")
            receipt = root / "receipt.json"
            status = {"netbox-version": "4.7.0", "plugins": {
                "netbox_diode_plugin": "1.17.0", "netbox_branching": "1.1.2"}}
            branch = {"id": 1, "name": "Demo", "schema_id": "branch_schema",
                      "status": {"value": "ready"}}
            empty = {"tag": []}
            populated = {"tag": [{"id": 7, "name": "Demo", "slug": "demo"}]}
            env = {"DIODE_TARGET": "grpc://diode.example", "DIODE_CLIENT_ID": "id",
                   "DIODE_CLIENT_SECRET": "secret", "DIODE_MODE": "direct",
                   "DIODE_BRANCH": "branch_schema", "DIODE_WRITES": "1",
                   "DIODE_CONFIG_SOURCE": "https://netbox.example/plugins/diode/settings/",
                   "DIODE_CONFIG_CONFIRMED_AT": "2099-01-01T00:00:00Z"}
            with patch.dict(os.environ, env, clear=True), \
                    patch("estates.load._confirmed_at", return_value=True), \
                    patch("estates.load._target", return_value=(Target(), status, branch)), \
                    patch("estates.load._sdk", return_value=0.01) as sdk, \
                    patch("estates.load.fetch_inventory",
                          side_effect=[empty, empty, empty, populated, populated]), \
                    patch("estates.load._verify_paths", return_value={
                        "cables_expected": 0, "cables_traced": 0, "failures": []}):
                result = load_diode(root, url=Target.base, token=Target.token, branch="Demo",
                                    receipt_path=receipt, decision={"selected": "diode"}, timeout=1)
            self.assertTrue(result["success"])
            self.assertEqual(result["result"], "loaded")
            self.assertEqual(result["phases"][0]["status"], "applied")
            self.assertEqual(sdk.call_count, 2)  # package validation, then one ingest request
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_exported_schedules_accept_no_deferred_primary_and_back_reference_forms(self):
        cases = [
            ([{"key": "tag:one", "kind": "tag", "attrs": {"name": "One"}, "refs": {}}], None),
            ([{"key": "site:one", "kind": "site", "attrs": {"name": "One"}, "refs": {}},
              {"key": "ip:one", "kind": "ip_address", "attrs": {"address": "192.0.2.1/32"}, "refs": {}},
              {"key": "device:one", "kind": "device", "attrs": {"name": "one"},
               "refs": {"site": "site:one", "primary_ip4": "ip:one"}}], "primary-addresses"),
            ([{"key": "vc:one", "kind": "virtual_chassis", "attrs": {"name": "One"},
               "refs": {"master": "device:one"}},
              {"key": "device:one", "kind": "device", "attrs": {"name": "one"},
               "refs": {"virtual_chassis": "vc:one"}}], "back-references"),
        ]
        for objects, final in cases:
            with self.subTest(final=final), tempfile.TemporaryDirectory() as temporary:
                plan = {"schema_version": 1, "generator_version": "0.9.0",
                        "recipe": {"as_of": "2026-09-09"}, "objects": objects}
                manifest = export(plan, Path(temporary) / "diode")
                indexed = {obj["key"]: obj for obj in objects}
                self.assertTrue(_schedule(manifest, indexed))
                if final:
                    self.assertEqual(manifest["phases"][-1]["purpose"], final)
                else:
                    self.assertTrue(all(row["purpose"] == "create" for row in manifest["phases"]))

    def test_diode_rejects_occupied_ambiguous_baseline_before_sdk_or_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = {"schema_version": 1, "generator_version": "0.9.0",
                    "recipe": {"as_of": "2026-09-09"}, "objects": [
                        {"key": "tag:demo", "kind": "tag", "attrs": {"name": "Demo"}, "refs": {}}]}
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(plan)}))
            export(plan, root / "diode")
            status = {"netbox-version": "4.7.0", "plugins": {
                "netbox_diode_plugin": "1.17.0", "netbox_branching": "1.1.2"}}
            branch = {"id": 1, "name": "Demo", "schema_id": "branch_schema",
                      "status": {"value": "ready"}}
            env = {"DIODE_TARGET": "grpc://diode.example", "DIODE_CLIENT_ID": "id",
                   "DIODE_CLIENT_SECRET": "secret", "DIODE_MODE": "direct",
                   "DIODE_BRANCH": "branch_schema", "DIODE_WRITES": "1",
                   "DIODE_CONFIG_SOURCE": "settings", "DIODE_CONFIG_CONFIRMED_AT": "fresh"}
            with patch.dict(os.environ, env, clear=True), \
                    patch("estates.load._confirmed_at", return_value=True), \
                    patch("estates.load._target", return_value=(Target(), status, branch)), \
                    patch("estates.load.fetch_inventory", return_value={"tag": [
                        {"id": 1, "name": "Demo"}, {"id": 2, "name": "Demo"}]}), \
                    patch("estates.load._sdk") as sdk:
                with self.assertRaisesRegex(LoadError, "requires empty inventories"):
                    load_diode(root, url=Target.base, token=Target.token, branch="Demo",
                               receipt_path=root / "receipt.json", decision={"selected": "diode"})
            sdk.assert_not_called()
            self.assertFalse((root / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
