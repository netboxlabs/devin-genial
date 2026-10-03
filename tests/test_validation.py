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
        # A hub's two circuits land on two NIDs (0.17), so the device-diversity
        # check holds; the CE behind them stays a single point the site
        # contract states rather than a finding this check predicts.
        self.assertEqual(diversity["expected"], [])

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
        redundant = _rules(artifact, "Carrier PoPs standards", "redundant_power")
        self.assertEqual([e["subject"] for e in redundant["expected"]], [name])
        # The single-supply time servers are already findings; the move adds exactly one.
        before = _rules(self.artifact, "Carrier PoPs resilience", "power_feed_blast_radius")["expected"]
        feeds = [e for e in _rules(artifact, "Carrier PoPs resilience", "power_feed_blast_radius")["expected"]
                 if e not in before]
        self.assertEqual(len(feeds), 1)
        self.assertTrue(feeds[0]["subject"].endswith(f": {name}"))
        with self.assertRaisesRegex(ValidationError, "not bound"):
            verify(self.artifact, plan)

    def test_mismatched_cable_ends_are_predicted_on_both_devices(self):
        rule = _rules(self.artifact, "Estate baseline", "symmetric_cabling")
        before = {e["subject"] for e in rule["expected"]}
        plan = copy.deepcopy(self.plan)
        g = _Graph(plan)
        pair = next((i, g.objects[g.peer[i["key"]]]) for i in g.kinds["interface"]
                    if g.objects.get(g.peer.get(i["key"]), {}).get("kind") == "interface"
                    and g.objects[g.peer[i["key"]]]["attrs"].get("type") == i["attrs"].get("type")
                    and not {g.objects[i["refs"]["device"]]["attrs"]["name"],
                             g.objects[g.objects[g.peer[i["key"]]]["refs"]["device"]]["attrs"]["name"]} & before
                    and all(g.objects[x["refs"]["device"]]["attrs"].get("status", "active") == "active"
                            for x in (i, g.objects[g.peer[i["key"]]])))
        pair[0]["attrs"]["type"] = "25gbase-x-sfp28"
        after = {e["subject"] for e in _rules(create(plan), "Estate baseline", "symmetric_cabling")["expected"]}
        self.assertEqual(after - before, {g.objects[x["refs"]["device"]]["attrs"]["name"] for x in pair})

    def test_panel_terminated_circuits_are_omitted_with_their_reason(self):
        pops = next(p for p in self.artifact["policies"] if p["name"].endswith("Carrier PoPs resilience"))
        self.assertEqual({o["check_name"] for o in pops["omitted"]},
                         {"site_connectivity_redundancy", "circuit_path_diversity"})
        self.assertIn("patch-panel rear ports", pops["omitted"][0]["reason"])
        # Re-cable one PoP termination straight onto a PE port: the engine now
        # sees that circuit, so the rules return and every other PoP reads 0.
        plan = copy.deepcopy(self.plan)
        g = _Graph(plan)
        term, end = next((t, g.objects[g.peer[t["key"]]]) for t in g.kinds["circuit_termination"]
                         if g.peer.get(t["key"], "").startswith("device/pop-")
                         and g.objects[t["refs"]["circuit"]]["attrs"].get("status", "active") == "active")
        self.assertEqual(end["kind"], "rear_port")
        cable = next(c for c in g.kinds["cable"] if end["key"] in (c["refs"]["a"], c["refs"]["b"]))
        side = "a" if cable["refs"]["a"] == end["key"] else "b"
        panel = g.objects[end["refs"]["device"]]
        port = next(i for i in g.kinds["interface"] if i["refs"]["device"].startswith("device/pop-")
                    and g.objects[i["refs"]["device"]]["refs"]["site"] == panel["refs"]["site"]
                    and "/pe-a" in i["refs"]["device"] and i["key"] not in g.peer
                    and g.objects[i["refs"]["device"]]["attrs"].get("status", "active") == "active")
        cable["refs"][side] = port["key"]
        mutated = next(p for p in create(plan)["policies"] if p["name"].endswith("Carrier PoPs resilience"))
        self.assertEqual(mutated["omitted"], [])
        rule = next(r for r in mutated["rules"] if r["check_name"] == "site_connectivity_redundancy")
        pop_sites = {g.objects[d["refs"]["site"]]["attrs"]["name"] for d in g.active
                     if d["key"].startswith("device/pop-")}
        self.assertEqual({e["subject"] for e in rule["expected"]}, pop_sites)

    def test_an_unassigned_address_fails_every_holder_of_its_routing_table(self):
        plan = copy.deepcopy(self.plan)
        ip = next(o for o in plan["objects"] if o["kind"] == "ip_address" and "vrf" not in o["refs"]
                  and o["refs"].get("assigned_object"))
        before = {e["subject"] for e in _rules(create(plan), "Estate baseline", "no_orphan_ips")["expected"]}
        del ip["refs"]["assigned_object"]
        after = {e["subject"] for e in _rules(create(plan), "Estate baseline", "no_orphan_ips")["expected"]}
        self.assertGreater(len(after), len(before))

    def test_only_active_devices_are_subjects(self):
        rule = _rules(self.artifact, "Estate baseline", "symmetric_cabling")
        name = next(e["subject"] for e in rule["expected"] if not e["subject"].startswith("R0"))
        plan = copy.deepcopy(self.plan)
        next(o for o in plan["objects"] if o["kind"] == "device" and o["attrs"]["name"] == name)["attrs"]["status"] = "staged"
        after = _rules(create(plan), "Estate baseline", "symmetric_cabling")["expected"]
        self.assertNotIn(name, {e["subject"] for e in after})

    def test_a_cross_cabinet_cut_cable_is_predicted(self):
        rule = _rules(self.artifact, "Carrier PoPs resilience", "cable_single_point_of_failure")
        self.assertTrue(rule["expected"])
        # Every predicted cut is a management uplink to the far cabinet's PDU;
        # disabling that PDU (not active) takes the cut out of the engine's graph.
        plan = copy.deepcopy(self.plan)
        far = rule["expected"][0]["cause"].split(" the only cabled path to ")[1].split(" in the other")[0]
        pop = rule["expected"][0]["subject"].rsplit("-", 1)[0]
        pdu = next(o for o in plan["objects"] if o["kind"] == "device" and o["attrs"]["name"] == far
                   and o["key"].startswith(f"device/pop-{pop}"))
        pdu["attrs"]["status"] = "offline"
        after = _rules(create(plan), "Carrier PoPs resilience", "cable_single_point_of_failure")["expected"]
        self.assertEqual(len(after), len(rule["expected"]) - 1)


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

    def test_an_omitted_check_cannot_also_be_installed(self):
        artifact = create(_generate("enterprise-dc"))
        policy = artifact["policies"][0]
        policy["omitted"] = [{"check_name": policy["rules"][0]["check_name"], "reason": "x"}]
        with self.assertRaisesRegex(ValidationError, "omits"):
            verify(artifact, {})
        policy["omitted"] = [{"check_name": "site_connectivity_redundancy", "reason": ""}]
        with self.assertRaisesRegex(ValidationError, "omits"):
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
