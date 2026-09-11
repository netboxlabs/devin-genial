"""Optical inventory counterexamples over actual generated hardware and paths."""

from copy import deepcopy
import hashlib
import unittest

from estates.generate import generate
from estates.model import hardware_catalog
from estates.validate_optics import analyze


class OpticsValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = hardware_catalog()
        cls.bank = generate({"headquarters": 0, "branches": {"small": 1}})
        cls.provider = generate({"profile": "provider-backbone"})
        cls.aoc = generate({"profile": "school-district"})

    def fixture(self, source=None):
        plan = deepcopy(source or self.bank)
        plan.pop("contracts", None)
        for obj in plan["objects"]:
            obj.pop("meta", None)
        return plan, {o["key"]: o for o in plan["objects"]}

    def codes(self, plan, catalog=None):
        return {f["code"] for f in analyze(plan, catalog or self.catalog)[0]}

    def link(self, objects, medium="smf", part=None):
        for cable in objects.values():
            if cable["kind"] != "cable" or cable["attrs"].get("type") != medium:
                continue
            ports = [objects[cable["refs"][side]] for side in ("a", "b")]
            if all(p["kind"] == "interface" for p in ports):
                modules = [objects[p["refs"]["module"]] for p in ports]
                if part is None or any(m["refs"]["module_type"] == part for m in modules):
                    return cable, ports, modules
        self.fail("Fixture needs a real installed optical link")

    def branch_link(self, objects):
        return self.link(objects, part="module-type/Fortinet/FN-TRAN-SFP+LR")

    def test_all_five_profiles_without_metadata_or_contracts_and_models_only(self):
        plans = [self.bank, self.provider] + [generate({"profile": p}) for p in (
            "enterprise-data-center", "school-district", "hospital-clinics")]
        for original in plans:
            with self.subTest(profile=original["recipe"]["profile"]):
                plan, _ = self.fixture(original)
                before = deepcopy(plan)
                findings, extra = analyze({"objects": plan["objects"]}, self.catalog["models"])
                self.assertEqual(findings, [])
                self.assertEqual(plan, before)
                self.assertEqual(analyze(plan), (findings, extra))
                self.assertTrue(extra)
                self.assertTrue(all(type(v) is int and v > 0 for v in extra.values()))

    def test_missing_module_cannot_be_waived_by_removing_descriptions(self):
        plan, objects = self.fixture()
        _, ports, modules = self.branch_link(objects)
        ports[0]["refs"].pop("module")
        plan["objects"].remove(modules[0])
        self.assertIn("optics-module", self.codes(plan))

    def test_actual_vendor_and_model_not_identical_marketing_name_or_module_json(self):
        plan, objects = self.fixture()
        cisco = next(o for o in objects.values() if o["kind"] == "module" and
                     o["refs"].get("module_type") == "module-type/Cisco/SFP-10G-LR")
        cisco["refs"]["module_type"] = "module-type/Arista/SFP-10G-LR"
        objects[cisco["refs"]["module_type"]]["attrs"]["attributes"] = '{"power_reservation_mw": 0, "manufacturer": "Cisco"}'
        self.assertIn("optics-compatibility", self.codes(plan))
        _, extra = analyze(plan)
        self.assertEqual(extra[cisco["refs"]["device"]], 5)  # Two 1 W Cisco + 2 W Arista, ceil(4*1.25).
        self.assertIn("optics-part-details", self.codes(plan))

    def test_unknown_actual_host_and_module_fail_without_metadata(self):
        for field in ("device_type", "module_type"):
            plan, objects = self.fixture()
            _, ports, modules = self.branch_link(objects)
            target = objects[ports[0]["refs"]["device"]] if field == "device_type" else modules[0]
            target["refs"].pop(field)
            with self.subTest(field=field):
                self.assertIn("optics-hardware", self.codes(plan))

    def test_inactive_disconnected_module_still_reserves_its_full_power(self):
        plan, objects = self.fixture()
        cable, ports, modules = self.branch_link(objects)
        baseline = analyze(plan)[1]
        modules[0]["attrs"]["status"] = "offline"
        plan["objects"].remove(cable)
        findings, extra = analyze(plan)
        self.assertIn("optics-module", {f["code"] for f in findings})
        self.assertEqual(extra, baseline)
        modules[0]["attrs"]["status"] = "active"
        self.assertEqual(analyze(plan)[0], [])  # Installed uncabled detachable spare remains inventory.

    def test_duplicate_occupancy_counts_two_physical_modules_but_is_invalid(self):
        plan, objects = self.fixture()
        _, _, modules = self.branch_link(objects)
        duplicate = deepcopy(modules[0])
        duplicate["key"] += "/duplicate"
        plan["objects"].append(duplicate)
        self.assertIn("optics-module", self.codes(plan))
        self.assertGreater(analyze(plan)[1][duplicate["refs"]["device"]], analyze(self.bank)[1][duplicate["refs"]["device"]])

    def test_reused_module_binding_does_not_duplicate_power(self):
        plan, objects = self.fixture()
        _, ports, modules = self.branch_link(objects)
        before = analyze(plan)[1]
        ports[1]["refs"]["module"] = modules[0]["key"]
        self.assertIn("optics-module", self.codes(plan))
        self.assertEqual(analyze(plan)[1], before)

    def test_bay_owner_enabled_position_and_advertised_fit(self):
        mutations = {
            "owner": lambda bay: bay["refs"].update(device="device/unknown"),
            "disabled": lambda bay: bay["attrs"].update(enabled=False),
            "position": lambda bay: bay["attrs"].update(position="another-cage"),
            "name": lambda bay: bay["attrs"].update(name="Optic wrong"),
            "fit": lambda bay: bay["refs"].update(module_bay_types=[]),
        }
        for label, mutate in mutations.items():
            plan, objects = self.fixture()
            _, _, modules = self.branch_link(objects)
            mutate(objects[modules[0]["refs"]["module_bay"]])
            with self.subTest(mutation=label):
                self.assertIn("optics-module", self.codes(plan))
        plan, objects = self.fixture()
        module_type = objects["module-type/Cisco/SFP-10G-LR"]
        module_type["refs"]["module_bay_types"].append("optics-bay-type/Arista/sfpp")
        self.assertIn("optics-module", self.codes(plan))

    def test_optical_cages_cannot_be_nested_under_psu_or_optical_modules(self):
        for parent_kind in ("same-host-psu", "other-host-psu", "optical", "self", "cycle"):
            plan, objects = self.fixture()
            _, _, modules = self.branch_link(objects)
            optic, peer = modules
            bay = objects[optic["refs"]["module_bay"]]
            psus = [objects[p["refs"]["module"]] for p in objects.values() if p["kind"] == "power_port"
                    and p["refs"].get("module") in objects]
            if parent_kind.endswith("psu"):
                same_host = parent_kind == "same-host-psu"
                parent = next(m for m in psus if (m["refs"]["device"] == optic["refs"]["device"]) == same_host)
            else:
                parent = optic if parent_kind == "self" else peer
            bay["refs"]["module"] = parent["key"]
            if parent_kind == "cycle":
                objects[peer["refs"]["module_bay"]]["refs"]["module"] = optic["key"]
            with self.subTest(parent=parent_kind):
                findings, extra = analyze(plan)
                self.assertTrue(any(f["code"] == "optics-module" and f["object"] == optic["key"] for f in findings))
                self.assertEqual(extra, analyze(self.bank)[1])

    def test_unreviewed_physical_cages_and_radio_virtual_management_cannot_host_optics(self):
        for mutation in (dict(type="1000base-t"), dict(type="virtual"), dict(type="ieee802.11ax"),
                         dict(mgmt_only=True), dict(enabled=False), dict(name="invented-optical-cage")):
            plan, objects = self.fixture()
            _, ports, _ = self.branch_link(objects)
            ports[0]["attrs"].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertIn("optics-port", self.codes(plan))
        plan, objects = self.fixture()
        _, ports, _ = self.branch_link(objects)
        ports[0]["kind"] = "vm_interface"
        self.assertIn("optics-port", self.codes(plan))

    def test_smf_on_fixed_copper_is_invalid_even_without_any_module(self):
        plan, objects = self.fixture()
        cable = next(o for o in objects.values() if o["kind"] == "cable" and o["attrs"].get("type") == "cat6")
        cable["attrs"]["type"] = "smf"
        self.assertIn("optics-port", self.codes(plan))

    def test_provider_one_gigabit_sfp_in_ten_gigabit_cage_uses_effective_speed(self):
        plan, objects = self.fixture(self.provider)
        port = next(o for o in objects.values() if o["kind"] == "interface" and o["attrs"].get("speed") == 1000000
                    and o["attrs"].get("type") == "10gbase-x-sfpp" and "module" in o["refs"])
        self.assertEqual(analyze(plan)[0], [])
        self.assertEqual(objects[port["refs"]["module"]]["refs"]["module_type"], "module-type/Juniper/SFP-1GE-LX")
        port["attrs"].pop("speed")
        self.assertIn("optics-compatibility", self.codes(plan))

    def test_invalid_effective_speed_is_not_coerced(self):
        for rate in (True, 10000000.0, "10000000", 0, float("nan"), 1000):
            plan, objects = self.fixture()
            _, ports, _ = self.branch_link(objects)
            ports[0]["attrs"]["speed"] = rate
            with self.subTest(rate=rate):
                self.assertIn("optics-compatibility", self.codes(plan))

    def test_channel_media_length_units_finiteness_and_status(self):
        changes = (("type", "dac-active", "optics-media"), ("type", "cat6", "optics-media"),
                   ("length", 101, "optics-path"), ("length", 2, "optics-reach"),
                   ("length", float("nan"), "optics-path"), ("length", 10**1000, "optics-path"),
                   ("length", True, "optics-path"), ("length_unit", "furlong", "optics-path"),
                   ("status", "planned", "optics-path"))
        for field, value, code in changes:
            plan, objects = self.fixture()
            cable, _, _ = self.branch_link(objects)
            cable["attrs"][field] = value
            with self.subTest(field=field, value=repr(value)[:20]):
                self.assertIn(code, self.codes(plan))

    def passive_link(self, source=None):
        plan, objects = self.fixture(source)
        cable, ports, _ = self.branch_link(objects)
        owner = deepcopy(objects[ports[0]["refs"]["device"]])
        owner["key"] = "fixture/lc-panel"
        owner["refs"]["device_type"] = "hardware/patch-panel"
        front = dict(key="fixture/lc-front", kind="front_port",
                     attrs=dict(name="F01", type="lc", rear_port_position=1),
                     refs=dict(device=owner["key"], rear_port="fixture/lc-rear"))
        rear = dict(key="fixture/lc-rear", kind="rear_port", attrs=dict(name="R01", type="lc", positions=1),
                    refs=dict(device=owner["key"]))
        second = deepcopy(cable)
        second["key"] = "fixture/lc-second"
        second["refs"] = dict(a=rear["key"], b=ports[1]["key"])
        cable["refs"] = dict(a=ports[0]["key"], b=front["key"])
        cable["attrs"]["length"] = second["attrs"]["length"] = 30
        plan["objects"].extend((owner, front, rear, second))
        objects.update({o["key"]: o for o in (owner, front, rear, second)})
        return plan, objects, cable, second, front, rear

    def test_real_lc_front_rear_path_and_complete_distance(self):
        plan, _, cable, second, _, _ = self.passive_link()
        self.assertEqual(analyze(plan)[0], [])
        cable["attrs"]["length"] = second["attrs"]["length"] = 60
        self.assertIn("optics-path", self.codes(plan))

    def test_passive_copper_connector_bad_position_or_wrong_owner_is_not_an_optical_path(self):
        for field, value in (("type", "8p8c"), ("rear_port_position", 2), ("rear_port_position", True)):
            plan, _, _, _, front, _ = self.passive_link()
            front["attrs"][field] = value
            with self.subTest(field=field):
                self.assertIn("optics-path", self.codes(plan))
        plan, _, _, _, _, rear = self.passive_link()
        rear["refs"]["device"] = "device/unknown"
        self.assertIn("optics-path", self.codes(plan))

    def test_smaller_reference_endpoint_reach_is_enforced(self):
        plan, objects = self.fixture()
        cable, _, _ = self.link(objects, part="module-type/Devin Reference Designs/Reference 10G-LR transceiver")
        catalog = deepcopy(self.catalog)
        catalog["optics"]["local_max_m"] = 1000
        cable["attrs"]["length"] = 101
        self.assertIn("optics-reach", self.codes(plan, catalog))

    def test_duplicate_cable_and_site_mismatch_are_not_valid_local_links(self):
        plan, objects = self.fixture()
        cable, ports, _ = self.branch_link(objects)
        duplicate = deepcopy(cable)
        duplicate["key"] += "/duplicate"
        plan["objects"].append(duplicate)
        self.assertIn("optics-path", self.codes(plan))
        plan["objects"].remove(duplicate)
        objects[ports[1]["refs"]["device"]]["refs"]["site"] = "site/somewhere-else"
        self.assertIn("optics-path", self.codes(plan))
        for port in ports:
            objects[port["refs"]["device"]]["refs"].pop("site")
        self.assertIn("optics-path", self.codes(plan))

    def test_unknown_remote_circuit_is_allowed_but_local_rate_and_site_are_checked(self):
        for change, expected in (({}, None), (dict(port_speed=1000), "optics-media")):
            plan, objects = self.fixture(self.provider)
            term = next(o for o in objects.values() if o["kind"] == "circuit_termination" and
                        any(c["kind"] == "cable" and c["attrs"].get("type") == "smf" and
                            o["key"] in c["refs"].values() for c in objects.values()))
            term["attrs"].update(change)
            with self.subTest(change=change):
                self.assertIn(expected, self.codes(plan)) if expected else self.assertEqual(analyze(plan)[0], [])
            term["refs"]["termination"] = "site/unknown"
            self.assertIn("optics-path", self.codes(plan))

    def test_aoc_two_ends_reserve_seven_watts_once_with_separate_host_rounding(self):
        plan, objects = self.fixture(self.aoc)
        _, ports, modules = self.link(objects, "aoc")
        only = {m["key"] for m in modules}
        # Keep actual host and fit records, disconnect unrelated optical cages.
        plan["objects"] = [o for o in plan["objects"] if o["kind"] != "module" or o["key"] in only]
        _, extra = analyze(plan)
        self.assertEqual(extra, {p["refs"]["device"]: 5 for p in ports})

    def test_aoc_serial_length_media_asset_tag_and_complete_assembly(self):
        for change in ("serial", "short", "long", "dac", "tag", "wrong-part", "disconnected", "reuse-serial", "comments"):
            plan, objects = self.fixture(self.aoc)
            cable, ports, modules = self.link(objects, "aoc")
            if change == "serial":
                modules[0]["attrs"]["serial"] = "AOC-" + "0" * 24
            elif change == "reuse-serial":
                for module in modules:
                    module["attrs"]["serial"] = "AOC-" + "0" * 24
            elif change in ("short", "long"):
                cable["attrs"]["length"] = 2 if change == "short" else 4
            elif change == "dac":
                cable["attrs"]["type"] = "dac-active"
            elif change == "tag":
                for module in modules:
                    module["attrs"]["asset_tag"] = "same-tag"
            elif change == "wrong-part":
                modules[0]["refs"]["module_type"] = "module-type/Arista/QSFP-100G-LR4"
            elif change == "disconnected":
                plan["objects"].remove(cable)
            elif change == "comments":
                cable["attrs"]["comments"] = "Assembly serial: some-other-cable"
            with self.subTest(change=change):
                expected = {"dac": "optics-media", "comments": "optics-assembly-details"}.get(change, "optics-assembly")
                self.assertIn(expected, self.codes(plan))

    def test_native_aoc_prose_cannot_claim_an_independently_replaceable_end(self):
        for field, value in (("description", "Replace this LR4 optic independently"), ("description", ""),
                             ("comments", ""), ("comments", "replace one captive end")):
            plan, objects = self.fixture(self.aoc)
            cable, _, modules = self.link(objects, "aoc")
            target = cable if field == "comments" else modules[0]
            if field == "comments" and value:
                value = f"Assembly serial: {modules[0]['attrs']['serial']}\n{value}"
            target["attrs"][field] = value
            with self.subTest(field=field, value=value):
                self.assertIn("optics-assembly-details", self.codes(plan))

    def test_objects_only_aoc_serial_cannot_be_shared_by_two_assemblies(self):
        plan, objects = self.fixture(self.aoc)
        cables = [o for o in objects.values() if o["kind"] == "cable" and o["attrs"].get("type") == "aoc"]
        self.assertGreaterEqual(len(cables), 2)
        for cable in cables[:2]:
            for endpoint in cable["refs"].values():
                objects[objects[endpoint]["refs"]["module"]]["attrs"]["serial"] = "AOC-" + "0" * 24
        self.assertIn("optics-assembly", self.codes({"objects": plan["objects"]}))

    def test_aoc_cannot_pass_through_lc_panel_even_at_three_meters(self):
        plan, objects, cable, second, front, rear = self.passive_link(self.aoc)
        _, aoc_ports, aoc_modules = self.link(objects, "aoc")
        old = next(o for o in plan["objects"] if o["kind"] == "cable" and o["refs"].get("a") == aoc_ports[0]["key"])
        plan["objects"].remove(old)
        cable["refs"]["a"] = aoc_ports[0]["key"]
        second["refs"]["b"] = aoc_ports[1]["key"]
        for segment in (cable, second):
            segment["attrs"].update(type="aoc", length=1.5)
        objects[front["refs"]["device"]]["refs"]["site"] = objects[aoc_ports[0]["refs"]["device"]]["refs"]["site"]
        serial = "AOC-" + hashlib.sha256(f"{plan['recipe']['namespace']}/{cable['key']}".encode()).hexdigest()[:24]
        for module in aoc_modules:
            module["attrs"]["serial"] = serial
        self.assertIn("optics-assembly", self.codes(plan))

    def test_invalid_catalog_reservations_and_margin_do_not_become_zero(self):
        for field, value in (("power_reservation_mw", True), ("power_reservation_mw", 0), ("reach_m", float("inf"))):
            catalog = deepcopy(self.catalog)
            catalog["optics"]["parts"]["cisco-10g-lr"][field] = value
            with self.subTest(field=field, value=value):
                self.assertIn("optics-catalog", self.codes(self.bank, catalog))
        catalog = deepcopy(self.catalog)
        catalog["optics"]["upstream_ac_allowance_multiplier"] = "NaN"
        self.assertIn("optics-catalog", self.codes(self.bank, catalog))

    def test_module_json_is_checked_but_never_used_for_hardware_or_power(self):
        for details in ('{}', '{"rate_kbps": 1}', '{"power_reservation_mw": 0}', 'not JSON', None):
            plan, objects = self.fixture()
            baseline = analyze(plan)[1]
            objects["module-type/Cisco/SFP-10G-LR"]["attrs"]["attributes"] = details
            with self.subTest(details=details):
                self.assertIn("optics-part-details", self.codes(plan))
                self.assertEqual(analyze(plan)[1], baseline)

    def test_detachable_serial_format_native_length_and_stable_binding(self):
        for serial in (None, "", "x" * 51, "OPT-" + "0" * 24):
            plan, objects = self.fixture()
            _, _, modules = self.branch_link(objects)
            modules[0]["attrs"]["serial"] = serial
            with self.subTest(serial=serial):
                self.assertIn("optics-serial", self.codes(plan))

    def test_legacy_dimensional_fixture_only_skips_without_native_inventory_or_generation(self):
        from tests.test_validate import branch_fixture
        plan, catalog = branch_fixture("modern")
        self.assertEqual(analyze(plan, catalog), ([], {}))
        for mutation in ("device_type", "module", "generation"):
            changed = deepcopy(plan)
            if mutation == "generation":
                changed["generator_version"] = "0.9"
            else:
                target = next(o for o in changed["objects"] if o["kind"] == ("device" if mutation == "device_type" else "interface"))
                target["refs"][mutation] = "actual/native/inventory"
            with self.subTest(mutation=mutation):
                self.assertIn("optics-catalog", {f["code"] for f in analyze(changed, catalog)[0]})

    def test_malformed_physical_references_report_findings_instead_of_crashing(self):
        for record, field in (("module", "device"), ("module", "module_type"), ("module", "module_bay"),
                              ("port", "device"), ("port", "module"), ("bay", "module_bay_types")):
            for value in ([], {}, None):
                plan, objects = self.fixture()
                _, ports, modules = self.branch_link(objects)
                target = {"module": modules[0], "port": ports[0], "bay": objects[modules[0]["refs"]["module_bay"]]}[record]
                target["refs"][field] = value
                with self.subTest(record=record, field=field, value=value):
                    self.assertTrue(analyze(plan)[0])


if __name__ == "__main__":
    unittest.main()
