"""Counterexamples for independent school demand and connected infrastructure."""

from collections import Counter
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.validate import validate


class SchoolValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(dict(profile="school-district", namespace="school-validation",
            schools=[dict(key="oak", classrooms=9, students_per_classroom=24,
                          administrative_staff=4, lab_seats=8, wan_peak_mbps=200)]))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def assertFinding(self, code):
        self.assertIn(code, {item["code"] for item in validate(self.plan)})

    def strip_contracts(self):
        self.plan["contracts"] = []

    def cable(self, port):
        return next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and port in obj["refs"].values())

    def disconnect(self, port):
        self.cable(port)["attrs"]["status"] = "planned"

    def test_requested_graph_passes_and_has_new_school_services(self):
        self.assertEqual(validate(self.plan), [])
        services = {obj["meta"].get("service") for obj in self.plan["objects"] if obj["kind"] == "virtual_machine"}
        self.assertEqual(services, {"identity", "dns", "learning-portal", "files", "monitoring"})

    def test_panels_and_custom_carrier_tiers_are_valid(self):
        for change in ({"patching": "panels"}, {"wan_tiers_mbps": [100, 250, 500, 1000]}):
            with self.subTest(change=change):
                self.assertEqual(validate(generate(self.baseline["recipe"] | change)), [])

    def test_absent_desks_cannot_be_hidden_by_absent_contracts(self):
        key = "device/school-oak/teacher-001"
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != key]
        self.strip_contracts()
        self.assertFinding("school-endpoint-inventory")
        self.assertFinding("school-endpoint-placement")

    def test_wrong_endpoint_cohort_and_segment_fail(self):
        for field, value in (("cohort", "administration"), ("network", "students")):
            with self.subTest(field=field):
                self.setUp()
                self.objects["device/school-oak/teacher-001"]["meta"][field] = value
                self.strip_contracts()
                self.assertFinding("school-endpoint-placement")

    def test_planned_endpoint_channel_is_not_connected(self):
        self.disconnect("device/school-oak/teacher-001/if/eth0")
        self.strip_contracts()
        self.assertFinding("school-endpoint-path")

    def test_endpoint_cannot_use_another_floors_spare_port(self):
        port = "device/school-oak/teacher-009/if/eth0"
        cable = self.cable(port)
        free = "device/school-oak/access-01/if/GigabitEthernet1/0/24"
        for field, target in list(cable["refs"].items()):
            if target != port:
                cable["refs"][field] = free
        self.objects[free]["attrs"]["mode"] = "access"
        self.objects[free]["refs"]["untagged_vlan"] = "vlan/school-oak/staff"
        self.strip_contracts()
        self.assertFinding("school-endpoint-path")

    def test_actual_switch_ports_preserve_reserved_headroom(self):
        endpoints = {obj["key"] for obj in self.plan["objects"] if obj["kind"] == "device" and obj["meta"].get("endpoint")}
        ports = Counter()
        for obj in self.plan["objects"]:
            if obj["kind"] == "cable":
                ends = [self.objects[target] for target in obj["refs"].values()]
                if any(end["refs"].get("device") in endpoints for end in ends):
                    for end in ends:
                        parent = end["refs"].get("device")
                        if self.objects.get(parent, {}).get("refs", {}).get("role") == "role/access":
                            ports[parent] += 1
        full = next(key for key, count in ports.items() if count == 19)
        spare = next(key for key, count in ports.items() if count < 19 and
                     self.objects[key]["refs"]["location"] == self.objects[full]["refs"]["location"])
        cable = next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and any(
            target.startswith(spare + "/if/GigabitEthernet1/0/") for target in obj["refs"].values()))
        old = next(target for target in cable["refs"].values() if target.startswith(spare + "/"))
        free = f"{full}/if/GigabitEthernet1/0/20"
        self.objects[free]["attrs"]["mode"] = "access"
        self.objects[free]["refs"]["untagged_vlan"] = self.objects[old]["refs"]["untagged_vlan"]
        cable["refs"] = {field: free if target == old else target for field, target in cable["refs"].items()}
        self.strip_contracts()
        self.assertFinding("school-access-capacity")

    def test_missing_floor_uplink_fails_without_contract(self):
        self.disconnect("device/school-oak/idf-02-access-01/if/TenGigabitEthernet1/1/1")
        self.strip_contracts()
        self.assertFinding("school-uplink-path")

    def test_distribution_interconnect_is_required_without_contract(self):
        self.disconnect("device/school-oak/dist-a/if/Ethernet49/1")
        self.strip_contracts()
        self.assertFinding("school-distribution-interconnect")

    def test_school_geography_must_remain_one_district_without_contract(self):
        site = self.objects["site/school-oak"]
        city, state, zone = ("Detroit", "MI", "America/Detroit") if site["meta"]["geography"]["city"] != "Detroit" else ("Chicago", "IL", "America/Chicago")
        site["meta"]["geography"].update(city=city, state=state)
        site["attrs"]["time_zone"] = zone
        site["refs"]["region"] = f"region/school-validation/us/{state.lower()}"
        self.strip_contracts()
        self.assertFinding("school-geography")
        for field in ("physical_address", "time_zone"):
            with self.subTest(field=field):
                self.setUp()
                self.objects["site/school-oak"]["attrs"][field] = []
                self.strip_contracts()
                self.assertFinding("school-geography")

    def test_unaddressed_campus_gateway_fails(self):
        ports = {obj["key"] for obj in self.plan["objects"] if obj["kind"] == "interface" and
                 obj["attrs"].get("type") == "virtual" and obj["refs"].get("untagged_vlan") == "vlan/school-oak/students"}
        self.plan["objects"] = [obj for obj in self.plan["objects"] if not
                                (obj["kind"] == "ip_address" and obj["refs"].get("assigned_object") in ports)]
        self.strip_contracts()
        self.assertFinding("school-gateway-inventory")

    def test_room_capacity_and_identity_cannot_be_inflated(self):
        room = self.objects["location/school-oak/classroom-001"]
        room["meta"]["capacity"]["students"] = 400
        self.strip_contracts()
        self.assertFinding("school-room-capacity")
        self.setUp()
        self.objects["location/school-oak/classroom-009"]["meta"]["floor"] = 1
        self.strip_contracts()
        self.assertFinding("school-room-placement")

    def test_reported_wireless_demand_does_not_invent_people(self):
        contract = next(c for c in self.plan["contracts"] if c["kind"] == "school")
        contract["demand"]["wireless_students"] += contract["demand"]["wired_student_seats"]
        self.assertFinding("school-demand-report")

    def test_panel_outlet_must_serve_its_actual_endpoint_without_contracts(self):
        self.plan = generate(self.baseline["recipe"] | {"patching": "panels"})
        outlet = next(obj for obj in self.plan["objects"] if obj["kind"] == "device" and obj["meta"].get("purpose") == "wall-outlet")
        outlet["meta"]["serves_endpoint"] = "device/school-oak/teacher-009"
        self.strip_contracts()
        self.assertFinding("school-endpoint-patching")

    def test_planned_campus_power_and_single_panel_fail(self):
        self.disconnect("device/school-oak/dist-a/power/0")
        self.strip_contracts()
        self.assertFinding("dc-power-path")
        self.setUp()
        for obj in self.plan["objects"]:
            if obj["kind"] == "power_feed" and obj["refs"].get("power_panel") == "panel/school-oak/b":
                obj["refs"]["power_panel"] = "panel/school-oak/a"
        self.strip_contracts()
        self.assertFinding("dc-power-diversity")

    def test_purpose_or_endpoint_metadata_cannot_hide_infrastructure_power(self):
        for field, value in (("purpose", "wall-outlet"), ("endpoint", True)):
            with self.subTest(field=field):
                self.setUp()
                self.objects["device/school-oak/access-01"]["meta"][field] = value
                self.disconnect("device/school-oak/access-01/power/PS-1")
                self.strip_contracts()
                self.assertFinding("dc-power-path")

    def test_management_and_serial_are_required_without_contracts(self):
        for port, code in (("device/school-oak/access-01/if/GigabitEthernet0/0", "school-management-path"),
                           ("device/school-oak/mgmt-01/if/TenGigabitEthernet1/1/1", "school-management-uplink"),
                           ("device/school-oak/dist-a/console_port/Console", "school-console-path")):
            with self.subTest(port=port):
                self.setUp()
                self.disconnect(port)
                self.strip_contracts()
                self.assertFinding(code)

    def test_school_carrier_commitment_follows_recipe(self):
        self.objects["circuit/school-oak/a/1"]["attrs"]["commit_rate"] = 100000
        self.strip_contracts()
        self.assertFinding("school-wan-capacity")

    def test_absent_district_service_and_auth_listener_fail(self):
        self.plan["objects"] = [obj for obj in self.plan["objects"] if obj["key"] != "vm/dc-01/learning-portal/002"]
        self.strip_contracts()
        self.assertFinding("dc-workload-inventory")
        self.setUp()
        self.plan["objects"] = [obj for obj in self.plan["objects"] if not (obj["kind"] == "service" and obj["attrs"].get("name") == "radius")]
        self.strip_contracts()
        self.assertFinding("dc-workload-listener")

    def test_saved_recipe_is_bounded_before_inventory_expansion(self):
        for field, value in (("classrooms", 10**100), ("students_per_classroom", 37), ("administrative_staff", 49),
                             ("wired_seats_per_classroom", 13), ("key", []), ("wan_peak_mbps", 801)):
            with self.subTest(field=field):
                self.setUp()
                self.plan["recipe"]["schools"][0][field] = value
                self.assertFinding("school-recipe")
        for profile, code in (("school-district", "school-recipe"), ("enterprise-data-center", "dc-recipe")):
            with self.subTest(profile=profile):
                self.plan = generate({"profile": profile})
                self.plan["recipe"]["reserve_fraction"] = 10**1000
                self.assertFinding(code)


if __name__ == "__main__":
    unittest.main()
