"""Provider counterexamples exercise actual ownership and physical paths."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import tomllib
import unittest

from estates.generate import generate
from estates.model import DesignError
from estates.validate import validate
from estates.validate_provider import _loads


ROOT = Path(__file__).parents[1]


LIFECYCLE_RECIPE = {"profile": "provider-backbone", "customers": [
    dict(key="harbor-logistics", hub_pop="chicago-west", sites=[
        dict(pop="chicago-west"), dict(pop="detroit-south", status="planned"),
        dict(pop="cleveland-east", status="decommissioning")]),
    dict(key="maple-schools", hub_pop="detroit-south", status="planned", lan_endpoints=0,
         sites=[dict(pop="detroit-south"), dict(pop="cleveland-east")])]}


class ProviderLifecycleTests(unittest.TestCase):
    """Onboarding, provisioning and decommissioning premises are honest, never healthy capacity."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(LIFECYCLE_RECIPE)

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {o["key"]: o for o in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def test_each_premises_lifecycle_reaches_every_record_it_owns(self):
        self.assertEqual(validate(self.plan), [])
        o = self.objects
        for sid, site, device, circuit, ip, bgp in (
                ("ce-maple-schools-detroit-south-001", "planned", "planned", "planned", "reserved", "planned"),
                ("ce-harbor-logistics-detroit-south-001", "staging", "staged", "provisioning", "reserved", "planned"),
                ("ce-harbor-logistics-cleveland-east-001", "decommissioning", "decommissioning", "deprovisioning", "deprecated", "offline"),
                ("ce-harbor-logistics-chicago-west-001", "active", "active", "active", "active", "active")):
            with self.subTest(sid=sid):
                self.assertEqual(o[f"site/{sid}"]["attrs"]["status"], site)
                self.assertEqual(o[f"device/{sid}/edge-01"]["attrs"]["status"], device)
                self.assertEqual(o[f"circuit/customer/{sid}"]["attrs"]["status"], circuit)
                self.assertEqual(o[f"ip/device/{sid}/edge-01/if/wan1"]["attrs"]["status"], ip)
                self.assertEqual(o[f"bgp-session/customer/{sid}"]["attrs"]["status"], bgp)
                self.assertEqual("install_date" in o[f"circuit/customer/{sid}"]["attrs"], circuit in ("active", "deprovisioning"))
        self.assertEqual(o["virtual-circuit/customer/maple-schools"]["attrs"]["status"], "planned")
        # Nothing records an installation that has not happened.
        self.assertNotIn("journal/device/ce-maple-schools-detroit-south-001/edge-01/equipment-record", o)
        self.assertFalse([k for k in o if k.startswith("journal/circuit/customer/ce-maple-schools-")])
        # Only in-service premises offer traffic to the capacity model.
        capacity = next(c for c in self.plan["contracts"] if c.get("provider"))["provider"]["capacity"]
        self.assertFalse(any(capacity["normal_load_mbps"].values()))

    def test_status_counterexamples_are_reported(self):
        sid = "ce-maple-schools-detroit-south-001"
        cases = ((f"circuit/customer/{sid}", "install_date", "2026-01-01", "provider-timeline"),
                 (f"site/{sid}", "status", "active", "provider-site-context"),
                 (f"device/ce-harbor-logistics-cleveland-east-001/edge-01", "status", "active", "provider-device-inventory"),
                 (f"ip/device/{sid}/edge-01/if/wan1", "status", "active", "provider-routed-address"),
                 (f"bgp-session/customer/{sid}", "status", "active", "provider-bgp-session"),
                 (f"circuit/customer/ce-harbor-logistics-detroit-south-001", "status", "active", "provider-circuit-path"),
                 ("virtual-circuit/customer/maple-schools", "status", "active", "provider-customer-service"))
        for key, field, value, code in cases:
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["attrs"][field] = value
                self.assertIn(code, self.codes())

    def test_recipe_lifecycle_bounds(self):
        bad = deepcopy(LIFECYCLE_RECIPE)
        bad["customers"][0]["sites"][0]["status"] = "planned"  # the hub of an active customer
        with self.assertRaisesRegex(DesignError, "hub_pop entry of an active customer"):
            generate(bad)
        bad = deepcopy(LIFECYCLE_RECIPE)
        bad["customers"][1]["sites"][0]["status"] = "active"
        with self.assertRaisesRegex(DesignError, "every premises of a planned customer is planned"):
            generate(bad)
        bad = deepcopy(LIFECYCLE_RECIPE)
        bad["customers"][0]["sites"][1]["status"] = "retired"
        with self.assertRaisesRegex(DesignError, "status must be one of"):
            generate(bad)

    def test_lifecycle_moves_forward_as_growth(self):
        grown = deepcopy(self.plan["recipe"])
        grown["customers"][1]["status"] = "active"
        for entry in grown["customers"][1]["sites"]:
            entry.pop("status", None)
        next(e for e in grown["customers"][0]["sites"] if e["pop"] == "detroit-south").pop("status")
        plan = generate(grown, previous=self.plan)
        self.assertEqual(validate(plan), [])
        after = {o["key"]: o for o in plan["objects"]}
        self.assertEqual(after["circuit/customer/ce-maple-schools-detroit-south-001"]["attrs"]["status"], "active")
        self.assertEqual(after["circuit/customer/ce-harbor-logistics-chicago-west-001"],
                         self.objects["circuit/customer/ce-harbor-logistics-chicago-west-001"])
        back = deepcopy(self.plan["recipe"])
        next(e for e in back["customers"][0]["sites"] if e["pop"] == "cleveland-east").pop("status")  # decommissioning -> active
        with self.assertRaisesRegex(DesignError, "requires a new baseline"):
            generate(back, previous=self.plan)


class ProviderValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate({"profile": "provider-backbone"})
        cls.customer = "ce-harbor-logistics-chicago-west-001"
        cls.pe = "device/pop-chicago-west/pe-a"

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {o["key"]: o for o in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def strip(self):
        self.plan["contracts"] = []
        for obj in self.plan["objects"]:
            obj["meta"] = {}

    def cable(self, port):
        return next(o for o in self.plan["objects"] if o["kind"] == "cable" and port in o["refs"].values())

    def term(self, circuit, side):
        return next(o for o in self.plan["objects"] if o["kind"] == "circuit_termination" and o["refs"]["circuit"] == circuit and o["attrs"]["term_side"] == side)

    def test_direct_and_panel_customer_backbones_are_valid(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(validate(generate(self.plan["recipe"] | {"patching": "panels"})), [])
        self.assertEqual(sum(o["kind"] == "device" and o["refs"].get("role") == "role/provider-edge" for o in self.plan["objects"]), 6)
        self.assertEqual(sum(o["kind"] == "virtual_circuit_termination" for o in self.plan["objects"]), 4)

    def test_missing_noc_circuit_cannot_hide_behind_shared_wan_mode(self):
        self.plan["objects"].remove(self.objects["circuit/noc/a"])
        self.strip()
        self.assertIn("provider-noc-wan", self.codes())
        self.assertIn("provider-circuit-inventory", self.codes())

    def test_noc_requires_distinct_actual_selected_pop_edges_and_racks(self):
        self.term("circuit/noc/a", "Z")["refs"]["termination"] = "site/pop-detroit-south"
        self.strip()
        self.assertIn("provider-noc-wan", self.codes())
        self.setUp()
        self.objects["device/dc-01/edge-001-b"]["refs"]["rack"] = self.objects["device/dc-01/edge-001-a"]["refs"]["rack"]
        self.assertIn("provider-noc-diversity", self.codes())

    def test_span_requires_both_real_sites_and_local_physical_channels(self):
        circuit = "circuit/backbone/chicago-west-a/detroit-south-a"
        for mutation in ("wrong-site", "planned-patch", "mark-only", "virtual-port"):
            with self.subTest(mutation=mutation):
                self.setUp()
                term = self.term(circuit, "Z")
                cable = self.cable(term["key"])
                port = next(value for value in cable["refs"].values() if value != term["key"])
                if mutation == "wrong-site":
                    term["refs"]["termination"] = self.term(circuit, "A")["refs"]["termination"]
                elif mutation == "planned-patch":
                    cable["attrs"]["status"] = "planned"
                elif mutation == "mark-only":
                    self.plan["objects"].remove(cable)
                    term["attrs"]["mark_connected"] = True
                else:
                    self.objects[port]["attrs"]["type"] = "virtual"
                self.strip()
                self.assertIn("provider-circuit-path", self.codes())

    def test_router_mode_and_active_status_are_obligations(self):
        for key, field, value in ((f"{self.pe}/if/et-0/0/3", "enabled", True),
                                  (f"{self.pe}/if/et-0/0/0", "speed", 200000000),
                                  (f"{self.pe}/if/xe-0/1/0", "speed", 10000000)):
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["attrs"][field] = value
                self.strip()
                self.assertIn("provider-port-mode", self.codes())
        self.setUp()
        self.objects[self.pe]["attrs"]["status"] = "offline"
        self.strip()
        self.assertIn("provider-device-inventory", self.codes())
        self.assertIn("provider-pair-path", self.codes())

    def test_management_is_out_of_band_and_has_two_real_data_uplinks(self):
        fxp0 = f"{self.pe}/if/fxp0"
        self.plan["objects"].remove(self.cable(fxp0))
        self.strip()
        self.assertIn("provider-management-mode", self.codes())
        self.setUp()
        self.objects[f"ip/{fxp0}"]["refs"].pop("vrf")
        self.objects[f"{fxp0}.0"]["refs"].pop("vrf")  # Junos addresses fxp0 on unit 0
        self.assertIn("provider-management-mode", self.codes())
        # The console server's independent broadband path: cut, or re-homed
        # into the carrier's own management context, it no longer counts.
        console = "device/pop-chicago-west/console-01/if/NET2"
        self.setUp()
        self.plan["objects"].remove(self.cable(console))
        self.assertIn("provider-oob", self.codes())
        self.setUp()
        self.objects[f"ip/{console}"]["refs"]["vrf"] = "vrf/provider"
        self.assertIn("provider-oob", self.codes())
        self.setUp()
        self.cable(f"{self.pe}/if/xe-0/1/6")["attrs"]["status"] = "planned"
        self.assertIn("provider-management-uplink", self.codes())
        self.setUp()
        self.objects["device/pop-chicago-west/mgmt-01/if/Vlan10"]["attrs"]["enabled"] = False
        self.assertIn("provider-management-gateway", self.codes())

    def test_point_to_point_masks_addresses_and_vrfs_are_actual(self):
        key = f"ip/{self.pe}/if/et-0/0/0"
        for field, value in (("address", "10.255.0.0/32"), ("address", "10.1.1.1/31"), ("vrf", "vrf/management")):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.objects[key]["refs" if field == "vrf" else "attrs"][field] = value
                self.strip()
                self.assertIn("provider-routed-address", self.codes())
        self.setUp()
        self.objects[f"ip/{self.pe}/if/lo0"]["attrs"]["address"] = self.objects["ip/device/pop-chicago-west/pe-b/if/lo0"]["attrs"]["address"]
        self.assertIn("provider-loopback", self.codes())

    def test_opaque_transit_cannot_invent_an_unowned_remote_ip(self):
        port = f"{self.pe}/if/xe-0/1/7"
        local = self.objects[f"ip/{port}"]
        from ipaddress import ip_interface
        network = ip_interface(local["attrs"]["address"]).network
        for mask in (31, 32):
            with self.subTest(mask=mask):
                self.setUp()
                self.plan["objects"].append(dict(key="ip/invented-transit-peer", kind="ip_address",
                    attrs={"address": f"{network[0]}/{mask}", "status": "reserved"}, refs={}, meta={}))
                self.strip()
                self.assertIn("provider-routed-address", self.codes())

    def test_customer_virtual_membership_and_physical_parent_are_required(self):
        term = f"virtual-circuit-termination/{self.customer}"
        port = f"device/{self.customer}/edge-01/if/PrivateL3"
        for key, field, value in ((term, "role", "peer"), (port, "parent", f"device/{self.customer}/edge-01/if/wan2"),
                                  (port, "vrf", "vrf/provider"), (port, "type", "1000base-t")):
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["attrs" if field in {"role", "type"} else "refs"][field] = value
                self.strip()
                self.assertIn("provider-virtual-membership", self.codes())
        self.setUp()
        self.plan["objects"].remove(self.objects[term])
        self.strip()
        self.assertIn("provider-service-inventory", self.codes())

    def test_spoke_membership_cannot_claim_the_hub_role(self):
        spoke = "virtual-circuit-termination/ce-harbor-logistics-detroit-south-001"
        self.assertEqual(self.objects[spoke]["attrs"]["role"], "spoke")
        self.assertEqual(self.objects[f"virtual-circuit-termination/{self.customer}"]["attrs"]["role"], "hub")
        self.objects[spoke]["attrs"]["role"] = "hub"
        self.strip()
        self.assertIn("provider-virtual-membership", self.codes())

    def test_access_circuit_records_its_route_length(self):
        circuit = self.objects[f"circuit/customer/{self.customer}"]
        self.assertEqual(circuit["attrs"]["distance_unit"], "km")
        self.assertGreater(circuit["attrs"]["distance"], 0)
        for change in ({"distance": circuit["attrs"]["distance"] + 5}, {"distance": None}):
            with self.subTest(change=change):
                self.setUp()
                attrs = self.objects[f"circuit/customer/{self.customer}"]["attrs"]
                attrs.update(change)
                if attrs["distance"] is None:
                    del attrs["distance"], attrs["distance_unit"]
                self.strip()
                self.assertIn("provider-access-geography", self.codes())

    def test_customer_asn_belongs_to_its_customer_and_upstream_asns_to_nobody(self):
        self.assertEqual(self.objects["asn/customer/harbor-logistics"]["refs"]["tenant"], "tenant/cust-harbor-logistics")
        for key, change in (("asn/customer/harbor-logistics", "tenant"), ("asn/transit-a", "tenant")):
            with self.subTest(key=key):
                self.setUp()
                self.objects[key]["refs"]["tenant"] = change
                self.strip()
                self.assertIn("provider-asn", self.codes())

    def test_local_handoffs_terminate_in_their_equipment_room(self):
        term = self.term(f"circuit/customer/{self.customer}", "A")
        self.assertEqual(term["refs"]["termination"], f"location/{self.customer}")
        for target in (f"site/{self.customer}", "location/pop-chicago-west"):
            with self.subTest(target=target):
                self.setUp()
                self.term(f"circuit/customer/{self.customer}", "A")["refs"]["termination"] = target
                self.strip()
                self.assertIn("provider-circuit-path", self.codes())

    def test_carrier_handoffs_into_a_pop_record_cross_connect_and_enclosure_position(self):
        span = next(k for k, o in self.objects.items() if o["kind"] == "circuit" and k.startswith("circuit/backbone/")
                    and o["refs"]["provider"] != "provider/operator")
        term = self.term(span, "A")
        self.assertRegex(term["attrs"]["xconnect_id"], r"^XC-\d{7}$")
        self.assertRegex(term["attrs"]["pp_info"], r"^chicago-west-odf-[ab], panel [1-4], port \d+$")
        customer = self.term(f"circuit/customer/{self.customer}", "Z")
        self.assertNotIn("xconnect_id", customer["attrs"])
        other = self.term(f"circuit/transit/a", "A")
        cases = ((term, "xconnect_id", None), (term, "pp_info", "odf-9, panel 1, port 1"),
                 (customer, "xconnect_id", "XC-1234567"), (other, "xconnect_id", term["attrs"]["xconnect_id"]))
        for index, (target, field, value) in enumerate(cases):
            with self.subTest(case=index):
                self.setUp()
                obj = self.objects[target["key"]]
                if value is None:
                    del obj["attrs"][field]
                else:
                    obj["attrs"][field] = value
                self.strip()
                self.assertIn("provider-cross-connect", self.codes())

    def test_one_global_site_block_parents_every_segment(self):
        block = self.objects["prefix/pop-chicago-west/reservation"]
        self.assertNotIn("vrf", block["refs"])
        self.assertFalse([k for k in self.objects if k.startswith("prefix/dc-01/") and k.endswith("/reservation")
                          and k != "prefix/dc-01/reservation"])
        block["refs"]["vrf"] = "vrf/provider"
        self.strip()
        self.assertIn("provider-site-network", self.codes())

    def test_customer_tenant_account_route_target_and_asn_cannot_be_swapped(self):
        cases = (("virtual-circuit/customer/harbor-logistics", "tenant", "tenant", "provider-customer-service"),
                 ("provider-account/customer/harbor-logistics", "provider", "provider/transit-a", "provider-customer-service"),
                 ("vrf/customer/harbor-logistics", "export_targets", [], "provider-customer-routing"),
                 (f"site/{self.customer}", "asns", ["asn/operator"], "provider-asn-consumer"))
        for key, field, value, code in cases:
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["refs"][field] = value
                self.strip()
                self.assertIn(code, self.codes())
        self.setUp()
        self.objects["asn/customer/harbor-logistics"]["attrs"]["asn"] += 1
        self.assertIn("provider-asn", self.codes())

    def test_customer_endpoint_and_real_routed_gateway_cannot_be_shortcut(self):
        device = f"device/{self.customer}/pc-001"
        for key, field, value, code in (
            (f"{device}/if/eth0", "untagged_vlan", f"vlan/{self.customer}/management", "provider-customer-endpoint"),
            (device, "location", f"location/{self.customer}", "provider-device-inventory"),
            (f"device/{self.customer}/edge-01/if/Clients", "parent", f"device/{self.customer}/edge-01/if/port2", "provider-customer-gateway"),
            ("device/pop-chicago-west/console-01/if/NET1", "vrf", f"vrf/customer/harbor-logistics", "provider-console-management")):
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["refs"][field] = value
                self.strip()
                self.assertIn(code, self.codes())
        self.setUp()
        self.cable(f"device/{self.customer}/edge-01/if/port1")["attrs"]["status"] = "planned"
        self.strip()
        self.assertIn("provider-customer-gateway", self.codes())

    def test_small_premises_kit_cannot_hide_a_pop_or_premises_defect(self):
        """Single-feed and console-free applies only to the validator's own premises list."""
        # A premises is one circuit and one PDU, every supply still on a live local path.
        pdus = {o["key"] for o in self.plan["objects"] if o["kind"] == "device"
                and o["refs"].get("site") == f"site/{self.customer}" and o["refs"]["role"] == "role/pdu"}
        self.assertEqual(len(pdus), 1)
        self.assertEqual({self.objects[p]["refs"]["device_type"] for p in pdus}, {"hardware/pdu-120"})
        feed = next(o for o in self.plan["objects"] if o["kind"] == "power_feed" and o["refs"]["rack"]
                    == self.objects[f"device/{self.customer}/edge-01"]["refs"]["rack"])
        self.assertEqual((feed["attrs"]["voltage"], feed["attrs"]["amperage"]), (120, 20))
        # Moving a PoP switch's B supply onto its A PDU is still a diversity finding.
        switch = "device/pop-chicago-west/mgmt-01"
        ports = sorted(o["key"] for o in self.plan["objects"] if o["kind"] == "power_port" and o["refs"]["device"] == switch)
        a_outlet = next(v for v in (self.cable(ports[0])["refs"]["a"], self.cable(ports[0])["refs"]["b"]) if v != ports[0])
        spare = next(o["key"] for o in self.plan["objects"] if o["kind"] == "power_outlet"
                     and o["refs"]["device"] == self.objects[a_outlet]["refs"]["device"]
                     and not any(o["key"] in c["refs"].values() for c in self.plan["objects"] if c["kind"] == "cable"))
        cable = self.cable(ports[1])
        cable["refs"].update(a=spare, b=ports[1])
        self.strip()
        self.assertIn("dc-power-diversity", self.codes())
        # A console server at a premises, a sequential street or a renamed cage is refused.
        self.setUp()
        self.objects[f"site/{self.customer}"]["attrs"]["physical_address"] = (
            "1172 Business Way\nChicago, Illinois\nUnited States")
        self.assertIn("provider-site-context", self.codes())
        self.setUp()
        self.objects["location/pop-chicago-west"]["attrs"]["name"] = "MDF"
        self.assertIn("provider-room-geometry", self.codes())
        self.setUp()
        server = deepcopy(self.objects["device/pop-chicago-west/console-01"])
        server["key"] = f"device/{self.customer}/console-01"
        server["refs"].update(site=f"site/{self.customer}", location=f"location/{self.customer}")
        self.plan["objects"].append(server)
        self.assertIn("provider-device-inventory", self.codes())

    def test_geometry_and_panel_paths_cannot_borrow_another_site(self):
        self.plan = generate(self.plan["recipe"] | {"patching": "panels"})
        self.objects = {o["key"]: o for o in self.plan["objects"]}
        outlet = next(o for o in self.plan["objects"] if o["kind"] == "device" and o["refs"].get("site") == f"site/{self.customer}" and o["refs"].get("device_type") == "hardware/wall-outlet")
        outlet["refs"]["location"] = "location/pop-chicago-west"
        self.strip()
        self.assertIn("provider-customer-patching", self.codes())
        self.setUp()
        self.cable(f"device/{self.customer}/pc-001/if/eth0")["attrs"]["length"] = 90
        self.assertIn("provider-customer-route", self.codes())

    def test_pop_rack_and_populated_supply_failure_domains_are_required(self):
        second = "device/pop-chicago-west/pe-b"
        self.objects[second]["refs"]["rack"] = self.objects[self.pe]["refs"]["rack"]
        self.strip()
        self.assertIn("provider-router-racks", self.codes())
        for mutation in ("missing", "module", "allowance", "path"):
            with self.subTest(mutation=mutation):
                self.setUp()
                port = f"{self.pe}/power/PEM 1"
                if mutation == "missing":
                    self.plan["objects"].remove(self.objects[port])
                elif mutation == "module":
                    self.objects[port]["refs"]["module"] = f"{self.pe}/module/Power Supply 0"
                elif mutation == "allowance":
                    self.objects[port]["attrs"]["maximum_draw"] = 319
                else:
                    self.cable(port)["attrs"]["status"] = "planned"
                self.strip()
                self.assertIn("provider-power-path" if mutation == "path" else "provider-psu-inventory", self.codes())

    def test_pop_rack_coordinates_and_actual_cross_rack_patch_length_are_checked(self):
        rack = "rack/pop-chicago-west/network-02"
        # Bayed beside network-01: one cabinet width along the row.
        self.assertEqual(self.objects[rack]["meta"]["position_m"], [1.6, 1.0, 0])
        pair = self.cable(f"{self.pe}/if/et-0/0/0")
        self.assertEqual(pair["attrs"]["length"], 4)
        pair["attrs"]["length"] = 3
        self.assertIn("provider-cable-geometry", self.codes())
        self.setUp()
        self.objects[rack]["meta"]["position_m"] = [1.0, 1.0, 0]
        self.plan["contracts"] = []
        self.assertIn("provider-rack-geometry", self.codes())

    def test_ledger_corruption_cannot_suppress_required_topology(self):
        for scope, value in (("provider-pop-order", {}), ("provider-customers", {"harbor-logistics": True}),
                             ("provider-link-prefixes", []), (f"provider-transport-ports/{self.pe}", {}),
                             ("provider-service-ports/chicago-west", {"noc/a": 0, self.customer: 0})):
            with self.subTest(scope=scope):
                self.setUp()
                self.plan["reservations"][scope] = value
                self.strip()
                self.assertIn("provider-allocation", self.codes())
        for value in (1, 255, 65535, True, []):
            with self.subTest(site_slot=value):
                self.setUp()
                self.plan["allocations"][self.customer] = value
                self.assertIn("provider-allocation", self.codes())

    def test_saved_demand_has_bounded_types_private_pool_and_hub_headroom(self):
        for field, value in (("pops", []), ("customers", {}), ("noc_peak_mbps", True), ("reserve_fraction", float("nan")),
                             ("asn_base", 4294967294), ("address_pool", "127.0.0.0/8"), ("as_of", [])):
            with self.subTest(field=field):
                self.setUp()
                self.plan["recipe"][field] = value
                self.assertIn("provider-recipe", self.codes())
        self.setUp()
        self.plan["recipe"]["customers"][0]["hub_commit_mbps"] = 50
        self.strip()
        self.assertIn("provider-recipe", self.codes())

    def test_new_pop_and_customer_growth_preserve_existing_native_identities(self):
        recipe = deepcopy(self.plan["recipe"])
        recipe["pops"].append(dict(key="aaa-new", metro="milwaukee"))
        recipe["customers"][0]["sites"].append(dict(pop="aaa-new", count=1))
        recipe["customers"][0]["lan_endpoints"] = 6
        grown = generate(recipe, previous=self.plan)
        self.assertEqual(validate(grown), [])
        current = {o["key"]: o for o in grown["objects"]}
        stable_kinds = {"cable", "circuit", "circuit_termination", "ip_address", "journal_entry", "contact", "rack", "location", "virtual_machine"}
        for key, obj in self.objects.items():
            if obj["kind"] in stable_kinds or (obj["kind"] == "interface" and any(o["kind"] == "cable" and key in o["refs"].values() for o in self.plan["objects"])):
                self.assertEqual(current[key], obj, key)
        for field in ("allocations", "reservations"):
            if field == "allocations":
                self.assertTrue(self.plan[field].items() <= grown[field].items())
            else:
                for scope, slots in self.plan[field].items():
                    self.assertTrue(slots.items() <= grown[field][scope].items(), scope)
        bad = deepcopy(grown)
        bad["reservations"]["provider-backbone-spans"]["circuit/backbone/aaa-new-a/aaa-new-b"] = len(bad["reservations"]["provider-backbone-spans"])
        self.assertIn("provider-allocation", {o["code"] for o in validate(bad)})
        recipe["customers"][0]["site_peak_mbps"] += 1
        with self.assertRaises(DesignError):
            generate(recipe, previous=grown)

    def test_shortest_hop_model_is_directed_and_recomputes_after_span_loss(self):
        graph = {"a": [("ab", "b", "ab"), ("ac", "c", "ac")],
                 "b": [("ab", "a", "ab"), ("bd", "d", "bd")],
                 "c": [("ac", "a", "ac"), ("cd", "d", "cd")],
                 "d": [("bd", "b", "bd"), ("cd", "c", "cd")]}
        flows = {("a", "d"): 80, ("b", "d"): 30}
        self.assertEqual(_loads(graph, flows), Counter({("ab", "a", "b"): 80, ("bd", "b", "d"): 110}))
        self.assertEqual(_loads(graph, flows, "bd"), Counter({("ac", "a", "c"): 110, ("cd", "c", "d"): 110, ("ab", "b", "a"): 30}))

    def test_finite_service_and_management_descriptors_reject_execution_guarantees(self):
        for key, field, text in (
            ("virtual-circuit/customer/harbor-logistics", "comments", "All customer sites have guaranteed availability after any single PE failure; configured BGP and MPLS forwarding are proven."),
            (f"{self.pe}/if/lo0", "description", "Out-of-band management is isolated from all production failures."),
            (f"site/{self.customer}", "description", "Dual-homed customer site with tested failover."),
            (self.pe, "description", "Guaranteed customer availability during all router failures.")):
            with self.subTest(key=key):
                self.setUp()
                self.objects[key]["attrs"][field] = text
                self.strip()
                self.assertIn("provider-scope-text", self.codes())

    def test_shared_noc_resources_listeners_and_rack_replicas_remain_checked(self):
        for field, value in (("vcpus", 1), ("memory", 1), ("disk", 1)):
            with self.subTest(field=field):
                self.setUp()
                self.objects["vm/dc-01/identity/001"]["attrs"][field] = value
                self.strip()
                self.assertIn("dc-workload-resources", self.codes())
        self.setUp()
        self.objects["vm/dc-01/identity/002"]["refs"]["device"] = self.objects["vm/dc-01/identity/001"]["refs"]["device"]
        self.strip()
        self.assertIn("dc-replica-diversity", self.codes())
        self.setUp()
        service = next(o for o in self.plan["objects"] if o["kind"] == "service" and o["refs"].get("virtual_machine") == "vm/dc-01/dns/001")
        service["attrs"]["port_mappings"] = ["tcp/443"]
        self.strip()
        self.assertIn("dc-workload-listener", self.codes())

    def test_supported_long_labels_and_distinct_customer_routing_domains(self):
        recipe = {"profile": "provider-backbone", "namespace": "a"*20, "name": "N"*80,
            "pops": [{"key": letter*20, "metro": metro} for letter, metro in (("a", "chicago"), ("b", "detroit"), ("c", "cleveland"))],
            "customers": [{"key": letter*20, "hub_pop": "a"*20, "sites": [{"pop": "a"*20}, {"pop": "b"*20}], "lan_endpoints": 12} for letter in ("d", "e")]}
        plan = generate(recipe)
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        objects[f"vrf/customer/{'d'*20}"]["refs"]["export_targets"] = [f"route-target/customer/{'e'*20}"]
        self.assertIn("provider-customer-routing", {o["code"] for o in validate(plan)})

    def test_native_accounts_are_scoped_by_service_without_invented_tenant_field(self):
        recipe = deepcopy(self.plan["recipe"])
        recipe["customers"][0]["key"] = "noc"
        plan = generate(recipe)
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertIn("provider-account/operator/noc", objects)
        self.assertIn("provider-account/customer/noc", objects)
        self.assertNotEqual(objects["provider-account/operator/noc"]["attrs"]["account"], objects["provider-account/customer/noc"]["attrs"]["account"])
        self.assertNotIn("tenant", objects["provider-account/customer/noc"]["refs"])
        objects["provider-account/customer/noc"]["refs"]["tenant"] = "tenant/cust-noc"
        self.assertIn("provider-customer-service", {o["code"] for o in validate(plan)})

    def test_malformed_native_virtual_reference_and_asn_return_findings(self):
        term = f"virtual-circuit-termination/{self.customer}"
        self.objects[term]["refs"]["interface"] = []
        self.assertIn("invalid-reference", self.codes())
        self.setUp()
        self.objects["asn/operator"]["attrs"]["asn"] = []
        self.assertIn("asn-identity", self.codes())


class ProviderRealismTests(unittest.TestCase):
    """The showcase carrier: geography, carriers, numbering and one timeline."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(tomllib.loads((ROOT / "profiles/showcase-provider.toml").read_text()))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {o["key"]: o for o in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def spans(self):
        return {k: o for k, o in self.objects.items() if o["kind"] == "circuit" and k.startswith("circuit/backbone/")}

    def test_backbone_follows_the_lakeshore_with_owned_metro_fiber(self):
        self.assertEqual(validate(self.plan), [])
        metro = {f"site/pop-{p['key']}": p["metro"] for p in self.plan["recipe"]["pops"]}
        pairs = Counter()
        for key, span in self.spans().items():
            a, z = (metro[self.objects[self.objects[f"{key}/{side}"]["refs"]["termination"]]["refs"]["site"]] for side in "AZ")
            if a == z:
                self.assertEqual(span["refs"]["provider"], "provider/operator", key)
                self.assertNotIn("commit_rate", span["attrs"])
                self.assertLess(span["attrs"]["distance"], 40, key)
            else:
                pairs[frozenset((a, z))] += 1
                self.assertIn(span["attrs"]["commit_rate"], (10000000, 100000000), key)
        # Milwaukee-Chicago-Detroit-Cleveland, two diverse spans per adjacency;
        # nothing crosses Lake Michigan and Cleveland never skips Detroit.
        self.assertEqual(pairs, {frozenset(("milwaukee", "chicago")): 2, frozenset(("chicago", "detroit")): 2,
                                 frozenset(("detroit", "cleveland")): 2})
        for key, span in self.spans().items():
            self.assertNotIn("seed", key + span["attrs"]["cid"] + span["attrs"]["description"])

    def test_carrier_numbering_and_public_space(self):
        asns = {k: o["attrs"]["asn"] for k, o in self.objects.items() if o["kind"] == "asn"}
        for key in ("asn/operator", "asn/transit-a", "asn/transit-b"):
            self.assertTrue(64496 <= asns[key] <= 64511, key)
            self.assertEqual(self.objects[key]["refs"]["rir"], "rir/arin")
        self.assertEqual(self.objects["rir/arin"]["attrs"]["name"], "ARIN")
        self.assertEqual(self.objects["ipv6/aggregate"]["refs"]["rir"], "rir/arin")
        loopback = self.objects["ip/device/pop-chicago-cermak/pe-a/if/lo0"]["attrs"]["address"]
        self.assertTrue(loopback.startswith("192.0.2."), loopback)
        self.assertFalse([k for k in self.objects if k.startswith("root/customer/")])
        # Customer slots, ASNs and route distinguishers follow onboarding order.
        slots = self.plan["reservations"]["provider-customers"]
        self.assertEqual(sorted(slots, key=slots.get), [c["key"] for c in self.plan["recipe"]["customers"]])
        operator = asns["asn/operator"]
        first = self.plan["recipe"]["customers"][0]["key"]
        self.assertEqual(self.objects[f"vrf/customer/{first}"]["attrs"]["rd"], f"{operator}:1001")
        for label in ("transport-a", "transport-b", "transit-a", "transit-b"):
            email = self.objects[f"contact/provider/{label}"]["attrs"]["email"]
            self.assertFalse(email.endswith(self.plan["recipe"]["namespace"] + ".example"), email)

    def test_premises_sit_in_their_serving_pops_area_and_growth_keeps_them_there(self):
        from estates.provider import km
        pops = {k: (o["attrs"]["latitude"], o["attrs"]["longitude"]) for k, o in self.objects.items() if k.startswith("site/pop-")}
        metro = {f"site/pop-{p['key']}": p["metro"] for p in self.plan["recipe"]["pops"]}
        for key, obj in self.objects.items():
            if not key.startswith("site/ce-"):
                continue
            home = next(p for p in pops if key.removeprefix("site/ce-").count(p.removeprefix("site/pop-") + "-"))
            here = (obj["attrs"]["latitude"], obj["attrs"]["longitude"])
            nearest = min((p for p in pops if metro[p] == metro[home]), key=lambda p: km(here, pops[p]))
            self.assertEqual(nearest, home, key)
        # A PoP appended into a metro never pulls an existing premises out of its area.
        grown = deepcopy(self.plan["recipe"])
        grown["pops"].append({"key": "chicago-ravenswood", "metro": "chicago"})
        grown["customers"][0]["sites"].append({"pop": "chicago-ravenswood", "count": 1})
        plan = generate(grown, previous=self.baseline)
        self.assertEqual(validate(plan), [])
        after = {o["key"]: o for o in plan["objects"]}
        self.assertFalse([k for k in self.objects if k.startswith("site/") and self.objects[k]["attrs"] != after[k]["attrs"]])

    def test_mutations_of_the_realism_obligations_fail(self):
        spans = sorted(self.spans())
        dark = next(k for k in spans if self.objects[k]["refs"]["provider"] == "provider/operator")
        leased = [k for k in spans if self.objects[k]["refs"]["provider"].startswith("provider/transport-")]
        hub = "circuit/customer/ce-lakeshore-health-cleveland-flats-001"
        second = "circuit/customer/ce-great-lakes-credit-detroit-dearborn-001"
        cases = (
            ("provider-backbone-geography", lambda: self.objects[dark]["attrs"].update(distance=1.0)),
            ("provider-backbone-diversity", lambda: [self.objects[k]["refs"].update(provider="provider/transport-a",
                provider_account="provider-account/provider/transport-a") for k in leased]),
            ("provider-circuit-path", lambda: self.objects[dark]["attrs"].update(commit_rate=10000000)),
            ("provider-timeline", lambda: self.objects[hub]["attrs"].update(install_date="2010-01-01")),
            ("provider-timeline", lambda: self.objects[second]["attrs"].update(install_date="2018-01-01")),
            # Homed on Flats but standing on top of the Lakewood PoP.
            ("provider-premises-geography", lambda: self.objects["site/ce-lakeshore-health-cleveland-flats-001"]["attrs"].update(
                latitude=self.objects["site/pop-cleveland-lakewood"]["attrs"]["latitude"],
                longitude=self.objects["site/pop-cleveland-lakewood"]["attrs"]["longitude"])),
            # In no other PoP's area, but farther than the service radius.
            ("provider-premises-geography", lambda: self.objects["site/ce-lakeshore-health-cleveland-flats-001"]["attrs"].update(
                latitude=self.objects["site/pop-cleveland-flats"]["attrs"]["latitude"] + 0.4)),
            # The core is the global table: a span /31 inside a VRF is refused.
            ("provider-routed-address", lambda: [self.objects[k]["refs"].update(vrf="vrf/provider")
                for k in (f"prefix/link/{leased[0]}",)]),
            # A customer VRF that stops importing the hub cuts NOC reach to its CEs.
            ("provider-customer-routing", lambda: self.objects["vrf/customer/lakeshore-health"]["refs"].update(
                import_targets=["route-target/customer/lakeshore-health"])),
            ("provider-routing-domain", lambda: self.objects["vrf/provider"]["refs"].update(
                import_targets=["route-target/management/hub"])),
            # A loopback on the network address of its /24.
            ("provider-allocation", lambda: self.plan["reservations"]["provider-loopbacks"].update(
                {"device/pop-chicago-cermak/pe-a": 254})),
            # A CE-only premises with a management VLAN and no switch to use it.
            ("provider-customer-gateway", lambda: self.objects["prefix/ce-lakeshore-health-cleveland-flats-001/management"]["refs"].update(
                vlan="vlan/ce-lakeshore-health-cleveland-flats-001/clients")),
            # Transit numbered from the operator's own aggregate.
            ("provider-public-space", lambda: self.objects["prefix/upstream/transit-a"]["attrs"].update(prefix="192.0.2.224/28")),
            ("provider-carrier-identity", lambda: self.objects["provider/transport-b"]["attrs"].update(name="Meridian Transport")),
            ("provider-asn", lambda: self.objects["asn/transit-a"]["attrs"].update(asn=self.objects["asn/transit-b"]["attrs"]["asn"])),
            ("provider-public-space", lambda: self.plan["objects"].remove(self.objects["aggregate/public/203.0.113.0/24"])),
        )
        for code, mutate in cases:
            with self.subTest(code=code):
                self.setUp()
                mutate()
                self.plan["contracts"] = []
                self.assertIn(code, self.codes())


if __name__ == "__main__":
    unittest.main()
