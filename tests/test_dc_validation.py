"""Bank DC omissions must not remove the independent baseline obligations."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.validate import validate


class DCValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 0, "branches": {"small": 1}})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}
        self.contract = next(c for c in self.plan["contracts"] if c["site"] == "site/dc-01")
        self.host = "device/dc-01/identity-host-01"

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def same_supply(self):
        """Use a real spare outlet; the mutation keeps physical occupancy valid."""
        port = self.host + "/power/PSU2"
        cable = next(o for o in self.objects.values() if o["kind"] == "cable" and port in o["refs"].values())
        occupied = {key for o in self.objects.values() if o["kind"] == "cable" for key in o["refs"].values()}
        outlet = next(key for key, o in self.objects.items() if o["kind"] == "power_outlet" and
                      o["refs"].get("device") == "device/dc-01/pdu-compute-01-a" and key not in occupied)
        field = next(field for field, key in cable["refs"].items() if self.objects[key]["kind"] == "power_outlet")
        cable["refs"][field] = outlet

    def test_baseline_passes_without_mutation_and_allows_unrelated_planned_inventory(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(self.plan, self.baseline)
        self.plan["objects"].append({"key": "planned-cluster", "kind": "cluster",
                                     "attrs": {"name": "Future compute cluster", "status": "planned"},
                                     "refs": deepcopy(self.objects["cluster/dc-01"]["refs"]), "meta": {}})
        # Planning inventory still needs its accountable technical desk; its
        # presence must not turn this unrelated cluster into active DC capacity.
        assignment = deepcopy(self.objects["contact-assignment/cluster/dc-01"])
        assignment["key"] = "contact-assignment/planned-cluster"
        assignment["refs"]["object"] = "planned-cluster"
        self.plan["objects"].append(assignment)
        self.assertEqual(validate(self.plan), [])

    def test_same_supply_is_one_semantic_finding_and_cannot_weaken_or_omit_contract(self):
        self.same_supply()
        findings = validate(self.plan)
        self.assertEqual([(f["code"], f["object"]) for f in findings], [("power-redundancy", self.host)])
        power = next(p for p in self.contract["power_redundancy"] if p["device"] == self.host)
        power["min_distinct_pdus"] = 1
        self.assertTrue({"dc-power-contract", "power-redundancy"} <= self.codes())
        self.contract["power_redundancy"].remove(power)
        self.assertTrue({"dc-power-contract", "power-redundancy"} <= self.codes())

    def test_omitting_entire_dc_contract_does_not_skip_power_checks(self):
        self.same_supply()
        self.plan["contracts"].remove(self.contract)
        self.assertTrue({"dc-contract", "dc-power-contract", "power-redundancy"} <= self.codes())

    def test_omitting_rack_contract_and_cables_cannot_hide_an_unpowered_host(self):
        self.objects[self.host]["refs"].pop("rack")
        for field in ("position", "face"):
            self.objects[self.host]["attrs"].pop(field)
        self.contract["power_redundancy"] = [p for p in self.contract["power_redundancy"] if p["device"] != self.host]
        ports = {key for key, o in self.objects.items() if o["kind"] == "power_port" and o["refs"].get("device") == self.host}
        self.plan["objects"] = [o for o in self.plan["objects"] if not (
            o["kind"] == "cable" and ports.intersection(o["refs"].values()))]
        self.assertTrue({"dc-device-rack", "dc-power-contract", "power-path", "power-redundancy"} <= self.codes())

    def test_required_power_paths_need_active_feeds_pdus_and_connected_cables(self):
        port = self.host + "/power/PSU1"
        inlet = "device/dc-01/pdu-compute-01-a/power/Input"
        cables = [key for key, o in self.objects.items() if o["kind"] == "cable" and
                  (port in o["refs"].values() or inlet in o["refs"].values())]
        cases = [("feed/rack/dc-01/compute-01/a", "offline"),
                 ("device/dc-01/pdu-compute-01-a", "offline")]
        cases.extend((cable, "planned") for cable in cables)
        for key, status in cases:
            with self.subTest(key=key):
                original = self.objects[key]["attrs"]["status"]
                self.objects[key]["attrs"]["status"] = status
                self.assertTrue({"power-path-status", "power-redundancy"} <= self.codes())
                self.objects[key]["attrs"]["status"] = original

    def test_actual_compute_hosts_are_checked_when_contract_omits_them(self):
        self.objects["vm/dc-01/identity/001"]["attrs"]["vcpus"] = 1024
        self.contract["compute"][0]["hosts"].remove(self.host)
        self.assertTrue({"dc-compute-contract", "compute-capacity"} <= self.codes())
        self.contract["compute"] = []
        self.assertTrue({"dc-compute-contract", "compute-capacity"} <= self.codes())

    def test_declared_host_capacity_and_reserve_cannot_weaken_policy(self):
        self.objects["vm/dc-01/identity/001"]["attrs"]["vcpus"] = 48
        self.contract["compute"][0]["reserve_fraction"] = 0
        self.contract["reserve_fraction"] = 0
        self.objects[self.host]["meta"]["resources"]["vcpus"] = 1024
        self.assertTrue({"dc-compute-contract", "dc-host-resources", "compute-capacity"} <= self.codes())

    def test_required_compute_and_services_need_active_infrastructure(self):
        for key, expected in ((self.host, "compute-host-status"),
                              ("cluster/dc-01", "compute-cluster-status"),
                              ("vm/dc-01/identity/001", "service-count")):
            with self.subTest(key=key):
                self.objects[key]["attrs"]["status"] = "offline"
                self.assertTrue({expected, "service-count"} <= self.codes())
                self.objects[key]["attrs"]["status"] = "active"

    def test_required_uplink_needs_connected_cable_enabled_ports_and_active_peers(self):
        interface = self.host + "/if/eth0"
        cable = next(o for o in self.objects.values() if o["kind"] == "cable" and interface in o["refs"].values())
        peer = next(key for key in cable["refs"].values() if key != interface)
        for key, field, value in ((cable["key"], "status", "planned"),
                                  (interface, "enabled", False), (peer, "enabled", False),
                                  (self.objects[peer]["refs"]["device"], "status", "offline")):
            with self.subTest(key=key):
                original = self.objects[key]["attrs"][field]
                self.objects[key]["attrs"][field] = value
                self.assertTrue({"uplink-path-status", "uplink-redundancy"} <= self.codes())
                self.objects[key]["attrs"][field] = original

    def test_required_service_primary_interface_must_be_enabled(self):
        self.objects["vm/dc-01/identity/001/eth0"]["attrs"]["enabled"] = False
        self.assertTrue({"service-interface", "service-count"} <= self.codes())

    def test_recipe_service_obligations_survive_missing_contract_entries(self):
        self.plan["objects"] = [o for o in self.plan["objects"] if not (
            o["kind"] == "service" and o["refs"].get("virtual_machine", "").startswith("vm/dc-01/identity/"))]
        self.contract["required_services"].pop("identity")
        self.assertTrue({"dc-service-contract", "service-endpoint"} <= self.codes())
        self.contract["required_services"] = {}
        self.assertTrue({"dc-service-contract", "service-endpoint"} <= self.codes())


if __name__ == "__main__":
    unittest.main()
