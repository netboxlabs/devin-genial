"""The showcase sidecar: plan-derived tour records, honest F1-F5 numbers, receipt-scoped writes."""

import copy
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from estates import branch, showcase
from estates.generate import generate
from estates.model import canonical
from estates.showcase import ShowcaseError, build, check, create, seed, unseed, verify
from estates.turbobulk import LoadError, WriteRejected

ROOT = Path(__file__).resolve().parents[1]


def _plan(profile="provider-backbone"):
    return generate(tomllib.loads((ROOT / f"profiles/{profile}.toml").read_text()))


class Derivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = _plan()
        cls.artifact = create(cls.plan)

    def test_filters_are_namespaced_retirable_and_select_plan_objects(self):
        self.assertTrue(self.artifact["filters"])
        for tenancy in ("shared", "dedicated"):
            recipe = dict(self.plan["recipe"], tenancy=tenancy)
            plan = dict(self.plan, recipe=recipe)
            matcher = branch.retirement_matcher("/api/extras/saved-filters/", recipe["namespace"],
                                                dedicated=tenancy == "dedicated")
            for f in create(plan)["filters"]:
                self.assertTrue(matcher(f["name"]), f["name"])
                self.assertTrue(f["slug"].startswith(recipe["namespace"] + "-"))
                self.assertGreater(f["count"], 0)

    def test_end_of_life_follows_catalog_lifecycle_dates(self):
        f = next(f for f in self.artifact["filters"] if f["label"] == "End-of-life hardware")
        self.assertIn("juniper-mx204", f["parameters"]["device_type"])  # TSB107750, 2026-06-15
        early = copy.deepcopy(self.plan)
        early["recipe"]["as_of"] = "2026-01-01"
        f = next((f for f in create(early)["filters"] if f["label"] == "End-of-life hardware"), None)
        self.assertNotIn("juniper-mx204", (f or {"parameters": {"device_type": []}})["parameters"]["device_type"])

    def test_dashboard_is_consistent_and_names_the_carrier(self):
        dashboard = self.artifact["dashboard"]
        self.assertEqual({e["id"] for e in dashboard["layout"]}, set(dashboard["config"]))
        note = next(w for w in dashboard["config"].values() if w["class"] == "extras.NoteWidget")
        self.assertIn(self.plan["recipe"]["name"], note["config"]["content"])
        self.assertTrue(self.artifact["first_impression"]["F1"]["pass"])

    def test_only_a_carrier_estate_is_toured(self):
        with self.assertRaisesRegex(ShowcaseError, "carrier estate"):
            create(_plan("bank"))

    def test_first_impression_checks_fail_on_mutation(self):
        impression = self.artifact["first_impression"]
        self.assertTrue(impression["F3"]["pass"])
        plan = copy.deepcopy(self.plan)
        tenant = next(o for o in plan["objects"] if o["kind"] == "tenant"
                      and o["refs"].get("group", "").endswith("/customers"))
        tenant["attrs"]["description"] = "A customer"
        self.assertEqual(create(plan)["first_impression"]["F3"]["customer_description_exceptions"], 1)
        self.assertFalse(create(plan)["first_impression"]["F3"]["pass"])
        # F1: with no IX port in service the tour has no third bookmark.
        plan = copy.deepcopy(self.plan)
        for o in plan["objects"]:
            if o["kind"] == "circuit" and o["refs"]["type"].endswith("/ix-port"):
                o["attrs"]["status"] = "offline"
        f1 = create(plan)["first_impression"]["F1"]
        self.assertEqual((f1["bookmarks"], f1["pass"]), (2, False))
        # F5: re-pointing the newest journals at a premises device thins the page.
        plan = copy.deepcopy(self.plan)
        premises = next(o for o in plan["objects"] if o["kind"] == "device" and o["key"].startswith("device/ce-"))
        newest = sorted((o for o in plan["objects"] if o["kind"] == "journal_entry" and o["attrs"].get("created")),
                        key=lambda o: (o["attrs"]["created"], o["key"]), reverse=True)[:showcase.F5_PAGE]
        for o in newest:
            o["refs"]["assigned_object"] = premises["key"]
        f5 = create(plan)["first_impression"]["F5"]
        self.assertEqual((f5["carrier_rows"], f5["pass"]), (0, False))

    def test_verify_rejects_tampering_and_a_foreign_plan(self):
        for mutate, pattern in ((lambda a: a["filters"].append(dict(a["filters"][0])), "duplicated"),
                                (lambda a: a["filters"][0].update(label="Anything"), "retirable"),
                                (lambda a: a["dashboard"]["layout"].pop(), "disagree")):
            mutated = copy.deepcopy(self.artifact)
            mutate(mutated)
            with self.assertRaisesRegex(ShowcaseError, pattern):
                verify(mutated, self.plan)
        other = copy.deepcopy(self.plan)
        other["recipe"]["seed"] = 1
        with self.assertRaisesRegex(ShowcaseError, "not bound"):
            verify(self.artifact, other)

    def test_build_and_check_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
            build(root / "plan.json", root / "out")
            self.assertEqual(check(root / "out", root / "plan.json")["filters"], len(self.artifact["filters"]))
            (root / "out/showcase.json").write_text(json.dumps(self.artifact))
            with self.assertRaisesRegex(ShowcaseError, "canonical"):
                check(root / "out", root / "plan.json")


