"""Physical family links, native cooling semantics, and stable allocations."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.equipment import KINDS, validate


class EquipmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"branches": {"small": 1}, "headquarters": 0})

    def test_all_physical_families_are_linked_and_qualified(self):
        self.assertEqual(validate(self.plan), [])
        objects = {o["key"]: o for o in self.plan["objects"]}
        self.assertFalse(KINDS - {o["kind"] for o in objects.values()})
        referenced = {key for o in objects.values() for value in o["refs"].values()
                      for key in (value if isinstance(value, list) else [value])}
        for obj in objects.values():
            if obj["kind"] in KINDS:
                self.assertTrue(obj["refs"] or obj["key"] in referenced, obj["key"])
        for device in [o for o in objects.values() if o["meta"].get("hardware") in {"liquid-chassis", "liquid-blade", "console-server"}]:
            self.assertIn("primary_ip4", device["refs"], device["key"])
            self.assertIn("rack", device["refs"], device["key"])
        modules = [o for o in objects.values() if o["kind"] == "module"]
        self.assertTrue(any(objects[o["refs"]["device"]]["refs"].get("role") == "role/management" for o in modules), "Late management-switch PSUs must also be installed")

    def test_wrong_module_inlet_and_inventory_component_fail(self):
        p = deepcopy(self.plan)
        objects = {o["key"]: o for o in p["objects"]}
        inlet = next(o for o in objects.values() if o["kind"] == "power_port" and "module" in o["refs"])
        inlet["refs"].pop("module")
        item = next(o for o in objects.values() if o["kind"] == "inventory_item" and "parent" in o["refs"])
        other = next(o for o in objects.values() if o["kind"] == "cooling_intake" and o["refs"]["device"] != item["refs"]["device"])
        item["refs"]["component"] = other["key"]
        codes = {f["code"] for f in validate(p)}
        self.assertIn("equipment-installed-psu", codes)
        self.assertIn("equipment-inventory-parent", codes)

    def test_configured_supply_facts_cannot_be_waived_by_missing_metadata(self):
        for mutation in ("manufacturer", "offline", "disabled-bay", "empty-bay-fit", "wrong-position", "missing-module", "missing-inlet-binding"):
            with self.subTest(mutation=mutation):
                plan = deepcopy(self.plan)
                plan["contracts"] = []
                objects = {o["key"]: o for o in plan["objects"]}
                for obj in objects.values():
                    obj["meta"] = {}
                self.assertEqual(validate(plan), [])
                module = next(o for o in objects.values() if o["kind"] == "module")
                bay = objects[module["refs"]["module_bay"]]
                if mutation == "manufacturer":
                    objects[module["refs"]["module_type"]]["refs"]["manufacturer"] = "manufacturer/Devin Reference Designs"
                elif mutation == "offline":
                    module["attrs"]["status"] = "offline"
                elif mutation == "disabled-bay":
                    bay["attrs"]["enabled"] = False
                elif mutation == "empty-bay-fit":
                    bay["refs"]["module_bay_types"] = []
                elif mutation == "wrong-position":
                    bay["attrs"]["position"] = "wrong"
                elif mutation == "missing-module":
                    plan["objects"].remove(module)
                else:
                    port = next(o for o in objects.values() if o["kind"] == "power_port" and o["refs"].get("module") == module["key"])
                    port["refs"].pop("module")
                self.assertIn("equipment-installed-psu", {f["code"] for f in validate(plan)})

    def test_cooling_cycle_and_capacity_are_independently_rejected(self):
        # Native v4.7 CoolingLoopValidationMixin rejects directed upstream loops;
        # a supply/return loop is ONE CoolingFeed, not reciprocal component refs.
        p = deepcopy(self.plan)
        objects = {o["key"]: o for o in p["objects"]}
        outflow = next(o for o in objects.values() if o["kind"] == "cooling_outflow")
        objects[outflow["refs"]["cooling_intake"]]["refs"]["cooling_outflow"] = outflow["key"]
        next(o for o in objects.values() if o["kind"] == "cooling_source")["attrs"]["cooling_capacity"] = .5
        codes = {f["code"] for f in validate(p)}
        self.assertIn("equipment-cooling-cycle", codes)
        self.assertIn("equipment-cooling-capacity", codes)

    def test_installed_hardware_requires_actual_reference_kinds(self):
        for target in ("device-type", "module-type", "manufacturer", "bay-type"):
            with self.subTest(target=target):
                plan = deepcopy(self.plan)
                plan["contracts"] = []
                objects = {o["key"]: o for o in plan["objects"]}
                for obj in objects.values():
                    obj["meta"] = {}
                module = next(o for o in objects.values() if o["kind"] == "module")
                module_type = objects[module["refs"]["module_type"]]
                key = {"device-type": objects[module["refs"]["device"]]["refs"]["device_type"],
                       "module-type": module_type["key"],
                       "manufacturer": module_type["refs"]["manufacturer"],
                       "bay-type": module_type["refs"]["module_bay_types"][0]}[target]
                objects[key]["kind"] = "tag"
                codes = {f["code"] for f in validate(plan)}
                self.assertTrue(codes & {"equipment-hardware", "equipment-installed-psu"}, target)

    def test_console_cross_site_and_missing_stack_link_fail(self):
        p = deepcopy(self.plan)
        objects = {o["key"]: o for o in p["objects"]}
        console_cable = next(o for o in objects.values() if o["kind"] == "cable" and
                             objects[o["refs"]["a"]]["kind"] == "console_port")
        server = objects[objects[console_cable["refs"]["b"]]["refs"]["device"]]
        server["refs"]["location"] = "location/dc-02" if server["refs"]["location"] != "location/dc-02" else "location/dc-01"
        stack_cable = next(o for o in objects.values() if o["kind"] == "cable" and
                           "StackPort" in o["refs"]["a"])
        p["objects"].remove(stack_cable)
        codes = {f["code"] for f in validate(p)}
        self.assertIn("equipment-console-link", codes)
        self.assertIn("equipment-stack-links", codes)

    def test_partial_fixture_uses_supplied_catalog(self):
        fixture = {"objects": [{"key": "device", "kind": "device", "meta": {"hardware": "access"}}]}
        self.assertEqual(validate(fixture, catalog={"access": {"console_ports": []}}), [])
        malformed = deepcopy(self.plan)
        next(o for o in malformed["objects"] if o["kind"] == "cooling_feed")["attrs"]["cooling_capacity"] = None
        self.assertIn("equipment-cooling-feed", {f["code"] for f in validate(malformed)})

    def test_growth_preserves_existing_physical_records(self):
        grown = generate(self.plan["recipe"] | {"branches": {"small": 2}}, previous=self.plan)
        self.assertEqual(validate(grown), [])
        objects = {o["key"]: o for o in grown["objects"]}
        for old in self.plan["objects"]:
            if old["kind"] in KINDS or (old["kind"] == "cable" and
                    ("console_" in old["key"] or "StackPort" in old["key"])):
                self.assertEqual(objects[old["key"]], old, old["key"])


if __name__ == "__main__":
    unittest.main()
