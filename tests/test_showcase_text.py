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
from estates.naming import disclaimer
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
            # The hostname carries the site's own facility code (CHI01 -> chi01).
            facility = self.objects[edge["refs"]["site"]]["attrs"]["facility"].lower()
            self.assertRegex(facility, r"^(chi|det|cle|mil)\d{2,}$")
            self.assertEqual(edge["attrs"]["name"], f"{customer}-{facility}-gw01")
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
            # Operational placement wording, not the builder's lane jargon.
            host = self.objects[vm["refs"]["device"]]["attrs"]["name"]
            self.assertEqual(vm["attrs"]["comments"],
                             f"Runs on {host}; its other replicas run on separate hosts in separate cabinets.")
            self.assertRegex(vm["attrs"]["description"], r"^[A-Z][A-Za-z ]+ service(, shard \d+)? \((primary|secondary)\)$")

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
        self.assertTrue([d for d in circuits if re.fullmatch(r"100G wavelength, .+ to .+, handed off on the 100G port", d)])
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
        self.assertEqual(sorted(t["attrs"]["name"] for t in self.of(self.bank, "tag")), ["Acquired", "Hub site", "PCI scope"])
        for plan in (self.provider, self.bank):
            # Tags are graph-derived: each one marks some, never every, object
            # of a kind it labels, and none is emitted unused.
            for tag in self.of(plan, "tag"):
                tagged = [o for o in plan["objects"] if tag["key"] in o["refs"].get("tags", [])]
                self.assertTrue(tagged, tag["key"])
                for kind in {o["kind"] for o in tagged}:
                    self.assertLess(sum(o["kind"] == kind for o in tagged), len(self.of(plan, kind)), (tag["key"], kind))
            for contact in self.of(plan, "contact"):
                self.assertRegex(contact["attrs"]["phone"], r"^\+1 (312|313|216|414)-555-01\d\d$")
                self.assertTrue(contact["attrs"]["email"].endswith(".example"))
        # A customer's desks answer from its own domain; a PoP cage's facilities
        # desk is its carrier hotel's remote hands, not carrier staff.
        customer = self.objects["contact/operations/tenant/cust-harbor-logistics"]["attrs"]
        self.assertEqual(customer["email"], "noc@harbor-logistics.example")
        premises = self.objects["contact/site/ce-harbor-logistics-chicago-west-001"]["attrs"]["email"]
        self.assertTrue(premises.startswith("facilities.") and premises.endswith("@harbor-logistics.example"), premises)
        hands = self.objects["contact/site/pop-chicago-west"]["attrs"]
        self.assertEqual((hands["name"], hands["email"]), ("Windward Interconnect remote hands at Chicago West Exchange",
                                                           "remote-hands@windward-interconnect.example"))
        for key, value in (("contact/operations/tenant/cust-harbor-logistics", "cust-harbor-logistics.noc@lakes-fiber.example"),
                           ("contact/site/pop-chicago-west", "pop-chicago-west.facilities@lakes-fiber.example")):
            before = self.objects[key]["attrs"]["email"]
            self.objects[key]["attrs"]["email"] = value
            try:
                self.assertIn("operations-contact", {f["code"] for f in validate(self.provider)})
            finally:
                self.objects[key]["attrs"]["email"] = before
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
        self.assertEqual(role("prefix/pop-chicago-west/reservation"), "Allocation pools")  # one global site block
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
        # A journal never restates its record: no rate, cid or termination.
        self.assertFalse([n for n in notes if "bps" in n])
        self.assertEqual({c["attrs"]["comments"].split(" order: ")[0] for c in self.of(self.bank, "circuit")},
                         {"Standard branch", "Data center aggregation", "Retained Birch contract"})
        hook = self.of(self.bank, "webhook")[0]
        self.assertTrue(hook["attrs"]["payload_url"].split("/")[2].endswith(".invalid"))
        self.assertIs(self.of(self.bank, "event_rule")[0]["attrs"]["enabled"], False)
        # Restating a record's own field is a journal-facts failure, not a free edit.
        entry = next(e for e in self.of(self.provider, "journal_entry") if "\nAccepted into service" in e["attrs"]["comments"])
        broken = {**self.provider, "objects": [
            dict(o, attrs={**o["attrs"], "comments": o["attrs"]["comments"] + " Handoff: 10 Gbps."})
            if o["key"] == entry["key"] else o for o in self.provider["objects"]]}
        self.assertIn(("operations-journal-facts", entry["key"]),
                      {(f["code"], f["object"]) for f in validate(broken)})


class RecordDisclaimers(unittest.TestCase):
    """Records carry operational data only; limitations live in docs and the report."""

    # One former per-record disclaimer per family the 0.16 sweep removed.
    REMOVED = {
        "bgp_session": ("comments", "Documentation inventory: the intended peering is recorded, nothing is configured."),
        "circuit": ("comments", "Purchased capacity record; no forwarding acceptance test is claimed."),
        "provider_account": ("comments", "Commercial inventory account; no credentials or live purchase is claimed."),
        "virtual_machine": ("comments", "Independent host and rack lanes; service execution and recovery are not verified."),
        "asn_range": ("comments", "Peering records are documentation inventory, never applied configuration."),
        "provider_network": ("comments", "The control plane is documented inventory, not executed routing."),
        "prefix": ("comments", "The far end belongs to the upstream; its remote interface and owner are unknown."),
        "webhook": ("description", "NetOps automation receiver; placeholder .invalid host"),
        "rir": ("name", "IPv6 documentation registry"),
        "location": ("comments", "Monitoring endpoints are reference inventory; no clinical certification is claimed."),
        "device": ("comments", "AP mount positions are planned; RF coverage is unverified."),
        "asn": ("comments", "Planning intent; no configuration is asserted."),
        "module_type": ("attributes", '{"power_basis": "authored conservative reservation"}'),
    }

    def test_no_profile_emits_a_disclaimer(self):
        for path in sorted((ROOT / "profiles").glob("*.toml")):
            raw = tomllib.loads(path.read_text())
            if raw.get("profile") == "provider-backbone":
                raw["discovery_lab"] = True
            with self.subTest(profile=path.name):
                offenders = [(o["key"], disclaimer(o)) for o in generate(raw)["objects"] if disclaimer(o)]
                self.assertEqual(offenders, [], f"{path.name}: {offenders[:5]}")

    def test_an_injected_disclaimer_fails_validation_in_every_removed_family(self):
        plan = plan_for("provider-backbone")
        self.assertNotIn("record-disclaimer", {f["code"] for f in validate(plan)})
        for kind, (field, text) in self.REMOVED.items():
            with self.subTest(kind=kind):
                victim = next(o for o in plan["objects"] if o["kind"] == kind)
                original = victim["attrs"].get(field)
                victim["attrs"][field] = text
                try:
                    self.assertIn(("record-disclaimer", victim["key"]),
                                  {(f["code"], f["object"]) for f in validate(plan)})
                finally:
                    if original is None:
                        victim["attrs"].pop(field)
                    else:
                        victim["attrs"][field] = original

    def test_the_substantive_safety_properties_remain_structural(self):
        plan = plan_for("provider-backbone")
        hook = next(o for o in plan["objects"] if o["kind"] == "webhook")
        self.assertTrue(hook["attrs"]["payload_url"].split("/")[2].endswith(".invalid"))
        self.assertIs(next(o for o in plan["objects"] if o["kind"] == "event_rule")["attrs"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
