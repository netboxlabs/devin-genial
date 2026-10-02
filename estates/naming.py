"""Display-name policy for every emitted object family.

Identities keep the stable ``<namespace>-…`` form: slugs, object keys, device
names, circuit ``cid`` values, asset tags, DNS names, provider ``account``
numbers, WLAN ``ssid`` values and the virtual-chassis ``domain`` all participate
in Diode/loader matching or in growth reuse, and renaming one would rebaseline an
estate.  Only the human-facing ``name`` is authored here — plus, for the two
models NetBox gives no ``name`` at all (``ASN`` and ``Aggregate``), the
``description`` the graph renders in its place, which names the provider or
tenant that actually holds the record.

Why this exists: NetBox's own tables tolerate a namespace prefix, but the
visualization layer does not.  Visual Explorer truncates graph node labels to a
fixed width, so four distinct power panels all rendered as
``aurora-peak-pop-chica…`` — indistinguishable.  A readable display name is a
functional requirement of the rendered estate, not decoration.

Two buckets, and the split is load-bearing:

* ``NAMESPACED_KINDS`` keep the prefix.  These are the main-scoped,
  Branching-exempt records that a branch load writes to ``main``; distinct
  namespaces coexist there by design, the loader's allowlist requires their
  plain-attribute identities to be disjoint from the plan, and ``just retire``
  selects them by an exact ``"<namespace> …"`` match (see
  ``estates/branch.py`` RETIREMENT_LABELS).  A clean name would make two
  estates collide on one main and would strand rows that retirement can no
  longer find.  The exception is a recipe with ``tenancy = "dedicated"``: one
  estate owns the tenant, so ``main_scoped_name`` drops the prefix and
  retirement matches the bare labels exactly instead.
* Everything else is branch- or estate-scoped.  The loader's fresh-load
  occupancy gate already refuses a second estate in one scope, so a clean
  display name cannot collide in practice even for the families NetBox holds
  globally unique (``OrganizationalModel`` subclasses such as circuit types,
  rack roles, cluster types, RIRs and IPAM roles, and — read back from the
  pinned 4.7.1 source — ``ConfigContext``, ``Tunnel``, ``L2VPN`` and the five
  IKE/IPsec crypto models).

``tests/test_naming_policy.py`` sweeps this inverted: *every* kind carrying a
``name``, minus ``NAMESPACED_KINDS``, minus ``IDENTITY_NAMED_KINDS`` below.  A
new family is therefore covered the moment it is emitted; 0.12's curated
opt-in list silently missed VRFs, FHRP groups, tunnels and the crypto records
for three releases.
"""

import ipaddress
import re

# Kinds whose ``name`` IS a cross-estate identity rather than a label, so the
# namespace has to stay in it.  Each entry needs a reason, not a convenience:
#
# * ``contact_group`` — the root group's canonical slug is derived from this
#   name and is deliberately omitted on the wire so the pinned plugin's
#   auto-slug matcher resolves it; a clean name silently moves the matching
#   identity (estates/operations_context.py).  A dedicated tenant drops the
#   prefix via ``main_scoped_name``: one estate owns it, so the bare name and
#   the slug derived from it cannot collide and still match on the wire.
# * ``route_target`` — the name IS the route distinguisher, "<asn>:<number>",
#   built from the estate's namespace-derived private ASN block.  NetBox holds
#   RouteTarget.name unique=True globally (pinned 4.7.1 ipam/models/vrfs.py),
#   so two estates on one target must not compute the same value.  It never
#   contains the namespace literally, only numerically.
IDENTITY_NAMED_KINDS = frozenset({"contact_group", "route_target"})

# Verified against the pinned NetBox 4.7.1 source: these are the kinds whose
# rows survive branch deletion and coexist across namespaces on one main.
NAMESPACED_KINDS = frozenset({
    "owner", "owner_group",
    "export_template", "webhook", "event_rule",
    "custom_field", "custom_field_choice_set", "custom_link",
})


