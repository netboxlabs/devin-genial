"""Resolved replica demand must create real, capacity-bounded failure domains."""

from collections import Counter, defaultdict
from copy import deepcopy
import unittest

from estates.blocks import Site, foundation
from estates.datacenter import build
from estates.model import DesignError, World, canonical


def demand(instances=50, replicas=2, failure_domain="host", **changes):
    return dict(key="orders", slot=0, network="applications", instances=instances,
                vcpus=4, memory_mb=8192, disk_mb=100000, criticality="tier-1",
                replica_description="Each synthetic shard replica represents its full workload capacity",
                listeners=[dict(key="", name="orders", protocol="tcp", ports=[8443])],
                replicas=replicas, failure_domain=failure_domain) | changes


def target(previous=None, reserve_fraction=0.2):
    # Exercise the shared construction boundary, not a complete industry profile.
    world = World({"headquarters": 0, "branches": {}, "reserve_fraction": reserve_fraction}, previous)
    world.reserve_sites(["dc-01"])
    foundation(world)
    return Site(world, "dc-01", "dc", "Replica construction test")


def generate(workload, *, previous=None, include_equipment=True, reserve_fraction=0.2):
    site = target(previous, reserve_fraction)
    build(site, workloads=[workload], wan_peak_mbps=100, assumptions=[], include_equipment=include_equipment)
    return site.w.finish()


