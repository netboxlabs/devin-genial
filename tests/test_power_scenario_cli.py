"""Run the operator's recipe, frozen-build and saved-scenario verification paths."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from estates import __main__ as cli
from estates.generate import generate
from estates.model import canonical
from estates.validate import validate


ROOT = Path(__file__).resolve().parents[1]
RECIPES = {
    "regional-bank": 'headquarters = 0\n[branches]\nsmall = 1\n',
    "enterprise-data-center": 'data_centers = 1\n[[workloads]]\nkey = "customer-portal"\n',
    "school-district": '[[schools]]\nkey = "oak"\nclassrooms = 2\nstudents_per_classroom = 12\nadministrative_staff = 4\nlab_seats = 8\n',
}


class PowerScenarioCliTests(unittest.TestCase):
    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-m", "estates", "--json", *(str(arg) for arg in args)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        return json.loads(result.stdout)

    def recipe(self, directory, profile):
        path = directory / "recipe.toml"
        path.write_text(f'profile = "{profile}"\nnamespace = "cli-power"\ndemo = "loss-of-power-diversity"\n' + RECIPES[profile])
        return path

    def test_all_profiles_preview_healthy_baselines_and_generate_verified_snapshots(self):
        for profile in RECIPES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                recipe = self.recipe(directory, profile)
                plan_path, output = directory / "plan.json", directory / "scenario"
                preview = self.run_cli("plan", recipe, "--out", plan_path)
                self.assertEqual(preview["scenario_preview"]["scenario"], "loss-of-power-diversity")
                self.assertEqual(preview["intent"]["resolved"]["demo"]["source"], "supplied")
                self.assertEqual(validate(json.loads(plan_path.read_text())), [])
                built = self.run_cli("generate", recipe, "--out", output)
                self.assertEqual(built["subject"], preview["scenario_preview"]["subject"])
                self.assertFalse(built["applied_to_target"])
                checked = self.run_cli("scenario-check", output / "scenario.json")
                self.assertEqual(checked["checks"], "expected-defect verified")
                self.assertFalse(checked["applied_to_target"])
                self.run_cli("check", output / "baseline/plan.json")
                error = self.run_cli("check", output / "changed/plan.json", code=2)
                self.assertIn("validation failure", error["message"])
                baseline_checks = json.loads((output / "baseline/checks.json").read_text())
                changed_checks = json.loads((output / "changed/checks.json").read_text())
                changed_intent = json.loads((output / "changed/intent.json").read_text())
                self.assertEqual(baseline_checks["status"], "passed")
                self.assertEqual(changed_checks["status"], "expected-defect verified")
                self.assertTrue(changed_checks["findings"])
                self.assertEqual(changed_checks["live_ingestion"], "not run")
                self.assertEqual(changed_intent["checks"]["offline_graph"], "expected-defect verified")
                self.assertEqual(changed_intent["scenario_stage"], "changed")
                self.assertEqual(changed_intent["demo"], "loss-of-power-diversity")
                self.assertIn("Deliberate", changed_intent["demo_scope"])
                self.assertNotIn("healthy baseline", changed_intent["demo_scope"].lower())
                self.assertTrue((output / "changed/report.md").read_text().startswith("**Deliberate defect snapshot.**"))
                self.assertTrue((output / "baseline/diode/manifest.json").exists())
                self.assertTrue((output / "changed/diode/manifest.json").exists())

    def test_frozen_build_preserves_scenario_and_wire_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recipe = self.recipe(directory, "school-district")
            first, second = directory / "generated", directory / "frozen"
            self.run_cli("generate", recipe, "--out", first)
            self.run_cli("build", first / "baseline/plan.json", "--out", second)
            self.run_cli("scenario-check", second / "scenario.json")
            self.assertEqual((first / "scenario.json").read_bytes(), (second / "scenario.json").read_bytes())
            for stage in ("baseline", "changed"):
                self.assertEqual((first / stage / "plan.json").read_bytes(), (second / stage / "plan.json").read_bytes())
                left = {path.relative_to(first / stage / "diode"): path.read_bytes()
                        for path in (first / stage / "diode").iterdir() if path.is_file()}
                right = {path.relative_to(second / stage / "diode"): path.read_bytes()
                         for path in (second / stage / "diode").iterdir() if path.is_file()}
                self.assertEqual(left, right)
                intent = json.loads((second / stage / "intent.json").read_text())
                self.assertTrue(all(field["source"] == "unknown (frozen plan)" for field in intent["resolved"].values()))

    def test_saved_scenario_tamper_cannot_be_approved_by_claimed_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recipe = self.recipe(directory, "enterprise-data-center")
            output = directory / "scenario"
            self.run_cli("generate", recipe, "--out", output)
            path = output / "scenario.json"
            original = json.loads(path.read_text())
            envelope = deepcopy(original)
            envelope["expected_findings"] = []
            path.write_bytes(canonical(envelope))
            (output / "checks.json").write_text('{"status":"passed","live_ingestion":"verified"}')
            self.run_cli("scenario-check", path, code=2)
            envelope = deepcopy(original)
            envelope["plan_files"]["changed"] = "baseline/plan.json"
            path.write_bytes(canonical(envelope))
            self.run_cli("scenario-check", path, code=2)
            path.write_bytes(canonical(original))
            changed_path = output / "changed/plan.json"
            changed = json.loads(changed_path.read_text())
            next(obj for obj in changed["objects"] if obj["kind"] == "site")["attrs"]["description"] = "Unrelated edit"
            changed_path.write_bytes(canonical(changed))
            self.run_cli("scenario-check", path, code=2)

    def test_changed_snapshot_is_rejected_for_ordinary_build_and_growth(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recipe = self.recipe(directory, "enterprise-data-center")
            output = directory / "scenario"
            self.run_cli("generate", recipe, "--out", output)
            changed = output / "changed/plan.json"
            for command in (("build", changed, "--out", directory / "bad-build"),
                            ("plan", recipe, "--previous", changed, "--out", directory / "bad-plan.json")):
                self.run_cli(*command, code=2)
            self.assertFalse((directory / "bad-build").exists())
            self.assertFalse((directory / "bad-plan.json").exists())

    def test_saved_wire_artifact_must_match_its_scenario_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recipe = self.recipe(directory, "enterprise-data-center")
            output = directory / "scenario"
            self.run_cli("generate", recipe, "--out", output)
            changed = output / "changed/diode"
            original_wire = directory / "original-wire"
            shutil.copytree(changed, original_wire)
            plans = {stage: (output / stage / "plan.json").read_bytes() for stage in ("baseline", "changed")}
            original_manifest = json.loads((changed / "manifest.json").read_text())
            shutil.rmtree(changed)
            shutil.copytree(output / "baseline/diode", changed)
            self.run_cli("scenario-check", output / "scenario.json", code=2)
            # Hashing a package against its own manifest is insufficient. A
            # copied healthy wire graph must not become the changed snapshot
            # merely by claiming the expected plan fingerprint.
            manifest = json.loads((changed / "manifest.json").read_text())
            manifest["plan_sha256"] = original_manifest["plan_sha256"]
            (changed / "manifest.json").write_bytes(canonical(manifest))
            self.run_cli("scenario-check", output / "scenario.json", code=2)
            shutil.rmtree(changed)
            shutil.copytree(original_wire, changed)
            manifest = json.loads((changed / "manifest.json").read_text())

            def edit_description(value):
                if isinstance(value, dict):
                    if isinstance(value.get("description"), str):
                        value["description"] += " Altered wire payload."
                        return True
                    return any(edit_description(item) for item in value.values())
                if isinstance(value, list):
                    return any(edit_description(item) for item in value)
                return False

            for entry in manifest["files"]:
                payload_path = changed / entry["path"]
                payload = json.loads(payload_path.read_text())
                if edit_description(payload):
                    wire = canonical(payload) + b"\n"
                    payload_path.write_bytes(wire)
                    entry.update(sha256=hashlib.sha256(wire).hexdigest(), bytes=len(wire))
                    break
            else:
                self.fail("The exported fixture must contain a described object for this wire mutation")
            (changed / "manifest.json").write_bytes(canonical(manifest))
            self.run_cli("scenario-check", output / "scenario.json", code=2)
            self.assertEqual(payload_path.read_bytes(), wire, "Verifier must not repair the supplied artifact")
            for stage, content in plans.items():
                self.assertEqual((output / stage / "plan.json").read_bytes(), content)

    def test_explicit_power_scenario_works_from_baseline_recipe_and_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path = directory / "plan.json"
            plan_path.write_bytes(canonical(generate(dict(profile="enterprise-data-center", data_centers=2))))
            result = self.run_cli("scenario", plan_path, "--kind", "loss-of-power-diversity", "--site", "dc-02", "--out", directory / "power")
            self.assertIn("/dc-02/", result["subject"])
            self.run_cli("scenario-check", directory / "power/scenario.json")
            for stage in ("baseline", "changed"):
                intent = json.loads((directory / "power" / stage / "intent.json").read_text())
                self.assertEqual(intent["demo"], "loss-of-power-diversity")
                self.assertEqual(intent["scenario_stage"], stage)
                if stage == "changed":
                    self.assertIn("Deliberate", intent["demo_scope"])
                    self.assertNotIn("healthy baseline", intent["demo_scope"].lower())
            self.run_cli("scenario", plan_path, "--kind", "loss-of-power-diversity", "--site", "absent", "--out", directory / "missing", code=2)
            self.assertFalse((directory / "missing").exists())

    def test_failed_second_export_is_atomic_and_existing_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path = directory / "plan.json"
            plan_path.write_bytes(canonical(generate(dict(profile="enterprise-data-center", data_centers=1))))
            output = directory / "scenario"
            original_export = cli.export
            calls = []

            def failing_export(plan, destination):
                calls.append(destination)
                if len(calls) == 2:
                    raise RuntimeError("Synthetic failure during changed snapshot export")
                return original_export(plan, destination)

            with patch.object(cli, "export", side_effect=failing_export), redirect_stdout(StringIO()) as captured:
                status = cli.main(["--json", "scenario", str(plan_path), "--kind", "loss-of-power-diversity", "--out", str(output)])
            self.assertEqual(status, 2)
            self.assertIn("Synthetic failure", json.loads(captured.getvalue())["message"])
            self.assertEqual(len(calls), 2)
            self.assertFalse(output.exists())
            self.assertFalse(list(directory.glob(".scenario-*")))
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("Earlier artifact")
            self.run_cli("scenario", plan_path, "--kind", "loss-of-power-diversity", "--out", output, code=2)
            self.assertEqual(sentinel.read_text(), "Earlier artifact")


if __name__ == "__main__":
    unittest.main()
