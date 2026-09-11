"""Cross-profile obligations survive missing metadata and ordinary growth."""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.scenarios import create
from estates.validate import validate
from estates.validate_networking import validate as networking_findings
from estates.validate_operations import validate as operations_findings


PROFILES = ("regional-bank", "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone")


class SharedDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {profile: generate({"profile": profile}) for profile in PROFILES}

    def stripped(self, profile):
        plan = deepcopy(self.plans[profile])
        plan["contracts"] = []
        for obj in plan["objects"]:
            obj["meta"] = {}
        return plan, {obj["key"]: obj for obj in plan["objects"]}

    def test_every_profile_requires_macs_even_when_all_evidence_is_removed(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.stripped(profile)
                self.assertEqual(networking_findings(plan), [])
                self.assertTrue(any(o["kind"] == "mac_address" for o in plan["objects"]))
                plan["objects"] = [o for o in plan["objects"] if o["kind"] != "mac_address"]
                for obj in objects.values():
                    obj["refs"].pop("primary_mac_address", None)
                self.assertIn("mac-identity", {f["code"] for f in networking_findings(plan)})

    def test_mac_shape_alone_does_not_prove_stable_identity(self):
        plan, _ = self.stripped("hospital-clinics")
        mac = next(o for o in plan["objects"] if o["kind"] == "mac_address")
        mac["attrs"]["mac_address"] = "02:00:00:00:00:01"
        self.assertIn("mac-identity", {f["code"] for f in networking_findings(plan)})

    def test_every_profile_requires_circuit_accounts_outside_contract_gate(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, _ = self.stripped(profile)
                self.assertEqual(operations_findings(plan), [])
                for obj in plan["objects"]:
                    if obj["kind"] == "circuit":
                        obj["refs"].pop("provider_account", None)
                self.assertIn("operations-provider-account", {f["code"] for f in operations_findings(plan)})

    def test_device_responsibility_uses_existing_actual_tenant_desk(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.stripped(profile)
                devices = [o for o in objects.values() if o["kind"] == "device" and
                           o["refs"].get("role") in {"role/server", "role/wan-edge", "role/provider-edge", "role/customer-edge", "role/ap"}]
                self.assertTrue(devices)
                for device in devices:
                    assignment = objects[f"contact-assignment/{device['key']}"]
                    tenant = device["refs"]["tenant"]
                    expected = "contact/operations" + (f"/{tenant}" if tenant != "tenant" else "")
                    self.assertEqual(assignment["refs"]["contact"], expected)
                plan["objects"].remove(objects[f"contact-assignment/{devices[0]['key']}"])
                self.assertIn("operations-contact", {f["code"] for f in operations_findings(plan)})

    def test_bank_acquisition_retains_separate_procurement_accounts(self):
        baseline = generate({"headquarters": 0, "branches": {"small": 1}, "site_designs": {"br-s0001": "inherited"}})
        scenario = create(baseline, "br-s0001")
        for stage in scenario["plans"].values():
            objects = {o["key"]: o for o in stage["objects"]}
            for side in ("a", "b"):
                self.assertEqual(objects[f"circuit/br-s0001/{side}/1"]["refs"]["provider_account"],
                                 f"provider-account/provider/{side}/inherited")
                self.assertEqual(objects[f"circuit/dc-01/{side}/1"]["refs"]["provider_account"],
                                 f"provider-account/provider/{side}")
            self.assertEqual(validate(stage), [])
        broken = deepcopy(scenario["plans"]["acquired"])
        circuit = next(o for o in broken["objects"] if o["key"] == "circuit/br-s0001/a/1")
        circuit["refs"]["provider_account"] = "provider-account/provider/a"
        broken["contracts"] = []
        self.assertIn("operations-provider-account", {f["code"] for f in operations_findings(broken)})

    def test_all_five_enrichments_reproduce_and_keep_old_records_when_growing(self):
        kinds = {"mac_address", "provider_account", "contact", "contact_assignment", "journal_entry"}
        for profile, old in self.plans.items():
            with self.subTest(profile=profile):
                self.assertEqual(generate(old["recipe"]), old)
                recipe = deepcopy(old["recipe"])
                if profile == "regional-bank":
                    recipe["branches"]["small"] += 1
                elif profile == "enterprise-data-center":
                    recipe["workloads"].append({"key": "a-new-workload", "groups": 2})
                elif profile == "school-district":
                    recipe["schools"][0]["classrooms"] += 2
                elif profile == "hospital-clinics":
                    recipe["hospitals"][0]["wards"][0]["beds"] += 2
                    recipe["hospitals"][0]["wards"].append({"key": "a-new-ward", "beds": 4, "clinical_desks": 2})
                else:
                    recipe["customers"][0]["lan_endpoints"] += 1
                    customer = deepcopy(recipe["customers"][0])
                    customer["key"] = "a-new-customer"
                    recipe["customers"].append(customer)
                grown = generate(recipe, previous=old)
                self.assertEqual(validate(grown), [])
                actual = {o["key"]: o for o in grown["objects"]}
                for obj in old["objects"]:
                    if obj["kind"] in kinds:
                        self.assertEqual(actual[obj["key"]], obj, obj["key"])

    def test_equipment_history_is_bounded_and_cannot_be_omitted_or_falsified(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.stripped(profile)
                notes = [o for o in plan["objects"] if o["kind"] == "journal_entry"
                         and objects[o["refs"]["assigned_object"]]["kind"] == "device"]
                racks = {objects[o["refs"]["assigned_object"]]["refs"]["rack"] for o in notes}
                subjects = {o["refs"]["assigned_object"] for o in notes}
                self.assertEqual(len(racks), len(subjects))
                optical = [o for o in notes if o["key"].endswith("/optic-replacement-plan")]
                legacy = [o for o in notes if o not in optical]
                self.assertGreater(len(legacy), 2 * len(racks))  # Still includes a PSU story.
                self.assertLessEqual(len(legacy), 3 * len(racks))
                self.assertTrue(optical)
                self.assertLessEqual(len(optical), len(racks))
                self.assertEqual(len(optical), len({o["refs"]["assigned_object"] for o in optical}))
                self.assertLessEqual(len(notes), 4 * len(racks))
                self.assertEqual(operations_findings(plan), [])
                for event in ("equipment-record", "maintenance-plan", "psu-replacement-plan", "optic-replacement-plan"):
                    key = next(o["key"] for o in notes if o["key"].endswith('/'+event))
                    broken = deepcopy(plan)
                    broken["objects"] = [o for o in broken["objects"] if o["key"] != key]
                    self.assertIn("operations-journal", {f["code"] for f in operations_findings(broken)})
                    for mutation in ("false-fact", "wrong-subject", "executed", "date"):
                        broken = deepcopy(plan)
                        note = next(o for o in broken["objects"] if o["key"] == key)
                        if mutation == "false-fact":
                            note["attrs"]["comments"] = note["attrs"]["comments"].replace("Device: ", "Device: wrong-", 1)
                        elif mutation == "wrong-subject":
                            note["refs"]["assigned_object"] = "site/dc-01"
                        elif mutation == "executed":
                            note["attrs"]["comments"] += "\nReplacement executed successfully."
                        else:
                            note["attrs"]["comments"] = "2099-01-01" + note["attrs"]["comments"][10:]
                        self.assertTrue(any(f["code"].startswith("operations-journal") for f in operations_findings(broken)), mutation)


if __name__ == "__main__":
    unittest.main()
