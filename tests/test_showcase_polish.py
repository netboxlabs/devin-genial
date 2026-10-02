"""The 0.16 showcase polish: each finding pinned on a healthy estate plus a
failing mutation, so a validator that stops looking fails here.

Covers short-reach in-room optics, switch-only 802.1Q access mode, unracked
container-lab routers, held-role config-context scope and installed-only
hardware taxonomy.
"""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.model import ROOT, hardware_catalog, recipe_from_file
from estates.validate import validate


def codes(findings):
    return {finding["code"] for finding in findings}


class ShowcasePolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.showcase = generate(recipe_from_file(ROOT / "profiles/showcase-provider.toml"))
        cls.objects = {obj["key"]: obj for obj in cls.showcase["objects"]}

    def mutate(self, change):
        plan = deepcopy(self.showcase)
        change({obj["key"]: obj for obj in plan["objects"]}, plan)
        return plan

    def of(self, kind):
        return [obj for obj in self.showcase["objects"] if obj["kind"] == kind]

    def test_showcase_validates(self):
        self.assertEqual(validate(self.showcase), [])

    def module_type(self, port):
        return self.objects[self.objects[self.objects[port]["refs"]["module"]]["refs"]["module_type"]]["attrs"]["model"]

    def test_in_room_jumpers_run_multimode_short_reach_optics(self):
        local = [c for c in self.of("cable") if all(self.objects.get(c["refs"][s], {}).get("kind") == "interface"
                                                     for s in "ab") and c["attrs"].get("type") in ("smf", "mmf")]
        multimode = [c for c in local if c["attrs"]["type"] == "mmf"]
        self.assertTrue(multimode)
        for cable in multimode:
            self.assertLessEqual(cable["attrs"]["length"], 100)
            for side in "ab":
                self.assertRegex(self.module_type(cable["refs"][side]), r"-SR4?$")
        # The only single-mode jumpers left are those with a cage that has no
        # reviewed multimode part (the FortiGate 100F).
        for cable in (c for c in local if c["attrs"]["type"] == "smf"):
            self.assertIn("FN-TRAN-SFP+LR", {self.module_type(cable["refs"][s]) for s in "ab"})
        # PE pairs, management uplinks and the NOC fabric no longer carry 10 km LR on 3 m jumpers.
        pair = self.objects["device/pop-chicago-cermak/pe-a/if/et-0/0/0"]
        self.assertEqual(self.module_type(pair["key"]), "JNP-QSFP-100G-SR4")

        parts = hardware_catalog()["optics"]["parts"]
        lr = {p["model"]: p for p in parts.values() if p["manufacturer"] == "Juniper"}["JNP-QSFP-100G-LR4"]

        def long_reach(objects, plan):
            cable = next(c for c in objects.values() if c["kind"] == "cable" and c["attrs"].get("type") == "mmf"
                         and self.module_type(c["refs"]["a"]) == "JNP-QSFP-100G-SR4")
            cable["attrs"]["type"] = "smf"
            for side in "ab":
                module = objects[objects[cable["refs"][side]]["refs"]["module"]]
                module["refs"]["module_type"] = f"module-type/Juniper/{lr['model']}"
        self.assertIn("optics-reach-class", codes(validate(self.mutate(long_reach))))

    def test_only_switch_ports_carry_an_access_vlan(self):
        switching = {"role/access", "role/management", "role/distribution", "role/stack", "role/leaf", "role/spine"}
        for port in self.of("interface"):
            if port["attrs"].get("mode") != "access":
                continue
            device = self.objects[port["refs"]["device"]]
            parent = self.objects.get(port["refs"].get("parent"), {})
            self.assertTrue(device["refs"]["role"] in switching and not port["attrs"].get("mgmt_only")
                            or parent.get("attrs", {}).get("mode") == "tagged", port["key"])
        fxp0 = self.objects["device/pop-chicago-cermak/pe-a/if/fxp0"]
        self.assertNotIn("mode", fxp0["attrs"])
        self.assertNotIn("untagged_vlan", fxp0["refs"])

        def switchport(objects, plan):
            port = objects["device/pop-chicago-cermak/pe-a/if/fxp0"]
            port["attrs"]["mode"] = "access"
            port["refs"]["untagged_vlan"] = "vlan/pop-chicago-cermak/management"
        self.assertIn("interface-host-mode", codes(validate(self.mutate(switchport))))

    def test_container_lab_routers_are_located_not_racked(self):
        lab = [d for d in self.of("device") if d["refs"].get("role") == "role/lab-router"]
        self.assertTrue(lab)
        for device in lab:
            self.assertEqual(device["refs"]["location"], "location/dc-01/network-lab")
            self.assertNotIn("rack", device["refs"])
            self.assertNotIn("position", device["attrs"])
            self.assertTrue(device["refs"].get("primary_ip4"))

        def racked(objects, plan):
            objects[lab[0]["key"]]["attrs"]["position"] = 1
        self.assertIn("lab-placement", codes(validate(self.mutate(racked))))

    def test_switch_baseline_targets_only_held_roles(self):
        held = {d["refs"].get("role") for d in self.of("device")}
        roles = self.objects["config-context/switching"]["refs"]["roles"]
        self.assertTrue(roles)
        self.assertLessEqual(set(roles), held)
        self.assertNotIn("role/access", self.objects)  # a role no device holds is not emitted

        def idle(objects, plan):
            # A role no device holds (the PoP panels hold Patch Panel since 0.17).
            objects["config-context/switching"]["refs"]["roles"].append("role/wall-outlet")
            plan["objects"].append({"key": "role/wall-outlet", "kind": "device_role",
                                    "attrs": {"name": "Wall Outlet", "slug": "inland-fiber-wall-outlet", "color": "b0bec5"},
                                    "refs": {}, "meta": {}})
        self.assertIn("automation-context", codes(validate(self.mutate(idle))))

    def test_taxonomy_is_only_what_the_estate_installs(self):
        used = {d["refs"]["device_type"] for d in self.of("device")}
        self.assertEqual({t["key"] for t in self.of("device_type")}, used)
        installed = {m["refs"]["module_type"] for m in self.of("module")}
        self.assertEqual({t["key"] for t in self.of("module_type")}, installed)
        # PSU types carry no profile; the only profile is the optics datasheet one.
        self.assertEqual([p["attrs"]["name"] for p in self.of("module_type_profile")], ["Installed pluggable optic"])
        self.assertFalse([d for d in self.of("device") if d["key"].rsplit("/", 1)[-1].startswith("odf-")])


if __name__ == "__main__":
    unittest.main()
