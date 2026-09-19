"""The `[hardware]` vendor lever: resolution, freezing and a whole fit matrix.

Every profile is generated at every selectable combination and put through the
same offline checks the default estates use. The point is that choosing a line
changes which real hardware is installed and nothing else: PSU fit, PoE budgets,
installed optics and port arithmetic must all still hold.
"""

from copy import deepcopy
import itertools
import unittest

from estates.generate import generate
from estates.model import (DesignError, World, hardware_catalog, resolve_hardware,
                           resolve_recipe, selected_alias)
from estates.validate import validate


# Small but complete estates: one of everything each profile insists on.
SMALL = {
    "regional-bank": dict(namespace="tinybank", name="Tiny Bank", address_pool="10.0.0.0/12",
                          headquarters=0, branches={"small": 1}),
    "enterprise-data-center": dict(profile="enterprise-data-center", namespace="tinyent",
                                   name="Tiny Enterprise", address_pool="10.64.0.0/12",
                                   data_centers=1, wan_peak_mbps=100,
                                   workloads=[dict(key="api", groups=1, replicas=2,
                                                   failure_domain="host")]),
    "school-district": dict(profile="school-district", namespace="tinysch", name="Tiny District",
                            address_pool="10.128.0.0/12",
                            schools=[dict(key="a", classrooms=1, administrative_staff=1,
                                          lab_seats=0, wan_peak_mbps=50)]),
    "hospital-clinics": dict(profile="hospital-clinics", namespace="tinyhos", name="Tiny Health",
                             address_pool="10.144.0.0/12"),
    "provider-backbone": dict(profile="provider-backbone", namespace="tinyisp", name="Tiny Fiber",
                              address_pool="10.0.0.0/8"),
    "retail-chain": dict(profile="retail-chain", namespace="tinyret", name="Tiny Retail",
                         address_pool="10.160.0.0/12", stores={"small": 1},
                         headquarters=0, distribution_centers=0),
    "university-campus": dict(profile="university-campus", namespace="tinyuni",
                              name="Tiny University", address_pool="10.176.0.0/12",
                              buildings=[dict(key="science", classrooms=1, lab_seats=0, offices=1)],
                              residences=[dict(key="aspen", rooms=10)],
                              library=dict(reading_seats=24, aps=1)),
    "msp": dict(profile="msp", namespace="tinymsp", name="Tiny Managed Networks",
                address_pool="10.192.0.0/12",
                customers=[dict(key="one", offices=1, staff=4)]),
    "manufacturing": dict(profile="manufacturing", namespace="tinymfg", name="Tiny Manufacturing",
                          address_pool="10.208.0.0/12",
                          plants=[dict(key="one", production_lines=1, warehouse_docks=1,
                                       office_staff=4)]),
}
# The models this repository shipped before the lever existed. A default recipe
# must still build exactly these.
BASELINE = {"access": ("Cisco", "Catalyst 9200L-24P-4X"),
            "leaf": ("Arista", "DCS-7050SX3-48C8-F"),
            "ap": ("Devin Reference Designs", "Reference PoE access point")}
VARIANT = {"access": ("Juniper", "EX3400-24P"),
           "leaf": ("Juniper", "QFX5120-48Y-AFO2"),
           "ap": ("HPE", "Aruba AP-505")}
CHOICE = {"access": "juniper", "leaf": "juniper", "ap": "aruba"}


def combinations():
    """Default, each family alone, and every mixture up to all three."""
    families = sorted(CHOICE)
    return [dict(zip(picked, (CHOICE[f] for f in picked)))
            for n in range(len(families) + 1)
            for picked in itertools.combinations(families, n)]


def build(profile, hardware):
    raw = deepcopy(SMALL[profile])
    if hardware:
        raw["hardware"] = dict(hardware)
    return generate(resolve_recipe(raw))


def installed(plan):
    """Manufacturer/model of every emitted device type, keyed by catalog alias."""
    objects = {o["key"]: o for o in plan["objects"]}
    return {key.removeprefix("hardware/"):
            (objects[obj["refs"]["manufacturer"]]["attrs"]["name"], obj["attrs"]["model"])
            for key, obj in objects.items() if obj["kind"] == "device_type"}


