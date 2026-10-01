"""Executable preview follower policy contracts."""
from tests import preview_family_support as harness

class OverrideKindTests(harness.OverrideKindTests):
    def test_an_unarmed_follower_cannot_infer_intent(self):
        self.assertIsNone(harness.preview_override_kind(expected_layer=None, current_layer=3))

    def test_either_layer_handle_moving_is_a_layer_override(self):
        self.assertEqual("layer", harness.preview_override_kind(expected_layer=3, current_layer=4))
        self.assertEqual("layer", harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                        expected_minimum_layer=1, current_minimum_layer=2))
        self.assertIsNone(harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                expected_minimum_layer=1, current_minimum_layer=None),
                          "an unreadable minimum cannot attest a change")

    def test_a_path_handle_move_outside_the_tolerance_is_a_path_override(self):
        self.assertEqual("path", harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_path=10.0, current_path=11.0))
        self.assertIsNone(harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                expected_path=10.0, current_path=10.5))
        self.assertEqual("path", harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_path=10.0, current_path=10.75))
        self.assertEqual("path", harness.preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_minimum_path=2, current_minimum_path=3))

    def test_matching_handles_report_no_override(self):
        self.assertIsNone(harness.preview_override_kind(
            expected_layer=3, current_layer=3, expected_minimum_layer=1, current_minimum_layer=1,
            expected_path=5.0, current_path=5.2, expected_minimum_path=1, current_minimum_path=1))


class FollowerDetachTests(harness.FollowerDetachTests):
    def test_a_manual_layer_move_detaches_the_follower(self):
        view = harness.FakeView(layer=3)
        follower, _ = harness.follower_of(view)
        follower.remember()
        view.layer = 4
        self.assertEqual("layer", follower.detect_override())
        self.assertFalse(follower.state.attached)

    def test_a_manual_path_scroll_detaches_the_follower(self):
        view = harness.FakeView(layer=3, path=10.0, minimum_path=2)
        follower, _ = harness.follower_of(view)
        follower.remember()
        view.path = 20.0
        self.assertEqual("path", follower.detect_override())
        self.assertFalse(follower.state.attached)

    def test_a_still_view_reports_no_override(self):
        follower, _ = harness.follower_of(harness.FakeView(layer=3))
        follower.remember()
        self.assertIsNone(follower.detect_override())

    def test_a_detached_suspended_or_viewless_follower_never_reads_intent(self):
        follower, port = harness.follower_of(harness.FakeView(layer=3))
        follower.remember()
        follower.attach(False)
        self.assertIsNone(follower.detect_override())
        follower.attach(True)
        port.suspended = True
        self.assertIsNone(follower.detect_override())
        port.suspended = False
        port.view = None
        self.assertIsNone(follower.detect_override())

    def test_an_unreadable_layer_handle_reads_as_no_change(self):
        follower, port = harness.follower_of(harness.FakeView(layer=3))
        follower.remember()

        def explode():
            raise RuntimeError("view torn down")
        port.view.getCurrentLayer = explode
        self.assertIsNone(follower.detect_override())

    def test_the_first_change_after_an_unarmed_window_detaches(self):
        follower, _ = harness.follower_of(harness.FakeView(layer=3))
        self.assertIsNone(follower.detect_override(), "unarmed: adopt the view's position")
        self.assertEqual(3, follower.state.expected_layer, "the baseline was adopted")
        follower._cura.view.layer = 5
        self.assertEqual("layer", follower.detect_override())


