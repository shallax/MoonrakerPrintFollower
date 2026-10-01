"""Executable index features contracts."""
from mpf.gcode import IndexCodec as index_codec, IndexCache as index_cache
from tests import index_plate_support as harness

class FeatureTypeTests(harness.FeatureTypeTests):
    def test_bead_width_profile_is_per_motion_and_survives_compact_cache_and_preparation(self):
        from math import pi
        from mpf.gcode.PlateProgress import prepare_layer, encode_layer, decode_layer
        data = (b'; filament_diameter = 2\nM83\n;LAYER:0\nG0 Z0.2\n;TYPE:WALL-OUTER\n'
                b'G1 X10 E0.2\nG1 X20 E0.4\nG1 E-1\n;LAYER:1\n'
                b'G0 Z0.5\nG0 X30\nG1 E1\nG1 X40 E0.3\n')
        with harness.tempfile.TemporaryDirectory() as directory:
            path = harness.os.path.join(directory, "widths.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            full = harness.build_index_from_file(path, compact=False)
            compact = harness.build_index_from_file(path, compact=True)
            self.assertAlmostEqual(full.filament_diameter, 2)
            self.assertAlmostEqual(full.layer_heights[0], .2)
            self.assertAlmostEqual(full.layer_heights[1], .3)
            identity = harness.RemoteFileIdentity("widths.gcode", len(data), 1, "widths")
            store = harness.PersistentIndexCache(directory)
            store.save(identity, compact)
            compact = store.load(identity)
            self.assertIsNotNone(compact)
            for layer in (1, 0):
                self.assertTrue(harness.hydrate_layer_from_file(compact, path, layer))
                a = prepare_layer(full, layer)
                b = prepare_layer(compact, layer)
                self.assertEqual(a["widths"], b["widths"])
                loaded = decode_layer(encode_layer(a), immutable=True)
                self.assertEqual(encode_layer(a), encode_layer(loaded))
            widths = prepare_layer(full, 0)["widths"]
            self.assertEqual(widths[0], 0)
            self.assertAlmostEqual(widths[1], pi * .2 / (10 * .2), places=6)
            self.assertAlmostEqual(widths[2], pi * .4 / (10 * .2), places=6)
            self.assertEqual(widths[3], 0)
            store.save(identity, full)
            restored = store.load(identity)
            self.assertEqual(restored.motion_extrusion, full.motion_extrusion)
            self.assertEqual(restored.layer_heights, full.layer_heights)

    def test_arc_width_uses_the_path_length_not_the_endpoint_chord(self):
        from math import pi
        from mpf.gcode.PlateProgress import prepare_layer
        data = b'; filament_diameter = 2\nM83\nG0 X10 Y0 Z0.2\n;LAYER:0\nG3 X-10 Y0 I-10 J0 E1\n'
        index = harness.build_index_from_bytes(data)
        width = prepare_layer(index, 0)["widths"][0]
        self.assertAlmostEqual(width, pi / (pi * 10 * .2), delta=.001)

    def test_extruder_events_distinguish_retractions_unretractions_and_resets(self):
        from mpf.gcode.PlateProgress import prepare_layer, encode_layer, decode_layer
        data = (b"M83\n;LAYER:0\nG1 X10 E1\nG1 E-1\nG0 X20\nG92 E0\n"
                b"G1 E1\nG1 X30 E1\nG10\nG0 X40\nG11\n;TIME_ELAPSED:2\n")
        index = harness.build_index_from_bytes(data)
        self.assertEqual(index.extruder_events[0], [(1, True), (3, False), (5, True), (6, False)])
        payload = prepare_layer(index, 0)
        self.assertEqual(payload["retractions"], [(10, 0, 1), (30, 0, 5)])
        self.assertEqual(payload["unretractions"], [(20, 0, 3), (40, 0, 6)])
        decoded = decode_layer(encode_layer(payload), immutable=True)
        self.assertEqual(encode_layer(decoded), encode_layer(payload))
        self.assertEqual(tuple(payload["retractions"]), decoded["retractions"])

    def test_retraction_state_survives_compact_hydration_and_cache_restore(self):
        data = b"M83\n;LAYER:0\nG1 X10 E1\nG1 E-1\n;LAYER:1\nG0 X20\nG1 E1\nG1 X30 E1\n"
        with harness.tempfile.TemporaryDirectory() as directory:
            path = harness.os.path.join(directory, "events.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            index = harness.build_index_from_file(path, compact=True)
            self.assertEqual(index.layer_start_retracted, [False, True])
            self.assertTrue(harness.hydrate_layer_from_file(index, path, 1))
            self.assertEqual(index.extruder_events[1], [(1, False)])
            identity = harness.RemoteFileIdentity("events.gcode", len(data), 1, "events")
            store = harness.PersistentIndexCache(directory)
            store.save(identity, index)
            restored = store.load(identity)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.extruder_events, index.extruder_events)
            self.assertEqual(restored.layer_start_retracted, [False, True])

    def test_a_marker_types_every_motion_that_follows_it(self):
        index = harness.build_index_from_bytes(
            b";LAYER:0\n"
            b";TYPE:WALL-OUTER\n"
            b"G1 X1 Y1 E0.5\n"
            b"G1 X2 Y2 E0.6\n"
            b";TYPE:SKIN\n"
            b"G1 X3 Y3 E0.7\n")
        names = index.type_names
        self.assertEqual(names, ["WALL-OUTER", "SKIN"])
        codes = harness._codes(names)
        # A slicer writes a marker when the feature CHANGES, so a motion
        # with no marker of its own keeps the last one seen (the run).
        self.assertEqual(index.motion_types[0],
                         [[2, codes["WALL-OUTER"]], [1, codes["SKIN"]]])
        self.assertEqual(sum(run[0] for run in index.motion_types[0]), 3)

    def test_a_marker_tolerates_indentation_and_keeps_a_multi_word_name(self):
        index = harness.build_index_from_bytes(b";LAYER:0\n  \t;TYPE:Internal perimeter\nG1 X1 E1\n")
        self.assertEqual(index.type_names, ["Internal perimeter"])
        self.assertEqual(index.motion_types[0], [[1, 2]])

    def test_a_valueless_or_absent_marker_is_not_a_marker(self):
        # ";TYPE:" with nothing after it (and no marker at all) leaves the
        # motion untyped: the code word alone is not a feature name.
        index = harness.build_index_from_bytes(b";LAYER:0\n;TYPE:\nG1 X1 E1\nG0 X2\n")
        self.assertEqual(index.type_names, [])
        self.assertEqual(index.motion_types[0], [[2, harness._TYPE_NONE]])

    def test_a_layer_closed_by_its_elapsed_marker_keeps_its_features(self):
        # ;TIME_ELAPSED closes a block as surely as the next ;LAYER does
        # (PauseAtHeight emits its pause after it), so the feature arrays
        # must be handed over there too — the last layer of a real file is
        # exactly this shape.
        index = harness.build_index_from_bytes(
            b";LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\nG1 E0.0\n;TIME_ELAPSED:7\n")
        self.assertEqual(index.motion_types, [[[2, 2]]])
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.layer_elapsed_times, [7.0])

    def test_the_vocabulary_is_capped_and_its_overflow_reads_unknown(self):
        names = ["T%d" % value for value in range(harness._MAX_TYPE_NAMES + 6)]
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % name.encode("ascii") for name in names)
        index = harness.build_index_from_bytes(data)
        self.assertEqual(len(index.type_names), harness._MAX_TYPE_NAMES)
        # The vocabulary cap is a bound on the index, not an error: past it
        # every name shares _TYPE_OTHER, so a hostile file cannot grow the
        # table with the line count (and the shared code merges their runs).
        self.assertEqual(index.motion_types[0][-1], [len(names) - harness._MAX_TYPE_NAMES, harness._TYPE_OTHER])
        self.assertEqual(sum(run[0] for run in index.motion_types[0]), len(names))

    def test_a_hydrated_layer_names_an_off_vocabulary_type_as_unknown(self):
        # The compact hydrator rebuilds the runs from the file's bytes; a
        # name the SCAN never registered (it fell past the vocabulary cap)
        # has no code of its own, so the layer's tail reads unknown rather
        # than being named by a code that belongs to another feature.
        names = ["T%d" % value for value in range(harness._MAX_TYPE_NAMES + 1)]
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % name.encode("ascii") for name in names)
        path = harness._write_gcode(data)
        self.addCleanup(harness.os.remove, path)
        full = harness.build_index_from_file(path, compact=False)
        compact = harness.build_index_from_file(path, compact=True)
        self.assertTrue(harness.hydrate_layer_from_file(compact, path, 0))
        self.assertEqual(compact.motion_types, full.motion_types)
        self.assertEqual(compact.motion_types[0][-1], [1, harness._TYPE_OTHER])

    def test_the_run_list_is_capped_and_its_tail_reads_unknown(self):
        # Every marker followed by one motion: past the run cap the layer's
        # tail collapses into the last run instead of growing a list per
        # motion. The runs must still total the motion count, or save()
        # would refuse the index as ragged.
        count = harness._MAX_TYPE_RUNS_PER_LAYER + 500
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % (b"A" if value % 2 else b"B")
            for value in range(count))
        index = harness.build_index_from_bytes(data)
        runs = index.motion_types[0]
        self.assertEqual(len(runs), harness._MAX_TYPE_RUNS_PER_LAYER)
        self.assertEqual(sum(run[0] for run in runs), count)
        self.assertEqual(runs[-1][1], harness._TYPE_OTHER)


