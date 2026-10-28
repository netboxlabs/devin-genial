"""The pinned NetBox data model, and Genial's hand-maintained facts checked against it.

NetBox Labs' private ``netbox-datamodel-extractor`` repository publishes one
declarative schema per NetBox version, read from a running NetBox's Django
``_meta`` (field types and ``max_length``, unique constraints, ratified
natural keys, ``on_delete``, what ``save()`` fills in, what a create also
creates). Genial keeps several of those facts by hand — ``naming.NAME_LIMITS``,
the Diode identities behind ``docs/schema-map.md``, the save-derived columns
``turbobulk._save_copies`` compiles — and each was read once from NetBox
source. This module pins the extractor's 4.7.1 baseline to a commit, derives
the bounded extract ``catalog/netbox-datamodel-4.7.1.json`` from it, and
checks the hand-maintained facts against the extract, so a NetBox upgrade or
a misread fact fails ``just check`` instead of a live load.

Only ``derive`` reads the extractor's YAML, through the small block-YAML reader
below (the baseline is machine-written: block mappings and lists, one-line flow
mappings, single-quoted or folded scalars; nothing else). Everything the tests
read comes from the JSON extract, so the stdlib is enough and the 900 KB
baseline never enters the repository.

Checks are read-only over a frozen plan or the loader's own tables. They never
change generator behaviour: a discrepancy is reported with the baseline's
evidence, and ``tests/test_datamodel_pin.py`` documents the ones left for a
human decision.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import diode, naming, turbobulk

EXTRACT = Path(__file__).resolve().parent.parent / "catalog" / "netbox-datamodel-4.7.1.json"
NETBOX_VERSION = "4.7.1"
SOURCE_REPO = "netboxlabs/netbox-datamodel-extractor"
# A commit, never a branch: the extractor re-extracts every baseline when the
# extractor changes, so "which data model did the test read" must be one SHA.
SOURCE_COMMIT = "d6193772e8597faa27ea2014b21ea5544b8f72fa"
SOURCE_PATH = f"netbox_data_models/{NETBOX_VERSION}/{NETBOX_VERSION}.yaml"
LICENSE = "PolyForm Shield License 1.0.0 (https://polyformproject.org/licenses/shield/1.0.0)"

# Field keys the extract keeps per field; everything else (descriptions,
# filters, validators, acceptance flags) stays in the baseline.
FIELD_KEYS = ("type", "max_length", "on_delete", "db_only", "read_only", "default",
              "supplied_by_netbox", "api_name")

# Genial reference names that are not a NetBox field of the same name, with the
# baseline field(s) they resolve to. Generic foreign keys resolve through the
# synthetic ``ref:A|B`` field's ``api_mapping`` when the model has one and to
# the ``<name>_type``/``<name>_id`` pair otherwise; these are the remainder.
REF_ALIASES = {
    ("cable", "a"): ("a_terminations",), ("cable", "b"): ("b_terminations",),
    ("cluster", "scope_site"): ("scope_type", "scope_id"),
    ("cluster", "scope_location"): ("scope_type", "scope_id"),
    ("prefix", "scope_site"): ("scope_type", "scope_id"),
    ("vlan_group", "scope_site"): ("scope_type", "scope_id"),
    ("vlan_group", "scope_location"): ("scope_type", "scope_id"),
    ("wireless_lan", "scope_site"): ("scope_type", "scope_id"),
    ("service", "virtual_machine"): ("parent_object_type", "parent_object_id"),
    ("fhrp_group_assignment", "interface"): ("interface_type", "interface_id"),
    ("event_rule", "action_object"): ("action_object_type", "action_object_id"),
    ("config_context", "roles"): ("roles",),
}


# --- a reader for the extractor's block YAML -------------------------------

class BaselineError(ValueError):
    pass


_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+\.\d+$")
_ENTRY = re.compile(r"^(- |[\w.|'\"-]+:( |$))")
_INLINE_KEY = re.compile(r"^(?:[\w.|-]+|'[^']*'):( |$)")
_QUOTED_VALUE = re.compile(r"^(?:- |[\w.|'\"-]+: )?'")


def _scalar(text):
    text = text.strip()
    if text in ("", "null", "~"):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "{}":
        return {}
    if text == "[]":
        return []
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'":
            raise BaselineError(f"unterminated quote: {text!r}")
        return text[1:-1].replace("''", "'")
    if text[0] == '"':
        if len(text) < 2 or text[-1] != '"':
            raise BaselineError(f"unterminated quote: {text!r}")
        return text[1:-1].encode().decode("unicode_escape")
    if text[0] == "{":
        return _flow_map(text)
    if text[0] == "[":
        return _flow_list(text)
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


def _split_flow(body):
    parts, depth, current, quote = [], 0, "", None
    for ch in body:
        if quote:
            current += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return [part.strip() for part in parts]


def _flow_map(text):
    if not text.endswith("}"):
        raise BaselineError(f"unterminated flow map: {text!r}")
    out = {}
    for part in _split_flow(text[1:-1]):
        key, _, value = part.partition(":")
        out[_scalar(key)] = _scalar(value)
    return out


def _flow_list(text):
    if not text.endswith("]"):
        raise BaselineError(f"unterminated flow list: {text!r}")
    return [_scalar(part) for part in _split_flow(text[1:-1])]


def _open_quote(body):
    """True while a single-quoted scalar started on this line is unterminated."""
    match = _QUOTED_VALUE.match(body)
    if not match:
        return False
    return body[match.end():].replace("''", "").count("'") % 2 == 0


def _lines(text):
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        out.append((number, len(raw) - len(raw.lstrip(" ")), raw.strip()))
    # Join continuation lines (a quoted scalar spanning lines, or a folded
    # plain scalar indented deeper than its key) back onto their key line.
    joined, i = [], 0
    while i < len(out):
        number, indent, body = out[i]
        if _open_quote(body):
            while i + 1 < len(out) and _open_quote(body):
                i += 1
                body += " " + out[i][2]
        else:
            while i + 1 < len(out) and out[i + 1][1] > indent and not _ENTRY.match(out[i + 1][2]):
                i += 1
                body += " " + out[i][2]
        joined.append((number, indent, body))
        i += 1
    return joined


def load_yaml(text):
    """Parse the extractor's block YAML; refuse anything it does not write."""
    lines = _lines(text)
    if not lines:
        return None
    value, index = _block(lines, 0, lines[0][1])
    if index != len(lines):
        raise BaselineError(f"trailing content at line {lines[index][0]}")
    return value


