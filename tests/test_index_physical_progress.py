"""Executable index physical progress contracts."""
from tests import index_plate_support as harness

class PlateSplitRefinementTests(harness.PlateSplitRefinementTests):
    def test_flat_first_layer_with_start_and_end_z_moves_is_not_spiral(self):
        from array import array
        from plugins.GCodeIndex import LayerMotionIndex

        index = LayerMotionIndex(
            ranges=[(0, 200)],
            motion_offsets=[array("Q", range(10, 142, 11))],
            motion_x=[array("f", range(12))],
            motion_y=[array("f", [0] * 12)],
            motion_z=[array("f", [.2] * 11 + [.4])],
            layer_start_positions=[(0, 0, 0.0)],
        )
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.assertFalse(view.continuous_z_at(0))
        self.assertIsNone(view.spiral_z_split(0, .05))

    def test_flat_to_spiral_transition_uses_exact_next_boundary(self):
        from array import array
        from plugins.GCodeIndex import LayerMotionIndex

        # The real vase file's seventh layer stays at 1.4 for half its
        # motions, then winds from 1.4 to 1.6. The first half must retain
        # XY progress, while the eighth layer must wait for Z 1.6.
        heights = [1.4] * 33 + [1.4 + .2 * n / 32 for n in range(1, 33)]
        index = LayerMotionIndex(
            ranges=[(0, 200), (200, 300)],
            motion_z=[array("f", heights), array("f", [1.602 + n * .018 for n in range(12)])],
            layer_start_positions=[(0, 0, 1.4), (0, 0, 1.6)],
        )
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.assertFalse(view.continuous_z_at(0))
        self.assertIsNone(view.spiral_z_split(0, 1.5))
        self.assertAlmostEqual(view.continuous_z_boundary(1), 1.6)

        # A conventional first layer has two Z jumps but no rising
        # toolpath, so it must keep the usual flat-layer threshold.
        index.layer_start_positions[0] = (0, 0, 0.0)
        index.motion_z[0] = array("f", [.2] * 64 + [.4])
        self.assertIsNone(view.continuous_z_boundary(1))
        index.motion_z[0] = array("f", [.2] * 45 + [.3] * 10 + [.4] * 10)
        self.assertIsNone(view.continuous_z_boundary(1),
                          "two discrete height changes do not make a spiral")

    def test_spiral_layer_enters_before_halfway_through_its_z_ramp(self):
        from array import array
        from plugins.GCodeIndex import LayerMotionIndex

        index = LayerMotionIndex(
            ranges=[(0, 100), (100, 200), (200, 300)],
            motion_offsets=[array("Q", [50]), array("Q", [110 + 7 * n for n in range(12)]),
                            array("Q", [250])],
            motion_x=[array("f", [0]), array("f", range(12)), array("f", [0])],
            motion_y=[array("f", [0]), array("f", [0] * 12), array("f", [0])],
            motion_z=[array("f", [1.6]), array("f", [1.602 + n * .018 for n in range(12)]),
                      array("f", [1.8])],
            layer_start_positions=[(0, 0, 1.4), (0, 0, 1.6), (0, 0, 1.8)],
        )
        # The parser can enter the new layer before the nozzle does.
        self.assertFalse(index.layer_entry_confirmed(1, 1, (0, 0, 1.59), previous_z=1.56,
                                                     continuous_z=True))
        # Once the nozzle is on its ramp, the completed motion's Z
        # naturally trails the live Z. It must not hold the fill at zero.
        self.assertTrue(index.layer_entry_confirmed(1, 1, (1.5, 0, 1.627), previous_z=1.56,
                                                    continuous_z=True))
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.assertTrue(view.continuous_z_at(1))
        self.assertEqual(view.continuous_z_boundary(2), 1.8)
        index.motion_z[1] = array("f", [1.6] * 11 + [1.8])
        self.assertFalse(view.continuous_z_at(1), "one end-of-layer Z move is not a spiral")
        self.assertIsNone(view.continuous_z_boundary(2))
        # A flat first layer may start at zero and end with a move to
        # the next layer's Z. Its two jumps are not continuous motion.
        index.layer_start_positions[1] = (0, 0, 0.0)
        index.motion_z[1] = array("f", [.2] * 11 + [.4])
        self.assertFalse(view.continuous_z_at(1))
        index.layer_start_positions[1] = (0, 0, 1.6)
        dipping = [1.602 + n * .018 for n in range(12)]
        dipping[5] = dipping[4] - .01
        index.motion_z[1] = array("f", dipping)
        self.assertFalse(view.continuous_z_at(1), "a nonmonotonic Z path cannot use binary search")
        index.motion_z[1] = array("f", [1.602 + n * .018 for n in range(12)])
        self.assertGreater(view.spiral_z_split(1, 1.65), 0)
        self.assertIsNone(view.spiral_z_split(1, 1.59))
        self.service._view = view
        self.service.observe_motion(0, 50, (0, 0, 1.6), extruding=True)
        first = self.service.observe_motion(1, 140, (3.5, 0, 1.65), extruding=True)
        self.assertGreater(first.fraction, 0, "physical spiral entry must publish on its first poll")
        self.assertEqual(first.split, view.spiral_z_split(1, 1.65))
        self.service._split_tracker.reset()
        self.service.observe_motion(0, 50, (0, 0, 1.6), extruding=True)
        missed_xy = self.service.observe_motion(1, 140, (3.5, 4, 1.65), extruding=True)
        self.assertGreater(missed_xy.fraction, 0, "rising Z must recover a missed XY match")
        self.assertEqual(missed_xy.split, first.split)

    def test_penguin_telemetry_enters_all_three_layers(self):
        from tests.harness.gcodegen import penguin_playback, playback_sample
        data, rows = penguin_playback()
        index = harness.build_index_from_bytes(data)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        best = [0.0, 0.0, 0.0]
        for tick in range(1, 81):
            sample = playback_sample(rows, tick / 80)
            motion = self.service.observe_motion(sample["layer"], sample["offset"],
                sample["position"][:3], extruding=sample["extruder_velocity"] > 1e-6)
            best[sample["layer"]] = max(best[sample["layer"]], motion.fraction)
        for layer, progress in enumerate(best):
            self.assertGreater(progress, .9, f"layer {layer + 1} never followed: {best}")

    def test_partial_motion_projects_only_onto_the_accepted_unfinished_move(self):
        from plugins.GCodeIndex import LayerMotionIndex
        from array import array
        index = LayerMotionIndex(ranges=[(0, 100)], motion_x=[array("f", [10, 20])],
            motion_y=[array("f", [0, 0])], motion_z=[array("f", [.2, .2])],
            layer_start_positions=[(0, 0, .2)])
        self.assertAlmostEqual(index.partial_motion(0, 0, (4, 0, .2)), .4)
        self.assertAlmostEqual(index.partial_motion(0, 1, (16, 0, .2)), .6)
        self.assertEqual(index.partial_motion(0, 0, (16, 0, .2)), 0,
                         "projection must not search forward to a different motion")
        self.assertEqual(index.partial_motion(0, 0, (4, 0, 5)), 0)
        self.assertEqual(index.partial_motion(0, 2, (20, 0, .2)), 0)
        self.assertEqual(index.partial_motion(-1, 0, (4, 0, .2)), 0)
    def test_live_motion_is_independent_of_geometry_and_shared_without_rematching(self):
        from unittest.mock import patch

        offsets = self._bind()
        self.service._plate_layers_memos.clear()
        self.service._decoded_lru.clear()
        # The hydrated motion index can track even before any renderer asks
        # for a layer. Holding a snapshot must not require a decoded canvas.
        motion = self.service.observe_motion(0, offsets[19], (5.0, 0.0, 0.2))
        self.assertEqual((motion.split, motion.motion_total, motion.fraction), (6, 20, 0.3))
        floor = self.service._split_tracker.floor
        with patch.object(self.service, "observe_motion", side_effect=AssertionError("double match")):
            payload = self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2), motion=motion)
        self.assertEqual(payload["split"], motion.split)
        self.assertEqual(self.service._split_tracker.floor, floor)
        self.service.set_manual_anchor(0)
        self.service.set_manual_split(2)
        self.assertEqual(self.service.plate_progress(0)["split"], 2)
        self.assertEqual(self.service._split_tracker.floor, floor, "manual scrubbing must not change physical tracking")

    def test_excluded_object_entry_travel_cannot_seed_a_future_boundary(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                self.service._split_tracker.reset()
                offsets = self._bind(layers=3)
                index = self.service._view._index
                if compact:
                    index.layer_motion_counts = [20] * 3
                    index.motion_offsets = [harness.array("Q") for _ in range(3)]
                self.assertGreater(self.service.plate_progress(
                    0, offsets[19], (5.0, 0.0, 0.2), extruding=True)["split"], 0)
                # At the new Z, a long travel crosses future geometry while
                # excluded G-code advances the parser far into the new layer.
                for x in (10.0, 12.0, 8.0, 5.0):
                    self.assertEqual(self.service.plate_progress(
                        1, offsets[19], (x, 0.0, 0.2), extruding=False)["split"], 0)
                self.assertTrue(self.service._split_tracker.awaiting_layer_entry)
                self.assertEqual(self.service.plate_progress(
                    1, offsets[19], (2.0, 0.0, 0.2), extruding=True)["split"], 2 if compact else 3)
                self.assertFalse(self.service._split_tracker.awaiting_layer_entry)
                # Ordinary travel after entry can still advance printed history.
                self.assertEqual(self.service.plate_progress(
                    1, offsets[19], (5.0, 0.0, 0.2), extruding=False)["split"], 5 if compact else 6)

    def test_a_machine_without_live_telemetry_keeps_the_coarse_boundary(self):
        offsets = self._bind()
        self.assertEqual(self.service.plate_progress(0, offsets[9])["split"], 9)
        # The floor never latches onto a coarse value, so a printer that
        # reports no live position paints exactly what it always did.
        self.assertEqual(self.service.plate_progress(0, offsets[19])["split"], 19)

    def test_the_live_position_refines_the_split_and_holds_it(self):
        offsets = self._bind()
        # The dispatcher is at the layer's end; the head is at x = 5,
        # which is 6 finished motions.
        payload = self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))
        self.assertEqual(payload["split"], 6)
        self.assertEqual(payload["method"], "motion index")
        self.assertIsNotNone(payload["layers"]["current"])
        # Telemetry gone: the painted boundary is held, never replaced
        # by the dispatcher's position.
        self.assertEqual(self.service.plate_progress(0, offsets[19])["split"], 6)
        # Telemetry present but off-path (a park, a probe): the same.
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (-40.0, -40.0, 0.2))["split"], 6)

    def test_the_boundary_never_walks_backwards_across_polls(self):
        offsets = self._bind()
        painted = 0
        for x in (4.0, 9.0, 9.0, 6.0, 12.0, 11.0, 18.5):
            split = self.service.plate_progress(0, offsets[19], (x, 0.0, 0.2))["split"]
            self.assertGreaterEqual(split, painted, "the fill rewound at x=%s" % x)
            painted = split
        self.assertEqual(painted, 19)

    def test_a_paused_park_holds_the_boundary(self):
        offsets = self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # Paused: the dispatcher is frozen where it stopped and the
        # pause macro parks the head off the path. Nothing is being
        # printed, so nothing new is painted — the stalled-ahead parser
        # position is never the answer, and repetition changes nothing.
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, offsets[19], (140.0, 140.0, 10.0))["split"], 6)
        # Resumed: the head is back on the path ahead, and the paint
        # follows it again.
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (12.0, 0.0, 0.2))["split"], 13)

    def test_a_pause_park_on_old_geometry_cannot_erase_the_boundary(self):
        offsets = self._bind()
        self.assertEqual(self.service.plate_progress(0, offsets[19], (12.0, 0.0, 0.2))["split"], 13)
        for _ in range(8):
            self.assertEqual(self.service.plate_progress(
                0, offsets[19], (1.0, 0.0, 0.2), paused=True)["split"], 13)
        # Resume can first report the macro's parked head before the
        # restored physical position lands. Those are not print moves.
        for _ in range(5):
            self.assertEqual(self.service.plate_progress(
                0, offsets[19], (1.0, 0.0, 0.2))["split"], 13)
        self.assertEqual(self.service.plate_progress(0, offsets[19], (13.0, 0.0, 0.2))["split"], 14)

    def test_an_anchor_off_the_index_reads_unavailable(self):
        # No layer is no boundary: the face ghosts rather than colouring
        # to another layer's count, whatever the telemetry says.
        offsets = self._bind()
        payload = self.service.plate_progress(9, offsets[19], (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload["method"], "unavailable")

    def test_the_boundary_resets_with_the_layer(self):
        offsets = self._bind(layers=3)
        self.assertEqual(
            self.service.plate_progress(1, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # Another layer's count is another layer's boundary: layer 2
        # starts its own, unfloored by layer 1's paint. The poll that
        # ENTERS a layer reads zero — nothing has printed on it yet —
        # and the poll after it refines from the boundary that entry
        # set, so the reset costs one poll and never inherits the
        # previous layer's paint.
        self.assertEqual(
            self.service.plate_progress(2, offsets[19], (2.0, 0.0, 0.2))["split"], 0)
        self.assertEqual(
            self.service.plate_progress(2, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

    def test_entering_a_layer_never_publishes_a_future_match(self):
        # The live report: the fill jumped at the START of a new layer and
        # rewound once the truth caught up. The nozzle has just arrived and
        # has printed nothing on the layer, while the parser's read position
        # is already past it — so the entry poll's search window spans the
        # whole layer and a coincidental XY match anywhere in it won. The
        # head over x=5 reaches layer 1's own geometry, and reading that as
        # the boundary is the jump.
        offsets = self._bind(layers=3)
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        entry = self.service.plate_progress(1, offsets[19], (5.0, 0.0, 0.2))
        self.assertEqual(entry["split"], 0,
                         "entering a layer published a match from its future")
        # And the entry is an INITIALISATION, not a cap: the next poll
        # refines to the head's real place with no lag behind it.
        self.assertEqual(
            self.service.plate_progress(1, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

    def test_the_boundary_resets_with_the_print(self):
        offsets = self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # The next print's file: the same anchor, a different job, so
        # the previous print's boundary is not this one's floor.
        self.job = ("next.gcode", 200, 1)
        self.service.bind(self.job)
        self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

    def test_parser_layer_advance_waits_for_physical_height_in_both_geometry_paths(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                self.service._split_tracker.reset()
                self._bind(layers=3)
                index = self.service._view._index
                index.layer_start_positions = [(0.0, 0.0, 0.0),
                                               (0.0, 0.0, 0.2),
                                               (0.0, 0.0, 0.4)]
                index.motion_z = [harness.array("f", [0.2] * 20),
                                  harness.array("f", [0.4] * 20),
                                  harness.array("f", [0.6] * 20)]
                positions = [int(offsets[-1]) for offsets in index.motion_offsets]
                if compact:
                    index.layer_motion_counts = [20] * 3
                    index.motion_offsets = [harness.array("Q") for _ in range(3)]
                    index.motion_z = [harness.array("f") for _ in range(3)]
                self.assertGreater(self.service.plate_progress(
                    0, positions[0], (5.0, 0.0, 0.2))["split"], 0)
                for _ in range(5):
                    self.assertEqual(self.service.plate_progress(
                        1, positions[1], (5.0, 0.0, 0.2))["split"], 0,
                        "repeated XY on the previous height seeded the new layer")
                self.assertGreater(self.service.plate_progress(
                    1, positions[1], (5.0, 0.0, 0.4))["split"], 0)

    def test_compact_layer_markers_after_the_z_move_do_not_pin_progress_at_zero(self):
        self._bind(layers=3)
        index = self.service._view._index
        positions = [int(offsets[-1]) for offsets in index.motion_offsets]
        index.layer_start_positions = [(0.0, 0.0, 0.2),
                                       (0.0, 0.0, 0.4),
                                       (0.0, 0.0, 0.6)]
        index.layer_motion_counts = [20] * 3
        index.motion_offsets = [harness.array("Q") for _ in range(3)]
        index.motion_z = [harness.array("f") for _ in range(3)]
        self.assertGreater(self.service.plate_progress(
            0, positions[0], (5.0, 0.0, 0.2))["split"], 0)
        self.assertEqual(self.service.plate_progress(
            1, positions[1], (5.0, 0.0, 0.4))["split"], 0)
        self.assertGreater(self.service.plate_progress(
            1, positions[1], (5.0, 0.0, 0.4))["split"], 0)

    def test_float_noise_cannot_choose_a_future_repeated_pass(self):
        best = harness.gcode_index.better_candidate(1e-12, 5, float("inf"), None, 4)
        self.assertEqual(harness.gcode_index.better_candidate(0.0, 500, *best, 4)[1], 5)
        reverse = harness.gcode_index.better_candidate(0.0, 500, float("inf"), None, 4)
        self.assertEqual(harness.gcode_index.better_candidate(1e-12, 5, *reverse, 4)[1], 5)

    def test_a_late_floor_reaches_back_to_the_nozzle_after_a_wrong_match(self):
        # An off-path hop whose XY lands on a LATER pass's stroke is a
        # genuine match at distance zero, and the floor moves there while
        # the head never went anywhere. Well short of it now lies the
        # geometry the nozzle is actually on, and the search never looks
        # below the floor: every poll REFUSES. A refusal has to count as
        # an unconfirmed poll — a counter that resets on it can never
        # widen the window that would reach the nozzle, and the fill sits
        # hundreds of motions ahead of the head for the layer's life.
        self._bind_runs([self._run(0, 100, 0.0), self._run(900, 100, 40.0)],
                        motions=4000)
        # 90/100 of the layer's byte range is motion 3600 of 4000: the
        # dispatcher reading far past a nozzle that is 50 motions in.
        self.assertEqual(
            self.service.plate_progress(0, 90, (50.0, 0.0, 0.2))["split"], 50)
        for _hop in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 90, (50.0, 40.0, 0.2))["split"], 950,
                "the hop was not matched where it landed")
        # Back on its own stroke, every poll now REFUSES: the window
        # hangs off the wrong floor, so the geometry under the nozzle is
        # outside it and the search finds nothing at all rather than a
        # below-floor match. The displayed boundary holds the wrong pass
        # until the expansion reaches the head, then steps back onto it.
        splits = [self.service.plate_progress(0, 90, (50.5 + poll, 0.0, 0.2))["split"]
                  for poll in range(5)]
        self.assertEqual(splits[0], 950, "the fill moved before the evidence landed")
        self.assertLess(splits[-1], 55, "three physical matches never corrected the floor")
        self.assertGreater(splits[-1], 49, "the fill lost the head's own stroke")
        self.assertEqual(self.service._split_tracker.floor, splits[-1])
        # ...and tracks the nozzle from there, on the pass it is on.
        self.assertEqual(
            self.service.plate_progress(0, 90, (60.0, 0.0, 0.2))["split"], 60)

    def test_an_unhydrated_layer_holds_the_nozzles_own_pass(self):
        # The same repeated toolpath with the arrays still empty: the
        # payload's geometry is all the search has, and the pass the
        # nozzle is on is the one the tie resolves to — the vertex order
        # the painter happened to emit is not evidence of anything. Once
        # the boundary has cleared the first pass, the fill follows the
        # nozzle into the second instead of painting the first again.
        self._bind_runs([self._run(0, 200, 0.0), self._run(200, 200, 0.0)],
                        motions=400)
        self.assertEqual(
            self.service.plate_progress(0, 90, (199.0, 0.0, 0.2))["split"], 199)
        painted = 199
        for x, expected in ((5.0, 205), (6.0, 206), (10.0, 210), (11.0, 211)):
            split = self.service.plate_progress(0, 90, (x, 0.0, 0.2))["split"]
            self.assertEqual(split, expected,
                             "the fill resolved to the pass already printed")
            self.assertGreaterEqual(split, painted)
            painted = split

    def test_the_unhydrated_layer_splits_on_its_payload_geometry(self):
        # The arrays are empty until the hydration lands, and the byte
        # fraction that stands in for them is NOT proportional to motion
        # — the live report's drifting fill. The payload's geometry is
        # what the plate displays, so the search runs over THAT.
        self._bind_payload()
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertEqual(payload["split"], 5, "the byte fraction won over the geometry")
        self.assertEqual(payload["method"], "motion index")
        self.assertIsNotNone(payload["layers"]["current"])

    def test_a_payload_boundary_holds_when_the_head_leaves_the_path(self):
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))["split"], 5)
        # A park or a probe: the search finds nothing near, so the
        # boundary already painted is what the fill keeps.
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 50, (140.0, 140.0, 10.0))["split"], 5)

    def test_the_first_poll_of_an_unhydrated_layer_paints_nothing_off_path(self):
        # The byte fraction can read ahead of the nozzle, so it is never
        # the first boundary: with telemetry and no match yet, nothing
        # has printed on the layer and nothing is painted.
        self._bind_payload()
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 50, (140.0, 140.0, 10.0))["split"], 0)

    def test_a_machine_without_live_telemetry_paints_the_payload_coarse(self):
        # No live position anywhere: the coarse is all that machine has,
        # and the floor keeps it monotonic across polls.
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50)["split"], 10)
        self.assertEqual(self.service.plate_progress(0, 20)["split"], 10,
                         "the parser's earlier position walked the fill back")

    def test_the_overshoot_lock_steps_the_floor_back_to_the_truth(self):
        # The nozzle sitting ON geometry behind an overshot floor is the
        # overshoot-lock's evidence: three polls of the same verdict step
        # the floor back to the truth instead of stalling the fill until
        # the nozzle catches up.
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50, (15.0, 0.0, 0.2))["split"], 15)
        for _poll in range(2):
            self.assertEqual(self.service.plate_progress(0, 50, (6.0, 0.0, 0.2))["split"], 15)
            self.assertEqual(self.service._split_tracker.floor, 15,
                             "the floor stepped back before the third below-floor verdict")
        self.assertEqual(self.service.plate_progress(0, 50, (6.0, 0.0, 0.2))["split"], 6)
        self.assertEqual(self.service._split_tracker.floor, 6, "the overshoot lock never released")
        # Resumed: the truth is the floor now, and the fill follows the
        # nozzle forward from it again.
        self.assertEqual(self.service.plate_progress(0, 50, (12.0, 0.0, 0.2))["split"], 12)

    def test_the_observed_advance_widens_the_next_search(self):
        # The ahead side is the advance the last poll actually observed
        # (a fresh floor means the nozzle is a bounded step past it), so
        # repeated geometry cannot win the match and overshoot the fill.
        self._bind_payload(motions=6000)
        self.assertEqual(self.service.plate_progress(0, 50, (1000.0, 0.0, 0.2))["split"], 1000)
        self.assertEqual(self.service._split_tracker.advance_max, 512,
                         "the floor-less first poll measured an advance")
        self.assertEqual(self.service.plate_progress(0, 50, (2900.0, 0.0, 0.2))["split"], 2900)
        self.assertEqual(self.service._split_tracker.advance_max, 1900,
                         "the observed advance never widened the next window")

    def test_a_layer_without_a_motion_count_reads_unavailable(self):
        # Neither arrays nor a count: there is nothing to measure, so the
        # face ghosts rather than colouring a boundary it cannot know.
        self._bind_payload(count=0)
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload["method"], "unavailable")

    def test_the_split_without_an_index_reads_unavailable(self):
        self._bind_payload()
        self.assertEqual(len(self.service.plate_layers(0)), 3)
        self.service._view = None
        self.assertEqual(self.service.plate_layers(0), {})
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload, {"layers": {}, "split": None, "method": "unavailable",
                               "partial": 0.0, "motionTotal": 0, "anchor": 0, "refusal": ""})


