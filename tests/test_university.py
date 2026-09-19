"""University composition must follow campus demand and keep an inspectable estate."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.generate import generate
from estates.load import main as load_main
from estates.model import DesignError, ROOT, canonical, hardware_catalog, recipe_from_file
from estates.report import markdown
from estates.validate import validate
from estates.__main__ import main


SMALL = dict(buildings=[dict(key="science", classrooms=4, lab_seats=24, offices=12)],
             residences=[dict(key="aspen", rooms=60)],
             library=dict(reading_seats=48, aps=3), wan_peak_mbps=2000)


def plan_for(**changes):
    return generate({"profile": "university-campus"} | changes)


def small(**changes):
    return plan_for(**(deepcopy(SMALL) | changes))


class UniversityResolverTests(unittest.TestCase):
    def test_unknown_and_out_of_range_requests_are_actionable(self):
        cases = [
            ({"schools": []}, "Unknown university recipe fields: schools"),
            ({"stores": {}}, "Unknown university recipe fields: stores"),
            ({"headquarters_staff": 100}, "Unknown university recipe fields: headquarters_staff"),
            ({"design_mix": {"modern": 1}}, "Unknown university recipe fields: design_mix"),
            ({"acquired_sites": []}, "Unknown university recipe fields: acquired_sites"),
            ({"buildings": []}, "buildings must be a list of 1–16"),
            ({"buildings": [{"key": "a"}] * 17}, "buildings must be a list of 1–16"),
            ({"buildings": [{"classrooms": 4}]}, "needs a stable key"),
            ({"buildings": [{"key": "a", "beds": 4}]}, "only supported demand fields"),
            ({"buildings": [{"key": "A"}]}, "unique lowercase identifiers"),
            ({"buildings": [{"key": "a"}, {"key": "a"}]}, "unique lowercase identifiers"),
            ({"buildings": [{"key": "a-b"}, {"key": "ab"}]}, "distinct without hyphens"),
            ({"buildings": [{"key": "a", "classrooms": 41}]}, "classrooms must be an integer from 0 through 40"),
            ({"buildings": [{"key": "a", "classrooms": -1}]}, "classrooms must be an integer from 0 through 40"),
            ({"buildings": [{"key": "a", "classrooms": True}]}, "classrooms must be an integer from 0 through 40"),
            ({"buildings": [{"key": "a", "lab_seats": 201}]}, "lab_seats must be an integer from 0 through 200"),
            ({"buildings": [{"key": "a", "offices": 61}]}, "offices must be an integer from 0 through 60"),
            ({"buildings": [{"key": "a", "classrooms": 0, "lab_seats": 0, "offices": 0}]},
             "needs lecture halls, lab seats or faculty offices"),
            ({"residences": [{"key": "h", "rooms": 9}]}, "rooms must be an integer from 10 through 400"),
            ({"residences": [{"key": "h", "rooms": 401}]}, "rooms must be an integer from 10 through 400"),
            ({"residences": [{"key": "h", "rooms": 60, "wired_ports_per_room": 3}]},
             "wired_ports_per_room must be an integer from 0 through 2"),
            ({"residences": [{"key": "h", "rooms": 60}] * 17}, "residences must be a list of 0–16"),
            ({"library": {"reading_seats": 23}}, "reading_seats must be an integer from 24 through 300"),
            ({"library": {"reading_seats": 301}}, "reading_seats must be an integer from 24 through 300"),
            ({"library": {"aps": 0}}, "aps must be an integer from 1 through 16"),
            ({"library": {"aps": 17}}, "aps must be an integer from 1 through 16"),
            ({"library": {"seats": 10}}, "library accepts only reading_seats and aps"),
            ({"library": {"reading_seats": 24, "aps": 6}}, "radios exceed the entrance mount"),
            ({"wan_peak_mbps": 0}, "wan_peak_mbps must be an integer between 1 and 16000"),
            ({"wan_peak_mbps": 16001}, "wan_peak_mbps must be an integer between 1 and 16000"),
            ({"wan_peak_mbps": 10}, "does not cover the"),
            ({"address_pool": "192.168.0.0/20"}, "/16 site reservations"),
            ({"reservation_user": "someone"}, "reservation_user"),
            ({"demo": "invent-a-workflow"}, "not yet implemented"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(DesignError, message):
                plan_for(**changes)

    def test_supported_bounds_are_accepted_at_their_edges(self):
        edge = plan_for(buildings=[dict(key="a", classrooms=40, lab_seats=200, offices=60)],
                        residences=[], library=dict(reading_seats=300, aps=16), wan_peak_mbps=2000)
        self.assertEqual(validate(edge), [])
        self.assertEqual(edge["recipe"]["residences"], [])
        # A 400-room hall fits at one port per room but not at two: the combined
        # access-switch capacity is checked separately from the input bound.
        self.assertEqual(validate(plan_for(buildings=[dict(key="a", classrooms=2, lab_seats=0, offices=0)],
                                           residences=[dict(key="h", rooms=400)],
                                           wan_peak_mbps=2000)), [])
        with self.assertRaisesRegex(DesignError, "access switches"):
            plan_for(buildings=[dict(key="a", classrooms=2, lab_seats=0, offices=0)],
                     residences=[dict(key="h", rooms=400, wired_ports_per_room=2)],
                     wan_peak_mbps=2000)

    def test_wireless_zone_requests_are_bounded_and_named(self):
        cases = [({"wireless": {"lecture-099": {"managed": 4}}}, "wireless must map existing zones"),
                 ({"wireless": {"lecture-001": {"clients": 4}}}, "only managed and guest device counts"),
                 ({"wireless": {"lecture-001": 4}}, "only managed and guest device counts"),
                 ({"wireless": {"lecture-001": {"managed": 120, "guest": 60}}}, "total at most 128")]
        for extra, message in cases:
            with self.subTest(extra=extra), self.assertRaisesRegex(DesignError, message):
                small(buildings=[dict(key="science", classrooms=4, lab_seats=24, offices=12, **extra)])

    def test_defaults_are_frozen_into_the_recipe(self):
        recipe = plan_for()["recipe"]
        self.assertEqual([item["key"] for item in recipe["buildings"]],
                         ["business", "engineering", "humanities", "science"])
        self.assertEqual([item["key"] for item in recipe["residences"]], ["aspen", "cypress", "willow"])
        self.assertEqual(recipe["library"], {"reading_seats": 160, "aps": 8})
        self.assertEqual(recipe["namespace"], "lakemont")
        self.assertEqual(recipe["wan_peak_mbps"], 4000)
        self.assertEqual(recipe["demo"], "baseline")
        self.assertNotIn("headquarters_staff", recipe)
        self.assertEqual(recipe["buildings"][3]["wireless"]["lecture-001"], {"managed": 28, "guest": 4})
        self.assertEqual(recipe["residences"][0]["wireless"]["floor-01"], {"managed": 100, "guest": 2})

    def test_shipped_profile_resolves_and_validates(self):
        plan = generate(recipe_from_file(ROOT / "profiles/university-campus.toml"))
        self.assertEqual(validate(plan), [])
        self.assertEqual(len(plan["recipe"]["buildings"]), 4)
        self.assertEqual(len(plan["recipe"]["residences"]), 3)


class UniversityCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = small()

    def test_one_campus_connects_its_own_demand_without_other_industries(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(canonical(generate(self.plan["recipe"])), canonical(self.plan))
        objects = self.plan["objects"]
        self.assertEqual({o["key"] for o in objects if o["kind"] == "site"},
                         {"site/dc-01", "site/bldg-science", "site/hall-aspen", "site/library-01"})
        self.assertEqual({o["meta"]["service"] for o in objects if o["kind"] == "virtual_machine"},
                         {"learning-portal", "identity", "dns", "research-storage", "monitoring", "backup"})
        # One campus, one metro, one data center.
        self.assertEqual(len({o["meta"]["geography"]["city"] for o in objects if o["kind"] == "site"}), 1)
        self.assertEqual(len([o for o in objects if o["kind"] == "site" and o["key"].startswith("site/dc-")]), 1)
        text = canonical(self.plan).decode().lower()
        for forbidden in ("teller", "atm", "birch", "cedar", "bedside", "point-of-sale", "stockroom"):
            self.assertNotIn(forbidden, text, forbidden)
        for phrase in ("lecture hall", "teaching lab", "residence room", "reading room", "eduroam-style"):
            self.assertIn(phrase.lower(), text, phrase)
        guide = markdown(self.plan)
        for phrase in ("lecture halls", "residence rooms", "Replica group placement"):
            self.assertIn(phrase, guide)

    def test_academic_building_places_halls_labs_and_offices_on_their_segments(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        roles = {}
        for obj in self.plan["objects"]:
            if obj["kind"] == "device" and obj["refs"].get("site") == "site/bldg-science":
                roles[obj["refs"]["role"]] = roles.get(obj["refs"]["role"], 0) + 1
        self.assertEqual(roles["role/workstation"], 4 + 24 + 12)
        self.assertEqual(objects["device/bldg-science/instructor-001"]["refs"]["location"],
                         "location/bldg-science/lecture-001")
        self.assertEqual(objects["device/bldg-science/instructor-001"]["meta"]["network"], "staff")
        self.assertEqual(objects["device/bldg-science/lab-01-01"]["meta"]["network"], "research")
        self.assertEqual(objects["device/bldg-science/office-01-01"]["meta"]["network"], "staff")
        for segment in ("management", "staff", "students", "research", "wireless", "security", "guest"):
            self.assertEqual(objects[f"vlan/bldg-science/{segment}"]["refs"]["site"], "site/bldg-science")
            self.assertEqual(objects[f"prefix/bldg-science/{segment}"]["attrs"]["status"], "active")
        # Segments are /22: a 400-room hall addresses 800 installed ports.
        self.assertTrue(objects["prefix/bldg-science/students"]["attrs"]["prefix"].endswith("/22"))

    def test_residence_hall_ports_are_installed_capacity_on_the_student_segment(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        ports = [o for o in self.plan["objects"] if o["kind"] == "device"
                 and o["refs"].get("site") == "site/hall-aspen"
                 and o["key"].startswith("device/hall-aspen/room-")]
        self.assertEqual(len(ports), 60)  # 60 rooms x 1 wired port
        self.assertEqual(objects["device/hall-aspen/room-001-01"]["refs"]["location"],
                         "location/hall-aspen/room-001")
        self.assertEqual(objects["device/hall-aspen/room-001-01"]["meta"]["network"], "students")
        self.assertNotIn("vlan/hall-aspen/research", objects)
        # Halls have no lanes, beds or ATMs; radios live in the floor corridor.
        self.assertEqual(objects["device/hall-aspen/ap-floor-01"]["refs"]["location"],
                         "location/hall-aspen/corridor-01")

    def test_library_radio_count_is_an_explicit_mount_list(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        radios = [o for o in self.plan["objects"] if o["kind"] == "device"
                  and o["refs"].get("site") == "site/library-01" and o["refs"]["role"] == "role/ap"]
        self.assertEqual(len(radios), 3)
        self.assertEqual(objects["device/library-01/ap-library-01"]["refs"]["location"],
                         "location/library-01/reception")
        self.assertEqual(objects["device/library-01/study-01-01"]["meta"]["network"], "students")

    def test_wireless_serves_staff_students_and_campus_guests_but_never_the_dc(self):
        lans = {o["key"] for o in self.plan["objects"] if o["kind"] == "wireless_lan"}
        for sid in ("bldg-science", "hall-aspen", "library-01"):
            for role in ("staff", "students", "guest"):
                self.assertIn(f"wireless-lan/{sid}/{role}", lans, (sid, role))
        self.assertFalse({key for key in lans if key.startswith("wireless-lan/dc-")})
        # eduroam-style naming only: no authentication protocol is configured.
        identity = [o for o in self.plan["objects"] if o["kind"] == "service"
                    and o["attrs"].get("name") == "radius"]
        self.assertTrue(identity)

    def test_endpoints_reach_an_access_pair_in_their_own_floor_equipment_room(self):
        objects = {o["key"]: o for o in self.plan["objects"]}
        upper = [o for o in self.plan["objects"] if o["kind"] == "device"
                 and o["refs"].get("site") == "site/hall-aspen" and o["meta"].get("endpoint")
                 and o["meta"]["placement"]["floor"] == 2]
        self.assertTrue(upper)
        for device in upper:
            self.assertEqual(device["meta"]["placement"]["cable_origin"], "location/hall-aspen/idf-02")
            self.assertLessEqual(device["meta"]["access_channel_length_m"], 80)
        self.assertEqual(objects["location/hall-aspen/idf-02"]["meta"]["space_type"], "equipment_room")

    def test_growth_appends_buildings_halls_and_rooms_without_moving_anything(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        recipe["buildings"].append(dict(key="arts", classrooms=6, lab_seats=0, offices=24))
        recipe["residences"].append(dict(key="willow", rooms=100))
        recipe["buildings"][0]["classrooms"] = 8   # grow an existing building
        recipe["residences"][0]["rooms"] = 90      # grow an existing hall
        recipe["library"]["reading_seats"] = 72
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old = {o["key"]: o for o in before["objects"]}
        new = {o["key"]: o for o in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        occupied = {end for o in before["objects"] if o["kind"] == "cable" for end in o["refs"].values()}
        for key, obj in old.items():
            if obj["kind"] == "power_port":
                self.assertGreaterEqual(new[key]["attrs"].get("maximum_draw", 0),
                                        obj["attrs"].get("maximum_draw", 0), key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
            elif obj["kind"] in {"site", "rack", "location", "cable", "ip_address", "vlan", "prefix",
                                 "virtual_machine", "wireless_lan"} or key in occupied:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                for field in ("location", "rack", "site"):
                    self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field), key)
        for scope, items in before["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_added_lecture_halls_never_renumber_existing_labs_and_offices(self):
        before = small()
        old = {o["key"]: o for o in before["objects"]}
        recipe = deepcopy(before["recipe"])
        recipe["buildings"][0]["classrooms"] = 20  # pushes rooms onto new floors
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        new = {o["key"]: o for o in after["objects"]}
        for room in ("location/bldg-science/lab-01", "location/bldg-science/office-01"):
            self.assertEqual(new[room]["meta"]["floor"], old[room]["meta"]["floor"], room)
            self.assertEqual(new[room]["meta"]["position_m"], old[room]["meta"]["position_m"], room)
        self.assertEqual(new["device/bldg-science/lab-01-01"]["meta"]["placement"],
                         old["device/bldg-science/lab-01-01"]["meta"]["placement"])

    def test_radio_channels_and_endpoint_ports_survive_growth(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        recipe["residences"][0]["rooms"] = 120
        after = generate(recipe, previous=before)
        old_rf = {o["key"]: o["attrs"]["rf_channel"] for o in before["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        new_rf = {o["key"]: o["attrs"]["rf_channel"] for o in after["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        self.assertTrue(old_rf)
        self.assertEqual({k: new_rf.get(k) for k in old_rf}, old_rf)
        for scope, items in before["reservations"].items():
            if scope.startswith("access-endpoints/"):
                for key, slot in items.items():
                    self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_reductions_and_profile_changes_need_a_new_baseline(self):
        before = small(buildings=[dict(key="science", classrooms=4, lab_seats=24, offices=12),
                                  dict(key="arts", classrooms=4, lab_seats=0, offices=12)],
                       residences=[dict(key="aspen", rooms=60), dict(key="willow", rooms=60)])
        # The resolver sorts keyed entries, so address them by key, never index.
        def entry(recipe, field, key):
            return next(item for item in recipe[field] if item["key"] == key)

        cases = [
            ("drop a building", lambda r: r["buildings"].remove(entry(r, "buildings", "arts"))),
            ("drop a hall", lambda r: r["residences"].remove(entry(r, "residences", "willow"))),
            ("shrink classrooms", lambda r: entry(r, "buildings", "science").__setitem__("classrooms", 1)),
            ("shrink lab seats", lambda r: entry(r, "buildings", "science").__setitem__("lab_seats", 0)),
            ("shrink offices", lambda r: entry(r, "buildings", "science").__setitem__("offices", 0)),
            ("shrink rooms", lambda r: entry(r, "residences", "aspen").__setitem__("rooms", 20)),
            ("repin room ports",
             lambda r: entry(r, "residences", "aspen").__setitem__("wired_ports_per_room", 2)),
            ("shrink reading seats", lambda r: r["library"].__setitem__("reading_seats", 24)),
            ("shrink library radios", lambda r: r["library"].__setitem__("aps", 1)),
            ("renew campus wan", lambda r: r.__setitem__("wan_peak_mbps", 3000)),
            ("shrink a wireless zone",
             lambda r: entry(r, "buildings", "science")["wireless"]["lecture-001"].__setitem__("managed", 1)),
        ]
        for label, change in cases:
            recipe = deepcopy(before["recipe"])
            change(recipe)
            with self.subTest(label=label), self.assertRaisesRegex(DesignError, "new baseline|new estate"):
                generate(recipe, previous=before)
        with self.assertRaisesRegex(DesignError, "new estate|new baseline"):
            generate(deepcopy(before["recipe"]) | {"namespace": "other"}, previous=before)

    def test_growth_beyond_the_frozen_campus_edge_names_the_rebaseline_exit(self):
        from estates.university import resolve, total_peak
        exact = deepcopy(SMALL) | {"profile": "university-campus"}
        exact["wan_peak_mbps"] = total_peak(resolve(exact))
        before = generate(exact)
        recipe = deepcopy(before["recipe"])
        recipe["residences"].append(dict(key="willow", rooms=100))
        with self.assertRaises(DesignError) as caught:
            generate(recipe, previous=before)
        self.assertIn("new baseline", str(caught.exception))
        self.assertIn("regenerate without the previous plan", str(caught.exception))
        self.assertNotIn("raise the campus edge", str(caught.exception))
        # The same shortfall at baseline keeps the actionable raise-it advice.
        with self.assertRaisesRegex(DesignError, "raise the campus edge"):
            generate(recipe)

    def test_damaged_previous_ledger_is_rejected_before_allocation(self):
        before = small()
        damaged = deepcopy(before)
        damaged["objects"] = [o for o in damaged["objects"]
                              if o["key"] != "device/bldg-science/instructor-001"]
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(deepcopy(before["recipe"]), previous=damaged)


class UniversityNamingTests(unittest.TestCase):
    def test_campus_site_names_are_unique_and_campus_flavoured(self):
        plan = plan_for(buildings=[dict(key="science", classrooms=4, lab_seats=0, offices=12),
                                   dict(key="arts", classrooms=4, lab_seats=0, offices=12)],
                        residences=[dict(key="aspen", rooms=60), dict(key="willow", rooms=60)],
                        wan_peak_mbps=2000)
        sites = [o for o in plan["objects"] if o["kind"] == "site"]
        names = [o["attrs"]["name"] for o in sites]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(o["attrs"]["name"].endswith(" Hall")
                            for o in sites if o["key"].startswith("site/bldg-")))
        self.assertTrue(all(o["attrs"]["name"].endswith(" House")
                            for o in sites if o["key"].startswith("site/hall-")))
        library = next(o for o in sites if o["key"] == "site/library-01")
        self.assertTrue(library["attrs"]["name"].endswith(" Library"))
        dc = next(o for o in sites if o["key"] == "site/dc-01")
        self.assertTrue(dc["attrs"]["name"].endswith(" Campus Data Center"))
        self.assertTrue(all(o["attrs"]["facility"] for o in sites))
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}), len(sites))

    def test_site_names_override_and_legacy_naming_stay_available(self):
        plan = small(site_names={"bldg-science": {"name": "Founders Science Centre", "facility": "SCI01"}})
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertEqual(objects["site/bldg-science"]["attrs"]["name"], "Founders Science Centre")
        self.assertEqual(objects["site/bldg-science"]["attrs"]["facility"], "SCI01")
        self.assertEqual(validate(plan), [])
        legacy = small(naming="legacy")
        legacy_objects = {o["key"]: o for o in legacy["objects"]}
        self.assertEqual(legacy_objects["site/bldg-science"]["attrs"]["name"], "lakemont-bldg-science")
        self.assertEqual(validate(legacy), [])
        with self.assertRaisesRegex(DesignError, "unknown site ids"):
            small(site_names={"school-oak": {"name": "Nope"}})


class UniversityValidatorTests(unittest.TestCase):
    """Every independent university assertion must reject a matching mutation."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = small()
        assert validate(cls.baseline) == []

    def mutated(self, change):
        plan = deepcopy(self.baseline)
        objects = {o["key"]: o for o in plan["objects"]}
        change(plan, objects)
        return {finding["code"] for finding in validate(plan)}

    def test_recipe_bounds_are_restated_independently(self):
        cases = [("buildings", []), ("buildings", [{"key": "x", "classrooms": 99}]),
                 ("residences", [{"key": "h", "rooms": 9999}]),
                 ("library", {"reading_seats": 4, "aps": 1}),
                 ("wan_peak_mbps", 0), ("reserve_fraction", 0.9)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.assertIn("university-recipe", self.mutated(
                    lambda plan, objects, f=field, v=value: plan["recipe"].__setitem__(f, v)))
        self.assertIn("university-recipe", self.mutated(
            lambda plan, objects: plan["recipe"].__setitem__("wan_tiers_mbps", [100, 50])))

    def test_campus_edge_that_does_not_cover_the_buildings_is_reported(self):
        def shrink(plan, objects):
            plan["recipe"]["wan_peak_mbps"] = 100
        self.assertIn("university-campus-capacity", self.mutated(shrink))

    def test_missing_or_extra_sites_are_reported(self):
        def add_building(plan, objects):
            plan["recipe"]["buildings"].append({"key": "ghost", "classrooms": 2, "lab_seats": 0,
                                                "offices": 0, "wireless": {}})
        self.assertIn("university-site-inventory", self.mutated(add_building))

    def test_removed_endpoint_is_reported_even_when_the_contract_agrees(self):
        def remove(plan, objects):
            plan["objects"] = [o for o in plan["objects"] if o["key"] != "device/bldg-science/lab-01-24"]
            for contract in plan["contracts"]:
                if contract["site"] == "site/bldg-science":
                    contract["demand"]["workstations"] -= 1
                    contract["endpoint_count"] -= 1
        codes = self.mutated(remove)
        self.assertIn("university-endpoint-inventory", codes)
        self.assertIn("university-endpoint-demand", codes)
        self.assertIn("university-demand-report", codes)

    def test_lab_seat_moved_to_another_room_is_reported(self):
        def move(plan, objects):
            objects["device/bldg-science/lab-01-02"]["refs"]["location"] = "location/bldg-science/corridor-01"
        codes = self.mutated(move)
        self.assertIn("university-endpoint-placement", codes)
        self.assertIn("endpoint-room", codes)

    def test_lab_seat_on_the_wrong_segment_is_reported(self):
        def rewire(plan, objects):
            objects["device/bldg-science/lab-01-02/if/eth0"]["refs"]["untagged_vlan"] = "vlan/bldg-science/staff"
        self.assertIn("university-endpoint-path", self.mutated(rewire))

    def test_radio_cabled_to_an_uplink_port_is_reported(self):
        def relocate(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and "device/bldg-science/ap-lecture-001/if/eth0" in obj["refs"].values():
                    for field in ("a", "b"):
                        if obj["refs"][field] != "device/bldg-science/ap-lecture-001/if/eth0":
                            obj["refs"][field] = "device/bldg-science/access-01/if/TenGigabitEthernet1/1/4"
        self.assertIn("university-ap-power", self.mutated(relocate))

    def test_unplugged_radio_is_reported(self):
        def unplug(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and "device/bldg-science/ap-lecture-001/if/eth0" in obj["refs"].values():
                    obj["attrs"]["status"] = "planned"
        self.assertIn("university-endpoint-path", self.mutated(unplug))

    def test_moved_endpoint_mount_and_route_are_reported(self):
        def shift(plan, objects):
            objects["device/hall-aspen/room-001-01"]["meta"]["placement"]["position_m"] = [99, 99, 0.8]
        self.assertIn("university-endpoint-route", self.mutated(shift))

    def test_renumbered_room_ledger_is_reported(self):
        def swap(plan, objects):
            ledger = plan["reservations"]["campus-rooms/bldg-science"]
            ledger["lab-01"] = max(ledger.values()) + 1
        self.assertIn("university-room-allocation", self.mutated(swap))

    def test_relocated_segment_prefix_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/bldg-science/research"]["attrs"]["prefix"] = "10.99.99.0/22"
        self.assertIn("university-prefix-policy", self.mutated(renumber))

    def test_research_segment_outside_an_academic_building_is_rejected(self):
        def add_segment(plan, objects):
            plan["objects"].append({"key": "vlan/hall-aspen/research", "kind": "vlan",
                                    "attrs": {"name": "lakemont-hall-aspen-research", "vid": 40,
                                              "status": "active"},
                                    "refs": {"site": "site/hall-aspen", "tenant": "tenant"}, "meta": {}})
        self.assertIn("university-segment-unrequested", self.mutated(add_segment))

    def test_wireless_zone_budget_disagreement_is_reported(self):
        def widen(plan, objects):
            plan["recipe"]["buildings"][0]["wireless"]["lecture-001"] = {"managed": 999, "guest": 0}
        self.assertIn("university-wireless-demand", self.mutated(widen))

    def test_library_radio_overcount_is_reported(self):
        def widen(plan, objects):
            plan["recipe"]["library"]["aps"] = 16
        self.assertIn("university-wireless-demand", self.mutated(widen))

    def test_undersized_building_wan_commitment_is_reported(self):
        def downgrade(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "circuit" and obj["key"].startswith("circuit/bldg-science/"):
                    obj["attrs"]["commit_rate"] = 50000
        self.assertIn("university-wan-capacity", self.mutated(downgrade))

    def test_both_carriers_on_one_edge_are_reported(self):
        def collapse(plan, objects):
            for obj in plan["objects"]:
                if obj["kind"] == "cable" and any(str(v).startswith("circuit/bldg-science/b/")
                                                  for v in obj["refs"].values()):
                    for field in ("a", "b"):
                        if str(obj["refs"][field]).startswith("device/"):
                            obj["refs"][field] = "device/bldg-science/edge-a/if/wan2"
        self.assertIn("university-wan-diversity", self.mutated(collapse))

    def test_missing_gateway_svi_is_reported(self):
        def disable(plan, objects):
            for obj in plan["objects"]:
                if (obj["kind"] == "interface" and obj["refs"].get("device") == "device/bldg-science/dist-a"
                        and obj["refs"].get("untagged_vlan") == "vlan/bldg-science/security"):
                    obj["attrs"]["enabled"] = False
        self.assertIn("university-gateway-inventory", self.mutated(disable))

    def test_infrastructure_moved_out_of_an_equipment_room_is_reported(self):
        def move(plan, objects):
            objects["device/bldg-science/access-01"]["refs"]["location"] = "location/bldg-science/corridor-01"
        self.assertIn("university-equipment-placement", self.mutated(move))

    def test_reserved_access_slot_that_disagrees_with_the_graph_is_reported(self):
        def swap(plan, objects):
            scope = "access-endpoints/bldg-science/location/bldg-science"
            slots = plan["reservations"][scope]
            slots["device/bldg-science/instructor-001"] = max(slots.values()) + 1
        self.assertIn("university-access-allocation", self.mutated(swap))

    def test_removed_campus_service_replica_is_reported(self):
        def remove(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not o["key"].startswith("vm/dc-01/identity/002")]
        self.assertIn("dc-workload-inventory", self.mutated(remove))

    def test_wrong_dc_prefix_offset_is_reported(self):
        def renumber(plan, objects):
            objects["prefix/dc-01/database"]["attrs"]["prefix"] = "10.200.16.0/20"
        self.assertIn("dc-prefix-policy", self.mutated(renumber))

    def test_every_remaining_university_assertion_rejects_its_own_mutation(self):
        def extra_room(plan, objects):
            plan["objects"].append({"key": "location/bldg-science/annex", "kind": "location",
                                    "attrs": {"name": "Annex", "slug": "lakemont-bldg-science-annex",
                                              "status": "active"},
                                    "refs": {"site": "site/bldg-science", "tenant": "tenant",
                                             "parent": "location/bldg-science/floor-01"},
                                    "meta": {"space_type": "reading_room", "floor": 1,
                                             "position_m": [40, 40, 0]}})

        def extra_switch(plan, objects):
            plan["objects"].append(dict(objects["device/bldg-science/access-01"],
                                        key="device/bldg-science/access-09",
                                        attrs=dict(objects["device/bldg-science/access-01"]["attrs"],
                                                   name="bldgscience-as09", position=19)))

        cases = [
            ("university-address-allocation",
             lambda plan, objects: plan["allocations"].__setitem__("bldg-science", 0)),
            ("university-site-status",
             lambda plan, objects: objects["site/bldg-science"]["attrs"].__setitem__("status", "planned")),
            ("university-contract-inventory", lambda plan, objects: plan.__setitem__(
                "contracts", [c for c in plan["contracts"] if c["site"] != "site/bldg-science"])),
            ("university-room-inventory", extra_room),
            ("university-room-placement",
             lambda plan, objects: objects["location/bldg-science/lecture-001"]["meta"].__setitem__("position_m", [7, 4, 0])),
            ("university-closet-inventory",
             lambda plan, objects: objects["location/bldg-science"]["meta"].__setitem__("space_type", "office")),
            ("university-endpoint-address",
             lambda plan, objects: objects["ip/device/bldg-science/instructor-001/if/eth0"]["attrs"].__setitem__("status", "reserved")),
            ("university-access-inventory", extra_switch),
            ("university-access-capacity",
             lambda plan, objects: objects["device/bldg-science/access-01"]["meta"].__setitem__("hardware", "leaf")),
            ("university-core-inventory",
             lambda plan, objects: objects["device/bldg-science/dist-b"]["refs"].__setitem__("role", "role/access")),
            ("university-uplink-path",
             lambda plan, objects: objects["device/bldg-science/access-01/if/TenGigabitEthernet1/1/1"]["attrs"].__setitem__("enabled", False)),
            ("university-wan-inventory",
             lambda plan, objects: objects["circuit/bldg-science/b/1"]["refs"].__setitem__("provider", "provider/a")),
            ("university-equipment-role",
             lambda plan, objects: objects["device/bldg-science/console-01"]["refs"].__setitem__("role", "role/workstation")),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                self.assertIn(code, self.mutated(change))

    def test_power_path_break_is_reported(self):
        def unplug(plan, objects):
            for obj in plan["objects"]:
                if (obj["kind"] == "cable" and obj["attrs"].get("type") == "power" and
                        any("device/bldg-science/access-01/power/" in str(v) for v in obj["refs"].values())):
                    obj["attrs"]["status"] = "planned"
                    break
        codes = self.mutated(unplug)
        self.assertTrue({"dc-power-path", "power-path-status"} & codes, codes)


class UniversityArtifactTests(unittest.TestCase):
    def test_cli_preview_generation_and_turbobulk_load_check(self):
        def call(*args):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = main(["--json", *map(str, args)])
            self.assertEqual(status, 0, output.getvalue() + error.getvalue())
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "campus.toml"
            recipe.write_text('profile = "university-campus"\nwan_peak_mbps = 2000\n'
                              '[[buildings]]\nkey = "science"\nclassrooms = 4\nlab_seats = 24\n'
                              'offices = 12\n\n[[residences]]\nkey = "aspen"\nrooms = 60\n\n'
                              '[library]\nreading_seats = 48\naps = 3\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["profile"], "university-campus")
            self.assertEqual(preview["resolved"]["buildings"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["reserve_fraction"]["source"], "default")
            call("generate", recipe, "--out", root / "build")
            self.assertEqual(json.loads((root / "build/intent.json").read_text()), preview)
            call("check", root / "build/plan.json")
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = load_main([str(root / "build"), "--load-check"])
            self.assertEqual(status, 0, error.getvalue())
            verdict = json.loads(output.getvalue())
            self.assertTrue(verdict["turbobulk_loadable"], verdict)
            self.assertEqual(verdict["turbobulk_uncovered"], [])
            self.assertEqual(verdict["turbobulk_unsupported_refs"], [])

    def test_catalog_hardware_is_reused_without_new_models(self):
        plan = small()
        aliases = {o["meta"]["hardware"] for o in plan["objects"] if o["kind"] == "device"}
        self.assertTrue(aliases <= set(hardware_catalog()["models"]))
        self.assertEqual(aliases & {"atm", "inherited-access", "liquid-blade", "liquid-chassis"}, set())
        roles = {o["refs"]["role"] for o in plan["objects"] if o["kind"] == "device"}
        self.assertEqual(roles & {"role/pos-terminal", "role/scanner", "role/medical-device",
                                  "role/imaging-device", "role/atm"}, set())


if __name__ == "__main__":
    unittest.main()
