"""One property-selected discovery-drift twin of a documented estate.

NetBox Assurance compares Diode-ingested observation against documented NetBox
state, so it cannot demonstrate anything on flawless data. This writes the
missing half: a bounded "observed" Diode payload expressing only the drifted
records, the exact expected deviation set, and an operator walkthrough.

Nothing here contacts a target, mutates the bound baseline artifact, or claims
live Assurance behaviour. What an ingest *should* produce is derived from the
public Diode changeset contract; what Assurance actually does with it requires
target-side evidence under the Cloud qualification protocol.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from ipaddress import ip_address as _ip, ip_interface, ip_network
import re

from . import __version__
from .diode import SDK_VERSION, _References, _deferred_fields, _index
from .model import DesignError, canonical, digest
from .power_scenario import _healthy as _healthy_plan
from .report import _cell, _table


ARTIFACT = "discovery-drift"
LABEL = "Drift twin"

# Our own review labels for the four behaviours NetBox Labs advertises Assurance
# detecting (netboxlabs.com/docs/assurance/, read 2026-09-19). They are *not*
# product deviation-type names: Diode generates a deviation type per object type
# from its change type, so the wire-level prediction below is change_type.
CLASSES = ("undocumented-object", "drift-vs-intent", "documented-not-observed", "data-quality")

# Diode's reconciler defines exactly three change types and no deletion:
# netboxlabs/diode diode-server/reconciler/changeset/changeset.go (read 2026-09-19).
CHANGE_TYPES = ("create", "update")

# The Assurance-equipped target runs NetBox 4.6, so the payload is restricted to
# models that exist there. This is exactly the set the current writer reaches, in
# emitted records and in nested matching identities alike; every one of them long
# predates 4.6. It stays tight on purpose - a kind that appears here for the first
# time fails the check by name and gets a version review rather than a silent pass.
NETBOX_46_KINDS = frozenset({
    "device", "device_role", "device_type", "interface", "ip_address", "location",
    "manufacturer", "rack", "site", "tag", "tenant", "tenant_group", "vlan",
    "vlan_group", "vrf",
})
NETBOX_47_ONLY_KINDS = frozenset({
    "cooling_source", "cooling_feed", "cooling_intake", "cooling_outflow", "module_bay_type",
})

SOURCES = [
    "https://netboxlabs.com/docs/assurance/",
    "https://netboxlabs.com/docs/assurance/using-the-ui/",
    "https://netboxlabs.com/docs/enterprise/helm/configuration/diode/",
    "https://github.com/netboxlabs/diode/blob/develop/diode-server/reconciler/changeset/changeset.go",
    "https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/sdk/diode/v1/ingester_pb2.pyi",
    "https://github.com/netboxlabs/diode-netbox-plugin/blob/develop/docs/matching-criteria-documentation.md",
    "https://github.com/netbox-community/netbox/blob/main/docs/release-notes/version-4.7.md",
]

TARGET = {
    "netbox": "4.6", "diode_sdk": SDK_VERSION, "diode_netbox_plugin": "1.17.0",
    "assurance": "unverified", "live_verified": False,
}
EXECUTION = {
    "transition": "ingest-only", "applied_to_target": False,
    "observed_payload": "create/update entities only",
    "assurance_deviations": "predicted-offline-unverified",
}
LIMITATIONS = [
    "Expected deviations are what this ingest should produce against the bound documented baseline. "
    "Live Assurance behaviour - deviation creation, matching outcomes, review versus auto-apply - requires target-side evidence.",
    "Diode expresses creates and updates only. Its IngestRequest carries no absence, tombstone or delete signal, "
    "so documented-not-observed items are named for the operator and are deliberately absent from observed/.",
    "Assurance is licensed on Cloud/Enterprise. This generator has never run against an Assurance-equipped target; "
    "no ingest acknowledgement, deviation count or diff rendering is claimed here.",
    "The observed payload is a Diode ingest artifact, not a loadable estate: it has no plan.json, fails ordinary "
    "validation by construction, and must never be pushed through the TurboBulk loader.",
    "Observed addresses and VLAN IDs are unused in the bound baseline but are not reserved by the estate allocator; "
    "later growth may legitimately claim them. Regenerate the drift twin after growing the estate.",
    "Undocumented objects cannot be withdrawn by re-ingesting documented state. Clearing them requires target-side removal.",
    "Drift is confined to one selected site so the walkthrough stays inspectable; it is not an estate-wide error rate.",
]


def _require(condition, message):
    if not condition:
        raise DesignError(f"{LABEL}: {message}")


def _graph(plan):
    objects = {obj["key"]: obj for obj in plan["objects"]}
    children = defaultdict(list)
    for key, obj in objects.items():
        for field, value in obj["refs"].items():
            for target in (value if isinstance(value, list) else [value]):
                children[(field, target)].append(key)
    for rows in children.values():
        rows.sort()
    return objects, children


def _token(*parts):
    return digest(["drift", __version__, *parts])


def _natural(key):
    """Numeric-aware ordering: Gi1/0/2 sorts before Gi1/0/10, so growth that
    creates higher-numbered ports never reshuffles the first picks."""
    return [int(piece) if piece.isdigit() else piece
            for piece in re.split(r"(\d+)", key)]


def _ports(objects, children, device):
    """Enabled, described, VLAN-bearing physical access ports, mgmt excluded."""
    return sorted((key for key in children[("device", device)]
                   if objects[key]["kind"] == "interface"
                   and objects[key]["attrs"].get("enabled") is True
                   and not objects[key]["attrs"].get("mgmt_only")
                   and objects[key]["attrs"].get("type") not in (None, "virtual", "lag", "bridge")
                   and objects[key]["attrs"].get("description")
                   and isinstance(objects[key]["refs"].get("untagged_vlan"), str)),
                  key=_natural)


def _addressed(objects, key):
    """The device's own primary IPv4 and the interface carrying it, or None."""
    address = objects[key]["refs"].get("primary_ip4")
    if not isinstance(address, str) or objects.get(address, {}).get("kind") != "ip_address":
        return None
    interface = objects[address]["refs"].get("assigned_object")
    if objects.get(interface, {}).get("kind") != "interface" or objects[interface]["refs"].get("device") != key:
        return None
    return {"ip": address, "interface": interface}


