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


def _rack(location, name, tag, position_m=None):
    return {"kind": "rack", "key": f"rack/{location}/{name.lower()}",
            "attrs": {"name": name, "asset_tag": tag, "status": "active",
                      "u_height": 42, "width": 19},
            "refs": {"location": f"location/{location}", "site": "site/hq"},
            "meta": {} if position_m is None else {"position_m": list(position_m)}}


def _plan(rack_names=("R01", "R02"), extra_location=False, positions=None):
    objects = [
        {"kind": "site", "key": "site/hq",
         "attrs": {"name": "Headquarters", "slug": "acme-hq", "status": "active"},
         "refs": {}, "meta": {}},
        {"kind": "location", "key": "location/room-a",
         "attrs": {"name": "Equipment room", "slug": "acme-hq-room-a", "status": "active"},
         "refs": {"site": "site/hq"}, "meta": {}},
    ]
    positions = positions or {}
    objects += [_rack("room-a", name, f"tag-{name}", positions.get(name))
                for name in rack_names]
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

    def test_authored_positions_take_precedence_over_derived_rows(self):
        plan = _plan(("R01", "R02"),
                     positions={"R01": (4.0, 4.0, 0), "R02": (4 + 1.2 * 3, 34.0, 0)})
        artifact = create(plan)
        floorplan = artifact["floorplans"][0]
        self.assertEqual(floorplan["layout"], "authored")
        by_name = {shape["rack_name"]: shape for shape in floorplan["shapes"]}
        self.assertEqual((by_name["R01"]["x"], by_name["R01"]["y"]),
                         (MARGIN + 400, MARGIN + 400))
        # 1.2 * 3 metres is not float-exact; the conversion must still land on
        # the authored 760 cm.
        self.assertEqual((by_name["R02"]["x"], by_name["R02"]["y"]),
                         (MARGIN + 760, MARGIN + 3400))
        self.assertEqual({shape["orientation_intent"] for shape in floorplan["shapes"]}, {0})
        self.assertEqual(floorplan["depth"], MARGIN + 3400 + RACK_DEPTH + MARGIN)

    def test_appending_an_authored_rack_never_moves_existing_shapes(self):
        before = create(_plan(("R01",), positions={"R01": (4.0, 4.0, 0)}))
        grown = create(_plan(("R01", "R02"),
                             positions={"R01": (4.0, 4.0, 0), "R02": (5.2, 7.0, 0)}))
        first = before["floorplans"][0]["shapes"][0]
        same = next(shape for shape in grown["floorplans"][0]["shapes"]
                    if shape["rack_name"] == "R01")
        self.assertEqual((first["x"], first["y"]), (same["x"], same["y"]))

    def test_mixed_authored_and_unpositioned_racks_are_refused(self):
        plan = _plan(("R01", "R02"), positions={"R01": (4.0, 4.0, 0)})
        with self.assertRaisesRegex(GeometryError, "mixes authored"):
            create(plan)

    def test_floorplan_names_carry_the_site(self):
        artifact = create(_plan())
        self.assertEqual(artifact["floorplans"][0]["name"],
                         "Headquarters — Equipment room")

    def test_rows_wrap_at_capacity_with_the_aisle(self):
        names = tuple(f"R{i:02d}" for i in range(ROW_CAPACITY + 1))
        artifact = create(_plan(names))
        floorplan = artifact["floorplans"][0]
        self.assertEqual(floorplan["layout"], "rows")
        shapes = floorplan["shapes"]
        self.assertEqual(shapes[0]["x"], MARGIN)
        self.assertEqual(shapes[ROW_CAPACITY - 1]["x"], MARGIN + (ROW_CAPACITY - 1) * RACK_WIDTH)
        wrapped = shapes[ROW_CAPACITY]
        self.assertEqual((wrapped["x"], wrapped["orientation_intent"]), (MARGIN, 180))
        self.assertGreater(wrapped["y"], MARGIN + RACK_DEPTH)

    def test_verify_rejects_an_overlap_mutation(self):
        plan = _plan()
        artifact = create(plan)
        artifact["floorplans"][0]["shapes"][1]["x"] = artifact["floorplans"][0]["shapes"][0]["x"]
        with self.assertRaisesRegex(GeometryError, "overlapping"):
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

    def test_check_rejects_a_missing_or_unbound_checks_file(self):
        plan = _plan()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(plan) + b"\n")
            build(root / "plan.json", root / "out")
            (root / "out/checks.json").write_text(json.dumps({"status": "failed"}))
            with self.assertRaisesRegex(GeometryError, "checks.json"):
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
        self.rack_ids = {}
        self.corrupt_object_ids = False

    def all(self, path):
        _, page = self.request(path + "?limit=1000&ordering=id", branch=False)
        return page.get("results", [])

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
            # Every rack resolves to its own distinct id; a shared id would
            # blind the readback to a rebound object_id.
            rack_id = self.rack_ids.setdefault(name, 40 + len(self.rack_ids) + 1)
            return 200, {"count": 1, "results": [{"id": rack_id, "asset_tag": f"tag-{name}"}]}
        for kind in ("floorplans", "layers", "shapes"):
            if f"physical-geometry/{kind}/" in path:
                rows = [dict(row) for row in self.created[kind]]
                if kind == "shapes" and self.corrupt_object_ids:
                    for row in rows:
                        row["object_id"] = None
                if kind == "floorplans" and self.occupied:
                    rows.append({"id": 1, "name": "Old plan", "location": {"id": 7}})
                return 200, {"count": len(rows), "results": rows, "next": None}
        raise AssertionError(f"unexpected request: {method} {path}")


