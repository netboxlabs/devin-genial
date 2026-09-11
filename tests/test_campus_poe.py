"""Power-limited access growth must retain ports without wasting copper holes."""

from copy import deepcopy
import unittest

from estates.campus import _reserve_endpoints
from estates.model import DesignError, World


def allocate(aps, desks=(), previous=None, hardware="access"):
    world = World({})
    if previous:
        world.reservations = deepcopy(previous)
    members = []
    for alias, keys in (("ap", aps), ("endpoint", desks)):
        for key in keys:
            world.add("device", key, {}, {"device_type": f"hardware/{alias}"})
            members.append((key, "wireless" if alias == "ap" else "users"))
    slots = _reserve_endpoints(world, "test-closet", members, world.catalog["models"][hardware], 19)
    return slots, world.reservations


class CampusPoeTests(unittest.TestCase):
    def test_power_binds_before_copper_and_wired_demand_can_fill_the_hole(self):
        aps = [f"ap-{i:02}" for i in range(25)]
        slots, _ = allocate(aps, ["desk"])
        self.assertEqual(slots[aps[-1]], 38)  # Third switch, first port.
        self.assertEqual(slots["desk"], 24)  # First switch, thirteenth port.
        self.assertEqual(len(set(slots.values())), 26)

    def test_earlier_new_pd_cannot_consume_incumbent_power_or_move_its_port(self):
        aps = [f"z-ap-{i:02}" for i in range(24)]
        old, previous = allocate(aps)
        grown, _ = allocate(["a-new"] + aps, ["a-desk"], previous)
        self.assertEqual({key: grown[key] for key in old}, old)
        self.assertEqual(grown["a-new"], 38)
        self.assertEqual(grown["a-desk"], 24)

    def test_wired_only_capacity_is_not_limited_to_twelve_ports_per_switch(self):
        slots, _ = allocate([], [f"desk-{i}" for i in range(38)])
        self.assertEqual(set(slots.values()), set(range(38)))

    def test_fixed_supply_ex_uses_its_405w_budget_without_claiming_redundancy(self):
        slots, _ = allocate([f"ap-{i}" for i in range(27)], hardware="inherited-access")
        self.assertEqual(slots["ap-25"], 25)
        self.assertEqual(slots["ap-26"], 38)

    def test_invalid_retained_reservations_fail_instead_of_moving_equipment(self):
        aps = [f"ap-{i}" for i in range(26)]
        previous = {"test-closet": {key: slot for slot, key in enumerate(aps)}}
        with self.assertRaisesRegex(DesignError, "retained PoE reservations"):
            allocate(aps, previous=previous)
        with self.assertRaisesRegex(DesignError, "removed or moved"):
            allocate([], previous={"test-closet": {"old": 0}})


if __name__ == "__main__":
    unittest.main()
