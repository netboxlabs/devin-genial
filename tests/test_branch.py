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
