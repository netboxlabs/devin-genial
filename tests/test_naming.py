"""Short display labels retain scoped identity and stable physical references."""

import unittest
from copy import deepcopy

from estates.bank import generate
from estates.generate import generate as generate_profile
from estates.validate import validate


class NamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"namespace": "abcdefghijklmnopqrst", "patching": "panels",
                             "headquarters": 0, "branches": {"small": 3},
                             "site_designs": {"br-s0001": "modern", "br-s0002": "inherited", "br-s0003": "refreshed"}})
        cls.objects = {o["key"]: o for o in cls.plan["objects"]}

    def test_compact_names_preserve_lineage_without_duplicated_asset_tags(self):
        for key, name in {"device/br-s0001/atm-001": "s0001-atm01",
                          "device/br-s0002/access-01": "birch-s0002-as01",
                          "device/br-s0002/outlet-atm-001": "birch-s0002-jack-atm01",
                          "device/br-s0003/access-r01": "s0003-asr01"}.items():
            self.assertEqual(self.objects[key]["attrs"]["name"], name)
        devices = [o for o in self.plan["objects"] if o["kind"] == "device"]
        self.assertTrue(all("asset_tag" not in o["attrs"] and o["attrs"]["serial"] for o in devices))
        self.assertLessEqual(max(len(o["attrs"]["name"]) for o in devices), 32)

    def test_dns_retains_full_namespace_and_endpoint_owner(self):
        device = self.objects["device/br-s0002/atm-001"]
        ip = self.objects[device["refs"]["primary_ip4"]]
        self.assertEqual(ip["attrs"]["dns_name"], "birch-s0002-atm01.abcdefghijklmnopqrst.example")

    def test_panel_labels_fit_without_losing_route_descriptions(self):
        cables = [o for o in self.plan["objects"] if o["kind"] == "cable"]
        self.assertLessEqual(max(len(o["attrs"]["label"]) for o in cables), 20)
        path = {o["attrs"]["label"]: o for o in cables if o["attrs"]["label"].startswith("s0002-atm01-")}
        self.assertEqual(set(path), {"s0002-atm01-P", "s0002-atm01-H", "s0002-atm01-R"})
        self.assertTrue(all(o["attrs"]["description"] for o in path.values()))

    def test_long_rack_tags_fit_native_limit_and_keep_room_identity_on_growth(self):
        recipe = dict(profile="hospital-clinics", namespace="abcdefghijklmnopqrst",
                      hospitals=[dict(key="abcdefghijklmnopqrst", administrative_desks=0,
                                      imaging_rooms=0, wards=[dict(key="first", beds=4, clinical_desks=2)])],
                      clinics=[])
        before = generate_profile(recipe)
        recipe["hospitals"][0]["wards"].append(dict(key="second", beds=4, clinical_desks=2))
        after = generate_profile(recipe, previous=before)
        self.assertEqual(validate(after), [])
        tags = [{o["key"]: o["attrs"]["asset_tag"] for o in plan["objects"] if o["kind"] == "rack"}
                for plan in (before, after)]
        self.assertTrue(any(len(tag) == 50 for tag in tags[0].values()))
        self.assertTrue(all(len(tag) <= 50 for tag in tags[1].values()))
        self.assertEqual(len(tags[1]), len(set(tags[1].values())))
        self.assertTrue(all(tags[1][key] == tag for key, tag in tags[0].items()))
        for rack in (o for o in self.plan["objects"] if o["kind"] == "rack"):
            self.assertEqual(rack["attrs"]["asset_tag"], "abcdefghijklmnopqrst-" + rack["key"].removeprefix("rack/").replace("/", "-"))

    def test_native_rack_tag_limit_and_global_uniqueness_ignore_contracts(self):
        racks = [{"key": "rack/a", "kind": "rack", "attrs": {"name": "A", "asset_tag": "x" * 50}},
                 {"key": "rack/b", "kind": "rack", "attrs": {"name": "B", "asset_tag": "y" * 50}}]
        plan = {"objects": racks}
        self.assertFalse(any(f["code"] == "rack-asset-tag" for f in validate(plan)))
        for value in ("z" * 51, "x" * 50, 1):
            bad = deepcopy(plan)
            bad["objects"][1]["attrs"]["asset_tag"] = value
            self.assertIn(("rack-asset-tag", "rack/b"), {(f["code"], f["object"]) for f in validate(bad)})


if __name__ == "__main__":
    unittest.main()
