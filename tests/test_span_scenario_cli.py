"""Exercise the provider operator path and its saved graph-to-wire boundary."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest

from estates import __main__ as cli
from estates.generate import generate
from estates.model import canonical
from estates.validate import validate


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "profiles/provider-maintenance.toml"


class SpanScenarioCliTests(unittest.TestCase):
    def run_cli(self, *args, code=0):
        with redirect_stdout(StringIO()) as output:
            actual = cli.main(["--json", *(str(arg) for arg in args)])
        self.assertEqual(actual, code, output.getvalue())
        return json.loads(output.getvalue())

    def test_recipe_preview_generate_frozen_rebuild_and_exact_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frozen, first, second = root / "plan.json", root / "generated", root / "rebuilt"
            preview = self.run_cli("plan", RECIPE, "--out", frozen)
            self.assertEqual(preview["scenario_preview"]["scenario"], "provider-span-maintenance")
            self.assertEqual(preview["intent"]["resolved"]["demo"]["source"], "supplied")
            self.assertEqual(validate(json.loads(frozen.read_text())), [])
            built = self.run_cli("generate", RECIPE, "--out", first)
            self.assertEqual(built["subject"], preview["scenario_preview"]["subject"])
            self.assertEqual(built["checks"], "expected-maintenance verified")
            self.assertFalse(built["applied_to_target"])
            self.run_cli("build", frozen, "--out", second)
            self.assertEqual((first / "scenario.json").read_bytes(), (second / "scenario.json").read_bytes())
            for artifact in (first, second):
                result = self.run_cli("scenario-check", artifact / "scenario.json")
                self.assertTrue(result["wire_snapshots_match"])
                self.assertEqual(result["checks"], "expected-maintenance verified")
                self.assertFalse(result["applied_to_target"])
                self.run_cli("check", artifact / "baseline/plan.json")
                self.run_cli("check", artifact / "changed/plan.json", code=2)
                checks = json.loads((artifact / "changed/checks.json").read_text())
                intent = json.loads((artifact / "changed/intent.json").read_text())
                self.assertEqual(checks["status"], "expected-maintenance verified")
                self.assertTrue(checks["findings"])
                self.assertEqual(intent["checks"]["offline_graph"], checks["status"])
                self.assertEqual(intent["scenario_stage"], "changed")
                self.assertEqual(intent["demo"], "provider-span-maintenance")
                self.assertTrue((artifact / "changed/report.md").read_text().startswith("**Planned span maintenance.**"))
            for stage in ("baseline", "changed"):
                self.assertEqual({p.name: p.read_bytes() for p in (first / stage / "diode").iterdir()},
                                 {p.name: p.read_bytes() for p in (second / stage / "diode").iterdir()})

    def test_explicit_selection_from_baseline_and_invalid_filters_publish_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = tomllib.loads(RECIPE.read_text())
            recipe["demo"] = "baseline"
            plan = root / "plan.json"
            plan.write_bytes(canonical(generate(recipe)))
            selected = "circuit/backbone/seed-01"
            built = self.run_cli("scenario", plan, "--kind", "provider-span-maintenance",
                                 "--span", selected, "--out", root / "span")
            self.assertEqual(built["subject"], selected)
            self.run_cli("scenario-check", root / "span/scenario.json")
            for index, flags in enumerate((
                ("--kind", "provider-span-maintenance", "--span", "absent"),
                ("--kind", "provider-span-maintenance", "--site", "chicago-west"),
                ("--kind", "loss-of-power-diversity", "--span", selected),
                ("--kind", "acquire-and-refresh", "--span", selected),
            )):
                destination = root / f"invalid-{index}"
                self.run_cli("scenario", plan, *flags, "--out", destination, code=2)
                self.assertFalse(destination.exists())

    def test_span_demo_is_provider_only_and_bad_envelopes_are_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "wrong-profile.toml"
            recipe.write_text('profile = "enterprise-data-center"\ndemo = "provider-span-maintenance"\n')
            self.run_cli("generate", recipe, "--out", root / "invalid", code=2)
            self.assertFalse((root / "invalid").exists())
            path = root / "scenario.json"
            for envelope in ([], None, "scenario", {"scenario": "unknown"}, {"scenario": "provider-span-maintenance"}):
                with self.subTest(envelope=envelope):
                    path.write_text(json.dumps(envelope))
                    result = self.run_cli("scenario-check", path, code=2)
                    self.assertTrue(result["message"])

    def test_saved_healthy_wire_cannot_substitute_for_maintenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "scenario"
            self.run_cli("generate", RECIPE, "--out", output)
            changed = output / "changed/diode"
            manifest = json.loads((changed / "manifest.json").read_text())
            shutil.rmtree(changed)
            shutil.copytree(output / "baseline/diode", changed)
            forged = json.loads((changed / "manifest.json").read_text())
            forged["plan_sha256"] = manifest["plan_sha256"]
            (changed / "manifest.json").write_bytes(canonical(forged))
            result = self.run_cli("scenario-check", output / "scenario.json", code=2)
            self.assertIn("differs from its checked snapshot", result["message"])
            self.assertEqual(json.loads((changed / "manifest.json").read_text()), forged)


if __name__ == "__main__":
    unittest.main()
