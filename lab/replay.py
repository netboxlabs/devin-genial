"""Bounded qualification of the generated LOCAL Compose target, using native SDK replay.

This is a disposable-lab harness, not an edition-independent loader. Diode 2.2
removes a Redis request only after its ingestion logs have been persisted; those
logs must then reach APPLIED/NO_CHANGES before the next phase can be submitted.
Sources: netboxlabs/diode@960fcb418892c36cbee4d4faad0e29b9b0bcd2e2,
diode-server/reconciler/{ingestion_processor.go,logs_retriever.go};
diode-sdk-python v1.14.0/netboxlabs/diode/scripts/dryrun_replay.py.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from estates.diode import verify_export
from lab.setup import _front_port_patch


GRPCURL_IMAGE = "fullstorydev/grpcurl:v1.9.3@sha256:085e183ca334eb4e81ca81ee12cbb2b2737505d1d77f5e33dabc5d066593d998"
STREAM = "diode.v1.ingest-stream"
CONTEXT = "colima-netbox-generator"
NETWORK = "netbox-generator-link"


class ReplayError(RuntimeError):
    """A safe diagnostic; raw subprocess/HTTP errors must never be printed."""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReplayError("Phase deadline expired before successful reconciliation")
    return remaining


def _run(args, deadline, label, env=None, cwd=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd,
                                timeout=_remaining(deadline), check=False)
    except subprocess.TimeoutExpired:
        raise ReplayError(f"{label} exceeded its deadline") from None
    except OSError:
        raise ReplayError(f"{label} could not start") from None
    if result.returncode:
        raise ReplayError(f"{label} failed with exit code {result.returncode}; raw output suppressed")
    return result.stdout


class LocalTarget:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.config = json.loads((self.directory / "target.json").read_text())
        required = {"schema_version": 1, "docker_context": CONTEXT, "docker_network": NETWORK,
                    "netbox_version": "4.7.0", "diode_version": "2.2.0", "plugin_version": "1.17.0"}
        if any(self.config.get(key) != value for key, value in required.items()):
            raise ReplayError("Target is not the pinned local qualification stack")
        url = urllib.parse.urlsplit(self.config["diode_target"])
        if (url.scheme != "grpc" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.path != "/diode" or url.username or url.password or url.query or url.fragment):
            raise ReplayError("Qualification requires a loopback grpc:// target ending in /diode")
        self.auth_url = urllib.parse.urlunsplit(("http", url.netloc, "/diode/auth/token", "", ""))
        self.clients = json.loads((self.directory / "diode/oauth2/client/client-credentials.json").read_text())
        for name in ("diode-ingest", "netbox-to-diode"):
            matches = [client for client in self.clients if client.get("client_id") == name]
            if len(matches) != 1 or not isinstance(matches[0].get("client_secret"), str) or not matches[0]["client_secret"]:
                raise ReplayError("Required local Diode client credentials are absent or ambiguous")
        self.token = None
        self.token_expires = 0

    def client(self, name):
        return next(client for client in self.clients if client["client_id"] == name)

    def verify_front_port_compat(self, deadline):
        """Read installed code in both app containers; prepared metadata is insufficient."""
        try:
            expected, _, _ = _front_port_patch()
        except (ValueError, OSError, KeyError):
            raise ReplayError("Local front-port patch artifacts failed their source guards") from None
        if self.config.get("local_compatibility") != {"front_port_mapping": expected}:
            raise ReplayError("Target front-port patch metadata differs from the pinned local patch")
        hashes = expected["output_sha256"]
        # The command prints only public code hashes and a package version. It
        # does not import Django, load configuration, or inspect credentials.
        probe = (
            "import hashlib,json; from pathlib import Path; "
            "from importlib.util import find_spec; from importlib.metadata import version; "
            "root=Path(next(iter(find_spec('netbox_diode_plugin').submodule_search_locations))); "
            f"files={list(hashes)!r}; "
            "print(json.dumps({'plugin_version':version('netboxlabs-diode-netbox-plugin'),"
            "'sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}}))"
        )
        directory = self.directory / "netbox"
        observed = {}
        for service in ("netbox", "netbox-worker"):
            args = ["docker", "--context", CONTEXT, "compose", "--project-directory", str(directory),
                    "-f", str(directory / "docker-compose.yml"), "-f", str(directory / "compose.local.json"),
                    "exec", "-T", service, "/opt/netbox/venv/bin/python", "-c", probe]
            try:
                result = json.loads(_run(args, min(deadline, time.monotonic() + 15),
                                         f"{service} local patch code verification"))
            except ValueError:
                raise ReplayError(f"{service} returned invalid local patch code evidence") from None
            if result != {"plugin_version": expected["plugin_version"], "sha256": hashes}:
                raise ReplayError(f"{service} does not run the pinned local front-port patch; no data was submitted")
            observed[service] = result
        return {**expected, "runtime": observed,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "scope": "Verified local patched runtime only; not official NetBox/plugin compatibility"}

    def redis(self, operation, deadline):
        directory = self.directory / "diode"
        args = ["docker", "--context", CONTEXT, "compose", "--project-directory", str(directory),
                "-f", str(directory / "docker-compose.yaml"), "-f", str(directory / "compose.local.json"),
                "exec", "-T", "redis", "sh", "-c",
                'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli -p "$REDIS_PORT" -n 1 --json "$@"',
                "redis-probe", *operation]
        return json.loads(_run(args, min(deadline, time.monotonic() + 15), "Redis diagnostic"))

    def ready(self, deadline):
        groups = self.redis(["XINFO", "GROUPS", STREAM], deadline)
        # Redis CLI JSON encodes XINFO map replies as arrays of alternating keys/values.
        groups = [dict(zip(group[::2], group[1::2])) if isinstance(group, list) else group for group in groups]
        if not any(group.get("name") == "diode-reconciler" for group in groups):
            raise ReplayError("Diode Redis consumer group is not ready; no data was submitted")

    def stream_length(self, deadline):
        result = self.redis(["XLEN", STREAM], deadline)
        if type(result) is not int or result < 0:
            raise ReplayError("Redis returned an invalid stream length")
        return result

    def read_token(self, deadline):
        if self.token and time.monotonic() < self.token_expires:
            return self.token
        client = self.client("netbox-to-diode")
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client["client_id"],
                                      "client_secret": client["client_secret"], "scope": "diode:read"}).encode()
        request = urllib.request.Request(self.auth_url, data=data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=min(15, _remaining(deadline))) as response:
                result = json.loads(response.read(65536))
            self.token = result["access_token"]
            if not isinstance(self.token, str) or not self.token:
                raise ValueError("missing token")
            self.token_expires = time.monotonic() + max(0, float(result.get("expires_in", 60)) - 30)
        except Exception:
            raise ReplayError("Local read-token acquisition failed; response suppressed") from None
        return self.token

    def logs(self, request, deadline):
        env = os.environ.copy()
        env["DIODE_READ_TOKEN"] = self.read_token(deadline)
        # grpcurl expands this literal placeholder itself; the token is absent from argv.
        args = ["docker", "--context", CONTEXT, "run", "--rm", "--network", NETWORK,
                "-e", "DIODE_READ_TOKEN", GRPCURL_IMAGE, "-plaintext", "-max-time", "15",
                "-expand-headers", "-H", "authorization: Bearer ${DIODE_READ_TOKEN}",
                "-d", json.dumps(request), "diode-reconciler:8081",
                "diode.v1.ReconcilerService/RetrieveIngestionLogs"]
        return json.loads(_run(args, min(deadline, time.monotonic() + 25), "Reconciliation diagnostic", env=env))


def wait_ready(target, deadline):
    """Retry only read-only startup probes while Compose services initialize."""
    while True:
        _remaining(deadline)
        try:
            target.ready(deadline)
            target.read_token(deadline)
            return
        except (ReplayError, ValueError, OSError):
            if time.monotonic() >= deadline:
                raise ReplayError("Local consumer/auth readiness deadline expired; no data was submitted") from None
        time.sleep(min(2, _remaining(deadline)))


def wait_reconciled(target, deadline):
    """The stream barrier must precede interpreting even apparently successful metrics."""
    while True:
        _remaining(deadline)
        if target.stream_length(deadline) == 0:
            result = target.logs({"only_metrics": True}, deadline)
            metrics = result.get("metrics", {})
            fields = ("total", "queued", "reconciled", "failed", "noChanges")
            if (not isinstance(metrics, dict) or set(metrics) - set(fields) or
                    any(type(metrics.get(key, 0)) is not int or metrics.get(key, 0) < 0 for key in fields)):
                raise ReplayError("Reconciler returned unknown or invalid metric fields")
            counts = {key: metrics.get(key, 0) for key in fields}
            if counts["failed"]:
                raise ReplayError(f"Reconciliation failed for {counts['failed']} ingestion logs")
            accounted = counts["queued"] + counts["reconciled"] + counts["failed"] + counts["noChanges"]
            if accounted > counts["total"]:
                raise ReplayError("Reconciler returned inconsistent state counts")
            if counts["total"] == counts["reconciled"] + counts["noChanges"]:
                return counts
            # ERRORED, APPLYING and OPEN have no metrics buckets in Diode 2.2.
            # Unknown states likewise cannot satisfy the total/accounted equality:
            # they fail closed at the deadline rather than being treated as success.
            errors = target.logs({"state": "ERRORED", "page_size": 1}, deadline)
            if errors.get("logs"):
                raise ReplayError("Reconciliation contains ERRORED ingestion logs")
        time.sleep(min(2, _remaining(deadline)))


def _check_manifest_compatibility(manifest, front_port_compat):
    source = manifest.get("source_checked_target", {})
    if (manifest.get("format_version") != 1 or manifest.get("diode_sdk_schema") != "1.14.0"
            or source.get("diode_sdk") != "1.14.0" or source.get("diode_netbox_plugin") != "1.17.0"):
        raise ReplayError("Export does not use the pinned local SDK/plugin contract")
    incompatible = manifest.get("known_incompatible_netbox")
    if source.get("netbox") == "4.7.0" and incompatible == []:
        return
    counts = manifest.get("counts", {})
    if (front_port_compat and (source.get("netbox") == "4.4.10" or
            (source.get("netbox") == "4.7.0" and manifest.get("local_compatibility_required") == ["front_port_mapping"]))
            and incompatible == [">=4.5.0"]
            and type(counts.get("front_port")) is int and counts["front_port"] > 0
            and type(counts.get("rear_port")) is int and counts["rear_port"] > 0):
        return
    raise ReplayError("Export is incompatible with the local NetBox 4.7 qualification target")


def _check_external_references(manifest, target_dir):
    bindings = manifest.get("external_references", [])
    if not bindings:
        return []
    from .verify import fetch_inventory
    if any(binding.get("kind") != "user" or set(binding.get("identity", {})) != {"username"}
           for binding in bindings):
        raise ReplayError("Only explicit existing-user bindings are supported by the local harness")
    try:
        token = (Path(target_dir) / "netbox-token").read_text().strip()
        users = fetch_inventory("http://127.0.0.1:8000", token, ["user"])["user"]
    except (OSError, ValueError, RuntimeError):
        raise ReplayError("Could not verify existing-user bindings; no data was submitted") from None
    checked = []
    for binding in bindings:
        name = binding["identity"]["username"]
        matches = [user["id"] for user in users if user["username"] == name]
        if len(matches) != 1:
            raise ReplayError("A required reservation username does not resolve uniquely; no data was submitted")
        checked.append({"kind": "user", "username": name, "id": matches[0]})
    return checked


def _verified_top_level(directory, entries, kinds):
    """Read only full entity envelopes from bytes matching the verified manifest."""
    for name, entry in entries.items():
        payload = (directory / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ReplayError("An export request changed after SDK verification")
        for entity in json.loads(payload)["entities"]:
            for kind in kinds & entity.keys():
                yield kind, entity[kind]


def _check_global_numeric_identities(directory, entries, target_dir):
    """Guard global matchers using full top-level records, never thin nested refs.

    This is a coexistence check, not an allocation step or a concurrency lock.
    ASN is globally unique; plugin 1.17 matches FHRP groups on group_id alone.
    """
    fields = {"asn": "asn", "fhrp_group": "group_id"}

    def identity(kind, row):
        value = row.get(fields[kind])
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        raise ReplayError(f"Invalid {kind} numeric identity; no data was submitted")

    def ownership(kind, row):
        values = ({"name": row.get("name")} if kind == "fhrp_group" else
                  {"rir": (row.get("rir") or {}).get("name"), "description": row.get("description")})
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ReplayError(f"Missing {kind} ownership fields; no data was submitted")
        return values

    expected = {}
    for kind, row in _verified_top_level(directory, entries, fields.keys()):
        key, owner = (kind, identity(kind, row)), ownership(kind, row)
        if key in expected and expected[key] != owner:
            raise ReplayError(f"Conflicting {kind} ownership in export; no data was submitted")
        expected[key] = owner
    if not expected:
        return []

    from .verify import fetch_inventory
    try:
        token = (Path(target_dir) / "netbox-token").read_text().strip()
        inventory = fetch_inventory("http://127.0.0.1:8000", token,
                                    sorted({kind for kind, _ in expected}))
        existing = {}
        for kind in {kind for kind, _ in expected}:
            for row in inventory[kind]:
                existing.setdefault((kind, identity(kind, row)), []).append(row)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        raise ReplayError("Could not verify global numeric identities; no data was submitted") from None

    checked = []
    for (kind, number), owner in sorted(expected.items()):
        matches = existing.get((kind, number), [])
        if len(matches) > 1 or (matches and ownership(kind, matches[0]) != owner):
            raise ReplayError(f"Global {kind} identity {number} is already owned or ambiguous; no data was submitted")
        checked.append({"kind": kind, fields[kind]: number, "ownership": owner,
                        "existing_id": matches[0]["id"] if matches else None,
                        "status": "owned-existing" if matches else "available"})
    return checked


def _check_aggregate_ranges(directory, entries, target_dir):
    """NetBox 4.7 Aggregate.clean forbids overlaps across all RIRs and tenants.

    Source: netbox-community/netbox v4.7.0, netbox/ipam/models/ip.py.
    Permit an exact owned replay; never shrink, reassign, or create shared roots.
    """
    def network(row):
        try:
            return ipaddress.ip_network(row["prefix"])
        except (ValueError, KeyError, TypeError):
            raise ReplayError("Invalid aggregate prefix; no data was submitted") from None

    def ownership(row):
        rir = row.get("rir")
        values = {"rir": rir.get("name") if isinstance(rir, dict) else None,
                  "description": row.get("description")}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ReplayError("Missing aggregate ownership fields; no data was submitted")
        return values

    expected = [(network(row), ownership(row))
                for _, row in _verified_top_level(directory, entries, {"aggregate"})]
    if not expected:
        return []
    expected.sort(key=lambda item: (item[0].version, int(item[0].network_address), item[0].prefixlen))
    for (previous, _), (current, _) in zip(expected, expected[1:]):
        if previous.overlaps(current):
            raise ReplayError(f"Export aggregate ranges {previous} and {current} overlap; no data was submitted")

    from .verify import fetch_inventory
    try:
        token = (Path(target_dir) / "netbox-token").read_text().strip()
        rows = fetch_inventory("http://127.0.0.1:8000", token, ["aggregate"])["aggregate"]
        existing = [(network(row), row) for row in rows]
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        raise ReplayError("Could not verify aggregate ranges; no data was submitted") from None
    checked = []
    # ponytail: profiles emit a few aggregate roots. Index target intervals if that grows;
    # currently this scans the target once per authored root, not once per estate object.
    for prefix, owner in expected:
        matches = [(other, row) for other, row in existing if prefix.overlaps(other)]
        if (len(matches) > 1 or (matches and
                (matches[0][0] != prefix or ownership(matches[0][1]) != owner))):
            raise ReplayError(f"Aggregate {prefix} overlaps an existing or ambiguous aggregate; no data was submitted")
        checked.append({"prefix": str(prefix), "ownership": owner,
                        "existing_id": matches[0][1]["id"] if matches else None,
                        "status": "owned-existing" if matches else "available"})
    return checked


def replay(export_dir, target_dir, receipt_path, phase_timeout=300, front_port_compat=False):
    if not math.isfinite(phase_timeout) or phase_timeout <= 0:
        raise ReplayError("Phase timeout must be finite and positive")
    directory = Path(export_dir).resolve()
    verified = verify_export(directory)
    manifest_bytes = (directory / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    _check_manifest_compatibility(manifest, front_port_compat)
    bindings = _check_external_references(manifest, target_dir)
    ordered = []
    if not manifest.get("phases"):
        raise ReplayError("Export has no dependency phases")
    for number, phase in enumerate(manifest["phases"], 1):
        if (phase["phase"] != number or phase["requires_completed_phases"] != list(range(1, number))
                or not phase["files"]):
            raise ReplayError("Export dependency phases are incomplete or out of order")
        ordered.extend(phase["files"])
    entries = {entry["path"]: entry for entry in manifest["files"]}
    if any(entry["entities"] <= 0 for entry in entries.values()):
        raise ReplayError("Export requests must contain at least one entity")
    if len(ordered) != len(set(ordered)) or set(ordered) != set(entries):
        raise ReplayError("Export phase membership does not match its verified request files")
    if any((directory / name).resolve().parent != directory for name in ordered):
        raise ReplayError("Export requests must be files inside their export directory")
    target = LocalTarget(target_dir)
    if target.config.get("local_compatibility") and not front_port_compat:
        raise ReplayError("Target has a local patch; explicit --front-port-compat is required")
    if front_port_compat and not target.config.get("local_compatibility"):
        raise ReplayError("Target has no prepared local front-port patch; no data was submitted")
    receipt = {"schema_version": 1, "success": False, "started_at": datetime.now(timezone.utc).isoformat(),
               "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "sdk_verification": verified,
               "target": {key: target.config[key] for key in
                          ("docker_context", "docker_network", "diode_target", "netbox_version", "diode_version", "plugin_version")},
               "phase_timeout_seconds": phase_timeout, "phases": [], "external_references": bindings,
               "global_numeric_preflight": {"success": False, "checks": []},
               "aggregate_preflight": {"success": False, "checks": []},
               "limitations": ["This receipt establishes local Diode processing, not full NetBox graph equivalence.",
                               "Verify resulting NetBox inventory and relationships with the separate readback check.",
                               "Redis barriers apply only to this pinned, disposable local Compose stack."]}
    # Reserve a new private receipt before target writes; preserve partial/failure evidence.
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        def save():
            output.seek(0)
            output.write(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
            output.truncate()
            output.flush()

        save()
        try:
            receipt["global_numeric_preflight"]["checks"] = _check_global_numeric_identities(directory, entries, target_dir)
            receipt["global_numeric_preflight"]["success"] = True
            save()
            receipt["aggregate_preflight"]["checks"] = _check_aggregate_ranges(directory, entries, target_dir)
            receipt["aggregate_preflight"]["success"] = True
            save()
            if front_port_compat:
                receipt["local_compatibility"] = {
                    "front_port_mapping": target.verify_front_port_compat(time.monotonic() + phase_timeout)}
                save()
            wait_ready(target, time.monotonic() + phase_timeout)
            receipt["read_auth_verified"] = True
            save()
            for phase in manifest["phases"]:
                started = time.monotonic()
                deadline = started + phase_timeout
                record = {"phase": phase["phase"], "purpose": phase["purpose"], "success": False,
                          "requests": len(phase["files"]),
                          "entities": sum(entries[name]["entities"] for name in phase["files"])}
                receipt["phases"].append(record)
                save()
                for name in phase["files"]:
                    if hashlib.sha256((directory / name).read_bytes()).hexdigest() != entries[name]["sha256"]:
                        raise ReplayError("An export request changed after SDK verification")
                env = os.environ.copy()
                env.pop("DIODE_SENTRY_DSN", None)
                env.update(DIODE_CLIENT_ID="diode-ingest",
                           DIODE_CLIENT_SECRET=target.client("diode-ingest")["client_secret"],
                           DIODE_SDK_LOG_LEVEL="ERROR")
                args = [sys.executable, "-m", "netboxlabs.diode.scripts.dryrun_replay",
                        "--target", target.config["diode_target"], "--app-name", "devin-generator",
                        "--app-version", manifest["generator_version"],
                        *(str(directory / name) for name in phase["files"])]
                _run(args, deadline, "Upstream SDK replay", env=env)
                record["ingest_acknowledged"] = True
                record["ingest_seconds"] = round(time.monotonic() - started, 3)
                save()
                record["global_ingestion_metrics"] = wait_reconciled(target, deadline)
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                record["success"] = True
                save()
                print(f"Phase {phase['phase']}: {record['entities']} entities reconciled in {record['elapsed_seconds']}s", flush=True)
            receipt["success"] = True
        except Exception as exc:
            receipt["error"] = str(exc) if isinstance(exc, ReplayError) else f"{type(exc).__name__}; details suppressed"
            raise ReplayError(receipt["error"]) from None
        finally:
            receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--target-dir", type=Path, default=Path("build/local-target"))
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--phase-timeout", type=float, default=300)
    parser.add_argument("--front-port-compat", action="store_true",
                        help="qualify only the pinned local panel patch, after verifying both running containers")
    args = parser.parse_args(argv)
    try:
        replay(args.export_dir, args.target_dir, args.receipt, args.phase_timeout, args.front_port_compat)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ReplayError) else f"{type(exc).__name__}; details suppressed"
        parser.exit(1, f"Local qualification failed: {message}\n")
    print(f"Local Diode reconciliation completed; receipt: {args.receipt}")


if __name__ == "__main__":
    main()
