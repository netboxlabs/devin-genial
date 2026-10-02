"""Artifact-scoped teardown of one estate from a dedicated tenant's main.

The inverse of the main-seed delivery policy.  A seeded main has no supported
way back, which makes iterating on a visualization demo impossible: every
improved estate needs a platform-side wipe first.  This module deletes exactly
what one frozen artifact put there and nothing else.

Scoping is the safety property.  The same strict readback the loader uses to
prove a load landed (``lab.verify.verify_plan``) resolves every plan object to
its target row; only those IDs are ever deleted, and every row the plan does
not claim is reported and left alone.  A target whose plan-matched inventory
does not look like the artifact is refused before any write.

NetBox protects referenced rows rather than cascading, so deletion walks the
loader's dependency phases in reverse.  This is destructive and irreversible:
it is for a dedicated demo or visualization tenant, never for one whose data
matters.
"""

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
import time

from .branch import retire_namespace_rows
from .naming import dedicated
from .diode import _phases
from .model import digest
from .turbobulk import (Client, LoadError, REST_PATCH_ROWS, SPECS, _artifact,
                        _readback_fields, _write_receipt)
from lab.verify import ENDPOINTS, fetch_inventory, verify_plan


RECEIPT_VERSION = 1
TEARDOWN_VERSION = "main-teardown-1"
TRUE = {"1", "true", "yes", "on"}
# A bulk DELETE rolls the whole batch back on any failure, so a batch is also
# the retry unit; keep it at the loader's bounded-write size.
DELETE_BATCH_ROWS = REST_PATCH_ROWS
# Cable terminations vanish with their cable and have no independent endpoint
# delete in the loader's forward path; deleting the cable removes both.
IMPLICIT_KINDS = {"cable_termination"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _enabled(name):
    return os.environ.get(name, "").strip().lower() in TRUE


def deletion_order(objects):
    """Return canonical keys grouped into batches, dependents first.

    The loader creates along ``_phases`` so that every reference exists before
    the row that needs it; removal must run the other way or NetBox's PROTECT
    rejects the parent while a child still points at it.
    """
    phases = list(_phases(objects))
    return [list(phase) for phase in reversed(phases)]


def _namespace(plan):
    recipe = plan.get("recipe") or {}
    namespace = recipe.get("namespace")
    if not namespace:
        raise LoadError("artifact has no recipe namespace; refusing to guess what to retire")
    return namespace


def _plan_rows(verification, objects):
    """Resolve plan objects to target rows, grouped by kind, dependents first.

    Only ``verify_plan``'s matched identities are returned: a row the plan does
    not claim can never enter a delete batch.
    """
    ids = verification.get("ids") or {}
    ordered = []
    for phase in deletion_order(objects):
        rows = []
        for key in phase:
            ref = ids.get(key)
            kind = objects[key]["kind"]
            if ref is None or kind in IMPLICIT_KINDS:
                continue
            rows.append({"key": key, "kind": kind, "id": ref["id"]})
        if rows:
            ordered.append(rows)
    return ordered


def _batches(rows, size):
    """Split one phase into per-kind bounded batches.

    Bulk DELETE is per endpoint, so a batch never mixes kinds; within a kind the
    order is irrelevant because the phase already cleared their dependents.
    """
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
    out = []
    for kind in sorted(by_kind):
        values = by_kind[kind]
        for offset in range(0, len(values), size):
            out.append((kind, values[offset:offset + size]))
    return out


def _existing_ids(client, kind, ids):
    """Which of these IDs the target still holds.

    A bulk DELETE rejects the whole batch when any listed object is missing, so
    a resume must never resend an ID it already removed.  Re-reading is also
    what makes an interrupted run idempotent without trusting the receipt.
    """
    endpoint = f"/api/{ENDPOINTS[kind]}/"
    wanted = sorted(ids)
    found = set()
    for offset in range(0, len(wanted), 200):
        chunk = wanted[offset:offset + 200]
        query = endpoint + "?brief=1&limit=1000&" + "&".join(f"id={value}" for value in chunk)
        _, page = client.request(query, branch=False)
        found.update(row["id"] for row in page.get("results", []))
    return found


def _delete_batch(client, kind, ids):
    """Bulk-delete one kind's IDs, then prove the outcome by re-reading.

    NetBox's bulk endpoint takes ``[{"id": N}, …]`` and rolls the whole batch
    back on any failure, reporting a dependency conflict as 409 with the
    protecting objects named — that list is the actionable part of a PROTECT
    failure.  A success returns 204 with an empty body, which the shared client
    surfaces as a parse error, so the response alone cannot distinguish success
    from failure; the target's own state can.  Returns ``(deleted, error)``.
    """
    endpoint = f"/api/{ENDPOINTS[kind]}/"
    payload = json.dumps([{"id": value} for value in sorted(ids)]).encode()
    reported = None
    try:
        client.request(endpoint, method="DELETE", body=payload,
                       headers={"Content-Type": "application/json"}, branch=False)
    except LoadError as exc:
        reported = str(exc)[:600]
    remaining = _existing_ids(client, kind, ids)
    if not remaining:
        return len(ids), None
    return len(ids) - len(remaining), reported or (
        f"{len(remaining)} of {len(ids)} {kind} rows survived the delete")


def _survivors(client, plan, objects, kinds):
    """Re-read every emitted kind and report plan rows that are still present."""
    inventory = fetch_inventory(client.base, client.token, kinds,
                                None, fields_by_kind=_readback_fields(plan), workers=4)
    verification = verify_plan(plan, inventory)
    remaining = []
    for key, ref in (verification.get("ids") or {}).items():
        if objects[key]["kind"] in IMPLICIT_KINDS:
            continue
        remaining.append({"key": key, "kind": ref["kind"], "id": ref["id"],
                          "endpoint": f"/api/{ENDPOINTS[ref['kind']]}/{ref['id']}/"})
    return remaining


def default_receipt(artifact, target):
    """Stable private receipt path for one artifact/target teardown."""
    plan_path, _, plan, _, _ = _artifact(artifact)
    import hashlib
    binding = json.dumps({"canonical_sha256": digest(plan), "target": target.rstrip("/"),
                          "operation": "main-teardown"}, sort_keys=True, separators=(",", ":"))
    suffix = hashlib.sha256(binding.encode()).hexdigest()[:12]
    name = plan_path.parent.name if plan_path.name == "plan.json" else plan_path.stem
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "estate"
    return Path("build/load-receipts") / f"{slug}-teardown-{suffix}.json"


def teardown(artifact, *, url, token, receipt_path, branch="", confirm=False,
             explain=False, match_threshold=0.5):
    """Delete one artifact's rows from a target's main, dependents first."""
    started = time.monotonic()
    if branch:
        raise LoadError("teardown removes rows from main; a branch is deleted with "
                        "just branch-delete or just retire, not here")
    plan_path, raw, plan, objects, _ = _artifact(artifact)
    kinds = sorted({obj["kind"] for obj in objects.values()} & ENDPOINTS.keys())
    unsupported = sorted({obj["kind"] for obj in objects.values()} - ENDPOINTS.keys())
    if unsupported:
        raise LoadError("no strict readback mapping for canonical kinds: " + ", ".join(unsupported))

    client = Client(url, token)
    client.request("/api/status/", branch=False)
    inventory = fetch_inventory(client.base, client.token, kinds, None,
                               fields_by_kind=_readback_fields(plan), workers=4)
    verification = verify_plan(plan, inventory)
    matched = verification.get("matched_objects", 0)
    claimable = sum(1 for obj in objects.values() if obj["kind"] not in IMPLICIT_KINDS)
    ratio = matched / claimable if claimable else 0.0
    foreign = verification.get("unmatched_target_ids") or {}

    plan_phases = _plan_rows(verification, objects)
    planned = [batch for phase in plan_phases for batch in _batches(phase, DELETE_BATCH_ROWS)]
    by_kind = Counter()
    for _, rows in planned:
        by_kind[rows[0]["kind"]] += len(rows)

    summary = {
        "artifact": str(plan_path), "canonical_sha256": digest(plan), "target": client.base,
        "operation": "main-teardown", "teardown_version": TEARDOWN_VERSION,
        "namespace": _namespace(plan),
        "canonical_objects": len(objects), "claimable_objects": claimable,
        "matched_objects": matched, "match_ratio": round(ratio, 4),
        "delete_by_kind": dict(sorted(by_kind.items())),
        "delete_total": sum(by_kind.values()), "delete_batches": len(planned),
        "foreign_rows_left_alone": {kind: len(values) for kind, values in sorted(foreign.items())},
    }
    if explain:
        return {"success": True, "result": "explained", **summary}

    # A bound receipt from a previous run on this exact target and artifact is
    # stronger evidence of identity than any live count, and by design a
    # partially torn-down target no longer matches its own artifact. Load it
    # before the ratio gate so a resume is possible at all; without one, a
    # target that does not substantially hold this artifact is the wrong
    # target and is refused before any write.
    receipt_path = Path(receipt_path)
    receipt = {"receipt_version": RECEIPT_VERSION, **summary,
               "started_at": _now(), "batches": [], "retired_rows": [], "success": False}
    resuming = False
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        for key in ("canonical_sha256", "target", "operation"):
            if previous.get(key) != receipt[key]:
                raise LoadError(f"receipt {receipt_path} has different {key}; choose a new receipt")
        receipt["batches"] = previous.get("batches", [])
        receipt["retired_rows"] = previous.get("retired_rows", [])
        resuming = bool(receipt["batches"] or receipt["retired_rows"])

    if ratio < match_threshold and not resuming:
        raise LoadError(
            f"target holds only {matched} of {claimable} objects from this artifact "
            f"({ratio:.1%}); refusing to tear down a target that does not look like it. "
            "Check the artifact and target, or run the explain recipe to inspect.")
    if not _enabled("ALLOW_MAIN_TEARDOWN"):
        raise LoadError("deleting an estate from main requires ALLOW_MAIN_TEARDOWN=1 in the environment")
    if not confirm:
        raise LoadError(
            f"refusing to delete {summary['delete_total']} rows without --confirm; "
            "run the explain recipe first to review what would be removed")

    done = {entry["purpose"] for entry in receipt["batches"] if entry.get("deleted")}
    _write_receipt(receipt_path, receipt)

    for number, (kind, rows) in enumerate(planned, 1):
        purpose = f"delete:{kind}:batch-{number}"
        if purpose in done:
            continue
        ids = [row["id"] for row in rows]
        # Re-resolve: a resumed run must not resend an already-deleted ID, and
        # the bulk endpoint rejects the whole batch when one is missing.
        present = _existing_ids(client, kind, ids)
        entry = {"purpose": purpose, "kind": kind, "requested": len(ids),
                 "present": len(present), "intent_at": _now()}
        receipt["batches"].append(entry)
        _write_receipt(receipt_path, receipt)
        if not present:
            entry.update(deleted=0, status="already-absent", completed_at=_now())
            _write_receipt(receipt_path, receipt)
            continue
        deleted, error = _delete_batch(client, kind, present)
        if error:
            entry.update(deleted=deleted, status="failed", error=error, completed_at=_now())
            receipt.update(failed_at=_now(), error=f"{kind}: {error}")
            _write_receipt(receipt_path, receipt)
            raise LoadError(
                f"deleting {kind} failed: {error}. NetBox protects referenced rows, so a "
                "dependent object it names must be removed first; the receipt preserves "
                "the attempt and the same command resumes.")
        entry.update(deleted=deleted, status="deleted", completed_at=_now())
        print(f"{purpose}: {deleted:,} rows deleted", flush=True)
        _write_receipt(receipt_path, receipt)

    # The Branching-exempt main-scoped rows the estate also wrote; branch.py
    # owns this exact order and the exact-name matching that keeps it from
    # reaching a customer's own webhook.
    if not receipt["retired_rows"]:
        receipt["retired_rows"] = retire_namespace_rows(
            client, receipt["namespace"], dedicated=dedicated(plan.get("recipe") or {}))
        _write_receipt(receipt_path, receipt)

    remaining = _survivors(client, plan, objects, kinds)
    receipt.update(survivors=remaining, completed_at=_now(),
                   wall_seconds=round(time.monotonic() - started, 6),
                   success=not remaining)
    _write_receipt(receipt_path, receipt)
    if remaining:
        raise LoadError(
            f"{len(remaining)} of this artifact's rows survived teardown, first: "
            + ", ".join(f"{row['kind']} {row['endpoint']}" for row in remaining[:5])
            + ". The receipt lists them all.")
    return {"success": True, "result": "torn-down", "deleted": summary["delete_total"],
            "retired_rows": len(receipt["retired_rows"]),
            "receipt": str(receipt_path), **summary}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Delete one artifact's estate from a dedicated tenant's main")
    parser.add_argument("artifact", help="the generated directory or plan.json that was seeded")
    parser.add_argument("target", nargs="?", default=os.environ.get("NETBOX_URL"),
                        help="NetBox http(s) origin")
    parser.add_argument("--receipt", type=Path,
                        help="private checkpoint receipt (default is stable per artifact and target)")
    parser.add_argument("--branch", default="", help="rejected; teardown is main-only")
    parser.add_argument("--confirm", action="store_true",
                        help="actually delete; without it the run stops after the summary")
    parser.add_argument("--explain", action="store_true", help="summarize with zero writes")
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not args.target or not token:
        parser.error("target/NETBOX_URL and NETBOX_TOKEN are required")
    receipt = args.receipt or default_receipt(args.artifact, args.target)
    try:
        if args.confirm and not args.explain:
            print("MAIN TEARDOWN: permanently deleting this artifact's rows from main; "
                  "only for a dedicated demo tenant.", file=os.sys.stderr, flush=True)
        result = teardown(args.artifact, url=args.target, token=token, receipt_path=receipt,
                          branch=args.branch, confirm=args.confirm, explain=args.explain)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (LoadError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Teardown failed: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
