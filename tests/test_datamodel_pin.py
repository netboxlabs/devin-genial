"""Genial's hand-maintained NetBox facts against the pinned 4.7.1 data model.

``catalog/netbox-datamodel-4.7.1.json`` is derived from the NetBox Labs
``netbox-datamodel-extractor`` baseline at one commit (``datamodel.SOURCE_COMMIT``).
These tests compare it with the facts Genial reads from NetBox source by hand:
``naming.NAME_LIMITS``, the loader's model/endpoint table and reference names
behind docs/schema-map.md, the Diode matching identities, the save-derived
columns ``turbobulk._save_copies`` compiles and the loader's model defaults.

Two sets below are documented discrepancies, not passes.  Each is frozen so a
change in either direction fails here with its own message; resolving one is a
generator decision (CLAUDE.md: never change behaviour from a test), so the test
records the evidence and leaves the decision to a human.
"""

import copy
import json
import tomllib
import unittest
from pathlib import Path

from estates import datamodel, diode, naming, turbobulk
from estates.generate import generate

PROFILES = Path(__file__).resolve().parent.parent / "profiles"

# (a) Emitted kinds whose native ``name`` max_length is below the 100 that
# ``naming.NAME_LIMIT_DEFAULT`` checks them against, and that ``NAME_LIMITS``
# does not list.  ``Workspace.finish`` sweeps every object with a ``name``, so
# these are checked against a limit NetBox would not accept.  Latent today:
# ``test_every_profile_fits_the_baseline_limits`` proves no emitted name in any
# profile exceeds the real limit (longest device name 40, route target 16,
# custom field 32).  Adding them to NAME_LIMITS tightens a check only; it is
# left for a human because NAME_LIMITS is documented as the families naming.py
# composes from site display names, and these are identities and vendor port
# names, not authored labels.
KNOWN_NAME_LIMIT_GAP = {
    "console_port": 64, "console_server_port": 64, "cooling_intake": 64, "cooling_outflow": 64,
    "custom_field": 50, "device": 64, "device_bay": 64, "front_port": 64, "interface": 64,
    "inventory_item": 64, "module_bay": 64, "power_outlet": 64, "power_port": 64, "rear_port": 64,
    "route_target": 21, "virtual_device_context": 64, "virtual_disk": 64, "virtual_machine": 64,
    "vm_interface": 64,
}

# (b) Diode identities (docs/schema-map.md Identity column) that no unique
# constraint or affirmed natural key in the baseline proves unique.  NetBox
# keys none of these models: Aggregate, Prefix, IPAddress, IPRange, FHRPGroup,
# MACAddress, Contact, WirelessLAN and JournalEntry carry no unique constraint
# at all (uniqueness of addresses is per-VRF configuration, not a constraint),
# and VirtualChassis is unique only on ``master``, never on ``name``.  Genial's
# identities are therefore conventions the strict readback enforces by
# rejecting ambiguity (CLAUDE.md), not NetBox keys; the baseline cannot say
# whether Diode's own matcher treats them as such.  Left for a human.
KNOWN_IDENTITY_GAP = {
    "aggregate", "contact", "fhrp_group", "ip_address", "ip_range", "journal_entry",
    "mac_address", "prefix", "virtual_chassis", "wireless_lan",
}

# (c) Save-derived columns the baseline states NetBox fills, with the source it
# names.  Everything else the compiler writes is only ``db_only``/``read_only``
# there: the baseline says the API never takes it, not how NetBox computes it.
STATED_SAVE_SOURCES = {
    "device.airflow": "device_type.airflow",
    "device.cooling_method": "device_type.cooling_method",
    "device.location": "rack.location",
    "interface.rf_channel_frequency": None,
    "interface.rf_channel_width": None,
    "rack.form_factor": "rack_type.form_factor",
    "rack.width": "rack_type.width",
    "virtual_machine.site": None,
}

