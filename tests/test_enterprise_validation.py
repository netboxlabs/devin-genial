"""Mutation evidence for independent DC obligations, not builder assertions."""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.validate import validate


class EnterpriseValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({
            "profile": "enterprise-data-center", "namespace": "dc-validation",
            "name": "Independent DC validation", "seed": 42, "as_of": "2026-09-09",
            "data_centers": 1, "wan_peak_mbps": 900, "reserve_fraction": 0.2,
            "workloads": [{"key": "customer-portal", "groups": 14, "replicas": 2,
                "failure_domain": "rack", "network": "applications", "vcpus": 4,
                "memory_mb": 8192, "disk_mb": 100000, "criticality": "tier-1",
                "listeners": [{"key": "", "name": "customer-portal", "protocol": "tcp", "ports": [8443]},
                              {"key": "metrics", "name": "metrics", "protocol": "tcp", "ports": [9090]}]}]})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {o["key"]: o for o in self.plan["objects"]}

    def assertFinding(self, code):
        self.assertIn(code, {f["code"] for f in validate(self.plan)})

    def vm(self, ordinal=1):
        return self.objects[f"vm/dc-01/customer-portal/{ordinal:03}"]

    def test_requested_graph_passes_without_bank_family_obligations(self):
        self.assertEqual(validate(self.plan), [])
        self.assertNotIn("atm-switch", {o.get("meta", {}).get("service") for o in self.plan["objects"]})

    def test_deleting_whole_workload_and_contract_cannot_hide_demand(self):
        self.plan["objects"] = [o for o in self.plan["objects"] if o["kind"] != "virtual_machine"]
        self.plan["contracts"] = []
        self.assertFinding("dc-workload-inventory")

    def test_placement_uses_actual_replica_hosts_and_racks(self):
        with self.subTest(domain="host"):
            self.vm(2)["refs"]["device"] = self.vm()["refs"]["device"]
            self.assertFinding("dc-replica-diversity")
        self.setUp()
        with self.subTest(domain="rack"):
            first = self.objects[self.vm()["refs"]["device"]]
            second = self.objects[self.vm(2)["refs"]["device"]]
            second["refs"]["rack"] = first["refs"]["rack"]
            self.assertFinding("dc-replica-diversity")

    def test_resources_come_from_recipe_and_reference_host(self):
        self.vm()["attrs"]["memory"] += 1
        self.assertFinding("dc-workload-resources")
        self.setUp()
        host = self.objects[self.vm()["refs"]["device"]]
        host["meta"]["resources"]["memory_mb"] *= 10
        self.assertFinding("dc-host-resources")

    def test_host_budget_survives_removed_compute_contract(self):
        host = self.vm()["refs"]["device"]
        for obj in self.plan["objects"]:
            if obj["kind"] == "virtual_machine":
                obj["refs"]["device"] = host
        self.plan["contracts"] = []
        self.assertFinding("dc-host-capacity")

    def test_listener_and_interface_obligations_are_not_self_declared(self):
        service = next(o for o in self.plan["objects"] if o["kind"] == "service" and o["refs"].get("virtual_machine") == self.vm()["key"])
        service["attrs"]["ports"] = [443]
        self.assertFinding("dc-workload-listener")
        self.setUp()
        address = self.objects[self.vm()["refs"]["primary_ip4"]]
        self.objects[address["refs"]["assigned_object"]]["attrs"]["enabled"] = False
        self.assertFinding("dc-workload-address")

    def test_nonactive_host_or_cluster_is_not_available(self):
        for field in ("device", "cluster"):
            with self.subTest(field=field):
                self.setUp()
                self.objects[self.vm()["refs"][field]]["attrs"]["status"] = "planned"
                self.assertFinding("dc-workload-placement")

    def test_cable_status_remains_an_obligation_without_uplink_contract(self):
        host = self.vm()["refs"]["device"]
        cable = next(o for o in self.plan["objects"] if o["kind"] == "cable" and
                     f"{host}/if/eth0" in o["refs"].values())
        cable["attrs"]["status"] = "planned"
        self.plan["contracts"] = []
        self.assertFinding("dc-uplink-path")

    def test_power_path_and_panel_diversity_survive_removed_contract(self):
        host = self.vm()["refs"]["device"]
        port = next(o["key"] for o in self.plan["objects"] if o["kind"] == "power_port" and o["refs"].get("device") == host)
        cable = next(o for o in self.plan["objects"] if o["kind"] == "cable" and port in o["refs"].values())
        cable["attrs"]["status"] = "planned"
        self.plan["contracts"] = []
        self.assertFinding("dc-power-path")
        self.setUp()
        panels = [o["key"] for o in self.plan["objects"] if o["kind"] == "power_panel"]
        for obj in self.plan["objects"]:
            if obj["kind"] == "power_feed":
                obj["refs"]["power_panel"] = panels[0]
        self.plan["contracts"] = []
        self.assertFinding("dc-power-diversity")

    def test_wan_uses_connected_committed_capacity_not_contract_total(self):
        circuit = next(o for o in self.plan["objects"] if o["kind"] == "circuit")
        circuit["attrs"]["commit_rate"] = 100000
        self.assertFinding("dc-wan-path")
        self.assertFinding("dc-wan-capacity")

    def test_power_obligations_use_actual_hardware_when_metadata_is_stripped_or_spoofed(self):
        for metadata in ({}, {"hardware": "endpoint"}):
            with self.subTest(metadata=metadata):
                self.setUp()
                host = self.vm()["refs"]["device"]
                self.objects[host]["meta"] = metadata
                self.plan["contracts"] = []
                port = f"{host}/power/PSU2"
                cable = next(o for o in self.plan["objects"] if o["kind"] == "cable" and port in o["refs"].values())
                cable["attrs"]["status"] = "planned"
                self.assertFinding("dc-power-path")
                self.assertFinding("dc-power-diversity")

    def test_unaddressed_gateways_are_not_healthy(self):
        gateways = {o["key"] for o in self.plan["objects"] if o["kind"] == "interface" and
                    o["attrs"].get("type") == "virtual" and o["refs"].get("untagged_vlan") == "vlan/dc-01/applications"}
        self.plan["objects"] = [o for o in self.plan["objects"] if not
                                (o["kind"] == "ip_address" and o["refs"].get("assigned_object") in gateways)]
        self.assertFinding("dc-gateway-inventory")

    def test_moved_rack_requires_longer_physical_cables(self):
        rack = self.objects[self.objects[self.vm()["refs"]["device"]]["refs"]["rack"]]
        rack["meta"]["position_m"][0] += 50
        self.assertFinding("dc-cable-geometry")

    def test_prefix_policy_is_independent_of_containing_addresses(self):
        prefix = self.objects["prefix/dc-01/applications"]
        prefix["attrs"]["prefix"] = prefix["attrs"]["prefix"].replace("/20", "/24")
        self.assertFinding("dc-prefix-policy")

    def test_unhashable_recipe_choices_report_findings(self):
        for field in ("network", "failure_domain", "protocol"):
            with self.subTest(field=field):
                self.setUp()
                workload = self.plan["recipe"]["workloads"][0]
                target = workload["listeners"][0] if field == "protocol" else workload
                target[field] = []
                self.assertFinding("dc-recipe")

    def test_replica_policy_metadata_cannot_mislead_the_report(self):
        for field, value in (("failure_domain", "none"), ("replicas", 99), ("replica_group", True)):
            with self.subTest(field=field):
                self.setUp()
                self.vm()["meta"][field] = value
                self.assertFinding("dc-workload-identity")

    def test_address_pool_survives_workloads_at_multiple_sites(self):
        self.assertEqual(validate(generate({"profile": "enterprise-data-center", "data_centers": 2})), [])

    def test_exact_compute_budget_passes_and_one_mb_over_fails_both_checks(self):
        for reserve, budget in ((0.1, 7200000), (0.2, 6400000), (0.25, 6000000), (0.3, 5600000),
                                (0.32, 5440000), (0.33, 5360000), (0.34, 5280000), (0.4, 4800000)):
            with self.subTest(reserve=reserve):
                plan = generate({"profile": "enterprise-data-center", "data_centers": 1,
                    "reserve_fraction": reserve,
                    "workloads": [{"key": "volume-shards", "groups": 17, "disk_mb": budget // 8}]})
                self.assertEqual(validate(plan), [])
                first = next(o for o in plan["objects"] if o["kind"] == "virtual_machine")
                first["attrs"]["disk"] += 1
                codes = {finding["code"] for finding in validate(plan)}
                self.assertTrue({"compute-capacity", "dc-host-capacity"} <= codes, codes)

    def test_unsupported_demands_fail_before_expanding_expected_inventory(self):
        for field, value in (("groups", 513), ("groups", 10**100), ("replicas", 5), ("vcpus", 65),
                             ("memory_mb", 262145), ("disk_mb", 8000001), ("key", ""), ("key", "../outside")):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.plan["recipe"]["workloads"][0][field] = value
                findings = validate(self.plan)
                self.assertEqual([f["code"] for f in findings], ["dc-recipe"])
        for field, value in (("workloads", []), ("workloads", [self.baseline["recipe"]["workloads"][0]] * 17),
                             ("wan_peak_mbps", 0), ("wan_peak_mbps", 16001)):
            with self.subTest(field=field):
                self.setUp()
                self.plan["recipe"][field] = value
                self.assertEqual([f["code"] for f in validate(self.plan)], ["dc-recipe"])

    def test_management_paths_remain_required_without_contracts(self):
        for path in ("dedicated", "uplink"):
            with self.subTest(path=path):
                self.setUp()
                host = self.objects[self.vm()["refs"]["device"]]
                primary = self.objects[host["refs"]["primary_ip4"]]["refs"]["assigned_object"]
                if path == "dedicated":
                    cable = next(o for o in self.plan["objects"] if o["kind"] == "cable" and primary in o["refs"].values())
                    cable["attrs"]["status"] = "planned"
                    expected = "dc-management-path"
                else:
                    manager = next(o for o in self.plan["objects"] if o["kind"] == "device" and o["refs"].get("role") == "role/management")
                    uplink = f"{manager['key']}/if/TenGigabitEthernet1/1/1"
                    self.plan["objects"] = [o for o in self.plan["objects"] if not (o["kind"] == "cable" and uplink in o["refs"].values())]
                    expected = "dc-management-uplink"
                self.plan["contracts"] = []
                self.assertFinding(expected)

    def test_console_path_is_required_without_contracts(self):
        console = next(o["key"] for o in self.plan["objects"] if o["kind"] == "console_port" and
                       self.objects[o["refs"]["device"]]["refs"].get("role") == "role/spine")
        cable = next(o for o in self.plan["objects"] if o["kind"] == "cable" and console in o["refs"].values())
        cable["attrs"]["status"] = "planned"
        self.plan["contracts"] = []
        self.assertFinding("dc-console-path")


if __name__ == "__main__":
    unittest.main()
