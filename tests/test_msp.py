"""MSP composition must follow managed demand and keep every customer separate."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.generate import generate
from estates.load import main as load_main
from estates.model import DesignError, ROOT, canonical, hardware_catalog, recipe_from_file
from estates.report import markdown
from estates.validate import validate
from estates.__main__ import main


SMALL = dict(customers=[dict(key="summit-legal", offices=1, staff=14),
                        dict(key="harbor-dental", offices=1, staff=8)])


def plan_for(**changes):
    return generate({"profile": "msp"} | changes)


def small(**changes):
    return plan_for(**(deepcopy(SMALL) | changes))


class MspResolverTests(unittest.TestCase):
    def test_unknown_and_out_of_range_requests_are_actionable(self):
        cases = [
            ({"schools": []}, "Unknown MSP recipe fields: schools"),
            ({"stores": {}}, "Unknown MSP recipe fields: stores"),
            ({"buildings": []}, "Unknown MSP recipe fields: buildings"),
            ({"headquarters_staff": 100}, "Unknown MSP recipe fields: headquarters_staff"),
            ({"design_mix": {"modern": 1}}, "Unknown MSP recipe fields: design_mix"),
            ({"acquired_sites": []}, "Unknown MSP recipe fields: acquired_sites"),
            ({"wan_peak_mbps": 2000}, "Unknown MSP recipe fields: wan_peak_mbps"),
            ({"customers": {}}, "customers must be a list"),
            ({"customers": []}, "customers must be a list"),
            ({"customers": [dict(key=f"c{n:02}") for n in range(25)]}, "customers must be a list"),
            ({"customers": [{"offices": 1}]}, "needs a stable key"),
            ({"customers": [dict(key="a", branches=2)]}, "only supported demand fields"),
            ({"customers": [dict(key="A-Corp")]}, "hyphen-separated lowercase identifiers"),
            ({"customers": [dict(key="acme-")]}, "no leading, trailing or doubled hyphen"),
            ({"customers": [dict(key="ac--me")]}, "no leading, trailing or doubled hyphen"),
            ({"customers": [dict(key="a"), dict(key="a")]}, "hyphen-separated lowercase identifiers"),
            ({"customers": [dict(key="a-b"), dict(key="ab")]}, "distinct without hyphens"),
            ({"customers": [dict(key="a", offices=0)]}, "offices must be an integer from 1 through 4"),
            ({"customers": [dict(key="a", offices=5)]}, "offices must be an integer from 1 through 4"),
            ({"customers": [dict(key="a", offices=1.0)]}, "offices must be an integer from 1 through 4"),
            ({"customers": [dict(key="a", staff=3)]}, "staff must be an integer from 4 through 48"),
            ({"customers": [dict(key="a", staff=49)]}, "staff must be an integer from 4 through 48"),
            ({"customers": [dict(key="a", staff=True)]}, "staff must be an integer from 4 through 48"),
            ({"address_pool": "10.0.0.0/21"}, "address_pool must be an aligned RFC1918"),
            ({"address_pool": "10.1.0.0/18"}, "must hold /16 site reservations"),
            ({"reservation_user": "someone"}, "rack-user reservations are not implemented"),
            ({"demo": "provider-span-maintenance"}, "demo supports baseline"),
            ({"namespace": "X"}, "namespace must be a 2"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(DesignError, message):
                plan_for(**changes)

    def test_supported_bounds_are_accepted_at_their_edges(self):
        widest = plan_for(customers=[dict(key="edge-corp", offices=4, staff=48)])
        self.assertEqual(validate(widest), [])
        self.assertEqual(len([o for o in widest["objects"] if o["kind"] == "site"]), 5)
        narrowest = plan_for(customers=[dict(key="one", offices=1, staff=4)])
        self.assertEqual(validate(narrowest), [])
        self.assertEqual(len([o for o in narrowest["objects"] if o["kind"] == "site"]), 2)

    def test_wireless_zone_requests_are_bounded_and_named(self):
        cases = [
            ({"reception": {"managed": 4}, "pod-09": {"managed": 4}},
             "wireless must map existing zones"),
            ({"reception": {"visitors": 4}}, "only managed and guest device counts"),
            ({"reception": {"managed": 129}}, "managed/guest must be integers 0"),
            ({"reception": {"managed": 100, "guest": 40}}, "total at most 128"),
        ]
        for wireless, message in cases:
            with self.subTest(wireless=wireless), self.assertRaisesRegex(DesignError, message):
                plan_for(customers=[dict(key="acme", offices=1, staff=24, wireless=wireless)])
        crowded = {"reception": {"guest": 51}} | {f"pod-{n+1:02}": {"guest": 51} for n in range(4)}
        with self.assertRaisesRegex(DesignError, "exceed the 252 addresses"):
            plan_for(customers=[dict(key="acme", offices=1, staff=48, wireless=crowded)])

    def test_defaults_are_frozen_into_the_recipe(self):
        recipe = plan_for()["recipe"]
        self.assertEqual(recipe["profile"], "msp")
        self.assertEqual(recipe["namespace"], "arbor")
        self.assertEqual(recipe["name"], "Arbor Managed Networks")
        self.assertEqual(recipe["demo"], "baseline")
        self.assertEqual([item["key"] for item in recipe["customers"]],
                         ["brightline-media", "cornerstone-realty", "harbor-dental", "summit-legal"])
        summit = next(item for item in recipe["customers"] if item["key"] == "summit-legal")
        self.assertEqual(summit["offices"], 2)
        self.assertEqual(summit["staff"], 24)
        # Two twelve-desk pods plus the reception, each with an explicit budget.
        self.assertEqual(set(summit["wireless"]), {"reception", "pod-01", "pod-02"})
        self.assertEqual(summit["wireless"]["pod-01"], {"managed": 24, "guest": 0})
        self.assertEqual(summit["wireless"]["reception"], {"managed": 6, "guest": 12})

    def test_shipped_profile_resolves_and_validates(self):
        plan = generate(recipe_from_file(ROOT / "profiles/msp.toml"))
        self.assertEqual(validate(plan), [])
        self.assertEqual(len(plan["recipe"]["customers"]), 4)
        self.assertEqual(len([o for o in plan["objects"] if o["kind"] == "site"]), 9)


class MspCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = small()

    def objects(self, plan=None):
        return {o["key"]: o for o in (plan or self.plan)["objects"]}

    def test_managed_estate_connects_its_own_demand_without_other_industries(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(canonical(generate(deepcopy(self.plan["recipe"]))["objects"]),
                         canonical(self.plan["objects"]))
        objects = self.objects()
        self.assertEqual({key for key, obj in objects.items() if obj["kind"] == "site"},
                         {"site/noc-01", "site/off-summit-legal-01", "site/off-harbor-dental-01"})
        services = {key.split("/")[2] for key, obj in objects.items() if obj["kind"] == "virtual_machine"}
        self.assertEqual(services, {"monitoring", "rmm", "helpdesk", "identity", "dns",
                                    "inventory-db", "backup"})
        blob = canonical(self.plan).decode()
        for forbidden in ("teller", "classroom", "patient", "point-of-sale", "lecture hall",
                          "residence room", "ATM"):
            self.assertNotIn(forbidden, blob, forbidden)
        for required in ("managed-network contract", "Staff office pod", "Reception"):
            self.assertIn(required, blob, required)
        guide = markdown(self.plan)
        for phrase in ("staff desks", "office pods", "Replica group placement"):
            self.assertIn(phrase, guide)

    def test_customer_office_places_desks_pods_and_radios_on_its_own_segments(self):
        objects = self.objects()
        desks = [key for key, obj in objects.items()
                 if obj["kind"] == "device" and obj["refs"].get("role") == "role/workstation"
                 and key.startswith("device/off-summit-legal-01/")]
        self.assertEqual(len(desks), 14)
        self.assertEqual(objects["device/off-summit-legal-01/desk-001"]["refs"]["location"],
                         "location/off-summit-legal-01/pod-01")
        self.assertEqual(objects["device/off-summit-legal-01/desk-013"]["refs"]["location"],
                         "location/off-summit-legal-01/pod-02")
        self.assertEqual(objects["device/off-summit-legal-01/desk-001"]["meta"]["network"], "staff")
        self.assertEqual(objects["device/off-summit-legal-01/ap-reception"]["refs"]["location"],
                         "location/off-summit-legal-01/reception")
        for role in ("management", "staff", "wireless", "security", "guest"):
            prefix = objects[f"prefix/off-summit-legal-01/{role}"]
            self.assertEqual(prefix["attrs"]["prefix"].split("/")[1], "24")
            self.assertEqual(prefix["refs"]["scope_site"], "site/off-summit-legal-01")
        # The provider's own operations site carries none of the office segments.
        self.assertNotIn("prefix/noc-01/staff", objects)

    def test_every_customer_is_its_own_tenant_inside_one_customer_group(self):
        objects = self.objects()
        tenants = {key for key, obj in objects.items() if obj["kind"] == "tenant"}
        self.assertEqual(tenants, {"tenant", "tenant/summit-legal", "tenant/harbor-dental"})
        group = objects["tenant-group/customers"]
        self.assertEqual(group["kind"], "tenant_group")
        self.assertEqual(group["attrs"]["name"], "arbor customers")
        for key in ("tenant/summit-legal", "tenant/harbor-dental"):
            self.assertEqual(objects[key]["refs"]["group"], "tenant-group/customers")
        for key in ("site/off-summit-legal-01", "device/off-summit-legal-01/access-01",
                    "vlan/off-summit-legal-01/staff", "prefix/off-summit-legal-01/staff",
                    "rack/off-summit-legal-01/network-01", "circuit/off-summit-legal-01/a/1"):
            self.assertEqual(objects[key]["refs"]["tenant"], "tenant/summit-legal", key)
        self.assertEqual(objects["site/noc-01"]["refs"]["tenant"], "tenant")

    def test_nothing_joins_two_customers(self):
        objects = self.objects()
        owners = {"site/off-summit-legal-01": "tenant/summit-legal",
                  "site/off-harbor-dental-01": "tenant/harbor-dental"}

        def owner(key):
            seen = set()
            while key in objects and key not in seen:
                if objects[key]["kind"] == "site":
                    return owners.get(key)
                seen.add(key)
                key = next((objects[key]["refs"][f] for f in
                            ("site", "scope_site", "device", "rack", "location",
                             "assigned_object", "termination")
                            if isinstance(objects[key]["refs"].get(f), str)), None)
            return None

        for cable in (o for o in self.plan["objects"] if o["kind"] == "cable"):
            ends = {owner(cable["refs"]["a"]), owner(cable["refs"]["b"])}
            self.assertEqual(len(ends), 1, cable["key"])
        # Every customer segment sits in a routing context that names only itself.
        for customer in ("summit-legal", "harbor-dental"):
            vrfs = {key for key in objects if key.startswith(f"vrf/customer/{customer}/")}
            self.assertTrue(vrfs)
            for vrf in vrfs:
                self.assertEqual(objects[vrf]["refs"]["tenant"], f"tenant/{customer}")
            users = {objects[key]["refs"].get("tenant") for key, obj in objects.items()
                     if obj["kind"] == "prefix" and obj["refs"].get("vrf") in vrfs}
            self.assertEqual(users, {f"tenant/{customer}"})

    def test_offices_are_owned_by_the_customer_and_operated_by_the_provider(self):
        objects = self.objects()
        desk = objects["contact-assignment/site/off-summit-legal-01"]
        self.assertEqual(desk["refs"]["contact"], "contact/operations/tenant/summit-legal")
        self.assertEqual(desk["refs"]["role"], "contact-role/operations")
        for device in ("access-01", "dist-a", "edge-a", "mgmt-01"):
            assignment = objects[f"contact-assignment/device/off-summit-legal-01/{device}"]
            self.assertEqual(assignment["refs"]["contact"], "contact/operations/tenant/summit-legal")
        self.assertIn("contact-group/operations",
                      objects["contact/operations/tenant/summit-legal"]["refs"]["groups"])
        site = objects["site/off-summit-legal-01"]
        self.assertIn("managed-network contract", site["attrs"]["comments"])
        self.assertIn(objects["prefix/off-summit-legal-01/management"]["attrs"]["prefix"],
                      site["attrs"]["comments"])
        self.assertIn("No service-level commitment, remote-access path or ticketing workflow",
                      site["attrs"]["comments"])
        self.assertEqual(site["meta"]["operated_by"]["provider"], "arbor")
        # The provider holds the carrier account; the customer holds the circuit.
        circuit = objects["circuit/off-summit-legal-01/a/1"]
        account = objects[circuit["refs"]["provider_account"]]
        self.assertEqual(account["refs"]["owner"], "owner/operations")
        self.assertNotIn("tenant", account["refs"])

    def test_wireless_serves_managed_staff_and_requested_visitors_but_never_the_noc(self):
        objects = self.objects()
        for sid in ("off-summit-legal-01", "off-harbor-dental-01"):
            for label in ("staff", "guest"):
                self.assertEqual(objects[f"wireless-lan/{sid}/{label}"]["kind"], "wireless_lan")
            self.assertEqual(objects[f"wireless-lan/{sid}/guest"]["attrs"]["auth_type"], "open")
            self.assertEqual(objects[f"wireless-lan/{sid}/staff"]["refs"]["tenant"],
                             objects[f"site/{sid}"]["refs"]["tenant"])
        self.assertNotIn("wireless-lan/noc-01/staff", objects)
        quiet = plan_for(customers=[dict(key="quiet-co", offices=1, staff=8,
                                         wireless={"reception": {"guest": 0}})])
        self.assertEqual(validate(quiet), [])
        keys = {o["key"] for o in quiet["objects"]}
        self.assertNotIn("wireless-lan/off-quiet-co-01/guest", keys)
        self.assertNotIn("vlan/off-quiet-co-01/guest", keys)
        # The provider's own service inventory serves every customer WLAN.
        contract = next(c for c in self.plan["contracts"] if c["site"] == "site/off-summit-legal-01")
        entry = next(e for e in contract["wireless_services"]
                     if e["wlan"] == "wireless-lan/off-summit-legal-01/staff")
        self.assertTrue(entry["dns_tcp"])
        for service in entry["dns_tcp"]:
            self.assertTrue(service.startswith("service/vm/noc-01/dns/"), service)

    def test_growth_appends_customers_offices_and_desks_without_moving_anything(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        summit = next(item for item in recipe["customers"] if item["key"] == "summit-legal")
        summit["offices"] = 3
        summit["staff"] = 30
        summit["wireless"]["reception"]["guest"] = 20
        recipe["customers"].append(dict(key="lakeside-clinic", offices=1, staff=8))
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old = {o["key"]: o for o in before["objects"]}
        new = {o["key"]: o for o in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        occupied = {end for o in before["objects"] if o["kind"] == "cable" for end in o["refs"].values()}
        for key, obj in old.items():
            if obj["kind"] == "power_port":
                self.assertGreaterEqual(new[key]["attrs"].get("maximum_draw", 0),
                                        obj["attrs"].get("maximum_draw", 0), key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
            elif obj["kind"] == "location":
                # Hiring fills the last staff pod, so a room's installed desk
                # capacity grows. Its identity, floor and position never move.
                self.assertEqual(new[key]["attrs"], obj["attrs"], key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
                for field in ("space_type", "floor", "position_m"):
                    self.assertEqual(new[key]["meta"].get(field), obj["meta"].get(field), key)
                self.assertGreaterEqual(new[key]["meta"].get("capacity", {}).get("workstations", 0),
                                        obj["meta"].get("capacity", {}).get("workstations", 0), key)
            elif obj["kind"] in {"site", "rack", "cable", "ip_address", "vlan", "prefix",
                                 "virtual_machine", "tenant", "vrf"} or key in occupied:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                for field in ("location", "rack", "site", "tenant"):
                    self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field), key)
        for scope, items in before["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_added_desks_never_renumber_existing_pods(self):
        before = small()
        old = {o["key"]: o for o in before["objects"]}
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["customers"] if item["key"] == "summit-legal")["staff"] = 44
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        new = {o["key"]: o for o in after["objects"]}
        for room in ("location/off-summit-legal-01/pod-01", "location/off-summit-legal-01/pod-02",
                     "location/off-summit-legal-01/reception"):
            self.assertEqual(new[room]["meta"]["position_m"], old[room]["meta"]["position_m"], room)
        self.assertEqual(new["device/off-summit-legal-01/desk-001"]["meta"]["placement"],
                         old["device/off-summit-legal-01/desk-001"]["meta"]["placement"])
        self.assertEqual(after["reservations"]["office-pods/off-summit-legal-01"]["pod-01"], 0)
        self.assertEqual(len(after["reservations"]["office-pods/off-summit-legal-01"]), 4)

    def test_radio_channels_and_endpoint_ports_survive_growth(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["customers"] if item["key"] == "harbor-dental")["staff"] = 24
        after = generate(recipe, previous=before)
        old_rf = {o["key"]: o["attrs"]["rf_channel"] for o in before["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        new_rf = {o["key"]: o["attrs"]["rf_channel"] for o in after["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        self.assertTrue(old_rf)
        self.assertEqual({k: new_rf.get(k) for k in old_rf}, old_rf)
        for scope, items in before["reservations"].items():
            if scope.startswith("access-endpoints/"):
                for key, slot in items.items():
                    self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_reductions_and_profile_changes_need_a_new_baseline(self):
        before = small()

        def entry(recipe, key):
            return next(item for item in recipe["customers"] if item["key"] == key)

        grown = generate(deepcopy(before["recipe"]), previous=before)
        recipe = deepcopy(grown["recipe"])
        entry(recipe, "summit-legal")["offices"] = 2
        wider = generate(recipe, previous=grown)
        shrunk = deepcopy(wider["recipe"])
        entry(shrunk, "summit-legal")["offices"] = 1
        with self.assertRaisesRegex(DesignError, "new baseline"):
            generate(shrunk, previous=wider)
        cases = [
            ("dropped customer", lambda r: r["customers"].remove(entry(r, "harbor-dental"))),
            ("fewer desks", lambda r: entry(r, "summit-legal").__setitem__("staff", 8)),
            ("smaller managed budget",
             lambda r: entry(r, "summit-legal")["wireless"]["pod-01"].__setitem__("managed", 4)),
            ("smaller guest budget",
             lambda r: entry(r, "summit-legal")["wireless"]["reception"].__setitem__("guest", 0)),
        ]
        for label, change in cases:
            recipe = deepcopy(before["recipe"])
            change(recipe)
            with self.subTest(label=label), self.assertRaisesRegex(DesignError, "new baseline"):
                generate(recipe, previous=before)
        with self.assertRaisesRegex(DesignError, "new estate|new baseline"):
            generate(deepcopy(before["recipe"]) | {"namespace": "other"}, previous=before)

    def test_damaged_previous_ledger_is_rejected_before_allocation(self):
        before = small()
        damaged = deepcopy(before)
        damaged["objects"] = [o for o in damaged["objects"]
                              if o["key"] != "device/off-summit-legal-01/desk-001"]
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(deepcopy(before["recipe"]), previous=damaged)


class MspNamingTests(unittest.TestCase):
    def test_site_names_are_unique_and_managed_service_flavoured(self):
        plan = plan_for()
        sites = [o for o in plan["objects"] if o["kind"] == "site"]
        names = [o["attrs"]["name"] for o in sites]
        self.assertEqual(len(set(names)), len(names))
        offices = [o for o in sites if o["key"] != "site/noc-01"]
        for site in offices:
            self.assertIn(" Office ", site["attrs"]["name"])
            self.assertTrue(site["attrs"]["facility"])
        noc = next(o for o in sites if o["key"] == "site/noc-01")
        self.assertIn("Operations Center", noc["attrs"]["name"])
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}), len(sites))
        self.assertIn("Summit Legal", next(o["attrs"]["name"] for o in offices
                                           if o["key"] == "site/off-summit-legal-01"))

    def test_site_names_override_and_legacy_naming_stay_available(self):
        override = small(site_names={"off-summit-legal-01": {"name": "Summit Legal, Loop Office",
                                                             "facility": "SL-LOOP"}})
        self.assertEqual(validate(override), [])
        site = next(o for o in override["objects"] if o["key"] == "site/off-summit-legal-01")
        self.assertEqual(site["attrs"]["name"], "Summit Legal, Loop Office")
        self.assertEqual(site["attrs"]["facility"], "SL-LOOP")
        legacy = small(naming="legacy")
        self.assertEqual(validate(legacy), [])
        site = next(o for o in legacy["objects"] if o["key"] == "site/off-summit-legal-01")
        self.assertEqual(site["attrs"]["name"], "arbor-off-summit-legal-01")
        with self.assertRaisesRegex(DesignError, "unknown site ids"):
            small(site_names={"off-nobody-01": {"name": "Nowhere"}})


class MspValidatorTests(unittest.TestCase):
    """Every independent MSP assertion must reject a matching mutation."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = small()
        assert validate(cls.baseline) == []

    def mutated(self, change):
        plan = deepcopy(self.baseline)
        objects = {o["key"]: o for o in plan["objects"]}
        change(plan, objects)
        return {finding["code"] for finding in validate(plan)}

    def test_recipe_bounds_are_restated_independently(self):
        for change in (lambda plan, objects: plan["recipe"].__setitem__("customers", []),
                       lambda plan, objects: plan["recipe"]["customers"][0].__setitem__("offices", 9),
                       lambda plan, objects: plan["recipe"]["customers"][0].__setitem__("staff", 96),
                       lambda plan, objects: plan["recipe"].__setitem__("reserve_fraction", 0.9)):
            self.assertIn("msp-recipe", self.mutated(change))
        self.assertIn("msp-recipe", self.mutated(
            lambda plan, objects: plan["recipe"].__setitem__("wan_tiers_mbps", [100, 50])))

    def test_missing_or_extra_sites_are_reported(self):
        def add_customer(plan, objects):
            plan["recipe"]["customers"].append(dict(key="ghost", offices=1, staff=8,
                                                    wireless={"reception": {"managed": 6, "guest": 12}}))
        self.assertIn("msp-site-inventory", self.mutated(add_customer))

    def test_a_customer_tenant_outside_the_customer_group_is_reported(self):
        self.assertIn("msp-tenant-group", self.mutated(
            lambda plan, objects: objects["tenant/summit-legal"]["refs"].pop("group")))
        self.assertIn("msp-tenant-group", self.mutated(
            lambda plan, objects: objects["tenant-group/customers"]["attrs"].__setitem__("name", "misc")))

    def test_an_extra_or_missing_customer_tenant_is_reported(self):
        def extra_tenant(plan, objects):
            plan["objects"].append({"key": "tenant/stowaway", "kind": "tenant",
                                    "attrs": {"name": "arbor Stowaway", "slug": "arbor-stowaway"},
                                    "refs": {"group": "tenant-group/customers"}, "meta": {}})
        self.assertIn("msp-tenant-inventory", self.mutated(extra_tenant))

    def test_equipment_owned_by_the_wrong_customer_is_reported(self):
        self.assertIn("msp-tenant-ownership", self.mutated(
            lambda plan, objects: objects["device/off-summit-legal-01/access-01"]["refs"]
            .__setitem__("tenant", "tenant/harbor-dental")))
        self.assertIn("msp-tenant-ownership", self.mutated(
            lambda plan, objects: objects["prefix/off-summit-legal-01/staff"]["refs"]
            .__setitem__("tenant", "tenant")))

    def test_a_segment_borrowed_from_another_customer_is_reported(self):
        self.assertIn("msp-tenant-isolation", self.mutated(
            lambda plan, objects: objects["prefix/off-summit-legal-01/staff"]["refs"]
            .__setitem__("vrf", "vrf/customer/harbor-dental/staff")))

    def test_every_customer_routing_context_is_checked_including_the_wan_one(self):
        # Each of these was silently accepted before the WAN context was covered.
        for role in ("management", "staff", "wireless", "security", "guest", "wan"):
            for change in (lambda plan, objects, r=role: objects[f"vrf/customer/summit-legal/{r}"]["refs"]
                           .__setitem__("tenant", "tenant/harbor-dental"),
                           lambda plan, objects, r=role: objects[f"vrf/customer/summit-legal/{r}"]["refs"]
                           .__setitem__("tenant", "tenant"),
                           lambda plan, objects, r=role: objects[f"vrf/customer/summit-legal/{r}"]["attrs"]
                           .__setitem__("enforce_unique", False),
                           lambda plan, objects, r=role: plan.__setitem__(
                               "objects", [o for o in plan["objects"]
                                           if o["key"] != f"vrf/customer/summit-legal/{r}"])):
                with self.subTest(role=role):
                    self.assertIn("msp-tenant-isolation", self.mutated(change))

    def test_a_routing_context_for_no_requested_segment_is_reported(self):
        def stray(plan, objects):
            plan["objects"].append(dict(objects["vrf/customer/summit-legal/staff"],
                                        key="vrf/customer/summit-legal/voice"))
        self.assertIn("msp-tenant-isolation", self.mutated(stray))

    def test_a_bonded_interface_reaching_another_customer_is_reported(self):
        self.assertIn("msp-tenant-isolation", self.mutated(
            lambda plan, objects: objects["device/off-harbor-dental-01/access-01/if/"
                                          "TenGigabitEthernet1/1/3"]["refs"].__setitem__(
                "lag", "device/off-summit-legal-01/access-01/if/TenGigabitEthernet1/1/3")))

    def test_a_wlan_group_nested_under_another_customer_is_reported(self):
        self.assertIn("msp-tenant-isolation", self.mutated(
            lambda plan, objects: objects["wireless-group/off-summit-legal-01"]["refs"]
            .__setitem__("parent", "wireless-group/off-harbor-dental-01")))
        self.assertIn("msp-tenant-isolation", self.mutated(
            lambda plan, objects: objects["wireless-lan/off-summit-legal-01/staff"]["refs"]
            .__setitem__("group", "wireless-group/off-harbor-dental-01")))

    def test_a_cable_between_two_customers_is_reported(self):
        def bridge(plan, objects):
            cable = deepcopy(objects["cable/device/off-summit-legal-01/access-01/if/"
                                     "GigabitEthernet1/0/1--device/off-summit-legal-01/desk-001/if/eth0"])
            cable["key"] = "cable/cross-customer"
            cable["refs"] = {"a": "device/off-summit-legal-01/access-01/if/TenGigabitEthernet1/1/4",
                             "b": "device/off-harbor-dental-01/access-01/if/TenGigabitEthernet1/1/4"}
            plan["objects"].append(cable)
        self.assertIn("msp-tenant-isolation", self.mutated(bridge))

    def test_customer_tenancy_on_the_operations_site_is_reported(self):
        self.assertIn("msp-tenant-isolation", self.mutated(
            lambda plan, objects: objects["device/noc-01/spine-a"]["refs"]
            .__setitem__("tenant", "tenant/summit-legal")))

    def test_removed_endpoint_is_reported_even_when_the_contract_agrees(self):
        def drop(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not o["key"].startswith("device/off-summit-legal-01/desk-014")]
            contract = next(c for c in plan["contracts"] if c["site"] == "site/off-summit-legal-01")
            contract["demand"]["workstations"] = 13
        codes = self.mutated(drop)
        self.assertIn("msp-endpoint-inventory", codes)
        self.assertIn("msp-endpoint-demand", codes)
        self.assertIn("msp-demand-report", codes)

    def test_desk_moved_to_another_pod_is_reported(self):
        codes = self.mutated(lambda plan, objects: objects["device/off-summit-legal-01/desk-001"]["refs"]
                             .__setitem__("location", "location/off-summit-legal-01/pod-02"))
        self.assertIn("msp-endpoint-placement", codes)

    def test_desk_on_the_wrong_segment_is_reported(self):
        def rewire(plan, objects):
            objects["device/off-summit-legal-01/desk-001/if/eth0"]["refs"]["untagged_vlan"] = \
                "vlan/off-summit-legal-01/security"
        self.assertIn("msp-endpoint-path", self.mutated(rewire))

    def test_radio_cabled_to_an_uplink_port_is_reported(self):
        def relocate(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == \
                        "device/off-summit-legal-01/ap-reception/if/eth0":
                    cable["refs"]["a"] = "device/off-summit-legal-01/access-01/if/TenGigabitEthernet1/1/4"
        self.assertIn("msp-ap-power", self.mutated(relocate))

    def test_unplugged_radio_is_reported(self):
        def unplug(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not (o["kind"] == "cable" and
                                       "device/off-summit-legal-01/ap-reception/if/eth0" in o["refs"].values())]
        self.assertIn("msp-endpoint-path", self.mutated(unplug))

    def test_moved_endpoint_mount_and_route_are_reported(self):
        def shift(plan, objects):
            objects["device/off-summit-legal-01/desk-001"]["meta"]["placement"]["position_m"] = [12, 21, 0.8]
        self.assertIn("msp-endpoint-route", self.mutated(shift))

    def test_renumbered_pod_ledger_is_reported(self):
        def swap(plan, objects):
            plan["reservations"]["office-pods/off-summit-legal-01"] = {"pod-01": 1, "pod-02": 2}
        self.assertIn("msp-pod-allocation", self.mutated(swap))

    def test_relocated_segment_prefix_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/off-summit-legal-01/staff"]["attrs"]["prefix"] = "10.9.9.0/24"
        self.assertIn("msp-prefix-policy", self.mutated(renumber))

    def test_reservation_container_leaving_the_customer_vrf_is_reported(self):
        def borrow(plan, objects):
            objects["prefix/off-summit-legal-01/staff/reservation"]["refs"]["vrf"] = "vrf/management"
        self.assertIn("msp-prefix-policy", self.mutated(borrow))

    def test_customer_record_borrowing_a_provider_global_vrf_is_reported(self):
        def borrow(plan, objects):
            objects["prefix/off-summit-legal-01/staff"]["refs"]["vrf"] = "vrf/management"
        self.assertLessEqual({"msp-tenant-isolation", "msp-prefix-policy"}, self.mutated(borrow))

    def test_noc_reservation_moved_into_a_customer_vrf_is_reported(self):
        def borrow(plan, objects):
            objects["prefix/noc-01/management/reservation"]["refs"]["vrf"] = \
                "vrf/customer/summit-legal/management"
        self.assertIn("msp-tenant-isolation", self.mutated(borrow))

    def test_ap_desk_retargeted_across_customers_is_reported(self):
        def retarget(plan, objects):
            assignments = [o for o in plan["objects"] if o["kind"] == "contact_assignment"
                           and "/ap-" in o["key"] and "off-summit-legal" in o["key"]]
            assert assignments
            for record in assignments:
                record["refs"]["contact"] = "contact/operations/tenant/harbor-dental"
        self.assertIn("msp-managed-by", self.mutated(retarget))

    def test_missing_shared_operations_owner_is_reported(self):
        def strip_office(plan, objects):
            del objects["site/off-summit-legal-01"]["refs"]["owner"]
        def strip_noc(plan, objects):
            del objects["site/noc-01"]["refs"]["owner"]
        self.assertIn("msp-shared-owner", self.mutated(strip_office))
        self.assertIn("msp-shared-owner", self.mutated(strip_noc))

    def test_guest_segment_without_visitor_demand_is_rejected(self):
        def drop_demand(plan, objects):
            for item in plan["recipe"]["customers"]:
                for zone in item["wireless"].values():
                    zone["guest"] = 0
        self.assertIn("msp-segment-unrequested", self.mutated(drop_demand))

    def test_wireless_zone_budget_disagreement_is_reported(self):
        def widen(plan, objects):
            plan["recipe"]["customers"][0]["wireless"]["reception"]["guest"] = 300
        self.assertIn("msp-wireless-demand", self.mutated(widen))

    def test_undersized_office_wan_commitment_is_reported(self):
        def downgrade(plan, objects):
            objects["circuit/off-summit-legal-01/a/1"]["attrs"]["commit_rate"] = 50000
        self.assertIn("msp-wan-capacity", self.mutated(downgrade))

    def test_both_carriers_on_one_edge_are_reported(self):
        def collapse(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == "circuit/off-summit-legal-01/b/1/A":
                    cable["refs"]["a"] = "device/off-summit-legal-01/edge-a/if/wan2"
        self.assertIn("msp-wan-diversity", self.mutated(collapse))

    def test_missing_gateway_svi_is_reported(self):
        def disable(plan, objects):
            objects["device/off-summit-legal-01/dist-b/if/Vlan20"]["attrs"]["enabled"] = False
        self.assertIn("msp-gateway-inventory", self.mutated(disable))

    def test_infrastructure_moved_out_of_the_equipment_room_is_reported(self):
        def move(plan, objects):
            objects["device/off-summit-legal-01/access-02"]["refs"]["location"] = \
                "location/off-summit-legal-01/pod-01"
        self.assertIn("msp-equipment-placement", self.mutated(move))

    def test_reserved_access_slot_that_disagrees_with_the_graph_is_reported(self):
        def swap(plan, objects):
            scope = plan["reservations"]["access-endpoints/off-summit-legal-01/location/off-summit-legal-01"]
            scope["device/off-summit-legal-01/desk-001"], scope["device/off-summit-legal-01/desk-002"] = \
                scope["device/off-summit-legal-01/desk-002"], scope["device/off-summit-legal-01/desk-001"]
        self.assertIn("msp-access-allocation", self.mutated(swap))

    def test_lost_managed_by_record_is_reported(self):
        self.assertIn("msp-managed-by", self.mutated(
            lambda plan, objects: objects["site/off-summit-legal-01"]["attrs"].__setitem__("comments", "Office.")))
        self.assertIn("msp-managed-by", self.mutated(
            lambda plan, objects: objects["contact-assignment/device/off-summit-legal-01/access-01"]["refs"]
            .__setitem__("contact", "contact/operations")))
        self.assertIn("msp-managed-by", self.mutated(
            lambda plan, objects: objects["contact-assignment/site/off-summit-legal-01"]["refs"]
            .__setitem__("role", "contact-role/facilities")))
        self.assertIn("msp-managed-by", self.mutated(
            lambda plan, objects: objects["contact/operations/tenant/summit-legal"]["refs"]
            .__setitem__("groups", [])))

    def test_missing_or_out_of_range_reservations_are_reported(self):
        self.assertIn("msp-address-allocation", self.mutated(
            lambda plan, objects: plan["allocations"].pop("off-summit-legal-01")))
        scope = "access-endpoints/off-summit-legal-01/location/off-summit-legal-01"
        self.assertIn("msp-access-allocation", self.mutated(
            lambda plan, objects: plan["reservations"][scope].pop("device/off-summit-legal-01/desk-001")))
        self.assertIn("msp-access-allocation", self.mutated(
            lambda plan, objects: plan["reservations"][scope]
            .__setitem__("device/off-summit-legal-01/desk-001", 99999)))

    def test_removed_managed_service_replica_is_reported(self):
        def drop(plan, objects):
            plan["objects"] = [o for o in plan["objects"] if not o["key"].startswith("vm/noc-01/dns/002")]
        self.assertIn("dc-workload-inventory", self.mutated(drop))

    def test_wrong_dc_prefix_offset_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/noc-01/applications"]["attrs"]["prefix"] = "10.0.240.0/20"
        self.assertIn("dc-prefix-policy", self.mutated(renumber))

    def test_every_remaining_msp_assertion_rejects_its_own_mutation(self):
        def extra_room(plan, objects):
            plan["objects"].append({"key": "location/off-summit-legal-01/annex", "kind": "location",
                                    "attrs": {"name": "Annex", "slug": "arbor-off-summit-legal-01-annex",
                                              "status": "active"},
                                    "refs": {"site": "site/off-summit-legal-01",
                                             "tenant": "tenant/summit-legal",
                                             "parent": "location/off-summit-legal-01/floor-01"},
                                    "meta": {"space_type": "staff_office", "floor": 1,
                                             "position_m": [40, 40, 0]}})

        def extra_switch(plan, objects):
            plan["objects"].append(dict(objects["device/off-summit-legal-01/access-01"],
                                        key="device/off-summit-legal-01/access-09",
                                        attrs=dict(objects["device/off-summit-legal-01/access-01"]["attrs"],
                                                   name="offsummitlegal01-as09", position=19)))

        cases = [
            ("msp-address-allocation",
             lambda plan, objects: plan["allocations"].__setitem__("off-summit-legal-01", 0)),
            ("msp-site-status",
             lambda plan, objects: objects["site/off-summit-legal-01"]["attrs"].__setitem__("status", "planned")),
            ("msp-contract-inventory", lambda plan, objects: plan.__setitem__(
                "contracts", [c for c in plan["contracts"] if c["site"] != "site/off-summit-legal-01"])),
            ("msp-demand-report", lambda plan, objects: next(
                c for c in plan["contracts"] if c["site"] == "site/off-summit-legal-01")
                .__setitem__("managed_customer", "harbor-dental")),
            ("msp-room-inventory", extra_room),
            ("msp-room-placement",
             lambda plan, objects: objects["location/off-summit-legal-01/pod-01"]["meta"]
             .__setitem__("position_m", [9, 18, 0])),
            ("msp-closet-inventory",
             lambda plan, objects: objects["location/off-summit-legal-01"]["meta"]
             .__setitem__("space_type", "office")),
            ("msp-endpoint-address",
             lambda plan, objects: objects["ip/device/off-summit-legal-01/desk-001/if/eth0"]["attrs"]
             .__setitem__("status", "reserved")),
            ("msp-access-inventory", extra_switch),
            ("msp-access-capacity",
             lambda plan, objects: objects["device/off-summit-legal-01/access-01"]["meta"]
             .__setitem__("hardware", "leaf")),
            ("msp-core-inventory",
             lambda plan, objects: objects["device/off-summit-legal-01/dist-b"]["refs"]
             .__setitem__("role", "role/access")),
            ("msp-uplink-path",
             lambda plan, objects: objects["device/off-summit-legal-01/access-01/if/"
                                           "TenGigabitEthernet1/1/1"]["attrs"].__setitem__("enabled", False)),
            ("msp-wan-inventory",
             lambda plan, objects: objects["circuit/off-summit-legal-01/b/1"]["refs"]
             .__setitem__("provider", "provider/a")),
            ("msp-equipment-role",
             lambda plan, objects: objects["device/off-summit-legal-01/console-01"]["refs"]
             .__setitem__("role", "role/workstation")),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                self.assertIn(code, self.mutated(change))

    def test_power_path_break_is_reported(self):
        def cut(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["attrs"].get("type") == "power":
                    cable["attrs"]["status"] = "planned"
                    break
        self.assertTrue({"dc-power-path", "power-path-status"} & self.mutated(cut))


class MspArtifactTests(unittest.TestCase):
    def test_cli_preview_generation_and_turbobulk_load_check(self):
        def call(*args):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = main(["--json", *map(str, args)])
            self.assertEqual(status, 0, output.getvalue() + error.getvalue())
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "msp.toml"
            recipe.write_text('profile = "msp"\n\n[[customers]]\nkey = "summit-legal"\n'
                              'offices = 1\nstaff = 14\n\n[[customers]]\nkey = "harbor-dental"\n'
                              'offices = 1\nstaff = 8\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["profile"], "msp")
            self.assertEqual(preview["resolved"]["customers"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["reserve_fraction"]["source"], "default")
            call("generate", recipe, "--out", root / "build")
            self.assertEqual(json.loads((root / "build/intent.json").read_text()), preview)
            call("check", root / "build/plan.json")
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = load_main([str(root / "build"), "--load-check"])
            self.assertEqual(status, 0, error.getvalue())
            verdict = json.loads(output.getvalue())
            self.assertTrue(verdict["turbobulk_loadable"], verdict)
            self.assertEqual(verdict["turbobulk_uncovered"], [])
            self.assertEqual(verdict["turbobulk_unsupported_refs"], [])

    def test_catalog_hardware_is_reused_without_new_models(self):
        plan = small()
        aliases = {o["meta"]["hardware"] for o in plan["objects"] if o["kind"] == "device"}
        self.assertTrue(aliases <= set(hardware_catalog()["models"]))
        self.assertEqual(aliases & {"atm", "inherited-access", "liquid-blade", "liquid-chassis"}, set())
        roles = {o["refs"]["role"] for o in plan["objects"] if o["kind"] == "device"}
        self.assertEqual(roles & {"role/pos-terminal", "role/scanner", "role/medical-device",
                                  "role/imaging-device", "role/atm", "role/provider-edge"}, set())


if __name__ == "__main__":
    unittest.main()
