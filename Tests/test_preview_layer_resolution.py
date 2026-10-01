"""Executable preview layer resolution contracts."""
from tests import preview_family_support as harness

class LayerResolverBasicsTests(harness.LayerResolverBasicsTests):
    def test_the_layer_total_prefers_the_index_then_the_object_then_metadata(self):
        resolver = harness.LayerResolver()
        index = harness.FakeIndex(layers=7)
        self.assertEqual(7, resolver.resolve({}, harness.layer_config(), index=index).total)
        self.assertEqual(12, resolver.resolve(harness.z_status(1, 1, total_layer=12), harness.layer_config()).total)
        self.assertEqual(9, resolver.resolve({}, harness.layer_config(), metadata={"layer_count": 9}).total)
        self.assertEqual(4, resolver.resolve({}, harness.layer_config(), heights=(0.2, 0.4, 0.6, 0.8)).total)
        self.assertIsNone(resolver.resolve({}, harness.layer_config()).total)
        # A nonsensical total falls through to the next source, never clamping.
        self.assertEqual(9, resolver.resolve(harness.z_status(1, 1, total_layer=0), harness.layer_config(),
                                             metadata={"layer_count": 9}).total)

    def test_current_layer_is_mapped_then_one_based_adjusted(self):
        resolver = harness.LayerResolver()
        resolver.resolve(harness.z_status(1, 1, current_layer=5, progress=None), harness.layer_config())
        mapped = resolver.resolve(harness.z_status(1, 1, current_layer=5, progress=None), harness.layer_config(),
                                  index=harness.FakeIndex(mapping={5: 2}))
        self.assertEqual((2, "G-code mapped current_layer"), (mapped.index, mapped.source))
        self.assertEqual((4, "Moonraker current_layer"),
                         (harness.LayerResolver().resolve(harness.z_status(1, 1, current_layer=5, progress=None),
                                                  harness.layer_config()).index, "Moonraker current_layer"))
        zero_based = harness.LayerResolver().resolve(harness.z_status(1, 1, current_layer=5, progress=None),
                                             harness.layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual(5, zero_based.index)

    def test_a_pre_print_zero_layer_falls_through_to_the_file_position(self):
        resolver = harness.LayerResolver()
        index = harness.FakeIndex(at={500: 3})
        status = harness.z_status(1, 1, current_layer=0, progress=None)
        status["virtual_sdcard"] = {"file_position": 500}
        self.assertEqual(("Moonraker current_layer",), (resolver.resolve(status, harness.layer_config(moonraker_layer_is_one_based=False)).source,))
        self.assertEqual((3, "G-code file position"),
                         (resolver.resolve(status, harness.layer_config(), index=index).index, "G-code file position"))

    def test_a_non_mapping_status_is_tolerated(self):
        layer = harness.LayerResolver().resolve(harness.z_status(0, 0, objects=False), harness.layer_config())
        self.assertIsNone(layer.index)
        self.assertIsNone(layer.total)

    def test_a_non_finite_or_overflowing_number_reads_as_absent(self):
        resolver = harness.LayerResolver()
        self.assertIsNone(resolver.resolve(harness.z_status(1, 1, total_layer=float("inf")),
                                           harness.layer_config()).total)
        self.assertIsNone(resolver._number("abc"))
        self.assertIsNone(resolver._number(10 ** 400))
        self.assertIsNone(resolver._number(None, int))


class LayerResolverZEstimateTests(harness.LayerResolverZEstimateTests):
    def test_the_first_increment_seeds_a_layer_and_the_baseline_records_no_extrusion(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertIsNone(layers[0].index, "the baseline observation seeds nothing")
        self.assertEqual((1, "extrusion-guarded Z height"), (layers[-1].index, layers[-1].source))
        self.assertEqual(0.2, layers[-1].thickness)

    def test_a_progress_of_zero_blocks_the_estimate_before_the_file_starts(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0)], progress=0.0,
                               metadata={"layer_height": 0.2})
        self.assertIsNone(layers[-1].index)

    def test_a_continuing_rise_re_anchors_the_provisional_guess(self):
        samples = [(0.2, 0.0), (0.4, 0.1), (0.5, 0.1), (0.6, 0.1), (0.7, 0.1), (0.8, 0.1),
                   (0.9, 0.2)]
        _, layers = self.drive(samples, metadata={"layer_height": 0.2,
                                                  "first_layer_height": 0.2})
        self.assertEqual(3, layers[-1].index, "the re-anchored step is the declared layer height")

    def test_a_rise_without_a_declared_step_retires_the_guess(self):
        samples = [(0.2, 0.0), (0.4, 0.1), (0.5, 0.1), (0.6, 0.1), (0.7, 0.1), (0.8, 0.1),
                   (0.9, 0.2)]
        _, layers = self.drive(samples, metadata={})
        self.assertIsNone(layers[-1].index, "an inflated layer number is worse than none")

    def test_a_rise_re_anchors_on_the_measured_median_without_a_header(self):
        # One plateau measures a 0.05 step; the sixth rise then trips the
        # re-anchor, which re-seeds from that measured median.
        samples = [(0.2, 0.0), (0.25, 1.0), (0.25, 1.0)]
        samples += [(0.30, 1.0), (0.35, 1.0), (0.40, 1.0), (0.45, 1.0), (0.50, 1.0)]
        samples += [(0.55, 1.1)]
        _, layers = self.drive(samples, metadata={})
        self.assertEqual(10, layers[-1].index)

    def test_a_poll_too_small_to_seed_still_enters_the_estimate(self):
        # A rise under the 0.02 provisional-seed floor: the estimate's own
        # mid-print seed rule decides instead, so the layer is not lost.
        _, layers = self.drive([(0.2, 0.0), (0.205, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual((0, 0.2), (layers[-1].index, layers[-1].height))

    def test_an_early_tiny_rise_seeds_only_near_the_bed(self):
        # Before ~3% progress a quiet rise is start-gcode creep, but a height
        # still inside the first couple of layers is honestly layer zero.
        _, layers = self.drive([(0.2, 0.0), (0.205, 1.0)], progress=0.01,
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(0, layers[-1].index)

    def test_a_z_hop_descent_cancels_the_provisional_seed(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0), (0.2, 1.0)],
                               metadata={"layer_height": 0.2})
        self.assertIsNone(layers[-1].index, "the hop was not a layer change")

    def test_a_descent_that_keeps_ascent_does_not_read_as_a_hop(self):
        _, layers = self.drive([(0.0, 0.0), (1.0, 1.0), (1.05, 1.0), (0.5, 1.0), (0.1, 1.0)],
                               metadata={"layer_height": 0.2})
        self.assertIsNotNone(layers[-1].index, "the ascent never cancelled out")

    def test_plateaus_record_measured_layer_deltas_and_bound_their_history(self):
        resolver = harness.LayerResolver()
        config = harness.layer_config()
        resolver.resolve(harness.z_status(0.0, 0.0), config, metadata={})
        resolver.resolve(harness.z_status(0.2, 1.0), config, metadata={})
        for step in range(30):
            resolver.resolve(harness.z_status(0.2 + step * 0.2, 1.0), config, metadata={})
            resolver.resolve(harness.z_status(0.2 + step * 0.2 + 0.002, 1.0), config, metadata={})
        self.assertTrue(resolver._z_deltas)
        self.assertLessEqual(len(resolver._z_deltas), 24, "the delta history is bounded")

    def test_late_progress_seeds_the_estimate_from_the_current_height(self):
        _, layers = self.drive([(0.2, 0.0), (5.0, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(24, layers[-1].index)

    def test_an_early_wipe_height_is_not_taken_for_a_layer(self):
        _, layers = self.drive([(0.2, 0.0), (10.0, 1.0)], progress=0.01,
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertIsNone(layers[-1].index)

    def test_the_extrapolation_advances_one_layer_and_never_jumps(self):
        _, layers = self.drive([(0.4, 1.0), (0.6, 2.0), (20.0, 3.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(2, layers[1].index)
        self.assertEqual(3, layers[-1].index, "a lift must not jump the physical layer")

    def test_a_lower_candidate_corrects_only_after_three_observations(self):
        samples = [(0.4, 1.0), (0.6, 2.0), (0.6, 3.0)]
        samples += [(2.0, 4.0), (2.0, 5.0), (2.0, 6.0), (2.0, 7.0), (2.0, 8.0), (2.0, 9.0),
                    (2.0, 10.0)]
        _, layers = self.drive(samples, metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(9, layers[-1].index, "the estimate climbed one layer per poll")
        _, corrected = self.drive(samples + [(1.0, 11.0), (1.0, 12.0), (1.0, 13.0)],
                                  metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(9, corrected[-3].index, "one correction must not move the layer")
        self.assertEqual(4, corrected[-1].index, "three consecutive corrections settle it")

    def test_an_object_height_bounds_the_estimate(self):
        _, layers = self.drive([(0.2, 0.0), (9.0, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2,
                                         "object_height": 1.0})
        self.assertEqual(5, layers[-1].index)

    def test_a_measured_median_step_replaces_an_absent_header(self):
        resolver = harness.LayerResolver()
        config = harness.layer_config()
        resolver.resolve(harness.z_status(0.0, 0.0), config, metadata={})
        # Two plateaus measure a 0.2 step; the next resolve consults the median.
        for z in (0.2, 0.4):
            resolver.resolve(harness.z_status(z, 1.0), config, metadata={})
            resolver.resolve(harness.z_status(z + 0.002, 1.0), config, metadata={})
        layer = resolver.resolve(harness.z_status(1.0, 2.0), config, metadata={})
        self.assertIsNotNone(layer.index)


class LayerResolverGeometryTests(harness.LayerResolverGeometryTests):
    def test_heights_pin_the_layer_at_an_exact_match(self):
        layer = self.resolve_at(0.6, heights=self.HEIGHTS)
        self.assertEqual(2, layer.index)
        self.assertEqual(0.6, layer.height)
        self.assertAlmostEqual(0.2, layer.thickness)

    def test_a_mid_layer_position_maps_to_the_last_start_below_it(self):
        layer = self.resolve_at(0.55, heights=self.HEIGHTS)
        self.assertEqual(1, layer.index)
        self.assertEqual(0.4, layer.height)

    def test_a_position_past_the_last_start_reads_as_the_top_layer(self):
        layer = self.resolve_at(1.01, heights=self.HEIGHTS)
        self.assertEqual(4, layer.index)
        self.assertEqual(1.0, layer.height)
        self.assertAlmostEqual(0.2, layer.thickness)

    def test_geometry_from_another_scene_does_not_clamp_the_observation(self):
        layer = self.resolve_at(5.0, heights=(0.1, 0.11, 0.12))
        self.assertIsNotNone(layer.index, "the heights were not this file's")

    def test_the_layer_zero_thickness_uses_the_first_layer_height(self):
        layer = self.resolve_at(0.3, heights=(),
                                metadata={"layer_height": 0.2, "first_layer_height": 0.3})
        self.assertEqual(0, layer.index)
        self.assertEqual(0.3, layer.height)
        self.assertEqual(0.3, layer.thickness)

    def test_a_derived_height_uses_the_resolved_step_without_geometry(self):
        layer = self.resolve_at(0.6, heights=())
        self.assertAlmostEqual(0.6, layer.height)
        self.assertEqual(0.2, layer.thickness)


class LayerResolverStartTests(harness.LayerResolverStartTests):
    def test_an_active_print_at_layer_zero_reports_the_start(self):
        status = harness.z_status(0.2, 0.0, current_layer=0, progress=0.01)
        layer = harness.LayerResolver().resolve(status, harness.layer_config())
        self.assertEqual((0, "print start"), (layer.index, layer.source))

    def test_past_three_percent_the_same_signal_is_not_a_stuck_layer_one(self):
        status = harness.z_status(0.2, 0.0, current_layer=0, progress=0.5)
        self.assertIsNone(harness.LayerResolver().resolve(status, harness.layer_config()).index)

    def test_a_standby_printer_at_layer_zero_gets_no_layer(self):
        status = harness.z_status(0.2, 0.0, current_layer=0, progress=0.01, state="standby")
        self.assertIsNone(harness.LayerResolver().resolve(status, harness.layer_config()).index)

    def test_a_negative_layer_is_floored(self):
        status = harness.z_status(0.2, 0.0, current_layer=-3, progress=None)
        layer = harness.LayerResolver().resolve(status, harness.layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual(0, layer.index)

    def test_a_zero_based_scene_reads_layer_zero_as_reported(self):
        status = harness.z_status(0.2, 0.0, current_layer=0, progress=0.01)
        layer = harness.LayerResolver().resolve(status, harness.layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual((0, "Moonraker current_layer"), (layer.index, layer.source))

    def test_reset_clears_the_z_estimate(self):
        resolver = harness.LayerResolver()
        config = harness.layer_config()
        resolver.resolve(harness.z_status(0.2, 1.0, progress=0.5), config, metadata={"layer_height": 0.2})
        resolver.reset()
        self.assertIsNone(resolver._prev_z)
        self.assertEqual([], resolver._z_deltas)


class PrintSnapshotTests(harness.PrintSnapshotTests):
    def test_active_covers_printing_and_paused_only(self):
        for state, expected in (("printing", True), ("paused", True), ("standby", False),
                                ("cancelled", False)):
            snap = harness.PrintSnapshot(observation=harness.SimpleNamespace(state=state))
            self.assertEqual(expected, snap.active, state)
        self.assertFalse(harness.PrintSnapshot().active)

    def test_a_snapshot_is_frozen_with_defaults(self):
        snap = harness.replace(harness.PrintSnapshot(), layer_eta=12.0, index_ready=True)
        self.assertEqual(harness.PhysicalLayer(), snap.layer)
        self.assertEqual((12.0, True), (snap.layer_eta, snap.index_ready))