class ResolutionTests(unittest.TestCase):
    def test_absent_key_resolves_to_the_shipped_default_models(self):
        catalog = hardware_catalog()
        resolved = resolve_hardware({}, catalog)
        self.assertEqual(resolved, {"access": "cisco", "ap": "reference", "leaf": "arista"})
        for family, identity in BASELINE.items():
            alias = catalog["hardware_lines"][family]["lines"][resolved[family]]
            model = catalog["models"][alias]
            self.assertEqual((model["manufacturer"], model["model"]), identity)

    def test_explicit_default_is_identical_to_omitting_the_key(self):
        self.assertEqual(resolve_hardware({}), resolve_hardware({"access": "cisco"}))
        self.assertEqual(resolve_recipe(dict(SMALL["regional-bank"]))["hardware"],
                         resolve_recipe(dict(SMALL["regional-bank"],
                                             hardware={"leaf": "arista"}))["hardware"])

    def test_unknown_family_is_rejected_and_names_the_real_families(self):
        with self.assertRaises(DesignError) as caught:
            resolve_hardware({"spine": "juniper"})
        message = str(caught.exception)
        self.assertIn("hardware.spine", message)
        for family in ("access", "ap", "leaf"):
            self.assertIn(family, message)

    def test_unknown_vendor_is_rejected_and_lists_the_real_choices(self):
        with self.assertRaises(DesignError) as caught:
            resolve_hardware({"access": "brocade"})
        message = str(caught.exception)
        self.assertIn("'brocade'", message)
        self.assertIn("cisco (Cisco Catalyst 9200L-24P-4X)", message)
        self.assertIn("juniper (Juniper EX3400-24P)", message)

    def test_a_vendor_from_another_family_is_rejected(self):
        with self.assertRaises(DesignError):
            resolve_hardware({"ap": "juniper"})
        with self.assertRaises(DesignError):
            resolve_hardware({"leaf": "aruba"})

    def test_non_table_values_are_rejected(self):
        for value in ("juniper", ["juniper"], 3, None):
            with self.assertRaises(DesignError):
                resolve_hardware(value)

    def test_every_profile_accepts_and_keeps_the_key(self):
        for profile, raw in SMALL.items():
            with self.subTest(profile=profile):
                recipe = resolve_recipe(dict(raw, hardware={"access": "juniper"}))
                self.assertEqual(recipe["hardware"]["access"], "juniper")
                self.assertEqual(recipe["hardware"]["leaf"], "arista")
                self.assertEqual(selected_alias(recipe, "access"), "access-juniper")
                self.assertEqual(selected_alias(recipe, "leaf"), "leaf")

    def test_plain_aliases_pass_through_resolution(self):
        recipe = resolve_recipe(dict(SMALL["regional-bank"], hardware=dict(CHOICE)))
        for alias in ("core", "edge", "server", "pdu", "endpoint", "inherited-access"):
            self.assertEqual(selected_alias(recipe, alias), alias)

    def test_selection_is_frozen_under_growth(self):
        previous = build("regional-bank", CHOICE)
        grown = dict(SMALL["regional-bank"], branches={"small": 2}, hardware=dict(CHOICE))
        World(grown, previous=previous)  # same selection: ordinary growth
        for changed in ({}, {"access": "cisco", "leaf": "juniper", "ap": "aruba"}):
            with self.subTest(hardware=changed):
                with self.assertRaises(DesignError) as caught:
                    World(dict(grown, hardware=changed), previous=previous)
                self.assertIn("hardware", str(caught.exception))


