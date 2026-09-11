"""Independent counterexamples for the shared graph-selected power narrative."""

from collections import Counter
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.model import DesignError, canonical, digest
from estates.power_scenario import create, markdown, verify
from estates.validate import validate


RECIPES = {
    "regional-bank": dict(profile="regional-bank", namespace="narrative-bank", headquarters=0,
                          branches={"small": 1}),
    "enterprise-data-center": dict(profile="enterprise-data-center", namespace="narrative-dc", data_centers=2,
        workloads=[dict(key="courseware-api", listeners=[dict(key="", name="courseware", protocol="tcp", ports=[8443])])]),
    "school-district": dict(profile="school-district", namespace="narrative-school",
        schools=[dict(key="oak", classrooms=2, students_per_classroom=12, administrative_staff=4, lab_seats=8)]),
}


def finding_pairs(plan):
    return Counter((finding["code"], finding["object"]) for finding in validate(plan))


class PowerScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baselines = {profile: generate(recipe) for profile, recipe in RECIPES.items()}
        cls.scenarios = {profile: create(plan) for profile, plan in cls.baselines.items()}

    def test_all_three_profiles_have_one_physical_defect_and_exact_findings(self):
        for profile, envelope in self.scenarios.items():
            with self.subTest(profile=profile):
                verify(envelope)
                self.assertEqual(envelope["scenario"], "loss-of-power-diversity")
                self.assertEqual(envelope["schema_version"], 1)
                baseline, changed = (envelope["plans"][stage] for stage in ("baseline", "changed"))
                self.assertEqual(validate(baseline), [])
                expected = Counter((item["code"], item["object"]) for item in envelope["expected_findings"])
                self.assertTrue(expected)
                self.assertEqual(finding_pairs(changed), expected)
                self.assertTrue({code for code, _ in expected} <= {"power-redundancy", "dc-power-diversity"})
                self.assertEqual({key for _, key in expected}, {envelope["subject"]})
                changes = envelope["changes"]
                self.assertEqual(changes["counts"], {"create": 1, "update": 0, "delete": 1})
                self.assertEqual(changes["update"], [])
                self.assertTrue(all(obj["kind"] == "cable" for field in ("create", "delete") for obj in changes[field]))
                self.assertNotEqual(changes["create"][0]["key"], changes["delete"][0]["key"])
                for field in baseline.keys() - {"objects"}:
                    self.assertEqual(changed[field], baseline[field], field)

    def test_repeated_selection_and_input_object_order_are_stable(self):
        for profile, baseline in self.baselines.items():
            with self.subTest(profile=profile):
                original = canonical(baseline)
                first = create(baseline)
                self.assertEqual(canonical(first), canonical(create(baseline)))
                reordered = deepcopy(baseline)
                reordered["objects"].reverse()
                reordered["contracts"].reverse()
                self.assertEqual(validate(reordered), [])
                second = create(reordered)
                self.assertEqual(first["subject"], second["subject"])
                self.assertEqual(first["changes"], second["changes"])
                self.assertEqual(canonical(baseline), original)

    def test_inverse_restores_exact_baseline_including_record_order(self):
        for profile, envelope in self.scenarios.items():
            with self.subTest(profile=profile):
                baseline, changed = (envelope["plans"][stage] for stage in ("baseline", "changed"))
                inverse = envelope["restoration"]["inverse"]
                self.assertEqual(inverse["create"], envelope["changes"]["delete"])
                self.assertEqual(inverse["delete"], envelope["changes"]["create"])
                self.assertEqual(len(inverse["create"]), 1)
                old, new = inverse["create"][0], inverse["delete"][0]
                restored = deepcopy(changed)
                restored["objects"] = [deepcopy(old) if obj["key"] == new["key"] else obj for obj in restored["objects"]]
                self.assertEqual(canonical(restored), canonical(baseline))
                self.assertEqual(digest(restored), envelope["restoration"]["baseline_sha256"])
                self.assertEqual(validate(restored), [])

    def test_create_verify_and_render_do_not_mutate_frozen_inputs(self):
        baseline = deepcopy(self.baselines["enterprise-data-center"])
        baseline_bytes = canonical(baseline)
        envelope = create(baseline)
        envelope_bytes = canonical(envelope)
        verify(envelope)
        markdown(envelope)
        self.assertEqual(canonical(baseline), baseline_bytes)
        self.assertEqual(canonical(envelope), envelope_bytes)
        envelope["plans"]["changed"]["objects"][0]["attrs"]["description"] = "Private changed copy"
        self.assertEqual(canonical(envelope["plans"]["baseline"]), baseline_bytes)
        self.assertEqual(canonical(baseline), baseline_bytes)

    def test_site_filter_and_empty_candidate_set_are_explicit(self):
        for profile in ("regional-bank", "enterprise-data-center"):
            with self.subTest(profile=profile):
                envelope = create(self.baselines[profile], "dc-02")
                objects = {obj["key"]: obj for obj in envelope["plans"]["baseline"]["objects"]}
                self.assertEqual(objects[envelope["subject"]]["refs"]["site"], "site/dc-02")
                self.assertEqual(envelope["selection"]["site_filter"], "dc-02")
                verify(envelope)
        for site in ("absent", "school-oak"):
            with self.subTest(site=site), self.assertRaises(DesignError):
                create(self.baselines["school-district"], site)

    def test_different_workload_names_and_seeds_use_actual_hosts_and_services(self):
        for seed, workload in ((0, "online-archive"), (1, "research-compute"), (19, "payments-api")):
            with self.subTest(seed=seed, workload=workload):
                baseline = generate(dict(profile="enterprise-data-center", seed=seed, data_centers=1,
                    workloads=[dict(key=workload, groups=2, listeners=[dict(key="", name=workload, protocol="tcp", ports=[8443])])]))
                envelope = create(baseline)
                objects = {obj["key"]: obj for obj in baseline["objects"]}
                self.assertEqual(objects[envelope["subject"]]["meta"]["service_pool"], workload)
                vms = {key for key, obj in objects.items() if obj["kind"] == "virtual_machine" and obj["refs"].get("device") == envelope["subject"]}
                services = {key for key, obj in objects.items() if obj["kind"] == "service" and obj["refs"].get("virtual_machine") in vms}
                self.assertEqual(set(envelope["affected"]["virtual_machines"]), vms)
                self.assertEqual(set(envelope["affected"]["services"]), services)
                self.assertTrue(services)
                self.assertTrue(all(objects[key]["attrs"]["ports"] == [8443] for key in services))
                verify(envelope)

    def test_shared_scenario_supports_passive_campus_profiles(self):
        for profile in ("regional-bank", "school-district"):
            with self.subTest(profile=profile):
                baseline = generate(RECIPES[profile] | {"patching": "panels"})
                self.assertTrue(any(obj["kind"] == "front_port" for obj in baseline["objects"]))
                verify(create(baseline))

    def test_unhealthy_baseline_is_never_accepted(self):
        for profile, envelope in self.scenarios.items():
            with self.subTest(profile=profile), self.assertRaises(DesignError):
                create(envelope["plans"]["changed"])

    def test_ordinary_previous_cannot_consume_deliberately_changed_snapshot(self):
        for profile, envelope in self.scenarios.items():
            with self.subTest(profile=profile), self.assertRaises(DesignError):
                generate(envelope["plans"]["baseline"]["recipe"], previous=envelope["plans"]["changed"])

    def test_reported_claims_and_restoration_cannot_be_tampered(self):
        original = self.scenarios["enterprise-data-center"]
        mutations = (
            lambda e: e.update(schema_version=2),
            lambda e: e.update(schema_version=True),
            lambda e: e.update(scenario="ordinary-baseline"),
            lambda e: e.update(subject="device/dc-02/not-selected"),
            lambda e: e.update(expected_findings=[]),
            lambda e: e["selection"].update(site_filter="absent"),
            lambda e: e["paths"].update(baseline=[]),
            lambda e: e["affected"].update(services=[]),
            lambda e: e.update(answers=[]),
            lambda e: e["changes"]["counts"].update(delete=0),
            lambda e: e["checks"].update(defects=True),
            lambda e: e["restoration"].update(baseline_sha256="0" * 64),
            lambda e: e["restoration"]["inverse"].update(delete=[]),
            lambda e: e.update(limitations=[]),
            lambda e: e.update(execution={}),
            lambda e: e.update(live_workflow_verified=True),
        )
        for ordinal, mutate in enumerate(mutations):
            with self.subTest(mutation=ordinal):
                envelope = deepcopy(original)
                mutate(envelope)
                with self.assertRaises(DesignError):
                    verify(envelope)

    def test_unsupported_frozen_versions_cannot_claim_current_qualification(self):
        for field, value in (("schema_version", 999), ("schema_version", True), ("generator_version", "999.0.0")):
            with self.subTest(field=field, value=value):
                baseline = deepcopy(self.baselines["enterprise-data-center"])
                baseline[field] = value
                with self.assertRaises(DesignError):
                    create(baseline)

    def test_unrelated_defect_cannot_be_laundered_as_an_expected_finding(self):
        envelope = deepcopy(self.scenarios["enterprise-data-center"])
        changed = envelope["plans"]["changed"]
        original_hash = digest(changed)
        unrelated = next(obj for obj in changed["objects"] if obj["kind"] == "virtual_machine")
        unrelated["attrs"]["status"] = "planned"
        actual = [{"code": f["code"], "object": f["object"]} for f in validate(changed)]
        self.assertGreater(len(actual), len(envelope["expected_findings"]))
        envelope["expected_findings"] = actual
        changed_hash = digest(changed)

        def resign(value):
            if isinstance(value, dict):
                return {key: resign(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resign(item) for item in value]
            return changed_hash if value == original_hash else value

        envelope = resign(envelope)
        with self.assertRaises(DesignError):
            verify(envelope)

    def test_locally_valid_unrelated_annotation_and_ledger_edit_are_rejected(self):
        for mutation in ("annotation", "reservation"):
            with self.subTest(mutation=mutation):
                envelope = deepcopy(self.scenarios["enterprise-data-center"])
                changed = envelope["plans"]["changed"]
                if mutation == "annotation":
                    obj = next(obj for obj in changed["objects"] if obj["kind"] == "site")
                    obj["attrs"]["description"] = "Unrelated annotation outside this change"
                    self.assertEqual(finding_pairs(changed), finding_pairs(self.scenarios["enterprise-data-center"]["plans"]["changed"]))
                else:
                    changed["reservations"]["unrelated"] = {"new-allocation": 0}
                with self.assertRaises(DesignError):
                    verify(envelope)

    def test_rewire_cannot_keep_the_original_cables_identity(self):
        envelope = deepcopy(self.scenarios["enterprise-data-center"])
        old, new = (envelope["changes"][field][0] for field in ("delete", "create"))
        cable = next(obj for obj in envelope["plans"]["changed"]["objects"] if obj["key"] == new["key"])
        cable["key"] = old["key"]
        self.assertEqual(finding_pairs(envelope["plans"]["changed"]), finding_pairs(self.scenarios["enterprise-data-center"]["plans"]["changed"]))
        with self.assertRaises(DesignError):
            verify(envelope)


if __name__ == "__main__":
    unittest.main()