def dedicated(recipe):
    """``tenancy = "dedicated"``: one estate owns the whole tenant."""
    return recipe.get("tenancy") == "dedicated"


def main_scoped_name(recipe, label):
    """The ``name`` of one ``NAMESPACED_KINDS`` record.

    Shared tenancy (the default) prefixes ``"<namespace> "`` so estates coexist
    on one main.  A dedicated tenant drops it: nothing else can coexist there
    (the fresh-load occupancy gate), so the prefix is only noise in the UI.
    Retirement then matches the bare labels exactly
    (``estates/branch.py`` RETIREMENT_LABELS).  ``custom_field`` names are an
    identifier, not a label, and keep the namespace in both modes.
    """
    return label if dedicated(recipe) else f"{recipe['namespace']} {label}"

# Tokens whose conventional casing a naive .title() would destroy.
_ACRONYMS = {
    "ap": "AP", "atm": "ATM", "nid": "NID", "bgp": "BGP", "ce": "CE", "csv": "CSV", "dc": "DC",
    "api": "API", "db": "DB", "dhcp": "DHCP", "dns": "DNS", "ems": "EMS", "erp": "ERP",
    "hmi": "HMI", "hq": "HQ", "idf": "IDF",
    "ike": "IKE", "ip": "IP", "ipam": "IPAM", "ipsec": "IPsec", "it": "IT",
    "l2": "L2", "l3": "L3", "lan": "LAN", "mdf": "MDF", "mes": "MES", "noc": "NOC",
    "ot": "OT", "pdu": "PDU", "pe": "PE", "poe": "PoE", "pos": "POS",
    "psu": "PSU",
    "radius": "RADIUS", "rir": "RIR", "rmm": "RMM", "rtu": "RTU", "scada": "SCADA",
    "ssid": "SSID",
    "us": "US", "vlan": "VLAN", "vm": "VM", "vpn": "VPN", "vrf": "VRF",
    "wan": "WAN", "wifi": "Wi-Fi",
}


def titleize(text):
    """Human title for a slug-ish token run, preserving known acronyms."""
    words = str(text).replace("_", " ").replace("-", " ").split()
    out = []
    for word in words:
        lowered = word.lower()
        if lowered in _ACRONYMS:
            out.append(_ACRONYMS[lowered])
        elif word.isupper() and len(word) > 1:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def site_display(world, site_key):
    """The site's emitted display name, once places.locate has authored it.

    Every label built from a site reads it through here rather than through
    ``Site.name``, which is the namespaced *slug* stem, not a display name.
    """
    site = world.objects.get(site_key) if hasattr(world, "objects") else None
    return (site or {}).get("attrs", {}).get("name")


# Native ``name`` limits, read back from the pinned NetBox 4.7.1 source for the
# families whose label this module composes from a site display name (itself up
# to the native Site limit of 100).  Everything else is checked against the
# common 100; a too-generous default only weakens the net, it cannot reject a
# valid name.  dcim.models.devices.VirtualChassis.name and
# ipam.models.vlans.VLAN.name are both max_length=64.
NAME_LIMIT_DEFAULT = 100
NAME_LIMITS = {"vlan": 64, "virtual_chassis": 64}


def name_limit(kind):
    return NAME_LIMITS.get(kind, NAME_LIMIT_DEFAULT)


def display_name(namespace, kind, text, *, titleized=True):
    """The emitted ``name`` for one object of ``kind``.

    ``text`` is the authored, namespace-free label.  Main-scoped kinds regain
    the prefix so retirement and cross-namespace coexistence keep working.
    """
    label = titleize(text) if titleized else str(text)
    if kind in NAMESPACED_KINDS:
        return f"{namespace} {label}"
    return label


