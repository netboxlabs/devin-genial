"""Fibre-plant delivery lanes: panels, port mappings, LAG and Q-in-Q (DESIGN L9).

Each test pins one loader obligation and fails if that obligation is removed:
front/rear ports compile as device components without the legacy mapping
column, the mapping travels as a receipt-first REST rear_ports PATCH, review
history expects one PortMapping create per mapped front port, readback traces
pass-through and circuit ends through /paths/, and the LAG / Q-in-Q
references compile to their native columns.
"""

import json
from pathlib import Path
import tempfile
import unittest

from estates.turbobulk import (DEFERRED, DIRECT_REFS, SUPPORTED_REFS, LoadError,
                               _candidate_bucket_key, _complete_rest,
                               _expected_change_diff_counts, _finalizer_plan, _matches,
                               _render, _rendered_columns, _rest_patch_preflight,
                               _row_bucket_keys, _verify_paths, rest_mutation_requirements)


def _obj(kind, key, attrs=None, refs=None):
    return {"kind": kind, "key": key, "attrs": attrs or {}, "refs": refs or {}, "meta": {}}


SITE = _obj("site", "site", {"name": "S", "slug": "s"})
DEVICE = _obj("device", "panel", {"name": "P1"}, {"site": "site"})
REAR = _obj("rear_port", "panel/rear/1", {"name": "R1", "type": "lc-apc", "positions": 1},
            {"device": "panel"})
FRONT = _obj("front_port", "panel/front/1",
             {"name": "F1", "type": "lc-apc", "rear_port_position": 1},
             {"device": "panel", "rear_port": "panel/rear/1"})
OBJECTS = {o["key"]: o for o in (SITE, DEVICE, REAR, FRONT)}
IDS = {"site": 2, "panel": 10, "panel/rear/1": 31, "panel/front/1": 41}


class PanelCompilation(unittest.TestCase):
    def test_front_port_row_drops_legacy_mapping_and_keeps_component_caches(self):
        row = _render(FRONT, OBJECTS, IDS, {})
        self.assertNotIn("rear_port", row)
        self.assertNotIn("rear_port_position", row)
        self.assertNotIn("rear_port_id", row)
        self.assertEqual(row["positions"], 1)
        self.assertEqual((row["device_id"], row["_site_id"], row["_location_id"], row["_rack_id"]),
                         (10, 2, None, None))
        self.assertEqual(set(row), _rendered_columns(FRONT))
        rear = _render(REAR, OBJECTS, IDS, {})
        self.assertEqual((rear["positions"], rear["_site_id"]), (1, 2))
        self.assertEqual(set(rear), _rendered_columns(REAR))

    def test_mapping_is_a_declared_rest_completion(self):
        self.assertIn("rear_port", DEFERRED)
        self.assertEqual(rest_mutation_requirements(OBJECTS)["patch_fields"],
                         ["front_port.rear_port"])

    def test_completion_patches_rear_ports_once_and_refuses_a_foreign_mapping(self):
        plan = {"objects": list(OBJECTS.values())}

        class Target:
            def __init__(self, mapping):
                self.mapping = mapping
                self.patches = []

            def all(self, path):
                assert path == "/api/dcim/front-ports/", path
                return [{"id": 41, "rear_ports": self.mapping}]

            def request(self, path, **kwargs):
                self.patches.append((path, json.loads(kwargs["body"])))
                return 200, []

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            fresh = Target([])
            _complete_rest(fresh, plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)
            self.assertEqual(fresh.patches, [("/api/dcim/front-ports/", [
                {"id": 41, "rear_ports": [{"position": 1, "rear_port": 31,
                                           "rear_port_position": 1}]}])])
            done = Target([{"position": 1, "rear_port": {"id": 31}, "rear_port_position": 1}])
            _complete_rest(done, plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)
            self.assertEqual(done.patches, [])
            with self.assertRaisesRegex(LoadError, "rear_ports changed concurrently"):
                _complete_rest(Target([{"position": 1, "rear_port": 99, "rear_port_position": 1}]),
                               plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)

    def test_patch_preflight_requires_the_writable_rear_ports_field(self):
        class Target:
            def __init__(self, fields):
                self.fields = fields

            def request(self, path, **_kwargs):
                return 200, {"actions": {"PATCH": dict.fromkeys(self.fields)}}

        self.assertEqual(_rest_patch_preflight(Target({"rear_ports", "name"}), OBJECTS),
                         {"front_port": ["rear_ports"]})
        with self.assertRaisesRegex(LoadError, "cannot write: rear_ports"):
            _rest_patch_preflight(Target({"name"}), OBJECTS)

    def test_review_history_expects_one_portmapping_create_per_mapped_front_port(self):
        unmapped = _obj("front_port", "panel/front/2", {"name": "F2", "type": "lc-apc"},
                        {"device": "panel"})
        counts = _expected_change_diff_counts({**OBJECTS, unmapped["key"]: unmapped})
        self.assertEqual(counts["dcim.portmapping"], 1)
        self.assertEqual(counts["dcim.frontport"], 2)
        self.assertEqual(counts["dcim.rearport"], 1)

    def test_ports_match_by_name_and_device(self):
        for obj in (FRONT, REAR):
            row = {"id": IDS[obj["key"]], "name": obj["attrs"]["name"], "device": {"id": 10}}
            self.assertTrue(_matches(obj, row, IDS, OBJECTS))
            self.assertIn(_candidate_bucket_key(obj, IDS, OBJECTS),
                          _row_bucket_keys(obj["kind"], row))
            self.assertFalse(_matches(obj, {**row, "device": {"id": 11}}, IDS, OBJECTS))

    def test_finalizers_rebuild_links_paths_and_counters_for_cabled_panels(self):
        cable = _obj("cable", "c", {"label": "X1"}, {"a": "panel/rear/1", "b": "panel/front/1"})
        hooks = {(model, hook) for _purpose, model, hook in
                 _finalizer_plan({**OBJECTS, "c": cable})}
        self.assertLessEqual({("dcim.device", "fix_counters"),
                              ("dcim.cabletermination", "fix_cable_links"),
                              ("dcim.cabletermination", "rebuild_cable_paths")}, hooks)


