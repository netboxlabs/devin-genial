"""Dual-stack additions must neither confuse nor replace IPv4 obligations."""

from copy import deepcopy
from ipaddress import ip_interface
import unittest

from estates.generate import generate
from estates.validate import validate


PROFILES = ("regional-bank", "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone")


class IPv4FamilyValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {profile: generate({"profile": profile, "ipv6_pool": "2001:db8::/32"}) for profile in PROFILES}

    def copy(self, profile):
        plan = deepcopy(self.plans[profile])
        return plan, {obj["key"]: obj for obj in plan["objects"]}

    def codes(self, plan):
        return {finding["code"] for finding in validate(plan)}

    def addresses(self, plan, port, version):
        return [obj for obj in plan["objects"] if obj["kind"] == "ip_address"
                and obj["refs"].get("assigned_object") == port and ip_interface(obj["attrs"]["address"]).version == version]

    def test_all_profiles_accept_both_families_independent_of_object_order(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                self.assertEqual(validate(plan), [])
                shuffled = deepcopy(plan)
                shuffled["objects"].reverse()
                self.assertEqual(validate(shuffled), [])

    def test_provider_still_requires_exactly_one_ipv4_per_routed_end(self):
        for mutation in ("missing", "extra"):
            with self.subTest(mutation=mutation):
                plan, objects = self.copy("provider-backbone")
                port = next(key for key, obj in objects.items() if obj["kind"] == "interface" and key.endswith("/if/et-0/0/0"))
                address = self.addresses(plan, port, 4)[0]
                self.assertEqual(len(self.addresses(plan, port, 6)), 1)
                if mutation == "missing":
                    plan["objects"].remove(address)
                else:
                    extra = deepcopy(address)
                    extra["key"] = "ip/extra-routed-ipv4"
                    plan["objects"].append(extra)
                self.assertIn("provider-routed-address", self.codes(plan))

    def test_provider_unaddressed_management_port_rejects_ipv6_too(self):
        plan, objects = self.copy("provider-backbone")
        port = next(key for key, obj in objects.items() if obj["kind"] == "interface" and key.endswith("/if/fxp0"))
        router = objects[port]["refs"]["device"]
        extra = deepcopy(objects[objects[router]["refs"]["primary_ip6"]])
        extra["key"] = "ipv6/extra-fxp0"
        extra["refs"]["assigned_object"] = port
        plan["objects"].append(extra)
        self.assertIn("provider-management-mode", self.codes(plan))

    def test_dc_listeners_require_exact_requested_primary_family_order(self):
        for mutation in ("missing-v6", "reversed", "borrowed", "unrequested"):
            with self.subTest(mutation=mutation):
                plan, objects = self.copy("enterprise-data-center")
                service = next(obj for obj in plan["objects"] if obj["kind"] == "service")
                vm = objects[service["refs"]["virtual_machine"]]
                self.assertEqual(service["refs"]["ipaddresses"], [vm["refs"]["primary_ip4"], vm["refs"]["primary_ip6"]])
                if mutation == "missing-v6":
                    service["refs"]["ipaddresses"].pop()
                elif mutation == "reversed":
                    service["refs"]["ipaddresses"].reverse()
                elif mutation == "borrowed":
                    other = next(obj for obj in plan["objects"] if obj["kind"] == "virtual_machine" and obj["key"] != vm["key"])
                    # Matching an equally forged primary ref does not make this
                    # address belong to the current VM's inventory interface.
                    vm["refs"]["primary_ip6"] = other["refs"]["primary_ip6"]
                    service["refs"]["ipaddresses"][1] = other["refs"]["primary_ip6"]
                else:
                    plan["recipe"].pop("ipv6_pool")
                self.assertIn("dc-workload-listener", self.codes(plan))

    def test_ipv6_does_not_replace_dc_ipv4_gateway_witness(self):
        plan, objects = self.copy("enterprise-data-center")
        ports = [obj["key"] for obj in plan["objects"] if obj["kind"] == "interface"
                 and obj["refs"].get("untagged_vlan") == "vlan/dc-01/applications"
                 and obj["attrs"].get("type") == "virtual"]
        self.assertTrue(ports)
        for port in ports:
            self.assertTrue(self.addresses(plan, port, 6))
            for address in self.addresses(plan, port, 4):
                plan["objects"].remove(address)
        self.assertIn("dc-gateway-inventory", self.codes(plan))

    def test_bank_fhrp_vip_remains_ipv4_only(self):
        for mutation in ("extra-v6", "replace-v4"):
            with self.subTest(mutation=mutation):
                plan, objects = self.copy("regional-bank")
                group = next(obj for obj in plan["objects"] if obj["kind"] == "fhrp_group")
                vip = self.addresses(plan, group["key"], 4)[0]
                extra = deepcopy(vip)
                extra.update(key="ipv6/unsupported-fhrp-vip")
                extra["attrs"]["address"] = "2001:db8::f/64"
                plan["objects"].append(extra)
                if mutation == "replace-v4":
                    plan["objects"].remove(vip)
                self.assertIn("fhrp-address", self.codes(plan))

    def test_recovery_tunnel_retains_one_ipv4_inside_and_ipv4_outside(self):
        for mutation in ("extra-inside-v4", "v6-outside"):
            with self.subTest(mutation=mutation):
                plan, objects = self.copy("regional-bank")
                term = next(obj for obj in plan["objects"] if obj["kind"] == "tunnel_termination")
                if mutation == "extra-inside-v4":
                    extra = deepcopy(self.addresses(plan, term["refs"]["termination"], 4)[0])
                    extra["key"] = "ip/extra-recovery-ipv4"
                    plan["objects"].append(extra)
                    expected = "tunnel-inside-subnet"
                else:
                    parent = objects[term["refs"]["outside_ip"]]["refs"]["assigned_object"]
                    term["refs"]["outside_ip"] = self.addresses(plan, parent, 6)[0]["key"]
                    expected = "tunnel-outside-ip"
                self.assertIn(expected, self.codes(plan))


if __name__ == "__main__":
    unittest.main()