def _block(lines, index, indent):
    if lines[index][2].startswith("- "):
        return _list(lines, index, indent)
    return _map(lines, index, indent)


def _map(lines, index, indent):
    out = {}
    while index < len(lines):
        number, ind, body = lines[index]
        if ind < indent or body.startswith("- "):
            break
        if ind > indent:
            raise BaselineError(f"unexpected indent at line {number}")
        key, sep, rest = body.partition(":")
        if not sep or (rest and not rest.startswith(" ")):
            raise BaselineError(f"not a mapping entry at line {number}: {body!r}")
        key, rest = _scalar(key), rest.strip()
        index += 1
        if rest:
            out[key] = _scalar(rest)
        elif index < len(lines) and lines[index][1] > indent:
            out[key], index = _block(lines, index, lines[index][1])
        elif index < len(lines) and lines[index][1] == indent and lines[index][2].startswith("- "):
            out[key], index = _list(lines, index, indent)
        else:
            out[key] = None
    return out, index


def _nested(lines, index, indent, number, item):
    """Collect a list item's continuation lines into a sub-block at indent+2."""
    child = indent + 2
    sub = [(number, child, item)]
    while index < len(lines) and lines[index][1] > indent:
        sub.append(lines[index])
        index += 1
    return sub, child, index


def _list(lines, index, indent):
    out = []
    while index < len(lines):
        number, ind, body = lines[index]
        if ind < indent or not body.startswith("- "):
            break
        if ind > indent:
            raise BaselineError(f"unexpected indent at line {number}")
        item = body[2:].strip()
        index += 1
        if item.startswith("- ") or _INLINE_KEY.match(item):
            sub, child, index = _nested(lines, index, indent, number, item)
            parse = _list if item.startswith("- ") else _map
            value, used = parse(sub, 0, child)
            if used != len(sub):
                raise BaselineError(f"unparsed list item at line {sub[used][0]}")
            out.append(value)
        elif item == "":
            value, index = _block(lines, index, lines[index][1])
            out.append(value)
        else:
            out.append(_scalar(item))
    return out, index