class CatalogFitTests(unittest.TestCase):
    """Each alternate line must meet or beat the model it substitutes."""

    def setUp(self):
        self.catalog = hardware_catalog()
        self.models = self.catalog["models"]

    def alias(self, family, vendor):
        return self.catalog["hardware_lines"][family]["lines"][vendor]

    def test_every_declared_line_names_a_real_catalog_model(self):
        for family, spec in self.catalog["hardware_lines"].items():
            self.assertIn(spec["default"], spec["lines"], family)
            for vendor, alias in spec["lines"].items():
                self.assertIn(alias, self.models, (family, vendor))
                model = self.models[alias]
                for field in ("manufacturer", "model", "slug", "source_ids", "description"):
                    self.assertTrue(model.get(field), (alias, field))

    def test_access_line_matches_or_beats_the_default(self):
        base, variant = (self.models[self.alias("access", v)] for v in ("cisco", "juniper"))
        self.assertEqual((variant["manufacturer"], variant["model"]), VARIANT["access"])
        for field in ("access_ports", "uplink_ports", "stack_ports", "power_ports",
                      "configured_modules", "console_ports"):
            self.assertGreaterEqual(len(variant[field]), len(base[field]), field)
        base_poe, variant_poe = base["poe_pse"], variant["poe_pse"]
        self.assertEqual(variant_poe["type"], base_poe["type"])
        self.assertGreaterEqual(variant_poe["per_port_max_mw"], base_poe["per_port_max_mw"])
        self.assertEqual(variant_poe["planning_supply_losses"], base_poe["planning_supply_losses"])

        def deliverable(model, supplies):
            # What the switch can actually hand out: the supply budget capped by
            # the per-port limit across its own access ports. The EX3400-24P's raw
            # two-supply figure is 720 W against the C9200L's 740 W, but both are
            # capped at 24 x 30 W, so no resolver bound moves.
            policy = model["poe_pse"]
            return min(policy["budget_by_active_supplies_mw"][str(supplies)],
                       len(model["access_ports"]) * policy["per_port_max_mw"])

        for supplies in (0, 1, 2):
            self.assertGreaterEqual(deliverable(variant, supplies), deliverable(base, supplies),
                                    f"{supplies} supplies")
        # Every access port is 1G copper PoE+, as the shared PoE checks require.
        for model in (base, variant):
            for name in model["access_ports"]:
                port = next(p for p in model["interfaces"] if p["name"] == name)
                self.assertEqual((port["type"], port["poe_mode"], port["poe_type"]),
                                 ("1000base-t", "pse", "type2-ieee802.3at"))

        def rate(model, name):
            port = next(p for p in model["interfaces"] if p["name"] == name)
            return port.get("speed", {"10gbase-x-sfpp": 10000000,
                                      "25gbase-x-sfp28": 25000000}[port["type"]])

        for index, name in enumerate(base["uplink_ports"]):
            self.assertGreaterEqual(rate(variant, variant["uplink_ports"][index]),
                                    rate(base, name), index)
        for model in (base, variant):
            # The DC service stack cables exactly two stacking-medium ports.
            self.assertEqual(len(model["stack_ports"]), 2)
            for name in model["stack_ports"]:
                port_type = next(p for p in model["interfaces"] if p["name"] == name)["type"]
                self.assertTrue("stack" in port_type or port_type == "juniper-vcp", name)
        # One rj-45 console (the console builder cables only rj-45), the same
        # management-port count (each consumes a management block slot) and the
        # same rack height, so no room or console ledger moves between lines.
        self.assertEqual(len([p for p in variant["console_ports"] if p["type"] == "rj-45"]),
                         len([p for p in base["console_ports"] if p["type"] == "rj-45"]))
        self.assertEqual(len([p for p in variant["interfaces"] if p.get("mgmt_only")]),
                         len([p for p in base["interfaces"] if p.get("mgmt_only")]))
        self.assertEqual(variant["u_height"], base["u_height"])

    def test_leaf_line_matches_or_beats_the_default(self):
        base, variant = (self.models[self.alias("leaf", v)] for v in ("arista", "juniper"))
        self.assertEqual((variant["manufacturer"], variant["model"]), VARIANT["leaf"])
        for field in ("fabric_ports", "uplink_ports", "power_ports", "configured_modules"):
            self.assertGreaterEqual(len(variant[field]), len(base[field]), field)

        def rate(model, name):
            port = next(p for p in model["interfaces"] if p["name"] == name)
            return port.get("speed", {"10gbase-x-sfpp": 10000000,
                                      "25gbase-x-sfp28": 25000000,
                                      "100gbase-x-qsfp28": 100000000}[port["type"]])

        for field in ("fabric_ports", "uplink_ports"):
            for index, name in enumerate(base[field]):
                self.assertEqual(rate(variant, variant[field][index]), rate(base, name),
                                 (field, index))
        # Exactly one management port, so the shared management builder reserves
        # one block slot per chassis on either line.
        for model in (base, variant):
            self.assertEqual(len([p for p in model["interfaces"] if p.get("mgmt_only")]), 1)

    def test_ap_line_matches_the_default_envelope(self):
        base, variant = (self.models[self.alias("ap", v)] for v in ("reference", "aruba"))
        self.assertEqual((variant["manufacturer"], variant["model"]), VARIANT["ap"])
        self.assertEqual(variant["poe_pd"]["required_type"], base["poe_pd"]["required_type"])
        # A larger reservation would repack every room's access-port ledger.
        self.assertEqual(variant["poe_pd"]["pse_reservation_mw"],
                         base["poe_pd"]["pse_reservation_mw"])
        self.assertLessEqual(variant["poe_pd"]["max_input_mw"], base["poe_pd"]["max_input_mw"])
        self.assertEqual(len(variant["radio_bands"]), len(base["radio_bands"]))
        for model in (base, variant):
            powered = next(p for p in model["interfaces"] if p["name"] == model["poe_pd"]["interface"])
            self.assertEqual(powered["type"], "1000base-t")
            self.assertEqual([p["name"] for p in model["interfaces"] if p["type"].startswith("ieee802.11")],
                             sorted(model["radio_bands"]))

    def test_tampered_hardware_declaration_fails_validation(self):
        # selected_alias silently falls back to the default line, so a
        # hand-edited plan must be caught by the independent check instead.
        plan = generate(deepcopy(SMALL["regional-bank"]))
        self.assertEqual(validate(plan), [])
        tampered = deepcopy(plan)
        tampered["recipe"]["hardware"]["access"] = "brocade"
        self.assertTrue(any(f["code"] == "hardware-declaration"
                            for f in validate(tampered)), validate(tampered)[:3])

    def test_foreign_cage_bay_types_stay_out_of_default_estates(self):
        # The QFX's SFP28 cages must not publish a Juniper SFP28 bay type into
        # an estate whose device types carry no SFP28 cage at all.
        default = generate(deepcopy(SMALL["regional-bank"]))
        keys = {o["key"] for o in default["objects"] if o["kind"] == "module_bay_type"}
        self.assertNotIn("optics-bay-type/Juniper/sfp28", keys)
        variant = generate(deepcopy(SMALL["regional-bank"]) | {"hardware": dict(CHOICE)})
        keys = {o["key"] for o in variant["objects"] if o["kind"] == "module_bay_type"}
        self.assertIn("optics-bay-type/Juniper/sfp28", keys)

    def test_ap_lines_follow_the_portable_endpoint_convention(self):
        # Shared builders and checkers address every AP line as eth0/wlan0/wlan1
        # (a declared normalization of vendor labels, never a vendor claim).
        # This pins that contract: a future line could satisfy the envelope test
        # and still break the ~15 call sites that name these ports literally.
        for vendor, alias in self.catalog["hardware_lines"]["ap"]["lines"].items():
            model = self.models[alias]
            self.assertEqual(model["poe_pd"]["interface"], "eth0", alias)
            self.assertEqual(set(model["radio_bands"]), {"wlan0", "wlan1"}, alias)
            self.assertIn("eth0", {p["name"] for p in model["interfaces"]}, alias)

    def test_every_variant_optical_cage_has_a_reviewed_part(self):
        cages = {"1000base-x-sfp", "10gbase-x-sfpp", "25gbase-x-sfp28", "100gbase-x-qsfp28"}
        supported = {(alias, name) for part in self.catalog["optics"]["parts"].values()
                     for alias, names in part["compatible_interfaces"].items() for name in names}
        for family in ("access", "leaf"):
            for vendor, alias in self.catalog["hardware_lines"][family]["lines"].items():
                for port in self.models[alias]["interfaces"]:
                    if port["type"] in cages:
                        self.assertIn((alias, port["name"]), supported, (alias, port["name"]))


