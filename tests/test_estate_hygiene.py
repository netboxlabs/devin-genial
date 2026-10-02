"""List-view hygiene every profile owes: graph-derived tags, statuses and taxonomy.

Each check pairs the healthy estate with a failing mutation, so a validator
that stops looking fails here (estates/validate_operations.py ``_shared``,
estates/validate_networking.py MAC policy).
"""

from collections import Counter
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.model import hardware_catalog
from estates.naming import ROLE_COLORS, TAGS
from estates.validate import validate
from estates.validate_networking import validate as networking
from estates.validate_operations import validate as operations


def codes(findings):
    return {finding["code"] for finding in findings}


class EstateHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {profile: generate({"profile": profile}) for profile in
                     ("regional-bank", "provider-backbone", "manufacturing", "school-district")}
        cls.junos = generate({"profile": "school-district", "hardware": {"access": "juniper"}})

    def mutate(self, profile, change):
        plan = deepcopy(self.plans[profile])
        objects = {obj["key"]: obj for obj in plan["objects"]}
        change(plan, objects)
        return plan

    def of(self, plan, kind):
        return [obj for obj in plan["objects"] if obj["kind"] == kind]

    def test_custom_link_guard_matches_every_site(self):
        """The link renders only when its Jinja guard is true; guard on the namespaced slug, not the authored name."""
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                ns = plan["recipe"]["namespace"]
                link = self.of(plan, "custom_link")[0]["attrs"]["link_text"]
                self.assertIn("object.slug.startswith('" + ns + "-')", link)
                self.assertTrue(all(site["attrs"]["slug"].startswith(ns + "-") for site in self.of(plan, "site")))

    def test_estates_validate_and_statuses_are_not_one_colour(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                self.assertEqual(validate(plan), [])
                ranges = Counter(obj["attrs"]["status"] for obj in self.of(plan, "ip_range"))
                self.assertTrue(ranges["active"] and ranges["reserved"])
                self.assertTrue(any(obj["attrs"].get("enabled") is False for obj in self.of(plan, "interface")))
                self.assertGreaterEqual(len({obj["attrs"]["kind"] for obj in self.of(plan, "journal_entry")}), 3)
                self.assertIn("secondary", {obj["attrs"]["priority"] for obj in self.of(plan, "contact_assignment")})

    def test_tags_are_graph_derived_scoped_and_discriminating(self):
        self.assertEqual({t["key"] for t in self.of(self.plans["provider-backbone"], "tag")},
                         {"tag/hub-site", "tag/dual-homed", "tag/route-reflector", "tag/transit-edge", "tag/managed-ce"})
        self.assertIn("tag/pci-scope", {t["key"] for t in self.of(self.plans["regional-bank"], "tag")})
        self.assertIn("tag/ot-zone", {t["key"] for t in self.of(self.plans["manufacturing"], "tag")})
        objects = {obj["key"]: obj for obj in self.plans["provider-backbone"]["objects"]}
        reflectors = [key for key, obj in objects.items() if "tag/route-reflector" in obj["refs"].get("tags", [])]
        self.assertEqual(len(reflectors), 2)
        self.assertEqual({objects[key]["refs"]["role"] for key in reflectors}, {"role/provider-edge"})

        def blanket(plan, objects):
            for obj in objects.values():
                if obj["kind"] == "site":
                    obj["refs"]["tags"] = ["tag/hub-site"]

        def wrong_kind(plan, objects):
            next(obj for obj in objects.values() if obj["kind"] == "device")["refs"]["tags"] = ["tag/hub-site"]

        def unused(plan, objects):
            for obj in objects.values():
                obj["refs"].get("tags", []).count("tag/managed-ce") and obj["refs"]["tags"].remove("tag/managed-ce")

        for change in (blanket, wrong_kind, unused):
            with self.subTest(change=change.__name__):
                self.assertIn("operations-tag", codes(operations(self.mutate("provider-backbone", change))))

    def test_taxonomy_lists_only_what_is_used_in_distinct_colours(self):
        self.assertEqual(len(set(ROLE_COLORS.values())), len(ROLE_COLORS))
        self.assertEqual(len({tag[1] for tag in TAGS.values()}), len(TAGS))
        provider = self.plans["provider-backbone"]
        self.assertFalse({"role/database", "role/backup-service", "role/wall-outlet"}
                         & {obj["key"] for obj in provider["objects"]})
        self.assertNotIn("hardware/patch-panel", {obj["key"] for obj in provider["objects"]})

        def orphan(plan, objects):
            plan["objects"].append({"key": "role/unused", "kind": "device_role", "attrs": {"name": "Unused",
                                    "slug": "unused", "color": "000001"}, "refs": {}, "meta": {}})

        def clash(plan, objects):
            objects["role/access"]["attrs"]["color"] = objects["role/wan-edge"]["attrs"]["color"]

        for change in (orphan, clash):
            with self.subTest(change=change.__name__):
                self.assertIn("operations-taxonomy", codes(operations(self.mutate("regional-bank", change))))

    def test_unused_switch_ports_agree_with_the_disable_unused_context(self):
        def reopen(plan, objects):
            port = next(obj for obj in objects.values() if obj["kind"] == "interface" and obj["attrs"].get("enabled") is False)
            port["attrs"]["enabled"] = True

        self.assertIn("operations-unused-port", codes(operations(self.mutate("school-district", reopen))))

    def test_circuit_pairs_and_service_tiers_follow_the_graph(self):
        plan = self.plans["regional-bank"]
        pair = [obj for obj in self.of(plan, "circuit_group_assignment") if obj["refs"]["group"] == "circuit-group/dc-01/1"]
        self.assertEqual(sorted(obj["attrs"]["priority"] for obj in pair), ["primary", "secondary"])

        def same_priority(plan, objects):
            for obj in objects.values():
                if obj["kind"] == "circuit_group_assignment":
                    obj["attrs"]["priority"] = "primary"

        def wrong_tier(plan, objects):
            field = next(iter(objects["site/dc-01"]["attrs"]["custom_fields"]))
            objects["site/dc-01"]["attrs"]["custom_fields"][field] = {"selection": "tier-3"}

        self.assertIn("operations-circuit-group", codes(operations(self.mutate("regional-bank", same_priority))))
        self.assertIn("operations-custom-field", codes(operations(self.mutate("regional-bank", wrong_tier))))

    def test_vendor_oui_macs_svi_names_and_dns(self):
        ouis = hardware_catalog()["mac_ouis"]
        plan = self.plans["regional-bank"]
        objects = {obj["key"]: obj for obj in plan["objects"]}
        for mac in self.of(plan, "mac_address"):
            port = objects[mac["refs"]["assigned_object"]]
            self.assertNotIn("description", mac["attrs"])
            if port["kind"] == "vm_interface":
                self.assertTrue(mac["attrs"]["mac_address"].startswith(ouis["virtual_machine"]))
            else:
                maker = objects[objects[objects[port["refs"]["device"]]["refs"]["device_type"]]["refs"]["manufacturer"]]["attrs"]["name"]
                if maker in ouis["manufacturers"]:
                    self.assertTrue(mac["attrs"]["mac_address"].startswith(ouis["manufacturers"][maker]), mac["key"])

        def foreign_oui(plan, objects):
            mac = next(obj for obj in objects.values() if obj["kind"] == "mac_address"
                       and not obj["attrs"]["mac_address"].startswith("00:05:85"))
            mac["attrs"]["mac_address"] = "00:05:85" + mac["attrs"]["mac_address"][8:]

        self.assertIn("mac-identity", codes(networking(self.mutate("regional-bank", foreign_oui))))
        names = {obj["attrs"]["name"] for obj in self.junos["objects"] if obj["kind"] == "interface"
                 and obj["key"].rsplit("/", 1)[-1].startswith("Vlan")
                 and self.junos_device(obj["refs"]["device"]).startswith("hardware/access-juniper")}
        self.assertTrue(names and all(name.startswith("irb.") for name in names))
        self.assertEqual(validate(self.junos), [])
        for address in self.of(plan, "ip_address"):
            port = objects.get(address["refs"].get("assigned_object"), {})
            self.assertNotIn("description", address["attrs"]) if port.get("kind") == "interface" else None
            if port.get("kind") == "interface" and objects[port["refs"]["device"]]["refs"].get("primary_ip4") != address["key"]:
                self.assertEqual(address["attrs"]["dns_name"].split(".", 1)[1].split(".")[0],
                                 objects[port["refs"]["device"]]["attrs"]["name"])
        loopbacks = [obj for obj in self.of(self.plans["provider-backbone"], "ip_address") if obj["attrs"].get("role") == "loopback"]
        self.assertTrue(loopbacks)

    def test_install_notes_follow_each_sites_own_timeline(self):
        plan = self.plans["provider-backbone"]
        objects = {obj["key"]: obj for obj in plan["objects"]}
        first = {}
        for term in self.of(plan, "circuit_termination"):
            if term["refs"]["termination"].startswith("site/"):
                day = objects[term["refs"]["circuit"]]["attrs"]["install_date"]
                first[term["refs"]["termination"]] = min(day, first.get(term["refs"]["termination"], day))
        # A device's own earliest circuit (cabled to one of its ports).
        carried = {}
        for cable in self.of(plan, "cable"):
            for near, far in (("a", "b"), ("b", "a")):
                end = objects[cable["refs"][far]]
                if end["kind"] == "circuit_termination":
                    device = objects[cable["refs"][near]]["refs"].get("device")
                    day = objects[end["refs"]["circuit"]]["attrs"]["install_date"]
                    carried[device] = min(day, carried.get(device, day))
        checked = 0
        for note in self.of(plan, "journal_entry"):
            subject = objects[note["refs"]["assigned_object"]]
            site = subject["key"] if subject["kind"] == "site" else subject["refs"].get("site")
            when = note["attrs"]["comments"][:10]
            if note["key"].endswith("/equipment-record") and site in first:
                # racked before the earliest service it carries, else its site's first
                self.assertLessEqual(when, carried.get(subject["key"], first[site]))
                checked += 1
            elif note["key"].endswith("/access-plan") and site in first:
                self.assertLess(when, first[site])  # the site is readied before service
            elif note["key"].endswith("/psu-replacement-plan") and site in first:
                self.assertGreaterEqual(when, first[site])
        self.assertTrue(checked)

        def shifted(plan, objects):
            note = next(obj for obj in objects.values() if obj["key"].endswith("/equipment-record"))
            note["attrs"]["comments"] = plan["recipe"]["as_of"] + note["attrs"]["comments"][10:]

        self.assertIn("operations-journal-date", codes(operations(self.mutate("provider-backbone", shifted))))

    def junos_device(self, key):
        return next(obj for obj in self.junos["objects"] if obj["key"] == key)["refs"]["device_type"]


if __name__ == "__main__":
    unittest.main()
