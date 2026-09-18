"""The supported loader must fail before target writes when its contract is unsafe."""

import hashlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock
import urllib.parse
from unittest.mock import ANY, Mock, patch

from estates.model import canonical, digest
from estates.turbobulk import (COMPILER_VERSION, DEFAULT_JOB_ROWS, POST_HOOKS, RECEIPT_VERSION, Client,
                               JobTimeout, LoadError, WorkerDied,
                               _arbitrate_worker_death, _resolve_worker_death,
                               _bounded_readback, _readback_fields,
                               _adopt_finalizer_job, _artifact, _batch_request_settings,
                               _complete_rest, _job_result,
                               _component_cache_ids, _component_filter_preflight,
                               _finalizer_plan, _finalizer_request_settings, _job_request_schedule,
                               _bulk_patch, _load_model_batches, _load_termination_batches,
                               _reconcile_rest_patches, _rest_payload, _run_finalizers,
                               _matches, _NoRedirect, _refresh, _render, _rendered_columns,
                               _poll, _review_history_preflight, _schema_preflight, _token,
                               _verify_component_caches, _verify_review_history, _write_receipt,
                               delivery_contract, load)


class FakeClient:
    def __init__(self, models, schemas):
        self.models = models
        self.schemas = schemas

    def request(self, path, **_kwargs):
        if path.endswith("/models/"):
            return 200, [{"full_name": name, "export_only": False} for name in self.models]
        model = path.removeprefix("/api/plugins/turbobulk/models/").removesuffix("/")
        return 200, {"fields": [{"name": name} for name in self.schemas[model]]}


def response(value):
    result = io.BytesIO(json.dumps(value).encode())
    result.status = 200
    return result


