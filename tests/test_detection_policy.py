"""Observation-only failure signal policy."""

import unittest

from mpf.detection.DetectionPolicy import DetectionPolicy


class DetectionPolicyTests(unittest.TestCase):
    def test_untrained_camera_learns_six_frames_then_detects_a_rise_without_absorbing_it(self):
        context = ("printer", "print", "camera")
        policy = DetectionPolicy(warning_threshold=1, failure_threshold=9, safe_seconds=0)
        for index in range(6):
            self.assertEqual(policy.observe(.2, now=index * 10, context=context), "learning")
            self.assertIsNone(policy.state(now=index * 10, context=context, active=True).score)
            self.assertAlmostEqual(policy.baseline["mean"], .2)
            # Restart partway through calibration must resume its real count.
            if index == 2:
                saved = policy.baseline
                policy = DetectionPolicy(warning_threshold=1, failure_threshold=9, safe_seconds=0)
                policy.restore_baseline(saved)
        self.assertEqual(policy.baseline["frames"], 6)
        self.assertEqual(policy.observe(.2, now=60, context=context), "normal")
        levels = [policy.observe(1.2, now=index * 10, context=context) for index in range(7, 10)]
        self.assertIn("failure", levels)
        self.assertLess(policy.baseline["mean"], .201)
        self.assertGreater(policy.state(now=90, context=context, active=True).score, 0)

    def test_sensitivity_scales_classification_and_score_monotonically(self):
        policies = [DetectionPolicy(sensitivity=value, safe_seconds=0) for value in (.8, 1, 1.2)]
        context = ("printer", "job", "camera")
        for policy in policies:
            policy.restore_baseline({"mean": 0, "frames": 7200})
            for index in range(30):
                policy.observe(0, now=index*10, context=context)
            for index in range(30, 36):
                policy.observe(.9, now=index*10, context=context)
        scores = [policy.state(now=350, context=context, active=True).score for policy in policies]
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[0])
        for invalid in (True, float('nan'), .7, 1.3, 10**400):
            with self.subTest(invalid=str(invalid)[:20]), self.assertRaises(ValueError):
                DetectionPolicy(sensitivity=invalid)


    def test_only_fresh_samples_from_the_current_print_can_show_green(self):
        policy = DetectionPolicy()
        job = ("printer-a", "job-1", "camera-bed")
        self.assertEqual(policy.state(now=0, context=job, active=True).name, "waiting")
        policy.observe(0.12, now=1, context=job)
        self.assertEqual(policy.state(now=1, context=job, active=False).name, "idle")
        self.assertIsNone(policy.state(now=1, context=job, active=False).raw_score)
        self.assertEqual(policy.state(now=1, context=job, active=True).name, "learning")
        self.assertEqual(policy.state(now=1, context=job, active=True).raw_score, .12)
        self.assertEqual(policy.state(now=1, context=("printer-a", "job-2", "camera-bed"),
                                      active=True).name, "waiting")
        self.assertEqual(policy.state(now=32, context=job, active=True).name, "stale")
        self.assertIsNone(policy.state(now=32, context=job, active=True).raw_score)
        policy.observe(0.10, now=33, context=job)
        self.assertEqual(policy.state(now=33, context=job, active=True).name, "learning")

    def test_obico_safe_frames_adaptive_warning_and_confirmed_failure(self):
        policy = DetectionPolicy()
        job = ("printer-a", "job-1", "camera-bed")
        policy.restore_baseline({"mean": 0.0, "frames": 7200})
        for index in range(28):
            self.assertEqual(policy.observe(0.0, now=index * 10, context=job), "normal")
        self.assertEqual(policy.observe(4.0, now=280, context=job), "normal",
                         "Obico suppresses decisions while the frame count is below 30")
        self.assertEqual(policy.observe(1.0, now=290, context=job), "normal")
        levels = [policy.observe(1.0 if index < 34 else 4.0, now=index * 10, context=job)
                  for index in range(30, 45)]
        self.assertIn("warning", levels)
        self.assertIn("failure", levels)
        self.assertEqual(policy.state(now=440, context=job, active=True).name, "failure")
        for index in range(45, 90):
            policy.observe(0.0, now=index * 10, context=job)
        self.assertEqual(policy.state(now=890, context=job, active=True).name, "normal")

    def test_context_change_discards_old_failure(self):
        policy = DetectionPolicy()
        first = ("printer-a", "job-1", "camera-bed")
        second = ("printer-a", "job-1", "camera-toolhead")
        policy.observe(.9, now=0, context=first)
        baseline = policy.baseline
        policy.observe(.1, now=10, context=second)
        self.assertEqual(policy.state(now=10, context=second, active=True).name, "learning")
        self.assertEqual(policy.state(now=10, context=first, active=True).name, "waiting")
        self.assertGreater(policy.baseline["frames"], baseline["frames"])
        self.assertEqual(policy.state(now=10, context=second, active=True).raw_score, .1)

    def test_raw_model_evidence_is_not_the_adaptive_decision(self):
        policy = DetectionPolicy(safe_seconds=0)
        job = ("printer", "print", "camera")
        policy.observe(.11, now=0, context=job)
        result = policy.state(now=0, context=job, active=True)
        self.assertIsNone(result.score)
        self.assertEqual(result.raw_score, .11)
        policy.observe(1.42, now=10, context=job)
        self.assertEqual(policy.state(now=10, context=job, active=True).raw_score, 1.42)

    def test_invalid_confidence_is_not_reported_as_health(self):
        policy = DetectionPolicy()
        for value in (-.1, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.observe(value, now=0, context=("p", "j", "c"))
        self.assertIsNone(policy.state(now=0, context=("p", "j", "c"), active=True).score)

    def test_custom_thresholds_apply_to_the_same_relative_signal(self):
        context = ("printer-a", "job", "camera")
        low = DetectionPolicy(warning_threshold=20, failure_threshold=60)
        high = DetectionPolicy(warning_threshold=70, failure_threshold=90)
        for policy in (low, high):
            policy.restore_baseline({"mean": 0.0, "frames": 7200})
            for index in range(30):
                policy.observe(0, now=index * 10, context=context)
        for index in range(30, 36):
            low.observe(.7, now=index * 10, context=context)
            high.observe(.7, now=index * 10, context=context)
        self.assertNotEqual(low.state(now=350, context=context, active=True).name,
                            high.state(now=350, context=context, active=True).name)

    def test_displayed_signal_uses_the_selected_adaptive_boundaries(self):
        context = ("printer-a", "job", "camera")
        low = DetectionPolicy(warning_threshold=10, failure_threshold=26, safe_seconds=0)
        default = DetectionPolicy(safe_seconds=0)
        for policy in (low, default):
            policy.restore_baseline({"mean": 0.0, "frames": 7200})
            for index in range(30):
                policy.observe(0, now=index * 10, context=context)
            for index in range(30, 33):
                policy.observe(.3, now=index * 10, context=context)
        warning = low.state(now=320, context=context, active=True)
        normal = default.state(now=320, context=context, active=True)
        self.assertEqual(warning.name, "warning")
        self.assertGreaterEqual(warning.score, 10)
        self.assertLess(warning.score, 26)
        self.assertEqual(normal.name, "normal")
        self.assertLess(normal.score, 38)
        for index, confidence in enumerate((.5, 1, 2), start=33):
            low.observe(confidence, now=index * 10, context=context)
            default.observe(confidence, now=index * 10, context=context)
        failure = low.state(now=350, context=context, active=True)
        still_warning = default.state(now=350, context=context, active=True)
        self.assertEqual(failure.name, "failure")
        self.assertGreaterEqual(failure.score, 26)
        self.assertEqual(still_warning.name, "warning")
        self.assertGreaterEqual(still_warning.score, 38)
        self.assertLess(still_warning.score, 78)

        zero = DetectionPolicy(warning_threshold=0, failure_threshold=1, safe_seconds=0)
        zero.restore_baseline({"mean": 0, "frames": 6})
        zero.observe(0, now=0, context=context)
        self.assertEqual(zero.state(now=0, context=context, active=True).score, 0)

    def test_baseline_survives_print_reset_and_can_be_restored(self):
        policy = DetectionPolicy()
        policy.observe(2.3, now=0, context=("p", "one", "camera"))
        saved = policy.baseline
        policy.reset()
        self.assertEqual(policy.baseline, saved)
        restored = DetectionPolicy()
        restored.restore_baseline(saved)
        self.assertEqual(restored.baseline, saved)

    def test_invalid_threshold_pairs_are_rejected(self):
        for warning, failure in ((-1, 75), (35, 101), (50, 50), (60, 40),
                                 (True, 75), (35.5, 75)):
            with self.subTest(warning=warning, failure=failure), self.assertRaises(ValueError):
                DetectionPolicy(warning_threshold=warning, failure_threshold=failure)

    def test_safe_period_uses_elapsed_print_time_not_sample_count(self):
        context = ("printer-a", "job", "camera")
        immediate = DetectionPolicy(safe_seconds=0)
        delayed = DetectionPolicy(safe_seconds=900)
        for policy in (immediate, delayed):
            policy.restore_baseline({"mean": 0.0, "frames": 7200})
            for index in range(30):
                policy.observe(0, now=index * 10, context=context,
                               print_elapsed_seconds=index * 10)
        self.assertIn(immediate.observe(4, now=300, context=context,
                                        print_elapsed_seconds=300), ("warning", "failure"))
        self.assertEqual(delayed.observe(4, now=300, context=context,
                                         print_elapsed_seconds=890), "normal")
        self.assertIn(delayed.observe(4, now=310, context=context,
                                      print_elapsed_seconds=900), ("warning", "failure"))

    def test_a_suppressed_safe_period_signal_stays_in_the_normal_band(self):
        context = ("printer-a", "job", "camera")
        policy = DetectionPolicy(safe_seconds=300)
        policy.restore_baseline({"mean": 0.0, "frames": 7200})
        for index in range(30):
            policy.observe(0, now=index * 10, context=context,
                           print_elapsed_seconds=index * 10)
        # A failure-sized signal inside the safe period: the level is
        # suppressed, and the number must be too — a green "Normal"
        # may never carry a failing score.
        self.assertEqual(policy.observe(4, now=300, context=context,
                                        print_elapsed_seconds=290), "normal")
        suppressed = policy.state(now=300, context=context, active=True)
        self.assertEqual(suppressed.name, "normal")
        self.assertEqual(suppressed.raw_score, 4)
        self.assertLess(suppressed.score, policy.warning_threshold)
        # The same evidence once the period has passed escalates.
        policy.observe(4, now=310, context=context, print_elapsed_seconds=310)
        escalated = policy.state(now=310, context=context, active=True)
        self.assertNotEqual(escalated.name, "normal")
        self.assertGreaterEqual(escalated.score, policy.warning_threshold)

    def test_safe_period_bounds_and_invalid_elapsed_time(self):
        for value in (-1, 901, True, 5.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DetectionPolicy(safe_seconds=value)
        policy = DetectionPolicy()
        with self.assertRaises(ValueError):
            policy.observe(0, now=0, context=("p", "job", "camera"),
                           print_elapsed_seconds=-1)


if __name__ == "__main__":
    unittest.main()
