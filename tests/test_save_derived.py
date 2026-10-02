"""The TurboBulk compiler writes the columns NetBox derives in save().

A raw bulk insert skips Model.save(), pre_save fields and post_save signals, so
the compiler compiles those columns into the original row and strict readback
proves them through NetBox's own serializer, filters and default ordering.
"""

import unittest
import urllib.parse

from estates.turbobulk import (_plan_prefix_hierarchy, _prefix_hierarchy, _render,
                               _rendered_columns, _verify_save_derived, naturalize_interface)


def obj(key, kind, attrs=None, refs=None):
    return {"key": key, "kind": kind, "attrs": attrs or {}, "refs": refs or {}}


class NaturalizeInterface(unittest.TestCase):
    # Golden values produced by netbox/utilities/ordering.py (v4.7.2) itself.
    GOLDEN = {
        "GigabitEthernet1/0/10": "0001000099999999GigabitEthernet000010............",
        "GigabitEthernet1/0/2": "0001000099999999GigabitEthernet000002............",
        "Ethernet1": "9999999999999999Ethernet000001............",
        "Ethernet1/1.100": "0001999999999999Ethernet000001......000100",
        "xe-0/0/1:2": "0000000099999999xe-000001000002......",
        "eth0": "9999999999999999eth000000............",
        "ae1.5": "9999999999999999ae000001......000005",
        "port 2 uplink": "9999999999999999port 000002............ uplink",
        "Ethernet1/1 lane 12": "0001999999999999Ethernet000001............ lane 00000012",
        "x" * 120 + "1": "9999999999999999" + "x" * 84,
    }

    def test_port_matches_netbox(self):
        for name, expected in self.GOLDEN.items():
            self.assertEqual(naturalize_interface(name, 100), expected, name)

    def test_interfaces_and_vm_interfaces_compile_name(self):
        objects = {"d": obj("d", "device", refs={"site": "s"})}
        for kind, parent in (("interface", "device"), ("vm_interface", "virtual_machine")):
            row = _render(obj("i", kind, {"name": "Ethernet1/1.100"}, {parent: "d"}),
                          objects, {"d": 1, "s": 2}, {})
            self.assertEqual(row["_name"], self.GOLDEN["Ethernet1/1.100"])
            self.assertIn("_name", _rendered_columns(obj("i", kind, {"name": "x"})))


