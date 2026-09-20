"""The restore-grace owner: witnessed stamps, the exact cursor rule, and
the fail-open unknown branch — the panel's R2-3 shape plus the live
mode-0 ruling (clean restores are allowed in every windowed mode).
"""
from __future__ import annotations

import unittest

from plugins.ExcludeGrace import (
    CLEAN,
    IN_GRACE,
    NEVER,
    PAST_GRACE,
    UNKNOWN,
    ExcludeGrace,
)


class ExcludeGraceTests(unittest.TestCase):
    def grace(self):
        return ExcludeGrace()

    def test_an_unstamped_exclusion_fails_open_with_the_note(self):
        g = self.grace()
        allowed, verdict, detail = g.evaluate("part_a", window_mode=3,
                                              window_layers=3, layer=87)
        self.assertTrue(allowed)
        self.assertEqual(verdict, UNKNOWN)
        self.assertIn("outside the plugin", detail)

    def test_an_unstamped_exclusion_is_refused_at_mode_zero(self):
        # Zero tolerance cannot be proven for an exclusion the plugin
        # did not witness — fail closed, consistent with the ruling.
        g = self.grace()
        allowed, verdict, _ = g.evaluate("part_a", window_mode=0,
                                         window_layers=0, layer=87)
        self.assertFalse(allowed)
        self.assertEqual(verdict, UNKNOWN)

    def test_never_mode_allows_everything(self):
        g = self.grace()
        g.note_exclusion("part_a", 80)
        g.observe("part_a", {"part_a"})
        allowed, _, _ = g.evaluate("part_a", window_mode=NEVER,
                                   window_layers=0, layer=200)
        self.assertTrue(allowed)

    def test_the_clean_case_is_allowed_in_every_windowed_mode(self):
        # The ruling: while the block has not been reached, a restore
        # leaves no gap — mode 0 included.
        g = self.grace()
        g.note_exclusion("part_a", 87)
        for mode in (0, 3):
            allowed, verdict, detail = g.evaluate(
                "part_a", window_mode=mode, window_layers=3, layer=87)
            self.assertTrue(allowed)
            self.assertEqual(verdict, CLEAN)
            self.assertIn("no gap", detail)

    def test_reached_within_the_window_allows_with_a_gap_note(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.observe("part_a", {"part_a"})  # its block is being skipped
        allowed, verdict, detail = g.evaluate(
            "part_a", window_mode=3, window_layers=3, layer=89)
        self.assertTrue(allowed)
        self.assertEqual(verdict, IN_GRACE)
        self.assertIn("gap of about 2 layers", detail)

    def test_reached_beyond_the_window_blocks_with_the_reason(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.observe("part_a", {"part_a"})
        allowed, verdict, detail = g.evaluate(
            "part_a", window_mode=3, window_layers=3, layer=95)
        self.assertFalse(allowed)
        self.assertEqual(verdict, PAST_GRACE)
        self.assertIn("skipped 8 layers ago", detail)
        self.assertIn("3-layer", detail)

    def test_reached_at_mode_zero_blocks(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.observe("part_a", {"part_a"})
        allowed, _, detail = g.evaluate("part_a", window_mode=0,
                                        window_layers=0, layer=87)
        self.assertFalse(allowed)
        self.assertIn("zero layers", detail)

    def test_an_unmeasurable_layer_reads_past_grace_and_allows(self):
        # One missing endpoint is incomplete verification, and
        # incomplete verification must not block the rescue.
        g = self.grace()
        g.note_exclusion("part_a", None)
        g.observe("part_a", {"part_a"})
        allowed, verdict, _ = g.evaluate("part_a", window_mode=3,
                                         window_layers=3, layer=None)
        self.assertTrue(allowed)
        self.assertEqual(verdict, PAST_GRACE)

    def test_the_cursor_marks_consumed_only_for_the_current_object(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.note_exclusion("part_b", 87)
        g.observe("part_a", {"part_a", "part_b"})
        allowed, verdict, _ = g.evaluate("part_b", window_mode=3,
                                         window_layers=3, layer=87)
        self.assertTrue(allowed)
        self.assertEqual(verdict, CLEAN)
        allowed, verdict, _ = g.evaluate("part_a", window_mode=0,
                                         window_layers=0, layer=87)
        self.assertFalse(allowed)
        self.assertEqual(verdict, IN_GRACE)

    def test_a_restore_clears_the_stamp(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.note_restored("part_a")
        allowed, verdict, _ = g.evaluate("part_a", window_mode=3,
                                         window_layers=3, layer=87)
        self.assertTrue(allowed)
        self.assertEqual(verdict, UNKNOWN)

    def test_a_later_observation_backfills_a_missing_layer(self):
        g = self.grace()
        g.note_exclusion("part_a", None)
        g.note_exclusion("part_a", 87)
        g.observe("part_a", {"part_a"})
        allowed, verdict, detail = g.evaluate(
            "part_a", window_mode=3, window_layers=3, layer=89)
        self.assertTrue(allowed)
        self.assertEqual(verdict, IN_GRACE)
        self.assertIn("gap of about 2 layers", detail)

    def test_the_job_epoch_clear_drops_every_stamp(self):
        g = self.grace()
        g.note_exclusion("part_a", 87)
        g.clear()
        allowed, verdict, _ = g.evaluate("part_a", window_mode=3,
                                         window_layers=3, layer=87)
        self.assertTrue(allowed)
        self.assertEqual(verdict, UNKNOWN)


if __name__ == "__main__":
    unittest.main()
