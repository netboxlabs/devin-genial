"""Target-aware TurboBulk delivery for a frozen Genial graph.

This compiler deliberately consumes ``plan.json`` rather than generator internals.
TurboBulk receives database-shaped rows; bounded REST updates close relationships
which are cyclic or absent from the installed TurboBulk schema.
"""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import http.client
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
from lab.verify import CONTENT_TYPES as API_CONTENT_TYPES, ENDPOINTS, fetch_inventory, verify_plan


class LoadError(RuntimeError):
    """The target cannot safely or completely receive the artifact."""


class WriteRejected(LoadError):
    """The target returned a definite client-error response to a write."""


class JobTimeout(LoadError):
    def __init__(self, job_id, job, timeout):
        self.job_id = job_id
        self.job = job
        super().__init__(
            f"TurboBulk job {job_id} did not finish within {timeout}s; receipt preserved. "
            "Inspect the recorded job once and resume only after it progresses or reaches a terminal state. "
            "If it remains stuck, have the job or branch cleaned up, or use a new branch and receipt."
        )


class WorkerDied(JobTimeout):
    """RQ confirms the job's worker died; the row can never finalize on its own."""

    def __init__(self, job_id, job, rq_status, elapsed):
        self.job_id = job_id
        self.job = job
        self.rq_status = rq_status
        LoadError.__init__(
            self,
            f"TurboBulk job {job_id} was abandoned by a dead worker after {elapsed:.0f}s: "
            f"RQ reports {rq_status!r} while the job row is still nonterminal, and only the "
            "worker process that died could have finalized it. Receipt preserved. Rows may "
            "have committed before the kill; never resubmit this job. Use a new branch and "
            "receipt, or resume after the row reaches a terminal state (for example once the "
            "orphaned-job reaper marks it errored)."
        )


RECEIPT_VERSION = 4
COMPILER_VERSION = "v02-turbobulk-13"
DEFAULT_JOB_ROWS = 2_000
# TurboBulk's JSONL reader fixes the column set from the first chunk (10,000
# rows), so a sparse payload spanning chunks can silently drop columns.
MAX_JOB_ROWS = 10_000
REST_PATCH_ROWS = 100
READ_ATTEMPTS = 4
DEVICE_COMPONENT_KINDS = {
    "console_port", "console_server_port", "interface", "module_bay", "power_outlet", "power_port",
}
# Models NetBox registers no search indexer for (verified against the pinned
# 4.7.1 source via netbox.search.get_indexer): a rebuild_search_index hook on
# them errors instead of no-opping.
SEARCH_FINALIZER_EXCLUDED_KINDS = {
    "contact_assignment", "owner", "owner_group",
    "circuit_group_assignment", "custom_field_choice_set", "custom_link",
    "fhrp_group_assignment", "l2vpn_termination", "tunnel_termination",
}

DELIVERY_POLICIES = ("reviewable", "disposable-baseline")
POST_HOOKS = {
    "fix_denormalized": True,
    "rebuild_search_index": True,
    "fix_counters": True,
    "fix_cable_links": True,
    "rebuild_cable_paths": True,
}


def delivery_contract(policy):
    if policy not in DELIVERY_POLICIES:
        raise LoadError(f"unsupported delivery policy {policy!r}")
    reviewable = policy == "reviewable"
    return {
        "policy": policy,
        "warning": (None if reviewable else
                    "This branch cannot be reviewed, merged, or reverted; delete it after use."),
        "branch_capabilities": {
            "reviewable": reviewable,
            "mergeable": reviewable,
            "revertible_after_merge": reviewable,
        },
        "request_settings": {
            "validation_mode": "full",
            "create_changelogs": reviewable,
            "apply_save_hooks": False,
            "dispatch_events": False,
            "post_hooks": dict(POST_HOOKS),
        },
    }


def _batch_request_settings(base):
    """Keep every data-bearing request bounded to its rows, not global hooks."""
    return {**base, "post_hooks": {name: False for name in POST_HOOKS}}


def _finalizer_request_settings(base, hook):
    """Return a zero-row request which runs exactly one global maintenance hook."""
    if hook not in POST_HOOKS:
        raise LoadError(f"unsupported TurboBulk finalizer hook {hook!r}")
    return {**base, "create_changelogs": False,
            "post_hooks": {name: name == hook for name in POST_HOOKS}}


def _finalizer_plan(objects):
    """Order global maintenance after all canonical rows and REST completion."""
    kinds = {obj["kind"] for obj in objects.values()}
    models = sorted({SPECS[obj["kind"]][0] for obj in objects.values()
                     if obj["kind"] not in REST_CREATE_KINDS})
    result = []
    if kinds & DEVICE_COMPONENT_KINDS:
        result.extend([
            ("finalize:dcim.device:fix_denormalized", "dcim.device", "fix_denormalized"),
            ("finalize:dcim.device:fix_counters", "dcim.device", "fix_counters"),
        ])
    if "vm_interface" in kinds:
        result.append(("finalize:virtualization.virtualmachine:fix_counters",
                       "virtualization.virtualmachine", "fix_counters"))
    if "cable" in kinds:
        result.extend([
            ("finalize:dcim.cabletermination:fix_cable_links",
             "dcim.cabletermination", "fix_cable_links"),
            ("finalize:dcim.cabletermination:rebuild_cable_paths",
             "dcim.cabletermination", "rebuild_cable_paths"),
        ])
    searchable = sorted({SPECS[obj["kind"]][0] for obj in objects.values()
                         if obj["kind"] not in
                         REST_CREATE_KINDS | SEARCH_FINALIZER_EXCLUDED_KINDS})
    result.extend((f"finalize:{model}:rebuild_search_index", model,
                   "rebuild_search_index") for model in searchable)
    return result


def rest_mutation_requirements(objects):
    """Return canonical intent that cannot be completed by TurboBulk alone."""
    creates = sorted({obj["kind"] for obj in objects.values()} & REST_CREATE_KINDS)
    patches = sorted({(obj["kind"], field) for obj in objects.values()
                      for field in set(obj["refs"]) & DEFERRED})
    return {"create_kinds": creates,
            "patch_fields": [f"{kind}.{field}" for kind, field in patches]}


def disposable_rest_blocker(objects):
    required = rest_mutation_requirements(objects)
    parts = []
    if required["create_kinds"]:
        parts.append("REST create: " + ", ".join(required["create_kinds"]))
    if required["patch_fields"]:
        parts.append("REST completion PATCH: " + ", ".join(required["patch_fields"]))
    if not parts:
        return None
    return ("disposable-baseline requires a TurboBulk-only artifact because REST writes would create "
            "partial branch history; " + "; ".join(parts))


SPECS = {
    "aggregate": ("ipam.aggregate", "/api/ipam/aggregates/"),
    "console_port": ("dcim.consoleport", "/api/dcim/console-ports/"),
    "console_server_port": ("dcim.consoleserverport", "/api/dcim/console-server-ports/"),
    "contact": ("tenancy.contact", "/api/tenancy/contacts/"),
    "contact_assignment": ("tenancy.contactassignment", "/api/tenancy/contact-assignments/"),
    "contact_group": ("tenancy.contactgroup", "/api/tenancy/contact-groups/"),
    "contact_role": ("tenancy.contactrole", "/api/tenancy/contact-roles/"),
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
    "journal_entry": ("extras.journalentry", "/api/extras/journal-entries/"),
    "mac_address": ("dcim.macaddress", "/api/dcim/mac-addresses/"),
    "module": ("dcim.module", "/api/dcim/modules/"),
    "module_bay": ("dcim.modulebay", "/api/dcim/module-bays/"),
    "module_bay_type": (None, "/api/dcim/module-bay-types/"),
    "module_type": ("dcim.moduletype", "/api/dcim/module-types/"),
    "module_type_profile": ("dcim.moduletypeprofile", "/api/dcim/module-type-profiles/"),
    "owner": ("users.owner", "/api/users/owners/"),
    "owner_group": ("users.ownergroup", "/api/users/owner-groups/"),
    "platform": ("dcim.platform", "/api/dcim/platforms/"),
    "power_feed": ("dcim.powerfeed", "/api/dcim/power-feeds/"),
    "virtual_machine": ("virtualization.virtualmachine", "/api/virtualization/virtual-machines/"),
    "power_outlet": ("dcim.poweroutlet", "/api/dcim/power-outlets/"),
    "provider_account": ("circuits.provideraccount", "/api/circuits/provider-accounts/"),
    "rack_role": ("dcim.rackrole", "/api/dcim/rack-roles/"),
    "region": ("dcim.region", "/api/dcim/regions/"),
    "rir": ("ipam.rir", "/api/ipam/rirs/"),
    "service": ("ipam.service", "/api/ipam/services/"),
    "site_group": ("dcim.sitegroup", "/api/dcim/site-groups/"),
    "virtual_disk": ("virtualization.virtualdisk", "/api/virtualization/virtual-disks/"),
    "vlan_group": ("ipam.vlangroup", "/api/ipam/vlan-groups/"),
    "vm_interface": ("virtualization.vminterface", "/api/virtualization/interfaces/"),
    "cable": ("dcim.cable", "/api/dcim/cables/"),
    "asn": ("ipam.asn", "/api/ipam/asns/"),
    "asn_range": ("ipam.asnrange", "/api/ipam/asn-ranges/"),
    "route_target": ("ipam.routetarget", "/api/ipam/route-targets/"),
    "virtual_circuit": ("circuits.virtualcircuit", "/api/circuits/virtual-circuits/"),
    "virtual_circuit_termination": ("circuits.virtualcircuittermination",
                                    "/api/circuits/virtual-circuit-terminations/"),
    "virtual_circuit_type": ("circuits.virtualcircuittype", "/api/circuits/virtual-circuit-types/"),
    "cable_bundle": ("dcim.cablebundle", "/api/dcim/cable-bundles/"),
    "circuit_group": ("circuits.circuitgroup", "/api/circuits/circuit-groups/"),
    "circuit_group_assignment": ("circuits.circuitgroupassignment", "/api/circuits/circuit-group-assignments/"),
    "cluster_group": ("virtualization.clustergroup", "/api/virtualization/cluster-groups/"),
    "cooling_feed": ("dcim.coolingfeed", "/api/dcim/cooling-feeds/"),
    "cooling_intake": ("dcim.coolingintake", "/api/dcim/cooling-intakes/"),
    "cooling_outflow": ("dcim.coolingoutflow", "/api/dcim/cooling-outflows/"),
    "cooling_source": ("dcim.coolingsource", "/api/dcim/cooling-sources/"),
    "custom_field": ("extras.customfield", "/api/extras/custom-fields/"),
    "custom_field_choice_set": ("extras.customfieldchoiceset", "/api/extras/custom-field-choice-sets/"),
    "custom_link": ("extras.customlink", "/api/extras/custom-links/"),
    "device_bay": ("dcim.devicebay", "/api/dcim/device-bays/"),
    "fhrp_group": ("ipam.fhrpgroup", "/api/ipam/fhrp-groups/"),
    "fhrp_group_assignment": ("ipam.fhrpgroupassignment", "/api/ipam/fhrp-group-assignments/"),
    "ike_policy": ("vpn.ikepolicy", "/api/vpn/ike-policies/"),
    "ike_proposal": ("vpn.ikeproposal", "/api/vpn/ike-proposals/"),
    "inventory_item": ("dcim.inventoryitem", "/api/dcim/inventory-items/"),
    "inventory_item_role": ("dcim.inventoryitemrole", "/api/dcim/inventory-item-roles/"),
    "ip_range": ("ipam.iprange", "/api/ipam/ip-ranges/"),
    "ip_sec_policy": ("vpn.ipsecpolicy", "/api/vpn/ipsec-policies/"),
    "ip_sec_profile": ("vpn.ipsecprofile", "/api/vpn/ipsec-profiles/"),
    "ip_sec_proposal": ("vpn.ipsecproposal", "/api/vpn/ipsec-proposals/"),
    "l2vpn": ("vpn.l2vpn", "/api/vpn/l2vpns/"),
    "l2vpn_termination": ("vpn.l2vpntermination", "/api/vpn/l2vpn-terminations/"),
    "rack_group": ("dcim.rackgroup", "/api/dcim/rack-groups/"),
    "rack_type": ("dcim.racktype", "/api/dcim/rack-types/"),
    "role": ("ipam.role", "/api/ipam/roles/"),
    "tenant_group": ("tenancy.tenantgroup", "/api/tenancy/tenant-groups/"),
    "tunnel": ("vpn.tunnel", "/api/vpn/tunnels/"),
    "tunnel_group": ("vpn.tunnelgroup", "/api/vpn/tunnel-groups/"),
    "tunnel_termination": ("vpn.tunneltermination", "/api/vpn/tunnel-terminations/"),
    "virtual_chassis": ("dcim.virtualchassis", "/api/dcim/virtual-chassis/"),
    "virtual_device_context": ("dcim.virtualdevicecontext", "/api/dcim/virtual-device-contexts/"),
    "virtual_machine_type": ("virtualization.virtualmachinetype", "/api/virtualization/virtual-machine-types/"),
    "vlan_translation_policy": ("ipam.vlantranslationpolicy", "/api/ipam/vlan-translation-policies/"),
    "vlan_translation_rule": ("ipam.vlantranslationrule", "/api/ipam/vlan-translation-rules/"),
    "wireless_link": ("wireless.wirelesslink", "/api/wireless/wireless-links/"),
    "wireless_lan": ("wireless.wirelesslan", "/api/wireless/wireless-lans/"),
    "wireless_lan_group": ("wireless.wirelesslangroup", "/api/wireless/wireless-lan-groups/"),
}

CONTENT_TYPES = {
    "asn": ("ipam", "asn"),
    "asn_range": ("ipam", "asnrange"),
    "route_target": ("ipam", "routetarget"),
    "virtual_circuit": ("circuits", "virtualcircuit"),
    "virtual_circuit_termination": ("circuits", "virtualcircuittermination"),
    "virtual_circuit_type": ("circuits", "virtualcircuittype"),
    "circuit": ("circuits", "circuit"),
    "cluster": ("virtualization", "cluster"),
    "console_port": ("dcim", "consoleport"),
    "console_server_port": ("dcim", "consoleserverport"),
    "device": ("dcim", "device"),
    "site": ("dcim", "site"),
    "provider_network": ("circuits", "providernetwork"),
    "interface": ("dcim", "interface"),
    "vm_interface": ("virtualization", "vminterface"),
    "circuit_termination": ("circuits", "circuittermination"),
    "power_port": ("dcim", "powerport"),
    "power_outlet": ("dcim", "poweroutlet"),
    "power_feed": ("dcim", "powerfeed"),
    "virtual_machine": ("virtualization", "virtualmachine"),
    "fhrp_group": ("ipam", "fhrpgroup"),
    "vlan": ("ipam", "vlan"),
    "cooling_intake": ("dcim", "coolingintake"),
}

