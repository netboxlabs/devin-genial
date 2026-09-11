"""The operator tour follows actual responsibility and journal references."""

from collections import defaultdict
import unittest

from estates.report import _operations_walkthrough


class OperationsReportTests(unittest.TestCase):
    def test_actual_refs_scoped_names_and_bounded_family_selection(self):
        objects = {}
        kinds = defaultdict(list)

        def add(key, kind, attrs, refs=None):
            obj = dict(key=key, kind=kind, attrs=attrs, refs=refs or {})
            objects[key] = obj
            kinds[kind].append(obj)

        add("role", "contact_role", {"name": "Facilities"})
        add("contact", "contact", {"name": "Alex | Morgan", "email": "facilities@example.test"})
        for number in range(20):
            key = f"circuit/{number:02}"
            add(key, "circuit", {"cid": f"C-{number:02}"})
            add(f"assignment/{number:02}", "contact_assignment", {"priority": "secondary"},
                {"object": key, "contact": "contact", "role": "role"})
        add("site/west", "site", {"name": "Operations"})
        add("site/east", "site", {"name": "Operations"})
        for side in ("west", "east"):
            add(f"assignment/{side}", "contact_assignment", {"priority": "primary"},
                {"object": f"site/{side}", "contact": "contact", "role": "role"})
            add(f"journal/{side}", "journal_entry", {"kind": "info", "comments": f"2026-09-01: {side} access design recorded."},
                {"assigned_object": f"site/{side}"})
        text = "\n".join(_operations_walkthrough(objects, kinds))
        self.assertIn("Operations [site/west]", text)
        self.assertIn("Operations [site/east]", text)
        self.assertIn("west access design recorded", text)
        self.assertIn("facilities@example.test", text)
        self.assertIn("Alex &#124; Morgan", text)
        self.assertIn("22 assignments", text)
        self.assertEqual(text.count("| circuit |"), 3)
        self.assertIn("ingestion time", text)


if __name__ == "__main__":
    unittest.main()
