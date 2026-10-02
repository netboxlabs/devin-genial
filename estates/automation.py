"""Automation inventory every estate carries: config contexts, export templates,
one webhook and one event rule.

These four NetBox models have no entity in the pinned Diode SDK 1.14.0
IngestRequest, so the TurboBulk/REST loader is the only transport that delivers
them; `estates/diode.py` excludes them from the wire package and records the
omission in its manifest (LOADER_ONLY_KINDS).

Nothing here executes. Config-context data is documentation intent, never
applied configuration. None of these four models exposes a REST-writable
``comments`` field in the pinned 4.7 serializers, so their descriptions read
operationally and this limitation is stated here and in docs/modeling.md; the export templates are template text for NetBox to
render on request; the webhook points at a host in the reserved `.invalid`
top-level domain (RFC 2606) and its event rule ships disabled, so the estate
dispatches nothing.

Display naming splits this module in two (estates/naming.py): the export
templates, webhook and event rule are Branching-exempt rows that land on main,
so on a shared tenant they keep the "<namespace> …" name `just retire`
matches (a dedicated tenant drops it; see naming.main_scoped_name).  Config contexts
are branch-scoped — `get_branchable_object_types()` lists extras.configcontext
— so they go with the branch and carry an authored, namespace-free name.
"""

from collections import defaultdict

from .model import DesignError
from .naming import main_scoped_name


# Service hosts recorded per workload. Two servers is a believable list, and
# the cap keeps the emitted context stable while an estate grows: new service
# instances take later ordinals and sort after these. The cap counts HOSTS, not
# addresses, so a dual-stack estate lists both families of the same two hosts
# rather than one host twice.
HOSTS_PER_WORKLOAD = 2
# Scope for the role-weighted context: every switching role, in preference
# order, that at least one device in the estate actually holds. A role with no
# devices is never targeted (a provider estate has management switches, not
# access switches). The same set marks the switch side of an access link
# (operations._host_ports): only these devices' ports carry an 802.1Q mode.
SWITCH_ROLES = ("role/access", "role/management", "role/distribution", "role/stack",
                "role/leaf", "role/spine")
WEBHOOK_URL = "https://hooks.internal.invalid/netops"
EVENT_TYPES = ["object_created", "object_updated"]

# Both templates render in NetBox's own Jinja2 environment and read only
# attribute paths the pinned 4.7.1 models expose: verified read-only on the
# local stack with utilities.jinja2.render_jinja2 over a stand-in queryset and
# an empty one, plus Device/Cable field and property checks. NetBox renders
# them on demand from `queryset` (extras ExportTemplate.get_context); Genial
# never executes them, and the offline validator checks structure only, because
# the runtime imports no Jinja2.
DEVICE_TEMPLATE = """name,site,role,type,serial,status
{% for device in queryset %}{{ device.name }},{{ device.site.name }},\
{{ device.role.name }},{{ device.device_type.model }},{{ device.serial }},{{ device.status }}
{% endfor %}"""

CABLE_TEMPLATE = """label,type,status,length,a_terminations,b_terminations
{% for cable in queryset %}{{ cable.label }},{{ cable.type }},{{ cable.status }},\
{{ cable.length }},{{ cable.a_terminations|join(" ") }},{{ cable.b_terminations|join(" ") }}
{% endfor %}"""


def _service_addresses(world):
    """Addresses this estate's own service listeners bind, keyed by workload.

    Derived from the finished graph — the service record, its VM and the IPs the
    listener is bound to — so a config context can only cite addresses the
    estate actually serves. Hosts keep their canonical order, so growth appends
    instead of rerolling. Independent checks repeat this derivation.
    """
    by_workload = defaultdict(dict)
    for service in sorted((o for o in world.objects.values() if o["kind"] == "service"),
                          key=lambda o: o["key"]):
        vm = service["refs"].get("virtual_machine", "")
        parts = vm.split("/")
        if len(parts) != 4:
            continue
        hosts = by_workload[parts[2]]
        for key in service["refs"].get("ipaddresses", []):
            address = world.obj(key)["attrs"]["address"].split("/")[0]
            if address not in hosts.setdefault(vm, []):
                hosts[vm].append(address)
    return {workload: [address for host in list(hosts)[:HOSTS_PER_WORKLOAD]
                       for address in hosts[host]]
            for workload, hosts in sorted(by_workload.items())}


def _context_data(namespace, endpoints):
    """The global context's payload, entirely derived from the estate."""
    data = {"domain": f"{namespace}.example",
            "service_endpoints": {workload.replace("-", "_"): list(addresses)
                                  for workload, addresses in endpoints.items()}}
    if endpoints.get("dns"):
        # The resolver list automation reads; the same addresses the estate's
        # own DNS listeners bind, never an invented server.
        data["dns_servers"] = list(endpoints["dns"])
    return data


def enrich(world):
    """Attach the automation records after every service and role exists."""
    ns = world.recipe["namespace"]
    owner = "owner/operations"
    if world.objects.get(owner, {}).get("kind") != "owner":
        raise DesignError("Automation records need the estate's shared operations owner")

    def add(kind, key, attrs, refs=None):
        return world.add(kind, key, attrs, {"owner": owner, **(refs or {})},
                         {"operations": True, "automation": True})

    endpoints = _service_addresses(world)
    add("config_context", "config-context/global",
        {"name": "Global service baseline", "weight": 100, "is_active": True,
         "description": "Service endpoints and DNS resolvers for automation tooling",
         "data": _context_data(ns, endpoints)})

    held = {obj["refs"].get("role") for obj in world.objects.values() if obj["kind"] == "device"}
    roles = [role for role in SWITCH_ROLES if role in held and role in world.objects]
    if not roles:
        raise DesignError("Automation context needs a switching role some device in the estate holds")
    add("config_context", "config-context/switching",
        {"name": "Switch platform baseline", "weight": 1000, "is_active": True,
         "description": "Switch logging, edge-port and management defaults for config templates",
         "data": {"logging": {"severity": "informational"},
                  "ports": {"disable_unused": True, "edge_protection": True},
                  "management": {"in_band_vlan_only": True}}},
        {"roles": roles})

    add("export_template", "export-template/device-inventory",
        {"name": main_scoped_name(world.recipe, "Device inventory (CSV)"), "object_types": ["dcim.device"],
         "template_code": DEVICE_TEMPLATE, "mime_type": "text/csv",
         "file_extension": "csv", "as_attachment": True,
         "description": "One row per device with its site, role, type and serial"})
    add("export_template", "export-template/cable-report",
        {"name": main_scoped_name(world.recipe, "Cable report (CSV)"), "object_types": ["dcim.cable"],
         "template_code": CABLE_TEMPLATE, "mime_type": "text/csv",
         "file_extension": "csv", "as_attachment": True,
         "description": "One row per cable with its media, status, length and both termination lists"})

    webhook = add("webhook", "webhook/netops",
                  {"name": main_scoped_name(world.recipe, "NetOps automation endpoint"), "payload_url": WEBHOOK_URL,
                   "http_method": "POST", "http_content_type": "application/json",
                   "ssl_verification": True,
                   "description": "NetOps automation receiver"})
    add("event_rule", "event-rule/device-change",
        {"name": main_scoped_name(world.recipe, "Device change notification"), "object_types": ["dcim.device"],
         "event_types": list(EVENT_TYPES), "action_type": "webhook", "enabled": False,
         "description": "Notify NetOps automation of device changes; disabled until the receiver is live"},
        {"action_object": webhook})