class FitMatrixTests(unittest.TestCase):
    """Every profile, every selectable combination, the full offline checks."""

    def test_matrix(self):
        for profile in SMALL:
            for hardware in combinations():
                label = ",".join(f"{k}={v}" for k, v in sorted(hardware.items())) or "default"
                with self.subTest(profile=profile, hardware=label):
                    plan = build(profile, hardware)
                    self.assertEqual(validate(plan), [])
                    emitted = installed(plan)
                    for family, vendor in hardware.items():
                        alias = selected_alias(plan["recipe"], family)
                        if alias in emitted:
                            self.assertEqual(emitted[alias], VARIANT[family])
                            self.assertNotIn(
                                BASELINE[family][1], {model for _, model in emitted.values()})

    def test_default_matrix_cell_still_builds_the_shipped_models(self):
        for profile in SMALL:
            with self.subTest(profile=profile):
                emitted = installed(build(profile, {}))
                for family, identity in BASELINE.items():
                    alias = selected_alias({}, family)
                    if alias in emitted:
                        self.assertEqual(emitted[alias], identity)

    def test_selecting_a_line_swaps_the_installed_hardware(self):
        before, after = build("school-district", {}), build("school-district", CHOICE)
        vendors = {model["attrs"]["model"] for model in before["objects"]
                   if model["kind"] == "device_type"}
        swapped = {model["attrs"]["model"] for model in after["objects"]
                   if model["kind"] == "device_type"}
        for family in CHOICE:
            self.assertIn(BASELINE[family][1], vendors)
            self.assertNotIn(BASELINE[family][1], swapped)
            self.assertIn(VARIANT[family][1], swapped)

    def test_a_two_point_four_gigahertz_radio_rejects_a_five_gigahertz_channel(self):
        plan = build("school-district", {"ap": "aruba"})
        self.assertEqual(validate(plan), [])
        radios = [obj for obj in plan["objects"] if obj["kind"] == "interface"
                  and obj["attrs"].get("name") == "wlan1" and obj["attrs"].get("rf_channel")]
        self.assertTrue(radios)
        for radio in radios:
            self.assertTrue(radio["attrs"]["rf_channel"].startswith("2.4g-"))
        radios[0]["attrs"]["rf_channel"] = "5g-36-5180-20"
        self.assertIn("wireless-radio-band", {f["code"] for f in validate(plan)})


if __name__ == "__main__":
    unittest.main()