class FollowerViewTests(harness.FollowerViewTests):
    def test_attach_records_the_view_and_a_detach_drops_the_motion(self):
        view = harness.FakeView(layer=2, minimum=1, path=3.0, minimum_path=1)
        follower, _ = harness.follower_of(view)
        motion = harness.MotionTrace()
        follower.bind_motion(motion)
        follower.attach()
        self.assertEqual((2, 1, 3.0), (follower.state.expected_layer, follower.state.expected_minimum,
                                       follower.state.expected_path))
        follower.attach(False)
        self.assertFalse(follower.state.attached)
        self.assertEqual(1, motion.reset_calls, "a detach drops the glide")

    def test_invalidate_view_clears_only_the_view_handles(self):
        follower, _ = harness.follower_of(harness.FakeView(layer=2))
        follower.remember()
        follower.invalidate_view()
        self.assertIsNone(follower.state.expected_layer)
        self.assertIsNone(follower.state.expected_minimum)
        self.assertTrue(follower.state.attached)

    def test_reset_print_keeps_the_armed_baseline(self):
        follower, _ = harness.follower_of(harness.FakeView(layer=2))
        follower.remember()
        follower.reset_print()
        self.assertEqual(2, follower.state.expected_layer, "the view survives a print-end reset")

    def test_reset_tracking_clears_the_estimate_not_the_baseline(self):
        follower, _ = harness.follower_of(harness.FakeView(layer=2))
        follower.observe(harness.snapshot(4), harness.status(), harness.preview_config(), harness.FakeIndex())
        follower.reset_tracking()
        self.assertIsNone(follower.state.anchor_layer)
        self.assertIsNone(follower.state.path_layer)
        self.assertEqual("", follower.state.eta_text)
        self.assertEqual(4, follower.state.expected_layer, "the armed baseline survives")


