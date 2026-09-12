"""Target-aware TurboBulk delivery for a frozen Genial graph.

This compiler deliberately consumes ``plan.json`` rather than generator internals.
TurboBulk receives database-shaped rows; bounded REST updates close relationships
which are cyclic or absent from the installed TurboBulk schema.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .diode import _index, _phases
from .model import canonical, digest
from lab.verify import ENDPOINTS, fetch_inventory, verify_plan


class LoadError(RuntimeError):
    """The target cannot safely or completely receive the artifact."""


class JobTimeout(LoadError):
    def __init__(self, job_id, job, timeout):
        self.job_id = job_id
        self.job = job
        super().__init__(
            f"TurboBulk job {job_id} did not finish within {timeout}s; receipt preserved. "
            "Inspect the recorded job once and resume only after it progresses or reaches a terminal state. "
            "If it remains stuck, have the job or branch cleaned up, or use a new branch and receipt."
        )


RECEIPT_VERSION = 1
COMPILER_VERSION = "v02-turbobulk-3"


SPECS = {
    "manufacturer": ("dcim.manufacturer", "/api/dcim/manufacturers/"),
    "tag": ("extras.tag", "/api/extras/tags/"),
    "circuit_type": ("circuits.circuittype", "/api/circuits/circuit-types/"),
    "cluster_type": ("virtualization.clustertype", "/api/virtualization/cluster-types/"),
    "provider": ("circuits.provider", "/api/circuits/providers/"),
    "tenant": ("tenancy.tenant", "/api/tenancy/tenants/"),
    "device_role": ("dcim.devicerole", "/api/dcim/device-roles/"),
    "site": ("dcim.site", "/api/dcim/sites/"),
    "vrf": ("ipam.vrf", "/api/ipam/vrfs/"),
    "device_type": ("dcim.devicetype", "/api/dcim/device-types/"),
    "provider_network": ("circuits.providernetwork", "/api/circuits/provider-networks/"),
    "circuit": ("circuits.circuit", "/api/circuits/circuits/"),
    "location": ("dcim.location", "/api/dcim/locations/"),
    "vlan": ("ipam.vlan", "/api/ipam/vlans/"),
    "cluster": ("virtualization.cluster", "/api/virtualization/clusters/"),
    "device": ("dcim.device", "/api/dcim/devices/"),
    "prefix": ("ipam.prefix", "/api/ipam/prefixes/"),
    "circuit_termination": ("circuits.circuittermination", "/api/circuits/circuit-terminations/"),
    "rack": ("dcim.rack", "/api/dcim/racks/"),
    "power_panel": ("dcim.powerpanel", "/api/dcim/power-panels/"),
    "power_port": ("dcim.powerport", "/api/dcim/power-ports/"),
    "interface": ("dcim.interface", "/api/dcim/interfaces/"),
    "ip_address": ("ipam.ipaddress", "/api/ipam/ip-addresses/"),
    "power_feed": ("dcim.powerfeed", "/api/dcim/power-feeds/"),
    "virtual_machine": ("virtualization.virtualmachine", "/api/virtualization/virtual-machines/"),
    "power_outlet": ("dcim.poweroutlet", "/api/dcim/power-outlets/"),
    "service": ("ipam.service", "/api/ipam/services/"),
    "vm_interface": ("virtualization.vminterface", "/api/virtualization/interfaces/"),
    "cable": ("dcim.cable", "/api/dcim/cables/"),
}

CONTENT_TYPES = {
    "site": ("dcim", "site"),
    "provider_network": ("circuits", "providernetwork"),
    "interface": ("dcim", "interface"),
    "vm_interface": ("virtualization", "vminterface"),
    "circuit_termination": ("circuits", "circuittermination"),
    "power_port": ("dcim", "powerport"),
    "power_outlet": ("dcim", "poweroutlet"),
    "power_feed": ("dcim", "powerfeed"),
    "virtual_machine": ("virtualization", "virtualmachine"),
}

DIRECT_REFS = {
    "tenant": "tenant_id", "site": "site_id", "location": "location_id",
    "rack": "rack_id", "device_type": "device_type_id", "role": "role_id",
    "manufacturer": "manufacturer_id", "provider": "provider_id", "type": "type_id",
    "cluster": "cluster_id", "device": "device_id", "vrf": "vrf_id",
    "vlan": "vlan_id", "untagged_vlan": "untagged_vlan_id",
    "power_panel": "power_panel_id", "power_port": "power_port_id",
    "virtual_machine": "virtual_machine_id", "circuit": "circuit_id",
}

DEFERRED = {"primary_ip4", "primary_ip6", "oob_ip", "tagged_vlans", "tags"}
SUPPORTED_REFS = {
    "cable": {"a", "b"},
    "circuit": {"provider", "tenant", "type"},
    "circuit_termination": {"circuit", "termination"},
    "circuit_type": set(),
    "cluster": {"scope_site", "tenant", "type"},
    "cluster_type": set(),
    "device": {"cluster", "device_type", "location", "primary_ip4", "rack", "role", "site", "tags", "tenant"},
    "device_role": set(),
    "device_type": {"manufacturer"},
    "interface": {"device", "tagged_vlans", "untagged_vlan", "vrf"},
    "ip_address": {"assigned_object", "tenant", "vrf"},
    "location": {"site"},
    "manufacturer": set(),
    "power_feed": {"power_panel", "rack"},
    "power_outlet": {"device", "power_port"},
    "power_panel": {"location", "site"},
    "power_port": {"device"},
    "prefix": {"scope_site", "tenant", "vlan", "vrf"},
    "provider": set(),
    "provider_network": {"provider"},
    "rack": {"location", "site", "tenant"},
    "service": {"virtual_machine"},
    "site": {"tags", "tenant"},
    "tag": set(),
    "tenant": set(),
    "virtual_machine": {"cluster", "device", "primary_ip4", "tags", "tenant"},
    "vlan": {"site", "tenant"},
    "vm_interface": {"untagged_vlan", "virtual_machine", "vrf"},
    "vrf": {"tenant"},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _elapsed(first, second):
    if not first or not second:
        return None
    return (datetime.fromisoformat(second.replace("Z", "+00:00")) -
            datetime.fromisoformat(first.replace("Z", "+00:00"))).total_seconds()


def _token(value):
    value = value.strip()
    for prefix in ("Bearer ", "Token "):
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _nested_id(value):
    return value.get("id") if isinstance(value, dict) else value


def _numeric_ids(values):
    return {key: int(_nested_id(value)) for key, value in values.items()}


def _artifact(path):
    path = Path(path)
    plan_path = path / "plan.json" if path.is_dir() else path
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    objects = _index(plan)
    checks_path = plan_path.parent / "checks.json"
    if not checks_path.exists():
        raise LoadError(f"{checks_path} is required to prove this canonical plan passed offline checks")
    offline = json.loads(checks_path.read_text())
    if offline.get("status") != "passed" or offline.get("plan_sha256") != digest(plan):
        raise LoadError(f"{checks_path} does not prove this canonical plan passed offline checks")
    return plan_path, raw, plan, objects, offline


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url, token, branch_id=None):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise LoadError("target must be an http(s) origin without credentials, path, query, or fragment")
        self.base = url.rstrip("/")
        self.token = _token(token)
        self.branch_id = branch_id
        self.opener = urllib.request.build_opener(_NoRedirect)
        if not self.token:
            raise LoadError("NETBOX_TOKEN is empty")

    @property
    def headers(self):
        prefix = "Bearer " if self.token.startswith("nbt_") else "Token "
        headers = {"Authorization": prefix + self.token, "Accept": "application/json"}
        if self.branch_id:
            headers["X-NetBox-Branch"] = self.branch_id
        return headers

    def request(self, path, *, method="GET", body=None, headers=None, branch=True):
        request_headers = dict(self.headers)
        if not branch:
            request_headers.pop("X-NetBox-Branch", None)
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode(errors="replace")
            raise LoadError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc

    def all(self, path):
        rows = []
        next_url = self.base + path + ("&" if "?" in path else "?") + "limit=1000&ordering=id"
        seen = set()
        while next_url:
            target = urllib.parse.urlsplit(next_url)
            if (target.scheme, target.netloc) != urllib.parse.urlsplit(self.base)[:2] or next_url in seen:
                raise LoadError(f"unsafe or repeated pagination URL for {path}")
            seen.add(next_url)
            request = urllib.request.Request(next_url, headers=self.headers)
            try:
                with self.opener.open(request, timeout=120) as response:
                    page = json.load(response)
            except urllib.error.HTTPError as exc:
                raise LoadError(f"GET {path} returned HTTP {exc.code}") from exc
            rows.extend(page["results"])
            next_url = page.get("next")
        return rows


def _branch(client, name):
    _, data = client.request("/api/plugins/branching/branches/?name=" + urllib.parse.quote(name), branch=False)
    rows = [row for row in data.get("results", data) if row.get("name") == name]
    if len(rows) != 1:
        raise LoadError(f"expected one ready NetBox branch named {name!r}; found {len(rows)}")
    status = rows[0].get("status")
    status = status.get("value") if isinstance(status, dict) else status
    if status != "ready":
        raise LoadError(f"branch {name!r} is {status!r}, not ready")
    schema_id = rows[0].get("schema_id")
    if not schema_id:
        raise LoadError(f"branch {name!r} has no schema_id")
    client.branch_id = schema_id
    return rows[0]


def _ref_id(obj, name, ids):
    return ids[obj["refs"][name]]


def _matches(obj, row, ids):
    kind, attrs = obj["kind"], obj["attrs"]
    if kind in {"manufacturer", "tag", "circuit_type", "cluster_type", "provider", "tenant",
                "device_role", "site", "device_type"}:
        return row.get("slug") == attrs["slug"]
    if kind == "vrf":
        return (row.get("name") == attrs["name"]
                and ("tenant" not in obj["refs"] or _nested_id(row.get("tenant")) == _ref_id(obj, "tenant", ids))
                and (not attrs.get("rd") or row.get("rd") == attrs["rd"]))
    if kind == "provider_network":
        return row.get("name") == attrs["name"] and _nested_id(row.get("provider")) == _ref_id(obj, "provider", ids)
    if kind == "circuit":
        return row.get("cid") == attrs["cid"] and _nested_id(row.get("provider")) == _ref_id(obj, "provider", ids)
    if kind in {"location", "power_panel"}:
        return row.get("name") == attrs["name"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
    if kind == "rack":
        return (row.get("asset_tag") == attrs.get("asset_tag") if attrs.get("asset_tag") else
                row.get("name") == attrs["name"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
                and ("location" not in obj["refs"] or _nested_id(row.get("location")) == _ref_id(obj, "location", ids)))
    if kind == "vlan":
        return row.get("vid") == attrs["vid"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
    if kind == "cluster":
        return (row.get("name") == attrs["name"]
                and ("scope_site" not in obj["refs"] or row.get("scope_id") == _ref_id(obj, "scope_site", ids)))
    if kind == "device":
        return (row.get("asset_tag") == attrs.get("asset_tag") if attrs.get("asset_tag") else
                row.get("name") == attrs["name"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
                and ("tenant" not in obj["refs"] or _nested_id(row.get("tenant")) == _ref_id(obj, "tenant", ids)))
    if kind == "prefix":
        return row.get("prefix") == attrs["prefix"] and _nested_id(row.get("vrf")) == _ref_id(obj, "vrf", ids)
    if kind == "circuit_termination":
        side = row.get("term_side")
        side = side.get("value") if isinstance(side, dict) else side
        return side == attrs["term_side"] and _nested_id(row.get("circuit")) == _ref_id(obj, "circuit", ids)
    if kind in {"power_port", "power_outlet", "interface"}:
        return row.get("name") == attrs["name"] and _nested_id(row.get("device")) == _ref_id(obj, "device", ids)
    if kind == "ip_address":
        return row.get("address") == attrs["address"] and _nested_id(row.get("vrf")) == _ref_id(obj, "vrf", ids)
    if kind == "power_feed":
        return row.get("name") == attrs["name"] and _nested_id(row.get("power_panel")) == _ref_id(obj, "power_panel", ids)
    if kind == "virtual_machine":
        return (row.get("name") == attrs["name"]
                and ("cluster" not in obj["refs"] or _nested_id(row.get("cluster")) == _ref_id(obj, "cluster", ids))
                and ("tenant" not in obj["refs"] or _nested_id(row.get("tenant")) == _ref_id(obj, "tenant", ids)))
    if kind == "service":
        parent = row.get("parent_object_id", _nested_id(row.get("parent")))
        return row.get("name") == attrs["name"] and parent == _ref_id(obj, "virtual_machine", ids)
    if kind == "vm_interface":
        return row.get("name") == attrs["name"] and _nested_id(row.get("virtual_machine")) == _ref_id(obj, "virtual_machine", ids)
    if kind == "cable":
        return row.get("label") == attrs["label"]
    raise LoadError(f"no target identity matcher for {kind}")


def _content_types(client):
    result = {}
    for kind, (app_label, model) in CONTENT_TYPES.items():
        rows = client.all(f"/api/core/object-types/?app_label={app_label}&model={model}")
        if len(rows) != 1:
            raise LoadError(f"content type {app_label}.{model}: expected one row, found {len(rows)}")
        result[kind] = rows[0]["id"]
    return result


def _render(obj, objects, ids, content_types):
    row = dict(obj["attrs"])
    if obj["kind"] == "virtual_machine" and "start_on_boot" not in row:
        row["start_on_boot"] = "off"
    for name, column in DIRECT_REFS.items():
        if name in obj["refs"] and not (obj["kind"] == "service" and name == "virtual_machine"):
            row[column] = ids[obj["refs"][name]]
    if "scope_site" in obj["refs"]:
        row["scope_type_id"], row["scope_id"] = content_types["site"], ids[obj["refs"]["scope_site"]]
    for field in ("termination", "assigned_object"):
        if field in obj["refs"]:
            target = objects[obj["refs"][field]]
            row[field + "_type_id"], row[field + "_id"] = content_types[target["kind"]], ids[target["key"]]
    if obj["kind"] == "service":
        target = objects[obj["refs"]["virtual_machine"]]
        row["parent_object_type_id"] = content_types[target["kind"]]
        row["parent_object_id"] = ids[target["key"]]
    for name in DEFERRED:
        row.pop(name, None)
    return row


def _rendered_columns(obj):
    """Return database columns without needing resolved target IDs."""
    columns = set(obj["attrs"]) - DEFERRED
    columns.update(column for name, column in DIRECT_REFS.items()
                   if name in obj["refs"] and not (obj["kind"] == "service" and name == "virtual_machine"))
    if "scope_site" in obj["refs"]:
        columns.update(("scope_type_id", "scope_id"))
    for field in ("termination", "assigned_object"):
        if field in obj["refs"]:
            columns.update((field + "_type_id", field + "_id"))
    if obj["kind"] == "service":
        columns.update(("parent_object_type_id", "parent_object_id"))
    if obj["kind"] == "virtual_machine" and "start_on_boot" not in obj["attrs"]:
        columns.add("start_on_boot")
    return columns


def _multipart(fields, filename, payload):
    boundary = "----genial" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/gzip\r\n\r\n'.encode() + payload + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def _write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _poll(client, job_id, timeout):
    deadline = time.monotonic() + timeout
    interval = 0.5
    while True:
        _, job = client.request(f"/api/plugins/turbobulk/jobs/{job_id}/", branch=False)
        if job["status"] in {"completed", "failed", "errored", "cancelled"}:
            return job
        if time.monotonic() >= deadline:
            raise JobTimeout(job_id, job, timeout)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        interval = min(interval * 1.5, 5.0)


def _job_result(job, expected):
    data = job.get("data") or {}
    affected = (data.get("rows_inserted") or 0) + (data.get("rows_updated") or 0)
    failed_hooks = {name: value for name, value in (data.get("post_hooks") or {}).items()
                    if value.get("success") is False}
    if job["status"] != "completed" or data.get("errors") or affected != expected or failed_hooks:
        raise LoadError(f"TurboBulk job {job.get('job_id', '<unknown>')} did not produce {expected} valid rows")
    return data


def _record_observed(entry, job):
    data = job.get("data") or {}
    entry.update({"status": job["status"], "last_observed_at": _now(),
              "job_created_at": job.get("created"),
              "job_started_at": job.get("started"), "job_completed_at": job.get("completed"),
              "queue_seconds": _elapsed(job.get("created"), job.get("started")),
              "job_lifecycle_seconds": _elapsed(job.get("created"), job.get("completed")),
              "server_duration_seconds": job.get("duration_seconds"),
              "rows_processed": data.get("rows_processed"), "rows_inserted": data.get("rows_inserted"),
              "rows_updated": data.get("rows_updated"), "changelogs_created": data.get("changelogs_created"),
              "save_hooks_applied": data.get("save_hooks_applied"), "post_hooks": data.get("post_hooks"),
              "errors": data.get("errors", []), "job_error": job.get("error"), "data_error": data.get("error")})
    return entry


def _record_terminal(entry, job, wall_seconds=None):
    _record_observed(entry, job)
    if wall_seconds is not None:
        entry["wall_seconds"] = wall_seconds
    return _job_result(job, entry["rows_expected"])


def _submit(client, branch_name, model, rows, purpose, keys, receipt, receipt_path, timeout, mode="insert"):
    compile_started = time.monotonic()
    payload = gzip.compress(b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode()
                                     for row in rows), mtime=0)
    compile_seconds = round(time.monotonic() - compile_started, 6)
    boundary, body = _multipart({"model": model, "mode": mode, "validation_mode": "full",
                                 "branch": branch_name, "create_changelogs": "true",
                                 "apply_save_hooks": "false"}, model + ".jsonl.gz", payload)
    started = time.monotonic()
    status, answer = client.request("/api/plugins/turbobulk/load/", method="POST", body=body,
                                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, branch=False)
    uploaded = time.monotonic()
    entry = {"purpose": purpose, "model": model, "canonical_keys": keys,
             "rows_expected": len(rows), "compressed_bytes": len(payload),
             "payload_sha256": hashlib.sha256(payload).hexdigest(),
             "compile_seconds": compile_seconds, "upload_seconds": round(uploaded - started, 6),
             "http_status": status,
             "job_id": answer["job_id"], "status": "submitted", "submitted_at": _now()}
    receipt["jobs"].append(entry)
    _write_receipt(receipt_path, receipt)
    job = _poll(client, entry["job_id"], timeout)
    finished = time.monotonic()
    entry["poll_seconds"] = round(finished - uploaded, 6)
    _record_terminal(entry, job, round(finished - started, 6))
    _write_receipt(receipt_path, receipt)
    print(f"{purpose}: {len(rows):,} rows completed", flush=True)
    return entry


def _refresh(client, kind, candidates, ids, *, allow_missing=False):
    rows = client.all(SPECS[kind][1])
    by_id = {row["id"]: row for row in rows}
    for obj in candidates:
        if obj["key"] in ids:
            row = by_id.get(ids[obj["key"]])
            if row is None or not _matches(obj, row, ids):
                raise LoadError(f"checkpoint identity {obj['key']} no longer matches target ID {ids[obj['key']]}; use a fresh branch")
            continue
        found = [row for row in rows if _matches(obj, row, ids)]
        if len(found) > 1:
            raise LoadError(f"{obj['key']}: target identity is ambiguous ({len(found)} rows)")
        if found:
            ids[obj["key"]] = found[0]["id"]
        elif not allow_missing:
            raise LoadError(f"{obj['key']}: submitted row is absent after completed job")


def _schema_preflight(client, objects):
    unsupported_refs = {}
    for obj in objects.values():
        if extra := set(obj["refs"]) - SUPPORTED_REFS[obj["kind"]]:
            unsupported_refs.setdefault(obj["kind"], set()).update(extra)
    if unsupported_refs:
        detail = "; ".join(f"{kind}: {', '.join(sorted(refs))}"
                           for kind, refs in sorted(unsupported_refs.items()))
        raise LoadError("TurboBulk compiler has no translation for canonical references: " + detail)
    _, available = client.request("/api/plugins/turbobulk/models/", branch=False)
    models = {row["full_name"] for row in available if not row.get("export_only")}
    required = {SPECS[obj["kind"]][0] for obj in objects.values()}
    if any(obj["kind"] == "cable" for obj in objects.values()):
        required.add("dcim.cabletermination")
    missing = sorted(required - models)
    if missing:
        raise LoadError("TurboBulk cannot write required models: " + ", ".join(missing))
    schemas = {}
    for model in sorted(required):
        _, schema = client.request(f"/api/plugins/turbobulk/models/{model}/", branch=False)
        schemas[model] = {field["name"] for field in schema["fields"]}
    missing_columns = {}
    for obj in objects.values():
        model = SPECS[obj["kind"]][0]
        columns = set(obj["attrs"]) if obj["kind"] == "cable" else _rendered_columns(obj)
        if absent := columns - schemas[model]:
            missing_columns.setdefault(model, set()).update(absent)
    if any(obj["kind"] == "cable" for obj in objects.values()):
        required_terminations = {"cable_id", "cable_end", "termination_type_id", "termination_id"}
        if absent := required_terminations - schemas["dcim.cabletermination"]:
            missing_columns["dcim.cabletermination"] = absent
    if missing_columns:
        detail = "; ".join(f"{model}: {', '.join(sorted(columns))}"
                           for model, columns in sorted(missing_columns.items()))
        raise LoadError("installed TurboBulk schemas would drop required columns: " + detail)
    return {"models": sorted(required), "schema_fields": {model: len(fields) for model, fields in schemas.items()}}


def _bulk_patch(client, endpoint, rows, receipt, receipt_path, purpose, batch_size=50):
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        started = time.monotonic()
        status, _ = client.request(endpoint, method="PATCH", body=json.dumps(batch).encode(),
                                   headers={"Content-Type": "application/json"})
        receipt["rest_batches"].append({"purpose": purpose, "offset": offset, "rows": len(batch),
                                         "http_status": status,
                                         "wall_seconds": round(time.monotonic() - started, 6)})
        _write_receipt(receipt_path, receipt)


def _trace_contains_cable(value, cable_id, label):
    if isinstance(value, dict):
        if value.get("id") == cable_id and value.get("label") == label:
            return True
        return any(_trace_contains_cable(item, cable_id, label) for item in value.values())
    if isinstance(value, list):
        return any(_trace_contains_cable(item, cable_id, label) for item in value)
    return False


def _verify_paths(client, plan, objects, ids, workers=8):
    cables = [obj for obj in plan["objects"] if obj["kind"] == "cable"]
    if not cables:
        return {"cables_expected": 0, "cables_traced": 0, "failures": [], "wall_seconds": 0.0}

    def trace(cable):
        endpoint = objects[cable["refs"]["a"]]
        endpoint_id = ids[endpoint["key"]]
        endpoint_id = endpoint_id["id"] if isinstance(endpoint_id, dict) else endpoint_id
        cable_id = ids[cable["key"]]
        cable_id = cable_id["id"] if isinstance(cable_id, dict) else cable_id
        _, result = client.request(f"/api/{ENDPOINTS[endpoint['kind']]}/{endpoint_id}/trace/")
        if not result or not _trace_contains_cable(result, cable_id, cable["attrs"]["label"]):
            raise LoadError("trace did not contain its cable")
        return cable["key"]

    started = time.monotonic()
    failures, traced = [], 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(trace, cable): cable["key"] for cable in cables}
        for future in as_completed(pending):
            try:
                future.result()
                traced += 1
            except Exception as exc:
                failures.append({"cable": pending[future], "error": str(exc)})
    result = {"cables_expected": len(cables), "cables_traced": traced,
              "failures": failures[:20], "wall_seconds": round(time.monotonic() - started, 6),
              "workers": workers}
    return result


def _complete_rest(client, plan, objects, ids, receipt, receipt_path):
    current = {kind: {row["id"]: row for row in client.all(SPECS[kind][1])}
               for kind in sorted({obj["kind"] for obj in plan["objects"]
                                   if obj["kind"] in SPECS and (set(obj["refs"]) & DEFERRED)})}
    for kind, rows in current.items():
        patches = []
        for obj in plan["objects"]:
            if obj["kind"] != kind:
                continue
            existing, patch = rows[ids[obj["key"]]], {"id": ids[obj["key"]]}
            for field in ("primary_ip4", "primary_ip6", "oob_ip"):
                if field in obj["refs"]:
                    desired = ids[obj["refs"][field]]
                    actual = _nested_id(existing.get(field))
                    if actual not in (None, desired):
                        raise LoadError(f"{obj['key']}.{field} changed outside this receipt; use a fresh branch")
                    if actual != desired:
                        patch[field] = desired
            if "tagged_vlans" in obj["refs"]:
                desired = sorted(ids[key] for key in obj["refs"]["tagged_vlans"])
                actual = sorted(_nested_id(value) for value in existing.get("tagged_vlans", []))
                if set(actual) - set(desired):
                    raise LoadError(f"{obj['key']}.tagged_vlans has concurrent values; refusing to replace them")
                if actual != desired:
                    patch["tagged_vlans"] = desired
            if "tags" in obj["refs"]:
                desired = sorted(ids[key] for key in obj["refs"]["tags"])
                actual = sorted(_nested_id(value) for value in existing.get("tags", []))
                if set(actual) - set(desired):
                    raise LoadError(f"{obj['key']}.tags has concurrent values; refusing to replace them")
                if actual != desired:
                    patch["tags"] = desired
            if kind == "virtual_machine" and existing.get("start_on_boot") is None:
                patch["start_on_boot"] = "off"
            if len(patch) > 1:
                patches.append(patch)
        if patches:
            _bulk_patch(client, SPECS[kind][1], patches, receipt, receipt_path, f"complete:{kind}")
    return sum(row["rows"] for row in receipt["rest_batches"])


def load(plan_path, *, url, token, branch, receipt_path, timeout=900):
    """Load one frozen artifact into a disposable branch and strictly read it back."""
    started_at = _now()
    overall = time.monotonic()
    plan_path, raw, plan, objects, offline = _artifact(plan_path)
    unsupported = sorted({obj["kind"] for obj in objects.values()} - SPECS.keys())
    if unsupported:
        raise LoadError("TurboBulk compiler does not yet cover canonical kinds: " + ", ".join(unsupported))
    receipt_path = Path(receipt_path)
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    branch_row = _branch(client, branch)
    raw_sha = hashlib.sha256(raw).hexdigest()
    target_contract = {"netbox": status.get("netbox-version"),
                       "plugins": {name: status.get("plugins", {}).get(name) for name in
                                   ("netbox_turbobulk", "netbox_branching")}}
    binding = {"receipt_version": RECEIPT_VERSION, "compiler_version": COMPILER_VERSION,
               "artifact": str(plan_path), "plan_sha256": raw_sha, "canonical_sha256": digest(plan),
               "target": client.base, "branch": branch, "branch_id": client.branch_id,
               "transport": "turbobulk+rest", "target_contract": target_contract}

    receipt = None
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; choose a new receipt and fresh branch")

    # A complete matching target is the strongest idempotency signal and needs no writes.
    readback_started = time.monotonic()
    inventory = fetch_inventory(client.base, client.token, (obj["kind"] for obj in plan["objects"]), client.branch_id)
    existing = verify_plan(plan, inventory, strict_inventory=True)
    preflight_readback_seconds = round(time.monotonic() - readback_started, 6)
    if existing["success"]:
        current_ids = _numeric_ids(existing["ids"])
        checkpoint_ids = _numeric_ids(receipt.get("resolved_ids", {})) if receipt else {}
        changed_ids = sorted(key for key, value in checkpoint_ids.items()
                             if current_ids.get(key) != value)
        if changed_ids:
            raise LoadError(f"{len(changed_ids)} checkpoint target IDs changed despite exact natural-key readback; use a fresh branch and receipt")
        paths = _verify_paths(client, plan, objects, current_ids)
        if paths["failures"]:
            raise LoadError(f"computed-path readback failed for {len(paths['failures'])} of {paths['cables_expected']} cables")
        observation = {"observed_at": _now(), "wall_seconds": round(time.monotonic() - overall, 6),
                       "readback_seconds": preflight_readback_seconds,
                       "verification": existing, "computed_paths": paths}
        if receipt is not None:
            recovered = not receipt.get("success")
            receipt.setdefault("attempts", []).append({"started_at": started_at,
                                                        "completed_at": observation["observed_at"],
                                                        "wall_seconds": observation["wall_seconds"],
                                                        "result": "recovered-matched" if recovered else "already-matched",
                                                        "success": True})
            receipt.setdefault("repeat_verifications", []).append(observation)
            receipt["last_verified_at"] = observation["observed_at"]
            receipt["verification"] = existing
            receipt["computed_paths"] = paths
            receipt["resolved_ids"] = current_ids
            receipt["success"] = True
            if recovered:
                receipt["result"] = "recovered-matched"
                receipt["completed_at"] = observation["observed_at"]
                receipt["wall_seconds"] = round(time.monotonic() - overall, 6)
                receipt.pop("error", None)
                receipt.pop("failed_at", None)
            else:
                receipt.setdefault("result", "already-matched")
        else:
            receipt = {**binding, "started_at": started_at, "completed_at": observation["observed_at"],
                       "wall_seconds": observation["wall_seconds"], "success": True,
                       "result": "already-matched", "target_status": status, "offline_checks": offline,
                       "preflight": {"strict_existing_readback": existing,
                                     "readback_seconds": preflight_readback_seconds},
                       "computed_paths": paths, "jobs": [], "rest_batches": [],
                       "attempts": [{"started_at": started_at, "completed_at": observation["observed_at"],
                                     "wall_seconds": observation["wall_seconds"],
                                     "result": "already-matched", "success": True}],
                       "resolved_ids": current_ids, "verification": existing}
        _write_receipt(receipt_path, receipt)
        return receipt

    if receipt is not None:
        if receipt.get("success"):
            raise LoadError("successful receipt no longer matches strict readback; target changed")
        ids = {key: int(value) for key, value in receipt.get("resolved_ids", {}).items()}
        receipt.setdefault("jobs", [])
        receipt.setdefault("rest_batches", [])
        receipt.setdefault("attempts", []).append({"started_at": started_at, "success": False})
    else:
        occupied = {kind: len(rows) for kind, rows in inventory.items() if rows}
        if occupied:
            detail = ", ".join(f"{kind}={count}" for kind, count in sorted(occupied.items()))
            raise LoadError("fresh load requires empty inventories for every emitted kind; " + detail)
        ids = {}
        receipt = {**binding, "started_at": started_at, "success": False, "target_status": status,
                   "offline_checks": offline, "jobs": [], "rest_batches": [], "resolved_ids": {},
                   "attempts": [{"started_at": started_at, "success": False}]}
    receipt["preflight"] = {"strict_existing_readback": existing,
                            "readback_seconds": preflight_readback_seconds,
                            "branch": {key: branch_row.get(key) for key in ("id", "name", "schema_id", "status")}}
    _write_receipt(receipt_path, receipt)

    try:
        receipt["preflight"]["transport"] = _schema_preflight(client, objects)
        _write_receipt(receipt_path, receipt)
        content_types = _content_types(client)
        for phase_number, keys in enumerate(_phases(objects), 1):
            grouped = defaultdict(list)
            for key in keys:
                grouped[objects[key]["kind"]].append(objects[key])
            for kind in sorted(grouped):
                candidates = grouped[kind]
                completed = [obj for obj in candidates if obj["key"] in ids]
                if completed:
                    _refresh(client, kind, completed, ids)
                pending = [obj for obj in candidates if obj["key"] not in ids]
                purpose = f"phase-{phase_number}:{kind}"
                if pending:
                    prior = next((job for job in receipt["jobs"] if job["purpose"] == purpose), None)
                    if prior:
                        job = _poll(client, prior["job_id"], timeout)
                        _record_terminal(prior, job)
                        _write_receipt(receipt_path, receipt)
                        _refresh(client, kind, pending, ids)
                    else:
                        # Existing identities without a bound receipt could belong to someone else.
                        before = dict(ids)
                        _refresh(client, kind, pending, ids, allow_missing=True)
                        adopted = sorted(set(ids) - set(before))
                        if adopted:
                            raise LoadError(f"found {len(adopted)} uncheckpointed {kind} identities; discard this branch and start with a new branch and receipt")
                        rows = ([obj["attrs"] for obj in pending] if kind == "cable" else
                                [_render(obj, objects, ids, content_types) for obj in pending])
                        _submit(client, branch, SPECS[kind][0], rows, purpose,
                                [obj["key"] for obj in pending], receipt, receipt_path, timeout)
                        _refresh(client, kind, pending, ids)
                if kind == "cable":
                    termination_purpose = purpose + ":terminations"
                    prior_terminations = next((job for job in receipt["jobs"]
                                               if job["purpose"] == termination_purpose), None)
                    if prior_terminations:
                        job = _poll(client, prior_terminations["job_id"], timeout)
                        _record_terminal(prior_terminations, job)
                    else:
                        terminations = []
                        for obj in candidates:
                            for side, field in (("A", "a"), ("B", "b")):
                                target = objects[obj["refs"][field]]
                                terminations.append({"cable_id": ids[obj["key"]], "cable_end": side,
                                                     "termination_type_id": content_types[target["kind"]],
                                                     "termination_id": ids[target["key"]]})
                        _submit(client, branch, "dcim.cabletermination", terminations,
                                termination_purpose, [], receipt, receipt_path, timeout)
                receipt["resolved_ids"] = ids
                _write_receipt(receipt_path, receipt)

        receipt["rest_rows_attempted"] = _complete_rest(client, plan, objects, ids, receipt, receipt_path)
        readback_started = time.monotonic()
        inventory = fetch_inventory(client.base, client.token, (obj["kind"] for obj in plan["objects"]), client.branch_id)
        verification = verify_plan(plan, inventory, strict_inventory=True)
        receipt["verification"] = verification
        receipt["readback_seconds"] = round(time.monotonic() - readback_started, 6)
        if not verification["success"]:
            raise LoadError(f"strict readback found {verification['mismatch_count']} mismatches")
        receipt["computed_paths"] = _verify_paths(client, plan, objects, ids)
        if receipt["computed_paths"]["failures"]:
            raise LoadError(f"computed-path readback failed for {len(receipt['computed_paths']['failures'])} of {receipt['computed_paths']['cables_expected']} cables")
        receipt.pop("error", None)
        receipt.pop("failed_at", None)
        receipt.update(success=True, result="loaded", completed_at=_now(),
                       wall_seconds=round(time.monotonic() - overall, 6), resolved_ids=ids)
        receipt["attempts"][-1].update(success=True, result="loaded",
                                       completed_at=receipt["completed_at"],
                                       wall_seconds=receipt["wall_seconds"])
        _write_receipt(receipt_path, receipt)
        return receipt
    except Exception as exc:
        if isinstance(exc, JobTimeout):
            entry = next((job for job in receipt["jobs"] if job.get("job_id") == exc.job_id), None)
            if entry is not None:
                _record_observed(entry, exc.job)
        receipt.update(success=False, failed_at=_now(), error=str(exc), resolved_ids=ids)
        receipt["attempts"][-1].update(success=False, result="failed",
                                       completed_at=receipt["failed_at"],
                                       wall_seconds=round(time.monotonic() - overall, 6),
                                       error=str(exc))
        _write_receipt(receipt_path, receipt)
        raise


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Load one frozen Genial plan through TurboBulk plus bounded REST completion")
    parser.add_argument("artifact", help="generated directory or plan.json")
    parser.add_argument("--target", default=os.environ.get("NETBOX_URL"))
    parser.add_argument("--branch", required=True, help="ready disposable NetBox branch name")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not args.target or not token:
        parser.error("--target/NETBOX_URL and NETBOX_TOKEN are required")
    try:
        result = load(args.artifact, url=args.target, token=token, branch=args.branch,
                      receipt_path=args.receipt, timeout=args.timeout)
        print(json.dumps({"success": True, "result": result["result"], "transport": result["transport"],
                          "objects": result["verification"]["matched_objects"],
                          "receipt": str(args.receipt)}, sort_keys=True))
        return 0
    except (LoadError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Load failed: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
