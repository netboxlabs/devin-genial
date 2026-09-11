"""The local phase barrier must not confuse ingest acceptance with reconciliation."""

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lab.replay import (LocalTarget, ReplayError, _check_aggregate_ranges, _check_global_numeric_identities,
                        _check_manifest_compatibility, replay, wait_ready, wait_reconciled)
from lab.setup import _front_port_patch


def panel_manifest():
    return {"format_version": 1, "diode_sdk_schema": "1.14.0", "generator_version": "0.3.0",
            "source_checked_target": {"netbox": "4.4.10", "diode_sdk": "1.14.0", "diode_netbox_plugin": "1.17.0"},
            "known_incompatible_netbox": [">=4.5.0"], "counts": {"front_port": 1, "rear_port": 1}}


class LocalReplayBarrierTests(unittest.TestCase):
    def test_aggregate_preflight_allows_owned_replay_and_rejects_any_other_overlap_before_submission(self):
        owner = {"rir": {"name": "bank registry"}, "description": "bank allocation"}
        expected = [{"prefix": prefix, **owner} for prefix in ("10.0.0.0/8", "172.16.0.0/12", "2001:db8::/32")]
        cases = [("owned", {"id": 41, **expected[0]}, True),
                 ("ipv6-owned", {"id": 41, **expected[2]}, True),
                 ("ipv6-owned-expanded", {"id": 41, **expected[2], "prefix": "2001:0DB8:0000::/32"}, True),
                 ("covering", {"id": 41, **owner, "prefix": "10.0.0.0/7"}, False),
                 ("contained", {"id": 41, **owner, "prefix": "10.1.0.0/16"}, False),
                 ("foreign-rir", {"id": 41, **expected[0], "rir": {"name": "other registry"}}, False),
                 ("foreign-description", {"id": 41, **expected[0], "description": "other allocation"}, False),
                 ("ipv6-contained", {"id": 41, **owner, "prefix": "2001:db8:1::/48"}, False),
                 ("ambiguous", {"id": 41, **expected[0]}, False)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "netbox-token").write_text("fixture-token")
            request = root / "phase-01-part-01.json"
            # Nested references must not introduce extra aggregate declarations.
            entities = [{"aggregate": row} for row in expected]
            entities.append({"prefix": {"aggregate": {"prefix": "10.0.0.0/7"}}})
            request.write_text(json.dumps({"entities": entities}))
            manifest = panel_manifest()
            manifest["source_checked_target"]["netbox"] = "4.7.0"
            manifest.update(known_incompatible_netbox=[], counts={"aggregate": 3},
                            phases=[{"phase": 1, "requires_completed_phases": [], "purpose": "test", "files": [request.name]}],
                            files=[{"path": request.name, "entities": len(entities),
                                    "sha256": hashlib.sha256(request.read_bytes()).hexdigest()}])
            (root / "manifest.json").write_text(json.dumps(manifest))
            target = Mock()
            target.config = {"docker_context": "colima-netbox-generator", "docker_network": "netbox-generator-link",
                             "diode_target": "grpc://127.0.0.1:18080/diode", "netbox_version": "4.7.0",
                             "diode_version": "2.2.0", "plugin_version": "1.17.0"}
            target.client.return_value = {"client_secret": "fixture-secret"}
            payload_before = request.read_bytes()
            for name, existing, success in cases:
                inventory = {"aggregate": [existing, {"id": 42, **owner, "prefix": "192.0.2.0/24"},
                                            {"id": 43, **owner, "prefix": "2001:db9::/32"}]}
                if name == "ambiguous":
                    inventory["aggregate"].append({"id": 44, **expected[0]})
                before = deepcopy(inventory)
                receipt_path = root / f"aggregate-{name}.json"
                with self.subTest(name=name), patch("lab.replay.verify_export", return_value={}), \
                        patch("lab.replay.LocalTarget", return_value=target), \
                        patch("lab.verify.fetch_inventory", return_value=inventory) as fetch, \
                        patch("lab.replay._run") as run, patch("lab.replay.wait_ready") as ready, \
                        patch("lab.replay.wait_reconciled", return_value={"total": 4, "reconciled": 4}), \
                        redirect_stdout(io.StringIO()):
                    if success:
                        result = replay(root, root, receipt_path)
                        self.assertTrue(result["aggregate_preflight"]["success"])
                        self.assertEqual([(row["status"], row["existing_id"]) for row in result["aggregate_preflight"]["checks"]],
                                         ([("available", None), ("available", None), ("owned-existing", 41)]
                                          if name.startswith("ipv6-") else
                                          [("owned-existing", 41), ("available", None), ("available", None)]))
                        self.assertEqual(run.call_count, 1)
                    else:
                        with self.assertRaisesRegex(ReplayError, "overlaps"):
                            replay(root, root, receipt_path)
                        run.assert_not_called()
                        ready.assert_not_called()
                    fetch.assert_called_once_with("http://127.0.0.1:8000", "fixture-token", ["aggregate"])
                saved = json.loads(receipt_path.read_text())
                self.assertEqual(saved["success"], success)
                self.assertEqual(saved["aggregate_preflight"]["success"], success)
                if not success:
                    self.assertEqual(saved["phases"], [])
                self.assertNotIn("fixture-token", receipt_path.read_text())
                self.assertNotIn("fixture-secret", receipt_path.read_text())
                self.assertEqual(inventory, before)
            self.assertEqual(request.read_bytes(), payload_before)

    def test_internal_aggregate_overlaps_fail_before_inventory_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "phase-01-part-01.json"
            for prefixes in (("10.0.0.0/8", "10.1.0.0/16"), ("10.1.0.0/16", "10.0.0.0/8"),
                             ("10.0.0.0/8", "10.0.0.0/8"), ("2001:db8::/32", "2001:db8:1::/48")):
                request.write_text(json.dumps({"entities": [{"aggregate": {"prefix": prefix,
                    "rir": {"name": f"registry-{number}"}, "description": "authored allocation"}}
                    for number, prefix in enumerate(prefixes)]}))
                entries = {request.name: {"sha256": hashlib.sha256(request.read_bytes()).hexdigest()}}
                with self.subTest(prefixes=prefixes), patch("lab.verify.fetch_inventory") as fetch:
                    with self.assertRaisesRegex(ReplayError, "Export aggregate ranges"):
                        _check_aggregate_ranges(root, entries, root)
                    fetch.assert_not_called()

    def test_numeric_preflight_uses_full_records_and_ignores_nested_references(self):
        entities = [{"asn": {"asn": 4200000001, "rir": {"name": "bank registry"}, "description": "bank domain"}},
                    {"fhrp_group": {"group_id": 125, "name": "bank-dc-applications"}},
                    {"site": {"name": "bank-dc", "asns": [{"asn": 4200000002}]}},
                    {"ip_address": {"assigned_object_fhrp_group": {"group_id": 126}}}]
        inventory = {"asn": [{"id": 41, **entities[0]["asn"]}],
                     "fhrp_group": [{"id": 42, "group_id": 126, "name": "unrelated"}]}
        before = deepcopy(inventory)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "netbox-token").write_text("fixture-token")
            request = root / "phase-01-part-01.json"
            request.write_text(json.dumps({"entities": entities}))
            entries = {request.name: {"sha256": hashlib.sha256(request.read_bytes()).hexdigest()}}
            with patch("lab.verify.fetch_inventory", return_value=inventory) as fetch:
                checks = _check_global_numeric_identities(root, entries, root)
                fetch.assert_called_once_with("http://127.0.0.1:8000", "fixture-token", ["asn", "fhrp_group"])
            self.assertEqual([(row["kind"], row["status"], row["existing_id"]) for row in checks],
                             [("asn", "owned-existing", 41), ("fhrp_group", "available", None)])
            self.assertEqual(checks[0]["ownership"], {"rir": "bank registry", "description": "bank domain"})
            self.assertNotIn("fixture-token", json.dumps(checks))
            self.assertEqual(inventory, before)
            inventory["fhrp_group"].append({"id": 43, **entities[1]["fhrp_group"]})
            with patch("lab.verify.fetch_inventory", return_value=inventory):
                checks = _check_global_numeric_identities(root, entries, root)
            self.assertEqual((checks[1]["status"], checks[1]["existing_id"]), ("owned-existing", 43))
            request.write_text(json.dumps({"entities": []}))
            with patch("lab.verify.fetch_inventory") as fetch, self.assertRaisesRegex(ReplayError, "changed"):
                _check_global_numeric_identities(root, entries, root)
            fetch.assert_not_called()

    def test_foreign_or_ambiguous_numeric_identity_stops_before_submission_and_saves_failure(self):
        records = {"asn": {"asn": 4200000001, "rir": {"name": "bank registry"}, "description": "bank domain"},
                   "fhrp_group": {"group_id": 125, "name": "bank-dc-applications", "protocol": "vrrp3"}}
        cases = [("asn", {"rir": {"name": "other registry"}}, False),
                 ("asn", {"description": "other domain"}, False),
                 ("fhrp_group", {"name": "other-bank", "protocol": "vrrp2"}, False),
                 ("fhrp_group", {}, True)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "netbox-token").write_text("fixture-token")
            request = root / "phase-01-part-01.json"
            request.write_text(json.dumps({"entities": [{kind: row} for kind, row in records.items()]}))
            manifest = panel_manifest()
            manifest["source_checked_target"]["netbox"] = "4.7.0"
            manifest.update(known_incompatible_netbox=[], counts={"asn": 1, "fhrp_group": 1},
                            phases=[{"phase": 1, "requires_completed_phases": [], "purpose": "test", "files": [request.name]}],
                            files=[{"path": request.name, "entities": 2,
                                    "sha256": hashlib.sha256(request.read_bytes()).hexdigest()}])
            (root / "manifest.json").write_text(json.dumps(manifest))
            target = Mock()
            target.config = {"docker_context": "colima-netbox-generator", "docker_network": "netbox-generator-link",
                             "diode_target": "grpc://127.0.0.1:18080/diode", "netbox_version": "4.7.0",
                             "diode_version": "2.2.0", "plugin_version": "1.17.0"}
            payload_before = request.read_bytes()
            for number, (kind, changes, duplicate) in enumerate(cases):
                existing = {"id": 41, **deepcopy(records[kind]), **changes}
                inventory = {"asn": [], "fhrp_group": []}
                inventory[kind] = [existing, {**existing, "id": 42}] if duplicate else [existing]
                receipt_path = root / f"collision-{number}.json"
                with self.subTest(kind=kind, changes=changes, duplicate=duplicate), \
                        patch("lab.replay.verify_export", return_value={}), \
                        patch("lab.replay.LocalTarget", return_value=target), \
                        patch("lab.verify.fetch_inventory", return_value=inventory), \
                        patch("lab.replay._run") as run, patch("lab.replay.wait_ready") as ready:
                    with self.assertRaisesRegex(ReplayError, "already owned or ambiguous"):
                        replay(root, root, receipt_path)
                    run.assert_not_called()
                    ready.assert_not_called()
                saved = json.loads(receipt_path.read_text())
                self.assertFalse(saved["success"])
                self.assertFalse(saved["global_numeric_preflight"]["success"])
                self.assertEqual(saved["phases"], [])
                self.assertNotIn("fixture-token", receipt_path.read_text())
            self.assertEqual(request.read_bytes(), payload_before)

    def test_local_panel_exception_does_not_widen_official_compatibility(self):
        panel = panel_manifest()
        with self.assertRaises(ReplayError):
            _check_manifest_compatibility(panel, False)
        _check_manifest_compatibility(panel, True)
        direct = deepcopy(panel)
        direct["source_checked_target"]["netbox"] = "4.7.0"
        direct["known_incompatible_netbox"] = []
        direct["counts"] = {}
        _check_manifest_compatibility(direct, False)
        for field, value in (("known_incompatible_netbox", [">=4.5.0", ">=4.8.0"]),
                             ("counts", {}), ("diode_sdk_schema", "1.15.0")):
            changed = deepcopy(panel)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ReplayError):
                _check_manifest_compatibility(changed, True)

    def test_both_running_containers_must_match_before_any_submission(self):
        record, _, _ = _front_port_patch()
        runtime = {"plugin_version": "1.17.0", "sha256": record["output_sha256"]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "phase-01-part-01.json"
            request.write_text('{"entities": []}\n')
            manifest = panel_manifest()
            manifest.update(phases=[{"phase": 1, "requires_completed_phases": [], "purpose": "test",
                                     "files": [request.name]}],
                            files=[{"path": request.name, "entities": 1,
                                    "sha256": hashlib.sha256(request.read_bytes()).hexdigest()}])
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            manifest_before = manifest_path.read_bytes()
            target = LocalTarget.__new__(LocalTarget)
            target.directory = root
            target.config = {"docker_context": "colima-netbox-generator", "docker_network": "netbox-generator-link",
                             "diode_target": "grpc://127.0.0.1:18080/diode", "netbox_version": "4.7.0",
                             "diode_version": "2.2.0", "plugin_version": "1.17.0",
                             "local_compatibility": {"front_port_mapping": record}}
            target.clients = [{"client_id": "diode-ingest", "client_secret": "fixture-secret"}]
            bad_worker = deepcopy(runtime)
            bad_worker["sha256"]["api/front_port_compat.py"] = "unpatched"
            for attempt, outputs, success in (
                    ("worker-mismatch", [json.dumps(runtime), json.dumps(bad_worker)], False),
                    ("matching", [json.dumps(runtime), json.dumps(runtime), ""], True)):
                receipt_path = root / (attempt + ".json")
                with patch("lab.replay.verify_export", return_value={"sdk_version": "1.14.0"}), \
                        patch("lab.replay.LocalTarget", return_value=target), \
                        patch("lab.replay._run", side_effect=outputs) as run, \
                        patch("lab.replay.wait_ready") as ready, \
                        patch("lab.replay.wait_reconciled", return_value={"total": 1, "reconciled": 1}), \
                        redirect_stdout(io.StringIO()):
                    if success:
                        result = replay(root, root, receipt_path, front_port_compat=True)
                        self.assertEqual(result["global_numeric_preflight"], {"success": True, "checks": []})
                        self.assertEqual(result["aggregate_preflight"], {"success": True, "checks": []})
                        self.assertEqual(set(result["local_compatibility"]["front_port_mapping"]["runtime"]),
                                         {"netbox", "netbox-worker"})
                        self.assertEqual(len(run.call_args_list), 3)
                    else:
                        with self.assertRaisesRegex(ReplayError, "netbox-worker"):
                            replay(root, root, receipt_path, front_port_compat=True)
                        ready.assert_not_called()
                        self.assertEqual(len(run.call_args_list), 2)
                    commands = [call.args[0] for call in run.call_args_list]
                    self.assertIn("netbox", commands[0])
                    self.assertIn("netbox-worker", commands[1])
                    self.assertTrue(all("exec" in command and "fixture-secret" not in repr(command)
                                        for command in commands[:2]))
                saved = json.loads(receipt_path.read_text())
                self.assertEqual(saved["success"], success)
                self.assertNotIn("fixture-secret", receipt_path.read_text())
            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            # Unknown overrides and prepared metadata drift cannot authorize probes or ingestion.
            target.config["local_compatibility"]["unknown_override"] = True
            with patch("lab.replay._run") as run:
                with self.assertRaisesRegex(ReplayError, "metadata"):
                    target.verify_front_port_compat(float("inf"))
                run.assert_not_called()

    @patch("lab.replay.time.sleep")
    def test_startup_retries_only_read_probes(self, sleep):
        target = Mock()
        target.ready.side_effect = [ReplayError("consumer starting"), None, None]
        target.read_token.side_effect = [ReplayError("auth starting"), "unused-token"]
        wait_ready(target, float("inf"))
        self.assertEqual([call[0] for call in target.mock_calls],
                         ["ready", "ready", "read_token", "ready", "read_token"])
        self.assertEqual(sleep.call_count, 2)

    @patch("lab.replay.time.sleep")
    def test_broker_drains_before_metrics_and_only_terminal_success_passes(self, sleep):
        target = Mock()
        target.stream_length.side_effect = [1, 0, 0]
        target.logs.side_effect = [{"metrics": {"total": 2, "reconciled": 1}}, {"logs": []},
                                   {"metrics": {"total": 2, "reconciled": 1, "noChanges": 1}}]
        self.assertEqual(wait_reconciled(target, float("inf"))["total"], 2)
        self.assertEqual([call[0] for call in target.mock_calls],
                         ["stream_length", "stream_length", "logs", "logs", "stream_length", "logs"])
        self.assertEqual(sleep.call_count, 2)
        for metrics in ({"total": 1, "failed": 1}, {"total": 1, "futureState": 1}):
            target = Mock()
            target.stream_length.return_value = 0
            target.logs.return_value = {"metrics": metrics}
            with self.assertRaises(ReplayError):
                wait_reconciled(target, float("inf"))


if __name__ == "__main__":
    unittest.main()
