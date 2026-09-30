"""Circuit terminations are only finished by NetBox's save hooks.

A raw bulk insert leaves Circuit.termination_a/_z null and the termination's
scope cache null, so `?site_id=` filters return nothing and WAN maps draw no
arcs. The loader asks TurboBulk for its bounded save-hook fixups on that one
kind and proves the outcome at readback; these tests pin both halves.
"""

import unittest

from estates.turbobulk import (LoadError, POST_HOOKS, SAVE_HOOK_KINDS,
                               SAVE_HOOK_REQUIRED_COLUMNS, _batch_request_settings,
                               _job_request_schedule, _job_result,
                               _verify_circuit_terminations, delivery_contract)


BASE = delivery_contract("reviewable")["request_settings"]


def _termination(key, circuit_key, side, termination_key=None):
    refs = {"circuit": circuit_key}
    if termination_key:
        refs["termination"] = termination_key
    return {"key": key, "kind": "circuit_termination", "attrs": {"term_side": side},
            "refs": refs, "meta": {}}


class SaveHookRequestSettings(unittest.TestCase):
    def test_circuit_terminations_request_save_hooks(self):
        settings = _batch_request_settings(BASE, "circuit_termination")
        self.assertIs(settings["apply_save_hooks"], True)

    def test_every_other_kind_leaves_save_hooks_off(self):
        for kind in ("device", "interface", "cable", "site", None):
            with self.subTest(kind=kind):
                self.assertIs(_batch_request_settings(BASE, kind)["apply_save_hooks"], False)

    def test_save_hooks_never_re_enable_global_post_hooks(self):
        settings = _batch_request_settings(BASE, "circuit_termination")
        self.assertEqual(settings["post_hooks"], {name: False for name in POST_HOOKS})

    def test_the_schedule_binds_save_hooks_to_the_termination_purpose(self):
        objects = {
            "circuit:1": {"key": "circuit:1", "kind": "circuit", "attrs": {"cid": "c1"},
                          "refs": {}, "meta": {}},
            "ct:a": _termination("ct:a", "circuit:1", "A"),
        }
        schedule = _job_request_schedule([["circuit:1"], ["ct:a"]], objects, 100, BASE)
        self.assertIs(schedule["phase-2:circuit_termination"]["apply_save_hooks"], True)
        self.assertIs(schedule["phase-1:circuit"]["apply_save_hooks"], False)


class JobResultContract(unittest.TestCase):
    """A server that ignored the flag must not pass the other checks."""

    def _job(self, **data):
        base = {"rows_processed": 2, "rows_inserted": 2, "rows_updated": 0,
                "changelogs_created": 2,
                "post_hooks": {name: {"skipped": True} for name in POST_HOOKS}}
        base.update(data)
        return {"status": "completed", "job_id": "j1", "data": base}

    def test_requested_save_hooks_must_be_reported_for_every_row(self):
        settings = _batch_request_settings(BASE, "circuit_termination")
        _job_result(self._job(save_hooks_applied=2), 2, settings, "insert")

    def test_a_silently_ignored_flag_fails(self):
        settings = _batch_request_settings(BASE, "circuit_termination")
        with self.assertRaisesRegex(LoadError, "applied save hooks to None rows instead of 2"):
            _job_result(self._job(), 2, settings, "insert")

    def test_a_partial_save_hook_pass_fails(self):
        settings = _batch_request_settings(BASE, "circuit_termination")
        with self.assertRaisesRegex(LoadError, "applied save hooks to 1 rows instead of 2"):
            _job_result(self._job(save_hooks_applied=1), 2, settings, "insert")

    def test_unrequested_save_hooks_fail(self):
        settings = _batch_request_settings(BASE, "device")
        with self.assertRaisesRegex(LoadError, "although they were not requested"):
            _job_result(self._job(save_hooks_applied=2), 2, settings, "insert")

    def test_absent_field_is_correct_when_not_requested(self):
        settings = _batch_request_settings(BASE, "device")
        _job_result(self._job(), 2, settings, "insert")