def _endpoint_ranks(plan, site_id):
    """Append-only room-ledger slots for this site's access endpoints.

    Profiles built on stable campus access reserve every endpoint a permanent
    physical port slot; ordering by that slot keeps drift subjects fixed under
    in-place growth (new pods and rooms take higher slots, never earlier ones).
    Estates without a ledger fall back to name order, which is stable there
    because their in-place endpoint growth is rebaseline-frozen.
    """
    ranks = {}
    for scope, items in plan.get("reservations", {}).items():
        if scope.startswith(f"access-endpoints/{site_id}/"):
            for endpoint, slot in items.items():
                if isinstance(slot, int):
                    ranks[endpoint] = min(slot, ranks.get(endpoint, slot))
    return ranks


def _site_subjects(objects, children, site, ranks):
    """Every drift subject this site offers, or None when one is missing."""
    switches = [key for key in children[("site", site)]
                if objects[key]["kind"] == "device" and objects[key]["refs"].get("role") == "role/access"
                and objects[key]["attrs"].get("status") == "active" and objects[key]["attrs"].get("serial")
                and _addressed(objects, key) and len(_ports(objects, children, key)) >= 4]
    endpoints = [key for key in children[("site", site)]
                 if objects[key]["kind"] == "device" and objects[key]["meta"].get("endpoint")
                 and objects[key]["attrs"].get("status") == "active" and objects[key]["attrs"].get("serial")
                 and _addressed(objects, key)]
    if not switches or len(endpoints) < 4:
        return None
    # Subjects order by append-only room-ledger slot where one exists (natural
    # name order otherwise), so in-place growth at the anchor site keeps the
    # same picks: new endpoints take higher slots and new ports higher numbers.
    switches.sort(key=_natural)
    endpoints.sort(key=lambda key: (ranks.get(key, float("inf")), _natural(key)))
    switch = switches[0]
    management = _addressed(objects, switch)
    ports = _ports(objects, children, switch)[:4]
    vlan = objects[ports[1]]["refs"]["untagged_vlan"]
    scope = "group" if "group" in objects[vlan]["refs"] else "site"
    if scope not in objects[vlan]["refs"]:
        return None
    return {"site": site, "switch": switch, "management_ip": management["ip"],
            "management_interface": management["interface"],
            "drift_ports": ports[:3], "unobserved_port": ports[3],
            "vlan_scope_field": scope, "vlan_scope": objects[vlan]["refs"][scope],
            "endpoints": endpoints[:4]}


def _select(plan, objects, children):
    # ponytail: permanent allocation order, not lexicographic. Site reservations
    # are append-only, so growing the estate never moves the anchor site onto a
    # newer one; lexicographic order would, the first time a "br-m…" appeared
    # beside an existing "br-s…".
    allocations = plan.get("allocations", {})
    sites = sorted((key for key, obj in objects.items() if obj["kind"] == "site"),
                   key=lambda key: (allocations.get(key.removeprefix("site/"), float("inf")), key))
    for site in sites:
        subjects = _site_subjects(objects, children, site,
                                  _endpoint_ranks(plan, site.removeprefix("site/")))
        if subjects is not None:
            return subjects
    raise DesignError(
        f"{LABEL}: no site offers an active access switch with four drift-eligible ports and four addressed "
        "endpoint devices. Data-center-only estates (enterprise-data-center) model fabric, not campus access; "
        "generate a baseline from a profile with branch, campus, office or plant access - regional-bank, "
        "school-district, hospital-clinics, provider-backbone, retail-chain, university-campus, msp, "
        "manufacturing or utility.")


def _holding_prefix(objects, address, vrf):
    """The most specific documented prefix containing this address in its VRF."""
    host = ip_interface(address).ip
    best, best_length = None, -1
    for key in sorted(objects):
        obj = objects[key]
        if obj["kind"] != "prefix" or obj["refs"].get("vrf") != vrf:
            continue
        network = ip_network(obj["attrs"]["prefix"])
        if network.version == host.version and host in network and network.prefixlen > best_length:
            best, best_length = key, network.prefixlen
    return best


