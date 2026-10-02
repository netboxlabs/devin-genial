"""The Validation sidecar must derive premise-matched policies and an honest prediction."""

import copy
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from estates.generate import generate
from estates.model import canonical
from estates.validation import (EXCLUDED, _Graph, LoadError, ValidationError, build, check, compare, create,
                                seed, unseed, verify)

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("bank", "enterprise-dc", "school-district", "hospital-clinics", "provider-backbone",
            "retail-chain", "university-campus", "msp", "manufacturing", "utility")


def _generate(profile):
    return generate(tomllib.loads((ROOT / f"profiles/{profile}.toml").read_text()))


def _rules(artifact, policy_suffix, check_name):
    policy = next(p for p in artifact["policies"] if p["name"].endswith(policy_suffix))
    return next(r for r in policy["rules"] if r["check_name"] == check_name)


class EveryProfile(unittest.TestCase):
    def test_policies_are_premise_matched_for_every_profile(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan = _generate(profile)
                artifact = create(plan)
                self.assertEqual(canonical(artifact), canonical(create(copy.deepcopy(plan))))
                verify(artifact, plan)
                slugs = {o["attrs"].get("slug") for o in plan["objects"]}
                for policy in artifact["policies"]:
                    checks = [r["check_name"] for r in policy["rules"]]
                    # 1.14.1 files a check's results under its first rule.
                    self.assertEqual(len(checks), len(set(checks)), policy["name"])
                    self.assertTrue(set(checks).isdisjoint(EXCLUDED))
                    for slug in policy["site_groups"] + policy["roles"] + policy["platforms"]:
                        self.assertIn(slug, slugs)
                    for rule in policy["rules"]:
                        if rule["engine"] == "graph":
                            self.assertTrue(policy["graph"])
                            # The graph engine ignores rule roles; PDUs stay out by policy scope.
                            self.assertEqual(rule["roles"], [])
                            self.assertFalse(any("pdu" in role for role in policy["roles"]))
                        for slug in rule["roles"] + rule["platforms"]:
                            self.assertIn(slug, slugs)


class Provider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = _generate("provider-backbone")
        cls.artifact = create(cls.plan)

    def test_derived_parameters_follow_the_plan(self):
        vlan = _rules(self.artifact, "Customer premises standards", "vlan_id_range_by_site")
        vids = {o["attrs"]["vid"] for o in self.plan["objects"] if o["kind"] == "vlan"
                and "/ce-" in o["key"]}
        self.assertEqual((vlan["parameters"]["min_vid"], vlan["parameters"]["max_vid"]), (min(vids), max(vids)))
        p2p = _rules(self.artifact, "Estate baseline", "point_to_point_subnet_sizing")
        self.assertEqual(p2p["parameters"]["allowed_masks"], [31])
        keys = _rules(self.artifact, "Estate baseline", "config_context_required_keys")["parameters"]
        self.assertIn("service_endpoints.dns", keys["required_keys"])
        naming = _rules(self.artifact, "Juniper Junos naming", "interface_naming_consistent")
        self.assertRegex("xe-0/1/0", naming["parameters"]["pattern_by_type"]["10gbase-x-sfpp"])
        self.assertNotRegex("Ethernet1", naming["parameters"]["pattern_by_type"]["10gbase-x-sfpp"])

    def test_premises_are_scored_on_what_their_design_can_satisfy(self):
        base = _rules(self.artifact, "Customer premises resilience", "site_connectivity_redundancy")
        self.assertEqual(base["parameters"], {"min_circuits": 1, "min_providers": 1})
        self.assertEqual(base["expected"], [])
        hubs = next(p for p in self.artifact["policies"] if p["name"].endswith("dual-homed resilience"))
        designed = {o["attrs"]["slug"] for o in self.plan["objects"] if o["kind"] == "site"
                    and "tag/dual-homed" in o["refs"].get("tags", [])
                    and o["refs"].get("group", "").endswith("/customer")}
        self.assertLessEqual(designed, set(hubs["sites"]))
        diversity = next(r for r in hubs["rules"] if r["check_name"] == "circuit_path_diversity")
        # Two circuits into one CE: the CE is the remaining single point.
        self.assertTrue(diversity["expected"])
        self.assertTrue(all(e["cause"] == "all circuits on one device" for e in diversity["expected"]))

    def test_a_hub_losing_an_active_circuit_is_predicted(self):
        plan = copy.deepcopy(self.plan)
        hub = next(p for p in self.artifact["policies"] if p["name"].endswith("dual-homed resilience"))
        site = next(o for o in plan["objects"] if o["kind"] == "site" and o["attrs"]["slug"] == hub["sites"][0])
        circuit, _ = _Graph(plan).site_circuits(site["key"])[0]
        circuit["attrs"]["status"] = "deprovisioning"
        rule = _rules(create(plan), "dual-homed resilience", "site_connectivity_redundancy")
        self.assertEqual([e["subject"] for e in rule["expected"]], [site["attrs"]["name"]])

    def test_uncabling_a_supply_is_predicted(self):
        plan = copy.deepcopy(self.plan)
        cable = next(o for o in plan["objects"] if o["kind"] == "cable" and "/pe-a/power/" in o["refs"]["b"]
                     and o["refs"]["b"].startswith("device/pop-"))
        plan["objects"].remove(cable)
        device = next(o for o in plan["objects"] if o["key"] == cable["refs"]["b"].split("/power/")[0])
        artifact = create(plan)
        name = device["attrs"]["name"]
        redundant = _rules(artifact, "Provider PoPs standards", "redundant_power")
        self.assertEqual([e["subject"] for e in redundant["expected"]], [name])
        feeds = _rules(artifact, "Provider PoPs resilience", "power_feed_blast_radius")["expected"]
        self.assertEqual(len(feeds), 1)
        self.assertTrue(feeds[0]["subject"].endswith(f": {name}"))
        with self.assertRaisesRegex(ValidationError, "not bound"):
            verify(self.artifact, plan)


class Artifact(unittest.TestCase):
    def test_build_check_and_tamper(self):
        plan = _generate("enterprise-dc")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            build(root / "plan.json", root / "out")
            check(root / "out", root / "plan.json")
            artifact = json.loads((root / "out/validation.json").read_text())
            artifact["policies"][0]["rules"][0]["severity"] = "info"
            (root / "out/validation.json").write_bytes(canonical(artifact) + b"\n")
            with self.assertRaisesRegex(ValidationError, "recomputed"):
                check(root / "out", root / "plan.json")

    def test_a_repeated_check_in_one_policy_is_refused(self):
        artifact = create(_generate("enterprise-dc"))
        policy = artifact["policies"][0]
        policy["rules"].append(dict(policy["rules"][0], name="Duplicate"))
        with self.assertRaisesRegex(ValidationError, "repeats"):
            verify(artifact, {})


class Readback(unittest.TestCase):
    def test_compare_maps_device_feed_and_site_subjects(self):
        rule = {"check_name": "power_feed_blast_radius",
                "expected": [{"subject": "Network 01: a-gw01, a-sw01", "cause": "x"},
                             {"subject": "Network 01: b-gw01", "cause": "y"}]}
        results = [
            {"status": {"value": "fail"}, "device_name": None, "message": "m",
             "extra": {"failure_point": "Network 01", "unprotected_devices": [{"name": "a-sw01"}, {"name": "a-gw01"}]}},
            {"status": {"value": "fail"}, "device_name": None, "message": "m",
             "extra": {"failure_point": "Site X"}},
            {"status": {"value": "pass"}, "device_name": "c-gw01", "message": "ok", "extra": {}},
        ]
        row = compare(rule, results)
        self.assertEqual((row["failing"], row["predicted"]), (2, 2))
        self.assertEqual(row["unpredicted"], ["Site X"])
        self.assertEqual(row["missing"], ["Network 01: b-gw01"])
        self.assertEqual(row["results"], {"fail": 2, "pass": 1})

    def test_writes_require_the_environment_gate(self):
        with patch.dict(os.environ, {"VALIDATION_WRITES": ""}):
            with self.assertRaisesRegex(LoadError, "VALIDATION_WRITES=1"):
                seed("unused", url="https://example.invalid", token="x", receipt_path=Path("unused"))
            with self.assertRaisesRegex(LoadError, "VALIDATION_WRITES=1"):
                unseed("unused", url="https://example.invalid", token="x")

    def test_unseed_refuses_another_targets_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "r.json"
            receipt.write_text(json.dumps({"target": "https://other.invalid", "policies": {}}))
            with patch.dict(os.environ, {"VALIDATION_WRITES": "1"}):
                with self.assertRaisesRegex(LoadError, "belongs to"):
                    unseed(receipt, url="https://example.invalid", token="x")


if __name__ == "__main__":
    unittest.main()
