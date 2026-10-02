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

    def mutated(self, plan, key, **refs):
        def changed(o):
            merged = {**o["refs"], **refs}
            return dict(o, refs={k: v for k, v in merged.items() if v is not None})
        return {**plan, "objects": [changed(o) if o["key"] == key else o for o in plan["objects"]]}

    def test_every_prefix_and_vlan_carries_the_ipam_role_its_purpose_implies(self):
        for plan in (self.provider, self.bank):
            objects = {o["key"]: o for o in plan["objects"]}
            for obj in self.of(plan, "prefix") + self.of(plan, "vlan"):
                self.assertEqual(objects[obj["refs"]["role"]]["kind"], "role", obj["key"])
            roles = {o["key"]: o["attrs"]["name"] for o in self.of(plan, "role")}
            self.assertEqual(len(set(roles.values())), len(roles))
        role = lambda key: self.objects[self.objects[key]["refs"]["role"]]["attrs"]["name"]
        self.assertEqual(role("prefix/loopback/device/pop-chicago-west/pe-a"), "Loopbacks")
        self.assertEqual(role("prefix/link/pair/pop-chicago-west"), "Transit")
        self.assertEqual(role("prefix/pop-chicago-west/reservation"), "Backbone")
        self.assertEqual(role("root/customer/harbor-logistics"), "Customer")
        self.assertEqual(role("prefix/ce-harbor-logistics-chicago-west-001/clients"), "Users")
        self.assertEqual(role("vlan/pop-chicago-west/management"), "Management")
        # Wrong role, missing role, and a prefix that disagrees with its VLAN.
        for key, refs in (("prefix/link/pair/pop-chicago-west", {"role": "ip-role/management"}),
                          ("vlan/ce-harbor-logistics-chicago-west-001/clients", {"role": None}),
                          ("prefix/ce-harbor-logistics-chicago-west-001/clients", {"role": "ip-role/customer"})):
            findings = validate(self.mutated(self.provider, key, **refs))
            self.assertIn(("ipam-role", key), {(f["code"], f["object"]) for f in findings})

    def test_vlans_carry_short_names_unique_within_their_site_group(self):
        for plan in (self.provider, self.bank):
            seen = set()
            for vlan in self.of(plan, "vlan"):
                self.assertNotIn(" ", vlan["attrs"]["name"], vlan["key"])
                identity = (vlan["refs"]["group"], vlan["attrs"]["name"])
                self.assertNotIn(identity, seen)
                seen.add(identity)

    def test_journals_circuits_sites_and_automation_read_operationally(self):
        for plan in (self.provider, self.bank):
            for entry in self.of(plan, "journal_entry"):
                self.assertNotIn("kbps", entry["attrs"]["comments"])
            for site in self.of(plan, "site"):
                self.assertNotIn("inventory for", site["attrs"].get("comments", ""))
            for obj in (self.of(plan, "config_context") + self.of(plan, "webhook")
                        + self.of(plan, "event_rule") + self.of(plan, "platform")
                        + self.of(plan, "virtual_machine_type")):
                self.assertNotRegex(obj["attrs"].get("description", ""), r"; no |not applied|unspecified|Inert|Wiring only")
        notes = [e["attrs"]["comments"] for e in self.of(self.provider, "journal_entry")]
        self.assertTrue(any("Committed capacity: 100 Gbps\n" in n for n in notes))
        self.assertEqual({c["attrs"]["comments"].split(" order: ")[0] for c in self.of(self.bank, "circuit")},
                         {"Standard branch", "Data center aggregation", "Retained Birch contract"})
        hook = self.of(self.bank, "webhook")[0]
        self.assertTrue(hook["attrs"]["payload_url"].split("/")[2].endswith(".invalid"))
        self.assertIs(self.of(self.bank, "event_rule")[0]["attrs"]["enabled"], False)
        # Rewording a stated rate is a journal-facts failure, not a free edit.
        entry = next(e for e in self.of(self.provider, "journal_entry") if "Committed capacity: 100 Gbps" in e["attrs"]["comments"])
        broken = {**self.provider, "objects": [
            dict(o, attrs={**o["attrs"], "comments": o["attrs"]["comments"].replace("100 Gbps", "100000000 kbps")})
            if o["key"] == entry["key"] else o for o in self.provider["objects"]]}
        self.assertIn(("operations-journal-facts", entry["key"]),
                      {(f["code"], f["object"]) for f in validate(broken)})


if __name__ == "__main__":
    unittest.main()
