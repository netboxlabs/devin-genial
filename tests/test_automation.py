"""Automation records must stay estate-derived, inert, and loader-deliverable."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.automation import WEBHOOK_URL
from estates.bank import generate
from estates.branch import RETIREMENT_ENDPOINTS
from estates.diode import LOADER_ONLY_KINDS, deliverable, export, loader_only_records
from estates.generate import generate as generate_profile
from estates.load import load_diode, main as load_main
from estates.model import ROOT, canonical, digest
from estates.turbobulk import (BRANCH_EXEMPT_KINDS, MAIN_SCOPED_KINDS, REST_CREATE_KINDS,
                               SPECS, SUPPORTED_REFS, _create_rest, _expected_change_diff_counts,
                               _index, _render_rest_create)
from estates.validate_operations import validate

AUTOMATION_KEYS = {
    "config-context/global": "config_context",
    "config-context/switching": "config_context",
    "export-template/device-inventory": "export_template",
    "export-template/cable-report": "export_template",
    "webhook/netops": "webhook",
    "event-rule/device-change": "event_rule",
}


class AutomationRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"headquarters": 0, "branches": {"small": 1}})

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def test_every_profile_carries_the_same_owned_automation_pack(self):
        for profile in ("regional-bank", "enterprise-data-center", "school-district"):
            with self.subTest(profile=profile):
                plan = generate_profile({"profile": profile})
                ns = plan["recipe"]["namespace"]
                found = {obj["key"]: obj for obj in plan["objects"]
                         if obj["kind"] in LOADER_ONLY_KINDS}
                self.assertEqual({key: obj["kind"] for key, obj in found.items()}, AUTOMATION_KEYS)
                for obj in found.values():
                    self.assertTrue(obj["attrs"]["name"].startswith(f"{ns} "), obj["key"])
                    self.assertEqual(obj["refs"]["owner"], "owner/operations")
                    self.assertTrue(obj["meta"]["automation"])
                self.assertEqual(validate(plan), [])

    def test_server_lists_are_the_estates_own_service_addresses(self):
        data = self.objects["config-context/global"]["attrs"]["data"]
        bound = set()
        for obj in self.plan["objects"]:
            if obj["kind"] == "service":
                bound.update(self.objects[key]["attrs"]["address"].split("/")[0]
                             for key in obj["refs"]["ipaddresses"])
        self.assertTrue(set(data["dns_servers"]) <= bound)
        self.assertEqual(data["dns_servers"], data["service_endpoints"]["dns"])
        self.assertEqual(data["domain"], self.plan["recipe"]["namespace"] + ".example")
        for addresses in data["service_endpoints"].values():
            self.assertTrue(set(addresses) <= bound)
        self.assertEqual(validate(self.plan), [])

    def test_invented_or_dropped_service_endpoints_fail(self):
        mutations = (
            ("invented", lambda data: data["service_endpoints"]["dns"].append("192.0.2.53")),
            ("foreign-resolver", lambda data: data.update(dns_servers=["192.0.2.53"])),
            ("stale-resolver", lambda data: data["dns_servers"].append(
                data["service_endpoints"]["identity"][0])),
            ("dropped-workload", lambda data: data["service_endpoints"].pop("identity")),
            ("invented-workload", lambda data: data["service_endpoints"].update(ntp=["10.0.0.1"])),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                self.setUp()
                mutate(self.objects["config-context/global"]["attrs"]["data"])
                self.assertIn("automation-context-facts", self.codes())

    def test_dual_stack_lists_both_families_of_the_same_two_hosts(self):
        plan = generate({"headquarters": 0, "branches": {"small": 1},
                         "ipv6_pool": "2001:db8::/32"})
        objects = {obj["key"]: obj for obj in plan["objects"]}
        data = objects["config-context/global"]["attrs"]["data"]
        serving = {}
        for obj in plan["objects"]:
            if obj["kind"] == "service" and obj["key"].split("/")[3] == "dns":
                for key in obj["refs"]["ipaddresses"]:
                    serving[objects[key]["attrs"]["address"].split("/")[0]] = obj["refs"]["virtual_machine"]
        resolvers = data["dns_servers"]
        self.assertEqual(len(resolvers), 4)  # two hosts, two families each
        self.assertEqual(len({serving[address] for address in resolvers}), 2)
        self.assertEqual(len([address for address in resolvers if ":" in address]), 2)
        self.assertEqual(validate(plan), [])
        # A third host in the list is a roster, not a server list.
        extra = next(address for address, vm in sorted(serving.items())
                     if vm not in {serving[address] for address in resolvers})
        data["dns_servers"] = resolvers + [extra]
        data["service_endpoints"]["dns"] = data["dns_servers"]
        self.assertIn("automation-context-facts",
                      {finding["code"] for finding in validate(plan)})

    def test_global_context_cannot_carry_unexplained_keys_or_a_foreign_domain(self):
        for name, mutate in (("domain", lambda data: data.update(domain="acme.example")),
                             ("extra", lambda data: data.update(ntp_servers=["10.0.0.1"]))):
            with self.subTest(mutation=name):
                self.setUp()
                mutate(self.objects["config-context/global"]["attrs"]["data"])
                self.assertIn("automation-context", self.codes())

    def test_scope_references_must_exist_and_the_narrow_context_must_win(self):
        mutations = (
            ("missing-role", lambda plan, objects: objects["config-context/switching"]["refs"]
             .update(roles=["role/does-not-exist"])),
            ("unscoped", lambda plan, objects: objects["config-context/switching"]["refs"].pop("roles")),
            ("wrong-kind", lambda plan, objects: objects["config-context/switching"]["refs"]
             .update(roles=["site/dc-01"])),
            ("weight", lambda plan, objects: objects["config-context/switching"]["attrs"]
             .update(weight=10)),
            ("weight-type", lambda plan, objects: objects["config-context/global"]["attrs"]
             .update(weight="100")),
            ("no-data", lambda plan, objects: objects["config-context/switching"]["attrs"]
             .update(data={})),
            ("inactive", lambda plan, objects: objects["config-context/global"]["attrs"]
             .update(is_active=False)),
            ("removed", lambda plan, objects: plan["objects"].remove(
                objects["config-context/switching"])),
            ("unscoped-extra", lambda plan, objects: plan["objects"].append(
                {"key": "config-context/extra", "kind": "config_context",
                 "attrs": {"name": objects["config-context/global"]["attrs"]["name"] + " extra",
                           "description": "x", "weight": 5, "is_active": True, "data": {}},
                 "refs": {"owner": "owner/operations"}, "meta": {}})),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                self.setUp()
                mutate(self.plan, self.objects)
                self.assertIn("automation-context", self.codes())

    def test_export_templates_must_render_on_emitted_object_types(self):
        mutations = (
            ("no-loop", lambda attrs: attrs.update(template_code="name\n{{ device.name }}")),
            ("unbalanced", lambda attrs: attrs.update(
                template_code=attrs["template_code"].replace("{{ device.name }}", "{{ device.name }"))),
            ("column-count", lambda attrs: attrs.update(
                template_code=attrs["template_code"].replace("name,site,", "name,site,extra,"))),
            ("constant-column", lambda attrs: attrs.update(
                template_code=attrs["template_code"].replace("{{ device.serial }}", "unknown"))),
            ("foreign-object", lambda attrs: attrs.update(
                template_code=attrs["template_code"].replace("{{ device.name }}", "{{ site.name }}"))),
            ("absent-type", lambda attrs: attrs.update(object_types=["wireless.wirelesslink"])),
            ("not-csv", lambda attrs: attrs.update(mime_type="text/plain")),
            ("inline", lambda attrs: attrs.update(as_attachment=False)),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                self.setUp()
                mutate(self.objects["export-template/device-inventory"]["attrs"])
                self.assertIn("automation-template", self.codes())

    def test_missing_export_template_is_reported(self):
        self.plan["objects"].remove(self.objects["export-template/cable-report"])
        self.assertIn("automation-template", self.codes())

    def test_webhook_and_event_rule_stay_inert_and_connected(self):
        mutations = (
            ("reachable-host", lambda objects: objects["webhook/netops"]["attrs"]
             .update(payload_url="https://hooks.example.com/netops")),
            ("plain-http", lambda objects: objects["webhook/netops"]["attrs"]
             .update(payload_url="http://hooks.internal.invalid/netops")),
            ("no-tls-check", lambda objects: objects["webhook/netops"]["attrs"]
             .update(ssl_verification=False)),
            ("enabled", lambda objects: objects["event-rule/device-change"]["attrs"]
             .update(enabled=True)),
            ("unbound", lambda objects: objects["event-rule/device-change"]["refs"]
             .update(action_object="owner/operations")),
            ("script-action", lambda objects: objects["event-rule/device-change"]["attrs"]
             .update(action_type="script")),
            ("invented-event", lambda objects: objects["event-rule/device-change"]["attrs"]
             .update(event_types=["object_purged"])),
            ("absent-type", lambda objects: objects["event-rule/device-change"]["attrs"]
             .update(object_types=["wireless.wirelesslink"])),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                self.setUp()
                mutate(self.objects)
                self.assertIn("automation-event-rule", self.codes())
        self.assertTrue(WEBHOOK_URL.endswith(".invalid/netops"))

    def test_exactly_one_webhook_and_one_event_rule(self):
        for key in ("webhook/netops", "event-rule/device-change"):
            for mode in ("remove", "duplicate"):
                with self.subTest(object=key, mode=mode):
                    self.setUp()
                    victim = self.objects[key]
                    if mode == "remove":
                        self.plan["objects"].remove(victim)
                    else:
                        extra = deepcopy(victim)
                        extra["key"] += "-second"
                        extra["attrs"]["name"] += " (second)"
                        self.plan["objects"].append(extra)
                    self.assertIn("automation-event-rule", self.codes())

    def test_automation_records_keep_namespace_ownership_and_native_bounds(self):
        mutations = (
            ("namespace", lambda obj: obj["attrs"].update(name="Global service baseline")),
            ("long-name", lambda obj: obj["attrs"].update(name=obj["attrs"]["name"] + "x" * 100)),
            ("long-description", lambda obj: obj["attrs"].update(description="x" * 201)),
            ("unowned", lambda obj: obj["refs"].pop("owner")),
        )
        for key in AUTOMATION_KEYS:
            for name, mutate in mutations:
                with self.subTest(object=key, mutation=name):
                    self.setUp()
                    mutate(self.objects[key])
                    self.assertIn("automation-record", self.codes())


    def test_report_names_every_automation_record_with_its_limits(self):
        from collections import defaultdict
        from estates.report import _automation_walkthrough
        kinds = defaultdict(list)
        for obj in self.plan["objects"]:
            kinds[obj["kind"]].append(obj)
        text = "\n".join(_automation_walkthrough(self.objects, kinds))
        for key in AUTOMATION_KEYS:
            self.assertIn(self.objects[key]["attrs"]["name"], text)
        self.assertIn("Disabled: nothing is sent", text)
        self.assertIn("Unreachable by design", text)
        self.assertIn("no device is configured", text)

    def test_growth_preserves_automation_identities_and_existing_endpoints(self):
        grown = generate({"headquarters": 0, "branches": {"small": 3}}, previous=self.baseline)
        self.assertEqual(validate(grown), [])
        after = {obj["key"]: obj for obj in grown["objects"] if obj["kind"] in LOADER_ONLY_KINDS}
        self.assertEqual(set(after), set(AUTOMATION_KEYS))
        for key, obj in after.items():
            # Names are matching identities on the target: growth may never
            # rename them, and an existing endpoint list may never be rerolled.
            self.assertEqual(obj["attrs"]["name"], self.objects[key]["attrs"]["name"])
        before = self.objects["config-context/global"]["attrs"]["data"]["service_endpoints"]
        current = after["config-context/global"]["attrs"]["data"]["service_endpoints"]
        for workload, addresses in before.items():
            self.assertEqual(current[workload], addresses, workload)


class AutomationTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"headquarters": 0, "branches": {"small": 1}})

    def test_diode_package_omits_and_records_the_loader_only_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = export(self.plan, Path(temporary) / "diode")
        omitted = manifest["loader_only_records"]
        self.assertEqual(omitted["counts"], {"config_context": 2, "event_rule": 1,
                                             "export_template": 2, "webhook": 1})
        self.assertEqual(omitted["kinds"], sorted(LOADER_ONLY_KINDS))
        self.assertEqual(manifest["canonical_records"], len(self.plan["objects"]) - omitted["total"])
        self.assertFalse(set(manifest["counts"]) & LOADER_ONLY_KINDS)
        entities = 0
        for phase in manifest["phases"]:
            entities += sum(entry["entities"] for entry in manifest["files"]
                            if entry["path"] in phase["files"])
        self.assertEqual(entities, manifest["ingestion_entities"])
        self.assertEqual(loader_only_records(self.plan)["total"], omitted["total"])

    def test_a_delivered_record_may_never_reference_a_loader_only_record(self):
        plan = deepcopy(self.plan)
        site = next(obj for obj in plan["objects"] if obj["kind"] == "site")
        site["refs"]["owner"] = "webhook/netops"
        with self.assertRaisesRegex(ValueError, "reference loader-only records"):
            deliverable(_index(plan))

    def test_loader_covers_every_automation_kind_with_the_right_branch_scope(self):
        for kind in LOADER_ONLY_KINDS:
            self.assertIn(kind, SPECS)
            self.assertIn(kind, SUPPORTED_REFS)
            self.assertIn(kind, REST_CREATE_KINDS)
        # Branching 1.2.1 EXEMPT_MODELS covers the automation trio but not
        # config contexts, which stay branch-scoped and counted.
        exempt = {"event_rule", "export_template", "webhook"}
        self.assertTrue(exempt <= BRANCH_EXEMPT_KINDS & MAIN_SCOPED_KINDS)
        self.assertNotIn("config_context", BRANCH_EXEMPT_KINDS | MAIN_SCOPED_KINDS)
        expected = _expected_change_diff_counts(_index(self.plan))
        self.assertEqual(expected["extras.configcontext"], 2)
        for model in ("extras.eventrule", "extras.exporttemplate", "extras.webhook"):
            self.assertNotIn(model, expected)

    def test_type_audit_classifies_every_automation_model_as_loader_only(self):
        audit = json.loads((ROOT / "catalog/type-coverage.json").read_text())
        rows = {row["kind"]: row for row in audit["native_without_sdk"] if "kind" in row}
        self.assertEqual(set(rows), LOADER_ONLY_KINDS)
        for kind, row in rows.items():
            self.assertEqual(row["category"], "loader_only", kind)
            self.assertEqual(row["native_model"], SPECS[kind][0], kind)
            self.assertIs(row["sdk_top_level"], False, kind)
        # The audit stays the SDK-entity list; these four are not in it.
        self.assertFalse({row["kind"] for row in audit["types"]} & LOADER_ONLY_KINDS)

    def test_main_scoped_automation_rows_are_retired_before_their_owner(self):
        endpoints = [endpoint for endpoint, _mode in RETIREMENT_ENDPOINTS]
        for endpoint in ("/api/extras/event-rules/", "/api/extras/webhooks/",
                         "/api/extras/export-templates/"):
            self.assertIn(endpoint, endpoints)
        self.assertLess(endpoints.index("/api/extras/event-rules/"),
                        endpoints.index("/api/extras/webhooks/"))
        self.assertLess(endpoints.index("/api/extras/webhooks/"),
                        endpoints.index("/api/users/owners/"))
        self.assertNotIn("/api/extras/config-contexts/", endpoints)

    def test_rest_payloads_carry_scopes_and_the_generic_action_reference(self):
        objects = _index(self.plan)
        ids = {key: position for position, key in enumerate(objects, 1)}
        context = _render_rest_create(objects["config-context/switching"], ids, objects)
        self.assertEqual(context["roles"],
                         sorted(ids[role] for role in
                                objects["config-context/switching"]["refs"]["roles"]))
        self.assertEqual(context["owner"], ids["owner/operations"])
        rule = _render_rest_create(objects["event-rule/device-change"], ids, objects)
        self.assertEqual(rule["action_object_type"], "extras.webhook")
        self.assertEqual(rule["action_object_id"], ids["webhook/netops"])
        self.assertNotIn("action_object", rule)

    def test_rest_create_posts_the_automation_rows_and_resolves_their_ids(self):
        objects = _index(self.plan)
        ids = {"owner/operations": 3, "role/access": 4, "role/leaf": 5}

        class Client:
            def __init__(self):
                self.rows = {}
                self.posts = []

            def all(self, path):
                return self.rows.get(path, [])

            def request(self, path, **kwargs):
                payload = json.loads(kwargs["body"])
                self.posts.append((path, payload))
                row = {**payload, "id": 60 + len(self.posts)}
                self.rows.setdefault(path, []).append(row)
                return 201, row

        target = Client()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, path = {"rest_creates": []}, Path(temporary) / "receipt.json"
            for key in ("webhook/netops", "config-context/switching", "event-rule/device-change"):
                _create_rest(target, objects[key]["kind"], [objects[key]], ids,
                             receipt, path, objects)
            # A resumed run finds the rows already there and never posts twice.
            _create_rest(target, "webhook", [objects["webhook/netops"]], ids,
                         receipt, path, objects)
        posted = dict(target.posts)
        self.assertEqual(len(target.posts), 3)
        self.assertEqual(posted["/api/extras/webhooks/"]["payload_url"], WEBHOOK_URL)
        self.assertEqual(posted["/api/extras/config-contexts/"]["roles"], [4, 5])
        rule = posted["/api/extras/event-rules/"]
        self.assertEqual(rule["action_object_type"], "extras.webhook")
        self.assertEqual(rule["action_object_id"], ids["webhook/netops"])
        self.assertEqual(rule["owner"], 3)
        recovered = next(entry for entry in receipt["rest_creates"]
                         if entry["canonical_key"] == "webhook/netops")
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["target_id"], ids["webhook/netops"])

    def test_diode_lane_binds_the_whole_artifact_and_loads_what_it_can_carry(self):
        import os
        from unittest.mock import patch
        plan = {"schema_version": 1, "generator_version": "0.9.0",
                "recipe": {"as_of": "2026-09-09"}, "objects": [
                    {"key": "tag:demo", "kind": "tag",
                     "attrs": {"name": "Demo", "slug": "demo"}, "refs": {}},
                    {"key": "webhook/netops", "kind": "webhook",
                     "attrs": {"name": "acme hook", "payload_url": WEBHOOK_URL}, "refs": {}},
                    {"key": "event-rule/device-change", "kind": "event_rule",
                     "attrs": {"name": "acme rule"}, "refs": {"action_object": "webhook/netops"}}]}
        status = {"netbox-version": "4.7.0", "plugins": {
            "netbox_diode_plugin": "1.17.0", "netbox_branching": "1.1.2"}}
        branch = {"id": 1, "name": "Demo", "schema_id": "branch_schema",
                  "status": {"value": "ready"}}
        env = {"DIODE_TARGET": "grpc://diode.example", "DIODE_CLIENT_ID": "id",
               "DIODE_CLIENT_SECRET": "secret", "DIODE_MODE": "direct",
               "DIODE_BRANCH": "branch_schema", "DIODE_WRITES": "1",
               "DIODE_CONFIG_SOURCE": "https://netbox.example/plugins/diode/settings/",
               "DIODE_CONFIG_CONFIRMED_AT": "2099-01-01T00:00:00Z"}
        empty, populated = {"tag": []}, {"tag": [{"id": 7, "name": "Demo", "slug": "demo"}]}

        class Target:
            base, token, branch_id = "https://netbox.example", "token", "branch_schema"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(plan)}))
            export(plan, root / "diode")
            receipt = root / "receipt.json"
            with patch.dict(os.environ, env, clear=True), \
                    patch("estates.load._confirmed_at", return_value=True), \
                    patch("estates.load._target", return_value=(Target(), status, branch)), \
                    patch("estates.load._sdk", return_value=0.01), \
                    patch("estates.load.fetch_inventory",
                          side_effect=[empty, empty, empty, populated, populated]) as fetch, \
                    patch("estates.load._verify_paths", return_value={
                        "cables_expected": 0, "cables_traced": 0, "failures": []}), \
                    redirect_stderr(io.StringIO()) as printed:
                result = load_diode(root, url=Target.base, token=Target.token, branch="Demo",
                                    receipt_path=receipt, decision={"selected": "diode"}, timeout=1)
        # The manifest binds to the whole artifact; only delivery is restricted.
        self.assertTrue(result["success"])
        self.assertEqual(result["canonical_sha256"], digest(plan))
        self.assertEqual(result["loader_only_records"]["total"], 2)
        self.assertEqual(set(fetch.call_args.args[2]), {"tag"})
        self.assertIn("delivers a partial estate", printed.getvalue())

    def test_diode_scoped_readback_excludes_and_records_the_loader_only_records(self):
        from unittest.mock import patch
        import lab.verify as verify
        plan = {"objects": [
            {"key": "s", "kind": "site", "attrs": {"name": "DC"}, "refs": {}, "meta": {}},
            {"key": "w", "kind": "webhook", "attrs": {"name": "acme hook"}, "refs": {}, "meta": {}},
        ]}
        inventory = {"site": [{"id": 1, "name": "DC"}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text(json.dumps(plan))
            receipt = root / "readback.json"
            with patch("lab.verify.fetch_inventory", return_value=inventory) as fetch, \
                    redirect_stdout(io.StringIO()):
                status = verify.main([str(root / "plan.json"), "--url", "http://localhost:8000",
                                      "--receipt", str(receipt), "--strict-inventory",
                                      "--diode-delivered-only"])
            self.assertEqual(status, 0)
            # The webhook is never even read back: no Diode request could create it.
            self.assertEqual(set(fetch.call_args.args[2]), {"site"})
            recorded = json.loads(receipt.read_text())
        self.assertEqual(recorded["scope"], "diode-delivered")
        self.assertEqual(recorded["loader_only_records_excluded"], 1)
        self.assertTrue(any("loader-only" in limit for limit in recorded["limits"]))

    def test_load_check_counts_the_automation_kinds_as_loadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(self.plan)}))
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = load_main([str(root), "--load-check"])
        self.assertEqual(status, 0, error.getvalue())
        verdict = json.loads(output.getvalue())
        self.assertTrue(verdict["turbobulk_loadable"], verdict)
        self.assertEqual(verdict["turbobulk_uncovered"], [])
        self.assertEqual(verdict["turbobulk_only_kinds"], sorted(LOADER_ONLY_KINDS))
        self.assertEqual(verdict["turbobulk_only_records"], 6)
        self.assertTrue(set(LOADER_ONLY_KINDS) <= set(verdict["rest_create_kinds"]))


if __name__ == "__main__":
    unittest.main()