class PrefixHierarchy(unittest.TestCase):
    def test_depth_and_children_follow_annotate_hierarchy_per_vrf(self):
        rows = [("agg", None, "10.0.0.0/8"), ("site", None, "10.1.0.0/16"),
                ("lan", None, "10.1.2.0/24"), ("host", None, "10.1.2.0/31"),
                ("vrf-lan", "v", "10.1.2.0/24"), ("v6", None, "2001:db8::/32"),
                ("v6-lan", None, "2001:db8:1::/64")]
        self.assertEqual(_prefix_hierarchy(rows), {
            "agg": (0, 3), "site": (1, 2), "lan": (2, 1), "host": (3, 0),
            # Another VRF is another table: nothing contains it.
            "vrf-lan": (0, 0), "v6": (0, 1), "v6-lan": (1, 0)})

    def test_duplicates_count_once_for_depth_and_per_row_for_children(self):
        rows = [("a", None, "10.0.0.0/8"), ("b", None, "10.0.0.0/8"), ("c", None, "10.1.0.0/16")]
        # depth: COUNT(DISTINCT prefix) of containers; children: COUNT(prefix).
        self.assertEqual(_prefix_hierarchy(rows), {"a": (0, 1), "b": (0, 1), "c": (1, 0)})

    def test_existing_target_containers_count_but_already_loaded_rows_do_not(self):
        objects = {o["key"]: o for o in (
            obj("vrf:1", "vrf"),
            obj("p:site", "prefix", {"prefix": "10.1.0.0/16"}),
            obj("p:lan", "prefix", {"prefix": "10.1.2.0/24"}, {"vrf": "vrf:1"}),
        )}
        existing = [{"id": 1, "prefix": "10.0.0.0/8", "vrf": None},       # foreign container
                    {"id": 2, "prefix": "10.1.0.0/16", "vrf": None},      # p:site, loaded earlier
                    {"id": 3, "prefix": "10.1.2.0/24", "vrf": {"id": 7}}]  # p:lan, loaded earlier
        hierarchy = _plan_prefix_hierarchy(objects, {"vrf:1": 7}, existing)
        self.assertEqual((hierarchy["p:site"], hierarchy["p:lan"]), ((1, 0), (0, 0)))

    def test_render_compiles_counters_and_scope_cache(self):
        objects = {o["key"]: o for o in (
            obj("region:p", "region"), obj("region:c", "region", refs={"parent": "region:p"}),
            obj("group:g", "site_group"),
            obj("site:1", "site", refs={"region": "region:c", "group": "group:g"}),
            obj("vrf:1", "vrf"),
            obj("p:outer", "prefix", {"prefix": "10.0.0.0/16"}, {"scope_site": "site:1"}),
            obj("p:inner", "prefix", {"prefix": "10.0.1.0/24"}),
            obj("p:vrf", "prefix", {"prefix": "10.0.1.0/24"}, {"vrf": "vrf:1"}),
        )}
        ids = {"region:p": 1, "region:c": 2, "group:g": 3, "site:1": 4, "vrf:1": 5}
        hierarchy = _plan_prefix_hierarchy(objects)
        outer = _render(objects["p:outer"], objects, ids, {"site": 9}, hierarchy=hierarchy)
        self.assertEqual({k: outer[k] for k in ("_depth", "_children", "_site_id", "_region_id",
                                                 "_site_group_id", "_location_id")},
                         {"_depth": 0, "_children": 1, "_site_id": 4, "_region_id": 2,
                          "_site_group_id": 3, "_location_id": None})
        self.assertEqual(_render(objects["p:vrf"], objects, ids, {})["_depth"], 0)
        self.assertNotIn("_site_id", _render(objects["p:inner"], objects, ids, {}))
        self.assertLessEqual({"_depth", "_children", "_site_id", "_region_id", "_site_group_id"},
                             _rendered_columns(objects["p:outer"]))


class SaveCopies(unittest.TestCase):
    def test_each_save_derived_column(self):
        objects = {o["key"]: o for o in (
            obj("site:1", "site"),
            obj("cluster:1", "cluster", {"name": "c"}, {"scope_site": "site:1"}),
            obj("wlan:1", "wireless_lan", {"ssid": "s"}, {"scope_site": "site:1"}),
            obj("dt:1", "device_type", {"airflow": "front-to-rear", "cooling_method": "air"}),
            obj("dev:1", "device", {"name": "d"}, {"device_type": "dt:1", "site": "site:1"}),
            obj("dev:2", "device", {"name": "d", "airflow": "rear-to-front"}, {"device_type": "dt:1"}),
            obj("vm:1", "virtual_machine", {"name": "v"}, {"cluster": "cluster:1"}),
            obj("range:1", "ip_range", {"start_address": "10.0.0.10/24", "end_address": "10.0.0.19/24"}),
            obj("feed:1", "power_feed", {"voltage": 208, "amperage": 30, "max_utilization": 80,
                                         "phase": "three-phase"}),
            obj("feed:2", "power_feed", {"voltage": 120, "amperage": 20, "max_utilization": 80,
                                         "phase": "single-phase"}),
            obj("radio:1", "interface", {"name": "wlan0", "rf_channel": "5g-36-5180-20"},
                {"device": "dev:1"}),
            obj("cable:1", "cable", {"label": "c", "length": 3, "length_unit": "ft"}),
            obj("vg:1", "vlan_group", {"name": "g"}, {"scope_site": "site:1"}),
        )}
        ids = {"site:1": 4, "cluster:1": 7, "dt:1": 8, "dev:1": 5}
        render = lambda key: _render(objects[key], objects, ids, {"site": 9})
        self.assertEqual(render("cluster:1")["_site_id"], 4)
        self.assertEqual(render("wlan:1")["_site_id"], 4)
        self.assertNotIn("_site_id", render("vg:1"))  # VLANGroup has no scope cache
        self.assertEqual((render("dev:1")["airflow"], render("dev:1")["cooling_method"]),
                         ("front-to-rear", "air"))
        self.assertEqual(render("dev:2")["airflow"], "rear-to-front")  # its own value wins
        self.assertEqual(render("vm:1")["site_id"], 4)
        self.assertEqual(render("range:1")["size"], 10)
        self.assertEqual(render("feed:1")["available_power"], 8646)  # round(208 * 30 * .8 * 1.732)
        self.assertEqual(render("feed:2")["available_power"], 1920)
        self.assertEqual((render("radio:1")["rf_channel_frequency"],
                          render("radio:1")["rf_channel_width"]), (5180.0, 20.0))
        self.assertEqual(render("cable:1")["_abs_length"], 0.9144)
        self.assertEqual((render("vg:1")["vid_ranges"], render("vg:1")["total_vlan_ids"]),
                         (["[1,4095)"], 4094))


