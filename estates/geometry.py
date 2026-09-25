"""Floorplan geometry sidecar for the physical-geometry plugin.

Like the drift twin, this is a derived artifact over a frozen ``plan.json``,
never a canonical graph change: ``build`` writes a deterministic floorplan,
layer and rack-shape layout bound to the plan's canonical SHA-256, ``check``
recomputes and byte-compares it, and ``seed`` writes the records through the
plugin's REST API with a private receipt and exact readback. Geometry records
are seeded and read back; nothing here claims what any visualization renders.
"""

from pathlib import Path
import argparse
import hashlib
import json
import os
import time
import urllib.parse

from .model import canonical, digest
from .turbobulk import Client, LoadError, _write_receipt

RECEIPT_VERSION = 1
WRITER_VERSION = "physical-geometry-1"
API = "/api/plugins/physical-geometry/"

# Authored layout constants (centimeters). Racks are laid out in rows in their
# plan appearance order: the generator's ledgers make rack allocation
# append-only under growth, so appending racks never moves existing shapes.
RACK_WIDTH = 60
RACK_DEPTH = 107
ROW_CAPACITY = 8
AISLE = 122
MARGIN = 100


class GeometryError(RuntimeError):
    """The artifact cannot be built, verified or seeded faithfully."""


def _floorplans(plan):
    """Derive one floorplan per rack-bearing location, deterministically."""
    objects = {obj["key"]: obj for obj in plan["objects"]}
    racks_by_location = {}
    for obj in plan["objects"]:
        if obj["kind"] == "rack":
            racks_by_location.setdefault(obj["refs"]["location"], []).append(obj)
    floorplans = []
    for location_key in sorted(racks_by_location,
                               key=lambda key: objects[key]["attrs"]["slug"]):
        location = objects[location_key]
        site = objects[location["refs"]["site"]]
        racks = racks_by_location[location_key]
        shapes = []
        for index, rack in enumerate(racks):
            row, column = divmod(index, ROW_CAPACITY)
            shapes.append({
                "rack_name": rack["attrs"]["name"],
                "asset_tag": rack["attrs"].get("asset_tag"),
                "type": "rack",
                "x": MARGIN + column * RACK_WIDTH,
                "y": MARGIN + row * (RACK_DEPTH + AISLE),
                "width": RACK_WIDTH,
                "depth": RACK_DEPTH,
                # Alternate rows face each other (hot/cold aisle pairs). The
                # plugin's accepted orientation values are confirmed against
                # live OPTIONS metadata at seed time; the artifact records the
                # intent only.
                "orientation_intent": 180 if row % 2 else 0,
            })
        rows = (len(racks) + ROW_CAPACITY - 1) // ROW_CAPACITY
        widest = min(len(racks), ROW_CAPACITY)
        floorplans.append({
            "site": site["attrs"]["name"],
            "location_slug": location["attrs"]["slug"],
            "location_name": location["attrs"]["name"],
            "name": location["attrs"]["name"],
            "base_unit": "cm",
            "width": 2 * MARGIN + widest * RACK_WIDTH,
            "depth": 2 * MARGIN + rows * RACK_DEPTH + (rows - 1) * AISLE,
            "layer": {"name": "Base"},
            "shapes": shapes,
        })
    return floorplans


def create(plan):
    return {
        "artifact": "floorplan-geometry",
        "schema_version": 1,
        "plan_sha256": digest(plan),
        "layout": {"unit": "cm", "rack_width": RACK_WIDTH, "rack_depth": RACK_DEPTH,
                   "row_capacity": ROW_CAPACITY, "aisle": AISLE, "margin": MARGIN,
                   "order": "plan appearance order per location; append-only under growth"},
        "floorplans": _floorplans(plan),
    }


