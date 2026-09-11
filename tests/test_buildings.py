"""Building-driven access, backbone, management and power acceptance checks."""

from collections import Counter
from copy import deepcopy
import math
import unittest

from estates.bank import generate
from estates.model import DesignError, hardware_catalog
from estates.report import markdown
from estates.validate import validate


class BuildingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 1, "headquarters_staff": 180, "branches": {"small": 1}})
        cls.panels = generate(cls.baseline["recipe"] | {"patching": "panels"})

    def setUp(self):
        self.use_plan(self.baseline)

    def use_plan(self, plan):
        self.plan = deepcopy(plan)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}
        self.contract = next(c for c in self.plan["contracts"] if c["site"] == "site/hq-01")

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def cable_at(self, port):
        return next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and port in obj["refs"].values())

    def spare_port(self, device, kind="interface"):
        occupied = {port for obj in self.plan["objects"] if obj["kind"] == "cable" for port in obj["refs"].values()}
        return next(obj for obj in self.plan["objects"] if obj["kind"] == kind and obj["refs"].get("device") == device
                    and obj["key"] not in occupied and (kind != "interface" or obj["attrs"].get("type") == "1000base-t"))

    def test_two_and_four_floor_buildings_size_each_closet_from_actual_demand(self):
        for staff, floors in ((96, 2), (180, 4)):
            for patching, seed, reserve in (("direct", 42, 0.2), ("panels", 43, 0.4)):
                with self.subTest(staff=staff, patching=patching, seed=seed, reserve=reserve):
                    plan = generate({"headquarters": 1, "headquarters_staff": staff, "branches": {},
                                     "patching": patching, "seed": seed, "reserve_fraction": reserve})
                    self.assertEqual(validate(plan), [])
                    objects = {obj["key"]: obj for obj in plan["objects"]}
                    contract = next(c for c in plan["contracts"] if c["site"] == "site/hq-01")
                    rooms = contract["placement"]["equipment_locations"]
                    self.assertEqual(set(rooms), {str(floor) for floor in range(1, floors+1)})
                    demand, access = Counter(), Counter()
                    for obj in plan["objects"]:
                        if obj["kind"] != "device" or obj["refs"].get("site") != "site/hq-01":
                            continue
                        room = obj["refs"]["location"]
                        if obj["meta"].get("endpoint"):
                            floor = str(objects[room]["meta"]["floor"])
                            self.assertEqual(obj["meta"]["placement"]["cable_origin"], rooms[floor])
                            demand[rooms[floor]] += 1
                        elif obj["refs"].get("role") == "role/access":
                            access[room] += 1
                    usable = math.floor(len(hardware_catalog()["models"]["access"]["access_ports"])*(1-reserve))
                    self.assertEqual(access, {room: max(2, math.ceil(demand[room]/usable)) for room in rooms.values()})

    def test_closet_map_cannot_assign_upper_floor_to_mdf(self):
        self.contract["placement"]["equipment_locations"]["2"] = "location/hq-01"
        self.assertIn("equipment-room-floor", self.codes())

    def test_endpoint_must_reach_its_floor_access_switch_despite_changed_metadata(self):
        endpoint = next(obj for obj in self.plan["objects"] if obj["kind"] == "device" and obj["meta"].get("endpoint")
                        and obj["refs"].get("site") == "site/hq-01" and obj["meta"]["placement"]["floor"] == 2)
        port = endpoint["key"] + "/if/eth0"
        cable = self.cable_at(port)
        side = next(side for side, key in cable["refs"].items() if key != port)
        previous = cable["refs"][side]
        replacement = self.spare_port("device/hq-01/access-01")
        replacement["attrs"]["mode"] = "access"
        replacement["refs"]["untagged_vlan"] = self.objects[previous]["refs"]["untagged_vlan"]
        cable["refs"][side] = replacement["key"]
        endpoint["meta"]["placement"]["cable_origin"] = "location/hq-01"
        for connection in self.contract["required_connections"]:
            if connection.get("a") == previous:
                connection["a"] = replacement["key"]
        self.assertIn("endpoint-serving-room", self.codes())
        self.assertNotIn("required-connection", self.codes())

    def test_panel_must_share_floor_room_with_serving_switch(self):
        self.use_plan(self.panels)
        panel = self.objects["device/hq-01/idf-02-patch-01"]
        panel["refs"].update(rack="rack/hq-01/network-01", location="location/hq-01")
        self.assertIn("endpoint-panel-room", self.codes())

    def test_local_capacity_cannot_be_met_by_spare_switch_on_another_floor(self):
        device = self.objects["device/hq-01/idf-02-access-03"]
        device["refs"].update(location="location/hq-01/idf-03", rack="rack/hq-01/idf-03-network-01")
        self.assertIn("building-access-capacity", self.codes())

    def test_interroom_backbone_rejects_impossible_lengths_and_wrong_media(self):
        port = "device/hq-01/idf-04-access-01/if/TenGigabitEthernet1/1/1"
        for change, code in (({"length": 0}, "backbone-length"), ({"length": 3}, "backbone-length"),
                             ({"type": "cat6"}, "backbone-media")):
            with self.subTest(change=change):
                self.use_plan(self.baseline)
                self.cable_at(port)["attrs"].update(change)
                self.assertIn(code, self.codes())

    def test_missing_floor_management_uplink_is_detected(self):
        cable = self.cable_at("device/hq-01/idf-02-mgmt-01/if/TenGigabitEthernet1/1/1")
        self.plan["objects"].remove(cable)
        self.assertIn("building-management-backbone", self.codes())

    def test_management_copper_cannot_reach_switch_on_another_floor(self):
        device = self.objects["device/hq-01/idf-02-access-01"]
        primary = self.objects[device["refs"]["primary_ip4"]]["refs"]["assigned_object"]
        cable = self.cable_at(primary)
        side = next(side for side, key in cable["refs"].items() if key != primary)
        previous = self.objects[cable["refs"][side]]
        replacement = self.spare_port("device/hq-01/mgmt-01")
        replacement["attrs"]["mode"] = "access"
        replacement["refs"]["untagged_vlan"] = previous["refs"]["untagged_vlan"]
        cable["refs"][side] = replacement["key"]
        self.assertIn("management-room", self.codes())

    def test_power_must_stay_in_device_rack_and_equipment_room(self):
        supply = "device/hq-01/idf-02-access-01/power/PS-1"
        for location in ("location/hq-01", "location/dc-01"):
            with self.subTest(location=location):
                self.use_plan(self.baseline)
                pdu = next(obj for obj in self.plan["objects"] if obj["kind"] == "device" and obj["refs"].get("location") == location
                           and obj["refs"].get("role") == "role/pdu")
                outlet = self.spare_port(pdu["key"], "power_outlet")
                cable = self.cable_at(supply)
                side = next(side for side, key in cable["refs"].items() if key != supply)
                cable["refs"][side] = outlet["key"]
                self.assertIn("power-locality", self.codes())

    def test_feed_panel_must_be_in_its_own_room(self):
        feed = next(obj for obj in self.plan["objects"] if obj["kind"] == "power_feed"
                    and obj["refs"]["rack"] == "rack/hq-01/idf-02-network-01")
        feed["refs"]["power_panel"] = "panel/hq-01/a"
        self.assertIn("power-locality", self.codes())

    def test_rack_names_are_room_local_but_same_room_duplicates_fail(self):
        self.assertEqual(validate(self.plan), [])
        duplicate = deepcopy(self.objects["rack/hq-01/idf-02-network-01"])
        duplicate["key"] += "-duplicate"
        duplicate["attrs"]["asset_tag"] += "-duplicate"
        self.plan["objects"].append(duplicate)
        self.assertIn("duplicate-name", self.codes())

    def test_demand_contract_cannot_weaken_the_recipe(self):
        self.contract["demand"]["peak_mbps"] = 1
        self.assertIn("building-demand", self.codes())

    def test_relocated_top_floor_cannot_hide_behind_edited_room_map(self):
        self.contract["placement"]["equipment_locations"].pop("4")
        for obj in self.plan["objects"]:
            if obj["kind"] == "device" and obj["refs"].get("site") == "site/hq-01" and obj["meta"].get("endpoint"):
                if obj["meta"]["placement"]["floor"] == 4:
                    obj["refs"]["location"] = "location/hq-01/office-09"
                    obj["meta"]["placement"].update(room="location/hq-01/office-09", floor=3,
                                                    cable_origin="location/hq-01/idf-03")
        self.assertTrue({"equipment-room-floor", "building-floor-demand"} <= self.codes())

    def test_report_coverage_and_risers_follow_actual_cables(self):
        text = markdown(self.plan)
        self.assertIn("Equipment-room coverage", text)
        self.assertIn("Equipment-room backbone", text)
        self.assertIn("Representative endpoint access paths", text)
        self.assertNotIn("Example ATM access paths", text)
        endpoints = [obj for obj in self.plan["objects"] if obj["kind"] == "device"
                     and obj["refs"].get("site") == "site/hq-01" and obj["refs"].get("role") == "role/workstation"]
        endpoint = min(endpoints, key=lambda obj: (-self.objects[obj["refs"]["location"]]["meta"]["floor"], obj["key"]))
        cable = self.cable_at(endpoint["key"] + "/if/eth0")
        switch_port = next(self.objects[key] for key in cable["refs"].values()
                           if self.objects[key]["refs"].get("device") != endpoint["key"])
        switch = self.objects[switch_port["refs"]["device"]]
        room = self.objects[switch["refs"]["location"]]
        self.assertIn(endpoint["attrs"]["name"], text)
        self.assertIn(f"{room['attrs']['name']} / {switch['attrs']['name']}", text)
        for obj in self.plan["objects"]:
            if obj["kind"] != "cable" or obj["attrs"].get("type") != "smf":
                continue
            devices = [self.objects[self.objects[obj["refs"][side]]["refs"]["device"]] for side in ("a", "b")]
            if devices[0]["refs"].get("location") != devices[1]["refs"].get("location"):
                self.assertIn(obj["attrs"]["label"], text)
        self.plan["objects"].remove(cable)
        self.assertIn("1 endpoint devices have no complete traced channel", markdown(self.plan))

    def test_estate_growth_preserves_hq_graph_and_staff_change_requires_rebaseline(self):
        for change in ({"branches": {"small": 3}}, {"headquarters": 2}):
            with self.subTest(change=change):
                grown = generate(self.baseline["recipe"] | change, previous=self.baseline)
                self.assertEqual(validate(grown), [])
                current = {obj["key"]: obj for obj in grown["objects"]}
                for obj in self.baseline["objects"]:
                    if "/hq-01/" in obj["key"] or obj["key"].endswith("/hq-01"):
                        self.assertEqual(current[obj["key"]], obj)
        with self.assertRaisesRegex(DesignError, "rebaseline"):
            generate(self.baseline["recipe"] | {"headquarters_staff": 96}, previous=self.baseline)


if __name__ == "__main__":
    unittest.main()
