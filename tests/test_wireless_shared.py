"""Shared WLAN membership on generated radios and actual wired paths."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from estates import networking
from estates.generate import generate
from estates.model import DesignError
from estates.validate_networking import validate
from estates.validate import validate as validate_all


def before_wireless(recipe):
    captured = []
    build = networking.wireless

    def capture(world, sites, **options):
        captured.append((deepcopy(world), [site["key"] for site in sites], options))
        return build(world, sites, **options)

    with patch.object(networking, "wireless", side_effect=capture):
        generate(recipe)
    return captured[0]


class SharedWirelessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worlds = {
            "school": before_wireless(dict(profile="school-district", schools=[
                dict(key="oak", classrooms=2, administrative_staff=0, lab_seats=0),
                dict(key="pine", classrooms=1, administrative_staff=0, lab_seats=0)])),
            "hospital": before_wireless(dict(profile="hospital-clinics", patching="panels",
                hospitals=[dict(key="central", administrative_desks=0, imaging_rooms=0,
                                wards=[dict(key="medical", beds=4, clinical_desks=2)])],
                clinics=[dict(key="west", exam_rooms=4, administrative_desks=0, imaging_rooms=0)])),
            "bank": before_wireless(dict(branches={"small": 1}, headquarters=0)),
        }

    def world(self, profile):
        world, keys, options = deepcopy(self.worlds[profile])
        options.pop("guest_sites", None)
        return world, [world.obj(key) for key in keys], options

    def add_guest_vlan(self, world, site):
        sid = site["key"].split("/", 1)[1]
        source = world.obj(f"vlan/{sid}/wireless")
        return world.add("vlan", f"vlan/{sid}/guest",
                         source["attrs"] | {"name": f"{sid}-guest", "vid": 3905},
                         deepcopy(source["refs"]))

    def test_guest_shares_existing_radio_and_preserves_every_baseline_channel_and_group(self):
        for profile in self.worlds:
            with self.subTest(profile=profile):
                before, sites, options = self.world(profile)
                selected = next(site for site in sites if any(obj["kind"] == "device"
                    and obj["refs"].get("role") == "role/ap" and obj["refs"].get("site") == site["key"]
                    for obj in before.objects.values()))
                guest_vlan = self.add_guest_vlan(before, selected)
                after = deepcopy(before)
                networking.wireless(before, sites, **options)
                networking.wireless(after, sites, **options, guest_sites=[selected["key"]])
                guest_key = f"wireless-lan/{selected['key'].split('/', 1)[1]}/guest"
                self.assertEqual(after.objects.keys() - before.objects.keys(), {guest_key})
                guest = after.obj(guest_key)
                self.assertEqual(guest["attrs"]["auth_type"], "open")
                self.assertNotIn("auth_cipher", guest["attrs"])
                self.assertNotIn("auth_psk", guest["attrs"])
                self.assertIn("no captive portal", guest["attrs"]["comments"])
                self.assertEqual(guest["refs"]["vlan"], guest_vlan)
                self.assertEqual(guest["refs"]["scope_site"], selected["key"])
                peers = networking._physical_peers(after.objects)
                for key, obj in before.objects.items():
                    if obj["kind"] in {"wireless_lan", "wireless_lan_group", "wireless_link"}:
                        self.assertEqual(obj, after.obj(key), key)
                    if obj["kind"] != "interface" or not str(obj["attrs"].get("type", "")).startswith("ieee802.11"):
                        continue
                    actual = after.obj(key)
                    self.assertEqual(obj["attrs"], actual["attrs"], key)
                    device = after.obj(obj["refs"]["device"])
                    if device["refs"]["site"] == selected["key"] and obj["attrs"]["name"] == "wlan0":
                        self.assertEqual(actual["refs"]["wireless_lans"], sorted(obj["refs"]["wireless_lans"] + [guest_key]))
                        ethernet = f"{device['key']}/if/eth0"
                        for port in (ethernet, peers[ethernet]):
                            self.assertEqual(set(after.obj(port)["refs"]["tagged_vlans"]),
                                             set(before.obj(port)["refs"]["tagged_vlans"]) | {guest_vlan})
                            self.assertEqual(after.obj(port)["refs"]["untagged_vlan"],
                                             before.obj(port)["refs"]["untagged_vlan"])
                    else:
                        self.assertEqual(obj, actual, key)
                    if actual["refs"].get("wireless_lans"):
                        self.assertNotIn("mode", actual["attrs"])
                        self.assertFalse({"tagged_vlans", "untagged_vlan"} & actual["refs"].keys())

    def test_multiple_base_labels_on_one_radio_do_not_reassign_the_other_radio_channel(self):
        world, sites, options = self.world("school")
        baseline = deepcopy(world)
        networking.wireless(baseline, sites, **options)
        roles = (("staff", "staff", "wlan0"), ("staff-extra", "staff", "wlan0"),
                 ("students", "students", "wlan1"))
        networking.wireless(world, sites, **(options | {"lan_roles": roles}))
        for obj in world.objects.values():
            if obj["kind"] == "interface" and obj["attrs"].get("name") in {"wlan0", "wlan1"}:
                self.assertEqual(obj["attrs"], baseline.obj(obj["key"])["attrs"])
                self.assertEqual(len(obj["refs"]["wireless_lans"]), 2 if obj["attrs"]["name"] == "wlan0" else 1)

    def test_missing_guest_vlan_fails_before_any_mutation(self):
        world, sites, options = self.world("hospital")
        before = deepcopy(world.objects)
        with self.assertRaisesRegex(DesignError, "guest WLAN requires its local guest VLAN"):
            networking.wireless(world, sites, **options, guest_sites=[sites[-1]["key"]])
        self.assertEqual(world.objects, before)

    def test_guest_sites_must_be_supplied_canonical_sites_with_aps(self):
        for guests in (["site/missing"], ["site/dc-01"], ["hospital-central"],
                       "site/hospital-central", [None], None):
            with self.subTest(guests=guests):
                world, sites, options = self.world("hospital")
                before = deepcopy(world.objects)
                with self.assertRaisesRegex(DesignError, "guest_sites"):
                    networking.wireless(world, sites, **options, guest_sites=guests)
                self.assertEqual(world.objects, before)
        world, sites, options = self.world("hospital")
        with self.assertRaisesRegex(DesignError, "guest_sites"):
            networking.wireless(world, [], **options, guest_sites=[sites[0]["key"]])

    def test_duplicate_labels_and_nonexistent_radios_fail_before_mutation(self):
        cases = [((("staff", "staff", "wlan0"), ("staff", "students", "wlan1")), "distinct labels"),
                 ((("staff", "staff", "wlan2"),), "existing wlan2 radio"),
                 ((("staff", "staff", "eth0"),), "existing eth0 radio")]
        for roles, message in cases:
            with self.subTest(roles=roles):
                world, sites, options = self.world("school")
                before = deepcopy(world.objects)
                with self.assertRaisesRegex(DesignError, message):
                    networking.wireless(world, sites, **(options | {"lan_roles": roles}))
                self.assertEqual(world.objects, before)

    def test_guest_label_collision_and_broken_wired_path_fail_before_mutation(self):
        world, sites, options = self.world("hospital")
        self.add_guest_vlan(world, sites[0])
        before = deepcopy(world.objects)
        with self.assertRaisesRegex(DesignError, "distinct labels"):
            networking.wireless(world, sites, **(options | {"lan_roles": (("guest", "staff", "wlan0"),)}),
                                guest_sites=[sites[0]["key"]])
        self.assertEqual(world.objects, before)
        ethernet = next(obj["key"] for obj in world.objects.values() if obj["kind"] == "interface"
            and obj["attrs"]["name"] == "eth0" and world.obj(obj["refs"]["device"])["refs"].get("role") == "role/ap")
        cable = next(obj for obj in world.objects.values() if obj["kind"] == "cable"
                     and ethernet in (obj["refs"]["a"], obj["refs"]["b"]))
        del world.objects[cable["key"]]
        before = deepcopy(world.objects)
        with self.assertRaisesRegex(DesignError, "real wired access path"):
            networking.wireless(world, sites, **options)
        self.assertEqual(world.objects, before)

    def test_hospital_guest_policy_follows_actual_resolved_facility_demand(self):
        recipe = deepcopy(self.worlds["hospital"][0].recipe)
        recipe["clinics"][0]["wireless"]["reception"]["guest"] = 12
        plan = generate(recipe)
        plan["contracts"] = []
        self.assertEqual(validate(plan), [])
        objects = {obj["key"]: obj for obj in plan["objects"]}
        guest = objects["wireless-lan/clinic-west/guest"]
        self.assertEqual(objects[guest["refs"]["group"]]["refs"]["parent"], "wireless-group/staff")
        guest["attrs"]["auth_cipher"] = "aes"
        self.assertIn("wireless-lan-scope", {finding["code"] for finding in validate(plan)})
        del guest["attrs"]["auth_cipher"]
        plan["recipe"]["clinics"][0]["wireless"]["reception"]["guest"] = 0
        self.assertIn("wireless-lan-scope", {finding["code"] for finding in validate(plan)})

    def test_all_profiles_reject_missing_wrong_band_and_unauthored_serving_channels(self):
        for profile, (world, _, _) in self.worlds.items():
            baseline = generate(world.recipe)
            unchanged = deepcopy(baseline)
            self.assertEqual(validate_all(baseline), [])
            self.assertEqual(baseline, unchanged)
            radio_key = next(obj["key"] for obj in baseline["objects"] if obj["kind"] == "interface"
                             and obj["attrs"].get("name") == "wlan0" and obj["refs"].get("wireless_lans"))
            for channel in (None, "", "2g-1-2412-22", "5g-40-5200-20"):
                with self.subTest(profile=profile, channel=channel):
                    plan = deepcopy(baseline)
                    plan["contracts"] = []
                    objects = {obj["key"]: obj for obj in plan["objects"]}
                    for obj in objects.values():
                        obj["meta"] = {}
                    if channel is None:
                        objects[radio_key]["attrs"].pop("rf_channel")
                    else:
                        objects[radio_key]["attrs"]["rf_channel"] = channel
                    self.assertIn(("wireless-radio-band", radio_key),
                                  {(finding["code"], finding["object"]) for finding in validate_all(plan)})

    def test_radio_model_and_band_come_from_actual_type_and_catalog_without_metadata(self):
        world = self.worlds["school"][0]
        plan = generate(world.recipe)
        plan["contracts"] = []
        objects = {obj["key"]: obj for obj in plan["objects"]}
        for obj in objects.values():
            obj["meta"] = {}
        radio = next(obj for obj in objects.values() if obj["kind"] == "interface" and obj["attrs"].get("name") == "wlan0")
        device = objects[radio["refs"]["device"]]
        actual_type = objects[device["refs"]["device_type"]]
        catalog = deepcopy(world.catalog)
        self.assertEqual(validate(plan, catalog), [])
        self.assertEqual(validate(plan, catalog["models"]), [])
        model = next(model for model in catalog["models"].values() if model["model"] == actual_type["attrs"]["model"])
        model["radio_bands"]["wlan0"] = "2g"
        self.assertIn(("wireless-radio-band", radio["key"]),
                      {(finding["code"], finding["object"]) for finding in validate(plan, catalog)})
        for field, value in (("device_type", "hardware/endpoint"), ("device_type", "hardware/missing")):
            changed = deepcopy(plan)
            next(obj for obj in changed["objects"] if obj["key"] == device["key"])["refs"][field] = value
            self.assertIn(("wireless-radio-band", radio["key"]),
                          {(finding["code"], finding["object"]) for finding in validate(changed)})
        actual_type["attrs"]["model"] = "Unreviewed AP model"
        self.assertIn(("wireless-radio-band", radio["key"]),
                      {(finding["code"], finding["object"]) for finding in validate(plan)})

    def test_matching_diagnostic_channels_cannot_bypass_actual_band_or_channel_policy(self):
        baseline = generate(self.worlds["bank"][0].recipe)
        link = next(obj for obj in baseline["objects"] if obj["kind"] == "wireless_link")
        ports = [link["refs"][field] for field in ("interface_a", "interface_b")]
        for channel in (None, "2g-1-2412-22", "5g-36-5180-20"):
            with self.subTest(channel=channel):
                plan = deepcopy(baseline)
                plan["contracts"] = []
                for obj in plan["objects"]:
                    obj["meta"] = {}
                    if obj["key"] in ports:
                        if channel is None:
                            obj["attrs"].pop("rf_channel")
                        else:
                            obj["attrs"]["rf_channel"] = channel
                findings = {(finding["code"], finding["object"]) for finding in validate_all(plan)}
                self.assertTrue({("wireless-radio-band", port) for port in ports} <= findings)


if __name__ == "__main__":
    unittest.main()