# --- derivation -----------------------------------------------------------

def genial_models():
    """content_type -> kind for every canonical kind that is a NetBox core model."""
    out = {}
    for kind, (model, _endpoint) in turbobulk.SPECS.items():
        if model and not model.startswith("netbox_bgp."):
            out[model] = kind
    return out


def derive(text, *, source_commit=SOURCE_COMMIT, baseline_sha256=None):
    """The bounded extract: the baseline's facts for the models Genial emits."""
    doc = load_yaml(text)
    source = doc.get("source") or {}
    if source.get("netbox_version") != NETBOX_VERSION:
        raise BaselineError(f"baseline is NetBox {source.get('netbox_version')!r}, expected {NETBOX_VERSION}")
    models = doc["models"]
    by_name = {name: model.get("content_type") for name, model in models.items()}
    wanted = genial_models()
    ratified = doc.get("ratified") or {}
    out = {}
    for name, model in models.items():
        content_type = model.get("content_type")
        if content_type not in wanted:
            continue
        fields = {}
        for field, spec in (model.get("fields") or {}).items():
            kept = {key: spec[key] for key in FIELD_KEYS if key in spec}
            mapping = (spec.get("api_mapping") or {}).get("polymorphic")
            if mapping:
                kept["polymorphic"] = {k: mapping[k] for k in ("type_field", "id_field") if k in mapping}
            fields[field] = kept
        api_only = {field: {k: spec[k] for k in ("type", "on_write", "writes") if k in spec}
                    for field, spec in (model.get("api_only_fields") or {}).items()}
        creates = []
        for entry in model.get("on_create_creates") or []:
            row = {"model": by_name.get(entry["model"], entry["model"]),
                   "from": by_name.get(entry.get("from"), entry.get("from")), "via": entry.get("via")}
            for key in ("skipped_if", "adopts_if"):
                if entry.get(key) is not None:
                    row[key] = entry[key]
            creates.append(row)
        decision = ratified.get(f"{name}.natural_key_fields") or {}
        out[content_type] = {
            "model": name, "kind": wanted[content_type], "endpoint": model.get("endpoint"),
            "natural_key_fields": model.get("natural_key_fields"),
            "natural_key_status": decision.get("status"),
            "unique_constraints": model.get("unique_constraints") or [],
            "fields": fields, "api_only_fields": api_only, "on_create_creates": creates,
        }
    missing = sorted(set(wanted) - set(out))
    if missing:
        raise BaselineError(f"baseline has no model for {', '.join(missing)}")
    provenance = {
        "source_repo": SOURCE_REPO, "source_path": SOURCE_PATH, "source_commit": source_commit,
        "baseline_sha256": baseline_sha256, "extractor_commit": source.get("extractor_commit"),
        "schema_version": doc.get("schema_version"), "netbox_version": NETBOX_VERSION,
        "license": LICENSE,
        "note": "Derived extract of the extractor's committed baseline, bounded to the NetBox models "
                "Genial emits (estates/datamodel.py derive). Regenerate with `just datamodel-pin`.",
    }
    return {"provenance": provenance, "models": dict(sorted(out.items()))}


def load(path=EXTRACT):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _by_kind(extract):
    return {model["kind"]: model for model in extract["models"].values()}


# --- checks over the loader's tables --------------------------------------

