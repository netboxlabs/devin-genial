"""Finite geography/placement contracts, independent of hardware generation."""

import math
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from estates.model import DesignError, World
from estates.places import arrange, foundation, locate, place_endpoint


def sample(site_id="br-s0001", kind="branch", demand=None, seed=42, tenant="tenant", design="modern"):
    w = World({"seed": seed})
    foundation(w)
    key = f"site/{site_id}"
    w.add("site", key, {"name": f"cedar-{site_id}"}, {"tenant": tenant})
    site = SimpleNamespace(w=w, id=site_id, key=key, name=f"cedar-{site_id}", tenant=tenant,
                           design=design, contract={"kind": kind, "assumptions": []})
    locate(site)
    if demand:
        arrange(site, demand)
        for role, field in (("workstation", "workstations"), ("atm", "atms"), ("ap", "aps"), ("camera", "cameras")):
            for ordinal in range(1, demand[field] + 1):
                endpoint = f"device/{site_id}/{role}-{ordinal}"
                w.add("device", endpoint, {"name": endpoint}, {"site": key, "tenant": tenant})
                place_endpoint(site, endpoint, role, ordinal)
    return site


class PlacesTests(unittest.TestCase):
    def test_geography_does_not_depend_on_seed_growth_or_design(self):
        first = sample()
        other = sample(seed=99, design="inherited")
        self.assertEqual(first.w.objects, other.w.objects)
        initial = first.w.obj(first.key)
        self.assertNotIn("latitude", initial["attrs"])
        self.assertNotIn("longitude", initial["attrs"])
        self.assertTrue(initial["meta"]["geography"]["synthetic"])
        self.assertTrue(initial["attrs"]["physical_address"].endswith("United States"))
        ZoneInfo(initial["attrs"]["time_zone"])
        dc1, dc2 = sample("dc-01", "dc"), sample("dc-02", "dc")
        self.assertNotEqual(dc1.w.obj(dc1.key)["refs"]["region"], dc2.w.obj(dc2.key)["refs"]["region"])
        self.assertTrue(all(o["attrs"]["name"].startswith("cedar ") for o in first.w.objects.values()
                            if o["kind"] in {"region", "site_group"}))

    def test_rooms_are_owned_hierarchical_and_proportional_to_demand(self):
        for staff in (12, 36, 84):
            with self.subTest(staff=staff):
                site = sample(demand={"workstations": staff, "atms": 2, "aps": 2, "cameras": 2})
                offices = [o for o in site.w.objects.values() if o["meta"].get("space_type") == "office"]
                self.assertEqual(len(offices), math.ceil((staff - 4) / 12))
                self.assertEqual(site.equipment_location, "location/br-s0001")
                for obj in site.w.objects.values():
                    if obj["kind"] == "location":
                        self.assertEqual(obj["refs"]["site"], site.key)
                        self.assertEqual(obj["refs"]["tenant"], site.tenant)
                        visited, current = set(), obj
                        while "parent" in current["refs"]:
                            self.assertNotIn(current["key"], visited)
                            visited.add(current["key"])
                            current = site.w.obj(current["refs"]["parent"])
                            self.assertEqual(current["refs"]["site"], site.key)
                for obj in site.w.objects.values():
                    if obj["kind"] != "device":
                        continue
                    room = site.w.obj(obj["refs"]["location"])
                    self.assertEqual(obj["meta"]["placement"]["room"], room["key"])
                    self.assertGreater(obj["meta"]["access_channel_length_m"], 10)
                    self.assertLessEqual(obj["meta"]["access_channel_length_m"], 80)
                    self.assertIn(room["attrs"]["name"], obj["attrs"]["description"])
                    if "/atm-" in obj["key"]:
                        self.assertEqual(room["meta"]["space_type"], "atm_lobby")
                teller = site.w.obj("device/br-s0001/workstation-4")
                office = site.w.obj("device/br-s0001/workstation-5")
                self.assertEqual(site.w.obj(teller["refs"]["location"])["meta"]["space_type"], "teller_hall")
                self.assertEqual(site.w.obj(office["refs"]["location"])["meta"]["space_type"], "office")

    def test_hq_creates_floors_and_keeps_copper_routes_bounded(self):
        site = sample("hq-01", "hq", {"workstations": 180, "atms": 0, "aps": 15, "cameras": 8})
        floors = [o for o in site.w.objects.values() if o["meta"].get("space_type") == "floor"]
        self.assertEqual(len(floors), 4)
        last_desk = site.w.obj("device/hq-01/workstation-180")
        self.assertEqual(last_desk["meta"]["placement"]["floor"], 4)
        ap_floors = {o["meta"]["placement"]["floor"] for o in site.w.objects.values()
                     if o["kind"] == "device" and "/ap-" in o["key"]}
        self.assertEqual(ap_floors, {1, 2, 3, 4})
        self.assertTrue(all(o["meta"]["access_channel_length_m"] <= 80 for o in site.w.objects.values()
                            if o["kind"] == "device"))

    def test_new_rooms_and_refresh_do_not_move_existing_endpoints(self):
        old = sample(demand={"workstations": 12, "atms": 2, "aps": 2, "cameras": 2}, design="inherited")
        grown = sample(demand={"workstations": 36, "atms": 4, "aps": 4, "cameras": 4}, design="refreshed")
        for key, obj in old.w.objects.items():
            self.assertEqual(grown.w.obj(key), obj, key)
        acquired = sample(demand={"workstations": 12, "atms": 2, "aps": 2, "cameras": 2}, tenant="tenant/acquired")
        for obj in acquired.w.objects.values():
            if obj["kind"] == "location":
                self.assertEqual(obj["refs"]["tenant"], "tenant/acquired")
            if obj["kind"] == "device":
                self.assertEqual(obj["meta"]["placement"], old.w.obj(obj["key"])["meta"]["placement"])

    def test_unsupported_footprint_and_endpoint_ordinals_fail(self):
        for site_id, kind, desks in (("br-l0001", "branch", 101), ("hq-01", "hq", 193)):
            with self.subTest(kind=kind), self.assertRaisesRegex(DesignError, "footprint"):
                sample(site_id, kind, {"workstations": desks, "atms": 2, "aps": 2, "cameras": 2})
        with self.assertRaisesRegex(DesignError, "office zone"):
            sample(demand={"workstations": 12, "atms": 2, "aps": 4, "cameras": 2})
        site = sample(demand={"workstations": 12, "atms": 2, "aps": 2, "cameras": 2})
        with self.assertRaisesRegex(DesignError, "ordinal"):
            place_endpoint(site, "device/br-s0001/workstation-1", "workstation", 0)


if __name__ == "__main__":
    unittest.main()
