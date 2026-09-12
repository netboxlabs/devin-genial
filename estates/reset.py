"""Safely replace one disposable NetBox Branching branch."""

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .turbobulk import Client, LoadError, _write_receipt


RECEIPT_VERSION = 1
BRANCHES = "/api/plugins/branching/branches/"
TRANSITIONAL = {"provisioning", "syncing", "migrating", "merging", "reverting"}
TERMINAL_FAILURES = {"failed", "merged", "archived", "pending-migrations"}
TRANSPORT_ERRORS = (OSError, EOFError, json.JSONDecodeError, http.client.HTTPException)


class HTTPRejected(LoadError):
    """The target returned a definite HTTP rejection for a request."""

    def __init__(self, method, path, status, detail):
        self.status = status
        super().__init__(f"{method} {path} returned HTTP {status}: {detail}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _status(row):
    value = row.get("status")
    return value.get("value") if isinstance(value, dict) else value


def _request_json(client, path, *, method="GET", body=None, allowed=(), include_headers=False):
    """Issue a request while accepting empty DELETE responses."""
    headers = dict(client.headers)
    headers.pop("X-NetBox-Branch", None)
    if body is not None:
        body = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(client.base + path, data=body, headers=headers, method=method)
    try:
        with client.opener.open(request, timeout=120) as response:
            payload = response.read()
            result = (response.status, json.loads(payload) if payload else None)
            return (*result, response.headers) if include_headers else result
    except urllib.error.HTTPError as exc:
        payload = exc.read(2000)
        if exc.code in allowed:
            result = (exc.code, json.loads(payload) if payload else None)
            return (*result, exc.headers) if include_headers else result
        detail = payload.decode(errors="replace")
        raise HTTPRejected(method, path, exc.code, detail) from exc


def _allowed(headers):
    value = headers.get("Allow", "")
    return {method.strip().upper() for method in value.split(",") if method.strip()}


def _capability_preflight(client, branch_id):
    """Require Branching's read-only create metadata and detail DELETE capability."""
    try:
        _, list_metadata, list_headers = _request_json(
            client, BRANCHES, method="OPTIONS", include_headers=True)
        _, _, detail_headers = _request_json(
            client, f"{BRANCHES}{branch_id}/", method="OPTIONS", include_headers=True)
    except TRANSPORT_ERRORS as exc:
        raise LoadError(f"reset capability preflight could not complete: {exc}") from exc
    list_allow, detail_allow = _allowed(list_headers), _allowed(detail_headers)
    actions = list_metadata.get("actions", {}) if isinstance(list_metadata, dict) else {}
    if "POST" not in list_allow or "POST" not in actions:
        raise LoadError("reset preflight did not prove branch create permission")
    if "DELETE" not in detail_allow:
        raise LoadError("reset preflight did not prove branch delete capability")
    return {"checked_at": _now(), "list_allow": sorted(list_allow),
            "list_actions": sorted(actions), "detail_allow": sorted(detail_allow)}


def _rows(client, name):
    path = BRANCHES + "?name=" + urllib.parse.quote(name, safe="")
    return [row for row in client.all(path) if row.get("name") == name]


def _one(rows, name):
    if len(rows) != 1:
        raise LoadError(f"expected exactly one branch named {name!r}; found {len(rows)}")
    return rows[0]


def _slug(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "branch"


def _receipt_path(target, branch, old_id, root=Path("build/reset-receipts")):
    binding = json.dumps({"target": target.rstrip("/"), "branch": branch,
                          "old_branch_id": old_id}, sort_keys=True, separators=(",", ":"))
    suffix = hashlib.sha256(binding.encode()).hexdigest()[:12]
    return root / f"{_slug(branch)}-{suffix}.json"


def _read_receipt(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LoadError(f"cannot read reset receipt {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LoadError(f"reset receipt {path} is not a JSON object")
    return value


def _unfinished_receipt(target, branch, root):
    matches = []
    if root.exists():
        for path in root.glob("*.json"):
            receipt = _read_receipt(path)
            if (not receipt.get("success") and receipt.get("target") == target.rstrip("/")
                    and receipt.get("branch") == branch):
                matches.append((path, receipt))
    if len(matches) > 1:
        raise LoadError(f"found {len(matches)} unfinished reset receipts for this target and branch")
    return matches[0] if matches else (None, None)


def _observe(receipt, row=None, *, event):
    receipt.setdefault("observations", []).append({
        "at": _now(), "event": event, "branch_id": row.get("id") if row else None,
        "status": _status(row) if row else "absent",
    })


def _record_error(receipt, path, message):
    receipt.update(success=False, failed_at=_now(), error=str(message))
    _write_receipt(path, receipt)


def _archive_load_receipts(receipt, receipt_path, load_root):
    archive = receipt.setdefault("load_receipts", [])
    if not receipt.get("load_receipt_archive"):
        destination = load_root / "history" / ("reset-" + receipt["operation_id"])
        receipt["load_receipt_archive"] = str(destination)
        for source in sorted(load_root.glob("*.json")) if load_root.exists() else []:
            try:
                value = json.loads(source.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise LoadError(f"cannot inspect load receipt {source}: {exc}") from exc
            if (value.get("target", "").rstrip("/") == receipt["target"]
                    and value.get("branch") == receipt["branch"]
                    and value.get("branch_id") == receipt["old_branch"].get("schema_id")):
                archive.append({"source": str(source), "destination": str(destination / source.name),
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                "status": "pending"})
        _write_receipt(receipt_path, receipt)

    for item in archive:
        source, destination = Path(item["source"]), Path(item["destination"])
        if item.get("status") == "moved":
            if not destination.exists():
                raise LoadError(f"archived load receipt is missing: {destination}")
            continue
        if source.exists() and destination.exists():
            raise LoadError(f"load receipt exists at both source and archive: {source}")
        if not source.exists() and not destination.exists():
            raise LoadError(f"load receipt disappeared during reset: {source}")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != item["sha256"]:
            raise LoadError(f"archived load receipt checksum changed: {destination}")
        item.update(status="moved", moved_at=_now())
        _write_receipt(receipt_path, receipt)
    receipt["stage"] = "load-receipts-archived"
    _write_receipt(receipt_path, receipt)


def _delete_old(client, receipt, receipt_path):
    old = receipt["old_branch"]
    status, row = _request_json(client, f"{BRANCHES}{old['id']}/", allowed=(404,))
    if status == 404:
        _observe(receipt, event="old-branch-absent")
        receipt.update(stage="deleted", deleted_at=_now())
        _write_receipt(receipt_path, receipt)
        return
    if not isinstance(row, dict) or row.get("id") != old["id"]:
        raise LoadError("checkpointed branch detail no longer identifies the old branch")
    if row.get("name") != receipt["branch"]:
        raise LoadError("checkpointed old branch was renamed; refusing to treat it as deleted")
    if _status(row) != "ready":
        raise LoadError(f"checkpointed old branch is {_status(row)!r}, not ready; refusing delete")
    evidence = _capability_preflight(client, old["id"])
    receipt.setdefault("capability_preflights", []).append(evidence)
    _observe(receipt, row, event="before-delete")
    _write_receipt(receipt_path, receipt)
    try:
        status, _ = _request_json(client, f"{BRANCHES}{old['id']}/", method="DELETE")
    except HTTPRejected as exc:
        receipt.update(delete_outcome="rejected", delete_http_status=exc.status)
        _record_error(receipt, receipt_path, exc)
        raise LoadError(f"branch DELETE was rejected with HTTP {exc.status}; nothing was resubmitted") from exc
    except TRANSPORT_ERRORS as exc:
        receipt["delete_outcome"] = "ambiguous"
        _record_error(receipt, receipt_path, exc)
        raise LoadError("branch DELETE outcome is ambiguous; rerun with the same receipt to inspect by ID") from exc
    receipt.update(delete_http_status=status, delete_outcome="accepted", stage="deleted", deleted_at=_now())
    receipt.pop("error", None)
    receipt.pop("failed_at", None)
    _write_receipt(receipt_path, receipt)


def _create_new(client, receipt, receipt_path):
    rows = _rows(client, receipt["branch"])
    if receipt.get("new_branch"):
        row = _one(rows, receipt["branch"])
        if row.get("id") != receipt["new_branch"]["id"]:
            raise LoadError("recreated branch ID changed outside this reset receipt")
        return row
    if rows:
        raise LoadError("a branch appeared after delete but before its create response was checkpointed; outcome is ambiguous")
    if receipt.get("create_outcome") == "ambiguous":
        raise LoadError("branch POST outcome remains ambiguous; inspect or remove the branch before retrying")

    receipt.update(stage="create-intent", create_started_at=_now())
    _write_receipt(receipt_path, receipt)
    try:
        status, row = _request_json(client, BRANCHES, method="POST", body={
            "name": receipt["branch"], "description": receipt["old_branch"].get("description") or ""})
    except HTTPRejected as exc:
        receipt.update(create_outcome="rejected", create_http_status=exc.status)
        _record_error(receipt, receipt_path, exc)
        raise LoadError(f"branch POST was rejected with HTTP {exc.status}; creation was not accepted") from exc
    except TRANSPORT_ERRORS as exc:
        receipt["create_outcome"] = "ambiguous"
        _record_error(receipt, receipt_path, exc)
        raise LoadError("branch POST outcome is ambiguous; rerun only to inspect, never to resubmit") from exc
    if not isinstance(row, dict) or not row.get("id") or row.get("name") != receipt["branch"]:
        receipt["create_outcome"] = "ambiguous"
        _record_error(receipt, receipt_path, "create response did not identify the requested branch")
        raise LoadError("branch create response was incomplete; outcome is ambiguous")
    receipt.update(create_http_status=status, create_outcome="accepted", stage="created",
                   new_branch={"id": row["id"], "schema_id": row.get("schema_id")}, created_at=_now())
    receipt.pop("error", None)
    receipt.pop("failed_at", None)
    _observe(receipt, row, event="created")
    _write_receipt(receipt_path, receipt)
    return row


def _wait_ready(client, receipt, receipt_path, timeout, poll_interval):
    branch_id = receipt["new_branch"]["id"]
    deadline = time.monotonic() + timeout
    while True:
        status, row = _request_json(client, f"{BRANCHES}{branch_id}/", allowed=(404,))
        if status == 404 or not isinstance(row, dict):
            raise LoadError("recreated branch disappeared while waiting for readiness")
        if row.get("name") != receipt["branch"]:
            raise LoadError("recreated branch ID no longer has the requested name")
        state = _status(row)
        _observe(receipt, row, event="poll")
        receipt["new_branch"].update(schema_id=row.get("schema_id"), status=state)
        _write_receipt(receipt_path, receipt)
        if state == "ready":
            receipt.update(stage="complete", success=True, result="reset", completed_at=_now())
            receipt.pop("error", None)
            receipt.pop("failed_at", None)
            _write_receipt(receipt_path, receipt)
            return receipt
        if state in TERMINAL_FAILURES:
            raise LoadError(f"recreated branch reached {state!r}, not ready")
        if state not in TRANSITIONAL | {"new"}:
            raise LoadError(f"recreated branch has unknown status {state!r}")
        if time.monotonic() >= deadline:
            raise LoadError(f"recreated branch did not become ready within {timeout}s; rerun the same command to resume polling")
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def reset(target, branch, *, token, timeout=900, receipt_path=None, poll_interval=2,
          receipt_root=Path("build/reset-receipts"), load_receipt_root=Path("build/load-receipts")):
    branch = branch.strip()
    if not branch or branch.casefold() == "main":
        raise LoadError("refusing to reset a blank or main branch")
    client = Client(target, token)
    _, target_status = client.request("/api/status/", branch=False)
    if not target_status.get("plugins", {}).get("netbox_branching"):
        raise LoadError("target does not report the NetBox Branching plugin")

    receipt = None
    path = Path(receipt_path) if receipt_path else None
    if path and path.exists():
        receipt = _read_receipt(path)
    elif not path:
        path, receipt = _unfinished_receipt(client.base, branch, Path(receipt_root))
    if receipt:
        for key, expected in (("target", client.base), ("branch", branch), ("receipt_version", RECEIPT_VERSION)):
            if receipt.get(key) != expected:
                raise LoadError(f"reset receipt has different {key}")
        if receipt.get("success"):
            row = _one(_rows(client, branch), branch)
            if (row.get("id") != receipt.get("new_branch", {}).get("id")
                    or _status(row) != "ready"):
                raise LoadError("successful reset receipt no longer matches the ready recreated branch")
            return receipt
    else:
        old = _one(_rows(client, branch), branch)
        state = _status(old)
        if state != "ready":
            raise LoadError(f"branch {branch!r} is {state!r}, not ready; refusing delete")
        path = path or _receipt_path(client.base, branch, old["id"], Path(receipt_root))
        if path.exists():
            raise LoadError(f"reset receipt already exists unexpectedly: {path}")
        receipt = {
            "receipt_version": RECEIPT_VERSION, "operation_id": uuid.uuid4().hex,
            "target": client.base, "branch": branch, "started_at": _now(),
            "receipt_path": str(path), "stage": "delete-intent", "success": False,
            "target_contract": {"netbox": target_status.get("netbox-version"),
                                "branching": target_status["plugins"]["netbox_branching"]},
            "old_branch": {"id": old["id"], "schema_id": old.get("schema_id"),
                           "status": state, "description": old.get("description", "")},
            "observations": [], "load_receipts": [],
        }
        _observe(receipt, old, event="selected")
        _write_receipt(path, receipt)

    try:
        if receipt["stage"] in {"delete-intent"}:
            _delete_old(client, receipt, path)
        if receipt["stage"] == "deleted":
            _archive_load_receipts(receipt, path, Path(load_receipt_root))
        if receipt["stage"] in {"load-receipts-archived", "create-intent", "created"}:
            _create_new(client, receipt, path)
        if receipt["stage"] == "created":
            return _wait_ready(client, receipt, path, timeout, poll_interval)
        if receipt["stage"] == "complete":
            return receipt
        raise LoadError(f"reset receipt has unsupported stage {receipt['stage']!r}")
    except LoadError as exc:
        _record_error(receipt, path, exc)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replace one disposable NetBox branch")
    parser.add_argument("target", help="NetBox http(s) origin")
    parser.add_argument("branch", help="exact disposable branch name")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not token:
        parser.error("NETBOX_TOKEN is required")
    try:
        result = reset(args.target, args.branch, token=token, timeout=args.timeout,
                       receipt_path=args.receipt)
        print(json.dumps({"success": result["success"], "result": result.get("result"),
                          "receipt": result["receipt_path"],
                          "new_schema_id": result["new_branch"]["schema_id"]}, sort_keys=True))
        return 0
    except (LoadError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Reset failed: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