def _unused_address(objects, documented, vrf, taken):
    """Highest unused host in the documented prefix holding `documented`.

    Descending keeps the choice stable while the estate's own allocators append
    upward. Documented addresses and documented ranges are both avoided; the
    result is still not an allocator reservation, only an unused address today.
    """
    holder = _holding_prefix(objects, documented, vrf)
    _require(holder is not None, f"no documented prefix in this VRF contains {documented}; the observed payload "
                                 "must place a discovered address inside documented space")
    network = ip_network(objects[holder]["attrs"]["prefix"])
    _require(network.prefixlen <= 30 and network.num_addresses >= 8,
             f"documented prefix {network} is too small to hold an undiscovered host address")
    used = set(taken)
    for obj in objects.values():
        if obj["kind"] == "ip_address" and obj["refs"].get("vrf") == vrf:
            used.add(ip_interface(obj["attrs"]["address"]).ip)
    ranges = [(ip_interface(obj["attrs"]["start_address"]).ip, ip_interface(obj["attrs"]["end_address"]).ip)
              for obj in objects.values() if obj["kind"] == "ip_range" and obj["refs"].get("vrf") == vrf]
    for offset in range(1, 257):
        candidate = _ip(int(network.broadcast_address) - offset)
        if candidate <= network.network_address:
            break
        if candidate in used or any(start <= candidate <= end for start, end in ranges):
            continue
        # The candidate must still live in the SAME holding prefix: a more
        # specific documented block covering it would change the story's home.
        if _holding_prefix(objects, f"{candidate}/{network.prefixlen}", vrf) != holder:
            continue
        taken.add(candidate)
        return holder, f"{candidate}/{network.prefixlen}"
    raise DesignError(f"{LABEL}: documented prefix {network} has no unused host address in its top 256 for an "
                      "undocumented discovery record; grow the estate into a larger block or choose another baseline")


def _unused_vid(objects, children, scope_field, scope):
    used = {objects[key]["attrs"]["vid"] for key in children[(scope_field, scope)]
            if objects[key]["kind"] == "vlan"}
    for vid in range(999, 99, -1):
        if vid not in used:
            return vid
    raise DesignError(f"{LABEL}: {scope} documents every VLAN ID between 100 and 999; "
                      "an undocumented VLAN cannot be expressed without colliding with documented inventory")


def _observed_serial(key, documented):
    """A replacement chassis serial in the estate's own serial grammar."""
    match = re.fullmatch(r"([A-Za-z]+)-(\d+)", documented)
    prefix, width = (match.group(1), len(match.group(2))) if match else ("SYN", 10)
    digits = str(int(_token(key, "replacement-chassis"), 16))[:width].rjust(width, "0")
    observed = f"{prefix}-{digits}"
    _require(observed != documented, "the replacement chassis serial must differ from the documented serial")
    return observed


def _mutated(objects, key, item, attrs=None, refs=None):
    record = deepcopy(objects[key])
    record["attrs"].update(attrs or {})
    record["refs"].update(refs or {})
    # Compare the wire-bearing halves only: a meta label is not an observation.
    _require((record["attrs"], record["refs"]) != (objects[key]["attrs"], objects[key]["refs"]),
             f"{item} must change an observed field of {key}")
    record["meta"] = dict(record["meta"], drift_item=item)
    return record


def _created(key, kind, item, attrs, refs):
    return dict(key=key, kind=kind, attrs=attrs, refs=refs, meta={"drift_item": item, "undocumented": True})


