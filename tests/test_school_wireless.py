"""School WLAN checks follow actual radios and both campus client segments."""

from copy import deepcopy
import unittest

from estates.generate import generate
from estates.validate_networking import validate


class SchoolWirelessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"profile": "school-district", "namespace": "wlan-check",
                             "schools": [{"key": "oak", "classrooms": 2, "students_per_classroom": 12,
                                          "administrative_staff": 4, "lab_seats": 8},
                                         {"key": "pine", "classrooms": 1, "students_per_classroom": 8,
                                          "administrative_staff": 0, "lab_seats": 0}]})
        recipe = deepcopy(cls.plan["recipe"])
        recipe["schools"][0]["wireless"]["classroom-001"]["guest"] = 12
        cls.guest_plan = generate(recipe)

    def mutation(self, edit, code, source=None):
        plan = deepcopy(self.plan if source is None else source)
        plan["contracts"] = []
        objects = {obj["key"]: obj for obj in plan["objects"]}
        edit(objects, plan)
        self.assertIn(code, {finding["code"] for finding in validate(plan)})

    def test_two_client_lans_have_real_separate_management_and_no_diagnostic_hop(self):
        self.assertEqual(validate(self.plan), [])
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        wlans = [obj for obj in objects.values() if obj["kind"] == "wireless_lan"]
        self.assertEqual(len(wlans), 4)
        self.assertEqual({obj["attrs"]["ssid"] for obj in wlans}, {"wlan-check-staff", "wlan-check-students"})
        self.assertEqual(len({obj["refs"]["group"] for obj in wlans}), 2)
        self.assertFalse(any(obj["kind"] == "wireless_link" for obj in objects.values()))
        for ap in [obj for obj in objects.values() if obj["kind"] == "device" and obj["refs"].get("role") == "role/ap"]:
            sid = ap["refs"]["site"].split("/", 1)[1]
            eth = objects[f"{ap['key']}/if/eth0"]
            self.assertEqual(eth["refs"]["untagged_vlan"], f"vlan/{sid}/wireless")
            self.assertEqual(set(eth["refs"]["tagged_vlans"]), {f"vlan/{sid}/staff", f"vlan/{sid}/students"})
            radios = [objects[f"{ap['key']}/if/wlan{i}"] for i in range(2)]
            self.assertNotEqual(radios[0]["attrs"]["rf_channel"], radios[1]["attrs"]["rf_channel"])

    def test_growing_classrooms_admin_pods_and_lab_keeps_existing_radio_channels(self):
        recipe = deepcopy(self.plan["recipe"])
        recipe["schools"][0].update(classrooms=3, administrative_staff=13, lab_seats=12)
        grown = generate(recipe, previous=self.plan)
        self.assertEqual(validate(grown), [])
        objects = {obj["key"]: obj for obj in grown["objects"]}
        for obj in self.plan["objects"]:
            if obj["kind"] == "interface" and obj["attrs"].get("name") in {"wlan0", "wlan1"}:
                self.assertEqual(obj, objects[obj["key"]], obj["key"])

    def test_student_tag_cannot_disappear_from_either_ap_wire_end(self):
        ap = next(obj for obj in self.plan["objects"] if obj["kind"] == "device" and obj["refs"].get("role") == "role/ap")
        ethernet = f"{ap['key']}/if/eth0"
        cable = next(obj for obj in self.plan["objects"] if obj["kind"] == "cable" and ethernet in (obj["refs"]["a"], obj["refs"]["b"]))
        for port in (cable["refs"]["a"], cable["refs"]["b"]):
            with self.subTest(port=port):
                self.mutation(lambda o, p: o[port]["refs"].update(tagged_vlans=[tag for tag in o[port]["refs"]["tagged_vlans"] if not tag.endswith("/students")]), "wireless-uplink")

    def test_student_tag_cannot_disappear_from_campus_backbone(self):
        objects = {obj["key"]: obj for obj in self.plan["objects"]}
        upstream = next(obj for obj in objects.values() if obj["kind"] == "interface"
                        and objects[obj["refs"]["device"]]["refs"].get("role") == "role/access"
                        and obj["attrs"].get("mode") == "tagged" and len(obj["refs"].get("tagged_vlans", [])) >= 5)
        self.mutation(lambda o, p: o[upstream["key"]]["refs"].update(tagged_vlans=[tag for tag in upstream["refs"]["tagged_vlans"] if not tag.endswith("/students")]), "school-wireless-trunk")

    def test_missing_wlan_family_is_not_hidden_by_absent_contracts(self):
        self.mutation(lambda o, p: p["objects"].__setitem__(slice(None), [obj for obj in p["objects"] if obj["kind"] != "wireless_lan"]), "school-wireless-lans")

    def test_every_ap_must_serve_both_lans(self):
        radio = next(obj for obj in self.plan["objects"] if obj["kind"] == "interface" and obj["attrs"].get("name") == "wlan1")
        self.mutation(lambda o, p: o[radio["key"]]["refs"].update(wireless_lans=[]), "school-wireless-radio")
        self.mutation(lambda o, p: o[radio["key"]]["attrs"].update(enabled=False), "wireless-radio")

    def test_radio_vlan_fields_and_duplicate_memberships_are_rejected(self):
        radio = next(obj for obj in self.plan["objects"] if obj["kind"] == "interface" and obj["attrs"].get("name") == "wlan0")
        wlan = radio["refs"]["wireless_lans"][0]
        self.mutation(lambda o, p: o[radio["key"]]["attrs"].update(mode="access"), "wireless-radio")
        self.mutation(lambda o, p: o[radio["key"]]["refs"].update(untagged_vlan=o[wlan]["refs"]["vlan"]), "wireless-radio")
        self.mutation(lambda o, p: o[radio["key"]]["refs"].update(tagged_vlans=[]), "wireless-radio")
        self.mutation(lambda o, p: o[radio["key"]]["refs"].update(wireless_lans=[wlan, wlan]), "wireless-radio")

    def test_wlan_radio_must_belong_to_an_actual_ap(self):
        radio = next(obj for obj in self.plan["objects"] if obj["kind"] == "interface" and obj["attrs"].get("name") == "wlan0")
        self.mutation(lambda o, p: o[radio["refs"]["device"]]["refs"].update(role="role/workstation"), "wireless-radio")

    def test_managed_authentication_policy_cannot_become_open(self):
        wlan = next(obj for obj in self.plan["objects"] if obj["kind"] == "wireless_lan")
        self.mutation(lambda o, p: o[wlan["key"]]["attrs"].update(auth_type="open"), "wireless-lan-scope")

    def test_requested_guest_uses_staff_radio_and_preserves_scoped_group(self):
        self.assertEqual(validate(self.guest_plan), [])
        objects = {obj["key"]: obj for obj in self.guest_plan["objects"]}
        guest = objects["wireless-lan/school-oak/guest"]
        self.assertEqual(guest["attrs"]["auth_type"], "open")
        self.assertEqual(guest["refs"]["vlan"], "vlan/school-oak/guest")
        self.assertEqual(objects[guest["refs"]["group"]]["refs"]["parent"], "wireless-group/campus")
        for obj in objects.values():
            if obj["kind"] == "interface" and obj["attrs"].get("name") in {"wlan0", "wlan1"}:
                sid = objects[obj["refs"]["device"]]["refs"]["site"].split("/", 1)[1]
                expected = [f"wireless-lan/{sid}/staff"] if obj["attrs"]["name"] == "wlan0" else [f"wireless-lan/{sid}/students"]
                if sid == "school-oak" and obj["attrs"]["name"] == "wlan0":
                    expected.append(guest["key"])
                self.assertEqual(obj["refs"]["wireless_lans"], sorted(expected))

    def test_guest_auth_vlan_and_requested_demand_cannot_be_faked(self):
        guest = "wireless-lan/school-oak/guest"
        for extra in ({"auth_type": "wpa-enterprise"}, {"auth_cipher": "aes"}, {"auth_psk": "example-only"}):
            with self.subTest(extra=extra):
                self.mutation(lambda o, p: o[guest]["attrs"].update(extra), "wireless-lan-scope", self.guest_plan)
        self.mutation(lambda o, p: o[guest]["refs"].update(vlan="vlan/school-oak/staff"), "wireless-lan-scope", self.guest_plan)
        self.mutation(lambda o, p: p["recipe"]["schools"][0]["wireless"]["classroom-001"].update(guest=0), "school-wireless-lans", self.guest_plan)

    def test_guest_membership_is_exact_and_requires_wired_path_and_gateway(self):
        objects = {obj["key"]: obj for obj in self.guest_plan["objects"]}
        radio = next(obj for obj in objects.values() if obj["kind"] == "interface"
                     and "wireless-lan/school-oak/guest" in obj["refs"].get("wireless_lans", []))
        self.mutation(lambda o, p: o[radio["key"]]["refs"].update(wireless_lans=["wireless-lan/school-oak/staff"]), "school-wireless-radio", self.guest_plan)
        second = f"{radio['refs']['device']}/if/wlan1"
        self.mutation(lambda o, p: o[second]["refs"]["wireless_lans"].append("wireless-lan/school-oak/guest"), "school-wireless-radio", self.guest_plan)
        ethernet = f"{radio['refs']['device']}/if/eth0"
        self.mutation(lambda o, p: o[ethernet]["refs"]["tagged_vlans"].remove("vlan/school-oak/guest"), "wireless-uplink", self.guest_plan)
        trunk = next(obj["key"] for obj in objects.values() if obj["kind"] == "interface"
                     and objects[obj["refs"]["device"]]["refs"].get("role") == "role/access"
                     and len(obj["refs"].get("tagged_vlans", [])) >= 6 and "vlan/school-oak/guest" in obj["refs"]["tagged_vlans"])
        self.mutation(lambda o, p: o[trunk]["refs"]["tagged_vlans"].remove("vlan/school-oak/guest"), "school-wireless-trunk", self.guest_plan)

        def disable_gateways(objects, plan):
            for obj in objects.values():
                if obj["kind"] == "interface" and obj["attrs"].get("type") == "virtual" and obj["refs"].get("untagged_vlan") == "vlan/school-oak/guest":
                    obj["attrs"]["enabled"] = False
        self.mutation(disable_gateways, "school-wireless-gateway", self.guest_plan)

    def test_native_ap_management_cannot_become_student_segment(self):
        radio = next(obj for obj in self.plan["objects"] if obj["kind"] == "interface" and obj["attrs"].get("name") == "wlan1")
        ethernet = f"{radio['refs']['device']}/if/eth0"
        wlan = radio["refs"]["wireless_lans"][0]
        self.mutation(lambda o, p: o[ethernet]["refs"].update(untagged_vlan=o[wlan]["refs"]["vlan"]), "school-wireless-management")

    def test_shared_ssid_groups_cannot_merge_school_identity(self):
        self.mutation(lambda o, p: o["wireless-lan/school-pine/staff"]["refs"].update(group=o["wireless-lan/school-oak/staff"]["refs"]["group"]), "school-wireless-group")

    def test_student_gateway_must_be_enabled_and_addressed(self):
        def remove_gateways(objects, plan):
            for obj in objects.values():
                if obj["kind"] == "interface" and obj["attrs"].get("type") == "virtual" and str(obj["refs"].get("untagged_vlan", "")).endswith("/students"):
                    obj["attrs"]["enabled"] = False
        self.mutation(remove_gateways, "school-wireless-gateway")


if __name__ == "__main__":
    unittest.main()
