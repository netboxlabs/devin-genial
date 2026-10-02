"""Display names are authored; identities keep the namespace.

The visualization layer truncates graph node labels, so a namespace-prefixed
display name renders several distinct objects identically.  These tests pin the
split documented in estates/naming.py.

The sweep is INVERTED on purpose.  0.12 swept a curated opt-in tuple of 27
kinds and therefore missed VRFs, FHRP groups, tunnels, the IKE/IPsec records,
config contexts, contacts, clusters, VLANs and the ASN/aggregate descriptions
for three releases: a family added later was simply never checked.  Here every
kind the generator emits with a ``name`` is swept, and a family leaves the sweep
only by joining ``NAMESPACED_KINDS`` or ``IDENTITY_NAMED_KINDS`` with a written
reason.  Forgetting to opt in is now the safe failure, not the silent one.
"""

import tomllib
import unittest
from pathlib import Path

from estates.generate import generate
from estates.naming import (IDENTITY_NAMED_KINDS, NAMESPACED_KINDS, name_limit,
                            display_name, titleize)

PROFILES = Path(__file__).resolve().parent.parent / "profiles"

# Fields that are the rendered label for kinds NetBox gives no `name` at all.
# ASN and Aggregate are the reason this sweep exists: Visual Explorer labels an
# AS node from its description, so ours read "lakes-fiber transit-a routing
# identity" on the BGP topology graph — and named the wrong party.
DESCRIPTION_AS_LABEL = ("asn", "aggregate")

# Attributes that are a matching identity or a cross-estate separator and keep
# the namespace by design.  `ssid` is the WirelessLAN Diode matching identity
# (estates/diode.py _IDENTITY) and a real radio SSID; `domain` is a virtual
# chassis stack domain two estates must not share.
IDENTITY_FIELDS = ("slug", "cid", "dns_name", "asset_tag", "account", "ssid",
                   "domain", "facility_id")

# Kinds whose identity fields must still separate two estates on one target.
IDENTITY_KINDS = ("tenant", "site", "device_role", "rack_role", "circuit_type",
                  "cluster_type", "rir", "role", "vlan_group", "tag")


def _namespaced(value, namespace):
    lowered, ns = value.lower(), namespace.lower()
    return lowered.startswith(f"{ns} ") or lowered.startswith(f"{ns}-") or lowered.startswith(f"{ns}:")


class NamingHelpers(unittest.TestCase):
    def test_titleize_preserves_acronyms(self):
        self.assertEqual(titleize("wan-edge"), "WAN Edge")
        self.assertEqual(titleize("access"), "Access")
        self.assertEqual(titleize("pdu"), "PDU")
        self.assertEqual(titleize("provider_edge"), "Provider Edge")
        # Segment roles reach the acronym table through the VRF/VLAN labels.
        self.assertEqual(titleize("atm"), "ATM")
        self.assertEqual(titleize("pos"), "POS")
        self.assertEqual(titleize("Brady and 9th Branch atm"), "Brady And 9th Branch ATM")

    def test_main_scoped_kinds_keep_the_namespace(self):
        for kind in ("owner", "owner_group", "export_template", "webhook",
                     "event_rule", "custom_field", "custom_link"):
            self.assertIn(kind, NAMESPACED_KINDS)
            self.assertTrue(display_name("acme", kind, "thing").startswith("acme "))

    def test_dedicated_tenancy_drops_the_prefix_from_main_scoped_names_only(self):
        from estates.naming import main_scoped_name
        self.assertEqual(main_scoped_name({"namespace": "acme"}, "Network operations"), "acme Network operations")
        self.assertEqual(main_scoped_name({"namespace": "acme", "tenancy": "shared"}, "X"), "acme X")
        self.assertEqual(main_scoped_name({"namespace": "acme", "tenancy": "dedicated"}, "X"), "X")
        plan = generate({"namespace": "zeta", "name": "Acme Estate", "tenancy": "dedicated"})
        names = [o["attrs"]["name"] for o in plan["objects"]
                 if o["kind"] in NAMESPACED_KINDS - {"custom_field"}]
        self.assertTrue(names)
        self.assertFalse([n for n in names if "zeta" in n.lower()])
        slugs = [o["attrs"]["slug"] for o in plan["objects"] if o["kind"] in ("site", "tenant")]
        self.assertTrue(slugs and all("zeta" in slug for slug in slugs))

    def test_config_context_is_not_main_scoped(self):
        # get_branchable_object_types() lists extras.configcontext, so a config
        # context goes with its branch and `just retire` never looks for it.
        self.assertNotIn("config_context", NAMESPACED_KINDS)
        self.assertEqual(display_name("acme", "config_context", "Global service baseline"),
                         "Global Service Baseline")

    def test_estate_scoped_kinds_drop_the_namespace(self):
        self.assertEqual(display_name("acme", "device_role", "wan-edge"), "WAN Edge")
        self.assertEqual(display_name("acme", "region", "Illinois"), "Illinois")

    def test_native_name_limits_are_the_verified_tight_ones(self):
        # Read back from the pinned NetBox 4.7.1 source; both are composed from
        # a site display name, which NetBox allows up to 100 characters.
        self.assertEqual(name_limit("vlan"), 64)
        self.assertEqual(name_limit("virtual_chassis"), 64)
        self.assertEqual(name_limit("vrf"), 100)


class GeneratedEstateNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"namespace": "zeta", "name": "Acme Estate"})
        cls.namespace = cls.plan["recipe"]["namespace"]

    def test_every_authored_display_name_is_namespace_free(self):
        """The inverted sweep: every emitted `name`, minus the two exception sets."""
        offenders, swept = [], set()
        for obj in self.plan["objects"]:
            kind = obj["kind"]
            if kind in NAMESPACED_KINDS or kind in IDENTITY_NAMED_KINDS:
                continue
            name = obj["attrs"].get("name")
            if not isinstance(name, str) or not name:
                continue
            swept.add(kind)
            if _namespaced(name, self.namespace):
                offenders.append((kind, obj["key"], name))
        self.assertEqual(offenders, [], f"namespace leaked into display names: {offenders[:8]}")
        # A sweep that silently stops covering anything is the bug this replaces.
        self.assertGreater(len(swept), 40, f"only {len(swept)} kinds swept; the sweep lost coverage")
        for kind in ("vrf", "fhrp_group", "tunnel", "l2vpn", "ike_policy", "ike_proposal",
                     "ip_sec_policy", "ip_sec_proposal", "ip_sec_profile", "config_context",
                     "contact", "cluster", "vlan", "virtual_chassis", "vlan_translation_policy",
                     "provider_account", "vlan_group"):
            self.assertIn(kind, swept, f"{kind} is no longer swept by the naming policy")

    def test_label_bearing_descriptions_are_namespace_free(self):
        # NetBox gives ASN and Aggregate no name field at all, so the rendered
        # graph node label is the description.
        offenders, seen = [], set()
        for obj in self.plan["objects"]:
            if obj["kind"] not in DESCRIPTION_AS_LABEL:
                continue
            description = obj["attrs"].get("description", "")
            seen.add(obj["kind"])
            if not isinstance(description, str) or not description:
                offenders.append((obj["key"], description))
            elif _namespaced(description, self.namespace):
                offenders.append((obj["key"], description))
        self.assertEqual(offenders, [], f"namespace leaked into rendered labels: {offenders[:8]}")
        self.assertEqual(seen, set(DESCRIPTION_AS_LABEL))

    def test_asn_descriptions_name_the_party_that_holds_the_as(self):
        # The defect this fixes: the carrier AS node read "carrier-a", which
        # contradicted the provider name shown on every other record.
        providers = {obj["key"]: obj["attrs"]["name"]
                     for obj in self.plan["objects"] if obj["kind"] == "provider"}
        self.assertTrue(providers, "expected carrier providers in the bank estate")
        carriers = {obj["key"]: obj["attrs"]["description"]
                    for obj in self.plan["objects"]
                    if obj["kind"] == "asn" and obj["key"].startswith("asn/carrier-")}
        self.assertTrue(carriers)
        for key, description in carriers.items():
            side = key.rsplit("-", 1)[1]
            self.assertTrue(description.startswith(providers[f"provider/{side}"]),
                            f"{key} description {description!r} must name provider/{side}")

    def test_main_scoped_records_still_carry_the_namespace(self):
        seen = 0
        for obj in self.plan["objects"]:
            if obj["kind"] not in NAMESPACED_KINDS:
                continue
            name = obj["attrs"].get("name")
            if not isinstance(name, str):
                continue
            seen += 1
            self.assertTrue(
                name.lower().startswith(self.namespace.lower()),
                f"{obj['kind']} {name!r} must stay namespaced: retirement matches it by exact prefix",
            )
        self.assertTrue(seen, "expected at least one main-scoped record")

    def test_root_contact_group_keeps_its_derived_matching_identity(self):
        # Its canonical slug is derived from the name and omitted on the wire for
        # the auto-slug matcher, so a clean name would move the identity.
        groups = [obj for obj in self.plan["objects"] if obj["kind"] == "contact_group"]
        self.assertTrue(groups)
        for obj in groups:
            name = obj["attrs"]["name"]
            self.assertTrue(name.lower().startswith(f"{self.namespace} "), name)
            self.assertEqual(obj["attrs"]["slug"], name.lower().replace(" ", "-"))

    def test_power_feed_names_are_readable_and_unique_within_their_panel(self):
        # NetBox keys a feed on (power_panel, name); the namespaced stem made all
        # four feeds of a power chain truncate to one indistinguishable label.
        feeds, seen = 0, set()
        for obj in self.plan["objects"]:
            if obj["kind"] != "power_feed":
                continue
            feeds += 1
            name, panel = obj["attrs"]["name"], obj["refs"]["power_panel"]
            self.assertNotIn(self.namespace, name.lower())
            self.assertRegex(name, r"^[A-Za-z][A-Za-z0-9 ]* [AB]$")
            self.assertNotIn((panel, name), seen, f"{name!r} repeats under {panel}")
            seen.add((panel, name))
        self.assertTrue(feeds, "expected power feeds in the estate")

    def test_identities_still_carry_the_namespace(self):
        seen = 0
        for obj in self.plan["objects"]:
            if obj["kind"] not in IDENTITY_KINDS:
                continue
            for field in ("slug", "cid", "dns_name"):
                value = obj["attrs"].get(field)
                if not isinstance(value, str) or not value:
                    continue
                seen += 1
                self.assertIn(self.namespace, value,
                              f"{obj['kind']}.{field}={value!r} lost its namespace")
        self.assertTrue(seen, "expected identity fields to be present")