class ReplicaBuilderTests(unittest.TestCase):
    def test_complete_replica_groups_use_distinct_hosts_with_resource_reserve(self):
        workload = demand()
        frozen = deepcopy(workload)
        plan = generate(workload)
        self.assertEqual(workload, frozen)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        vms = [obj for obj in objects.values() if obj["kind"] == "virtual_machine"]
        allocations = Counter(vm["refs"]["device"] for vm in vms)
        self.assertEqual(sorted(allocations.values()), [1, 1, 12, 12, 12, 12])
        groups = defaultdict(list)
        for vm in vms:
            ordinal = int(vm["key"].rsplit("/", 1)[1]) - 1
            groups[ordinal // 2].append(vm)
            self.assertEqual(vm["meta"]["replica_group"], ordinal // 2 + 1)
            self.assertEqual(vm["meta"]["replica_lane"], ordinal % 2)
            self.assertEqual(vm["meta"]["failure_domain"], "host")
            self.assertIn(f"service/{vm['key']}", objects)
        self.assertEqual(len(groups), 25)
        for replicas in groups.values():
            self.assertEqual(len({vm["refs"]["device"] for vm in replicas}), 2)
        for host, count in allocations.items():
            resources = objects[host]["meta"]["resources"]
            for field in ("vcpus", "memory_mb", "disk_mb"):
                self.assertLessEqual(count * workload[field], resources[field] * 0.8)
        contract = plan["contracts"][0]
        self.assertEqual(contract["required_services"]["orders"], 50)
        policy = contract["workload_policies"]["orders"]
        self.assertEqual((policy["groups"], policy["replicas"], policy["failure_domain"]), (25, 2, "host"))
        self.assertEqual(set(policy["hosts"]), set(allocations))
        self.assertIn("not executed or verified", policy["guarantee"])

    def test_memory_and_disk_each_drive_lane_host_count(self):
        for changes in ({"memory_mb": 100000}, {"disk_mb": 3000000}):
            with self.subTest(changes=changes):
                plan = generate(demand(instances=6, **changes))
                allocations = Counter(obj["refs"]["device"] for obj in plan["objects"] if obj["kind"] == "virtual_machine")
                self.assertEqual(sorted(allocations.values()), [1, 1, 2, 2])

    def test_exact_decimal_disk_boundary_fits_and_one_mb_over_fails(self):
        for reserve, budget in ((0.1, 7200000), (0.2, 6400000), (0.25, 6000000), (0.3, 5600000),
                                (0.32, 5440000), (0.33, 5360000), (0.34, 5280000), (0.4, 4800000)):
            with self.subTest(reserve=reserve):
                plan = generate(demand(instances=2, disk_mb=budget), reserve_fraction=reserve)
                placements = Counter(obj["refs"]["device"] for obj in plan["objects"] if obj["kind"] == "virtual_machine")
                self.assertEqual(sorted(placements.values()), [1, 1])
                packed = generate(demand(instances=6, disk_mb=budget // 2), reserve_fraction=reserve)
                placements = Counter(obj["refs"]["device"] for obj in packed["objects"] if obj["kind"] == "virtual_machine")
                self.assertEqual(sorted(placements.values()), [1, 1, 2, 2])
                site = target(reserve_fraction=reserve)
                before = canonical(site.w.finish())
                with self.assertRaisesRegex(DesignError, "disk_mb budget"):
                    build(site, workloads=[demand(instances=2, disk_mb=budget + 1)], wan_peak_mbps=100, assumptions=[])
                self.assertEqual(canonical(site.w.finish()), before)

    def test_rack_loss_preserves_replica_and_a_physical_fabric_path(self):
        plan = generate(demand(instances=9, replicas=3, failure_domain="rack"))
        objects = {obj["key"]: obj for obj in plan["objects"]}
        groups, links = defaultdict(list), defaultdict(set)
        for obj in objects.values():
            if obj["kind"] == "virtual_machine":
                ordinal = int(obj["key"].rsplit("/", 1)[1]) - 1
                groups[ordinal // 3].append(obj["refs"]["device"])
            if obj["kind"] == "cable":
                a, b = (objects[obj["refs"][end]] for end in ("a", "b"))
                if (a["kind"] == b["kind"] == "interface" and
                        all("vlan/dc-01/applications" in port["refs"].get("tagged_vlans", []) for port in (a, b))):
                    left, right = a["refs"]["device"], b["refs"]["device"]
                    links[left].add(right)
                    links[right].add(left)
        for hosts in groups.values():
            self.assertEqual(len({objects[host]["refs"]["rack"] for host in hosts}), 3)
        racks = {obj["key"] for obj in objects.values() if obj["kind"] == "rack"}
        for failed_rack in racks:
            lost = {obj["key"] for obj in objects.values()
                    if obj["kind"] == "device" and obj["refs"].get("rack") == failed_rack}
            for hosts in groups.values():
                surviving = set(hosts) - lost
                self.assertGreaterEqual(len(surviving), 2)
                for host in surviving:
                    visited, pending = {host}, [host]
                    while pending:
                        for peer in links[pending.pop()] - visited - lost:
                            visited.add(peer)
                            pending.append(peer)
                    roles = {objects[device]["refs"]["role"] for device in visited}
                    self.assertIn("role/wan-edge", roles, f"{host} lost WAN path with {failed_rack}")
                    self.assertIn("role/spine", roles, f"{host} lost fabric with {failed_rack}")

    def test_growth_keeps_old_replica_placements_and_connected_identities(self):
        before = generate(demand(instances=26, failure_domain="rack"))
        after = generate(demand(instances=50, failure_domain="rack"), previous=before)
        objects = {obj["key"]: obj for obj in after["objects"]}
        for obj in before["objects"]:
            current = objects[obj["key"]]
            if obj["kind"] in ("virtual_machine", "ip_address", "cable"):
                self.assertEqual(obj, current)
            if obj["kind"] == "device":
                self.assertEqual(obj["refs"].get("rack"), current["refs"].get("rack"))
                self.assertEqual(obj["attrs"].get("position"), current["attrs"].get("position"))

    def test_omitting_equipment_demonstrations_retains_serial_console_access(self):
        plan = generate(demand(instances=2), include_equipment=False)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        self.assertFalse(any(obj["kind"] in ("cooling_source", "virtual_chassis", "device_bay") for obj in objects.values()))
        self.assertFalse(any(obj["kind"] == "device" and obj["refs"]["role"] in ("role/laboratory", "role/stack")
                             for obj in objects.values()))
        consoles = [obj for obj in objects.values() if obj["kind"] == "console_port"]
        self.assertTrue(consoles)
        attachments = set()
        for obj in objects.values():
            if obj["kind"] == "cable":
                a, b = (objects[obj["refs"][end]] for end in ("a", "b"))
                if {a["kind"], b["kind"]} == {"console_port", "console_server_port"}:
                    attachments.add(a["key"] if a["kind"] == "console_port" else b["key"])
        for port in consoles:
            device = objects[port["refs"]["device"]]
            if device["refs"]["role"] in ("role/spine", "role/leaf", "role/wan-edge") and port["attrs"]["type"] == "rj-45":
                self.assertIn(port["key"], attachments)

    def test_default_policy_preserves_legacy_graph_and_contract(self):
        explicit = demand(instances=15, replicas=1, failure_domain="none")
        implicit = {key: value for key, value in explicit.items() if key not in ("replicas", "failure_domain")}
        plan = generate(implicit)
        self.assertEqual(canonical(plan), canonical(generate(explicit)))
        self.assertNotIn("workload_policies", plan["contracts"][0])
        self.assertTrue(all("replica_lane" not in obj["meta"] for obj in plan["objects"]))

    def test_invalid_replication_and_actual_lane_capacity_fail_before_allocation(self):
        cases = [(demand(replicas=True), "replicas"), (demand(replicas=0), "replicas"),
                 (demand(replicas=5), "replicas"), (demand(failure_domain=[]), "failure_domain"),
                 (demand(failure_domain="site"), "failure_domain"), (demand(replicas=1), "at least 2"),
                 (demand(failure_domain="none"), "require host or rack"),
                 (demand(instances=1), "divisible"), (demand(instances=3), "divisible"),
                 (demand(instances=183, replicas=3), "16-host reservation")]
        for workload, message in cases:
            with self.subTest(workload=workload):
                site = target()
                before = canonical(site.w.finish())
                frozen = deepcopy(workload)
                with self.assertRaisesRegex(DesignError, message):
                    build(site, workloads=[workload], wan_peak_mbps=100, assumptions=[])
                self.assertEqual(canonical(site.w.finish()), before)
                self.assertEqual(workload, frozen)
        site = target()
        before = canonical(site.w.finish())
        with self.assertRaisesRegex(DesignError, "include_equipment"):
            build(site, workloads=[demand()], wan_peak_mbps=100, assumptions=[], include_equipment="false")
        self.assertEqual(canonical(site.w.finish()), before)


if __name__ == "__main__":
    unittest.main()
