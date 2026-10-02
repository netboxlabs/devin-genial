"""Finite geography/placement contracts, independent of hardware generation."""

import math
from pathlib import Path
import tomllib
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from estates.generate import generate
from estates.model import DesignError, World
from estates.places import (ADDRESS_STREETS, ANCHORS, CAMPUSES, CHICAGO_GRID, FLAT_KINDS, JITTER_LAT,
                            JITTER_LON, STREETS, arrange, clli_place, foundation, locate, place_endpoint)

# Conservative (lat, lon) exclusion polygons: Lake Michigan off Chicago and
# Milwaukee, Lake Erie off Cleveland, and for Detroit Lake St. Clair, the
# Detroit River and Windsor, Ontario together. Shore-side vertices sit 200-300 m
# beyond the furthest-out OpenStreetMap lake edge in each 0.01-degree band (the
# Detroit River side follows its OSM centreline on the Canadian side), fetched
# from Overpass on 2026-10-01, so no polygon covers US land. The old +/-0.12
# degree metro jitter plotted roughly half the lakeshore sites inside these.
WATER = {
    "Chicago": (
        (42.06, -87.667), (42.05, -87.666), (42.04, -87.666), (42.03, -87.664), (42.02, -87.66), (42.01, -87.652),
        (42.0, -87.651), (41.99, -87.646), (41.98, -87.643), (41.97, -87.588), (41.96, -87.628), (41.95, -87.633),
        (41.94, -87.629), (41.93, -87.626), (41.92, -87.569), (41.91, -87.617), (41.9, -87.589), (41.89, -87.587),
        (41.88, -87.588), (41.87, -87.542), (41.86, -87.603), (41.85, -87.604), (41.84, -87.598), (41.83, -87.595),
        (41.82, -87.589), (41.81, -87.581), (41.8, -87.572), (41.79, -87.528), (41.78, -87.565), (41.77, -87.556),
        (41.76, -87.535), (41.75, -87.528), (41.74, -87.526), (41.73, -87.49), (41.72, -87.521), (41.72, -87.0),
        (42.06, -87.0),
    ),
    "Milwaukee": (
        (43.15, -87.887), (43.14, -87.893), (43.13, -87.898), (43.12, -87.893), (43.11, -87.883), (43.1, -87.872),
        (43.09, -87.868), (43.08, -87.865), (43.07, -87.861), (43.06, -87.867), (43.05, -87.877), (43.04, -87.877),
        (43.03, -87.879), (43.02, -87.881), (43.01, -87.874), (43.0, -87.874), (42.99, -87.858), (42.98, -87.849),
        (42.97, -87.845), (42.96, -87.842), (42.95, -87.84), (42.94, -87.84), (42.93, -87.842), (42.92, -87.838),
        (42.91, -87.836), (42.9, -87.842), (42.89, -87.84), (42.88, -87.841), (42.87, -87.834), (42.87, -87.4),
        (43.15, -87.4),
    ),
    "Cleveland": (
        (41.498, -81.95), (41.495, -81.94), (41.493, -81.93), (41.492, -81.92), (41.491, -81.91), (41.489, -81.9),
        (41.488, -81.89), (41.487, -81.88), (41.485, -81.87), (41.487, -81.86), (41.49, -81.85), (41.493, -81.84),
        (41.495, -81.83), (41.497, -81.82), (41.498, -81.81), (41.5, -81.8), (41.5, -81.79), (41.498, -81.78),
        (41.495, -81.77), (41.494, -81.76), (41.491, -81.75), (41.495, -81.74), (41.497, -81.73), (41.502, -81.72),
        (41.509, -81.71), (41.513, -81.7), (41.522, -81.69), (41.528, -81.68), (41.534, -81.67), (41.533, -81.66),
        (41.54, -81.65), (41.549, -81.64), (41.55, -81.63), (41.554, -81.62), (41.56, -81.61), (41.566, -81.6),
        (41.577, -81.59), (41.581, -81.58), (41.59, -81.57), (41.597, -81.56), (41.606, -81.55), (41.613, -81.54),
        (41.618, -81.53), (41.62, -81.52), (41.624, -81.51), (41.63, -81.5), (41.9, -81.5), (41.9, -81.95),
    ),
    "Detroit": (
        (42.5, -82.878), (42.49, -82.876), (42.48, -82.877), (42.47, -82.87), (42.46, -82.866), (42.45, -82.865),
        (42.44, -82.869), (42.43, -82.873), (42.42, -82.877), (42.41, -82.879), (42.4, -82.881), (42.39, -82.888),
        (42.38, -82.893), (42.37, -82.915), (42.36, -82.905), (42.345, -82.925), (42.34, -82.95), (42.336, -82.96),
        (42.335, -82.97), (42.332, -82.98), (42.33, -82.99), (42.33, -83.0), (42.331, -83.01), (42.327, -83.03),
        (42.317, -83.06), (42.311, -83.07), (42.31, -83.072), (42.29, -83.094), (42.27, -83.105), (42.26, -83.109),
        (42.25, -83.118), (42.24, -83.11), (42.23, -83.11), (42.22, -83.112), (42.21, -83.112), (42.2, -83.11),
        (42.2, -82.5), (42.5, -82.5),
    ),
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
                    # The street is a real street of the anchor the site sits on,
                    # and a Chicago grid direction agrees with the coordinate.
                    number, _, street = attrs["physical_address"].split("\n")[0].partition(" ")
                    self.assertTrue(number.isdecimal() and int(number) > 0, attrs["physical_address"])
                    direction, _, rest = street.partition(" ")
                    pools = {name for a in anchors for name in ADDRESS_STREETS[(a[1], a[0][0])]}
                    if locality == "Chicago" and direction in {"North", "South", "East", "West"}:
                        self.assertTrue(any(name.partition(" ")[2] == rest for name in pools), street)
                        lat0, lon0 = CHICAGO_GRID[:2]
                        self.assertEqual(direction in {"North", "East"},
                                         (lat >= lat0) if direction in {"North", "South"} else (lon >= lon0), street)
                    else:
                        self.assertIn(street, pools)
            sites = [o for o in plan["objects"] if o["kind"] == "site"]
            facilities = [o["attrs"]["facility"] for o in sites]
            self.assertEqual(len(set(facilities)), len(facilities), path.name)

    def test_every_anchor_has_real_streets_and_codes_have_their_shape(self):
        keys = {(anchor[1], anchor[0][0]) for anchors in ANCHORS.values() for anchor in anchors}
        self.assertEqual(set(ADDRESS_STREETS), keys)
        self.assertTrue(all(ADDRESS_STREETS.values()))
        self.assertEqual([clli_place(*args) for args in (("Chicago", "IL"), ("Detroit", "MI"), ("West Allis", "WI"),
                                                          ("Troy", "MI"))], ["CHCGIL", "DTRTMI", "WSTLWI", "TRYRMI"])

    def test_flat_premises_carry_no_pass_through_levels_and_pops_are_suites(self):
        plan = generate({"profile": "provider-backbone"})
        objects = {o["key"]: o for o in plan["objects"]}
        kinds = {c["site"]: c["kind"] for c in plan["contracts"]}
        for location in (o for o in plan["objects"] if o["kind"] == "location"):
            kind = kinds[location["refs"]["site"]]
            self.assertIn(kind, FLAT_KINDS)
            self.assertNotIn(location["meta"]["space_type"], {"building", "floor"})
        for pop in (key for key, kind in kinds.items() if kind == "pop"):
            sid = pop.removeprefix("site/")
            cage, suite = objects[f"location/{sid}"], objects[f"location/{sid}/suite"]
            self.assertEqual(cage["refs"]["parent"], suite["key"])
            self.assertRegex(suite["attrs"]["name"], r"^Suite [2-9]\d\d$")
            self.assertRegex(cage["attrs"]["name"], r"^Cage [A-H]\d\d$")
            self.assertRegex(objects[pop]["attrs"]["facility"], r"^[A-Z]{4}(IL|MI|OH|WI)[A-Z0-9]{2}$")
        # Failing mutation: a flat premises room cannot hang from another room.
        from estates.validate import validate
        customer = next(key for key, kind in kinds.items() if kind == "customer").removeprefix("site/")
        objects[f"location/{customer}/office-01"]["refs"]["parent"] = f"location/{customer}"
        self.assertIn("location-floor", {f["code"] for f in validate(plan)})
        # A multi-floor kind keeps its building and floors.
        school = generate({"profile": "school-district"})
        self.assertTrue(any(o["kind"] == "location" and o["meta"]["space_type"] == "building" for o in school["objects"]))


if __name__ == "__main__":
    unittest.main()