class FollowerObserveTests(harness.FollowerObserveTests):
    def test_an_inactive_snapshot_reports_connected_and_resets_the_print(self):
        self.follower.remember()
        self.assertEqual(("Connected", ()), self.observe(harness.snapshot(3, active=False)))
        self.assertIsNone(self.follower.state.observed_layer)
        self.assertEqual(3, self.follower.state.expected_layer)

    def test_a_disabled_follower_observes_but_drives_nothing(self):
        self.assertEqual(("Print active", ()), self.observe(config=harness.preview_config(enabled=False)))
        self.assertEqual([], self.view.paths)
        self.assertEqual(0, self.port.writes)

    def test_a_detached_follower_reports_detached(self):
        self.follower.attach(False)
        self.assertEqual(("Detached", ()), self.observe())

    def test_a_busy_cura_is_reported_rather_than_driven(self):
        self.port.suspended = True
        self.assertEqual(("Cura busy", ()), self.observe())
        self.assertEqual([], self.view.paths)

    def test_a_missing_view_or_toolpath_falls_back_to_print_active(self):
        self.port.view = None
        self.assertEqual(("Print active", ()), self.observe())
        self.port.view = harness.FakeView(layer=3)
        self.port.has_toolpath = False
        self.assertEqual(("Print active", ()), self.observe())

    def test_a_missing_layer_waits_for_layer_data(self):
        self.assertEqual(("Waiting for layer data", ()), self.observe(harness.snapshot(None)))

    def test_missing_cura_layer_data_is_named(self):
        self.port.max_layer = None
        self.assertEqual(("Cura layer data unavailable", ()), self.observe())

    def test_a_non_numeric_speed_and_duration_fall_back(self):
        st = harness.status(print_stats={"print_duration": "soon"}, gcode_move={"speed_factor": "fast"})
        self.observe(st=st)
        self.assertEqual((1.0, None), (self.follower.state.speed, self.follower.state.duration))

    def test_a_zero_speed_factor_reads_as_one_and_a_negative_one_is_floored(self):
        self.observe(st=harness.status(gcode_move={"speed_factor": 0}))
        self.assertEqual(1.0, self.follower.state.speed)
        self.observe(st=harness.status(gcode_move={"speed_factor": -2}))
        self.assertEqual(0.05, self.follower.state.speed)

    def test_the_view_is_driven_and_the_baseline_re_armed(self):
        self.view.layer = 2                     # a view trailing the printer
        self.observe()
        self.assertEqual([("minimum", 0), ("layer", 3)], self.view.writes)
        self.assertEqual(3, self.follower.state.expected_layer)

    def test_an_agreeing_view_is_not_written_again(self):
        self.view.layer, self.view.minimum = 3, 0
        self.observe()
        self.assertEqual([], self.view.writes, "the decision was already satisfied")

    def test_the_auto_preview_switch_fires_once(self):
        config = harness.preview_config(auto_preview=True)
        self.observe(config=config)
        self.assertTrue(self.follower.state.switched)
        self.observe(config=config)
        self.assertEqual(1, self.port.switches)

    def test_a_refused_preview_switch_is_retried(self):
        self.port.switch_result = False
        self.observe(config=harness.preview_config(auto_preview=True))
        self.observe(config=harness.preview_config(auto_preview=True))
        self.assertEqual(2, self.port.switches)
        self.assertFalse(self.follower.state.switched)

    def test_a_deferred_view_write_is_issued_but_not_re_armed(self):
        view = harness.DeferredView(layer=0)
        follower, _ = harness.follower_of(view)
        follower.observe(harness.snapshot(3), harness.status(), harness.preview_config(), harness.FakeIndex())
        self.assertEqual([("setMinimumLayer", 0), ("setLayer", 3)], view.pending)
        self.assertEqual(3, follower.state.observed_layer)

    def test_a_deferred_view_write_keeps_the_previous_baseline(self):
        view = harness.DeferredView(layer=3)
        follower, _ = harness.follower_of(view)
        follower.remember()
        follower.observe(harness.snapshot(5), harness.status(), harness.preview_config(), harness.FakeIndex())
        self.assertEqual(3, follower.state.expected_layer,
                         "a not-yet-applied write must not re-arm the baseline")

    def test_the_nozzle_is_left_alone_without_a_followed_path(self):
        config = harness.preview_config(show_toolhead_indicator=True)
        self.observe(config=config)
        self.assertFalse(self.follower.state.nozzle_valid)
        self.assertEqual(0, self.port.nozzles)

    def test_a_paused_observation_is_named(self):
        detail, _ = self.observe(harness.snapshot(3, state="paused"))
        self.assertEqual("Printer paused", detail)

    def test_the_drift_learner_clamps_and_ignores_an_empty_index(self):
        config = harness.preview_config(eta_learn=True)
        index = harness.FakeIndex(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        # duration/boundary = 600/60 clamps to the 2.0 ceiling
        self.observe(st=harness.status(print_stats={"print_duration": 600.0}), config=config, index=index)
        self.assertEqual(2.0, self.follower.state.drift)
        follower, _ = harness.follower_of(harness.FakeView(layer=3))
        follower.observe(harness.snapshot(3), harness.status(print_stats={"print_duration": 30.0}), config,
                         harness.FakeIndex(elapsed=[]))
        self.assertIsNone(follower.state.drift)
        follower2, _ = harness.follower_of(harness.FakeView(layer=3))
        follower2.observe(harness.snapshot(3), harness.status(print_stats={"print_duration": 30.0}), config,
                          harness.FakeIndex(elapsed=[None, None, None, None, None]))
        self.assertIsNone(follower2.state.drift)

    def test_a_short_first_boundary_is_not_turned_into_a_drift(self):
        config = harness.preview_config(eta_learn=True)
        index = harness.FakeIndex(elapsed=[0.0, 30.0, 60.0, 90.0, 120.0])
        self.observe(st=harness.status(print_stats={"print_duration": 600.0}), config=config, index=index)
        self.assertIsNone(self.follower.state.drift)   # boundary <= 60 s is refused

    def test_the_learned_drift_is_clamped_upwards(self):
        config = harness.preview_config(eta_learn=True)
        index = harness.FakeIndex(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0])
        self.observe(st=harness.status(print_stats={"print_duration": 60.0}), config=config, index=index)
        self.assertEqual(0.5, self.follower.state.drift)

    def test_an_index_without_the_layer_leaves_the_drift_alone(self):
        config = harness.preview_config(eta_learn=True)
        index = harness.FakeIndex(elapsed=[0.0, 600.0, 1200.0], layers=2)   # layer 3 is out of range
        self.observe(st=harness.status(print_stats={"print_duration": 60.0}), config=config, index=index)
        self.assertIsNone(self.follower.state.drift)

    def test_the_duration_shortfall_is_ignored_without_a_duration(self):
        config = harness.preview_config(eta_learn=True)
        self.observe(st=harness.status(print_stats={}), config=config,
                     index=harness.FakeIndex(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0]))
        self.assertIsNone(self.follower.state.drift)


