"""Changelog-bearing loads above the vendor's large-import guidance must be refused."""

import unittest
from unittest.mock import patch

from estates.turbobulk import (REVIEWABLE_SCALE_OVERRIDE, REVIEWABLE_SCALE_ROWS,
                               reviewable_scale_blocker)


class ReviewableScaleGate(unittest.TestCase):
    def test_reviewable_above_the_guidance_is_refused_with_the_remedies(self):
        message = reviewable_scale_blocker(REVIEWABLE_SCALE_ROWS + 1, "reviewable")
        self.assertIsNotNone(message)
        self.assertIn("disposable-baseline", message)
        self.assertIn(REVIEWABLE_SCALE_OVERRIDE, message)
        self.assertIn("delete", message)

    def test_reviewable_at_or_below_the_guidance_passes(self):
        self.assertIsNone(reviewable_scale_blocker(REVIEWABLE_SCALE_ROWS, "reviewable"))

    def test_disposable_loads_are_never_gated(self):
        self.assertIsNone(reviewable_scale_blocker(10 * REVIEWABLE_SCALE_ROWS, "disposable-baseline"))

    def test_explicit_override_permits_deliberate_qualification(self):
        with patch.dict("os.environ", {REVIEWABLE_SCALE_OVERRIDE: "1"}):
            self.assertIsNone(reviewable_scale_blocker(REVIEWABLE_SCALE_ROWS + 1, "reviewable"))


if __name__ == "__main__":
    unittest.main()
