"""The operator's drift-twin build and re-check paths, including tampering."""

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from estates import __main__ as cli
from estates.generate import generate
from estates.model import canonical


ROOT = Path(__file__).resolve().parents[1]
RECIPES = {
    "regional-bank": dict(profile="regional-bank", headquarters=0, branches={"small": 1}),
    "msp": dict(profile="msp", customers=[dict(key="brightline", offices=1, staff=24)]),
    "manufacturing": dict(profile="manufacturing", plants=[dict(
        key="granite", production_lines=2, warehouse_docks=1, office_staff=12)]),
}
_PLANS = {}


def plan_bytes(profile):
    if profile not in _PLANS:
        _PLANS[profile] = canonical(generate(RECIPES[profile])) + b"\n"
    return _PLANS[profile]


class DriftCliTests(unittest.TestCase):
    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-m", "estates", "--json", *(str(arg) for arg in args)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        return json.loads(result.stdout)

    def build(self, directory, profile="regional-bank"):
        plan = directory / "plan.json"
        plan.write_bytes(plan_bytes(profile))
        output = directory / "drift"
        return plan, output, self.run_cli("drift", plan, "--out", output)

    def test_build_then_check_round_trips_and_never_claims_a_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan, output, built = self.build(directory)
            self.assertEqual(built["artifact"], "discovery-drift")
            self.assertFalse(built["applied_to_target"])
            self.assertEqual(built["checks"], "expected-drift verified")
            self.assertGreaterEqual(built["items"], 12)
            checked = self.run_cli("drift-check", output)
            self.assertTrue(checked["observed_payload_matches_manifest"])
            self.assertFalse(checked["applied_to_target"])
            self.assertEqual(checked["evidence"]["items"], built["items"])
            for name in ("manifest.json", "drift.md", "checks.json",
                         "baseline/plan.json", "observed/manifest.json"):
                self.assertTrue((output / name).exists(), name)
            self.assertFalse((output / "observed/plan.json").exists())

    def test_the_bound_baseline_copy_is_byte_identical_to_its_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan, output, _ = self.build(directory)
            self.assertEqual((output / "baseline/plan.json").read_bytes(), plan.read_bytes())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["baseline"]["plan_file"], "baseline/plan.json")
            self.assertEqual(manifest["checks"]["plan_sha256"]["baseline"],
                             manifest["baseline"]["plan_sha256"])

    def test_two_builds_of_one_baseline_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan, first, _ = self.build(directory)
            second = directory / "again"
            self.run_cli("drift", plan, "--out", second)
            for path in sorted(first.rglob("*")):
                if path.is_file():
                    mirror = second / path.relative_to(first)
                    self.assertEqual(path.read_bytes(), mirror.read_bytes(), str(path.relative_to(first)))

    def test_the_observed_payload_holds_only_the_drifted_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, built = self.build(directory)
            wire = json.loads((output / "observed/manifest.json").read_text())
            self.assertEqual(wire["canonical_records"], built["observed_records"])
            self.assertEqual(wire["projection"]["emitted_records"], built["observed_records"])
            self.assertGreater(wire["projection"]["unemitted_records_resolve_identities_only"], 1000)
            self.assertLessEqual(set(wire["counts"]), {"device", "interface", "ip_address", "vlan"})
            self.assertEqual(wire["known_incompatible_netbox"], [])

    def test_the_observed_payload_never_reaches_a_47_only_kind(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            manifest = json.loads((output / "manifest.json").read_text())
            from estates import drift
            self.assertLessEqual(set(manifest["checks"]["emitted_kinds"]), drift.NETBOX_46_KINDS)
            payload = b"".join(path.read_bytes() for path in sorted((output / "observed").glob("phase-*.json")))
            for kind in drift.NETBOX_47_ONLY_KINDS:
                self.assertNotIn(f'"{kind}"'.encode(), payload)
            self.assertEqual(manifest["target"]["netbox"], "4.6")

    def test_a_tampered_manifest_cannot_be_approved_by_its_own_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            path = output / "manifest.json"
            original = json.loads(path.read_text())
            for mutate in (lambda envelope: envelope["items"].pop(),
                           lambda envelope: envelope["counts"].update(items=99),
                           lambda envelope: envelope["limitations"].clear(),
                           lambda envelope: envelope["target"].update(live_verified=True),
                           lambda envelope: envelope["checks"].update(netbox_46_kinds_only="waived")):
                envelope = deepcopy(original)
                mutate(envelope)
                path.write_bytes(canonical(envelope))
                (output / "checks.json").write_text('{"status":"passed","live_ingestion":"verified"}')
                self.run_cli("drift-check", output, code=2)
            path.write_bytes(canonical(original))

    def test_a_tampered_baseline_copy_breaks_the_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            path = output / "baseline/plan.json"
            plan = json.loads(path.read_text())
            manifest = json.loads((output / "manifest.json").read_text())
            switch = next(obj for obj in plan["objects"] if obj["key"] == manifest["selection"]["switch"])
            switch["attrs"]["serial"] = "SYN-0000000009"
            path.write_bytes(canonical(plan))
            self.run_cli("drift-check", output, code=2)

    def test_a_tampered_checks_record_breaks_the_binding(self):
        # checks.json is the honesty record: claiming live verification in it
        # must not ride a green drift-check.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            path = output / "checks.json"
            record = json.loads(path.read_text())
            record["live_ingestion"] = "verified"
            record["assurance_deviations"] = "14 deviations confirmed in the Assurance UI"
            path.write_bytes(canonical(record) + b"\n")
            self.run_cli("drift-check", output, code=2)
            path.unlink()
            self.run_cli("drift-check", output, code=2)

    def test_a_tampered_observed_payload_breaks_the_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            wire_manifest = output / "observed/manifest.json"
            manifest = json.loads(wire_manifest.read_text())
            entry = manifest["files"][-1]
            path = output / "observed" / entry["path"]
            payload = json.loads(path.read_text())
            payload["entities"] = payload["entities"][:-1] or payload["entities"]
            payload["stream"] = "tampered"
            wire = canonical(payload) + b"\n"
            path.write_bytes(wire)
            entry.update(sha256=hashlib.sha256(wire).hexdigest(), bytes=len(wire))
            wire_manifest.write_bytes(canonical(manifest))
            self.run_cli("drift-check", output, code=2)
            self.assertEqual(path.read_bytes(), wire, "the checker must not repair the supplied artifact")

    def test_a_removed_observed_request_breaks_the_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            next(iter(sorted((output / "observed").glob("phase-*.json")))).unlink()
            self.run_cli("drift-check", output, code=2)

    def test_a_rewritten_walkthrough_breaks_the_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, output, _ = self.build(directory)
            path = output / "drift.md"
            path.write_text(path.read_text().replace("Active Deviations", "Verified Deviations"))
            self.run_cli("drift-check", output, code=2)

    def test_a_defective_scenario_snapshot_is_not_a_drift_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = directory / "plan.json"
            plan.write_bytes(plan_bytes("regional-bank"))
            scenario = directory / "power"
            self.run_cli("scenario", plan, "--kind", "loss-of-power-diversity", "--out", scenario)
            self.run_cli("drift", scenario / "changed/plan.json", "--out", directory / "bad", code=2)
            self.assertFalse((directory / "bad").exists())

    def test_a_recipe_is_refused_with_actionable_guidance(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recipe = directory / "recipe.toml"
            recipe.write_text('profile = "regional-bank"\n')
            error = self.run_cli("drift", recipe, "--out", directory / "bad", code=2)
            self.assertIn("not a frozen plan.json", error["message"])

    def test_an_existing_output_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = directory / "plan.json"
            plan.write_bytes(plan_bytes("regional-bank"))
            output = directory / "drift"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("Earlier artifact")
            self.run_cli("drift", plan, "--out", output, code=2)
            self.assertEqual(sentinel.read_text(), "Earlier artifact")

    def test_a_failed_export_leaves_no_partial_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "plan.json"
            path.write_bytes(plan_bytes("regional-bank"))
            output = directory / "drift"
            with patch.object(cli, "export", side_effect=RuntimeError("Synthetic export failure")), \
                    redirect_stdout(StringIO()) as captured:
                status = cli.main(["--json", "drift", str(path), "--out", str(output)])
            self.assertEqual(status, 2)
            self.assertIn("Synthetic export failure", json.loads(captured.getvalue())["message"])
            self.assertFalse(output.exists())
            self.assertFalse(list(directory.glob(".drift-*")))

    def test_three_profiles_build_and_check_the_same_reviewed_shape(self):
        for profile in RECIPES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _, output, built = self.build(directory, profile)
                self.assertEqual(built["items"], 14)
                checked = self.run_cli("drift-check", output)
                self.assertEqual(checked["checks"], "expected-drift verified")
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["baseline"]["profile"], RECIPES[profile]["profile"])
                self.assertIn("Active Deviations", (output / "drift.md").read_text())

    def test_drift_adds_no_gate_to_ordinary_plan_checking(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan, output, _ = self.build(directory)
            self.run_cli("check", plan)
            self.run_cli("check", output / "baseline/plan.json")
            # The observed snapshot is an ingest payload, never a loadable estate.
            self.assertEqual([path.relative_to(output).as_posix() for path in sorted(output.rglob("plan.json"))],
                             ["baseline/plan.json"])

    def test_a_whole_estate_export_is_unaffected_by_the_projection_option(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = json.loads(plan_bytes("regional-bank"))
            from estates.diode import export
            whole = export(plan, directory / "whole")
            projected = export(plan, directory / "projected", keys=[obj["key"] for obj in plan["objects"]])
            self.assertEqual(whole["canonical_records"], projected["canonical_records"])
            self.assertEqual(whole["estate_sha256"], projected["estate_sha256"])
            self.assertNotIn("projection", whole)
            requests = {name: (directory / "whole" / name).read_bytes()
                        for name in sorted(path.name for path in (directory / "whole").glob("phase-*.json"))}
            mirrored = {name: (directory / "projected" / name).read_bytes() for name in requests}
            self.assertEqual(sorted(requests), sorted(mirrored))
            for name in requests:
                self.assertNotEqual(requests[name], mirrored[name],
                                    "a projection must not reuse whole-estate request IDs")
                self.assertEqual(json.loads(requests[name])["entities"],
                                 json.loads(mirrored[name])["entities"])

    def test_a_projection_cannot_name_a_record_outside_its_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            from estates.diode import export
            plan = json.loads(plan_bytes("regional-bank"))
            with self.assertRaises(ValueError) as caught:
                export(plan, Path(temporary) / "bad", keys=["device/invented/nowhere"])
            self.assertIn("absent records", str(caught.exception))
            with self.assertRaises(ValueError):
                export(plan, Path(temporary) / "empty", keys=[])
            self.assertFalse((Path(temporary) / "bad").exists())


if __name__ == "__main__":
    unittest.main()
