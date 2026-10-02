"""The provider PoP plant (estates/fibre.py): elevation, panels, LAGs, cable policy.

Builds the default provider recipe's PoPs, spans, transit and cellular OOB
through the builders themselves (no customers, no enrichment), so these
checks pin the plant independently of the services and validator work.
Each shape check is paired with a mutation the check must catch.
"""

from copy import deepcopy
from datetime import date, timedelta
import unittest

from estates import fibre, geometry, operations, provider
from estates.blocks import foundation
from estates.model import DesignError, World


POPS = [dict(key="chicago-cermak", metro="chicago"), dict(key="chicago-west", metro="chicago"),
        dict(key="detroit-south", metro="detroit"), dict(key="cleveland-east", metro="cleveland")]


def plant_world():
    # Two Chicago PoPs give an owned dark-fibre span beside the leased waves.
    r = provider.resolve({"pops": POPS})
    w = World(r, None)
    entries = provider.premises(r)
    w.reserve_sites(["dc-01"] + [f"pop-{p['key']}" for p in r["pops"]] + [sid for sid, *_ in entries])
    for p in r["pops"]:
        w.reserve("provider-pop-order", p["key"], 64)
    metros = {p["key"]: p["metro"] for p in r["pops"]}
    w.provider_metros = {"dc-01": metros[r["noc_pop_a"]], **{f"pop-{k}": v for k, v in metros.items()},
                         **{sid: metros[pop] for sid, _, pop, _ in entries}}
    foundation(w, industry="provider", inherited=False, include_carriers=False,
               networks=("management", "applications", "database", "backup", "storage", "provider"),
               site_kinds={"pop", "customer", "dc"},
               device_roles=("provider-edge", "customer-edge", "wan-edge", "access", "spine", "leaf", "server",
                             "management", "pdu", "workstation", "patch-panel", "wall-outlet"),
               hardware_aliases={"provider-edge", "core", "leaf", "edge", "server", "access", "pdu",
                                 "console-server", "endpoint", "patch-panel", "wall-outlet"})
    provider._registry(w)
    pop_sites = {p["key"]: provider._pop(w, p) for p in r["pops"]}
    points = provider._points(w)
    spans = provider._plan_spans(w, points)
    launch = provider._launch(w, spans)
    provider._topology(w, pop_sites, points, spans, launch)
    order = w.reservations["provider-pop-order"]
    ordered = sorted(r["pops"], key=lambda p: order[p["key"]])
    for index, side in enumerate("ab"):
        pop = ordered[index]["key"]
        site, routers = pop_sites[pop]
        port = site.interface(routers[index], "xe-0/1/7")
        provider._circuit(w, f"circuit/transit/{side}", f"provider/transit-{side}", f"provider-account/provider/transit-{side}",
                          "transit", site, port, f"provider-network/transit/{side}", None, 10000,
                          cid=f"TEST-{side}", installed=date(2024, 1, 1))
    for p in ordered:
        provider._oob(w, pop_sites[p["key"]][0], p["key"], launch[p["key"]])
    return w, pop_sites


SPEC = {  # DESIGN.md §5, U by U: (position, name, model)
    "R01": [(42, "R01 OSP Panel", fibre.OSP_PANEL), (40, "R01 CM-40", fibre.CABLE_MANAGER_2U),
            (39, "R01 Colo Demarc", fibre.COLO_PANEL), (38, "R01 CM-38", fibre.CABLE_MANAGER_1U),
            (37, "pe-a", "provider-edge"), (36, "R01 CM-36", fibre.CABLE_MANAGER_1U),
            (35, "agg-a", fibre.AGGREGATION), (34, "mgmt-01", fibre.POP_MANAGEMENT), (33, "console-01", fibre.POP_OOB)],
    "R02": [(42, "R02 OSP Panel", fibre.OSP_PANEL), (40, "R02 CM-40", fibre.CABLE_MANAGER_2U),
            (39, "R02 Colo Demarc", fibre.COLO_PANEL), (38, "R02 CM-38", fibre.CABLE_MANAGER_1U),
            (37, "pe-b", "provider-edge"), (36, "R02 CM-36", fibre.CABLE_MANAGER_1U),
            (35, "agg-b", fibre.AGGREGATION)],
}


def elevation(objects, rack):
    rows = []
    for o in objects.values():
        if o["kind"] == "device" and o["refs"].get("rack") == rack and o["attrs"].get("position"):
            name = o["attrs"]["name"]
            label = name if name.startswith("R0") else o["key"].rsplit("/", 1)[-1]
            rows.append((o["attrs"]["position"], label, o["meta"]["hardware"]))
    return sorted(rows, reverse=True)


