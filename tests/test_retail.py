"""Retail composition must follow store demand and keep an inspectable estate."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.generate import generate
from estates.load import main as load_main
from estates.model import DesignError, ROOT, canonical, hardware_catalog, recipe_from_file
from estates.report import markdown
from estates.validate import validate
from estates.__main__ import main


def plan_for(**changes):
    return generate({"profile": "retail-chain"} | changes)


class RetailResolverTests(unittest.TestCase):
    def test_unknown_and_out_of_range_requests_are_actionable(self):
        cases = [({"classrooms": 4}, "Unknown retail recipe fields: classrooms"),
                 ({"wards": []}, "Unknown retail recipe fields: wards"),
                 ({"design_mix": {"modern": 1}}, "Unknown retail recipe fields: design_mix"),
                 ({"acquired_sites": []}, "Unknown retail recipe fields: acquired_sites"),
                 ({"stores": {"tiny": 1}}, "only small, medium and large"),
                 ({"stores": {"small": -1}}, "between 0 and 2000"),
                 ({"stores": {"small": True}}, "between 0 and 2000"),
                 ({"stores": {"small": 2001}}, "between 0 and 2000"),
                 ({"stores": {}}, "at least one store"),
                 ({"stores": {"small": 0, "medium": 0, "large": 0}}, "at least one store"),
                 ({"headquarters": 2}, "headquarters must be an integer between 0 and 1"),
                 ({"headquarters": -1}, "headquarters must be an integer between 0 and 1"),
                 ({"distribution_centers": 7}, "distribution_centers must be an integer between 0 and 6"),
                 ({"distribution_centers": "one"}, "distribution_centers must be an integer between 0 and 6"),
                 ({"address_pool": "192.168.0.0/20"}, "/16 site reservations"),
                 ({"reservation_user": "someone"}, "reservation_user"),
                 ({"demo": "invent-a-workflow"}, "not yet implemented"),
                 ({"stores": {"large": 200}}, "16,000 Mbps")]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(DesignError, message):
                plan_for(**changes)

    def test_defaults_are_frozen_into_the_recipe(self):
        recipe = plan_for(stores={"small": 1})["recipe"]
        self.assertEqual(recipe["stores"], {"small": 1, "medium": 0, "large": 0})
        self.assertEqual((recipe["headquarters"], recipe["distribution_centers"]), (1, 1))
        self.assertEqual(recipe["namespace"], "harvest")
        self.assertEqual(recipe["demo"], "baseline")

    def test_shipped_profile_resolves_and_validates(self):
        plan = generate(recipe_from_file(ROOT / "profiles/retail-chain.toml"))
        self.assertEqual(validate(plan), [])
        self.assertEqual(plan["recipe"]["stores"], {"small": 4, "medium": 2, "large": 1})
        self.assertEqual(plan["recipe"]["distribution_centers"], 1)


class RetailCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = plan_for()

    def test_default_chain_connects_store_demand_without_other_industries(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(canonical(generate(self.plan["recipe"])), canonical(self.plan))
        objects = self.plan["objects"]
        sites = [o for o in objects if o["kind"] == "site"]
        self.assertEqual(len(sites), 11)  # 2 DC + 1 HQ + 1 DI + 7 stores
        self.assertEqual({o["key"] for o in objects if o["kind"] == "site"},
                         {"site/dc-01", "site/dc-02", "site/hq-01", "site/di-01",
                          "site/st-s0001", "site/st-s0002", "site/st-s0003", "site/st-s0004",
                          "site/st-m0001", "site/st-m0002", "site/st-l0001"})
        self.assertEqual({o["meta"]["service"] for o in objects if o["kind"] == "virtual_machine"},
                         {"commerce-api", "pos-gateway", "inventory-db", "loyalty",
                          "identity", "dns", "monitoring", "backup"})
        text = canonical(self.plan).decode().lower()
        for forbidden in ("teller", "bank", "birch", "cedar", "classroom", "enrollment",
                          "bedside", "nurse", "exam room"):
            self.assertNotIn(forbidden, text, forbidden)
        for phrase in ("point-of-sale", "sales floor", "stockroom", "warehouse", "Store"):
            self.assertIn(phrase.lower(), text, phrase)
        guide = markdown(self.plan)
        for phrase in ("pos terminals", "Distribution Center", "Replica group placement"):
            self.assertIn(phrase, guide)

    def test_store_formats_place_their_own_endpoints_and_segments(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        for sid, workstations, lanes, aps, cameras in (("st-s0001", 2, 4, 2, 4),
                                                       ("st-m0001", 4, 8, 3, 8),
                                                       ("st-l0001", 8, 16, 5, 12)):
            roles = {}
            for obj in self.plan["objects"]:
                if obj["kind"] == "device" and obj["refs"].get("site") == f"site/{sid}":
                    roles[obj["refs"]["role"]] = roles.get(obj["refs"]["role"], 0) + 1
            self.assertEqual(roles["role/workstation"], workstations, sid)
            self.assertEqual(roles["role/pos-terminal"], lanes, sid)
            self.assertEqual(roles["role/ap"], aps, sid)
            self.assertEqual(roles["role/camera"], cameras, sid)
            for segment in ("backoffice", "pos", "wireless", "security", "guest", "management"):
                self.assertEqual(objects[f"vlan/{sid}/{segment}"]["refs"]["site"], f"site/{sid}")
                self.assertEqual(objects[f"prefix/{sid}/{segment}"]["attrs"]["status"], "active")
            # A lane sits on the sales floor and a desk in the back office.
            self.assertEqual(objects[f"device/{sid}/pos-001"]["refs"]["location"], f"location/{sid}/sales-floor")
            self.assertEqual(objects[f"device/{sid}/desk-001"]["refs"]["location"], f"location/{sid}/back-office")

    def test_distribution_centre_uses_scanners_and_no_lane_or_guest_segment(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        roles = {}
        for obj in self.plan["objects"]:
            if obj["kind"] == "device" and obj["refs"].get("site") == "site/di-01":
                roles[obj["refs"]["role"]] = roles.get(obj["refs"]["role"], 0) + 1
        self.assertEqual(roles["role/scanner"], 24)
        self.assertEqual(roles["role/workstation"], 12)
        self.assertNotIn("role/pos-terminal", roles)
        self.assertNotIn("vlan/di-01/pos", objects)
        self.assertNotIn("vlan/di-01/guest", objects)
        self.assertEqual(objects["device/di-01/scan-001"]["refs"]["location"],
                         "location/di-01/warehouse-floor")

    def test_wireless_serves_staff_and_public_guest_where_requested(self):
        lans = {o["key"] for o in self.plan["objects"] if o["kind"] == "wireless_lan"}
        self.assertIn("wireless-lan/st-s0001/guest", lans)
        self.assertIn("wireless-lan/st-s0001/staff", lans)
        self.assertIn("wireless-lan/hq-01/staff", lans)
        self.assertNotIn("wireless-lan/di-01/guest", lans)
        self.assertIn("wireless-lan/di-01/staff", lans)

    def test_growth_adds_stores_and_preserves_every_existing_identity(self):
        before = plan_for(stores={"small": 2, "medium": 1}, distribution_centers=1)
        recipe = deepcopy(before["recipe"])
        recipe["stores"].update(small=4, large=1)
        recipe["distribution_centers"] = 2
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old = {o["key"]: o for o in before["objects"]}
        new = {o["key"]: o for o in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        occupied = {end for o in before["objects"] if o["kind"] == "cable" for end in o["refs"].values()}
        for key, obj in old.items():
            if obj["kind"] == "power_port":
                # PoE and optical reservations grow with new connected demand;
                # the inlet identity and its catalog name never move.
                self.assertGreaterEqual(new[key]["attrs"].get("maximum_draw", 0),
                                        obj["attrs"].get("maximum_draw", 0), key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
            elif obj["kind"] in {"site", "rack", "location", "cable", "ip_address", "vlan", "prefix",
                                 "virtual_machine", "wireless_lan"} or key in occupied:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                for field in ("location", "rack", "site"):
                    self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field), key)
        for scope, items in before["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_reductions_and_profile_changes_need_a_new_baseline(self):
        before = plan_for(stores={"small": 2, "medium": 1}, distribution_centers=2, headquarters=1)
        for field, value in (("small", 1), ("medium", 0)):
            recipe = deepcopy(before["recipe"])
            recipe["stores"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(DesignError, "new baseline"):
                generate(recipe, previous=before)
        for key in ("headquarters", "distribution_centers"):
            recipe = deepcopy(before["recipe"])
            recipe[key] -= 1
            with self.subTest(key=key), self.assertRaisesRegex(DesignError, "new baseline"):
                generate(recipe, previous=before)
        with self.assertRaisesRegex(DesignError, "new estate|new baseline"):
            generate(deepcopy(before["recipe"]) | {"namespace": "other"}, previous=before)

    def test_damaged_previous_ledger_is_rejected_before_allocation(self):
        before = plan_for(stores={"small": 1}, headquarters=0, distribution_centers=0)
        damaged = deepcopy(before)
        damaged["objects"] = [o for o in damaged["objects"] if o["key"] != "device/st-s0001/desk-001"]
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(deepcopy(before["recipe"]), previous=damaged)


class RetailNamingTests(unittest.TestCase):
    def test_store_and_distribution_names_are_unique_and_retail_flavoured(self):
        plan = plan_for(stores={"small": 12, "medium": 4, "large": 2}, distribution_centers=6)
        sites = [o for o in plan["objects"] if o["kind"] == "site"]
        names = [o["attrs"]["name"] for o in sites]
        self.assertEqual(len(names), len(set(names)))
        stores = [o for o in sites if o["key"].startswith("site/st-")]
        self.assertEqual(len(stores), 18)
        self.assertTrue(all(o["attrs"]["name"].endswith(" Store") for o in stores))
        self.assertTrue(all(" and " in o["attrs"]["name"] for o in stores))
        centres = [o for o in sites if o["key"].startswith("site/di-")]
        self.assertEqual(len(centres), 6)
        self.assertTrue(all("Distribution Center" in o["attrs"]["name"] for o in centres))
        self.assertTrue(all(o["attrs"]["facility"] for o in sites))
        # Addresses stay distinct so a walkthrough can name a real premises.
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}), len(sites))

    def test_site_names_override_and_legacy_naming_stay_available(self):
        plan = plan_for(stores={"small": 1}, headquarters=0, distribution_centers=0,
                        site_names={"st-s0001": {"name": "Flagship Riverside", "facility": "FLAG01"}})
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertEqual(objects["site/st-s0001"]["attrs"]["name"], "Flagship Riverside")
        self.assertEqual(objects["site/st-s0001"]["attrs"]["facility"], "FLAG01")
        self.assertEqual(validate(plan), [])
        legacy = plan_for(stores={"small": 1}, headquarters=0, distribution_centers=0, naming="legacy")
        legacy_objects = {o["key"]: o for o in legacy["objects"]}
        self.assertEqual(legacy_objects["site/st-s0001"]["attrs"]["name"], "harvest-st-s0001")
        self.assertEqual(validate(legacy), [])
        with self.assertRaisesRegex(DesignError, "unknown site ids"):
            plan_for(stores={"small": 1}, site_names={"br-s0001": {"name": "Nope"}})


class RetailValidatorTests(unittest.TestCase):
    """Every independent retail assertion must reject a matching mutation."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = plan_for(stores={"small": 1, "medium": 1}, headquarters=0, distribution_centers=1)
        assert validate(cls.baseline) == []

    def mutated(self, change):
        plan = deepcopy(self.baseline)
        objects = {o["key"]: o for o in plan["objects"]}
        change(plan, objects)
        return {finding["code"] for finding in validate(plan)}

    def test_recipe_bounds_are_restated_independently(self):
        for field, value in (("stores", {"small": -1, "medium": 0, "large": 0}),
                             ("headquarters", 4), ("distribution_centers", 9),
                             ("headquarters_staff", 4), ("reserve_fraction", 0.9)):
            with self.subTest(field=field):
                self.assertIn("retail-recipe", self.mutated(
                    lambda plan, objects, f=field, v=value: plan["recipe"].__setitem__(f, v)))
        self.assertIn("retail-recipe", self.mutated(
            lambda plan, objects: plan["recipe"].__setitem__("wan_tiers_mbps", [100, 50])))

    def test_missing_or_extra_sites_are_reported(self):
        def drop_site(plan, objects):
            plan["recipe"]["stores"]["large"] = 1
        self.assertIn("retail-site-inventory", self.mutated(drop_site))

    def test_removed_endpoint_is_reported_even_when_the_contract_agrees(self):
        def remove(plan, objects):
            plan["objects"] = [o for o in plan["objects"] if o["key"] != "device/st-s0001/pos-004"]
            for contract in plan["contracts"]:
                if contract["site"] == "site/st-s0001":
                    contract["demand"]["pos_terminals"] = 3
                    contract["endpoint_count"] -= 1
        codes = self.mutated(remove)
        self.assertIn("retail-endpoint-inventory", codes)
        self.assertIn("retail-endpoint-demand", codes)
        self.assertIn("retail-demand-report", codes)

    def test_lane_moved_off_the_sales_floor_is_reported(self):
        def move(plan, objects):
            objects["device/st-s0001/pos-002"]["refs"]["location"] = "location/st-s0001/stockroom"
        codes = self.mutated(move)
        self.assertIn("retail-endpoint-placement", codes)
        self.assertIn("endpoint-room", codes)

    def test_lane_on_the_wrong_segment_is_reported(self):
        def rewire(plan, objects):
            objects["device/st-s0001/pos-002/if/eth0"]["refs"]["untagged_vlan"] = "vlan/st-s0001/backoffice"
        self.assertIn("retail-endpoint-path", self.mutated(rewire))

    def test_radio_without_a_poe_access_port_is_reported(self):
        def unplug(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and "device/st-s0001/ap-001/if/eth0" in obj["refs"].values():
                    obj["attrs"]["status"] = "planned"
        codes = self.mutated(unplug)
        self.assertIn("retail-endpoint-path", codes)

    def test_radio_cabled_to_an_uplink_port_is_reported(self):
        def relocate(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and "device/st-s0001/ap-001/if/eth0" in obj["refs"].values():
                    for field in ("a", "b"):
                        if obj["refs"][field] != "device/st-s0001/ap-001/if/eth0":
                            obj["refs"][field] = "device/st-s0001/access-01/if/TenGigabitEthernet1/1/4"
        codes = self.mutated(relocate)
        self.assertIn("retail-ap-power", codes)

    def test_moved_endpoint_mount_and_route_are_reported(self):
        def shift(plan, objects):
            objects["device/st-s0001/cam-001"]["meta"]["placement"]["position_m"] = [99, 99, 2.5]
        self.assertIn("retail-endpoint-route", self.mutated(shift))

    def test_relocated_segment_prefix_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/st-s0001/pos"]["attrs"]["prefix"] = "10.99.99.0/24"
        self.assertIn("retail-prefix-policy", self.mutated(renumber))

    def test_undersized_store_wan_commitment_is_reported(self):
        def downgrade(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "circuit" and obj["key"].startswith("circuit/st-m0001/"):
                    obj["attrs"]["commit_rate"] = 50000
        self.assertIn("retail-wan-capacity", self.mutated(downgrade))

    def test_both_carriers_on_one_edge_are_reported(self):
        def collapse(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and any(str(v).startswith("circuit/st-s0001/b/") for v in obj["refs"].values()):
                    for field in ("a", "b"):
                        if str(obj["refs"][field]).startswith("device/"):
                            obj["refs"][field] = "device/st-s0001/edge-a/if/wan2"
        codes = self.mutated(collapse)
        self.assertIn("retail-wan-diversity", codes)

    def test_missing_gateway_svi_is_reported(self):
        def disable(plan, objects):
            for obj in plan["objects"]:
                if (obj["kind"] == "interface" and obj["refs"].get("device") == "device/st-s0001/dist-a"
                        and obj["refs"].get("untagged_vlan") == "vlan/st-s0001/security"):
                    obj["attrs"]["enabled"] = False
        codes = self.mutated(disable)
        self.assertIn("retail-gateway-inventory", codes)

    def test_distribution_centre_lane_segment_is_rejected(self):
        def add_segment(plan, objects):
            plan["objects"].append({"key": "vlan/di-01/pos", "kind": "vlan",
                                    "attrs": {"name": "harvest-di-01-pos", "vid": 30, "status": "active"},
                                    "refs": {"site": "site/di-01", "tenant": "tenant"}, "meta": {}})
        self.assertIn("retail-segment-unrequested", self.mutated(add_segment))

    def test_infrastructure_moved_out_of_the_equipment_room_is_reported(self):
        def move(plan, objects):
            objects["device/st-s0001/access-01"]["refs"]["location"] = "location/st-s0001/stockroom"
        codes = self.mutated(move)
        self.assertIn("retail-equipment-placement", codes)

    def test_reserved_access_slot_that_disagrees_with_the_graph_is_reported(self):
        def swap(plan, objects):
            scope = "access-endpoints/st-s0001/location/st-s0001"
            slots = plan["reservations"][scope]
            slots["device/st-s0001/desk-001"] = max(slots.values()) + 1
        self.assertIn("retail-access-allocation", self.mutated(swap))

    def test_removed_commerce_service_replica_is_reported(self):
        def remove(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not o["key"].startswith("vm/dc-01/pos-gateway/002")]
        codes = self.mutated(remove)
        self.assertTrue({"dc-workload-inventory"} & codes, codes)

    def test_wrong_dc_prefix_offset_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/dc-01/database"]["attrs"]["prefix"] = "10.200.16.0/20"
        self.assertIn("dc-prefix-policy", self.mutated(renumber))

    def test_every_remaining_retail_assertion_rejects_its_own_mutation(self):
        def extra_room(plan, objects):
            plan["objects"].append({"key": "location/st-s0001/annex", "kind": "location",
                                    "attrs": {"name": "Annex", "slug": "harvest-st-s0001-annex", "status": "active"},
                                    "refs": {"site": "site/st-s0001", "tenant": "tenant",
                                             "parent": "location/st-s0001/floor-01"},
                                    "meta": {"space_type": "stockroom", "floor": 1, "position_m": [40, 40, 0]}})

        def extra_switch(plan, objects):
            plan["objects"].append(dict(objects["device/st-s0001/access-01"],
                                        key="device/st-s0001/access-03",
                                        attrs=dict(objects["device/st-s0001/access-01"]["attrs"],
                                                   name="sts0001-as03", position=9)))

        def one_carrier(plan, objects):
            objects["circuit/st-s0001/b/1"]["refs"]["provider"] = "provider/a"

        cases = [
            ("retail-address-allocation", lambda plan, objects: plan["allocations"].__setitem__("st-s0001", 0)),
            ("retail-site-status", lambda plan, objects: objects["site/st-s0001"]["attrs"].__setitem__("status", "planned")),
            ("retail-contract-inventory", lambda plan, objects: plan.__setitem__(
                "contracts", [c for c in plan["contracts"] if c["site"] != "site/st-s0001"])),
            ("retail-room-inventory", extra_room),
            ("retail-room-placement", lambda plan, objects: objects["location/st-s0001/back-office"]["meta"].__setitem__("position_m", [7, 30, 0])),
            ("retail-closet-inventory", lambda plan, objects: objects["location/st-s0001"]["meta"].__setitem__("space_type", "office")),
            ("retail-endpoint-address", lambda plan, objects: objects["ip/device/st-s0001/desk-001/if/eth0"]["attrs"].__setitem__("status", "reserved")),
            ("retail-access-inventory", extra_switch),
            ("retail-access-capacity", lambda plan, objects: objects["device/st-s0001/access-01"]["meta"].__setitem__("hardware", "leaf")),
            ("retail-core-inventory", lambda plan, objects: objects["device/st-s0001/dist-b"]["refs"].__setitem__("role", "role/access")),
            ("retail-uplink-path", lambda plan, objects: objects["device/st-s0001/access-01/if/TenGigabitEthernet1/1/1"]["attrs"].__setitem__("enabled", False)),
            ("retail-wan-inventory", one_carrier),
            ("retail-equipment-role", lambda plan, objects: objects["device/st-s0001/console-01"]["refs"].__setitem__("role", "role/workstation")),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                self.assertIn(code, self.mutated(change))

    def test_power_path_break_is_reported(self):
        def unplug(plan, objects):
            for obj in plan["objects"]:
                if (obj["kind"] == "cable" and obj["attrs"].get("type") == "power" and
                        any("device/st-s0001/access-01/power/" in str(v) for v in obj["refs"].values())):
                    obj["attrs"]["status"] = "planned"
                    break
        codes = self.mutated(unplug)
        self.assertTrue({"dc-power-path", "power-path-status"} & codes, codes)


class RetailArtifactTests(unittest.TestCase):
    def test_cli_preview_generation_and_turbobulk_load_check(self):
        def call(*args):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = main(["--json", *map(str, args)])
            self.assertEqual(status, 0, output.getvalue() + error.getvalue())
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "retail.toml"
            recipe.write_text('profile = "retail-chain"\nheadquarters = 0\n'
                              'distribution_centers = 1\n[stores]\nsmall = 1\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["profile"], "retail-chain")
            self.assertEqual(preview["resolved"]["stores"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["headquarters_staff"]["source"], "default")
            call("generate", recipe, "--out", root / "build")
            self.assertEqual(json.loads((root / "build/intent.json").read_text()), preview)
            call("check", root / "build/plan.json")
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = load_main([str(root / "build"), "--load-check"])
            self.assertEqual(status, 0, error.getvalue())
            verdict = json.loads(output.getvalue())
            self.assertTrue(verdict["turbobulk_loadable"], verdict)
            self.assertEqual(verdict["turbobulk_uncovered"], [])
            self.assertEqual(verdict["turbobulk_unsupported_refs"], [])

    def test_catalog_hardware_is_reused_without_new_models(self):
        plan = plan_for(stores={"small": 1}, headquarters=0, distribution_centers=1)
        aliases = {o["meta"]["hardware"] for o in plan["objects"] if o["kind"] == "device"}
        self.assertTrue(aliases <= set(hardware_catalog()["models"]))
        self.assertEqual(aliases & {"atm", "inherited-access", "liquid-blade", "liquid-chassis"}, set())


if __name__ == "__main__":
    unittest.main()