class MotionLineFastPathTests(harness.MotionLineFastPathTests):
    def test_the_dominant_shape_takes_the_fast_path(self):
        # The structural perf guard: if the pre-check ever stops
        # claiming the dominant shape, the hydrate silently falls back
        # to the regex front and the seek win regresses.
        command, axes = harness.gcode_index._fast_motion_line(b"G1 X5.5 Y10 E0.2")
        self.assertEqual(command, b"G1")
        self.assertEqual(axes, {"X": 5.5, "Y": 10.0, "E": 0.2})
        for line in (b"G0 X5 Y0", b"G2 X5 Y0 I2 J0", b"G3 X5 Y0 I2 J0 R5"):
            self.assertIsNotNone(harness.gcode_index._fast_motion_line(line),
                                 "%r did not take the fast path" % line)

    def test_every_claim_agrees_with_the_regex_front(self):
        for line in self.BATTERY:
            code = line.split(b";", 1)[0]
            fast = harness.gcode_index._fast_motion_line(code)
            if fast is None:
                continue  # the fallback is always legal
            match = harness.gcode_index._COMMAND.search(code)
            command = match.group(1).upper() if match else b""
            axes = harness.gcode_index._parse_axes(code)
            self.assertEqual(fast, (command, axes), "%r" % line)
            self.assertIsNotNone(harness.gcode_index._MOTION.search(line),
                                 "%r claimed a motion the motion regex refuses" % line)

    def test_a_hydrated_layer_matches_the_full_scan_through_the_fast_path(self):
        # A layer mixing dominant fast-path lines and fallback shapes
        # hydrates into the same polylines the full scan derives.
        from tests.test_plate_progress import layer_polylines
        data = (b"M82\nG90\n;LAYER:0\n;TYPE:WALL-OUTER\n"
                + b"G2 X8 Y8 I1 J0\n"     # an arc FIRST: any code-part leak
                + b"".join(b"G1 X%.3f Y%.3f E%.4f\n" % (float(i), float(i % 50),
                                                        0.05 + i * 0.001)
                           for i in range(40))
                + b"G1 X 5 Y 10 E0.2\n"   # the spaced axis — the regex path
                + b"g1 x5 y10 e0.2\n"     # lowercase — the regex path
                + b"G1 X5. Y10\n"         # the dot-trailing number
                + b"G1 E-0.3\n")          # a retraction
        path = harness._write_gcode(data)
        self.addCleanup(harness.os.remove, path)
        full = harness.build_index_from_file(path, compact=False)
        compact = harness.build_index_from_file(path, compact=True)
        self.assertTrue(harness.hydrate_layer_from_file(compact, path, 0, keep_anchor=0))
        self.assertEqual(compact.motion_count(0), full.motion_count(0))
        self.assertEqual(layer_polylines(compact, 0), layer_polylines(full, 0),
                         "the fast-path hydrate diverged from the full scan")