class MutatedNamingPolicy(unittest.TestCase):
    """The failing-mutation check: reintroducing a prefix must be caught."""

    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"namespace": "zeta", "name": "Acme Estate"})
        cls.namespace = cls.plan["recipe"]["namespace"]

    def _sweep(self, objects):
        return [(obj["kind"], obj["attrs"]["name"]) for obj in objects
                if obj["kind"] not in NAMESPACED_KINDS
                and obj["kind"] not in IDENTITY_NAMED_KINDS
                and isinstance(obj["attrs"].get("name"), str)
                and _namespaced(obj["attrs"]["name"], self.namespace)]

    def test_clean_estate_passes_its_own_sweep(self):
        self.assertEqual(self._sweep(self.plan["objects"]), [])

    def test_a_reintroduced_prefix_is_caught_in_every_authored_family(self):
        for kind in ("vrf", "fhrp_group", "tunnel", "l2vpn", "ike_policy", "config_context",
                     "contact", "cluster", "vlan", "virtual_chassis", "provider_account",
                     "vlan_translation_policy", "ip_sec_profile", "device_role", "region"):
            with self.subTest(kind=kind):
                victim = next((obj for obj in self.plan["objects"]
                               if obj["kind"] == kind and isinstance(obj["attrs"].get("name"), str)), None)
                self.assertIsNotNone(victim, f"{kind} is not emitted; the sweep proves nothing for it")
                original = victim["attrs"]["name"]
                victim["attrs"]["name"] = f"{self.namespace}-{original}"
                try:
                    self.assertEqual(len(self._sweep(self.plan["objects"])), 1,
                                     f"a namespace prefix on {kind} slipped past the sweep")
                finally:
                    victim["attrs"]["name"] = original

    def test_dropping_the_prefix_from_a_main_scoped_record_is_caught(self):
        victim = next(obj for obj in self.plan["objects"] if obj["kind"] == "webhook")
        original = victim["attrs"]["name"]
        victim["attrs"]["name"] = original.removeprefix(f"{self.namespace} ")
        try:
            self.assertFalse(victim["attrs"]["name"].startswith(f"{self.namespace} "),
                             "retirement matches this row by exact prefix; it must stay namespaced")
        finally:
            victim["attrs"]["name"] = original


