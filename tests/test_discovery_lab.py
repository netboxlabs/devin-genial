"""The provider's optional network lab: off by default, additive, isolated, and checked independently.

Every lab check in ``validate_provider.discovery_lab`` has a failing mutation
here; ``lab/discovery/test_render.py`` pins that the renderer reads the plan.
"""

from copy import deepcopy
from ipaddress import ip_interface
from pathlib import Path
import tomllib
import unittest

from estates.discovery_lab import LAB_POOL, ROLE
from estates.generate import generate
from estates.model import DesignError
from estates.validate import validate

ROOT = Path(__file__).parents[1]


def recipe(**extra):
    return tomllib.loads((ROOT / "profiles/provider-backbone.toml").read_text()) | extra


def index(plan):
    return {o["key"]: o for o in plan["objects"]}


def codes(plan):
    return {f["code"] for f in validate(plan)}


class DiscoveryLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.off = generate(recipe())
        cls.plan = generate(recipe(discovery_lab=True))
        cls.objects = index(cls.plan)
        cls.devices = sorted(k for k, o in cls.objects.items() if o["kind"] == "device" and o["refs"]["role"] == ROLE)

    def mutated(self, change):
        plan = deepcopy(self.plan)
        change(index(plan), plan)
        return codes(plan)

    def test_off_by_default_and_purely_additive_when_on(self):
        self.assertFalse(self.off["recipe"]["discovery_lab"])
        self.assertEqual(self.plan["recipe"]["discovery_lab"], {"nodes": 3})
        self.assertEqual(validate(self.plan), [])
        self.assertTrue(all(o in self.plan["objects"] for o in self.off["objects"]))
        extra = [o for o in self.plan["objects"] if o not in self.off["objects"]]
        self.assertTrue(extra and all(o["meta"].get("discovery_lab") for o in extra))
        self.assertEqual(len(self.devices), 3)
        four = generate(recipe(discovery_lab={"nodes": 4}))
        self.assertEqual(validate(four), [])
        self.assertEqual(sum(o["refs"].get("role") == ROLE for o in four["objects"]), 4)

    def test_lab_routers_mirror_named_production_routers(self):
        for key in self.devices:
            device = self.objects[key]
            mirrored = self.objects[device["meta"]["mirrors"]]
            self.assertEqual(device["attrs"]["name"], "lab-" + mirrored["attrs"]["name"])
            self.assertIn(mirrored["attrs"]["name"], device["attrs"]["comments"])
            self.assertIn("container", device["attrs"]["comments"])
            self.assertEqual(self.objects[device["refs"]["location"]]["attrs"]["name"], "Network Lab")
            self.assertEqual(self.objects[device["refs"]["platform"]]["attrs"]["name"], "NOKIA_SRL v26.7.2")
            self.assertEqual(self.objects[device["refs"]["device_type"]]["attrs"]["model"], "7220 IXR-D2L")

    def test_unknown_settings_and_growth_changes_are_refused(self):
        for bad in ({"nodes": 5}, {"nodes": 3, "size": 1}, "yes", 3):
            with self.assertRaises(DesignError):
                generate(recipe(discovery_lab=bad))
        with self.assertRaisesRegex(DesignError, "discovery_lab"):
            generate(recipe(discovery_lab=True), previous=self.off)
        self.assertEqual(index(generate(recipe(discovery_lab=True), previous=self.plan)), self.objects)

    def test_records_without_the_recipe_key_are_refused(self):
        plan = deepcopy(self.plan)
        plan["recipe"]["discovery_lab"] = False
        self.assertIn("lab-recipe", codes(plan))

    def test_production_reference_to_a_lab_port_is_refused(self):
        port = f"{self.devices[0]}/if/ethernet-1/1"
        session = next(o for o in self.plan["objects"] if o["kind"] == "bgp_session")
        self.assertIn("lab-isolation", self.mutated(lambda o, _: o[session["key"]]["refs"].__setitem__("site", port)))

    def test_lab_cable_to_a_production_port_is_refused(self):
        cable = next(k for k, o in self.objects.items() if o["kind"] == "cable" and k.startswith("cable/device/dc-01/network-lab/"))
        pe_port = next(k for k, o in self.objects.items() if o["kind"] == "interface"
                       and o["refs"]["device"] == self.objects[self.devices[0]]["meta"]["mirrors"])
        self.assertIn("lab-isolation", self.mutated(lambda o, _: o[cable]["refs"].__setitem__("b", pe_port)))

    def test_lab_vrf_is_refused(self):
        self.assertIn("lab-isolation", self.mutated(lambda o, _: o["prefix/lab/management"]["refs"].__setitem__("vrf", "vrf/provider")))

    def test_production_address_in_lab_space_and_lab_address_outside_it_are_refused(self):
        mgmt = f"ip/{self.devices[0]}/if/mgmt0.0"
        self.assertIn("lab-address", self.mutated(lambda o, _: o[mgmt]["attrs"].__setitem__("address", "10.250.0.1/24")))
        production = next(k for k, o in self.objects.items() if o["kind"] == "ip_address"
                          and not ip_interface(o["attrs"]["address"]).network.subnet_of(LAB_POOL)
                          if ip_interface(o["attrs"]["address"]).version == 4)
        self.assertIn("lab-address", self.mutated(lambda o, _: o[production]["attrs"].__setitem__("address", "198.18.200.1/24")))
        self.assertIn("lab-address", self.mutated(lambda o, _: o[mgmt]["attrs"].__setitem__("address", "198.18.9.1/24")))

    def test_wrong_hardware_missing_port_and_missing_mac_are_refused(self):
        self.assertIn("lab-hardware", self.mutated(lambda o, _: o["hardware/lab-router"]["attrs"].__setitem__("model", "MX204")))
        port = f"{self.devices[0]}/if/ethernet-1/2"
        self.assertIn("lab-hardware", self.mutated(lambda o, p: p["objects"].remove(o[port]) or p["objects"].remove(o[f"mac/{port}"])))
        self.assertIn("lab-hardware", self.mutated(lambda o, _: o[port]["refs"].pop("primary_mac_address")))

    def test_lab_router_outside_the_lab_room_is_refused(self):
        self.assertIn("lab-placement", self.mutated(
            lambda o, _: o[self.devices[0]]["refs"].__setitem__("rack", "rack/dc-01/network-01")))
        self.assertIn("lab-placement", self.mutated(
            lambda o, _: o["location/dc-01/network-lab"]["attrs"].__setitem__("name", "Data hall 2")))

    def test_lab_must_mirror_the_first_pop_and_its_adjacencies(self):
        device = self.devices[-1]
        self.assertIn("lab-mirror", self.mutated(lambda o, _: o[device]["attrs"].__setitem__("name", "lab-unknown")))
        cable = next(k for k, o in self.objects.items() if o["kind"] == "cable" and k.startswith("cable/device/dc-01/network-lab/"))
        self.assertIn("lab-mirror", self.mutated(lambda o, p: p["objects"].remove(o[cable])))


if __name__ == "__main__":
    unittest.main()