class FollowerPathTests(harness.FollowerPathTests):
    def test_a_view_without_the_path_api_reports_unavailable(self):
        self.assertEqual(("Path tracking unavailable", ()),
                         self.path_detail(view=harness.PathBlockedView(layer=3)))

    def test_a_layer_past_the_index_waits(self):
        detail, hydration = self.path_detail(index=harness.FakeIndex(layers=2, elapsed=[0.0, 10.0]))
        self.assertEqual(("Waiting for index", ()), (detail, hydration))
        self.assertEqual([0.0], self.view.paths, "the stale path is parked at the layer start")

    def test_a_missing_index_waits_and_resets_the_motion(self):
        motion = harness.MotionTrace()
        self.follower.bind_motion(motion)
        detail, _ = self.path_detail(index=None)
        self.assertEqual("Waiting for index", detail)
        self.assertEqual(1, motion.reset_calls)

    def test_an_unhydrated_layer_requests_hydration(self):
        detail, hydration = self.path_detail(index=harness.FakeIndex(hydrated=()))
        self.assertEqual(("Hydrating layer", (3,)), (detail, hydration))
        self.assertEqual(0.0, self.follower.state.path_fraction)
        self.assertEqual([0.0], self.view.paths, "a stale target must not fight the hydration")

    def test_an_unhydrated_layer_stops_a_running_animation(self):
        motion = harness.MotionTrace()
        self.follower.bind_motion(motion)
        self.path_detail(index=harness.FakeIndex(hydrated=()))
        self.assertEqual(1, motion.reset_calls)

    def test_a_missing_or_unparsable_file_position_waits(self):
        self.assertEqual("Waiting for motion position", self.path_detail(st={"gcode_move": {}})[0])
        self.assertEqual("Waiting for motion position",
                         self.path_detail(vsc={"file_position": "far"})[0])

    def test_a_maximum_path_count_that_is_missing_or_empty(self):
        self.view.maximum = None
        self.assertEqual("Waiting for Cura paths", self.path_detail()[0])
        self.view.maximum = 0
        self.assertEqual("Layer has no paths", self.path_detail()[0])

    def test_the_minimum_path_handle_is_parked_at_zero(self):
        self.view.minimum_path = 7
        detail, hydration = self.path_detail()
        self.assertEqual(0, self.view.minimum_path)
        self.assertEqual((4,), hydration)
        self.assertTrue(detail.startswith("path 500/1000 "), detail)
        self.assertEqual([0], self.view.minimum_paths)

    def test_the_smoothed_path_is_delegated_to_the_motion_driver(self):
        motion = harness.MotionTrace()
        self.run_path(motion=motion)
        self.assertEqual([(3, 0.5, "byte-range")], motion.writes)
        self.assertEqual([], self.view.paths, "the driver owns the view while smoothing")

    def test_an_unsmoothed_path_writes_the_view_directly(self):
        motion = harness.MotionTrace()
        self.follower.bind_motion(motion)
        config = harness.preview_config(path_follow=True, path_smoothing=False)
        self.run_path(config=config, motion=motion)
        self.assertEqual(1, motion.reset_calls, "the glide is dropped, not animated")
        self.assertEqual([500.0], self.view.paths)
        self.view.path = 499.9
        self.run_path(config=config, motion=motion)
        self.assertEqual([500.0], self.view.paths, "a sub-half-path move is not rewritten")

    def test_a_real_path_write_arms_the_nozzle_indicator(self):
        self.run_path(config=harness.preview_config(path_follow=True, show_toolhead_indicator=True))
        self.assertTrue(self.follower.state.nozzle_valid)
        self.assertEqual(1, self.port.nozzles)

    def test_a_layer_change_drops_the_stale_fraction(self):
        self.run_path()
        self.assertEqual(3, self.follower.state.path_layer)
        self.assertEqual(0.5, self.follower.state.path_fraction)


