"""Optics report facts follow actual inventory; no journal restates an installed optic."""

from copy import deepcopy
from collections import defaultdict
import re
import unittest

from estates.generate import generate
from estates.report import markdown, _optics_walkthrough
from estates.validate_operations import validate


PROFILES = ("regional-bank", "enterprise-data-center", "school-district", "hospital-clinics", "provider-backbone")


class OpticsContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {profile: generate({"profile": profile}) for profile in PROFILES}

    def bare(self, profile="school-district"):
        plan = deepcopy(self.plans[profile])
        plan["contracts"] = []
        for obj in plan["objects"]:
            obj["meta"] = {}
        return plan, {obj["key"]: obj for obj in plan["objects"]}

    def test_no_journal_restates_installed_optics(self):
        # An optic's part, serial, bay and interface are module fields; a note
        # repeating them added nothing, so none is written or accepted.
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.bare(profile)
                self.assertEqual(validate(plan), [])
                serials = {o["attrs"]["serial"] for o in plan["objects"] if o["kind"] == "module"}
                notes = [o for o in plan["objects"] if o["kind"] == "journal_entry"]
                self.assertTrue(notes)
                self.assertFalse([n["key"] for n in notes if any(s in n["attrs"]["comments"] for s in serials)])
        plan, objects = self.bare()
        device = next(o for o in plan["objects"] if o["kind"] == "journal_entry"
                      and o["key"].endswith("/equipment-record"))["refs"]["assigned_object"]
        plan["objects"].append({"key": f"journal/{device}/optic-replacement-plan", "kind": "journal_entry",
                                "attrs": {"kind": "info", "comments": "2026-01-01 — Optic replacement note\nSwap it.",
                                          "created": "2026-01-01T15:00:00Z"},
                                "refs": {"assigned_object": device}, "meta": {}})
        self.assertIn("operations-journal", {f["code"] for f in validate(plan)})

    def test_report_uses_actual_part_identity_sources_support_and_aoc_assembly(self):
        plan, objects = self.bare()
        for obj in plan["objects"]:
            if obj["kind"] == "module_type":
                obj["attrs"]["attributes"] = '{"source":"https://invented.invalid","reach_m":999999}'
        text = markdown(plan)
        self.assertIn("Installed optics and local paths", text)
        self.assertIn("Transceiver-Data-Sheet.pdf", text)
        self.assertNotIn("invented.invalid", text)
        self.assertNotIn("999999m", text)
        self.assertIn("captive end; whole assembly SKU", text)
        aoc = next(o for o in plan["objects"] if o["kind"] == "cable" and o["attrs"].get("type") == "aoc")
        module = objects[objects[aoc["refs"]["a"]]["refs"]["module"]]
        self.assertIn(module["attrs"]["serial"], text)
        self.assertIn(aoc["attrs"]["label"], text)
        self.assertIn("NOC duty desk", text)
        self.assertIn("deleting that module can cascade", text)

    def test_report_distinguishes_passive_paths_and_unknown_carrier_optics(self):
        # Exercise the path reader on a three-cable passive fiber channel. This
        # is an isolated report fixture, not a newly claimed generator profile.
        plan, objects = self.bare("enterprise-data-center")
        original = next(o for o in plan["objects"] if o["kind"] == "cable" and o["attrs"].get("type") == "smf")
        a, b = original["refs"]["a"], original["refs"]["b"]
        del objects[original["key"]]
        for side in ("a", "b"):
            objects[f"panel-{side}"] = dict(key=f"panel-{side}", kind="device", attrs={"name": f"Patch-{side.upper()}"}, refs={})
            for kind in ("front_port", "rear_port"):
                key = f"{side}/{kind}"
                objects[key] = dict(key=key, kind=kind, attrs={"name": "1", "positions": 1},
                                    refs={"device": f"panel-{side}"} | ({"rear_port": f"{side}/rear_port"} if kind == "front_port" else {}))
        for i, (left, right, length) in enumerate([(a, "a/front_port", 2), ("a/rear_port", "b/rear_port", 5), ("b/front_port", b, 3)]):
            key = f"probe-cable-{i}"
            objects[key] = dict(key=key, kind="cable", attrs={"type": "smf", "length": length, "length_unit": "m"}, refs={"a": left, "b": right})
        kinds, peers, passive, cables = defaultdict(list), {}, {}, {}
        for obj in objects.values():
            kinds[obj["kind"]].append(obj)
            if obj["kind"] == "cable":
                left, right = obj["refs"]["a"], obj["refs"]["b"]
                peers[left], peers[right] = right, left
                cables[left] = cables[right] = obj
            if obj["kind"] == "front_port":
                passive[obj["key"]], passive[obj["refs"]["rear_port"]] = obj["refs"]["rear_port"], obj["key"]
        text = "\n".join(_optics_walkthrough(objects, kinds, peers, passive, cables))
        self.assertIn("Patch-A → Patch-B", text)
        self.assertIn("10m /", text)
        provider = markdown(self.plans["provider-backbone"])
        self.assertIn("carrier far-end optic unknown", provider)
        self.assertIn("SFP-1GE-LX", provider)


if __name__ == "__main__":
    unittest.main()
