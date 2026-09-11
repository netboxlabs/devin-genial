"""The representative profile must exercise the whole pinned estate surface."""

import unittest

from estates.bank import generate
from estates.model import ROOT, recipe_from_file
from estates.report import type_coverage


class CoverageTests(unittest.TestCase):
    def test_complete_profile_and_explicit_external_user(self):
        plan = generate(recipe_from_file(ROOT / "profiles/bank-depth.toml"))
        coverage = type_coverage(plan)
        self.assertEqual(coverage["candidate_types"], 101)
        self.assertEqual(coverage["generated_candidate_types"], 101)
        self.assertEqual(coverage["missing_candidate_types"], [])
        users = [obj for obj in plan["objects"] if obj["kind"] == "user"]
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0]["meta"]["external"])
        self.assertEqual(set(users[0]["attrs"]), {"username"})
        self.assertFalse(coverage["live_verified"])
        # Coverage must form one estate, even without its shared generated tag.
        # Definition dependencies connect custom-field values to their schema.
        links = {obj["key"]: set() for obj in plan["objects"] if obj["kind"] != "tag"}
        for obj in plan["objects"]:
            for value in list(obj["refs"].values()) + [obj["meta"].get("requires", [])]:
                for target in value if isinstance(value, list) else [value]:
                    if obj["key"] in links and target in links:
                        links[obj["key"]].add(target)
                        links[target].add(obj["key"])
        seen, pending = {"site/dc-01"}, ["site/dc-01"]
        while pending:
            for target in links[pending.pop()] - seen:
                seen.add(target)
                pending.append(target)
        self.assertEqual(seen, set(links))
        try:
            from netboxlabs.diode.sdk.diode.v1.ingester_pb2 import Entity
        except ImportError:
            return
        self.assertEqual({item["kind"] for item in coverage["types"]},
                         {field.name for field in Entity.DESCRIPTOR.fields if field.name != "timestamp"})

    def test_optional_binding_and_abstracted_passive_paths_are_visible_gaps(self):
        coverage = type_coverage(generate({"headquarters": 0, "branches": {"small": 1}}))
        self.assertEqual(set(coverage["missing_candidate_types"]),
                         {"front_port", "rear_port", "rack_reservation"})


if __name__ == "__main__":
    unittest.main()
