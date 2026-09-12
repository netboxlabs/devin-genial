"""Focused safety tests for disposable Branching resets."""

import json
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from estates import reset as subject
from estates.turbobulk import LoadError


TARGET = "https://netbox.example"
NAME = "Generator Benchmark"


def branch(branch_id, status="ready", name=NAME, description="SE scratch branch"):
    return {"id": branch_id, "name": name, "description": description,
            "schema_id": f"branch_{branch_id}", "status": {"value": status}}


class FakeClient:
    api = None

    def __init__(self, url, token):
        if not token:
            raise LoadError("empty token")
        self.base = url.rstrip("/")
        self.api = type(self).api

    def request(self, path, **_kwargs):
        if path == "/api/status/":
            plugins = {"netbox_branching": "1.1.2"} if self.api.plugin else {}
            if self.api.turbobulk_plugin:
                plugins["netbox_turbobulk"] = "0.3.0"
            return 200, {"netbox-version": "4.6.8", "plugins": plugins}
        raise AssertionError(path)

    def all(self, _path):
        if _path.startswith("/api/core/jobs/"):
            self.api.job_reads += 1
            if self.api.job_snapshots:
                index = min(self.api.job_reads - 1, len(self.api.job_snapshots) - 1)
                return list(self.api.job_snapshots[index])
            if self.api.jobs_on_read == self.api.job_reads:
                return list(self.api.late_jobs)
            return list(self.api.jobs)
        return list(self.api.branches)


