"""Executable qt preview integration contracts."""
from tests import qt_integration_support as harness

class PreviewMotionTests(harness.PreviewMotionTests):
    def test_layer_change_jumps_and_same_layer_cruises(self):
        # The fake view exposes 100 max paths; the driver works in fractions.
        self.motion.write(0, 0.5)
        self.assertEqual(self.view.path, 50.0)  # first observation jumps
        # Build a filled observation window so the cruise rate is estimated.
        self.qt.events(300)
        self.motion.write(0, 0.55)
        self.qt.events(300)
        self.motion.write(0, 0.6)
        self.qt.events(400)
        # The head cruises at the windowed rate: it has advanced past the
        # first observation's position and never exceeds the newest
        # observation (the hard ceiling).
        self.assertGreater(self.view.path, 50.0)
        self.assertLessEqual(self.view.path, 60.0)
        # A new layer jumps straight to its target; no cross-layer animation.
        self.motion.write(1, 0.05)
        self.assertEqual(self.view.path, 5.0)

    def test_target_ramps_between_observations(self):
        # Two observations 0.6 s apart: the target is reconstructed between
        # them, so the head glides forward instead of holding at the first
        # observation until the poll lands and then stepping.
        self.motion.write(0, 0.5)
        self.assertEqual(self.view.path, 50.0)
        self.qt.events(600)
        self.motion.write(0, 0.8)
        self.qt.events(250)
        # Mid-ramp: the head has advanced continuously past the first
        # observation but never exceeds the newest one (the hard cap).
        self.assertGreater(self.view.path, 50.0)
        self.assertLess(self.view.path, 80.0)
        # Once the ramp saturates, the display converges to the newest
        # observation, never through it and never backwards.
        self.qt.events(700)
        self.assertLessEqual(self.view.path, 80.0)

    def test_timer_snaps_and_stops_when_decay_converges(self):
        # With no velocity estimate, gap decay approaches the target
        # asymptotically; the last invisible sliver must snap so the timer
        # does not tick at 30 Hz for the whole duration of a pause.
        self.motion.write(0, 0.5)
        self.motion._history.clear()  # a single sample carries no rate
        self.motion.write(0, 0.6)
        self.motion._displayed = 0.6 - 1e-7
        self.motion._tick()
        self.assertEqual(self.motion._displayed, 0.6)
        self.assertFalse(self.motion._timer.isActive())
        self.assertEqual(self.view.path, 60.0)

    def test_slow_poll_interval_scales_the_velocity_window(self):
        # A 5 s poll interval would prune a fixed 2 s window to a single
        # sample and freeze the rate estimate; the window must scale with
        # the measured interval.
        time_module = self.qt.load("PreviewMotion").time
        with harness.patch.object(time_module, "monotonic", side_effect=[0.0, 5.0, 10.0]):
            self.motion._inter_poll = 5.0
            self.motion.write(0, 0.5)
            self.motion.write(0, 0.8)
            self.motion.write(0, 0.9)
        self.assertGreater(self.motion._velocity, 0.0)

    def test_ramp_chains_from_reached_position_and_reset_clears_it(self):
        time_module = self.qt.load("PreviewMotion").time
        with harness.patch.object(time_module, "monotonic", side_effect=[0.0, 0.5, 1.0, 1.25]):
            self.motion.write(0, 0.5)
            self.motion.write(0, 0.8)
            self.motion.write(0, 0.9)
            # The previous ramp (0.5 -> 0.8 over 0.5 s) had saturated by the
            # time the next observation arrived, so the new ramp continues
            # from the reached position without a jump.
            self.assertAlmostEqual(self.motion._ramp_from, 0.8)
            self.assertEqual(self.motion._ramp_to, 0.9)
            self.assertAlmostEqual(self.motion._inter_poll, 0.5)
            # Mid-ramp the reconstructed target is linearly between the two.
            self.assertAlmostEqual(self.motion._current_target(1.25), 0.85)
        self.motion.reset()
        self.assertIsNone(self.motion._ramp_from)
        self.assertIsNone(self.motion._ramp_to)
        self.assertIsNone(self.motion._obs_time)
        self.assertEqual(self.motion._inter_poll, 0.5)  # survives a reset

    def test_trace_writes_only_when_a_path_is_provided(self):
        with harness.tempfile.TemporaryDirectory() as directory:
            traced = self.qt.load("PreviewMotion").PreviewMotion(
                self.cura, lambda: None, trace_path=harness.os.path.join(directory, "trace.csv"))
            self.addCleanup(traced.close)
            traced.write(0, 0.5)
            traced.write(0, 0.55)
            path = harness.os.path.join(directory, "trace.csv")
            self.assertTrue(harness.os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            # The header is written on rollover; observation rows always are.
            self.assertIn(",obs,0,0.500000,", content)

    def test_target_behind_display_never_moves_backwards(self):
        self.motion.write(0, 0.9)
        self.qt.events(200)
        reached = self.view.path
        self.motion.write(0, 0.3)  # stale/ambiguous observation behind us
        self.qt.events(200)
        self.assertGreaterEqual(self.view.path, reached)
        self.assertLessEqual(self.view.path, 90.0)

    def test_writes_are_remembered(self):
        self.motion.write(0, 0.5)
        self.qt.events(60)
        self.assertGreater(self.remembers, 0)

    def test_reset_stops_animation_until_next_observation(self):
        self.motion.write(0, 0.7)
        self.qt.events(60)
        moving = self.view.path
        self.motion.reset()
        self.qt.events(120)
        self.assertEqual(self.view.path, moving)
        self.motion.write(0, 0.75)
        self.assertEqual(self.view.path, 75.0)  # re-synchronises with a jump