class Seeder(unittest.TestCase):
    def _build(self, root, plan=None):
        plan = plan or _plan()
        (root / "plan.json").write_bytes(canonical(plan) + b"\n")
        build(root / "plan.json", root / "out")

    def _seed(self, client, root):
        with patch("estates.geometry.Client", return_value=client), \
                patch.dict("os.environ", {"GEOMETRY_WRITES": "1"}):
            return seed(root / "out", url="http://t.example", token="x",
                        receipt_path=root / "receipt.json"), \
                json.loads((root / "receipt.json").read_text())

    def test_write_gate_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            with patch.dict("os.environ", {"GEOMETRY_WRITES": ""}):
                with self.assertRaisesRegex(LoadError, "GEOMETRY_WRITES"):
                    seed(root / "out", url="http://t.example", token="x",
                         receipt_path=root / "receipt.json")

    def test_creates_floorplan_then_layer_then_shapes_with_full_references(self):
        client = _FakeClient("http://t.example", "x",
                             orientation_choices=[{"value": 0, "display_name": "0"},
                                                  {"value": 180, "display_name": "180"}])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            result, receipt = self._seed(client, root)
        self.assertTrue(result["success"])
        kinds = [kind for kind, _ in client.posts]
        self.assertEqual(kinds, ["floorplans", "layers", "shapes", "shapes"])
        floorplan_id = client.created["floorplans"][0]["id"]
        layer = client.created["layers"][0]
        self.assertEqual(layer["floorplan"], floorplan_id)
        rack_ids = set()
        for _, payload in client.posts[2:]:
            self.assertEqual(payload["floorplan"], floorplan_id)
            self.assertEqual(payload["layer"], layer["id"])
            self.assertEqual(payload["object_type"], "dcim.rack")
            self.assertIsInstance(payload["object_id"], int)
            rack_ids.add(payload["object_id"])
            self.assertIn("orientation", payload)
        self.assertEqual(len(rack_ids), 2)
        self.assertTrue(receipt["orientation"]["emitted"])
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["verification"]["mismatches"], 0)

    def test_readback_fails_when_the_plugin_drops_object_ids(self):
        client = _FakeClient("http://t.example", "x")
        client.corrupt_object_ids = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            with self.assertRaisesRegex(LoadError, "mismatches"):
                self._seed(client, root)
            receipt = json.loads((root / "receipt.json").read_text())
        self.assertFalse(receipt["success"])
        self.assertGreater(receipt["verification"]["mismatches"], 0)

    def test_orientation_is_omitted_without_live_choice_metadata(self):
        client = _FakeClient("http://t.example", "x", orientation_choices=None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            result, receipt = self._seed(client, root)
        self.assertTrue(result["success"])
        for _, payload in client.posts:
            self.assertNotIn("orientation", payload)
        self.assertFalse(receipt["orientation"]["emitted"])

    def test_orientation_receipt_is_honest_when_choices_never_match(self):
        client = _FakeClient("http://t.example", "x",
                             orientation_choices=[{"value": "front", "display_name": "Front"}])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            result, receipt = self._seed(client, root)
        self.assertTrue(result["success"])
        for _, payload in client.posts:
            self.assertNotIn("orientation", payload)
        self.assertFalse(receipt["orientation"]["emitted"])

    def test_tampered_artifact_refuses_before_any_write(self):
        client = _FakeClient("http://t.example", "x")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            artifact = json.loads((root / "out/geometry.json").read_text())
            artifact["floorplans"][0]["shapes"][1]["x"] = artifact["floorplans"][0]["shapes"][0]["x"]
            (root / "out/geometry.json").write_text(json.dumps(artifact))
            with patch("estates.geometry.Client", return_value=client), \
                    patch.dict("os.environ", {"GEOMETRY_WRITES": "1"}):
                with self.assertRaisesRegex(GeometryError, "overlapping"):
                    seed(root / "out", url="http://t.example", token="x",
                         receipt_path=root / "receipt.json")
        self.assertEqual(client.posts, [])

    def test_occupied_location_is_refused_fresh(self):
        client = _FakeClient("http://t.example", "x", occupied=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            with self.assertRaisesRegex(LoadError, "did not create"):
                self._seed(client, root)

    def test_resume_repeats_no_writes_and_still_verifies(self):
        client = _FakeClient("http://t.example", "x")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            result, _ = self._seed(client, root)
            self.assertTrue(result["success"])
            client.posts.clear()
            result, receipt = self._seed(client, root)
        self.assertTrue(result["success"])
        self.assertEqual(client.posts, [])
        self.assertTrue(receipt["success"])

    def test_a_foreign_floorplan_is_refused_on_resume_too(self):
        client = _FakeClient("http://t.example", "x")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            result, _ = self._seed(client, root)
            self.assertTrue(result["success"])
            client.occupied = True
            with self.assertRaisesRegex(LoadError, "did not create"):
                self._seed(client, root)


class SeederContentType(unittest.TestCase):
    def test_every_post_declares_json(self):
        """The live plugin 415s form-encoded bodies; every POST must say JSON."""
        import inspect
        from estates import geometry
        source = inspect.getsource(geometry)
        posts = source.count('method="POST"')
        self.assertGreater(posts, 0)
        self.assertEqual(source.count('"Content-Type": "application/json"'), posts)



if __name__ == "__main__":
    unittest.main()
