"""Shared campus construction preserves connected room-local growth."""

from collections import Counter
import unittest

from estates import campus, places
from estates.blocks import Site, foundation
from estates.model import DesignError, World, canonical


DESIGN = dict(access_hardware="access", upstreams=2, label="access-")
SEGMENTS = ("users", "security", "management")


def target(ordinals, previous=None, *, compact=False, extra_rooms=0):
    world = World({"headquarters_staff": 192}, previous)
    world.reserve_sites(["hq-01"])
    foundation(world)
    site = Site(world, "hq-01", "hq", "Shared campus construction check")
    places.arrange(site, dict(workstations=192, atms=0, aps=16, cameras=8))
    _, _, upstreams = campus.aggregation(site, SEGMENTS, 100, compact=compact)
    endpoints = []
    for ordinal in ordinals:
        segment = "security" if ordinal % 2 else "users"
        key = site.device("endpoint", f"desk-{ordinal:03}", "workstation", racked=False,
                          meta=dict(endpoint=True, network=segment))
        places.place_endpoint(site, key, "workstation", ordinal)
        endpoints.append((key, segment))
    for index in range(extra_rooms):
        room = world.add("location", f"location/hq-01/test-idf-{index:02}",
                         dict(name=f"Test IDF {index}", status="active"),
                         dict(site=site.key, parent="location/hq-01/building"),
                         dict(space_type="equipment_room", position_m=[index, 0, 0], floor=1))
        key = site.device("endpoint", f"room-endpoint-{index:02}", "workstation", racked=False)
        world.obj(key)["refs"]["location"] = room
        world.obj(key)["meta"].update(placement=dict(cable_origin=room), access_channel_length_m=10)
        endpoints.append((key, "users"))
    facts = campus.access(site, endpoints, upstreams, SEGMENTS, DESIGN, compact=compact, stable=True)
    return world.finish(), facts


class CampusTests(unittest.TestCase):
    def test_growth_across_pair_boundary_preserves_ports_addresses_and_rack_positions(self):
        before, facts = target(list(range(1, 39)) + [49, 50])
        self.assertEqual(facts["access_count"], 4)
        frozen = canonical(before)
        after, facts = target([39, 40] + list(range(1, 39)) + [49, 50], before)
        self.assertEqual(facts["access_count"], 6)
        self.assertEqual(canonical(before), frozen)
        old = {obj["key"]: obj for obj in before["objects"]}
        new = {obj["key"]: obj for obj in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        for key, obj in old.items():
            if obj["kind"] in {"ip_address", "cable"}:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                self.assertEqual(new[key]["refs"].get("rack"), obj["refs"].get("rack"), key)
                self.assertEqual(new[key]["attrs"].get("position"), obj["attrs"].get("position"), key)
            if obj["kind"] == "cable":
                for end in obj["refs"].values():
                    self.assertEqual(new[end], old[end], end)
        for scope, allocations in before["reservations"].items():
            for key, slot in allocations.items():
                self.assertEqual(after["reservations"][scope][key], slot)

    def test_real_ports_and_uplinks_serve_endpoints_from_their_actual_equipment_rooms(self):
        plan, facts = target(list(range(1, 41)) + list(range(49, 69)))
        objects = {obj["key"]: obj for obj in plan["objects"]}
        links = {}
        for cable in (obj for obj in objects.values() if obj["kind"] == "cable"):
            a, b = cable["refs"]["a"], cable["refs"]["b"]
            self.assertNotIn(a, links)
            self.assertNotIn(b, links)
            links[a], links[b] = b, a
        occupancy = Counter()
        for endpoint in (obj for obj in objects.values() if obj["meta"].get("endpoint")):
            port = objects[links[f"{endpoint['key']}/if/eth0"]]
            switch = objects[port["refs"]["device"]]
            self.assertEqual(switch["refs"]["location"], endpoint["meta"]["placement"]["cable_origin"])
            self.assertEqual(port["refs"]["untagged_vlan"], f"vlan/hq-01/{endpoint['meta']['network']}")
            occupancy[switch["key"]] += 1
        self.assertEqual(facts["access_usable_ports"], 19)
        self.assertEqual(sorted(occupancy.values()), [1, 1, 10, 10, 19, 19])
        for switch in facts["access_devices"]:
            parents = []
            for port in (obj for obj in objects.values() if obj["kind"] == "interface" and
                         obj["refs"].get("device") == switch and obj["attrs"].get("mode") == "tagged"):
                peer = objects[links[port["key"]]]
                parents.append(peer["refs"]["device"])
                self.assertEqual(port["refs"]["tagged_vlans"], [f"vlan/hq-01/{role}" for role in SEGMENTS])
            self.assertEqual(set(parents), {"device/hq-01/dist-a", "device/hq-01/dist-b"})

    def test_compact_growth_that_needs_another_pair_fails(self):
        target(list(range(1, 39)), compact=True)
        with self.assertRaisesRegex(DesignError, "compact branches support two access switches"):
            target(list(range(1, 40)), compact=True)

    def test_many_independent_serving_rooms_cannot_exceed_distribution_ports(self):
        with self.assertRaisesRegex(DesignError, "40 access switches exceed 38 supported distribution attachments"):
            target([], extra_rooms=20)

    def test_upstream_svi_numbers_come_from_the_actual_vlan(self):
        plan, _ = target([1])
        objects = {obj["key"]: obj for obj in plan["objects"]}
        for side in "ab":
            for role in SEGMENTS:
                vlan = objects[f"vlan/hq-01/{role}"]
                svi = objects[f"device/hq-01/dist-{side}/if/Vlan{vlan['attrs']['vid']}"]
                self.assertEqual(svi["refs"]["untagged_vlan"], vlan["key"])


if __name__ == "__main__":
    unittest.main()
