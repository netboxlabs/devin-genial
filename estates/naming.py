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
  ``estates/branch.py`` EXACT_RETIREMENT_NAMES).  A clean name would make two
  estates collide on one main and would strand rows that retirement can no
  longer find.
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

# Kinds whose ``name`` IS a cross-estate identity rather than a label, so the
# namespace has to stay in it.  Each entry needs a reason, not a convenience:
#
# * ``contact_group`` — the root group's canonical slug is derived from this
#   name and is deliberately omitted on the wire so the pinned plugin's
#   auto-slug matcher resolves it; a clean name silently moves the matching
#   identity (estates/operations_context.py).
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

# Tokens whose conventional casing a naive .title() would destroy.
_ACRONYMS = {
    "ap": "AP", "atm": "ATM", "bgp": "BGP", "ce": "CE", "csv": "CSV", "dc": "DC",
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
