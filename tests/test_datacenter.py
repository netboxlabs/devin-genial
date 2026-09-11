"""Shared DC construction consumes explicit demand without bank service policy."""

from collections import Counter
from copy import deepcopy
import unittest

from estates.blocks import Site, foundation
from estates.datacenter import build
from estates.model import DesignError, World, canonical


def workload(key="learning-hub", slot=0, instances=15):
    return dict(key=key, slot=slot, network="applications", instances=instances,
                vcpus=4, memory_mb=8192, disk_mb=100000, criticality="tier-2",
                replica_description="local instances; no host-failure guarantee",
                listeners=[dict(key="", name=key, protocol="tcp", ports=[8443]),
                           dict(key="metrics", name=f"{key}-metrics", protocol="tcp", ports=[9090])])


def site():
    # This exercises the extracted block, not a complete school/DC profile.
    world = World({"headquarters": 0, "branches": {}})
    world.reserve_sites(["dc-01"])
    foundation(world)
    return Site(world, "dc-01", "dc", "Shared construction test")


def generate(workloads, wan_peak_mbps=900):
    target = site()
    build(target, workloads=workloads, wan_peak_mbps=wan_peak_mbps, assumptions=["Synthetic workload sizing"])
    return target.w.finish()


class DataCenterTests(unittest.TestCase):
    def test_custom_demand_drives_connected_hosts_services_and_wan(self):
        demand = workload()
        before = canonical(demand)
        plan = generate([demand])
        self.assertEqual(canonical(demand), before)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        vms = [obj for obj in objects.values() if obj["kind"] == "virtual_machine"]
        self.assertEqual(len(vms), 15)
        self.assertEqual({obj["meta"]["service"] for obj in vms}, {"learning-hub"})
        allocations = Counter(vm["refs"]["device"] for vm in vms)
        self.assertEqual(sorted(allocations.values()), [3, 12])
        peers = {}
        for obj in objects.values():
            if obj["kind"] == "cable":
                a, b = obj["refs"]["a"], obj["refs"]["b"]
                self.assertNotIn(a, peers)
                self.assertNotIn(b, peers)
                peers[a], peers[b] = b, a
        for host in allocations:
            upstreams = {objects[peers[f"{host}/if/eth{i}"]]["refs"]["device"] for i in (0, 1)}
            self.assertEqual(len(upstreams), 2)
            self.assertTrue(all(objects[key]["refs"]["role"] == "role/leaf" for key in upstreams))
            self.assertIn(objects[host]["refs"]["rack"], objects)
        for vm in vms:
            host = objects[vm["refs"]["device"]]
            self.assertEqual(vm["refs"]["cluster"], host["refs"]["cluster"])
            address = objects[vm["refs"]["primary_ip4"]]
            interface = objects[address["refs"]["assigned_object"]]
            self.assertEqual(interface["refs"]["virtual_machine"], vm["key"])
            self.assertEqual(vm["attrs"]["disk"], 100000)
            for suffix, port in (("", 8443), ("/metrics", 9090)):
                listener = objects[f"service/{vm['key']}{suffix}"]
                self.assertEqual(listener["refs"], {"virtual_machine": vm["key"], "ipaddresses": [address["key"]]})
                self.assertEqual(listener["attrs"]["ports"], [port])
        circuits = [obj for obj in objects.values() if obj["kind"] == "circuit"]
        self.assertEqual(len(circuits), 4, "900 Mbps with 20% reserve needs two 1 Gbps paths per provider")
        self.assertEqual({obj["attrs"]["commit_rate"] for obj in circuits}, {1000000})

    def test_reordering_explicit_workload_slots_does_not_move_infrastructure(self):
        demands = [workload("learning-hub", 2, 3), workload("research-api", 0, 2)]
        self.assertEqual(canonical(generate(demands)), canonical(generate(list(reversed(demands)))))

    def test_listener_lists_are_owned_by_each_generated_object(self):
        demand = workload(instances=2)
        plan = generate([demand])
        listeners = [obj for obj in plan["objects"] if obj["kind"] == "service" and obj["attrs"]["name"] == demand["key"]]
        listeners[0]["attrs"]["ports"].append(9443)
        self.assertEqual(listeners[1]["attrs"]["ports"], [8443])
        self.assertEqual(demand["listeners"][0]["ports"], [8443])

    def test_invalid_resolved_demand_fails_before_allocating_dc(self):
        cases = [([], "nonempty"), ([workload(), workload()], "unique"),
                 ([workload(), workload("other")], "slot"),
                 ([workload() | {"slot": True}], "slot"),
                 ([workload() | {"network": "users"}], "network"),
                 ([workload() | {"instances": 0}], "instances"),
                 ([workload() | {"vcpus": 1024}], "budget"),
                 ([workload() | {"memory_mb": 1000000}], "budget"),
                 ([workload() | {"disk_mb": 8000001}], "budget"),
                 ([workload() | {"listeners": []}], "listeners"),
                 ([workload() | {"unexpected": True}], "exactly")]
        for field, value, error in (("key", "../outside", "keys"), ("ports", [True], "ports"),
                                    ("ports", [65536], "ports"), ("ports", [443, 443], "ports"),
                                    ("protocol", "http", "protocol")):
            bad = workload()
            bad["listeners"][0][field] = value
            cases.append(([bad], error))
        for demands, message in cases:
            with self.subTest(demands=demands):
                target = site()
                original = canonical(target.w.finish())
                frozen = deepcopy(demands)
                with self.assertRaisesRegex(DesignError, message):
                    build(target, workloads=demands, wan_peak_mbps=100, assumptions=[])
                self.assertEqual(canonical(target.w.finish()), original)
                self.assertEqual(demands, frozen)

    def test_capacity_and_invalid_wan_demand_fail_explicitly(self):
        for instances in (193, 10**400):
            with self.subTest(instances=instances), self.assertRaisesRegex(DesignError, "16-host reservation"):
                generate([workload(instances=instances)])
        for demand in (-1, True, 1.5):
            with self.subTest(demand=demand), self.assertRaisesRegex(DesignError, "wan_peak_mbps"):
                generate([workload()], wan_peak_mbps=demand)

    def test_distinct_keys_cannot_collapse_to_same_scoped_display_identity(self):
        for a, b in (("api-1", "api-01"), ("compute", "cp"), ("host", "h")):
            with self.subTest(keys=(a, b)):
                target = site()
                before = canonical(target.w.finish())
                with self.assertRaisesRegex(DesignError, "collides"):
                    build(target, workloads=[workload(a, 0, 1), workload(b, 1, 1)], wan_peak_mbps=100, assumptions=[])
                self.assertEqual(canonical(target.w.finish()), before)
        demand = workload()
        demand["listeners"][1]["name"] = demand["listeners"][0]["name"]
        with self.assertRaisesRegex(DesignError, "listener names must be unique"):
            generate([demand])


if __name__ == "__main__":
    unittest.main()
