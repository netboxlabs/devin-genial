"""Run with ``python -m unittest discover -s tests -p 'test_validate.py'``."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from estates.validate import validate


CATALOG = {
    "unit": {
        "u_height": 1, "is_full_depth": False,
        "interfaces": [{"name": "eth1", "type": "1000base-t"}, {"name": "eth2", "type": "1000base-t"}],
        "power_ports": [],
    },
    "panel": {"u_height": 0, "is_full_depth": False, "interfaces": [], "power_ports": []},
}


def fixture():
    """A tiny independently authored graph with a passive path and two upstreams."""
    objects = [
        {"key": "s", "kind": "site", "attrs": {"name": "site"}},
        {"key": "other", "kind": "site", "attrs": {"name": "other"}},
        {"key": "rack", "kind": "rack", "attrs": {"name": "rack", "u_height": 4}, "refs": {"site": "s"}},
        {"key": "role", "kind": "device_role", "attrs": {"name": "network"}},
        {"key": "vrf", "kind": "vrf", "attrs": {"name": "corp"}},
        {"key": "vlan", "kind": "vlan", "attrs": {"name": "corp", "vid": 10}, "refs": {"scope_site": "s"}},
        {"key": "prefix", "kind": "prefix", "attrs": {"prefix": "10.0.0.0/24"}, "refs": {"vrf": "vrf", "vlan": "vlan", "scope_site": "s"}},
        {"key": "ip", "kind": "ip_address", "attrs": {"address": "10.0.0.3/24"}, "refs": {"vrf": "vrf", "assigned_object": "d3/eth1"}},
        {"key": "cluster", "kind": "cluster", "attrs": {"name": "compute"}, "refs": {"scope_site": "s"}},
        {"key": "vm", "kind": "virtual_machine", "attrs": {"name": "app", "vcpus": 1, "memory": 128, "disk": 10}, "refs": {"cluster": "cluster", "device": "d1"}},
        {"key": "panel", "kind": "device", "attrs": {"name": "panel"}, "refs": {"site": "s"}, "meta": {"hardware": "panel"}},
        {"key": "front", "kind": "front_port", "attrs": {"name": "1", "type": "8p8c", "rear_port_position": 1}, "refs": {"device": "panel", "rear_port": "rear"}},
        {"key": "rear", "kind": "rear_port", "attrs": {"name": "1", "type": "8p8c", "positions": 1}, "refs": {"device": "panel"}},
    ]
    for n in range(1, 4):
        key = f"d{n}"
        rel = {"site": "s", "rack": "rack", "role": "role"}
        if n == 1:
            rel["cluster"] = "cluster"
        objects.append({"key": key, "kind": "device", "attrs": {"name": key, "position": n, "face": "front"}, "refs": rel, "meta": {"hardware": "unit", "resources": {"vcpus": 4, "memory_mb": 1024, "disk_mb": 100}}})
        for port in (1, 2):
            objects.append({"key": f"{key}/eth{port}", "kind": "interface", "attrs": {"name": f"eth{port}", "type": "1000base-t", "mode": "access"}, "refs": {"device": key, "untagged_vlan": "vlan"}})
    for n, (a, b) in enumerate((("d1/eth1", "front"), ("rear", "d3/eth1"), ("d3/eth2", "d2/eth1"), ("d1/eth2", "d2/eth2"))):
        objects.append({"key": f"c{n}", "kind": "cable", "attrs": {"type": "cat6"}, "refs": {"a": a, "b": b}})
    return {"schema_version": 1, "objects": objects, "contracts": [{
        "site": "s", "required_device_roles": {"network": 3},
        "required_connections": [{"a": "d1/eth1", "b": "d3/eth1"}],
        "redundant_uplinks": [{"device": "d3", "peers": ["d1", "d2"], "min_distinct_peers": 2}],
        "compute": [{"cluster": "cluster", "hosts": ["d1"], "reserve_fraction": 0.2}],
    }]}


def branch_fixture(design):
    """Independent two-port hardware makes a hardcoded 24-port budget fail."""
    supplies = 1 if design == "inherited" else 2
    alias = "inherited-access" if design == "inherited" else "access"
    access_spec = {"u_height": 1, "is_full_depth": False,
                   "interfaces": [{"name": n, "type": "1000base-t"} for n in ("c1", "c2")] + [{"name": n, "type": "10gbase-x-sfpp"} for n in ("u1", "u2")],
                   "access_ports": ["c1", "c2"], "uplink_ports": ["u1", "u2"],
                   "power_ports": [{"name": f"PSU{i}", "type": "iec-60320-c14"} for i in range(supplies)]}
    catalog = {alias: access_spec,
               "distribution": {"u_height": 1, "interfaces": [{"name": n, "type": "10gbase-x-sfpp"} for n in ("p0", "p1")], "power_ports": []},
               "edge": {"u_height": 0, "interfaces": [], "power_ports": []},
               "pdu": {"u_height": 0, "interfaces": [], "power_ports": [{"name": "Input", "type": "iec-60320-c20"}], "power_outlets": [{"name": f"O{i}", "type": "iec-60320-c13"} for i in range(2)]}}
    site_key, site_id = "site/br-s0001", "br-s0001"
    owner = "tenant/inherited" if design == "inherited" else "tenant"
    objects = []

    def add(key, kind, attrs=None, refs=None, meta=None):
        objects.append({"key": key, "kind": kind, "attrs": attrs or {}, "refs": refs or {}, "meta": meta or {}})

    def device(key, hardware, role):
        add(key, "device", {"name": key}, {"site": site_key, "tenant": owner, "role": f"role/{role}"}, {"hardware": hardware})
        for port in catalog[hardware]["interfaces"]:
            add(f"{key}/{port['name']}", "interface", dict(port), {"device": key})
        for port in catalog[hardware]["power_ports"]:
            add(f"{key}/{port['name']}", "power_port", dict(port), {"device": key})

    def cable(a, b, typ):
        add(f"cable/{a}/{b}", "cable", {"type": typ}, {"a": a, "b": b})

    add("tenant", "tenant", {"name": "parent"})
    add("tenant/inherited", "tenant", {"name": "inherited"})
    add(site_key, "site", {"name": "sample"}, {"tenant": owner}, {"branch_design": design, "lineage": "cedar" if design == "modern" else "birch"})
    for role in ("access", "distribution", "wan-edge", "pdu"):
        add(f"role/{role}", "device_role", {"name": role})
    vrf = f"vrf/inherited/{site_id}/users" if design != "modern" else "vrf/users"
    add(vrf, "vrf", {"name": "users"}, {"tenant": owner})
    add("prefix", "prefix", {"prefix": "172.16.0.0/24"}, {"scope_site": site_key, "tenant": owner, "vrf": vrf})
    contract = {"site": site_key, "kind": "branch", "branch_design": design, "access_hardware": alias,
                "access_devices": ["access0", "access1"], "access_usable_ports": 1,
                "redundant_uplinks": [], "power_redundancy": []}
    for side in range(2):
        device(f"dist{side}", "distribution", "distribution")
        device(f"edge{side}", "edge", "wan-edge")
        device(f"pdu{side}", "pdu", "pdu")
        add(f"panel{side}", "power_panel", {"name": f"panel{side}"}, {"site": site_key})
        add(f"feed{side}", "power_feed", {"name": f"feed{side}"}, {"power_panel": f"panel{side}"})
        cable(f"pdu{side}/Input", f"feed{side}", "power")
        for i in range(2):
            add(f"pdu{side}/O{i}", "power_outlet", {"name": f"O{i}", "type": "iec-60320-c13"}, {"device": f"pdu{side}", "power_port": f"pdu{side}/Input"})
    for i in range(2):
        access = f"access{i}"
        device(access, alias, "access")
        peers = [f"dist{i}"] if design == "inherited" else ["dist0", "dist1"]
        for j, peer in enumerate(peers):
            cable(f"{access}/u{j+1}", f"{peer}/p{i}", "smf")
        for j in range(supplies):
            cable(f"{access}/PSU{j}", f"pdu{j}/O{i}", "power")
        contract["redundant_uplinks"].append({"device": access, "peers": ["dist0", "dist1"], "min_distinct_peers": supplies})
        contract["power_redundancy"].append({"device": access, "min_distinct_pdus": supplies})
    return {"schema_version": 1, "recipe": {"reserve_fraction": 0.2, "site_designs": {site_id: design}, "acquired_sites": []},
            "design_assignments": {site_id: design}, "objects": objects, "contracts": [contract]}, catalog


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.plan = fixture()
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}
        self.catalog = deepcopy(CATALOG)
        patcher = patch("estates.validate._catalog", return_value=self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def add_bank_gateway(self):
        self.plan["contracts"][0].update(kind="branch", required_device_roles={})
        self.objects["d1"]["refs"]["role"] = "role/distribution"
        self.objects["d3"]["meta"]["endpoint"] = True
        self.plan["objects"].extend([
            {"key": "role/distribution", "kind": "device_role", "attrs": {"name": "distribution"}},
            {"key": "svi", "kind": "interface", "attrs": {"name": "Vlan10", "type": "virtual", "mode": "access"}, "refs": {"device": "d1", "untagged_vlan": "vlan", "vrf": "vrf"}},
            {"key": "gateway-ip", "kind": "ip_address", "attrs": {"address": "10.0.0.1/24"}, "refs": {"assigned_object": "svi", "vrf": "vrf"}},
        ])

    def add_dns_service(self):
        self.objects["vm"]["meta"] = {"service": "dns"}
        self.objects["vm"]["refs"]["primary_ip4"] = "dns/ip"
        self.plan["objects"].extend([
            {"key": "dns/eth0", "kind": "vm_interface", "attrs": {"name": "eth0"}, "refs": {"virtual_machine": "vm"}},
            {"key": "dns/ip", "kind": "ip_address", "attrs": {"address": "10.0.0.4/24"},
             "refs": {"assigned_object": "dns/eth0", "vrf": "vrf"}},
        ])
        self.plan["contracts"][0]["required_services"] = {"dns": 1}
        for protocol, name in (("tcp", "dns"), ("udp", "dns-udp")):
            self.plan["objects"].append({"key": f"dns/{protocol}", "kind": "service", "attrs": {"name": name, "protocol": protocol, "ports": [53]}, "refs": {"virtual_machine": "vm", "ipaddresses": ["dns/ip"]}})

    def test_valid_graph_and_no_mutation(self):
        before = deepcopy(self.plan)
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(self.plan, before)

    def test_missing_reference(self):
        self.objects["ip"]["refs"]["assigned_object"] = "missing"
        self.assertIn("missing-reference", self.codes())

    def test_duplicate_identity_and_key(self):
        self.plan["objects"].append(deepcopy(self.objects["d1"]))
        self.objects["d2"]["attrs"]["name"] = "d1"
        self.assertTrue({"duplicate-key", "duplicate-name"} <= self.codes())

    def test_hardware_inventory_and_port_type(self):
        self.objects["d1/eth1"]["attrs"]["name"] = "imaginary"
        self.objects["d2/eth1"]["attrs"]["type"] = "25gbase-x-sfp28"
        self.assertTrue({"hardware-inventory", "hardware-component", "hardware-component-type"} <= self.codes())

    def test_cable_occupancy(self):
        duplicate = deepcopy(self.objects["c0"])
        duplicate["key"] = "duplicate-cable"
        self.plan["objects"].append(duplicate)
        self.assertIn("cable-occupancy", self.codes())

    def test_fiber_cannot_connect_copper_ports(self):
        self.objects["c0"]["attrs"]["type"] = "smf"
        self.assertIn("cable-media", self.codes())

    def test_logical_interface_cannot_terminate_cable(self):
        self.objects["d1/eth1"]["attrs"]["type"] = "lag"
        self.assertIn("cable-endpoint", self.codes())

    def test_missing_passive_mapping_breaks_trace_and_redundancy(self):
        self.objects["front"]["refs"]["rear_port"] = "missing"
        self.assertTrue({"passive-mapping", "required-connection", "uplink-redundancy"} <= self.codes())

    def test_two_connections_to_same_peer_are_not_redundant(self):
        self.plan["contracts"][0]["redundant_uplinks"][0]["peers"] = ["d1", "d1"]
        self.assertIn("uplink-redundancy", self.codes())

    def test_access_capacity_counts_endpoints_through_patch_panels(self):
        self.plan["objects"].append({"key": "role/access", "kind": "device_role", "attrs": {"name": "access"}})
        self.objects["d1"]["refs"]["role"] = "role/access"
        self.objects["d2"]["meta"]["endpoint"] = True
        self.objects["d3"]["meta"]["endpoint"] = True
        self.plan["contracts"][0]["required_device_roles"] = {}
        self.plan["contracts"][0]["access_usable_ports"] = 2
        self.assertEqual(validate(self.plan), [])
        self.plan["contracts"][0]["access_usable_ports"] = 1
        self.assertIn("access-capacity", self.codes())

    def test_correct_total_with_wrong_endpoint_mix_fails(self):
        self.plan["contracts"][0]["required_device_roles"] = {}
        self.plan["contracts"][0]["endpoint_count"] = 3
        self.plan["contracts"][0]["demand"] = {"workstations": 1, "atms": 1, "cameras": 1}
        for index, role in enumerate(("workstation", "atm", "camera"), 1):
            self.plan["objects"].append({"key": f"role/{role}", "kind": "device_role", "attrs": {"name": role}})
            self.objects[f"d{index}"]["refs"]["role"] = f"role/{role}"
            self.objects[f"d{index}"]["meta"]["endpoint"] = True
        self.assertEqual(validate(self.plan), [])
        self.objects["d2"]["refs"]["role"] = "role/workstation"
        self.assertIn("endpoint-demand", self.codes())
        self.assertNotIn("endpoint-count", self.codes())

    def test_rack_overlap_and_opposite_half_depth_faces(self):
        self.objects["d2"]["attrs"]["position"] = 1
        self.assertIn("rack-overlap", self.codes())
        self.objects["d2"]["attrs"]["face"] = "rear"
        self.assertNotIn("rack-overlap", self.codes())
        self.catalog["unit"]["is_full_depth"] = True
        self.assertIn("rack-overlap", self.codes())

    def test_rack_bounds_and_site(self):
        self.objects["d2"]["attrs"]["position"] = 5
        self.objects["d2"]["refs"]["site"] = "other"
        self.assertTrue({"rack-bounds", "rack-site"} <= self.codes())

    def test_ip_containment_uses_vrf(self):
        self.objects["ip"]["refs"].pop("vrf")
        self.assertIn("ip-outside-prefix", self.codes())

    def test_container_prefix_does_not_make_an_allocated_subnet(self):
        self.objects["prefix"]["attrs"]["status"] = "container"
        self.assertIn("ip-outside-prefix", self.codes())

    def test_ip_duplicate_ignores_mask_and_respects_vrf(self):
        duplicate = deepcopy(self.objects["ip"])
        duplicate["key"] = "duplicate-ip"
        duplicate["attrs"]["address"] = "10.0.0.3/32"
        self.plan["objects"].append(duplicate)
        self.assertIn("ip-duplicate", self.codes())
        duplicate["refs"].pop("vrf")
        self.assertNotIn("ip-duplicate", self.codes())

    def test_vlan_scope(self):
        self.objects["vlan"]["refs"]["scope_site"] = "other"
        self.assertIn("vlan-scope", self.codes())

    def test_blank_vlan_mode_would_lose_membership(self):
        self.objects["d1/eth1"]["attrs"].pop("mode")
        self.assertIn("vlan-mode", self.codes())

    def test_access_vlan_must_match_across_a_passive_path(self):
        self.plan["objects"].append({"key": "other-vlan", "kind": "vlan", "attrs": {"name": "other", "vid": 20}, "refs": {"scope_site": "s"}})
        self.objects["d1/eth1"]["refs"]["untagged_vlan"] = "other-vlan"
        self.assertIn("vlan-continuity", self.codes())

    def test_both_upstream_links_must_carry_endpoint_vlan(self):
        self.add_bank_gateway()
        self.assertEqual(validate(self.plan), [])
        for key in ("d3/eth2", "d2/eth1"):
            self.objects[key]["refs"].pop("untagged_vlan")
        self.assertIn("uplink-vlan", self.codes())
        self.assertNotIn("vlan-continuity", self.codes())

    def test_declared_gateway_needs_an_address(self):
        self.add_bank_gateway()
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "gateway-ip"]
        self.assertIn("vlan-gateway", self.codes())

    def test_dual_attached_endpoint_cannot_bridge_an_isolated_upstream(self):
        self.add_bank_gateway()
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "c3"]
        self.assertIn("uplink-vlan", self.codes())
        self.assertNotIn("uplink-redundancy", self.codes())

    def test_vm_host_uplinks_carry_the_vm_vlan(self):
        self.add_bank_gateway()
        self.objects["d3"]["meta"].pop("endpoint")
        self.plan["objects"].append({"key": "vm/eth0", "kind": "vm_interface", "attrs": {"name": "eth0", "mode": "access"}, "refs": {"virtual_machine": "vm", "untagged_vlan": "vlan"}})
        self.plan["contracts"][0]["redundant_uplinks"] = [{"device": "d1", "peers": ["d2", "d3"], "min_distinct_peers": 2}]
        self.assertEqual(validate(self.plan), [])
        for key in ("d1/eth1", "d3/eth1"):
            self.objects[key]["refs"].pop("untagged_vlan")
        self.assertIn("uplink-vlan", self.codes())

    def test_primary_address_belongs_to_its_device(self):
        self.objects["d3"]["refs"]["primary_ip4"] = "ip"
        self.assertNotIn("primary-ip", self.codes())
        self.objects["d1"]["refs"]["primary_ip4"] = "ip"
        self.assertIn("primary-ip", self.codes())

    def test_effective_speed_checked_through_passive_path(self):
        for key in ("d1/eth1", "d3/eth1"):
            self.objects[key]["attrs"]["type"] = "10gbase-x-sfpp"
        self.objects["d3/eth1"]["attrs"]["type"] = "25gbase-x-sfp28"
        for key in ("front", "rear"):
            self.objects[key]["attrs"]["type"] = "lc"
        for key in ("c0", "c1"):
            self.objects[key]["attrs"]["type"] = "mmf-om4"
        self.assertIn("path-speed", self.codes())
        self.objects["d3/eth1"]["attrs"]["speed"] = 10000000
        self.assertNotIn("path-speed", self.codes())

    def test_resource_capacity_and_host_cluster(self):
        self.objects["vm"]["attrs"]["memory"] = 900
        self.objects["vm"]["refs"].pop("cluster")
        self.assertTrue({"compute-capacity", "vm-cluster"} <= self.codes())

    def test_disk_capacity_uses_megabytes(self):
        self.objects["vm"]["attrs"]["disk"] = 81
        findings = validate(self.plan)
        self.assertTrue(any(f["code"] == "compute-capacity" and "disk_mb" in f["message"] for f in findings))

    def test_required_service_checks_protocol_port_and_owner(self):
        self.add_dns_service()
        self.assertEqual(validate(self.plan), [])
        udp = next(o for o in self.plan["objects"] if o["key"] == "dns/udp")
        udp["attrs"]["ports"] = [54]
        self.assertIn("service-endpoint", self.codes())
        udp["attrs"]["ports"] = [53]
        udp["refs"] = {"device": "d1"}
        self.assertIn("service-endpoint", self.codes())

    def test_required_service_checks_replica_count_and_site(self):
        self.add_dns_service()
        self.plan["contracts"][0]["required_services"]["dns"] = 2
        self.assertIn("service-count", self.codes())
        self.plan["contracts"][0]["required_services"]["dns"] = 1
        self.plan["contracts"].append({"site": "other", "required_services": {"dns": 1}})
        self.assertTrue(any(f["code"] == "service-count" and f["object"] == "other" for f in validate(self.plan)))

    def test_service_vm_requires_a_host_in_its_cluster(self):
        self.add_dns_service()
        self.objects["vm"]["refs"]["device"] = "d2"
        self.assertIn("service-placement", self.codes())

    def test_power_redundancy_traces_upstream_panels(self):
        self.catalog["unit"]["power_ports"] = [{"name": "A", "type": "iec-60320-c14"}, {"name": "B", "type": "iec-60320-c14"}]
        self.catalog["pdu"] = {"u_height": 0, "interfaces": [], "power_ports": [{"name": "Input", "type": "iec-60320-c20"}], "power_outlets": [{"name": "Outlet", "type": "iec-60320-c13"}]}
        for n in range(1, 4):
            for side in ("A", "B"):
                self.plan["objects"].append({"key": f"d{n}/{side}", "kind": "power_port", "attrs": {"name": side, "type": "iec-60320-c14"}, "refs": {"device": f"d{n}"}})
        for side in ("A", "B"):
            self.plan["objects"].extend([
                {"key": f"panel{side}", "kind": "power_panel", "attrs": {"name": side}, "refs": {"site": "s"}},
                {"key": f"feed{side}", "kind": "power_feed", "attrs": {"name": side}, "refs": {"power_panel": f"panel{side}"}},
                {"key": f"pdu{side}", "kind": "device", "attrs": {"name": f"pdu{side}"}, "refs": {"site": "s"}, "meta": {"hardware": "pdu"}},
                {"key": f"inlet{side}", "kind": "power_port", "attrs": {"name": "Input", "type": "iec-60320-c20"}, "refs": {"device": f"pdu{side}"}},
                {"key": f"outlet{side}", "kind": "power_outlet", "attrs": {"name": "Outlet", "type": "iec-60320-c13"}, "refs": {"device": f"pdu{side}", "power_port": f"inlet{side}"}},
                {"key": f"power-in{side}", "kind": "cable", "attrs": {"type": "power"}, "refs": {"a": f"inlet{side}", "b": f"feed{side}"}},
                {"key": f"power-out{side}", "kind": "cable", "attrs": {"type": "power"}, "refs": {"a": f"outlet{side}", "b": f"d1/{side}"}},
            ])
        self.plan["contracts"][0]["power_redundancy"] = [{"device": "d1", "min_distinct_pdus": 2}]
        self.assertEqual(validate(self.plan), [])
        next(o for o in self.plan["objects"] if o["key"] == "feedB")["refs"]["power_panel"] = "panelA"
        self.assertIn("power-redundancy", self.codes())

    def test_malformed_object_is_reported(self):
        self.plan["objects"].append({"key": "bad", "kind": "site", "attrs": []})
        self.assertIn("object-format", self.codes())

    def use_branch(self, design):
        self.plan, catalog = branch_fixture(design)
        self.catalog.clear()
        self.catalog.update(catalog)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def test_each_authored_branch_design_is_valid(self):
        for design in ("modern", "inherited", "refreshed"):
            with self.subTest(design=design):
                self.use_branch(design)
                self.assertEqual(validate(self.plan), [])

    def test_inherited_wrong_hardware_or_power_inlet_fails(self):
        self.use_branch("inherited")
        self.objects["access0"]["meta"]["hardware"] = "distribution"
        self.assertIn("branch-access-hardware", self.codes())
        self.objects["access0"]["meta"]["hardware"] = "inherited-access"
        self.objects["access0/PSU0"]["attrs"]["name"] = "PSU1"
        self.assertIn("hardware-component", self.codes())

    def test_inherited_uplinks_must_alternate(self):
        self.use_branch("inherited")
        cable = next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and obj["refs"]["a"] == "access1/u1")
        cable["refs"]["b"] = "dist0/p1"
        self.assertIn("branch-inherited-distribution", self.codes())
        self.assertNotIn("uplink-redundancy", self.codes())

    def test_modern_and_refreshed_cannot_weaken_uplink_requirement(self):
        for design in ("modern", "refreshed"):
            with self.subTest(design=design):
                self.use_branch(design)
                self.plan["objects"] = [obj for obj in self.plan["objects"] if not (obj["kind"] == "cable" and obj["refs"]["a"] == "access0/u2")]
                self.plan["contracts"][0]["redundant_uplinks"][0]["min_distinct_peers"] = 1
                self.assertTrue({"branch-uplinks", "branch-uplink-contract"} <= self.codes())

    def test_branch_budget_is_derived_from_catalog_not_contract(self):
        self.use_branch("modern")
        self.plan["contracts"][0]["access_usable_ports"] = 19
        self.assertIn("branch-port-budget", self.codes())

    def test_branch_access_inventory_and_design_assignment_are_checked(self):
        self.use_branch("inherited")
        self.plan["contracts"][0]["access_devices"] = ["access0"]
        self.plan["design_assignments"]["br-s0001"] = "modern"
        self.assertTrue({"branch-access-inventory", "branch-design-selection"} <= self.codes())

    def test_refreshed_branch_retains_lineage_vrf_with_parent_ownership(self):
        self.use_branch("refreshed")
        self.objects["prefix"]["refs"]["vrf"] = "vrf/users"
        self.objects["site/br-s0001"]["refs"]["tenant"] = "tenant/inherited"
        self.assertTrue({"branch-vrf-lineage", "branch-ownership"} <= self.codes())

    def test_dual_supply_design_cannot_weaken_power_contract(self):
        self.use_branch("modern")
        self.plan["contracts"][0]["power_redundancy"][0]["min_distinct_pdus"] = 1
        self.assertIn("branch-power-contract", self.codes())

    def test_malformed_scalar_reference_is_reported_without_traceback(self):
        self.objects["d1/eth1"]["refs"]["device"] = ["d1", "d2"]
        self.assertIn("invalid-reference", self.codes())

    def test_negative_resource_is_reported_without_traceback(self):
        self.objects["vm"]["attrs"]["memory"] = -1
        self.assertIn("object-field", self.codes())

    def test_circuit_site_and_provider_are_checked(self):
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "c3"]
        self.plan["objects"].extend([
            {"key": "providerA", "kind": "provider", "attrs": {"name": "A"}},
            {"key": "providerB", "kind": "provider", "attrs": {"name": "B"}},
            {"key": "carrier", "kind": "provider_network", "attrs": {"name": "carrier"}, "refs": {"provider": "providerA"}},
            {"key": "circuit", "kind": "circuit", "attrs": {"cid": "WAN-1"}, "refs": {"provider": "providerA"}},
            {"key": "termA", "kind": "circuit_termination", "attrs": {"term_side": "A", "port_speed": 1000000}, "refs": {"circuit": "circuit", "termination": "s"}},
            {"key": "termZ", "kind": "circuit_termination", "attrs": {"term_side": "Z", "port_speed": 1000000}, "refs": {"circuit": "circuit", "termination": "carrier"}},
            {"key": "wan-cable", "kind": "cable", "attrs": {"type": "cat6"}, "refs": {"a": "d1/eth2", "b": "termA"}},
        ])
        self.assertEqual(validate(self.plan), [])
        next(o for o in self.plan["objects"] if o["key"] == "termA")["refs"]["termination"] = "other"
        next(o for o in self.plan["objects"] if o["key"] == "carrier")["refs"]["provider"] = "providerB"
        self.assertTrue({"circuit-site", "circuit-provider"} <= self.codes())

    def test_wan_budget_uses_actual_committed_connected_circuits_per_provider(self):
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "c3"]
        self.plan["contracts"][0].update(kind="dc", wan_peak_mbps=50, reserve_fraction=0.2)
        self.plan["contracts"].append({"site": "other", "kind": "branch", "demand": {"peak_mbps": 50}})
        for side, interface in (("A", "d1/eth2"), ("B", "d2/eth2")):
            self.plan["objects"].extend([
                {"key": f"provider{side}", "kind": "provider", "attrs": {"name": side}},
                {"key": f"carrier{side}", "kind": "provider_network", "attrs": {"name": side}, "refs": {"provider": f"provider{side}"}},
                {"key": f"circuit{side}", "kind": "circuit", "attrs": {"cid": side, "commit_rate": 1000000}, "refs": {"provider": f"provider{side}"}},
                {"key": f"term{side}/A", "kind": "circuit_termination", "attrs": {"term_side": "A", "port_speed": 1000000}, "refs": {"circuit": f"circuit{side}", "termination": "s"}},
                {"key": f"term{side}/Z", "kind": "circuit_termination", "attrs": {"term_side": "Z", "port_speed": 1000000}, "refs": {"circuit": f"circuit{side}", "termination": f"carrier{side}"}},
                {"key": f"wan-cable{side}", "kind": "cable", "attrs": {"type": "cat6"}, "refs": {"a": interface, "b": f"term{side}/A"}},
            ])
        self.assertEqual(validate(self.plan), [])
        circuit_b = next(o for o in self.plan["objects"] if o["key"] == "circuitB")
        circuit_b["attrs"]["commit_rate"] = 50000
        self.assertIn("wan-capacity", self.codes())
        circuit_b["attrs"]["commit_rate"] = 1000000
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "wan-cableB"]
        self.assertIn("wan-capacity", self.codes())
        self.plan["contracts"][0]["wan_peak_mbps"] = 49
        self.assertIn("wan-demand", self.codes())


if __name__ == "__main__":
    unittest.main()
