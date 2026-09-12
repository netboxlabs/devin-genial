"""The supported loader must fail before target writes when its contract is unsafe."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, patch

from estates.model import canonical, digest
from estates.turbobulk import (COMPILER_VERSION, RECEIPT_VERSION, Client, JobTimeout, LoadError,
                               _artifact, _complete_rest, _job_result, _NoRedirect, _rendered_columns,
                               _poll, _schema_preflight, _token, load)


class FakeClient:
    def __init__(self, models, schemas):
        self.models = models
        self.schemas = schemas

    def request(self, path, **_kwargs):
        if path.endswith("/models/"):
            return 200, [{"full_name": name, "export_only": False} for name in self.models]
        model = path.removeprefix("/api/plugins/turbobulk/models/").removesuffix("/")
        return 200, {"fields": [{"name": name} for name in self.schemas[model]]}


class TurboBulkLoaderTests(unittest.TestCase):
    def test_token_normalization_and_target_validation(self):
        self.assertEqual(_token("Bearer nbt_key.secret\n"), "nbt_key.secret")
        self.assertEqual(_token("Token legacy"), "legacy")
        self.assertEqual(Client("https://netbox.example", "Bearer nbt_key.secret").headers["Authorization"],
                         "Bearer nbt_key.secret")
        with self.assertRaises(LoadError):
            Client("https://user:secret@netbox.example", "token")
        client = Client("https://netbox.example", "token")
        redirect = next(handler for handler in client.opener.handlers if isinstance(handler, _NoRedirect))
        self.assertIsNone(redirect.redirect_request(None, None, 302, "moved", {}, "https://untrusted.example"))

    def test_artifact_requires_matching_offline_evidence_when_present(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag", "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}
        ]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({"status": "passed", "plan_sha256": digest(plan)}))
            self.assertEqual(_artifact(root)[2], plan)
            (root / "checks.json").write_text(json.dumps({"status": "passed", "plan_sha256": "wrong"}))
            with self.assertRaisesRegex(LoadError, "does not prove"):
                _artifact(root)
            (root / "checks.json").unlink()
            with self.assertRaisesRegex(LoadError, "is required"):
                _artifact(root)

    def test_schema_preflight_checks_compiled_columns_and_termination_shape(self):
        objects = {
            "tag:demo": {"key": "tag:demo", "kind": "tag",
                         "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}},
            "cable:1": {"key": "cable:1", "kind": "cable",
                         "attrs": {"label": "CAB-1", "status": "connected"},
                         "refs": {"a": "interface:a", "b": "interface:b"}},
        }
        schemas = {
            "extras.tag": {"name", "slug"},
            "dcim.cable": {"label", "status"},
            "dcim.cabletermination": {"cable_id", "cable_end", "termination_type_id", "termination_id"},
        }
        result = _schema_preflight(FakeClient(schemas, schemas), objects)
        self.assertEqual(result["models"], sorted(schemas))
        bad = {name: set(fields) for name, fields in schemas.items()}
        bad["dcim.cable"].remove("label")
        with self.assertRaisesRegex(LoadError, "dcim.cable: label"):
            _schema_preflight(FakeClient(bad, bad), objects)
        objects["tag:demo"]["refs"]["unknown"] = "tag:other"
        with self.assertRaisesRegex(LoadError, "no translation.*tag: unknown"):
            _schema_preflight(FakeClient(schemas, schemas), objects)

    def test_deferred_fields_are_not_silently_compiled_for_turbobulk(self):
        obj = {"kind": "device", "attrs": {"name": "edge-1", "status": "active"},
               "refs": {"site": "site:1", "primary_ip4": "ip:1", "tags": ["tag:demo"]}}
        self.assertEqual(_rendered_columns(obj), {"name", "status", "site_id"})

    def test_terminal_job_must_account_for_every_row_and_hook(self):
        job = {"job_id": "job-1", "status": "completed", "data": {
            "rows_inserted": 2, "rows_updated": 0, "errors": [],
            "post_hooks": {"paths": {"success": True}}}}
        self.assertEqual(_job_result(job, 2)["rows_inserted"], 2)
        job["data"]["post_hooks"]["paths"]["success"] = False
        with self.assertRaises(LoadError):
            _job_result(job, 2)

    def test_poll_timeout_preserves_last_server_observation_and_safe_guidance(self):
        running = {"job_id": "job-1", "status": "running", "data": {
            "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0}}

        class Target:
            def request(self, *_args, **_kwargs):
                return 200, running

        with self.assertRaises(JobTimeout) as caught:
            _poll(Target(), "job-1", 0)
        self.assertIs(caught.exception.job, running)
        self.assertIn("resume only after it progresses", str(caught.exception))

    def test_rest_tag_completion_uses_numeric_ids_and_refuses_extras(self):
        tag = {"key": "tag:demo", "kind": "tag", "attrs": {"slug": "demo"}, "refs": {}}
        site = {"key": "site:demo", "kind": "site", "attrs": {"name": "Demo", "slug": "demo"},
                "refs": {"tags": ["tag:demo"]}}
        plan, objects, ids = {"objects": [tag, site]}, {row["key"]: row for row in (tag, site)}, {
            "tag:demo": 7, "site:demo": 9}

        class Target:
            def __init__(self, tags):
                self.tags = tags
                self.patches = []

            def all(self, _path):
                return [{"id": 9, "tags": self.tags}]

            def request(self, path, **kwargs):
                self.patches.append((path, json.loads(kwargs["body"])))
                return 200, []

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": []}
            target = Target([])
            self.assertEqual(_complete_rest(target, plan, objects, ids, receipt, receipt_path), 1)
            self.assertEqual(target.patches[0][1], [{"id": 9, "tags": [7]}])
            with self.assertRaisesRegex(LoadError, "concurrent values"):
                _complete_rest(Target([{"id": 8, "slug": "other"}]), plan, objects, ids,
                               {"rest_batches": []}, receipt_path)

    def test_fresh_dirty_branch_and_mismatched_receipt_fail_before_write(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag", "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None
            calls = []

            def request(self, path, **_kwargs):
                self.calls.append(path)
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                if path.startswith("/api/plugins/branching/branches/"):
                    return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                              "status": {"value": "ready"}}]}
                raise AssertionError(f"unexpected request after dirty-branch check: {path}")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({"status": "passed", "plan_sha256": digest(plan)}))
            receipt = root / "receipt.json"
            target = Target()
            with patch("estates.turbobulk.Client", return_value=target), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 99}]}), \
                    patch("estates.turbobulk.verify_plan", return_value={"success": False}):
                with self.assertRaisesRegex(LoadError, "requires empty inventories"):
                    load(root, url=target.base, token=target.token, branch="Demo", receipt_path=receipt)
            self.assertFalse(receipt.exists())
            self.assertEqual(len(target.calls), 2)

            receipt.write_text(json.dumps({"receipt_version": 0}))
            target.calls = []
            with patch("estates.turbobulk.Client", return_value=target), \
                    patch("estates.turbobulk.fetch_inventory") as fetch:
                with self.assertRaisesRegex(LoadError, "different receipt_version"):
                    load(root, url=target.base, token=target.token, branch="Demo", receipt_path=receipt)
                fetch.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text()), {"receipt_version": 0})

    def test_exact_repeat_preserves_original_receipt_evidence(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag", "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None

            def request(self, path, **_kwargs):
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                          "status": {"value": "ready"}}]}

        verified = {"success": True, "ids": {"tag:demo": 41}, "matched_objects": 1}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({"status": "passed", "plan_sha256": digest(plan)}))
            receipt_path = root / "receipt.json"
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                load(root, url="https://netbox.example", token="fixture", branch="Demo", receipt_path=receipt_path)
                receipt = json.loads(receipt_path.read_text())
                receipt["jobs"] = [{"job_id": "original-job"}]
                receipt_path.write_text(json.dumps(receipt))
                load(root, url="https://netbox.example", token="fixture", branch="Demo", receipt_path=receipt_path)
            repeated = json.loads(receipt_path.read_text())
            self.assertEqual(repeated["jobs"], [{"job_id": "original-job"}])
            self.assertEqual(len(repeated["repeat_verifications"]), 1)
            self.assertEqual([attempt["result"] for attempt in repeated["attempts"]],
                             ["already-matched", "already-matched"])
            repeated["resolved_ids"]["tag:demo"] = 99
            receipt_path.write_text(json.dumps(repeated))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                with self.assertRaisesRegex(LoadError, "checkpoint target IDs changed"):
                    load(root, url="https://netbox.example", token="fixture",
                         branch="Demo", receipt_path=receipt_path)
            repeated["resolved_ids"]["tag:demo"] = 41
            repeated.update(success=False, error="client stopped after target completion")
            repeated.pop("result")
            receipt_path.write_text(json.dumps(repeated))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                recovered = load(root, url="https://netbox.example", token="fixture",
                                 branch="Demo", receipt_path=receipt_path)
            self.assertTrue(recovered["success"])
            self.assertEqual(recovered["result"], "recovered-matched")
            self.assertEqual(recovered["jobs"], [{"job_id": "original-job"}])
            self.assertEqual(recovered["attempts"][-1]["result"], "recovered-matched")
            self.assertNotIn("error", recovered)

    def test_resume_with_resolved_cable_still_finishes_termination_job(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "interface:a", "kind": "interface", "attrs": {"name": "A"}, "refs": {}},
            {"key": "interface:b", "kind": "interface", "attrs": {"name": "B"}, "refs": {}},
            {"key": "cable:1", "kind": "cable", "attrs": {"label": "CAB-1"},
             "refs": {"a": "interface:a", "b": "interface:b"}},
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None

            def request(self, path, **_kwargs):
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                          "status": {"value": "ready"}}]}

            def all(self, path):
                self.last_path = path
                return [{"id": 51, "label": "CAB-1"}]

        terminal = {"job_id": "terms-job", "status": "completed", "duration_seconds": 0.1,
                    "data": {"rows_inserted": 2, "rows_updated": 0, "errors": [], "post_hooks": {}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = canonical(plan) + b"\n"
            (root / "plan.json").write_bytes(raw)
            (root / "checks.json").write_text(json.dumps({"status": "passed", "plan_sha256": digest(plan)}))
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps({
                "receipt_version": RECEIPT_VERSION, "compiler_version": COMPILER_VERSION,
                "artifact": str(root / "plan.json"), "plan_sha256": hashlib.sha256(raw).hexdigest(),
                "canonical_sha256": digest(plan), "target": "https://netbox.example", "branch": "Demo",
                "branch_id": "schema1", "transport": "turbobulk+rest",
                "target_contract": {"netbox": "4.6.8", "plugins": {
                    "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}},
                "success": False, "resolved_ids": {"interface:a": 41, "interface:b": 42, "cable:1": 51},
                "jobs": [{"purpose": "phase-1:cable:terminations", "job_id": "terms-job",
                          "rows_expected": 2, "status": "submitted"}], "rest_batches": []}))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={}), \
                    patch("estates.turbobulk.verify_plan", side_effect=[{"success": False},
                                                                         {"success": True, "mismatch_count": 0}]), \
                    patch("estates.turbobulk._schema_preflight", return_value={}), \
                    patch("estates.turbobulk._content_types", return_value={"interface": 1}), \
                    patch("estates.turbobulk._phases", return_value=[["cable:1"]]), \
                    patch("estates.turbobulk._complete_rest", return_value=0), \
                    patch("estates.turbobulk._verify_paths", return_value={"cables_expected": 1,
                                                                           "cables_traced": 1,
                                                                           "failures": []}), \
                    patch("estates.turbobulk._poll", return_value=terminal) as poll:
                result = load(root, url="https://netbox.example", token="fixture",
                              branch="Demo", receipt_path=receipt_path)
            poll.assert_called_once_with(ANY, "terms-job", 900)
            self.assertTrue(result["success"])
            self.assertEqual(result["attempts"][-1]["result"], "loaded")


if __name__ == "__main__":
    unittest.main()