SAMPLE_YAML = """\
schema_version: 1.18.0
source:
  netbox_version: 4.7.1
  extractor_commit: abc
ratified:
  NetBoxThing.natural_key_fields:
    status: affirmed
    provenance: 'conformance_writes.py against a live NetBox 4.7.0: a second object
      colliding on this key was refused'
models:
  NetBoxThing:
    content_type: dcim.thing
    endpoint: dcim.things
    endpoint_operations:
    - list
    - create
    natural_key_fields:
    - name
    unique_constraints:
    - fields:
      - name
      case_insensitive: [name]
    - fields:
      - site
      - name
      condition: tenant__isnull=true
    validation_rules:
    - type: refuses_when
      condition:
        differ:
        - - a
          - b
        - - c
          - d
    fields:
      name:
        type: string
        max_length: 64
        description: This URL will be called using the HTTP method defined. Must be http:// or
          https://. Jinja2 template processing is supported.
        default: ''
      'null':
        type: string
      scope_type:
        type: content_type
        api_mapping:
          polymorphic: {type_field: scope_type, id_field: scope_id, object_field: scope}
    filters:
      name: {lookups: [empty, ic, n]}
      q: {lookups: []}
"""


class YamlReader(unittest.TestCase):
    def test_reads_the_extractor_shapes(self):
        doc = datamodel.load_yaml(SAMPLE_YAML)
        model = doc["models"]["NetBoxThing"]
        self.assertEqual(doc["schema_version"], "1.18.0")
        self.assertEqual(model["endpoint_operations"], ["list", "create"])
        self.assertEqual(model["unique_constraints"][0], {"fields": ["name"], "case_insensitive": ["name"]})
        self.assertEqual(model["unique_constraints"][1]["condition"], "tenant__isnull=true")
        self.assertEqual(model["validation_rules"][0]["condition"]["differ"], [["a", "b"], ["c", "d"]])
        self.assertEqual(model["fields"]["name"]["max_length"], 64)
        self.assertEqual(model["fields"]["name"]["default"], "")
        self.assertIn("https://. Jinja2", model["fields"]["name"]["description"])
        self.assertEqual(model["fields"]["null"]["type"], "string")
        self.assertEqual(model["fields"]["scope_type"]["api_mapping"]["polymorphic"]["id_field"], "scope_id")
        self.assertEqual(model["filters"]["name"], {"lookups": ["empty", "ic", "n"]})
        self.assertEqual(model["filters"]["q"], {"lookups": []})
        self.assertIn("colliding on this key was refused",
                      doc["ratified"]["NetBoxThing.natural_key_fields"]["provenance"])

    def test_refuses_what_it_cannot_read(self):
        with self.assertRaises(datamodel.BaselineError):
            datamodel.load_yaml("a:\n  - b\n c: d\n")
        with self.assertRaises(datamodel.BaselineError):
            datamodel.load_yaml("a: {b: [1, 2}\n")


