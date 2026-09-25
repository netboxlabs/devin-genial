"""The geometry sidecar must be deterministic, append-stable and honestly seeded."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from estates.geometry import (GeometryError, LoadError, MARGIN, RACK_DEPTH, RACK_WIDTH,
                              ROW_CAPACITY, build, check, create, seed, verify)
from estates.model import canonical


def _rack(location, name, tag):
    return {"kind": "rack", "key": f"rack/{location}/{name.lower()}",
            "attrs": {"name": name, "asset_tag": tag, "status": "active",
                      "u_height": 42, "width": 19},
            "refs": {"location": f"location/{location}", "site": "site/hq"}, "meta": {}}


def _plan(rack_names=("R01", "R02"), extra_location=False):
    objects = [
        {"kind": "site", "key": "site/hq",
         "attrs": {"name": "Headquarters", "slug": "acme-hq", "status": "active"},
         "refs": {}, "meta": {}},
        {"kind": "location", "key": "location/room-a",
         "attrs": {"name": "Equipment room", "slug": "acme-hq-room-a", "status": "active"},
         "refs": {"site": "site/hq"}, "meta": {}},
    ]
    objects += [_rack("room-a", name, f"tag-{name}") for name in rack_names]
    if extra_location:
        objects.append({"kind": "location", "key": "location/room-b",
                        "attrs": {"name": "Annex room", "slug": "acme-hq-room-b",
                                  "status": "active"},
                        "refs": {"site": "site/hq"}, "meta": {}})
        objects.append(_rack("room-b", "B01", "tag-B01"))
    return {"schema_version": 1, "generator_version": "fixture", "objects": objects}


class Layout(unittest.TestCase):
    def test_same_plan_builds_byte_identical_artifacts(self):
        plan = _plan()
        self.assertEqual(canonical(create(plan)), canonical(create(plan)))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            build(root / "plan.json", root / "one")
            build(root / "plan.json", root / "two")
            self.assertEqual((root / "one/geometry.json").read_bytes(),
                             (root / "two/geometry.json").read_bytes())

    def test_appending_racks_never_moves_existing_shapes(self):
        before = create(_plan(("R01", "R02")))
        grown = create(_plan(("R01", "R02", "R03", "R04"), extra_location=True))
        positions = {(f["location_slug"], s["rack_name"]): (s["x"], s["y"])
                     for f in grown["floorplans"] for s in f["shapes"]}
        for floorplan in before["floorplans"]:
            for shape in floorplan["shapes"]:
                self.assertEqual(positions[(floorplan["location_slug"], shape["rack_name"])],
                                 (shape["x"], shape["y"]))

    def test_rows_wrap_at_capacity_with_the_aisle(self):
        names = tuple(f"R{i:02d}" for i in range(ROW_CAPACITY + 1))
        artifact = create(_plan(names))
        shapes = artifact["floorplans"][0]["shapes"]
        self.assertEqual(shapes[0]["x"], MARGIN)
        self.assertEqual(shapes[ROW_CAPACITY - 1]["x"], MARGIN + (ROW_CAPACITY - 1) * RACK_WIDTH)
        wrapped = shapes[ROW_CAPACITY]
        self.assertEqual((wrapped["x"], wrapped["orientation_intent"]), (MARGIN, 180))
        self.assertGreater(wrapped["y"], MARGIN + RACK_DEPTH)

    def test_verify_rejects_an_overlap_mutation(self):
        plan = _plan()
        artifact = create(plan)
        artifact["floorplans"][0]["shapes"][1]["x"] = artifact["floorplans"][0]["shapes"][0]["x"]
        with self.assertRaisesRegex(GeometryError, "differs from the layout|overlapping"):
            verify(artifact, plan)

    def test_verify_rejects_a_dropped_shape_and_a_foreign_plan(self):
        plan = _plan()
        artifact = create(plan)
        mutated = copy.deepcopy(artifact)
        mutated["floorplans"][0]["shapes"].pop()
        with self.assertRaises(GeometryError):
            verify(mutated, plan)
        with self.assertRaisesRegex(GeometryError, "not bound"):
            verify(artifact, _plan(("R01",)))

    def test_check_rejects_tampered_bytes(self):
        plan = _plan()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            build(root / "plan.json", root / "out")
            artifact = json.loads((root / "out/geometry.json").read_text())
            artifact["floorplans"][0]["shapes"][0]["x"] += 1
            (root / "out/geometry.json").write_text(json.dumps(artifact))
            with self.assertRaises(GeometryError):
                check(root / "out", root / "plan.json")


class _FakeClient:
    """Answers the seeder's reads and remembers its writes."""

    def __init__(self, url, token, orientation_choices=None, occupied=False):
        self.base = url.rstrip("/")
        self.orientation_choices = orientation_choices
        self.created = {"floorplans": [], "layers": [], "shapes": []}
        self.posts = []
        self.next_id = 100
        self.occupied = occupied

    def request(self, path, method="GET", body=None, headers=None, branch=True):
        if method == "OPTIONS":
            field = ({"choices": self.orientation_choices}
                     if self.orientation_choices is not None else {})
            return 200, {"actions": {"POST": {"orientation": field}}}
        if method == "POST":
            payload = json.loads(body)
            kind = path.rstrip("/").rsplit("/", 1)[-1]
            self.next_id += 1
            row = dict(payload, id=self.next_id)
            self.created[kind].append(row)
            self.posts.append((kind, payload))
            return 201, row
        if path.startswith("/api/status/"):
            return 200, {"plugins": {"netbox_physical_geometry": "0.2.0"}}
        if path.startswith("/api/dcim/locations/"):
            return 200, {"count": 1, "results": [{"id": 7}]}
        if path.startswith("/api/dcim/racks/"):
            name = path.split("name=")[1]
            return 200, {"count": 1, "results": [{"id": 40 + len(name), "asset_tag": f"tag-{name}"}]}
        for kind in ("floorplans", "layers", "shapes"):
            if f"physical-geometry/{kind}/" in path:
                rows = list(self.created[kind])
                if kind == "floorplans" and self.occupied:
                    rows.append({"id": 1, "name": "Old plan", "location": {"id": 7}})
                return 200, {"count": len(rows), "results": rows, "next": None}
        raise AssertionError(f"unexpected request: {method} {path}")


