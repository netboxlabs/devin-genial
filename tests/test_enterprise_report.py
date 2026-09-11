"""Operator walkthroughs must follow observed placement and physical paths."""

from copy import deepcopy
import unittest

from estates.model import canonical, resolve_recipe
from estates.report import markdown


class EnterpriseReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from estates.enterprise import generate

        recipe = resolve_recipe({"profile": "enterprise-data-center", "namespace": "report-dc", "data_centers": 1})
        recipe["workloads"] = [recipe["workloads"][0] | {
            "key": "catalog-api", "groups": 2, "replicas": 2, "failure_domain": "rack",
            "listeners": [{"key": "https", "name": "Catalog browser", "protocol": "tcp", "ports": [8443]},
                          {"key": "metrics", "name": "Catalog metrics", "protocol": "tcp", "ports": [9090]}],
        }]
        cls.plan = generate(recipe)

    def test_custom_workload_has_an_actionable_graph_walkthrough_without_mutation(self):
        before = canonical(self.plan)
        text = markdown(self.plan)
        self.assertEqual(canonical(self.plan), before)
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        vm = min((obj for obj in objects.values() if obj["kind"] == "virtual_machine"), key=lambda obj: obj["key"])
        host = objects[vm["refs"]["device"]]
        site = objects[host["refs"]["site"]]
        rack = objects[host["refs"]["rack"]]
        address = objects[vm["refs"]["primary_ip4"]]
        walkthrough = text.split("## Connected walkthroughs\n", 1)[1].split("## Estate topology\n", 1)[0]
        for expected in (f"Where does catalog-api run at {site['attrs']['name']}?", vm["attrs"]["name"],
                         host["attrs"]["name"], rack["attrs"]["name"], address["attrs"]["address"],
                         "Catalog browser: tcp/8443", "Catalog metrics: tcp/9090",
                         "Which physical uplinks serve", "Which power paths serve"):
            self.assertIn(expected, walkthrough)
        peers = {}
        for obj in objects.values():
            if obj["kind"] == "cable":
                a, b = obj["refs"]["a"], obj["refs"]["b"]
                peers[a], peers[b] = b, a
        uplinks = [obj for obj in objects.values() if obj["kind"] == "interface" and obj["refs"].get("device") == host["key"]
                   and not obj["attrs"].get("mgmt_only")]
        self.assertEqual(len(uplinks), 2)
        for port in uplinks:
            peer = objects[peers[port["key"]]]
            self.assertIn(f"{port['attrs']['name']} → {objects[peer['refs']['device']]['attrs']['name']} / {peer['attrs']['name']}", walkthrough)
        for inlet in (obj for obj in objects.values() if obj["kind"] == "power_port" and obj["refs"].get("device") == host["key"]):
            outlet = objects[peers[inlet["key"]]]
            feed = objects[peers[outlet["refs"]["power_port"]]]
            panel = objects[feed["refs"]["power_panel"]]
            self.assertIn(f"{feed['attrs']['name']} → {panel['attrs']['name']}", walkthrough)

    def test_enterprise_report_has_no_unrelated_bank_or_empty_endpoint_story(self):
        text = markdown(self.plan)
        for phrase in ("Branch designs", "Banking", "Birch", "Small branches", "headquarters peak", "HQ floor",
                       "Branch nodes aggregate", "Representative endpoint access paths", "most modeled endpoints"):
            self.assertNotIn(phrase, text)
        self.assertIn("DC demand is the explicit peak for that site", text)
        self.assertIn("runtime HA or cross-site replication", text)
        self.assertIn("Replica group | Replica lane | Requested domain", text)

    def test_replica_inventory_counts_actual_hosts_and_racks_even_when_contract_is_unchanged(self):
        baseline = markdown(self.plan).split("## Replica group placement\n", 1)[1].split("## Questions to explore\n", 1)[0]
        row = next(line for line in baseline.splitlines() if "| catalog-api |" in line)
        self.assertTrue(row.endswith("| 4 | 2 | rack | 2 | 2 | 1 | 1 |"), row)
        changed = deepcopy(self.plan)
        vms = [obj for obj in changed["objects"] if obj["kind"] == "virtual_machine"]
        host = vms[0]["refs"]["device"]
        for vm in vms:
            vm["refs"]["device"] = host
        text = markdown(changed).split("## Replica group placement\n", 1)[1].split("## Questions to explore\n", 1)[0]
        row = next(line for line in text.splitlines() if "| catalog-api |" in line)
        self.assertTrue(row.endswith("| 4 | 2 | rack | 1 | 1 | 0 | 0 |"), row)

    def test_disconnected_uplink_is_not_presented_as_healthy(self):
        changed = deepcopy(self.plan)
        objects = {obj["key"]: obj for obj in changed["objects"]}
        vm = min((obj for obj in objects.values() if obj["kind"] == "virtual_machine"), key=lambda obj: obj["key"])
        port = f"{vm['refs']['device']}/if/eth0"
        cable = next(obj for obj in objects.values() if obj["kind"] == "cable" and port in (obj["refs"]["a"], obj["refs"]["b"]))
        cable["attrs"]["status"] = "planned"
        walkthrough = markdown(changed).split("## Connected walkthroughs\n", 1)[1].split("## Estate topology\n", 1)[0]
        self.assertIn(f"[{cable['attrs']['label']}: planned]", walkthrough)


if __name__ == "__main__":
    unittest.main()
