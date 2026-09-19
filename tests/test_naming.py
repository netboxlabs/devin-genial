"""Site naming: authored defaults, the legacy switch, overrides and stability."""

import unittest

from estates.bank import generate
from estates.model import DesignError


def _recipe(**overrides):
    base = {"namespace": "acme", "name": "Acme Bank", "seed": 7,
            "branches": {"small": 2, "medium": 0, "large": 0}, "headquarters": 1}
    base.update(overrides)
    return base


def _sites(plan):
    return {o["key"]: o["attrs"] for o in plan["objects"] if o["kind"] == "site"}


class NamingTests(unittest.TestCase):
    def test_authored_is_the_default_with_facility_and_coordinates(self):
        sites = _sites(generate(_recipe()))
        for key, attrs in sites.items():
            self.assertFalse(attrs["name"].startswith("acme-"), key)
            self.assertRegex(attrs["facility"], r"^[A-Z]{3}\d{4}$")
            self.assertIsInstance(attrs["latitude"], float)
            self.assertIsInstance(attrs["longitude"], float)
            # slugs keep the stable namespace form: matching and DNS unchanged
            self.assertTrue(attrs["slug"].startswith("acme-"), key)
        self.assertEqual(len({a["name"] for a in sites.values()}), len(sites))

    def test_legacy_switch_restores_namespace_ordinal_names(self):
        sites = _sites(generate(_recipe(naming="legacy")))
        for key, attrs in sites.items():
            self.assertTrue(attrs["name"].startswith("acme-"), key)
            self.assertNotIn("facility", attrs)
            self.assertNotIn("latitude", attrs)

    def test_site_names_override_wins_and_is_validated(self):
        plan = generate(_recipe(site_names={
            "br-s0001": {"name": "350 East Cermak", "facility": "CHI-CERMAK"}}))
        attrs = _sites(plan)["site/br-s0001"]
        self.assertEqual(attrs["name"], "350 East Cermak")
        self.assertEqual(attrs["facility"], "CHI-CERMAK")
        with self.assertRaises(DesignError) as caught:
            generate(_recipe(site_names={"no-such-site": {"name": "Ghost"}}))
        self.assertIn("unknown site ids", str(caught.exception))
        with self.assertRaises(DesignError):
            generate(_recipe(site_names={"br-s0001": {"name": "x" * 101}}))
        with self.assertRaises(DesignError):
            generate(_recipe(site_names={"br-s0001": {"street": "not a field"}}))

    def test_duplicate_display_names_are_refused(self):
        with self.assertRaises(DesignError) as caught:
            generate(_recipe(site_names={"br-s0001": {"name": "Twin"},
                                         "br-s0002": {"name": "Twin"}}))
        self.assertIn("globally unique", str(caught.exception))

    def test_growth_never_renames_an_existing_site(self):
        baseline = generate(_recipe())
        before = _sites(baseline)
        grown = generate(_recipe(branches={"small": 4, "medium": 1, "large": 0}),
                         previous=baseline)
        after = _sites(grown)
        for key, attrs in before.items():
            self.assertEqual(after[key]["name"], attrs["name"], key)
            self.assertEqual(after[key]["facility"], attrs["facility"], key)
        self.assertGreater(len(after), len(before))

    def test_names_do_not_vary_with_seed(self):
        first = _sites(generate(_recipe(seed=7)))
        second = _sites(generate(_recipe(seed=9001)))
        self.assertEqual({k: a["name"] for k, a in first.items()},
                         {k: a["name"] for k, a in second.items()})


if __name__ == "__main__":
    unittest.main()
