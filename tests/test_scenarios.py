"""Acquisition and refresh checks against actual generated graph differences."""

from ipaddress import ip_interface
import unittest
from unittest.mock import patch

from estates.bank import generate
from estates.model import DesignError, canonical
from estates.scenarios import create, markdown, _scopes
from estates.validate import validate


class AcquisitionRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 0, "branches": {"small": 2},
                                 "site_designs": {"br-s0001": "inherited"}})
        cls.scenario = create(cls.baseline, "br-s0001")

    def test_deterministic_snapshots_and_explicit_changes(self):
        original = canonical(self.baseline)
        self.assertEqual(canonical(create(self.baseline, "br-s0001")), canonical(self.scenario))
        self.assertEqual(canonical(self.baseline), original)
        for snapshot in self.scenario["plans"].values():
            self.assertEqual(validate(snapshot), [])
        acquire, refresh = (self.scenario["changes"][stage] for stage in ("acquire", "refresh"))
        self.assertFalse(acquire["create"])
        self.assertFalse(acquire["delete"])
        self.assertTrue(acquire["tenant_changes"])
        self.assertTrue(any(change["key"].startswith("vrf/inherited/br-s0001/")
                            for change in acquire["tenant_changes"]))
        self.assertTrue(self.scenario["checks"]["only_selected_site_changes"])
        self.assertTrue(acquire["requires_target_reconciliation"])
        self.assertTrue(refresh["create"])
        self.assertTrue(refresh["delete"])
        self.assertFalse(refresh["replay_deletes_objects"])
        old = {obj["key"] for obj in refresh["delete"] if obj["kind"] == "device" and obj["refs"].get("role") == "role/access"}
        new = {obj["key"] for obj in refresh["create"] if obj["kind"] == "device" and obj["refs"].get("role") == "role/access"}
        self.assertTrue(old and new)
        self.assertTrue(old.isdisjoint(new))

    def test_answers_derive_impact_uplinks_and_supply_paths(self):
        answers = {answer["id"]: answer["answer"] for answer in self.scenario["answers"]}
        inventory = answers["inherited-equipment"]
        impacted = [endpoint for record in inventory.values() for endpoint in record["affected_endpoints"]]
        self.assertEqual(len(impacted), len(set(impacted)))
        self.assertEqual(set(impacted), set(answers["preserved-addressing"]["names"]))
        for record in inventory.values():
            self.assertEqual(len({path["upstream"] for path in record["uplinks"]}), 1)
            self.assertEqual(len(record["peer_trunks"]), 1)
            self.assertEqual(len({path["panel"] for path in record["supply_paths"]}), 1)
        for record in answers["refreshed-resilience"].values():
            self.assertEqual(len({path["upstream"] for path in record["uplinks"]}), 2)
            self.assertEqual(len(record["peer_trunks"]), 1)
            self.assertEqual(len({path["panel"] for path in record["supply_paths"]}), 2)
            self.assertTrue(all(path["cables"] for path in record["supply_paths"]))
        for stage in ("before", "acquired", "refreshed"):
            objects = {obj["key"]: obj for obj in self.scenario["plans"][stage]["objects"]}
            for endpoint, addresses in answers["preserved-addressing"]["addresses"].items():
                self.assertEqual(objects[endpoint]["attrs"]["name"], answers["preserved-addressing"]["names"][endpoint])
                for address in addresses:
                    actual = objects[address["key"]]
                    self.assertEqual(actual["attrs"]["address"], address["address"])
                    self.assertEqual(actual["attrs"]["dns_name"], address["dns_name"])
        self.assertGreater(self.scenario["checks"]["foreign_objects_checked"], 0)

    def test_acquisition_updates_local_responsibilities_without_changing_shared_desks(self):
        before = {obj["key"]: obj for obj in self.scenario["plans"]["before"]["objects"]}
        after = {obj["key"]: obj for obj in self.scenario["plans"]["acquired"]["objects"]}
        scopes = _scopes(before)
        changes = [change for change in self.scenario["changes"]["acquire"]["update"]
                   if change["kind"] == "contact_assignment"]
        expected = {key for key, obj in before.items() if obj["kind"] == "contact_assignment"
                    and obj["refs"]["role"] == "contact-role/operations"
                    and scopes[key] == frozenset({"site/br-s0001"})}
        self.assertEqual({change["key"] for change in changes}, expected)
        self.assertGreater(len(changes), 3)  # Equipment as well as site/circuits.
        for change in changes:
            self.assertEqual(scopes[change["key"]], frozenset({"site/br-s0001"}))
            self.assertEqual(change["before"]["refs"]["contact"], "contact/operations/tenant/inherited")
            self.assertEqual(change["after"]["refs"]["contact"], "contact/operations")
        for key, obj in before.items():
            if obj["kind"] in {"contact", "contact_group", "contact_role", "journal_entry"}:
                self.assertEqual(after[key], obj)
            if obj["kind"] == "contact":
                self.assertFalse(scopes[key])

    def test_walkthrough_distinguishes_snapshot_deletes_from_ingestion(self):
        text = markdown(self.scenario)
        self.assertIn("EX3300-24P", text)
        self.assertIn("asr01", text)
        self.assertIn("Diode replay does not delete", text)
        self.assertIn("matching identity can change", text)

    def test_rejects_ineligible_absent_or_already_acquired_sites(self):
        for source, site in ((self.baseline, "absent"), (self.baseline, "br-s0002"),
                             (self.scenario["plans"]["acquired"], "br-s0001"),
                             (self.scenario["plans"]["refreshed"], "br-s0001")):
            with self.subTest(site=site):
                with self.assertRaises(DesignError):
                    create(source, site)

    def test_endpoint_address_drift_is_detected_even_when_plan_is_locally_valid(self):
        def drift(recipe, previous=None, transition=None):
            result = generate(recipe, previous, transition)
            if transition["operation"] == "refresh":
                endpoint = next(obj for obj in result["objects"] if obj["kind"] == "device" and
                                obj["refs"].get("site") == "site/br-s0001" and obj["meta"].get("endpoint"))
                address = next(obj for obj in result["objects"] if obj["key"] == endpoint["refs"]["primary_ip4"])
                network = ip_interface(address["attrs"]["address"]).network
                address["attrs"]["address"] = f"{network[-2]}/{network.prefixlen}"
                self.assertEqual(validate(result), [], "Mutation should pass standalone graph validation")
            return result

        with patch("estates.scenarios.generate", side_effect=drift):
            with self.assertRaisesRegex(DesignError, "endpoint-address-stability"):
                create(self.baseline, "br-s0001")

    def test_changes_to_other_sites_are_detected(self):
        def drift(recipe, previous=None, transition=None):
            result = generate(recipe, previous, transition)
            if transition["operation"] == "refresh":
                site = next(obj for obj in result["objects"] if obj["key"] == "site/br-s0002")
                site["attrs"]["description"] = "Unrelated site edited by a broken transition"
                self.assertEqual(validate(result), [])
            return result

        with patch("estates.scenarios.generate", side_effect=drift):
            with self.assertRaisesRegex(DesignError, "unrelated-sites-unchanged"):
                create(self.baseline, "br-s0001")

    def test_passive_panel_mode_traces_through_panel_and_outlet(self):
        before = generate(self.baseline["recipe"] | {"patching": "panels"})
        scenario = create(before, "br-s0001")
        impact = next(answer for answer in scenario["answers"] if answer["id"] == "replacement-impact")
        self.assertTrue(impact["evidence"])
        for paths in impact["evidence"].values():
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(paths[0]["cables"]), 3)

    def test_shared_definitions_cannot_be_edited_added_or_removed(self):
        for operation in ("update", "create", "delete"):
            with self.subTest(operation=operation):
                def drift(recipe, previous=None, transition=None):
                    result = generate(recipe, previous, transition)
                    if transition["operation"] == "refresh":
                        if operation == "update":
                            role = next(obj for obj in result["objects"] if obj["key"] == "role/access")
                            role["attrs"]["description"] = "Unrelated shared annotation"
                        elif operation == "create":
                            result["objects"].append({"key": "role/unrelated", "kind": "device_role",
                                                      "attrs": {"name": "Unrelated", "slug": "unrelated"},
                                                      "refs": {}, "meta": {}})
                            result["objects"].sort(key=lambda obj: obj["key"])
                        else:
                            result["objects"] = [obj for obj in result["objects"] if obj["key"] != "role/patch-panel"]
                        self.assertEqual(validate(result), [], "Mutation must pass standalone validation")
                    return result

                with patch("estates.scenarios.generate", side_effect=drift):
                    with self.assertRaisesRegex(DesignError, "unrelated-sites-unchanged"):
                        create(self.baseline, "br-s0001")

    def test_foreign_vrf_ownership_is_inferred_from_consumers(self):
        before = generate(self.baseline["recipe"] | {
            "site_designs": {"br-s0001": "inherited", "br-s0002": "inherited"}})

        def drift(recipe, previous=None, transition=None):
            result = generate(recipe, previous, transition)
            if transition["operation"] == "refresh":
                vrf = next(obj for obj in result["objects"] if obj["key"] == "vrf/inherited/br-s0002/users")
                vrf["attrs"]["description"] = "Unrelated branch VRF edited"
                self.assertEqual(validate(result), [])
            return result

        with patch("estates.scenarios.generate", side_effect=drift):
            with self.assertRaisesRegex(DesignError, "unrelated-sites-unchanged.*vrf/inherited/br-s0002/users"):
                create(before, "br-s0001")


if __name__ == "__main__":
    unittest.main()
