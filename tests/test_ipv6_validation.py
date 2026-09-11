"""Requested IPv6 coverage cannot be erased by deleting its emitted evidence."""

from copy import deepcopy
from ipaddress import ip_interface
import unittest

from estates.generate import generate
from estates.intent import resolution
from estates.model import DesignError, resolve_recipe
from estates.scenarios import create
from estates.validate import validate
from estates.validate_ipv6 import validate as check_ipv6


PROFILES = ("regional-bank", "enterprise-data-center", "school-district",
            "hospital-clinics", "provider-backbone")


class IPv6ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {p: generate(dict(profile=p, ipv6_pool="2001:db8::/32")) for p in PROFILES}

    def stripped(self, profile="provider-backbone"):
        plan = deepcopy(self.plans[profile])
        plan["contracts"] = []
        for obj in plan["objects"]:
            obj["meta"] = {}
        return plan, {o["key"]: o for o in plan["objects"]}

    def test_all_profiles_validate_and_cannot_omit_the_entire_requested_family(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                self.assertEqual(validate(plan), [])
                self.assertEqual(generate(plan["recipe"], previous=plan), plan)
                bare, _ = self.stripped(profile)
                self.assertEqual(check_ipv6(bare), [])
                bare["objects"] = [o for o in bare["objects"] if not o["key"].startswith("ipv6/")]
                for obj in bare["objects"]:
                    obj["refs"].pop("primary_ip6", None)
                    if obj["kind"] == "service":
                        obj["refs"]["ipaddresses"] = [a for a in obj["refs"]["ipaddresses"] if not a.startswith("ipv6/")]
                bare["reservations"] = {k:v for k,v in bare["reservations"].items() if not k.startswith("ipv6-")}
                self.assertIn("ipv6-reservation", {f["code"] for f in check_ipv6(bare)})

    def test_mutations_fail_without_metadata_or_contracts(self):
        def first(objects, kind, predicate=lambda o: True):
            return next(o for o in objects.values() if o["kind"] == kind and predicate(o))

        def ip(objects):
            return first(objects, "ip_address", lambda o: o["key"].startswith("ipv6/"))

        def leaf(objects):
            return first(objects, "prefix", lambda o: o["key"].startswith("ipv6/") and o["refs"].get("vlan"))

        def service(objects):
            return first(objects, "service")

        def changed_host(plan, objects):
            obj = ip(objects)
            value = ip_interface(obj["attrs"]["address"])
            obj["attrs"]["address"] = f"{value.ip+1}/{value.network.prefixlen}"

        def fabricated_transit(plan, objects):
            obj = deepcopy(first(objects, "ip_address", lambda o: o["key"].startswith("ipv6/") and
                o["refs"].get("assigned_object", "").endswith("/if/xe-0/1/7")))
            obj["key"] = "remote/transit/address"
            value = ip_interface(obj["attrs"]["address"])
            obj["attrs"]["address"] = f"{value.ip+1}/127"
            plan["objects"].append(obj)

        def duplicate_slot(plan, objects):
            slots = plan["reservations"]["ipv6-sites"]
            a, b = list(slots)[:2]
            slots[b] = slots[a]

        def vm_routed(plan, objects):
            address = first(objects, "ip_address", lambda o: o["key"].startswith("ipv6/") and
                            o["attrs"]["address"].endswith("/127"))
            port = first(objects, "vm_interface")["key"]
            address["refs"]["assigned_object"] = port
            objects[address["key"].removeprefix("ipv6/")]["refs"]["assigned_object"] = port

        mutations = [
            ("missing-ip", "ipv6-address", lambda p,o: p["objects"].remove(ip(o))),
            ("host-ordinal", "ipv6-address", changed_host),
            ("wrong-vrf", "ipv6-address", lambda p,o: ip(o)["refs"].update(vrf="vrf/management")),
            ("wrong-tenant", "ipv6-address", lambda p,o: ip(o)["refs"].update(tenant="tenant/unknown")),
            ("wrong-owner", "ipv6-address", lambda p,o: ip(o)["refs"].update(assigned_object="not-a-port")),
            ("duplicate-slot", "ipv6-reservation", duplicate_slot),
            ("invalid-slot", "ipv6-reservation", lambda p,o: p["reservations"]["ipv6-sites"].update({"site/dc-01": True})),
            ("unknown-ledger", "ipv6-reservation", lambda p,o: p["reservations"].update({"ipv6-mystery": {"x": 0}})),
            ("missing-prefix", "ipv6-prefix", lambda p,o: p["objects"].remove(leaf(o))),
            ("LAN-127", "ipv6-prefix", lambda p,o: leaf(o)["attrs"].update(prefix="2001:db8::/127")),
            ("registry", "ipv6-registry", lambda p,o: o["ipv6/rir"]["attrs"].update(slug="different")),
            ("aggregate", "ipv6-aggregate", lambda p,o: o["ipv6/aggregate"]["attrs"].update(prefix="2001:db8::/40")),
            ("missing-primary", "ipv6-primary", lambda p,o: first(o,"device",lambda d:"primary_ip6" in d["refs"])["refs"].pop("primary_ip6")),
            ("missing-listener", "ipv6-service", lambda p,o: service(o)["refs"]["ipaddresses"].pop()),
            ("borrowed-listener", "ipv6-service", lambda p,o: service(o)["refs"]["ipaddresses"].__setitem__(1,ip(o)["key"])),
            ("remote-transit", "ipv6-unexpected", fabricated_transit),
            ("ordinary-vm-routed", "ipv6-policy", vm_routed),
            ("loopback-outside", "ipv6-prefix", lambda p,o: first(o,"prefix",lambda d:d["key"].startswith("ipv6/prefix/loopback/"))["attrs"].update(prefix="2001:db8::1/128")),
        ]
        for name, code, mutate in mutations:
            with self.subTest(mutation=name):
                plan, objects = self.stripped()
                mutate(plan, objects)
                self.assertIn(code, {f["code"] for f in check_ipv6(plan)})

    def test_missing_gateway_and_invented_ipv6_fhrp_fail(self):
        plan, objects = self.stripped("regional-bank")
        gateway = next(o for o in objects.values() if o["kind"] == "interface" and
                       o["attrs"].get("type") == "virtual" and o["refs"].get("untagged_vlan"))
        address = next(o for o in objects.values() if o["kind"] == "ip_address" and
                       o["key"].startswith("ipv6/") and o["refs"].get("assigned_object") == gateway["key"])
        plan["objects"].remove(address)
        self.assertIn("ipv6-address", {f["code"] for f in check_ipv6(plan)})
        plan, objects = self.stripped("regional-bank")
        vip = deepcopy(next(o for o in objects.values() if o["kind"] == "ip_address" and
                   objects[o["refs"]["assigned_object"]]["kind"] == "fhrp_group"))
        vip["key"], vip["attrs"]["address"] = "ipv6/vip", "2001:db8::ff/64"
        plan["objects"].append(vip)
        self.assertIn("ipv6-unexpected", {f["code"] for f in check_ipv6(plan)})

    def test_correlated_address_tenants_cannot_override_the_actual_segment_or_vm(self):
        plan, objects = self.stripped()
        vm = next(o for o in objects.values() if o["kind"] == "virtual_machine")
        for family in (4, 6):
            objects[vm["refs"][f"primary_ip{family}"]]["refs"]["tenant"] = "tenant/cust-harbor-logistics"
        self.assertIn("ipv6-address-owner", {f["code"] for f in check_ipv6(plan)})
        self.assertIn("ip-tenant", {f["code"] for f in validate(plan)})

    def test_unassigned_inventory_stays_ipv4_only_and_radio_prefix_needs_real_ends(self):
        plan, objects = self.stripped("regional-bank")
        prefix = next(o for o in objects.values() if o["kind"] == "prefix" and
                      not o["key"].startswith("ipv6/") and o["refs"].get("vlan"))
        plan["objects"].append(dict(kind="ip_address",key="ip/unassigned-reservation",meta={},
            attrs=dict(address=prefix["attrs"]["prefix"],status="reserved"),
            refs={k:prefix["refs"][k] for k in ("tenant","vrf")}))
        self.assertEqual(check_ipv6(plan), [])
        radio = next(o for o in objects.values() if o["kind"] == "prefix" and
                     o["key"].endswith("/radio-transit") and not o["key"].startswith("ipv6/"))
        plan["objects"].append(dict(radio,key="prefix/fake/radio-transit",attrs=dict(radio["attrs"],prefix="10.1.254.0/31")))
        self.assertIn("ipv6-policy", {f["code"] for f in check_ipv6(plan)})

    def test_all_public_inputs_preserve_optional_field_and_baseline_boundary(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                recipe = dict(plan["recipe"], ipv6_pool="2001:0DB8:0:0::/32")
                self.assertEqual(resolve_recipe(recipe), plan["recipe"])
                self.assertEqual(resolution(plan, recipe)["resolved"]["ipv6_pool"]["source"], "supplied")
                for change in ({}, {"ipv6_pool": "3fff:a::/32"}):
                    recipe = {k:v for k,v in plan["recipe"].items() if k != "ipv6_pool"}|change
                    with self.assertRaisesRegex(DesignError, "ipv6_pool"):
                        generate(recipe, previous=plan)
                for value in (None, False, "", "fd00::/32"):
                    with self.assertRaisesRegex(DesignError, "ipv6_pool"):
                        resolve_recipe(dict(plan["recipe"], ipv6_pool=value))
                before = generate({k:v for k,v in plan["recipe"].items() if k != "ipv6_pool"})
                with self.assertRaisesRegex(DesignError, "ipv6_pool"):
                    generate(plan["recipe"], previous=before)

    def test_all_profile_growth_retains_existing_ipv6_objects_and_slots(self):
        for profile, previous in self.plans.items():
            recipe = deepcopy(previous["recipe"])
            if profile == "regional-bank":
                recipe["branches"]["medium"] += 1
            elif profile == "enterprise-data-center":
                recipe["data_centers"] += 1
                workload = deepcopy(recipe["workloads"][0]); workload["key"] = "aaa-new"
                workload["listeners"] = [dict(key="",name="aaa-new",protocol="tcp",ports=[443])]
                recipe["workloads"].append(workload)
            elif profile == "school-district":
                recipe["schools"][0]["classrooms"] += 1
            elif profile == "hospital-clinics":
                recipe["hospitals"][0]["wards"].append(dict(key="aaa-new",beds=4,clinical_desks=2))
            else:
                recipe["pops"].append(dict(key="aaa-new",metro="milwaukee"))
                recipe["customers"][0]["lan_endpoints"] += 1
            with self.subTest(profile=profile):
                grown = generate(recipe, previous=previous)
                self.assertEqual(validate(grown), [])
                old = {o["key"]: o for o in previous["objects"] if o["key"].startswith("ipv6/")}
                new = {o["key"]: o for o in grown["objects"]}
                self.assertEqual({k:new.get(k) for k in old}, old)
                for scope, slots in previous["reservations"].items():
                    if scope.startswith("ipv6-"):
                        self.assertEqual({k:grown["reservations"][scope][k] for k in slots}, slots)

    def test_acquisition_and_refresh_keep_both_families_in_the_actual_tenant_scope(self):
        plan = generate(dict(headquarters=0, branches={"small":1}, site_designs={"br-s0001":"inherited"},
                             ipv6_pool="3fff:a::/40"))
        scenario = create(plan, "br-s0001")
        for stage, snapshot in scenario["plans"].items():
            self.assertEqual(validate(snapshot), [], stage)
            prefix = next(o for o in snapshot["objects"] if o["key"] == "ipv6/prefix/br-s0001/users")
            self.assertEqual(prefix["refs"]["tenant"], "tenant/inherited" if stage == "before" else "tenant")


if __name__ == "__main__":
    unittest.main()