def verify(artifact, plan):
    """Independent invariants plus exact recomputation from the bound plan."""
    if artifact.get("plan_sha256") != digest(plan):
        raise GeometryError("geometry artifact is not bound to this canonical plan")
    expected = create(plan)
    if canonical(artifact) != canonical(expected):
        raise GeometryError("geometry artifact differs from the layout recomputed "
                            "from the bound plan; rebuild it")
    racks_in_plan = {}
    for obj in plan["objects"]:
        if obj["kind"] == "rack":
            racks_in_plan.setdefault(obj["refs"]["location"], []).append(obj["attrs"]["name"])
    objects = {obj["key"]: obj for obj in plan["objects"]}
    slug_to_key = {objects[key]["attrs"]["slug"]: key for key in racks_in_plan}
    seen_slugs = set()
    for floorplan in artifact["floorplans"]:
        slug = floorplan["location_slug"]
        if slug in seen_slugs or slug not in slug_to_key:
            raise GeometryError(f"floorplan location {slug!r} is duplicated or has no plan racks")
        seen_slugs.add(slug)
        plan_racks = racks_in_plan[slug_to_key[slug]]
        shape_racks = [shape["rack_name"] for shape in floorplan["shapes"]]
        if sorted(shape_racks) != sorted(plan_racks) or len(set(shape_racks)) != len(shape_racks):
            raise GeometryError(f"floorplan {slug!r} shapes do not map one-to-one onto plan racks")
        footprints = []
        for shape in floorplan["shapes"]:
            if min(shape["x"], shape["y"]) < 0 or min(shape["width"], shape["depth"]) <= 0:
                raise GeometryError(f"floorplan {slug!r} has a non-positive shape footprint")
            box = (shape["x"], shape["y"], shape["x"] + shape["width"], shape["y"] + shape["depth"])
            for other in footprints:
                if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                    raise GeometryError(f"floorplan {slug!r} has overlapping rack footprints")
            footprints.append(box)
        if (max(box[2] for box in footprints) > floorplan["width"] - MARGIN
                or max(box[3] for box in footprints) > floorplan["depth"] - MARGIN):
            raise GeometryError(f"floorplan {slug!r} racks exceed the floor dimensions")
    if len(seen_slugs) != len(racks_in_plan):
        raise GeometryError("a rack-bearing plan location has no floorplan")
    return {"floorplans": len(artifact["floorplans"]),
            "shapes": sum(len(f["shapes"]) for f in artifact["floorplans"])}


def build(plan_path, out):
    plan = json.loads(Path(plan_path).read_text())
    artifact = create(plan)
    evidence = verify(artifact, plan)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "geometry.json").write_bytes(canonical(artifact) + b"\n")
    (out / "checks.json").write_bytes(canonical({
        "status": "passed", "scope": "offline geometry layout",
        "plan_sha256": artifact["plan_sha256"], **evidence}) + b"\n")
    return {"artifact": artifact["artifact"], "output": str(out), **evidence}


def check(directory, plan_path):
    directory = Path(directory)
    artifact = json.loads((directory / "geometry.json").read_text())
    plan = json.loads(Path(plan_path).read_text())
    evidence = verify(artifact, plan)
    expected = canonical(artifact) + b"\n"
    if (directory / "geometry.json").read_bytes() != expected:
        raise GeometryError("geometry.json bytes differ from their canonical form; rebuild")
    return {"artifact": artifact["artifact"], "checks": "layout verified", **evidence}


def _list(client, path, params=""):
    """Paginate a plugin endpoint without assuming ordering support."""
    rows, offset = [], 0
    while True:
        query = f"?limit=200&offset={offset}" + (f"&{params}" if params else "")
        _, page = client.request(path + query, branch=False)
        rows.extend(page.get("results", []))
        if not page.get("next") or not page.get("results"):
            return rows
        offset += 200


def _lookup_one(client, path, params, what):
    _, page = client.request(f"{path}?{params}", branch=False)
    results = page.get("results", [])
    if page.get("count") != 1 or len(results) != 1:
        raise LoadError(f"expected exactly one {what} on the target, found {page.get('count')}; "
                        "seed the estate first and keep identities unique")
    return results[0]


def _orientation_values(client):
    """Accepted orientation choice values from live OPTIONS metadata, or None."""
    try:
        _, options = client.request(API + "shapes/", method="OPTIONS", branch=False)
    except LoadError:
        return None
    field = (options.get("actions", {}).get("POST") or {}).get("orientation") or {}
    choices = field.get("choices")
    if not choices:
        return None
    return {str(choice.get("value")): choice.get("value") for choice in choices}


