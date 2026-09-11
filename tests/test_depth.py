"""Mutate generated intent to prove the physical and operational checks matter."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.validate import validate


class DepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 1, "branches": {"small": 1, "large": 1}})
        cls.panels = generate({"patching": "panels", "headquarters": 0, "branches": {"small": 1}})

    def mutate(self, change, code):
        plan = deepcopy(self.baseline)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        change(objects)
        self.assertIn(code, {f["code"] for f in validate(plan)})

    def test_generated_depth_is_coherent(self):
        self.assertEqual(validate(self.baseline), [])

    def test_room_cannot_be_at_another_site(self):
        self.mutate(lambda o: o["device/br-s0001/desk-001"]["refs"].update(location="location/dc-01"), "location-site")

    def test_location_parent_cannot_cycle(self):
        self.mutate(lambda o: o["location/br-s0001"]["refs"].update(parent="location/br-s0001"), "containment-cycle")

    def test_consistent_rack_and_members_cannot_move_into_public_lobby(self):
        def move(objects):
            rack, lobby = "rack/br-s0001/network-01", "location/br-s0001/atm-lobby"
            objects[rack]["refs"]["location"] = lobby
            for obj in objects.values():
                if obj["kind"] == "device" and obj["refs"].get("rack") == rack:
                    obj["refs"]["location"] = lobby
        self.mutate(move, "equipment-room")

    def test_room_cannot_be_reparented_without_its_floor_geometry(self):
        self.mutate(lambda o: o["location/hq-01/office-01"]["refs"].update(
            parent="location/hq-01/floor-02"), "location-floor")

    def test_endpoint_height_must_remain_on_its_assigned_floor(self):
        self.mutate(lambda o: o["device/br-s0001/desk-001"]["meta"]["placement"].update(
            position_m=[26, 7, 4.8]), "endpoint-floor")

    def test_atm_cannot_be_in_a_private_office(self):
        self.mutate(lambda o: o["device/br-s0001/atm-001"]["refs"].update(location="location/br-s0001/office-01"), "endpoint-room")

    def test_room_capacity_is_checked_from_occupants(self):
        self.mutate(lambda o: o["device/br-l0001/desk-017"]["refs"].update(location="location/br-l0001/office-01"), "room-capacity")

    def test_copper_route_is_neither_overlong_nor_impossibly_short(self):
        def length(objects, value):
            cable = next(o for o in objects.values() if o["kind"] == "cable" and
                         "device/br-s0001/atm-001/if/eth0" in o["refs"].values())
            cable["attrs"]["length"] = value
        self.mutate(lambda o: length(o, 250), "copper-channel-length")
        self.mutate(lambda o: length(o, 1), "endpoint-route-length")

    def test_listening_address_is_required_and_owned(self):
        self.mutate(lambda o: o["service/vm/dc-01/ledger-db/001"]["refs"].pop("ipaddresses"), "service-address")
        self.mutate(lambda o: o["service/vm/dc-01/ledger-db/001"]["refs"].update(
            ipaddresses=["ip/device/br-s0001/atm-001/if/eth0"]), "service-address")

    def test_power_allocation_and_feed_capacity_cannot_be_zeroed_or_overbooked(self):
        self.mutate(lambda o: o["device/br-s0001/access-01/power/PS-1"]["attrs"].update(allocated_draw=0), "power-allocation")
        self.mutate(lambda o: o["device/br-s0001/access-01/power/PS-1"]["attrs"].update(maximum_draw=60), "power-failover")
        self.mutate(lambda o: o["feed/rack/br-s0001/network-01/a"]["attrs"].update(amperage=1), "power-feed-capacity")

    def test_feed_utilization_cannot_inflate_its_electrical_budget(self):
        for value in (0, 101, 200):
            with self.subTest(value=value):
                self.mutate(lambda o: o["feed/rack/br-s0001/network-01/a"]["attrs"].update(
                    max_utilization=value), "power-feed-utilization")

    def test_site_time_zone_follows_its_geography(self):
        self.mutate(lambda o: o["site/dc-01"]["attrs"].update(time_zone="America/Detroit"), "site-time-zone")

    def test_panel_path_includes_room_outlet_and_unused_panel_positions(self):
        plan = generate({"patching": "panels", "headquarters": 0, "branches": {"small": 1}})
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        panel = "device/br-s0001/patch-01"
        outlet = "device/br-s0001/outlet-atm-001"
        self.assertEqual(objects[outlet]["refs"]["location"], "location/br-s0001/atm-lobby")
        ports = [o for o in plan["objects"] if o["refs"].get("device") == panel and o["kind"] == "front_port"]
        self.assertEqual(len(ports), 24)
        links = [o for o in plan["objects"] if o["kind"] == "cable" and o["attrs"].get("label", "").startswith("s0001-atm01-")]
        self.assertEqual(len(links), 3)
        self.assertEqual(sum(o["attrs"]["length"] for o in links), objects["device/br-s0001/atm-001"]["meta"]["access_channel_length_m"])
        objects[outlet]["refs"]["location"] = "location/br-s0001/office-01"
        self.assertIn("outlet-room", {f["code"] for f in validate(plan)})
        plan["objects"].remove(objects[f"{panel}/front/24"])
        self.assertIn("passive-inventory", {f["code"] for f in validate(plan)})

    def test_outlet_cannot_claim_another_endpoint_in_the_same_room(self):
        plan = deepcopy(self.panels)
        objects = {o["key"]: o for o in plan["objects"]}
        outlet = objects["device/br-s0001/outlet-atm-001"]
        outlet["meta"]["serves_endpoint"] = "device/br-s0001/atm-002"
        codes = {f["code"] for f in validate(plan)}
        self.assertIn("endpoint-patch-path", codes)
        self.assertNotIn("outlet-room", codes)

    def test_panel_mode_cannot_bypass_the_disconnected_room_outlet(self):
        plan = deepcopy(self.panels)
        endpoint = "device/br-s0001/atm-001/if/eth0"
        outlet = "device/br-s0001/outlet-atm-001"
        cord = next(o for o in plan["objects"] if o["kind"] == "cable" and endpoint in o["refs"].values())
        plan["objects"].remove(cord)
        horizontal = next(o for o in plan["objects"] if o["kind"] == "cable" and f"{outlet}/rear/1" in o["refs"].values())
        field = next(k for k, v in horizontal["refs"].items() if v == f"{outlet}/rear/1")
        horizontal["refs"][field] = endpoint
        codes = {f["code"] for f in validate(plan)}
        self.assertIn("endpoint-patch-path", codes)
        # Connectivity alone still passes: only the required physical sequence broke.
        self.assertNotIn("required-connection", codes)


if __name__ == "__main__":
    unittest.main()
