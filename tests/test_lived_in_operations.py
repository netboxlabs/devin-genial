"""v0.18 lived-in operations: the shared validators re-derive every new journal,
the plant's install days, legacy names, withdrawn circuits and the IX peering
LAN from the finished graph, and each check has a failing mutation."""

from copy import deepcopy
import re
import tomllib
import unittest

from estates.generate import generate
from estates.model import ROOT
from estates.validate import validate as validate_all
from estates.validate_ipv6 import validate as validate_ipv6
from estates.validate_operations import validate


def codes(findings):
    return {(f["code"], f["object"]) for f in findings}


class LivedInOperations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = generate(tomllib.loads((ROOT / "profiles/showcase-provider.toml").read_text()))
        cls.ops = codes(validate(cls.base))
        cls.six = codes(validate_ipv6(cls.base))

    def mutate(self, change, check=validate):
        plan = deepcopy(self.base)
        objects = {o["key"]: o for o in plan["objects"]}
        change(plan, objects)
        return codes(check(plan)) - (self.six if check is validate_ipv6 else self.ops)

    def journal(self, objects, pattern):
        return next(o for k, o in objects.items() if o["kind"] == "journal_entry" and re.search(pattern, k))

    def edit(self, pattern, before, after):
        def change(plan, objects):
            note = self.journal(objects, pattern)
            self.assertIn(before, note["attrs"]["comments"])
            note["attrs"]["comments"] = note["attrs"]["comments"].replace(before, after, 1)
        return change

    def assert_flags(self, code, change, check=validate):
        self.assertIn(code, {c for c, _ in self.mutate(change, check)})

    def test_baseline_operations_and_ipv6_are_clean(self):
        # The whole-plan pass (which sets the network lab aside as its own
        # closed slice); provider-side findings belong to validate_provider.
        ours = ("operations-", "ipv6-", "circuit-terminations", "record-disclaimer")
        self.assertEqual({f for f in codes(validate_all(self.base)) if f[0].startswith(ours)}, set())

    def test_every_new_journal_shape_is_bold_title_and_banded_time(self):
        notes = [o for o in self.base["objects"] if o["kind"] == "journal_entry"]
        self.assertFalse([o for o in notes if o["key"].endswith("/access-plan")])
        for note in notes:
            day = re.match(r"\*\*[^*\n]+\*\* · (\d{4}-\d{2}-\d{2})\n\n", note["attrs"]["comments"]).group(1)
            hour = int(note["attrs"]["created"][11:13])
            self.assertEqual(note["attrs"]["created"][:10], day)
            self.assertTrue(4 <= hour < 9 if "/maintenance-" in note["key"] else 14 <= hour < 22, note["key"])
        self.assert_flags("operations-journal-date", lambda plan, objects: self.journal(objects, r"/maintenance-1$")["attrs"].update(
            created=self.journal(objects, r"/maintenance-1$")["attrs"]["created"][:11] + "15:00:00Z"))
        self.assert_flags("operations-journal-date", lambda plan, objects: self.journal(objects, r"/handover$")["attrs"].update(
            created=self.journal(objects, r"/handover$")["attrs"]["created"][:11] + "05:00:00Z"))

    def test_site_access_is_a_site_comment(self):
        self.assert_flags("operations-site-access", lambda plan, objects: objects["site/dc-01"]["attrs"].update(comments="Visits by appointment."))
        self.assert_flags("operations-site-access", lambda plan, objects: objects["site/dc-01"]["attrs"].update(
            comments=objects["site/dc-01"]["attrs"]["comments"].replace("booked through", "booked through the wrong")))

    def test_carrier_paperwork_is_re_derived(self):
        self.assert_flags("operations-journal-facts", self.edit(r"/A/cross-connect-order$", "LOA-", "LOA-X"))
        self.assert_flags("operations-journal-facts", self.edit(r"/cir-upgrade-1$", "raised from ", "raised from 1"))
        self.assert_flags("operations-journal-facts", self.edit(r"/term-renewal-1$", "36 months", "12 months"))
        self.assert_flags("operations-journal-facts", self.edit(r"/maintenance-1$", "Status: completed", "Status: cancelled"))
        self.assert_flags("operations-journal", lambda plan, objects: plan["objects"].remove(self.journal(objects, r"/term-renewal-1$")))
        self.assert_flags("operations-journal", lambda plan, objects: plan["objects"].remove(self.journal(objects, r"/cage-audit-\d{4}$")))
        self.assert_flags("operations-journal", lambda plan, objects: plan["objects"].remove(objects["journal/site/dc-01/notice-journaling"]))

    def test_plant_history_is_re_derived(self):
        self.assert_flags("operations-journal-facts", self.edit(r"cermak/pe-a/cut-over$", "MX80 ", "MX80 x"))
        self.assert_flags("operations-journal-facts", self.edit(r"/removed/original-agg-a$", " U3", " U2"))
        self.assert_flags("operations-journal-facts", self.edit(r"/agg-a/end-of-sale$", "2027-12-31", "2028-12-31"))
        self.assert_flags("operations-journal", lambda plan, objects: plan["objects"].remove(self.journal(objects, r"cermak/legacy-pe-a/installed$")))

        def removed_moves(plan, objects):
            # A replacement whose rack gap was journaled on another day.
            note = self.journal(objects, r"cermak/agg-a/replaced$")
            title, rest = note["attrs"]["comments"].split(" · ", 1)
            day = rest[:10]
            later = f"{int(day[:4]) + 1}{day[4:]}"
            note["attrs"]["comments"] = f"{title} · {later}{rest[10:]}".replace(f"cut over {day[:7]}", f"cut over {later[:7]}")
        self.assert_flags("operations-journal-date", removed_moves)

    def test_exchange_ports_follow_their_cabled_path(self):
        self.assert_flags("operations-journal-facts", self.edit(r"/ix-port-chicago$", "xe-0/1/5", "xe-0/1/4"))
        self.assert_flags("operations-journal", lambda plan, objects: plan["objects"].remove(self.journal(objects, r"/ix-port-milwaukee-shut$")))

    def test_former_customer_circuits_keep_no_terminations(self):
        def terminated(plan, objects):
            plan["objects"].append({"key": "circuit/former/brew-city-labs/A", "kind": "circuit_termination", "attrs": {"term_side": "A"},
                                    "refs": {"circuit": "circuit/former/brew-city-labs", "termination": "site/dc-01"}, "meta": {}})
        self.assert_flags("operations-journal", terminated)
        self.assert_flags("circuit-terminations", terminated, validate_all)

        def undated(plan, objects):
            objects["circuit/former/brew-city-labs"]["attrs"].pop("termination_date")
        self.assert_flags("circuit-terminations", undated, validate_all)
        self.assert_flags("circuit-terminations", lambda plan, objects: objects["circuit/former/brew-city-labs"]["attrs"].update(status="active"),
                          validate_all)
        self.assert_flags("operations-journal-facts", self.edit(r"former/brew-city-labs/service-ceased$", "NID not recovered", "NID recovered"))

    def test_plant_serials_and_legacy_names_follow_install_days(self):
        self.assert_flags("operations-serial-date", lambda plan, objects: objects["device/pop-chicago-cermak/agg-spare"]["attrs"].update(
            serial="WR1026211023"))
        self.assert_flags("operations-tag", lambda plan, objects: objects["device/pop-chicago-cermak/agg-a"]["refs"]["tags"].remove(
            "tag/legacy-naming"))
        self.assert_flags("operations-tag", lambda plan, objects: objects["device/pop-chicago-cermak/pe-a"]["refs"].setdefault(
            "tags", []).append("tag/legacy-naming"))
        # A legacy name on a device whose own history says it arrived after NS-2.
        self.assert_flags("operations-tag", lambda plan, objects: objects["device/pop-chicago-cermak/pe-a"]["attrs"].update(
            name="chcgilcr-rtr9"))

    def test_only_equipment_in_service_needs_a_live_management_path(self):
        # A cold spare or retired MX80 is uncabled; the same chassis in service is a finding.
        flagged = {f for f in codes(validate_all(self.base)) if f[0] == "management-room"}
        self.assertEqual(flagged, set())
        for device in ("device/pop-chicago-cermak/agg-spare", "device/pop-chicago-cermak/legacy-pe-a"):
            with self.subTest(device=device):
                self.assertIn(("management-room", device), self.mutate(
                    lambda plan, objects: objects[device]["attrs"].update(status="active"), validate_all))

    def test_plant_roles_carry_the_operations_desk(self):
        self.assert_flags("operations-contact", lambda plan, objects: plan["objects"].remove(
            objects["contact-assignment/device/pop-chicago-cermak/ddos-01"]))

    def test_ipv6_only_peering_lan_is_bound_to_an_exchange(self):
        def stray(plan, objects):
            # An unassigned IPv6 address outside any exchange LAN still has no obligation.
            plan["objects"].append({"key": "ix/chicago/rs-9", "kind": "ip_address", "attrs": {"address": "3fff:fff:1ff::9/64", "status": "active"},
                                    "refs": {}, "meta": {}})
        self.assert_flags("ipv6-unexpected", stray, validate_ipv6)

        def uncabled(plan, objects):
            # Without the PE's cabled path to the exchange the LAN is no exchange's.
            for cable in [o for o in plan["objects"] if o["kind"] == "cable"
                          and "device/pop-chicago-cermak/pe-a/if/xe-0/1/5" in (o["refs"]["a"], o["refs"]["b"])]:
                plan["objects"].remove(cable)
        self.assertIn(("ipv6-unexpected", "ix/chicago/lan"), self.mutate(uncabled, validate_ipv6))


if __name__ == "__main__":
    unittest.main()
