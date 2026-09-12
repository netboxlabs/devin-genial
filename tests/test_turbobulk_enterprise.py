"""The enterprise profile must have an explicit, fail-closed transport contract."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from estates.generate import generate
from estates.model import canonical, digest, recipe_from_file
from estates.turbobulk import (LoadError, REST_CREATE_KINDS, SPECS, SUPPORTED_REFS,
                               _bound_job_id, _complete_rest, _create_rest, _index,
                               _matches, _render, _rendered_columns, _required_content_types,
                               _schema_preflight, _submit, delivery_contract, load)


ROOT = Path(__file__).resolve().parents[1]


class SchemaClient:
    def __init__(self, objects, rest=True, service_shape="protocol_ports"):
        self.calls = []
        self.schemas = {}
        for obj in objects.values():
            model = SPECS[obj["kind"]][0]
            if model:
                self.schemas.setdefault(model, set()).update(
                    obj["attrs"] if obj["kind"] == "cable" else
                    _rendered_columns(obj, service_shape))
        if any(obj["kind"] == "cable" for obj in objects.values()):
            self.schemas["dcim.cabletermination"] = {
                "cable_id", "cable_end", "termination_type_id", "termination_id"}
        self.rest = rest

    def request(self, path, method="GET", **_kwargs):
        self.calls.append((method, path))
        if path == "/api/schema/?format=json":
            parameters = [{"name": name, "in": "query"}
                          for name in ("site_id", "location_id", "rack_id")]
            return 200, {"paths": {
                SPECS[kind][1]: {"get": {"parameters": parameters}}
                for kind in ("console_port", "console_server_port", "interface", "module_bay",
                             "power_outlet", "power_port")
            }}
        if path == SPECS["module_bay_type"][1]:
            if not self.rest:
                raise LoadError("HTTP 404")
            return 200, {"actions": {"POST": {
                "name": {}, "slug": {}, "color": {}, "manufacturer": {}}}}
        if method == "OPTIONS":
            return 200, {"actions": {"PATCH": {field: {} for field in (
                "groups", "ipaddresses", "module_bay_types", "oob_ip", "primary_ip4",
                "primary_ip6", "primary_mac_address", "tagged_vlans", "tags")}}}
        if path.endswith("/models/"):
            return 200, [{"full_name": model, "export_only": False} for model in self.schemas]
        model = path.removeprefix("/api/plugins/turbobulk/models/").removesuffix("/")
        return 200, {"fields": [{"name": field} for field in self.schemas[model]]}


class EnterpriseTurboBulkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe_from_file(ROOT / "profiles/enterprise-dc.toml"))
        cls.objects = _index(cls.plan)

    def test_all_53_kinds_and_references_have_declarative_compilers(self):
        kinds = {obj["kind"] for obj in self.objects.values()}
        self.assertEqual(len(kinds), 53)
        self.assertFalse(kinds - SPECS.keys())
        unsupported = {(obj["kind"], ref) for obj in self.objects.values()
                       for ref in set(obj["refs"]) - SUPPORTED_REFS[obj["kind"]]}
        self.assertFalse(unsupported)
        self.assertEqual({"circuit_termination", "console_port", "console_server_port", "interface",
                          "power_feed", "power_outlet", "power_port"}
                         & _required_content_types(self.objects),
                         {"circuit_termination", "console_port", "console_server_port", "interface",
                          "power_feed", "power_outlet", "power_port"})
        result = _schema_preflight(SchemaClient(self.objects), self.objects)
        self.assertEqual(result["rest_create_fields"], {"module_bay_type": 4})
        ids = {key: position for position, key in enumerate(self.objects, 1)}
        content_types = {kind: position for position, kind in
                         enumerate(sorted(_required_content_types(self.objects)), 1)}
        for obj in self.objects.values():
            if obj["kind"] not in REST_CREATE_KINDS and obj["kind"] != "cable":
                _render(obj, self.objects, ids, content_types)
        module_type = next(obj for obj in self.objects.values() if obj["kind"] == "module_type")
        self.assertIn("attribute_data", _rendered_columns(module_type))
        self.assertNotIn("attributes", _rendered_columns(module_type))

    def test_service_compiles_to_target_selected_schema(self):
        vm = {"key": "vm:1", "kind": "virtual_machine", "attrs": {}, "refs": {}}
        service = {"key": "service:1", "kind": "service",
                   "attrs": {"name": "dns", "protocol": "udp", "ports": [53, 5353]},
                   "refs": {"virtual_machine": vm["key"]}}
        context = ({vm["key"]: vm, service["key"]: service},
                   {vm["key"]: 8}, {"virtual_machine": 12})
        row = _render(service, *context, service_shape="port_mappings")
        self.assertEqual(row["port_mappings"], ["udp/53", "udp/5353"])
        self.assertNotIn("protocol", row)
        self.assertNotIn("ports", row)
        self.assertEqual(_rendered_columns(service, "port_mappings"),
                         {"name", "port_mappings", "parent_object_type_id", "parent_object_id"})
        old_row = _render(service, *context, service_shape="protocol_ports")
        self.assertEqual((old_row["protocol"], old_row["ports"]), ("udp", [53, 5353]))
        self.assertNotIn("port_mappings", old_row)
        self.assertEqual(_schema_preflight(SchemaClient(self.objects, service_shape="protocol_ports"),
                                           self.objects)["service_shape"], "protocol_ports")
        self.assertEqual(_schema_preflight(SchemaClient(self.objects, service_shape="port_mappings"),
                                           self.objects)["service_shape"], "port_mappings")
        service["attrs"]["ports"] = [53, True]
        with self.assertRaisesRegex(LoadError, "unique integers"):
            _rendered_columns(service)

    def test_missing_module_bay_type_model_fails_before_any_turbobulk_write(self):
        client = SchemaClient(self.objects, rest=False)
        with self.assertRaisesRegex(LoadError, "no writable REST model.*module_bay_type"):
            _schema_preflight(client, self.objects)
        self.assertEqual(client.calls, [("OPTIONS", SPECS["module_bay_type"][1])])
        self.assertEqual(REST_CREATE_KINDS, {"module_bay_type"})

    def test_missing_rest_completion_field_fails_preflight(self):
        client = SchemaClient(self.objects)
        original = client.request

        def without_groups(path, method="GET", **kwargs):
            status, value = original(path, method=method, **kwargs)
            if method == "OPTIONS" and path == SPECS["contact"][1]:
                value["actions"]["PATCH"].pop("groups")
            return status, value

        client.request = without_groups
        with self.assertRaisesRegex(LoadError, "cannot write: groups"):
            _schema_preflight(client, self.objects)

    def test_rich_artifact_disposable_policy_refuses_before_target_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
            (root / "checks.json").write_text(json.dumps(
                {"status": "passed", "plan_sha256": digest(self.plan)}))
            with patch("estates.turbobulk.Client") as client:
                with self.assertRaisesRegex(
                        LoadError, "REST create: module_bay_type.*REST completion PATCH"):
                    load(root, url="https://netbox.example", token="fixture", branch="Demo",
                         receipt_path=root / "receipt.json",
                         delivery_policy="disposable-baseline")
            client.assert_not_called()
            self.assertFalse((root / "receipt.json").exists())

    def test_rest_create_recovers_only_when_durable_intent_matches_target(self):
        manufacturer = {"key": "manufacturer:acme", "kind": "manufacturer",
                        "attrs": {"name": "Acme", "slug": "acme"}, "refs": {}}
        bay_type = {"key": "module-bay-type:acme:linecard", "kind": "module_bay_type",
                    "attrs": {"name": "Linecard", "slug": "linecard", "color": "9e9e9e"},
                    "refs": {"manufacturer": manufacturer["key"]}}

        class Client:
            def __init__(self):
                self.rows = []
                self.posts = 0

            def all(self, _path):
                return self.rows

            def request(self, _path, **kwargs):
                self.posts += 1
                payload = json.loads(kwargs["body"])
                row = {**payload, "id": 71, "manufacturer": {"id": payload["manufacturer"]}}
                self.rows = [row]
                return 201, row

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"rest_creates": []}
            ids = {manufacturer["key"]: 7}
            target = Client()
            _create_rest(target, "module_bay_type", [bay_type], ids, receipt, receipt_path)
            self.assertEqual(ids[bay_type["key"]], 71)
            self.assertEqual(target.posts, 1)
            ids.pop(bay_type["key"])
            _create_rest(target, "module_bay_type", [bay_type], ids, receipt, receipt_path)
            self.assertEqual(ids[bay_type["key"]], 71)
            self.assertEqual(target.posts, 1)
            self.assertTrue(receipt["rest_creates"][0]["recovered"])

    def test_turbobulk_submit_records_intent_before_ambiguous_failure(self):
        class Client:
            def request(self, *_args, **_kwargs):
                raise LoadError("connection ended before acceptance response")

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"jobs": []}
            with self.assertRaisesRegex(LoadError, "before acceptance"):
                _submit(Client(), "Demo", "extras.tag", [{"name": "Demo"}], "phase-1:tag",
                        ["tag:demo"], receipt, receipt_path, 1)
            durable = json.loads(receipt_path.read_text())["jobs"][0]
            self.assertEqual(durable["status"], "submitting")
            self.assertEqual(durable["canonical_keys"], ["tag:demo"])
            self.assertIn("payload_sha256", durable)
            self.assertNotIn("job_id", durable)
            with self.assertRaisesRegex(LoadError, "ambiguous.*Inspect or clean up"):
                _bound_job_id(durable)

    def test_disposable_submit_sends_and_records_exact_request_policy(self):
        settings = delivery_contract("disposable-baseline")["request_settings"]
        terminal = {"job_id": "job-1", "status": "completed", "data": {
            "rows_inserted": 1, "rows_updated": 0, "changelogs_created": 0, "errors": [],
            "post_hooks": {name: {"success": True} for name in settings["post_hooks"]}}}

        class Client:
            body = None

            def request(self, _path, **kwargs):
                self.body = kwargs["body"]
                return 202, {"job_id": "job-1"}

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = {"jobs": []}
            target = Client()
            with patch("estates.turbobulk._poll", return_value=terminal):
                _submit(target, "Demo", "extras.tag", [{"name": "Demo"}], "phase-1:tag",
                        ["tag:demo"], receipt, receipt_path, 1, request_settings=settings)
            body = target.body.decode(errors="ignore")
            for field, value in (("create_changelogs", "false"),
                                 ("dispatch_events", "false"),
                                 ("validation_mode", "full")):
                self.assertIn(f'name="{field}"\r\n\r\n{value}\r\n', body)
            for hook in settings["post_hooks"]:
                self.assertIn(f'name="post_hooks.{hook}"\r\n\r\ntrue\r\n', body)
            self.assertEqual(receipt["jobs"][0]["request_settings"], settings)
            self.assertEqual(receipt["jobs"][0]["mode"], "insert")

    def test_rest_completion_covers_new_scalar_and_many_to_many_refs(self):
        mac = {"key": "mac:1", "kind": "mac_address", "attrs": {}, "refs": {}}
        group = {"key": "contact-group:ops", "kind": "contact_group", "attrs": {}, "refs": {}}
        interface = {"key": "interface:1", "kind": "interface", "attrs": {"name": "eth0"},
                     "refs": {"primary_mac_address": mac["key"]}}
        contact = {"key": "contact:1", "kind": "contact", "attrs": {"name": "Ops"},
                   "refs": {"groups": [group["key"]]}}
        plan = {"schema_version": 1, "generator_version": "fixture",
                "objects": [mac, group, interface, contact]}
        objects = _index(plan)
        ids = {mac["key"]: 3, group["key"]: 4, interface["key"]: 5, contact["key"]: 6}

        class Client:
            def __init__(self):
                self.patches = []

            def all(self, path):
                if path == SPECS["interface"][1]:
                    return [{"id": 5, "primary_mac_address": None}]
                return [{"id": 6, "groups": []}]

            def request(self, path, **kwargs):
                self.patches.extend(json.loads(kwargs["body"]))
                return 200, []

        with tempfile.TemporaryDirectory() as temporary:
            receipt = {"rest_batches": []}
            self.assertEqual(_complete_rest(Client(), plan, objects, ids, receipt,
                                            Path(temporary) / "receipt.json"), 2)
        self.assertEqual(receipt["rest_batches"][0]["rows"], 1)
        self.assertEqual(receipt["rest_batches"][1]["rows"], 1)

    def test_generic_matchers_include_content_type_when_ids_collide(self):
        device = {"key": "device:1", "kind": "device", "attrs": {}, "refs": {}}
        vm = {"key": "vm:1", "kind": "virtual_machine", "attrs": {}, "refs": {}}
        contact = {"key": "contact:1", "kind": "contact", "attrs": {}, "refs": {}}
        role = {"key": "role:1", "kind": "contact_role", "attrs": {}, "refs": {}}
        assignment = {"key": "assignment:1", "kind": "contact_assignment", "attrs": {},
                      "refs": {"object": device["key"], "contact": contact["key"], "role": role["key"]}}
        objects = {obj["key"]: obj for obj in (device, vm, contact, role, assignment)}
        ids = {key: 1 for key in objects}
        row = {"object_id": 1, "object_type": "dcim.device",
               "contact": {"id": 1}, "role": {"id": 1}}
        self.assertTrue(_matches(assignment, row, ids, objects))
        row["object_type"] = "virtualization.virtualmachine"
        self.assertFalse(_matches(assignment, row, ids, objects))


if __name__ == "__main__":
    unittest.main()