# TurboBulk 0.4.0 manufactures an integer NOT NULL default for any column whose
# name merely contains "count" (engine/merge.py _get_sql_default), so every
# circuits.provideraccount insert emits COALESCE("account", 0) and fails with a
# varchar/integer type error. Create those few rows through REST until upstream
# matches the default to the column type.
REST_CREATE_KINDS = {"module_bay_type", "provider_account",
                     # extras rows carry object_types M2Ms and choice lists that
                     # the raw bulk path cannot express; they are singletons.
                     "custom_field", "custom_field_choice_set", "custom_link"}
# Model-default values REST/Diode apply server-side but TurboBulk's raw path
# would otherwise manufacture as invalid '' (choice columns have no CHECK
# constraint, so the row inserts and only surfaces at the next full_clean —
# observed as a branching-merge failure on location/power_outlet status).
RENDER_DEFAULTS = {"location": {"status": "active"}, "power_outlet": {"status": "enabled"},
                   "rack": {"starting_unit": 1}}

ATTRIBUTE_RENAMES = {("module_type", "attributes"): "attribute_data"}

DIRECT_REFS = {
    "tenant": "tenant_id", "site": "site_id", "location": "location_id",
    "rack": "rack_id", "device_type": "device_type_id", "role": "role_id",
    "manufacturer": "manufacturer_id", "provider": "provider_id", "type": "type_id",
    "cluster": "cluster_id", "device": "device_id", "vrf": "vrf_id",
    "vlan": "vlan_id", "untagged_vlan": "untagged_vlan_id",
    "power_panel": "power_panel_id", "power_port": "power_port_id",
    "virtual_machine": "virtual_machine_id", "circuit": "circuit_id",
    "contact": "contact_id", "group": "group_id", "module": "module_id",
    "module_bay": "module_bay_id", "module_type": "module_type_id",
    "owner": "owner_id", "parent": "parent_id", "platform": "platform_id",
    "bundle": "bundle_id", "rack_type": "rack_type_id", "virtual_chassis": "virtual_chassis_id",
    "bridge": "bridge_id", "vlan_translation_policy": "vlan_translation_policy_id",
    "policy": "policy_id", "choice_set": "choice_set_id", "ipsec_profile": "ipsec_profile_id",
    "ike_policy": "ike_policy_id", "ipsec_policy": "ipsec_policy_id",
    "cooling_outflow": "cooling_outflow_id", "cooling_intake": "cooling_intake_id",
    "cooling_source": "cooling_source_id", "installed_device": "installed_device_id",
    "interface_a": "interface_a_id", "interface_b": "interface_b_id",
    "outside_ip": "outside_ip_id", "default_platform": "default_platform_id",
    "l2vpn": "l2vpn_id", "tunnel": "tunnel_id", "virtual_machine_type": "virtual_machine_type_id",
    "profile": "profile_id", "provider_account": "provider_account_id",
    "region": "region_id", "rir": "rir_id", "interface": "interface_id",
    "provider_network": "provider_network_id", "virtual_circuit": "virtual_circuit_id",
}

DEFERRED = {"primary_ip4", "primary_ip6", "oob_ip", "primary_mac_address",
            "tagged_vlans", "tags", "groups", "module_bay_types", "ipaddresses",
            "asns", "export_targets", "import_targets", "wireless_lans",
            "proposals", "master"}
