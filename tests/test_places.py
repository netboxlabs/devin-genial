"""Finite geography/placement contracts, independent of hardware generation."""

import math
from pathlib import Path
import tomllib
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from estates.generate import generate
from estates.model import DesignError, World
from estates.places import (ANCHORS, CAMPUSES, JITTER_LAT, JITTER_LON, STREETS, arrange, foundation,
                            locate, place_endpoint)

# Coarse (lat, lon) exclusion polygons, drawn a little offshore of the real
# shoreline: Lake Michigan off Chicago and Milwaukee, Lake Erie off Cleveland,
# and for Detroit the river, Lake St. Clair and Windsor, Ontario together.
# Every shore-side vertex was reverse-geocoded to open water or Canada
# (OpenStreetMap Nominatim, 2026-10-01). The old +/-0.12 degree metro jitter
# plotted roughly half the lakeshore sites inside these.
WATER = {
    "Chicago": ((42.06, -87.660), (42.00, -87.650), (41.97, -87.628), (41.93, -87.620), (41.90, -87.608),
                (41.87, -87.600), (41.84, -87.598), (41.81, -87.585), (41.79, -87.570), (41.76, -87.548),
                (41.72, -87.530), (41.69, -87.515), (41.69, -87.00), (42.06, -87.00)),
    "Milwaukee": ((43.15, -87.875), (43.10, -87.865), (43.075, -87.862), (43.055, -87.875), (43.04, -87.886),
                  (43.025, -87.885), (43.00, -87.870), (42.97, -87.840), (42.92, -87.826), (42.87, -87.820),
                  (42.87, -87.40), (43.15, -87.40)),
    "Cleveland": ((41.498, -81.95), (41.499, -81.85), (41.500, -81.80), (41.497, -81.75), (41.505, -81.72),
                  (41.513, -81.70), (41.527, -81.68), (41.540, -81.645), (41.562, -81.60), (41.590, -81.55),
                  (41.620, -81.50), (41.90, -81.50), (41.90, -81.95)),
    "Detroit": ((42.50, -82.865), (42.45, -82.868), (42.42, -82.875), (42.39, -82.905), (42.365, -82.935),
                (42.345, -82.975), (42.335, -83.010), (42.326, -83.040), (42.318, -83.065), (42.306, -83.085),
                (42.288, -83.105), (42.268, -83.125), (42.230, -83.138), (42.20, -83.135), (42.20, -82.50),
                (42.50, -82.50)),
}


def in_water(metro, lat, lon):
    """Even-odd ray cast against the metro's exclusion polygon."""
    inside, ring = False, WATER[metro]
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def on_anchor(anchor, lat, lon):
    """True when (lat, lon) sits in the jitter box of a point the generator can choose."""
    (a, b), (c, d) = anchor[2:4], anchor[-2:]
    return any(abs(lat - (a + k / 255 * (c - a))) <= JITTER_LAT + 1e-6 and
               abs(lon - (b + k / 255 * (d - b))) <= JITTER_LON + 1e-6 for k in range(256))


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
        # Authored naming emits metro-centered synthetic coordinates: id-derived,
        # seed-independent (the objects equality above pins that), 6dp-rounded.
        self.assertAlmostEqual(initial["attrs"]["latitude"], 43.04, delta=0.2)
        self.assertAlmostEqual(initial["attrs"]["longitude"], -87.91, delta=0.2)
        self.assertTrue(initial["meta"]["geography"]["synthetic"])
        self.assertTrue(initial["attrs"]["physical_address"].endswith("United States"))
        ZoneInfo(initial["attrs"]["time_zone"])
        dc1, dc2 = sample("dc-01", "dc"), sample("dc-02", "dc")
        self.assertNotEqual(dc1.w.obj(dc1.key)["refs"]["region"], dc2.w.obj(dc2.key)["refs"]["region"])
        # Display names are authored and namespace-free; the slug carries the
        # namespace that still separates two estates on one target.
        geography = [o for o in first.w.objects.values() if o["kind"] in {"region", "site_group"}]
        self.assertTrue(geography)
        self.assertTrue(all(not o["attrs"]["name"].startswith("cedar ") for o in geography))
        self.assertTrue(all(o["attrs"]["slug"].startswith("cedar-") for o in geography))

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


class SiteGeographyTests(unittest.TestCase):
    def test_water_polygons_catch_the_old_failure_modes(self):
        # Failing-mutation check: open lake, the Detroit River and Windsor are refused.
        for metro, lat, lon in (("Chicago", 41.88, -87.58), ("Milwaukee", 43.04, -87.85),
                                ("Cleveland", 41.53, -81.72), ("Detroit", 42.30, -83.00), ("Detroit", 42.32, -83.05)):
            self.assertTrue(in_water(metro, lat, lon), (metro, lat, lon))
        for metro, lat, lon in (("Chicago", 41.8781, -87.6298), ("Detroit", 42.3314, -83.0458)):
            self.assertFalse(in_water(metro, lat, lon), (metro, lat, lon))

    def test_named_pools_and_every_jitter_box_stay_on_land(self):
        for metro, anchors in ANCHORS.items():
            names = {name for anchor in anchors for name in anchor[0]}
            self.assertLessEqual(set(STREETS[metro]) | set(CAMPUSES[metro]), names, metro)
            for anchor in anchors:
                (a, b), (c, d) = anchor[2:4], anchor[-2:]
                for t in (0, 0.5, 1):
                    for dy in (-JITTER_LAT, JITTER_LAT):
                        for dx in (-JITTER_LON, JITTER_LON):
                            self.assertFalse(in_water(metro, a + t*(c-a) + dy, b + t*(d-b) + dx), anchor[0])

    def test_every_sample_site_plots_on_land_at_the_anchor_its_name_and_address_imply(self):
        for path in sorted((Path(__file__).resolve().parent.parent / "profiles").glob("*.toml")):
            with open(path, "rb") as handle:
                plan = generate(tomllib.load(handle))
            for site in (o for o in plan["objects"] if o["kind"] == "site"):
                with self.subTest(recipe=path.name, site=site["key"]):
                    attrs, metro = site["attrs"], site["meta"]["geography"]["city"]
                    lat, lon = attrs["latitude"], attrs["longitude"]
                    self.assertFalse(in_water(metro, lat, lon))
                    locality = attrs["physical_address"].split("\n")[1].split(", ")[0]
                    anchors = [a for a in ANCHORS[metro] if a[1] == locality and on_anchor(a, lat, lon)]
                    self.assertTrue(anchors, f"{attrs['name']} at {lat},{lon} is not on a {locality} anchor")
                    named = [a for a in ANCHORS[metro]
                             if any(f" {n.lower()} " in f" {attrs['name'].lower()} " for n in a[0])]
                    if named and plan["recipe"]["profile"] != "university-campus":
                        self.assertTrue(any(a in named for a in anchors), attrs["name"])


if __name__ == "__main__":
    unittest.main()
