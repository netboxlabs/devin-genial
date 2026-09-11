"""Dual-stack navigation follows actual references and stays bounded."""

from copy import deepcopy
import ipaddress
import unittest

from estates.generate import generate
from estates.model import canonical
from estates.report import markdown


def ipv6_section(plan):
    return markdown(plan).split("## Dual-stack IP inventory\n", 1)[1].split("\n## ", 1)[0]


class IPv6ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"profile": "provider-backbone", "ipv6_pool": "2001:db8::/32"})

    def test_bounded_primary_and_service_navigation_does_not_mutate_plan(self):
        before = canonical(self.plan)
        text = ipv6_section(self.plan)
        self.assertEqual(canonical(self.plan), before)
        self.assertIn("2001:db8::/32", text)
        self.assertIn("/127:", text)
        self.assertIn("/128:", text)
        self.assertIn("IPv6 interface", text)
        self.assertIn("Service example", text)
        self.assertEqual(sum(line.startswith("| ") for line in text.splitlines()), 8)
        self.assertIn("lo0 [", text)
        self.assertNotIn("bank radio", text)
        self.assertIn("unknown transit far end stays unknown", text)
        self.assertIn("no routing, RA/DHCPv6, application binding or live ingestion", text)

    def test_reference_removal_changes_counts_without_changing_address_inventory(self):
        changed = deepcopy(self.plan)
        ipv6 = {obj["key"] for obj in changed["objects"] if obj["kind"] == "ip_address"
                and ipaddress.ip_interface(obj["attrs"]["address"]).version == 6}
        for obj in changed["objects"]:
            obj["refs"].pop("primary_ip6", None)
            if obj["kind"] == "service":
                obj["refs"]["ipaddresses"] = [key for key in obj["refs"].get("ipaddresses", []) if key not in ipv6]
        changed["contracts"] = []
        text = ipv6_section(changed)
        self.assertIn(f"{len(ipv6):,} IPv6 addresses", text)
        self.assertIn("0 devices and 0 VMs have primary IPv6 references", text)
        self.assertIn("0 service objects list both address families", text)
        self.assertNotIn("| Primary", text)
        self.assertNotIn("| Service example", text)

    def test_ipv4_only_omits_section_and_scoped_names_are_escaped(self):
        v4 = generate({"profile": "enterprise-data-center"})
        self.assertNotIn("## Dual-stack IP inventory", markdown(v4))
        changed = deepcopy(self.plan)
        for device in changed["objects"]:
            if device["kind"] == "device" and device["refs"].get("primary_ip6"):
                device["attrs"]["name"] = "Ward|<router>"
        text = ipv6_section(changed)
        self.assertIn("Ward&#124;&lt;router&gt; [device/", text)
        self.assertNotIn("Ward|<router>", text)


if __name__ == "__main__":
    unittest.main()