def _items(objects, children, subjects):
    """Build the exact drift set: one owning item per observed record."""
    site, switch = subjects["site"], subjects["switch"]
    management_ip, management_interface = subjects["management_ip"], subjects["management_interface"]
    ports, endpoints = subjects["drift_ports"], subjects["endpoints"]
    first, second, third, fourth = endpoints
    management_vrf = objects[management_ip]["refs"].get("vrf")
    taken = set()
    items, observed = [], {}

    def emit(record):
        _require(record["key"] not in observed, f"{record['key']} is already owned by another drift item")
        observed[record["key"]] = record
        return record["key"]

    def add(identifier, deviation_class, story, deviation, records=(), fields=(), subject=None):
        _require(deviation_class in CLASSES, f"unknown deviation class {deviation_class}")
        changes = [{"record": key, "change_type": "update" if key in objects else "create"} for key in records]
        _require(all(row["change_type"] in CHANGE_TYPES for row in changes), "unknown change type")
        items.append({
            "id": identifier, "deviation_class": deviation_class,
            "detection": "diode-ingest" if changes else "requires-target-side-comparison",
            "expected_changes": changes, "object": subject,
            "kind": (objects[subject] if subject in objects else observed[subject])["kind"],
            "records": list(records), "fields": [dict(row) for row in fields],
            "documented": deepcopy(objects[subject]) if subject in objects else None,
            "story": story, "expected_deviation": deviation,
            "inverse": ("no ingest expresses this item" if not changes else
                        "; ".join(sorted({"re-ingest the documented record" if row["change_type"] == "update"
                                          else "requires target-side removal" for row in changes}))),
        })

    # 1. Undocumented object: a spare access switch patched in from stores.
    stem = re.sub(r"\d+$", "", objects[switch]["attrs"]["name"]) or objects[switch]["attrs"]["name"] + "-"
    rogue_name = f"{stem}99"
    _require(all(objects[key]["attrs"].get("name") != rogue_name for key in children[("site", site)]
                 if objects[key]["kind"] == "device"),
             f"{rogue_name} already names a documented device at this site")
    rogue = f"drift/device/{site}/undocumented-access"
    rogue_port = f"{rogue}/if/uplink"
    rogue_holder, rogue_address = _unused_address(objects, objects[management_ip]["attrs"]["address"], management_vrf, taken)
    rogue_ip = f"drift/ip/{site}/undocumented-access"
    template = objects[management_interface]
    emit(_created(rogue, "device", "undocumented-access-switch",
                  {"name": rogue_name, "status": "active",
                   "serial": _observed_serial(rogue, objects[switch]["attrs"]["serial"]),
                   "description": f"Discovered switch, not present in documented inventory at {objects[site]['attrs']['name']}"},
                  # Only what NetBox needs to create a Device plus the real
                  # documented placement. Discovery reports no estate tags.
                  {field: value for field, value in objects[switch]["refs"].items()
                   if field in ("device_type", "role", "site", "tenant", "location")} | {"primary_ip4": rogue_ip}))
    emit(_created(rogue_port, "interface", "undocumented-access-switch",
                  {"name": template["attrs"]["name"], "type": template["attrs"]["type"], "enabled": True,
                   "description": f"Discovered uplink toward {objects[switch]['attrs']['name']}"},
                  {field: value for field, value in template["refs"].items()
                   if field in ("untagged_vlan", "vrf")} | {"device": rogue}))
    emit(_created(rogue_ip, "ip_address", "undocumented-access-switch",
                  {"address": rogue_address, "status": "active",
                   "description": "Discovered management address on an undocumented switch"},
                  {field: value for field, value in objects[management_ip]["refs"].items()
                   if field in ("vrf", "tenant")} | {"assigned_object": rogue_port}))
    add("undocumented-access-switch", "undocumented-object", subject=rogue,
        records=[rogue, rogue_port, rogue_ip],
        story=(f"A spare access switch was pulled from stores during a desk move at {objects[site]['attrs']['name']} "
               f"and patched into the equipment room. Discovery reports {rogue_name} with an uplink and a management "
               "address; documented inventory has never heard of it."),
        deviation=(f"Three created objects: Device {rogue_name}, its Interface {template['attrs']['name']} and "
                   f"IP address {rogue_address}. Nothing matches documented state, so no before side exists."))

    # 2. Undocumented object: a hand-configured second management address.
    _, secondary_address = _unused_address(objects, objects[management_ip]["attrs"]["address"], management_vrf, taken)
    secondary_ip = f"drift/ip/{site}/secondary-management"
    emit(_created(secondary_ip, "ip_address", "undocumented-management-address",
                  {"address": secondary_address, "status": "active",
                   "description": f"Discovered secondary address on {objects[switch]['attrs']['name']}"},
                  {field: value for field, value in objects[management_ip]["refs"].items()
                   if field in ("vrf", "tenant")} | {"assigned_object": management_interface}))
    add("undocumented-management-address", "undocumented-object", subject=secondary_ip,
        records=[secondary_ip],
        story=(f"Someone added a second management address to {objects[switch]['attrs']['name']} during an out-of-hours "
               "change and never recorded it. Discovery reads both addresses off the interface."),
        deviation=(f"One created IP address {secondary_address} on the matched documented interface "
                   f"{objects[management_interface]['attrs']['name']}. The documented address is untouched."))

    # 3. Undocumented object: a VLAN configured on the switch but never recorded.
    vid = _unused_vid(objects, children, subjects["vlan_scope_field"], subjects["vlan_scope"])
    rogue_vlan = f"drift/vlan/{site}/undocumented-{vid}"
    emit(_created(rogue_vlan, "vlan", "undocumented-vlan",
                  {"vid": vid, "name": f"undocumented-{vid}", "status": "active",
                   "description": "Discovered VLAN, not present in documented inventory"},
                  {subjects["vlan_scope_field"]: subjects["vlan_scope"]}))
    add("undocumented-vlan", "undocumented-object", subject=rogue_vlan, records=[rogue_vlan],
        story=(f"VLAN {vid} was configured on the switch to segregate a temporary workspace. Nobody added it to "
               "the documented VLAN inventory, so capacity and segmentation reporting never saw it."),
        deviation=f"One created VLAN with VID {vid} in the documented VLAN scope.")

    # 4. Drift vs intent: the classic undocumented RMA.
    documented_serial = objects[switch]["attrs"]["serial"]
    replacement = _observed_serial(switch, documented_serial)
    emit(_mutated(objects, switch, "replaced-chassis-serial", attrs={"serial": replacement}))
    add("replaced-chassis-serial", "drift-vs-intent", subject=switch, records=[switch],
        fields=[{"field": "serial", "documented": documented_serial, "observed": replacement}],
        story=(f"{objects[switch]['attrs']['name']} failed and was swapped under RMA. The replacement kept the name, "
               "the rack position and the patching; the asset record still carries the dead chassis's serial."),
        deviation=("One changed attribute on the matched Device: serial "
                   f"{documented_serial} becomes {replacement}. Name, site, rack and role match, so the device is "
                   "updated rather than created."))

    # 5-7. Drift vs intent at port level: description, VLAN and admin state.
    described, moved, shut = ports
    observed_description = "AV-CONF-TEMP"
    emit(_mutated(objects, described, "interface-description-drift", attrs={"description": observed_description}))
    add("interface-description-drift", "drift-vs-intent", subject=described, records=[described],
        fields=[{"field": "description", "documented": objects[described]["attrs"]["description"],
                 "observed": observed_description}],
        story=("A port was repurposed for a temporary conference-room feed. The switch's port description was edited; "
               "the documented purpose was not."),
        deviation=(f"One changed attribute on the matched Interface {objects[described]['attrs']['name']}: "
                   "description."))

    documented_vlan = objects[moved]["refs"]["untagged_vlan"]
    emit(_mutated(objects, moved, "interface-vlan-drift", refs={"untagged_vlan": rogue_vlan}))
    add("interface-vlan-drift", "drift-vs-intent", subject=moved, records=[moved],
        fields=[{"field": "untagged_vlan", "documented": documented_vlan, "observed": rogue_vlan}],
        story=(f"The same temporary workspace moved this port onto the undocumented VLAN {vid}. Documented "
               f"segmentation still shows VLAN {objects[documented_vlan]['attrs']['vid']}."),
        deviation=(f"One changed reference on the matched Interface {objects[moved]['attrs']['name']}: untagged VLAN "
                   f"{objects[documented_vlan]['attrs']['vid']} becomes the created VLAN {vid}. Read it next to the "
                   "undocumented-vlan deviation; they are the same change."))

    emit(_mutated(objects, shut, "interface-disabled-drift", attrs={"enabled": False}))
    add("interface-disabled-drift", "drift-vs-intent", subject=shut, records=[shut],
        fields=[{"field": "enabled", "documented": True, "observed": False}],
        story=("A port was shut during an incident and never re-enabled. Documented state still presents it as "
               "live capacity."),
        deviation=f"One changed attribute on the matched Interface {objects[shut]['attrs']['name']}: enabled.")

    # 8. Drift vs intent: an endpoint re-addressed without an inventory update.
    first_address = _addressed(objects, first)
    documented_first_ip = first_address["ip"]
    first_vrf = objects[documented_first_ip]["refs"].get("vrf")
    _, readdressed = _unused_address(objects, objects[documented_first_ip]["attrs"]["address"], first_vrf, taken)
    readdressed_ip = f"drift/ip/{site}/readdressed-endpoint"
    emit(_created(readdressed_ip, "ip_address", "primary-address-drift",
                  {"address": readdressed, "status": "active",
                   "description": f"Discovered address on {objects[first]['attrs']['name']}"},
                  {field: value for field, value in objects[documented_first_ip]["refs"].items()
                   if field in ("vrf", "tenant")} | {"assigned_object": first_address["interface"]}))
    emit(_mutated(objects, first, "primary-address-drift", refs={"primary_ip4": readdressed_ip}))
    add("primary-address-drift", "drift-vs-intent", subject=first,
        records=[readdressed_ip, first],
        fields=[{"field": "primary_ip4", "documented": documented_first_ip, "observed": readdressed_ip}],
        story=(f"{objects[first]['attrs']['name']} was re-addressed after a swap. Documented state still points at "
               f"{objects[documented_first_ip]['attrs']['address']}."),
        deviation=(f"Two deviations from one story: IP address {readdressed} is created, and the matched Device "
                   f"{objects[first]['attrs']['name']} has its primary IPv4 changed to it. The documented address "
                   "stays on the target until it is removed there."))

    # 9-10. Documented but not observed. Diode carries no absence signal.
    unobserved_port = subjects["unobserved_port"]
    add("endpoint-not-observed", "documented-not-observed", subject=third, records=[],
        story=(f"{objects[third]['attrs']['name']} was decommissioned in a desk move and nobody retired the record. "
               "Discovery never sees it; documented inventory still bills, patches and reports on it."),
        deviation=("No deviation is produced by this payload. A Diode IngestRequest has no absence, tombstone or "
                   "delete representation, and the reconciler defines only create, update and noop change types. "
                   "Detecting it means comparing documented inventory against the observed set on the target side."))
    add("port-not-observed", "documented-not-observed", subject=unobserved_port, records=[],
        story=(f"The collector's port list for {objects[switch]['attrs']['name']} omits "
               f"{objects[unobserved_port]['attrs']['name']}. Either the hardware changed or the collector filtered "
               "it; documented state cannot tell the difference."),
        deviation=("No deviation is produced by this payload, for the same reason as endpoint-not-observed. It is "
                   "listed so the walkthrough names the class honestly instead of faking it."))

    # 11-14. Data quality: what a sloppy collector does to good inventory.
    emit(_mutated(objects, second, "blank-serial", attrs={"serial": ""}))
    add("blank-serial", "data-quality", subject=second, records=[second],
        fields=[{"field": "serial", "documented": objects[second]["attrs"]["serial"], "observed": ""}],
        story=(f"The collector could not read {objects[second]['attrs']['name']}'s serial and reported an empty "
               "string rather than omitting the field. Applying that answer erases a good asset record."),
        deviation=("One changed attribute on the matched Device: serial becomes empty. This artifact models an "
                   "explicitly empty value; a collector that omits the field entirely is a different case."))

    documented_dns = objects[management_ip]["attrs"].get("dns_name")
    _require(isinstance(documented_dns, str) and documented_dns and "." in documented_dns,
             "the selected management address needs a documented DNS name for the casing drift item")
    host, _, domain = documented_dns.partition(".")
    observed_dns = f"{host.upper()}.{domain}"
    _require(observed_dns != documented_dns, "the documented DNS name must contain a lower-case host label")
    emit(_mutated(objects, management_ip, "dns-name-case-drift", attrs={"dns_name": observed_dns}))
    add("dns-name-case-drift", "data-quality", subject=management_ip, records=[management_ip],
        fields=[{"field": "dns_name", "documented": documented_dns, "observed": observed_dns}],
        story=("One collector upper-cases hostnames. Nothing moved, nothing broke, and the inventory now disagrees "
               "with itself about what this host is called."),
        deviation=("One changed attribute on the matched IP address: dns_name casing. The address and VRF are the "
                   "matching identity, so the record is updated, not duplicated."))

    fourth_address = _addressed(objects, fourth)
    emit(_mutated(objects, documented_first_ip, "duplicate-address-appearance",
                  refs={"assigned_object": fourth_address["interface"]}))
    add("duplicate-address-appearance", "data-quality", subject=documented_first_ip,
        records=[documented_first_ip],
        fields=[{"field": "assigned_object", "documented": first_address["interface"],
                 "observed": fourth_address["interface"]}],
        story=(f"{objects[first]['attrs']['name']}'s old address was handed to {objects[fourth]['attrs']['name']} "
               "and discovery now reports it there. Read this next to primary-address-drift: it is the tail of the "
               "same re-addressing."),
        deviation=(f"One changed reference on the matched IP address {objects[documented_first_ip]['attrs']['address']}: "
                   "its interface assignment moves to another device. Diode matches an IP address on address and VRF, "
                   "so a payload cannot assert the same address twice; the reassignment is what the ingest expresses."))

    documented_fourth_serial = objects[fourth]["attrs"]["serial"]
    emit(_mutated(objects, fourth, "serial-case-drift", attrs={"serial": documented_fourth_serial.lower()}))
    add("serial-case-drift", "data-quality", subject=fourth, records=[fourth],
        fields=[{"field": "serial", "documented": documented_fourth_serial,
                 "observed": documented_fourth_serial.lower()}],
        story=("A second collector lower-cases serials. The hardware is unchanged; only the transcription differs, "
               "and every serial-keyed report now splits in two."),
        deviation=("One changed attribute on the matched Device: serial casing. Device names match case-insensitively "
                   "in NetBox and in Diode, so a case-drifted hostname would update the same device rather than "
                   "duplicate it; serials carry no such rule."))

    return items, observed


