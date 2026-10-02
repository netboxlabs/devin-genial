"""The v0.18 lived-in PoP plant (estates/fibre.py + estates/timeline.py), builder side.

DESIGN.md §3 and §4.1 (build/lived-in-design): stratigraphy with never-reused
gaps, blanking and rack journals, single-cabinet edge PoPs, the Cermak relic,
planned/staged successor and cold spare, TIA-606-style panel labels, and growth
stability. Each check is a small function over a plan, paired with a mutation
the check must catch. The independent validators are WP-D's.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
import re
import tomllib
import unittest

from estates import fibre, geometry, timeline
from estates.generate import generate
from estates.model import resolve_recipe


SHOWCASE = Path(__file__).resolve().parent.parent / "profiles" / "showcase-provider.toml"


def showcase():
    return resolve_recipe(tomllib.loads(SHOWCASE.read_text()))


def index(plan):
    return {o["key"]: o for o in plan["objects"]}


def pop_racks(objects):
    return sorted(k for k, o in objects.items() if o["kind"] == "rack" and o["refs"]["site"].startswith("site/pop-"))


def members(objects, rack):
    return sorted((o for o in objects.values() if o["kind"] == "device" and o["refs"].get("rack") == rack
                   and o["attrs"].get("position")), key=lambda o: -o["attrs"]["position"])


def height(plan, device):
    return next(o for o in plan["objects"] if o["key"] == device["refs"]["device_type"])["attrs"]["u_height"]


def stratigraphy_violations(plan):
    """Top-down install order, gaps == removed devices (blanked), one rack journal per removal."""
    objects, problems = index(plan), []
    journals = {o["key"] for o in objects.values() if o["kind"] == "journal_entry"}
    for rack in pop_racks(objects):
        rows = members(objects, rack)
        dated = [o for o in rows if o["meta"].get("installed") and o["attrs"]["status"] != "planned"]
        for upper, lower in zip(dated, dated[1:]):
            if upper["meta"]["installed"] > lower["meta"]["installed"]:
                problems.append(f"{rack}: {upper['key']} above {lower['key']} but installed later")
        occupied = Counter(u for o in rows for u in range(o["attrs"]["position"], o["attrs"]["position"] + height(plan, o)))
        if any(n > 1 for n in occupied.values()):
            problems.append(f"{rack}: overlapping units")
        blanks = {u for o in rows if o["meta"]["hardware"].startswith("blanking")
                  for u in range(o["attrs"]["position"], o["attrs"]["position"] + height(plan, o))}
        top, bottom = max(occupied), min(occupied)
        holes = {u for u in range(bottom, top + 1) if u not in occupied}
        if holes:
            problems.append(f"{rack}: unblanked gap {sorted(holes)}")
        removed_units = set()
        for gone in objects[rack]["meta"].get("removed", []):
            first, _, last = gone["units"].removeprefix("U").partition("-U")
            removed_units |= set(range(int(first), int(last or first) + 1))
            if f"journal/{rack}/removed/{gone['label']}" not in journals:
                problems.append(f"{rack}: no rack journal for removed {gone['label']}")
        if blanks != removed_units:
            problems.append(f"{rack}: blanked units {sorted(blanks)} != removed units {sorted(removed_units)}")
    return problems


def not_in_service_cabled(plan):
    """Non-active plant devices carry no cable at all (DESIGN §7 gate 4)."""
    objects = index(plan)
    idle = {k for k, o in objects.items() if o["kind"] == "device" and o["refs"]["site"].startswith("site/pop-")
            and o["attrs"]["status"] in ("decommissioning", "planned", "inventory")}
    return sorted({objects[o["refs"][e]]["refs"].get("device") for o in objects.values() if o["kind"] == "cable"
                   for e in ("a", "b") if objects[o["refs"][e]]["refs"].get("device") in idle})


class LivedInPlant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = showcase()
        cls.plan = generate(cls.recipe)
        cls.objects = index(cls.plan)

    def layout(self, rack):
        return [(o["attrs"]["position"], o["key"].rsplit("/", 1)[1], o["attrs"]["status"])
                for o in members(self.objects, rack)]

    def test_cermak_stratigraphy(self):
        r01 = self.layout("rack/pop-chicago-cermak/r01")
        self.assertEqual([(u, label) for u, label, _ in r01], [
            (42, "r01-osp"), (40, "r01-cm-40"), (39, "r01-demarc"), (38, "r01-cm-38"), (36, "legacy-pe-a"),
            (35, "r01-cm-35"), (34, "ntp-01"), (32, "r01-blank-32"), (31, "agg-a"), (30, "mgmt-01"),
            (29, "console-01"), (28, "pe-a"), (27, "r01-cm-27"), (25, "ddos-01"), (23, "pe-a2"), (22, "r01-cm-22")])
        statuses = {label: status for _, label, status in r01}
        self.assertEqual(statuses["legacy-pe-a"], "decommissioning")
        self.assertEqual(statuses["ddos-01"], "staged")
        self.assertIn(statuses["pe-a2"], ("planned", "staged"))
        r02 = {label: (u, status) for u, label, status in self.layout("rack/pop-chicago-cermak/r02")}
        self.assertEqual(r02["agg-spare"][1], "inventory")
        self.assertEqual(r02["legacy-pe-b"][1], "decommissioning")
        self.assertEqual(self.objects["device/pop-chicago-cermak/legacy-pe-a"]["attrs"]["name"], "chcgilcr-rtr1")
        self.assertTrue(self.objects["device/pop-chicago-cermak/legacy-pe-a"]["meta"]["legacy_naming"])
        self.assertEqual(self.objects["device/pop-chicago-cermak/pe-a2"]["attrs"]["name"], "chicago-cermak-pe-a2")

    def test_stratigraphy_gate_and_mutations(self):
        self.assertEqual(stratigraphy_violations(self.plan), [])
        swapped = deepcopy(self.plan)
        objects = index(swapped)
        a, b = objects["device/pop-chicago-cermak/agg-a"], objects["device/pop-chicago-cermak/pe-a"]
        a["attrs"]["position"], b["attrs"]["position"] = b["attrs"]["position"], a["attrs"]["position"]
        self.assertTrue(any("installed later" in p for p in stratigraphy_violations(swapped)))
        unjournalled = deepcopy(self.plan)
        unjournalled["objects"] = [o for o in unjournalled["objects"]
                                   if o["key"] != "journal/rack/pop-chicago-cermak/r01/removed/original-console"]
        self.assertTrue(any("no rack journal" in p for p in stratigraphy_violations(unjournalled)))
        unblanked = deepcopy(self.plan)
        unblanked["objects"] = [o for o in unblanked["objects"] if o["key"] != "device/pop-chicago-cermak/r01-blank-32"]
        self.assertTrue(any("unblanked gap" in p for p in stratigraphy_violations(unblanked)))

    def test_not_in_service_uncabled(self):
        self.assertEqual(not_in_service_cabled(self.plan), [])
        mutated = deepcopy(self.plan)
        mutated["objects"].append(dict(key="cable/x", kind="cable", attrs={}, meta={}, refs=dict(
            a="device/pop-chicago-cermak/legacy-pe-a/if/fxp0", b="device/pop-chicago-cermak/mgmt-01/if/ge-0/0/20")))
        self.assertEqual(not_in_service_cabled(mutated), ["device/pop-chicago-cermak/legacy-pe-a"])

    def test_edge_pops_are_one_cabinet_and_no_reserved_racks(self):
        recipe_tl = {p["key"]: p for p in self.recipe["pops"]}
        racks = Counter(self.objects[r]["refs"]["site"] for r in pop_racks(self.objects))
        statuses = {self.objects[r]["attrs"]["status"] for r in pop_racks(self.objects)}
        self.assertEqual(statuses, {"active"})
        edge = [k for k in recipe_tl if racks[f"site/pop-{k}"] == 1]
        self.assertIn("milwaukee-oak-creek", edge)
        for key in edge:
            room = self.objects[f"location/pop-{key}"]["attrs"]["name"]
            self.assertTrue(room.startswith("Cabinet "), room)
            names = {o["key"].rsplit("/", 1)[1] for o in members(self.objects, f"rack/pop-{key}/r01")}
            self.assertLessEqual({"pe-a", "pe-b", "agg-a", "agg-b", "mgmt-01", "console-01", "r01-osp", "r01-demarc"}, names)
        self.assertIn("Cage contract: 4 cabinet positions",
                      self.objects["location/pop-chicago-cermak"]["attrs"]["description"])
        self.assertEqual([(u, label) for u, label, _ in self.layout("rack/pop-milwaukee-oak-creek/r01")], [
            (42, "r01-osp"), (40, "r01-cm-40"), (39, "r01-demarc"), (38, "r01-cm-38"), (37, "pe-a"), (36, "pe-b"),
            (35, "r01-cm-35"), (34, "agg-a"), (33, "agg-b"), (32, "mgmt-01"), (31, "console-01")])

    def test_timeline_story(self):
        from estates.model import World
        w = World(self.recipe, None)
        w.reservations = deepcopy(self.plan["reservations"])
        tl = timeline.of(w)
        self.assertEqual(tl.refresh_order[-1], "chicago-cermak")
        self.assertEqual([p for p, relic in tl.relic.items() if relic], ["chicago-cermak"])
        self.assertTrue(all(day < date(2023, 1, 1) for day in tl.launch.values()))
        self.assertEqual(tl.launch["chicago-cermak"].year, 2011)
        self.assertEqual(sorted(tl.metro_entry, key=tl.metro_entry.get), ["chicago", "detroit", "cleveland", "milwaukee"])
        self.assertEqual([p for p, plan in tl.mx304.items() if plan], ["chicago-cermak"])
        services = Counter(o["meta"].get("in_service", "")[:4] for o in self.objects.values()
                           if o["kind"] == "site" and o["key"].startswith("site/ce-"))
        self.assertLessEqual(max(services.values()) / sum(services.values()), 0.15, services)

    def test_cold_spare_matches_metro_aggregation(self):
        for o in self.objects.values():
            if o["kind"] == "device" and o["key"].endswith("/agg-spare"):
                site = o["refs"]["site"]
                self.assertEqual(o["meta"]["hardware"], self.objects[f"device/{site.removeprefix('site/')}/agg-a"]["meta"]["hardware"])
                self.assertEqual(o["refs"]["rack"], f"rack/{site.removeprefix('site/')}/r02")

    def test_panel_labels(self):
        port = self.objects["device/pop-chicago-cermak/r01-demarc/front/7"]
        self.assertEqual(port["attrs"]["label"], "G09-01.39:07")
        bad = [o["key"] for o in self.objects.values() if o["kind"] == "front_port" and "/pop-" in o["key"]
               and not re.fullmatch(r"[A-H]\d{2}-\d{2}\.\d{1,2}:\d{2}", o["attrs"].get("label", ""))]
        self.assertEqual(bad, [])

    def test_cage_floorplan_drawn_at_contract_size(self):
        plans = {f["location_slug"]: f for f in geometry.create(self.plan)["floorplans"]}
        cage = plans[self.objects["location/pop-chicago-cermak"]["attrs"]["slug"]]
        self.assertEqual(len(cage["shapes"]), 2)
        self.assertEqual(cage["width"], cage["shapes"][0]["x"] + fibre.CAGE_POSITIONS * geometry.RACK_WIDTH + geometry.MARGIN)

    @unittest.skipUnless(fibre.PE_LEGACY in __import__("json").loads(
        (SHOWCASE.parent.parent / "catalog" / "hardware.json").read_text())["models"],
        "STUB(WP-A): runtime catalog stubs change the hardware digest; runs once the catalog lands")
    def test_growth_never_moves_history(self):
        grown = deepcopy(self.recipe)
        grown["pops"].append(dict(key="milwaukee-bay-view", metro="milwaukee"))
        template = next(c for c in grown["customers"] if any(s["pop"] == "chicago-cermak" for s in c["sites"]))
        next(s for s in template["sites"] if s["pop"] == "chicago-cermak")["count"] += 1
        added = deepcopy(next(c for c in grown["customers"] if c.get("service") == "dia"))
        added["key"] = "lakeview-traders"
        added.pop("name", None)
        grown["customers"].append(added)
        grown_plan = generate(resolve_recipe(grown), self.plan)
        timeline_scopes = lambda plan: {k: v for k, v in plan["reservations"].items() if k.startswith("provider-timeline/")}
        after, ledger = index(grown_plan), timeline_scopes(grown_plan)
        for key, o in self.objects.items():
            if o["kind"] == "device" and o["refs"]["site"].startswith("site/pop-"):
                self.assertEqual((after[key]["attrs"].get("position"), after[key]["attrs"]["name"],
                                  after[key]["attrs"]["status"], after[key]["meta"].get("installed")),
                                 (o["attrs"].get("position"), o["attrs"]["name"], o["attrs"]["status"], o["meta"].get("installed")), key)
            if o["kind"] == "journal_entry" and o["meta"].get("history"):
                self.assertEqual(after[key]["attrs"], o["attrs"], key)
        old = timeline_scopes(self.plan)
        self.assertEqual({k: v for k, v in ledger.items() if k in old}, old)
        new = date.fromordinal(ledger["provider-timeline/launch/milwaukee-bay-view"]["day"])
        self.assertGreater(new, date(2026, 1, 1))
        layout = [o["meta"]["hardware"] for o in members(after, "rack/pop-milwaukee-bay-view/r01")]
        self.assertNotIn(fibre.PE_LEGACY, layout)
        self.assertFalse(any(a.startswith("blanking") for a in layout))


if __name__ == "__main__":
    unittest.main()
