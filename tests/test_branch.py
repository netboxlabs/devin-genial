"""Branch creation must refuse existing names and wait for actual readiness."""

import unittest

from estates.branch import create_branch
from estates.turbobulk import LoadError


class Stub:
    def __init__(self, existing=(), states=("new", "provisioning", "ready")):
        self.existing = list(existing)
        self.states = list(states)
        self.posted = 0

    def request(self, path, *, method="GET", **_kwargs):
        if method == "POST":
            self.posted += 1
            return 201, {"id": 7, "name": "demo", "schema_id": "abc", "status": "new"}
        if "?" in path:
            return 200, {"results": [{"name": name} for name in self.existing]}
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

    def test_failed_provisioning_is_a_loud_error(self):
        stub = Stub(states=["failed"])
        with self.assertRaises(LoadError) as caught:
            create_branch(stub, "demo", sleep=lambda _s: None)
        self.assertIn("provisioning failed", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