# Human labels for device roles, used where a description says what a device
# *is*.  The role slug ("provider-edge", "pdu") is an identity, not prose; an
# engineer reads "Provider edge router" and "Rack PDU".  Unlisted roles fall
# back to their titleized slug.
ROLE_LABELS = {
    "access": "Access switch", "distribution": "Distribution switch",
    "spine": "Spine switch", "leaf": "Leaf switch", "core": "Core switch",
    "management": "Management switch", "stack": "Stacked access switch",
    "wan-edge": "WAN edge router", "provider-edge": "Provider edge router",
    "customer-edge": "Customer edge gateway", "server": "Compute host",
    "console-server": "Console server", "pdu": "Rack PDU",
    "patch-panel": "Patch panel", "wall-outlet": "Wall outlet",
    "laboratory": "Analytics enclosure",
}


def role_label(role):
    return ROLE_LABELS.get(role, titleize(role))


# One distinct colour per device role (NetBox renders it as the role badge and
# the rack-elevation fill), so no two roles read alike in a list or a rack.
# ``tests/test_estate_hygiene.py`` pins uniqueness; an estate emits only the
# roles something references (estates/operations.py ``finalize``).  The role
# colours are one slice of ``PALETTE`` below, which keeps every coloured
# taxonomy family in an estate distinct from every other.
ROLE_COLORS = {
    "wan-edge": "e65100", "distribution": "6a1b9a", "access": "1565c0",
    "spine": "4a148c", "leaf": "7b1fa2", "server": "2e7d32", "management": "546e7a",
    "patch-panel": "78909c", "pdu": "c62828", "workstation": "00838f",
    "atm": "f9a825", "ap": "00acc1", "camera": "795548", "wall-outlet": "b0bec5",
    "medical-device": "d81b60", "imaging-device": "8e24aa",
    "pos-terminal": "ef6c00", "scanner": "5d4037",
    "plc": "bf360c", "hmi": "ff8f00", "field-device": "827717",
    "rtu": "00695c", "protection-relay": "ad1457", "station-gateway": "4527a0",
    "provider-edge": "5e35b1", "customer-edge": "0097a7", "nid": "00897b",
    "console-server": "455a64", "laboratory": "37474f", "stack": "283593", "lab-router": "ff6f00",
    # VM roles
    "application": "43a047", "database": "3949ab", "backup-service": "8d6e63",
}


# Graph-derived tags (estates/operations.py ``_tags``). Each is applied only
# where the finished graph shows the property and an estate emits only the
# tags it applies. ``object_types`` is the native-model scope a tag is meant
# for; independent checks refuse a tag on any other kind. Order is display order.
TAGS = {
    "hub-site": ("Hub site", "0288d1", ("site",),
                 "Hosts shared services or private-WAN hubs that other sites depend on"),
    "dual-homed": ("Dual-homed", "388e3c", ("site",),
                   "Active WAN access from two different carriers, or into two different provider edges"),
    "acquired": ("Acquired", "a1887f", ("site", "device"),
                 "Carried over from an acquired network with its retained design"),
    "route-reflector": ("Route reflector", "7e57c2", ("device",),
                        "iBGP route reflector for the backbone"),
    "transit-edge": ("Transit edge", "f57c00", ("device",),
                     "Terminates an upstream transit handoff"),
    "managed-service": ("Managed service", "00796b", ("device", "circuit"),
                        "Carrier-managed customer service: the managed CE and its access circuit"),
    "managed-ce": ("Managed CE", "26a69a", ("device",),
                   "Customer-premises edge operated by the service provider"),
    "pci-scope": ("PCI scope", "e53935", ("device", "vlan", "prefix"),
                  "Payment-card segment, or equipment that carries or attaches to one"),
    "clinical": ("Clinical", "ec407a", ("device", "vlan", "prefix"),
                 "Clinical or medical-device segment, or equipment that carries or attaches to one"),
    "ot-zone": ("OT zone", "d84315", ("device", "vlan", "prefix"),
                "Operational-technology segment, or equipment that carries or attaches to one"),
    "multi-site": ("Multi-site service", "5c6bc0", ("virtual_machine",),
                   "Workload with replicas in more than one site"),
}


