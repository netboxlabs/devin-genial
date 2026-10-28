"""The Designs catalogue sidecar: deterministic, schema-valid, faithful to the plan,
and every tamper fails naming the object."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from estates import designs
from estates.designs import (CatalogueError, build, check, create, dump_yaml, load_yaml,
                             validate_schema, verify)
from estates.generate import generate
from estates.model import canonical

_PLANS = {}


def plan(name="small"):
    if name not in _PLANS:
        recipe = {"small": {"stores": {"small": 2}}, "mixed": {"stores": {"medium": 1, "large": 1}}}[name]
        _PLANS[name] = generate({"profile": "retail-chain", "headquarters": 0,
                                 "distribution_centers": 0, **recipe})
    return _PLANS[name]


class Build(unittest.TestCase):
    def test_same_plan_builds_byte_identical_catalogues(self):
        first, second = create(plan()), create(plan())
        self.assertEqual(first["files"], second["files"])
        self.assertEqual(first["site"], "site/st-s0001")
        self.assertEqual(first["format"], "small")
        self.assertEqual(sorted(first["files"]), [
            "README.md", "catalogue/devices/apc-ap9572.yaml",
            "catalogue/devices/arista-dcs-7050sx3-48c8-f.yaml",
            "catalogue/devices/cisco-c9200l-24p-4x.yaml", "catalogue/devices/fortinet-fg-100f.yaml",
            "catalogue/devices/generic-wired-endpoint-1gbe.yaml",
            "catalogue/devices/opengear-cm8116.yaml", "catalogue/retail/small_store.yaml",
            "deploy-vars.json", "manifest.json"])

    def test_selects_the_smallest_format_in_allocation_order(self):
        result = create(plan("mixed"))
        self.assertEqual((result["site"], result["format"]), ("site/st-m0001", "medium"))
        self.assertIn("catalogue/retail/medium_store.yaml", result["files"])
        self.assertEqual(result["same_format"], ["site/st-m0001"])

    def test_refuses_a_plan_without_stores(self):
        other = deepcopy(plan())
        other["recipe"]["profile"] = "regional-bank"
        with self.assertRaisesRegex(CatalogueError, "reads a retail-chain plan"):
            create(other)

    def test_manifest_records_what_is_left_out_and_why(self):
        manifest = create(plan())["manifest"]
        self.assertEqual(manifest["declared"]["Device"], 20)
        self.assertEqual(manifest["declared"]["Site"], 1)
        omitted = {(entry["infra_type"], entry["count"]): entry["reason"] for entry in manifest["omitted"]}
        self.assertIn("C9120AXI-E.yaml", omitted[("Device", 2)])
        self.assertIn("eth0/wlan0/wlan1", omitted[("Device", 2)])
        self.assertTrue(any(kind == "JournalEntry" for kind, _ in omitted))
        self.assertTrue(any(kind == "MACAddress" for kind, _ in omitted))
        self.assertTrue(any("#1004" in reason for reason in omitted.values()))
        self.assertTrue(any("PSU module" in reason for reason in omitted.values()))
        fields = {(entry["infra_type"], entry["field"]) for entry in manifest["omitted_fields"]}
        self.assertIn(("Site", "custom_fields"), fields)
        self.assertIn(("Device", "serial"), fields)
        self.assertEqual(manifest["store"]["same_format_sites"], ["site/st-s0001", "site/st-s0002"])
        self.assertNotIn("Catalyst 9120", json.dumps(manifest["device_designs"]))
        for entry in manifest["device_designs"].values():
            self.assertEqual(entry["explicit"], entry["dtl"] is None)
        self.assertIn("what Designs plans or applies is not verified", manifest["verification"])

    def test_deploy_vars_are_the_selected_stores_values(self):
        result = create(plan())
        variables = json.loads(result["files"]["deploy-vars.json"])
        objects = {obj["key"]: obj for obj in plan()["objects"]}
        site = objects["site/st-s0001"]
        self.assertEqual(variables["site_slug"], site["attrs"]["slug"])
        self.assertEqual(variables["site_name"], site["attrs"]["name"])
        self.assertEqual(variables["hostname"], "sts0001")
        self.assertEqual(variables["site_block"], objects["prefix/st-s0001/reservation"]["attrs"]["prefix"])
        self.assertEqual(variables["backoffice_net"] + ".0/24", objects["prefix/st-s0001/backoffice"]["attrs"]["prefix"])
        store = load_yaml(result["files"]["catalogue/retail/small_store.yaml"])
        required = {name for name, value in store["variables"].items() if value == "$USER"}
        self.assertEqual(required, variables.keys() - {"dns_domain"})

    def test_device_designs_pin_the_catalog_commit_and_declare_no_template_ports(self):
        files = create(plan())["files"]
        pinned = load_yaml(files["catalogue/devices/cisco-c9200l-24p-4x.yaml"])
        device = pinned["infra"][0]
        self.assertEqual(device["device_type"],
                         "dtl:device-types/Cisco/C9200L-24P-4X.yaml@72cc49fbb445f1e2f310d3b8dfef55e12d0b7138")
        self.assertNotIn("interfaces", device)
        self.assertEqual([m["module_type"] for m in device["modules"]],
                         ["dtl:module-types/Cisco/PWR-C5-600WAC.yaml@72cc49fbb445f1e2f310d3b8dfef55e12d0b7138"] * 2)
        explicit = load_yaml(files["catalogue/devices/generic-wired-endpoint-1gbe.yaml"])["infra"][0]
        self.assertEqual(explicit["device_type"]["infra_type"], "DeviceType")
        self.assertEqual([i["name"] for i in explicit["interfaces"]], ["eth0"])
        self.assertTrue(explicit["interfaces"][0]["ip_addresses"][0]["primary"])


class Verify(unittest.TestCase):
    def setUp(self):
        self.result = create(plan())
        self.files = dict(self.result["files"])

    def test_catalogue_verifies_for_every_same_format_store(self):
        evidence = verify(self.files, plan())
        self.assertEqual(evidence["stores_verified"], ["site/st-s0001", "site/st-s0002"])
        self.assertEqual(evidence["declared"], sum(self.result["manifest"]["declared"].values()))

    def test_every_design_passes_the_published_schema(self):
        schema = designs._schema()
        for path, text in self.files.items():
            if path.endswith(".yaml"):
                self.assertEqual(validate_schema(load_yaml(text), schema), [], path)
        bad = load_yaml(self.files["catalogue/retail/small_store.yaml"])
        bad["loops"] = 3
        self.assertTrue(any("unexpected key 'loops'" in e for e in validate_schema(bad, schema)))
        bad = load_yaml(self.files["catalogue/retail/small_store.yaml"])
        del bad["infra"][0]["infra_type"]
        self.assertTrue(validate_schema(bad, schema))

    def test_yaml_writer_and_reader_round_trip(self):
        for path, text in self.files.items():
            if path.endswith(".yaml"):
                document = load_yaml(text)
                self.assertEqual(dump_yaml(document), text, path)
                self.assertEqual(load_yaml(dump_yaml(document)), document, path)
        self.assertEqual(load_yaml("a: plain text\nb: 'it''s'\nc:\n  - 1\n  - x\nd: {}\n"),
                         {"a": "plain text", "b": "it's", "c": [1, "x"], "d": {}})

    def _mutated_store(self, mutate):
        store = load_yaml(self.files["catalogue/retail/small_store.yaml"])
        mutate(store)
        self.files["catalogue/retail/small_store.yaml"] = dump_yaml(store)

    def test_editing_a_field_fails_naming_the_object(self):
        def mutate(store):
            store["infra"][0]["racks"][0]["u_height"] = 42
        self._mutated_store(mutate)
        with self.assertRaisesRegex(CatalogueError, r"Rack .*'u_height' is 42 in the design but 24 in the plan"):
            verify(self.files, plan())

    def test_editing_an_included_devices_variable_fails_naming_the_device(self):
        def mutate(store):
            store["infra"][0]["racks"][0]["devices"][0]["template_vars"]["position"] = 9
        self._mutated_store(mutate)
        with self.assertRaisesRegex(CatalogueError, r"Device .*'position' is 9 in the design but 5 in the plan"):
            verify(self.files, plan())

    def test_dropping_an_object_fails_naming_it(self):
        def mutate(store):
            group = store["infra"][0]["vlan_groups"][0]
            group["vlans"] = [vlan for vlan in group["vlans"] if vlan["name"] != "Guest"]
        self._mutated_store(mutate)
        with self.assertRaisesRegex(CatalogueError, r"vlan/st-s0001/guest \(vlan\) is neither declared nor listed as omitted"):
            verify(self.files, plan())

    def test_declaring_a_record_the_plan_lacks_fails(self):
        def mutate(store):
            store["infra"][0]["locations"].append({"infra_type": "Location", "name": "Annex",
                                                   "slug": "annex", "status": "active",
                                                   "site": "{{ parent }}"})
        self._mutated_store(mutate)
        with self.assertRaisesRegex(CatalogueError, r"Location .*Annex.*matches 0 plan objects"):
            verify(self.files, plan())

    def test_changing_a_variable_value_fails_naming_the_object(self):
        variables = json.loads(self.files["deploy-vars.json"])
        variables["hostname"] = "sts0009"
        self.files["deploy-vars.json"] = canonical(variables).decode() + "\n"
        with self.assertRaisesRegex(CatalogueError, r"(Device|Interface) .*sts0009"):
            verify(self.files, plan())

    def test_wrong_dtl_pin_or_template_port_fails(self):
        self.files["catalogue/devices/fortinet-fg-100f.yaml"] = self.files[
            "catalogue/devices/fortinet-fg-100f.yaml"].replace("@72cc49fb", "@deadbeef")
        with self.assertRaisesRegex(CatalogueError, "not a catalog source"):
            verify(self.files, plan())
        self.setUp()
        self.files["catalogue/retail/small_store.yaml"] = self.files[
            "catalogue/retail/small_store.yaml"].replace("'Interface', 'wan1'", "'Interface', 'wan9'", 1)
        with self.assertRaisesRegex(CatalogueError, "has no Interface 'wan9' in the plan"):
            verify(self.files, plan())

    def test_binding_and_regeneration_are_enforced(self):
        manifest = json.loads(self.files["manifest.json"])
        manifest["plan_sha256"] = "0" * 64
        self.files["manifest.json"] = canonical(manifest).decode() + "\n"
        with self.assertRaisesRegex(CatalogueError, "not bound to this canonical plan"):
            verify(self.files, plan())
        self.setUp()
        self.files["README.md"] += "edited\n"
        with self.assertRaisesRegex(CatalogueError, "README.md differs from its regeneration"):
            verify(self.files, plan())
        self.setUp()
        del self.files["catalogue/devices/apc-ap9572.yaml"]
        with self.assertRaisesRegex(CatalogueError, "includes unknown 'nbd:devices/apc-ap9572'"):
            verify(self.files, plan())


class Cli(unittest.TestCase):
    def run_main(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = designs.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def test_build_then_check_and_a_tampered_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan()) + b"\n")
            code, out, err = self.run_main("build", root / "plan.json", "--out", root / "designs")
            self.assertEqual((code, err), (0, ""), out)
            self.assertIn("7 designs", out)
            self.assertEqual(json.loads((root / "designs/checks.json").read_text())["status"], "passed")
            code, out, _ = self.run_main("check", root / "designs", "--plan", root / "plan.json")
            self.assertEqual(code, 0, out)
            self.assertIn("catalogue verified", out)
            result = check(root / "designs", root / "plan.json")
            self.assertEqual(result["stores_verified"], ["site/st-s0001", "site/st-s0002"])
            store = root / "designs/catalogue/retail/small_store.yaml"
            store.write_text(store.read_text().replace('vid: 130', 'vid: 131', 1))
            code, _, err = self.run_main("check", root / "designs", "--plan", root / "plan.json")
            self.assertEqual(code, 2)
            self.assertIn("VLAN", err)
            self.assertIn("131", err)
            with self.assertRaises(CatalogueError):
                build(root / "plan.json", root / "designs")