def utilization(w, rack):
    used = sum(w.catalog["models"][o["meta"]["hardware"]]["u_height"] for o in w.objects.values()
               if o["kind"] == "device" and o["refs"].get("rack") == rack and o["attrs"].get("position")
               and not w.catalog["models"][o["meta"]["hardware"]].get("exclude_from_utilization"))
    return used / w.obj(rack)["attrs"]["u_height"]


class PopPlant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w, cls.pops = plant_world()
        cls.objects = cls.w.objects
        cls.site = cls.pops["chicago-west"][0]

    def rack(self, name):
        return f"rack/{self.site.id}/{name.lower()}"

    def test_elevation_matches_the_design_table(self):
        for name, rows in SPEC.items():
            self.assertEqual(elevation(self.objects, self.rack(name)), rows, name)
        self.assertAlmostEqual(utilization(self.w, self.rack("R01")), 10 / 42)
        self.assertAlmostEqual(utilization(self.w, self.rack("R02")), 8 / 42)
        mutated = deepcopy(self.objects)
        mutated[f"device/{self.site.id}/agg-a"]["attrs"]["position"] = 32
        self.assertNotEqual(elevation(mutated, self.rack("R01")), SPEC["R01"])

    def test_cage_has_four_bays_two_reserved_and_bare(self):
        racks = sorted((o for o in self.objects.values() if o["kind"] == "rack" and o["refs"]["site"] == self.site.key),
                       key=lambda o: o["attrs"]["name"])
        self.assertEqual([(r["attrs"]["name"], r["attrs"]["status"], r["attrs"]["u_height"]) for r in racks],
                         [("R01", "active", 42), ("R02", "active", 42), ("R03", "reserved", 42), ("R04", "reserved", 42)])
        self.assertEqual({r["refs"]["location"] for r in racks}, {self.site.equipment_location})
        self.assertEqual([r["meta"]["position_m"] for r in racks], [[1.0, 1.0, 0], [1.6, 1.0, 0], [2.2, 1.0, 0], [2.8, 1.0, 0]])
        location = self.objects[self.site.equipment_location]
        self.assertTrue(location["attrs"]["name"].startswith("Cage "))
        self.assertEqual(self.objects[location["refs"]["parent"]]["meta"]["space_type"], "suite")
        for rack in racks[2:]:
            self.assertEqual(rack["attrs"]["description"], fibre.RESERVED_DESCRIPTION)
            self.assertNotIn("asset_tag", rack["attrs"])
            held = [o for o in self.objects.values() if o["refs"].get("rack") == rack["key"]]
            self.assertEqual(held, [], "a reserved position holds no device, PDU or feed")
        self.assertFalse(any(o["kind"] == "rack_reservation" for o in self.objects.values()))

    def test_hygiene_rule_counts_and_blanking(self):
        heights = {alias: self.w.catalog["models"][alias]["u_height"] for alias in fibre.ROLES}
        managers = [o for o in self.objects.values() if o["kind"] == "device" and o["refs"]["site"] == self.site.key
                    and o["refs"]["role"] == "role/cable-management"]
        self.assertEqual(len(managers), 6)
        self.assertFalse(any(o["meta"]["hardware"].startswith("blanking") for o in managers))
        # A 2U hole inside the band takes one blanking panel; a 3U hole none.
        gap = [(42, "x", fibre.CABLE_MANAGER_1U), (39, "y", fibre.CABLE_MANAGER_1U), (35, "z", fibre.CABLE_MANAGER_1U)]
        self.assertEqual(fibre._blanking(gap, heights), [(40, "blank-40", fibre.BLANKING_2U)])
        # Mutation: a PE directly above the aggregation switch must take a manager.
        rule = fibre.hygiene([("p", "provider-edge", "pe"), ("g", fibre.AGGREGATION, "agg")], heights)
        self.assertEqual([alias for _, _, alias in rule], ["provider-edge", fibre.CABLE_MANAGER_1U, fibre.AGGREGATION])
        rule = fibre.hygiene([("p", "provider-edge", "pe"), ("g", fibre.AGGREGATION, None)], heights)
        self.assertEqual(len(rule), 2)

    def test_panels_map_front_to_rear_one_to_one(self):
        panels = [o for o in self.objects.values() if o["kind"] == "device" and o["refs"]["site"] == self.site.key
                  and o["meta"]["hardware"] in (fibre.OSP_PANEL, fibre.COLO_PANEL)]
        self.assertEqual(len(panels), 4)
        for panel in panels:
            fronts = [o for o in self.objects.values() if o["kind"] == "front_port" and o["refs"]["device"] == panel["key"]]
            self.assertEqual(len(fronts), len(self.w.catalog["models"][panel["meta"]["hardware"]]["front_ports"]))
            for front in fronts:
                rear = self.objects[front["refs"]["rear_port"]]
                self.assertEqual(rear["key"], front["key"].replace("/front/", "/rear/"))
                self.assertEqual((rear["attrs"]["positions"], front["attrs"]["rear_port_position"]), (1, 1))
        colo = next(p for p in panels if p["meta"]["hardware"] == fibre.COLO_PANEL)
        self.assertEqual(self.objects[colo["refs"]["tenant"]]["attrs"]["name"], "Windward Interconnect")

    def test_lags_are_straight_four_member_and_carry_only_home_vlans(self):
        for side in "ab":
            for device, lag in ((f"agg-{side}", fibre.AGG_LAG), (f"pe-{side}", fibre.PE_LAG)):
                key = f"device/{self.site.id}/{device}/if/{lag}"
                members = [o for o in self.objects.values() if o["refs"].get("lag") == key]
                self.assertEqual(len(members), 4)
                self.assertEqual(self.objects[key]["refs"]["tagged_vlans"], [f"vlan/{self.site.id}/{side}/nid-management"])
                racks = {self.objects[self.objects[m["refs"]["device"]]["refs"]["rack"]]["attrs"]["name"] for m in members}
                self.assertEqual(racks, {"R0" + str(1 + "ab".index(side))})
        w = deepcopy(self.w)
        pop = self.site.id.removeprefix("pop-")
        vlan = w.add("vlan", "vlan/test", {"name": "t", "vid": 100}, {})
        fibre.carry(w, pop, "a", vlan)
        self.assertIn(vlan, w.obj(f"device/{self.site.id}/pe-a/if/ae1")["refs"]["tagged_vlans"])
        self.assertNotIn(vlan, w.obj(f"device/{self.site.id}/pe-b/if/ae1")["refs"]["tagged_vlans"])

    def test_nid_management_is_per_side_and_gatewayed_on_its_own_pe(self):
        pop = self.site.id.removeprefix("pop-")
        nets = []
        for side, vid in (("a", 4001), ("b", 4002)):
            vlan, net, gateway = fibre.nid_management(self.w, pop, side)
            nets.append(net)
            self.assertEqual(self.objects[vlan]["attrs"]["vid"], vid)
            self.assertEqual(net.prefixlen, 25)
            unit = self.objects[gateway]
            self.assertEqual((unit["attrs"]["name"], unit["refs"]["parent"], unit["refs"]["untagged_vlan"]),
                             (f"ae1.{vid}", f"device/{self.site.id}/pe-{side}/if/ae1", vlan))
            self.assertEqual(self.objects[f"ip/{gateway}"]["attrs"]["address"], f"{net[1]}/25")
        self.assertFalse(nets[0].overlaps(nets[1]))
        self.assertEqual(fibre.nid_host(deepcopy(self.w), pop, "a", "nid-x"), 2)

    def test_attachment_contract_and_ceiling(self):
        pop = self.site.id.removeprefix("pop-")
        got = fibre.attachment(self.w, pop, "b", 0)
        self.assertEqual(got["uni"], f"device/{self.site.id}/agg-b/if/xe-0/0/0")
        self.assertEqual(got["pe_lag"], f"device/{self.site.id}/pe-b/if/ae1")
        self.assertIn(got["uni"], self.objects)
        self.assertNotIn(got["uni"], {o["refs"][s] for o in self.objects.values() if o["kind"] == "cable" for s in "ab"})
        with self.assertRaisesRegex(DesignError, "MX304"):
            fibre.attachment(self.w, pop, "a", fibre.UNIS_PER_AGG)

    def test_every_pop_cable_follows_the_policy(self):
        # The gate is on the finished graph: data links take their medium's
        # colour in operations._cables; every functional colour is the plant's.
        w = deepcopy(self.w)
        operations._cables(w)
        for site, _ in self.pops.values():
            self.assertEqual(fibre.policy_gaps(w.objects, site.key), [])
        xc = [o for o in w.objects.values() if o["kind"] == "cable" and o["attrs"].get("description", "").startswith("Carrier-hotel")]
        osp = [o for o in w.objects.values() if o["kind"] == "cable" and o["attrs"].get("description", "").startswith("Operator outside")]
        self.assertTrue(xc and osp)
        self.assertEqual({o["attrs"]["color"] for o in xc}, {fibre.FUNCTION_COLORS["xc"]})
        self.assertEqual({o["attrs"]["color"] for o in osp}, {fibre.FUNCTION_COLORS["osp"]})
        cable = osp[0]
        del cable["attrs"]["length"]
        site = next(w.obj(w.obj(e)["refs"]["device"])["refs"]["site"] for e in cable["refs"].values() if "device" in w.obj(e)["refs"])
        self.assertEqual(fibre.policy_gaps(w.objects, site), [(cable["key"], ["length"])])

    def test_spans_land_on_panels(self):
        circuits = [o for o in self.objects.values() if o["kind"] == "circuit" and o["key"].startswith("circuit/backbone/")]
        kinds = set()
        for circuit in circuits:
            owned = circuit["refs"]["provider"] == "provider/operator"
            kinds.add(owned)
            for side in "AZ":
                term = self.objects[f"{circuit['key']}/{side}"]
                cable = next(o for o in self.objects.values() if o["kind"] == "cable" and term["key"] in o["refs"].values())
                rear = self.objects[next(v for v in cable["refs"].values() if v != term["key"])]
                self.assertEqual(rear["kind"], "rear_port")
                panel = self.objects[rear["refs"]["device"]]
                self.assertEqual(panel["meta"]["hardware"], fibre.OSP_PANEL if owned else fibre.COLO_PANEL)
                if not owned:
                    self.assertEqual(cable["attrs"]["label"], term["attrs"]["xconnect_id"])
                    self.assertIn("pp_info", term["attrs"])
                # Trace from the PE port back through the panel to this termination.
                front = rear["key"].replace("/rear/", "/front/")
                jumper = next(o for o in self.objects.values() if o["kind"] == "cable" and front in o["refs"].values())
                pe_port = next(v for v in jumper["refs"].values() if v != front)
                self.assertEqual(self.objects[pe_port]["refs"]["device"].rsplit("/", 1)[-1][:3], "pe-")
                self.assertEqual(fibre.far_end(self.objects, front), term["key"])
        self.assertEqual(kinds, {True, False}, "both an owned dark-fibre span and a carrier wave")

    def test_cellular_oob_is_uncabled_and_mark_connected(self):
        console = f"device/{self.site.id}/console-01"
        lte = next(o for o in self.objects.values() if o["kind"] == "interface" and o["refs"]["device"] == console
                   and o["attrs"]["type"] == "lte")
        self.assertNotIn(lte["key"], {o["refs"][s] for o in self.objects.values() if o["kind"] == "cable" for s in "ab"})
        self.assertTrue(self.objects[f"ip/{lte['key']}"]["attrs"]["address"].startswith("100.64."))
        term = self.objects[f"circuit/oob/{self.site.id.removeprefix('pop-')}/A"]
        self.assertTrue(term["attrs"]["mark_connected"])
        self.assertEqual(term["refs"]["termination"], self.site.key)
        self.assertEqual(self.objects[f"circuit/oob/{self.site.id.removeprefix('pop-')}"]["refs"]["type"],
                         "circuit-type/cellular-oob")

    def test_pdus_are_named_networked_and_on_l6_30_feeds(self):
        pdus = sorted(o["attrs"]["name"] for o in self.objects.values() if o["kind"] == "device"
                      and o["refs"]["site"] == self.site.key and o["refs"]["role"] == "role/pdu")
        self.assertEqual(pdus, ["R01 PDU-A", "R01 PDU-B", "R02 PDU-A", "R02 PDU-B"])
        feeds = [o for o in self.objects.values() if o["kind"] == "power_feed" and o["refs"]["rack"].startswith(f"rack/{self.site.id}/")]
        self.assertEqual(len(feeds), 4)
        self.assertEqual({(f["attrs"]["voltage"], f["attrs"]["amperage"]) for f in feeds}, {fibre.POP_FEED})
        cabled = {o["refs"][s] for o in self.objects.values() if o["kind"] == "cable" for s in "ab"}
        for pdu in (o for o in self.objects.values() if o["kind"] == "device" and o["refs"]["role"] == "role/pdu"
                    and o["refs"]["site"] == self.site.key):
            self.assertIn(f"{pdu['key']}/if/Network", cabled)
            self.assertIn("primary_ip4", pdu["refs"])

    def test_geometry_lays_out_the_cage_row(self):
        plan = {"objects": list(self.objects.values())}
        cage = next(f for f in geometry.create(plan)["floorplans"]
                    if f["location_slug"] == self.objects[self.site.equipment_location]["attrs"]["slug"])
        self.assertEqual(cage["layout"], "authored")
        self.assertEqual([(s["rack_name"], s["x"], s["y"]) for s in cage["shapes"]],
                         [("R01", 200, 200), ("R02", 260, 200), ("R03", 320, 200), ("R04", 380, 200)])
        geometry._intrinsic({"artifact": "floorplan-geometry", "floorplans": [cage]})

    def test_build_is_deterministic(self):
        again, _ = plant_world()
        self.assertEqual(again.objects, self.w.objects)
        self.assertEqual(again.reservations, self.w.reservations)


if __name__ == "__main__":
    unittest.main()
