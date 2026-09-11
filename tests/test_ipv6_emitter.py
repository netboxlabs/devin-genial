"""Address geometry, scope and growth checks for the optional shared emitter."""

from copy import deepcopy
from ipaddress import ip_interface, ip_network
import unittest

from estates.generate import generate
from estates.ipv6 import enrich, resolve_pool
from estates.model import DesignError, World


PROFILES = ("regional-bank", "enterprise-data-center", "school-district",
            "hospital-clinics", "provider-backbone")


class IPv6EmitterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baselines = {profile: generate({"profile": profile}) for profile in PROFILES}

    def world(self, profile="regional-bank", pool="2001:db8::/32"):
        plan = self.baselines[profile]
        world = World(plan["recipe"], previous=plan)
        world.objects = {obj["key"]: deepcopy(obj) for obj in plan["objects"]}
        if pool is not None:
            world.recipe["ipv6_pool"] = pool
        return world

    def test_pool_is_strict_documentation_space_and_canonical(self):
        for raw, expected in (("2001:0DB8:0000:0000::/32", "2001:db8::/32"),
                              ("2001:db8:ab00::/40", "2001:db8:ab00::/40"),
                              ("3fff:a::/32", "3fff:a::/32")):
            self.assertEqual(resolve_pool(raw), expected)
        for bad in (None, False, 32, {}, "", " ", "2001:db8::1/32", "2001:db8::/48",
                    "2001:db8::/31", "2001:db9::/32", "3fff:ffff::/32", "10.0.0.0/8", "fd00::/32"):
            with self.subTest(value=bad), self.assertRaisesRegex(DesignError, "ipv6_pool"):
                resolve_pool(bad)

    def test_absent_pool_is_exact_noop_but_explicit_null_is_invalid(self):
        world = self.world(pool=None)
        before = deepcopy(world.finish())
        enrich(world)
        self.assertEqual(world.finish(), before)
        world.recipe["ipv6_pool"] = None
        with self.assertRaisesRegex(DesignError, "ipv6_pool"):
            enrich(world)

    def test_all_profiles_preserve_existing_estate_and_actual_address_owners(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                world = self.world(profile)
                before = deepcopy(world.objects)
                enrich(world)
                expected = {f"ipv6/{obj['key']}" for obj in before.values() if obj["kind"] == "ip_address"
                            and before[obj["refs"]["assigned_object"]]["kind"] in {"interface", "vm_interface"}}
                actual = {key for key, obj in world.objects.items() if key.startswith("ipv6/") and obj["kind"] == "ip_address"}
                self.assertEqual(actual, expected)
                for key, old in before.items():
                    new = deepcopy(world.obj(key))
                    new["refs"].pop("primary_ip6", None)
                    if new["kind"] == "service":
                        new["refs"]["ipaddresses"] = [ip for ip in new["refs"].get("ipaddresses", []) if not ip.startswith("ipv6/")]
                    self.assertEqual(new, old, key)
                for key in actual:
                    old, new = before[key.removeprefix("ipv6/")], world.obj(key)
                    self.assertEqual(new["refs"], old["refs"])
                    self.assertEqual({k: v for k, v in new["attrs"].items() if k != "address"},
                                     {k: v for k, v in old["attrs"].items() if k != "address"})
                rir = world.obj("ipv6/rir")["attrs"]
                self.assertEqual(rir["name"], f"{world.recipe['namespace']} IPv6 documentation registry")
                self.assertEqual(rir["slug"], f"{world.recipe['namespace']}-ipv6-docs")
                self.assertFalse(rir["is_private"])
                self.assertEqual(world.obj("ipv6/aggregate")["refs"], {"rir": "ipv6/rir"})

    def test_primary_and_service_binding_reuse_exact_interface_companions(self):
        world = self.world("provider-backbone")
        before = deepcopy(world.objects)
        enrich(world)
        for key, old in before.items():
            if primary := old["refs"].get("primary_ip4"):
                self.assertEqual(world.obj(key)["refs"]["primary_ip6"], f"ipv6/{primary}")
            if old["kind"] == "service":
                ips = old["refs"]["ipaddresses"]
                self.assertEqual(world.obj(key)["refs"]["ipaddresses"], [*ips, *(f"ipv6/{ip}" for ip in ips)])
        loops = [obj for obj in world.objects.values() if obj["key"].startswith("ipv6/prefix/loopback/")]
        self.assertTrue(loops)
        for obj in loops:
            self.assertEqual(ip_network(obj["attrs"]["prefix"]).prefixlen, 128)
        for obj in before.values():
            if obj["kind"] == "interface" and obj["attrs"]["name"] == "fxp0":
                self.assertNotIn(f"ipv6/ip/{obj['key']}", world.objects)

    def test_lan_radio_recovery_offsets_and_ipv4_only_fhrp(self):
        world = self.world()
        enrich(world)
        for obj in list(world.objects.values()):
            if obj["kind"] != "ip_address" or obj["key"].startswith("ipv6/"):
                continue
            port = world.obj(obj["refs"]["assigned_object"])
            if port["kind"] == "fhrp_group":
                self.assertNotIn(f"ipv6/{obj['key']}", world.objects)
                continue
            old = ip_interface(obj["attrs"]["address"])
            new = ip_interface(world.obj(f"ipv6/{obj['key']}")["attrs"]["address"])
            offset = int(old.ip) - int(old.network.network_address)
            if port["attrs"]["name"] == "Recovery0":
                self.assertEqual(new.network.prefixlen, 127)
                self.assertEqual(world.obj(f"ipv6/{obj['key']}")["attrs"]["status"], "reserved")
            else:
                self.assertEqual(new.network.prefixlen, 64)
            self.assertEqual(int(new.ip) - int(new.network.network_address), offset + (port["attrs"]["name"] == "wlan1"))
        routed = ip_network(world.obj("ipv6/prefix/recovery")["attrs"]["prefix"])
        self.assertEqual(int(routed.network_address) % (1 << 64), 2)

    def test_growth_before_existing_sort_order_preserves_ipv6_and_inherited_scope(self):
        recipe = dict(namespace="v6-growth", headquarters=0, branches={"small": 1},
                      site_designs={"br-s0001": "inherited"}, ipv6_pool="3fff:a::/40")
        initial = generate(recipe)
        grown = generate(dict(recipe, branches={"small": 1, "medium": 1}), previous=initial)
        old = {o["key"]: o for o in initial["objects"]}
        new = {o["key"]: o for o in grown["objects"]}
        for key, obj in old.items():
            if key.startswith("ipv6/"):
                self.assertEqual(new[key], obj, key)
        self.assertLess(grown["reservations"]["ipv6-sites"]["site/br-s0001"],
                        grown["reservations"]["ipv6-sites"]["site/br-m0001"])
        inherited = new["ipv6/prefix/br-s0001/users"]
        self.assertEqual(inherited["refs"]["tenant"], "tenant/inherited")
        self.assertEqual(inherited["refs"]["vrf"], "vrf/inherited/br-s0001/users")

    def test_unknown_shapes_masks_and_ledgers_fail_actionably(self):
        world = self.world()
        world.add("prefix", "prefix/unknown", {"prefix": "192.168.250.0/30", "status": "active"},
                  {"vrf": "vrf/recovery", "tenant": "tenant"})
        with self.assertRaisesRegex(DesignError, "unsupported segment prefix/unknown"):
            enrich(world)
        world = self.world()
        ip = next(o for o in world.objects.values() if o["kind"] == "ip_address")
        ip["attrs"]["address"] = "192.168.255.1/32"
        with self.assertRaisesRegex(DesignError, "same VRF and exact prefix mask"):
            enrich(world)
        world = self.world()
        world.reservations["ipv6-mystery"] = {"retired": 0}
        with self.assertRaisesRegex(DesignError, "unknown reservation scope"):
            enrich(world)

    def test_site_capacity_reserves_infrastructure_and_does_not_wrap_retired_slots(self):
        world = self.world(pool="2001:db8::/40")
        world.reservations["ipv6-sites"] = {"site/retired": 254}
        world._next["ipv6-sites"] = 255
        with self.assertRaisesRegex(DesignError, "ipv6-sites: capacity 255 exhausted"):
            enrich(world)

    def test_routed_name_and_forged_ledger_cannot_enroll_an_ordinary_vm(self):
        for forge in (False, True):
            with self.subTest(forged_ledger=forge):
                world = self.world("provider-backbone")
                vm_port = next(o["key"] for o in world.objects.values() if o["kind"] == "vm_interface")
                world.add("prefix", "prefix/link/not-a-routed-link", {"prefix": "10.1.254.0/31", "status": "active"},
                          {"vrf": "vrf/applications", "tenant": "tenant"})
                world.add("ip_address", "ip/not-a-routed-link", {"address": "10.1.254.0/31", "status": "active"},
                          {"assigned_object": vm_port, "vrf": "vrf/applications", "tenant": "tenant"})
                if forge:
                    world.reservations["provider-link-prefixes"]["not-a-routed-link"] = 16383
                with self.assertRaisesRegex(DesignError, "unsupported segment|actual provider routed physical interface"):
                    enrich(world)

    def test_empty_diagnostic_prefix_is_not_an_actual_radio_link(self):
        world = self.world()
        prefix = next(o for o in world.objects.values() if o["kind"] == "prefix" and o["key"].endswith("/radio-transit"))
        world.add("prefix", "prefix/fake/radio-transit", dict(prefix["attrs"], prefix="10.1.254.0/31"), dict(prefix["refs"]))
        with self.assertRaisesRegex(DesignError, "both actual wireless-link endpoints"):
            enrich(world)


if __name__ == "__main__":
    unittest.main()