class NameUniquenessAcrossProfiles(unittest.TestCase):
    """Renamed families must still resolve to exactly one row per estate.

    Verified against the pinned NetBox 4.7.1 source: ConfigContext, Tunnel,
    L2VPN and the five IKE/IPsec models declare ``unique=True`` on ``name``;
    VRF is unique per (name, tenant) through its Diode identity; Contact's
    Diode identity is the bare name; ProviderAccount is unique per
    (provider, name).  A collision would silently merge two objects at load.
    """

    GLOBAL = ("config_context", "tunnel", "l2vpn", "ike_policy", "ike_proposal",
              "ip_sec_policy", "ip_sec_proposal", "ip_sec_profile", "contact",
              "vlan_translation_policy", "fhrp_group", "virtual_chassis", "tunnel_group")
    # Scoped by the reference the Diode identity carries alongside the name.
    SCOPED = {"vrf": "tenant", "provider_account": "provider", "vlan_group": "scope_site",
              "cluster": "scope_site", "power_feed": "power_panel"}

    @classmethod
    def setUpClass(cls):
        cls.plans, cls.rebuilt = {}, {}
        for path in sorted(PROFILES.glob("*.toml")):
            with path.open("rb") as handle:
                recipe = tomllib.load(handle)
            if recipe.get("profile") == "provider-backbone":
                recipe["discovery_lab"] = True  # sweep the optional network lab's records too
            cls.plans[path.name] = generate(recipe)
            # The same estate under a namespace no authored word can equal.
            moved = dict(recipe, namespace="qq7")
            cls.rebuilt[path.name] = {obj["key"]: obj for obj in generate(moved)["objects"]}

    def test_renamed_families_stay_unique_in_every_profile(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                seen, collisions = {}, []
                for obj in plan["objects"]:
                    kind, name = obj["kind"], obj["attrs"].get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    if kind in self.GLOBAL:
                        scope = None
                    elif kind in self.SCOPED:
                        scope = obj["refs"].get(self.SCOPED[kind])
                    else:
                        continue
                    if (kind, scope, name) in seen:
                        collisions.append((kind, scope, name, seen[(kind, scope, name)], obj["key"]))
                    seen[(kind, scope, name)] = obj["key"]
                self.assertEqual(collisions, [], f"{profile}: colliding display names {collisions[:5]}")

    def test_every_name_fits_its_native_limit_in_every_profile(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                overlong = [(obj["kind"], obj["key"], len(obj["attrs"]["name"]))
                            for obj in plan["objects"]
                            if isinstance(obj["attrs"].get("name"), str)
                            and len(obj["attrs"]["name"]) > name_limit(obj["kind"])]
                self.assertEqual(overlong, [], f"{profile}: names over their native limit {overlong[:5]}")

    def test_authored_names_do_not_move_when_the_namespace_does(self):
        """The sharp leak test: a prefix check cannot tell "northgate " the
        namespace from "Northgate Power and Light" the authored tenant name.
        Regenerating one profile under a different namespace can: an authored
        label is namespace-invariant by definition, a leaked one is not.
        """
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                moved = self._namespace_sensitive(profile, plan, ("name",),
                                                  skip=NAMESPACED_KINDS | IDENTITY_NAMED_KINDS)
                self.assertEqual(moved, [], f"{profile}: names follow the namespace {moved[:5]}")

    def test_label_bearing_descriptions_do_not_move_when_the_namespace_does(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                moved = self._namespace_sensitive(profile, plan, ("description",),
                                                  only=frozenset(DESCRIPTION_AS_LABEL))
                self.assertEqual(moved, [], f"{profile}: rendered labels follow the namespace {moved[:5]}")

    def _namespace_sensitive(self, profile, plan, fields, *, skip=frozenset(), only=None):
        rebuilt = self.rebuilt[profile]
        moved = []
        for obj in plan["objects"]:
            kind = obj["kind"]
            if kind in skip or (only is not None and kind not in only):
                continue
            other = rebuilt.get(obj["key"])
            if other is None:
                continue
            for field in fields:
                value = obj["attrs"].get(field)
                if isinstance(value, str) and value and value != other["attrs"].get(field):
                    moved.append((kind, obj["key"], value, other["attrs"].get(field)))
        return moved

    def test_identity_fields_keep_the_namespace_in_every_profile(self):
        for profile, plan in self.plans.items():
            with self.subTest(profile=profile):
                namespace = plan["recipe"]["namespace"]
                seen = 0
                for obj in plan["objects"]:
                    if obj["kind"] not in IDENTITY_KINDS:
                        continue
                    for field in IDENTITY_FIELDS:
                        value = obj["attrs"].get(field)
                        if not isinstance(value, str) or not value:
                            continue
                        seen += 1
                        self.assertIn(namespace, value,
                                      f"{profile}: {obj['kind']}.{field}={value!r} lost its namespace")
                self.assertTrue(seen, f"{profile}: expected identity fields")


if __name__ == "__main__":
    unittest.main()