class TravelBoundaryTests(harness.TravelBoundaryTests):
    def test_a_retraction_starts_the_travel_and_the_prime_ends_it(self):
        index = harness.build_index_from_bytes(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 Y1 E1.0\n"
            b"G1 X2 Y2 E1.1\n"
            b"G1 E0.1\n"
            b"G0 X9 Y9\n"
            b"G1 E1.1\n"
            b"G1 X3 Y3 E1.2\n")
        # E stops rising at the retraction (falling is the same rule's
        # decreasing case) and resumes at the prime.
        self.assertEqual(index.travel_starts, [[2]])
        self.assertEqual(index.travel_ends, [[4]])
        self.assertEqual(index.layer_start_extruding, [True])

    def test_flat_e_classifies_travel_without_retractions(self):
        index = harness.build_index_from_bytes(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 Y1 E1.0\n"
            b"G0 X9 Y9\n"
            b"G0 X8 Y8\n"
            b"G1 X3 Y3 E1.1\n")
        # Retraction disabled: E merely holds across the travel, which is
        # the same "not extruding" state the retraction reaches.
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[3]])

    def test_any_positive_e_step_extrudes(self):
        # The rule is the G-code's own: a positive step extrudes, no
        # matter how small. The live file's fine walls step E by
        # ~0.014 mm per move, and a 0.05 mm layer height's short
        # segments step it by ~1e-4 — both were misread as travel by
        # the old magnitude floors (the live reports).
        index = harness.build_index_from_bytes(
            b"M82\n;LAYER:0\nG1 X1 E1.0\n"
            b"G1 X2 E1.00008\n"
            b"G1 X3 E1.00016\n"
            b"G1 X4 E1.01402\n"
            b"G0 X5\n"
            b"G1 X6 E1.09\n")
        # One travel: the E-less move, and only that one.
        self.assertEqual(index.travel_starts, [[4]])
        self.assertEqual(index.travel_ends, [[5]])

    def test_flat_and_falling_e_do_not_extrude(self):
        # No E at all, an E that merely holds, and a retraction all
        # read as travel: the sign, not the magnitude, decides.
        index = harness.build_index_from_bytes(
            b"M82\n;LAYER:0\nG1 X1 E1.0\n"
            b"G1 X2\n"
            b"G1 X3 E1.0\n"
            b"G1 X4 E0.25\n"
            b"G1 X5 E1.2\n")
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[4]])

    def test_relative_e_and_a_g92_rebase_keep_the_rule(self):
        index = harness.build_index_from_bytes(
            b"M83\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 E1.0\n"
            b"G1 E-1.0\n"
            b"G0 X9\n"
            b"G1 E1.0\n"
            b"G92 E0\n"
            b"G1 X2 E0.2\n")
        # A G92 re-bases the axis without moving it: Cura's per-layer G92
        # E0 is not a retraction, and the rise that follows it is not a
        # travel end (no travel was open).
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[3]])
        self.assertEqual(index.layer_start_e_absolute, [False])

    def test_a_travel_that_crosses_a_layer_boundary_leaves_its_two_halves(self):
        path = harness._write_gcode(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 E1.0\n"
            b"G1 E0.0\n"
            b";LAYER:1\n"
            b"G1 X5 E1.0\n"
            b"G1 X6 E1.2\n")
        self.addCleanup(harness.os.remove, path)
        full = harness.build_index_from_file(path, compact=False)
        self.assertEqual(full.travel_starts, [[1], []])
        self.assertEqual(full.travel_ends, [[], [0]])
        # The next layer opens mid-travel, so it must be told: the seed is
        # the pair the payload reads to know the glyph it drew in the
        # previous layer is the one this layer closes.
        self.assertEqual(full.layer_start_extruding, [True, False])
        self.assertEqual(full.layer_start_types, [harness._TYPE_NONE, harness._codes(full.type_names)["WALL"]])

    def test_hydration_reproduces_the_full_scans_features(self):
        data = (b"M82\n;LAYER:0\n;TYPE:WALL-OUTER\n"
                b"G1 X1 Y1 E1.0\nG1 X2 Y2 E1.1\nG1 E0.1\nG0 X9 Y9\nG1 E1.1\n"
                b";LAYER:1\n;TYPE:SKIN\n"
                b"G1 X5 Y5 E1.2\nG0 X1 Y1\nG1 X6 Y6 E1.3\n"
                b";LAYER:2\nG1 X7 Y7 E1.4\n")
        path = harness._write_gcode(data)
        self.addCleanup(harness.os.remove, path)
        full = harness.build_index_from_file(path, compact=False)
        # The compact hydrator runs its own parse loop over one layer's
        # bytes with no history; it must agree with the scan it stands in
        # for or the preview changes colour with the hydration state.
        for layer in range(len(full.ranges)):
            compact = harness.build_index_from_file(path, compact=True)
            self.assertTrue(harness.hydrate_layer_from_file(compact, path, layer, keep_anchor=layer))
            self.assertEqual(compact.motion_types[layer], full.motion_types[layer])
            self.assertEqual(compact.travel_starts[layer], full.travel_starts[layer])
            self.assertEqual(compact.travel_ends[layer], full.travel_ends[layer])
            self.assertEqual(compact.type_names, full.type_names)
            self.assertEqual(compact.motion_x[layer], full.motion_x[layer])


