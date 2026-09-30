"""Display-name policy for every emitted object family.

Identities keep the stable ``<namespace>-…`` form: slugs, object keys, device
names, circuit ``cid`` values, asset tags and DNS names all participate in
Diode/loader matching or in growth reuse, and renaming one would rebaseline an
estate.  Only the human-facing ``name`` is authored here.

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
  rack roles, cluster types, RIRs and IPAM roles).
"""

# Verified against the pinned NetBox 4.7.1 source: these are the kinds whose
# rows survive branch deletion and coexist across namespaces on one main.
NAMESPACED_KINDS = frozenset({
    "owner", "owner_group",
    "export_template", "webhook", "event_rule",
    "custom_field", "custom_field_choice_set", "custom_link",
})

# Tokens whose conventional casing a naive .title() would destroy.
_ACRONYMS = {
    "ap": "AP", "bgp": "BGP", "ce": "CE", "csv": "CSV", "dc": "DC",
    "dhcp": "DHCP", "dns": "DNS", "hmi": "HMI", "hq": "HQ", "idf": "IDF",
    "ike": "IKE", "ip": "IP", "ipam": "IPAM", "ipsec": "IPsec", "it": "IT",
    "l2": "L2", "l3": "L3", "lan": "LAN", "mdf": "MDF", "noc": "NOC",
    "ot": "OT", "pdu": "PDU", "pe": "PE", "poe": "PoE", "psu": "PSU",
    "radius": "RADIUS", "rir": "RIR", "rtu": "RTU", "ssid": "SSID",
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


def display_name(namespace, kind, text, *, titleized=True):
    """The emitted ``name`` for one object of ``kind``.

    ``text`` is the authored, namespace-free label.  Main-scoped kinds regain
    the prefix so retirement and cross-namespace coexistence keep working.
    """
    label = titleize(text) if titleized else str(text)
    if kind in NAMESPACED_KINDS:
        return f"{namespace} {label}"
    return label
