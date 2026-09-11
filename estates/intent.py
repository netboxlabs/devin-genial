"""Inspectable resolved inputs; supplied values are not verified customer facts."""

import json
from pathlib import Path


def compatibility_guidance(plan):
    """Present the pinned export policy without rewriting frozen assumptions."""
    audit = json.loads((Path(__file__).resolve().parent.parent / "catalog/type-coverage.json").read_text())
    versions = audit["versions"]
    mapping = any(obj["kind"] == "front_port" and "rear_port" in obj.get("refs", {}) for obj in plan["objects"])
    expanded = tuple(map(int, plan["generator_version"].split("."))) >= (0, 7, 0)
    target = "4.4.10" if mapping and not expanded else versions["netbox"]
    assumptions = sorted({text for contract in plan["contracts"] for text in contract.get("assumptions", [])})
    historical = [text for text in assumptions if expanded and
                  text.startswith("Passive front/rear patch mappings use the legacy Diode fields;")]
    notes = ["Source checks and SDK validation do not establish live NetBox acceptance; live qualification receipts are separate evidence."]
    if mapping:
        if expanded:
            notes.append("NetBox 4.4.10 evidence applies only to legacy front/rear mappings. The complete generated package has no NetBox 4.4.10 compatibility claim.")
        notes.append(f"Unpatched Diode SDK {versions['sdk']} / plugin {versions['plugin']} cannot restore these legacy mappings on NetBox 4.5.0+, including 4.7.0.")
        if expanded:
            notes.append(f"Panel qualification on NetBox {target} requires the explicit opt-in local front-port bridge. This local qualification exception does not establish official Cloud or Enterprise compatibility.")
    return {"compatibility": {
                "source_checked_target": {"netbox": target, "diode_sdk": versions["sdk"],
                                          "diode_netbox_plugin": versions["plugin"], "live_verified": False},
                "local_compatibility_required": ["front_port_mapping"] if mapping and expanded else [],
                "notes": notes,
                "sources": audit["sources"],
            },
            "assumptions": [text for text in assumptions if text not in historical],
            "historical_assumptions": historical}


def resolution(plan, supplied=None):
    recipe = plan["recipe"]
    fields = {}
    for key, value in recipe.items():
        source = "unknown (frozen plan)" if supplied is None else "supplied" if key in supplied else "default"
        fields[key] = {"value": value, "source": source}
    workloads = []
    given = {item["key"]: item for item in supplied.get("workloads", [])} if supplied is not None else {}
    for item in recipe.get("workloads", []):
        workloads.append({"key": item["key"], "vms_per_site": item["groups"]*item["replicas"],
                          "fields": {key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                              "supplied" if key in given.get(item["key"], {}) else "default"} for key,value in item.items()}})
    schools = []
    given_schools = {item["key"]: item for item in supplied.get("schools", [])} if supplied is not None else {}
    contracts = {item["site"]: item for item in plan["contracts"]}
    for item in recipe.get("schools", []):
        schools.append({"key": item["key"],
            "fields": {key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                       "supplied" if key in given_schools.get(item["key"], {}) else "default"} for key,value in item.items()},
            "demand": contracts[f"site/school-{item['key']}"]["demand"]})
    healthcare = []
    for family, kind in (("hospitals", "hospital"), ("clinics", "clinic")):
        given_facilities = {item["key"]: item for item in supplied.get(family, [])} if supplied is not None else {}
        for item in recipe.get(family, []):
            given = given_facilities.get(item["key"], {})
            wards = []
            given_wards = {ward["key"]: ward for ward in given.get("wards", [])}
            for ward in item.get("wards", []):
                wards.append({"key": ward["key"], "fields": {
                    key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                          "supplied" if key in given_wards.get(ward["key"], {}) else "default"}
                    for key, value in ward.items()}})
            healthcare.append({"key": item["key"], "kind": kind,
                "fields": {key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                           "supplied" if key in given else "default"} for key, value in item.items() if key != "wards"},
                "wards": wards, "demand": contracts[f"site/{kind}-{item['key']}"]["demand"]})
    provider = {}
    if recipe["profile"] == "provider-backbone":
        for family in ("pops", "customers"):
            given_items = {item["key"]: item for item in supplied.get(family, [])} if supplied is not None else {}
            provider[family] = []
            for item in recipe[family]:
                given = given_items.get(item["key"], {})
                row = {"key": item["key"], "fields": {
                    key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                          "supplied" if key in given else "default"}
                    for key, value in item.items() if key != "sites"}}
                if family == "customers":
                    given_sites = {entry["pop"]: entry for entry in given.get("sites", [])}
                    row["sites"] = [{"pop": entry["pop"], "fields": {
                        key: {"value": value, "source": "unknown (frozen plan)" if supplied is None else
                              "supplied" if key in given_sites.get(entry["pop"], {}) else "default"}
                        for key, value in entry.items()}} for entry in item["sites"]]
                provider[family].append(row)
    return {"profile": recipe["profile"], "demo": recipe.get("demo", "baseline"), "resolved": fields,
            "workloads": workloads,
            **({"demo_scope": "Plan is the healthy baseline; generate/build produces a checked baseline and deliberate-defect scenario, for separate fresh targets."}
               if recipe.get("demo") == "loss-of-power-diversity" else {}),
            **({"demo_scope": "Plan is the healthy baseline; generate/build produces a checked span-maintenance snapshot with actual alternate routes and an assessment of remaining further-failure protection. Live status transition is unverified."}
               if recipe.get("demo") == "provider-span-maintenance" else {}),
            **({"schools": schools} if schools else {}),
            **({"healthcare_facilities": healthcare} if healthcare else {}),
            **({"provider": provider} if provider else {}),
            **compatibility_guidance(plan),
            "checks": {"offline_graph": "passed", "live_target": "not checked"},
            "provenance_note": "Supplied means present in the input recipe; synthetic planning assumptions are not verified customer facts."}