class Seeder(unittest.TestCase):
    def _seed(self, client, plan=None):
        plan = plan or _plan()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            build(root / "plan.json", root / "out")
            with patch("estates.geometry.Client", return_value=client), \
                    patch.dict("os.environ", {"GEOMETRY_WRITES": "1"}):
                return seed(root / "out", url="http://t.example", token="x",
                            receipt_path=root / "receipt.json"), \
                    json.loads((root / "receipt.json").read_text())

    def test_write_gate_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(_plan()) + b"\n")
            build(root / "plan.json", root / "out")
            with patch.dict("os.environ", {"GEOMETRY_WRITES": ""}):
                with self.assertRaisesRegex(LoadError, "GEOMETRY_WRITES"):
                    seed(root / "out", url="http://t.example", token="x",
                         receipt_path=root / "receipt.json")

    def test_creates_floorplan_then_layer_then_shapes_with_full_references(self):
        client = _FakeClient("http://t.example", "x",
                             orientation_choices=[{"value": 0, "display_name": "0"},
                                                  {"value": 180, "display_name": "180"}])
        result, receipt = self._seed(client)
        self.assertTrue(result["success"])
        kinds = [kind for kind, _ in client.posts]
        self.assertEqual(kinds, ["floorplans", "layers", "shapes", "shapes"])
        floorplan_id = client.posts[0][1] and client.created["floorplans"][0]["id"]
        layer = client.created["layers"][0]
        self.assertEqual(layer["floorplan"], floorplan_id)
        for _, payload in client.posts[2:]:
            self.assertEqual(payload["floorplan"], floorplan_id)
            self.assertEqual(payload["layer"], layer["id"])
            self.assertEqual(payload["object_type"], "dcim.rack")
            self.assertIsInstance(payload["object_id"], int)
            self.assertIn("orientation", payload)
        self.assertTrue(receipt["orientation"]["emitted"])
        self.assertTrue(receipt["success"])

    def test_orientation_is_omitted_without_live_choice_metadata(self):
        client = _FakeClient("http://t.example", "x", orientation_choices=None)
        result, receipt = self._seed(client)
        self.assertTrue(result["success"])
        for _, payload in client.posts:
            self.assertNotIn("orientation", payload)
        self.assertFalse(receipt["orientation"]["emitted"])

    def test_occupied_location_is_refused_fresh(self):
        client = _FakeClient("http://t.example", "x", occupied=True)
        with self.assertRaisesRegex(LoadError, "already carry floorplans"):
            self._seed(client)


if __name__ == "__main__":
    unittest.main()