def name_limit_findings(extract, limits=None, default=None):
    """(mismatches, missing): NAME_LIMITS entries the baseline contradicts, and
    emitted kinds whose native ``name`` limit is below the default but absent."""
    limits = naming.NAME_LIMITS if limits is None else limits
    default = naming.NAME_LIMIT_DEFAULT if default is None else default
    models = _by_kind(extract)
    mismatches, missing = {}, {}
    for kind, limit in sorted(limits.items()):
        actual = models.get(kind, {}).get("fields", {}).get("name", {}).get("max_length")
        if actual != limit:
            mismatches[kind] = {"declared": limit, "baseline": actual}
    for kind, model in sorted(models.items()):
        actual = model["fields"].get("name", {}).get("max_length")
        if actual is not None and actual < default and kind not in limits:
            missing[kind] = actual
    return mismatches, missing


def resolve_ref(extract, kind, ref):
    """The baseline column(s) a Genial reference name lands on, or None."""
    model = _by_kind(extract).get(kind)
    if model is None:
        return None
    fields = model["fields"]
    if (kind, ref) in REF_ALIASES:
        columns = REF_ALIASES[(kind, ref)]
    elif ref in turbobulk.REST_PATCH_FIELD_NAMES:
        # A reference the loader completes over REST under another name
        # (FrontPort.rear_port became the API-only rear_ports mapping list in 4.5).
        columns = (turbobulk.REST_PATCH_FIELD_NAMES[ref],)
    elif ref in fields and fields[ref].get("polymorphic"):
        mapping = fields[ref]["polymorphic"]
        columns = (mapping["type_field"], mapping["id_field"])
    elif ref in fields:
        columns = (ref,)
    elif f"{ref}_type" in fields and f"{ref}_id" in fields:
        columns = (f"{ref}_type", f"{ref}_id")
    elif ref in model["api_only_fields"]:
        columns = (ref,)
    else:
        return None
    if all(column in fields or column in model["api_only_fields"] for column in columns):
        return columns
    return None


def endpoint_findings(extract, specs=None):
    """Loader endpoints (schema-map REST endpoint column) the baseline's pynetbox
    path (`app.model_names`) does not rebuild as `/api/app/model-names/`."""
    specs = turbobulk.SPECS if specs is None else specs
    findings = {}
    for content_type, model in sorted(extract["models"].items()):
        kind = model["kind"]
        if kind not in specs:
            continue
        app, _, names = (model.get("endpoint") or "").partition(".")
        rebuilt = f"/api/{app}/{names.replace('_', '-')}/" if names else None
        if rebuilt != specs[kind][1] or specs[kind][0] != content_type:
            findings[kind] = {"loader": specs[kind], "baseline": (content_type, model.get("endpoint"))}
    return findings


def reference_findings(extract, refs=None):
    """Genial reference names (schema-map References column) with no baseline column."""
    refs = turbobulk.SUPPORTED_REFS if refs is None else refs
    models = _by_kind(extract)
    findings = {}
    for kind, names in sorted(refs.items()):
        if kind not in models:
            continue
        unresolved = sorted(ref for ref in names if resolve_ref(extract, kind, ref) is None)
        if unresolved:
            findings[kind] = unresolved
    return findings


def identity_findings(extract, identities=None):
    """Diode identities (schema-map Identity column) no baseline unique constraint
    or natural key proves unique: kind -> what the baseline states instead."""
    identities = diode._IDENTITY if identities is None else identities
    models = _by_kind(extract)
    findings = {}
    for kind, (attrs, scope) in sorted(identities.items()):
        model = models.get(kind)
        if model is None:
            continue
        columns = set()
        for name in attrs:
            columns.update(resolve_ref(extract, kind, name) or (name,))
        for ref in scope:
            columns.update(resolve_ref(extract, kind, ref) or (ref,))
        keys = [set(c["fields"]) for c in model["unique_constraints"]]
        natural = model.get("natural_key_fields")
        if natural and model.get("natural_key_status") == "affirmed":
            keys.append(set(natural))
        if not any(key <= columns for key in keys):
            findings[kind] = {"identity": sorted(columns),
                              "unique_constraints": [sorted(c["fields"]) for c in model["unique_constraints"]],
                              "natural_key_fields": natural, "natural_key_status": model.get("natural_key_status")}
    return findings


