"""Provider counterexamples exercise actual ownership and physical paths."""

from collections import Counter
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.model import DesignError
from estates.validate import validate
from estates.validate_provider import _loads


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
        self.assertEqual(sum(o["kind"] == "virtual_circuit_termination" for o in self.plan["objects"]), 3)

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
        circuit = "circuit/backbone/seed-01"
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

    def test_management_is_inband_and_has_two_real_data_uplinks(self):
        self.objects[f"{self.pe}/if/fxp0"]["refs"]["vrf"] = "vrf/provider"
        self.strip()
        self.assertIn("provider-management-mode", self.codes())
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
                    attrs={"address": f"{network[1]}/{mask}", "status": "reserved"}, refs={"vrf": "vrf/provider"}, meta={}))
                self.strip()
                self.assertIn("provider-routed-address", self.codes())

    def test_customer_virtual_membership_and_physical_parent_are_required(self):
        term = f"virtual-circuit-termination/{self.customer}"
        port = f"device/{self.customer}/edge-01/if/PrivateL3"
        for key, field, value in ((term, "role", "hub"), (port, "parent", f"device/{self.customer}/edge-01/if/wan2"),
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
            (f"device/{self.customer}/console-01/if/mgmt0", "vrf", "vrf/provider", "provider-console-management")):
            with self.subTest(key=key, field=field):
                self.setUp()
                self.objects[key]["refs"][field] = value
                self.strip()
                self.assertIn(code, self.codes())
        self.setUp()
        self.cable(f"device/{self.customer}/edge-01/if/port1")["attrs"]["status"] = "planned"
        self.strip()
        self.assertIn("provider-customer-gateway", self.codes())

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
        self.assertEqual(self.objects[rack]["meta"]["position_m"], [5.2, 4, 0])
        pair = self.cable(f"{self.pe}/if/et-0/0/0")
        self.assertEqual(pair["attrs"]["length"], 5)
        pair["attrs"]["length"] = 3
        self.assertIn("provider-cable-geometry", self.codes())
        self.setUp()
        self.objects[rack]["meta"]["position_m"] = [4, 4, 0]
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
        bad["reservations"]["provider-parents/aaa-new/a"] = {"device/pop-aaa-new/pe-a": 0}
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
        service["attrs"]["ports"] = [443]
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


if __name__ == "__main__":
    unittest.main()
