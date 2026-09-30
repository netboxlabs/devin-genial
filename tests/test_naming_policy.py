"""Display names are authored; identities keep the namespace.

The visualization layer truncates graph node labels, so a namespace-prefixed
display name renders several distinct objects identically.  These tests pin the
split documented in estates/naming.py.
"""

import unittest

from estates.generate import generate
from estates.naming import NAMESPACED_KINDS, display_name, titleize


# Families whose human-facing name must never carry the namespace.
AUTHORED_KINDS = (
    "region", "site_group", "tenant", "tenant_group", "provider",
    "provider_network", "device_role", "rack_role", "rack_group", "platform",
    "circuit_type", "virtual_circuit_type", "cluster_type", "cluster_group",
    "rir", "role", "asn_range", "tunnel_group", "vlan_group", "tag",
    "contact_role", "power_panel", "inventory_item_role", "cable_bundle",
    "virtual_machine_type", "circuit_group", "wireless_lan_group",
)

# Kinds whose identity fields must still separate two estates on one target.
# Catalog-sourced kinds (hardware models and their parts) are shared by design.
IDENTITY_KINDS = ("tenant", "site", "device_role", "rack_role", "circuit_type",
                  "cluster_type", "rir", "role", "vlan_group", "tag")
IDENTITY_FIELDS = ("slug", "cid", "dns_name")


class NamingHelpers(unittest.TestCase):
    def test_titleize_preserves_acronyms(self):
        self.assertEqual(titleize("wan-edge"), "WAN Edge")
        self.assertEqual(titleize("access"), "Access")
        self.assertEqual(titleize("pdu"), "PDU")
        self.assertEqual(titleize("provider_edge"), "Provider Edge")

    def test_main_scoped_kinds_keep_the_namespace(self):
        for kind in ("owner", "owner_group", "export_template", "webhook",
                     "event_rule", "custom_field", "custom_link"):
            self.assertIn(kind, NAMESPACED_KINDS)
            self.assertTrue(display_name("acme", kind, "thing").startswith("acme "))

    def test_estate_scoped_kinds_drop_the_namespace(self):
        self.assertEqual(display_name("acme", "device_role", "wan-edge"), "WAN Edge")
        self.assertEqual(display_name("acme", "region", "Illinois"), "Illinois")


class GeneratedEstateNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"namespace": "zeta", "name": "Acme Estate"})
        cls.namespace = cls.plan["recipe"]["namespace"]

    def test_authored_display_names_are_namespace_free(self):
        offenders = []
        for obj in self.plan["objects"]:
            if obj["kind"] not in AUTHORED_KINDS:
                continue
            name = obj["attrs"].get("name")
            if not isinstance(name, str):
                continue
            lowered, ns = name.lower(), self.namespace.lower()
            if lowered.startswith(f"{ns} ") or lowered.startswith(f"{ns}-") or lowered.startswith(f"{ns}:"):
                offenders.append((obj["kind"], name))
        self.assertEqual(offenders, [], f"namespace leaked into display names: {offenders[:8]}")

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

    def test_identities_still_carry_the_namespace(self):
        seen = 0
        for obj in self.plan["objects"]:
            if obj["kind"] not in IDENTITY_KINDS:
                continue
            for field in IDENTITY_FIELDS:
                value = obj["attrs"].get(field)
                if not isinstance(value, str) or not value:
                    continue
                seen += 1
                self.assertIn(self.namespace, value,
                              f"{obj['kind']}.{field}={value!r} lost its namespace")
        self.assertTrue(seen, "expected identity fields to be present")


if __name__ == "__main__":
    unittest.main()
