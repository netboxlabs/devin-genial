"""Branch creation must refuse existing names, wait for actual readiness, and
clean up its own stuck branch so the name is never burned."""

import unittest

from estates.branch import create_branch
from estates.turbobulk import LoadError


class Stub:
    def __init__(self, existing=(), states=("new", "provisioning", "ready")):
        self.existing = list(existing)
        self.states = list(states)
        self.posted = 0
        self.deleted = False

    def request(self, path, *, method="GET", **_kwargs):
        if method == "POST":
            self.posted += 1
            return 201, {"id": 7, "name": "demo", "schema_id": "abc", "status": "new"}
        if method == "DELETE":
            self.deleted = True
            # a 204 empty body surfaces as a parse failure in Client.request
            raise LoadError("DELETE failed without retry because the request could write")
        if "?" in path:
            return 200, {"results": [{"id": 7, "name": name, "schema_id": "abc"}
                                     for name in self.existing]}
        if self.deleted:
            raise LoadError("GET returned HTTP 404")
        return 200, {"id": 7, "name": "demo", "status": self.states.pop(0)}


class BranchTests(unittest.TestCase):
    def test_refuses_an_existing_name_without_posting(self):
        stub = Stub(existing=["demo"])
        with self.assertRaises(LoadError) as caught:
            create_branch(stub, "demo")
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(stub.posted, 0)

    def test_waits_until_the_branch_is_ready(self):
        stub = Stub()
        row = create_branch(stub, "demo", sleep=lambda _s: None)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(stub.posted, 1)
        self.assertFalse(stub.deleted)

    def test_failed_provisioning_deletes_the_stuck_branch(self):
        stub = Stub(states=["failed"])
        with self.assertRaises(LoadError) as caught:
            create_branch(stub, "demo", sleep=lambda _s: None)
        self.assertIn("provisioning failed", str(caught.exception))
        self.assertIn("the branch was deleted so the name is free", str(caught.exception))
        self.assertTrue(stub.deleted)

    def test_delete_removes_exactly_one_named_branch(self):
        from estates.branch import delete_branch
        stub = Stub(existing=["demo"])
        row = delete_branch(stub, "demo", sleep=lambda _s: None)
        self.assertEqual(row, {"id": 7, "name": "demo", "schema_id": "abc", "deleted": True})
        self.assertTrue(stub.deleted)

    def test_delete_refuses_a_missing_branch(self):
        from estates.branch import delete_branch
        stub = Stub(existing=[])
        with self.assertRaises(LoadError) as caught:
            delete_branch(stub, "demo", sleep=lambda _s: None)
        self.assertIn("found 0", str(caught.exception))
        self.assertFalse(stub.deleted)

    def test_retire_namespace_rows_deletes_only_exact_prefix_matches(self):
        from estates.branch import retire_namespace_rows

        class Owners:
            rows = {3: "lakes-fiber Infrastructure operations",
                    13: "meridian Infrastructure operations",
                    14: "meridian-east Infrastructure operations"}

            def __init__(self):
                self.deleted = set()

            def all(self, path):
                return self.request(path + "?limit=1000")[1]["results"]

            def request(self, path, *, method="GET", **_kwargs):
                if "?limit" in path:
                    return 200, {"results": [{"id": i, "name": n}
                                             for i, n in self.rows.items()]}
                row_id = int(path.rstrip("/").rsplit("/", 1)[1])
                if method == "DELETE":
                    self.deleted.add(row_id)
                    raise LoadError("204 empty body")
                if row_id in self.deleted:
                    raise LoadError("GET returned HTTP 404")
                return 200, {"id": row_id}

        stub = Owners()
        deleted = retire_namespace_rows(stub, "meridian")
        # only the exact-namespace row goes; meridian-east and lakes-fiber stay
        self.assertEqual(stub.deleted, {13})
        self.assertTrue(all(item["name"].startswith("meridian ") for item in deleted))

    def test_retirement_matches_custom_field_underscore_names(self):
        from estates.branch import retire_namespace_rows

        class Extras:
            def __init__(self):
                self.deleted = set()

            def all(self, path):
                return self.request(path + "?limit=1000")[1]["results"]

            def request(self, path, *, method="GET", **_kwargs):
                if "limit=" in path:
                    if "custom-fields" in path:
                        rows = [{"id": 1, "name": "mercy_north_operations_tier"},
                                {"id": 2, "name": "mercy_northeast_tier"},
                                {"id": 3, "name": "mercy_operations_tier"}]
                    else:
                        rows = []
                    return 200, {"results": rows, "next": None, "count": len(rows)}
                row_id = int(path.rstrip("/").rsplit("/", 1)[1])
                if method == "DELETE":
                    self.deleted.add(row_id)
                    raise LoadError("204 empty body")
                if row_id in self.deleted:
                    raise LoadError("GET returned HTTP 404")
                return 200, {"id": row_id}

        stub = Extras()
        deleted = retire_namespace_rows(stub, "mercy-north", sleep=lambda _s: None)
        # exact generated name only
        self.assertEqual(stub.deleted, {1})
        self.assertEqual([d["name"] for d in deleted], ["mercy_north_operations_tier"])
        # the inverted (dangerous) direction: namespace "mercy" must NEVER touch
        # mercy-north's field — prefix matching on the underscore form would have
        stub2 = Extras()
        deleted2 = retire_namespace_rows(stub2, "mercy", sleep=lambda _s: None)
        self.assertEqual(stub2.deleted, {3})
        self.assertEqual([d["name"] for d in deleted2], ["mercy_operations_tier"])

    def test_dedicated_retirement_matches_exact_bare_labels_only(self):
        from estates.branch import retire_namespace_rows

        class Owners:
            rows = {1: "Network operations", 2: "Network operations team",
                    3: "lakes-fiber Network operations", 4: "Infrastructure operations"}

            def __init__(self):
                self.deleted = set()

            def all(self, path):
                return [{"id": i, "name": n} for i, n in self.rows.items()] if path == "/api/users/owners/" else []

            def request(self, path, *, method="GET", **_kwargs):
                row_id = int(path.rstrip("/").rsplit("/", 1)[1])
                if method == "DELETE":
                    self.deleted.add(row_id)
                    raise LoadError("204 empty body")
                if row_id in self.deleted:
                    raise LoadError("GET returned HTTP 404")
                return 200, {"id": row_id}

        stub = Owners()
        retire_namespace_rows(stub, "lakes-fiber", dedicated=True, sleep=lambda _s: None)
        self.assertEqual(stub.deleted, {1, 4})  # never a lookalike or a shared-mode row
        stub = Owners()
        retire_namespace_rows(stub, "lakes-fiber", sleep=lambda _s: None)
        self.assertEqual(stub.deleted, {3})

    def test_every_generated_main_scoped_row_resolves_for_retirement_in_both_modes(self):
        import tomllib
        from pathlib import Path
        from estates.branch import RETIREMENT_PATHS, retirement_matcher
        from estates.generate import generate
        from estates.naming import NAMESPACED_KINDS
        from estates.turbobulk import SPECS

        for path in sorted((Path(__file__).parents[1] / "profiles").glob("*.toml")):
            raw = tomllib.loads(path.read_text())
            for tenancy in ("shared", "dedicated"):
                with self.subTest(profile=path.name, tenancy=tenancy):
                    plan = generate(dict(raw, tenancy=tenancy))
                    ns, dedicated = plan["recipe"]["namespace"], tenancy == "dedicated"
                    rows = [o for o in plan["objects"] if o["kind"] in NAMESPACED_KINDS]
                    self.assertTrue(rows)
                    for obj in rows:
                        endpoint, name = SPECS[obj["kind"]][1], obj["attrs"]["name"]
                        self.assertIn(endpoint, RETIREMENT_PATHS)
                        self.assertTrue(retirement_matcher(endpoint, ns, dedicated=dedicated)(name),
                                        f"{obj['kind']} {name!r} would strand on retirement")
                        self.assertFalse(retirement_matcher(endpoint, "qq7", dedicated=False)(name))
                        if dedicated and obj["kind"] != "custom_field":
                            self.assertNotIn(ns, name.lower(), "dedicated tenancy must drop the namespace")
                        if not dedicated:
                            self.assertTrue(name.startswith(ns.replace("-", "_") if obj["kind"] == "custom_field" else f"{ns} "))
                    for obj in plan["objects"]:
                        for field in ("slug",):
                            if obj["kind"] in ("tenant", "site") and isinstance(obj["attrs"].get(field), str):
                                self.assertIn(ns, obj["attrs"][field], "identities keep the namespace in both modes")

    def test_timeout_deletes_the_stuck_branch_and_names_the_worker(self):
        stub = Stub(states=["new"] * 5)
        with self.assertRaises(LoadError) as caught:
            create_branch(stub, "demo", timeout=0, sleep=lambda _s: None)
        message = str(caught.exception)
        self.assertIn("is still 'new'", message)
        self.assertIn("name is free", message)
        self.assertIn("RQ worker", message)
        self.assertTrue(stub.deleted)


if __name__ == "__main__":
    unittest.main()
