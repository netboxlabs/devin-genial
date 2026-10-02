"""The provider backbone's BGP inventory: shape, attribution, growth and limits.

Every record here is documentation, not configuration. The tests below pin both
halves: the graph attribution a network engineer would check, and the fact that
nothing in the estate claims a configured, established or converged session.
"""

from copy import deepcopy
from ipaddress import ip_interface
import json
from pathlib import Path
import tomllib
import unittest

from estates.bgp import GROUPS, INVENTORY_NOTE, POLICIES
from estates.diode import LOADER_ONLY_KINDS, deliverable, loader_only_records
from estates.generate import generate
from estates.model import DesignError
from estates.turbobulk import (BRANCH_EXEMPT_KINDS, MAIN_SCOPED_KINDS, REST_CREATE_KINDS,
                               SPECS, SUPPORTED_REFS, _expected_change_diff_counts, _index,
                               _render_rest_create)
from estates.validate import validate
from lab.verify import ENDPOINTS

ROOT = Path(__file__).parents[1]
BGP_KINDS = ("bgp_routing_policy", "bgp_peer_group", "bgp_session")
# Words that would turn an inventory record into a claim about running routing.
FORBIDDEN_CLAIMS = ("converge", "established", "establishes", "is up", "advertising now",
                    "applied to", "pushed to", "deployed to")


def recipe():
    return tomllib.loads((ROOT / "profiles/provider-backbone.toml").read_text())


def index(plan):
    return {obj["key"]: obj for obj in plan["objects"]}


def of_kind(plan, kind):
    return {obj["key"]: obj for obj in plan["objects"] if obj["kind"] == kind}


class ProviderBgpShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe())
        cls.objects = index(cls.plan)

    def test_the_estate_carries_named_policies_peer_groups_and_every_session(self):
        self.assertEqual(len(of_kind(self.plan, "bgp_routing_policy")), len(POLICIES))
        self.assertEqual(len(of_kind(self.plan, "bgp_peer_group")), len(GROUPS))
        sessions = of_kind(self.plan, "bgp_session")
        # Three PoPs: a reflector pair (1 session), four clients against both
        # reflectors (8), two transit handoffs and three customer premises.
        self.assertEqual(len(sessions), 1 + 2 * (2 * 3 - 2) + 2 + 3)
        groups = {key: len([k for k in sessions if k.startswith(f"bgp-session/{key}")])
                  for key in ("ibgp", "transit", "customer")}
        self.assertEqual(groups, {"ibgp": 9, "transit": 2, "customer": 3})

    def test_ibgp_reflectors_sit_in_two_metros_by_permanent_pop_order(self):
        order = self.plan["reservations"]["provider-pop-order"]
        metro = {pop["key"]: pop["metro"] for pop in self.plan["recipe"]["pops"]}
        ordered = sorted(order, key=order.get)
        second = next(pop for pop in ordered if metro[pop] != metro[ordered[0]])
        reflectors = {f"device/pop-{ordered[0]}/pe-a", f"device/pop-{second}/pe-a"}
        clients, seen = set(), set()
        for key, obj in of_kind(self.plan, "bgp_session").items():
            if not key.startswith("bgp-session/ibgp/"):
                continue
            local, remote = obj["refs"]["device"], None
            remote = self.objects[obj["refs"]["remote_address"]]["refs"]["assigned_object"]
            remote = self.objects[remote]["refs"]["device"]
            self.assertIn(remote, reflectors, key)
            # Both ends peer from the in-band loopback, never a link address.
            for field in ("local_address", "remote_address"):
                port = self.objects[obj["refs"][field]]["refs"]["assigned_object"]
                self.assertEqual(self.objects[port]["attrs"]["name"], "lo0", key)
            self.assertEqual(obj["refs"]["local_as"], obj["refs"]["remote_as"])
            seen.add((local, remote))
            if local not in reflectors:
                clients.add(local)
        routers = {key for key, obj in self.objects.items()
                   if obj["kind"] == "device" and obj["refs"].get("role") == "role/provider-edge"}
        self.assertEqual(clients, routers - reflectors)
        # Exactly one record per adjacency: no reversed duplicate.
        self.assertFalse({pair for pair in seen if tuple(reversed(pair)) in seen})

    def test_transit_and_customer_sessions_are_attributed_from_their_own_circuits(self):
        sessions = of_kind(self.plan, "bgp_session")
        for side in ("a", "b"):
            session = sessions[f"bgp-session/transit/{side}"]
            local = self.objects[session["refs"]["local_address"]]
            port = self.objects[local["refs"]["assigned_object"]]
            self.assertEqual(port["refs"]["device"], session["refs"]["device"])
            self.assertEqual(session["refs"]["remote_as"], f"asn/transit-{side}")
            # The far end is deliberately a prefix, not an invented address: the
            # remote interface and its owner are unknown at a carrier handoff.
            self.assertNotIn("remote_address", session["refs"])
            link = self.objects[session["refs"]["remote_prefix"]]
            self.assertEqual(link["attrs"]["prefix"],
                             str(ip_interface(local["attrs"]["address"]).network))
            self.assertEqual(link["attrs"]["prefix"].split("/")[1], "31")
        for premises in ("ce-harbor-logistics-chicago-west-001",):
            session = sessions[f"bgp-session/customer/{premises}"]
            circuit = self.objects[f"circuit/customer/{premises}"]
            self.assertEqual(session["refs"]["tenant"], circuit["refs"]["tenant"])
            self.assertEqual(session["refs"]["remote_as"],
                             self.objects[f"site/{premises}"]["refs"]["asns"][0])
            local = ip_interface(self.objects[session["refs"]["local_address"]]["attrs"]["address"])
            remote = ip_interface(self.objects[session["refs"]["remote_address"]]["attrs"]["address"])
            self.assertEqual(local.network, remote.network)
            self.assertNotEqual(local.ip, remote.ip)
            self.assertEqual(self.objects[session["refs"]["device"]]["refs"]["role"],
                             "role/provider-edge")

    def test_display_names_are_authored_and_namespace_free(self):
        namespace = self.plan["recipe"]["namespace"]
        for obj in self.plan["objects"]:
            if obj["kind"] not in BGP_KINDS:
                continue
            name = obj["attrs"]["name"]
            # Branch-scoped kinds, so they are not in naming.NAMESPACED_KINDS:
            # the graph view truncates labels and a prefix would hide the peers.
            self.assertNotIn(namespace, name, obj["key"])
            self.assertLessEqual(len(name), 256, obj["key"])
            self.assertLessEqual(len(obj["attrs"]["description"]), 200, obj["key"])

    def test_only_the_provider_profile_emits_bgp_records(self):
        other = generate({"headquarters": 0, "branches": {"small": 1}})
        self.assertFalse([obj for obj in other["objects"] if obj["kind"].startswith("bgp_")])
        with self.assertRaisesRegex(DesignError, "provider backbone only"):
            from estates import bgp

            class _World:
                recipe = {"profile": "regional-bank"}
            bgp.enrich(_World())