def seed(artifact_dir, *, url, token, receipt_path):
    if os.environ.get("GEOMETRY_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError("writing geometry requires GEOMETRY_WRITES=1 in the environment")
    directory = Path(artifact_dir)
    artifact = json.loads((directory / "geometry.json").read_text())
    if artifact.get("artifact") != "floorplan-geometry":
        raise LoadError(f"{directory} is not a floorplan-geometry artifact")
    geometry_sha = hashlib.sha256((directory / "geometry.json").read_bytes()).hexdigest()
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    plugin = status.get("plugins", {}).get("netbox_physical_geometry")
    if not plugin:
        raise LoadError("the target does not run the netbox_physical_geometry plugin")

    binding = {"receipt_version": RECEIPT_VERSION, "writer_version": WRITER_VERSION,
               "artifact": str(directory), "geometry_sha256": geometry_sha,
               "plan_sha256": artifact["plan_sha256"], "target": client.base,
               "plugin_version": plugin}
    receipt_path = Path(receipt_path)
    receipt = None
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; "
                                "choose a new receipt or rebuild the artifact")
    resuming = receipt is not None
    if receipt is None:
        receipt = {**binding, "started_at": _now(), "records": []}

    # Resolve every plan-side identity read-only before any write.
    resolved = []
    for floorplan in artifact["floorplans"]:
        location = _lookup_one(client, "/api/dcim/locations/",
                               "slug=" + urllib.parse.quote(floorplan["location_slug"]),
                               f"location with slug {floorplan['location_slug']!r}")
        racks = {}
        for shape in floorplan["shapes"]:
            rack = _lookup_one(
                client, "/api/dcim/racks/",
                f"location_id={location['id']}&name=" + urllib.parse.quote(shape["rack_name"]),
                f"rack {shape['rack_name']!r} in {floorplan['location_slug']!r}")
            if shape.get("asset_tag") and rack.get("asset_tag") != shape["asset_tag"]:
                raise LoadError(f"rack {shape['rack_name']!r} in {floorplan['location_slug']!r} "
                                "has a different asset tag than the bound plan")
            racks[shape["rack_name"]] = rack["id"]
        resolved.append((floorplan, location["id"], racks))

    existing_floorplans = _list(client, API + "floorplans/")
    by_location = {}
    for row in existing_floorplans:
        location_ref = row.get("location")
        location_id = location_ref.get("id") if isinstance(location_ref, dict) else location_ref
        by_location.setdefault(location_id, []).append(row)
    if not resuming:
        occupied = sorted(f["location_slug"] for f, location_id, _ in resolved
                          if by_location.get(location_id))
        if occupied:
            raise LoadError("these locations already carry floorplans; geometry seeding is "
                            "fresh-only (delete the plugin rows first): " + ", ".join(occupied))

    orientation_values = _orientation_values(client)
    receipt["orientation"] = ({"emitted": True, "values": sorted(orientation_values)}
                              if orientation_values is not None else
                              {"emitted": False,
                               "reason": "orientation choices unavailable from live OPTIONS metadata"})
    _write_receipt(receipt_path, receipt)

    def record(kind, identity, payload, endpoint, existing_id=None):
        entry = next((row for row in receipt["records"]
                      if row["kind"] == kind and row["identity"] == identity), None)
        if entry is None:
            entry = {"kind": kind, "identity": identity, "intent": payload,
                     "status": "submitting", "recorded_at": _now()}
            receipt["records"].append(entry)
        if entry.get("id"):
            return entry["id"]
        if existing_id:
            entry.update(id=existing_id, status="adopted")
            _write_receipt(receipt_path, receipt)
            return existing_id
        entry.update(intent=payload, status="submitting")
        _write_receipt(receipt_path, receipt)
        status_code, created = client.request(endpoint, method="POST",
                                              body=json.dumps(payload).encode(), branch=False)
        entry.update(id=created["id"], status="created", http_status=status_code)
        _write_receipt(receipt_path, receipt)
        return created["id"]

    started = time.monotonic()
    existing_layers = _list(client, API + "layers/") if resuming else []
    existing_shapes = _list(client, API + "shapes/") if resuming else []
    for floorplan, location_id, racks in resolved:
        identity = floorplan["location_slug"]
        prior = next((row for row in by_location.get(location_id, [])
                      if row.get("name") == floorplan["name"]), None) if resuming else None
        floorplan_id = record(
            "floorplan", identity,
            {"name": floorplan["name"], "location": location_id,
             "base_unit": floorplan["base_unit"],
             "width": floorplan["width"], "depth": floorplan["depth"]},
            API + "floorplans/", existing_id=prior and prior["id"])
        prior_layer = next((row for row in existing_layers
                            if _id(row.get("floorplan")) == floorplan_id
                            and row.get("name") == floorplan["layer"]["name"]), None)
        layer_id = record(
            "layer", f"{identity}/{floorplan['layer']['name']}",
            {"floorplan": floorplan_id, "name": floorplan["layer"]["name"]},
            API + "layers/", existing_id=prior_layer and prior_layer["id"])
        for shape in floorplan["shapes"]:
            rack_id = racks[shape["rack_name"]]
            payload = {"floorplan": floorplan_id, "layer": layer_id, "type": shape["type"],
                       # The 0.1.0-era footgun: object_type and object_id must
                       # travel together or the shape is silently unusable.
                       "object_type": "dcim.rack", "object_id": rack_id,
                       "x": shape["x"], "y": shape["y"],
                       "width": shape["width"], "depth": shape["depth"]}
            if orientation_values is not None and str(shape["orientation_intent"]) in orientation_values:
                payload["orientation"] = orientation_values[str(shape["orientation_intent"])]
            prior_shape = next((row for row in existing_shapes
                                if _id(row.get("floorplan")) == floorplan_id
                                and row.get("object_id") == rack_id), None)
            record("shape", f"{identity}/{shape['rack_name']}", payload,
                   API + "shapes/", existing_id=prior_shape and prior_shape["id"])

    verification = _readback(client, artifact, receipt)
    receipt.update(success=verification["mismatches"] == 0, completed_at=_now(),
                   wall_seconds=round(time.monotonic() - started, 6),
                   verification=verification)
    _write_receipt(receipt_path, receipt)
    if verification["mismatches"]:
        raise LoadError(f"geometry readback found {verification['mismatches']} mismatches; "
                        f"receipt: {receipt_path}")
    return {"success": True, "floorplans": len(artifact["floorplans"]),
            "shapes": verification["shapes"], "receipt": str(receipt_path)}