class FakeAPI:
    def __init__(self, rows, *, plugin=True, created_status="ready"):
        self.branches = list(rows)
        self.plugin = plugin
        self.turbobulk_plugin = False
        self.created_status = created_status
        self.next_id = 100
        self.delete_error_after_accept = False
        self.delete_error_before_accept = False
        self.rename_error_after_accept = False
        self.create_error_after_accept = False
        self.delete_rejection = None
        self.create_rejection = None
        self.create_allowed = True
        self.delete_allowed = True
        self.jobs = []
        self.job_snapshots = []
        self.late_jobs = []
        self.jobs_on_read = None
        self.queue_original_job_on_delete = False
        self.job_reads = 0
        self.calls = []

    def request(self, _client, path, *, method="GET", body=None, allowed=(), include_headers=False):
        self.calls.append((method, path, body))
        if method == "OPTIONS":
            if path == subject.BRANCHES:
                payload = {"actions": {"POST": {"name": {}, "description": {}}}
                           if self.create_allowed else {}}
                result = (200, payload, {"Allow": "GET, POST, HEAD, OPTIONS"})
            else:
                allow = "GET, PUT, PATCH, DELETE, HEAD, OPTIONS" if self.delete_allowed else "GET, PUT, PATCH, HEAD, OPTIONS"
                result = (200, {"actions": {"PUT": {}}}, {"Allow": allow})
            return result if include_headers else result[:2]
        if method == "DELETE":
            branch_id = int(path.rstrip("/").split("/")[-1])
            if self.delete_rejection:
                raise subject.HTTPRejected(method, path, self.delete_rejection, "denied")
            if self.delete_error_before_accept:
                self.delete_error_before_accept = False
                raise OSError("connection closed")
            self.branches = [row for row in self.branches if row["id"] != branch_id]
            if self.queue_original_job_on_delete:
                self.jobs.append({"id": 99, "name": "Bulk Load", "status": "pending",
                                  "data": {"branch": NAME}})
            if self.delete_error_after_accept:
                self.delete_error_after_accept = False
                raise OSError("connection closed")
            return 204, None
        if method == "PATCH":
            branch_id = int(path.rstrip("/").split("/")[-1])
            row = next(row for row in self.branches if row["id"] == branch_id)
            row["name"] = body["name"]
            if self.rename_error_after_accept:
                self.rename_error_after_accept = False
                raise OSError("connection closed")
            return 200, row
        if method == "POST":
            if self.create_rejection:
                raise subject.HTTPRejected(method, path, self.create_rejection, "denied")
            row = branch(self.next_id, self.created_status, body["name"], body["description"])
            self.next_id += 1
            self.branches.append(row)
            if self.create_error_after_accept:
                self.create_error_after_accept = False
                raise OSError("connection closed")
            return 201, row
        branch_id = int(path.rstrip("/").split("/")[-1])
        row = next((row for row in self.branches if row["id"] == branch_id), None)
        return (200, row) if row else (404, None)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.reset_root = root / "reset-receipts"
        self.load_root = root / "load-receipts"
        self.api = FakeAPI([branch(7)])
        FakeClient.api = self.api
        self.client_patch = patch.object(subject, "Client", FakeClient)
        self.http_patch = patch.object(subject, "_request_json", self.api.request)
        self.client_patch.start()
        self.http_patch.start()

    def tearDown(self):
        self.http_patch.stop()
        self.client_patch.stop()
        self.temporary.cleanup()

    def run_reset(self, **kwargs):
        return subject.reset(TARGET, NAME, token="nbt_secret", poll_interval=0,
                             receipt_root=self.reset_root, load_receipt_root=self.load_root,
                             **kwargs)

    def test_initial_reset_archives_matching_load_receipt_and_preserves_description(self):
        self.load_root.mkdir(parents=True)
        matching = self.load_root / "estate.json"
        matching.write_text(json.dumps({"target": TARGET, "branch": NAME,
                                        "branch_id": "branch_7", "token": None}))
        stale = self.load_root / "same-name-old-generation.json"
        stale.write_text(json.dumps({"target": TARGET, "branch": NAME,
                                     "branch_id": "branch_6"}))
        unrelated = self.load_root / "other.json"
        unrelated.write_text(json.dumps({"target": TARGET, "branch": "Other"}))

        result = self.run_reset()

        self.assertTrue(result["success"])
        self.assertEqual(result["old_branch"]["id"], 7)
        self.assertEqual(result["new_branch"]["id"], 100)
        self.assertEqual(result["new_branch"]["schema_id"], "branch_100")
        self.assertEqual(result["receipt_path"], str(next(self.reset_root.glob("*.json"))))
        self.assertEqual(self.api.branches[0]["description"], "SE scratch branch")
        archived = Path(result["load_receipts"][0]["destination"])
        self.assertFalse(matching.exists())
        self.assertTrue(archived.exists())
        self.assertTrue(stale.exists())
        self.assertTrue(unrelated.exists())
        receipt_path = next(self.reset_root.glob("*.json"))
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)

    def test_pending_job_with_null_data_drains_after_quarantine(self):
        pending = {"id": 41, "name": "Bulk Load", "status": {"value": "pending"}, "data": None}
        self.api.job_snapshots = [[pending], [pending], []]

        result = self.run_reset(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["initial_active_jobs"][0]["status"], "pending")
        self.assertEqual(self.api.branches[0]["name"], result["new_branch_name"])
        self.assertNotEqual(result["new_branch_name"], NAME)

    def test_running_job_with_missing_metadata_times_out_with_old_branch_quarantined(self):
        for data in (None, {}, {"branch": None}, {"branch": ""}):
            with self.subTest(data=data):
                self.api.jobs = [{"id": 42, "name": "Bulk Delete", "status": "running",
                                  "data": data}]
                with self.assertRaisesRegex(LoadError, "did not quiesce"):
                    self.run_reset(timeout=0)
                receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
                self.assertEqual(receipt["stage"], "quarantined")
                self.assertEqual(self.api.branches[0]["name"], receipt["quarantine_name"])
                self.assertFalse(any(call[0] == "DELETE" for call in self.api.calls))
                self.api.branches = [branch(7)]
                for path in self.reset_root.glob("*.json"):
                    path.unlink()

    def test_job_drain_timeout_resumes_quarantined_branch(self):
        pending = {"id": 43, "name": "Bulk Export", "status": "pending", "data": None}
        self.api.jobs = [pending]
        with self.assertRaisesRegex(LoadError, "did not quiesce"):
            self.run_reset(timeout=0)
        receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
        self.assertEqual(self.api.branches[0]["name"], receipt["quarantine_name"])

        self.api.jobs = []
        result = self.run_reset(timeout=1)

        self.assertTrue(result["success"])
        self.assertEqual(self.api.branches[0]["name"], result["new_branch_name"])
        self.assertEqual(len([call for call in self.api.calls if call[0] == "PATCH"]), 1)

    def test_turbobulk_installation_rotates_to_unique_replacement_name(self):
        self.api.turbobulk_plugin = True
        result = self.run_reset()
        self.assertTrue(result["success"])
        self.assertEqual(self.api.branches[0]["name"], result["new_branch_name"])
        self.assertNotEqual(result["new_branch_name"], NAME)
        self.assertNotEqual(result["quarantine_name"], NAME)

    def test_late_original_name_job_cannot_target_unique_replacement(self):
        self.api.turbobulk_plugin = True
        self.api.queue_original_job_on_delete = True

        result = self.run_reset()

        self.assertTrue(result["success"])
        self.assertEqual(self.api.jobs[0]["data"]["branch"], NAME)
        self.assertNotEqual(result["new_branch_name"], NAME)
        self.assertEqual(self.api.branches[0]["name"], result["new_branch_name"])
        self.assertFalse(any(row["name"] == NAME for row in self.api.branches))

    def test_rename_response_loss_recovers_by_old_id(self):
        self.api.rename_error_after_accept = True
        with self.assertRaisesRegex(LoadError, "rename outcome is ambiguous"):
            self.run_reset()
        receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
        self.assertEqual(self.api.branches[0]["name"], receipt["quarantine_name"])

        result = self.run_reset()
        self.assertTrue(result["success"])
        self.assertEqual(self.api.branches[0]["name"], result["new_branch_name"])
        self.assertEqual(len([call for call in self.api.calls if call[0] == "PATCH"]), 1)

    def test_unrelated_and_terminal_jobs_do_not_block_reset(self):
        self.api.jobs = [
            {"id": 1, "name": "Bulk Load", "status": "running", "data": {"branch": "Other"}},
            {"id": 2, "name": "Unrelated Job", "status": "running", "data": {"branch": NAME}},
            *({"id": number, "name": "Bulk Load", "status": status, "data": {"branch": NAME}}
              for number, status in enumerate(("completed", "errored", "failed", "scheduled"), 3)),
        ]
        result = self.run_reset()
        self.assertTrue(result["success"])

    def test_job_appearing_after_initial_check_blocks_immediately_before_delete(self):
        self.api.jobs_on_read = 3
        self.api.late_jobs = [{"id": 51, "name": "Bulk Load", "status": "running",
                               "data": {"branch": NAME}}]
        with self.assertRaisesRegex(LoadError, "work reappeared"):
            self.run_reset()
        self.assertTrue(self.reset_root.exists())
        self.assertFalse(any(call[0] == "DELETE" for call in self.api.calls))

    def test_active_job_blocks_ambiguous_delete_resume(self):
        self.api.delete_error_before_accept = True
        with self.assertRaisesRegex(LoadError, "DELETE outcome is ambiguous"):
            self.run_reset()
        self.api.jobs = [{"id": 52, "name": "Bulk Load", "status": "pending",
                          "data": {"branch": NAME}}]

        with self.assertRaisesRegex(LoadError, "work reappeared"):
            self.run_reset()

        self.assertEqual(len([call for call in self.api.calls if call[0] == "DELETE"]), 1)

    def test_delete_response_loss_resumes_by_observing_old_id_absent(self):
        self.api.delete_error_after_accept = True
        with self.assertRaisesRegex(LoadError, "DELETE outcome is ambiguous"):
            self.run_reset()
        receipt_path = next(self.reset_root.glob("*.json"))
        first = json.loads(receipt_path.read_text())
        self.assertEqual(first["delete_outcome"], "ambiguous")

        result = self.run_reset()

        self.assertTrue(result["success"])
        self.assertEqual(result["new_branch"]["id"], 100)
        deletes = [call for call in self.api.calls if call[0] == "DELETE"]
        self.assertEqual(len(deletes), 1)

    def test_delete_response_loss_with_same_ready_survivor_retries_by_id(self):
        self.api.delete_error_before_accept = True
        with self.assertRaisesRegex(LoadError, "DELETE outcome is ambiguous"):
            self.run_reset()

        result = self.run_reset()

        self.assertTrue(result["success"])
        self.assertEqual(result["new_branch"]["id"], 100)
        self.assertEqual(len([call for call in self.api.calls if call[0] == "DELETE"]), 2)

    def test_asymmetric_create_and_delete_preflight_permissions_stop_before_delete(self):
        for attribute, message in (("create_allowed", "create permission"),
                                   ("delete_allowed", "delete capability")):
            with self.subTest(attribute=attribute):
                setattr(self.api, attribute, False)
                with self.assertRaisesRegex(LoadError, message):
                    self.run_reset()
                self.assertEqual([row["id"] for row in self.api.branches], [7])
                self.assertFalse(any(call[0] == "DELETE" for call in self.api.calls))
                setattr(self.api, attribute, True)
                self.api.calls.clear()
                for path in self.reset_root.glob("*.json"):
                    path.unlink()

    def test_definitive_http_rejection_is_not_recorded_as_ambiguous(self):
        self.api.delete_rejection = 403
        with self.assertRaisesRegex(LoadError, "DELETE was rejected with HTTP 403"):
            self.run_reset()
        receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
        self.assertEqual(receipt["delete_outcome"], "rejected")
        self.assertEqual(receipt["delete_http_status"], 403)
        self.assertNotEqual(receipt["delete_outcome"], "ambiguous")

    def test_definitive_create_rejection_is_not_recorded_as_ambiguous(self):
        self.api.create_rejection = 403
        with self.assertRaisesRegex(LoadError, "POST was rejected with HTTP 403"):
            self.run_reset()
        receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
        self.assertEqual(receipt["create_outcome"], "rejected")
        self.assertEqual(receipt["create_http_status"], 403)
        self.assertNotEqual(receipt["create_outcome"], "ambiguous")

    def test_ambiguous_delete_renamed_survivor_fails_closed_by_id(self):
        self.api.delete_error_before_accept = True
        with self.assertRaisesRegex(LoadError, "DELETE outcome is ambiguous"):
            self.run_reset()
        self.api.branches[0]["name"] = "Renamed by operator"

        with self.assertRaisesRegex(LoadError, "renamed"):
            self.run_reset()

        self.assertEqual(self.api.branches[0]["id"], 7)
        self.assertEqual(len([call for call in self.api.calls if call[0] == "DELETE"]), 1)

    def test_resumed_delete_requires_surviving_old_branch_still_ready(self):
        self.api.delete_error_before_accept = True
        with self.assertRaisesRegex(LoadError, "DELETE outcome is ambiguous"):
            self.run_reset()
        self.api.branches[0]["status"] = {"value": "failed"}

        with self.assertRaisesRegex(LoadError, "not ready"):
            self.run_reset()

        self.assertEqual(len([call for call in self.api.calls if call[0] == "DELETE"]), 1)

    def test_poll_timeout_resumes_created_branch_without_recreating(self):
        self.api.created_status = "provisioning"
        with self.assertRaisesRegex(LoadError, "did not become ready"):
            self.run_reset(timeout=0)
        self.api.branches[0]["status"] = {"value": "ready"}

        result = self.run_reset(timeout=1)

        self.assertTrue(result["success"])
        posts = [call for call in self.api.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)

    def test_create_response_loss_fails_closed_without_second_post(self):
        self.api.create_error_after_accept = True
        with self.assertRaisesRegex(LoadError, "POST outcome is ambiguous"):
            self.run_reset()
        with self.assertRaisesRegex(LoadError, "appeared.*ambiguous"):
            self.run_reset()
        posts = [call for call in self.api.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)

    def test_refuses_blank_and_main_before_connecting(self):
        with patch.object(subject, "Client", side_effect=AssertionError("must not connect")):
            for name in ("", "  ", "main", "MAIN"):
                with self.subTest(name=name), self.assertRaisesRegex(LoadError, "blank or main"):
                    subject.reset(TARGET, name, token="nbt_secret")

    def test_requires_branching_plugin(self):
        self.api.plugin = False
        with self.assertRaisesRegex(LoadError, "does not report.*Branching"):
            self.run_reset()
        self.assertFalse(self.reset_root.exists())

    def test_refuses_missing_multiple_and_every_nonready_branch(self):
        cases = (
            ([], "found 0"),
            ([branch(7), branch(8)], "found 2"),
            *(([branch(7, status)], "not ready") for status in (
                "new", "provisioning", "syncing", "migrating", "merging", "reverting",
                "failed", "merged", "archived", "pending-migrations")),
        )
        for rows, message in cases:
            with self.subTest(message=message):
                self.api.branches = rows
                with self.assertRaisesRegex(LoadError, message):
                    self.run_reset()
                if self.reset_root.exists():
                    for path in self.reset_root.glob("*.json"):
                        path.unlink()

    def test_exact_name_filter_rejects_wrong_result(self):
        self.api.branches = [branch(7, name="Generator Benchmark copy")]
        with self.assertRaisesRegex(LoadError, "found 0"):
            self.run_reset()

    def test_failed_recreated_branch_is_recorded(self):
        self.api.created_status = "failed"
        with self.assertRaisesRegex(LoadError, "reached 'failed'"):
            self.run_reset()
        receipt = json.loads(next(self.reset_root.glob("*.json")).read_text())
        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["new_branch"]["status"], "failed")
        self.assertIn("failed", receipt["error"])

    def test_cli_prints_actual_receipt_and_new_schema_id(self):
        receipt_path = self.reset_root / "explicit.json"
        output = io.StringIO()
        with patch.dict(os.environ, {"NETBOX_TOKEN": "nbt_secret"}), redirect_stdout(output):
            code = subject.main([TARGET, NAME, "--receipt", str(receipt_path)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["receipt"], str(receipt_path))
        self.assertEqual(result["new_branch_name"], self.api.branches[0]["name"])
        self.assertNotEqual(result["new_branch_name"], NAME)
        self.assertEqual(result["new_schema_id"], "branch_100")


if __name__ == "__main__":
    unittest.main()