class TurboBulkLoaderTests(unittest.TestCase):
    def test_hook_schedule_separates_rows_from_global_finalizers(self):
        objects = {
            "tag:a": {"key": "tag:a", "kind": "tag", "attrs": {}, "refs": {}},
            "tag:b": {"key": "tag:b", "kind": "tag", "attrs": {}, "refs": {}},
            "cable:a": {"key": "cable:a", "kind": "cable", "attrs": {}, "refs": {}},
            "cable:b": {"key": "cable:b", "kind": "cable", "attrs": {}, "refs": {}},
        }
        base = delivery_contract("reviewable")["request_settings"]
        schedule = _job_request_schedule(
            [["tag:a", "cable:a"], ["tag:b", "cable:b"]], objects, 1, base)

        self.assertFalse(any(schedule["phase-1:tag"]["post_hooks"].values()))
        self.assertFalse(any(schedule["phase-1:cable"]["post_hooks"].values()))
        self.assertFalse(any(schedule[
            "phase-1:cable:terminations:batch-2-of-2"]["post_hooks"].values()))
        self.assertFalse(any(schedule["phase-2:cable"]["post_hooks"].values()))
        self.assertFalse(any(schedule[
            "phase-2:cable:terminations:batch-2-of-2"]["post_hooks"].values()))
        search = schedule["finalize:dcim.cable:rebuild_search_index"]
        self.assertTrue(search["post_hooks"]["rebuild_search_index"])
        self.assertEqual(sum(search["post_hooks"].values()), 1)
        self.assertFalse(search["create_changelogs"])
        self.assertTrue(schedule[
            "finalize:dcim.cabletermination:fix_cable_links"]["post_hooks"]["fix_cable_links"])
        self.assertTrue(schedule[
            "finalize:dcim.cabletermination:rebuild_cable_paths"]["post_hooks"]["rebuild_cable_paths"])

    def test_interface_refresh_is_indexed_at_75k_scale(self):
        count = 75_519
        candidates = [{"key": f"interface:{number}", "kind": "interface",
                       "attrs": {"name": f"Ethernet{number}"},
                       "refs": {"device": "device:1"}}
                      for number in range(count)]
        rows = [{"id": number + 1, "name": f"Ethernet{number}", "device": {"id": 7}}
                for number in range(count)]

        class Target:
            def all(self, _path):
                return rows

        ids = {"device:1": 7}
        with patch("estates.turbobulk._matches", wraps=_matches) as matcher:
            _refresh(Target(), "interface", candidates, ids)
        self.assertEqual(len(ids), count + 1)
        self.assertEqual(matcher.call_count, count)

    def test_indexed_generic_identity_keeps_content_type_in_the_key(self):
        device = {"key": "device:1", "kind": "device", "attrs": {}, "refs": {}}
        contact = {"key": "contact:1", "kind": "contact", "attrs": {}, "refs": {}}
        role = {"key": "role:1", "kind": "contact_role", "attrs": {}, "refs": {}}
        assignment = {"key": "assignment:1", "kind": "contact_assignment", "attrs": {},
                      "refs": {"object": "device:1", "contact": "contact:1", "role": "role:1"}}
        objects = {obj["key"]: obj for obj in (device, contact, role, assignment)}
        ids = {"device:1": 5, "contact:1": 6, "role:1": 7}

        class Target:
            def all(self, _path):
                return [
                    {"id": 80, "object_id": 5, "object_type": "virtualization.virtualmachine",
                     "contact": {"id": 6}, "role": {"id": 7}},
                    {"id": 81, "object_id": 5, "object_type": "dcim.device",
                     "contact": {"id": 6}, "role": {"id": 7}},
                ]

        _refresh(Target(), "contact_assignment", [assignment], ids, objects=objects)
        self.assertEqual(ids["assignment:1"], 81)

    def test_indexed_refresh_still_rejects_ambiguous_natural_identity(self):
        interface = {"key": "interface:1", "kind": "interface",
                     "attrs": {"name": "Ethernet1"}, "refs": {"device": "device:1"}}

        class Target:
            def all(self, _path):
                return [{"id": 1, "name": "Ethernet1", "device": {"id": 7}},
                        {"id": 2, "name": "Ethernet1", "device": {"id": 7}}]

        with self.assertRaisesRegex(LoadError, r"target identity is ambiguous \(2 rows\)"):
            _refresh(Target(), "interface", [interface], {"device:1": 7})

    def test_scale_data_jobs_are_bounded_and_run_no_global_hooks(self):
        candidates = [{"key": f"tag:{number}", "kind": "tag",
                       "attrs": {"name": str(number)}, "refs": {}}
                      for number in range(4_706)]
        receipt = {"jobs": [], "resolved_ids": {}}
        ids = {}
        submissions = []
        refreshes = []

        def submit(_client, _branch, _model, rows, purpose, keys, *_args, **kwargs):
            submissions.append((purpose, len(rows), list(keys), kwargs["request_settings"]))

        def refresh(_client, _kind, batch, resolved, *, allow_missing=False, **_kwargs):
            refreshes.append(list(batch))
            if not allow_missing:
                resolved.update({obj["key"]: number for number, obj in enumerate(batch, 1)})

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit", side_effect=submit), \
                patch("estates.turbobulk._refresh", side_effect=refresh):
            _load_model_batches(
                object(), "Demo", "tag", candidates, {obj["key"]: obj for obj in candidates},
                ids, {}, "protocol_ports", "phase-1:tag", receipt,
                Path(temporary) / "receipt.json", 1, DEFAULT_JOB_ROWS,
                delivery_contract("reviewable")["request_settings"])

        self.assertEqual([(row[0], row[1]) for row in submissions], [
            ("phase-1:tag:batch-1-of-3", 2_000),
            ("phase-1:tag:batch-2-of-3", 2_000),
            ("phase-1:tag:batch-3-of-3", 706),
        ])
        self.assertTrue(all(not any(row[3]["post_hooks"].values()) for row in submissions))
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(len(refreshes[0]), 4_706)

    def test_cable_data_batches_run_no_global_hooks(self):
        rows = [{"cable_id": number} for number in range(4_001)]
        submissions = []

        def submit(_client, _branch, _model, batch, purpose, _keys, *_args, **kwargs):
            submissions.append((purpose, len(batch), kwargs["request_settings"]))

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit", side_effect=submit):
            _load_termination_batches(
                object(), "Demo", rows, "phase-9:cable:terminations", {"jobs": []},
                Path(temporary) / "receipt.json", 1, DEFAULT_JOB_ROWS,
                delivery_contract("reviewable")["request_settings"])

        self.assertEqual([row[1] for row in submissions], [2_000, 2_000, 1])
        self.assertTrue(all(not any(row[2]["post_hooks"].values()) for row in submissions))

    def test_finalizers_are_zero_row_single_hook_jobs(self):
        objects = {obj["key"]: obj for obj in [
            {"key": "tag:a", "kind": "tag", "attrs": {}, "refs": {}},
            {"key": "owner:a", "kind": "owner", "attrs": {}, "refs": {}},
            {"key": "cable:a", "kind": "cable", "attrs": {}, "refs": {}},
            {"key": "device:a", "kind": "device", "attrs": {}, "refs": {}},
            {"key": "interface:a", "kind": "interface", "attrs": {}, "refs": {}},
            {"key": "vm:a", "kind": "virtual_machine", "attrs": {}, "refs": {}},
            {"key": "vm-interface:a", "kind": "vm_interface", "attrs": {}, "refs": {}},
        ]}
        receipt = {"jobs": []}
        submissions = []

        def submit(_client, _branch, model, rows, purpose, keys, *_args, **kwargs):
            submissions.append((purpose, model, rows, keys, kwargs["request_settings"]))

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit", side_effect=submit):
            _run_finalizers(object(), "Demo", objects, receipt,
                            Path(temporary) / "receipt.json", 1,
                            delivery_contract("reviewable")["request_settings"])

        self.assertEqual(len(submissions), 11)
        self.assertTrue(all(rows == [] and keys == []
                            for _purpose, _model, rows, keys, _settings in submissions))
        self.assertTrue(all(not settings["create_changelogs"]
                            for *_rest, settings in submissions))
        self.assertTrue(all(sum(settings["post_hooks"].values()) == 1
                            for *_rest, settings in submissions))
        purposes = {purpose for purpose, *_rest in submissions}
        self.assertIn("finalize:dcim.cabletermination:rebuild_cable_paths", purposes)
        self.assertIn("finalize:dcim.device:fix_denormalized", purposes)
        self.assertIn("finalize:dcim.device:fix_counters", purposes)
        self.assertIn("finalize:virtualization.virtualmachine:fix_counters", purposes)
        self.assertIn("finalize:extras.tag:rebuild_search_index", purposes)
        self.assertNotIn("finalize:users.owner:rebuild_search_index", purposes)

    def test_verified_finalizer_is_not_resubmitted(self):
        objects = {"tag:a": {"key": "tag:a", "kind": "tag", "attrs": {}, "refs": {}}}
        base = delivery_contract("reviewable")["request_settings"]
        plan = _finalizer_plan(objects)
        receipt = {"jobs": [{"purpose": purpose, "model": model, "mode": "insert",
                              "rows_expected": 0, "request_verified": True,
                              "request_settings": _finalizer_request_settings(base, hook)}
                             for purpose, model, hook in plan]}
        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit") as submit, \
                patch("estates.turbobulk._poll") as poll:
            _run_finalizers(object(), "Demo", objects, receipt,
                            Path(temporary) / "receipt.json", 1, base)
        submit.assert_not_called()
        poll.assert_not_called()

    def test_lost_finalizer_submission_adopts_exactly_one_job_and_persists(self):
        settings = _finalizer_request_settings(
            delivery_contract("reviewable")["request_settings"], "rebuild_search_index")
        entry = {"purpose": "finalize:extras.tag:rebuild_search_index",
                 "model": "extras.tag", "mode": "insert", "rows_expected": 0,
                 "request_settings": settings,
                 "intent_recorded_at": "2026-09-13T01:00:00+00:00"}
        receipt = {"failed_at": "2026-09-13T01:02:00+00:00", "jobs": [entry]}
        detail = {"job_id": "job-candidate", "status": "completed",
                  "created": "2026-09-13T01:00:30+00:00", "error": "",
                  "data": {"branch": "Demo", "model": "extras.tag", "mode": "insert",
                           "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0,
                           "changelogs_created": 0, "errors": [],
                           "post_hooks": {
                               name: ({"success": True} if name == "rebuild_search_index"
                                      else {"skipped": True})
                               for name in POST_HOOKS}}}
        case = self

        class Target:
            def request(self, path, **kwargs):
                case.assertFalse(kwargs.get("branch", True))
                if path.startswith("/api/core/jobs/?"):
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                    case.assertIn("created__after", query)
                    case.assertIn("created__before", query)
                    return 200, {"results": [{"job_id": "job-candidate", "name": "Bulk Load"}]}
                case.assertEqual(path, "/api/plugins/turbobulk/jobs/job-candidate/")
                return 200, detail

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            _adopt_finalizer_job(Target(), "Demo", entry, receipt, receipt_path)
            saved = json.loads(receipt_path.read_text())["jobs"][0]
        self.assertEqual(saved["job_id"], "job-candidate")
        self.assertEqual(saved["status"], "adopted")
        self.assertEqual(saved["adoption_evidence"], {
            "created": "2026-09-13T01:00:30+00:00", "model": "extras.tag",
            "branch": "Demo", "rows_processed": 0})

    def test_lost_finalizer_submission_refuses_zero_or_multiple_matches(self):
        for count in (0, 2):
            with self.subTest(count=count):
                settings = _finalizer_request_settings(
                    delivery_contract("reviewable")["request_settings"],
                    "rebuild_search_index")
                entry = {"purpose": "finalize:extras.tag:rebuild_search_index",
                         "model": "extras.tag", "mode": "insert", "rows_expected": 0,
                         "request_settings": settings,
                         "intent_recorded_at": "2026-09-13T01:00:00+00:00"}
                receipt = {"failed_at": "2026-09-13T01:02:00+00:00", "jobs": [entry]}

                class Target:
                    def request(self, path, **_kwargs):
                        if path.startswith("/api/core/jobs/?"):
                            return 200, {"results": [
                                {"job_id": f"job-{number}", "name": "Bulk Load"}
                                for number in range(count)]}
                        job_id = path.removeprefix(
                            "/api/plugins/turbobulk/jobs/").removesuffix("/")
                        return 200, {"job_id": job_id, "status": "completed", "error": "",
                                     "data": {
                            "branch": "Demo", "model": "extras.tag", "mode": "insert",
                            "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0,
                            "changelogs_created": 0, "errors": [],
                            "post_hooks": {
                                name: ({"success": True} if name == "rebuild_search_index"
                                       else {"skipped": True})
                                for name in POST_HOOKS}}}

                with tempfile.TemporaryDirectory() as temporary:
                    receipt_path = Path(temporary) / "receipt.json"
                    receipt_path.write_text(json.dumps(receipt))
                    with self.assertRaisesRegex(LoadError, f"found {count} matching zero-row jobs"):
                        _adopt_finalizer_job(Target(), "Demo", entry, receipt, receipt_path)
                self.assertNotIn("job_id", entry)

    def test_lost_finalizer_submission_rejects_wrong_hook_result(self):
        settings = _finalizer_request_settings(
            delivery_contract("reviewable")["request_settings"], "rebuild_search_index")
        entry = {"purpose": "finalize:extras.tag:rebuild_search_index",
                 "model": "extras.tag", "mode": "insert", "rows_expected": 0,
                 "request_settings": settings,
                 "intent_recorded_at": "2026-09-13T01:00:00+00:00"}
        receipt = {"failed_at": "2026-09-13T01:02:00+00:00", "jobs": [entry]}

        class Target:
            def request(self, path, **_kwargs):
                if path.startswith("/api/core/jobs/?"):
                    return 200, {"results": [{"job_id": "wrong-hook", "name": "Bulk Load"}]}
                return 200, {"job_id": "wrong-hook", "status": "completed", "error": "",
                             "data": {
                                 "branch": "Demo", "model": "extras.tag", "mode": "insert",
                                 "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0,
                                 "changelogs_created": 0, "errors": [],
                                 "post_hooks": {
                                     name: ({"success": True} if name == "fix_counters"
                                            else {"skipped": True})
                                     for name in POST_HOOKS}}}

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(LoadError, "found 0 matching zero-row jobs"):
                _adopt_finalizer_job(Target(), "Demo", entry, receipt, receipt_path)
        self.assertNotIn("job_id", entry)

    def test_lost_finalizer_submission_refuses_paginated_candidate_window(self):
        settings = _finalizer_request_settings(
            delivery_contract("reviewable")["request_settings"], "rebuild_search_index")
        entry = {"purpose": "finalize:extras.tag:rebuild_search_index",
                 "model": "extras.tag", "mode": "insert", "rows_expected": 0,
                 "request_settings": settings,
                 "intent_recorded_at": "2026-09-13T01:00:00+00:00"}
        receipt = {"failed_at": "2026-09-13T01:02:00+00:00", "jobs": [entry]}
        client = Mock()
        client.request.return_value = (200, {
            "count": 2, "next": "https://netbox.example/api/core/jobs/?offset=1",
            "results": [{"job_id": "job-1", "name": "Bulk Load"}],
        })
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(LoadError, "matching job window is paginated"):
                _adopt_finalizer_job(client, "Demo", entry, receipt,
                                     Path(temporary) / "receipt.json")
        self.assertEqual(client.request.call_count, 1)
        self.assertNotIn("job_id", entry)

    def test_lost_nonfinalizer_submission_refuses_without_querying(self):
        entry = {"purpose": "phase-1:tag", "model": "extras.tag", "mode": "insert",
                 "rows_expected": 1, "intent_recorded_at": "2026-09-13T01:00:00+00:00"}
        receipt = {"failed_at": "2026-09-13T01:02:00+00:00", "jobs": [entry]}
        client = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(LoadError, "durable intent exists without a job ID"):
                _adopt_finalizer_job(client, "Demo", entry, receipt,
                                     Path(temporary) / "receipt.json")
        client.request.assert_not_called()

    def test_terminal_failed_finalizer_is_recorded_and_retried(self):
        objects = {"tag:a": {"key": "tag:a", "kind": "tag", "attrs": {}, "refs": {}}}
        base = delivery_contract("reviewable")["request_settings"]
        purpose, model, hook = _finalizer_plan(objects)[0]
        settings = _finalizer_request_settings(base, hook)
        prior = {"purpose": purpose, "model": model, "mode": "insert",
                 "job_id": "failed-finalizer", "rows_expected": 0,
                 "request_settings": settings, "request_verified": False}
        receipt = {"jobs": [prior]}
        terminal = {"job_id": "failed-finalizer", "status": "completed", "data": {
            "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0,
            "changelogs_created": 0, "errors": [],
            "post_hooks": {name: ({"error": "reindex failed"} if name == hook else {"skipped": True})
                           for name in POST_HOOKS}}}
        submissions = []

        def submit(_client, _branch, _model, _rows, submitted_purpose,
                   _keys, *_args, **_kwargs):
            submissions.append(submitted_purpose)

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._poll", return_value=terminal), \
                patch("estates.turbobulk._submit", side_effect=submit):
            _run_finalizers(object(), "Demo", objects, receipt,
                            Path(temporary) / "receipt.json", 1, base)

        self.assertTrue(prior["retryable_finalizer"])
        self.assertEqual(submissions.count(purpose), 1)

    def test_second_terminal_finalizer_failure_requires_a_fresh_branch(self):
        objects = {"tag:a": {"key": "tag:a", "kind": "tag", "attrs": {}, "refs": {}}}
        base = delivery_contract("reviewable")["request_settings"]
        purpose, model, hook = _finalizer_plan(objects)[0]
        settings = _finalizer_request_settings(base, hook)
        receipt = {"jobs": [
            {"purpose": purpose, "model": model, "mode": "insert", "job_id": job_id,
             "rows_expected": 0, "request_settings": settings, "request_verified": False}
            for job_id in ("failed-finalizer-1", "failed-finalizer-2")
        ]}
        terminal = {"job_id": "failed-finalizer-2", "status": "completed", "data": {
            "rows_processed": 0, "rows_inserted": 0, "rows_updated": 0,
            "changelogs_created": 0, "errors": [],
            "post_hooks": {name: ({"error": "reindex failed"} if name == hook else {"skipped": True})
                           for name in POST_HOOKS}}}

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._poll", return_value=terminal), \
                patch("estates.turbobulk._submit") as submit:
            with self.assertRaisesRegex(LoadError, "failed after 2 attempts.*fresh branch"):
                _run_finalizers(object(), "Demo", objects, receipt,
                                Path(temporary) / "receipt.json", 1, base)
        submit.assert_not_called()

    def test_batch_resume_keeps_original_boundaries_and_skips_verified_jobs(self):
        candidates = [{"key": f"tag:{number}", "kind": "tag",
                       "attrs": {"name": str(number)}, "refs": {}}
                      for number in range(5)]
        base = delivery_contract("reviewable")["request_settings"]
        receipt = {"jobs": [{"purpose": "phase-1:tag:batch-1-of-3",
                              "request_settings": _batch_request_settings(base),
                              "request_verified": True}], "resolved_ids": {}}
        submitted = []

        def submit(_client, _branch, _model, _rows, purpose, _keys, *_args, **_kwargs):
            submitted.append(purpose)

        def refresh(_client, _kind, batch, resolved, *, allow_missing=False, **_kwargs):
            if not allow_missing:
                resolved.update({obj["key"]: number + 1 for number, obj in enumerate(batch)})

        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit", side_effect=submit), \
                patch("estates.turbobulk._refresh", side_effect=refresh):
            _load_model_batches(
                object(), "Demo", "tag", candidates, {obj["key"]: obj for obj in candidates},
                {}, {}, "protocol_ports", "phase-1:tag", receipt,
                Path(temporary) / "receipt.json", 1, 2,
                base)

        self.assertEqual(submitted, ["phase-1:tag:batch-2-of-3", "phase-1:tag:batch-3-of-3"])

    def test_batch_resume_rejects_a_prior_job_with_the_wrong_hook_schedule(self):
        candidates = [{"key": f"tag:{number}", "kind": "tag",
                       "attrs": {"name": str(number)}, "refs": {}}
                      for number in range(3)]
        base = delivery_contract("reviewable")["request_settings"]
        wrong = _batch_request_settings(base)
        wrong["post_hooks"]["rebuild_search_index"] = True
        receipt = {"jobs": [{"purpose": "phase-1:tag:batch-1-of-2",
                              "request_settings": wrong, "request_verified": True}]}
        with tempfile.TemporaryDirectory() as temporary, \
                patch("estates.turbobulk._submit") as submit, \
                patch("estates.turbobulk._refresh") as refresh:
            with self.assertRaisesRegex(LoadError, "different request settings"):
                _load_model_batches(
                    object(), "Demo", "tag", candidates,
                    {obj["key"]: obj for obj in candidates}, {}, {}, "protocol_ports",
                    "phase-1:tag", receipt, Path(temporary) / "receipt.json", 1, 2, base)
        submit.assert_not_called()
        refresh.assert_not_called()

    def test_batch_settings_are_independent_from_delivery_contract(self):
        base = delivery_contract("reviewable")["request_settings"]
        batched = _batch_request_settings(base)
        batched["post_hooks"]["fix_counters"] = True
        self.assertTrue(all(base["post_hooks"].values()))

    def test_operator_interrupt_closes_receipt_attempt(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag",
             "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}},
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None

            def request(self, path, **_kwargs):
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                if path.startswith("/api/plugins/branching/branches/"):
                    return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                               "status": {"value": "ready"}}]}
                if path == "/api/core/object-changes/?limit=1":
                    return 200, {"count": 0, "results": []}
                raise AssertionError(path)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({
                "status": "passed", "plan_sha256": digest(plan)}))
            receipt_path = root / "receipt.json"
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": []}), \
                    patch("estates.turbobulk.verify_plan", return_value={"success": False,
                                                                          "ids": {}}), \
                    patch("estates.turbobulk._schema_preflight", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=receipt_path, delivery_policy="disposable-baseline")

            receipt = json.loads(receipt_path.read_text())
            self.assertFalse(receipt["success"])
            self.assertEqual(receipt["error"], "KeyboardInterrupt")
            self.assertEqual(receipt["attempts"][-1]["result"], "failed")
            self.assertEqual(receipt["attempts"][-1]["error"], "KeyboardInterrupt")
            self.assertIn("completed_at", receipt["attempts"][-1])

    def test_exact_recovery_interrupt_closes_receipt_attempt(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag",
             "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}},
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None

            def request(self, path, **_kwargs):
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                if path.startswith("/api/plugins/branching/branches/"):
                    return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                               "status": {"value": "ready"}}]}
                if path == "/api/core/object-changes/?limit=1":
                    return 200, {"count": 0, "results": []}
                raise AssertionError(path)

        verified = {"success": True, "ids": {"tag:demo": 41}, "matched_objects": 1}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({
                "status": "passed", "plan_sha256": digest(plan)}))
            receipt_path = root / "receipt.json"
            common = [patch("estates.turbobulk.Client", return_value=Target()),
                      patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}),
                      patch("estates.turbobulk.verify_plan", return_value=verified)]
            with common[0], common[1], common[2]:
                load(root, url="https://netbox.example", token="fixture", branch="Demo",
                     receipt_path=receipt_path, delivery_policy="disposable-baseline")
            receipt = json.loads(receipt_path.read_text())
            base = delivery_contract("disposable-baseline")["request_settings"]
            purpose, model, hook = _finalizer_plan({"tag:demo": plan["objects"][0]})[0]
            receipt["jobs"] = [
                {"purpose": "phase-1:tag", "model": "extras.tag",
                 "mode": "insert", "rows_expected": 1, "request_verified": True,
                 "request_settings": _batch_request_settings(base)},
                {"purpose": purpose, "model": model, "mode": "insert",
                 "job_id": "pending-finalizer", "rows_expected": 0,
                 "request_verified": False,
                 "request_settings": _finalizer_request_settings(base, hook)},
            ]
            receipt_path.write_text(json.dumps(receipt))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified), \
                    patch("estates.turbobulk._run_finalizers", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=receipt_path, delivery_policy="disposable-baseline")

            interrupted = json.loads(receipt_path.read_text())
            self.assertFalse(interrupted["success"])
            self.assertEqual(interrupted["attempts"][-1]["result"], "failed")
            self.assertEqual(interrupted["attempts"][-1]["error"], "KeyboardInterrupt")

    def test_delivery_contract_keeps_reviewable_default_and_explicit_disposable_tradeoff(self):
        reviewable = delivery_contract("reviewable")
        self.assertTrue(reviewable["request_settings"]["create_changelogs"])
        self.assertTrue(all(reviewable["branch_capabilities"].values()))
        self.assertEqual(reviewable["request_settings"]["post_hooks"], POST_HOOKS)
        self.assertFalse(reviewable["request_settings"]["dispatch_events"])

        disposable = delivery_contract("disposable-baseline")
        self.assertFalse(disposable["request_settings"]["create_changelogs"])
        self.assertFalse(any(disposable["branch_capabilities"].values()))
        self.assertEqual(disposable["request_settings"]["post_hooks"], POST_HOOKS)

        with self.assertRaisesRegex(LoadError, "unsupported delivery policy"):
            delivery_contract("fast")

    def test_token_normalization_and_target_validation(self):
        self.assertEqual(_token("Bearer nbt_key.secret\n"), "nbt_key.secret")
        self.assertEqual(_token("Token legacy"), "legacy")
        self.assertEqual(Client("https://netbox.example", "Bearer nbt_key.secret").headers["Authorization"],
                         "Bearer nbt_key.secret")
        with self.assertRaises(LoadError):
            Client("https://user:secret@netbox.example", "token")

    @patch("estates.turbobulk.time.sleep")
    def test_client_retries_reads_but_never_writes(self, sleep):
        client = Client("https://netbox.example", "token")
        client.opener = Mock()
        client.opener.open.side_effect = [ConnectionResetError(), response({"ok": True})]
        self.assertEqual(client.request("/api/status/"), (200, {"ok": True}))
        self.assertEqual(client.opener.open.call_count, 2)
        sleep.assert_called_once_with(0.5)

        client.opener.reset_mock()
        client.opener.open.side_effect = ConnectionResetError()
        with self.assertRaisesRegex(LoadError, "without retry"):
            client.request("/api/write/", method="POST", body=b"{}")
        client.opener.open.assert_called_once()
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

    def test_device_components_compile_parent_placement_caches(self):
        objects = {
            "site:1": {"key": "site:1", "kind": "site", "attrs": {}, "refs": {}},
            "location:1": {"key": "location:1", "kind": "location", "attrs": {},
                           "refs": {"site": "site:1"}},
            "rack:1": {"key": "rack:1", "kind": "rack", "attrs": {},
                       "refs": {"location": "location:1"}},
            "device:1": {"key": "device:1", "kind": "device", "attrs": {},
                         "refs": {"site": "site:1", "rack": "rack:1"}},
            "interface:1": {"key": "interface:1", "kind": "interface",
                            "attrs": {"name": "Ethernet1", "type": "1000base-t"},
                            "refs": {"device": "device:1"}},
        }
        ids = {"site:1": 10, "location:1": 20, "rack:1": 30, "device:1": 40}

        self.assertEqual(_component_cache_ids(objects["interface:1"], objects, ids), {
            "_site_id": 10, "_location_id": 20, "_rack_id": 30,
        })
        row = _render(objects["interface:1"], objects, ids, {})
        self.assertEqual((row["_site_id"], row["_location_id"], row["_rack_id"]),
                         (10, 20, 30))
        objects["device:1"]["refs"]["location"] = "location:1"
        self.assertEqual(_component_cache_ids(objects["interface:1"], objects, ids), {
            "_site_id": 10, "_location_id": 20, "_rack_id": 30,
        })
        self.assertEqual(_rendered_columns(objects["interface:1"]), {
            "name", "type", "device_id", "_site_id", "_location_id", "_rack_id",
        })

    def test_component_cache_readback_uses_exact_placement_filters(self):
        objects = {
            "site:1": {"key": "site:1", "kind": "site", "attrs": {}, "refs": {}},
            "device:1": {"key": "device:1", "kind": "device", "attrs": {},
                         "refs": {"site": "site:1"}},
            "interface:1": {"key": "interface:1", "kind": "interface", "attrs": {},
                            "refs": {"device": "device:1"}},
        }
        ids = {"site:1": 10, "device:1": 40, "interface:1": 50}

        class Target:
            paths = []

            def all(self, path):
                self.paths.append(path)
                return [{"id": 50}]

        target = Target()
        result = _verify_component_caches(target, objects, ids)
        self.assertEqual(result["components_expected"], 1)
        self.assertEqual(result["failures"], [])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(target.paths[0]).query)
        self.assertEqual(query, {"brief": ["1"], "site_id": ["10"],
                                 "location_id": ["null"], "rack_id": ["null"]})

        class Stale(Target):
            def all(self, path):
                self.paths.append(path)
                return []

        stale = _verify_component_caches(Stale(), objects, ids)
        self.assertEqual(stale["failures"][0]["missing_ids"], [50])

    def test_component_cache_readback_sorts_null_and_numeric_placements(self):
        objects = {
            "site:1": {"key": "site:1", "kind": "site", "attrs": {}, "refs": {}},
            "location:1": {"key": "location:1", "kind": "location", "attrs": {},
                           "refs": {"site": "site:1"}},
            "device:1": {"key": "device:1", "kind": "device", "attrs": {},
                         "refs": {"site": "site:1"}},
            "device:2": {"key": "device:2", "kind": "device", "attrs": {},
                         "refs": {"site": "site:1", "location": "location:1"}},
            "interface:1": {"key": "interface:1", "kind": "interface", "attrs": {},
                            "refs": {"device": "device:1"}},
            "interface:2": {"key": "interface:2", "kind": "interface", "attrs": {},
                            "refs": {"device": "device:2"}},
        }
        ids = {"site:1": 10, "location:1": 20, "device:1": 40, "device:2": 41,
               "interface:1": 50, "interface:2": 51}

        class Target:
            def all(self, path):
                location = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["location_id"][0]
                return [{"id": 50 if location == "null" else 51}]

        result = _verify_component_caches(Target(), objects, ids)
        self.assertEqual(result["placement_queries"], 2)
        self.assertEqual(result["failures"], [])

    def test_component_cache_filter_preflight_requires_all_filters(self):
        objects = {"interface:1": {"key": "interface:1", "kind": "interface",
                                    "attrs": {}, "refs": {}}}

        class Target:
            def __init__(self, names):
                self.names = names

            def request(self, path, **_kwargs):
                self.path = path
                return 200, {"paths": {"/api/dcim/interfaces/": {"get": {
                    "parameters": [{"$ref": f"#/components/parameters/{name}"}
                                   for name in self.names],
                }}}, "components": {"parameters": {
                    name: {"name": name, "in": "query"} for name in self.names
                }}}

        target = Target({"site_id", "location_id", "rack_id"})
        self.assertEqual(_component_filter_preflight(target, objects), {
            "interface": ["location_id", "rack_id", "site_id"],
        })
        self.assertEqual(target.path, "/api/schema/?format=json")

        with self.assertRaisesRegex(LoadError, "lacks component-cache readback filters: rack_id"):
            _component_filter_preflight(Target({"site_id", "location_id"}), objects)

    def test_terminal_job_must_account_for_every_row_and_hook(self):
        settings = delivery_contract("reviewable")["request_settings"]
        hooks = {name: {"success": True} for name in POST_HOOKS}
        hooks["fix_cable_links"] = {"skipped": True, "reason": "not applicable"}
        job = {"job_id": "job-1", "status": "completed", "data": {
            "rows_processed": 2, "rows_inserted": 2, "rows_updated": 0, "changelogs_created": 2,
            "errors": [], "post_hooks": hooks}}
        self.assertEqual(_job_result(job, 2, settings, "insert")["rows_inserted"], 2)

        for mutate, message in (
                (lambda value: value["data"].pop("post_hooks"), "post-hook results are missing"),
                (lambda value: value["data"]["post_hooks"].pop("fix_counters"),
                 "missing fix_counters"),
                (lambda value: value["data"]["post_hooks"].update(
                    {"unknown_hook": {"success": True}}), "unexpected unknown_hook"),
                (lambda value: value["data"]["post_hooks"].update({
                    "rebuild_search_index": {"success": True, "message": "Reindex failed",
                                             "error": "command exited 1"}}),
                 "rebuild_search_index reported an error"),
                (lambda value: value["data"]["post_hooks"].update(
                    {"fix_cable_links": {"skipped": True}}), "neither succeeded"),
                (lambda value: value["data"].update({"changelogs_created": 0}),
                 "0 changelogs instead of 2"),
                (lambda value: value["data"].update({"changelogs_created": 1}),
                 "1 changelogs instead of 2")):
            broken = deepcopy(job)
            mutate(broken)
            with self.assertRaisesRegex(LoadError, message):
                _job_result(broken, 2, settings, "insert")

        deferred = _batch_request_settings(settings)
        skipped = deepcopy(job)
        skipped["data"]["post_hooks"] = {name: {"skipped": True} for name in POST_HOOKS}
        self.assertEqual(_job_result(skipped, 2, deferred, "insert")["rows_inserted"], 2)
        skipped["data"]["post_hooks"]["fix_counters"] = {"success": True}
        with self.assertRaisesRegex(LoadError, "disabled post-hook fix_counters"):
            _job_result(skipped, 2, deferred, "insert")

    def test_review_history_requires_empty_start_and_exact_create_counts(self):
        objects = {"tag:demo": {"kind": "tag"}, "cable:demo": {"kind": "cable"}}

        class Target:
            totals = {"extras.tag": 1, "dcim.cable": 1, "dcim.cabletermination": 2}
            initial = 0
            ids = {"extras.tag": 11, "dcim.cable": 12, "dcim.cabletermination": 13}

            def request(self, path, *, branch=True, **_kwargs):
                self.last_branch = branch
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                if path.startswith("/api/core/object-types/"):
                    label = query["app_label"][0] + "." + query["model"][0]
                    return 200, {"count": 1, "results": [{"id": self.ids[label]}]}
                object_type_id = query.get("object_type_id")
                if not object_type_id:
                    return 200, {"count": self.initial, "results": []}
                label = next(name for name, value in self.ids.items()
                             if value == int(object_type_id[0]))
                return 200, {"count": 0 if self.initial == 0 else self.totals[label],
                             "results": []}

        target = Target()
        preflight = _review_history_preflight(target, {"id": 5}, objects)
        self.assertEqual(preflight["initial_count"], 0)
        target.initial = 4
        evidence = _verify_review_history(target, {"id": 5}, objects, preflight)
        self.assertEqual(evidence["expected"], 4)
        self.assertEqual(evidence["observed"], 4)
        self.assertFalse(target.last_branch)

        target.initial = 1
        with self.assertRaisesRegex(LoadError, "fresh branch.*found 1"):
            _review_history_preflight(target, {"id": 5}, objects)
        target.initial = 4
        target.totals["dcim.cabletermination"] = 1
        with self.assertRaisesRegex(LoadError, "dcim.cabletermination.*expected 2"):
            _verify_review_history(target, {"id": 5}, objects, preflight)
        target.totals["dcim.cabletermination"] = 2
        target.initial = 5
        with self.assertRaisesRegex(LoadError, "branch total.*expected 4.*found 5"):
            _verify_review_history(target, {"id": 5}, objects, preflight)

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

    def test_readback_projection_covers_only_plain_kinds_and_all_compared_fields(self):
        plan = {"objects": [
            {"kind": "interface", "attrs": {"name": "e0", "type": "1000base-t"},
             "refs": {"device": "d", "untagged_vlan": "v"}},
            {"kind": "circuit_termination", "attrs": {"term_side": "A"}, "refs": {"termination": "s"}},
        ]}
        fields = _readback_fields(plan)
        self.assertEqual(set(fields), {"interface"})  # composed-field kinds are never projected
        projection = fields["interface"]
        # everything verify_plan will compare must be requested: identity, attrs, refs
        for required in ("id", "name", "device", "type", "untagged_vlan"):
            self.assertIn(required, projection)

    def test_bounded_readback_keeps_count_and_sample(self):
        verification = {"mismatch_count": 100, "mismatches": [{"n": i} for i in range(100)], "success": False}
        bounded = _bounded_readback(verification)
        self.assertEqual(len(bounded["mismatches"]), 20)
        self.assertEqual(bounded["mismatches_omitted"], 80)
        self.assertEqual(bounded["mismatch_count"], 100)
        small = {"mismatch_count": 3, "mismatches": [{}, {}, {}]}
        self.assertIs(_bounded_readback(small), small)

    def test_orphaned_committed_job_is_adopted_from_exact_diff_counts(self):
        receipt = {"review_history_preflight": {"branch_id": 5, "object_types": {"dcim.site": 31}},
                   "jobs": []}
        done = {"purpose": "phase-2:site:batch-1-of-2", "model": "dcim.site", "mode": "insert",
                "rows_expected": 100, "request_verified": True}
        orphan = {"purpose": "phase-2:site:batch-2-of-2", "model": "dcim.site", "mode": "insert",
                  "rows_expected": 40, "job_id": "dead-1"}
        receipt["jobs"] = [done, orphan]
        job = {"status": "errored", "error": "Job orphaned: the worker terminated before finalizing it",
               "data": {"rows_inserted": 40}}

        class Target:
            def request(self, path, **_kwargs):
                assert "branch_id=5" in path and "object_type_id=31" in path and "action=create" in path
                return 200, {"count": 140}

        with tempfile.TemporaryDirectory() as tmp:
            adopted = _resolve_worker_death(Target(), orphan, receipt,
                                            Path(tmp) / "r.json", job)
        self.assertTrue(adopted)
        self.assertTrue(orphan["request_verified"])
        self.assertEqual(orphan["adopted_after_worker_death"]["observed_create_diffs"], 140)
        self.assertEqual(orphan["adopted_after_worker_death"]["verified_rows_before"], 100)

    def test_orphaned_rolled_back_job_is_superseded_for_resubmission(self):
        receipt = {"review_history_preflight": {"branch_id": 5, "object_types": {"dcim.site": 31}},
                   "jobs": []}
        orphan = {"purpose": "phase-2:site:batch-1-of-1", "model": "dcim.site", "mode": "insert",
                  "rows_expected": 40, "job_id": "dead-1"}
        receipt["jobs"] = [orphan]
        job = {"status": "errored", "error": "Job orphaned: worker terminated", "data": {}}

        class Target:
            def request(self, path, **_kwargs):
                return 200, {"count": 0}

        with tempfile.TemporaryDirectory() as tmp:
            adopted = _resolve_worker_death(Target(), orphan, receipt,
                                            Path(tmp) / "r.json", job)
        self.assertFalse(adopted)
        self.assertNotIn("request_verified", orphan)
        self.assertEqual(orphan["superseded"]["reason"], "worker death before commit")

    def test_orphaned_job_with_unexplained_diff_count_requires_fresh_branch(self):
        receipt = {"review_history_preflight": {"branch_id": 5, "object_types": {"dcim.site": 31}},
                   "jobs": []}
        orphan = {"purpose": "p", "model": "dcim.site", "mode": "insert",
                  "rows_expected": 40, "job_id": "dead-1"}
        receipt["jobs"] = [orphan]

        class Target:
            def request(self, path, **_kwargs):
                return 200, {"count": 17}

        with self.assertRaises(LoadError) as caught:
            _arbitrate_worker_death(Target(), receipt, orphan)
        self.assertIn("unexplained branch state", str(caught.exception))
        self.assertIn("new branch", str(caught.exception))

    def test_orphaned_job_without_history_preflight_requires_fresh_branch(self):
        receipt = {"jobs": []}
        orphan = {"purpose": "p", "model": "dcim.site", "mode": "insert",
                  "rows_expected": 40, "job_id": "dead-1"}
        receipt["jobs"] = [orphan]
        with self.assertRaises(LoadError) as caught:
            _arbitrate_worker_death(object(), receipt, orphan)
        self.assertIn("lacks the reviewable history preflight", str(caught.exception))

    def test_superseded_entries_are_ignored_and_adopted_rows_count_as_verified(self):
        # arbitration must count adopted entries and skip superseded ones
        receipt = {"review_history_preflight": {"branch_id": 5, "object_types": {"dcim.site": 31}},
                   "jobs": []}
        superseded = {"purpose": "b1", "model": "dcim.site", "mode": "insert",
                      "rows_expected": 40, "superseded": {"reason": "worker death before commit"}}
        adopted = {"purpose": "b1", "model": "dcim.site", "mode": "insert",
                   "rows_expected": 40, "request_verified": True,
                   "adopted_after_worker_death": {}}
        orphan = {"purpose": "b2", "model": "dcim.site", "mode": "insert",
                  "rows_expected": 10, "job_id": "dead-2"}
        receipt["jobs"] = [superseded, adopted, orphan]

        class Target:
            def request(self, path, **_kwargs):
                return 200, {"count": 50}

        verdict, evidence = _arbitrate_worker_death(Target(), receipt, orphan)
        self.assertEqual(verdict, "committed")
        self.assertEqual(evidence["verified_rows_before"], 40)

    def test_poll_stops_early_when_rq_confirms_the_worker_died(self):
        running = {"job_id": "job-1", "status": "running", "data": {"rows_processed": 100}}

        class Target:
            def request(self, path, **_kwargs):
                if path.startswith("/api/core/background-tasks/"):
                    return 200, {"status": "failed", "ended_at": None}
                return 200, running

        with unittest.mock.patch("estates.turbobulk.RQ_ORACLE_AFTER_SECONDS", 0.0):
            with self.assertRaises(WorkerDied) as caught:
                _poll(Target(), "job-1", 900)
        self.assertIs(caught.exception.job, running)
        self.assertEqual(caught.exception.rq_status, "failed")
        self.assertIsInstance(caught.exception, JobTimeout)  # receipt handling path
        self.assertIn("never resubmit", str(caught.exception))
        self.assertIn("new branch and receipt", str(caught.exception))

    def test_poll_treats_missing_rq_hash_as_inconclusive(self):
        running = {"job_id": "job-1", "status": "running", "data": {}}

        class Target:
            def request(self, path, **_kwargs):
                if path.startswith("/api/core/background-tasks/"):
                    raise LoadError("GET returned HTTP 500: NoSuchJobError: job-1")
                return 200, running

        with unittest.mock.patch("estates.turbobulk.RQ_ORACLE_AFTER_SECONDS", 0.0):
            with self.assertRaises(JobTimeout) as caught:
                _poll(Target(), "job-1", 0)
        self.assertNotIsInstance(caught.exception, WorkerDied)

    def test_poll_survives_an_unavailable_rq_oracle(self):
        running = {"job_id": "job-1", "status": "running", "data": {}}

        class Target:
            def request(self, path, **_kwargs):
                if path.startswith("/api/core/background-tasks/"):
                    raise LoadError("GET returned HTTP 404: not found")
                return 200, running

        with unittest.mock.patch("estates.turbobulk.RQ_ORACLE_AFTER_SECONDS", 0.0):
            with self.assertRaises(JobTimeout) as caught:
                _poll(Target(), "job-1", 0)
        self.assertNotIsInstance(caught.exception, WorkerDied)

    def test_poll_requires_two_consecutive_dead_observations(self):
        running = {"job_id": "job-1", "status": "running", "data": {}}

        class Target:
            def __init__(self):
                self.oracle_reads = 0

            def request(self, path, **_kwargs):
                if path.startswith("/api/core/background-tasks/"):
                    self.oracle_reads += 1
                    # one flaky 'failed' read, then healthy 'started' forever
                    status = "failed" if self.oracle_reads == 1 else "started"
                    return 200, {"status": status}
                return 200, running

        target = Target()
        with unittest.mock.patch("estates.turbobulk.RQ_ORACLE_AFTER_SECONDS", 0.0):
            with self.assertRaises(JobTimeout) as caught:
                _poll(target, "job-1", 0.2)
        self.assertNotIsInstance(caught.exception, WorkerDied)
        self.assertGreaterEqual(target.oracle_reads, 2)

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

    def test_rest_patch_persists_intent_before_request(self):
        rows = [{"id": 9, "tags": [7]}]
        case = self

        class Target:
            def request(self, _path, **_kwargs):
                saved = json.loads(receipt_path.read_text())["rest_batches"][0]
                case.assertEqual(saved["status"], "submitting")
                case.assertEqual(saved["payload"], rows)
                case.assertEqual(saved["payload_sha256"], hashlib.sha256(
                    json.dumps(rows, separators=(",", ":")).encode()).hexdigest())
                case.assertEqual(len(saved["attempts"]), 1)
                return 200, []

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": []}
            receipt_path.write_text(json.dumps(receipt))
            _bulk_patch(Target(), "/api/dcim/sites/", rows, receipt, receipt_path,
                        "complete:site")
        self.assertEqual(receipt["rest_batches"][0]["status"], "completed")

    def test_rest_patch_persists_ambiguous_timeout(self):
        class Target:
            def request(self, _path, **_kwargs):
                raise LoadError("PATCH response was lost")

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": []}
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(LoadError, "response was lost"):
                _bulk_patch(Target(), "/api/dcim/sites/", [{"id": 9, "tags": [7]}],
                            receipt, receipt_path, "complete:site")
            saved = json.loads(receipt_path.read_text())["rest_batches"][0]
        self.assertEqual(saved["status"], "submitting")
        self.assertEqual(saved["attempts"][-1]["result"], "no-terminal-response")

    def test_rest_patch_exact_match_recovers_without_resend(self):
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        entry = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                 "rows": 1, "target_ids": [9], "payload": payload,
                 "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                 "status": "submitting", "attempts": [
                     {"result": "no-terminal-response", "wall_seconds": 120.0}]}
        client = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": [entry]}
            receipt_path.write_text(json.dumps(receipt))
            _reconcile_rest_patches(client, "/api/dcim/sites/",
                                    {9: {"id": 9, "tags": [{"id": 7}]}},
                                    receipt, receipt_path, "complete:site")
        client.request.assert_not_called()
        self.assertEqual(entry["status"], "completed")
        self.assertTrue(entry["recovered"])

    def test_rest_patch_safe_preimage_requires_fresh_branch_without_resend(self):
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        entry = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                 "rows": 1, "target_ids": [9], "payload": payload,
                 "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                 "status": "submitting", "attempts": [
                     {"result": "no-terminal-response", "wall_seconds": 120.0}]}

        client = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": [entry]}
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(LoadError, "still unresolved.*fresh branch"):
                _reconcile_rest_patches(client, "/api/dcim/sites/",
                                        {9: {"id": 9, "tags": []}},
                                        receipt, receipt_path, "complete:site")
        client.request.assert_not_called()
        self.assertEqual(entry["status"], "submitting")

    def test_rest_patch_recovery_rejects_conflict_and_hash_drift(self):
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        base = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                "rows": 1, "target_ids": [9], "payload": payload,
                "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                "status": "submitting", "attempts": []}
        cases = [
            (deepcopy(base), {9: {"id": 9, "tags": [{"id": 8}]}}, "concurrent"),
            ({**deepcopy(base), "payload_sha256": "0" * 64},
             {9: {"id": 9, "tags": []}}, "invalid REST write-ahead"),
        ]
        for entry, current, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary:
                receipt_path = Path(temporary) / "receipt.json"
                receipt = {"rest_batches": [entry]}
                receipt_path.write_text(json.dumps(receipt))
                client = Mock()
                with self.assertRaisesRegex(LoadError, error):
                    _reconcile_rest_patches(client, "/api/dcim/sites/", current,
                                            receipt, receipt_path, "complete:site")
                client.request.assert_not_called()

    def test_complete_rest_recovers_pending_exact_batch_without_duplicate_request(self):
        tag = {"key": "tag:demo", "kind": "tag", "attrs": {"slug": "demo"}, "refs": {}}
        site = {"key": "site:demo", "kind": "site", "attrs": {"name": "Demo"},
                "refs": {"tags": [tag["key"]]}}
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        entry = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                 "offset": 0, "rows": 1, "target_ids": [9], "payload": payload,
                 "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                 "status": "submitting", "attempts": [
                     {"result": "no-terminal-response", "wall_seconds": 120.0}]}

        class Target:
            def all(self, _path):
                return [{"id": 9, "tags": [{"id": 7}]}]

            def request(self, *_args, **_kwargs):
                raise AssertionError("recovered completion batch must not be resent")

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": [entry]}
            receipt_path.write_text(json.dumps(receipt))
            rows = _complete_rest(Target(), {"objects": [tag, site]},
                                  {obj["key"]: obj for obj in (tag, site)},
                                  {tag["key"]: 7, site["key"]: 9}, receipt, receipt_path)
        self.assertEqual(rows, 1)
        self.assertEqual(len(receipt["rest_batches"]), 1)
        self.assertEqual(entry["status"], "completed")
        self.assertTrue(entry["recovered"])

    def test_complete_rest_rejects_drift_from_completed_checkpoint(self):
        tag = {"key": "tag:demo", "kind": "tag", "attrs": {"slug": "demo"}, "refs": {}}
        site = {"key": "site:demo", "kind": "site", "attrs": {"name": "Demo"},
                "refs": {"tags": [tag["key"]]}}
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        entry = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                 "offset": 0, "rows": 1, "target_ids": [9], "payload": payload,
                 "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                 "status": "completed", "attempts": []}

        class Target:
            def all(self, _path):
                return [{"id": 9, "tags": []}]

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": [entry]}
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(LoadError, "completed REST checkpoint no longer matches"):
                _complete_rest(Target(), {"objects": [tag, site]},
                               {obj["key"]: obj for obj in (tag, site)},
                               {tag["key"]: 7, site["key"]: 9}, receipt, receipt_path)

    def test_complete_rest_rejects_receipt_row_count_drift(self):
        tag = {"key": "tag:demo", "kind": "tag", "attrs": {"slug": "demo"}, "refs": {}}
        site = {"key": "site:demo", "kind": "site", "attrs": {"name": "Demo"},
                "refs": {"tags": [tag["key"]]}}
        payload = [{"id": 9, "tags": [7]}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        entry = {"purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                 "offset": 0, "rows": 2, "target_ids": [9], "payload": payload,
                 "payload_sha256": hashlib.sha256(encoded).hexdigest(),
                 "status": "submitting", "attempts": []}

        class Target:
            def all(self, _path):
                return [{"id": 9, "tags": [{"id": 7}]}]

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_batches": [entry]}
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(LoadError, "invalid REST write-ahead checkpoint"):
                _complete_rest(Target(), {"objects": [tag, site]},
                               {obj["key"]: obj for obj in (tag, site)},
                               {tag["key"]: 7, site["key"]: 9}, receipt, receipt_path)

    def test_rest_payload_hash_is_stable_across_receipt_reload_and_key_order(self):
        payload = [{"start_on_boot": "off", "tags": [7], "id": 9, "primary_ip4": 11}]
        encoded = _rest_payload(payload)
        receipt = {"rest_batches": [{"payload": payload,
                                      "payload_sha256": hashlib.sha256(encoded).hexdigest()}]}
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            _write_receipt(receipt_path, receipt)
            reloaded = json.loads(receipt_path.read_text())["rest_batches"][0]
        reordered = [{"id": 9, "primary_ip4": 11, "tags": [7], "start_on_boot": "off"}]
        self.assertEqual(_rest_payload(reloaded["payload"]), encoded)
        self.assertEqual(_rest_payload(reordered), encoded)
        self.assertEqual(hashlib.sha256(_rest_payload(reloaded["payload"])).hexdigest(),
                         reloaded["payload_sha256"])

    def test_exact_match_load_rejects_corrupt_rest_payload_hash(self):
        tag = {"key": "tag:demo", "kind": "tag",
               "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}
        site = {"key": "site:demo", "kind": "site",
                "attrs": {"name": "Demo", "slug": "demo"},
                "refs": {"tags": [tag["key"]]}}
        plan = {"schema_version": 1, "generator_version": "fixture",
                "objects": [tag, site]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None

            def request(self, path, **kwargs):
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.6.8", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                if path.startswith("/api/plugins/branching/branches/"):
                    return 200, {"results": [{"id": 1, "name": "Demo",
                                               "schema_id": "schema1",
                                               "status": {"value": "ready"}}]}
                raise AssertionError(f"unexpected request: {kwargs.get('method', 'GET')} {path}")

            def all(self, path):
                if path == "/api/dcim/sites/":
                    return [{"id": 9, "name": "Demo", "slug": "demo",
                             "tags": [{"id": 7, "slug": "demo"}]}]
                raise AssertionError(f"unexpected inventory request: {path}")

        verified = {"success": True, "ids": {tag["key"]: 7, site["key"]: 9},
                    "matched_objects": 2}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps({
                "status": "passed", "plan_sha256": digest(plan)}))
            receipt_path = root / "receipt.json"
            common = (
                patch("estates.turbobulk.Client", return_value=Target()),
                patch("estates.turbobulk._review_history_preflight", return_value={
                    "branch_id": 1, "initial_count": 0,
                    "object_types": {"extras.tag": 11, "dcim.site": 12}}),
                patch("estates.turbobulk._verify_review_history", return_value={
                    "expected": 2, "observed": 2}),
                patch("estates.turbobulk.fetch_inventory", return_value={
                    "tag": [{"id": 7}], "site": [{"id": 9}]}),
                patch("estates.turbobulk.verify_plan", return_value=verified),
            )
            with common[0], common[1], common[2], common[3], common[4]:
                load(root, url="https://netbox.example", token="fixture", branch="Demo",
                     receipt_path=receipt_path)
            receipt = json.loads(receipt_path.read_text())
            receipt["rest_batches"] = [{
                "purpose": "complete:site", "endpoint": "/api/dcim/sites/",
                "offset": 0, "rows": 1, "target_ids": [9],
                "payload": [{"id": 9, "tags": [7]}], "payload_sha256": "0" * 64,
                "status": "completed", "attempts": [],
            }]
            receipt_path.write_text(json.dumps(receipt))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk._verify_review_history", return_value={
                        "expected": 2, "observed": 2}), \
                    patch("estates.turbobulk.fetch_inventory", return_value={
                        "tag": [{"id": 7}], "site": [{"id": 9}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                with self.assertRaisesRegex(LoadError, "invalid REST write-ahead checkpoint"):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=receipt_path)

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
                    patch("estates.turbobulk._review_history_preflight", return_value={
                        "branch_id": 1, "initial_count": 0,
                        "object_types": {"extras.tag": 11}}), \
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

    def test_disposable_policy_requires_fresh_branch_before_inventory_or_write(self):
        plan = {"schema_version": 1, "generator_version": "fixture", "objects": [
            {"key": "tag:demo", "kind": "tag",
             "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}}
        ]}

        class Target:
            base = "https://netbox.example"
            token = "fixture"
            branch_id = None
            writes = 0

            def request(self, path, method="GET", **_kwargs):
                self.writes += method != "GET"
                if path == "/api/status/":
                    return 200, {"netbox-version": "4.7.0", "plugins": {
                        "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}}
                if path.startswith("/api/plugins/branching/branches/"):
                    return 200, {"results": [{"id": 1, "name": "Demo", "schema_id": "schema1",
                                              "status": {"value": "ready"}}]}
                if path == "/api/core/object-changes/?limit=1":
                    return 200, {"count": 1, "results": [{"id": 9}]}
                raise AssertionError(path)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(plan)}))
            target = Target()
            with patch("estates.turbobulk.Client", return_value=target), \
                    patch("estates.turbobulk.fetch_inventory") as inventory:
                with self.assertRaisesRegex(LoadError, "fresh empty branch.*cannot be reviewed"):
                    load(root, url=target.base, token=target.token, branch="Demo",
                         receipt_path=root / "receipt.json",
                         delivery_policy="disposable-baseline")
            inventory.assert_not_called()
            self.assertEqual(target.writes, 0)
            self.assertFalse((root / "receipt.json").exists())

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
                    patch("estates.turbobulk._review_history_preflight", return_value={
                        "branch_id": 1, "initial_count": 0,
                        "object_types": {"extras.tag": 11}}), \
                    patch("estates.turbobulk._verify_review_history", return_value={
                        "expected": 1, "observed": 1}), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                load(root, url="https://netbox.example", token="fixture", branch="Demo", receipt_path=receipt_path)
                receipt = json.loads(receipt_path.read_text())
                with self.assertRaisesRegex(LoadError, "different delivery_policy"):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=receipt_path, delivery_policy="disposable-baseline")
                receipt["turbobulk_request_settings"]["post_hooks"]["rebuild_search_index"] = False
                receipt_path.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(LoadError, "different turbobulk_request_settings"):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=receipt_path)
                receipt["turbobulk_request_settings"] = delivery_contract("reviewable")["request_settings"]
                recorded_job = {"job_id": "original-job", "purpose": "phase-1:tag",
                                "model": "extras.tag",
                                "mode": "insert", "rows_expected": 1,
                                "request_settings": _batch_request_settings(
                                    delivery_contract("reviewable")["request_settings"]),
                                "request_verified": True}
                receipt["jobs"] = [recorded_job]
                for purpose, model, hook in _finalizer_plan({"tag:demo": plan["objects"][0]}):
                    receipt["jobs"].append({
                        "purpose": purpose, "model": model, "mode": "insert",
                        "rows_expected": 0, "request_verified": True,
                        "request_settings": _finalizer_request_settings(
                            delivery_contract("reviewable")["request_settings"], hook),
                    })
                receipt_path.write_text(json.dumps(receipt))
                load(root, url="https://netbox.example", token="fixture", branch="Demo", receipt_path=receipt_path)
            repeated = json.loads(receipt_path.read_text())
            self.assertEqual(repeated["jobs"], receipt["jobs"])
            self.assertEqual(len(repeated["repeat_verifications"]), 1)
            self.assertEqual([attempt["result"] for attempt in repeated["attempts"]],
                             ["already-matched", "already-matched"])
            repeated["resolved_ids"]["tag:demo"] = 99
            receipt_path.write_text(json.dumps(repeated))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk._verify_review_history", return_value={
                        "expected": 1, "observed": 1}), \
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
                    patch("estates.turbobulk._verify_review_history", return_value={
                        "expected": 1, "observed": 1}), \
                    patch("estates.turbobulk.fetch_inventory", return_value={"tag": [{"id": 41}]}), \
                    patch("estates.turbobulk.verify_plan", return_value=verified):
                recovered = load(root, url="https://netbox.example", token="fixture",
                                 branch="Demo", receipt_path=receipt_path)
            self.assertTrue(recovered["success"])
            self.assertEqual(recovered["result"], "recovered-matched")
            self.assertEqual(recovered["jobs"], receipt["jobs"])
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

        settings = _batch_request_settings(
            delivery_contract("reviewable")["request_settings"])
        terminal = {"job_id": "terms-job", "status": "completed", "duration_seconds": 0.1,
                    "data": {"rows_processed": 2, "rows_inserted": 2, "rows_updated": 0,
                             "changelogs_created": 2, "errors": [],
                             "post_hooks": {name: {"skipped": True} for name in POST_HOOKS}}}
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
                "delivery_policy": "reviewable",
                "delivery_warning": None,
                "branch_capabilities": delivery_contract("reviewable")["branch_capabilities"],
                "turbobulk_request_settings": delivery_contract("reviewable")["request_settings"],
                "turbobulk_max_job_rows": DEFAULT_JOB_ROWS,
                "review_history_preflight": {"branch_id": 1, "initial_count": 0,
                                             "object_types": {"dcim.interface": 11,
                                                              "dcim.cable": 12,
                                                              "dcim.cabletermination": 13}},
                "target_contract": {"netbox": "4.6.8", "plugins": {
                    "netbox_turbobulk": "0.3.0", "netbox_branching": "1.1.2"}},
                "success": False, "resolved_ids": {"interface:a": 41, "interface:b": 42, "cable:1": 51},
                "jobs": [{"purpose": "phase-1:cable:terminations", "model": "dcim.cabletermination",
                          "job_id": "terms-job",
                          "mode": "insert", "rows_expected": 2, "status": "submitted",
                          "request_settings": settings}], "rest_batches": []}))
            with patch("estates.turbobulk.Client", return_value=Target()), \
                    patch("estates.turbobulk.fetch_inventory", return_value={}), \
                    patch("estates.turbobulk.verify_plan", side_effect=[{"success": False},
                                                                         {"success": True, "mismatch_count": 0}]), \
                    patch("estates.turbobulk._schema_preflight", return_value={}), \
                    patch("estates.turbobulk._content_types", return_value={"interface": 1}), \
                    patch("estates.turbobulk._phases", return_value=[["cable:1"]]), \
                    patch("estates.turbobulk._complete_rest", return_value=0), \
                    patch("estates.turbobulk._run_finalizers"), \
                    patch("estates.turbobulk._verify_paths", return_value={"cables_expected": 1,
                                                                           "cables_traced": 1,
                                                                           "failures": []}), \
                    patch("estates.turbobulk._verify_component_caches", return_value={
                        "components_expected": 2, "placement_queries": 1, "failures": []}), \
                    patch("estates.turbobulk._verify_review_history", return_value={
                        "expected": 5, "observed": 5}), \
                    patch("estates.turbobulk._poll", return_value=terminal) as poll:
                result = load(root, url="https://netbox.example", token="fixture",
                              branch="Demo", receipt_path=receipt_path)
            poll.assert_called_once_with(ANY, "terms-job", 900)
            self.assertTrue(result["success"])
            self.assertTrue(result["jobs"][0]["request_verified"])
            self.assertEqual(result["attempts"][-1]["result"], "loaded")


if __name__ == "__main__":
    unittest.main()
