"""Independent procurement, physical handoff, capacity and retention checks."""

from copy import deepcopy
from datetime import date, timedelta
import unittest

from estates.bank import generate
from estates.model import DesignError, canonical
from estates.report import markdown
from estates.scenarios import create
from estates.validate import validate


class WanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 1, "branches": {"small": 3},
                                 "site_designs": {"br-s0002": "inherited", "br-s0003": "refreshed"}})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def cable_at(self, port):
        return next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and port in obj["refs"].values())

    def test_product_tiers_carrier_minima_and_retained_contract_floor(self):
        self.assertEqual(validate(self.plan), [])
        for site, a, b in (("br-s0001", 50, 100), ("br-s0002", 200, 200), ("br-s0003", 200, 200),
                           ("hq-01", 500, 500), ("dc-01", 1000, 1000), ("dc-02", 1000, 1000)):
            for carrier, expected in (("a", a), ("b", b)):
                circuit = self.objects[f"circuit/{site}/{carrier}/1"]
                self.assertEqual(circuit["attrs"]["commit_rate"], expected*1000)
                self.assertEqual(self.objects[circuit["key"]+"/A"]["attrs"]["port_speed"], 1000000)
                self.assertEqual(self.objects[circuit["key"]+"/Z"]["attrs"]["port_speed"], 1000000)
                installed, observed = date.fromisoformat(circuit["attrs"]["install_date"]), date.fromisoformat(self.plan["recipe"]["as_of"])
                self.assertLess(installed, observed)
                self.assertTrue(circuit["attrs"]["comments"].startswith("Procurement record: "))

    def test_custom_tiers_round_up_after_carrier_and_retained_minima(self):
        plan = generate(self.baseline["recipe"] | {"wan_tiers_mbps": [50, 250, 1000]})
        self.assertEqual(validate(plan), [])
        objects = {obj["key"]: obj for obj in plan["objects"]}
        self.assertEqual(objects["circuit/br-s0001/b/1"]["attrs"]["commit_rate"], 250000)
        self.assertEqual(objects["circuit/br-s0002/a/1"]["attrs"]["commit_rate"], 250000)

    def test_exact_decimal_tier_and_dc_pair_boundaries(self):
        for staff, reserve, expected in ((33, 0.34, 100), (170, 0.32, 500), (66, 0.34, 200), (165, 0.34, 500)):
            with self.subTest(staff=staff, reserve=reserve):
                plan = generate({"headquarters": 1, "headquarters_staff": staff, "branches": {}, "reserve_fraction": reserve})
                self.assertEqual(validate(plan), [])
                self.assertEqual(next(obj for obj in plan["objects"] if obj["key"] == "circuit/hq-01/a/1")["attrs"]["commit_rate"], expected*1000)
        plan = generate({"headquarters": 2, "headquarters_staff": 165, "branches": {}, "reserve_fraction": 0.34})
        self.assertEqual(validate(plan), [])
        for site in ("dc-01", "dc-02"):
            self.assertEqual(sum(obj["kind"] == "circuit" and obj["key"].startswith(f"circuit/{site}/") for obj in plan["objects"]), 2)

    def test_cir_cannot_be_lowered_by_weakening_contract_and_procurement_metadata(self):
        circuit = self.objects["circuit/hq-01/a/1"]
        circuit["attrs"]["commit_rate"] = 100000
        circuit["meta"]["procurement"].update(planned_peak_mbps=1, minimum_commit_mbps=1)
        contract = next(c for c in self.plan["contracts"] if c["site"] == "site/hq-01")
        contract["demand"]["peak_mbps"] = 1
        self.assertTrue({"wan-cir-policy", "wan-carrier-capacity"} <= self.codes())

    def test_retained_rate_is_derived_from_saved_design_not_circuit_story(self):
        circuit = self.objects["circuit/br-s0002/a/1"]
        circuit["attrs"]["commit_rate"] = 50000
        circuit["meta"]["procurement"] = {"cohort": "cedar-standard", "minimum_commit_mbps": 50}
        self.assertIn("wan-cir-policy", self.codes())

    def test_rate_must_be_smallest_permitted_tier_below_handoff_capacity(self):
        for rate, code in ((200000, "wan-cir-policy"), (75000, "wan-cir-tier"), (2000000, "wan-handoff-speed")):
            with self.subTest(rate=rate):
                self.objects["circuit/br-s0001/a/1"]["attrs"]["commit_rate"] = rate
                self.assertIn(code, self.codes())

    def test_both_terminations_and_edge_speed_are_checked(self):
        for key, field in (("circuit/br-s0001/a/1/A", "port_speed"), ("circuit/br-s0001/a/1/Z", "port_speed"),
                           ("device/br-s0001/edge-a/if/wan1", "speed")):
            with self.subTest(key=key):
                self.setUp()
                self.objects[key]["attrs"][field] = 10000
                self.assertTrue({"wan-handoff-speed", "wan-carrier-capacity"} <= self.codes())

    def test_malformed_bandwidth_returns_findings_without_arithmetic_errors(self):
        for key, field in (("circuit/br-s0001/a/1", "commit_rate"), ("circuit/br-s0001/a/1/Z", "port_speed"),
                           ("device/br-s0001/edge-a/if/wan1", "speed")):
            for value in ("bogus", [], -1, True):
                with self.subTest(key=key, value=value):
                    self.setUp()
                    self.objects[key]["attrs"][field] = value
                    self.assertIn("object-field", self.codes())
        for key, field, code in (("circuit/br-s0001/a/1", "commit_rate", "wan-cir-tier"),
                                 ("circuit/br-s0001/a/1/Z", "port_speed", "wan-handoff-speed")):
            with self.subTest(nullable_field=field):
                self.setUp()
                self.objects[key]["attrs"][field] = None
                self.assertIn(code, self.codes())

    def test_handoff_requires_enabled_wan1_on_an_active_wan_edge(self):
        for key, section, field, value in (("device/br-s0001/edge-a/if/wan1", "attrs", "enabled", False),
                                            ("device/br-s0001/edge-a/if/wan1", "attrs", "name", "wan2"),
                                            ("device/br-s0001/edge-a", "refs", "role", "role/access"),
                                            ("device/br-s0001/edge-a", "attrs", "status", "offline")):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.objects[key][section][field] = value
                self.assertTrue({"wan-handoff", "wan-carrier-capacity"} <= self.codes())

    def test_inactive_circuit_or_planned_cable_cannot_supply_capacity(self):
        self.objects["circuit/br-s0001/a/1"]["attrs"]["status"] = "planned"
        self.assertTrue({"wan-status", "wan-carrier-capacity"} <= self.codes())
        self.setUp()
        self.cable_at("circuit/br-s0001/a/1/A")["attrs"]["status"] = "planned"
        self.assertTrue({"wan-handoff", "wan-carrier-capacity"} <= self.codes())

    def test_provider_network_must_match_the_actual_circuit_provider(self):
        self.objects["circuit/br-s0001/a/1/Z"]["refs"]["termination"] = "carrier/b"
        self.assertTrue({"wan-provider", "wan-carrier-capacity"} <= self.codes())

    def test_missing_dc_pair_cannot_be_hidden_by_weakening_aggregate_contract(self):
        key = "circuit/dc-01/a/1"
        cable = self.cable_at(key+"/A")
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] not in {key, key+"/A", key+"/Z", cable["key"]}]
        contract = next(c for c in self.plan["contracts"] if c["site"] == "site/dc-01")
        contract.update(wan_pairs=0, wan_peak_mbps=0, wan_usable_mbps=0)
        contract["required_connections"] = [c for c in contract["required_connections"] if c.get("b") != key+"/A"]
        self.assertTrue({"wan-circuit-count", "wan-carrier-capacity"} <= self.codes())

    def test_extra_dc_pairs_fail_even_when_all_demand_is_covered(self):
        self.plan = generate({"headquarters": 2, "headquarters_staff": 165, "branches": {}, "reserve_fraction": 0.35})
        self.plan["recipe"]["reserve_fraction"] = 0.34
        self.assertIn("wan-circuit-count", self.codes())

    def test_dates_and_portable_provenance_cannot_be_missing_or_fabricated_as_future(self):
        circuit = self.objects["circuit/br-s0001/a/1"]
        for value in ("2026-02-30", (date.fromisoformat(self.plan["recipe"]["as_of"])+timedelta(days=1)).isoformat()):
            with self.subTest(value=value):
                circuit["attrs"]["install_date"] = value
                self.assertIn("wan-date", self.codes())
        circuit["attrs"]["comments"] = ""
        self.assertIn("wan-provenance", self.codes())

    def test_tier_input_is_validated_and_immutable_during_growth(self):
        for tiers in ([], [0, 1000], [50, 50, 1000], [1000, 50], [50, 999], [50.0, 1000], [True, 1000]):
            with self.subTest(tiers=tiers), self.assertRaises(DesignError):
                generate({"wan_tiers_mbps": tiers})
        with self.assertRaisesRegex(DesignError, "rebaseline"):
            generate(self.baseline["recipe"] | {"wan_tiers_mbps": [50, 250, 1000]}, previous=self.baseline)

    def test_growth_and_access_refresh_preserve_procured_wan(self):
        grown = generate(self.baseline["recipe"] | {"branches": {"small": 3, "large": 4}}, previous=self.baseline)
        self.assertEqual(validate(grown), [])
        objects = {obj["key"]: obj for obj in grown["objects"]}
        for obj in self.baseline["objects"]:
            if obj["kind"] == "circuit":
                self.assertEqual(objects[obj["key"]], obj)
        scenario = create(self.baseline, "br-s0002")
        for stage in ("acquired", "refreshed"):
            current = {obj["key"]: obj for obj in scenario["plans"][stage]["objects"]}
            for obj in self.baseline["objects"]:
                if obj["kind"] == "circuit" and obj["key"].startswith("circuit/br-s0002/"):
                    self.assertEqual(current[obj["key"]]["attrs"], obj["attrs"])
                    self.assertEqual(current[obj["key"]]["meta"], obj["meta"])
                    self.assertEqual(current[obj["key"]]["refs"], obj["refs"] | {"tenant": "tenant"})

    def report_row(self):
        text = markdown(self.plan).split("## WAN capacity by provider and site\n", 1)[1].split("## Active IP plan", 1)[0]
        provider = self.objects["provider/a"]["attrs"]["name"]
        site = self.objects["site/br-s0001"]["attrs"]["name"]
        return next([cell.strip() for cell in line.split("|")[1:-1]] for line in text.splitlines()
                    if line.startswith(f"| {provider} | {site} |"))

    def test_report_uses_actual_both_side_handoff_and_preserves_input(self):
        before = canonical(self.plan)
        text = markdown(self.plan)
        self.assertEqual(canonical(self.plan), before)
        circuit = self.objects["circuit/br-s0001/a/1"]["attrs"]
        for field in ("cid", "install_date", "comments"):
            self.assertIn(circuit[field], text)
        self.objects["circuit/br-s0001/a/1/Z"]["attrs"]["port_speed"] = 10000
        self.assertEqual(self.report_row()[3:], ["50", "10", "10", "8", "20", "-12"])
        for change in ("disabled", "removed", "planned"):
            with self.subTest(change=change):
                self.setUp()
                cable = self.cable_at("circuit/br-s0001/a/1/A")
                if change == "disabled":
                    self.objects["device/br-s0001/edge-a/if/wan1"]["attrs"]["enabled"] = False
                elif change == "removed":
                    self.plan["objects"].remove(cable)
                else:
                    cable["attrs"]["status"] = "planned"
                self.assertEqual(self.report_row()[3:], ["50", "0", "0", "0", "20", "-20"])


if __name__ == "__main__":
    unittest.main()