# Columns the compiler writes because NetBox would derive them in save(), a
# pre_save or a signal (docs/loading.md save-derived table), by kind.
SAVE_DERIVED = {
    "rack": turbobulk.RACK_TYPE_COPIES,
    "device_type": ("_abs_weight",),
    "device": turbobulk.DEVICE_TYPE_COPIES + ("location",),
    "interface": ("_name", "rf_channel_frequency", "rf_channel_width"),
    "vm_interface": ("_name",),
    "ip_range": ("size",),
    "power_feed": ("available_power",),
    "cable": ("_abs_length",),
    "vlan_group": ("vid_ranges", "total_vlan_ids"),
    "prefix": ("_depth", "_children", "_site", "_region", "_site_group", "_location"),
    "cluster": ("_site", "_region", "_site_group", "_location"),
    "wireless_lan": ("_site", "_region", "_site_group", "_location"),
    "virtual_machine": ("site",),
}
for _kind in sorted(turbobulk.DEVICE_COMPONENT_KINDS):
    SAVE_DERIVED[_kind] = SAVE_DERIVED.get(_kind, ()) + ("_site", "_location", "_rack")


def save_derived_findings(extract):
    """How the baseline accounts for each compiled column.

    Returns ``{"missing": [...], "stated": {...}, "unstated": {...}}``: columns
    the baseline has no field for; columns it states NetBox supplies (with the
    mechanism and source); and columns it only marks ``db_only``/``read_only``
    (the API never takes them, so the raw insert must compile them) without
    saying how NetBox fills them — a limit of the baseline, not evidence either way.
    """
    models = _by_kind(extract)
    missing, stated, unstated = [], {}, {}
    for kind, columns in sorted(SAVE_DERIVED.items()):
        fields = models[kind]["fields"]
        for column in columns:
            spec = fields.get(column)
            if spec is None:
                missing.append(f"{kind}.{column}")
            elif spec.get("supplied_by_netbox"):
                stated[f"{kind}.{column}"] = spec["supplied_by_netbox"]
            else:
                unstated[f"{kind}.{column}"] = {k: spec.get(k) for k in ("db_only", "read_only", "default")}
    return {"missing": missing, "stated": stated, "unstated": unstated}


def default_findings(extract):
    """Loader defaults (turbobulk.RENDER_DEFAULTS and the VLAN-group fill) that
    disagree with the baseline's model defaults."""
    models = _by_kind(extract)
    declared = {kind: dict(values) for kind, values in turbobulk.RENDER_DEFAULTS.items()}
    declared.setdefault("vlan_group", {}).update(
        turbobulk._save_copies({"kind": "vlan_group", "attrs": {}, "refs": {}}, {}))
    findings = {}
    for kind, values in sorted(declared.items()):
        for column, value in sorted(values.items()):
            baseline = models[kind]["fields"].get(column, {}).get("default")
            if column == "vid_ranges":
                # The plan carries the Postgres range literal; the baseline the list form.
                value = [[int(v) for v in r.strip("[)").split(",")] for r in value]
                value = [[lo, hi - 1] for lo, hi in value]
            if baseline != value:
                findings[f"{kind}.{column}"] = {"loader": value, "baseline": baseline}
    return findings


def template_findings(extract):
    """Component templates NetBox would instantiate on create (``on_create_creates``)
    that Genial also emits as a kind: both would make the component."""
    kinds = {model["kind"] for model in extract["models"].values()}
    by_ct = extract["models"]
    findings = []
    for content_type, model in sorted(by_ct.items()):
        for entry in model["on_create_creates"]:
            template = entry.get("from")
            if template in by_ct and by_ct[template]["kind"] in kinds:
                findings.append(f"{model['kind']} creates {entry['model']} from emitted {by_ct[template]['kind']}")
    return findings


# --- checks over a frozen plan ---------------------------------------------

