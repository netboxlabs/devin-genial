"""Operations examples retain useful relationships and do not invent accounts."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.validate_operations import validate


class OperationsTests(unittest.TestCase):
    def test_all_profiles_supply_required_owner_group_without_contract_dependency(self):
        from estates.generate import generate as generate_profile
        from estates.validate import validate as validate_plan
        for profile in ("regional-bank", "enterprise-data-center", "school-district"):
            with self.subTest(profile=profile):
                plan = generate_profile({"profile": profile})
                self.assertEqual(validate_plan(plan), [])
                owner = next(o for o in plan["objects"] if o["kind"] == "owner")
                group = next(o for o in plan["objects"] if o["key"] == owner["refs"]["group"])
                self.assertEqual(group["kind"], "owner_group")
                del owner["refs"]["group"]
                plan["contracts"] = []
                self.assertIn("owner-group", {f["code"] for f in validate_plan(plan)})

    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 0, "branches": {"small": 1}, "reservation_user": "admin"})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def test_operations_are_attached_to_existing_inventory(self):
        self.assertIn("owner/operations", self.objects)
        self.assertEqual(validate(self.plan), [])
        for obj in self.plan["objects"]:
            for ref in obj["refs"].values():
                for key in ref if isinstance(ref, list) else [ref]:
                    self.assertIn(key, self.objects)
        self.assertEqual(self.objects["contact-assignment/cluster/dc-01"]["refs"]["object"], "cluster/dc-01")
        self.assertEqual(self.objects["virtual-disk/vm/dc-01/ledger-db/001/disk0"]["attrs"]["size"],
                         self.objects["vm/dc-01/ledger-db/001"]["attrs"]["disk"])

    def test_disk_budget_cannot_be_double_counted(self):
        disk = self.objects["virtual-disk/vm/dc-01/ledger-db/001/disk0"]
        disk["attrs"]["size"] += 1
        self.assertIn("operations-disk-budget", self.codes())

    def test_provider_account_cannot_cross_carriers(self):
        self.objects["circuit/dc-01/a/1"]["refs"]["provider_account"] = "provider-account/provider/b"
        self.assertIn("operations-provider-account", self.codes())

    def test_reservation_cannot_overlap_equipment(self):
        self.objects["rack-reservation/dc-01/future-wan"]["attrs"]["units"] = [1]
        self.assertIn("operations-reservation-space", self.codes())

    def test_reservation_requires_external_existing_user_only(self):
        user = self.objects["external/user/reservation"]
        self.assertEqual(user["attrs"], {"username": "admin"})
        self.assertTrue(user["meta"]["external"])
        user["attrs"]["is_staff"] = True
        self.assertIn("operations-external-user", self.codes())
        self.assertIn("operations-reservation-user", self.codes())
        without = generate(self.baseline["recipe"] | {"reservation_user": ""})
        self.assertFalse(any(obj["kind"] in {"user", "rack_reservation"} for obj in without["objects"]))
        self.assertEqual(validate(without), [])
        contract = next(c for c in without["contracts"] if c["site"] == "site/dc-01")
        self.assertIn("requires an existing user", contract["operations"]["reservation_dependency"])

    def test_custom_field_requires_definition_and_declared_choice(self):
        site = self.objects["site/dc-01"]
        site["meta"]["requires"].remove("custom-field/operations-tier")
        self.assertIn("operations-custom-field", self.codes())
        site["meta"]["requires"].append("custom-field/operations-tier")
        next(iter(site["attrs"]["custom_fields"].values()))["selection"] = "undeclared"
        self.assertIn("operations-custom-field", self.codes())

    def test_contacts_and_bundles_need_real_infrastructure_attachments(self):
        self.objects["contact-assignment/site/dc-01"]["refs"]["object"] = "manufacturer/Cisco"
        self.assertIn("operations-contact", self.codes())
        cable = next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and obj["refs"].get("bundle"))
        del cable["refs"]["bundle"]
        self.assertIn("operations-cable-bundle", self.codes())

    def test_rack_type_must_match_actual_rack_geometry(self):
        self.objects["rack/dc-01/network-01"]["refs"]["rack_type"] = "rack-type/24u"
        self.assertIn("operations-rack-type", self.codes())

    def test_growth_retains_operations_identities_and_journal_text(self):
        grown = generate(self.baseline["recipe"] | {"branches": {"small": 3}}, previous=self.baseline)
        self.assertEqual(validate(grown), [])
        current = {obj["key"]: obj for obj in grown["objects"]}
        for obj in self.baseline["objects"]:
            if obj["meta"].get("operations"):
                self.assertEqual(current[obj["key"]], obj)


if __name__ == "__main__":
    unittest.main()