class Readback(unittest.TestCase):
    OBJECTS = {o["key"]: o for o in (
        obj("region:1", "region"),
        obj("site:1", "site", refs={"region": "region:1"}),
        obj("p:outer", "prefix", {"prefix": "10.0.0.0/16"}, {"scope_site": "site:1"}),
        obj("p:inner", "prefix", {"prefix": "10.0.1.0/24"}),
        obj("feed:1", "power_feed", {"voltage": 120, "amperage": 20, "max_utilization": 80}),
        obj("dev:1", "device", {"name": "d"}, {"site": "site:1"}),
        obj("if:1", "interface", {"name": "Ethernet2"}, {"device": "dev:1"}),
        obj("if:2", "interface", {"name": "Ethernet10"}, {"device": "dev:1"}),
    )}
    IDS = {"region:1": 1, "site:1": 2, "p:outer": 10, "p:inner": 11, "feed:1": 20,
           "dev:1": 30, "if:1": 40, "if:2": 41}

    def inventory(self, depth=1):
        return {"prefix": [
            {"id": 10, "prefix": "10.0.0.0/16", "vrf": None, "_depth": 0, "children": 1},
            {"id": 11, "prefix": "10.0.1.0/24", "vrf": None, "_depth": depth, "children": 0},
        ]}

    class Target:
        def __init__(self, order=("Ethernet2", "Ethernet10"), site_rows=(10,), power=(20,)):
            self.order, self.site_rows, self.power = order, site_rows, power

        def all(self, path, ordering="id"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            if path.startswith("/api/dcim/interfaces/"):
                assert ordering is None, "natural order needs the model's Meta ordering"
                return [{"id": 40 + ("Ethernet10" == name), "name": name, "device": {"id": 30}}
                        for name in self.order]
            if "available_power" in query:
                return [{"id": i} for i in self.power] if query["available_power"] == ["1920"] else []
            if "site_id" in query or "region_id" in query:
                return [{"id": i} for i in self.site_rows]
            raise AssertionError(path)

    def check(self, target, inventory=None):
        return _verify_save_derived(target, self.OBJECTS, self.IDS, inventory or self.inventory())

    def test_matching_target_passes(self):
        result = self.check(self.Target())
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["checked"]["prefix?site_id"], 1)
        self.assertEqual(result["checked"]["interface._name"], 1)

    def test_failing_mutations(self):
        self.assertEqual(self.check(self.Target(), self.inventory(depth=0))["failures"][0]["field"],
                         "_depth")
        unsorted = self.check(self.Target(order=("Ethernet10", "Ethernet2")))["failures"]
        self.assertEqual(unsorted[0]["error"], "not in natural order")
        empty_cache = self.check(self.Target(site_rows=()))["failures"]
        self.assertEqual({f["filter"] for f in empty_cache}, {"site_id=2", "region_id=1"})
        leaked = self.check(self.Target(site_rows=(10, 11)))["failures"]
        self.assertEqual(leaked[0]["unexpected_ids"], [11])
        self.assertEqual(self.check(self.Target(power=()))["failures"][0]["filter"],
                         "available_power=1920")


if __name__ == "__main__":
    unittest.main()