# Every coloured taxonomy record an estate can emit, keyed by its plan key.
# NetBox shows these colours side by side (role badges, tag pills, rack-role
# elevation fills, module-bay-type swatches), and two families sharing one hex
# read as the same thing — the reviewer found access switches, network racks and
# the hub-site tag all in one blue.  ``operations.finalize`` applies this map to
# every coloured record and then gives any record not listed here (a
# manufacturer's module-bay class a future catalog adds) the first unused
# ``SPARE_COLORS`` entry in key order, so an estate never repeats a colour
# across families.  ``tests/test_estate_hygiene.py`` pins global uniqueness.
# Bay classes: PSU bays sit in reds, optic cages in teals, one per maker.
PALETTE = {
    **{f"role/{role}": colour for role, colour in ROLE_COLORS.items()},
    **{f"tag/{slug}": spec[1] for slug, spec in TAGS.items()},
    "rack-role/network": "0d47a1", "rack-role/compute": "1b5e20",
    "virtual-circuit-type/private-l3": "c0ca33",
    "inventory-role/cooling": "26c6da",
    "module-bay-type/Arista/ac-psu": "b71c1c", "module-bay-type/Cisco/ac-psu": "d32f2f",
    "module-bay-type/Juniper/ac-psu": "ef5350", "module-bay-type/Supermicro/ac-psu": "e57373",
    "optics-bay-type/Arista/qsfp28": "006064", "optics-bay-type/Arista/sfpp": "00796b",
    "optics-bay-type/Cisco/sfpp": "009688", "optics-bay-type/Cisco/sfp": "4db6ac",
    "optics-bay-type/Fortinet/sfpp": "80cbc4", "optics-bay-type/Juniper/qsfp28": "00897b",
    "optics-bay-type/Juniper/sfpp": "b2dfdb", "optics-bay-type/Juniper/sfp28": "004d40",
    "optics-bay-type/Supermicro/sfpp": "18ffff",
}
SPARE_COLORS = ("9c27b0", "673ab7", "3f51b5", "03a9f4", "8bc34a", "cddc39", "ffc107",
                "ff5722", "607d8b", "9e9e9e", "e91e63", "4caf50")
# Kinds whose ``color`` is a taxonomy colour the palette governs.  Cables carry
# a colour too, but that is the physical jacket (CABLE_COLORS), shared by design.
COLOURED_KINDS = ("device_role", "rack_role", "tag", "inventory_item_role",
                  "module_bay_type", "virtual_circuit_type", "circuit_type")

# Cable jacket colour by medium, the convention a technician reads at the
# patch field: single-mode yellow, OM3/OM4 multimode and active optical aqua,
# copper data blue, serial console cyan, and power cords black on the A feed
# and red on the B feed.
CABLE_COLORS = {"smf": "ffeb3b", "mmf": "00bcd4", "aoc": "00bcd4", "cat6": "2196f3",
                "console": "00e5ff", "power-a": "212121", "power-b": "d50000"}

# Module-bay classes: a bay type names the form factor a maker's chassis takes,
# not one supply model, so a chassis family's bays read as one class.  Which
# supply actually fits a given chassis still follows that device type's own
# catalog entry (equipment.validate), never this label.
BAY_CLASSES = {"ac-psu": "AC PSU bay"}
OPTIC_CAGE_LABELS = {"sfp": "SFP", "sfpp": "SFP+", "sfp28": "SFP28", "qsfp28": "QSFP28"}


