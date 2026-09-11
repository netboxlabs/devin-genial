"""CLI: plan recipes, build frozen plans, and independently check artifacts."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib

from .generate import generate
from .intent import resolution
from .diode import export, verify_export
from .model import DesignError, canonical, digest, hardware_catalog
from .report import markdown, summary, type_coverage
from .scenarios import create as create_scenario, markdown as scenario_markdown
from . import __version__, power_scenario
from .validate import validate


def checked(plan):
    if (not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or
            plan["schema_version"] != 1 or plan.get("generator_version") != __version__):
        raise DesignError(f"Expected a frozen schema 1 plan from generator {__version__}; regenerate unsupported or incomplete plans")
    findings = validate(plan)
    if findings:
        raise DesignError(f"{len(findings)} validation failure(s):\n" + "\n".join(
            f"{f['code']}: {f['object']}: {f['message']}" for f in findings[:20]))
    if plan.get("hardware_digest") != digest(hardware_catalog()):
        raise DesignError("Plan hardware digest differs from the current pinned catalog")
    return plan


@contextmanager
def new_output(destination):
    """Publish a complete artifact directory, preserving earlier builds on failure."""
    destination = Path(destination)
    if destination.exists():
        raise DesignError(f"Output {destination} already exists. Choose a new build path; existing artifacts are preserved.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        yield temporary
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _write_artifact(plan, destination, supplied=None):
    """Write an already checked snapshot; callers own the validation boundary."""
    destination.mkdir(exist_ok=True)
    (destination / "plan.json").write_bytes(canonical(plan) + b"\n")
    (destination / "report.md").write_text(markdown(plan))
    (destination / "coverage.json").write_text(json.dumps(type_coverage(plan), indent=2) + "\n")
    (destination / "intent.json").write_bytes(canonical(resolution(plan, supplied)) + b"\n")
    return export(plan, destination / "diode")


def build(plan, destination, supplied=None):
    checked(plan)
    demo = plan["recipe"].get("demo", "baseline")
    if demo in ("loss-of-power-diversity", "provider-span-maintenance"):
        return build_snapshot_scenario(plan, None, destination, supplied, kind=demo)
    with new_output(destination) as temporary:
        manifest = _write_artifact(plan, temporary, supplied)
        (temporary / "checks.json").write_bytes(canonical({"status": "passed", "scope": "offline",
            "plan_sha256": digest(plan), "findings": [], "live_ingestion": "not run",
            "source_checked_target": manifest["source_checked_target"],
            "known_incompatible_netbox": manifest["known_incompatible_netbox"]}) + b"\n")
    return summary(plan) | {"output": str(destination), "diode_files": len(manifest["files"]), "checks": "offline passed",
                           "source_checked_target": manifest["source_checked_target"],
                           "known_incompatible_netbox": manifest["known_incompatible_netbox"]}


def _snapshot_scenario(kind):
    if kind == "loss-of-power-diversity":
        return power_scenario
    if kind == "provider-span-maintenance":
        from . import span_scenario
        return span_scenario
    raise DesignError(f"Unsupported saved snapshot scenario {kind!r}; expected power diversity or provider span maintenance")


def build_snapshot_scenario(plan, selection, destination, supplied=None, *, kind):
    checked(plan)
    implementation = _snapshot_scenario(kind)
    scenario = implementation.create(plan, selection)
    checks = implementation.verify(scenario)
    maintenance = kind == "provider-span-maintenance"
    changed_status = "expected-maintenance verified" if maintenance else "expected-defect verified"
    with new_output(destination) as temporary:
        for stage, snapshot in scenario["plans"].items():
            manifest = _write_artifact(snapshot, temporary / stage, supplied)
            findings = validate(snapshot)
            status = "passed" if stage == "baseline" else changed_status
            intent = resolution(snapshot, supplied)
            intent["checks"]["offline_graph"] = status
            intent["scenario_stage"] = stage
            intent["demo"] = scenario["scenario"]
            intent["demo_scope"] = (f"Healthy baseline for {kind}." if stage == "baseline" else
                "Planned span maintenance; ordinary baseline validation must fail. Use scenario-check for exact expected findings and the scenario report for remaining further-failure protection."
                if maintenance else "Deliberate power-diversity defect; ordinary validation must fail. Use scenario-check for exact expected findings.")
            (temporary / stage / "intent.json").write_bytes(canonical(intent) + b"\n")
            if stage == "changed":
                report = temporary / stage / "report.md"
                notice = ("**Planned span maintenance.** Ordinary baseline validation reports the expected unavailable span "
                          "and any loss of further-failure protection identified in the scenario report. Physical inventory remains installed; modeled alternate routes "
                          "are not executed failover. Use the parent scenario report and scenario-check. "
                          "The local status transition remains unverified.\n\n" if maintenance else
                          "**Deliberate defect snapshot.** Ordinary validation must fail; use the parent scenario report and "
                          "scenario-check. Load only into a separate fresh target.\n\n")
                report.write_text(notice + report.read_text())
            (temporary / stage / "checks.json").write_bytes(canonical({"status": status,
                "scope": "offline scenario snapshot", "plan_sha256": digest(snapshot),
                "findings": findings, "live_ingestion": "not run",
                "source_checked_target": manifest["source_checked_target"],
                "known_incompatible_netbox": manifest["known_incompatible_netbox"]}) + b"\n")
        (temporary / "report.md").write_text(implementation.markdown(scenario))
        scenario["plan_files"] = {stage: f"{stage}/plan.json" for stage in scenario.pop("plans")}
        (temporary / "scenario.json").write_bytes(canonical(scenario) + b"\n")
        (temporary / "checks.json").write_bytes(canonical({"status": changed_status,
            "scope": "offline scenario", "live_ingestion": "not run", **checks}) + b"\n")
    return {"scenario": scenario["scenario"], "subject": scenario["subject"],
            "output": str(destination), "checks": changed_status,
            "expected_findings": scenario["expected_findings"], "applied_to_target": False}


def check_snapshot_scenario(path):
    scenario = json.loads(path.read_text())
    if not isinstance(scenario, dict):
        raise DesignError("Expected a saved scenario JSON object")
    implementation = _snapshot_scenario(scenario.get("scenario"))
    expected_files = {stage: f"{stage}/plan.json" for stage in ("baseline", "changed")}
    if scenario.get("plan_files") != expected_files or "plans" in scenario:
        raise DesignError("Expected a scenario.json with baseline/plan.json and changed/plan.json snapshots")
    scenario["plans"] = {stage: json.loads((path.parent / relative).read_text())
                         for stage, relative in expected_files.items()}
    checked(scenario["plans"]["baseline"])
    checks = implementation.verify(scenario)
    # Bind the loadable bytes to the checked graphs. SDK schema checking alone
    # would accept a healthy baseline export substituted for the changed one.
    for stage, snapshot in scenario["plans"].items():
        actual = path.parent / stage / "diode"
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary) / "diode"
            export(snapshot, expected)
            names = {item.name for item in expected.iterdir()}
            if not actual.is_dir() or {item.name for item in actual.iterdir()} != names:
                raise DesignError(f"Scenario {stage} Diode file inventory differs from its checked snapshot")
            for name in sorted(names):
                if not (actual / name).is_file() or (actual / name).read_bytes() != (expected / name).read_bytes():
                    raise DesignError(f"Scenario {stage} Diode file {name} differs from its checked snapshot; rebuild the scenario")
    return {"scenario": scenario["scenario"], "checks": "expected-maintenance verified"
            if scenario["scenario"] == "provider-span-maintenance" else "expected-defect verified",
            "evidence": checks, "wire_snapshots_match": True, "applied_to_target": False}


def build_scenario(plan, site, destination):
    with new_output(destination) as temporary:
        scenario = create_scenario(plan, site)
        plans = scenario.pop("plans")
        scenario["plan_files"] = {stage: f"{stage}/plan.json" for stage in plans}
        for stage, snapshot in plans.items():
            path = temporary / scenario["plan_files"][stage]
            path.parent.mkdir()
            path.write_bytes(canonical(snapshot) + b"\n")
        (temporary / "scenario.json").write_bytes(canonical(scenario) + b"\n")
        (temporary / "report.md").write_text(scenario_markdown(scenario))
        (temporary / "checks.json").write_bytes(canonical({"status": "passed", "scope": "offline",
            "live_ingestion": "not run", **scenario["checks"]}) + b"\n")
    return {"scenario": scenario["scenario"], "site": site, "output": str(destination),
            "endpoints_preserved": scenario["checks"]["endpoint_count"],
            "changes": {stage: change["counts"] for stage, change in scenario["changes"].items()},
            "checks": "offline passed", "applied_to_target": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate deterministic connected NetBox estates. No AI or network access required.")
    parser.add_argument("--json", action="store_true", help="machine-readable success/error output")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "generate"):
        p = sub.add_parser(command, help="resolve and validate a recipe" if command == "plan" else "plan, validate and export a recipe")
        p.add_argument("recipe", nargs="?", default="profiles/bank.toml")
        p.add_argument("--previous", type=Path, help="prior plan.json whose identities/reservations must survive growth")
        p.add_argument("--out", type=Path, required=command == "generate", help="new plan file (plan) or new build directory (generate)")
    p = sub.add_parser("build", help="export an existing frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="independently validate a saved plan")
    p.add_argument("plan", type=Path)
    p = sub.add_parser("report", help="print a report derived from a saved plan")
    p.add_argument("plan", type=Path)
    p = sub.add_parser("sdk-check", help="qualify Diode files against the optional pinned SDK")
    p.add_argument("directory", type=Path)
    p = sub.add_parser("scenario-check", help="verify saved power or span-maintenance snapshots, exact findings and restoration")
    p.add_argument("scenario", type=Path, help="scenario.json inside a generated scenario directory")
    p = sub.add_parser("scenario", help="generate graph-selected demo snapshots; no target writes")
    p.add_argument("plan", type=Path)
    p.add_argument("--kind", choices=("acquire-and-refresh", "loss-of-power-diversity", "provider-span-maintenance"), default="acquire-and-refresh")
    p.add_argument("--site", help="branch ID for acquisition, optional site ID filter for power diversity")
    p.add_argument("--span", help="optional actual Circuit key for provider span maintenance")
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "sdk-check":
            print(json.dumps(verify_export(args.directory), sort_keys=True))
            return 0
        if args.command == "scenario-check":
            result = check_snapshot_scenario(args.scenario)
            print(json.dumps(result, sort_keys=True) if args.json else
                  f"{result['scenario']}: exact expected findings and offline restoration verified; live workflow unverified")
            return 0
        if args.command in ("plan", "generate"):
            with open(args.recipe, "rb") as handle:
                supplied = tomllib.load(handle)
            previous = json.loads(args.previous.read_text()) if args.previous else None
            if previous is not None:
                checked(previous)
            plan = checked(generate(supplied, previous=previous))
        else:
            plan = checked(json.loads(args.plan.read_text()))
        if args.command == "scenario":
            if args.span is not None and args.kind != "provider-span-maintenance":
                raise DesignError("--span applies only to provider-span-maintenance; use --site for other scenarios")
            if args.kind in ("loss-of-power-diversity", "provider-span-maintenance"):
                if args.kind == "provider-span-maintenance" and args.site is not None:
                    raise DesignError("Provider span maintenance uses --span, not --site")
                result = build_snapshot_scenario(plan, args.span if args.kind == "provider-span-maintenance" else args.site,
                                                 args.out, kind=args.kind)
                boundary = "fresh targets only" if args.kind == "loss-of-power-diversity" else "live status transition unverified"
                print(json.dumps(result, sort_keys=True) if args.json else
                      f"{result['scenario']}: {args.out / 'report.md'}; {result['checks']}; {boundary}")
                return 0
            if plan["recipe"]["profile"] != "regional-bank":
                raise DesignError("Acquisition/refresh requires regional-bank; use --kind loss-of-power-diversity for a shared scenario")
            if not args.site:
                raise DesignError("Acquisition/refresh requires --site with an independent inherited branch ID")
            result = build_scenario(plan, args.site, args.out)
            if args.json:
                print(json.dumps(result, sort_keys=True))
            else:
                print(f"Acquisition/refresh: {args.site}; {result['endpoints_preserved']} endpoints retain their names and addressing")
                for stage, counts in result["changes"].items():
                    print(f"  {stage}: {counts['create']} create, {counts['update']} update, {counts['delete']} removed from candidate")
                print(f"Review: {args.out / 'report.md'}; offline checks passed")
                print("Candidate snapshots only. Diode replay does not apply the deletions or establish a migration.")
            return 0
        if args.command in ("generate", "build"):
            result = build(plan, args.out, supplied if args.command == "generate" else None)
        elif args.command == "report":
            print(json.dumps({"summary": summary(plan), "markdown": markdown(plan)}) if args.json else markdown(plan))
            return 0
        else:
            result = summary(plan) | {"checks": "offline passed"}
            if args.command == "plan":
                result["intent"] = resolution(plan, supplied)
                if plan["recipe"].get("demo") in ("loss-of-power-diversity", "provider-span-maintenance"):
                    scenario = _snapshot_scenario(plan["recipe"]["demo"]).create(plan)
                    result["scenario_preview"] = {key: scenario[key] for key in
                                                  ("scenario", "subject", "expected_findings", "limitations")}
            if args.command == "plan" and args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                with args.out.open("xb") as handle:
                    handle.write(canonical(plan) + b"\n")
                result["plan"] = str(args.out)
        if args.json:
            print(json.dumps(result, sort_keys=True))
        elif "scenario" in result:
            print(f"{result['scenario']}: {args.out / 'report.md'}; {result['checks']}")
            print("Baseline and changed Diode snapshots require separate fresh targets; live transition unverified"
                  if result["scenario"] == "loss-of-power-diversity" else
                  "Baseline and maintenance Diode snapshots are available; live status transition unverified")
        else:
            print(f"{result['name']}: {result['objects']:,} objects; offline checks passed")
            for kind in ("site", "rack", "device", "interface", "cable", "circuit", "virtual_machine"):
                print(f"  {kind}: {result['counts'].get(kind, 0):,}")
            if "intent" in result:
                intent = result["intent"]
                print("Defaulted inputs: " + ", ".join(key for key,value in intent["resolved"].items() if value["source"] == "default"))
                for item in intent["workloads"]:
                    fields = item["fields"]
                    print(f"  {item['key']}: {fields['groups']['value']} groups × {fields['replicas']['value']} replicas = {item['vms_per_site']} VMs/site; {fields['failure_domain']['value']} separation")
                for item in intent.get("schools", []):
                    d = item["demand"]
                    print(f"  {item['key']}: {d['enrollment']} students, {d['staff']} staff; {d['wired_student_seats']} wired student seats, {d['wireless_students']} planned wireless students, {d['aps']} APs")
                print("Use --json for resolved values, input provenance and all synthetic planning assumptions.")
            if "scenario_preview" in result:
                print(f"Demo: {result['scenario_preview']['scenario']}; subject {result['scenario_preview']['subject']}")
                print("The plan is the healthy baseline. Generate builds both snapshots and checks the exact expected findings.")
            if "output" in result:
                print(f"Build: {result['output']} ({result['diode_files']} Diode files)")
                target = result['source_checked_target']['netbox']
                print(f"Source-checked NetBox target: {target}; live ingestion not verified")
                if result["known_incompatible_netbox"]:
                    print("Incompatible NetBox targets: " + ", ".join(result["known_incompatible_netbox"]))
            print(f"SHA256: {result['sha256']}")
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
