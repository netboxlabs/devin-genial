"""User-facing artifact round trip and failure preservation checks."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from estates.__main__ import main
from estates.bank import generate
from estates.model import canonical, digest
from estates.report import markdown


class CliTests(unittest.TestCase):
    def test_frozen_commands_reject_missing_or_unsupported_generator_envelopes(self):
        plan = generate({"headquarters": 0, "branches": {"small": 1}})
        cases = []
        for field, value in (("schema_version", True), ("schema_version", 999), ("generator_version", "999.0.0")):
            candidate = deepcopy(plan)
            candidate[field] = value
            cases.append(candidate)
        candidate = deepcopy(plan)
        del candidate["generator_version"]
        del candidate["recipe"]["profile"]
        candidate["contracts"] = []
        cases.append(candidate)
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "plan.json", Path(directory) / "output"
            for candidate in cases:
                source.write_bytes(canonical(candidate))
                for command in (("check", str(source)), ("build", str(source), "--out", str(destination))):
                    status, output, _ = self.call("--json", *command)
                    self.assertEqual(status, 2, output)
                    self.assertIn("frozen schema 1 plan", json.loads(output)["message"])
                self.assertFalse(destination.exists())

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(list(args))
        return status, out.getvalue(), err.getvalue()

    def test_plan_build_report_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "a bank's recipe.toml"
            recipe.write_text('headquarters=0\n[branches]\nsmall=1\n')
            frozen, first, second = root / "plan.json", root / "first", root / "second"
            status, output, errors = self.call("--json", "plan", str(recipe), "--out", str(frozen))
            self.assertEqual((status, errors), (0, ""), output)
            plan = json.loads(frozen.read_text())
            self.assertEqual(json.loads(output)["sha256"], digest(plan))
            for destination in (first, second):
                status, output, errors = self.call("--json", "build", str(frozen), "--out", str(destination))
                self.assertEqual((status, errors), (0, ""), output)
                self.assertEqual(json.loads(output)["checks"], "offline passed")
            for path in first.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (second / path.relative_to(first)).read_bytes())
            before = canonical(plan)
            report = markdown(plan)
            self.assertEqual(canonical(plan), before)
            self.assertEqual(report, (first / "report.md").read_text())
            for obj in plan["objects"]:
                if obj["kind"] in {"site", "rack", "virtual_machine"}:
                    self.assertIn(obj["attrs"]["name"], report)
            self.assertIn("Disk MB", report)
            ledger = next(o for o in plan["objects"] if o["kind"] == "virtual_machine" and o["meta"]["service"] == "ledger-db")
            self.assertEqual(ledger["attrs"]["disk"], 500000)
            self.assertEqual(self.call("check", str(first / "plan.json"))[0], 0)
            self.assertEqual(self.call("report", str(frozen))[1].rstrip(), report.rstrip())
            status, output, _ = self.call("--json", "generate", str(recipe), "--out", str(first))
            self.assertEqual(status, 2)
            self.assertIn("already exists", json.loads(output)["message"])
            self.assertEqual((first / "plan.json").read_bytes(), frozen.read_bytes())
            status, _, _ = self.call("--json", "plan", str(recipe), "--out", str(frozen))
            self.assertEqual(status, 2)
            self.assertEqual(frozen.read_bytes(), before + b"\n")

    def test_bad_recipe_leaves_no_partial_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "bad.toml"
            recipe.write_text('max_objects=100\n')
            destination = root / "output"
            status, output, _ = self.call("--json", "generate", str(recipe), "--out", str(destination))
            self.assertEqual(status, 2)
            self.assertIn("budget", json.loads(output)["message"])
            self.assertFalse(destination.exists())

    def test_scenario_artifacts_preserve_baseline_and_do_not_hide_deletes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = generate({"headquarters": 0, "branches": {"small": 1},
                             "site_designs": {"br-s0001": "inherited"}})
            source, destination = root / "source.json", root / "scenario"
            source.write_bytes(canonical(plan) + b"\n")
            status, output, errors = self.call("--json", "scenario", str(source), "--site", "br-s0001", "--out", str(destination))
            self.assertEqual((status, errors), (0, ""), output)
            self.assertFalse(json.loads(output)["applied_to_target"])
            scenario = json.loads((destination / "scenario.json").read_text())
            for stage, relative in scenario["plan_files"].items():
                snapshot = json.loads((destination / relative).read_text())
                self.assertEqual(digest(snapshot), scenario["checks"]["plan_sha256"][stage])
            self.assertEqual((destination / "before/plan.json").read_bytes(), source.read_bytes())
            self.assertFalse(list(destination.rglob("phase-*.json")))
            self.assertTrue(scenario["changes"]["refresh"]["delete"])
            self.assertIn("Diode replay does not delete", (destination / "report.md").read_text())
            files = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
            status, output, _ = self.call("--json", "scenario", str(source), "--site", "br-s0001", "--out", str(destination))
            self.assertEqual(status, 2)
            self.assertIn("already exists", json.loads(output)["message"])
            self.assertEqual(files, {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()})
            failed = root / "failed"
            self.assertEqual(self.call("scenario", str(source), "--site", "absent", "--out", str(failed))[0], 2)
            self.assertFalse(failed.exists())
            self.assertFalse(list(root.glob(".failed-*")))


if __name__ == "__main__":
    unittest.main()
