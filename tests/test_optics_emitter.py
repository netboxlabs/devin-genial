"""Catalog part selection preserves real cages, assembly identity and growth."""

from collections import Counter
from copy import deepcopy
import unittest

from estates import optics
from estates.generate import generate
from estates.model import DesignError, World, canonical, hardware_catalog, recipe_from_file


class OpticsEmitterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {name: generate(recipe_from_file(f"profiles/{name}.toml")) for name in (
            "bank-depth", "enterprise-dc", "school-wireless", "hospital-wireless", "provider-backbone")}

    def test_all_five_have_only_occupied_cage_inventory_and_resolved_references(self):
        seen = set()
        for name, plan in self.plans.items():
            with self.subTest(profile=name):
                objects = {o["key"]: o for o in plan["objects"]}
                cable_ends = {o["refs"][side] for o in objects.values() if o["kind"] == "cable" for side in ("a", "b")}
                optics_ports = [o for o in objects.values() if o["kind"] == "interface" and "module" in o["refs"]]
                installed = [o for o in objects.values() if o["key"].startswith("optics-module/")]
                self.assertTrue(installed)
                self.assertEqual(len(optics_ports), len(installed))
                self.assertEqual(len({o["refs"]["module"] for o in optics_ports}), len(installed))
                for port in optics_ports:
                    self.assertIn(port["key"], cable_ends)
                    module = objects[port["refs"]["module"]]
                    bay = objects[module["refs"]["module_bay"]]
                    self.assertEqual(port["refs"]["device"], module["refs"]["device"])
                    self.assertEqual(module["refs"]["device"], bay["refs"]["device"])
                    self.assertLessEqual(len(module["attrs"]["serial"]), 50)
                    self.assertLessEqual(len(bay["attrs"]["name"]), 64)
                    self.assertLessEqual(len(bay["attrs"]["position"]), 30)
                    mt = objects[module["refs"]["module_type"]]
                    seen.add((objects[mt["refs"]["manufacturer"]]["attrs"]["name"], mt["attrs"]["model"]))
                for obj in objects.values():
                    for value in obj["refs"].values():
                        for target in value if isinstance(value, list) else [value]:
                            self.assertIn(target, objects, (obj["key"], target))
        self.assertEqual(seen, {(p["manufacturer"], p["model"]) for p in hardware_catalog()["optics"]["parts"].values()})

    def test_aoc_is_one_assembly_with_two_captive_ends(self):
        plan = self.plans["school-wireless"]
        objects = {o["key"]: o for o in plan["objects"]}
        serials = Counter(o["attrs"]["serial"] for o in objects.values() if o["key"].startswith("optics-module/"))
        aocs = [o for o in objects.values() if o["kind"] == "cable" and o["attrs"].get("type") == "aoc"]
        self.assertTrue(aocs)
        for cable in aocs:
            ends = [objects[objects[cable["refs"][side]]["refs"]["module"]] for side in ("a", "b")]
            self.assertEqual(ends[0]["attrs"]["serial"], ends[1]["attrs"]["serial"])
            self.assertEqual(serials[ends[0]["attrs"]["serial"]], 2)
            self.assertEqual({objects[e["refs"]["module_type"]]["attrs"]["model"] for e in ends}, {"AOC-Q-Q-100G-3M"})
            self.assertTrue(all("asset_tag" not in e["attrs"] for e in ends))
            self.assertEqual((cable["attrs"]["length"], cable["attrs"]["length_unit"]), (3, "m"))

    def test_provider_effective_1g_rate_uses_lx_in_sfp_plus_cage(self):
        objects = {o["key"]: o for o in self.plans["provider-backbone"]["objects"]}
        ports = [o for o in objects.values() if o["kind"] == "interface" and o["attrs"].get("type") == "10gbase-x-sfpp"
                 and o["attrs"].get("speed") == 1000000 and "module" in o["refs"]]
        self.assertTrue(ports)
        for port in ports:
            module = objects[port["refs"]["module"]]
            self.assertEqual(objects[module["refs"]["module_type"]]["attrs"]["model"], "SFP-1GE-LX")

    def test_unsupported_media_and_ambiguous_catalog_fail_actionably(self):
        for mode in ("media", "ambiguous"):
            with self.subTest(mode=mode):
                world = World({})
                world.add("device", "d", refs={"device_type": "hardware/access"})
                world.add("interface", "i", {"name": "TenGigabitEthernet1/1/1", "type": "10gbase-x-sfpp"}, {"device": "d"})
                world.add("circuit_termination", "t")
                world.add("cable", "c", {"type": "mmf" if mode == "media" else "smf"}, {"a": "i", "b": "t"})
                if mode == "ambiguous":
                    world.catalog["optics"]["parts"]["duplicate"] = deepcopy(world.catalog["optics"]["parts"]["cisco-10g-lr"])
                with self.assertRaisesRegex(DesignError, "no reviewed optic|ambiguous selection"):
                    optics.enrich(world)

    def test_replay_and_branch_growth_preserve_existing_modules_and_bays(self):
        before = self.plans["bank-depth"]
        self.assertEqual(canonical(generate(before["recipe"], previous=before)), canonical(before))
        recipe = deepcopy(before["recipe"])
        recipe["branches"]["small"] += 1
        grown = {o["key"]: o for o in generate(recipe, previous=before)["objects"]}
        for obj in before["objects"]:
            if obj["kind"] in {"module", "module_type", "module_bay", "module_bay_type"}:
                self.assertEqual(grown[obj["key"]], obj, obj["key"])


if __name__ == "__main__":
    unittest.main()