class RepeatedGeometrySplitTests(harness.RepeatedGeometrySplitTests):
    def test_a_read_burst_does_not_seed_the_floor_above_the_nozzle(self):
        # A chunk read after a stutter puts the dispatcher's read point
        # many hundreds of motions past the nozzle — well beyond the
        # window the search used to keep around it. Only motions the head
        # has not reached are left in that window, and on this layer the
        # nearest of them shares the head's XY: the fill painted the
        # later pass, and because that value was then the floor, every
        # later poll agreed with it and the fill never came back.
        self._bind(passes=60)
        for truth in (10.0, 13.5, 17.0, 20.5, 24.0):
            split = self._poll(truth, lead=1200)
            self.assertLessEqual(
                split, truth + 1, "the fill painted a stroke the head has not reached")
            self.assertGreaterEqual(
                split, truth - 1, "the fill lost the head's own stroke")
            self.assertLessEqual(
                self.service._split_tracker.floor, truth, "the floor locked ahead of the head")

    def test_the_fill_never_walks_back_across_a_repeated_pass(self):
        # The nozzle finishes pass 0 and starts pass 1 on the same
        # coordinates. Resolving that tie to the earlier pass pulls the
        # boundary back under paint the plate already drew, and the
        # overshoot lock then commits the rewind: the fill visibly jumps
        # backwards every few polls, at the print's own pace. Nothing
        # may be repainted and no stroke ahead of the head may be drawn.
        self._bind(passes=6)
        painted = 0
        truth = 18.0
        while truth <= 30.0:
            split = self._poll(truth, lead=40)
            self.assertGreaterEqual(
                split, painted, "the fill walked back at motion %s" % truth)
            self.assertLessEqual(
                split, truth + 1, "the fill painted a stroke the head has not reached")
            painted = split
            truth += 1.5
        self.assertGreaterEqual(painted, 29, "the fill stopped following the head")
        # Adversarial ordering: the status feed hands the same sample
        # twice, then one from before the boundary (a re-applied G-code
        # offset moves the reported position back without the nozzle
        # moving). Repeated geometry ties either way, and no tie may walk
        # the fill back under paint the plate already drew.
        self.assertGreaterEqual(self._poll(30.0, lead=40), painted)
        stale = self._poll(18.0, lead=40)
        self.assertGreaterEqual(stale, painted, "a stale sample rewound the fill")
        self.assertLessEqual(stale, painted + 2 * harness._LINES_PER_PASS,
                             "a stale sample painted far ahead of the head")

    def test_a_hydration_landing_mid_layer_keeps_the_fill_on_its_own_pass(self):
        # The layer's first polls land before its arrays arrive: the
        # payload's geometry is the only toolpath the search has, so the
        # boundary the payload search accepts is the floor the hydrated
        # search inherits. The delivery is asynchronous — the arrays land
        # whenever the worker gets to them — and it is not a new layer:
        # the fill must not jump, blank or lose the pass it was on.
        index = harness.build_index_from_bytes(harness._repeated_layer_gcode(passes=6))
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.job, index)
        from plugins.PlateProgress import prepare_layer
        self.service._decoded_lru[0] = prepare_layer(index, 0)
        self.index = index
        self.count = index.motion_count(0)
        self.offsets = list(index.motion_offsets[0])
        hydrated = harness.array(index.motion_offsets[0].typecode,
                         index.motion_offsets[0])
        index.motion_offsets[0] = harness.array("Q")
        for truth in (10.0, 14.0, 18.0):
            split = self._poll(truth, lead=40)
            self.assertLessEqual(split, truth + 1, "the payload search painted ahead")
            self.assertGreaterEqual(split, truth - 1, "the payload search lost the head")
        floor = self.service._split_tracker.floor
        # The hydration lands: the arrays are what the split rides from
        # here, and the floor is the continuity between the two halves.
        index.motion_offsets[0] = hydrated
        for truth in (19.5, 21.0, 22.5):
            position = self.offsets[int(truth) + 40]
            payload = self.service.plate_progress(
                0, position, harness._nozzle_at(index, 0, truth))
            self.assertIsNotNone(payload["layers"]["current"],
                                 "the hydration blanked the layer")
            split = payload["split"]
            self.assertGreaterEqual(split, floor, "the fill jumped back at the handover")
            self.assertLessEqual(split, truth + 1, "the hydrated search painted ahead")
            self.assertGreaterEqual(split, truth - 1, "the hydrated search lost the head")
            floor = split

    def test_an_off_path_travel_then_resumption_holds_the_nozzles_own_pass(self):
        # A park, a probe, a cross-plate travel: the nozzle is off the
        # extrusion geometry and nothing is printing, so the boundary
        # holds. Resumed, the head is back on its own pass — the stroke a
        # later pass also visits must not claim the fill, and the stroke
        # already passed must not take it back.
        self._bind(passes=60)
        self.assertEqual(self._poll(20.0, lead=1200), 20)
        for _park in range(2):
            self.assertEqual(
                self._poll(20.0, lead=1200, live=(140.0, 140.0, 10.0)), 20,
                "an off-path park moved the boundary")
        for truth in (21.0, 22.0, 23.0):
            split = self._poll(truth, lead=1200)
            self.assertLessEqual(split, truth + 1)
            self.assertGreaterEqual(split, truth - 1)

    def test_a_stale_live_sample_does_not_strand_the_fill(self):
        # The nozzle is reported where it is not — a stale position, a
        # re-applied homing origin — standing on a stroke a later pass
        # owns. That match is genuine, it is the nearest geometry by a
        # wide margin, and the floor moves there hundreds of motions
        # ahead of the head. What must not happen is what the live
        # report showed: the head's own stroke is then outside the
        # search's window, the refinement returns NOTHING poll after
        # poll, and a search that only widens its window on a below-floor
        # MATCH never reaches the geometry the nozzle is standing on. The
        # fill stays stranded ahead of the head for the layer's life.
        # Three reliable samples on the truth step the floor back to it.
        self._bind(passes=60, drift=4.0)
        # Line 3's stroke: motion 8 of pass 0, and the same stroke 32 mm
        # up at motion 8 of pass 8. Lines 0-9 of each pass are unique to
        # it — the drift is two and a half line spacings — so neither
        # sample is a tie.
        self.assertEqual(self._poll(8.0, lead=1200, live=(50.0, 1.2, 0.2)), 8)
        stranded = self._poll(8.0, lead=1200, live=(50.0, 33.2, 0.2))
        self.assertEqual(stranded, 8 * 2 * harness._LINES_PER_PASS + 8,
                         "the stale sample was not matched where it landed")
        for poll in range(3):
            split = self._poll(8.0, lead=1200, live=(50.0, 1.6 + 0.4 * poll, 0.2))
            if poll < 2:
                self.assertEqual(split, stranded,
                                 "the floor stepped back before the third sample")
        self.assertEqual(split, 14, "the fill stayed stranded on the later pass")
        self.assertEqual(self.service._split_tracker.floor, 14)
        # The head keeps printing: the fill follows it from the truth,
        # unhaunted by the pass the stale sample claimed.
        self.assertEqual(self._poll(8.0, lead=1200, live=(50.0, 2.8, 0.2)), 16)


