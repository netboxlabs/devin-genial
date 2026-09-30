"""Teardown must delete only what the artifact claims, dependents first."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from estates.diode import _phases
from estates.model import canonical, digest
from estates.teardown import (DELETE_BATCH_ROWS, IMPLICIT_KINDS, LoadError, _batches,
                              _plan_rows, deletion_order, teardown)


ROOT = Path(__file__).resolve().parents[1]


def _objects():
    """A tiny graph with a clear dependency chain."""
    return {
        "site:a": {"key": "site:a", "kind": "site", "attrs": {"name": "A"}, "refs": {}},
        "rack:a": {"key": "rack:a", "kind": "rack", "attrs": {"name": "R"},
                   "refs": {"site": "site:a"}},
        "device:a": {"key": "device:a", "kind": "device", "attrs": {"name": "D"},
                     "refs": {"site": "site:a", "rack": "rack:a"}},
    }


class DeletionOrder(unittest.TestCase):
    def test_order_is_the_reverse_of_the_load_phases(self):
        objects = _objects()
        self.assertEqual(deletion_order(objects), list(reversed(list(_phases(objects)))))

    def test_dependents_are_removed_before_what_they_reference(self):
        order = deletion_order(_objects())
        flat = [key for phase in order for key in phase]
        self.assertLess(flat.index("device:a"), flat.index("rack:a"))
        self.assertLess(flat.index("rack:a"), flat.index("site:a"))

    def test_cable_terminations_are_implicit(self):
        # The loader creates them as their own rows; deleting the cable takes
        # them with it, so they must never enter a delete batch of their own.
        self.assertIn("cable_termination", IMPLICIT_KINDS)


class ArtifactScoping(unittest.TestCase):
    def test_only_plan_matched_rows_are_selected(self):
        objects = _objects()
        verification = {"ids": {
            "site:a": {"kind": "site", "id": 10},
            "rack:a": {"kind": "rack", "id": 20},
            # device:a deliberately unmatched — never resolved, never deleted
        }}
        rows = _plan_rows(verification, objects)
        selected = {(row["kind"], row["id"]) for phase in rows for row in phase}
        self.assertEqual(selected, {("site", 10), ("rack", 20)})

    def test_a_foreign_row_can_never_enter_a_batch(self):
        objects = _objects()
        verification = {"ids": {"site:a": {"kind": "site", "id": 10}}}
        rows = _plan_rows(verification, objects)
        ids = {row["id"] for phase in rows for row in phase}
        self.assertNotIn(999, ids)  # a row the plan does not claim
        self.assertEqual(ids, {10})

    def test_implicit_kinds_are_skipped_even_when_matched(self):
        objects = dict(_objects())
        objects["ct:a"] = {"key": "ct:a", "kind": "cable_termination",
                           "attrs": {}, "refs": {"site": "site:a"}}
        verification = {"ids": {"ct:a": {"kind": "cable_termination", "id": 77},
                                "site:a": {"kind": "site", "id": 10}}}
        rows = _plan_rows(verification, objects)
        kinds = {row["kind"] for phase in rows for row in phase}
        self.assertNotIn("cable_termination", kinds)


class Batching(unittest.TestCase):
    def test_a_batch_never_mixes_kinds(self):
        rows = ([{"key": f"d{i}", "kind": "device", "id": i} for i in range(5)]
                + [{"key": f"r{i}", "kind": "rack", "id": 100 + i} for i in range(3)])
        for kind, batch in _batches(rows, DELETE_BATCH_ROWS):
            self.assertEqual({row["kind"] for row in batch}, {kind})

    def test_batches_respect_the_bound(self):
        rows = [{"key": f"d{i}", "kind": "device", "id": i} for i in range(250)]
        batches = _batches(rows, DELETE_BATCH_ROWS)
        self.assertEqual(sum(len(b) for _, b in batches), 250)
        self.assertTrue(all(len(b) <= DELETE_BATCH_ROWS for _, b in batches))


class Gates(unittest.TestCase):
    def _artifact(self, temporary):
        """A minimal on-disk artifact: teardown only needs a bound plan."""
        plan = {"schema_version": 1, "generator_version": "fixture",
                "recipe": {"namespace": "probe", "name": "Probe"},
                "objects": [
                    {"key": "site:a", "kind": "site", "attrs": {"name": "A"}, "refs": {}},
                    {"key": "rack:a", "kind": "rack", "attrs": {"name": "R"},
                     "refs": {"site": "site:a"}},
                ]}
        root = Path(temporary) / "estate"
        root.mkdir(exist_ok=True)
        (root / "plan.json").write_bytes(canonical(plan) + b"\n")
        (root / "checks.json").write_text(json.dumps(
            {"status": "passed", "plan_sha256": digest(plan)}))
        return root

    def test_a_branch_is_refused_before_anything_else(self):
        with self.assertRaisesRegex(LoadError, "branch"):
            teardown("missing-artifact", url="http://unreachable.invalid", token="x",
                     receipt_path="/nonexistent.json", branch="Some Branch")

    def test_env_gate_and_confirm_are_both_required(self):
        # Both refusals must fire before any write; they are checked after the
        # read-only match assessment, so a fake verification short-circuits it.
        with tempfile.TemporaryDirectory() as temporary:
            artifact = self._artifact(temporary)
            fake = {"ids": {}, "matched_objects": 2, "unmatched_target_ids": {}}
            with patch("estates.teardown.Client"), \
                    patch("estates.teardown.fetch_inventory", return_value={}), \
                    patch("estates.teardown.verify_plan", return_value=fake):
                with patch.dict(os.environ, {"ALLOW_MAIN_TEARDOWN": ""}, clear=False):
                    os.environ.pop("ALLOW_MAIN_TEARDOWN", None)
                    with self.assertRaisesRegex(LoadError, "ALLOW_MAIN_TEARDOWN"):
                        teardown(artifact, url="http://t.example", token="x",
                                 receipt_path=Path(temporary) / "r.json")
                with patch.dict(os.environ, {"ALLOW_MAIN_TEARDOWN": "1"}):
                    with self.assertRaisesRegex(LoadError, "--confirm"):
                        teardown(artifact, url="http://t.example", token="x",
                                 receipt_path=Path(temporary) / "r.json")

    def test_a_target_that_does_not_hold_the_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = self._artifact(temporary)
            fake = {"ids": {}, "matched_objects": 0, "unmatched_target_ids": {}}
            with patch("estates.teardown.Client"), \
                    patch("estates.teardown.fetch_inventory", return_value={}), \
                    patch("estates.teardown.verify_plan", return_value=fake), \
                    patch.dict(os.environ, {"ALLOW_MAIN_TEARDOWN": "1"}):
                with self.assertRaisesRegex(LoadError, "does not look like it"):
                    teardown(artifact, url="http://t.example", token="x",
                             receipt_path=Path(temporary) / "r.json", confirm=True)

    def test_explain_writes_nothing_and_needs_no_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = self._artifact(temporary)
            fake = {"ids": {}, "matched_objects": 2, "unmatched_target_ids": {"tag": [9]}}
            receipt = Path(temporary) / "r.json"
            with patch("estates.teardown.Client"), \
                    patch("estates.teardown.fetch_inventory", return_value={}), \
                    patch("estates.teardown.verify_plan", return_value=fake):
                os.environ.pop("ALLOW_MAIN_TEARDOWN", None)
                result = teardown(artifact, url="http://t.example", token="x",
                                  receipt_path=receipt, explain=True)
            self.assertEqual(result["result"], "explained")
            self.assertEqual(result["foreign_rows_left_alone"], {"tag": 1})
            self.assertFalse(receipt.exists(), "explain must not write a receipt")


class _FakeClient:
    """A target holding rows; DELETE removes them, GET reports what remains."""

    def __init__(self, present):
        self.base = "http://t.example"
        self.token = "x"
        self.present = dict(present)          # kind endpoint fragment -> set of ids
        self.deletes = []

    def _kind_of(self, path):
        return path.split("/api/")[1].split("?")[0].rstrip("/")

    def request(self, path, *, method="GET", body=None, headers=None, branch=True):
        if path == "/api/status/":
            return 200, {"netbox-version": "4.7.1"}
        endpoint = self._kind_of(path)
        held = self.present.setdefault(endpoint, set())
        if method == "DELETE":
            ids = {entry["id"] for entry in json.loads(body)}
            self.deletes.append((endpoint, sorted(ids)))
            self.present[endpoint] = held - ids
            raise LoadError("DELETE returned 204 with an empty body")  # the real client's shape
        asked = {int(v) for part in path.split("&") if part.startswith("id=")
                 for v in [part.split("=")[1]]}
        return 200, {"results": [{"id": i} for i in sorted(held & asked)]}

    def all(self, path):
        return []


class TeardownRun(unittest.TestCase):
    PLAN_IDS = {"site:a": {"kind": "site", "id": 10},
                "rack:a": {"kind": "rack", "id": 20}}

    def _run(self, temporary, present, final_ids, receipt=None, client=None):
        artifact = Gates._artifact(self, temporary)
        client = client or _FakeClient(present)
        verifications = [{"ids": self.PLAN_IDS, "matched_objects": 2,
                          "unmatched_target_ids": {}},
                         {"ids": final_ids, "matched_objects": len(final_ids),
                          "unmatched_target_ids": {}}]
        with patch("estates.teardown.Client", return_value=client), \
                patch("estates.teardown.fetch_inventory", return_value={}), \
                patch("estates.teardown.verify_plan", side_effect=verifications), \
                patch("estates.teardown.retire_namespace_rows",
                      return_value=[{"endpoint": "/api/users/owners/", "id": 5}]) as retire, \
                patch.dict(os.environ, {"ALLOW_MAIN_TEARDOWN": "1"}):
            result = teardown(artifact, url="http://t.example", token="x",
                              receipt_path=receipt or (Path(temporary) / "r.json"),
                              confirm=True)
        return result, client, retire

    def test_deletes_dependents_first_then_retires_main_scoped_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            present = {"dcim/sites": {10}, "dcim/racks": {20}}
            result, client, retire = self._run(temporary, present, {})
            self.assertTrue(result["success"])
            order = [endpoint for endpoint, _ in client.deletes]
            self.assertLess(order.index("dcim/racks"), order.index("dcim/sites"),
                            "a rack must be deleted before the site it references")
            retire.assert_called_once()   # extras and owners come last
            self.assertEqual(result["retired_rows"], 1)

    def test_a_resume_skips_batches_the_receipt_already_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "r.json"
            present = {"dcim/sites": {10}, "dcim/racks": {20}}
            self._run(temporary, present, {}, receipt=receipt)
            first = json.loads(receipt.read_text())
            self.assertTrue(any(e.get("deleted") for e in first["batches"]))
            # Rerun against an already-empty target with the same receipt.
            fresh = _FakeClient({"dcim/sites": set(), "dcim/racks": set()})
            self._run(temporary, {}, {}, receipt=receipt, client=fresh)
            self.assertEqual(fresh.deletes, [],
                             "a completed batch must never be re-sent on resume")

    def test_a_survivor_fails_the_run_and_is_named(self):
        with tempfile.TemporaryDirectory() as temporary:
            present = {"dcim/sites": {10}, "dcim/racks": {20}}
            with self.assertRaisesRegex(LoadError, "survived teardown"):
                # The final readback still resolves the site: it did not go.
                self._run(temporary, present, {"site:a": {"kind": "site", "id": 10}})

    def test_bulk_delete_success_is_proven_by_rereading_not_by_the_response(self):
        # The real client turns a 204 empty body into a LoadError; the fake
        # reproduces that, so a passing run proves the re-read is what decides.
        with tempfile.TemporaryDirectory() as temporary:
            result, client, _ = self._run(temporary, {"dcim/sites": {10}, "dcim/racks": {20}}, {})
            self.assertTrue(result["success"])
            self.assertEqual(sorted(e for e, _ in client.deletes),
                             ["dcim/racks", "dcim/sites"])


if __name__ == "__main__":
    unittest.main()


class ResumeAfterPartialTeardown(unittest.TestCase):
    """A partially torn-down target no longer matches its own artifact, so the
    anti-wrong-target ratio gate must not apply to a receipt-bound resume."""

    def test_bound_receipt_bypasses_the_match_ratio_gate(self):
        import inspect
        from estates import teardown
        src = inspect.getsource(teardown.teardown)
        gate = src.index("ratio < match_threshold")
        load = src.index('receipt_path.exists()')
        self.assertLess(load, gate,
                        "the receipt must be loaded before the ratio gate or resume is impossible")
        self.assertIn("and not resuming", src[gate:gate + 120])
