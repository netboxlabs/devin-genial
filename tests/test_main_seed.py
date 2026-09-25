"""The main-seed policy must be loudly opt-in and change nothing for branches."""

import os
import unittest
from unittest.mock import patch

from estates.turbobulk import (DELIVERY_POLICIES, LoadError, _submit, delivery_contract, load)


class MainSeedContract(unittest.TestCase):
    def test_policy_is_registered(self):
        self.assertIn("main-seed", DELIVERY_POLICIES)

    def test_contract_disables_changelogs_review_and_branch_capabilities(self):
        contract = delivery_contract("main-seed")
        self.assertFalse(contract["request_settings"]["create_changelogs"])
        self.assertEqual(contract["request_settings"]["validation_mode"], "auto")
        self.assertFalse(any(contract["branch_capabilities"].values()))
        self.assertIn("main", contract["warning"])
        self.assertIn("empty main", contract["warning"])

    def test_branch_policies_are_unchanged(self):
        self.assertTrue(delivery_contract("reviewable")["request_settings"]["create_changelogs"])
        self.assertIsNone(delivery_contract("reviewable")["warning"])
        disposable = delivery_contract("disposable-baseline")
        self.assertFalse(disposable["request_settings"]["create_changelogs"])
        self.assertIn("branch", disposable["warning"])


class MainSeedGate(unittest.TestCase):
    def _load(self, branch, policy):
        return load("missing-artifact", url="http://unreachable.invalid", token="x",
                    branch=branch, receipt_path="/nonexistent/receipt.json",
                    delivery_policy=policy)

    def test_branchless_reviewable_is_refused_naming_both_requirements(self):
        with patch.dict(os.environ, {"ALLOW_MAIN_WRITES": "1"}):
            with self.assertRaisesRegex(LoadError, "main-seed.*ALLOW_MAIN_WRITES"):
                self._load("", "reviewable")

    def test_main_seed_without_the_environment_gate_is_refused(self):
        environment = {k: v for k, v in os.environ.items() if k != "ALLOW_MAIN_WRITES"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(LoadError, "ALLOW_MAIN_WRITES"):
                self._load("", "main-seed")

    def test_main_seed_with_a_branch_is_refused(self):
        with patch.dict(os.environ, {"ALLOW_MAIN_WRITES": "1"}):
            with self.assertRaisesRegex(LoadError, "omit the branch"):
                self._load("Some Branch", "main-seed")

    def test_main_seed_with_the_gate_proceeds_past_the_branch_checks(self):
        with patch.dict(os.environ, {"ALLOW_MAIN_WRITES": "1"}):
            with self.assertRaises((LoadError, OSError)) as caught:
                self._load("", "main-seed")
        message = str(caught.exception)
        self.assertNotIn("ALLOW_MAIN_WRITES", message)
        self.assertNotIn("omit the branch", message)


class BranchlessSubmission(unittest.TestCase):
    class _Client:
        def __init__(self):
            self.body = None

        def request(self, path, method=None, body=None, headers=None, branch=None):
            self.body = body
            return 200, {"job_id": "j1"}

    def _submit(self, branch_name):
        client = self._Client()
        receipt = {"jobs": []}
        job = {"status": "completed", "data": {"rows_processed": 1,
                                               "rows_inserted": 1, "errors": []}}
        with patch("estates.turbobulk._write_receipt"), \
                patch("estates.turbobulk._poll", return_value=job), \
                patch("estates.turbobulk._job_result", return_value="loaded"):
            _submit(client, branch_name, "dcim.device", [{"name": "d-1"}], "phase-1:device",
                    [], receipt, "unused", 5, upload_format="jsonl")
        return client.body

    def test_branchless_submission_omits_the_branch_field_entirely(self):
        body = self._submit("")
        self.assertNotIn(b'name="branch"', body)

    def test_branch_submission_still_carries_the_field(self):
        body = self._submit("Demo")
        self.assertIn(b'name="branch"', body)
        self.assertIn(b"Demo", body)




class MainSeedWorkerDeathArbitration(unittest.TestCase):
    class _Client:
        def __init__(self, count):
            self.count = count
            self.paths = []

        def request(self, path, **kwargs):
            self.paths.append(path)
            return 200, {"count": self.count}

    def _receipt(self, verified_rows=0, allowlisted=None):
        jobs = []
        if verified_rows:
            jobs.append({"model": "circuits.provider", "mode": "insert",
                         "rows_expected": verified_rows, "request_verified": True})
        return {"delivery_policy": "main-seed", "jobs": jobs,
                "allowed_existing": allowlisted or {}}

    def _entry(self, rows=2):
        return {"model": "circuits.provider", "mode": "insert", "rows_expected": rows}

    def test_committed_when_count_includes_the_entry(self):
        from estates.turbobulk import _arbitrate_worker_death
        verdict, evidence = _arbitrate_worker_death(self._Client(2), self._receipt(), self._entry())
        self.assertEqual(verdict, "committed")
        self.assertEqual(evidence["observed_rows"], 2)

    def test_rolled_back_when_count_excludes_the_entry(self):
        from estates.turbobulk import _arbitrate_worker_death
        verdict, _ = _arbitrate_worker_death(self._Client(0), self._receipt(), self._entry())
        self.assertEqual(verdict, "rolled-back")

    def test_unexplained_count_is_a_hard_stop(self):
        from estates.turbobulk import LoadError, _arbitrate_worker_death
        with self.assertRaisesRegex(LoadError, "unexplained main state"):
            _arbitrate_worker_death(self._Client(1), self._receipt(), self._entry())

    def test_allowlisted_pre_existing_rows_count_toward_baseline(self):
        from estates.turbobulk import _arbitrate_worker_death
        # Pin the real _bootstrap_allowlist shape: per-kind ids under target_ids.
        receipt = self._receipt(allowlisted={
            "success": True,
            "target_ids": {"module_type_profile": [1, 2]},
            "target_identities": {"module_type_profile": [{"name": "a"}, {"name": "b"}]}})
        entry = {"model": "dcim.moduletypeprofile", "mode": "insert", "rows_expected": 3}
        verdict, evidence = _arbitrate_worker_death(self._Client(5), receipt, entry)
        self.assertEqual(verdict, "committed")
        self.assertEqual(evidence["allowlisted_pre_existing"], 2)

    def test_termination_model_resolves_its_endpoint(self):
        from estates.turbobulk import _arbitrate_worker_death
        client = self._Client(4)
        receipt = {"delivery_policy": "main-seed", "jobs": [], "allowed_existing": {}}
        entry = {"model": "dcim.cabletermination", "mode": "insert", "rows_expected": 4}
        verdict, _ = _arbitrate_worker_death(client, receipt, entry)
        self.assertEqual(verdict, "committed")
        self.assertIn("/api/dcim/cable-terminations/?limit=1&brief=1", client.paths[0])


if __name__ == "__main__":
    unittest.main()