class PayloadRefinementTests(harness.PayloadRefinementTests):
    def test_a_payload_and_a_live_position_are_both_required(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20)]}, motions=20)
        # No geometry, no telemetry, a position with no Z, or one that is
        # not a number at all: there is nothing to search with.
        self.assertIsNone(self._refine(None, 10, (5.0, 0.0, 0.2)))
        self.assertIsNone(self._refine(payload, 10, None))
        self.assertIsNone(self._refine(payload, 10, (5.0, 0.0)))
        self.assertIsNone(self._refine(payload, 10, (None, 0.0, 0.2)))

    def test_the_first_search_is_wide_around_the_coarse(self):
        # The layer's first poll has no floor, so the window is centred on
        # the parser's coarse position: the lookahead between the
        # dispatcher and the nozzle can be thousands of motions.
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        self.assertEqual(self._refine(payload, 10000, (5000.0, 0.0, 0.2)), 5000)
        # Outside the window the nearest edge is hundreds of mm away: the
        # refinement refuses rather than report that as the nozzle.
        self.assertIsNone(self._refine(payload, 10000, (1000.0, 0.0, 0.2)))

    def test_the_floor_seeds_the_window_and_the_motion_count_caps_it(self):
        payload = self._payload({"W": [self._row(0.0, 0, 6000)]}, motions=6000)
        # The seed is the FLOOR, not the coarse: the parser sits at 5900
        # while the nozzle is 50 motions behind it. The window is capped
        # by the layer's own motion count, never searched past it.
        self.assertEqual(self._refine(payload, 5900, (5850.4, 0.0, 0.2), floor=5800), 5850)

    def test_the_stall_expansion_reaches_a_pass_the_narrow_windows_miss(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        # 5,000 motions past the floor: neither the 1x nor the 8x window
        # can see it. Consecutive stalled polls expand the ahead side,
        # and only then does the search reach the pass the nozzle has
        # since moved on to.
        self.assertIsNone(
            self._refine(payload, 100, (5000.0, 0.0, 0.2), floor=0, ahead=512))
        self.assertEqual(
            self._refine(payload, 100, (5000.0, 0.0, 0.2), floor=0, ahead=512, stall=2),
            5000)

    def test_an_isolated_vertex_is_a_candidate_of_its_own(self):
        # A travel split can leave a motion carrying a single vertex: it
        # is that motion's only geometry, so skipping it hides the truth
        # and the search paints a future pass instead.
        self.assertEqual(
            self._refine(self._payload({"V": [[[7.0, 0.0, 12.0]]]}, motions=20),
                         10, (7.0, 0.0, 0.2)), 12)
        # A vertex outside the window is not a candidate — and it must not
        # stop the segment beside it from being searched.
        self.assertEqual(
            self._refine(self._payload({"Far": [[[900.0, 0.0, 900.0]]],
                                        "Near": [[[7.0, 0.0, 12.0]]]}, motions=1000),
                         12, (7.0, 0.0, 0.2), floor=10), 12)

    def test_a_segment_before_the_window_is_never_walked(self):
        # The bisect skips a segment the window has already left behind:
        # its motions are all below the window, so it contributes nothing
        # and the live pass beside it is found instead.
        payload = self._payload({"old": [self._row(0.0, 0, 100)],
                                 "live": [self._row(0.0, 500, 100, x0=500.0)]},
                                motions=600)
        self.assertEqual(self._refine(payload, 550, (550.0, 0.0, 0.2), floor=550), 550)

    def test_a_zero_length_edge_is_measured_at_its_own_endpoint(self):
        # A doubled vertex has no direction to project onto: the point IS
        # the geometry, and the projection would divide by zero.
        payload = self._payload(
            {"W": [[[5.0, 0.0, 10.0], [5.0, 0.0, 11.0], [9.0, 0.0, 12.0]]]}, motions=12)
        self.assertEqual(self._refine(payload, 5, (5.0, 0.0, 0.2)), 11)

    def test_the_near_tie_prefers_the_pass_at_or_above_the_floor(self):
        # Repeated toolpaths put the nozzle's own position on an earlier
        # and a later pass as well. Both runs below are 0.5 mm from the
        # nozzle; the near one carries the earlier motions.
        near = self._row(0.5, 11, 10, x0=-5.0)
        far = self._row(-0.5, 41, 10, x0=-5.0)
        low_first = self._payload({"low": [near], "high": [far]}, motions=60)
        high_first = self._payload({"high": [far], "low": [near]}, motions=60)
        # The seam resolves to the pass AT OR ABOVE the floor: an earlier
        # pass clamps to the floor and stalls the fill for seconds.
        self.assertEqual(self._refine(low_first, 10, (0.0, 0.0, 0.2), floor=30), 46)
        self.assertEqual(self._refine(high_first, 10, (0.0, 0.0, 0.2), floor=30), 46)
        # With no floor the smallest motion wins: the nozzle is on the
        # first of the coincident passes.
        self.assertEqual(self._refine(high_first, 10, (0.0, 0.0, 0.2)), 16)
        # Both below the floor (a whole overshot stretch, not one pass):
        # the closest to the floor wins, and the result is left for the
        # caller's overshoot-lock evidence — never clamped here.
        under = self._row(0.5, 40, 10, x0=-5.0)
        over = self._row(-0.5, 90, 10, x0=-5.0)
        self.assertEqual(
            self._refine(self._payload({"low": [under], "high": [over]}, motions=900),
                         10, (0.0, 0.0, 0.2), floor=100), 95)
        self.assertEqual(
            self._refine(self._payload({"high": [over], "low": [under]}, motions=900),
                         10, (0.0, 0.0, 0.2), floor=100), 95)

    def test_the_travel_gate_holds_the_split_while_the_nozzle_travels(self):
        # Nothing prints during a travel. The only extrusion within reach
        # is a FUTURE pass, and painting it jumps the fill ahead and locks
        # the overshoot into the floor — the nearest travel's tell is what
        # makes the honest answer "hold".
        payload = self._payload({"W": [self._row(2.0, 101, 10, x0=-5.0)]},
                                [self._row(0.05, 40, 10, x0=-5.0)], motions=200)
        self.assertIsNone(self._refine(payload, 100, (0.0, 0.0, 0.2), floor=100))

    def test_a_fine_skin_pass_cannot_win_over_the_live_travel(self):
        # The recorded shape transition matched future skin 0.024 mm away
        # while the nozzle was on a travel. A 0.2 mm margin admitted it.
        payload = self._payload({"SKIN": [self._row(0.024, 700, 10, x0=-5.0)]},
                                [self._row(0.00001, 40, 10, x0=-5.0)], motions=1000)
        self.assertIsNone(self._refine(payload, 100, (0.0, 0.0, 0.5), floor=40))
        # Once physically on the skin, progress must be accepted again.
        self.assertIsNotNone(self._refine(payload, 100, (0.0, 0.024, 0.5), floor=40))

    def test_a_travel_further_than_the_match_leaves_the_edge_winning(self):
        # The travel's tell is its proximity: a travel a clear margin away
        # says nothing about the nozzle's own pass. The travel channel
        # also carries a lone vertex, a stretch the window has left
        # behind, a doubled vertex and one that runs past the window's
        # far side — none of them is the tell.
        payload = self._payload(
            {"W": [self._row(0.5, 101, 10, x0=-5.0)]},
            [[[0.0, 0.0, 5.0]],
             self._row(0.6, 0, 20),
             [[-5.0, 0.5, 40.0], [-5.0, 0.5, 41.0], [4.0, 0.5, 42.0]],
             self._row(0.5, 600, 200, x0=-5.0)],
            motions=900)
        self.assertEqual(self._refine(payload, 100, (0.0, 0.0, 0.2), floor=100,
                                      ahead=512), 106)

    def test_a_head_off_the_geometry_returns_nothing(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        # A park, a Z-lift, a probe: no edge is within reach, so the
        # caller holds its floor instead of jumping to the parser.
        self.assertIsNone(self._refine(payload, 10000, (5000.0, 30.0, 0.2)))
        # The tolerance belongs to the caller: a tight one refuses a hit
        # the default would have taken.
        self.assertIsNone(self._refine(payload, 10000, (5000.5, 0.5, 0.2),
                                       max_distance_mm=0.1))

    def test_spatial_rejection_keeps_edges_crossing_the_nozzle(self):
        payload = self._payload({"W": [self._row(1.0, 10, 2, x0=-10, step=20),
                                      self._row(0.0, 20, 2, x0=-100, step=200)]},
                                motions=30)
        self.assertEqual(self._refine(payload, 20, (0.0, 0.0, 0.2)), 20)
        payload["travels"] = [self._row(0.0, 15, 2, x0=-100, step=200)]
        payload["classes"]["W"].pop()
        self.assertIsNone(self._refine(payload, 20, (0.0, 0.0, 0.2)))

    def test_spatial_rejection_preserves_ties_at_the_distance_limit(self):
        # A slightly more distant but earlier pass wins a near tie.
        # Its distance is outside the acceptance limit, so the result
        # must still be refused instead of accepting the other pass.
        payload = self._payload({"first": [self._row(2.995, 200, 2, x0=-1, step=2)],
                                 "earlier": [self._row(3.005, 100, 2, x0=-1, step=2)]},
                                motions=300)
        self.assertIsNone(self._refine(payload, 200, (0.0, 0.0, 0.2), floor=90))
        # Near zero, the shared comparator's absolute tolerance applies.
        payload["classes"]["first"] = [self._row(0.0, 200, 2, x0=-1, step=2)]
        payload["classes"]["earlier"] = [self._row(0.00009, 100, 2, x0=-1, step=2)]
        self.assertEqual(self._refine(payload, 200, (0.0, 0.0, 0.2), floor=90), 100)
