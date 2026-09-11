"""The healthcare operator sees demand provenance and actual connected inventory."""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.intent import resolution
from estates.model import canonical
from estates.report import markdown


class HospitalReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.supplied = {"profile": "hospital-clinics", "hospitals": [{"key": "central",
            "administrative_desks": 0, "imaging_rooms": 1,
            "wards": [{"key": "north", "beds": 4}]}], "clinics": []}
        cls.plan = generate(cls.supplied)

    def test_nested_input_provenance_and_frozen_unknowns(self):
        preview = resolution(self.plan, self.supplied)["healthcare_facilities"][0]
        self.assertEqual(preview["fields"]["imaging_rooms"]["source"], "supplied")
        self.assertEqual(preview["fields"]["wan_peak_mbps"]["source"], "default")
        ward = preview["wards"][0]
        self.assertEqual(ward["fields"]["beds"]["source"], "supplied")
        self.assertEqual(ward["fields"]["clinical_desks"]["source"], "default")
        self.assertEqual(preview["demand"]["bed_stations"], 4)
        frozen = resolution(self.plan)["healthcare_facilities"][0]
        self.assertEqual(frozen["wards"][0]["fields"]["beds"]["source"], "unknown (frozen plan)")

    def test_care_walkthrough_uses_actual_equipment_contacts_and_listener(self):
        before = canonical(self.plan)
        text = markdown(self.plan)
        self.assertEqual(canonical(self.plan), before)
        for phrase in ("Care units and shared services", "Biomedical support", "biomedical desk",
                       "Bed stations", "Monitors", "Modalities", "imaging-archive", "11112"):
            self.assertIn(phrase, text)
        for phrase in ("School population", "classroom workstation", "Banking", "ATM"):
            self.assertNotIn(phrase, text)
        section = text.split("## Care units and shared services\n", 1)[1].split("## Geography", 1)[0]
        row = next(line for line in section.splitlines() if "| lakeshore-hospital-central |" in line)
        self.assertIn("| 1 | 4 | 0 | 1 | 4 | 1 |", row)
        changed = deepcopy(self.plan)
        monitor = next(o for o in changed["objects"] if o["refs"].get("role") == "role/medical-device")
        monitor["refs"]["role"] = "role/workstation"
        section = markdown(changed).split("## Care units and shared services\n", 1)[1].split("## Geography", 1)[0]
        row = next(line for line in section.splitlines() if "| lakeshore-hospital-central |" in line)
        self.assertIn("| 1 | 4 | 0 | 1 | 3 | 1 |", row)


if __name__ == "__main__":
    unittest.main()
