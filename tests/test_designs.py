"""Procedural design boundaries, correlated variation, and stable growth."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.model import DesignError, canonical, hardware_catalog, resolve_recipe
from estates.validate import validate


class DesignTests(unittest.TestCase):
    def test_catalog_attachment_maps_reference_unique_real_ports(self):
        for alias in ("access", "inherited-access"):
            model = hardware_catalog()["models"][alias]
            ports = {p["name"]: p for p in model["interfaces"]}
            attachments = model["access_ports"] + model["uplink_ports"]
            self.assertEqual(len(attachments), len(set(attachments)))
            self.assertTrue(set(attachments) <= ports.keys())
            self.assertTrue(all(ports[p]["type"] == "1000base-t" for p in model["access_ports"]))
            self.assertTrue(all(ports[p]["type"] == "10gbase-x-sfpp" for p in model["uplink_ports"]))

    def test_each_design_expands_from_demand_at_every_branch_size(self):
        for design in ("modern", "inherited", "refreshed"):
            for size, endpoints, switches in (("small", 18, 2), ("medium", 48, 3), ("large", 106, 6)):
                site_id = f"br-{size[0]}0001"
                with self.subTest(design=design, size=size):
                    plan = generate({"headquarters": 0, "branches": {size: 1}, "site_designs": {site_id: design}})
                    self.assertEqual(validate(plan), [])
                    objects = {o["key"]: o for o in plan["objects"]}
                    site = objects[f"site/{site_id}"]
                    self.assertEqual(site["meta"]["branch_design"], design)
                    devices = [o for o in plan["objects"] if o["kind"] == "device" and o["refs"]["site"] == site["key"]]
                    self.assertEqual(sum(bool(o["meta"].get("endpoint")) for o in devices), endpoints)
                    access = [o for o in devices if o["refs"]["role"] == "role/access"]
                    self.assertEqual(len(access), switches)
                    self.assertEqual(sum(o["refs"]["role"] == "role/distribution" for o in devices), 0 if size == "small" else 2)
                    self.assertEqual(site["meta"]["branch_architecture"], "compact-routed-edge" if size == "small" else "distribution")
                    model = "inherited-access" if design == "inherited" else "access"
                    self.assertTrue(all(o["refs"]["device_type"] == f"hardware/{model}" for o in access))
                    for device in devices:
                        if device["meta"].get("endpoint"):
                            ip = objects[device["refs"]["primary_ip4"]]
                            if design == "modern":
                                self.assertNotIn("birch", device["attrs"]["name"])
                                self.assertTrue(ip["attrs"]["address"].startswith("10."))
                            else:
                                self.assertIn("birch", device["attrs"]["name"])
                                self.assertTrue(ip["attrs"]["address"].startswith("172.16."))
                                self.assertIn(f"inherited/{site_id}/", ip["refs"]["vrf"])

    def test_mixed_growth_preserves_assignments_and_existing_physical_graph(self):
        recipe = {"headquarters": 0, "branches": {"small": 5, "medium": 2},
                  "design_mix": {"modern": 60, "inherited": 25, "refreshed": 15},
                  "site_designs": {"br-s0001": "inherited", "br-s0002": "refreshed"}}
        baseline = generate(recipe)
        before = canonical(baseline)
        grown = generate(baseline["recipe"] | {"branches": {"small": 60, "medium": 10}}, previous=baseline)
        self.assertEqual(validate(grown), [])
        self.assertEqual(canonical(baseline), before)
        objects = {o["key"]: o for o in grown["objects"]}
        for key, design in baseline["design_assignments"].items():
            self.assertEqual(grown["design_assignments"][key], design)
        for obj in baseline["objects"]:
            if obj["kind"] in {"cable", "ip_address"}:
                self.assertEqual(objects[obj["key"]], obj)
            if obj["kind"] == "device":
                self.assertEqual(objects[obj["key"]]["attrs"].get("position"), obj["attrs"].get("position"))
        for scope, slots in baseline["reservations"].items():
            self.assertTrue(slots.items() <= grown["reservations"][scope].items())

    def test_pool_is_seeded_and_explicit_choices_override_it(self):
        recipe = {"headquarters": 0, "branches": {"small": 12},
                  "design_mix": {"modern": 1, "inherited": 1, "refreshed": 1},
                  "site_designs": {"br-s0001": "inherited"}}
        baseline = generate(recipe)
        self.assertEqual(canonical(generate(recipe)), canonical(baseline))
        other = generate(recipe | {"seed": 43})
        self.assertEqual(validate(other), [])
        self.assertNotEqual(other["design_assignments"], baseline["design_assignments"])
        self.assertEqual(other["design_assignments"]["br-s0001"], "inherited")

    def test_design_changes_require_an_explicit_transition(self):
        baseline = generate({"branches": {"small": 1}, "site_designs": {"br-s0001": "inherited"}})
        for change in ({"site_designs": {"br-s0001": "refreshed"}}, {"acquired_sites": ["br-s0001"]}):
            with self.assertRaisesRegex(DesignError, "scenario or rebaseline"):
                generate(baseline["recipe"] | change, previous=baseline)
        with self.assertRaisesRegex(DesignError, "rebaseline"):
            generate(baseline["recipe"] | {"design_mix": {"inherited": 100}}, previous=baseline)

    def test_bad_design_requests_and_unknown_branches_fail(self):
        for recipe in ({"design_mix": {}}, {"design_mix": {"modern": True}}, {"design_mix": {"random": 1}},
                       {"site_designs": {"br-s0001": "invented"}}, {"site_designs": {"br-s9999": "inherited"}},
                       {"acquired_sites": ["br-s0001", "br-s0001"]}, {"acquired_sites": ["br-s0001"]}):
            with self.subTest(recipe=recipe), self.assertRaises(DesignError):
                generate(recipe)
        with self.assertRaises(DesignError):
            resolve_recipe([])

    def test_inherited_address_pool_ceiling_is_not_wrapped(self):
        # Reserve pressure without constructing hundreds of branches first.
        baseline = generate({"headquarters": 0, "branches": {"small": 1}, "site_designs": {"br-s0001": "inherited"}})
        previous = deepcopy(baseline)
        previous["reservations"]["inherited-site-networks"]["retired-branch"] = 255
        with self.assertRaisesRegex(DesignError, "capacity 256 exhausted"):
            generate(baseline["recipe"] | {"branches": {"small": 2}, "site_designs": {"br-s0001": "inherited", "br-s0002": "inherited"}}, previous=previous)


class CompactBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 0, "branches": {"small": 3},
                                 "site_designs": {"br-s0001": "modern", "br-s0002": "inherited", "br-s0003": "refreshed"}})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def remove_cable(self, port):
        self.plan["objects"] = [obj for obj in self.plan["objects"]
                                if not (obj["kind"] == "cable" and port in obj["refs"].values())]

    def test_inherited_peer_trunk_is_required_even_with_each_local_gateway_reachable(self):
        self.remove_cable("device/br-s0002/access-01/if/xe-0/1/2")
        self.assertIn("branch-peer-trunk", self.codes())
        self.assertNotIn("vlan-gateway", self.codes(), "Each half still reaches a gateway; the shared broadcast domain is what broke")

    def test_peer_trunk_cannot_silently_omit_one_segment_on_both_ends(self):
        for device in ("access-01", "access-02"):
            port = self.objects[f"device/br-s0002/{device}/if/xe-0/1/2"]
            port["refs"]["tagged_vlans"] = [vlan for vlan in port["refs"]["tagged_vlans"] if vlan != "vlan/br-s0002/atm"]
        self.assertIn("branch-peer-trunk", self.codes())
        self.assertNotIn("vlan-continuity", self.codes(), "Both ends agree, but the shared ATM domain is disconnected")

    def test_direct_edge_requirement_cannot_be_replaced_by_indirect_peer_path(self):
        switch = "device/br-s0001/access-01"
        self.remove_cable(f"{switch}/if/TenGigabitEthernet1/1/2")
        contract = next(c for c in self.plan["contracts"] if c["site"] == "site/br-s0001")
        next(c for c in contract["redundant_uplinks"] if c["device"] == switch)["min_distinct_peers"] = 1
        self.assertTrue({"branch-uplinks", "branch-uplink-contract"} <= self.codes())

    def test_gateway_parent_must_be_its_own_enabled_bridge(self):
        gateway = self.objects["device/br-s0001/edge-a/if/Vlan20"]
        gateway["refs"]["parent"] = "device/br-s0001/edge-b/if/branch-lan"
        self.assertTrue({"interface-relation", "branch-gateway-carrier", "branch-edge-gateway"} <= self.codes())

    def test_gateway_does_not_float_above_detached_data_ports(self):
        for name in ("x1", "x2"):
            self.objects[f"device/br-s0001/edge-a/if/{name}"]["refs"].pop("bridge")
        self.assertTrue({"branch-edge-bridge", "branch-gateway-carrier"} <= self.codes())

    def test_each_edge_requires_an_addressed_gateway_for_every_segment(self):
        address = self.objects["ip/device/br-s0001/edge-a/if/Vlan20"]
        self.plan["objects"].remove(address)
        self.assertIn("branch-edge-gateway", self.codes())

    def test_architecture_cannot_be_weakened_in_contract_or_metadata(self):
        self.objects["site/br-s0001"]["meta"]["branch_architecture"] = "distribution"
        next(c for c in self.plan["contracts"] if c["site"] == "site/br-s0001")["branch_architecture"] = "distribution"
        self.assertIn("branch-architecture", self.codes())

    def test_management_path_and_primary_attachment_are_real(self):
        self.remove_cable("device/br-s0001/edge-a/if/port12")
        self.assertIn("branch-management-path", self.codes())

    def test_two_switches_retain_headroom_at_maximum_allowed_reserve(self):
        plan = generate({"headquarters": 0, "branches": {"small": 1}, "reserve_fraction": 0.4})
        self.assertEqual(validate(plan), [])
        contract = next(c for c in plan["contracts"] if c["site"] == "site/br-s0001")
        self.assertEqual(len(contract["access_devices"]), 2)
        self.assertEqual(contract["access_usable_ports"], 14)


if __name__ == "__main__":
    unittest.main()