class FollowerEtaTests(harness.FollowerEtaTests):
    def test_format_duration_pads_and_floors(self):
        self.assertEqual("00:00:00", harness.PreviewFollower.format_duration(-4))
        self.assertEqual("01:02:03", harness.PreviewFollower.format_duration(3723.4))
        self.assertEqual("10:00:00", harness.PreviewFollower.format_duration(36000))

    def test_remaining_needs_a_position_and_an_index(self):
        follower, _, index = self.make()
        self.assertIsNone(follower.remaining(4, None), "no index")
        follower.reset_print()
        self.assertIsNone(follower.remaining(4, index), "no observed layer")

    def test_remaining_reports_the_distance_to_the_selected_layer(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        self.assertAlmostEqual(120.0, follower.remaining(5, index))

    def test_remaining_yields_none_when_a_boundary_is_unknown(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, None, 240.0])
        self.assertIsNone(follower.remaining(4, index))

    def test_remaining_uses_the_within_layer_fraction_as_a_floor(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        follower.observe(harness.snapshot(3, motion=harness.MotionProgress(3, 750, 1000, "test")),
                         harness.status(print_stats={"print_duration": 60.0},
                                virtual_sdcard={"file_position": 400}),
                         harness.preview_config(path_follow=True),
                         harness.FakeIndex(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0], fraction=0.75))
        self.assertAlmostEqual(0.75, follower.state.path_fraction)
        self.assertAlmostEqual(15.0, follower.remaining(4, index),
                               msg="three quarters through the layer is not the layer start")

    def test_remaining_end_anchors_on_the_last_layer_and_the_estimate(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        self.assertAlmostEqual(180.0, follower.remaining_end(index, None))
        self.assertAlmostEqual(480.0, follower.remaining_end(index, 600.0),
                               msg="a later slicer estimate replaces the index end")

    def test_remaining_end_blends_the_mean_layer_duration_without_an_estimate(self):
        follower, _, _ = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        trailing = [0.0, 60.0, 120.0, 180.0, 240.0, None, None]
        self.assertAlmostEqual(180.0, follower.remaining_end(harness.FakeIndex(elapsed=trailing), None))

    def test_remaining_end_gives_up_on_a_position_or_a_timing_it_cannot_read(self):
        follower, _, index = self.make()
        self.assertIsNone(follower.remaining_end(None, 100.0), "no index")
        follower.reset_print()
        self.assertIsNone(follower.remaining_end(index, 100.0), "no observed layer")
        follower2, _, blank = self.make(elapsed=[None, None, None, None, None])
        self.assertIsNone(follower2.remaining_end(blank, 100.0), "no timing at all")
        follower3, _, partial = self.make(elapsed=[60.0, None, None, None, None],
                                          elapsed_layer=2)
        self.assertIsNone(follower3.remaining_end(partial, 100.0), "the layer start is unreadable")

    def test_remaining_end_applies_the_learned_drift(self):
        follower, _, index = self.make(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0], duration=60.0)
        baseline = follower.remaining_end(index, None)
        follower._state = harness.replace(follower.state, eta_learn=True, drift=2.0)
        self.assertAlmostEqual(baseline * 2.0, follower.remaining_end(index, None))
        follower._state = harness.replace(follower.state, eta_learn=True, drift=None)
        self.assertAlmostEqual(baseline, follower.remaining_end(index, None))

    def test_the_eta_text_covers_every_selection_relation(self):
        follower, port, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        cases = ((2, "already printed"), (3, "current print layer"))
        for selected, phrase in cases:
            port.selected_layer = selected
            follower.update_eta(harness.snapshot(3), index)
            self.assertIn(phrase, follower.state.eta_text)
        port.selected_layer = 5
        follower.update_eta(harness.snapshot(3), index)
        self.assertIn("in 00:02:00", follower.state.eta_text)
        self.assertIn("≈", follower.state.eta_text)

    def test_a_far_future_layer_names_the_weekday_clock(self):
        follower, port, index = self.make(elapsed=[0.0, 100000.0, 200000.0, 300000.0, 400000.0])
        port.selected_layer = 4
        follower.update_eta(harness.snapshot(3), index)
        self.assertRegex(follower.state.eta_text, r"^Selected layer 5 — in \d\d:\d\d:\d\d · ≈[A-Z][a-z]{2} \d\d:\d\d$")

    def test_a_missing_last_boundary_does_not_affect_a_mid_print_eta(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, None])
        self.assertAlmostEqual(60.0, follower.remaining(4, index))

    def test_an_unreadable_finish_boundary_falls_back_to_the_layer_start(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, None, 240.0])
        self.assertAlmostEqual(120.0, follower.remaining(5, index))

    def test_an_unreadable_timing_is_named_in_the_eta_text(self):
        follower, port, index = self.make(elapsed=[0.0, None, None, None, None])
        port.selected_layer = 4
        follower.update_eta(harness.snapshot(3), index)
        self.assertIn("ETA unavailable", follower.state.eta_text)

    def test_the_eta_text_is_blank_when_nothing_is_selected_or_observed(self):
        follower, _, index = self.make()
        follower.update_eta(harness.snapshot(3, active=False), index)
        self.assertEqual("", follower.state.eta_text)
        follower.update_eta(harness.snapshot(3), index)
        self.assertEqual("", follower.state.eta_text, "nothing is selected")
        follower.reset_print()
        follower._cura.selected_layer = 5
        follower.update_eta(harness.snapshot(3), index)
        self.assertEqual("", follower.state.eta_text, "no layer has been observed yet")


