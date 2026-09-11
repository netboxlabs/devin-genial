"""Actual WLAN dependencies, ownership boundaries and address capacity."""

from copy import deepcopy
from ipaddress import ip_interface, ip_network
from types import SimpleNamespace
import unittest

from estates.generate import generate
from estates.model import DesignError
from estates.wireless_context import enrich
from estates.validate_wireless_context import validate


def context(plan):
    result = deepcopy(plan)
    world = SimpleNamespace(recipe=result["recipe"], contracts=result["contracts"],
                            objects={obj["key"]: obj for obj in result["objects"]})
    enrich(world)
    return result


class WirelessContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = context(generate(dict(branches={"small": 2}, headquarters=0,
                                        site_designs={"br-s0002": "inherited"})))
        cls.school = context(generate(dict(profile="school-district", ipv6_pool="2001:db8::/32", schools=[
            dict(key="oak", classrooms=1, administrative_staff=0, lab_seats=0,
                 wireless={"classroom-001": {"managed": 20, "guest": 12}})])))
        cls.hospital = context(generate(dict(profile="hospital-clinics", hospitals=[
            dict(key="central", administrative_desks=0, imaging_rooms=0,
                 wards=[dict(key="medical", beds=4, clinical_desks=2)],
                 wireless={"reception": {"managed": 4, "guest": 12}})], clinics=[])))

    def entry(self, plan, wlan):
        return next(entry for contract in plan["contracts"] for entry in contract.get("wireless_services", [])
                    if entry["wlan"] == wlan)

    def mutation(self, change, code, source=None):
        plan = deepcopy(self.bank if source is None else source)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        change(plan, objects)
        self.assertIn(code, {finding["code"] for finding in validate(plan)})

    def test_profiles_have_actual_bounded_dependencies_and_guest_has_no_radius(self):
        for plan in (self.bank, self.school, self.hospital):
            self.assertEqual(validate(plan), [])
            objects = {obj["key"]: obj for obj in plan["objects"]}
            for contract in plan["contracts"]:
                for entry in contract.get("wireless_services", []):
                    self.assertTrue(all(len(entry[field]) <= 2 for field in ("dns_tcp", "dns_udp", "radius_udp")))
                    if objects[entry["wlan"]]["attrs"]["auth_type"] == "open":
                        self.assertEqual(entry["radius_udp"], [])
                    self.assertFalse(any(obj["kind"] == "contact_assignment" and obj["refs"]["object"] == entry["wlan"] for obj in objects.values()))
        entry = self.entry(self.bank, "wireless-lan/br-s0001/staff")
        objects = {obj["key"]: obj for obj in self.bank["objects"]}
        for family in ("dns_tcp", "dns_udp", "radius_udp"):
            sites = {objects[objects[objects[key]["refs"]["virtual_machine"]]["refs"]["device"]]["refs"]["site"] for key in entry[family]}
            self.assertEqual(sites, {"site/dc-01", "site/dc-02"})
        for profile in ("enterprise-data-center", "provider-backbone"):
            plan = context(generate({"profile": profile}))
            self.assertEqual(validate(plan), [])
            self.assertFalse(any(contract.get("wireless_services") for contract in plan["contracts"]))

    def test_independent_bank_does_not_borrow_corporate_services_and_acquisition_resolves_it(self):
        wlan = "wireless-lan/br-s0002/staff"
        entry = self.entry(self.bank, wlan)
        self.assertEqual(entry["external_required"], ["dns", "radius"])
        self.assertEqual(entry["technical_contact"], "contact/operations/tenant/inherited")
        self.assertEqual([entry[field] for field in ("dns_tcp", "dns_udp", "radius_udp")], [[], [], []])
        recipe = deepcopy(self.bank["recipe"])
        recipe["acquired_sites"] = ["br-s0002"]
        acquired = context(generate(recipe))
        self.assertEqual(validate(acquired), [])
        current = self.entry(acquired, wlan)
        self.assertEqual(current["external_required"], [])
        self.assertEqual(current["technical_contact"], "contact/operations")
        self.assertTrue(all(current[field] for field in ("dns_tcp", "dns_udp", "radius_udp")))

    def test_missing_contracts_and_wrong_or_duplicate_entries_are_rejected(self):
        self.mutation(lambda p, o: p.update(contracts=[]), "wireless-context-contract")
        self.mutation(lambda p, o: p["contracts"][-1]["wireless_services"].append(deepcopy(p["contracts"][-1]["wireless_services"][0])), "wireless-context-contract")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/br-s0001/staff").update(prefix="prefix/br-s0002/users"), "wireless-context-prefix")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/br-s0001/staff").update(available_ipv4=999), "wireless-context-capacity")

    def test_support_must_follow_actual_site_and_ap_assignments(self):
        self.mutation(lambda p, o: o["contact-assignment/site/br-s0001"]["refs"].update(contact="contact/operations/tenant/inherited"), "wireless-context-support")
        ap = next(obj["key"] for obj in self.bank["objects"] if obj["kind"] == "device"
                  and obj["refs"].get("role") == "role/ap" and obj["refs"]["site"] == "site/br-s0001")
        self.mutation(lambda p, o: o[f"contact-assignment/{ap}"]["refs"].update(contact="contact/operations/tenant/inherited"), "wireless-context-support")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/br-s0001/staff").update(technical_contact="contact/site/br-s0001"), "wireless-context-support")

    def test_service_membership_ports_tenant_host_and_primary_binding_are_checked(self):
        entry = self.entry(self.bank, "wireless-lan/br-s0001/staff")
        service = entry["radius_udp"][0]
        objects = {obj["key"]: obj for obj in self.bank["objects"]}
        vm = objects[service]["refs"]["virtual_machine"]
        host, primary = (objects[vm]["refs"][field] for field in ("device", "primary_ip4"))
        cases = [lambda p, o: o[service]["attrs"].update(ports=[1812]),
                 lambda p, o: o[service]["attrs"].update(protocol="tcp"),
                 lambda p, o: o[service]["refs"].update(ipaddresses=[]),
                 lambda p, o: o[vm]["refs"].update(tenant="tenant/inherited"),
                 lambda p, o: o[host]["attrs"].update(status="offline"),
                 lambda p, o: o[primary]["attrs"].update(status="reserved"),
                 lambda p, o: o[primary]["refs"].update(assigned_object="vm/dc-01/dns/001/eth0"),
                 lambda p, o: self.entry(p, "wireless-lan/br-s0001/staff")["dns_tcp"].pop()]
        for change in cases:
            self.mutation(change, "wireless-context-service")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/br-s0002/staff").update(radius_udp=entry["radius_udp"]), "wireless-context-service")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/br-s0001/staff").update(external_required=["dns", "radius"]), "wireless-context-external")

    def test_guest_cannot_acquire_radius_and_dual_stack_listener_must_bind_its_primary(self):
        staff = self.entry(self.school, "wireless-lan/school-oak/staff")
        self.mutation(lambda p, o: self.entry(p, "wireless-lan/school-oak/guest").update(radius_udp=staff["radius_udp"]), "wireless-context-service", self.school)
        service = staff["dns_udp"][0]
        self.mutation(lambda p, o: o[service]["refs"]["ipaddresses"].pop(), "wireless-context-service", self.school)

    def test_capacity_counts_unique_allocations_and_overlapping_held_ranges_in_the_actual_vrf(self):
        plan = deepcopy(self.bank)
        prefix = next(obj for obj in plan["objects"] if obj["key"] == "prefix/br-s0001/users")
        net, vrf = ip_network(prefix["attrs"]["prefix"]), prefix["refs"]["vrf"]
        for name, first, last in (("one", 140, 160), ("two", 150, 170)):
            plan["objects"].append(dict(key=f"range/test/{name}", kind="ip_range", meta={},
                attrs=dict(start_address=f"{net[first]}/24", end_address=f"{net[last]}/24", status="reserved"), refs=dict(vrf=vrf)))
        original = next(obj for obj in plan["objects"] if obj["kind"] == "ip_address" and obj["refs"].get("vrf") == vrf and ip_interface(obj["attrs"]["address"]).ip in net)
        plan["objects"].append(deepcopy(original) | {"key": "ip/test/duplicate"})
        updated = context(plan)
        available = set(net.hosts())
        for obj in updated["objects"]:
            if obj["refs"].get("vrf") != vrf:
                continue
            if obj["kind"] == "ip_address":
                available.discard(ip_interface(obj["attrs"]["address"]).ip)
            elif obj["kind"] == "ip_range":
                first, last = (ip_interface(obj["attrs"][field]).ip for field in ("start_address", "end_address"))
                available = {address for address in available if not first <= address <= last}
        self.assertEqual(self.entry(updated, "wireless-lan/br-s0001/staff")["available_ipv4"], len(available))
        self.assertEqual(validate(updated), [])

    def test_accurately_reported_guest_exhaustion_cannot_waive_recipe_demand(self):
        recipe = deepcopy(self.hospital["recipe"])
        recipe["clinics"] = [dict(key="west", exam_rooms=1, administrative_desks=0, imaging_rooms=0,
                                  wireless={"reception": {"managed": 4, "guest": 12}})]
        clinic = generate(recipe)
        for source, site in ((self.school, "school-oak"), (self.hospital, "hospital-central"), (clinic, "clinic-west")):
            for remaining in (0, 12):
                with self.subTest(site=site, remaining=remaining):
                    plan = deepcopy(source)
                    wlan = f"wireless-lan/{site}/guest"
                    entry = self.entry(plan, wlan)
                    prefix = next(obj for obj in plan["objects"] if obj["key"] == entry["prefix"])
                    net = ip_network(prefix["attrs"]["prefix"])
                    plan["objects"].append(dict(key=f"range/test/{site}/held", kind="ip_range", meta={},
                        attrs=dict(start_address=f"{net[1]}/{net.prefixlen}",
                                   end_address=f"{net[-2-remaining]}/{net.prefixlen}", status="reserved"),
                        refs=dict(vrf=prefix["refs"]["vrf"])))
                    entry["available_ipv4"] = remaining
                    if remaining == 0:
                        self.assertEqual({(finding["code"], finding["object"]) for finding in validate(plan)},
                                         {("wireless-context-capacity", wlan)})
                        with self.assertRaisesRegex(DesignError, "guest demand 12 exceeds 0 available IPv4"):
                            context(plan)
                    else:
                        self.assertEqual(validate(plan), [])
                        self.assertEqual(self.entry(context(plan), wlan)["available_ipv4"], 12)

    def test_optional_empty_records_do_not_crash_or_waive_actual_wlan_contracts(self):
        self.assertEqual(validate({"objects": [None, {"key": "bare", "kind": "site"},
                                                {"key": "bad", "kind": "device", "attrs": []}]}), [])
        plan = deepcopy(self.school)
        plan["objects"].extend([None, {"key": "bare", "kind": "site"},
                                {"key": "bad", "kind": "device", "refs": []}])
        plan["contracts"] = []
        expected = {obj["key"] for obj in self.school["objects"] if obj["kind"] == "wireless_lan"}
        self.assertEqual({finding["object"] for finding in validate(plan) if finding["code"] == "wireless-context-contract"}, expected)

    def test_enrichment_is_repeatable_and_changes_no_objects_except_wlan_comments(self):
        repeated = context(self.bank)
        self.assertEqual(repeated, self.bank)
        world = SimpleNamespace(recipe=deepcopy(self.bank["recipe"]), contracts=deepcopy(self.bank["contracts"]),
                                objects={obj["key"]: deepcopy(obj) for obj in self.bank["objects"]})
        before = deepcopy(world.objects)
        for obj in world.objects.values():
            if obj["kind"] == "wireless_lan":
                obj["attrs"]["comments"] = "Old planning comment"
        enrich(world)
        self.assertEqual(world.objects, before)
        self.assertFalse(any(obj["kind"] == "service" and 67 in obj["attrs"].get("ports", []) for obj in world.objects.values()))

    def test_missing_owned_listener_fails_generation_and_comments_cannot_claim_old_dependencies(self):
        world = SimpleNamespace(recipe=deepcopy(self.bank["recipe"]), contracts=deepcopy(self.bank["contracts"]),
                                objects={obj["key"]: deepcopy(obj) for obj in self.bank["objects"]
                                         if not (obj["kind"] == "service" and obj["attrs"]["protocol"] == "udp" and obj["attrs"]["ports"] == [1812, 1813])})
        with self.assertRaisesRegex(DesignError, "radius_udp"):
            enrich(world)
        self.mutation(lambda p, o: o["wireless-lan/br-s0001/staff"]["attrs"].update(comments="Services are running"), "wireless-context-comments")


if __name__ == "__main__":
    unittest.main()