class TerminationReadback(unittest.TestCase):
    OBJECTS = {
        "site:1": {"key": "site:1", "kind": "site", "attrs": {"name": "s"}, "refs": {}, "meta": {}},
        "circuit:1": {"key": "circuit:1", "kind": "circuit", "attrs": {"cid": "c1"},
                      "refs": {}, "meta": {}},
        "ct:a": _termination("ct:a", "circuit:1", "A", "site:1"),
        "ct:z": _termination("ct:z", "circuit:1", "Z"),
    }
    IDS = {"site:1": 7, "circuit:1": 11, "ct:a": 21, "ct:z": 22}

    class _Client:
        def __init__(self, circuit_row, site_filter_rows):
            self.circuit_row, self.site_filter_rows = circuit_row, site_filter_rows
            self.paths = []

        def all(self, path):
            self.paths.append(path)
            return self.site_filter_rows if "site_id=" in path else [self.circuit_row]

    def _run(self, circuit_row, site_filter_rows):
        client = self._Client(circuit_row, site_filter_rows)
        return client, _verify_circuit_terminations(client, self.OBJECTS, self.IDS)

    def test_correct_pointers_and_filter_pass(self):
        client, result = self._run(
            {"id": 11, "termination_a": {"id": 21}, "termination_z": {"id": 22}}, [{"id": 11}])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["circuits_expected"], 1)
        self.assertEqual(result["site_filter_queries"], 1)
        self.assertTrue(any("site_id=7" in p for p in client.paths))

    def test_null_pointers_fail_loudly(self):
        """The exact live symptom: bulk insert without the fixups."""
        _, result = self._run(
            {"id": 11, "termination_a": None, "termination_z": None}, [{"id": 11}])
        sides = sorted(f["side"] for f in result["failures"] if "side" in f)
        self.assertEqual(sides, ["A", "Z"])
        self.assertIsNone(result["failures"][0]["observed_termination_id"])

    def test_a_pointer_aimed_at_the_wrong_termination_fails(self):
        _, result = self._run(
            {"id": 11, "termination_a": {"id": 99}, "termination_z": {"id": 22}}, [{"id": 11}])
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["observed_termination_id"], 99)

    def test_a_broken_site_filter_fails_even_when_pointers_are_right(self):
        """The scope cache fails independently of the circuit pointers."""
        _, result = self._run(
            {"id": 11, "termination_a": {"id": 21}, "termination_z": {"id": 22}}, [])
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["missing_circuit_ids"], [11])

    def test_a_missing_circuit_is_reported(self):
        _, result = self._run({"id": 999}, [{"id": 11}])
        self.assertIn("absent", result["failures"][0]["error"])

    def test_an_estate_without_circuits_is_a_clean_no_op(self):
        client = self._Client({}, [])
        result = _verify_circuit_terminations(
            client, {"site:1": self.OBJECTS["site:1"]}, {"site:1": 7})
        self.assertEqual(result, {"circuits_expected": 0, "site_filter_queries": 0,
                                  "failures": [], "wall_seconds": result["wall_seconds"]})
        self.assertEqual(client.paths, [], "a circuit-free estate must issue no reads")


class RequiredColumns(unittest.TestCase):
    def test_both_models_are_guarded(self):
        self.assertEqual(set(SAVE_HOOK_REQUIRED_COLUMNS),
                         {"circuits.circuittermination", "circuits.circuit"})

    def test_the_termination_scope_cache_is_fully_listed(self):
        self.assertEqual(
            SAVE_HOOK_REQUIRED_COLUMNS["circuits.circuittermination"],
            {"_region_id", "_site_group_id", "_site_id", "_location_id", "_provider_network_id"})

    def test_circuit_termination_is_the_only_save_hook_kind(self):
        self.assertEqual(SAVE_HOOK_KINDS, {"circuit_termination"})


if __name__ == "__main__":
    unittest.main()
