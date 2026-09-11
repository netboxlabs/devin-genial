"""Generated guidance must distinguish package scope from historical mappings."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.__main__ import main
from estates.diode import export
from estates.generate import generate
from estates.intent import compatibility_guidance, resolution
from estates.model import canonical
from estates.report import markdown


class CompatibilityGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {
            "bank-panels": generate({"headquarters": 0, "branches": {"small": 1}, "patching": "panels"}),
            "bank-direct": generate({"headquarters": 0, "branches": {"small": 1}, "patching": "direct"}),
            "enterprise": generate({"profile": "enterprise-data-center", "workloads": [{"key": "web"}]}),
            "school": generate({"profile": "school-district", "schools": [{"key": "oak", "classrooms": 2}]}),
        }

    def test_report_and_intent_match_export_policy_without_changing_graph_or_wire(self):
        for name, plan in self.plans.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = canonical(plan)
                manifest = export(plan, root / "before")
                report, intent = markdown(plan), resolution(plan)
                self.assertEqual(canonical(plan), original)
                self.assertEqual(intent["compatibility"]["source_checked_target"], manifest["source_checked_target"])
                self.assertEqual(intent["compatibility"]["local_compatibility_required"], manifest.get("local_compatibility_required", []))
                self.assertIn("Whole-package source-checked target: NetBox **4.7.0**", report)
                self.assertIn("live qualification receipts are separate", report)
                self.assertFalse(intent["compatibility"]["source_checked_target"]["live_verified"])
                export(plan, root / "after")
                self.assertEqual({p.name: p.read_bytes() for p in (root / "before").iterdir()},
                                 {p.name: p.read_bytes() for p in (root / "after").iterdir()})

    def test_panel_history_is_preserved_but_removed_from_current_assumptions(self):
        plan = self.plans["bank-panels"]
        historical = next(text for c in plan["contracts"] for text in c["assumptions"] if "4.4.10 is the source-checked target" in text)
        intent, report = resolution(plan), markdown(plan)
        self.assertEqual(intent["historical_assumptions"], [historical])
        self.assertNotIn(historical, intent["assumptions"])
        current = report.split("## Explicit assumptions\n", 1)[1].split("## Historical mapping evidence\n", 1)[0]
        self.assertNotIn("4.4.10 is the source-checked target", current)
        history = report.split("## Historical mapping evidence\n", 1)[1]
        self.assertIn(historical, history)
        for phrase in ("4.4.10 evidence applies only", "no NetBox 4.4.10 compatibility claim",
                       "Unpatched", "4.5.0+, including 4.7.0", "explicit opt-in local front-port bridge",
                       "does not establish official Cloud or Enterprise compatibility"):
            self.assertIn(phrase, report)
            self.assertIn(phrase, " ".join(intent["compatibility"]["notes"]))
        for name in ("bank-direct", "enterprise", "school"):
            self.assertEqual(resolution(self.plans[name])["historical_assumptions"], [])
            self.assertNotIn("NetBox 4.4.10", markdown(self.plans[name]))

    def test_mapping_requirement_comes_from_actual_objects(self):
        plan = deepcopy(self.plans["bank-panels"])
        plan["recipe"]["patching"] = "direct"
        self.assertEqual(compatibility_guidance(plan)["compatibility"]["local_compatibility_required"], ["front_port_mapping"])

    def test_standalone_report_and_frozen_build_intent_all_three_profiles(self):
        for name in ("bank-panels", "enterprise", "school"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                plan = self.plans[name]
                root = Path(directory)
                source = root / "plan.json"
                source.write_bytes(canonical(plan))
                with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["report", str(source)]), 0)
                self.assertEqual(output.getvalue().strip(), markdown(plan).strip())
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["build", str(source), "--out", str(root / "artifact")]), 0)
                intent = json.loads((root / "artifact/intent.json").read_text())
                self.assertEqual(intent, resolution(plan))
                self.assertEqual(json.loads((root / "artifact/plan.json").read_text()), plan)
                self.assertEqual(source.read_bytes(), canonical(plan))


if __name__ == "__main__":
    unittest.main()
