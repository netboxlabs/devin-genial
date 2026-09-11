"""Stable optical preparation notes and report facts follow actual inventory."""

from copy import deepcopy
from collections import defaultdict
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

    def test_all_five_have_bounded_device_optic_notes_without_metadata(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.bare(profile)
                self.assertEqual(validate(plan), [])
                notes = [o for o in plan["objects"] if o["key"].endswith("/optic-replacement-plan")]
                self.assertTrue(notes)
                self.assertLessEqual(len(notes), sum(o["kind"] == "rack" for o in plan["objects"]))
                for note in notes:
                    self.assertEqual(objects[note["refs"]["assigned_object"]]["kind"], "device")
                    self.assertIn("no module deletion, hot-swap or replacement is recorded as executed", note["attrs"]["comments"])

    def test_note_facts_subject_chronology_and_execution_claims_rejected(self):
        for change in ("part", "serial", "interface", "contact", "date", "execution", "missing", "subject"):
            with self.subTest(change=change):
                plan, objects = self.bare()
                note = next(o for o in plan["objects"] if o["key"].endswith("/optic-replacement-plan"))
                if change == "missing":
                    plan["objects"].remove(note)
                elif change == "subject":
                    note["refs"]["assigned_object"] = "site/dc-01"
                else:
                    lines = note["attrs"]["comments"].splitlines()
                    if change == "date":
                        lines[0] = "2099-01-01" + lines[0][10:]
                    elif change == "execution":
                        lines.append("Replacement was executed successfully.")
                    else:
                        field = {"part": "Installed part:", "serial": "Installed serial:",
                                 "interface": "Interface:", "contact": "Facilities contact:"}[change]
                        lines = [field + " wrong" if line.startswith(field) else line for line in lines]
                    note["attrs"]["comments"] = "\n".join(lines)
                self.assertTrue(any(f["code"].startswith("operations-journal") for f in validate(plan)))

    def test_missing_or_wrong_device_module_cannot_waive_note_obligation(self):
        for change in ("missing-module", "wrong-device", "wrong-bay", "missing-reference-and-note"):
            with self.subTest(change=change):
                plan, objects = self.bare()
                note = next(o for o in plan["objects"] if o["key"].endswith("/optic-replacement-plan"))
                device = note["refs"]["assigned_object"]
                interface_name = next(line.removeprefix("Interface: ") for line in note["attrs"]["comments"].splitlines() if line.startswith("Interface: "))
                port = next(o for o in plan["objects"] if o["kind"] == "interface" and o["refs"].get("device") == device and o["attrs"]["name"] == interface_name)
                module = objects[port["refs"]["module"]]
                if change == "missing-module":
                    plan["objects"].remove(module)
                elif change == "wrong-device":
                    module["refs"]["device"] = "device/not-the-serving-device"
                elif change == "wrong-bay":
                    objects[module["refs"]["module_bay"]]["refs"]["device"] = "device/not-the-serving-device"
                else:
                    port["refs"].pop("module")
                    plan["objects"].remove(note)
                self.assertIn("operations-journal-facts", {f["code"] for f in validate(plan)})

    def test_ordinary_growth_preserves_exact_optic_journal_identity(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                before = self.plans[profile]
                recipe = deepcopy(before["recipe"])
                if profile == "regional-bank":
                    recipe["branches"]["small"] += 1
                elif profile == "enterprise-data-center":
                    recipe["workloads"][0]["groups"] += 2
                    recipe["workloads"].append({"key": "aaa-new", "groups": 1})
                elif profile == "school-district":
                    recipe["schools"][0]["classrooms"] += 2
                elif profile == "hospital-clinics":
                    recipe["hospitals"][0]["wards"][0]["beds"] += 2
                else:
                    recipe["customers"][0]["sites"][0]["count"] += 1
                after = generate(recipe, previous=before)
                self.assertEqual(validate(after), [])
                objects = {o["key"]: o for o in after["objects"]}
                for note in before["objects"]:
                    if note["key"].endswith("/optic-replacement-plan"):
                        self.assertEqual(note, objects[note["key"]])

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
