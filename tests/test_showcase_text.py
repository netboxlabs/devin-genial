"""User-visible text reads like a production network, not like its generator.

The 0.16 showcase review found opaque hostnames, raw role slugs, unit-less
rates, disclaimers in list-view descriptions and a service note that denied the
BGP sessions emitted beside it. These checks pin the fixes on the provider and
one shared-path profile; each would fail if its defect returned.
"""

import re
import tomllib
import unittest
from pathlib import Path

from estates.generate import generate
from estates.model import DesignError
from estates.validate import validate

ROOT = Path(__file__).parents[1]


def plan_for(profile):
    return generate(tomllib.loads((ROOT / f"profiles/{profile}.toml").read_text()))


class ShowcaseText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = plan_for("provider-backbone")
        cls.bank = plan_for("bank")
        cls.objects = {o["key"]: o for o in cls.provider["objects"]}

    def of(self, plan, kind):
        return [o for o in plan["objects"] if o["kind"] == kind]

    def test_premises_and_pop_hostnames_are_readable_and_unique(self):
        names = {o["attrs"]["name"] for o in self.of(self.provider, "device")}
        self.assertFalse([n for n in names if re.match(r"ce\d{5}-|pop[a-z]+-pe", n)])
        edges = [o for o in self.of(self.provider, "device") if o["refs"]["role"] == "role/customer-edge"]
        for edge in edges:
            customer = edge["refs"]["tenant"].removeprefix("tenant/cust-")
            self.assertRegex(edge["attrs"]["name"], rf"^{customer}-(chi|det|cle|mil)\d{{4,}}-gw01$")
        self.assertEqual(len({e["attrs"]["name"] for e in edges}), len(edges))
        self.assertTrue(any(n.endswith("-pe-a") and not n.startswith("pop") for n in names))

    def test_pop_keys_shaped_like_other_hostname_stems_are_refused(self):
        raw = tomllib.loads((ROOT / "profiles/provider-backbone.toml").read_text())
        raw["pops"][0]["key"] = "dc01"
        with self.assertRaises(DesignError):
            generate(raw)

    def test_service_note_points_at_the_bgp_inventory_instead_of_denying_it(self):
        self.assertTrue(self.of(self.provider, "bgp_session"))
        for vc in self.of(self.provider, "virtual_circuit"):
            self.assertIn("Customer Private L3 BGP peer group", vc["attrs"]["comments"])
            self.assertNotIn("configured BGP sessions", vc["attrs"]["comments"])
        for vm in self.of(self.provider, "virtual_machine"):
            self.assertNotIn("not verified", vm["attrs"]["description"])
            self.assertIn("not verified", vm["attrs"]["comments"])

    def test_customer_vrfs_carry_their_route_target_as_rd(self):
        for vrf in self.of(self.provider, "vrf"):
            if vrf["key"].startswith("vrf/customer/"):
                target = self.objects[vrf["refs"]["export_targets"][0]]
                self.assertEqual(vrf["attrs"]["rd"], target["attrs"]["name"])
        broken = {**self.provider, "objects": [dict(o, attrs={**o["attrs"], "rd": "1:1"})
                                               if o["key"].startswith("vrf/customer/") else o
                                               for o in self.provider["objects"]]}
        self.assertIn("provider-customer-routing", {f["code"] for f in validate(broken)})

    def test_rates_roles_and_segments_use_operator_wording(self):
        circuits = {o["attrs"]["description"] for o in self.of(self.provider, "circuit")}
        self.assertIn("100 Gbps backbone committed on a 100G handoff", circuits)
        self.assertFalse([d for d in circuits if "000 Mbps" in d])
        self.assertTrue(all(o["attrs"]["description"].endswith("private WAN on a 1G handoff; carrier " + o["key"].split("/")[2].upper())
                            for o in self.of(self.bank, "circuit")))
        descriptions = {o["attrs"]["description"] for o in self.of(self.provider, "device")}
        self.assertFalse([d for d in descriptions if re.match(r"[a-z-]+ at ", d)])
        self.assertIn("Provider edge router at Chicago West Exchange", descriptions)
        for plan in (self.provider, self.bank):
            for vlan in self.of(plan, "vlan"):
                self.assertNotRegex(vlan["attrs"].get("description", ""), r"^[a-z]+ segment$")
                self.assertNotRegex(vlan["attrs"]["name"], r"^ce\d")

    def test_tag_and_contacts_read_like_an_operations_directory(self):
        self.assertEqual([t["attrs"]["name"] for t in self.of(self.bank, "tag")], ["Managed"])
        for plan in (self.provider, self.bank):
            for contact in self.of(plan, "contact"):
                self.assertRegex(contact["attrs"]["phone"], r"^\+1 (312|313|216|414)-555-01\d\d$")
                self.assertTrue(contact["attrs"]["email"].endswith(".example"))
        chicago = self.objects["contact/site/pop-chicago-west"]["attrs"]["phone"]
        self.assertTrue(chicago.startswith("+1 312-"))
        self.objects["contact/site/pop-chicago-west"]["attrs"]["phone"] = "+1 313-555-0100"
        try:
            self.assertIn("operations-contact", {f["code"] for f in validate(self.provider)})
        finally:
            self.objects["contact/site/pop-chicago-west"]["attrs"]["phone"] = chicago


if __name__ == "__main__":
    unittest.main()