SUPPORTED_REFS = {
    "aggregate": {"rir", "tenant"},
    "asn": {"rir", "tenant"},
    "asn_range": {"rir", "tenant"},
    "cable": {"a", "b", "bundle"},
    "circuit": {"owner", "provider", "provider_account", "tenant", "type"},
    "circuit_termination": {"circuit", "termination"},
    "circuit_type": set(),
    "cluster": {"group", "owner", "scope_site", "tenant", "type"},
    "cluster_type": set(),
    "console_port": {"device"},
    "console_server_port": {"device"},
    "contact": {"groups"},
    "contact_assignment": {"contact", "object", "role"},
    "contact_group": set(),
    "contact_role": set(),
    "device": {"cluster", "device_type", "location", "primary_ip4", "primary_ip6", "rack", "role", "site", "tags", "tenant", "virtual_chassis"},
    "device_role": set(),
    "device_type": {"manufacturer"},
    "interface": {"bridge", "device", "module", "parent", "primary_mac_address", "tagged_vlans",
                  "untagged_vlan", "vlan_translation_policy", "vrf", "wireless_lans"},
    "ip_address": {"assigned_object", "tenant", "vrf"},
    "journal_entry": {"assigned_object"},
    "location": {"parent", "site", "tenant"},
    "mac_address": {"assigned_object"},
    "manufacturer": set(),
    "module": {"device", "module_bay", "module_type"},
    "module_bay": {"device", "module_bay_types"},
    "module_bay_type": {"manufacturer"},
    "module_type": {"manufacturer", "module_bay_types", "profile"},
    "module_type_profile": set(),
    "owner": {"group"},
    "owner_group": set(),
    "platform": set(),
    "power_feed": {"power_panel", "rack"},
    "power_outlet": {"device", "power_port"},
    "power_panel": {"location", "site"},
    "power_port": {"device", "module"},
    "route_target": {"tenant"},
    "virtual_circuit": {"provider_account", "provider_network", "tenant", "type"},
    "virtual_circuit_termination": {"interface", "virtual_circuit"},
    "virtual_circuit_type": set(),
    "prefix": {"role", "scope_site", "tenant", "vlan", "vrf"},
    "provider": {"asns"},
    "provider_account": {"owner", "provider"},
    "provider_network": {"provider"},
    "rack": {"group", "location", "rack_type", "role", "site", "tenant"},
    "rack_role": set(),
    "region": {"parent"},
    "rir": set(),
    "service": {"ipaddresses", "virtual_machine"},
    "site": {"asns", "group", "owner", "region", "tags", "tenant"},
    "site_group": set(),
    "tag": set(),
    "tenant": {"group"},
    "virtual_disk": {"owner", "virtual_machine"},
    "virtual_machine": {"cluster", "device", "platform", "primary_ip4", "primary_ip6", "role", "tags", "tenant", "virtual_machine_type"},
    "vlan": {"group", "role", "site", "tenant"},
    "vlan_group": {"scope_site", "tenant"},
    "vm_interface": {"primary_mac_address", "untagged_vlan", "virtual_machine", "vrf"},
    "vrf": {"export_targets", "import_targets", "tenant"},
    "cable_bundle": {"owner"},
    "circuit_group": {"owner", "tenant"},
    "circuit_group_assignment": {"group", "member"},
    "cluster_group": {"owner"},
    "cooling_feed": {"cooling_source", "rack", "tenant"},
    "cooling_intake": {"cooling_outflow", "device"},
    "cooling_outflow": {"cooling_intake", "device"},
    "cooling_source": {"location", "site"},
    "custom_field": {"choice_set", "owner"},
    "custom_field_choice_set": {"owner"},
    "custom_link": {"owner"},
    "device_bay": {"device", "installed_device"},
    "fhrp_group": set(),
    "fhrp_group_assignment": {"group", "interface"},
    "ike_policy": {"proposals"},
    "ike_proposal": set(),
    "inventory_item": {"component", "device", "manufacturer", "parent", "role"},
    "inventory_item_role": set(),
    "ip_range": {"role", "tenant", "vrf"},
    "ip_sec_policy": {"proposals"},
    "ip_sec_profile": {"ike_policy", "ipsec_policy"},
    "ip_sec_proposal": set(),
    "l2vpn": {"export_targets", "import_targets", "tenant"},
    "l2vpn_termination": {"assigned_object", "l2vpn"},
    "rack_group": {"owner"},
    "rack_type": {"manufacturer", "owner"},
    "role": set(),
    "tenant_group": {"owner"},
    "tunnel": {"group", "ipsec_profile", "tenant"},
    "tunnel_group": set(),
    "tunnel_termination": {"outside_ip", "termination", "tunnel"},
    "virtual_chassis": {"master"},
    "virtual_device_context": {"device", "tenant"},
    "virtual_machine_type": {"default_platform", "owner"},
    "vlan_translation_policy": set(),
    "vlan_translation_rule": {"policy"},
    "wireless_link": {"interface_a", "interface_b", "tenant"},
    "wireless_lan": {"group", "scope_site", "tenant", "vlan"},
    "wireless_lan_group": {"parent"},
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


def _content_type_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("app_label") and value.get("model"):
            return f"{value['app_label']}.{value['model']}"
        return value.get("value")
    return None


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


def _read_json(opener, request, timeout):
    """Retry transient failures only for caller-authorized read requests.

    A 5xx on a GET is a transient server condition (observed as sporadic 500s
    on the small shared-container targets under concurrent readback) and
    retries like a dropped connection; any 4xx stays an immediate error.
    """
    for attempt in range(READ_ATTEMPTS):
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt + 1 == READ_ATTEMPTS:
                raise
            time.sleep(0.5 * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, json.JSONDecodeError, EOFError):
            if attempt + 1 == READ_ATTEMPTS:
                raise
            time.sleep(0.5 * 2 ** attempt)


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
            if method == "GET" and body is None:
                return _read_json(self.opener, request, 120)
            with self.opener.open(request, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode(errors="replace")
            error = f"{method} {path} returned HTTP {exc.code}: {detail}"
            if method != "GET" and 400 <= exc.code < 500:
                raise WriteRejected(error) from exc
            raise LoadError(error) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, json.JSONDecodeError, EOFError) as exc:
            detail = (f"after {READ_ATTEMPTS} read attempts" if method == "GET" and body is None
                      else "without retry because the request could write")
            raise LoadError(f"{method} {path} failed {detail}: {exc}") from exc

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
                _, page = _read_json(self.opener, request, 120)
            except urllib.error.HTTPError as exc:
                raise LoadError(f"GET {path} returned HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    http.client.HTTPException, json.JSONDecodeError, EOFError) as exc:
                raise LoadError(
                    f"GET {path} failed after {READ_ATTEMPTS} read attempts: {exc}"
                ) from exc
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


def _component_cache_ids(obj, objects, ids):
    """Materialize ComponentModel caches from the component's parent device."""
    device = objects[obj["refs"]["device"]]
    site_key = device["refs"].get("site")
    rack_key = device["refs"].get("rack")
    location_key = device["refs"].get("location")
    if location_key is None and rack_key is not None:
        location_key = objects[rack_key]["refs"].get("location")
    return {
        "_site_id": ids[site_key] if site_key is not None else None,
        "_location_id": ids[location_key] if location_key is not None else None,
        "_rack_id": ids[rack_key] if rack_key is not None else None,
    }


def _matches(obj, row, ids, objects=None):
    kind, attrs = obj["kind"], obj["attrs"]
    if kind in {"manufacturer", "tag", "circuit_type", "cluster_type", "provider", "tenant",
                "device_role", "site", "device_type", "contact_group", "contact_role",
                "platform", "rack_role", "rir", "site_group", "wireless_lan_group"}:
        return row.get("slug") == attrs["slug"]
    if kind in {"contact", "module_type_profile", "owner", "owner_group",
                "cable_bundle", "circuit_group", "cluster_group", "custom_field", "custom_field_choice_set", "custom_link", "ike_policy", "ike_proposal", "inventory_item_role", "ip_sec_policy", "ip_sec_profile", "ip_sec_proposal", "l2vpn", "rack_group", "role", "tenant_group", "tunnel", "tunnel_group", "virtual_chassis", "virtual_machine_type", "vlan_translation_policy"}:
        return row.get("name") == attrs["name"]
    if kind == "region":
        return (row.get("slug") == attrs["slug"]
                and ("parent" not in obj["refs"] or _nested_id(row.get("parent")) == _ref_id(obj, "parent", ids)))
    if kind == "aggregate":
        return row.get("prefix") == attrs["prefix"] and _nested_id(row.get("rir")) == _ref_id(obj, "rir", ids)
    if kind == "vrf":
        return (row.get("name") == attrs["name"]
                and ("tenant" not in obj["refs"] or _nested_id(row.get("tenant")) == _ref_id(obj, "tenant", ids))
                and (not attrs.get("rd") or row.get("rd") == attrs["rd"]))
    if kind == "provider_network":
        return row.get("name") == attrs["name"] and _nested_id(row.get("provider")) == _ref_id(obj, "provider", ids)
    if kind == "circuit":
        return row.get("cid") == attrs["cid"] and _nested_id(row.get("provider")) == _ref_id(obj, "provider", ids)
    if kind == "provider_account":
        return row.get("account") == attrs["account"] and _nested_id(row.get("provider")) == _ref_id(obj, "provider", ids)
    if kind in {"location", "power_panel"}:
        return (row.get("name") == attrs["name"]
                and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
                and (kind != "location" or "parent" not in obj["refs"]
                     or _nested_id(row.get("parent")) == _ref_id(obj, "parent", ids)))
    if kind == "rack":
        return (row.get("asset_tag") == attrs.get("asset_tag") if attrs.get("asset_tag") else
                row.get("name") == attrs["name"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
                and ("location" not in obj["refs"] or _nested_id(row.get("location")) == _ref_id(obj, "location", ids)))
    if kind == "vlan":
        return row.get("vid") == attrs["vid"] and _nested_id(row.get("site")) == _ref_id(obj, "site", ids)
    if kind == "cluster":
        return (row.get("name") == attrs["name"]
                and ("scope_site" not in obj["refs"] or
                     (row.get("scope_id") == _ref_id(obj, "scope_site", ids)
                      and _content_type_name(row.get("scope_type")) == API_CONTENT_TYPES["site"])))
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
    if kind in {"console_port", "console_server_port", "module_bay", "power_port", "power_outlet",
                "interface", "cooling_intake", "cooling_outflow", "device_bay", "virtual_device_context"}:
        return row.get("name") == attrs["name"] and _nested_id(row.get("device")) == _ref_id(obj, "device", ids)
    if kind == "module":
        return _nested_id(row.get("module_bay")) == _ref_id(obj, "module_bay", ids)
    if kind in {"module_bay_type", "module_type"}:
        name = "model" if kind == "module_type" else "name"
        return row.get(name) == attrs[name] and _nested_id(row.get("manufacturer")) == _ref_id(obj, "manufacturer", ids)
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
        return (row.get("name") == attrs["name"] and parent == _ref_id(obj, "virtual_machine", ids)
                and _content_type_name(row.get("parent_object_type")) == API_CONTENT_TYPES["virtual_machine"])
    if kind == "vm_interface":
        return row.get("name") == attrs["name"] and _nested_id(row.get("virtual_machine")) == _ref_id(obj, "virtual_machine", ids)
    if kind == "virtual_disk":
        return row.get("name") == attrs["name"] and _nested_id(row.get("virtual_machine")) == _ref_id(obj, "virtual_machine", ids)
    if kind == "vlan_group":
        return (row.get("name") == attrs["name"]
                and row.get("scope_id") == _ref_id(obj, "scope_site", ids)
                and _content_type_name(row.get("scope_type")) == API_CONTENT_TYPES["site"])
    if kind == "contact_assignment":
        target_kind = objects[obj["refs"]["object"]]["kind"] if objects else None
        target = _nested_id(row.get("object", row.get("object_id")))
        return (target == _ref_id(obj, "object", ids)
                and _content_type_name(row.get("object_type")) == API_CONTENT_TYPES.get(target_kind)
                and _nested_id(row.get("contact")) == _ref_id(obj, "contact", ids)
                and _nested_id(row.get("role")) == _ref_id(obj, "role", ids))
    if kind in {"journal_entry", "mac_address"}:
        target_kind = objects[obj["refs"]["assigned_object"]]["kind"] if objects else None
        field = "comments" if kind == "journal_entry" else "mac_address"
        return (row.get(field) == attrs[field]
                and _nested_id(row.get("assigned_object", row.get("assigned_object_id")))
                == _ref_id(obj, "assigned_object", ids)
                and _content_type_name(row.get("assigned_object_type")) == API_CONTENT_TYPES.get(target_kind))
    if kind == "cable":
        return row.get("label") == attrs["label"]
    if kind == "asn":
        return row.get("asn") == attrs["asn"]
    if kind in {"asn_range", "route_target", "virtual_circuit_type"}:
        return row.get("name") == attrs["name"]
    if kind == "virtual_circuit":
        return (row.get("cid") == attrs["cid"]
                and _nested_id(row.get("provider_network")) == _ref_id(obj, "provider_network", ids))
    if kind == "virtual_circuit_termination":
        return (_nested_id(row.get("virtual_circuit")) == _ref_id(obj, "virtual_circuit", ids)
                and _nested_id(row.get("interface")) == _ref_id(obj, "interface", ids))
    if kind == "wireless_lan":
        return (row.get("ssid") == attrs["ssid"]
                and _nested_id(row.get("group")) == _ref_id(obj, "group", ids)
                and ("vlan" not in obj["refs"]
                     or _nested_id(row.get("vlan")) == _ref_id(obj, "vlan", ids)))
    if kind in {"cooling_feed", "cooling_source"}:
        anchor = "cooling_source" if kind == "cooling_feed" else "site"
        return (row.get("name") == attrs["name"]
                and _nested_id(row.get(anchor)) == _ref_id(obj, anchor, ids))
    if kind == "inventory_item":
        return (row.get("name") == attrs["name"]
                and _nested_id(row.get("device")) == _ref_id(obj, "device", ids)
                and ("parent" not in obj["refs"]
                     or _nested_id(row.get("parent")) == _ref_id(obj, "parent", ids)))
    if kind == "ip_range":
        return (row.get("start_address") == attrs["start_address"]
                and row.get("end_address") == attrs["end_address"]
                and ("vrf" not in obj["refs"] or _nested_id(row.get("vrf")) == _ref_id(obj, "vrf", ids)))
    if kind == "rack_type":
        return (row.get("model") == attrs["model"]
                and _nested_id(row.get("manufacturer")) == _ref_id(obj, "manufacturer", ids))
    if kind == "fhrp_group":
        return row.get("group_id") == attrs["group_id"]
    if kind == "vlan_translation_rule":
        return (row.get("local_vid") == attrs["local_vid"]
                and _nested_id(row.get("policy")) == _ref_id(obj, "policy", ids))
    if kind == "wireless_link":
        return (_nested_id(row.get("interface_a")) == _ref_id(obj, "interface_a", ids)
                and _nested_id(row.get("interface_b")) == _ref_id(obj, "interface_b", ids))
    if kind == "circuit_group_assignment":
        member = objects[obj["refs"]["member"]]
        return (_nested_id(row.get("group")) == _ref_id(obj, "group", ids)
                and _nested_id(row.get("member", row.get("member_id"))) == ids[member["key"]]
                and _content_type_name(row.get("member_type")) == API_CONTENT_TYPES[member["kind"]])
    if kind == "fhrp_group_assignment":
        target = objects[obj["refs"]["interface"]]
        return (_nested_id(row.get("group")) == _ref_id(obj, "group", ids)
                and _nested_id(row.get("interface", row.get("interface_id"))) == ids[target["key"]]
                and _content_type_name(row.get("interface_type")) == API_CONTENT_TYPES[target["kind"]])
    if kind == "tunnel_termination":
        target = objects[obj["refs"]["termination"]]
        return (_nested_id(row.get("tunnel")) == _ref_id(obj, "tunnel", ids)
                and _nested_id(row.get("termination", row.get("termination_id"))) == ids[target["key"]]
                and _content_type_name(row.get("termination_type")) == API_CONTENT_TYPES[target["kind"]])
    if kind == "l2vpn_termination":
        target = objects[obj["refs"]["assigned_object"]]
        return (_nested_id(row.get("l2vpn")) == _ref_id(obj, "l2vpn", ids)
                and _nested_id(row.get("assigned_object", row.get("assigned_object_id"))) == ids[target["key"]]
                and _content_type_name(row.get("assigned_object_type")) == API_CONTENT_TYPES[target["kind"]])
    raise LoadError(f"no target identity matcher for {kind}")


def _candidate_bucket_key(obj, ids, objects=None):
    """Return a coarse natural identity whose bucket is verified by ``_matches``."""
    kind, attrs, refs = obj["kind"], obj["attrs"], obj["refs"]
    if kind in {"manufacturer", "tag", "circuit_type", "cluster_type", "provider", "tenant",
                "device_role", "site", "device_type", "contact_group", "contact_role",
                "platform", "rack_role", "rir", "site_group", "region", "wireless_lan_group"}:
        return "slug", attrs["slug"]
    if kind in {"contact", "module_type_profile", "owner", "owner_group", "vrf", "cluster",
                "virtual_machine",
                "cable_bundle", "circuit_group", "cluster_group", "custom_field", "custom_field_choice_set", "custom_link", "ike_policy", "ike_proposal", "inventory_item_role", "ip_sec_policy", "ip_sec_profile", "ip_sec_proposal", "l2vpn", "rack_group", "role", "tenant_group", "tunnel", "tunnel_group", "virtual_chassis", "virtual_machine_type", "vlan_translation_policy"}:
        return "name", attrs["name"]
    if kind == "aggregate":
        return "prefix-rir", attrs["prefix"], _ref_id(obj, "rir", ids)
    if kind == "provider_network":
        return "name-provider", attrs["name"], _ref_id(obj, "provider", ids)
    if kind == "circuit":
        return "cid-provider", attrs["cid"], _ref_id(obj, "provider", ids)
    if kind == "provider_account":
        return "account-provider", attrs["account"], _ref_id(obj, "provider", ids)
    if kind in {"location", "power_panel"}:
        return "name-site", attrs["name"], _ref_id(obj, "site", ids)
    if kind in {"rack", "device"}:
        return (("asset-tag", attrs["asset_tag"]) if attrs.get("asset_tag") else
                ("name-site", attrs["name"], _ref_id(obj, "site", ids)))
    if kind == "vlan":
        return "vid-site", attrs["vid"], _ref_id(obj, "site", ids)
    if kind == "prefix":
        return "prefix-vrf", attrs["prefix"], _ref_id(obj, "vrf", ids)
    if kind == "circuit_termination":
        return "side-circuit", attrs["term_side"], _ref_id(obj, "circuit", ids)
    if kind in {"console_port", "console_server_port", "module_bay", "power_port", "power_outlet",
                "interface", "cooling_intake", "cooling_outflow", "device_bay", "virtual_device_context"}:
        return "name-device", attrs["name"], _ref_id(obj, "device", ids)
    if kind == "module":
        return "module-bay", _ref_id(obj, "module_bay", ids)
    if kind in {"module_bay_type", "module_type"}:
        name = "model" if kind == "module_type" else "name"
        return "name-manufacturer", attrs[name], _ref_id(obj, "manufacturer", ids)
    if kind == "ip_address":
        return "address-vrf", attrs["address"], _ref_id(obj, "vrf", ids)
    if kind == "power_feed":
        return "name-panel", attrs["name"], _ref_id(obj, "power_panel", ids)
    if kind in {"vm_interface", "virtual_disk"}:
        return "name-vm", attrs["name"], _ref_id(obj, "virtual_machine", ids)
    if kind == "service":
        return ("name-parent", attrs["name"], _ref_id(obj, "virtual_machine", ids),
                API_CONTENT_TYPES["virtual_machine"])
    if kind == "vlan_group":
        return "name-scope", attrs["name"], _ref_id(obj, "scope_site", ids), API_CONTENT_TYPES["site"]
    if kind == "contact_assignment":
        target_kind = objects[refs["object"]]["kind"]
        return ("assignment", _ref_id(obj, "object", ids), API_CONTENT_TYPES[target_kind],
                _ref_id(obj, "contact", ids), _ref_id(obj, "role", ids))
    if kind in {"journal_entry", "mac_address"}:
        target_kind = objects[refs["assigned_object"]]["kind"]
        field = "comments" if kind == "journal_entry" else "mac_address"
        return ("assigned", attrs[field], _ref_id(obj, "assigned_object", ids),
                API_CONTENT_TYPES[target_kind])
    if kind == "cable":
        return "label", attrs["label"]
    if kind == "asn":
        return "asn", attrs["asn"]
    if kind in {"asn_range", "route_target", "virtual_circuit_type"}:
        return "name", attrs["name"]
    if kind == "virtual_circuit":
        return "cid-provider-network", attrs["cid"], _ref_id(obj, "provider_network", ids)
    if kind == "virtual_circuit_termination":
        return ("vc-interface", _ref_id(obj, "virtual_circuit", ids),
                _ref_id(obj, "interface", ids))
    if kind == "wireless_lan":
        return "ssid-group", attrs["ssid"], _ref_id(obj, "group", ids)
    if kind in {"cooling_feed", "cooling_source"}:
        anchor = "cooling_source" if kind == "cooling_feed" else "site"
        return "name-anchor", attrs["name"], _ref_id(obj, anchor, ids)
    if kind == "inventory_item":
        return "name-device", attrs["name"], _ref_id(obj, "device", ids)
    if kind == "ip_range":
        return "range", attrs["start_address"], attrs["end_address"]
    if kind == "rack_type":
        return "name-manufacturer", attrs["model"], _ref_id(obj, "manufacturer", ids)
    if kind == "fhrp_group":
        return "group-id", attrs["group_id"]
    if kind == "vlan_translation_rule":
        return "policy-vid", _ref_id(obj, "policy", ids), attrs["local_vid"]
    if kind == "wireless_link":
        return "link", _ref_id(obj, "interface_a", ids), _ref_id(obj, "interface_b", ids)
    if kind == "circuit_group_assignment":
        member = objects[refs["member"]]
        return ("assignment", _ref_id(obj, "group", ids), ids[member["key"]],
                API_CONTENT_TYPES[member["kind"]])
    if kind == "fhrp_group_assignment":
        target = objects[refs["interface"]]
        return ("assignment", _ref_id(obj, "group", ids), ids[target["key"]],
                API_CONTENT_TYPES[target["kind"]])
    if kind == "tunnel_termination":
        target = objects[refs["termination"]]
        return ("assignment", _ref_id(obj, "tunnel", ids), ids[target["key"]],
                API_CONTENT_TYPES[target["kind"]])
    if kind == "l2vpn_termination":
        target = objects[refs["assigned_object"]]
        return ("assignment", _ref_id(obj, "l2vpn", ids), ids[target["key"]],
                API_CONTENT_TYPES[target["kind"]])
    raise LoadError(f"no target identity index for {kind}")


def _row_bucket_keys(kind, row):
    nested = _nested_id
    if kind in {"manufacturer", "tag", "circuit_type", "cluster_type", "provider", "tenant",
                "device_role", "site", "device_type", "contact_group", "contact_role",
                "platform", "rack_role", "rir", "site_group", "region", "wireless_lan_group"}:
        return [("slug", row.get("slug"))]
    if kind in {"contact", "module_type_profile", "owner", "owner_group", "vrf", "cluster",
                "virtual_machine",
                "cable_bundle", "circuit_group", "cluster_group", "custom_field", "custom_field_choice_set", "custom_link", "ike_policy", "ike_proposal", "inventory_item_role", "ip_sec_policy", "ip_sec_profile", "ip_sec_proposal", "l2vpn", "rack_group", "role", "tenant_group", "tunnel", "tunnel_group", "virtual_chassis", "virtual_machine_type", "vlan_translation_policy"}:
        return [("name", row.get("name"))]
    if kind == "aggregate":
        return [("prefix-rir", row.get("prefix"), nested(row.get("rir")))]
    if kind == "provider_network":
        return [("name-provider", row.get("name"), nested(row.get("provider")))]
    if kind == "circuit":
        return [("cid-provider", row.get("cid"), nested(row.get("provider")))]
    if kind == "provider_account":
        return [("account-provider", row.get("account"), nested(row.get("provider")))]
    if kind in {"location", "power_panel"}:
        return [("name-site", row.get("name"), nested(row.get("site")))]
    if kind in {"rack", "device"}:
        keys = [("name-site", row.get("name"), nested(row.get("site")))]
        if row.get("asset_tag"):
            keys.append(("asset-tag", row["asset_tag"]))
        return keys
    if kind == "vlan":
        return [("vid-site", row.get("vid"), nested(row.get("site")))]
    if kind == "prefix":
        return [("prefix-vrf", row.get("prefix"), nested(row.get("vrf")))]
    if kind == "circuit_termination":
        side = row.get("term_side")
        side = side.get("value") if isinstance(side, dict) else side
        return [("side-circuit", side, nested(row.get("circuit")))]
    if kind in {"console_port", "console_server_port", "module_bay", "power_port", "power_outlet",
                "interface", "cooling_intake", "cooling_outflow", "device_bay", "virtual_device_context"}:
        return [("name-device", row.get("name"), nested(row.get("device")))]
    if kind == "module":
        return [("module-bay", nested(row.get("module_bay")))]
    if kind in {"module_bay_type", "module_type"}:
        name = "model" if kind == "module_type" else "name"
        return [("name-manufacturer", row.get(name), nested(row.get("manufacturer")))]
    if kind == "ip_address":
        return [("address-vrf", row.get("address"), nested(row.get("vrf")))]
    if kind == "power_feed":
        return [("name-panel", row.get("name"), nested(row.get("power_panel")))]
    if kind in {"vm_interface", "virtual_disk"}:
        return [("name-vm", row.get("name"), nested(row.get("virtual_machine")))]
    if kind == "service":
        parent = row.get("parent_object_id", nested(row.get("parent")))
        return [("name-parent", row.get("name"), parent,
                 _content_type_name(row.get("parent_object_type")))]
    if kind == "vlan_group":
        return [("name-scope", row.get("name"), row.get("scope_id"),
                 _content_type_name(row.get("scope_type")))]
    if kind == "contact_assignment":
        target = nested(row.get("object", row.get("object_id")))
        return [("assignment", target, _content_type_name(row.get("object_type")),
                 nested(row.get("contact")), nested(row.get("role")))]
    if kind in {"journal_entry", "mac_address"}:
        field = "comments" if kind == "journal_entry" else "mac_address"
        target = nested(row.get("assigned_object", row.get("assigned_object_id")))
        return [("assigned", row.get(field), target,
                 _content_type_name(row.get("assigned_object_type")))]
    if kind == "cable":
        return [("label", row.get("label"))]
    if kind == "asn":
        return [("asn", row.get("asn"))]
    if kind in {"asn_range", "route_target", "virtual_circuit_type"}:
        return [("name", row.get("name"))]
    if kind == "virtual_circuit":
        return [("cid-provider-network", row.get("cid"), nested(row.get("provider_network")))]
    if kind == "virtual_circuit_termination":
        return [("vc-interface", nested(row.get("virtual_circuit")), nested(row.get("interface")))]
    if kind == "wireless_lan":
        return [("ssid-group", row.get("ssid"), nested(row.get("group")))]
    if kind in {"cooling_feed", "cooling_source"}:
        anchor = "cooling_source" if kind == "cooling_feed" else "site"
        return [("name-anchor", row.get("name"), nested(row.get(anchor)))]
    if kind == "inventory_item":
        return [("name-device", row.get("name"), nested(row.get("device")))]
    if kind == "ip_range":
        return [("range", row.get("start_address"), row.get("end_address"))]
    if kind == "rack_type":
        return [("name-manufacturer", row.get("model"), nested(row.get("manufacturer")))]
    if kind == "fhrp_group":
        return [("group-id", row.get("group_id"))]
    if kind == "vlan_translation_rule":
        return [("policy-vid", nested(row.get("policy")), row.get("local_vid"))]
    if kind == "wireless_link":
        return [("link", nested(row.get("interface_a")), nested(row.get("interface_b")))]
    if kind == "circuit_group_assignment":
        return [("assignment", nested(row.get("group")),
                 nested(row.get("member", row.get("member_id"))),
                 _content_type_name(row.get("member_type")))]
    if kind == "fhrp_group_assignment":
        return [("assignment", nested(row.get("group")),
                 nested(row.get("interface", row.get("interface_id"))),
                 _content_type_name(row.get("interface_type")))]
    if kind == "tunnel_termination":
        return [("assignment", nested(row.get("tunnel")),
                 nested(row.get("termination", row.get("termination_id"))),
                 _content_type_name(row.get("termination_type")))]
    if kind == "l2vpn_termination":
        return [("assignment", nested(row.get("l2vpn")),
                 nested(row.get("assigned_object", row.get("assigned_object_id"))),
                 _content_type_name(row.get("assigned_object_type")))]
    raise LoadError(f"no target identity index for {kind}")


def _content_types(client, required=None):
    result = {}
    for kind in sorted(required or CONTENT_TYPES):
        app_label, model = CONTENT_TYPES[kind]
        rows = client.all(f"/api/core/object-types/?app_label={app_label}&model={model}")
        if len(rows) != 1:
            raise LoadError(f"content type {app_label}.{model}: expected one row, found {len(rows)}")
        result[kind] = rows[0]["id"]
    return result


def _required_content_types(objects):
    required = set()
    for obj in objects.values():
        if "scope_site" in obj["refs"]:
            required.add("site")
        for field in ("termination", "assigned_object", "object", "member", "component"):
            if field in obj["refs"]:
                required.add(objects[obj["refs"][field]]["kind"])
        if obj["kind"] == "service":
            required.add(objects[obj["refs"]["virtual_machine"]]["kind"])
        if obj["kind"] == "fhrp_group_assignment":
            required.add(objects[obj["refs"]["interface"]]["kind"])
        if obj["kind"] == "cable":
            required.update(objects[obj["refs"][field]]["kind"] for field in ("a", "b"))
    return required


def _service_port_mappings(obj):
    protocol = obj["attrs"].get("protocol")
    ports = obj["attrs"].get("ports")
    if protocol not in {"tcp", "udp"}:
        raise LoadError(f"{obj['key']}: service protocol must be tcp or udp")
    if (not isinstance(ports, list) or not ports or len(ports) != len(set(ports))
            or any(type(port) is not int or not 1 <= port <= 65535 for port in ports)):
        raise LoadError(f"{obj['key']}: service ports must be unique integers from 1 through 65535")
    return [f"{protocol}/{port}" for port in ports]


def _cable_row(obj, ids):
    """Cables carry raw attrs plus their optional bundle reference; their
    terminations travel as separate rows."""
    row = dict(obj["attrs"])
    if "bundle" in obj["refs"]:
        row["bundle_id"] = ids[obj["refs"]["bundle"]]
    return row


def _render(obj, objects, ids, content_types, service_shape="protocol_ports"):
    row = dict(obj["attrs"])
    for name, value in RENDER_DEFAULTS.get(obj["kind"], {}).items():
        row.setdefault(name, value)
    for (kind, source), target in ATTRIBUTE_RENAMES.items():
        if obj["kind"] == kind and source in row:
            row[target] = row.pop(source)
    if "custom_fields" in row:
        # Canonical custom-field values carry Diode's single-key type envelope
        # ({"selection": "tier-1"}); the raw custom_field_data column stores the
        # bare value, and REST readback returns it re-wrapped for verification.
        row["custom_field_data"] = {name: next(iter(typed.values()))
                                    for name, typed in row.pop("custom_fields").items()}
    if obj["kind"] == "virtual_machine" and "start_on_boot" not in row:
        row["start_on_boot"] = "off"
    if obj["kind"] == "service":
        mappings = _service_port_mappings(obj)
        if service_shape == "port_mappings":
            row["port_mappings"] = mappings
            row.pop("protocol", None)
            row.pop("ports", None)
        elif service_shape != "protocol_ports":
            raise LoadError(f"unknown TurboBulk service shape {service_shape!r}")
    for name, column in DIRECT_REFS.items():
        if name in obj["refs"] and not (obj["kind"] == "service" and name == "virtual_machine") \
                and not (obj["kind"] == "fhrp_group_assignment" and name == "interface"):
            row[column] = ids[obj["refs"][name]]
    if obj["kind"] == "fhrp_group_assignment":
        target = objects[obj["refs"]["interface"]]
        row["interface_type_id"], row["interface_id"] = content_types[target["kind"]], ids[target["key"]]
    if obj["kind"] in DEVICE_COMPONENT_KINDS:
        row.update(_component_cache_ids(obj, objects, ids))
    if "scope_site" in obj["refs"]:
        row["scope_type_id"], row["scope_id"] = content_types["site"], ids[obj["refs"]["scope_site"]]
    for field in ("termination", "assigned_object", "object", "member", "component"):
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


def _rendered_columns(obj, service_shape="protocol_ports"):
    """Return database columns without needing resolved target IDs."""
    columns = (set(obj["attrs"]) | set(RENDER_DEFAULTS.get(obj["kind"], ()))) - DEFERRED
    if obj["kind"] == "cable" and "bundle" in obj["refs"]:
        columns.add("bundle_id")
    if obj["kind"] == "service":
        _service_port_mappings(obj)
        if service_shape == "port_mappings":
            columns.difference_update(("protocol", "ports"))
            columns.add("port_mappings")
        elif service_shape != "protocol_ports":
            raise LoadError(f"unknown TurboBulk service shape {service_shape!r}")
    if "custom_fields" in columns:
        columns.remove("custom_fields")
        columns.add("custom_field_data")
    for (kind, source), target in ATTRIBUTE_RENAMES.items():
        if obj["kind"] == kind and source in columns:
            columns.remove(source)
            columns.add(target)
    columns.update(column for name, column in DIRECT_REFS.items()
                   if name in obj["refs"] and not (obj["kind"] == "service" and name == "virtual_machine"))
    if obj["kind"] in DEVICE_COMPONENT_KINDS:
        columns.update(("_site_id", "_location_id", "_rack_id"))
    if "scope_site" in obj["refs"]:
        columns.update(("scope_type_id", "scope_id"))
    for field in ("termination", "assigned_object", "object", "member", "component"):
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


def _bounded_readback(verification):
    """Persist the preflight readback with a bounded mismatch sample.

    On a fresh branch every artifact object is an expected mismatch; the full
    list is tens of MB and gets rewritten with every receipt checkpoint. The
    count plus a sample is the evidence; the live target remains the source
    for anything deeper (matches the failures[:20] convention elsewhere).
    """
    mismatches = verification.get("mismatches")
    if not isinstance(mismatches, list) or len(mismatches) <= 20:
        return verification
    return {**verification, "mismatches": mismatches[:20],
            "mismatches_omitted": len(mismatches) - 20}


def _write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            # json.dumps-then-write is ~4x faster than streaming json.dump on
            # multi-MB receipts, and this function runs after every job/batch.
            handle.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


RQ_DEAD_STATUSES = frozenset({"failed", "stopped", "canceled"})
RQ_ORACLE_AFTER_SECONDS = 60.0


def _rq_job_state(client, job_id):
    """Best-effort read of RQ's view of a job; None when unavailable or ambiguous.

    NetBox's core background-tasks endpoint returns the RQ job record while its
    Redis hash exists and HTTP 500 mentioning NoSuchJobError once it is gone.
    Any other failure (endpoint absent, transient error) is indeterminate.
    """
    try:
        _, task = client.request(f"/api/core/background-tasks/{job_id}/", branch=False)
    except LoadError as exc:
        return "missing" if "NoSuchJobError" in str(exc) else None
    status = task.get("status")
    return status.lower() if isinstance(status, str) else None


def _poll(client, job_id, timeout):
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    interval = 0.15
    dead_observations = 0
    while True:
        _, job = client.request(f"/api/plugins/turbobulk/jobs/{job_id}/", branch=False)
        if job["status"] in {"completed", "failed", "errored", "cancelled"}:
            return job
        if time.monotonic() - started >= RQ_ORACLE_AFTER_SECONDS:
            # Only the worker process finalizes the job row, and RQ's own
            # terminal-failed statuses are written exactly when that process
            # died without returning (kill mid-job or abandoned sweep). Two
            # consecutive observations guard against a misread. A missing RQ
            # hash stays inconclusive at this age: a live horse can finish and
            # write the row without its Redis record.
            rq_status = _rq_job_state(client, job_id)
            if rq_status in RQ_DEAD_STATUSES:
                dead_observations += 1
                if dead_observations >= 2:
                    raise WorkerDied(job_id, job, rq_status, time.monotonic() - started)
            else:
                dead_observations = 0
        if time.monotonic() >= deadline:
            raise JobTimeout(job_id, job, timeout)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        interval = min(interval * 1.5, 5.0)


def _job_result(job, expected, request_settings, mode):
    data = job.get("data") or {}
    affected = (data.get("rows_inserted") or 0) + (data.get("rows_updated") or 0)
    issues = []
    if job.get("status") != "completed":
        issues.append(f"status is {job.get('status')!r}")
    if job.get("error") or data.get("error") or data.get("errors"):
        issues.append("job reported errors")
    if data.get("rows_processed") != expected:
        issues.append(f"processed {data.get('rows_processed')!r} rows instead of {expected}")
    if affected != expected:
        issues.append(f"affected {affected} rows instead of {expected}")

    requested_hooks = request_settings["post_hooks"]
    observed_hooks = data.get("post_hooks")
    if not isinstance(observed_hooks, dict):
        issues.append("post-hook results are missing")
    elif set(observed_hooks) != set(requested_hooks):
        missing = sorted(set(requested_hooks) - set(observed_hooks))
        extra = sorted(set(observed_hooks) - set(requested_hooks))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        issues.append("post-hook result keys differ from request (" + "; ".join(detail) + ")")
    else:
        for name, enabled in requested_hooks.items():
            result = observed_hooks[name]
            if not isinstance(result, dict):
                issues.append(f"post-hook {name} returned a malformed result")
            elif result.get("error"):
                issues.append(f"post-hook {name} reported an error: {result['error']}")
            elif enabled:
                succeeded = result.get("success") is True
                not_applicable = (result.get("skipped") is True and
                                  result.get("reason") == "not applicable")
                if not (succeeded or not_applicable):
                    issues.append(f"post-hook {name} neither succeeded nor was explicitly not applicable")
            elif result.get("skipped") is not True:
                issues.append(f"disabled post-hook {name} was not explicitly skipped")

    changelogs = data.get("changelogs_created")
    if mode == "insert" and request_settings["create_changelogs"] is True:
        if changelogs != expected:
            issues.append(f"created {changelogs!r} changelogs instead of {expected}")
    elif request_settings["create_changelogs"] is False and changelogs != 0:
        issues.append(f"created {changelogs!r} changelogs although changelogs were disabled")

    if issues:
        raise LoadError(
            f"TurboBulk job {job.get('job_id', '<unknown>')} violated its recorded request: "
            + "; ".join(issues)
        )
    return data


def _change_diff_count(client, branch_id, *, object_type_id=None, action=None):
    query = {"branch_id": branch_id, "limit": 1}
    if object_type_id is not None:
        query["object_type_id"] = object_type_id
    if action is not None:
        query["action"] = action
    path = "/api/plugins/branching/changes/?" + urllib.parse.urlencode(query)
    _, page = client.request(path, branch=False)
    count = page.get("count") if isinstance(page, dict) else None
    if type(count) is not int or count < 0:
        raise LoadError("Branching changes API did not return a valid count")
    return count


# Branching explicitly disables support for these models (netbox_branching
# constants.EXEMPT_MODELS, verified against the pinned 1.2.1 source): their
# writes land on main and create no ChangeDiffs, so the exact-count gate must
# not expect any.
BRANCH_EXEMPT_KINDS = {"custom_field", "custom_field_choice_set", "custom_link"}


def _expected_change_diff_counts(objects):
    expected = Counter(SPECS[obj["kind"]][0] or "dcim.modulebaytype"
                       for obj in objects.values()
                       if obj["kind"] not in BRANCH_EXEMPT_KINDS)
    expected["dcim.cabletermination"] += 2 * sum(
        obj["kind"] == "cable" for obj in objects.values())
    return +expected


WORKER_DEATH_SIGNATURE = "Job orphaned:"


def _is_worker_death_row(job):
    """True for a terminal row the orphaned-job reaper marked errored after worker death."""
    return (job.get("status") == "errored"
            and WORKER_DEATH_SIGNATURE in (job.get("error") or ""))


def _arbitrate_worker_death(client, receipt, entry):
    """Decide from exact branch evidence whether an orphaned insert committed.

    Reviewable inserts create exactly one create-ChangeDiff per row inside the
    same transaction as the rows, and no other writer touches these models on
    the branch (REST_CREATE_KINDS — module_bay_type and provider_account — are
    never TurboBulk job models; REST completion PATCHes do not change a diff's
    create action).
    The model's create-diff count therefore equals the rows of previously
    verified entries, plus this entry's rows exactly when its transaction
    committed before the worker died. Anything else is unexplained state.
    """
    preflight = receipt.get("review_history_preflight") or {}
    object_types = preflight.get("object_types") or {}
    model = entry["model"]
    if "branch_id" not in preflight or model not in object_types:
        raise LoadError(
            f"cannot arbitrate the orphaned job for {model}: the receipt lacks the "
            "reviewable history preflight for it; use a new branch and receipt"
        )
    verified_rows = sum(
        job["rows_expected"] for job in receipt["jobs"]
        if job is not entry and job.get("model") == model and job.get("mode") == "insert"
        and job.get("request_verified") is True and not job.get("superseded"))
    observed = _change_diff_count(client, preflight["branch_id"],
                                  object_type_id=object_types[model], action="create")
    evidence = {"model": model, "observed_create_diffs": observed,
                "verified_rows_before": verified_rows,
                "rows_expected": entry["rows_expected"], "read_at": _now()}
    if observed == verified_rows + entry["rows_expected"]:
        return "committed", evidence
    if observed == verified_rows:
        return "rolled-back", evidence
    raise LoadError(
        f"orphaned job for {model} left unexplained branch state: {observed} create "
        f"ChangeDiffs with {verified_rows} verified rows and {entry['rows_expected']} "
        "in question; use a new branch and receipt"
    )


def _resolve_worker_death(client, prior, receipt, receipt_path, job):
    """Adopt a committed orphaned insert or mark a rolled-back one superseded.

    Returns True when adopted (the entry counts as verified) and False when the
    caller must resubmit the batch as a fresh job. Read-only: recovery never
    resends the dead job itself.
    """
    verdict, evidence = _arbitrate_worker_death(client, receipt, prior)
    _record_observed(prior, job)
    if verdict == "committed":
        prior["adopted_after_worker_death"] = evidence
        prior["request_verified"] = True
        _write_receipt(receipt_path, receipt)
        return True
    # Resubmitting behind a still-live transaction would double every row, so
    # before a supersede ask RQ's own record as a cross-check: an affirmatively
    # live status refuses the resubmit. A dead, expired, or unreadable record
    # (the oracle needs staff access some tokens lack) leaves the ChangeDiff
    # arbitration as the deciding evidence.
    rq_status = _rq_job_state(client, _bound_job_id(prior))
    if rq_status is not None and rq_status != "missing" and rq_status not in RQ_DEAD_STATUSES:
        raise LoadError(
            f"orphaned job for {prior['model']} reads rolled back but RQ reports "
            f"{rq_status!r}; refusing to resubmit — use a fresh branch and receipt")
    evidence["rq_status"] = rq_status
    prior["superseded"] = {"reason": "worker death before commit", **evidence}
    _write_receipt(receipt_path, receipt)
    return False


def _review_history_preflight(client, branch_row, objects):
    count = _change_diff_count(client, branch_row["id"])
    if count:
        raise LoadError(
            f"reviewable delivery requires a fresh branch with no ChangeDiffs; found {count} "
            "— this branch already holds a load, and a changed or grown artifact needs a "
            "fresh branch (see docs/first-target.md, growing a loaded estate)"
        )
    object_types = {}
    for label in sorted(_expected_change_diff_counts(objects)):
        app_label, model = label.split(".", 1)
        path = "/api/core/object-types/?" + urllib.parse.urlencode(
            {"app_label": app_label, "model": model, "limit": 2})
        _, page = client.request(path, branch=False)
        rows = page.get("results", page) if isinstance(page, dict) else page
        if not isinstance(rows, list) or len(rows) != 1 or type(rows[0].get("id")) is not int:
            raise LoadError(f"reviewable history preflight expected one object type for {label}")
        object_type_id = rows[0]["id"]
        if _change_diff_count(client, branch_row["id"], object_type_id=object_type_id):
            raise LoadError(f"reviewable delivery requires zero initial ChangeDiffs for {label}")
        object_types[label] = object_type_id
    return {"verified_at": _now(), "branch_id": branch_row["id"], "initial_count": 0,
            "endpoint": "/api/plugins/branching/changes/", "object_types": object_types}


def _verify_review_history(client, branch_row, objects, preflight):
    expected = _expected_change_diff_counts(objects)
    object_types = preflight.get("object_types") if isinstance(preflight, dict) else None
    if not isinstance(object_types, dict) or set(object_types) != set(expected):
        raise LoadError("receipt does not bind the required ChangeDiff object types")
    models = {}
    failures = []
    for label, expected_count in sorted(expected.items()):
        object_type_id = object_types[label]
        if type(object_type_id) is not int:
            failures.append(f"{label}: invalid bound object type")
            continue
        observed = _change_diff_count(
            client, branch_row["id"], object_type_id=object_type_id)
        creates = _change_diff_count(
            client, branch_row["id"], object_type_id=object_type_id, action="create")
        models[label] = {"object_type_id": object_type_id, "expected": expected_count,
                         "observed": observed, "creates": creates}
        if observed != expected_count or creates != expected_count:
            failures.append(
                f"{label}: expected {expected_count} create ChangeDiffs, found "
                f"{observed} total and {creates} creates"
            )
    total = _change_diff_count(client, branch_row["id"])
    if total != sum(expected.values()):
        failures.append(
            f"branch total: expected {sum(expected.values())} ChangeDiffs, found {total}"
        )
    if failures:
        raise LoadError("reviewable branch history is incomplete or unattributed: " + "; ".join(failures))
    return {"verified_at": _now(), "branch_id": branch_row["id"],
            "expected": sum(expected.values()), "observed": total,
            "models": models}


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
    result = _job_result(job, entry["rows_expected"], entry["request_settings"], entry["mode"])
    entry["request_verified"] = True
    return result


def _record_failure(receipt, receipt_path, ids, overall, exc):
    """Close the active attempt and preserve the latest durable observation."""
    if isinstance(exc, JobTimeout):
        entry = next((job for job in receipt["jobs"] if job.get("job_id") == exc.job_id), None)
        if entry is not None:
            _record_observed(entry, exc.job)
    message = str(exc) if isinstance(exc, Exception) else type(exc).__name__
    receipt.update(success=False, failed_at=_now(), error=message, resolved_ids=ids)
    receipt["attempts"][-1].update(success=False, result="failed",
                                   completed_at=receipt["failed_at"],
                                   wall_seconds=round(time.monotonic() - overall, 6),
                                   error=message)
    _write_receipt(receipt_path, receipt)


def _submit(client, branch_name, model, rows, purpose, keys, receipt, receipt_path, timeout,
            mode="insert", request_settings=None):
    request_settings = request_settings or delivery_contract("reviewable")["request_settings"]
    compile_started = time.monotonic()
    payload = gzip.compress(b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode()
                                     for row in rows), mtime=0)
    compile_seconds = round(time.monotonic() - compile_started, 6)
    fields = {"model": model, "mode": mode, "branch": branch_name,
              "validation_mode": request_settings["validation_mode"],
              "create_changelogs": str(request_settings["create_changelogs"]).lower(),
              "apply_save_hooks": str(request_settings["apply_save_hooks"]).lower(),
              "dispatch_events": str(request_settings["dispatch_events"]).lower()}
    fields.update({f"post_hooks.{name}": str(enabled).lower()
                   for name, enabled in request_settings["post_hooks"].items()})
    boundary, body = _multipart(fields, model + ".jsonl.gz", payload)
    entry = {"purpose": purpose, "model": model, "mode": mode, "canonical_keys": keys,
             "rows_expected": len(rows), "compressed_bytes": len(payload),
             "payload_sha256": hashlib.sha256(payload).hexdigest(),
             "compile_seconds": compile_seconds, "status": "submitting",
             "intent_recorded_at": _now(), "request_settings": request_settings}
    receipt["jobs"].append(entry)
    _write_receipt(receipt_path, receipt)
    started = time.monotonic()
    status, answer = client.request("/api/plugins/turbobulk/load/", method="POST", body=body,
                                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, branch=False)
    uploaded = time.monotonic()
    entry.update(upload_seconds=round(uploaded - started, 6), http_status=status,
                 job_id=answer["job_id"], status="submitted", submitted_at=_now())
    _write_receipt(receipt_path, receipt)
    job = _poll(client, entry["job_id"], timeout)
    finished = time.monotonic()
    entry["poll_seconds"] = round(finished - uploaded, 6)
    _record_terminal(entry, job, round(finished - started, 6))
    _write_receipt(receipt_path, receipt)
    print(f"{purpose}: {len(rows):,} rows completed", flush=True)
    return entry


def _batches(values, size):
    return [values[offset:offset + size] for offset in range(0, len(values), size)]


def _batch_purpose(purpose, number, count):
    return purpose if count == 1 else f"{purpose}:batch-{number}-of-{count}"


def _job_request_schedule(phases, objects, max_job_rows, base_settings):
    """Return exact hook settings after considering every occurrence of each model."""
    grouped_phases = []
    for phase_number, keys in enumerate(phases, 1):
        grouped = defaultdict(list)
        for key in keys:
            grouped[objects[key]["kind"]].append(objects[key])
        grouped_phases.append((phase_number, grouped))

    schedule = {}
    for phase_number, grouped in grouped_phases:
        for kind, candidates in grouped.items():
            if kind in REST_CREATE_KINDS:
                continue
            purpose = f"phase-{phase_number}:{kind}"
            count = (len(candidates) + max_job_rows - 1) // max_job_rows
            for number in range(1, count + 1):
                schedule[_batch_purpose(purpose, number, count)] = _batch_request_settings(
                    base_settings)
            if kind == "cable":
                termination_purpose = purpose + ":terminations"
                termination_count = (2 * len(candidates) + max_job_rows - 1) // max_job_rows
                for number in range(1, termination_count + 1):
                    schedule[_batch_purpose(termination_purpose, number, termination_count)] = (
                        _batch_request_settings(base_settings))
    for purpose, _model, hook in _finalizer_plan(objects):
        schedule[purpose] = _finalizer_request_settings(base_settings, hook)
    return schedule


def _require_job_settings(entry, expected):
    if entry.get("request_settings") != expected:
        raise LoadError(
            f"receipt job {entry.get('purpose', '<unknown>')} has different request settings; "
            "choose a new receipt and fresh branch"
        )


def _load_model_batches(client, branch_name, kind, candidates, objects, ids, content_types,
                        service_shape, purpose, receipt, receipt_path, timeout, max_job_rows,
                        base_settings):
    """Submit deterministic model batches and checkpoint IDs after each one."""
    batches = _batches(candidates, max_job_rows)
    for number, batch in enumerate(batches, 1):
        batch_purpose = _batch_purpose(purpose, number, len(batches))
        settings = _batch_request_settings(base_settings)
        prior = next((job for job in receipt["jobs"]
                      if job["purpose"] == batch_purpose and not job.get("superseded")), None)
        if prior:
            _require_job_settings(prior, settings)
            if prior.get("request_verified") is not True:
                job = _poll(client, _bound_job_id(prior), timeout)
                if _is_worker_death_row(job):
                    if not _resolve_worker_death(client, prior, receipt, receipt_path, job):
                        prior = None
                else:
                    _record_terminal(prior, job)
                    _write_receipt(receipt_path, receipt)
        if prior is None and not all(obj["key"] in ids for obj in batch):
            pending = [obj for obj in batch if obj["key"] not in ids]
            rows = ([_cable_row(obj, ids) for obj in pending] if kind == "cable" else
                    [_render(obj, objects, ids, content_types, service_shape) for obj in pending])
            _submit(client, branch_name, SPECS[kind][0], rows, batch_purpose,
                    [obj["key"] for obj in pending], receipt, receipt_path, timeout,
                    request_settings=settings)
    _refresh(client, kind, candidates, ids, objects=objects)
    receipt["resolved_ids"] = ids
    _write_receipt(receipt_path, receipt)


def _load_termination_batches(client, branch_name, terminations, purpose, receipt,
                              receipt_path, timeout, max_job_rows, base_settings):
    """Submit deterministic cable-termination batches without global hooks."""
    batches = _batches(terminations, max_job_rows)
    for number, batch in enumerate(batches, 1):
        batch_purpose = _batch_purpose(purpose, number, len(batches))
        settings = _batch_request_settings(base_settings)
        prior = next((job for job in receipt["jobs"]
                      if job["purpose"] == batch_purpose and not job.get("superseded")), None)
        if prior:
            _require_job_settings(prior, settings)
            if prior.get("request_verified") is not True:
                job = _poll(client, _bound_job_id(prior), timeout)
                if _is_worker_death_row(job):
                    if not _resolve_worker_death(client, prior, receipt, receipt_path, job):
                        prior = None
                else:
                    _record_terminal(prior, job)
                    _write_receipt(receipt_path, receipt)
        if prior is not None:
            continue
        _submit(client, branch_name, "dcim.cabletermination", batch, batch_purpose, [],
                receipt, receipt_path, timeout, request_settings=settings)


def _run_finalizers(client, branch_name, objects, receipt, receipt_path, timeout,
                    base_settings):
    """Run global hooks as independently checkpointed zero-row jobs."""
    for purpose, model, hook in _finalizer_plan(objects):
        settings = _finalizer_request_settings(base_settings, hook)
        attempts = [job for job in receipt["jobs"] if job["purpose"] == purpose]
        prior = attempts[-1] if attempts else None
        if prior:
            _require_job_settings(prior, settings)
            if prior.get("request_verified") is True:
                continue
            if not prior.get("job_id"):
                _adopt_finalizer_job(
                    client, branch_name, prior, receipt, receipt_path)
            job = _poll(client, _bound_job_id(prior), timeout)
            try:
                _record_terminal(prior, job)
            except LoadError as exc:
                _write_receipt(receipt_path, receipt)
                if len(attempts) >= 2:
                    raise LoadError(
                        f"zero-row finalizer {purpose} failed after {len(attempts)} attempts; "
                        "choose a new receipt and fresh branch"
                    ) from exc
                prior.update(retryable_finalizer=True, retry_recorded_at=_now())
                _write_receipt(receipt_path, receipt)
            else:
                _write_receipt(receipt_path, receipt)
                continue
        _submit(client, branch_name, model, [], purpose, [], receipt, receipt_path,
                timeout, request_settings=settings)


def _adopt_finalizer_job(client, branch_name, entry, receipt, receipt_path):
    """Bind one otherwise unambiguous zero-row finalizer after a lost POST response."""
    if entry.get("rows_expected") != 0 or not entry.get("purpose", "").startswith("finalize:"):
        _bound_job_id(entry)
    started = datetime.fromisoformat(entry["intent_recorded_at"].replace("Z", "+00:00"))
    failed = datetime.fromisoformat(receipt["failed_at"].replace("Z", "+00:00"))
    query = urllib.parse.urlencode({
        "created__after": (started - timedelta(seconds=30)).isoformat(),
        "created__before": (failed + timedelta(seconds=30)).isoformat(),
        "limit": 100,
        "ordering": "created",
    })
    _, page = client.request(f"/api/core/jobs/?{query}", branch=False)
    rows = page.get("results", page)
    if isinstance(page, dict) and (page.get("next") or page.get("count", len(rows)) > len(rows)):
        raise LoadError(
            f"TurboBulk submission outcome for {entry['purpose']} is ambiguous: "
            "the matching job window is paginated. Inspect the target job and branch, "
            "or use a fresh branch and receipt."
        )
    bound = {job.get("job_id") for job in receipt["jobs"] if job.get("job_id")}
    matches = []
    for row in rows:
        job_id = row.get("job_id")
        if not job_id or job_id in bound or row.get("name") != "Bulk Load":
            continue
        _, detail = client.request(f"/api/plugins/turbobulk/jobs/{job_id}/", branch=False)
        data = detail.get("data") or {}
        if (data.get("branch") == branch_name
                and data.get("model") == entry["model"]
                and data.get("mode") == entry["mode"]
                and data.get("rows_processed") == 0):
            try:
                _job_result(detail, 0, entry["request_settings"], entry["mode"])
            except LoadError:
                continue
            matches.append(detail)
    if len(matches) != 1:
        raise LoadError(
            f"TurboBulk submission outcome for {entry['purpose']} is ambiguous: found "
            f"{len(matches)} matching zero-row jobs. Inspect or clean up the target job and branch, "
            "or use a fresh branch and receipt."
        )
    job = matches[0]
    entry.update(
        job_id=job["job_id"], status="adopted", adopted_at=_now(),
        adoption_evidence={
            "created": job.get("created"), "model": job["data"]["model"],
            "branch": job["data"]["branch"], "rows_processed": 0,
        })
    _write_receipt(receipt_path, receipt)


def _bound_job_id(entry):
    if not entry.get("job_id"):
        raise LoadError(
            f"TurboBulk submission outcome for {entry['purpose']} is ambiguous: durable intent exists "
            "without a job ID. Inspect or clean up the target job and branch, or use a fresh branch and receipt."
        )
    return entry["job_id"]


def _refresh(client, kind, candidates, ids, *, allow_missing=False, objects=None):
    rows = client.all(SPECS[kind][1])
    by_id = {row["id"]: row for row in rows}
    by_identity = defaultdict(list)
    for row in rows:
        for identity in _row_bucket_keys(kind, row):
            by_identity[identity].append(row)
    for obj in candidates:
        if obj["key"] in ids:
            row = by_id.get(ids[obj["key"]])
            if row is None or not _matches(obj, row, ids, objects):
                raise LoadError(f"checkpoint identity {obj['key']} no longer matches target ID {ids[obj['key']]}; use a fresh branch")
            continue
        bucket = by_identity[_candidate_bucket_key(obj, ids, objects)]
        found = [row for row in bucket if _matches(obj, row, ids, objects)]
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
    rest_schemas = _rest_schema_preflight(client, objects)
    rest_patch_schemas = _rest_patch_preflight(client, objects)
    component_filters = _component_filter_preflight(client, objects)
    _, available = client.request("/api/plugins/turbobulk/models/", branch=False)
    models = {row["full_name"] for row in available if not row.get("export_only")}
    required = {SPECS[obj["kind"]][0] for obj in objects.values()
                if obj["kind"] not in REST_CREATE_KINDS}
    if any(obj["kind"] == "cable" for obj in objects.values()):
        required.add("dcim.cabletermination")
    missing = sorted(required - models)
    if missing:
        raise LoadError("TurboBulk cannot write required models: " + ", ".join(missing))
    schemas = {}
    for model in sorted(required):
        _, schema = client.request(f"/api/plugins/turbobulk/models/{model}/", branch=False)
        schemas[model] = {field["name"] for field in schema["fields"]}
    service_shape = None
    if any(obj["kind"] == "service" for obj in objects.values()):
        service_fields = schemas["ipam.service"]
        if "port_mappings" in service_fields:
            service_shape = "port_mappings"
        elif {"protocol", "ports"} <= service_fields:
            service_shape = "protocol_ports"
        else:
            raise LoadError("installed TurboBulk service schema supports neither port_mappings nor protocol plus ports")
    missing_columns = {}
    for obj in objects.values():
        if obj["kind"] in REST_CREATE_KINDS:
            continue
        model = SPECS[obj["kind"]][0]
        columns = (set(obj["attrs"]) if obj["kind"] == "cable" else
                   _rendered_columns(obj, service_shape or "protocol_ports"))
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
    return {"models": sorted(required), "schema_fields": {model: len(fields) for model, fields in schemas.items()},
            "rest_create_fields": rest_schemas, "rest_patch_fields": rest_patch_schemas,
            "component_cache_filters": component_filters,
            "service_shape": service_shape}


def _component_filter_preflight(client, objects):
    """Require the REST filters used to prove ComponentModel cache fields."""
    kinds = sorted({obj["kind"] for obj in objects.values()} & DEVICE_COMPONENT_KINDS)
    if not kinds:
        return {}
    _, schema = client.request("/api/schema/?format=json", branch=False)

    def resolve(parameter):
        reference = parameter.get("$ref") if isinstance(parameter, dict) else None
        if not reference or not reference.startswith("#/"):
            return parameter
        value = schema
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        return value

    required = {"site_id", "location_id", "rack_id"}
    result = {}
    for kind in kinds:
        endpoint = SPECS[kind][1]
        operation = (schema.get("paths") or {}).get(endpoint, {}).get("get") or {}
        available = {resolved.get("name") for parameter in operation.get("parameters", [])
                     if isinstance((resolved := resolve(parameter)), dict)
                     and resolved.get("in") == "query"}
        if absent := required - available:
            raise LoadError(
                f"REST schema {endpoint} lacks component-cache readback filters: "
                + ", ".join(sorted(absent))
            )
        result[kind] = sorted(required)
    return result


def _rest_schema_preflight(client, objects):
    rest_only = sorted({obj["kind"] for obj in objects.values()} & REST_CREATE_KINDS)
    rest_schemas = {}
    for kind in rest_only:
        endpoint = SPECS[kind][1]
        try:
            _, options = client.request(endpoint, method="OPTIONS")
        except LoadError as exc:
            raise LoadError(
                f"target has no writable REST model for required canonical kind {kind} at {endpoint}; "
                "the artifact cannot be loaded exactly into this NetBox version"
            ) from exc
        fields = set((options.get("actions") or {}).get("POST") or {})
        required_fields = set().union(*(_rest_create_fields(kind, obj)
                                        for obj in objects.values() if obj["kind"] == kind))
        if absent := required_fields - fields:
            raise LoadError(f"REST schema {endpoint} would drop required fields: {', '.join(sorted(absent))}")
        rest_schemas[kind] = len(fields)
    return rest_schemas


def _rest_patch_preflight(client, objects):
    required = defaultdict(set)
    for obj in objects.values():
        required[obj["kind"]].update(set(obj["refs"]) & DEFERRED)
    result = {}
    for kind, fields in sorted(required.items()):
        if not fields:
            continue
        endpoint = SPECS[kind][1]
        try:
            _, options = client.request(endpoint, method="OPTIONS")
        except LoadError as exc:
            raise LoadError(f"cannot inspect REST completion schema for {kind} at {endpoint}") from exc
        actions = options.get("actions") or {}
        writable = set(actions.get("PATCH") or actions.get("POST") or {})
        if absent := fields - writable:
            raise LoadError(f"REST completion schema {endpoint} cannot write: {', '.join(sorted(absent))}")
        result[kind] = sorted(fields)
    return result


def _rest_create_fields(kind, obj):
    if kind == "module_bay_type":
        return set(obj["attrs"]) | {"manufacturer"}
    if kind == "provider_account":
        return set(obj["attrs"]) | {"provider"} | ({"owner"} if "owner" in obj["refs"] else set())
    if kind in {"custom_field", "custom_field_choice_set", "custom_link"}:
        return set(obj["attrs"]) | set(obj["refs"])
    raise LoadError(f"no REST create compiler for {kind}")


def _render_rest_create(obj, ids):
    if obj["kind"] == "module_bay_type":
        return {**obj["attrs"], "manufacturer": ids[obj["refs"]["manufacturer"]]}
    if obj["kind"] == "provider_account":
        payload = {**obj["attrs"], "provider": ids[obj["refs"]["provider"]]}
        if "owner" in obj["refs"]:
            payload["owner"] = ids[obj["refs"]["owner"]]
        return payload
    if obj["kind"] in {"custom_field", "custom_field_choice_set", "custom_link"}:
        payload = {**obj["attrs"],
                   **{name: ids[key] for name, key in obj["refs"].items()}}
        if "extra_choices" in payload:
            # Canonical choices use Diode's "value:label" strings; REST wants
            # [value, label] pairs.
            payload["extra_choices"] = [choice.split(":", 1)
                                        for choice in payload["extra_choices"]]
        return payload
    raise LoadError(f"no REST create compiler for {obj['kind']}")


def _create_rest(client, kind, candidates, ids, receipt, receipt_path, objects=None):
    """Create REST-only rows one at a time with durable intent for safe recovery."""
    endpoint = SPECS[kind][1]
    operations = receipt.setdefault("rest_creates", [])
    for obj in candidates:
        prior = next((entry for entry in operations if entry["canonical_key"] == obj["key"]), None)
        if prior:
            _refresh(client, kind, [obj], ids, allow_missing=True, objects=objects)
            if obj["key"] not in ids:
                if prior["status"] == "submitting":
                    raise LoadError(f"REST create outcome for {obj['key']} is ambiguous; use a fresh branch and receipt")
                raise LoadError(f"completed REST create for {obj['key']} is absent; use a fresh branch")
            prior.update(status="completed", target_id=ids[obj["key"]], recovered=True,
                         last_observed_at=_now())
            _write_receipt(receipt_path, receipt)
            continue
        before = dict(ids)
        _refresh(client, kind, [obj], ids, allow_missing=True, objects=objects)
        if set(ids) - set(before):
            raise LoadError(f"found uncheckpointed {kind} identity {obj['key']}; use a fresh branch and receipt")
        entry = {"purpose": f"create:{kind}", "canonical_key": obj["key"],
                 "status": "submitting", "submitted_at": _now()}
        operations.append(entry)
        _write_receipt(receipt_path, receipt)
        started = time.monotonic()
        status, row = client.request(endpoint, method="POST",
                                     body=json.dumps(_render_rest_create(obj, ids)).encode(),
                                     headers={"Content-Type": "application/json"})
        target_id = _nested_id(row)
        if not isinstance(target_id, int) or not _matches(obj, row, ids):
            raise LoadError(f"REST create for {obj['key']} returned an invalid target row")
        ids[obj["key"]] = target_id
        entry.update(status="completed", target_id=target_id, http_status=status,
                     wall_seconds=round(time.monotonic() - started, 6), completed_at=_now())
        _write_receipt(receipt_path, receipt)


REST_PATCH_LIST_FIELDS = {"tagged_vlans", "tags", "groups", "module_bay_types", "ipaddresses",
                          "asns", "export_targets", "import_targets", "wireless_lans", "proposals"}


def _observed_patch_value(value):
    """Readback shape back to the scalar the PATCH sent: FK dicts carry an id,
    choice fields come back as {"value", "label"} dicts (e.g. start_on_boot)."""
    if isinstance(value, dict) and "id" not in value and "value" in value:
        return value["value"]
    return _nested_id(value)


def _patch_state(actual, desired, purpose):
    """Return field matches after proving every nonmatching value is safe to replace."""
    matches = []
    for field, expected in desired.items():
        if field == "id":
            continue
        if field in REST_PATCH_LIST_FIELDS:
            observed = sorted(_nested_id(item) for item in (actual.get(field) or []))
            if set(observed) - set(expected):
                raise LoadError(
                    f"{purpose} target {desired['id']} field {field} has concurrent values"
                )
        else:
            observed = _observed_patch_value(actual.get(field))
            if observed not in (None, expected):
                raise LoadError(
                    f"{purpose} target {desired['id']} field {field} changed concurrently"
                )
        matches.append(observed == expected)
    return matches


def _rest_payload(rows):
    return json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()


def _send_rest_patch(client, endpoint, entry, receipt, receipt_path):
    attempt = {"started_at": _now()}
    entry.setdefault("attempts", []).append(attempt)
    _write_receipt(receipt_path, receipt)
    started = time.monotonic()
    try:
        status, _ = client.request(
            endpoint, method="PATCH", body=_rest_payload(entry["payload"]),
            headers={"Content-Type": "application/json"})
    except WriteRejected as exc:
        attempt.update(
            completed_at=_now(), result="rejected",
            wall_seconds=round(time.monotonic() - started, 6), error=str(exc))
        entry.update(status="rejected", rejected_at=attempt["completed_at"])
        _write_receipt(receipt_path, receipt)
        raise
    except BaseException as exc:
        attempt.update(
            completed_at=_now(), result="no-terminal-response",
            wall_seconds=round(time.monotonic() - started, 6),
            error=str(exc) if isinstance(exc, Exception) else type(exc).__name__)
        _write_receipt(receipt_path, receipt)
        raise
    attempt.update(completed_at=_now(), result="accepted", http_status=status,
                   wall_seconds=round(time.monotonic() - started, 6))
    entry.update(status="completed", http_status=status, completed_at=attempt["completed_at"],
                 wall_seconds=round(sum(item["wall_seconds"] for item in entry["attempts"]), 6))
    _write_receipt(receipt_path, receipt)


def _reconcile_rest_patches(client, endpoint, current, receipt, receipt_path, purpose,
                            expected=None):
    """Resolve durable PATCH intents before compiling any new completion work."""
    expected = expected or {}
    for entry in receipt["rest_batches"]:
        if entry.get("purpose") != purpose:
            continue
        if not entry.get("payload"):
            raise LoadError(
                f"{purpose} has a legacy REST checkpoint without its payload; use a fresh branch"
            )
        payload = entry["payload"]
        encoded = _rest_payload(payload)
        target_ids = [row.get("id") for row in payload]
        valid = (
            entry.get("endpoint") == endpoint
            and entry.get("target_ids") == target_ids
            and entry.get("payload_sha256") == hashlib.sha256(encoded).hexdigest()
            and len(target_ids) == len(set(target_ids))
            and all(type(target_id) is int and target_id in current for target_id in target_ids)
            and entry.get("rows") == len(payload)
            and entry.get("status") in {"submitting", "completed", "rejected"}
        )
        if not valid:
            raise LoadError(f"{purpose} has an invalid REST write-ahead checkpoint; use a fresh branch")
        for row in payload:
            artifact_row = expected.get(row["id"])
            if expected and artifact_row is None:
                raise LoadError(
                    f"{purpose} REST checkpoint is outside the bound artifact; use a fresh branch"
                )
            if artifact_row is not None and (set(row) - set(artifact_row)
                    or any(artifact_row.get(field) != value for field, value in row.items())):
                raise LoadError(
                    f"{purpose} REST checkpoint differs from the bound artifact; use a fresh branch"
                )
        matches = [matched for row in payload
                   for matched in _patch_state(current[row["id"]], row, purpose)]
        if entry["status"] == "rejected":
            raise LoadError(
                f"{purpose} REST batch was rejected by the target; use a fresh branch"
            )
        if entry["status"] == "completed":
            if not all(matches):
                raise LoadError(
                    f"{purpose} completed REST checkpoint no longer matches; use a fresh branch"
                )
            continue
        if all(matches):
            entry.update(status="completed", recovered=True, recovered_at=_now())
            _write_receipt(receipt_path, receipt)
            continue
        state = "partially applied or changed concurrently" if any(matches) else "still unresolved"
        raise LoadError(
            f"{purpose} REST batch is {state} after a lost response; use a fresh branch"
        )


def _bulk_patch(client, endpoint, rows, receipt, receipt_path, purpose,
                batch_size=REST_PATCH_ROWS):
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        payload = _rest_payload(batch)
        entry = {
            "purpose": purpose, "endpoint": endpoint, "offset": offset, "rows": len(batch),
            "target_ids": [row["id"] for row in batch], "payload": batch,
            "payload_sha256": hashlib.sha256(payload).hexdigest(), "status": "submitting",
            "intent_recorded_at": _now(), "attempts": [],
        }
        receipt["rest_batches"].append(entry)
        # _send_rest_patch durably writes the receipt (entry + attempt) before
        # the PATCH; a second pre-PATCH write here adds cost, not safety.
        _send_rest_patch(client, endpoint, entry, receipt, receipt_path)


def _trace_contains_cable(value, cable_id, label):
    if isinstance(value, dict):
        if value.get("id") == cable_id and value.get("label") == label:
            return True
        return any(_trace_contains_cable(item, cable_id, label) for item in value.values())
    if isinstance(value, list):
        return any(_trace_contains_cable(item, cable_id, label) for item in value)
    return False


# The only kinds whose pre-existing rows may be allowlisted past the hard
# empty-inventory rule. Kinds here must have a plain-attribute readback
# identity (never a reference) so disjointness needs no resolution. Keeping
# these explicit sets prevents the allowlist from silently adopting leftover
# population of arbitrary kinds.
ALLOWLISTED_BUILTIN_KINDS = {"module_type_profile"}  # factory rows (4.7 profiles)
# NetBox 4.7 owner/owner_group rows live on main and are not branch-isolated:
# another estate's rows are always visible to a fresh branch load on a shared
# target, and branch deletion does not remove them. Their names carry the
# estate namespace, so the same disjoint-identity machinery lets multiple
# namespaces share one target; an identity collision stays a hard block.
MAIN_SCOPED_KINDS = {"owner", "owner_group",
                     # extras definitions are not branch-isolated either; their
                     # names carry the namespace (custom_field uses its
                     # underscore form), so disjoint estates coexist.
                     "custom_field", "custom_field_choice_set", "custom_link"}
ALLOWLISTED_KINDS = ALLOWLISTED_BUILTIN_KINDS | MAIN_SCOPED_KINDS


def _colliding_rows(plan, rows, kind):
    """Rows whose identity is also planned — the only rows cleanup may touch.

    Everything else on the endpoint belongs to other namespaces (or the
    factory) and must stay, even when a collision blocks this kind's allowlist.
    """
    from lab.verify import IDENTITIES
    identity = IDENTITIES.get(kind, ("name",))
    planned = {tuple(obj["attrs"].get(field) for field in identity)
               for obj in plan["objects"] if obj["kind"] == kind}
    return {row["id"]: row.get("name") for row in rows
            if tuple(row.get(field) for field in identity) in planned}


def _merge_allowlists(prior, fresh):
    """Union two allowlists per kind, keeping ids and identities aligned."""
    kinds = set(prior.get("target_ids") or {}) | set(fresh.get("target_ids") or {})
    if not kinds:
        return {}
    def union(field):
        return {kind: sorted(set((prior.get(field) or {}).get(kind, []))
                             | set((fresh.get(field) or {}).get(kind, [])))
                for kind in kinds}
    return {"success": True, "target_ids": union("target_ids"),
            "target_identities": {kind: values for kind, values
                                  in union("target_identities").items() if values}}


def _bootstrap_allowlist(plan, inventory, extras_only=False):
    """Allow pre-existing rows that provably cannot collide with the artifact.

    Two declared groups qualify: target-native factory rows (for example the
    eight ModuleTypeProfiles on 4.7.1) and main-scoped owner/owner_group rows
    another estate left on a shared 4.7 target. A fresh load may proceed over
    them only for kinds in ALLOWLISTED_KINDS when every existing identity is
    disjoint from the plan's — the exact ids are recorded and allowlisted
    through strict readback, mirroring the lab bootstrap receipt. Anything
    else keeps the hard empty-inventory rule.

    With extras_only=True (verifying an already-loaded target, where planned
    rows are legitimately present) the allowlist is instead the per-row set of
    allowlistable rows whose identities are not planned.
    """
    from lab.verify import IDENTITIES

    allowed = {}
    identities = {}
    plan_kinds = defaultdict(list)
    for obj in plan["objects"]:
        plan_kinds[obj["kind"]].append(obj)
    for kind, rows in inventory.items():
        if kind not in ALLOWLISTED_KINDS or not rows:
            continue
        identity = IDENTITIES.get(kind, ("name",))
        objs = plan_kinds.get(kind, [])
        if any(field in obj["refs"] for obj in objs for field in identity):
            continue
        if any(field not in row for row in rows for field in identity):
            continue
        planned = {tuple(obj["attrs"].get(field) for field in identity) for obj in objs}
        extras = [row for row in rows
                  if tuple(row[field] for field in identity) not in planned]
        if not extras_only and len(extras) != len(rows):
            continue  # identity collision on a fresh load stays a hard block
        if extras:
            allowed[kind] = sorted(row["id"] for row in extras)
            # Record the excused identities alongside the ids so the receipt
            # is auditable: which exact rows were left in place, by name.
            identities[kind] = sorted(
                " ".join(str(row[field]) for field in identity) for row in extras)
    if not allowed:
        return {}
    return {"success": True, "target_ids": allowed, "target_identities": identities}


def _readback_fields(plan):
    """Per-kind ``?fields=`` projections derived from the plan's own emissions.

    verify_plan compares exactly the plan's attr/ref keys against each REST row
    (plus identity/matching fields and two documented special mappings), and it
    fails closed on any missing field — so a projection can only surface as a
    loud missing_api_field, never silent under-verification. Projections cut
    payloads, serializer prefetches, and per-row computed fields (for example
    interface connected_endpoints resolves a CablePath per row).
    """
    from lab.verify import IDENTITIES

    base = {"id", "url", "display", "name", "label", "slug", "description", "asset_tag"}
    # Projection is limited to kinds whose serializers hold no composed/generic
    # fields that NetBox cannot project (e.g. circuit_termination.termination):
    # interface dominates readback cost (its serializer resolves a CablePath per
    # row for connected_endpoints), and its fields are all plain. Widening this
    # set means adding the readback fields verify_plan reads that are not in
    # attrs/refs (front_port rear-port mapping, service port_mappings, cable
    # terminations) for each new kind.
    projectable = {"interface"}
    fields = {}
    for obj in plan["objects"]:
        kind = obj["kind"]
        if kind not in projectable:
            continue
        if kind not in fields:
            identity = set(IDENTITIES.get(kind, ()))
            fields[kind] = set(base) | identity
        fields[kind].update(obj["attrs"])
        fields[kind].update(obj["refs"])
    return fields


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


def _verify_component_caches(client, objects, ids):
    """Prove component placement caches through NetBox's cache-backed filters."""
    expected = defaultdict(set)
    for obj in objects.values():
        if obj["kind"] not in DEVICE_COMPONENT_KINDS:
            continue
        cache = _component_cache_ids(obj, objects, ids)
        placement = tuple(cache[field] for field in ("_site_id", "_location_id", "_rack_id"))
        expected[(obj["kind"], placement)].add(ids[obj["key"]])

    failures = []
    started = time.monotonic()
    order = lambda item: (item[0][0], tuple(-1 if value is None else value for value in item[0][1]))
    for (kind, placement), target_ids in sorted(expected.items(), key=order):
        parameters = [("brief", "1")]
        for field, value in zip(("site_id", "location_id", "rack_id"), placement):
            parameters.append((field, "null" if value is None else str(value)))
        rows = client.all(SPECS[kind][1] + "?" + urllib.parse.urlencode(parameters))
        observed_ids = {row["id"] for row in rows}
        if observed_ids != target_ids:
            failures.append({
                "kind": kind,
                "site_id": placement[0],
                "location_id": placement[1],
                "rack_id": placement[2],
                "missing_ids": sorted(target_ids - observed_ids)[:20],
                "unexpected_ids": sorted(observed_ids - target_ids)[:20],
            })
    return {
        "components_expected": sum(len(value) for value in expected.values()),
        "placement_queries": len(expected),
        "failures": failures[:20],
        "wall_seconds": round(time.monotonic() - started, 6),
    }


def _complete_rest(client, plan, objects, ids, receipt, receipt_path):
    current = {kind: {row["id"]: row for row in client.all(SPECS[kind][1])}
               for kind in sorted({obj["kind"] for obj in plan["objects"]
                                   if obj["kind"] in SPECS and (set(obj["refs"]) & DEFERRED)})}
    for kind, rows in current.items():
        purpose = f"complete:{kind}"
        expected = {}
        for obj in plan["objects"]:
            if obj["kind"] != kind:
                continue
            row = {"id": ids[obj["key"]]}
            for field in ("primary_ip4", "primary_ip6", "oob_ip", "primary_mac_address", "master"):
                if field in obj["refs"]:
                    row[field] = ids[obj["refs"][field]]
            for field in REST_PATCH_LIST_FIELDS:
                if field in obj["refs"]:
                    row[field] = sorted(ids[key] for key in obj["refs"][field])
            if kind == "virtual_machine":
                row["start_on_boot"] = obj["attrs"].get("start_on_boot", "off")
            expected[row["id"]] = row
        _reconcile_rest_patches(
            client, SPECS[kind][1], rows, receipt, receipt_path, purpose, expected)
        patches = []
        for obj in plan["objects"]:
            if obj["kind"] != kind:
                continue
            desired = expected[ids[obj["key"]]]
            matches = _patch_state(rows[desired["id"]], desired, obj["key"])
            patch = {"id": desired["id"]}
            fields = [field for field in desired if field != "id"]
            for field, matched in zip(fields, matches):
                if not matched:
                    patch[field] = desired[field]
            if len(patch) > 1:
                patches.append(patch)
        if patches:
            _bulk_patch(client, SPECS[kind][1], patches, receipt, receipt_path, purpose)
    completed = [row for row in receipt["rest_batches"] if row.get("status") == "completed"]
    receipt["rest_rows_completed"] = sum(row["rows"] for row in completed)
    receipt["rest_rows_recovered"] = sum(row["rows"] for row in completed if row.get("recovered"))
    receipt["rest_rows_write_intents"] = sum(
        row["rows"] * len(row.get("attempts", [])) for row in receipt["rest_batches"])
    # Backward-compatible name retained for existing receipt consumers.
    receipt["rest_rows_attempted"] = receipt["rest_rows_write_intents"]
    _write_receipt(receipt_path, receipt)
    return receipt["rest_rows_completed"]


def _verify_receipt_guard(path):
    """Refuse to overwrite anything but a prior verification receipt.

    Load receipts are the sole resume/recovery checkpoint for their branch; a
    verify run pointed at one must never replace it.
    """
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        existing = None
    if not isinstance(existing, dict) or existing.get("result") != "verify-only":
        raise LoadError(f"refusing to overwrite non-verification receipt {path}; "
                        "choose another --receipt path")


def verify_target(plan_path, *, url, token, branch=None, receipt_path=None):
    """Read-only strict verification of a target against a frozen plan.

    Runs the same inventory readback, attribute/reference comparison, cable
    traces and component-placement checks as a load's final gate, with zero
    writes. Works against main (no branch) or one ready branch — the
    acceptance check for any seeding path that bypasses the loader, such as a
    database-restore-seeded instance. Records what was checked in a
    verification receipt when a path is given.
    """
    started = time.monotonic()
    if receipt_path is not None:
        _verify_receipt_guard(Path(receipt_path))
    plan_path, raw, plan, objects, _offline = _artifact(plan_path)
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    branch_row = _branch(client, branch) if branch else None
    inventory = fetch_inventory(client.base, client.token,
                                (obj["kind"] for obj in plan["objects"]),
                                client.branch_id, fields_by_kind=_readback_fields(plan), workers=4)
    # On a loaded target the plan's own rows are present, so builtin factory
    # rows are the per-row extras beyond the plan (extras_only).
    allowed_existing = _bootstrap_allowlist(plan, inventory, extras_only=True)
    verification = verify_plan(plan, inventory, strict_inventory=True,
                               allow_existing_receipt=allowed_existing or None)
    result = {"verified_at": _now(), "result": "verify-only", "artifact": str(plan_path),
              "receipt_version": RECEIPT_VERSION, "compiler_version": COMPILER_VERSION,
              "plan_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": digest(plan),
              "target": client.base, "branch": branch,
              "branch_id": branch_row["schema_id"] if branch_row else None,
              "netbox": status.get("netbox-version"),
              "allowed_existing": allowed_existing,
              # Unlike a load's expected-mismatch preflight, mismatches here
              # are the deliverable: persist the full list.
              "verification": verification}
    success = verification["success"]
    if success:
        # verify_plan's id map holds {"id": N, ...} rows; downstream checks
        # expect the loader's plain-int shape.
        ids = {key: value["id"] if isinstance(value, dict) else value
               for key, value in verification["ids"].items()}
        result["computed_paths"] = _verify_paths(client, plan, objects, ids)
        result["component_caches"] = _verify_component_caches(client, objects, ids)
        success = (not result["computed_paths"]["failures"]
                   and not result["component_caches"]["failures"])
    result.update(success=success, wall_seconds=round(time.monotonic() - started, 6))
    if receipt_path is not None:
        _write_receipt(Path(receipt_path), result)
    return result


def load(plan_path, *, url, token, branch, receipt_path, timeout=900,
         delivery_policy="reviewable", max_job_rows=DEFAULT_JOB_ROWS):
    """Load one frozen artifact into a branch and strictly read it back."""
    started_at = _now()
    overall = time.monotonic()
    if (not isinstance(max_job_rows, int) or isinstance(max_job_rows, bool)
            or not 1 <= max_job_rows <= MAX_JOB_ROWS):
        raise LoadError(
            f"TurboBulk maximum job rows must be an integer from 1 through {MAX_JOB_ROWS}: "
            "TurboBulk's JSONL reader fixes the column set from the first chunk, so a "
            "sparse payload spanning chunks can silently drop columns")
    plan_path, raw, plan, objects, offline = _artifact(plan_path)
    unsupported = sorted({obj["kind"] for obj in objects.values()} - SPECS.keys())
    if unsupported:
        raise LoadError("TurboBulk compiler does not yet cover canonical kinds: " + ", ".join(unsupported))
    delivery = delivery_contract(delivery_policy)
    if delivery_policy == "disposable-baseline":
        blocker = disposable_rest_blocker(objects)
        if blocker:
            raise LoadError(blocker)
    phases = list(_phases(objects))
    job_schedule = _job_request_schedule(
        phases, objects, max_job_rows, delivery["request_settings"])
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
               "transport": "turbobulk+rest", "target_contract": target_contract,
               "delivery_policy": delivery_policy,
               "delivery_warning": delivery["warning"],
               "branch_capabilities": delivery["branch_capabilities"],
               "turbobulk_request_settings": delivery["request_settings"],
               "turbobulk_max_job_rows": max_job_rows}

    receipt = None
    history_preflight = None
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; choose a new receipt and fresh branch")
        for entry in receipt.get("jobs", []):
            if entry.get("mode") != "insert":
                raise LoadError(
                    f"receipt job {entry.get('purpose', '<unknown>')} has a different submission mode; "
                    "choose a new receipt and fresh branch"
                )
            expected_settings = job_schedule.get(entry.get("purpose"))
            if expected_settings is None:
                raise LoadError(
                    f"receipt job {entry.get('purpose', '<unknown>')} is outside the current batch schedule; "
                    "choose a new receipt and fresh branch"
                )
            _require_job_settings(entry, expected_settings)

    if delivery_policy == "reviewable":
        if receipt is None:
            history_preflight = _review_history_preflight(client, branch_row, objects)
        else:
            history_preflight = receipt.get("review_history_preflight")
            if (not isinstance(history_preflight, dict)
                    or history_preflight.get("branch_id") != branch_row["id"]
                    or history_preflight.get("initial_count") != 0
                    or set(history_preflight.get("object_types", {}))
                    != set(_expected_change_diff_counts(objects))
                    or any(type(value) is not int
                           for value in history_preflight.get("object_types", {}).values())):
                raise LoadError(
                    "receipt does not prove an empty initial ChangeDiff history; "
                    "choose a new receipt and fresh branch"
                )

    if delivery_policy == "disposable-baseline" and receipt is None:
        _, changes = client.request("/api/core/object-changes/?limit=1", branch=True)
        if changes.get("count", len(changes.get("results", []))):
            raise LoadError(
                "disposable-baseline requires a fresh empty branch with no recorded changes; "
                "it cannot be reviewed, merged, or reverted"
            )

    # NetBox 4.6 lacks module bay types entirely. Prove any REST-only model is
    # writable before inventory reads or target writes so exactness fails clearly.
    if {obj["kind"] for obj in objects.values()} & REST_CREATE_KINDS:
        _rest_schema_preflight(client, objects)

    # A complete matching target is the strongest idempotency signal and needs no writes.
    readback_started = time.monotonic()
    inventory = fetch_inventory(client.base, client.token, (obj["kind"] for obj in plan["objects"]),
                                client.branch_id, fields_by_kind=_readback_fields(plan), workers=4)
    # The receipt's allowlist is a load-time snapshot; another namespace may
    # have added its own main-scoped rows since (they are not branch-isolated).
    # Excuse current foreign extras exactly like verify_target does — plan-
    # matched rows keep full strictness — and keep the union as evidence.
    allowed_now = _merge_allowlists((receipt or {}).get("allowed_existing") or {},
                                    _bootstrap_allowlist(plan, inventory, extras_only=True))
    existing = verify_plan(plan, inventory, strict_inventory=True,
                           allow_existing_receipt=allowed_now or None)
    if receipt is not None and allowed_now:
        receipt["allowed_existing"] = allowed_now
    preflight_readback_seconds = round(time.monotonic() - readback_started, 6)
    if existing["success"]:
        if receipt is not None:
            recovered = not receipt.get("success")
            receipt.setdefault("attempts", []).append({"started_at": started_at, "success": False})
            _write_receipt(receipt_path, receipt)
        try:
            for entry in receipt.get("jobs", []) if receipt else []:
                if (entry.get("request_verified") is True
                        or entry.get("superseded")
                        or entry.get("purpose", "").startswith("finalize:")):
                    continue
                job = _poll(client, _bound_job_id(entry), timeout)
                if _is_worker_death_row(job):
                    # The exact strict readback above is the strongest signal;
                    # arbitration still proves the orphaned insert committed
                    # before the entry is adopted as verified. This path never
                    # resubmits, so a rolled-back verdict must refuse without
                    # persisting a supersede a rerun would silently skip.
                    verdict, evidence = _arbitrate_worker_death(client, receipt, entry)
                    if verdict != "committed":
                        raise LoadError(
                            f"orphaned job for {entry['model']} reads rolled back yet the "
                            "target matches the artifact exactly; use a fresh branch and receipt")
                    _record_observed(entry, job)
                    entry["adopted_after_worker_death"] = evidence
                    entry["request_verified"] = True
                    _write_receipt(receipt_path, receipt)
                    continue
                try:
                    _record_terminal(entry, job)
                finally:
                    _write_receipt(receipt_path, receipt)
            current_ids = _numeric_ids(existing["ids"])
            if receipt is not None:
                _complete_rest(client, plan, objects, current_ids, receipt, receipt_path)
            if receipt is not None and any(
                    not job.get("purpose", "").startswith("finalize:")
                    for job in receipt.get("jobs", [])):
                _run_finalizers(client, branch, objects, receipt, receipt_path, timeout,
                                delivery["request_settings"])
            checkpoint_ids = _numeric_ids(receipt.get("resolved_ids", {})) if receipt else {}
            changed_ids = sorted(key for key, value in checkpoint_ids.items()
                                 if current_ids.get(key) != value)
            if changed_ids:
                raise LoadError(f"{len(changed_ids)} checkpoint target IDs changed despite exact natural-key readback; use a fresh branch and receipt")
            paths = _verify_paths(client, plan, objects, current_ids)
            if paths["failures"]:
                raise LoadError(f"computed-path readback failed for {len(paths['failures'])} of {paths['cables_expected']} cables")
            component_caches = _verify_component_caches(client, objects, current_ids)
            if component_caches["failures"]:
                raise LoadError(
                    f"component-cache readback failed for {len(component_caches['failures'])} placements"
                )
            review_history = (_verify_review_history(client, branch_row, objects, history_preflight)
                              if delivery_policy == "reviewable" else None)
            observation = {"observed_at": _now(), "wall_seconds": round(time.monotonic() - overall, 6),
                           "readback_seconds": preflight_readback_seconds,
                           "verification": existing, "computed_paths": paths,
                           "component_caches": component_caches,
                           "review_history": review_history}
        except BaseException as exc:
            if receipt is not None:
                _record_failure(receipt, receipt_path, receipt.get("resolved_ids", {}), overall, exc)
            raise
        if receipt is not None:
            receipt["attempts"][-1].update(
                completed_at=observation["observed_at"],
                wall_seconds=observation["wall_seconds"],
                result="recovered-matched" if recovered else "already-matched",
                success=True)
            receipt.setdefault("repeat_verifications", []).append(observation)
            receipt["last_verified_at"] = observation["observed_at"]
            receipt["verification"] = existing
            receipt["computed_paths"] = paths
            receipt["component_caches"] = component_caches
            receipt["review_history"] = review_history
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
                       "preflight": {"strict_existing_readback": _bounded_readback(existing),
                                     "readback_seconds": preflight_readback_seconds},
                       "computed_paths": paths, "component_caches": component_caches,
                       "jobs": [], "rest_batches": [],
                       "rest_creates": [],
                       "review_history_preflight": history_preflight,
                       "review_history": review_history,
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
        observed_ids = _numeric_ids(existing.get("ids", {}))
        bound_keys = {key for job in receipt.get("jobs", []) if job.get("job_id")
                      for key in job.get("canonical_keys", [])}
        uncheckpointed = sorted(set(observed_ids) - set(ids) - bound_keys)
        if uncheckpointed:
            raise LoadError(
                f"found {len(uncheckpointed)} uncheckpointed identities; "
                "discard this branch and start with a new branch and receipt"
            )
        changed_ids = sorted(key for key in set(observed_ids) & set(ids)
                             if observed_ids[key] != ids[key])
        if changed_ids:
            raise LoadError(
                f"{len(changed_ids)} checkpoint target IDs changed; use a fresh branch and receipt"
            )
        ids.update({key: value for key, value in observed_ids.items()
                    if key in bound_keys})
        receipt.setdefault("jobs", [])
        receipt.setdefault("rest_batches", [])
        receipt.setdefault("rest_creates", [])
        receipt.setdefault("attempts", []).append({"started_at": started_at, "success": False})
    else:
        allowed_existing = _bootstrap_allowlist(plan, inventory)
        occupied = {kind: len(rows) for kind, rows in inventory.items()
                    if rows and kind not in allowed_existing.get("target_ids", {})}
        if occupied:
            parts = []
            for kind, count in sorted(occupied.items()):
                part = f"{kind}={count} (at {SPECS[kind][1]})"
                if kind in ALLOWLISTED_KINDS:
                    conflicts = _colliding_rows(plan, inventory.get(kind, []), kind)
                    part += f"; colliding rows {conflicts}"
                parts.append(part)
            raise LoadError("fresh load requires empty inventories for every emitted kind; "
                            + ", ".join(parts)
                            + "; delete only the colliding rows where named (other rows "
                            "belong to other namespaces), or use a fresh target")
        ids = {}
        receipt = {**binding, "started_at": started_at, "success": False, "target_status": status,
                   "offline_checks": offline, "jobs": [], "rest_batches": [], "resolved_ids": {},
                   "allowed_existing": allowed_existing,
                   "rest_creates": [],
                   "review_history_preflight": history_preflight,
                   "attempts": [{"started_at": started_at, "success": False}]}
    receipt["preflight"] = {"strict_existing_readback": _bounded_readback(existing),
                            "readback_seconds": preflight_readback_seconds,
                            "branch": {key: branch_row.get(key) for key in ("id", "name", "schema_id", "status")}}
    _write_receipt(receipt_path, receipt)

    try:
        receipt["preflight"]["transport"] = _schema_preflight(client, objects)
        _write_receipt(receipt_path, receipt)
        service_shape = receipt["preflight"]["transport"].get("service_shape") or "protocol_ports"
        content_types = _content_types(client, _required_content_types(objects))
        for phase_number, keys in enumerate(phases, 1):
            grouped = defaultdict(list)
            for key in keys:
                grouped[objects[key]["kind"]].append(objects[key])
            for kind in sorted(grouped):
                candidates = grouped[kind]
                purpose = f"phase-{phase_number}:{kind}"
                pending = [obj for obj in candidates if obj["key"] not in ids]
                if pending and kind in REST_CREATE_KINDS:
                    _create_rest(client, kind, pending, ids, receipt, receipt_path, objects)
                elif candidates:
                    _load_model_batches(
                        client, branch, kind, candidates, objects, ids, content_types,
                        service_shape, purpose, receipt, receipt_path, timeout, max_job_rows,
                        delivery["request_settings"])
                if kind == "cable":
                    termination_purpose = purpose + ":terminations"
                    terminations = []
                    for obj in candidates:
                        for side, field in (("A", "a"), ("B", "b")):
                            target = objects[obj["refs"][field]]
                            terminations.append({"cable_id": ids[obj["key"]], "cable_end": side,
                                                 "termination_type_id": content_types[target["kind"]],
                                                 "termination_id": ids[target["key"]]})
                    _load_termination_batches(
                        client, branch, terminations, termination_purpose, receipt, receipt_path,
                        timeout, max_job_rows, delivery["request_settings"])
                receipt["resolved_ids"] = ids
                _write_receipt(receipt_path, receipt)

        _complete_rest(client, plan, objects, ids, receipt, receipt_path)
        _run_finalizers(client, branch, objects, receipt, receipt_path, timeout,
                        delivery["request_settings"])
        readback_started = time.monotonic()
        inventory = fetch_inventory(client.base, client.token, (obj["kind"] for obj in plan["objects"]),
                                client.branch_id, fields_by_kind=_readback_fields(plan), workers=4)
        # Main-scoped rows another namespace loads DURING this run are visible
        # at readback but absent from the preflight snapshot: excuse current
        # foreign extras exactly like the preflight and verify_target do, and
        # keep the union as receipt evidence. Plan-matched rows stay strict.
        receipt["allowed_existing"] = _merge_allowlists(
            receipt.get("allowed_existing") or {},
            _bootstrap_allowlist(plan, inventory, extras_only=True))
        verification = verify_plan(plan, inventory, strict_inventory=True,
                                   allow_existing_receipt=receipt.get("allowed_existing") or None)
        receipt["verification"] = verification
        receipt["readback_seconds"] = round(time.monotonic() - readback_started, 6)
        if not verification["success"]:
            raise LoadError(f"strict readback found {verification['mismatch_count']} mismatches")
        receipt["computed_paths"] = _verify_paths(client, plan, objects, ids)
        if receipt["computed_paths"]["failures"]:
            raise LoadError(f"computed-path readback failed for {len(receipt['computed_paths']['failures'])} of {receipt['computed_paths']['cables_expected']} cables")
        receipt["component_caches"] = _verify_component_caches(client, objects, ids)
        if receipt["component_caches"]["failures"]:
            raise LoadError(
                f"component-cache readback failed for {len(receipt['component_caches']['failures'])} placements"
            )
        receipt["review_history"] = (_verify_review_history(
            client, branch_row, objects, history_preflight)
                                     if delivery_policy == "reviewable" else None)
        receipt.pop("error", None)
        receipt.pop("failed_at", None)
        receipt.update(success=True, result="loaded", completed_at=_now(),
                       wall_seconds=round(time.monotonic() - overall, 6), resolved_ids=ids)
        receipt["attempts"][-1].update(success=True, result="loaded",
                                       completed_at=receipt["completed_at"],
                                       wall_seconds=receipt["wall_seconds"])
        _write_receipt(receipt_path, receipt)
        return receipt
    except BaseException as exc:
        _record_failure(receipt, receipt_path, ids, overall, exc)
        raise


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Load one frozen Genial plan through TurboBulk plus bounded REST completion")
    parser.add_argument("artifact", help="generated directory or plan.json")
    parser.add_argument("--target", default=os.environ.get("NETBOX_URL"))
    parser.add_argument("--branch", required=True, help="ready disposable NetBox branch name")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-job-rows", type=int, default=DEFAULT_JOB_ROWS,
                        help=f"maximum rows per TurboBulk job (default: {DEFAULT_JOB_ROWS})")
    parser.add_argument("--delivery-policy", choices=DELIVERY_POLICIES, default="reviewable")
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not args.target or not token:
        parser.error("--target/NETBOX_URL and NETBOX_TOKEN are required")
    try:
        if args.delivery_policy == "disposable-baseline":
            print("DISPOSABLE BASELINE: this branch cannot be reviewed, merged, or reverted; delete it after use.",
                  file=os.sys.stderr, flush=True)
        result = load(args.artifact, url=args.target, token=token, branch=args.branch,
                      receipt_path=args.receipt, timeout=args.timeout,
                      delivery_policy=args.delivery_policy, max_job_rows=args.max_job_rows)
        print(json.dumps({"success": True, "result": result["result"], "transport": result["transport"],
                          "objects": result["verification"]["matched_objects"],
                          "receipt": str(args.receipt)}, sort_keys=True))
        return 0
    except (LoadError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Load failed: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