def plan_name_findings(extract, objects):
    """Emitted string attributes longer than the baseline's ``max_length``."""
    models = _by_kind(extract)
    findings = []
    for key, obj in sorted(objects.items()):
        fields = (models.get(obj["kind"]) or {}).get("fields", {})
        for attr, value in obj["attrs"].items():
            limit = fields.get(attr, {}).get("max_length")
            if isinstance(value, str) and limit is not None and len(value) > limit:
                findings.append({"key": key, "kind": obj["kind"], "field": attr,
                                 "length": len(value), "max_length": limit})
    return findings


def plan_save_copy_findings(extract, objects):
    """Rows whose compiled value disagrees with what the baseline says ``save()``
    copies from a referenced row (``supplied_by_netbox`` with ``mechanism: save``
    and a ``from: <ref>.<field>``), which the raw insert skips."""
    models = _by_kind(extract)
    findings = []

    def value(obj, column, compiled):
        # A column is an attribute or, for a foreign key, a reference (plan key).
        if column in compiled:
            return compiled[column]
        if column in obj["attrs"]:
            return obj["attrs"][column]
        return obj["refs"].get(column)

    for key, obj in sorted(objects.items()):
        model = models.get(obj["kind"])
        if model is None:
            continue
        compiled = turbobulk._save_copies(obj, objects)
        for column, spec in model["fields"].items():
            for fill in spec.get("supplied_by_netbox") or []:
                source = fill.get("from")
                if fill.get("mechanism") != "save" or not source or "." not in source:
                    continue
                ref, _, field = source.partition(".")
                target_key = obj["refs"].get(ref)
                if target_key not in objects:
                    continue
                expected = value(objects[target_key], field, {})
                if expected is None:
                    continue
                actual = value(obj, column, compiled)
                if (fill.get("overwrites_sent_value") or actual is None) and actual != expected:
                    findings.append({"key": key, "kind": obj["kind"], "field": column, "from": source,
                                     "compiled": actual, "expected": expected})
    return findings


def plan_findings(extract, objects):
    return {"names": plan_name_findings(extract, objects),
            "save_copies": plan_save_copy_findings(extract, objects)}


# --- CLI --------------------------------------------------------------------

def _derive_command(args):
    import hashlib
    text = Path(args.baseline).read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    extract = derive(text, source_commit=args.commit, baseline_sha256=digest)
    out = Path(args.out)
    out.write_text(json.dumps(extract, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(extract['models'])} models, baseline sha256 {digest[:12]}…)")


def _check_command(args):
    extract = load(args.extract)
    report = {
        "provenance": extract["provenance"],
        "name_limits": dict(zip(("mismatches", "missing"), name_limit_findings(extract))),
        "endpoints": endpoint_findings(extract),
        "references": reference_findings(extract),
        "identities": identity_findings(extract),
        "save_derived": save_derived_findings(extract),
        "defaults": default_findings(extract),
        "templates": template_findings(extract),
    }
    for path in args.plan:
        with Path(path).open(encoding="utf-8") as handle:
            plan = json.load(handle)
        objects = {obj["key"]: obj for obj in plan["objects"]}
        report.setdefault("plans", {})[path] = plan_findings(extract, objects)
    json.dump(report, sys.stdout, indent=1)
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m estates.datamodel", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    derive_cmd = sub.add_parser("derive", help="derive the bounded extract from an extractor baseline")
    derive_cmd.add_argument("baseline", help=f"path to the extractor's {SOURCE_PATH}")
    derive_cmd.add_argument("--commit", required=True, help="extractor commit the baseline was read at")
    derive_cmd.add_argument("--out", default=str(EXTRACT))
    derive_cmd.set_defaults(func=_derive_command)
    check_cmd = sub.add_parser("check", help="report the hand-maintained facts against the extract")
    check_cmd.add_argument("plan", nargs="*", help="frozen plan.json files to check as well")
    check_cmd.add_argument("--extract", default=str(EXTRACT))
    check_cmd.set_defaults(func=_check_command)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