def _derive(baseline):
    """Recompute the complete drift envelope from one frozen healthy baseline."""
    _healthy_plan(baseline, LABEL)
    fingerprint = digest(baseline)
    objects, children = _graph(baseline)
    subjects = _select(baseline, objects, children)
    items, observed = _items(objects, children, subjects)

    declared = Counter(key for item in items for key in item["records"])
    _require(sorted(declared) == sorted(observed),
             "every observed record must be declared by exactly one drift item")
    _require(all(count == 1 for count in declared.values()),
             "an observed record cannot be declared by two drift items")
    _require(len({item["id"] for item in items}) == len(items), "drift item identifiers must be unique")
    _require(all(observed[key]["meta"]["drift_item"] == item["id"]
                 for item in items for key in item["records"]),
             "each observed record must carry the identifier of the drift item that declares it")
    _require(all(item["object"] in objects or item["object"] in observed for item in items),
             "every drift item must name a real documented or observed record")
    _require(all((item["deviation_class"] == "documented-not-observed") == (not item["records"])
                 for item in items),
             "only documented-not-observed items may declare no observed record, and they must declare none")
    _require(set(item["deviation_class"] for item in items) == set(CLASSES),
             "the drift set must cover every reviewed deviation class")

    created = sorted(key for key in observed if key not in objects)
    drifted = sorted(key for key in observed if key in objects)
    _require(created and drifted, "the drift set needs both created and changed observed records")

    observed_plan = deepcopy(baseline)
    observed_plan["objects"] = sorted(({key: deepcopy(obj) for key, obj in objects.items()} | observed).values(),
                                      key=lambda obj: obj["key"])
    selected = sorted(observed)
    index = _index(observed_plan)

    # Identity discipline: a drifted record must keep the documented matching
    # identity so Assurance updates it, and a created record must not collide
    # with any documented identity of the same kind.
    documented_references, observed_references = _References(_index(baseline)), _References(index)
    for key in drifted:
        _require(canonical(documented_references.thin(key)) == canonical(observed_references.thin(key)),
                 f"{key} drifts its own Diode matching identity; the ingest would create a duplicate object")
    created_kinds = {observed[key]["kind"] for key in created}
    documented_identities = defaultdict(set)
    for key, obj in objects.items():
        if obj["kind"] in created_kinds:
            documented_identities[obj["kind"]].add(canonical(documented_references.thin(key)))
    for key in created:
        _require(canonical(observed_references.thin(key)) not in documented_identities[observed[key]["kind"]],
                 f"{key} reuses a documented matching identity and would update an existing object")

    # Exactly the kinds a NetBox 4.6 target can accept, emitted and nested.
    for key in selected:
        observed_references.entity(key, "1970-01-01T00:00:00Z")
        observed_references.entity(key, "1970-01-01T00:00:00Z", primary=True)
    kinds = sorted({index[key]["kind"] for key in observed_references.cache} |
                   {observed[key]["kind"] for key in selected})
    _require(not NETBOX_46_KINDS & NETBOX_47_ONLY_KINDS, "the reviewed 4.6 kind set cannot contain a 4.7 addition")
    _require(set(kinds) <= NETBOX_46_KINDS,
             f"observed payload reaches kinds outside the reviewed NetBox 4.6 set: "
             f"{sorted(set(kinds) - NETBOX_46_KINDS)}")

    # Inverse: documented values restore every changed record exactly. Created
    # records have no ingest inverse and say so.
    restored = {key: deepcopy(record) for key, record in observed.items()}
    for key in drifted:
        restored[key] = deepcopy(objects[key])
    for key in created:
        restored.pop(key)
    _require(canonical(sorted(({key: deepcopy(obj) for key, obj in objects.items()} | restored).values(),
                              key=lambda obj: obj["key"])) == canonical(baseline["objects"]),
             "replacing observed records with their documented values must restore the exact frozen baseline")
    _require(digest(baseline) == fingerprint, "deriving the drift set must not modify the bound baseline")

    entities = len(selected) + sum(1 for key in selected if _deferred_fields(index[key]) & index[key]["refs"].keys())
    changes = Counter(row["change_type"] for item in items for row in item["expected_changes"])
    counts = {"items": len(items), "emitted_records": len(selected), "ingestion_entities": entities,
              "by_class": {name: sum(1 for item in items if item["deviation_class"] == name) for name in CLASSES},
              "by_change_type": {name: changes[name] for name in CHANGE_TYPES},
              "expressible_items": sum(1 for item in items if item["expected_changes"]),
              "requires_target_side_comparison": sum(1 for item in items if not item["expected_changes"])}
    _require(counts["by_change_type"]["create"] == len(created) and
             counts["by_change_type"]["update"] == len(drifted) and
             sum(changes.values()) == len(selected),
             "declared change types must account for exactly the observed records")
    checks = {"baseline_valid": True, "baseline_unmodified": True, "one_item_per_observed_record": True,
              "matching_identities_preserved": len(drifted), "created_identities_distinct": len(created),
              "netbox_46_kinds_only": True, "emitted_kinds": kinds,
              "inverse_restores_documented_records": True,
              "undocumented_objects_need_target_side_removal": len(created),
              "deviation_classes_covered": len(CLASSES), "items": len(items),
              "plan_sha256": {"baseline": fingerprint, "observed": digest(observed_plan)}}
    inverse = {"baseline_sha256": fingerprint,
               "documented_records": [deepcopy(objects[key]) for key in drifted],
               "offline": "Replacing every observed record with its documented record restores the exact frozen baseline.",
               "ingest": "Re-ingesting the baseline artifact's own Diode files restores every changed field on every "
                         "matched object, so each drift-vs-intent and data-quality deviation resolves to no change.",
               "not_cleared_by_ingest": created,
               "live": "Whether Assurance opens, diffs, applies or clears these deviations requires target-side "
                       "evidence; no live run is claimed."}
    return {"schema_version": 1, "artifact": ARTIFACT, "generator_version": __version__,
            "baseline": {"plan_file": "baseline/plan.json", "plan_sha256": fingerprint,
                         "profile": baseline["recipe"]["profile"], "namespace": baseline["recipe"]["namespace"],
                         "name": baseline["recipe"]["name"]},
            "selection": subjects, "items": items, "counts": counts,
            "observed": {"directory": "observed", "records": selected, "ingestion_entities": entities},
            "observed_plan": observed_plan, "inverse": inverse, "checks": checks,
            "target": dict(TARGET), "execution": dict(EXECUTION), "limitations": list(LIMITATIONS),
            "sources": list(SOURCES)}


