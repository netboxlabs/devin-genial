"""Independent host, hardware, policy-dispatch and campus-availability regressions."""

from copy import deepcopy
from ipaddress import ip_interface
import unittest

from estates.generate import generate
from estates.model import canonical
from estates.validate import validate


RECIPES = {
    "regional-bank": {"profile": "regional-bank", "headquarters": 0, "branches": {"small": 1}},
    "enterprise-data-center": {"profile": "enterprise-data-center", "workloads": [{"key": "web"}]},
    "school-district": {"profile": "school-district", "schools": [{"key": "oak", "classrooms": 2}]},
    "hospital-clinics": {"profile": "hospital-clinics", "hospitals": [{"key": "central", "wards": [{"key": "north", "beds": 4}]}], "clinics": []},
}


def index(plan):
    return {obj["key"]: obj for obj in plan["objects"]}


def codes(plan):
    return {finding["code"] for finding in validate(plan)}


def host_plan(address, status="active", assigned=True):
    """An independently authored legacy graph, without profile-specific policy."""
    return {"objects": [
        {"key": "prefix", "kind": "prefix", "attrs": {"prefix": "10.0.0.0/16"}},
        {"key": "ip", "kind": "ip_address", "attrs": {"address": address, "status": status},
         "refs": {"assigned_object": "eth0"} if assigned else {}},
        {"key": "vm", "kind": "virtual_machine", "attrs": {"name": "app"},
         "refs": {"primary_ip4": "ip"} if assigned else {}},
        {"key": "eth0", "kind": "vm_interface", "attrs": {"name": "eth0"}, "refs": {"virtual_machine": "vm"}},
    ]}


class AddressSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baselines = {profile: generate(recipe) for profile, recipe in RECIPES.items()}

    def test_all_profile_baselines_are_valid_and_validation_does_not_mutate(self):
        for profile, plan in self.baselines.items():
            with self.subTest(profile=profile):
                before = canonical(plan)
                self.assertEqual(validate(plan), [])
                self.assertEqual(canonical(plan), before)
                self.assertEqual(canonical(generate(plan["recipe"])), before)

    def test_primary_listener_network_and_broadcast_addresses_fail_all_profiles(self):
        for profile, baseline in self.baselines.items():
            for boundary in ("network_address", "broadcast_address"):
                with self.subTest(profile=profile, boundary=boundary):
                    plan = deepcopy(baseline)
                    objects = index(plan)
                    service = next(o for o in plan["objects"] if o["kind"] == "service" and o["refs"].get("virtual_machine"))
                    vm = objects[service["refs"]["virtual_machine"]]
                    address = objects[vm["refs"]["primary_ip4"]]
                    network = ip_interface(address["attrs"]["address"]).network
                    address["attrs"]["address"] = f"{getattr(network, boundary)}/{network.prefixlen}"
                    plan["contracts"] = []
                    self.assertIn("ip-host-address", codes(plan))

    def test_host_boundaries_use_actual_mask_and_preserve_31_and_32(self):
        accepted = ["10.0.0.1/24", "10.0.0.254/24", "10.0.1.0/20", "10.0.0.255/20",
                    "10.0.0.0/31", "10.0.0.1/31", "10.0.0.0/32", "10.0.0.255/32"]
        for address in accepted:
            with self.subTest(address=address):
                self.assertEqual(validate(host_plan(address)), [])
        for address in ("10.0.0.0/24", "10.0.0.255/24", "10.0.0.0/20", "10.0.15.255/20"):
            with self.subTest(address=address):
                self.assertEqual(codes(host_plan(address)), {"ip-host-address"})

    def test_generated_vlan_gateways_need_the_segment_mask_without_contracts(self):
        for profile, baseline in self.baselines.items():
            objects = index(baseline)
            gateway = next(obj for obj in baseline["objects"] if obj["kind"] == "ip_address" and
                (port := objects.get(obj["refs"].get("assigned_object"), {})).get("kind") == "interface" and
                port["attrs"].get("type") == "virtual" and port["refs"].get("untagged_vlan"))
            for mask in (8, 32):
                with self.subTest(profile=profile, mask=mask):
                    plan = deepcopy(baseline)
                    ip = index(plan)[gateway["key"]]
                    ip["attrs"]["address"] = f"{ip_interface(ip['attrs']['address']).ip}/{mask}"
                    plan["contracts"] = []
                    self.assertIn("ip-vlan-mask", codes(plan))

    def test_reserved_boundary_is_inventory_only_not_an_assignment_loophole(self):
        for address in ("10.0.0.0/24", "10.0.0.255/24"):
            with self.subTest(address=address):
                self.assertEqual(validate(host_plan(address, "reserved", assigned=False)), [])
                self.assertIn("ip-host-address", codes(host_plan(address, "reserved")))
                self.assertIn("ip-host-address", codes(host_plan(address, "active", assigned=False)))
                for field in ("primary_ip4", "ipaddresses"):
                    plan = host_plan(address, "reserved", assigned=False)
                    owner = index(plan)["vm"]
                    owner["refs"][field] = ["ip"] if field == "ipaddresses" else "ip"
                    self.assertIn("ip-host-address", codes(plan))

    def test_matching_overspeed_at_both_physical_ends_is_not_a_valid_link(self):
        for profile, baseline in self.baselines.items():
            with self.subTest(profile=profile):
                plan = deepcopy(baseline)
                objects = index(plan)
                cable = next(o for o in plan["objects"] if o["kind"] == "cable" and all(
                    objects[key]["kind"] == "interface" and objects[key]["attrs"]["type"] == "10gbase-x-sfpp"
                    for key in (o["refs"]["a"], o["refs"]["b"])))
                for key in cable["refs"].values():
                    objects[key]["attrs"]["speed"] = 100000000
                findings = validate(plan)
                self.assertEqual({f["object"] for f in findings if f["code"] == "interface-speed"}, set(cable["refs"].values()))
                for speed in (10000000, 1000000):
                    for key in cable["refs"].values():
                        objects[key]["attrs"]["speed"] = speed
                    if speed == 10000000:
                        self.assertEqual(validate(plan), [])
                    else:
                        # These installed 10G LR modules cannot be made into
                        # 1G optics merely by changing both interface rates.
                        findings = validate(plan)
                        self.assertEqual({f["object"] for f in findings if f["code"] == "optics-compatibility"}, set(cable["refs"].values()))
                        self.assertNotIn("interface-speed", {f["code"] for f in findings})

    def test_unused_physical_port_still_has_a_nominal_ceiling(self):
        plan = deepcopy(self.baselines["enterprise-data-center"])
        occupied = {key for o in plan["objects"] if o["kind"] == "cable" for key in o["refs"].values()}
        port = next(o for o in plan["objects"] if o["kind"] == "interface" and
                    o["attrs"].get("type") == "10gbase-x-sfpp" and o["key"] not in occupied)
        port["attrs"]["speed"] = 100000000
        self.assertEqual([(f["code"], f["object"]) for f in validate(plan)], [("interface-speed", port["key"])])

    def test_generated_profile_cannot_disable_domain_policy(self):
        for profile, baseline in self.baselines.items():
            for value in (None, "typo-enterprise", "", False, [], {}):
                with self.subTest(profile=profile, value=value):
                    plan = deepcopy(baseline)
                    plan["recipe"]["profile"] = value
                    plan["contracts"] = []
                    self.assertEqual(codes(plan), {"plan-profile"})
            plan = deepcopy(baseline)
            del plan["recipe"]["profile"]
            self.assertEqual(codes(plan), {"plan-profile"})
            del plan["generator_version"]
            self.assertEqual(codes(plan), {"plan-profile"})
        plan = host_plan("10.0.0.1/24")
        self.assertEqual(validate(plan), [])
        plan["recipe"] = {"profile": None}
        self.assertEqual(codes(plan), {"plan-profile"})


class BankAvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recipe = {"headquarters": 1, "headquarters_staff": 60, "branches": {"small": 3, "medium": 1},
                  "site_designs": {"br-s0001": "modern", "br-s0002": "inherited", "br-s0003": "refreshed"}}
        cls.baselines = {patching: generate(recipe | {"patching": patching}) for patching in ("direct", "panels")}

    def changed(self, baseline):
        plan = deepcopy(baseline)
        plan["contracts"] = []
        return plan, index(plan)

    def test_direct_and_panel_baselines_and_unused_planned_inventory(self):
        for patching, baseline in self.baselines.items():
            with self.subTest(patching=patching):
                self.assertEqual(validate(baseline), [])
                plan = deepcopy(baseline)
                plan["objects"].append({"kind": "cable", "key": "future-spare-patch",
                    "attrs": {"type": "cat6", "status": "planned"}, "refs": {
                        "a": "device/br-s0001/edge-a/if/port1", "b": "device/br-s0001/edge-b/if/port1"}})
                self.assertEqual(validate(plan), [])

    def test_offline_access_and_endpoint_channels_fail_without_contracts(self):
        for patching, baseline in self.baselines.items():
            for site_id in ("br-s0001", "br-s0002", "br-s0003", "br-m0001", "hq-01"):
                for defect in ("access-offline", "endpoint-cable-planned", "endpoint-offline"):
                    with self.subTest(patching=patching, site=site_id, defect=defect):
                        plan, objects = self.changed(baseline)
                        endpoint = next(o for o in plan["objects"] if o["kind"] == "device" and
                                        o["refs"].get("site") == f"site/{site_id}" and o["refs"].get("role") == "role/workstation")
                        if defect == "access-offline":
                            for device in plan["objects"]:
                                if device["kind"] == "device" and device["refs"].get("site") == f"site/{site_id}" and device["refs"].get("role") == "role/access":
                                    device["attrs"]["status"] = "offline"
                        elif defect == "endpoint-offline":
                            endpoint["attrs"]["status"] = "offline"
                            endpoint["meta"]["endpoint"] = False
                        else:
                            port = endpoint["key"] + "/if/eth0"
                            next(o for o in plan["objects"] if o["kind"] == "cable" and port in o["refs"].values())["attrs"]["status"] = "planned"
                        self.assertIn("bank-endpoint-path", codes(plan))

    def test_required_upstream_peer_and_management_paths_need_available_components(self):
        for patching, baseline in self.baselines.items():
            for device, port, expected in (
                ("device/br-s0001/access-01", "TenGigabitEthernet1/1/1", "bank-uplink-path"),
                ("device/br-s0001/access-01", "TenGigabitEthernet1/1/3", "bank-peer-path"),
                ("device/br-m0001/dist-a", "Ethernet49/1", "bank-peer-path"),
                ("device/hq-01/idf-02-access-01", "TenGigabitEthernet1/1/1", "bank-uplink-path"),
                ("device/br-s0001/access-01", "GigabitEthernet0/0", "bank-management-path"),
                ("device/hq-01/idf-02-access-01", "GigabitEthernet0/0", "bank-management-path")):
                for mutation in ("planned", "disabled"):
                    with self.subTest(patching=patching, device=device, port=port, mutation=mutation):
                        plan, objects = self.changed(baseline)
                        key = device + "/if/" + port
                        if mutation == "disabled":
                            objects[key]["attrs"]["enabled"] = False
                        else:
                            next(o for o in plan["objects"] if o["kind"] == "cable" and key in o["refs"].values())["attrs"]["status"] = "planned"
                        self.assertIn(expected, codes(plan))

    def test_missing_contract_cannot_hide_actual_power_or_weaken_allowance(self):
        for patching, baseline in self.baselines.items():
            for site_id in ("br-s0001", "hq-01"):
                with self.subTest(patching=patching, site=site_id):
                    plan, objects = self.changed(baseline)
                    device = f"device/{site_id}/access-01"
                    objects[device]["meta"].update(planned_watts=0, endpoint=True, purpose="wall-outlet")
                    for port in plan["objects"]:
                        if port["kind"] == "power_port" and port["refs"].get("device") == device:
                            port["attrs"].update(allocated_draw=0, maximum_draw=0)
                    self.assertTrue({"bank-campus-contract", "bank-power-contract", "power-allocation"} <= codes(plan))
                    plan, objects = self.changed(baseline)
                    port = next(o for o in plan["objects"] if o["kind"] == "power_port" and o["refs"].get("device") == device)
                    next(o for o in plan["objects"] if o["kind"] == "cable" and port["key"] in o["refs"].values())["attrs"]["status"] = "planned"
                    self.assertTrue({"power-path-status", "power-redundancy"} <= codes(plan))


if __name__ == "__main__":
    unittest.main()
