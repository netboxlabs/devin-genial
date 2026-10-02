"""Provider customer-edge realism: the showcase carrier's own records read like a carrier's."""

from copy import deepcopy
from pathlib import Path
import tomllib
import unittest

from estates.generate import generate
from estates.validate import validate


ROOT = Path(__file__).parents[1]


class ShowcaseCustomerEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(tomllib.loads((ROOT / "profiles/showcase-provider.toml").read_text()))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.o = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def of(self, kind):
        return [obj for obj in self.o.values() if obj["kind"] == kind]

    def test_baseline_validates(self):
        self.assertEqual(validate(self.plan), [])

    # --- The carrier is not its own supplier ---------------------------------

    def test_operator_circuits_carry_service_orders_and_no_self_escalation(self):
        own = {c["key"] for c in self.of("circuit") if c["refs"]["provider"] == "provider/operator"}
        self.assertTrue(own)
        carrier_desk = {a["refs"]["object"] for a in self.of("contact_assignment")
                        if a["refs"]["role"] == "contact-role/carrier"}
        self.assertFalse(own & carrier_desk)
        self.assertNotIn("contact/provider/operator", self.o)
        for key in own:
            self.assertNotIn(f"journal/{key}/capacity-request", self.o)
        orders = [self.o[f"journal/{key}/service-order"] for key in own if f"journal/{key}/service-order" in self.o]
        self.assertTrue(orders)
        for journal in orders:
            self.assertNotIn("carrier", journal["attrs"]["comments"])
            self.assertIn("Service order for", journal["attrs"]["comments"])
        # Third-party carriers still escalate through their own desks.
        leased = {c["key"] for c in self.of("circuit") if c["refs"]["provider"] != "provider/operator"}
        self.assertEqual(leased, leased & carrier_desk)

    def test_self_escalation_is_refused(self):
        circuit = next(c["key"] for c in self.of("circuit") if c["refs"]["provider"] == "provider/operator")
        desk = next(a["refs"]["contact"] for a in self.of("contact_assignment") if a["refs"]["role"] == "contact-role/carrier")
        self.plan["objects"].append({"key": f"contact-assignment/{circuit}/carrier", "kind": "contact_assignment",
                                     "attrs": {"priority": "secondary"}, "meta": {},
                                     "refs": {"object": circuit, "contact": desk, "role": "contact-role/carrier"}})
        self.assertIn("operations-contact", self.codes())

    def test_dual_homed_tag_is_truthful(self):
        tagged = {s["key"] for s in self.of("site") if "tag/dual-homed" in s["refs"].get("tags", [])}
        pops = {s["key"] for s in self.of("site") if s["key"].startswith("site/pop-")}
        # Only PoPs where two different third-party carriers actually land.
        self.assertTrue(tagged & pops)
        self.assertLess(len(tagged & pops), len(pops))
        for site in tagged & pops:
            carriers = {self.o[t["refs"]["circuit"]]["refs"]["provider"] for t in self.of("circuit_termination")
                        if self.o.get(t["refs"]["termination"], {}).get("refs", {}).get("site") == site
                        and self.o[t["refs"]["circuit"]]["refs"]["type"] != "circuit-type/out-of-band"
                        and self.o[t["refs"]["circuit"]]["refs"]["provider"] != "provider/operator"}
            self.assertGreaterEqual(len(carriers), 2, site)

    def test_untruthful_dual_homed_tag_is_refused(self):
        site = next(s for s in self.of("site") if s["key"].startswith("site/pop-")
                    and "tag/dual-homed" not in s["refs"].get("tags", []))
        site["refs"]["tags"] = ["tag/dual-homed"]
        self.assertIn("provider-dual-homed", self.codes())


if __name__ == "__main__":
    unittest.main()
