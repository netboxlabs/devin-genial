"""PoE counterexamples over generated hardware and actual power/copper paths."""

from copy import deepcopy
from decimal import Decimal
import unittest

from estates.generate import generate
from estates.model import hardware_catalog
from estates.validate_poe import analyze


class PoEValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = hardware_catalog()
        cls.direct = generate({"headquarters": 0, "branches": {"small": 1}})
        cls.panels = generate({"headquarters": 0, "branches": {"small": 1}, "patching": "panels"})
        cls.inherited = generate({"headquarters": 0, "branches": {"small": 1},
                                  "site_designs": {"br-s0001": "inherited"}})

    def strip(self, plan):
        plan = deepcopy(plan)
        plan.pop("contracts", None)
        for obj in plan["objects"]:
            obj.pop("meta", None)
        return plan, {o["key"]: o for o in plan["objects"]}

    def codes(self, plan, catalog=None):
        return {f["code"] for f in analyze(plan, catalog or self.catalog)[0]}

    def power_fixture(self, count, inherited=False):
        """Keep generated hardware/power, replacing just PDs and their cabling.

        This fixture tests PoE independently; unrelated IP/service rows are not
        rebuilt or submitted to the full estate validator.
        """
        plan, objects = self.strip(self.inherited if inherited else self.direct)
        ap = objects["device/br-s0001/ap-001"]
        aps = {o["key"] for o in objects.values() if o["kind"] == "device" and o["refs"].get("device_type") == "hardware/ap"}
        removed = aps | {o["key"] for o in objects.values() if o["refs"].get("device") in aps}
        alias = "inherited-access" if inherited else "access"
        pse = next(o for o in objects.values() if o["kind"] == "device" and o["refs"].get("site") == "site/br-s0001"
                   and o["refs"].get("device_type") == f"hardware/{alias}")
        ports = self.catalog["models"][alias]["access_ports"]
        free = {o["key"] for o in objects.values() if o["kind"] == "interface" and
                o["refs"].get("device") == pse["key"] and o["attrs"].get("name") in ports}
        plan["objects"] = [o for o in plan["objects"] if o["key"] not in removed and not
                           (o["kind"] == "cable" and set(o["refs"].values()) & (removed | free))]
        input_attrs = next(p for p in self.catalog["models"]["ap"]["interfaces"] if p["name"] == "eth0")
        for n in range(count):
            key = f"fixture/ap-{n + 1}"
            new = deepcopy(ap)
            new["key"] = key
            new["attrs"]["name"] = f"ap-{n + 1}"
            new["refs"].pop("primary_ip4", None)
            new["refs"].pop("primary_ip6", None)
            plan["objects"].extend([new,
                dict(key=f"{key}/eth0", kind="interface", attrs=dict(input_attrs, enabled=True), refs={"device": key}),
                dict(key=f"fixture/cable-{n + 1}", kind="cable",
                     attrs=dict(type="cat6", status="connected", length=3, length_unit="m"),
                     refs={"a": f"{key}/eth0", "b": f"{pse['key']}/if/{ports[n]}"})])
        return plan, {o["key"]: o for o in plan["objects"]}, pse["key"]

    def test_all_five_profiles_use_actual_hardware_without_contracts_or_metadata(self):
        plans = [self.direct, self.panels, self.inherited] + [generate({"profile": p}) for p in (
            "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone")]
        for original in plans:
            with self.subTest(profile=original["recipe"]["profile"], patching=original["recipe"]["patching"]):
                plan, objects = self.strip(original)
                before = deepcopy(plan)
                findings, extra = analyze({"objects": plan["objects"]}, self.catalog["models"])
                self.assertEqual(findings, [])
                self.assertEqual(plan, before)
                self.assertTrue(all(type(watts) is int and watts >= 0 for watts in extra.values()))
                count = sum(o["kind"] == "device" and o["refs"].get("device_type") == "hardware/ap" for o in objects.values())
                self.assertGreaterEqual(sum(extra.values()), Decimal(count) * Decimal("37.5"))
                self.assertLessEqual(sum(extra.values()), Decimal(count) * Decimal("37.5") + sum(v > 0 for v in extra.values()))

    def test_cisco_pair_policy_and_exact_upstream_rounding(self):
        for count, watts, deficit in ((1, 38, False), (12, 450, False), (13, 488, True)):
            with self.subTest(count=count):
                plan, _, pse = self.power_fixture(count)
                findings, extra = analyze(plan, self.catalog)
                self.assertEqual(extra[pse], watts)
                self.assertEqual({f["code"] for f in findings}, {"poe-redundancy"} if deficit else set())

    def test_one_remaining_cisco_supply_can_deliver_without_meeting_redundancy(self):
        for count, overloaded in ((12, False), (13, True)):
            plan, objects, pse = self.power_fixture(count)
            module = next(o for o in objects.values() if o["kind"] == "module" and o["refs"].get("device") == pse)
            module["attrs"]["status"] = "offline"
            findings, _ = analyze(plan, self.catalog)
            codes = {f["code"] for f in findings}
            self.assertIn("poe-supply", codes)
            self.assertIn("poe-redundancy", codes)
            self.assertEqual("poe-budget" in codes, overloaded)
            self.assertTrue(all("not by itself a current PD outage" in f["message"] for f in findings if f["code"] == "poe-redundancy"))

    def test_fixed_juniper_budget_is_not_its_supply_rating_and_has_no_fake_redundancy(self):
        for count, overloaded in ((13, False), (14, True)):
            plan, _, pse = self.power_fixture(count, inherited=True)
            findings, extra = analyze(plan, self.catalog)
            self.assertEqual({f["code"] for f in findings}, {"poe-budget"} if overloaded else set())
            self.assertEqual(extra[pse], 488 if count == 13 else 525)
        plan, objects, pse = self.power_fixture(1, inherited=True)
        inlet = f"{pse}/power/PSU0"
        plan["objects"] = [o for o in plan["objects"] if not (o["kind"] == "cable" and inlet in o["refs"].values())]
        self.assertTrue({"poe-supply", "poe-budget"} <= self.codes(plan))
        self.assertNotIn("poe-redundancy", self.codes(plan))

    def test_ineligible_optical_radio_virtual_management_and_vm_interfaces_reject_poe(self):
        selectors = (lambda o: o["kind"] == "interface" and o["attrs"].get("mgmt_only"),
                     lambda o: o["kind"] == "interface" and "sfpp" in o["attrs"].get("type", ""),
                     lambda o: o["kind"] == "interface" and o["attrs"].get("name") == "wlan0",
                     lambda o: o["kind"] == "interface" and o["attrs"].get("type") == "virtual",
                     lambda o: o["kind"] == "vm_interface")
        for select in selectors:
            plan, objects = self.strip(self.direct)
            port = next(o for o in objects.values() if select(o))
            port["attrs"].update(poe_mode="pse", poe_type="type2-ieee802.3at")
            self.assertIn("poe-port", self.codes(plan), port["key"])

    def test_required_pd_and_pse_port_facts_cannot_be_omitted_or_retyped(self):
        for field, value in (("poe_mode", None), ("poe_type", "type1-ieee802.3af"),
                             ("enabled", False), ("type", "virtual"), ("name", "other")):
            plan, objects, pse = self.power_fixture(1)
            objects["fixture/ap-1/eth0"]["attrs"][field] = value
            self.assertIn("poe-port", self.codes(plan))
        plan, objects, pse = self.power_fixture(1)
        port = objects[f"{pse}/if/{self.catalog['models']['access']['access_ports'][0]}"]
        port["attrs"].pop("poe_mode")
        self.assertIn("poe-port", self.codes(plan))
        plan["objects"].remove(objects["fixture/ap-1/eth0"])
        self.assertIn("poe-port", self.codes(plan))

    def test_bad_connected_copper_and_passive_paths_fail(self):
        for field, value in (("status", "planned"), ("type", "smf"), ("type", []), ("length", 81),
                             ("length", 10 ** 1000), ("length", float("nan")), ("length_unit", "unknown")):
            plan, objects, _ = self.power_fixture(1)
            objects["fixture/cable-1"]["attrs"][field] = value
            self.assertIn("poe-path", self.codes(plan))
        for mutation in ("duplicate-cable", "bad-rear", "multiple-positions", "wrong-media"):
            plan, objects = self.strip(self.panels)
            port = "device/br-s0001/ap-001/if/eth0"
            cable = next(o for o in objects.values() if o["kind"] == "cable" and port in o["refs"].values())
            front = objects[next(v for v in cable["refs"].values() if v != port)]
            rear = objects[front["refs"]["rear_port"]]
            if mutation == "duplicate-cable":
                duplicate = deepcopy(cable); duplicate["key"] = "duplicate"
                plan["objects"].append(duplicate)
            elif mutation == "bad-rear": front["refs"]["rear_port"] = "missing"
            elif mutation == "multiple-positions": rear["attrs"]["positions"] = 2
            else: rear["attrs"]["type"] = "lc"
            self.assertIn("poe-path", self.codes(plan), mutation)

    def test_actual_supply_model_fit_binding_and_feed_availability_control_capacity(self):
        for mutation in ("module-type", "manufacturer", "bay-kind", "bay-enabled", "bay-position",
                         "bay-fit", "module-owner", "port-binding", "missing-module", "feed-offline", "feed-dc"):
            with self.subTest(mutation=mutation):
                plan, objects, pse = self.power_fixture(12)
                module = next(o for o in objects.values() if o["kind"] == "module" and o["refs"].get("device") == pse)
                bay = objects[module["refs"]["module_bay"]]
                port = next(o for o in objects.values() if o["kind"] == "power_port" and o["refs"].get("module") == module["key"])
                if mutation == "module-type": objects[module["refs"]["module_type"]]["attrs"]["model"] = "other"
                elif mutation == "manufacturer": objects[module["refs"]["module_type"]]["refs"]["manufacturer"] = "manufacturer/Juniper"
                elif mutation == "bay-kind": bay["kind"] = "tag"
                elif mutation == "bay-enabled": bay["attrs"]["enabled"] = False
                elif mutation == "bay-position": bay["attrs"]["position"] = "wrong"
                elif mutation == "bay-fit": bay["refs"]["module_bay_types"] = []
                elif mutation == "module-owner": module["refs"]["device"] = "other"
                elif mutation == "port-binding": port["refs"].pop("module")
                elif mutation == "missing-module": plan["objects"].remove(module)
                else:
                    cable = next(o for o in objects.values() if o["kind"] == "cable" and port["key"] in o["refs"].values())
                    outlet = objects[next(v for v in cable["refs"].values() if v != port["key"])]
                    inlet = outlet["refs"]["power_port"]
                    cable = next(o for o in objects.values() if o["kind"] == "cable" and inlet in o["refs"].values())
                    feed = objects[next(v for v in cable["refs"].values() if v != inlet)]
                    feed["attrs"]["status" if mutation == "feed-offline" else "supply"] = "offline" if mutation == "feed-offline" else "dc"
                self.assertIn("poe-supply", self.codes(plan))

    def test_same_panel_does_not_invent_a_current_delivery_outage(self):
        plan, objects, pse = self.power_fixture(12)
        rack = objects[pse]["refs"]["rack"]
        feeds = [o for o in objects.values() if o["kind"] == "power_feed" and o["refs"].get("rack") == rack]
        self.assertEqual(len(feeds), 2)
        feeds[1]["refs"]["power_panel"] = feeds[0]["refs"]["power_panel"]
        self.assertEqual(self.codes(plan), {"poe-feed-diversity"})

    def test_hardware_identity_and_finite_catalog_policy_are_not_metadata_claims(self):
        plan, objects, _ = self.power_fixture(1)
        objects["hardware/ap"]["attrs"]["model"] = "Unknown AP"
        self.assertIn("poe-hardware", self.codes(plan))
        for field, value in (("per_port_max_mw", True), ("per_port_max_mw", 30001),
                             ("upstream_ac_allowance_multiplier", "NaN"),
                             ("budget_by_active_supplies_mw", {"0": 0, "1": float("inf"), "2": 740000})):
            catalog = deepcopy(self.catalog)
            catalog["models"]["access"]["poe_pse"][field] = value
            plan, _, _ = self.power_fixture(1)
            self.assertIn("poe-catalog", self.codes(plan, catalog))
        catalog = deepcopy(self.catalog)
        catalog["models"]["access"]["poe_pse"]["per_port_max_mw"] = 15000
        plan, _, _ = self.power_fixture(1)
        self.assertIn("poe-port-budget", self.codes(plan, catalog))

    def test_legacy_generic_fixture_compatibility_never_substitutes_for_poe_hardware_proof(self):
        legacy = {"schema_version": 1, "recipe": {"reserve_fraction": .2}, "objects": [
            dict(key="d", kind="device", attrs={"name": "legacy"}, refs={}, meta={"hardware": "ap"}),
            dict(key="i", kind="interface", attrs={"name": "eth0", "type": "1000base-t"}, refs={"device": "d"})]}
        self.assertEqual(analyze(legacy, self.catalog), ([], {}))
        for mode in ("actual-type", "flags", "generator", "profile", "digest"):
            plan = deepcopy(legacy)
            if mode == "actual-type": plan["objects"][0]["refs"]["device_type"] = "hardware/ap"
            elif mode == "flags": plan["objects"][1]["attrs"].update(poe_mode="pd", poe_type="type2-ieee802.3at")
            elif mode == "generator": plan["generator_version"] = "0.9.0"
            elif mode == "profile": plan["recipe"]["profile"] = "regional-bank"
            else: plan["hardware_digest"] = "present"
            self.assertIn("poe-hardware", self.codes(plan), mode)
        plan, objects, _ = self.power_fixture(1)
        objects["fixture/ap-1"]["refs"].pop("device_type")
        objects["fixture/ap-1/eth0"]["attrs"].pop("poe_mode")
        objects["fixture/ap-1/eth0"]["attrs"].pop("poe_type")
        self.assertIn("poe-hardware", self.codes(plan))


if __name__ == "__main__":
    unittest.main()
