"""Status-only maintenance boundaries and graph-derived customer explanations."""

from collections import Counter, defaultdict, deque
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tomllib
import unittest

from estates.generate import generate
from estates.model import DesignError, canonical, digest
from estates.span_scenario import (_directions, _expected, _graph, _maintenance, _routes,
                                  _traffic, create, markdown, verify)
from estates.validate import validate
from estates.validate_provider import _loads, _tree


ROOT = Path(__file__).parents[1]


def recipe(name="provider-backbone"):
    return tomllib.loads((ROOT / "profiles" / (name + ".toml")).read_text())


def old_loads(adjacency, flows, excluded=None):
    """Pre-extraction implementation retained here to detect behavioral changes."""
    loads, sources = Counter(), defaultdict(list)
    for (origin, destination), peak in flows.items():
        sources[origin].append((destination, peak))
    for origin, destinations in sources.items():
        predecessors, pending = {origin: None}, deque([origin])
        while pending:
            node = pending.popleft()
            for _, peer, edge in adjacency.get(node, ()):
                if edge != excluded and peer not in predecessors:
                    predecessors[peer] = (node, edge)
                    pending.append(peer)
        for destination, peak in destinations:
            if destination not in predecessors:
                return None
            while destination != origin:
                previous, edge = predecessors[destination]
                loads[(edge, previous, destination)] += peak
                destination = previous
    return loads


class SpanScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(recipe())
        cls.envelope = create(cls.baseline)

    def test_actual_customer_paths_and_headroom_have_executable_witnesses(self):
        e = self.envelope
        self.assertEqual(e["subject"], "circuit/backbone/seed-01")
        site = "site/ce-harbor-logistics-cleveland-east-001"
        hub = "site/ce-harbor-logistics-chicago-west-001"
        self.assertEqual(e["affected"]["premises"], [site])
        self.assertEqual(e["affected"]["hubs"], [hub])
        self.assertEqual(e["affected"]["unchanged_premises_sharing_increased_load"], ["site/ce-harbor-logistics-detroit-south-001"])
        expected = ["pair/pop-cleveland-east", "circuit/backbone/seed-02", "pair/pop-detroit-south", "circuit/backbone/seed-03", "pair/pop-chicago-west"]
        before, after = (next(row for row in e["paths"][stage] if row["site"] == site) for stage in ("baseline", "changed"))
        self.assertEqual([hop["edge"] for hop in before["hops"]], [e["subject"]])
        self.assertEqual([hop["edge"] for hop in after["hops"]], expected)
        objects = {obj["key"]: obj for obj in self.baseline["objects"]}
        for stage, paths in e["paths"].items():
            summed = Counter()
            for row in paths:
                current = row["from_pe"]
                for hop in row["hops"]:
                    self.assertEqual(hop["from_pe"], current)
                    edge = e["edges"][hop["edge"]]
                    self.assertEqual({end["device"] for end in edge["ends"]}, {hop["from_pe"], hop["to_pe"]})
                    for end in edge["ends"]:
                        self.assertEqual(objects[end["interface"]]["refs"]["device"], end["device"])
                        cable = objects[end["cable"]]
                        self.assertIn(end["interface"], cable["refs"].values())
                        if edge["kind"] == "span":
                            self.assertIn(end["termination"], cable["refs"].values())
                            self.assertEqual(objects[end["termination"]]["refs"]["circuit"], hop["edge"])
                    summed[(hop["edge"], hop["from_pe"], hop["to_pe"])] += row["offered_kbps"]
                    current = hop["to_pe"]
                self.assertEqual(current, row["to_pe"])
            for row in e["capacity"]["directions"]:
                field = "baseline_kbps" if stage == "baseline" else "maintenance_kbps"
                self.assertEqual(row[field], summed[(row["edge"], row["from_pe"], row["to_pe"])] )
        active = [row for row in e["capacity"]["directions"] if row["maintenance_state"] == "active"]
        self.assertEqual(min(Decimal(row["headroom_kbps"]) for row in active), 79900000)

    def test_exact_findings_include_lost_additional_failure_margin(self):
        e = self.envelope
        self.assertEqual(validate(e["plans"]["baseline"]), [])
        actual = Counter((row["code"], row["object"]) for row in validate(e["plans"]["changed"]))
        self.assertEqual(actual, Counter((row["code"], row["object"]) for row in e["expected_findings"]))
        self.assertEqual(Counter(row["code"] for row in e["expected_findings"]), {
            "optics-path": 2, "provider-circuit-path": 1, "provider-link-connectivity": 5,
            "provider-router-connectivity": 4, "provider-customer-route": 2})
        self.assertNotIn(("provider-backbone-connectivity", "plan"), actual)
        self.assertTrue(verify(e)["modeled_routes_reachable"])

    def test_exact_inverse_preserves_every_object_and_input_bytes(self):
        before = canonical(self.baseline)
        e = create(self.baseline)
        frozen = canonical(e)
        self.assertEqual(e["changes"]["counts"], dict(create=0, update=1, delete=0))
        update = e["changes"]["update"][0]
        old, new = deepcopy(update["before"]), deepcopy(update["after"])
        self.assertEqual((old["attrs"].pop("status"), new["attrs"].pop("status")), ("active", "offline"))
        self.assertEqual(old, new)
        inverse = e["restoration"]["inverse"]
        self.assertEqual(inverse["create"], [])
        self.assertEqual(inverse["delete"], [])
        restored = deepcopy(e["plans"]["changed"])
        restored["objects"] = [deepcopy(inverse["update"][0]["after"]) if obj["key"] == e["subject"] else obj for obj in restored["objects"]]
        self.assertEqual(canonical(restored), before)
        self.assertEqual(digest(restored), e["restoration"]["baseline_sha256"])
        self.assertEqual(validate(restored), [])
        verify(e); markdown(e)
        self.assertEqual(canonical(e), frozen)
        self.assertEqual(canonical(self.baseline), before)
        self.assertEqual(canonical(create(self.baseline)), frozen)
        self.assertFalse(e["execution"]["applied_to_target"])
        self.assertEqual(e["execution"]["transition"], "status-update-unverified")

    def test_offline_span_is_installed_capacity_but_no_available_headroom(self):
        for row in self.envelope["capacity"]["directions"]:
            if row["edge"] != self.envelope["subject"]:
                continue
            self.assertEqual(row["limit_kbps"], 100000000)
            self.assertEqual(Decimal(row["usable_kbps"]), 80000000)
            self.assertEqual(Decimal(row["baseline_available_kbps"]), 80000000)
            self.assertEqual(row["maintenance_state"], "offline")
            self.assertEqual(Decimal(row["maintenance_available_kbps"]), 0)
            self.assertEqual(Decimal(row["headroom_kbps"]), 0)
            self.assertEqual(row["maintenance_kbps"], 0)

    def test_contact_roles_follow_real_assignments_not_carrier_customer_guess(self):
        objects = {obj["key"]: obj for obj in self.baseline["objects"]}
        expected = {"customer": "contact/operations/tenant/cust-harbor-logistics",
                    "operator": "contact/operations", "carrier": "contact/provider/transport-a"}
        for scope, rows in self.envelope["contacts"].items():
            self.assertTrue(rows)
            for row in rows:
                actual = objects[row["assignment"]]
                self.assertEqual(actual["refs"], {field: row[field] for field in ("object", "contact", "role")})
                if scope in expected:
                    self.assertEqual(row["contact"], expected[scope])
                else:
                    self.assertIn(row["object"], {end["site"] for end in self.envelope["edges"][self.envelope["subject"]]["ends"]})

    def test_unavailable_unused_wrong_profile_and_unhealthy_subjects_fail(self):
        for subject in ("circuit/backbone/seed-02", "circuit/transit/a", "circuit/noc/a", "device/pop-chicago-west/pe-a", "missing", 1):
            with self.subTest(subject=subject), self.assertRaises(DesignError):
                create(self.baseline, subject)
        offline = deepcopy(self.baseline)
        next(obj for obj in offline["objects"] if obj["key"] == self.envelope["subject"])["attrs"]["status"] = "offline"
        with self.assertRaises(DesignError): create(offline, self.envelope["subject"])
        with self.assertRaises(DesignError): create(generate(dict(profile="enterprise-data-center", namespace="wrong-kind")))
        malformed = deepcopy(self.baseline); malformed["hardware_digest"] = "forged"
        with self.assertRaises(DesignError): create(malformed)

    def test_correlated_extra_defects_or_metadata_edits_cannot_be_approved(self):
        for change in ("cable", "commit", "journal", "duplicate", "recipe", "reservations", "status"):
            e = deepcopy(self.envelope)
            plan = e["plans"]["changed"]
            if change == "cable":
                next(obj for obj in plan["objects"] if obj["kind"] == "cable")["attrs"]["status"] = "planned"
            elif change == "commit":
                next(obj for obj in plan["objects"] if obj["key"] == e["subject"])["attrs"]["commit_rate"] -= 1
            elif change == "journal":
                plan["objects"] = [obj for obj in plan["objects"] if obj["kind"] != "journal_entry"]
            elif change == "duplicate":
                plan["objects"].append(deepcopy(plan["objects"][0]))
            elif change == "recipe":
                plan["recipe"]["reserve_fraction"] = 0.1
            elif change == "reservations":
                plan["reservations"]["provider-pop-order"]["chicago-west"] = 9
            else:
                next(obj for obj in plan["objects"] if obj["key"] == e["subject"])["attrs"]["status"] = "decommissioned"
            e["expected_findings"] = [dict(code=row["code"], object=row["object"]) for row in validate(plan)]
            with self.subTest(change=change), self.assertRaises(DesignError): verify(e)

    def test_forged_paths_contacts_headroom_findings_and_execution_rejected(self):
        for field in ("paths", "premises", "contacts", "capacity", "expected_findings", "resilience", "restoration", "execution", "selection"):
            e = deepcopy(self.envelope)
            if field == "paths": e[field]["changed"][0]["hops"][0]["edge"] = e["subject"]
            elif field == "premises": next(iter(e[field].values()))["tenant"] = "tenant"
            elif field == "contacts": e[field]["customer"][0]["contact"] = e[field]["carrier"][0]["contact"]
            elif field == "capacity": e[field]["directions"][0]["headroom_kbps"] = "80000000"
            elif field in ("expected_findings", "resilience"): e[field].pop()
            elif field == "restoration": e[field]["baseline_sha256"] = "forged"
            elif field == "execution": e[field]["applied_to_target"] = True
            else: e[field]["span"] = "circuit/backbone/seed-03"
            with self.subTest(field=field), self.assertRaises(DesignError): verify(e)
        for malformed in ({}, None, {"schema_version": True, "scenario": "provider-span-maintenance"}):
            with self.assertRaises(DesignError): verify(malformed)

    def test_reordered_graph_retains_selection_and_physical_evidence(self):
        reordered = deepcopy(self.baseline)
        reordered["objects"].reverse(); reordered["contracts"].reverse()
        e = create(reordered)
        for field in ("subject", "selection", "paths", "edges", "premises", "capacity", "contacts", "expected_findings"):
            self.assertEqual(e[field], self.envelope[field], field)
        self.assertTrue(verify(e)["exact_inverse_restoration"])
        explicit = create(self.baseline, "circuit/backbone/seed-03")
        self.assertEqual(explicit["subject"], "circuit/backbone/seed-03")
        self.assertTrue(verify(explicit)["one_circuit_status_changed"])

    def test_frozen_scenario_survives_independent_growth_and_explicit_selection(self):
        raw = deepcopy(self.baseline["recipe"])
        raw["pops"].insert(0, dict(key="aaa-new", metro="milwaukee"))
        raw["customers"][0]["sites"].append(dict(pop="aaa-new", count=2))
        raw["customers"].insert(0, dict(key="aaa-customer", hub_pop="chicago-west", sites=[dict(pop="chicago-west"), dict(pop="detroit-south")]))
        grown = generate(raw, previous=self.baseline)
        old_bytes = canonical(self.envelope)
        e = create(grown, self.envelope["subject"])
        self.assertTrue(verify(e)["exact_inverse_restoration"])
        self.assertTrue(verify(self.envelope)["exact_inverse_restoration"])
        self.assertEqual(canonical(self.envelope), old_bytes)
        self.assertNotEqual(e["checks"]["plan_sha256"]["baseline"], self.envelope["checks"]["plan_sha256"]["baseline"])
        self.assertEqual(e["edges"][e["subject"]], self.envelope["edges"][self.envelope["subject"]])

    def test_panels_dualstack_multi_customer_recipe_and_bounded_report(self):
        e = create(generate(recipe("provider-maintenance")))
        self.assertTrue(verify(e)["directed_capacity_sufficient"])
        report = markdown(e)
        self.assertIn(e["subject"], report)
        for end in e["edges"][e["subject"]]["ends"]:
            self.assertIn(end["interface"], report)
        self.assertIn("cust-harbor-logistics", report)
        self.assertIn("cust-cedar-retail", report)
        self.assertIn("offline", report)
        self.assertIn("not executed failover", report)
        self.assertIn("unchanged premise routes", report)
        self.assertIn("status update and restoration remain unqualified", report)
        for heading, next_heading in (("## Directed headroom", "## Responsible contacts"), ("## Responsible contacts", "## Further-failure protection")):
            section = report.split(heading)[1].split(next_heading)[0]
            self.assertLessEqual(sum(line.startswith("|") for line in section.splitlines()), 14)

    def test_factored_bfs_preserves_predecessor_and_load_behavior(self):
        graph = _graph(self.baseline)
        adjacency = graph[4]
        _, _, flows, _ = _traffic(self.baseline, graph)
        flows.update({(router, router): 70 for router in adjacency})
        for removed in (None, *graph[3]):
            self.assertEqual(_loads(adjacency, flows, removed), old_loads(adjacency, flows, removed))
        diamond = {"a": [("first", "b", "first"), ("second", "c", "second")],
                   "b": [("last-b", "d", "last-b")], "c": [("last-c", "d", "last-c")], "d": []}
        self.assertEqual(_tree(diamond, "a")["d"], ("b", "last-b"))
        self.assertEqual(_tree(diamond, "a", "first")["d"], ("c", "last-c"))
        self.assertIsNone(_loads(diamond, {("d", "a"): 1}))

    def test_larger_mesh_can_retain_checked_margin_and_same_pe_flow(self):
        raw = recipe()
        raw["pops"].extend([dict(key="milwaukee-north", metro="milwaukee"), dict(key="chicago-east", metro="chicago")])
        for entry in raw["customers"][0]["sites"]:
            entry["count"] = 4
        raw["customers"][0]["sites"].extend([dict(pop="milwaukee-north", count=2), dict(pop="chicago-east", count=2)])
        raw["customers"].append(dict(key="zeta-retail", hub_pop="cleveland-east", site_peak_mbps=400,
                                     sites=[dict(pop="chicago-west", count=1), dict(pop="cleveland-east", count=1)]))
        grown = generate(raw, previous=self.baseline)
        e = create(grown, "circuit/backbone/chicago-east/b")
        self.assertEqual(len(e["affected"]["premises"]), 2)
        self.assertEqual(e["resilience"], [])
        self.assertEqual(len(e["expected_findings"]), 3)
        self.assertTrue(verify(e)["checked_further_failure_margin_retained"])
        self.assertIn("Further-failure protection is retained", markdown(e))
        self.assertNotIn("protection is reduced", markdown(e))
        same_pe = [row for row in e["paths"]["baseline"] if row["from_pe"] == row["to_pe"]]
        self.assertTrue(same_pe)
        self.assertTrue(all(row["hops"] == [] and row["offered_kbps"] > 0 for row in same_pe))

    def test_directions_do_not_sum_full_duplex_or_use_wrong_units(self):
        edges = {"ab": dict(limit_kbps=100000000)}
        before = Counter({("ab", "a", "b"): 60000000, ("ab", "b", "a"): 60000000})
        rows = _directions(edges, before, before, Decimal("0.8"), "elsewhere")
        self.assertEqual(len(rows), 2)
        self.assertEqual({Decimal(row["headroom_kbps"]) for row in rows}, {20000000})
        self.assertEqual({row["maintenance_kbps"] for row in rows}, {60000000})
        adjacency = {"a": [("ab", "b", "ab")], "b": [("ab", "a", "ab")]}
        _, _, good = _maintenance(adjacency, edges, {("a", "b"): 80000000}, "elsewhere", Decimal("0.8"))
        self.assertTrue(good)
        _, _, good = _maintenance(adjacency, edges, {("a", "b"): 80000001}, "elsewhere", Decimal("0.8"))
        self.assertFalse(good)

    def test_secondary_capacity_findings_preserve_multiplicity(self):
        # A small mathematical graph exercises two distinct further failures of
        # the same directed edge. It is not an invented hardware recipe.
        links = [("0-BE", "B", "E"), ("1-ED", "E", "D"), ("2-BC", "B", "C"),
                 ("3-CD", "C", "D"), ("4-AC", "A", "C"), ("5-AD", "A", "D")]
        adjacency = {node: [] for node in "ABCDE"}
        edges = {}
        for key, a, b in links:
            adjacency[a].append((key, b, key)); adjacency[b].append((key, a, key))
            edges[key] = dict(kind="span", limit_kbps=100, ends=[])
        for rows in adjacency.values(): rows.sort()
        flows, usable = {("A", "D"): 50, ("B", "D"): 30, ("C", "D"): 20}, Decimal("0.8")
        for removed in (None, *edges):
            self.assertTrue(_maintenance(adjacency, edges, flows, removed, usable)[2])
        maintained, loads, good = _maintenance(adjacency, edges, flows, "5-AD", usable)
        self.assertTrue(good)
        self.assertEqual(loads[("3-CD", "C", "D")], 70)
        expected, witnesses = _expected(({}, {}, {}, edges, adjacency, {}), maintained, flows, "5-AD", usable)
        self.assertEqual(Counter(row["object"] for row in expected if row["code"] == "provider-route-capacity"),
                         {"3-CD": 2, "0-BE": 1, "1-ED": 1})
        failed = {row["removed_span"] for row in witnesses if row["code"] == "provider-route-capacity" and row["object"] == "3-CD"}
        self.assertEqual(failed, {"0-BE", "1-ED"})


if __name__ == "__main__":
    unittest.main()
