"""Physical family links, native cooling semantics, and stable allocations."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from estates.bank import generate
from estates.equipment import KINDS, _console_size, validate
from estates.model import hardware_catalog, serial_pattern, vendor_serial
from estates.validate import validate as full_validate


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
                    objects[module["refs"]["module_type"]]["refs"]["manufacturer"] = "manufacturer/Generic"
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


class ShowcaseHardwareTests(unittest.TestCase):
    """Real catalog models, vendor-shaped serials and platforms (catalog 0.12)."""

    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"branches": {"small": 1}, "headquarters": 0})
        cls.catalog = hardware_catalog()

    def objects(self, plan=None):
        return {o["key"]: o for o in (plan or self.plan)["objects"]}

    def test_no_authored_name_or_placeholder_reaches_the_estate(self):
        for obj in self.plan["objects"]:
            text = " ".join(str(v) for v in obj["attrs"].values())
            self.assertNotIn("Devin", text, obj["key"])
            self.assertNotIn("SYN-", text, obj["key"])
            if obj["kind"] in {"device_type", "module_type", "rack_type"}:
                self.assertFalse(obj["attrs"]["model"].startswith("Reference"), obj["key"])

    def test_every_catalog_template_yields_its_own_pattern(self):
        for alias, model in self.catalog["models"].items():
            for field in ("serial_format", "module_serial_format"):
                if field == "serial_format" or model.get("configured_modules"):
                    fmt = model[field]
                    for n in (0, 7, 2 ** 61 + 12345):
                        serial = vendor_serial(fmt, n)
                        self.assertRegex(serial, "^" + serial_pattern(fmt) + "$", (alias, field))
                        self.assertLessEqual(len(serial), 50)

    def test_placeholder_serial_and_wrong_platform_are_rejected(self):
        self.assertEqual(full_validate(self.plan), [])
        objects = self.objects()
        switch = next(o for o in objects.values() if o["meta"].get("hardware") == "access")
        pdu = next(o for o in objects.values() if o["meta"].get("hardware") == "pdu")
        self.assertEqual(objects[switch["refs"]["platform"]]["attrs"]["name"], "Cisco IOS XE")
        self.assertNotIn("platform", pdu["refs"])
        for mutate, code in (
                (lambda o: o[switch["key"]]["attrs"].__setitem__("serial", "SYN-0000000001"), "hardware-serial"),
                (lambda o: o[switch["key"]]["refs"].pop("platform"), "hardware-platform"),
                (lambda o: o[switch["key"]]["refs"].__setitem__("platform", "platform/services"), "hardware-platform"),
                (lambda o: o[pdu["key"]]["refs"].__setitem__("platform", switch["refs"]["platform"]), "hardware-platform")):
            plan = deepcopy(self.plan)
            mutate(self.objects(plan))
            self.assertIn(code, {f["code"] for f in full_validate(plan)})

    def test_console_server_size_follows_first_demand_and_never_swaps(self):
        def site(world):
            return SimpleNamespace(w=world, id="br-x", room_prefix=lambda room: "")
        world = SimpleNamespace(catalog=self.catalog, reservations={})
        world.reserve = lambda scope, key, capacity: world.reservations.setdefault(scope, {}).setdefault(key, 0)
        self.assertEqual(_console_size(site(world), "room", ["p"] * 4), "console-server")
        # Growth past sixteen consoles adds a second CM8116, never a CM8148 swap.
        self.assertEqual(_console_size(site(world), "room", ["p"] * 30), "console-server")
        fresh = SimpleNamespace(catalog=self.catalog, reservations={}, reserve=None)
        fresh.reserve = lambda scope, key, capacity: fresh.reservations.setdefault(scope, {}).setdefault(key, 0)
        self.assertEqual(_console_size(site(fresh), "room", ["p"] * 30), "console-server-48")
        models = {self.objects()[o["refs"]["device_type"]]["attrs"]["model"]
                  for o in self.plan["objects"] if o["kind"] == "device" and o["refs"]["role"] == "role/console-server"}
        self.assertTrue(models and models <= {"CM8116", "CM8148"}, models)


if __name__ == "__main__":
    unittest.main()