class _FakeNetBox:
    """In-memory NetBox core endpoints the sidecar touches (not a claim about NetBox)."""
    base = "https://t.example"
    headers = {}

    def __init__(self, artifact, dashboard_exists=True):
        self.artifact, self.rows, self.next = artifact, {"saved-filters": {}, "bookmarks": {}}, 100
        self.dashboard = {"layout": [{"id": "x"}], "config": {"x": {"class": "extras.NoteWidget"}}} \
            if dashboard_exists else None
        self.counts = {f["slug"]: f["count"] for f in artifact["filters"]}
        self.deleted = []

    def request(self, path, method="GET", body=None, headers=None, branch=True):
        url = urlparse(path)
        query = {k: v for k, v in parse_qs(url.query).items()}
        payload = json.loads(body) if body else None
        if url.path == "/api/authentication-check/":
            return 200, {"id": 7, "username": "seeder"}
        if url.path == "/api/extras/dashboard/":
            if method == "PATCH":
                if self.dashboard is None:
                    raise LoadError("PATCH /api/extras/dashboard/ returned HTTP 500")
                self.dashboard = payload
            return 200, self.dashboard or {"layout": [], "config": {}}
        for kind in ("saved-filters", "bookmarks"):
            prefix = f"/api/extras/{kind}/"
            if url.path.startswith(prefix):
                rest = url.path[len(prefix):].strip("/")
                if method == "POST":
                    self.next += 1
                    self.rows[kind][self.next] = row = {"id": self.next, **payload}
                    return 201, row
                if rest:
                    return 200, self.rows[kind][int(rest)]
                found = [r for r in self.rows[kind].values()
                         if all(str(r.get(k.removesuffix("_id"))) == v[0] for k, v in query.items())]
                return 200, {"count": len(found), "results": found}
        # Estate list endpoints: answer with the plan's own counts.
        for f in self.artifact["filters"]:
            if url.path == showcase.ENDPOINTS[f["object_type"]] and \
                    {k: v for k, v in query.items() if k not in ("brief", "limit")} == \
                    {k: [str(x) for x in v] for k, v in f["parameters"].items()}:
                return 200, {"count": f["count"], "results": [{"id": 1}]}
        return 200, {"count": 1, "results": [{"id": 42}]}


class Seeding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = _plan()

    def _seed(self, fake, root):
        (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
        build(root / "plan.json", root / "out")
        with patch.object(showcase, "Client", return_value=fake), \
                patch.object(showcase, "_delete", side_effect=lambda c, p: fake.deleted.append(p) or
                             fake.rows[p.split("/")[3]].pop(int(p.rstrip("/").rsplit("/", 1)[1]), None)), \
                patch.dict(os.environ, {"SHOWCASE_WRITES": "1"}):
            return seed(root / "out", url=fake.base, token="x", receipt_path=root / "r.json")

    def test_seed_reads_back_and_unseed_restores(self):
        artifact = create(self.plan)
        fake = _FakeNetBox(artifact)
        prior = copy.deepcopy(fake.dashboard)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._seed(fake, root)
            self.assertTrue(result["success"])
            self.assertEqual(len(fake.rows["saved-filters"]), len(artifact["filters"]))
            self.assertEqual(fake.dashboard, artifact["dashboard"])
            with patch.object(showcase, "Client", return_value=fake), \
                    patch.object(showcase, "_delete", side_effect=lambda c, p: fake.deleted.append(p) or
                                 fake.rows[p.split("/")[3]].pop(int(p.rstrip("/").rsplit("/", 1)[1]), None)), \
                    patch.dict(os.environ, {"SHOWCASE_WRITES": "1"}):
                gone = unseed(root / "r.json", url=fake.base, token="x")
        self.assertTrue(gone["dashboard_restored"])
        self.assertEqual(fake.dashboard, prior)
        self.assertEqual(fake.rows, {"saved-filters": {}, "bookmarks": {}})

    def test_fresh_only_and_missing_dashboard_are_actionable(self):
        artifact = create(self.plan)
        fake = _FakeNetBox(artifact)
        fake.rows["saved-filters"][1] = {"id": 1, "slug": artifact["filters"][0]["slug"], "name": "x"}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(LoadError, "fresh-only"):
                self._seed(fake, Path(temporary))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(LoadError, "open Home once"):
                self._seed(_FakeNetBox(artifact, dashboard_exists=False), Path(temporary))

    def test_writes_require_the_gate(self):
        with patch.dict(os.environ, {"SHOWCASE_WRITES": ""}):
            with self.assertRaisesRegex(LoadError, "SHOWCASE_WRITES=1"):
                seed("unused", url="https://t.example", token="x", receipt_path=Path("unused"))
            with self.assertRaisesRegex(LoadError, "SHOWCASE_WRITES=1"):
                unseed("unused", url="https://t.example", token="x")


if __name__ == "__main__":
    unittest.main()