class ProviderBgpDualStackTests(unittest.TestCase):
    """With ipv6_pool every peering gains an IPv6 twin on the same endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe() | {"ipv6_pool": "2001:db8::/32"})
        cls.objects = index(cls.plan)

    def test_every_session_has_an_ipv6_twin_on_ipv6_addresses(self):
        sessions = of_kind(self.plan, "bgp_session")
        v4 = {key for key in sessions if not key.endswith("/ipv6")}
        self.assertEqual({key + "/ipv6" for key in v4}, set(sessions) - v4)
        for key in v4:
            twin = sessions[key + "/ipv6"]
            for field in ("device", "remote_as", "peer_group"):
                self.assertEqual(twin["refs"][field], sessions[key]["refs"][field], key)
            local = ip_interface(self.objects[twin["refs"]["local_address"]]["attrs"]["address"])
            self.assertEqual(local.version, 6, key)
            if "remote_prefix" in twin["refs"]:
                # Transit: the far end stays a /127 prefix, never an invented address.
                self.assertEqual(self.objects[twin["refs"]["remote_prefix"]]["attrs"]["prefix"], str(local.network))
                self.assertEqual(local.network.prefixlen, 127)
            else:
                remote = ip_interface(self.objects[twin["refs"]["remote_address"]]["attrs"]["address"])
                self.assertEqual(remote.version, 6, key)

    def test_a_missing_ipv6_twin_is_rejected(self):
        plan = deepcopy(self.plan)
        plan["objects"] = [obj for obj in plan["objects"] if obj["key"] != "bgp-session/transit/a/ipv6"]
        self.assertIn("provider-bgp-inventory", {finding["code"] for finding in validate(plan)})


class ProviderBgpScopeTests(unittest.TestCase):
    """Inventory, never execution — the inert-webhook precedent."""

    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe())

    def test_every_record_states_that_nothing_is_configured_or_established(self):
        records = [obj for obj in self.plan["objects"] if obj["kind"] in BGP_KINDS]
        self.assertTrue(records)
        for obj in records:
            self.assertEqual(obj["attrs"]["comments"], INVENTORY_NOTE, obj["key"])
            text = " ".join(str(value) for value in obj["attrs"].values()).lower()
            for claim in FORBIDDEN_CLAIMS:
                self.assertNotIn(claim, text.replace(INVENTORY_NOTE.lower(), ""), obj["key"])

    def test_named_policies_carry_no_rules_and_no_rule_bearing_kinds_exist(self):
        kinds = {obj["kind"] for obj in self.plan["objects"] if obj["kind"].startswith("bgp_")}
        self.assertEqual(kinds, set(BGP_KINDS))
        for obj in self.plan["objects"]:
            if obj["kind"] == "bgp_routing_policy":
                self.assertEqual(obj["refs"], {})

    def test_the_span_maintenance_snapshot_leaves_every_session_untouched(self):
        from estates.span_scenario import create
        raw = recipe()
        raw["demo"] = "provider-span-maintenance"
        baseline = generate(raw)
        envelope = create(baseline)
        before = of_kind(baseline, "bgp_session")
        after = {obj["key"]: obj for obj in envelope["plans"]["changed"]["objects"]
                 if obj["kind"] == "bgp_session"}
        self.assertTrue(before)
        self.assertEqual(before, after)


class ProviderBgpValidationTests(unittest.TestCase):
    """The failing-mutation check: reverting or corrupting the feature must fail."""

    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe())

    def findings(self, mutate):
        plan = deepcopy(self.plan)
        mutate({obj["key"]: obj for obj in plan["objects"]}, plan)
        return sorted({finding["code"] for finding in validate(plan)})

    def test_a_healthy_estate_has_no_findings(self):
        self.assertEqual(validate(self.plan), [])

    def test_removing_the_bgp_inventory_is_rejected(self):
        def revert(objects, plan):
            plan["objects"] = [obj for obj in plan["objects"] if obj["kind"] not in BGP_KINDS]
        self.assertIn("provider-bgp-inventory", self.findings(revert))

    def test_a_dropped_or_extra_session_is_rejected(self):
        def drop(objects, plan):
            plan["objects"].remove(objects["bgp-session/transit/a"])
        self.assertIn("provider-bgp-inventory", self.findings(drop))

        def rule(objects, plan):
            plan["objects"].append({"key": "bgp-rule/1", "kind": "bgp_routing_policy_rule",
                                    "attrs": {"name": "permit"}, "refs": {}, "meta": {}})
        self.assertIn("provider-bgp-inventory", self.findings(rule))

    def test_a_misattributed_peering_is_rejected(self):
        order = self.plan["reservations"]["provider-pop-order"]
        first = min(order, key=order.get)
        key = f"bgp-session/ibgp/pop-{first}/pe-b/pop-{first}/pe-a"

        def wrong_remote(objects, plan):
            objects[key]["refs"]["remote_address"] = objects[key]["refs"]["local_address"]
        self.assertIn("provider-bgp-session", self.findings(wrong_remote))

        def wrong_asn(objects, plan):
            objects["bgp-session/customer/ce-harbor-logistics-chicago-west-001"] \
                ["refs"]["remote_as"] = "asn/operator"
        self.assertIn("provider-bgp-session", self.findings(wrong_asn))

        def wrong_prefix(objects, plan):
            objects["bgp-session/transit/a"]["refs"]["remote_prefix"] = "prefix/link/circuit/noc/a"
        self.assertIn("provider-bgp-session", self.findings(wrong_prefix))

    def test_a_corrupted_policy_or_peer_group_is_rejected(self):
        def weight(objects, plan):
            objects["bgp-routing-policy/transit-in"]["attrs"]["weight"] = 1
        self.assertIn("provider-bgp-policy", self.findings(weight))

        def unbound(objects, plan):
            objects["bgp-peer-group/customer"]["refs"].pop("import_policies")
        self.assertIn("provider-bgp-group", self.findings(unbound))

    def test_dropping_the_inventory_only_note_is_rejected(self):
        def claim(objects, plan):
            objects["bgp-session/transit/b"]["attrs"]["comments"] = "Session established."
        self.assertIn("provider-bgp-scope-text", self.findings(claim))


class ProviderBgpGrowthTests(unittest.TestCase):
    def test_appending_a_pop_and_a_customer_never_moves_an_existing_session(self):
        baseline = generate(recipe())
        before = of_kind(baseline, "bgp_session")
        raw = recipe()
        raw["pops"].append({"key": "milwaukee-north", "metro": "milwaukee"})
        raw["customers"][0]["sites"].append({"pop": "milwaukee-north", "count": 1})
        raw["customers"].append({"key": "zeta-retail", "hub_pop": "cleveland-east",
                                 "sites": [{"pop": "chicago-west", "count": 1},
                                           {"pop": "cleveland-east", "count": 1}]})
        grown = generate(raw, previous=baseline)
        self.assertEqual(validate(grown), [])
        after = of_kind(grown, "bgp_session")
        self.assertLess(len(before), len(after))
        for key, obj in before.items():
            self.assertEqual(after[key], obj, key)
        # The reflector pair is a permanent ordinal, so it never moves either.
        for key, obj in of_kind(baseline, "bgp_peer_group").items():
            self.assertEqual(of_kind(grown, "bgp_peer_group")[key], obj, key)


class ProviderBgpTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate(recipe())
        cls.objects = _index(cls.plan)

    def test_the_wire_package_omits_bgp_and_records_the_omission(self):
        self.assertTrue(set(BGP_KINDS) <= LOADER_ONLY_KINDS)
        delivered = deliverable(self.objects)
        self.assertFalse([key for key, obj in delivered.items() if obj["kind"] in BGP_KINDS])
        omitted = loader_only_records(self.plan)
        self.assertEqual({kind: omitted["counts"][kind] for kind in BGP_KINDS},
                         {"bgp_routing_policy": 4, "bgp_peer_group": 3, "bgp_session": 14})

    def test_the_loader_covers_every_bgp_kind_on_the_rest_create_path(self):
        for kind in BGP_KINDS:
            self.assertIn(kind, SPECS)
            self.assertIn(kind, SUPPORTED_REFS)
            self.assertIn(kind, REST_CREATE_KINDS)
            self.assertIn(kind, ENDPOINTS)
            # Verified against netbox_bgp 0.20.1: a plugin's Django app_label is
            # its package name, and its viewsets register under /api/plugins/bgp/.
            self.assertTrue(SPECS[kind][0].startswith("netbox_bgp."), kind)
            self.assertTrue(SPECS[kind][1].startswith("/api/plugins/bgp/"), kind)
            self.assertEqual(SPECS[kind][1], "/api/" + ENDPOINTS[kind] + "/")
            # Branch-scoped, not main-scoped: the rows live and die with the branch.
            self.assertNotIn(kind, BRANCH_EXEMPT_KINDS)
            self.assertNotIn(kind, MAIN_SCOPED_KINDS)
        for key, obj in self.objects.items():
            if obj["kind"] in BGP_KINDS:
                self.assertFalse(set(obj["refs"]) - SUPPORTED_REFS[obj["kind"]], key)

    def test_every_bgp_row_keeps_its_own_create_change_diff(self):
        expected = _expected_change_diff_counts(self.objects)
        self.assertEqual(expected["netbox_bgp.bgpsession"], 14)
        self.assertEqual(expected["netbox_bgp.bgppeergroup"], 3)
        self.assertEqual(expected["netbox_bgp.routingpolicy"], 4)

    def test_the_rest_payload_resolves_every_reference_to_a_target_id(self):
        ids = {key: position for position, key in enumerate(self.objects, 1)}
        group = _render_rest_create(self.objects["bgp-peer-group/transit"], ids, self.objects)
        self.assertEqual(group["import_policies"], [ids["bgp-routing-policy/transit-in"]])
        self.assertEqual(group["local_as"], ids["asn/operator"])
        session = _render_rest_create(self.objects["bgp-session/transit/a"], ids, self.objects)
        self.assertEqual(session["remote_prefix"], ids["prefix/link/circuit/transit/a"])
        self.assertEqual(session["status"], "active")
        self.assertNotIn("remote_address", session)

    def test_the_type_audit_records_bgp_as_loader_only(self):
        audit = json.loads((ROOT / "catalog/type-coverage.json").read_text())
        rows = {row["kind"]: row for row in audit["native_without_sdk"] if "kind" in row}
        for kind in BGP_KINDS:
            self.assertEqual(rows[kind]["category"], "loader_only", kind)
            self.assertEqual(rows[kind]["native_model"], SPECS[kind][0], kind)
            self.assertIs(rows[kind]["sdk_top_level"], False, kind)


if __name__ == "__main__":
    unittest.main()
