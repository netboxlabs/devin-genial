"""Catalog part selection preserves real cages, assembly identity and growth."""

from collections import Counter
from copy import deepcopy
import unittest

from estates import fibre, optics
from estates.generate import generate
from estates.model import DesignError, World, canonical, hardware_catalog, recipe_from_file


class OpticsEmitterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {name: generate(recipe_from_file(f"profiles/{name}.toml")) for name in (
            "bank-depth", "enterprise-dc", "school-wireless", "hospital-wireless", "provider-backbone",
            "showcase-provider")}
        # The alternate vendor lines install their own reviewed parts; without a
        # variant estate the exhaustive-coverage assertion below could not see them.
        variant = recipe_from_file("profiles/school-wireless.toml")
        variant["hardware"] = {"access": "juniper", "leaf": "juniper", "ap": "aruba"}
        cls.plans["school-wireless-juniper"] = generate(variant)
        # The provider timeline makes every PoP launched before the 2019-07-01
        # cutover an ACX5048 site, and the showcase homes its 10G DIA customers
        # only there; the ACX5448-M's SFPP-10GE-* single-mode rungs need a 10G
        # UNI on a later PoP. Rehome one 2G DIA customer to a Milwaukee PoP.
        variant = recipe_from_file("profiles/showcase-provider.toml")
        customer = next(c for c in variant["customers"] if c["key"] == "sable-trading")
        customer["sites"] = [{"pop": "milwaukee-oak-creek", "count": 1}]
        cls.plans["showcase-provider-acx5448-10g"] = generate(variant)

    def test_every_profile_has_only_occupied_cage_inventory_and_resolved_references(self):
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
                    self.assertLessEqual(len(module["attrs"].get("serial", "")), 50)  # a planned optic has none yet
                    self.assertLessEqual(len(bay["attrs"]["name"]), 64)
                    self.assertLessEqual(len(bay["attrs"]["position"]), 30)
                    mt = objects[module["refs"]["module_type"]]
                    seen.add((objects[mt["refs"]["manufacturer"]]["attrs"]["name"], mt["attrs"]["model"]))
                for obj in objects.values():
                    for value in obj["refs"].values():
                        for target in value if isinstance(value, list) else [value]:
                            self.assertIn(target, objects, (obj["key"], target))
        # No dead inventory: every part a sampled estate could host is installed
        # somewhere, except a longer-reach sibling of an installed part (same
        # host cage, rate and medium). Owned-run reach depends on how far a
        # recipe's sites sit apart, so the long-reach rung stays in the catalog
        # as the shortest part that covers a longer run; it is never a
        # standalone part no recipe could select. Parts whose host types no
        # sampled estate emits are stock awaiting their builder.
        parts = hardware_catalog()["optics"]["parts"]
        hosts = {o["key"].removeprefix("hardware/") for plan in self.plans.values()
                 for o in plan["objects"] if o["kind"] == "device_type"}
        used = [p for p in parts.values() if (p["manufacturer"], p["model"]) in seen]

        def rung_above_used(part):
            return any(u["rate_kbps"] == part["rate_kbps"] and u["medium"] == part["medium"]
                       and u["reach_m"] < part["reach_m"]
                       and any(set(names) & set(u["compatible_interfaces"].get(alias, ()))
                               for alias, names in part["compatible_interfaces"].items())
                       for u in used)
        expected = {(p["manufacturer"], p["model"]) for p in parts.values()
                    if set(p["compatible_interfaces"]) & hosts and not rung_above_used(p)}
        self.assertLessEqual(expected, seen)
        self.assertLessEqual(seen, {(p["manufacturer"], p["model"]) for p in parts.values()})

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
        """A 1G handoff in an SFP+ cage takes a 1G part: LX up to 10 km of owned
        fiber, LH (70 km) beyond; the same rule picks LR4 or ER4 Lite at 100G."""
        objects = {o["key"]: o for o in self.plans["showcase-provider"]["objects"]}
        ports = [o for o in objects.values() if o["kind"] == "interface" and o["attrs"].get("type") == "10gbase-x-sfpp"
                 and o["attrs"].get("speed") == 1000000 and "module" in o["refs"]]
        self.assertTrue(ports)
        # The owned span plus the local cords the check adds, bounded by the
        # local-channel maximum, must fit the selected reach.
        margin = hardware_catalog()["optics"]["local_max_m"]
        models = Counter()
        for port in ports:
            cable = next(c for c in objects.values() if c["kind"] == "cable" and port["key"] in c["refs"].values())
            far = cable["refs"]["b" if cable["refs"]["a"] == port["key"] else "a"]
            span = optics.owned_span_m(objects, far)
            model = objects[objects[port["refs"]["module"]]["refs"]["module_type"]]["attrs"]["model"]
            models[model] += 1
            self.assertEqual(model, "SFP-1GE-LX" if span + margin <= 10000 else "SFP-1GE-LH", (port["key"], span))
        self.assertTrue(models["SFP-1GE-LX"] and models["SFP-1GE-LH"], models)
        # Owned spans land on the OSP panel: trace the termination's cable
        # through the panel to the PE cage that lights it.
        peers = {}
        for c in objects.values():
            if c["kind"] == "cable":
                peers[c["refs"]["a"]], peers[c["refs"]["b"]] = c["refs"]["b"], c["refs"]["a"]
        spans = {o["attrs"]["cid"]: (o["attrs"]["distance"], objects[objects["optics-module/" + fibre.far_end(objects, peers[f"{o['key']}/A"], peers)]["refs"]["module_type"]]["attrs"]["model"])
                 for o in objects.values() if o["kind"] == "circuit" and o["key"].startswith("circuit/backbone/")
                 and o["refs"]["type"] == "circuit-type/dark-fiber"}
        self.assertTrue(spans)
        for cid, (km, model) in spans.items():
            self.assertEqual(model, "JNP-QSFP-100G-LR4" if km * 1000 + margin <= 10000 else "QSFP-100G-ER4L", cid)

    def test_unsupported_media_and_ambiguous_catalog_fail_actionably(self):
        for mode in ("media", "ambiguous"):
            with self.subTest(mode=mode):
                world = World({})
                world.add("device", "d", refs={"device_type": "hardware/access"})
                world.add("interface", "i", {"name": "TenGigabitEthernet1/1/1", "type": "10gbase-x-sfpp"}, {"device": "d"})
                world.add("circuit_termination", "t")
                world.add("cable", "c", {"type": "aoc" if mode == "media" else "smf"}, {"a": "i", "b": "t"})
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