def _id(reference):
    return reference.get("id") if isinstance(reference, dict) else reference


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _readback(client, artifact, receipt):
    """Compare every emitted record against the live plugin rows exactly."""
    ids = {(row["kind"], row["identity"]): row["id"] for row in receipt["records"]}
    live_floorplans = {row["id"]: row for row in _list(client, API + "floorplans/")}
    live_layers = {row["id"]: row for row in _list(client, API + "layers/")}
    live_shapes = {row["id"]: row for row in _list(client, API + "shapes/")}
    mismatches, shapes = [], 0
    emit_orientation = receipt["orientation"]["emitted"]
    for floorplan in artifact["floorplans"]:
        identity = floorplan["location_slug"]
        live = live_floorplans.get(ids.get(("floorplan", identity)))
        if (live is None or live.get("name") != floorplan["name"]
                or live.get("width") != floorplan["width"] or live.get("depth") != floorplan["depth"]):
            mismatches.append(f"floorplan {identity}")
            continue
        floorplan_id = live["id"]
        layer = live_layers.get(ids.get(("layer", f"{identity}/{floorplan['layer']['name']}")))
        if layer is None or _id(layer.get("floorplan")) != floorplan_id:
            mismatches.append(f"layer {identity}")
        expected_shapes = {ids.get(("shape", f"{identity}/{shape['rack_name']}")): shape
                           for shape in floorplan["shapes"]}
        for shape_id, shape in expected_shapes.items():
            live_shape = live_shapes.get(shape_id)
            if (live_shape is None or _id(live_shape.get("floorplan")) != floorplan_id
                    or live_shape.get("object_id") is None
                    or any(live_shape.get(field) != shape[field]
                           for field in ("x", "y", "width", "depth"))):
                mismatches.append(f"shape {identity}/{shape['rack_name']}")
            else:
                shapes += 1
        extras = [row for row in live_shapes.values()
                  if _id(row.get("floorplan")) == floorplan_id and row["id"] not in expected_shapes]
        if extras:
            mismatches.append(f"unexpected shapes on floorplan {identity}: {len(extras)}")
    return {"mismatches": len(mismatches), "mismatch_sample": mismatches[:20],
            "shapes": shapes, "orientation_emitted": emit_orientation, "read_at": _now()}


def default_receipt(artifact_dir, target):
    origin = target.rstrip("/")
    slug = Path(artifact_dir).name or "geometry"
    suffix = hashlib.sha256(f"{slug}\n{origin}".encode()).hexdigest()[:12]
    return Path("build/load-receipts") / f"{slug}-geometry-{suffix}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Derive, verify and seed deterministic floorplan geometry for the physical-geometry plugin")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="derive the geometry artifact from a frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="recompute a saved geometry artifact from its bound plan")
    p.add_argument("directory", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("seed", help="write the artifact through the plugin REST API (GEOMETRY_WRITES=1)")
    p.add_argument("directory", type=Path)
    p.add_argument("target")
    p.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.plan, args.out)
            print(f"{result['artifact']}: {result['floorplans']} floorplans, "
                  f"{result['shapes']} rack shapes -> {result['output']}")
        elif args.command == "check":
            result = check(args.directory, args.plan)
            print(f"{result['artifact']}: {result['checks']} — {result['floorplans']} floorplans, "
                  f"{result['shapes']} rack shapes")
        else:
            token = os.environ.get("NETBOX_TOKEN")
            if not token:
                parser.error("NETBOX_TOKEN is required")
            receipt = args.receipt or default_receipt(args.directory, args.target)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            print(f"Receipt: {receipt}", flush=True)
            result = seed(args.directory, url=args.target, token=token, receipt_path=receipt)
            print(json.dumps(result, sort_keys=True))
        return 0
    except (GeometryError, LoadError, OSError, ValueError, KeyError) as exc:
        import sys
        print(f"Geometry failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
