"""Pure boundary-policy tests: no Qt, index, Cura or Moonraker required."""

import unittest

from plugins.PlateSplitTracker import PlateSplitTracker


class PlateSplitTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = PlateSplitTracker()
        self.assertFalse(self.tracker.begin(("print-a",), 0))

    def test_the_hydrated_raw_truth_can_correct_a_clamped_floor(self):
        self.assertEqual(self.tracker.accept(100, 100, 100, True), 100)
        for _ in range(2):
            self.assertEqual(self.tracker.accept(120, 100, 8, True), 100)
            self.assertEqual(self.tracker.floor, 100)
        self.assertEqual(self.tracker.accept(120, 100, 8, True), 8)
        self.assertEqual(self.tracker.floor, 8)
        self.assertEqual(self.tracker.accept(125, 12, 12, True), 12)

    def test_failed_matches_expand_stall_evidence_and_can_recover(self):
        self.tracker.accept(80, 80, 80, True)
        for _ in range(2):
            self.assertEqual(self.tracker.accept(100, None, None, True), 80)
        self.assertEqual(self.tracker.stall_polls, 2)
        self.assertEqual(self.tracker.accept(100, 80, 6, True), 6)

    def test_coarse_only_progress_is_monotonic_without_fake_evidence(self):
        self.assertEqual(self.tracker.accept(25, None, None, False), 25)
        self.assertEqual(self.tracker.accept(15, None, None, False), 25)
        self.assertEqual(self.tracker.stall_polls, 0)

    def test_first_poll_of_an_advanced_layer_is_not_a_future_match(self):
        self.tracker.accept(30, 30, 30, True)
        self.assertTrue(self.tracker.begin(("print-a",), 1))
        self.assertEqual(self.tracker.accept(900, 850, 850, True, advanced=True), 0)
        self.assertEqual(self.tracker.floor, 0)
        self.assertFalse(self.tracker.begin(("print-a",), 1))
        self.assertEqual(self.tracker.accept(900, 4, 4, True), 4)

    def test_a_print_change_and_seek_clear_the_previous_floor(self):
        self.tracker.accept(30, 30, 30, True)
        self.assertFalse(self.tracker.begin(("print-b",), 1))
        self.assertIsNone(self.tracker.floor)
        self.tracker.accept(2, 2, 2, True)
        self.assertFalse(self.tracker.begin(("print-b",), 6))
        self.assertIsNone(self.tracker.floor)

    def test_payload_observed_advance_controls_the_next_window(self):
        self.tracker.accept(500, 500, 500, True)
        self.assertEqual(self.tracker.payload_ahead_window, 1024)
        self.tracker.observe_payload_advance(2400)
        self.assertEqual(self.tracker.advance_max, 1900)
        self.assertEqual(self.tracker.payload_ahead_window, 3800)

    def test_near_floor_noise_does_not_rewind_on_one_poll(self):
        self.tracker.accept(50, 50, 50, True)
        self.assertEqual(self.tracker.accept(55, 50, 49, True), 50)
        self.assertEqual(self.tracker.floor, 50)
        self.assertEqual(self.tracker.accept(60, 51, 51, True), 51)
        self.assertEqual(self.tracker.stall_polls, 0)


if __name__ == "__main__":
    unittest.main()
