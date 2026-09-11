"""Shared provider seams: reserved address space and graph-derived operations."""

from copy import deepcopy
import unittest

from estates.model import World, DesignError
from estates.operations_context import enrich
from estates.validate_operations import validate


def empty_provider_world():
    # Exercise the shared seams independently of the provider recipe/emitter.
    world = World({"profile": "enterprise-data-center", "address_pool": "10.0.0.0/12"})
    world.recipe["profile"] = "provider-backbone"
    world.site_prefixlen = 24
    return world


class ProviderIntegrationTests(unittest.TestCase):
    def test_small_site_growth_cannot_consume_noc_or_infrastructure_pools(self):
        world = empty_provider_world()
        world.reserve_sites(["dc-01", "pop-z", "ce-z"])
        self.assertEqual(world.allocations, {"dc-01": 0, "pop-z": 256, "ce-z": 257})
        self.assertEqual(str(world.site_network("dc-01")), "10.0.0.0/16")
        self.assertEqual(str(world.site_network("pop-z")), "10.1.0.0/24")
        world.reserve_sites(["ce-a", "dc-01", "pop-z", "ce-z"])
        self.assertEqual(world.allocations["ce-a"], 258)
        for slot in (1, 255, 3840):
            with self.subTest(slot=slot):
                bad = empty_provider_world()
                bad.allocations = {"dc-01": 0, "pop-z": slot}
                with self.assertRaises(DesignError):
                    bad.reserve_sites(["dc-01", "pop-z"])
        world.allocations["last-site"] = 3839
        with self.assertRaises(DesignError):
            world.reserve_sites(["dc-01", "new-site"])

    def operations_plan(self, remote_kind):
        world = empty_provider_world()
        world.add("tenant", "tenant", {"name": "Operator"})
        world.add("tenant", "tenant/cust-acme", {"name": "Acme"})
        for key, name in (("site/a", "Chicago PoP"), ("site/z", "Detroit PoP")):
            world.add("site", key, {"name": name, "physical_address": name + " address", "time_zone": "America/Chicago"}, {"tenant": "tenant"})
        world.add("provider", "provider/transport", {"name": "Span carrier"})
        world.add("provider_account", "account/span", {"account": "span-account"}, {"provider": "provider/transport"})
        world.add("provider_network", "carrier/transit", {"name": "Transit network"}, {"provider": "provider/transport"})
        world.add("circuit", "circuit/span", {"cid": "SPAN-001", "commit_rate": 100000000, "install_date": "2026-01-01"}, {"provider": "provider/transport", "tenant": "tenant", "provider_account": "account/span"})
        for side, endpoint in (("A", "site/a"), ("Z", "site/z" if remote_kind == "site" else "carrier/transit")):
            world.add("circuit_termination", "termination/" + side, {"term_side": side, "port_speed": 100000000}, {"circuit": "circuit/span", "termination": endpoint})
        world.add("virtual_circuit", "virtual-circuit/acme", {"cid": "ACME-001"}, {"tenant": "tenant/cust-acme"})
        enrich(world)
        return world.finish()

    def test_handoff_notes_follow_both_actual_ends_and_customer_service_owner(self):
        for remote_kind, name in (("site", "Detroit PoP"), ("provider_network", "Transit network")):
            with self.subTest(remote=remote_kind):
                plan = self.operations_plan(remote_kind)
                self.assertEqual(validate(plan), [])
                objects = {obj["key"]: obj for obj in plan["objects"]}
                note = objects["journal/circuit/span/handoff-plan"]["attrs"]["comments"]
                if remote_kind == "site":
                    self.assertIn("A termination: Chicago PoP\nZ termination: " + name, note)
                    self.assertIn("A handoff: 100000000 kbps\nZ handoff: 100000000 kbps", note)
                else:
                    self.assertIn("A termination: Chicago PoP\nZ network boundary: " + name, note)
                    self.assertIn("A handoff: 100000000 kbps", note)
                    self.assertIn("Remote interface and owner: unknown.", note)
                    self.assertNotIn("Z handoff:", note)
                assignment = objects["contact-assignment/virtual-circuit/acme"]
                self.assertEqual(assignment["refs"]["contact"], "contact/operations/tenant/cust-acme")
                for key, field, value, code in (
                    ("journal/circuit/span/handoff-plan", "comments", note.replace(name, "Wrong destination"), "operations-journal-facts"),
                    ("termination/Z" if remote_kind == "site" else "termination/A", "port_speed", 1000000, "operations-journal-facts"),
                    ("contact-assignment/virtual-circuit/acme", "contact", "contact/operations", "operations-contact"),
                ):
                    bad = deepcopy(plan)
                    obj = next(obj for obj in bad["objects"] if obj["key"] == key)
                    obj["refs" if field == "contact" else "attrs"][field] = value
                    bad["contracts"] = []
                    self.assertIn(code, {finding["code"] for finding in validate(bad)})


if __name__ == "__main__":
    unittest.main()