class Derivation(unittest.TestCase):
    def test_derive_binds_version_commit_and_license(self):
        with self.assertRaises(datamodel.BaselineError):
            datamodel.derive(SAMPLE_YAML.replace("netbox_version: 4.7.1", "netbox_version: 4.7.2"))
        with self.assertRaises(datamodel.BaselineError) as caught:
            datamodel.derive(SAMPLE_YAML)  # one model; every emitted model must be present
        self.assertIn("baseline has no model for", str(caught.exception))

    def test_committed_extract_is_pinned(self):
        extract = datamodel.load()
        provenance = extract["provenance"]
        self.assertEqual(provenance["source_repo"], datamodel.SOURCE_REPO)
        self.assertEqual(provenance["source_commit"], datamodel.SOURCE_COMMIT)
        self.assertEqual(provenance["source_path"], datamodel.SOURCE_PATH)
        self.assertEqual(provenance["netbox_version"], "4.7.1")
        self.assertEqual(provenance["schema_version"], "1.18.0")
        self.assertRegex(provenance["baseline_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(provenance["extractor_commit"], r"^[0-9a-f]{40}$")
        self.assertIn("PolyForm Shield", provenance["license"])
        self.assertEqual(set(extract["models"]), set(datamodel.genial_models()),
                         "the extract must cover exactly the NetBox core models the loader emits")
        for content_type, model in extract["models"].items():
            self.assertEqual(datamodel.genial_models()[content_type], model["kind"])


class HandMaintainedFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extract = datamodel.load()

    def test_name_limits_match_the_baseline(self):
        mismatches, missing = datamodel.name_limit_findings(self.extract)
        self.assertEqual(mismatches, {}, "naming.NAME_LIMITS disagrees with the baseline")
        self.assertEqual(missing, KNOWN_NAME_LIMIT_GAP,
                         "emitted kinds with a native name limit below NAME_LIMIT_DEFAULT changed; "
                         "update NAME_LIMITS or the documented gap deliberately")

    def test_every_emitted_name_field_has_a_native_limit_at_most_default(self):
        # The default is only safe as an upper bound where the real limit is >= 100.
        for kind, model in sorted((m["kind"], m) for m in self.extract["models"].values()):
            limit = model["fields"].get("name", {}).get("max_length")
            if limit is None or kind in KNOWN_NAME_LIMIT_GAP:
                continue
            self.assertGreaterEqual(limit, naming.name_limit(kind), kind)

    def test_endpoints_and_models_match_the_baseline(self):
        self.assertEqual(datamodel.endpoint_findings(self.extract), {})

    def test_reference_names_are_baseline_columns(self):
        self.assertEqual(datamodel.reference_findings(self.extract), {})
        self.assertEqual(datamodel.resolve_ref(self.extract, "front_port", "rear_port"), ("rear_ports",))
        self.assertEqual(datamodel.resolve_ref(self.extract, "ip_address", "assigned_object"),
                         ("assigned_object_type", "assigned_object_id"))
        self.assertEqual(datamodel.resolve_ref(self.extract, "contact_assignment", "object"),
                         ("object_type", "object_id"))
        self.assertEqual(datamodel.resolve_ref(self.extract, "cable", "a"), ("a_terminations",))

    def test_identities_are_baseline_keys_except_the_documented_set(self):
        findings = datamodel.identity_findings(self.extract)
        self.assertEqual(set(findings), KNOWN_IDENTITY_GAP,
                         json.dumps(findings, indent=1, sort_keys=True))
        for kind in KNOWN_IDENTITY_GAP - {"virtual_chassis"}:
            self.assertEqual(findings[kind]["unique_constraints"], [], kind)
            self.assertIsNone(findings[kind]["natural_key_fields"], kind)
        self.assertEqual(findings["virtual_chassis"]["unique_constraints"], [["master"]])

    def test_save_derived_columns_exist_and_stated_sources_agree(self):
        report = datamodel.save_derived_findings(self.extract)
        self.assertEqual(report["missing"], [])
        self.assertEqual({column: next((fill.get("from") for fill in fills if fill.get("mechanism") == "save"), None)
                          for column, fills in report["stated"].items()}, STATED_SAVE_SOURCES)
        for column, spec in report["unstated"].items():
            self.assertTrue(spec["db_only"] or spec["read_only"] or column == "vlan_group.vid_ranges",
                            f"{column}: a writable column the compiler fills must be a stated fill or default")
        # The baseline states 15 rack columns copied from the rack type with
        # overwrites_sent_value; the compiler copies two (RACK_TYPE_COPIES) and
        # every profile's rack rows agree with their type on the rest
        # (test_every_profile_fits_the_baseline_limits).
        rack = self.extract["models"]["dcim.rack"]["fields"]
        copied = sorted(column for column, spec in rack.items()
                        if any(fill.get("from", "").startswith("rack_type.") for fill in spec.get("supplied_by_netbox") or []))
        self.assertEqual(len(copied), 15)
        self.assertTrue(set(turbobulk.RACK_TYPE_COPIES) <= set(copied))

    def test_loader_defaults_match_the_baseline(self):
        self.assertEqual(datamodel.default_findings(self.extract), {})

    def test_genial_emits_no_template_netbox_would_instantiate(self):
        self.assertEqual(datamodel.template_findings(self.extract), [])
        device = self.extract["models"]["dcim.device"]["on_create_creates"]
        self.assertIn("dcim.interfacetemplate", {entry["from"] for entry in device})


class EveryProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extract = datamodel.load()
        cls.plans = {}
        for path in sorted(PROFILES.glob("*.toml")):
            with path.open("rb") as handle:
                recipe = tomllib.load(handle)
            if recipe.get("profile") == "provider-backbone":
                recipe["discovery_lab"] = True
            cls.plans[path.name] = {obj["key"]: obj for obj in generate(recipe)["objects"]}

    def test_every_profile_fits_the_baseline_limits(self):
        for profile, objects in self.plans.items():
            with self.subTest(profile=profile):
                findings = datamodel.plan_findings(self.extract, objects)
                self.assertEqual(findings["names"], [])
                self.assertEqual(findings["save_copies"], [])

    def test_save_copy_check_covers_the_copied_rows(self):
        objects = self.plans["provider-backbone.toml"]
        racked = [obj for obj in objects.values() if obj["kind"] == "rack" and "rack_type" in obj["refs"]]
        devices = [obj for obj in objects.values() if obj["kind"] == "device" and "rack" in obj["refs"]]
        self.assertTrue(racked and devices)


class FailingMutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extract = datamodel.load()

    def test_a_changed_limit_fails(self):
        mismatches, _ = datamodel.name_limit_findings(self.extract, limits={"vlan": 100})
        self.assertEqual(mismatches, {"vlan": {"declared": 100, "baseline": 64}})
        mutated = copy.deepcopy(self.extract)
        mutated["models"]["ipam.vlan"]["fields"]["name"]["max_length"] = 100
        mismatches, missing = datamodel.name_limit_findings(mutated)
        self.assertEqual(mismatches, {"vlan": {"declared": 64, "baseline": 100}})
        mutated["models"]["dcim.site"]["fields"]["name"]["max_length"] = 50
        self.assertEqual(datamodel.name_limit_findings(mutated)[1].get("site"), 50)

    def test_a_changed_identity_fails(self):
        self.assertEqual(datamodel.identity_findings(self.extract, {"site": (("name",), ())}), {})
        self.assertIn("site", datamodel.identity_findings(self.extract, {"site": (("description",), ())}))
        mutated = copy.deepcopy(self.extract)
        mutated["models"]["dcim.site"]["unique_constraints"] = []
        mutated["models"]["dcim.site"]["natural_key_status"] = "deferred"
        self.assertIn("site", datamodel.identity_findings(mutated, {"site": (("name",), ())}))

    def test_a_changed_reference_endpoint_or_default_fails(self):
        self.assertEqual(datamodel.reference_findings(self.extract, {"device": {"nonexistent"}}),
                         {"device": ["nonexistent"]})
        self.assertIn("device", datamodel.endpoint_findings(
            self.extract, {"device": ("dcim.device", "/api/dcim/device/")}))
        mutated = copy.deepcopy(self.extract)
        mutated["models"]["dcim.rack"]["fields"]["starting_unit"]["default"] = 0
        self.assertEqual(set(datamodel.default_findings(mutated)), {"rack.starting_unit"})

    def test_a_template_kind_fails(self):
        mutated = copy.deepcopy(self.extract)
        mutated["models"]["dcim.interfacetemplate"] = {"kind": "interface_template", "fields": {},
                                                       "unique_constraints": [], "api_only_fields": {},
                                                       "on_create_creates": []}
        self.assertEqual(datamodel.template_findings(mutated),
                         ["device creates dcim.interface from emitted interface_template",
                          "module creates dcim.interface from emitted interface_template"])

    def test_a_plan_that_disagrees_with_the_baseline_fails(self):
        objects = {obj["key"]: obj for obj in generate({"namespace": "zeta", "name": "Acme Estate"})["objects"]}
        device = next(obj for obj in objects.values() if obj["kind"] == "device" and "rack" in obj["refs"])
        rack = objects[device["refs"]["rack"]]
        mutated = copy.deepcopy(objects)
        mutated[device["key"]]["attrs"]["name"] = "x" * 65
        mutated[rack["key"]]["attrs"]["width"] = 10 if rack["attrs"].get("width") != 10 else 23
        mutated[device["key"]]["refs"]["location"] = "somewhere-else"
        findings = datamodel.plan_findings(self.extract, mutated)
        self.assertEqual([(f["key"], f["field"], f["max_length"]) for f in findings["names"]],
                         [(device["key"], "name", 64)])
        self.assertEqual({(f["key"], f["field"], f["from"]) for f in findings["save_copies"]},
                         {(rack["key"], "width", "rack_type.width"), (device["key"], "location", "rack.location")})


if __name__ == "__main__":
    unittest.main()
