"""Run with ``python -m unittest discover -s tests -p 'test_generation.py'``."""

from copy import deepcopy
import re
import unittest

from estates.bank import generate
from estates.model import DesignError, ROOT, canonical, recipe_from_file, resolve_recipe
from estates.validate import validate
from estates.validate_optics import analyze as analyze_optics


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({})
        cls.objects = {obj["key"]: obj for obj in cls.baseline["objects"]}

    def test_same_recipe_and_seed_produce_identical_bytes(self):
        self.assertEqual(canonical(generate(dict(reversed(list(self.baseline["recipe"].items()))))),
                         canonical(self.baseline))
        self.assertEqual(validate(self.baseline), [])

    def test_v09_depth_revision_requires_explicit_v08_rebaseline(self):
        # v0.8's source and qualified artifact are preserved as historical
        # evidence. New operational records and version-salted choices require
        # a new baseline rather than silently growing that old graph.
        plan = generate(recipe_from_file(ROOT / "profiles/bank-depth.toml"))
        self.assertEqual(validate(plan), [])
        previous = deepcopy(plan)
        previous["generator_version"] = "0.8.0"
        with self.assertRaisesRegex(DesignError, "version.*rebaseline"):
            generate(plan["recipe"], previous=previous)

    def test_seed_changes_only_declared_local_variation(self):
        changed = generate(self.baseline["recipe"] | {"seed": 43})
        self.assertEqual(validate(changed), [])
        self.assertEqual(changed["allocations"], self.baseline["allocations"])
        self.assertEqual(changed["reservations"], self.baseline["reservations"])
        self.assertNotEqual(changed["objects"], self.baseline["objects"])
        for original, variant in zip(self.baseline["objects"], changed["objects"], strict=True):
            with self.subTest(object=original["key"]):
                a, b = deepcopy(original), deepcopy(variant)
                for obj in (a, b):
                    if obj["kind"] == "device":
                        self.assertRegex(obj["attrs"].pop("serial"), r"^SYN-\d{10}$")
                    if obj["kind"] == "module":
                        self.assertRegex(obj["attrs"].pop("serial"), (r"^(OPT|AOC)-[0-9a-f]{24}$"
                            if obj["key"].startswith("optics-module/") else r"^SYN-PSU-\d{10}$"))
                    if obj["kind"] == "circuit":
                        obj["attrs"].pop("install_date")
                    if obj["kind"] == "journal_entry":
                        obj["attrs"]["comments"] = re.sub(r"\d{4}-\d{2}-\d{2}", "<authored-date>", obj["attrs"]["comments"])
                        if obj["key"].endswith("/equipment-record"):
                            obj["attrs"]["comments"], count = re.subn(r"(?m)^Serial: SYN-\d{10}$", "Serial: <device-serial>", obj["attrs"]["comments"])
                            self.assertEqual(count, 1)
                        if obj["key"].endswith("/psu-replacement-plan"):
                            obj["attrs"]["comments"], count = re.subn(r"(?m)^Installed PSU serial: SYN-PSU-\d{10}$", "Installed PSU serial: <module-serial>", obj["attrs"]["comments"])
                            self.assertEqual(count, 1)
                    if "lifecycle_cohort" in obj["meta"]:
                        self.assertIn(obj["meta"].pop("lifecycle_cohort"),
                                      {"legacy-refresh", "established", "new-branch"})
                        obj["attrs"].pop("comments")
                self.assertEqual(a, b)

    def test_growth_preserves_addresses_cables_positions_and_used_ports(self):
        before = canonical(self.baseline)
        grown = generate(self.baseline["recipe"] | {
            "branches": {"small": 75, "medium": 10, "large": 10},
        }, previous=self.baseline)
        self.assertEqual(validate(grown), [])
        self.assertEqual(canonical(self.baseline), before, "Growth mutated its input plan")
        objects = {obj["key"]: obj for obj in grown["objects"]}
        self.assertGreater(len(objects), len(self.objects))
        self.assertTrue(self.objects.keys() <= objects.keys(), "Growth removed existing objects")
        _, before_optics = analyze_optics(self.baseline)
        _, after_optics = analyze_optics(grown)
        used_ports = set()
        for key, original in self.objects.items():
            if original["kind"] == "cable":
                used_ports.update(original["refs"].values())
            with self.subTest(object=key):
                if original["kind"] in {"ip_address", "cable"}:
                    self.assertEqual(objects[key], original)
                if original["kind"] == "device":
                    self.assertEqual(objects[key]["refs"].get("rack"), original["refs"].get("rack"))
                    for field in ("position", "face"):
                        self.assertEqual(objects[key]["attrs"].get(field), original["attrs"].get(field))
        for key in used_ports:
            with self.subTest(used_port=key):
                old, new = deepcopy(self.objects[key]), deepcopy(objects[key])
                device = old["refs"].get("device")
                extra = after_optics.get(device, 0) - before_optics.get(device, 0)
                if old["kind"] == "power_port" and extra > 0:
                    self.assertEqual(new["attrs"]["maximum_draw"] - old["attrs"]["maximum_draw"], extra)
                    for field in ("maximum_draw", "allocated_draw"):
                        self.assertGreaterEqual(new["attrs"].pop(field), old["attrs"].pop(field))
                    old["attrs"].pop("description", None)
                    new["attrs"].pop("description", None)
                self.assertEqual(new, old)
        for site, slot in self.baseline["allocations"].items():
            self.assertEqual(grown["allocations"][site], slot)
        for scope, reservations in self.baseline["reservations"].items():
            for key, slot in reservations.items():
                self.assertEqual(grown["reservations"][scope][key], slot, (scope, key))

    def test_removed_site_reservations_are_not_recycled(self):
        reduced = generate(self.baseline["recipe"] | {"branches": {}}, previous=self.baseline)
        self.assertEqual(reduced["allocations"], self.baseline["allocations"])
        restored = generate(self.baseline["recipe"], previous=reduced)
        self.assertEqual(canonical(restored), canonical(self.baseline))

    def test_private_pool_exhaustion_fails_without_changing_previous_plan(self):
        with self.assertRaisesRegex(DesignError, r"holds 1 /20 site reservations"):
            generate({"address_pool": "192.168.0.0/20"})
        baseline = generate({"address_pool": "192.168.0.0/16"})
        before = canonical(baseline)
        with self.assertRaisesRegex(DesignError, r"holds 16 /20 site reservations"):
            generate(baseline["recipe"] | {"branches": {"small": 20}}, previous=baseline)
        self.assertEqual(canonical(baseline), before)

    def test_conflicting_recipe_requires_explicit_rebaseline(self):
        for field, value in {"namespace": "maple", "seed": 43, "address_pool": "172.16.0.0/12",
                             "as_of": "2026-09-02", "reserve_fraction": 0.3, "patching": "panels"}.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(DesignError, "rebaseline"):
                    generate(self.baseline["recipe"] | {field: value}, previous=self.baseline)

    def test_growth_cannot_replace_a_global_aggregate(self):
        baseline = generate({"address_pool": "172.16.0.0/16", "headquarters": 0,
                             "branches": {}, "design_mix": {"inherited": 100}})
        frozen = canonical(baseline)
        with self.assertRaisesRegex(DesignError, "replace an aggregate allocation"):
            generate(baseline["recipe"] | {"branches": {"small": 1}}, previous=baseline)
        self.assertEqual(canonical(baseline), frozen)

    def test_customer_rename_requires_a_new_baseline_for_every_profile(self):
        from estates.generate import generate as generate_profile
        for profile in ("regional-bank", "enterprise-data-center", "school-district",
                        "hospital-clinics", "provider-backbone"):
            with self.subTest(profile=profile):
                baseline = generate_profile({"profile": profile})
                frozen = canonical(baseline)
                recipe = baseline["recipe"] | {"name": "Renamed Customer"}
                with self.assertRaisesRegex(DesignError, "Changing name.*rebaseline"):
                    generate_profile(recipe, previous=baseline)
                self.assertEqual(canonical(baseline), frozen)
                self.assertEqual(validate(generate_profile(recipe)), [])

    def test_changed_format_generator_or_hardware_requires_rebaseline(self):
        for field, value in {"schema_version": 999, "generator_version": "other",
                             "hardware_digest": "different"}.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(DesignError, "rebaseline"):
                    generate(self.baseline["recipe"], previous=self.baseline | {field: value})

    def test_corrupt_reservations_fail_before_reallocation(self):
        for slots in ({"a": -1}, {"a": 0, "b": 0}, {"a": True}):
            with self.subTest(slots=slots):
                previous = self.baseline | {"reservations": {"bad": slots}}
                with self.assertRaisesRegex(DesignError, "reservation ledger"):
                    generate(self.baseline["recipe"], previous=previous)

    def test_well_formed_edited_ledgers_cannot_silently_move_the_frozen_estate(self):
        for ledger in ("site", "rack", "address"):
            previous = deepcopy(self.baseline)
            if ledger == "site":
                previous["allocations"]["br-s0001"] = 100
            else:
                prefix = "rack-slots/br-s0001/" if ledger == "rack" else "addresses/br-s0001/"
                scope = next(key for key in previous["reservations"] if key.startswith(prefix))
                slots = previous["reservations"][scope]
                key = next(iter(slots))
                slots[key] = max(slots.values()) + 1
            with self.subTest(ledger=ledger):
                self.assertEqual(validate(previous), [], "This corruption is not a local graph violation")
                before = canonical(previous)
                with self.assertRaisesRegex(DesignError, "does not reproduce"):
                    generate(previous["recipe"], previous=previous)
                self.assertEqual(canonical(previous), before)

    def test_invalid_and_unknown_recipe_fields_fail_explicitly(self):
        cases = [
            ({"rack_count": 2}, "Unknown recipe fields"),
            ({"branches": {"smal": 2}}, "branches"),
            ({"branches": {"small": -1}}, "branch count"),
            ({"branches": {"small": True}}, "branch count"),
            ({"seed": True}, "seed"),
            ({"namespace": "BANK EXAMPLE"}, "namespace"),
            ({"namespace": "aa-"}, "namespace"),
            ({"address_pool": "192.168.0.0/21"}, "address_pool"),
            ({"address_pool": "192.168.1.0/20"}, "address_pool"),
            ({"address_pool": "8.0.0.0/8"}, "address_pool"),
            ({"as_of": "2026-02-30"}, "as_of"),
            ({"reserve_fraction": 0.01}, "reserve_fraction"),
            ({"data_centers": 1}, "data_centers"),
            ({"profile": "unreviewed-industry"}, "reviewed profile"),
            ({"patching": "imaginary"}, "patching"),
        ]
        for recipe, error in cases:
            with self.subTest(recipe=recipe):
                with self.assertRaisesRegex(DesignError, error):
                    resolve_recipe(recipe)

    def test_object_budget_fails_instead_of_returning_a_truncated_estate(self):
        with self.assertRaisesRegex(DesignError, "Object budget 100 exceeded"):
            generate({"max_objects": 100})

    def test_namespace_separates_target_identities_and_dns(self):
        other = generate(self.baseline["recipe"] | {"namespace": "maple"})
        self.assertEqual(validate(other), [])
        for kind, field in (("tenant", "name"), ("tenant", "slug"), ("site", "slug"), ("vrf", "name"),
                            ("circuit", "cid"), ("ip_address", "dns_name")):
            with self.subTest(kind=kind, field=field):
                original_names = {obj["attrs"][field] for obj in self.baseline["objects"] if obj["kind"] == kind}
                other_names = {obj["attrs"][field] for obj in other["objects"] if obj["kind"] == kind}
                self.assertTrue(original_names)
                self.assertTrue(original_names.isdisjoint(other_names))
        # Device/VM names are intentionally short and repeat across estates;
        # their fully qualified matching identities must still be disjoint.
        identities = []
        for plan in (self.baseline, other):
            objects = {o["key"]: o for o in plan["objects"]}
            identities.append({(o["kind"], o["attrs"]["name"],
                               objects[o["refs"].get("site", o["refs"].get("cluster"))]["attrs"]["name"],
                               objects[o["refs"]["tenant"]]["attrs"]["name"])
                              for o in plan["objects"] if o["kind"] in {"device", "virtual_machine"}})
        self.assertTrue(identities[0].isdisjoint(identities[1]))
        rds = [obj["attrs"]["rd"] for plan in (self.baseline, other) for obj in plan["objects"]
               if obj["kind"] == "vrf" and obj["attrs"].get("rd")]
        self.assertEqual(len(rds), len(set(rds)), "Distinct estates share a route distinguisher")

    def test_large_estate_exceeds_100k_objects_and_validates(self):
        plan = generate({"branches": {"small": 250}, "patching": "panels"})
        self.assertGreater(len(plan["objects"]), 100000)
        self.assertEqual(len(plan["allocations"]), 253)
        self.assertEqual(validate(plan), [])

    def test_explicit_panel_mode_preserves_passive_paths_and_growth(self):
        panel = generate({"patching": "panels", "headquarters": 0, "branches": {"small": 1}})
        self.assertEqual(validate(panel), [])
        self.assertTrue(any(o["kind"] == "front_port" for o in panel["objects"]))
        self.assertFalse(any(o["kind"] == "front_port" for o in self.baseline["objects"]))
        grown = generate(panel["recipe"] | {"branches": {"small": 10}}, previous=panel)
        self.assertEqual(validate(grown), [])
        new = {o["key"]: o for o in grown["objects"]}
        for obj in panel["objects"]:
            if obj["kind"] in {"front_port", "rear_port", "cable", "ip_address"}:
                self.assertEqual(new[obj["key"]], obj)


if __name__ == "__main__":
    unittest.main()
