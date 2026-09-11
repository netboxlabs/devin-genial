"""Real scope, authored chronology and append-only growth of operational context."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from estates.generate import generate
from estates.diode import export
from estates.validate_operations import validate


PROFILES = ("regional-bank", "enterprise-data-center", "school-district")
CONTEXT_KINDS = {"contact", "contact_group", "contact_role", "contact_assignment", "journal_entry"}


class OperationsContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = {profile: generate({"profile": profile}) for profile in PROFILES}
        cls.plans["provider-backbone"] = generate({"profile": "provider-backbone"})

    def plan(self, profile="enterprise-data-center"):
        plan = deepcopy(self.plans[profile])
        plan["contracts"] = []
        for obj in plan["objects"]:
            obj["meta"] = {}
        return plan, {obj["key"]: obj for obj in plan["objects"]}

    def assert_code(self, plan, code):
        self.assertIn(code, {finding["code"] for finding in validate(plan)})

    def provider_plan(self):
        plan, objects = self.plan("provider-backbone")
        plan.pop("contracts")
        for obj in plan["objects"]:
            obj.pop("meta")
        return plan, objects

    def test_provider_handoff_notes_distinguish_network_boundaries_and_real_sites(self):
        plan, objects = self.provider_plan()
        terms = {(o["refs"]["circuit"], o["attrs"]["term_side"]): o for o in objects.values()
                 if o["kind"] == "circuit_termination"}
        observed = set()
        for circuit in (o for o in objects.values() if o["kind"] == "circuit"):
            key, data = circuit["key"], circuit["attrs"]
            a, z = terms[key, "A"], terms[key, "Z"]
            local, remote = (objects[t["refs"]["termination"]] for t in (a, z))
            note = objects[f"journal/{key}/handoff-plan"]
            header = f"{data['install_date']} — Circuit handoff plan\n"
            if remote["kind"] == "provider_network":
                expected = (f"Circuit: {data['cid']}\nA termination: {local['attrs']['name']}\n"
                    f"Z network boundary: {remote['attrs']['name']}\nA handoff: {a['attrs']['port_speed']} kbps\n"
                    f"Recorded service date: {data['install_date']}\nRemote interface and owner: unknown.\n"
                    "Use the A termination to coordinate the local handoff; the Z record identifies an external network boundary.")
                self.assertNotIn("Z handoff:", note["attrs"]["comments"])
            else:
                # Preserve the pre-correction wording for actual two-site circuits.
                expected = (f"Circuit: {data['cid']}\nA termination: {local['attrs']['name']}\nZ termination: {remote['attrs']['name']}\n"
                    f"A handoff: {a['attrs']['port_speed']} kbps\nZ handoff: {z['attrs']['port_speed']} kbps\n"
                    f"Recorded service date: {data['install_date']}\nUse both termination records to coordinate the local handoffs.")
            self.assertEqual(note["attrs"], {"kind": "info", "comments": header + expected})
            self.assertEqual(note["refs"], {"assigned_object": key})
            observed.add(remote["kind"])
        self.assertEqual(observed, {"site", "provider_network"})
        self.assertEqual(validate(plan), [])

    def test_external_note_cannot_claim_two_local_physical_handoffs(self):
        plan, objects = self.provider_plan()
        circuit = objects["circuit/transit/a"]
        a, z = (objects[f"{circuit['key']}/{side}"] for side in ("A", "Z"))
        local, remote = (objects[t["refs"]["termination"]] for t in (a, z))
        data = circuit["attrs"]
        note = objects[f"journal/{circuit['key']}/handoff-plan"]
        note["attrs"]["comments"] = (f"{data['install_date']} — Circuit handoff plan\nCircuit: {data['cid']}\n"
            f"A termination: {local['attrs']['name']}\nZ termination: {remote['attrs']['name']}\n"
            f"A handoff: {a['attrs']['port_speed']} kbps\nZ handoff: {z['attrs']['port_speed']} kbps\n"
            f"Recorded service date: {data['install_date']}\nUse both termination records to coordinate the local handoffs.")
        self.assert_code(plan, "operations-journal-facts")

    def test_external_handoff_facts_and_unknown_remote_scope_are_checked(self):
        mutations = (("A termination: ", "A termination: wrong "),
                     ("Z network boundary: ", "Z network boundary: wrong "),
                     ("A handoff: 10000000 kbps", "A handoff: 1000000 kbps"),
                     ("Remote interface and owner: unknown.", "Remote interface and owner: transit-router xe-0/0/0."),
                     ("the Z record identifies an external network boundary.", "both local physical handoffs are installed."))
        for before, after in mutations:
            plan, objects = self.provider_plan()
            note = objects["journal/circuit/transit/a/handoff-plan"]
            self.assertIn(before, note["attrs"]["comments"])
            note["attrs"]["comments"] = note["attrs"]["comments"].replace(before, after)
            with self.subTest(field=before):
                self.assert_code(plan, "operations-journal-facts")

    def test_handoff_form_follows_actual_z_kind_not_circuit_name(self):
        for subject, other in (("circuit/transit/a", "circuit/backbone/seed-01"),
                               ("circuit/backbone/seed-01", "circuit/transit/a")):
            plan, objects = self.provider_plan()
            objects[f"{subject}/Z"]["refs"]["termination"] = objects[f"{other}/Z"]["refs"]["termination"]
            with self.subTest(subject=subject):
                self.assert_code(plan, "operations-journal-facts")

    def test_all_profiles_have_bounded_meaningful_history_without_metadata(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan, objects = self.plan(profile)
                self.assertEqual(validate(plan), [])
                scopes = {(o["refs"]["cluster"], o["key"].split("/")[2]) for o in plan["objects"] if o["kind"] == "virtual_machine"}
                journals = [o for o in plan["objects"] if o["kind"] == "journal_entry"]
                legacy_notes = [o for o in journals if objects[o["refs"]["assigned_object"]]["kind"] != "device"]
                self.assertEqual(len(legacy_notes), 2 * (len(scopes) + sum(o["kind"] in {"site", "circuit"} for o in plan["objects"])))
                by_target = {}
                for note in journals:
                    self.assertNotIn("Synthetic", note["attrs"]["comments"])
                    target = note["refs"]["assigned_object"]
                    by_target.setdefault(target, []).append(note)
                    self.assertIn(objects[target]["kind"], {"site", "circuit", "virtual_machine", "device"})
                for notes in by_target.values():
                    dates = [date.fromisoformat(o["attrs"]["comments"][:10]) for o in notes]
                    self.assertEqual(len(set(dates)), len(notes))
                    self.assertLess(max(dates), date.fromisoformat(plan["recipe"]["as_of"]))
                contacts = [o for o in plan["objects"] if o["kind"] == "contact"]
                self.assertEqual(len({o["attrs"]["name"] for o in contacts}), len(contacts))
                self.assertTrue(all(o["attrs"]["email"].endswith(f"@{plan['recipe']['namespace']}.example") for o in contacts))
                self.assertEqual(objects["contact/operations"]["attrs"]["email"], f"noc@{plan['recipe']['namespace']}.example")

    def test_missing_roles_groups_contacts_and_assignments_fail(self):
        for kind in ("contact", "contact_role", "contact_group", "contact_assignment"):
            with self.subTest(kind=kind):
                plan, _ = self.plan()
                victim = next(o for o in plan["objects"] if o["kind"] == kind)
                plan["objects"].remove(victim)
                self.assert_code(plan, "operations-contact")

    def test_contact_scopes_cannot_cross_tenants_sites_providers_or_workloads(self):
        cases = (("contact-assignment/site/dc-01", "contact/operations/tenant/inherited"),
                 ("contact-assignment/site/dc-01/facilities", "contact/site/dc-02"),
                 ("contact-assignment/circuit/dc-01/a/1/carrier", "contact/provider/b"),
                 ("contact-assignment/vm/dc-01/identity/001", "contact/service/tenant/dns"))
        for assignment, contact in cases:
            with self.subTest(assignment=assignment):
                plan, objects = self.plan("regional-bank")
                self.assertIn(contact, objects)
                objects[assignment]["refs"]["contact"] = contact
                self.assert_code(plan, "operations-contact")

    def test_independent_inherited_tenant_has_its_own_technical_desk(self):
        plan = generate({"headquarters": 0, "branches": {"small": 1}, "site_designs": {"br-s0001": "inherited"}})
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertEqual(objects["site/br-s0001"]["refs"]["tenant"], "tenant/inherited")
        for key in ("site/br-s0001", "circuit/br-s0001/a/1", "circuit/br-s0001/b/1"):
            self.assertEqual(objects[f"contact-assignment/{key}"]["refs"]["contact"], "contact/operations/tenant/inherited")
        self.assertEqual(validate(plan), [])

    def test_contact_group_mailbox_name_and_assignment_identity_fail_closed(self):
        mutations = (("group", lambda obj: obj["refs"].update(groups=["contact-group/carrier"])),
                     ("mail", lambda obj: obj["attrs"].update(email="noc@example.com")),
                     ("name", lambda obj: obj["attrs"].update(name="Other tenant desk")))
        for name, mutate in mutations:
            with self.subTest(name=name):
                plan, objects = self.plan()
                mutate(objects["contact/operations"])
                self.assert_code(plan, "operations-contact")
        plan, objects = self.plan()
        duplicate = deepcopy(objects["contact-assignment/site/dc-01"])
        duplicate["key"] = "contact-assignment/duplicate"
        plan["objects"].append(duplicate)
        self.assert_code(plan, "operations-contact")

    def test_contacts_cannot_claim_unmodeled_guarantees_or_contain_real_phone_numbers(self):
        plan, objects = self.plan()
        objects["contact/operations"]["attrs"]["description"] = "summit estate guarantees 24/7 staffed coverage and automatic carrier failover."
        self.assert_code(plan, "operations-contact")
        plan, objects = self.plan()
        objects["contact/operations"]["attrs"]["phone"] = "+1-555-0100"
        self.assert_code(plan, "operations-contact")

    def test_maximum_supported_names_fit_native_contact_fields(self):
        plan = generate({"profile": "enterprise-data-center", "namespace": "n" * 20, "name": "N" * 80,
                         "data_centers": 1, "workloads": [{"key": "w" * 40}]})
        self.assertEqual(validate(plan), [])
        for obj in plan["objects"]:
            if obj["kind"] == "contact":
                self.assertLessEqual(len(obj["attrs"]["name"]), 100)
                self.assertLessEqual(len(obj["attrs"]["description"]), 200)
                self.assertLessEqual(len(obj["attrs"]["email"].split("@")[0]), 64)

    def test_required_journal_cannot_be_omitted_or_retyped(self):
        for mode in ("remove", "retype"):
            with self.subTest(mode=mode):
                plan, objects = self.plan()
                note = objects["journal/site/dc-01/site-record"]
                if mode == "remove":
                    plan["objects"].remove(note)
                else:
                    note.update(kind="tag", attrs={"name": "A tag is not a journal", "slug": "wrong"}, refs={})
                self.assert_code(plan, "operations-journal")

    def test_journal_subject_and_account_binding_are_checked(self):
        for refs in ({"assigned_object": "site/dc-02"}, {"assigned_object": "site/dc-01", "created_by": "owner/operations"}):
            plan, objects = self.plan()
            objects["journal/site/dc-01/site-record"]["refs"] = refs
            self.assert_code(plan, "operations-journal")

    def test_journal_facts_and_added_execution_claim_are_rejected(self):
        for key, before, after in (
                ("journal/circuit/dc-01/a/1/capacity-request", "1000000 kbps", "999999 kbps"),
                ("journal/circuit/dc-01/a/1/handoff-plan", "summit-dc-01", "summit-dc-02"),
                ("journal/vm/dc-01/inventory-api/001/resource-plan", "8192 MB", "16384 MB"),
                ("journal/vm/dc-01/inventory-api/001/listener-plan", "tcp/443", "tcp/444")):
            with self.subTest(key=key):
                plan, objects = self.plan()
                note = objects[key]
                self.assertIn(before, note["attrs"]["comments"])
                note["attrs"]["comments"] = note["attrs"]["comments"].replace(before, after)
                self.assert_code(plan, "operations-journal-facts")
        plan, objects = self.plan()
        objects["journal/site/dc-01/access-plan"]["attrs"]["comments"] += "\nChange executed successfully."
        self.assert_code(plan, "operations-journal-facts")

    def test_invalid_future_and_shifted_dates_are_rejected(self):
        for replacement in ("2099-01-01", "2026-02-30", "2000-01-01"):
            with self.subTest(date=replacement):
                plan, objects = self.plan()
                note = objects["journal/site/dc-01/site-record"]
                note["attrs"]["comments"] = replacement + note["attrs"]["comments"][10:]
                self.assert_code(plan, "operations-journal-date")

    def test_nontext_contact_and_journal_fields_are_findings(self):
        for field in ("description", "name"):
            plan, objects = self.plan()
            objects["contact/operations"]["attrs"][field] = []
            self.assert_code(plan, "operations-contact")
        plan, objects = self.plan()
        objects["journal/site/dc-01/site-record"]["attrs"]["comments"] = None
        self.assert_code(plan, "operations-journal-facts")

    def test_nontext_source_site_facts_are_findings(self):
        for field in ("physical_address", "name", "time_zone"):
            for bad in ([], {}, None):
                with self.subTest(field=field, value=bad):
                    plan, objects = self.plan("school-district")
                    objects["site/dc-01"]["attrs"][field] = bad
                    self.assert_code(plan, "operations-journal-facts")

    def test_seeds_reproduce_and_change_only_their_own_authored_dates(self):
        for profile in PROFILES:
            plans = [generate({"profile": profile, "seed": seed}) for seed in (7, 107)]
            for plan in plans:
                self.assertEqual(validate(plan), [])
                self.assertEqual(plan, generate(plan["recipe"]))
            first = {o["key"]: o for o in plans[0]["objects"] if o["kind"] == "journal_entry"}
            second = {o["key"]: o for o in plans[1]["objects"] if o["kind"] == "journal_entry"}
            self.assertEqual(first.keys(), second.keys())
            self.assertNotEqual(first, second)

    def test_ordinary_growth_keeps_every_existing_context_record(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                old = self.plans[profile]
                recipe = deepcopy(old["recipe"])
                if profile == "regional-bank":
                    recipe["branches"]["small"] += 2
                elif profile == "enterprise-data-center":
                    recipe["data_centers"] += 1
                    recipe["wan_peak_mbps"] += 1000
                    recipe["workloads"][0]["groups"] += 3
                    recipe["workloads"].append(dict(key="z-new-service", groups=1))
                else:
                    recipe["schools"][0]["classrooms"] += 4
                    recipe["schools"].append(dict(key="valley", classrooms=2))
                grown = generate(recipe, previous=old)
                self.assertEqual(validate(grown), [])
                current = {o["key"]: o for o in grown["objects"]}
                for obj in old["objects"]:
                    if obj["kind"] in CONTEXT_KINDS:
                        self.assertEqual(obj, current[obj["key"]], obj["key"])

    def test_export_carries_native_vm_contacts_and_four_journal_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            export(self.plans["enterprise-data-center"], Path(directory))
            entities = []
            for path in Path(directory).glob("*.json"):
                entities.extend(json.loads(path.read_text()).get("entities", []))
            assignments = [e["contact_assignment"] for e in entities if "contact_assignment" in e]
            journals = [e["journal_entry"] for e in entities if "journal_entry" in e]
            self.assertTrue(any("object_virtual_machine" in assignment for assignment in assignments))
            for target in ("site", "circuit", "virtual_machine", "device"):
                self.assertTrue(any(f"assigned_object_{target}" in journal for journal in journals), target)
            self.assertTrue(all("created_by" not in journal for journal in journals))


if __name__ == "__main__":
    unittest.main()
