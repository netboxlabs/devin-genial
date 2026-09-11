"""Clinical demand/path counterexamples independent of explanatory metadata."""

from collections import Counter
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.validate import validate


class HospitalValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(dict(profile="hospital-clinics", namespace="health-validation",
            hospitals=[dict(key="central", administrative_desks=13, imaging_rooms=2, wan_peak_mbps=400,
                            wards=[dict(key="medical-a", beds=8, clinical_desks=4), dict(key="medical-b", beds=6, clinical_desks=2)])],
            clinics=[dict(key="west", exam_rooms=9, administrative_desks=4, imaging_rooms=1, wan_peak_mbps=200)]))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {item["code"] for item in validate(self.plan)}

    def strip_explanations(self):
        self.plan["contracts"] = []
        for obj in self.plan["objects"]:
            obj["meta"] = {}

    def cable(self, port):
        return next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and port in obj["refs"].values())

    def disable(self, port):
        self.cable(port)["attrs"]["status"] = "planned"

    def test_clinical_baseline_and_panel_paths_are_valid(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(validate(generate(self.plan["recipe"] | {"patching": "panels"})), [])
        vms = {obj["key"].split("/")[2] for obj in self.plan["objects"] if obj["kind"] == "virtual_machine"}
        self.assertEqual(vms, {"identity", "dns", "clinical-records", "imaging-archive", "monitoring"})
        for key, role, network in (("monitor-medical-a-001", "medical-device", "medical"),
                                    ("nurse-medical-a-001", "workstation", "clinical"),
                                    ("imaging-01", "imaging-device", "imaging"),
                                    ("diagnostic-01", "workstation", "clinical"),
                                    ("admin-001", "workstation", "staff")):
            device = f"device/hospital-central/{key}"
            self.assertEqual(self.objects[device]["refs"]["role"], f"role/{role}")
            self.assertEqual(self.objects[f"{device}/if/eth0"]["refs"]["untagged_vlan"], f"vlan/hospital-central/{network}")

    def test_removed_care_endpoints_cannot_hide_behind_missing_explanations(self):
        for label in ("monitor-medical-a-001", "nurse-medical-a-001", "imaging-01", "diagnostic-01"):
            with self.subTest(label=label):
                self.setUp()
                self.plan["objects"].remove(self.objects[f"device/hospital-central/{label}"])
                self.strip_explanations()
                self.assertIn("hospital-endpoint-inventory", self.codes())
                self.assertIn("hospital-endpoint-placement", self.codes())

    def test_clinical_role_room_and_vlan_are_actual_obligations(self):
        device = "device/hospital-central/monitor-medical-a-001"
        for field, value in (("role", "role/workstation"), ("location", "location/hospital-central/admin-01")):
            with self.subTest(field=field):
                self.setUp()
                self.objects[device]["refs"][field] = value
                self.strip_explanations()
                self.assertIn("hospital-endpoint-placement", self.codes())
        self.setUp()
        self.objects[f"{device}/if/eth0"]["refs"]["untagged_vlan"] = "vlan/hospital-central/staff"
        self.strip_explanations()
        self.assertIn("hospital-endpoint-path", self.codes())

    def test_cameras_cannot_move_into_patient_exam_or_imaging_rooms(self):
        for camera, room in (("device/hospital-central/camera-ward-medical-a-a", "location/hospital-central/ward-medical-a/patient-01"),
                             ("device/clinic-west/camera-ground-a", "location/clinic-west/exam-001"),
                             ("device/hospital-central/camera-ground-a", "location/hospital-central/imaging-01")):
            with self.subTest(room=room):
                self.setUp()
                self.objects[camera]["refs"]["location"] = room
                self.strip_explanations()
                self.assertIn("hospital-camera-placement", self.codes())

    def test_room_presence_parent_capacity_and_geometry_are_checked(self):
        room = "location/hospital-central/ward-medical-a/patient-01"
        for field, value, code in (("parent", "location/hospital-central/floor-01", "hospital-room-placement"),
                                    ("capacity", {"bed_stations": 200}, "hospital-room-geometry"),
                                    ("position_m", [24, 18, 4], "hospital-room-geometry")):
            with self.subTest(field=field):
                self.setUp()
                self.objects[room]["refs" if field == "parent" else "meta"][field] = value
                self.plan["contracts"] = []
                self.assertIn(code, self.codes())
        self.setUp()
        self.plan["objects"].remove(self.objects[room])
        self.strip_explanations()
        self.assertIn("hospital-room-inventory", self.codes())

    def test_corrupt_ward_floor_ledger_cannot_disable_demand(self):
        for bad in ({}, {"medical-a": 0, "medical-b": 0}, {"medical-a": 0, "medical-b": 7}, []):
            with self.subTest(ledger=bad):
                self.setUp()
                self.plan["reservations"]["healthcare-wards/hospital-central"] = bad
                self.assertIn("hospital-ward-allocation", self.codes())

    def test_planned_and_disabled_endpoint_paths_fail_without_explanations(self):
        port = "device/clinic-west/exam-009/if/eth0"
        for mode in ("cable", "port"):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == "cable":
                    self.disable(port)
                else:
                    self.objects[port]["attrs"]["enabled"] = False
                self.strip_explanations()
                self.assertIn("hospital-endpoint-path", self.codes())

    def test_endpoint_cannot_borrow_another_floors_free_access_port(self):
        port = "device/clinic-west/exam-009/if/eth0"
        cable = self.cable(port)
        free = "device/clinic-west/access-01/if/GigabitEthernet1/0/24"
        cable["refs"] = {field: port if target == port else free for field, target in cable["refs"].items()}
        self.objects[free]["attrs"]["mode"] = "access"
        self.objects[free]["refs"]["untagged_vlan"] = "vlan/clinic-west/clinical"
        self.strip_explanations()
        self.assertIn("hospital-endpoint-path", self.codes())

    def test_false_short_copper_route_cannot_use_edited_geometry_as_authority(self):
        port = "device/hospital-central/nurse-medical-a-001/if/eth0"
        self.cable(port)["attrs"]["length"] = 3
        self.objects["device/hospital-central/nurse-medical-a-001"]["meta"]["placement"]["position_m"] = [24, 18, 4]
        self.strip_explanations()
        self.assertIn("hospital-endpoint-route", self.codes())

    def test_panel_outlet_must_stay_with_its_endpoint_room(self):
        self.plan = generate(self.plan["recipe"] | {"patching": "panels"})
        self.objects = {obj["key"]: obj for obj in self.plan["objects"]}
        outlet = self.objects["device/hospital-central/outlet-monitor-medical-a-001"]
        outlet["refs"]["location"] = "location/hospital-central/admin-01"
        self.strip_explanations()
        self.assertIn("hospital-endpoint-patching", self.codes())

    def test_access_pairs_and_port_reserve_cannot_be_faked(self):
        switch = "device/hospital-central/idf-02-access-01"
        self.plan["objects"].remove(self.objects[switch])
        self.strip_explanations()
        self.assertIn("hospital-access-inventory", self.codes())
        self.setUp()
        # The 13-desk administrative floor has more than 19 endpoints. Move its
        # second switch's channels to free first-switch ports until reserve breaks.
        first, second = "device/hospital-central/access-01", "device/hospital-central/access-02"
        endpoint_keys = {obj["key"] for obj in self.plan["objects"] if obj["kind"] == "device" and obj["refs"].get("device_type") in {"hardware/endpoint", "hardware/ap"}}
        counts = Counter()
        candidates = []
        occupied = set()
        for obj in self.plan["objects"]:
            if obj["kind"] != "cable":
                continue
            ends = list(obj["refs"].values())
            occupied.update(ends)
            if not any(self.objects[end]["refs"].get("device") in endpoint_keys for end in ends):
                continue
            for end in ends:
                owner = self.objects[end]["refs"].get("device")
                counts[owner] += 1
                if owner == second:
                    candidates.append((obj, end))
        for number in range(1, 25):
            free = f"{first}/if/GigabitEthernet1/0/{number}"
            if counts[first] >= 20:
                break
            if free in occupied:
                continue
            cable, old = candidates.pop()
            self.objects[free]["attrs"].update(mode=self.objects[old]["attrs"]["mode"])
            self.objects[free]["refs"].update({k: v for k, v in self.objects[old]["refs"].items() if k != "device"})
            cable["refs"] = {field: free if value == old else value for field, value in cable["refs"].items()}
            counts[first] += 1
        self.assertEqual(counts[first], 20)
        self.strip_explanations()
        self.assertIn("hospital-access-capacity", self.codes())

    def test_core_uplinks_peer_link_and_gateways_are_required(self):
        for port, code in (("device/hospital-central/idf-02-access-01/if/TenGigabitEthernet1/1/1", "hospital-uplink-path"),
                           ("device/hospital-central/dist-a/if/Ethernet49/1", "hospital-distribution-interconnect")):
            with self.subTest(port=port):
                self.setUp()
                self.disable(port)
                self.strip_explanations()
                self.assertIn(code, self.codes())
        self.setUp()
        ports = {obj["key"] for obj in self.plan["objects"] if obj["kind"] == "interface" and obj["attrs"].get("type") == "virtual" and
                 obj["refs"].get("untagged_vlan") == "vlan/hospital-central/medical"}
        self.plan["objects"] = [obj for obj in self.plan["objects"] if not (obj["kind"] == "ip_address" and obj["refs"].get("assigned_object") in ports)]
        self.strip_explanations()
        self.assertIn("hospital-gateway-inventory", self.codes())

    def test_management_console_and_power_paths_survive_metadata_stripping(self):
        for port, code in (("device/hospital-central/idf-02-access-01/if/GigabitEthernet0/0", "hospital-management-path"),
                           ("device/hospital-central/idf-02-mgmt-01/if/TenGigabitEthernet1/1/1", "hospital-management-uplink"),
                           ("device/hospital-central/dist-a/console_port/Console", "hospital-console-path"),
                           ("device/hospital-central/dist-a/power/0", "dc-power-path")):
            with self.subTest(port=port):
                self.setUp()
                self.disable(port)
                self.strip_explanations()
                self.assertIn(code, self.codes())
        self.setUp()
        for obj in self.plan["objects"]:
            if obj["kind"] == "power_feed" and obj["refs"].get("power_panel") == "panel/hospital-central/b":
                obj["refs"]["power_panel"] = "panel/hospital-central/a"
        self.strip_explanations()
        self.assertIn("dc-power-diversity", self.codes())

    def test_wan_rate_provider_and_path_follow_declared_facility_peak(self):
        for field, value in (("commit_rate", 100000), ("status", "planned")):
            with self.subTest(field=field):
                self.setUp()
                self.objects["circuit/hospital-central/a/1"]["attrs"][field] = value
                self.strip_explanations()
                self.assertIn("hospital-wan-capacity", self.codes())
        self.setUp()
        self.objects["circuit/hospital-central/a/1"]["refs"]["provider"] = "provider/b"
        self.strip_explanations()
        self.assertIn("hospital-wan-inventory", self.codes())

    def test_clinical_segment_prefix_is_not_a_staff_alias(self):
        self.objects["prefix/hospital-central/clinical"]["attrs"]["prefix"] = self.objects["prefix/hospital-central/staff"]["attrs"]["prefix"]
        self.strip_explanations()
        self.assertIn("hospital-prefix-policy", self.codes())

    def test_staff_wlan_requires_all_actual_aps_and_separate_management(self):
        device = "device/hospital-central/ap-ward-medical-a-01"
        for field, value in (("wireless_lans", ["wireless-lan/clinic-west/staff"]), ("untagged_vlan", "vlan/hospital-central/clinical")):
            with self.subTest(field=field):
                self.setUp()
                self.objects[f"{device}/if/wlan0"]["refs"][field] = value
                self.strip_explanations()
                self.assertIn("hospital-wireless-path", self.codes())
        self.setUp()
        self.objects[f"{device}/if/eth0"]["refs"]["tagged_vlans"] = []
        self.strip_explanations()
        self.assertIn("hospital-wireless-path", self.codes())

    def test_radio_channel_and_reference_clinical_prose_cannot_claim_other_capabilities(self):
        self.objects["device/hospital-central/ap-ward-medical-a-01/if/wlan0"]["attrs"]["rf_channel"] = "not-a-channel"
        self.strip_explanations()
        self.assertIn("hospital-wireless-path", self.codes())
        for label in ("monitor-medical-a-001", "imaging-01", "nurse-medical-a-001"):
            with self.subTest(label=label):
                self.setUp()
                self.objects[f"device/hospital-central/{label}"]["attrs"]["description"] = "FDA-certified life-support ventilator with guaranteed continuous patient monitoring."
                self.strip_explanations()
                self.assertIn("hospital-endpoint-description", self.codes())

    def test_shared_vlan_mask_check_covers_clinical_gateway_svis(self):
        for mask in (8, 32):
            with self.subTest(mask=mask):
                self.setUp()
                svi = next(obj["key"] for obj in self.plan["objects"] if obj["kind"] == "interface" and
                           obj["refs"].get("device") == "device/hospital-central/dist-a" and
                           obj["refs"].get("untagged_vlan") == "vlan/hospital-central/medical" and obj["attrs"].get("type") == "virtual")
                address = next(obj for obj in self.plan["objects"] if obj["kind"] == "ip_address" and obj["refs"].get("assigned_object") == svi)
                address["attrs"]["address"] = address["attrs"]["address"].split("/")[0] + f"/{mask}"
                self.plan["contracts"] = []
                self.assertIn("ip-vlan-mask", self.codes())

    def test_biomedical_contact_requires_actual_site_scope(self):
        assignment = "contact-assignment/device/hospital-central/monitor-medical-a-001"
        self.objects[assignment]["refs"]["contact"] = "contact/biomedical/site/clinic-west"
        self.strip_explanations()
        self.assertIn("operations-contact", self.codes())
        self.setUp()
        self.plan["objects"].remove(self.objects[assignment])
        self.strip_explanations()
        self.assertIn("operations-contact", self.codes())

    def test_clinical_service_inventory_resources_listeners_and_failure_domains(self):
        vm = "vm/dc-01/clinical-records/002"
        self.plan["objects"].remove(self.objects[vm])
        self.strip_explanations()
        self.assertIn("dc-workload-inventory", self.codes())
        for field, value in (("vcpus", 4), ("memory", 8192), ("disk", 100000)):
            with self.subTest(field=field):
                self.setUp()
                self.objects[vm]["attrs"][field] = value
                self.strip_explanations()
                self.assertIn("dc-workload-resources", self.codes())
        self.setUp()
        self.objects["service/vm/dc-01/imaging-archive/001"]["attrs"]["ports"] = [443]
        self.strip_explanations()
        self.assertIn("dc-workload-listener", self.codes())
        self.setUp()
        self.objects[vm]["refs"]["device"] = self.objects["vm/dc-01/clinical-records/001"]["refs"]["device"]
        self.strip_explanations()
        self.assertIn("dc-replica-diversity", self.codes())

    def test_saved_demand_bounds_precede_inventory_expansion(self):
        for field, value in (("beds", 10**100), ("clinical_desks", True), ("key", [])):
            with self.subTest(field=field):
                self.setUp()
                self.plan["recipe"]["hospitals"][0]["wards"][0][field] = value
                self.assertIn("hospital-recipe", self.codes())
        for field, value in (("exam_rooms", 25), ("wan_peak_mbps", 801), ("administrative_desks", -1), ("imaging_rooms", 5)):
            with self.subTest(field=field):
                self.setUp()
                self.plan["recipe"]["clinics"][0][field] = value
                self.assertIn("hospital-recipe", self.codes())
        self.setUp()
        self.plan["recipe"]["reserve_fraction"] = 10**1000
        self.assertIn("hospital-recipe", self.codes())

    def test_actual_geography_and_functional_groups_cannot_disagree(self):
        for site in ("site/hospital-central", "site/clinic-west", "site/dc-01"):
            with self.subTest(site=site):
                self.setUp()
                node = self.objects[site]
                region = "region/health-validation/us/mi" if not node["refs"]["region"].endswith("/mi") else "region/health-validation/us/il"
                node["refs"]["region"] = region
                self.plan["contracts"] = []
                self.assertIn("hospital-geography", self.codes())
        self.setUp()
        self.objects["site/hospital-central"]["refs"]["group"] = "site-group/health-validation/clinic"
        self.strip_explanations()
        self.assertIn("hospital-site-group", self.codes())

    def test_duplicate_and_malformed_addresses_and_distant_metro_fail(self):
        for field, value in (("physical_address", []), ("time_zone", []),
                             ("physical_address", self.objects["site/hospital-central"]["attrs"]["physical_address"])):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.objects["site/clinic-west"]["attrs"][field] = value
                self.plan["contracts"] = []
                self.assertIn("hospital-geography", self.codes())
        self.setUp()
        node = self.objects["site/clinic-west"]
        city, state, state_name, zone = ("Detroit", "MI", "Michigan", "America/Detroit") if node["meta"]["geography"]["city"] != "Detroit" else ("Chicago", "IL", "Illinois", "America/Chicago")
        node["attrs"].update(physical_address=f"200 Community Way\n{city}, {state_name}\nUnited States", time_zone=zone)
        node["refs"]["region"] = f"region/health-validation/us/{state.lower()}"
        node["meta"]["geography"].update(city=city, state=state)
        self.plan["contracts"] = []
        self.assertIn("hospital-geography", self.codes())

    def test_saved_pool_requires_rfc1918_not_other_private_classification(self):
        for pool in ("127.0.0.0/8", "0.0.0.0/8", "169.254.0.0/16", "198.18.0.0/15"):
            with self.subTest(pool=pool):
                self.setUp()
                self.plan["recipe"]["address_pool"] = pool
                self.assertIn("hospital-address-allocation", self.codes())


if __name__ == "__main__":
    unittest.main()