class FeatureCacheTests(harness.FeatureCacheTests):
    def test_the_feature_columns_survive_the_round_trip(self):
        index = self._featured_index()
        identity = self._identity()
        self.cache.save(identity, index)
        restored = self.cache.load(identity)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.motion_types, index.motion_types)
        self.assertEqual(restored.travel_starts, index.travel_starts)
        self.assertEqual(restored.travel_ends, index.travel_ends)
        self.assertEqual(restored.type_names, index.type_names)
        self.assertEqual(restored.layer_start_types, index.layer_start_types)
        self.assertEqual(restored.layer_start_e, index.layer_start_e)
        self.assertEqual(restored.layer_start_e_absolute, index.layer_start_e_absolute)
        self.assertEqual(restored.layer_start_extruding, index.layer_start_extruding)
        self.assertEqual(restored.hydrated_layers, index.hydrated_layers)

    def test_a_compact_index_round_trips_its_hydrated_layers_features(self):
        data = (b"M82\n;LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\n;LAYER:1\n;TYPE:SKIN\nG1 X2 E1.1\n")
        path = harness._write_gcode(data)
        self.addCleanup(harness.os.remove, path)
        index = harness.build_index_from_file(path, compact=True)
        harness.hydrate_layer_from_file(index, path, 1)
        identity = self._identity()
        self.cache.save(identity, index)
        restored = self.cache.load(identity)
        self.assertEqual(restored.hydrated_layers, {1})
        # The unhydrated layer keeps empty columns; the hydrated one keeps
        # the colours it was hydrated for, with its seed intact.
        self.assertEqual(restored.motion_types, [[], index.motion_types[1]])
        self.assertEqual(restored.type_names, index.type_names)
        self.assertEqual(restored.layer_start_types, index.layer_start_types)

    def test_a_blob_from_the_previous_version_is_refused(self):
        identity = self._identity()
        # A reader that accepted the older format would restore it with
        # empty feature columns and draw a colourless, travel-less plate.
        self._write_raw(identity, self._header(identity, version=harness._CACHE_VERSION - 1))
        self.assertIsNone(self.cache.load(identity))

    def test_a_header_without_the_feature_columns_still_loads(self):
        identity = self._identity()
        self._write_raw(identity, self._header(identity))
        restored = self.cache.load(identity)
        self.assertIsNotNone(restored)
        # The motion is restored and reads untyped: the columns must still
        # come back one entry per layer, or the hydrator could not fill the
        # layer it is asked for.
        self.assertEqual(restored.motion_types, [[[1, harness._TYPE_NONE]]])
        self.assertEqual(restored.travel_starts, [[]])
        self.assertEqual(restored.travel_ends, [[]])
        self.assertEqual(restored.type_names, [])
        self.assertEqual(restored.layer_start_types, [harness._TYPE_NONE])
        self.assertEqual(restored.layer_start_e, [0.0])
        self.assertEqual(restored.layer_start_e_absolute, [True])
        self.assertEqual(restored.layer_start_extruding, [True])

    def test_a_ragged_feature_column_is_never_written(self):
        index = self._featured_index()
        identity = self._identity()
        for label, column in (
            ("runs short of the motions", [[[1, 2]]]),
            ("runs past the motions", [[[3, 2]]]),
            ("a code off the vocabulary", [[[2, 9]]]),
            ("a zero-length run", [[[0, 2]]]),
            ("a marker outside the layer", [[[2, 2]]]),
            ("a shorter column", []),
        ):
            with self.subTest(label):
                index.motion_types = [[[2, 2]]]
                index.travel_starts = [[1]]
                if label == "a marker outside the layer":
                    index.travel_starts = [[4]]
                elif label == "a shorter column":
                    index.travel_starts = []
                else:
                    index.motion_types = column
                self.cache.save(identity, index)
                # The per-print subdirectory may exist (the path's makedirs);
        # no blob file may have been written.
        written = [name for _root, _dirs, names in harness.os.walk(self.directory)
                   for name in names]
        self.assertEqual(written, [])

    def test_a_ragged_feature_column_in_a_blob_is_refused(self):
        identity = self._identity()
        for label, overrides in (
            ("runs short of the motions", {"counts": [2], "type_runs": [[[1, 0]]]}),
            ("runs that are not pairs", {"type_runs": [[[1]]]}),
            ("a run that is not a list", {"type_runs": [[1]]}),
            ("a marker outside the layer", {"travel_starts": [[1]]}),
            ("markers out of order", {"counts": [2], "travel_ends": [[1, 0]]}),
            ("a duplicated marker", {"counts": [2], "travel_ends": [[1, 1]]}),
            ("a code off the vocabulary", {"type_runs": [[[1, 7]]]}),
            ("a seed type off the vocabulary", {"start_types": [7]}),
            ("an unusable seed E", {"start_e": [None]}),
            ("a seed flag that is not a flag", {"start_extruding": [1]}),
            ("a vocabulary that is not a list", {"type_names": "junk"}),
            ("a vocabulary entry that is not a name", {"type_names": [7]}),
            ("a vocabulary past the cap", {"type_names": ["T%d" % v for v in range(harness._MAX_TYPE_NAMES + 1)]}),
            ("columns that are not columns", {"type_runs": 0}),
        ):
            with self.subTest(label):
                self._write_raw(identity, self._header(identity, **overrides))
                self.assertIsNone(self.cache.load(identity))

    def test_the_feature_columns_are_dropped_rather_than_refused_past_the_budget(self):
        index = self._featured_index()
        identity = self._identity()
        with harness.patch.object(index_codec, "_MAX_CACHE_FEATURE_ENTRIES", 0):
            self.cache.save(identity, index)
            restored = self.cache.load(identity)
        # The geometry is what the cache is for: a fragmented file keeps
        # its index and loses only the colours it could not afford.
        self.assertEqual(restored.motion_types, [[[4, harness._TYPE_NONE]]])
        self.assertEqual(restored.travel_starts, [[]])
        self.assertEqual(list(restored.motion_offsets[0]), list(index.motion_offsets[0]))
        self._write_raw(identity, self._header(identity, type_runs=[[[1, 0]]]))
        with harness.patch.object(index_codec, "_MAX_CACHE_FEATURE_ENTRIES", 0):
            # Budget measured across the whole blob: a layer's runs that
            # arrived over it are dropped here too, never half-restored.
            self.assertEqual(self.cache.load(identity).motion_types, [[[1, harness._TYPE_NONE]]])

    def test_an_over_long_header_is_not_written_at_all(self):
        identity = self._identity()
        with harness.patch.object(index_cache, "_MAX_CACHE_HEADER_BYTES", 8):
            # The reader would refuse this blob, so publishing it would
            # only spend the cache's byte budget on a dead file.
            self.cache.save(identity, self._featured_index())
        # The per-print subdirectory may exist (the path's makedirs);
        # no blob file may have been written.
        written = [name for _root, _dirs, names in harness.os.walk(self.directory)
                   for name in names]
        self.assertEqual(written, [])


