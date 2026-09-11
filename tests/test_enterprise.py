"""Real composition, reproducibility, operator preview and growth boundaries."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.__main__ import main
from estates.generate import generate
from estates.model import DesignError, canonical, resolve_recipe
from estates.validate import validate
from estates.validate_optics import analyze as analyze_optics


class EnterpriseTests(unittest.TestCase):
    def test_single_site_pool_boundary_has_one_container_per_vrf_and_builds(self):
        from estates.__main__ import build
        for pool in ("192.168.0.0/16", "172.16.0.0/16", "10.64.0.0/16"):
            with self.subTest(pool=pool), tempfile.TemporaryDirectory() as directory:
                plan = generate({"profile": "enterprise-data-center", "data_centers": 1,
                                 "address_pool": pool, "wan_peak_mbps": 1})
                self.assertEqual(validate(plan), [])
                prefixes = [o for o in plan["objects"] if o["kind"] == "prefix"]
                identities = [(o["refs"].get("vrf"), o["attrs"]["prefix"]) for o in prefixes]
                self.assertEqual(len(identities), len(set(identities)))
                containers = [o for o in prefixes if o["attrs"]["prefix"] == pool]
                self.assertEqual(len(containers), 6)
                self.assertTrue(all(o["refs"].get("scope_site") == "site/dc-01" for o in containers))
                self.assertEqual(canonical(generate(plan["recipe"])), canonical(plan))
                result = build(plan, Path(directory) / "artifact")
                self.assertEqual(result["checks"], "offline passed")
                grown_recipe = deepcopy(plan["recipe"])
                grown_recipe["workloads"][0]["groups"] += 1
                grown = generate(grown_recipe, previous=plan)
                self.assertEqual(validate(grown), [])
                current = {o["key"]: o for o in grown["objects"]}
                for prefix in prefixes:
                    self.assertEqual(current[prefix["key"]], prefix)

    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"profile": "enterprise-data-center", "data_centers": 1})

    def test_profile_has_meaningful_workload_and_reference_data(self):
        plan = self.baseline
        self.assertEqual(validate(plan), [])
        self.assertEqual(canonical(generate(plan["recipe"])), canonical(plan))
        self.assertEqual(plan["recipe"]["profile"], "enterprise-data-center")
        text = canonical(plan).decode()
        for forbidden in ("Birch", "teller-api", "atm-switch", "fictional bank", "analytics-enclosure", "Banking entities"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("branches", plan["recipe"])
        self.assertEqual({o["meta"]["service"] for o in plan["objects"] if o["kind"] == "virtual_machine"},
                         {"inventory-api", "orders-db", "build-artifacts"})
        self.assertEqual(len([o for o in plan["objects"] if o["kind"] == "tenant"]), 1)
        self.assertTrue(any(o["kind"] == "vlan_group" for o in plan["objects"]))

    def assertGrowth(self, recipe):
        before = canonical(self.baseline)
        grown = generate(recipe, previous=self.baseline)
        self.assertEqual(validate(grown), [])
        self.assertEqual(canonical(self.baseline), before)
        old = {o["key"]:o for o in self.baseline["objects"]}
        new = {o["key"]:o for o in grown["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        used = {key for o in old.values() if o["kind"] == "cable" for key in o["refs"].values()}
        _, old_optics = analyze_optics(self.baseline)
        _, new_optics = analyze_optics(grown)
        for key, obj in old.items():
            with self.subTest(key=key):
                owner = obj["refs"].get("device")
                delta = new_optics.get(owner, 0) - old_optics.get(owner, 0)
                if obj["kind"] == "power_port" and delta > 0:
                    actual, expected = deepcopy(new[key]), deepcopy(obj)
                    self.assertEqual(actual["attrs"]["maximum_draw"] - expected["attrs"]["maximum_draw"], delta)
                    for field in ("allocated_draw", "maximum_draw"):
                        self.assertGreaterEqual(actual["attrs"].pop(field), expected["attrs"].pop(field))
                    actual["attrs"].pop("description", None); expected["attrs"].pop("description", None)
                    self.assertEqual(actual, expected)
                elif obj["kind"] in {"virtual_machine", "vm_interface", "ip_address", "cable", "rack"} or key in used:
                    self.assertEqual(new[key], obj)
                if obj["kind"] == "device":
                    for field in ("rack", "location"):
                        self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field))
                    for field in ("position", "face"):
                        self.assertEqual(new[key]["attrs"].get(field), obj["attrs"].get(field))
        for scope, items in self.baseline["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(grown["reservations"][scope][key], slot)
        return grown

    def test_growth_and_new_earlier_workload_preserve_allocated_infrastructure(self):
        recipe = deepcopy(self.baseline["recipe"])
        next(w for w in recipe["workloads"] if w["key"] == "inventory-api")["groups"] = 20
        self.assertGrowth(recipe)
        recipe["workloads"].insert(0, {"key": "aaa-new-service", "groups": 3})
        self.assertGrowth(recipe)
        self.assertGrowth(self.baseline["recipe"] | {"data_centers": 2, "wan_peak_mbps": 2400})

    def test_input_order_and_seed_variation_are_deterministic(self):
        recipe = deepcopy(self.baseline["recipe"])
        recipe["workloads"].reverse()
        self.assertEqual(canonical(generate(recipe)), canonical(self.baseline))
        variant = generate(recipe | {"seed": 77})
        self.assertEqual(validate(variant), [])
        self.assertNotEqual(canonical(variant), canonical(self.baseline))
        self.assertEqual(variant["allocations"], self.baseline["allocations"])
        self.assertEqual(variant["reservations"], self.baseline["reservations"])
        self.assertEqual(canonical(generate(variant["recipe"])), canonical(variant))

    def test_previous_plan_cannot_hide_retirements_or_policy_remaps(self):
        cases = []
        for change in ("groups", "remove", "resources", "replicas", "listeners"):
            recipe = deepcopy(self.baseline["recipe"])
            workload = recipe["workloads"][0]
            if change == "groups": workload["groups"] -= 1
            elif change == "remove": recipe["workloads"].pop()
            elif change == "resources": workload["disk_mb"] += 1
            elif change == "replicas": workload["replicas"] = 3
            else: workload["listeners"][0]["ports"] = [8443]
            cases.append(recipe)
        cases.append(self.baseline["recipe"] | {"wan_peak_mbps": 100})
        for recipe in cases:
            with self.subTest(recipe=recipe), self.assertRaisesRegex(DesignError, "new baseline"):
                generate(recipe, previous=self.baseline)
        two = generate(self.baseline["recipe"] | {"data_centers": 2})
        with self.assertRaisesRegex(DesignError, "new baseline"):
            generate(self.baseline["recipe"], previous=two)

    def test_capacity_and_unsupported_intent_fail_actionably(self):
        cases = [({"data_centers": 2, "address_pool": "192.168.0.0/16"}, "/16 site reservations"),
                 ({"address_pool": "192.168.0.0/20"}, "/16 site reservations"),
                 ({"workloads": [{"key": "large", "groups": 500, "replicas": 2}]}, "16-host reservation"),
                 ({"workloads": [{"key": "unavailable", "failure_domain": "site"}]}, "cross-site"),
                 ({"branches": {}}, "Unknown enterprise"),
                 ({"demo": "invent-a-workflow"}, "not yet implemented"),
                 ({"data_centers": True}, "data_centers"),
                 ({"workloads": [{"key": "bad", "groups": True}]}, "groups")]
        for raw, message in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(DesignError, message):
                generate({"profile": "enterprise-data-center"} | raw)

    def test_frozen_graph_is_checked_before_reusing_its_ledgers(self):
        previous = deepcopy(self.baseline)
        vm = next(o for o in previous["objects"] if o["kind"] == "virtual_machine")
        vm["attrs"]["description"] = "Manually edited graph"
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(previous["recipe"], previous=previous)

    def test_documented_cli_preview_and_artifact_preserve_input_provenance(self):
        def call(*args):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = main(["--json", *map(str,args)])
            self.assertEqual(status, 0, out.getvalue()+err.getvalue())
            return json.loads(out.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recipe, artifact = root/"customer.toml", root/"demo"
            recipe.write_text('profile="enterprise-data-center"\ndata_centers=1\n[[workloads]]\nkey="customer-portal"\ngroups=3\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["resolved"]["data_centers"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["reserve_fraction"]["source"], "default")
            self.assertEqual(preview["workloads"][0]["vms_per_site"], 6)
            self.assertEqual(preview["workloads"][0]["fields"]["groups"]["source"], "supplied")
            self.assertEqual(preview["workloads"][0]["fields"]["replicas"]["source"], "default")
            self.assertTrue(preview["assumptions"])
            call("generate", recipe, "--out", artifact)
            self.assertEqual(json.loads((artifact/"intent.json").read_text()), preview)
            call("check", artifact/"plan.json")
            call("build", artifact/"plan.json", "--out", root/"from-frozen")
            frozen = json.loads((root/"from-frozen/intent.json").read_text())
            self.assertEqual(frozen["resolved"]["data_centers"]["source"], "unknown (frozen plan)")


if __name__ == "__main__":
    unittest.main()