# What each addressed segment carries, in the words an engineer puts on a VLAN.
# The role key stays the identity; this is description only.  Keep entries
# under 50 characters: prefix descriptions append a site name of up to 100.
SEGMENT_PURPOSES = {
    "management": "Network device management", "users": "Staff workstations",
    "atm": "ATMs and self-service banking", "wireless": "Wireless clients",
    "security": "Security cameras", "voice": "Voice and IP telephony",
    "applications": "Application servers", "database": "Database servers",
    "backup": "Backup network", "wan": "WAN transit", "storage": "Storage network",
    "staff": "Staff devices", "students": "Student devices", "guest": "Guest access",
    "clinical": "Clinical workstations", "medical": "Networked medical devices",
    "imaging": "Imaging and diagnostic workstations",
    "backoffice": "Back-office workstations", "pos": "Point-of-sale lanes",
    "research": "Research computing", "office": "Corporate office devices",
    "logistics": "Warehouse and dock scanners",
    "process": "Line controllers and field devices",
    "supervisory": "Line operator panels",
    "conduit": "Routed transit between OT and IT tiers",
    "protection": "Protection relays", "telemetry": "Remote terminal units",
    "station": "Station HMIs and gateway", "clients": "Office workstations",
    "provider": "Provider backbone",
}


def segment_purpose(role):
    return SEGMENT_PURPOSES.get(role, f"{titleize(role)} network")


# How a WAN circuit's procurement cohort reads in its comments.  The cohort key
# stays the persisted identity in the circuit's procurement metadata.
COHORT_LABELS = {
    "dc-aggregation": "Data center aggregation", "retained-birch": "Retained Birch contract",
    "cedar-standard": "Standard branch", "district-standard": "Standard school",
    "health-system-standard": "Standard clinic", "chain-standard": "Standard store",
    "campus-standard": "Standard building", "managed-standard": "Standard customer office",
    "plant-standard": "Standard plant", "substation-standard": "Standard substation",
}


def bandwidth(mbps):
    """A committed rate the way a circuit order reads it: 50 Mbps, 1 Gbps, 100 Gbps."""
    if mbps >= 1000 and mbps % 1000 == 0:
        return f"{int(mbps) // 1000} Gbps"
    return f"{mbps:g} Mbps"


def port_speed(mbps):
    """A physical handoff the way a port is spoken of: 1G, 10G, 100G."""
    if mbps >= 1000 and mbps % 1000 == 0:
        return f"{int(mbps) // 1000}G"
    return f"{mbps:g}M"


def rate_kbps(kbps):
    """A NetBox kbps field rendered for prose: 100000000 -> 100 Gbps."""
    return bandwidth(kbps / 1000)


# IPAM roles (ipam.Role) drive the Role column, the prefix heatmap and the
# radial IPAM map's colouring, so every prefix and VLAN carries one.  One small
# authored set serves every profile; an estate emits only the roles it uses.
# Order is the NetBox ``weight`` (lower sorts first).  Names are namespace-free
# like device roles: Role is an OrganizationalModel (name and slug globally
# unique) and branch-scoped, and the loader's fresh-load occupancy gate already
# keeps a second estate out of one scope.
IPAM_ROLES = {
    "pools": ("Allocation pools", "Global containers that hold site blocks across routing contexts"),
    "backbone": ("Backbone", "Provider backbone and PoP infrastructure"),
    "transit": ("Transit", "Routed transit and point-to-point links"),
    "loopbacks": ("Loopbacks", "Router loopback addresses"),
    "management": ("Management", "Network device management"),
    "users": ("Users", "Staff, student, office and handheld endpoints"),
    "voice": ("Voice", "IP telephony"),
    "wireless": ("Wireless", "Wireless client access"),
    "guest": ("Guest", "Isolated visitor access"),
    "servers": ("Servers", "Application, database and research compute"),
    "storage": ("Storage", "Storage and backup networks"),
    "security": ("Security", "Physical security cameras"),
    "payments": ("Payments", "Point-of-sale and ATM endpoints"),
    "clinical": ("Clinical", "Clinical workstations, imaging and medical devices"),
    "ot": ("Operational technology", "Plant-floor and substation equipment segments"),
    "customer": ("Customer", "Address space allocated to customer VPNs"),
    "customer-dia": ("Customer DIA", "Public address space assigned to dedicated internet customers"),
    "nid-management": ("NID management", "In-band management of customer-premises NIDs"),
    "dhcp": ("DHCP pools", "Dynamic client address scopes"),
    "reserved": ("Reserved", "Addresses held for onboarding and growth"),
}