class PathReadback(unittest.TestCase):
    def _run(self, a_kind, b_kind):
        objects = {
            "a": _obj(a_kind, "a", {"name": "A"}, {}),
            "b": _obj(b_kind, "b", {"name": "B"}, {}),
            "c": _obj("cable", "c", {"label": "XC-1"}, {"a": "a", "b": "b"}),
        }
        requested = []

        class Target:
            def request(self, path, **_kwargs):
                requested.append(path)
                return 200, [{"path": [[{"id": 1}], [{"id": 5, "label": "XC-1"}]]}]

        result = _verify_paths(Target(), {"objects": list(objects.values())}, objects,
                               {"a": 1, "b": 2, "c": 5}, workers=1)
        self.assertEqual(result["failures"], [])
        return requested[0]

    def test_trace_prefers_a_path_endpoint_on_either_side(self):
        self.assertEqual(self._run("interface", "front_port"), "/api/dcim/interfaces/1/trace/")
        self.assertEqual(self._run("rear_port", "interface"), "/api/dcim/interfaces/2/trace/")

    def test_passive_and_circuit_cables_read_their_cable_paths(self):
        self.assertEqual(self._run("rear_port", "circuit_termination"),
                         "/api/dcim/rear-ports/1/paths/")
        self.assertEqual(self._run("circuit_termination", "front_port"),
                         "/api/circuits/circuit-terminations/1/paths/")


class LagAndQinq(unittest.TestCase):
    def test_lag_and_service_vlan_compile_to_native_columns(self):
        self.assertEqual((DIRECT_REFS["lag"], DIRECT_REFS["qinq_svlan"]), ("lag_id", "qinq_svlan_id"))
        self.assertLessEqual({"lag", "qinq_svlan"}, SUPPORTED_REFS["interface"])
        self.assertIn("qinq_svlan", SUPPORTED_REFS["vlan"])
        objects = dict(OBJECTS)
        objects.update({o["key"]: o for o in (
            _obj("vlan", "svlan", {"vid": 3001, "name": "S", "qinq_role": "svlan"}, {"site": "site"}),
            _obj("vlan", "cvlan", {"vid": 10, "name": "C", "qinq_role": "cvlan"},
                 {"qinq_svlan": "svlan"}),
            _obj("interface", "ae0", {"name": "ae0", "type": "lag"}, {"device": "panel"}),
            _obj("interface", "xe0", {"name": "xe-0/0/40", "type": "10gbase-x-sfpp"},
                 {"device": "panel", "lag": "ae0"}),
            _obj("interface", "uni", {"name": "xe-0/0/1", "type": "10gbase-x-sfpp", "mode": "q-in-q"},
                 {"device": "panel", "qinq_svlan": "svlan"}),
        )})
        ids = {**IDS, "svlan": 70, "cvlan": 71, "ae0": 80, "xe0": 81, "uni": 82}
        self.assertEqual(_render(objects["xe0"], objects, ids, {})["lag_id"], 80)
        uni = _render(objects["uni"], objects, ids, {})
        self.assertEqual((uni["qinq_svlan_id"], uni["mode"]), (70, "q-in-q"))
        self.assertEqual(_render(objects["cvlan"], objects, ids, {})["qinq_svlan_id"], 70)
        self.assertIn("lag_id", _rendered_columns(objects["xe0"]))

    def test_vlan_identity_honours_site_group_and_service_vlan_scopes(self):
        objects = {o["key"]: o for o in (
            _obj("vlan", "grouped", {"vid": 4001, "name": "N"}, {"group": "g", "site": "site"}),
            _obj("vlan", "inner", {"vid": 10, "name": "C"}, {"qinq_svlan": "svlan"}),
        )}
        ids = {"g": 5, "site": 2, "svlan": 70}
        grouped, inner = objects["grouped"], objects["inner"]
        self.assertTrue(_matches(grouped, {"vid": 4001, "group": {"id": 5}, "site": {"id": 2}}, ids))
        self.assertFalse(_matches(grouped, {"vid": 4001, "group": {"id": 6}, "site": {"id": 2}}, ids))
        self.assertTrue(_matches(inner, {"vid": 10, "qinq_svlan": {"id": 70}}, ids))
        # The same VID under another S-VLAN, or unscoped, is a different VLAN.
        self.assertFalse(_matches(inner, {"vid": 10, "qinq_svlan": {"id": 71}}, ids))
        self.assertFalse(_matches(inner, {"vid": 10, "qinq_svlan": None, "site": {"id": 2}}, ids))
        self.assertIn(_candidate_bucket_key(inner, ids), _row_bucket_keys("vlan", {"vid": 10}))


if __name__ == "__main__":
    unittest.main()
