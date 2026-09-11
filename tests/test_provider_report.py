"""Provider operator guidance follows actual service and physical references."""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.intent import resolution
from estates.model import canonical
from estates.report import markdown


class ProviderReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.supplied = {"profile": "provider-backbone", "customers": [{"key": "cargo",
            "hub_pop": "chicago-west", "sites": [{"pop": "chicago-west"},
                {"pop": "detroit-south", "count": 2}]}]}
        cls.plan = generate(cls.supplied)

    def test_nested_provenance_and_frozen_unknowns(self):
        resolved = resolution(self.plan, self.supplied)["provider"]
        customer = resolved["customers"][0]
        self.assertEqual(customer["fields"]["hub_pop"]["source"], "supplied")
        self.assertEqual(customer["fields"]["lan_endpoints"]["source"], "default")
        self.assertEqual(customer["sites"][0]["fields"]["count"]["source"], "default")
        self.assertEqual(customer["sites"][1]["fields"]["count"]["source"], "supplied")
        self.assertEqual(resolved["pops"][0]["fields"]["metro"]["source"], "default")
        frozen = resolution(self.plan)["provider"]["customers"][0]
        self.assertEqual(frozen["sites"][1]["fields"]["count"]["source"], "unknown (frozen plan)")

    def test_actual_members_and_ports_with_explicit_scope(self):
        before = canonical(self.plan)
        text = markdown(self.plan)
        self.assertEqual(before, canonical(self.plan))
        for phrase in ("Provider service walkthrough", "single-homed", "lo0 in-band", "fxp0",
                       "spoke-to-hub", "exclude NOC, transit and background", "BGP session", "Wireless is outside"):
            self.assertIn(phrase, text)
        self.assertNotIn("Provider interiors are abstracted.", text)
        self.assertNotIn("WAN capacity by provider and site", text)
        section = text.split("## Provider service walkthrough", 1)[1].split("## Sites and demand", 1)[0]
        self.assertIn("| lakes-fiber-private-cargo | lakes-fiber cargo | 3 |", section)
        self.assertIn("| lakes-fiber-pop-chicago-west | 2 | 2 | 2 | 12 |", section)
        changed = deepcopy(self.plan)
        changed["contracts"] = []
        changed["objects"] = [o for o in changed["objects"] if o["key"] != "virtual-circuit-termination/ce-cargo-detroit-south-002"]
        section = markdown(changed).split("## Provider service walkthrough", 1)[1].split("## Sites and demand", 1)[0]
        self.assertIn("| lakes-fiber-private-cargo | lakes-fiber cargo | 2 |", section)

    def test_both_physical_handoffs_must_be_connected(self):
        text = markdown(self.plan)
        cid = next(o["attrs"]["cid"] for o in self.plan["objects"] if o["key"] == "circuit/customer/ce-cargo-chicago-west-001")
        lines = [line for line in text.splitlines() if f"| {cid} |" in line]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("not connected/enabled" not in line for line in lines))
        changed = deepcopy(self.plan)
        cable = next(o for o in changed["objects"] if o["kind"] == "cable" and
                     "circuit/customer/ce-cargo-chicago-west-001/Z" in o["refs"].values())
        cable["attrs"]["status"] = "planned"
        lines = [line for line in markdown(changed).splitlines() if f"| {cid} |" in line]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("not connected/enabled" in line for line in lines))

    def test_large_circuit_inventory_caps_actual_attachment_rows(self):
        recipe = deepcopy(self.plan["recipe"])
        recipe["pops"] += [{"key": f"z{n:02}", "metro": "milwaukee"} for n in range(17)]
        text = markdown(generate(recipe))
        procurement = text.split("## Circuit procurement", 1)[1].split("## Active IP plan", 1)[0]
        rows = [line for line in procurement.splitlines() if line.startswith("| ")]
        self.assertEqual(len(rows), 82)  # Header, delimiter, then exactly 80 attachments.
        self.assertNotIn("capacity totals below", procurement)


if __name__ == "__main__":
    unittest.main()