# The IPAM role each addressed segment (the VLAN/VRF role key) belongs to.  An
# unlisted segment is a hard error, so a new segment cannot ship role-less.
SEGMENT_ROLES = {
    "management": "management", "nid-management": "nid-management", "customer": "customer", "users": "users", "staff": "users", "students": "users",
    "clients": "users", "office": "users", "backoffice": "users", "logistics": "users",
    "voice": "voice", "wireless": "wireless", "guest": "guest",
    "applications": "servers", "database": "servers", "research": "servers",
    "storage": "storage", "backup": "storage", "security": "security",
    "pos": "payments", "atm": "payments",
    "clinical": "clinical", "medical": "clinical", "imaging": "clinical",
    "process": "ot", "supervisory": "ot", "protection": "ot", "telemetry": "ot", "station": "ot",
    "wan": "transit", "conduit": "transit", "recovery": "transit",
    "provider": "backbone", "oob": "management",
}


def segment_role(segment):
    try:
        return SEGMENT_ROLES[segment]
    except KeyError:
        raise ValueError(f"segment {segment!r} has no IPAM role in naming.SEGMENT_ROLES") from None


def prefix_role(key, prefix, vrf, vlan):
    """The IPAM role key of one prefix, from facts the finished graph carries.

    Host routes are loopbacks and /31 or /127 links are transit whatever VRF
    holds them; a prefix bound to a VLAN shares that segment's role; anything
    else follows its VRF — a provider customer VPN (``vrf/customer/<key>``), or
    the segment its per-segment routing context is named for.  The IPv6 infrastructure
    reservations name their purpose in their permanent key.
    """
    net = ipaddress.ip_network(prefix, strict=False)
    if net.prefixlen == net.max_prefixlen:
        return "loopbacks"
    if net.max_prefixlen - net.prefixlen == 1:
        return "transit"
    if key.startswith("prefix/dia/"):
        return "customer-dia"
    if key.startswith("prefix/nid-management/"):
        return "nid-management"
    if key.startswith("ipv6/infrastructure/"):
        return "loopbacks" if key.endswith("/loopbacks") else "transit"
    if vlan:
        return segment_role(vlan.rsplit("/", 1)[-1])
    if not vrf:
        return "pools"  # a global container parents site blocks in every VRF
    parts = str(vrf).split("/")
    if parts[:2] == ["vrf", "customer"] and len(parts) == 3:
        return "customer"
    return segment_role(parts[-1])


# Records carry operational text only (0.16.0).  Both showcase reviewers called
# per-record disclaimers ("documentation inventory…", "no … is claimed",
# "fictional", "placeholder") the loudest synthetic tell, so the modeling
# limitations live in docs/modeling.md and the generated report instead, and
# this pattern refuses them on any emitted string attribute.  The substantive
# guards (inert webhook, closed BGP kind/field set, no session state) are
# enforced as structure by their own validators, never by prose.
DISCLAIMER = re.compile(
    r"\b(?:not verified|unverified|claim(?:s|ed)?|asserted|placeholder|fictional|synthetic"
    r"|generator|authored|planning intent|inventory (?:only|intent)|(?:reference|documentation"
    r"|documented|service) inventory|documentation registry|not executed|is executed|recorded as executed|does not record"
    r"|is represented|not modell?ed|modell?ed|unknown|nothing is configured|is configured"
    r"|not configured|never applied)\b", re.I)


def disclaimer(obj):
    """The first disclaimer phrase in one object's string attributes, or None."""
    for value in obj.get("attrs", {}).values():
        if isinstance(value, str) and (found := DISCLAIMER.search(value)):
            return found.group(0)
    return None
