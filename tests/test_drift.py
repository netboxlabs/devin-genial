"""Independent checks on the Assurance discovery-drift twin.

Every assertion inside the drift checker gets a mutation that must trip it: a
manifest that merely claims an exact deviation set proves nothing.
"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from estates import drift
from estates.diode import _References, _index
from estates.generate import generate
from estates.model import DesignError, canonical, digest


RECIPES = {
    "regional-bank": dict(profile="regional-bank", headquarters=0, branches={"small": 1}),
    "school-district": dict(profile="school-district", schools=[dict(
        key="oak", classrooms=2, students_per_classroom=12, administrative_staff=4, lab_seats=8)]),
    "msp": dict(profile="msp", customers=[dict(key="brightline", offices=1, staff=24)]),
    "manufacturing": dict(profile="manufacturing", plants=[dict(
        key="granite", production_lines=2, warehouse_docks=1, office_staff=12)]),
}
_PLANS = {}
_ORIGINAL_ITEMS = drift._items


def plan_for(profile):
    if profile not in _PLANS:
        _PLANS[profile] = generate(RECIPES[profile])
    return deepcopy(_PLANS[profile])


def mutated_items(mutate):
    """Patch the item builder with a deliberately broken variant of itself."""
    def factory(objects, children, subjects):
        return mutate(objects, subjects, *_ORIGINAL_ITEMS(objects, children, subjects))
    return patch.object(drift, "_items", side_effect=factory)


class DriftSelectionTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_for("regional-bank")
        self.envelope = drift.create(self.plan)

    def test_every_reviewed_deviation_class_is_covered_by_a_named_item(self):
        classes = {item["deviation_class"] for item in self.envelope["items"]}
        self.assertEqual(classes, set(drift.CLASSES))
        self.assertGreaterEqual(len(self.envelope["items"]), 12)
        self.assertLessEqual(len(self.envelope["items"]), 20)
        self.assertEqual(len({item["id"] for item in self.envelope["items"]}), len(self.envelope["items"]))

    def test_subjects_are_real_documented_records_at_one_documented_site(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        selection = self.envelope["selection"]
        self.assertEqual(objects[selection["site"]]["kind"], "site")
        for key in [selection["switch"], selection["management_ip"], selection["management_interface"],
                    selection["unobserved_port"], *selection["drift_ports"], *selection["endpoints"]]:
            self.assertIn(key, objects, key)
        self.assertEqual(objects[selection["switch"]]["refs"]["site"], selection["site"])
        self.assertEqual(len(set(selection["endpoints"])), 4)
        self.assertNotIn(selection["unobserved_port"], selection["drift_ports"])

    def test_generation_is_deterministic_for_one_frozen_baseline(self):
        again = drift.create(plan_for("regional-bank"))
        self.assertEqual(canonical(self.envelope), canonical(again))

    def test_selection_survives_growth_that_appends_sites_and_endpoints(self):
        grown = generate(dict(profile="regional-bank", headquarters=0,
                              branches={"small": 3, "medium": 1, "large": 1}),
                         previous=plan_for("regional-bank"))
        after = drift.create(grown)
        self.assertEqual(after["selection"], self.envelope["selection"])
        self.assertEqual(canonical(after["items"]), canonical(self.envelope["items"]))

    def test_selection_survives_in_place_growth_at_the_anchor_site(self):
        # The adversarial review's failing case: growing MSP staff appends pods
        # whose names sort BEFORE existing endpoints; the room-ledger rank must
        # keep every subject fixed anyway.
        small = dict(profile="msp", namespace="drifter", name="Drifter Networks",
                     customers=[dict(key="brightline", offices=1, staff=4)])
        baseline = generate(small)
        before = drift.create(baseline)
        grown = generate(dict(small, customers=[dict(key="brightline", offices=1, staff=40)]),
                         previous=baseline)
        after = drift.create(grown)
        self.assertEqual(after["selection"], before["selection"])
        self.assertEqual(canonical(after["items"]), canonical(before["items"]))

    def test_data_center_only_estates_fail_with_an_actionable_error(self):
        plan = generate(dict(profile="enterprise-data-center", data_centers=1,
                             workloads=[dict(key="customer-portal")]))
        with self.assertRaises(DesignError) as caught:
            drift.create(plan)
        self.assertIn("no site offers an active access switch", str(caught.exception))
        self.assertIn("regional-bank", str(caught.exception))

    def test_an_unhealthy_baseline_is_refused_before_any_drift_is_planted(self):
        plan = plan_for("regional-bank")
        next(obj for obj in plan["objects"] if obj["kind"] == "cable")["attrs"]["status"] = "planned"
        with self.assertRaises(DesignError) as caught:
            drift.create(plan)
        self.assertIn("Drift twin", str(caught.exception))
        self.assertIn("healthy", str(caught.exception))

    def test_a_foreign_generator_version_is_refused(self):
        plan = plan_for("regional-bank")
        plan["generator_version"] = "0.0.1"
        with self.assertRaises(DesignError):
            drift.create(plan)


class DriftPayloadTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_for("regional-bank")
        self.envelope = drift.create(self.plan)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}
        self.observed = {obj["key"]: obj for obj in self.envelope["observed_plan"]["objects"]}

    def test_the_baseline_artifact_is_never_mutated(self):
        self.assertEqual(canonical(self.plan), canonical(plan_for("regional-bank")))
        self.assertEqual(self.envelope["baseline"]["plan_sha256"], digest(plan_for("regional-bank")))

    def test_each_observed_record_is_owned_by_exactly_one_drift_item(self):
        declared = [key for item in self.envelope["items"] for key in item["records"]]
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(set(declared), set(self.envelope["observed"]["records"]))
        for item in self.envelope["items"]:
            for key in item["records"]:
                self.assertEqual(self.observed[key]["meta"]["drift_item"], item["id"])

    def test_changed_records_keep_their_documented_diode_matching_identity(self):
        documented = _References(_index(self.plan))
        observed = _References(_index(self.envelope["observed_plan"]))
        changed = [key for key in self.envelope["observed"]["records"] if key in self.objects]
        self.assertTrue(changed)
        for key in changed:
            self.assertEqual(canonical(documented.thin(key)), canonical(observed.thin(key)), key)

    def test_created_records_do_not_reuse_a_documented_matching_identity(self):
        documented = _References(_index(self.plan))
        observed = _References(_index(self.envelope["observed_plan"]))
        created = [key for key in self.envelope["observed"]["records"] if key not in self.objects]
        self.assertTrue(created)
        for key in created:
            kind = self.observed[key]["kind"]
            taken = {canonical(documented.thin(other)) for other, obj in self.objects.items() if obj["kind"] == kind}
            self.assertNotIn(canonical(observed.thin(key)), taken, key)

    def test_declared_change_types_account_for_exactly_the_observed_records(self):
        rows = [row for item in self.envelope["items"] for row in item["expected_changes"]]
        self.assertEqual(sorted(row["record"] for row in rows), sorted(self.envelope["observed"]["records"]))
        for row in rows:
            self.assertEqual(row["change_type"], "update" if row["record"] in self.objects else "create")
        counts = self.envelope["counts"]["by_change_type"]
        self.assertEqual(counts["create"] + counts["update"], self.envelope["counts"]["emitted_records"])

    def test_documented_not_observed_items_emit_nothing_and_say_why(self):
        absent = [item for item in self.envelope["items"]
                  if item["deviation_class"] == "documented-not-observed"]
        self.assertTrue(absent)
        for item in absent:
            self.assertEqual(item["records"], [])
            self.assertEqual(item["expected_changes"], [])
            self.assertEqual(item["detection"], "requires-target-side-comparison")
            self.assertIn(item["object"], self.objects)
            self.assertNotIn(item["object"], self.envelope["observed"]["records"])
            self.assertIn("No deviation is produced by this payload", item["expected_deviation"])
            self.assertEqual(item["inverse"], "no ingest expresses this item")

    def test_every_drifted_field_actually_differs_from_documented_state(self):
        for item in self.envelope["items"]:
            for row in item["fields"]:
                self.assertNotEqual(row["documented"], row["observed"], item["id"])
        self.assertTrue(any(item["fields"] for item in self.envelope["items"]))

    def test_documented_field_values_are_read_back_from_the_baseline_graph(self):
        for item in self.envelope["items"]:
            if item["documented"] is None:
                continue
            self.assertEqual(canonical(item["documented"]), canonical(self.objects[item["object"]]))
            record = self.objects[item["object"]]
            for row in item["fields"]:
                stored = record["attrs"].get(row["field"], record["refs"].get(row["field"]))
                self.assertEqual(stored, row["documented"], (item["id"], row["field"]))

    def test_observed_payload_stays_inside_the_reviewed_netbox_46_kinds(self):
        self.assertFalse(drift.NETBOX_46_KINDS & drift.NETBOX_47_ONLY_KINDS)
        self.assertLessEqual(set(self.envelope["checks"]["emitted_kinds"]), drift.NETBOX_46_KINDS)
        self.assertTrue(drift.NETBOX_47_ONLY_KINDS.isdisjoint(self.envelope["checks"]["emitted_kinds"]))

    def test_reachable_identity_kinds_are_checked_not_only_emitted_kinds(self):
        # A device emits no `rack` record, yet its nested identity names one.
        self.assertIn("rack", self.envelope["checks"]["emitted_kinds"])
        self.assertNotIn("rack", {self.observed[key]["kind"] for key in self.envelope["observed"]["records"]})

    def test_observed_addresses_and_vlan_ids_are_unused_in_the_baseline(self):
        documented_addresses = {obj["attrs"]["address"] for obj in self.objects.values()
                                if obj["kind"] == "ip_address"}
        created = [key for key in self.envelope["observed"]["records"] if key not in self.objects]
        for key in created:
            record = self.observed[key]
            if record["kind"] == "ip_address":
                self.assertNotIn(record["attrs"]["address"], documented_addresses)
            if record["kind"] == "vlan":
                scope = self.envelope["selection"]["vlan_scope"]
                field = self.envelope["selection"]["vlan_scope_field"]
                siblings = {obj["attrs"]["vid"] for obj in self.objects.values()
                            if obj["kind"] == "vlan" and obj["refs"].get(field) == scope}
                self.assertNotIn(record["attrs"]["vid"], siblings)

    def test_inverse_restores_the_exact_frozen_baseline_object_set(self):
        restored = {obj["key"]: deepcopy(obj) for obj in self.plan["objects"]}
        for key in self.envelope["observed"]["records"]:
            if key in restored:
                restored[key] = deepcopy(self.observed[key])
        for record in self.envelope["inverse"]["documented_records"]:
            restored[record["key"]] = deepcopy(record)
        self.assertEqual(canonical(sorted(restored.values(), key=lambda obj: obj["key"])),
                         canonical(self.plan["objects"]))

    def test_inverse_names_the_objects_an_ingest_cannot_withdraw(self):
        created = sorted(key for key in self.envelope["observed"]["records"] if key not in self.objects)
        self.assertEqual(self.envelope["inverse"]["not_cleared_by_ingest"], created)
        self.assertTrue(created)
        self.assertIn("target-side", self.envelope["inverse"]["live"])

    def test_no_claim_of_live_assurance_behaviour_is_recorded(self):
        self.assertFalse(self.envelope["target"]["live_verified"])
        self.assertFalse(self.envelope["execution"]["applied_to_target"])
        self.assertEqual(self.envelope["execution"]["assurance_deviations"], "predicted-offline-unverified")
        text = canonical(self.envelope).decode().lower()
        for claim in ("live ingestion verified", "assurance verified", "deviations confirmed"):
            self.assertNotIn(claim, text)


class DriftVerificationTests(unittest.TestCase):
    """Each checker assertion needs a mutation that trips it."""

    def setUp(self):
        self.plan = plan_for("regional-bank")
        self.envelope = drift.create(self.plan)
        self.manifest = {key: value for key, value in deepcopy(self.envelope).items() if key != "observed_plan"}

    def test_the_untouched_manifest_verifies_against_its_baseline(self):
        checks, observed_plan, records = drift.verify(self.manifest, self.plan)
        self.assertTrue(checks["netbox_46_kinds_only"])
        self.assertEqual(sorted(records), sorted(self.envelope["observed"]["records"]))
        self.assertEqual(canonical(observed_plan), canonical(self.envelope["observed_plan"]))

    def test_a_manifest_cannot_drop_an_expected_item(self):
        self.manifest["items"] = self.manifest["items"][:-1]
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_manifest_cannot_relabel_a_deviation_class(self):
        self.manifest["items"][0]["deviation_class"] = "data-quality"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_manifest_cannot_claim_a_different_documented_value(self):
        item = next(item for item in self.manifest["items"] if item["fields"])
        item["fields"][0]["documented"] = "fabricated"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_manifest_cannot_soften_its_own_checks(self):
        self.manifest["checks"]["netbox_46_kinds_only"] = "waived"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_manifest_cannot_add_an_unsupported_claim(self):
        self.manifest["live_assurance"] = "verified"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_manifest_cannot_drop_a_limitation(self):
        self.manifest["limitations"] = self.manifest["limitations"][:1]
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_foreign_artifact_label_is_refused(self):
        self.manifest["artifact"] = "something-else"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, self.plan)

    def test_a_tampered_baseline_no_longer_matches_its_manifest(self):
        plan = deepcopy(self.plan)
        switch = next(obj for obj in plan["objects"] if obj["key"] == self.envelope["selection"]["switch"])
        switch["attrs"]["serial"] = "SYN-0000000001"
        with self.assertRaises(DesignError):
            drift.verify(self.manifest, plan)

    def test_an_expressible_item_that_owns_no_record_is_refused(self):
        def mutate(objects, subjects, items, observed):
            items.append(dict(items[0], id="ghost", records=[], expected_changes=[], fields=[]))
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("may declare no observed record", str(caught.exception))

    def test_an_item_naming_no_real_record_is_refused(self):
        def mutate(objects, subjects, items, observed):
            items[-1] = dict(items[-1], object="device/invented/nowhere")
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("must name a real documented or observed record", str(caught.exception))

    def test_two_items_cannot_declare_the_same_observed_record(self):
        def mutate(objects, subjects, items, observed):
            items.append(dict(next(item for item in items if item["records"]), id="second-owner"))
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("cannot be declared by two drift items", str(caught.exception))

    def test_an_item_identifier_cannot_be_reused(self):
        def mutate(objects, subjects, items, observed):
            items[1] = dict(items[1], id=items[0]["id"])
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("identifiers must be unique", str(caught.exception))

    def test_dropping_a_deviation_class_is_refused(self):
        def mutate(objects, subjects, items, observed):
            return [item for item in items if item["deviation_class"] != "documented-not-observed"], observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("every reviewed deviation class", str(caught.exception))

    def test_a_record_carrying_another_items_label_is_refused(self):
        def mutate(objects, subjects, items, observed):
            observed[items[-1]["records"][0]]["meta"]["drift_item"] = "mislabelled"
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("identifier of the drift item that declares it", str(caught.exception))

    def test_drifting_a_matching_identity_is_refused(self):
        def mutate(objects, subjects, items, observed):
            observed[subjects["switch"]]["attrs"]["name"] += "-renamed"
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("matching identity", str(caught.exception))

    def test_a_created_record_reusing_a_documented_identity_is_refused(self):
        def mutate(objects, subjects, items, observed):
            rogue = next(key for key in sorted(observed)
                         if key not in objects and observed[key]["kind"] == "device")
            observed[rogue]["attrs"]["name"] = objects[subjects["switch"]]["attrs"]["name"]
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("reuses a documented matching identity", str(caught.exception))

    def test_a_kind_outside_the_reviewed_46_set_is_refused(self):
        with patch.object(drift, "NETBOX_46_KINDS", drift.NETBOX_46_KINDS - {"vlan"}):
            with self.assertRaises(DesignError) as caught:
                drift.create(self.plan)
        self.assertIn("outside the reviewed NetBox 4.6 set", str(caught.exception))
        self.assertIn("vlan", str(caught.exception))

    def test_a_47_only_kind_cannot_be_declared_acceptable_on_46(self):
        with patch.object(drift, "NETBOX_46_KINDS", drift.NETBOX_46_KINDS | {"cooling_source"}):
            with self.assertRaises(DesignError) as caught:
                drift.create(self.plan)
        self.assertIn("cannot contain a 4.7 addition", str(caught.exception))

    def test_a_drift_set_without_created_records_is_refused(self):
        def mutate(objects, subjects, items, observed):
            for key in [key for key in sorted(observed) if key not in objects]:
                observed.pop(key)
            keep = []
            for item in items:
                item["records"] = [key for key in item["records"] if key in objects]
                item["expected_changes"] = [row for row in item["expected_changes"] if row["record"] in objects]
                if item["records"] or item["deviation_class"] == "documented-not-observed":
                    keep.append(item)
            keep[0]["deviation_class"] = "undocumented-object"
            return keep, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("both created and changed observed records", str(caught.exception))

    def test_mutating_the_bound_baseline_while_deriving_is_refused(self):
        def mutate(objects, subjects, items, observed):
            objects[subjects["switch"]]["attrs"]["serial"] = "SYN-0000000000"
            return items, observed
        with mutated_items(mutate), self.assertRaises(DesignError) as caught:
            drift.create(self.plan)
        self.assertIn("must not modify the bound baseline", str(caught.exception))

    def test_an_observed_record_that_changes_nothing_is_refused(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        key = self.envelope["selection"]["switch"]
        with self.assertRaises(DesignError) as caught:
            drift._mutated(objects, key, "replaced-chassis-serial",
                           attrs={"serial": objects[key]["attrs"]["serial"]})
        self.assertIn("must change an observed field", str(caught.exception))

    def test_a_replacement_serial_must_differ_from_the_documented_serial(self):
        with patch.object(drift, "_token", return_value="0" * 64):
            with self.assertRaises(DesignError) as caught:
                drift._observed_serial("device/x", "SYN-0000000000")
        self.assertIn("must differ", str(caught.exception))

    def test_an_address_outside_every_documented_prefix_is_refused(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        with self.assertRaises(DesignError) as caught:
            drift._unused_address(objects, "198.51.100.7/24", None, set())
        self.assertIn("no documented prefix", str(caught.exception))

    def test_successive_observed_addresses_never_collide(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        documented = objects[self.envelope["selection"]["management_ip"]]
        taken = set()
        first = drift._unused_address(objects, documented["attrs"]["address"],
                                      documented["refs"].get("vrf"), taken)
        second = drift._unused_address(objects, documented["attrs"]["address"],
                                       documented["refs"].get("vrf"), taken)
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])

    def test_a_fully_documented_vlan_scope_is_refused(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        _, children = drift._graph(self.plan)
        field, scope = self.envelope["selection"]["vlan_scope_field"], self.envelope["selection"]["vlan_scope"]
        crowded = dict(objects)
        for vid in range(100, 1000):
            key = f"vlan/crowded/{vid}"
            crowded[key] = dict(key=key, kind="vlan", attrs={"vid": vid}, refs={field: scope}, meta={})
            children[(field, scope)].append(key)
        with self.assertRaises(DesignError) as caught:
            drift._unused_vid(crowded, children, field, scope)
        self.assertIn("undocumented VLAN cannot be expressed", str(caught.exception))


class DriftProfileTests(unittest.TestCase):
    def test_every_access_bearing_profile_produces_the_same_reviewed_drift_shape(self):
        for profile in RECIPES:
            with self.subTest(profile=profile):
                envelope = drift.create(plan_for(profile))
                self.assertEqual(envelope["counts"]["items"], 14)
                self.assertEqual(envelope["counts"]["emitted_records"],
                                 envelope["counts"]["by_change_type"]["create"] +
                                 envelope["counts"]["by_change_type"]["update"])
                self.assertEqual({item["deviation_class"] for item in envelope["items"]}, set(drift.CLASSES))
                self.assertLessEqual(set(envelope["checks"]["emitted_kinds"]), drift.NETBOX_46_KINDS)
                self.assertEqual(envelope["baseline"]["profile"], plan_for(profile)["recipe"]["profile"])

    def test_the_walkthrough_is_derived_and_names_the_review_screen(self):
        plan = plan_for("msp")
        envelope = drift.create(plan)
        manifest = {key: value for key, value in deepcopy(envelope).items() if key != "observed_plan"}
        text = drift.markdown(manifest, plan)
        self.assertIn("Active Deviations", text)
        self.assertIn("just drift-check", text)
        for item in envelope["items"]:
            self.assertIn(item["id"], text)
        self.assertIn("no absence", text.lower())
        self.assertIn("target-side", text)

    def test_the_walkthrough_refuses_an_unverified_manifest(self):
        plan = plan_for("msp")
        manifest = {key: value for key, value in deepcopy(drift.create(plan)).items() if key != "observed_plan"}
        manifest["counts"]["items"] = 99
        with self.assertRaises(DesignError):
            drift.markdown(manifest, plan)


if __name__ == "__main__":
    unittest.main()