def create(plan):
    """Select the drift subjects and return the complete evidence envelope."""
    try:
        return _derive(deepcopy(plan))
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError, StopIteration) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"{LABEL}: malformed baseline or unsupported graph ({type(exc).__name__})") from None


def verify(envelope, baseline):
    """Recompute everything from the bound baseline; claimed items prove nothing."""
    try:
        _require(isinstance(envelope, dict) and type(envelope.get("schema_version")) is int and
                 envelope["schema_version"] == 1 and envelope.get("artifact") == ARTIFACT,
                 "unsupported drift manifest")
        expected = _derive(deepcopy(baseline))
        observed_plan = expected.pop("observed_plan")
        _require(set(envelope) == set(expected), "drift manifest has missing or unsupported claims")
        for key in expected:
            _require(canonical(envelope[key]) == canonical(expected[key]),
                     f"{key} does not match independently derived drift evidence")
        return deepcopy(expected["checks"]), observed_plan, expected["observed"]["records"]
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError, StopIteration) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(f"{LABEL}: malformed drift manifest ({type(exc).__name__})") from None


def markdown(envelope, baseline):
    """Render the operator walkthrough only after rechecking the evidence."""
    _, observed_plan, _ = verify(envelope, baseline)
    objects = {obj["key"]: obj for obj in baseline["objects"]}
    observed = {record["key"]: record for record in observed_plan["objects"]}
    selection, counts = envelope["selection"], envelope["counts"]
    site = objects[selection["site"]]["attrs"]["name"]

    def label(key):
        """Readable, owner-qualified: two devices can both carry an `eth0`."""
        record = (objects.get(key) or observed.get(key)) if isinstance(key, str) else None
        if record is None:
            return key
        attrs = record["attrs"]
        name = attrs.get("name", attrs.get("address", attrs.get("mac_address", key)))
        owner = record["refs"].get("device")
        return f"{label(owner)} / {name}" if isinstance(owner, str) and record["kind"] != "device" else name

    lines = [f"# Discovery drift at {_cell(site)}", "",
             f"The baseline is documented truth. This artifact is the other half of an Assurance demonstration: "
             f"a bounded Diode payload of **{counts['emitted_records']} observed records** carrying "
             f"**{counts['items']} deliberate drift items** across {selection['site'].removeprefix('site/')}, "
             "so a review screen has something real to show.", "",
             f"Predicted against that documented baseline: **{counts['by_change_type']['create']} created objects** "
             f"and **{counts['by_change_type']['update']} updated objects** from "
             f"{counts['expressible_items']} ingest-expressible items, plus "
             f"**{counts['requires_target_side_comparison']} items no ingest can express**.", "",
             "## Run it", "",
             "```sh",
             "# 1. Load the documented baseline first; it is the state Assurance compares against.",
             "#    Load the ORIGINAL build directory this plan came from (the baseline/ copy in",
             "#    here binds the artifact and is not a loadable build).",
             "just load <original-baseline-build-dir> <target> <branch>",
             "",
             "# 2. Mid-demo, ingest the observed snapshot through the target's Diode endpoint.",
             "#    drift-ingest replays the request files in phase order (later phases reference",
             "#    records earlier ones create); it needs the .env Diode credentials and the",
             "#    DIODE_WRITES=1 attestation. Acknowledgement is acceptance only: on an",
             "#    Assurance-review tenant the records become pending deviations to open in the UI.",
             "devenv --profile diode shell -- just drift-ingest <drift-dir>",
             "",
             "# 3. Re-check the artifact at any time; it recomputes from the bound baseline.",
             "just drift-check <drift-dir>",
             "```", "",
             "Mode matters: ingestion routing (Assurance review versus direct auto-apply) is a tenant setting the "
             "API neither chooses nor reports. On a review tenant, ingest the documented baseline first and accept "
             "it wholesale in Assurance, so the drift ingest arrives against real documented state; on an "
             "auto-apply tenant, load the baseline normally and confirm review routing before step 2, or every "
             "drift record silently applies instead of surfacing.", "",
             "Then open **Assurance → Deviations → Active Deviations** in NetBox. A deviation row carries its "
             "deviation type, source and change count; opening one shows the ingested data and the per-object "
             "before/after changes. Exact labels are version-dependent - check them against your target build "
             "before the call.", "",
             "## The drift set", ""]
    def shown(value):
        rendered = label(value)
        return "(empty)" if rendered == "" else rendered

    def summary(item):
        if item["fields"]:
            return "; ".join(f"{row['field']}: {shown(row['documented'])} → {shown(row['observed'])}"
                             for row in item["fields"])
        return "no documented counterpart" if item["expected_changes"] else "absent from discovery"

    def expected(item):
        changes = Counter(row["change_type"] for row in item["expected_changes"])
        return ", ".join(f"{count} {name}" for name, count in sorted(changes.items())) or "not expressible"

    _table(lines, ["Item", "Class", "Expected", "Object", "What changed"],
           [[item["id"], item["deviation_class"], expected(item), label(item["object"]), summary(item)]
            for item in envelope["items"]])
    lines.extend(["", "## The talk track", ""])
    for item in envelope["items"]:
        lines.extend([f"### {item['id']} · {item['deviation_class']}", "",
                      _cell(item["story"]), "",
                      f"**What the review screen should show.** {_cell(item['expected_deviation'])}", ""])
    lines.extend(["## What this artifact cannot do", "",
                  "Diode expresses creates and updates only. Its ingest request carries no absence signal and the "
                  "reconciler defines no delete change type, so the "
                  f"{counts['requires_target_side_comparison']} documented-not-observed items are named above and "
                  "deliberately absent from `observed/`. Detecting them means comparing documented inventory with "
                  "the observed set on the target side; this artifact will not fake it.", "",
                  "## Clearing the drift", "", _cell(envelope["inverse"]["ingest"]), "",
                  f"{len(envelope['inverse']['not_cleared_by_ingest'])} undocumented objects are not withdrawn by "
                  "re-ingesting documented state. Removing them is a target-side action.", "",
                  _cell(envelope["inverse"]["live"]), "", "## Boundaries", ""])
    lines.extend(f"- {_cell(value)}" for value in envelope["limitations"])
    lines.extend(["", "Sources:", ""])
    lines.extend(f"- <{value}>" for value in envelope["sources"])
    return "\n".join(lines) + "\n"
