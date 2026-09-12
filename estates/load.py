"""Choose and run a target-aware loader for one frozen estate artifact.

The Justfile is the public operator interface.  This module keeps transport
selection deterministic and refuses any target that cannot represent the
complete canonical graph.
"""

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .diode import _PRIMARY_IPS, _deferred_fields, _phases
from .model import digest
from .turbobulk import (Client, LoadError, SPECS, _artifact, _branch,
                        _numeric_ids, _schema_preflight, _verify_paths, _write_receipt,
                        load as load_turbobulk)
from lab.verify import ENDPOINTS, fetch_inventory, verify_plan


RECEIPT_VERSION = 1
ADAPTER_VERSION = "remote-diode-2"
TRUE = {"1", "true", "yes", "on"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _enabled(name):
    return os.environ.get(name, "").strip().lower() in TRUE


def _write(path, value):
    _write_receipt(Path(path), value)


def _diode_manifest(plan_path, plan):
    directory = plan_path.parent / "diode"
    path = directory / "manifest.json"
    if not path.is_file():
        return None, [f"missing generated Diode manifest: {path}"]
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, [f"invalid Diode manifest: {exc}"]
    reasons = []
    if manifest.get("format_version") != 1:
        reasons.append("unsupported Diode manifest format")
    if manifest.get("plan_sha256") != digest(plan):
        reasons.append("Diode manifest is not bound to this canonical plan")
    files = {entry.get("path"): entry for entry in manifest.get("files", [])
             if isinstance(entry, dict)}
    mentioned = [name for phase in manifest.get("phases", []) for name in phase.get("files", [])]
    if len(files) != len(manifest.get("files", [])) or sorted(files) != sorted(mentioned):
        reasons.append("Diode manifest file inventory is inconsistent")
    for name, entry in files.items():
        if not isinstance(name, str) or not re.fullmatch(r"phase-\d{3}-part-\d{4}\.json", name):
            reasons.append(f"invalid Diode request filename: {name!r}")
            break
        file = directory / str(name)
        if (not file.is_file() or file.stat().st_size != entry.get("bytes")
                or hashlib.sha256(file.read_bytes()).hexdigest() != entry.get("sha256")):
            reasons.append(f"Diode request checksum mismatch: {name}")
            break
    return (directory, manifest), reasons


def _target(url, token, branch):
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    plugins = status.get("plugins", {})
    row = None
    if branch and plugins.get("netbox_branching"):
        row = _branch(client, branch)
    return client, status, row


def _confirmed_at(value):
    try:
        confirmed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if confirmed.tzinfo is None:
            raise ValueError
    except (AttributeError, ValueError):
        return False
    age = (datetime.now(timezone.utc) - confirmed.astimezone(timezone.utc)).total_seconds()
    return -300 <= age <= 86400


def _diode_config(client, branch, plugins):
    """Validate an operator attestation of externally managed Diode routing.

    The public ingestion API cannot select a NetBox branch or Assurance mode.
    Requiring fresh, source-linked confirmation makes that external boundary
    explicit; it does not misrepresent the environment variables as controls.
    """
    names = ("DIODE_TARGET", "DIODE_CLIENT_ID", "DIODE_CLIENT_SECRET", "DIODE_MODE",
             "DIODE_BRANCH", "DIODE_CONFIG_SOURCE", "DIODE_CONFIG_CONFIRMED_AT")
    values = {name: os.environ.get(name, "").strip() for name in names}
    reasons = [f"{name} is not set" for name, value in values.items() if not value]
    if not _enabled("DIODE_WRITES"):
        reasons.append("DIODE_WRITES=1 is not set")
    if values["DIODE_MODE"] and values["DIODE_MODE"] != "direct":
        reasons.append("only externally confirmed direct auto-apply is executable; Assurance review routing cannot be enforced by the ingestion API")
    if values["DIODE_CONFIG_CONFIRMED_AT"] and not _confirmed_at(values["DIODE_CONFIG_CONFIRMED_AT"]):
        reasons.append("DIODE_CONFIG_CONFIRMED_AT must be a timezone-aware timestamp from the last 24 hours")
    expected_scope = client.branch_id if branch else "main"
    if values["DIODE_BRANCH"] and values["DIODE_BRANCH"] != expected_scope:
        reasons.append("DIODE_BRANCH attestation does not match the selected NetBox branch scope")
    if branch and not plugins.get("netbox_branching"):
        reasons.append("a branch was requested but the Branching plugin is absent")
    if plugins.get("netbox_branching") and not branch:
        reasons.append("this target has Branching; name a ready disposable branch")
    if not plugins.get("netbox_branching") and not branch and not _enabled("ALLOW_MAIN_WRITES"):
        reasons.append("target has no Branching; ALLOW_MAIN_WRITES=1 is required")
    target = values["DIODE_TARGET"].rstrip("/")
    if target:
        from urllib.parse import urlsplit
        parsed = urlsplit(target)
        if (parsed.scheme not in {"grpc", "grpcs", "http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            reasons.append("DIODE_TARGET must be a credential-free grpc(s) or http(s) endpoint")
    evidence = None
    if not reasons:
        evidence = {"target": target,
                    "client_id_sha256": hashlib.sha256(values["DIODE_CLIENT_ID"].encode()).hexdigest(),
                    "expected_branch": values["DIODE_BRANCH"],
                    "expected_mode": values["DIODE_MODE"],
                    "source": values["DIODE_CONFIG_SOURCE"],
                    "confirmed_at": values["DIODE_CONFIG_CONFIRMED_AT"],
                    "boundary": "operator-confirmed external tenant configuration; ingestion API does not control or introspect branch/mode"}
    return evidence, reasons


def _probe_endpoints(client, kinds):
    missing = []
    for kind in sorted(kinds):
        endpoint = ENDPOINTS.get(kind)
        if endpoint is None:
            missing.append(f"{kind} has no strict REST readback mapping")
            continue
        try:
            client.request(f"/api/{endpoint}/?limit=1")
        except LoadError as exc:
            # NetBox may return a rendered HTML 404 containing session details.
            match = re.search(r"returned HTTP (\d+)", str(exc))
            missing.append(f"{kind}: REST endpoint returned HTTP {match.group(1) if match else 'error'}")
    return missing


def inspect(artifact, *, url, token, branch, transport="auto"):
    """Read target capabilities and return one deterministic transport decision."""
    plan_path, raw, plan, objects, _ = _artifact(artifact)
    client, status, branch_row = _target(url, token, branch)
    kinds = {obj["kind"] for obj in objects.values()}
    plugins = status.get("plugins", {})
    candidates = {}

    tb_reasons = []
    if not plugins.get("netbox_turbobulk"):
        tb_reasons.append("TurboBulk plugin is absent")
    if not _enabled("TURBOBULK_WRITES"):
        tb_reasons.append("TURBOBULK_WRITES=1 is not set")
    unsupported = sorted(kinds - SPECS.keys())
    if unsupported:
        tb_reasons.append("compiler does not cover: " + ", ".join(unsupported))
    if not branch:
        tb_reasons.append("the qualified TurboBulk adapter requires a disposable branch")
    if branch and not branch_row:
        tb_reasons.append("requested branch is unavailable on this target")
    tb_preflight = None
    if plugins.get("netbox_turbobulk") and not unsupported and branch_row:
        try:
            tb_preflight = _schema_preflight(client, objects)
        except LoadError as exc:
            tb_reasons.append(str(exc))
    candidates["turbobulk"] = {"available": not tb_reasons, "reasons": tb_reasons,
                                "transport": "turbobulk+rest", "preflight": tb_preflight}

    package, diode_reasons = _diode_manifest(plan_path, plan)
    if not plugins.get("netbox_diode_plugin"):
        diode_reasons.append("Diode NetBox plugin is absent")
    diode_evidence, config_reasons = _diode_config(client, branch, plugins)
    diode_reasons.extend(config_reasons)
    if package:
        _, manifest = package
        checked = manifest.get("source_checked_target", {})
        target_netbox = status.get("netbox-version")
        target_diode = plugins.get("netbox_diode_plugin")
        if checked.get("netbox") != target_netbox:
            diode_reasons.append(
                f"artifact Diode export is source-checked for NetBox {checked.get('netbox')}; target is {target_netbox}")
        if checked.get("diode_netbox_plugin") != target_diode:
            diode_reasons.append(
                "artifact Diode export is source-checked for plugin "
                f"{checked.get('diode_netbox_plugin')}; target is {target_diode}")
        if manifest.get("local_compatibility_required"):
            diode_reasons.append("artifact requires a local-only compatibility bridge")
    # Endpoint probes catch target-version model gaps before any Diode request.
    diode_reasons.extend(_probe_endpoints(client, kinds))
    candidates["diode"] = {"available": not diode_reasons, "reasons": diode_reasons,
                            "transport": "diode+rest-readback",
                            "external_configuration": diode_evidence}
    candidates["rest"] = {"available": False,
                           "reasons": ["standalone canonical REST creation adapter is not implemented yet"],
                           "transport": "rest"}

    order = [transport] if transport != "auto" else ["turbobulk", "diode", "rest"]
    selected = next((name for name in order if candidates[name]["available"]), None)
    decision = {
        "selected": selected,
        "requested": transport,
        "artifact": str(plan_path),
        "plan_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": digest(plan),
        "objects": len(objects),
        "kinds": sorted(kinds),
        "target": client.base,
        "target_contract": {"netbox": status.get("netbox-version"), "plugins": plugins},
        "branch": None if branch_row is None else {
            key: branch_row.get(key) for key in ("id", "name", "schema_id", "status")},
        "candidates": candidates,
    }
    return decision


def _baseline(inventory):
    return {"success": True,
            "target_ids": {kind: sorted(row["id"] for row in rows)
                           for kind, rows in inventory.items()}}


def _phase_plan(plan, keys, final=False):
    selected = set(keys)
    result = {"objects": []}
    for obj in plan["objects"]:
        if obj["key"] not in selected:
            continue
        row = deepcopy(obj)
        if not final:
            for field in _deferred_fields(row):
                row["refs"].pop(field, None)
        result["objects"].append(row)
    return result


def _observed(client, plan, baseline, *, strict=False):
    inventory = fetch_inventory(client.base, client.token,
                                (obj["kind"] for obj in plan["objects"]), client.branch_id)
    return verify_plan(plan, inventory, strict_inventory=strict,
                       allow_existing_receipt=baseline if strict else None)


def _summary(result):
    return {key: result[key] for key in
            ("success", "canonical_objects", "matched_objects", "mismatch_count", "coverage")}


def _identity_conflicts(result):
    return [item for item in result.get("mismatches", [])
            if item.get("code") in {"ambiguous_identity", "canonical_identity_collision"}]


def _schedule(manifest, objects):
    phase_keys = _phases(objects)
    deferred = sorted(key for key, obj in objects.items()
                      if _deferred_fields(obj) & obj["refs"].keys())
    phases = manifest.get("phases", [])
    expected_count = len(phase_keys) + bool(deferred)
    expected_final = None
    if deferred:
        expected_final = "primary-addresses" if all(
            (_deferred_fields(objects[key]) & objects[key]["refs"].keys()) <= _PRIMARY_IPS
            for key in deferred) else "back-references"
    expected_sizes = [len(keys) for keys in phase_keys] + ([len(deferred)] if deferred else [])
    file_entries = {item["path"]: item for item in manifest.get("files", [])}
    if len(phases) != expected_count:
        raise LoadError("Diode manifest phases do not match the canonical dependency schedule")
    valid = True
    for index, phase in enumerate(phases):
        valid = valid and phase.get("phase") == index + 1
        valid = valid and phase.get("requires_completed_phases") == list(range(1, index + 1))
        valid = valid and phase.get("purpose") == (
            "create" if index < len(phase_keys) else expected_final)
        valid = valid and sum(file_entries[name].get("entities", -1)
                              for name in phase.get("files", []) if name in file_entries) == expected_sizes[index]
        valid = valid and all(name in file_entries for name in phase.get("files", []))
    if not valid:
        raise LoadError("Diode manifest phases do not match the canonical dependency schedule")
    return phase_keys


def _sdk(directory, manifest, files=None, timeout=120):
    if files is None:
        command = ["devenv", "shell", "--profile", "diode", "--", "python3", "-m", "estates",
                   "sdk-check", str(directory)]
    else:
        command = ["devenv", "shell", "--profile", "diode", "--", "python3", "-m",
                   "netboxlabs.diode.scripts.dryrun_replay", "--target", os.environ["DIODE_TARGET"],
                   "--app-name", "genial", "--app-version", manifest["generator_version"],
                   *(str(directory / name) for name in files)]
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise LoadError(f"Diode SDK command exceeded {timeout}s; output is not copied into the receipt") from None
    if result.returncode:
        raise LoadError(f"Diode SDK command failed with exit status {result.returncode}; credentials and output are not copied into the receipt")
    return round(time.monotonic() - started, 6)


def _continue_diode(plan, objects, directory, manifest, client, receipt, receipt_path, timeout):
    baseline = receipt["baseline"]
    phase_keys = _schedule(manifest, objects)
    inventory = fetch_inventory(client.base, client.token,
                                (obj["kind"] for obj in plan["objects"]), client.branch_id)
    full = verify_plan(plan, inventory, strict_inventory=True, allow_existing_receipt=baseline)
    if full["success"]:
        expected_files = {item["path"] for item in manifest["files"]}
        checkpointed_files = {request["path"] for phase in receipt["phases"]
                              for request in phase.get("requests", [])
                              if request.get("status") != "ready"}
        if not receipt.get("success") and checkpointed_files != expected_files:
            raise LoadError("exact graph contains uncheckpointed Diode phases; use a fresh branch and receipt")
        ids = _numeric_ids(full["ids"])
        paths = _verify_paths(client, plan, objects, ids)
        if paths["failures"]:
            raise LoadError(f"computed-path readback failed for {len(paths['failures'])} cables")
        receipt.update(success=True, result="already-matched" if receipt.get("success") else "recovered-matched",
                       completed_at=_now(), verification=full, computed_paths=paths)
        return receipt
    if receipt.get("success"):
        raise LoadError("successful receipt no longer matches strict readback; target changed")

    completed = []
    for index, phase in enumerate(manifest["phases"]):
        final = index == len(phase_keys)
        prior_count = len(completed)
        if not final:
            completed.extend(phase_keys[index])
        expected = _phase_plan(plan, completed, final=final)
        record = next((item for item in receipt["phases"] if item["phase"] == phase["phase"]), None)
        observed_at = time.monotonic()
        visible = _observed(client, expected, baseline)
        visibility_seconds = time.monotonic() - observed_at
        if visible["success"]:
            if record is None:
                raise LoadError("uncheckpointed canonical identities appeared; use a fresh branch and receipt")
            if any(request.get("status") == "ready" for request in record.get("requests", [])):
                raise LoadError("a Diode phase became visible before its request checkpoint; use a fresh branch and receipt")
            record.update(status="applied", applied_at=_now(), verification=_summary(visible),
                          visibility_seconds=round(record.get("visibility_seconds", 0) + visibility_seconds, 6))
            _write(receipt_path, receipt)
            continue
        if _identity_conflicts(visible):
            raise LoadError("ambiguous or colliding canonical identity appeared during Diode loading; use a fresh branch")
        if record and record.get("status") == "applied":
            raise LoadError(f"previously applied Diode phase {phase['phase']} no longer matches readback; target changed")
        if record is None:
            if not final and visible["matched_objects"] > prior_count:
                raise LoadError("uncheckpointed canonical identities appeared within the next Diode phase; use a fresh branch and receipt")
            record = {"phase": phase["phase"], "purpose": phase["purpose"],
                      "entities": sum(next(item["entities"] for item in manifest["files"]
                                             if item["path"] == name) for name in phase["files"]),
                      "status": "ready", "requests": [{"path": name, "status": "ready"}
                                                        for name in phase["files"]],
                      "visibility_seconds": round(visibility_seconds, 6)}
            receipt["phases"].append(record)
            _write(receipt_path, receipt)
        for request in record["requests"]:
            if request["status"] == "submitting":
                raise LoadError("a Diode request has ambiguous acceptance; use a fresh branch rather than resubmitting")
            if request["status"] == "acknowledged":
                continue
            request.update(status="submitting", submitted_at=_now())
            _write(receipt_path, receipt)
            request["sdk_seconds"] = _sdk(directory, manifest, [request["path"]], timeout=timeout)
            request.update(status="acknowledged", acknowledged_at=_now())
            _write(receipt_path, receipt)
        record["status"] = "acknowledged"

        deadline = time.monotonic() + timeout
        while True:
            observed_at = time.monotonic()
            visible = _observed(client, expected, baseline)
            record["visibility_seconds"] = round(
                record.get("visibility_seconds", 0) + time.monotonic() - observed_at, 6)
            if visible["success"]:
                record.update(status="applied", applied_at=_now(), verification=_summary(visible))
                _write(receipt_path, receipt)
                break
            if _identity_conflicts(visible):
                raise LoadError("ambiguous or colliding canonical identity appeared during Diode reconciliation")
            if time.monotonic() >= deadline:
                receipt.update(result="awaiting-visibility", failed_at=_now())
                _write(receipt_path, receipt)
                raise LoadError(f"Diode phase {phase['phase']} was accepted but did not become fully visible within {timeout}s; rerun the same command and receipt")
            time.sleep(min(5, max(0.05, timeout / 10)))

    readback_at = time.monotonic()
    verification = _observed(client, plan, baseline, strict=True)
    receipt["readback_seconds"] = round(time.monotonic() - readback_at, 6)
    if not verification["success"]:
        raise LoadError(f"strict readback found {verification['mismatch_count']} mismatches")
    ids = _numeric_ids(verification["ids"])
    paths_at = time.monotonic()
    paths = _verify_paths(client, plan, objects, ids)
    receipt["path_verification_seconds"] = round(time.monotonic() - paths_at, 6)
    if paths["failures"]:
        raise LoadError(f"computed-path readback failed for {len(paths['failures'])} cables")
    receipt.update(success=True, result="loaded", completed_at=_now(),
                   verification=verification, computed_paths=paths)
    return receipt


def load_diode(artifact, *, url, token, branch, receipt_path, decision, timeout=900):
    """Replay verified Diode phases with REST visibility barriers and checkpoints."""
    started = time.monotonic()
    plan_path, raw, plan, objects, offline = _artifact(artifact)
    client, status, branch_row = _target(url, token, branch)
    evidence, config_reasons = _diode_config(client, branch, status.get("plugins", {}))
    if config_reasons:
        raise LoadError("; ".join(config_reasons))
    package, reasons = _diode_manifest(plan_path, plan)
    if reasons or package is None:
        raise LoadError("; ".join(reasons))
    directory, manifest = package
    receipt_path = Path(receipt_path)
    binding = {
        "receipt_version": RECEIPT_VERSION, "adapter_version": ADAPTER_VERSION,
        "artifact": str(plan_path), "plan_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": digest(plan), "manifest_sha256": hashlib.sha256(
            (directory / "manifest.json").read_bytes()).hexdigest(),
        "target": client.base, "branch": branch or None, "branch_id": client.branch_id,
        "diode_target": evidence["target"], "diode_client_id_sha256": evidence["client_id_sha256"],
        "diode_scope": evidence["expected_branch"], "mode": evidence["expected_mode"],
        "configuration_source": evidence["source"], "transport": "diode+rest-readback",
        "target_contract": {"netbox": status.get("netbox-version"), "plugins": status.get("plugins", {})},
    }
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if receipt:
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; use a new receipt and fresh branch")

    inventory = fetch_inventory(client.base, client.token,
                                (obj["kind"] for obj in plan["objects"]), client.branch_id)
    if receipt is None:
        initial = verify_plan(plan, inventory, strict_inventory=False)
        conflicts = _identity_conflicts(initial)
        occupied = {kind: len(rows) for kind, rows in inventory.items() if rows}
        if occupied:
            detail = ", ".join(f"{kind}={count}" for kind, count in sorted(occupied.items()))
            raise LoadError("fresh Diode load requires empty inventories for every emitted kind; " + detail)
        if initial["matched_objects"] or conflicts:
            raise LoadError("fresh Diode load found matching or ambiguous canonical identities; use a fresh branch")
        receipt = {**binding, "started_at": _now(), "success": False, "result": "preflight",
                   "target_status": status, "offline_checks": offline, "selection": decision,
                   "external_configuration": evidence, "branch_record": branch_row,
                   "baseline": _baseline(inventory), "phases": [], "attempts": []}
    receipt.setdefault("attempts", [])
    attempt = {"started_at": _now(), "success": False,
               "external_configuration": evidence}
    receipt["attempts"].append(attempt)
    _write(receipt_path, receipt)
    try:
        receipt["sdk_validation_seconds"] = _sdk(directory, manifest, timeout=min(timeout, 120))
        result = _continue_diode(plan, objects, directory, manifest, client, receipt, receipt_path, timeout)
        elapsed = round(time.monotonic() - started, 6)
        attempt.update(success=result["success"], result=result["result"],
                       completed_at=_now(), wall_seconds=elapsed)
        result["wall_seconds"] = elapsed
        result["external_configuration"]["last_confirmed_at"] = evidence["confirmed_at"]
        result.pop("error", None)
        result.pop("failed_at", None)
        _write(receipt_path, result)
        return result
    except BaseException as exc:
        message = str(exc) if isinstance(exc, (LoadError, ValueError, OSError, KeyError, TypeError)) else type(exc).__name__
        elapsed = round(time.monotonic() - started, 6)
        receipt.update(success=False, failed_at=_now(), error=message, wall_seconds=elapsed)
        attempt.update(success=False, result="failed", completed_at=receipt["failed_at"],
                       wall_seconds=elapsed, error=message)
        _write(receipt_path, receipt)
        raise


def load(artifact, *, url, token, branch, receipt_path, transport="auto", timeout=900, explain=False):
    decision = inspect(artifact, url=url, token=token, branch=branch, transport=transport)
    if explain:
        return {"success": True, "result": "explained", "decision": decision}
    if decision["selected"] is None:
        names = [transport] if transport != "auto" else ["turbobulk", "diode", "rest"]
        details = "; ".join(f"{name}: {', '.join(decision['candidates'][name]['reasons'])}"
                            for name in names)
        raise LoadError("no faithful loader is available for this artifact and target; " + details)
    if decision["selected"] == "turbobulk":
        return load_turbobulk(artifact, url=url, token=token, branch=branch,
                              receipt_path=receipt_path, timeout=timeout)
    if decision["selected"] == "diode":
        return load_diode(artifact, url=url, token=token, branch=branch,
                          receipt_path=receipt_path, decision=decision, timeout=timeout)
    raise LoadError("selected REST transport is not implemented")


def default_receipt(artifact, target, branch):
    """Return a stable private receipt path for one artifact/target/scope."""
    plan_path, _, plan, _, _ = _artifact(artifact)
    origin = target.rstrip("/")
    scope = branch or "main"
    binding = json.dumps({"canonical_sha256": digest(plan), "target": origin,
                          "branch": scope}, sort_keys=True, separators=(",", ":"))
    suffix = hashlib.sha256(binding.encode()).hexdigest()[:12]
    artifact_name = plan_path.parent.name if plan_path.name == "plan.json" else plan_path.stem
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", artifact_name).strip("-.") or "estate"
    scope_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", scope).strip("-.") or "main"
    return Path("build/load-receipts") / f"{slug}-{scope_slug}-{suffix}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect a NetBox target and load one complete canonical estate")
    parser.add_argument("artifact", help="generated directory or plan.json")
    parser.add_argument("target", nargs="?", default=os.environ.get("NETBOX_URL"),
                        help="NetBox http(s) origin (defaults to NETBOX_URL for internal callers)")
    parser.add_argument("--branch", default="", help="ready disposable branch name; omit only with ALLOW_MAIN_WRITES=1")
    parser.add_argument("--receipt", type=Path,
                        help="private checkpoint receipt (default is stable per artifact, target and branch)")
    parser.add_argument("--transport", choices=("auto", "turbobulk", "diode", "rest"), default="auto")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--explain", action="store_true", help="inspect and explain without writing")
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not args.target or not token:
        parser.error("target/NETBOX_URL and NETBOX_TOKEN are required")
    receipt = args.receipt
    try:
        receipt = receipt or default_receipt(args.artifact, args.target, args.branch)
        if not args.explain:
            print(f"Receipt: {receipt}", file=os.sys.stderr, flush=True)
        result = load(args.artifact, url=args.target, token=token, branch=args.branch,
                      receipt_path=receipt, transport=args.transport,
                      timeout=args.timeout, explain=args.explain)
        if args.explain:
            print(json.dumps(result["decision"], indent=2, sort_keys=True))
        else:
            print(json.dumps({"success": result.get("success", False), "result": result.get("result"),
                              "transport": result.get("transport"), "receipt": str(receipt)}, sort_keys=True))
        return 0
    except (LoadError, ValueError, OSError, KeyError, TypeError) as exc:
        detail = f"; receipt: {receipt}" if receipt is not None and not args.explain else ""
        print(f"Load failed: {exc}{detail}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