class FeatureRetentionTests(harness.FeatureRetentionTests):
    def test_the_padding_loop_covers_the_feature_columns(self):
        path = harness._write_gcode(self.DATA)
        self.addCleanup(harness.os.remove, path)
        index = harness.build_index_from_file(path, compact=True)
        # A compact index carries a column per layer from the start; drop
        # them so the hydration's grow loop is the only thing filling
        # them, which is what a restored blob of an older shape needs.
        index.motion_offsets, index.motion_x, index.motion_y, index.motion_z = [], [], [], []
        index.motion_types, index.travel_starts, index.travel_ends = [], [], []
        index.layer_start_types, index.layer_start_e = [], []
        index.layer_start_e_absolute, index.layer_start_extruding = [], []
        self.assertTrue(harness.hydrate_layer_from_file(index, path, 1, keep_anchor=1))
        self.assertEqual(len(index.motion_types), len(index.ranges))
        self.assertEqual(index.motion_types[0], [])
        self.assertEqual(index.travel_starts[2], [])
        self.assertEqual(index.layer_start_types, [harness._TYPE_NONE] * len(index.ranges))
        self.assertEqual(index.layer_start_extruding, [True] * len(index.ranges))
        self.assertEqual(index.motion_count(1), 2)

    def test_the_retention_window_evicts_the_feature_columns(self):
        path = harness._write_gcode(self.DATA)
        self.addCleanup(harness.os.remove, path)
        index = harness.build_index_from_file(path, compact=True)
        for layer in range(4):
            self.assertTrue(harness.hydrate_layer_from_file(index, path, layer, keep_anchor=3))
        # The window is [anchor-1, anchor+1]: layers 0 and 1 are behind it,
        # and an evicted layer must not keep runs for a motion list it no
        # longer has.
        self.assertEqual(index.hydrated_layers, {2, 3})
        self.assertEqual(index.motion_types[0], [])
        self.assertEqual(index.travel_starts[0], [])
        self.assertEqual(index.travel_ends[0], [])
        # The arrays went, but the count it recorded persists.
        self.assertEqual(index.motion_count(0), 2)
        self.assertEqual(index.motion_types[3], [[1, 3]])
        self.assertEqual(index.travel_starts[2], [1])

    def test_the_frozen_layer_holds_its_own_window_beside_the_live_print(self):
        data = b"M82\n" + b"".join(b";LAYER:%d\nG1 X%d E1.0\n" % (n, n + 1) for n in range(6))
        path = harness._write_gcode(data)
        self.addCleanup(harness.os.remove, path)
        index = harness.build_index_from_file(path, compact=True)
        # The live print stands at layer 0; the follower is frozen on 5
        # (the pop-over's detach). Every hydration here evicts against
        # the live window alone — the frozen layer and its own
        # neighbours survive on the second anchor.
        index.manual_anchor = 5
        for layer in (5, 4, 3, 2):
            self.assertTrue(harness.hydrate_layer_from_file(index, path, layer, keep_anchor=0))
        # The frozen layer and the neighbour below it hold, and each
        # freshly hydrated layer keeps its own ±1 window until the
        # NEXT hydrate evicts it (the background pass's guarantee:
        # the arrays stay valid through the prepare and encode). The
        # walk's newest layer is always the survivor — memory stays
        # bounded at the three windows.
        self.assertEqual(index.hydrated_layers, {2, 3, 4, 5})
        self.assertEqual(index.motion_count(5), 1)
        # The control: with no frozen anchor the same walk keeps the
        # LAST freshly hydrated layer's window alone — still bounded,
        # and the pass's prepare window is always the most recent.
        control = harness.build_index_from_file(path, compact=True)
        for layer in (5, 4, 3, 2):
            self.assertTrue(harness.hydrate_layer_from_file(control, path, layer, keep_anchor=0))
        self.assertEqual(control.hydrated_layers, {2, 3})


