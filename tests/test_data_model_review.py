"""The 0.16 data-model review: each finding pinned on a healthy estate plus a
failing mutation, so a validator that stops looking fails here.

Covers the service-class field, rack types, one unused-port rule, SVIs without
a mode, Junos lo0.0, backbone MTU, class-based bay types, one colour palette,
the country/state/metro region tree, consistent owners, typed and coloured
cables, operational VM and IPAM-role text, sourced device-type facts, journal
``created`` timestamps and the loader/Diode handling of the new fields.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import re
import unittest

from estates import naming
from estates.diode import deliverable_plan, export  # noqa: F401  (export imported for parity)
from estates.generate import generate
from estates.model import ROOT, hardware_catalog, recipe_from_file
from estates.turbobulk import SUPPORTED_REFS, _render, _rendered_columns
from estates.validate import validate
from estates.validate_operations import validate as operations


def codes(findings):
    return {finding["code"] for finding in findings}


class DataModelReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = generate(recipe_from_file(ROOT / "profiles/provider-backbone.toml"))
        cls.bank = generate({"profile": "regional-bank"})
        cls.school = generate({"profile": "school-district"})

    def of(self, plan, kind):
        return [obj for obj in plan["objects"] if obj["kind"] == kind]

    def mutate(self, plan, change):
        plan = deepcopy(plan)
        change({obj["key"]: obj for obj in plan["objects"]}, plan)
        return plan

    def test_healthy_estates_validate(self):
        for plan in (self.provider, self.bank, self.school):
            self.assertEqual(validate(plan), [])

    def test_service_class_is_the_bandwidth_band_not_a_tag_echo(self):
        field = self.of(self.provider, "custom_field")[0]
        self.assertEqual(field["attrs"]["label"], "Service class")
        self.assertTrue(self.of(self.provider, "custom_field_choice_set")[0]["attrs"]["name"].endswith("Service classes"))
        classes = Counter(next(iter(site["attrs"]["custom_fields"].values()))["selection"]
                          for site in self.of(self.provider, "site"))
        self.assertGreater(len(classes), 1)

        def wrong(objects, plan):
            site = next(o for o in objects.values() if o["kind"] == "site")
            name = next(iter(site["attrs"]["custom_fields"]))
            current = site["attrs"]["custom_fields"][name]["selection"]
            site["attrs"]["custom_fields"][name] = {"selection": "backbone" if current != "backbone" else "essential"}

        self.assertIn("operations-custom-field", codes(operations(self.mutate(self.provider, wrong))))

    def test_racks_take_their_type_and_carry_no_deprecated_per_rack_fields(self):
        for plan in (self.provider, self.bank):
            self.assertFalse(self.of(plan, "rack_group"))
            for rack in self.of(plan, "rack"):
                self.assertFalse({"width", "form_factor"} & rack["attrs"].keys(), rack["key"])
                self.assertIn("rack_type", rack["refs"], rack["key"])

        def per_rack(objects, plan):
            next(o for o in objects.values() if o["kind"] == "rack")["attrs"]["width"] = 19

        self.assertIn("operations-rack-type", codes(operations(self.mutate(self.bank, per_rack))))

    def test_loader_copies_the_rack_type_fields_rack_save_would(self):
        objects = {obj["key"]: obj for obj in self.bank["objects"]}
        rack = next(o for o in objects.values() if o["kind"] == "rack")
        ids = {key: n for n, key in enumerate(objects, 1)}
        row = _render(rack, objects, ids, {})
        rack_type = objects[rack["refs"]["rack_type"]]["attrs"]
        self.assertEqual((row["width"], row["form_factor"]), (rack_type["width"], rack_type["form_factor"]))
        self.assertLessEqual({"width", "form_factor"}, _rendered_columns(rack))
        leaf = objects["hardware/leaf"]
        self.assertEqual(_render(leaf, objects, ids, {})["_abs_weight"], 9525)  # 21 lb in grams
        self.assertIn("_abs_weight", _rendered_columns(leaf))
        for kind in ("device", "rack", "prefix", "ip_address", "vlan", "site", "circuit", "cluster"):
            self.assertIn("owner", SUPPORTED_REFS[kind])

    def test_one_unused_port_rule_across_every_role(self):
        cabled = {end for c in self.of(self.provider, "cable") for end in (c["refs"]["a"], c["refs"]["b"])}
        roles = {self.provider_obj(p["refs"]["device"])["refs"]["role"] for p in self.of(self.provider, "interface")
                 if p["attrs"].get("enabled") is False}
        self.assertLessEqual({"role/customer-edge", "role/provider-edge", "role/management"}, roles)
        open_ports = [p for p in self.of(self.provider, "interface") if p["attrs"].get("enabled")
                      and p["key"] not in cabled and p["attrs"]["type"] not in ("virtual", "lag")]
        self.assertTrue(all(p["attrs"].get("mark_connected") for p in open_ports
                            if not p["key"].startswith("device/dc-01/network-lab/")))

        def reopen(objects, plan):
            port = next(o for o in objects.values() if o["kind"] == "interface" and o["attrs"].get("enabled") is False
                        and self.provider_obj(o["refs"]["device"])["refs"]["role"] == "role/provider-edge")
            port["attrs"]["enabled"] = True

        self.assertIn("operations-unused-port", codes(operations(self.mutate(self.provider, reopen))))

    def provider_obj(self, key):
        if not hasattr(self, "_provider_index"):
            self._provider_index = {o["key"]: o for o in self.provider["objects"]}
        return self._provider_index[key]

    def test_svis_carry_no_mode_and_their_vlan_is_derived(self):
        svis = [p for p in self.of(self.bank, "interface") if p["attrs"]["type"] == "virtual" and not p["refs"].get("parent")]
        self.assertTrue(svis)
        self.assertFalse([p["key"] for p in svis if p["attrs"].get("mode") or p["refs"].get("untagged_vlan")])

        def switchport(objects, plan):
            svi = next(o for o in objects.values() if o["kind"] == "interface" and o["attrs"]["name"].startswith("Vlan"))
            svi["attrs"]["mode"] = "access"

        self.assertIn("interface-svi-mode", codes(validate(self.mutate(self.bank, switchport))))

    def test_junos_loopback_addresses_sit_on_unit_zero(self):
        # PEs in service; a retired MX80 or an MX304 on order has no loopback.
        for pe in (d for d in self.of(self.provider, "device") if d["refs"]["role"] == "role/provider-edge"
                   and d["attrs"].get("status") == "active"):
            primary = self.provider_obj(pe["refs"]["primary_ip4"])
            unit = self.provider_obj(primary["refs"]["assigned_object"])
            self.assertEqual((unit["attrs"]["name"], unit["refs"]["parent"]), ("lo0.0", f"{pe['key']}/if/lo0"))

        def bare(objects, plan):
            pe = next(o for o in objects.values() if o["kind"] == "device" and o["refs"]["role"] == "role/provider-edge"
                      and "primary_ip4" in o["refs"])
            objects[pe["refs"]["primary_ip4"]]["refs"]["assigned_object"] = f"{pe['key']}/if/lo0"

        self.assertIn("provider-loopback", codes(validate(self.mutate(self.provider, bare))))

    def test_backbone_links_carry_one_mtu_at_both_ends(self):
        objects = {o["key"]: o for o in self.provider["objects"]}
        spans = 0
        for cable in self.of(self.provider, "cable"):
            ends = [objects[cable["refs"][side]] for side in ("a", "b")]
            routers = [e for e in ends if e["kind"] == "interface"
                       and objects[e["refs"]["device"]]["refs"]["role"] in {"role/provider-edge", "role/spine", "role/leaf"}]
            if len(routers) == 2 and not any(e["attrs"].get("mgmt_only") for e in routers):
                self.assertEqual(len({e["attrs"].get("mtu") for e in routers}), 1, cable["key"])
                self.assertTrue(routers[0]["attrs"].get("mtu"), cable["key"])
            circuit = next((objects[e["refs"]["circuit"]] for e in ends if e["kind"] == "circuit_termination"), None)
            if circuit and circuit["refs"]["type"] in {"circuit-type/backbone", "circuit-type/dark-fiber"}:
                spans += 1
                self.assertTrue(all(e["attrs"].get("mtu") for e in ends if e["kind"] == "interface"), cable["key"])
        self.assertTrue(spans)

    def test_bay_types_are_classes_and_fit_still_follows_the_device_type(self):
        bays = self.of(self.bank, "module_bay_type")
        names = [b["attrs"]["name"] for b in bays]
        self.assertTrue(all(name.endswith(("AC PSU bay", "optic cage")) for name in names), names)
        self.assertFalse([n for n in names if "SFPP" in n])
        self.assertEqual(len({b["attrs"]["color"] for b in bays}), len(bays))

        def foreign_supply(objects, plan):
            # Another supply in the same maker's bay class: the class admits it,
            # but the chassis's own catalog configuration does not.
            module = next(o for o in objects.values() if o["kind"] == "module"
                          and o["refs"]["module_type"] == "module-type/Arista/PWR-511-AC-RED")
            module["refs"]["module_type"] = "module-type/Arista/PWR-500AC-F"

        self.assertIn("equipment-installed-psu", codes(validate(self.mutate(self.bank, foreign_supply))))

    def test_one_palette_across_every_coloured_family(self):
        values = list(naming.PALETTE.values()) + list(naming.SPARE_COLORS)
        self.assertEqual(len(values), len(set(values)))
        for plan in (self.provider, self.bank, self.school):
            colours = [o["attrs"]["color"] for o in plan["objects"]
                       if o["kind"] in naming.COLOURED_KINDS and "color" in o["attrs"]]
            self.assertEqual(len(colours), len(set(colours)))

        def clash(objects, plan):
            objects["tag/hub-site"]["attrs"]["color"] = objects["role/provider-edge"]["attrs"]["color"]

        self.assertIn("operations-taxonomy", codes(operations(self.mutate(self.provider, clash))))

    def test_regions_run_country_state_metro_with_no_pass_through(self):
        regions = {o["key"]: o for o in self.of(self.provider, "region")}
        self.assertFalse([k for k in regions if "great-lakes" in k])
        for site in self.of(self.provider, "site"):
            metro = regions[site["refs"]["region"]]
            state = regions[metro["refs"]["parent"]]
            self.assertTrue(metro["attrs"]["name"].endswith(" metro"))
            self.assertEqual(state["refs"]["parent"], f"region/{self.provider['recipe']['namespace']}/us")
        school_states = [o for o in self.of(self.school, "region") if o["key"].count("/") == 3]
        self.assertEqual(len(school_states), 1)  # one metro district: unused states are pruned

        def orphan(objects, plan):
            ns = plan["recipe"]["namespace"]
            plan["objects"].append({"key": f"region/{ns}/us/xx", "kind": "region", "attrs": {"name": "Nowhere", "slug": "x"},
                                    "refs": {"parent": f"region/{ns}/us"}, "meta": {}})

        self.assertIn("operations-taxonomy", codes(operations(self.mutate(self.school, orphan))))

    def test_infrastructure_records_name_one_owner(self):
        for kind in ("site", "circuit", "device", "rack", "prefix", "ip_address", "vlan"):
            self.assertTrue(all(o["refs"].get("owner") == "owner/operations" for o in self.of(self.provider, kind)), kind)

        def unowned(objects, plan):
            next(o for o in objects.values() if o["kind"] == "prefix")["refs"].pop("owner")

        self.assertIn("operations-owner", codes(operations(self.mutate(self.provider, unowned))))

    def test_cables_are_typed_and_coloured_by_medium(self):
        objects = {o["key"]: o for o in self.bank["objects"]}
        consoles = [c for c in self.of(self.bank, "cable")
                    if {objects[c["refs"][s]]["kind"] for s in ("a", "b")} == {"console_port", "console_server_port"}]
        self.assertTrue(consoles)
        self.assertTrue(all(c["attrs"].get("type") == "cat6" and c["attrs"].get("color") == "00e5ff" for c in consoles))
        power = Counter(c["attrs"].get("color") for c in self.of(self.bank, "cable") if c["attrs"].get("type") == "power")
        self.assertEqual(set(power), {"212121", "d50000"})

        def jacket(objects, plan):
            next(o for o in objects.values() if o["kind"] == "cable" and o["attrs"].get("type") == "smf")["attrs"]["color"] = "2196f3"

        self.assertIn("operations-cable-colour", codes(operations(self.mutate(self.bank, jacket))))

    def test_vm_and_ipam_role_text_is_operational_and_per_estate(self):
        for vm in self.of(self.provider, "virtual_machine"):
            self.assertNotRegex(vm["attrs"]["description"], r"group \d|replica \d of")
            self.assertNotIn("lanes", vm["attrs"]["comments"])
        text = " ".join(o["attrs"].get("description", "") for o in self.of(self.provider, "role"))
        self.assertNotIn("student", text.lower())
        self.assertNotIn("research", text.lower())

    def test_device_types_carry_their_sourced_physical_facts(self):
        catalog = hardware_catalog()["models"]
        for dtype in self.of(self.bank, "device_type"):
            spec = catalog[dtype["key"].removeprefix("hardware/")]
            for field in ("part_number", "weight", "weight_unit", "airflow"):
                self.assertEqual(dtype["attrs"].get(field), spec.get(field), (dtype["key"], field))
        self.assertEqual(catalog["leaf"]["part_number"], "DCS-7050SX3-48C8-F")
        self.assertNotIn("part_number", catalog["ap"])  # the pinned source is the -E part, not the installed -B

    def test_journal_created_is_the_event_date_and_diode_leaves_it_to_the_loader(self):
        for note in self.of(self.provider, "journal_entry"):
            # The event date follows the bold title; the time sits in its band
            # (business hours, or night for third-party maintenance).
            day = re.match(r"\*\*[^*\n]+\*\* · (\d{4}-\d{2}-\d{2})\n\n", note["attrs"]["comments"]).group(1)
            self.assertRegex(note["attrs"]["created"], rf"^{day}T(1[4-9]|2[01]|0[4-8]):[0-5]\d:00Z$")
        self.assertFalse([o for o in deliverable_plan(self.provider)["objects"]
                          if o["kind"] == "journal_entry" and "created" in o["attrs"]])

        def stamped_now(objects, plan):
            next(o for o in objects.values() if o["kind"] == "journal_entry")["attrs"]["created"] = "2026-10-02T09:00:00Z"

        self.assertIn("operations-journal-date", codes(operations(self.mutate(self.provider, stamped_now))))


if __name__ == "__main__":
    unittest.main()
