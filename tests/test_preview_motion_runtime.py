"""Executable preview motion runtime contracts."""
from tests import preview_family_support as harness

class PreviewMotionTests(harness.PreviewMotionTests):
    def test_a_shared_backwards_correction_replaces_the_old_glide(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.9)
        self.motion._displayed = 0.7
        self.assertTrue(self.motion._timer.isActive())
        self.motion.write(1, 0.3)
        self.assertEqual((self.motion._target, self.motion._displayed), (0.3, 0.3))
        self.assertEqual(self.view.paths[-1], 300.0)
        self.assertEqual(self.motion._velocity, 0.0)
        self.assertFalse(self.motion._timer.isActive())
        self.assertEqual(len(self.motion._history), 1)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.assertTrue(self.motion._timer.isActive(), "normal smoothing must resume after correction")

    def test_the_first_observation_jumps_and_clears_the_old_layer(self):
        self.motion.write(1, 0.25)
        self.assertEqual((1, 0.25, 0.25), (self.motion._layer, self.motion._target,
                                           self.motion._displayed))
        self.assertFalse(self.motion._timer.isActive(), "no glide is owed yet")
        self.assertEqual(1, self.view.resets)
        self.assertEqual(250.0, self.view.paths[-1])
        self.assertEqual([0], self.view.minimum_paths, "the minimum is set once per view")
        self.assertEqual(1, self.remember.calls)

    def test_a_layer_transition_jumps_rather_than_gliding(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        rate = self.motion._velocity
        self.motion.write(2, 0.05)
        self.assertEqual((2, 0.05), (self.motion._layer, self.motion._displayed))
        self.assertEqual(2, self.view.resets, "each transition drops the old layer's cache")
        self.assertAlmostEqual(rate * 0.8, self.motion._velocity, "the rate is warm-started")

    def test_a_second_observation_in_a_layer_starts_the_glide(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.assertTrue(self.motion._timer.isActive())
        self.assertGreater(self.motion._velocity, 0.0)
        self.assertAlmostEqual(1.0, self.motion._inter_poll)

    def test_a_flat_stretch_keeps_the_previous_rate(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        rate = self.motion._velocity
        self.assertGreater(rate, 0.0)
        # A repeat of the newest value past the window carries no rate at all.
        self.clock.advance(5.0)
        self.motion.write(1, 0.4)
        self.assertEqual(rate, self.motion._velocity)

    def test_an_unchanged_window_never_invents_a_rate(self):
        flat = harness.PreviewMotion(self.cura, self.remember)
        flat.write(1, 0.4)
        self.clock.advance(1.0)
        flat.write(1, 0.4)
        self.assertEqual(0.0, flat._velocity)

    def test_a_travel_spike_is_clipped_at_the_velocity_cap(self):
        self.motion.write(1, 0.0)
        self.clock.advance(1.0)
        self.motion.write(1, 1.0)
        self.assertLessEqual(self.motion._velocity, 0.5)

    def test_a_late_poll_still_reconstructs_a_trajectory(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(0.5)
        target = self.motion._current_target(self.clock.now)
        self.assertGreaterEqual(target, 0.2)
        self.assertLessEqual(target, 0.4)

    def test_the_ramp_saturates_at_the_newest_observation(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(120.0)
        self.assertEqual(0.4, self.motion._current_target(self.clock.now))

    def test_the_tick_advances_the_display_and_stops_at_the_target(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(0.5)
        self.motion._tick()
        self.assertGreater(self.motion._displayed, 0.2)
        self.assertLessEqual(self.motion._displayed, self.motion._target)
        for _ in range(200):
            self.clock.advance(0.033)
            self.motion._tick()
        self.assertEqual(self.motion._target, self.motion._displayed)
        self.assertFalse(self.motion._timer.isActive())

    def test_a_tick_without_a_target_stops_the_timer(self):
        self.motion._timer.start()
        self.motion._tick()
        self.assertFalse(self.motion._timer.isActive())

    def test_reset_stops_the_glide_and_keeps_the_poll_estimate(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.motion.reset()
        self.assertFalse(self.motion._timer.isActive())
        self.assertIsNone(self.motion._displayed)
        self.assertEqual(0.0, self.motion._velocity)
        self.assertEqual(1.0, self.motion._inter_poll)
        self.motion.write(2, 0.5)
        self.assertEqual(0.5, self.motion._displayed)

    def test_a_write_without_a_view_or_paths_writes_nothing(self):
        self.cura.view = None
        self.motion.write(1, 0.5)
        self.assertEqual(0, self.remember.calls)
        self.cura.view = self.view
        self.view.maximum = None
        self.motion._write(0.5)
        self.assertEqual(0, self.remember.calls)
        self.view.maximum = 0
        self.motion._write(0.5)

    def test_a_replacement_view_receives_its_minimum_again(self):
        self.motion.write(1, 0.5)
        self.assertEqual([0], self.view.minimum_paths)
        self.motion._write(0.6)
        self.assertEqual([0], self.view.minimum_paths, "the minimum is not rewritten per tick")
        self.cura.view = harness.FakeView(layer=1, maximum=1000)
        self.motion._write(0.6)
        self.assertEqual([0], self.cura.view.minimum_paths, "a new view gets its own minimum")

    def test_the_trace_dumps_samples_and_rotates_a_large_file(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "trace.csv")
            motion = harness.PreviewMotion(self.cura, self.remember, trace_path=path)
            motion.write(1, 0.2)
            self.clock.advance(1.0)          # the sample window opens after the obs row
            motion._tick()
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("obs", text)
            self.assertIn("tick", text)
            motion._trace("tick", self.clock.now, 1, 0.3)   # inside the 2 Hz sample window
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(text, handle.read(), "tick samples are rate limited")
            with open(path, "wb") as handle:
                handle.write(b"x" * (512 * 1024 + 1))
            self.clock.advance(1.0)
            motion._trace("obs", self.clock.now, 1, 0.5)
            with open(path, encoding="utf-8") as handle:
                rotated = handle.read()
            self.assertTrue(rotated.startswith("time,event,layer,fraction,displayed,velocity,method"),
                            "an oversized trace is truncated and re-headered")

    def test_an_unwritable_trace_path_is_swallowed(self):
        motion = harness.PreviewMotion(self.cura, self.remember, trace_path="/nonexistent-dir/t.csv")
        motion.write(1, 0.5)
        motion._trace("obs", 1.0, 1, 0.5)

    def test_a_sub_epsilon_shortfall_snaps_to_the_target(self):
        # A rounding leftover must not leave the timer running forever a
        # millionth of a path short of the observation.
        self.motion.write(1, 0.5)
        self.motion._displayed = 0.5 - 1e-7
        self.motion._timer.start()
        self.motion._tick()
        self.assertEqual(0.5, self.motion._displayed)
        self.assertFalse(self.motion._timer.isActive(), "the glide is finished")

    def test_close_stops_the_timer(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.motion.close()
        self.assertFalse(self.motion._timer.isActive())


