import os
import pathlib
import random
import re
import tempfile
import threading
import unittest

from plugins.MoonrakerProtocol import RemoteFileIdentity
from plugins.GCodeIndex import (
    PersistentIndexCache,
    build_index_from_bytes,
    build_index_from_file,
    hydrate_layer_from_file,
)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "gcode"

_LAYER = re.compile(rb"^\s*;LAYER:\s*-?\d+\s*$", re.I)
_MOVE = re.compile(rb"^\s*(?:N\d+\s+)?G(?:0|1|2|3)(?:\s|$)", re.I)
_ELAPSED = re.compile(rb"^\s*;TIME_ELAPSED:", re.I)


def legacy_reference(data: bytes):
    """Small reference for the established layer/motion byte-offset semantics."""
    ranges = []
    motions = []
    current_start = None
    current_end = None
    current_moves = None
    offset = 0
    for line in data.splitlines(keepends=True):
        stripped = line.rstrip(b"\r\n")
        if _LAYER.search(stripped):
            if current_start is not None:
                end = current_end if current_end is not None else offset
                ranges.append((current_start, max(current_start + 1, end)))
                motions.append(current_moves)
            current_start = offset
            current_end = None
            current_moves = []
        elif current_start is not None and current_end is None and _ELAPSED.search(stripped):
            current_end = offset
        if current_start is not None and current_end is None and _MOVE.search(stripped):
            current_moves.append(offset)
        offset += len(line)
    if current_start is not None:
        end = current_end if current_end is not None else len(data)
        ranges.append((current_start, max(current_start + 1, end)))
        motions.append(current_moves)
    return ranges, motions


class IndexTests(unittest.TestCase):
    def test_the_marker_winner_is_decided_once_per_file(self):
        # The critic's catch: an earlier-ordered marker must win the
        # WHOLE file even when a later-ordered marker's line appears
        # first — the old per-line take-over mis-attributed the
        # prefix lines to the later marker's block and shifted every
        # layer (and the baked pauses) by one.
        priming = b"; a long klipper start macro\n" * 4000
        data = (b";FLAVOR:Marlin\n" + priming +
                b"SET_PRINT_STATS_INFO CURRENT_LAYER=1 TOTAL_LAYER=2\n"
                b";LAYER:0\nG1 X1 Y1 E0.1 F1800\n"
                b"SET_PRINT_STATS_INFO CURRENT_LAYER=2 TOTAL_LAYER=2\n"
                b";LAYER:1\nG1 X2 Y2 E0.2 F1800\n")
        index = build_index_from_bytes(data)
        self.assertEqual(len(index.ranges), 2)

    def test_baked_pause_commands_map_to_their_layers(self):
        # The 2026-09-16 ruling: pauses baked into the gcode surface as
        # read-only list rows — the index maps each pause command to
        # the layer whose block contains it.
        data = (b";LAYER:0\nG1 X1 Y1\nPAUSE\n;LAYER:1\nG1 X2 Y2\nM0\n"
                b";LAYER:2\nG1 X3 Y3\nM25\n;LAYER:3\nG1 X4 Y4\n")
        index = build_index_from_bytes(data)
        self.assertEqual(tuple(index.pauses), (0, 1, 2))

    def test_the_simulators_generated_gcode_bakes_the_pause_at_layer_20(self):
        # The harness's x10 premise (by request): the sim's
        # generated gcode carries ONE baked PAUSE, and the index must
        # find it at layer 20 — otherwise every index scenario
        # silently loses the baked-pause path.
        from tests.harness.gcodegen import make_gcode
        index = build_index_from_bytes(make_gcode(40).encode("utf-8"))
        self.assertEqual(tuple(index.pauses), (20,))
        # The per-layer elapsed markers are the x10 ETA's premise —
        # every layer must resolve (the panel's catch: the round-trip
        # previously asserted only the pause).
        self.assertEqual(len(index.layer_elapsed_times), 40)
        self.assertTrue(all(value is not None for value in index.layer_elapsed_times))
        self.assertEqual(index.layer_elapsed_times[:3], [30.0, 60.0, 90.0])

    def test_baked_pauses_before_the_first_layer_marker_are_skipped(self):
        data = b"G28\nPAUSE\n;LAYER:0\nG1 X1 Y1\n;LAYER:1\nG1 X2 Y2\n"
        index = build_index_from_bytes(data)
        self.assertEqual(tuple(index.pauses), ())

    def test_pause_comment_lines_never_count(self):
        data = b";LAYER:0\nG1 X1 Y1\n; PAUSE here on purpose\nPAUSE\n;LAYER:1\nG1 X2 Y2\n"
        index = build_index_from_bytes(data)
        self.assertEqual(tuple(index.pauses), (0,))

    def test_pause_after_the_elapsed_marker_belongs_to_that_layer(self):
        # PauseAtHeight emits its pause block AFTER the layer's
        # ;TIME_ELAPSED line, past the block's recorded end — the
        # mapping must follow the block starts (the live report: the
        # real job's M0 lines were dropped by the end-based check).
        data = b";LAYER:0\nG1 X1 Y1\n;TIME_ELAPSED:1\nPAUSE\n;LAYER:1\nG1 X2 Y2\n"
        index = build_index_from_bytes(data)
        self.assertEqual(tuple(index.pauses), (0,))

    def test_zero_based_current_layer_mapping(self):
        data = b""";LAYER:0\nSET_PRINT_STATS_INFO CURRENT_LAYER=0\nG1 X1 Y1\n;TIME_ELAPSED:1\n;LAYER:1\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X2 Y2\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.current_layer_map, {0: 0, 1: 1})

    def test_one_based_current_layer_mapping(self):
        data = b""";LAYER:0\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X1\n;LAYER:1\nSET_PRINT_STATS_INFO CURRENT_LAYER=2\nG1 X2\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.current_layer_map, {1: 0, 2: 1})

    def test_stats_markers_can_be_layer_fallback(self):
        data = b"""SET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X1\nSET_PRINT_STATS_INFO CURRENT_LAYER=2\nG1 X2\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.layer_count(), 2)
        self.assertEqual(index.current_layer_map, {1: 0, 2: 1})

    def test_prusaslicer_and_superslicer_layer_change_markers(self):
        data = b"""; generated by PrusaSlicer\n;LAYER_CHANGE\n;Z:0.2\n;HEIGHT:0.2\nG1 X1\n;LAYER_CHANGE\n;Z:0.4\n;HEIGHT:0.2\nG1 X2\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.layer_count(), 2)
        self.assertEqual([index.motion_count(0), index.motion_count(1)], [1, 1])

    def test_orcaslicer_numeric_markers_map_actual_values(self):
        data = b"""; layer num/total_layer_count: 1/2\nG1 X1\n; layer num/total_layer_count: 2/2\nG1 X2\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.layer_count(), 2)
        self.assertEqual(index.current_layer_map, {1: 0, 2: 1})

    def test_time_elapsed_values_are_retained_per_layer(self):
        data = b""";LAYER:0\nG1 X1\n;TIME_ELAPSED:12.5\n;LAYER:1\nG1 X2\n;TIME_ELAPSED:31.75\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.layer_elapsed_times, [12.5, 31.75])

    def test_time_elapsed_ends_layer_before_following_travel(self):
        data = b"""G90\n;LAYER:0\nG1 X10 Y10 Z0.2\n;TIME_ELAPSED:1\nG1 X20 Y20 Z0.4\n;LAYER:1\nG1 X30 Y30 Z0.4\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.motion_count(0), 1)
        self.assertEqual(index.layer_start_positions[1], (20.0, 20.0, 0.4))

    def test_relative_and_g92_coordinates(self):
        data = b"""G90\nG1 X10 Y10 Z1\nG92 X0 Y0\nG91\n;LAYER:0\nG1 X2 Y3 Z0.5\n"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.layer_start_positions[0], (0.0, 0.0, 1.0))
        self.assertAlmostEqual(index.motion_x[0][0], 2.0)
        self.assertAlmostEqual(index.motion_y[0][0], 3.0)
        self.assertAlmostEqual(index.motion_z[0][0], 1.5)

    def test_compact_gcode_and_inches(self):
        data = b"G20\nG90\n;LAYER:0\nN12G1X1Y2Z0.1\n"
        index = build_index_from_bytes(data)
        self.assertEqual(index.motion_count(0), 1)
        self.assertAlmostEqual(index.motion_x[0][0], 25.4, places=3)
        self.assertAlmostEqual(index.motion_y[0][0], 50.8, places=3)
        self.assertAlmostEqual(index.motion_z[0][0], 2.54, places=3)

    def test_file_fraction_counts_motion_commands(self):
        data = b";LAYER:0\nG1 X1\nG1 X2\nG1 X3\n"
        index = build_index_from_bytes(data)
        second = int(index.motion_offsets[0][1])
        fraction, method = index.file_fraction(0, second)
        self.assertEqual(method, "motion index")
        self.assertAlmostEqual(fraction, 2 / 3)

    def test_live_position_refines_behind_parser(self):
        lines = [b"G90\n", b";LAYER:0\n"]
        for x in range(1, 21):
            lines.append(f"G1 X{x} Y0 Z0.2\n".encode())
        index = build_index_from_bytes(b"".join(lines))
        coarse_pos = int(index.motion_offsets[0][17])
        coarse, _ = index.file_fraction(0, coarse_pos)
        refined, method = index.refined_fraction(0, coarse_pos, (10.2, 0.0, 0.2), lag_window=20)
        self.assertEqual(method, "live position")
        self.assertLess(refined, coarse)
        self.assertAlmostEqual(refined, 10.2 / 20.0, delta=0.06)

    def test_implausible_live_position_falls_back(self):
        data = b";LAYER:0\nG1 X1 Y0 Z0.2\nG1 X2 Y0 Z0.2\n"
        index = build_index_from_bytes(data)
        pos = int(index.motion_offsets[0][0])
        expected, expected_method = index.file_fraction(0, pos)
        actual, method = index.refined_fraction(0, pos, (1000, 1000, 1000))
        self.assertEqual(actual, expected)
        self.assertEqual(method, expected_method)

    def test_live_position_floor_prevents_closed_loop_rewind(self):
        data = b"""G90
G1 X0 Y0 Z0.2
;LAYER:0
G1 X10 Y0 Z0.2
G1 X10 Y10 Z0.2
G1 X0 Y10 Z0.2
G1 X0 Y0 Z0.2
G1 X5 Y0 Z0.2
"""
        index = build_index_from_bytes(data)
        coarse_pos = int(index.motion_offsets[0][-1])

        # At the loop closure, the live XYZ is also on the very first segment.
        # A stateless nearest-segment search can therefore jump back to the
        # beginning even though the parser is near the end of the layer.
        unconstrained, _ = index.refined_fraction(
            0, coarse_pos, (0.0, 0.0, 0.2), lag_window=20
        )
        self.assertLess(unconstrained, 0.2)

        stable, method = index.refined_fraction(
            0,
            coarse_pos,
            (0.0, 0.0, 0.2),
            lag_window=20,
            minimum_fraction=0.6,
        )
        self.assertGreaterEqual(stable, 0.6)
        # The search may dip below the floor (bounded lookback); the
        # monotonic clamp then labels the result honestly.
        self.assertTrue(method.startswith("live position"))

    def test_floor_dip_lets_refinement_recover_an_inflated_floor(self):
        # 1000 motions along X. The floor claims progress at motion ~500
        # while the live position is on the true segment 399. The bounded
        # floor lookback must let the search reach the true segment instead
        # of tripping the distance guard (the pre-8f56bbe floor cascade).
        lines = [b"G90\n", b";LAYER:0\n"]
        for x in range(1, 1001):
            lines.append(f"G1 X{x} Y0 Z0.2\n".encode())
        index = build_index_from_bytes(b"".join(lines))
        parser_pos = int(index.motion_offsets[0][500])
        fraction, method = index.refined_fraction(
            0, parser_pos, (400.0, 0.0, 0.2), minimum_fraction=0.5
        )
        self.assertEqual(fraction, 0.5)  # the monotonic clamp, never a rewind
        self.assertEqual(method, "live position (monotonic)")

    def test_off_model_position_with_floor_holds_the_last_value(self):
        data = b";LAYER:0\nG1 X1 Y0 Z0.2\nG1 X2 Y0 Z0.2\nG1 X3 Y0 Z0.2\n"
        index = build_index_from_bytes(data)
        pos = int(index.motion_offsets[0][0])
        fraction, method = index.refined_fraction(
            0, pos, (1000.0, 1000.0, 1000.0), minimum_fraction=0.3
        )
        self.assertEqual(fraction, 0.3)
        self.assertEqual(method, "held (refined unavailable)")

    def test_refined_fraction_never_drops_below_visible_progress_floor(self):
        data = b";LAYER:0\nG1 X1 Y0 Z0.2\nG1 X2 Y0 Z0.2\nG1 X3 Y0 Z0.2\n"
        index = build_index_from_bytes(data)
        first = int(index.motion_offsets[0][0])
        fraction, method = index.refined_fraction(
            0, first, None, minimum_fraction=0.75
        )
        self.assertEqual(fraction, 0.75)
        self.assertIn("monotonic", method)

    def test_lf_and_crlf_have_same_motion_counts_and_mapping(self):
        lf = b";LAYER:0\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X1\n;LAYER:1\nSET_PRINT_STATS_INFO CURRENT_LAYER=2\nG1 X2\n"
        crlf = lf.replace(b"\n", b"\r\n")
        a = build_index_from_bytes(lf)
        b = build_index_from_bytes(crlf)
        self.assertEqual([len(x) for x in a.motion_offsets], [len(x) for x in b.motion_offsets])
        self.assertEqual(a.current_layer_map, b.current_layer_map)

    def test_cancelled_build_returns_empty_index(self):
        event = threading.Event()
        event.set()
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n", event)
        self.assertFalse(index)

    def test_randomized_legacy_offset_semantics(self):
        rng = random.Random(4815162342)
        for _ in range(250):
            lines = [b";HEADER\n"]
            layer_count = rng.randint(1, 8)
            for layer in range(layer_count):
                lines.append(f";LAYER:{layer}\n".encode())
                for _move in range(rng.randint(1, 30)):
                    if rng.random() < 0.2:
                        lines.append(b"; comment\n")
                    lines.append(
                        f"G1 X{rng.random()*200:.4f} Y{rng.random()*200:.4f} Z{(layer+1)*0.2:.3f}\n".encode()
                    )
                if rng.random() < 0.8:
                    lines.append(f";TIME_ELAPSED:{layer+1}\n".encode())
                    if rng.random() < 0.5:
                        lines.append(b"G1 X0 Y0\n")
            data = b"".join(lines)
            expected_ranges, expected_moves = legacy_reference(data)
            actual = build_index_from_bytes(data)
            self.assertEqual(actual.ranges, expected_ranges)
            self.assertEqual([list(a) for a in actual.motion_offsets], expected_moves)

    def test_persistent_cache_round_trip(self):
        # The baked-pause layers must survive the cache too (the
        # 2026-09-16 report: every cached index loaded without them
        # because the old cache format predated the field).
        data = b";LAYER:0\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X1 Y2 Z0.2\nPAUSE\nG1 X2 Y3 Z0.2\n"
        index = build_index_from_bytes(data)
        self.assertEqual(tuple(index.pauses), (0,))
        identity = RemoteFileIdentity("a.gcode", len(data), 100.0, "uuid-1")
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory, max_bytes=8 * 1024 * 1024, max_entries=4)
            cache.save(identity, index)
            loaded = cache.load(identity)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.ranges, index.ranges)
            self.assertEqual(loaded.current_layer_map, index.current_layer_map)
            self.assertEqual(tuple(loaded.pauses), (0,))
            self.assertEqual([list(v) for v in loaded.motion_offsets], [list(v) for v in index.motion_offsets])
            self.assertEqual([list(v) for v in loaded.motion_x], [list(v) for v in index.motion_x])
            self.assertEqual(loaded.layer_motion_counts, index.layer_motion_counts)

    def test_compact_cache_carries_counts_for_evicted_layers(self):
        # A compact save writes the arrays in their EVICTED state, but
        # the eviction-proof counts ride beside them: a restored index
        # resolves a far layer's total before its first re-hydration.
        data = (b"G90\n;LAYER:0\nG1 X1 Y1 Z0.2\nG1 X2 Y2 Z0.2\n;LAYER:1\nG1 X3 Y3 Z0.4\n"
                b";LAYER:2\nG1 X4 Y4 Z0.6\n;LAYER:3\nG1 X5 Y5 Z0.8\n;LAYER:4\nG1 X6 Y6 Z1.0\n")
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            for layer in range(4):
                self.assertTrue(hydrate_layer_from_file(index, path, layer))
            self.assertEqual(index.hydrated_layers, {2, 3})
            self.assertEqual(index.motion_count(0), 2)
            identity = RemoteFileIdentity("a.gcode", len(data), 100.0, "uuid-1")
            with tempfile.TemporaryDirectory() as directory:
                cache = PersistentIndexCache(directory, max_bytes=8 * 1024 * 1024, max_entries=4)
                cache.save(identity, index)
                loaded = cache.load(identity)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.motion_count(0), 2)
                # A layer hydration never recorded stays unknown.
                self.assertEqual(loaded.motion_count(4), 0)
                self.assertEqual(loaded.hydrated_layers, {2, 3})
        finally:
            os.remove(path)

    def test_compact_index_hydrates_only_requested_layer(self):
        data = b"G90\n;LAYER:0\nG1 X1 Y1 Z0.2\nG1 X2 Y2 Z0.2\n;LAYER:1\nG1 X3 Y3 Z0.4\n"
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            self.assertTrue(index.compact)
            self.assertEqual(index.hydrated_layers, set())
            self.assertEqual(index.motion_count(0), 0)
            fraction, method = index.file_fraction(0, index.ranges[0][0] + 1)
            self.assertEqual(method, "byte position")
            self.assertTrue(hydrate_layer_from_file(index, path, 0))
            self.assertEqual(index.hydrated_layers, {0})
            self.assertEqual(index.motion_count(0), 2)
            self.assertEqual(index.motion_count(1), 0)
        finally:
            os.remove(path)

    def test_compact_hydration_evicts_layers_past_the_transition(self):
        # The retention bound (the live report's progress-driven
        # growth): hydrating for the live print must not accumulate
        # every crossed layer's motion arrays for the whole job.
        data = (b"G90\n;LAYER:0\nG1 X1 Y1 Z0.2\nG1 X2 Y2 Z0.2\n;LAYER:1\nG1 X3 Y3 Z0.4\n"
                b";LAYER:2\nG1 X4 Y4 Z0.6\n;LAYER:3\nG1 X5 Y5 Z0.8\n;LAYER:4\nG1 X6 Y6 Z1.0\n")
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            for layer in range(4):
                self.assertTrue(hydrate_layer_from_file(index, path, layer))
            self.assertEqual(index.hydrated_layers, {2, 3})
            # Eviction wipes the motion arrays but never the counts: a
            # seek to an evicted layer still resolves its total.
            self.assertEqual(index.motion_count(0), 2)
            self.assertEqual(index.motion_count(3), 1)
            self.assertTrue(hydrate_layer_from_file(index, path, 4))
            self.assertEqual(index.hydrated_layers, {3, 4})
            self.assertEqual(index.motion_count(2), 1)
            self.assertEqual(index.motion_count(4), 1)
        finally:
            os.remove(path)

    def test_compact_hydration_anchor_follows_the_followed_layer(self):
        # The eviction window anchors to the FOLLOWED layer, not to the
        # worker's last pick: a prefetched look-ahead layer survives
        # while the print is on the anchor, and a backward-jump
        # hydration cannot evict the layers around the live layer.
        data = (b"G90\n;LAYER:0\nG1 X1 Y1 Z0.2\nG1 X2 Y2 Z0.2\n;LAYER:1\nG1 X3 Y3 Z0.4\n"
                b";LAYER:2\nG1 X4 Y4 Z0.6\n;LAYER:3\nG1 X5 Y5 Z0.8\n;LAYER:4\nG1 X6 Y6 Z1.0\n")
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            # The live print is on layer 2; the worker prefetches 3.
            self.assertTrue(hydrate_layer_from_file(index, path, 1, keep_anchor=2))
            self.assertTrue(hydrate_layer_from_file(index, path, 2, keep_anchor=2))
            self.assertTrue(hydrate_layer_from_file(index, path, 3, keep_anchor=2))
            self.assertEqual(index.hydrated_layers, {1, 2, 3})
            # A stray hydration of an old layer cannot evict the window
            # around the followed layer.
            self.assertTrue(hydrate_layer_from_file(index, path, 0, keep_anchor=2))
            # The live window survives the stray hydration, and the
            # freshly hydrated layer keeps its own ±1 window until the
            # next hydrate (the background pass's prepare window —
            # bounded at the three windows' union).
            self.assertEqual(index.hydrated_layers, {0, 1, 2, 3})
            # The stray hydration's count persists.
            self.assertEqual(index.motion_count(0), 2)
        finally:
            os.remove(path)

    def test_compact_hydration_anchor_reads_the_indexes_followed_layer(self):
        # The production connection (the review repro): the service
        # updates the followed layer every poll and the worker reads
        # the LATEST anchor at completion — a prefetch of layer+1 must
        # not drift the window ahead of the print, and a worker
        # finishing after an anchor change applies the new policy.
        data = (b"G90\n;LAYER:0\nG1 X1 Y1 Z0.2\nG1 X2 Y2 Z0.2\n;LAYER:1\nG1 X3 Y3 Z0.4\n"
                b";LAYER:2\nG1 X4 Y4 Z0.6\n;LAYER:3\nG1 X5 Y5 Z0.8\n;LAYER:4\nG1 X6 Y6 Z1.0\n"
                b";LAYER:5\nG1 X7 Y7 Z1.2\n")
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            index.followed_layer = 3
            self.assertTrue(hydrate_layer_from_file(index, path, 2))
            self.assertTrue(hydrate_layer_from_file(index, path, 3))
            self.assertTrue(hydrate_layer_from_file(index, path, 4))  # the prefetch
            self.assertEqual(index.hydrated_layers, {2, 3, 4})
            # The print advances; a later worker applies the latest
            # anchor and evicts the stale previous layer.
            index.followed_layer = 4
            self.assertTrue(hydrate_layer_from_file(index, path, 5))
            self.assertEqual(index.hydrated_layers, {3, 4, 5})
            # Evicted with its arrays, but the recorded count persists.
            self.assertEqual(index.motion_count(2), 1)
        finally:
            os.remove(path)

    def test_compact_hydration_preserves_relative_and_inch_state(self):
        # Compact indexes must remember modal state at the layer boundary.
        # Otherwise hydrating only the selected layer would incorrectly parse
        # relative/inch moves as fresh absolute/mm coordinates.
        data = (
            b"G20\n"
            b"G91\n"
            b"G1 X1 Y1 Z0.1\n"
            b";LAYER:0\n"
            b"G1 X1 Y2 Z0.1\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            self.assertFalse(index.layer_start_absolute[0])
            self.assertAlmostEqual(index.layer_start_units[0], 25.4)
            self.assertTrue(hydrate_layer_from_file(index, path, 0))
            self.assertAlmostEqual(float(index.motion_x[0][0]), 50.8, places=3)
            self.assertAlmostEqual(float(index.motion_y[0][0]), 76.2, places=3)
            self.assertAlmostEqual(float(index.motion_z[0][0]), 5.08, places=3)
        finally:
            os.remove(path)

    def test_compact_index_persistent_cache_round_trip(self):
        data = b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n"
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            path = handle.name
            handle.write(data)
        try:
            index = build_index_from_file(path, compact=True)
            hydrate_layer_from_file(index, path, 1)
            identity = RemoteFileIdentity("large.gcode", len(data), 55.0, "compact-u")
            with tempfile.TemporaryDirectory() as directory:
                cache = PersistentIndexCache(directory)
                cache.save(identity, index)
                loaded = cache.load(identity)
                self.assertIsNotNone(loaded)
                self.assertTrue(loaded.compact)
                self.assertEqual(loaded.hydrated_layers, {1})
                self.assertEqual(loaded.motion_count(0), 0)
                self.assertEqual(loaded.motion_count(1), 1)
        finally:
            os.remove(path)

    def test_a_uuid_regeneration_never_invalidates_a_valid_cache(self):
        # The review's UUID-policy finding: Moonraker rolls a fresh
        # uuid per metadata extraction, and the loader must not
        # reject an otherwise-valid entry over it — the stable key
        # already keys on size/modified.
        data = b";LAYER:0\nG1 X1 Y1 Z0.2\n"
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            identity = RemoteFileIdentity("a.gcode", len(data), 100.0, "uuid-old")
            cache.save(identity, index)
            reextracted = RemoteFileIdentity("a.gcode", len(data), 100.0, "uuid-fresh")
            self.assertIsNotNone(cache.load(reextracted),
                                 "the fresh extraction uuid invalidated the cache")
            # A genuinely different file still refuses.
            other = RemoteFileIdentity("a.gcode", len(data) + 1, 100.0, "uuid-fresh")
            self.assertIsNone(cache.load(other),
                              "a different size read the old cache")

    def test_an_oversized_index_survives_its_own_prune(self):
        # The review's oversized-entry finding: a single index larger
        # than the whole budget must not be written and then
        # immediately evicted — the just-written entry is protected.
        data = b";LAYER:0\n" + b"G1 X1 Y1 Z0.2\n" * 40000
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory, max_bytes=1024, max_entries=1)
            identity = RemoteFileIdentity("big.gcode", len(data), 100.0, "uuid-1")
            cache.save(identity, index)
            loaded = cache.load(identity)
            self.assertIsNotNone(loaded,
                                 "the oversized index evicted itself after the write")

    def test_two_machine_namespaces_never_collide(self):
        # The review's two-printer test: the SAME remote identity (the
        # same filename, size and modified) under two machine
        # directories — each printer's cache is its own.
        data = b";LAYER:0\nG1 X1 Y1 Z0.2\n"
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache_a = PersistentIndexCache(os.path.join(directory, "a"))
            cache_b = PersistentIndexCache(os.path.join(directory, "b"))
            identity = RemoteFileIdentity("a.gcode", len(data), 100.0, "uuid-1")
            cache_a.save(identity, index)
            cache_b.save(identity, index)
            self.assertNotEqual(os.path.dirname(cache_a._path(identity)),
                                os.path.dirname(cache_b._path(identity)),
                                "the two machines share one directory")
            self.assertIsNotNone(cache_a.load(identity))
            self.assertIsNotNone(cache_b.load(identity))

    def test_persistent_cache_rejects_wrong_identity(self):
        # The new UUID policy (the review's finding): the same
        # filename/size/modified with ANY uuid is the same content —
        # the load succeeds. A genuinely different size refuses.
        data = b";LAYER:0\nG1 X1\n"
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            cache.save(RemoteFileIdentity("a", len(data), 1, "u1"), index)
            self.assertIsNotNone(cache.load(RemoteFileIdentity("a", len(data), 1, "u2")),
                                 "a fresh uuid refused the same content")
            self.assertIsNone(cache.load(RemoteFileIdentity("a", len(data) + 1, 1, "u1")),
                              "a different size read the cache")

    def test_persistent_cache_rejects_truncation(self):
        data = b";LAYER:0\nG1 X1\nG1 X2\n"
        index = build_index_from_bytes(data)
        identity = RemoteFileIdentity("a", len(data), 1, "u1")
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            cache.save(identity, index)
            path = cache._path(identity)
            with open(path, "rb") as handle:
                raw = handle.read()
            with open(path, "wb") as handle:
                handle.write(raw[: max(1, len(raw)//2)])
            self.assertIsNone(cache.load(identity))

    def test_cache_prunes_entry_count(self):
        data = b";LAYER:0\nG1 X1\n"
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory, max_entries=2)
            for i in range(4):
                cache.save(RemoteFileIdentity(f"{i}.gcode", len(data), float(i), f"u{i}"), index)
            blobs = [name for _root, _dirs, names in os.walk(directory)
                     for name in names if name.endswith('.mpfi.gz')]
            self.assertLessEqual(len(blobs), 2)

    def test_cura_orca_prusa_and_variable_layer_fixtures(self):
        cura = build_index_from_file(str(FIXTURES / "cura.gcode"), compact=False)
        self.assertEqual(cura.layer_count(), 3)
        self.assertEqual(cura.current_layer_map, {1: 0, 2: 1, 3: 2})
        self.assertEqual(cura.layer_elapsed_times, [120.0, 420.0, 900.0])
        orca = build_index_from_file(str(FIXTURES / "orca.gcode"), compact=False)
        self.assertEqual(orca.current_layer_map, {1: 0, 2: 1, 3: 2})
        self.assertEqual(build_index_from_file(str(FIXTURES / "prusa.gcode"), compact=False).layer_count(), 3)
        variable = build_index_from_file(str(FIXTURES / "variable_layers.gcode"), compact=False)
        self.assertEqual(variable.layer_elapsed_times, [10.0, 22.0, 45.0])

    def test_pause_missing_time_and_resume_fixtures_remain_indexable(self):
        paused = build_index_from_file(str(FIXTURES / "pause.gcode"), compact=False)
        self.assertEqual(paused.layer_count(), 3)
        self.assertEqual(paused.motion_count(1), 2)
        missing = build_index_from_file(str(FIXTURES / "missing_time.gcode"), compact=False)
        self.assertEqual(missing.layer_elapsed_times, [None, None])
        resumed = build_index_from_file(str(FIXTURES / "resume.gcode"), compact=False)
        self.assertEqual(resumed.current_layer_map, {1: 0, 2: 1, 3: 2, 4: 3})

    def test_leading_start_gcode_stats_value_does_not_shift_layer_map(self):
        # Klipper START_PRINT macros commonly emit CURRENT_LAYER=0 before the
        # first ;LAYER marker. The per-layer map must not treat that leading
        # value as layer zero's own.
        data = b"""SET_PRINT_STATS_INFO CURRENT_LAYER=0
G28
;LAYER:0
SET_PRINT_STATS_INFO CURRENT_LAYER=1
G1 X1
;TIME_ELAPSED:1
;LAYER:1
SET_PRINT_STATS_INFO CURRENT_LAYER=2
G1 X2
"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.current_layer_map, {1: 0, 2: 1})

    def test_trailing_stats_value_keeps_layer_map(self):
        data = b""";LAYER:0
SET_PRINT_STATS_INFO CURRENT_LAYER=1
G1 X1
;TIME_ELAPSED:1
;LAYER:1
SET_PRINT_STATS_INFO CURRENT_LAYER=2
G1 X2
SET_PRINT_STATS_INFO CURRENT_LAYER=3
"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.current_layer_map, {1: 0, 2: 1})

    def test_live_position_wins_when_parser_is_far_ahead(self):
        # Klipper's parser reads the file in ~4KB chunks, so the coarse file
        # position can sit hundreds of motions ahead of the nozzle. The
        # refinement window must cover that lead and track the live position
        # instead of falling back to the quantised coarse fraction (which
        # made the observed progress a cm-apart staircase).
        lines = [b"G90\n"]
        x = 0.0
        for _ in range(300):
            x += 1.0
            lines.append(f"G1 X{x} Y0 Z0.2\n".encode())
        index = build_index_from_bytes(b";LAYER:0\n" + b"".join(lines))
        position = int(index.motion_offsets[0][250])
        fraction, method = index.refined_fraction(0, position, (100.0, 0.0, 0.2))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 100 / 300, places=3)

    def test_leading_zero_motion_forms_count_as_motion(self):
        data = b"""G91
;LAYER:0
G01 X1 Y2 Z0.2
G00 X2 Y2 Z0.2
"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.motion_count(0), 2)
        self.assertAlmostEqual(index.motion_x[0][1], 3.0)

    def test_hostile_index_inputs_stay_bounded(self):
        # Panel security P2-4: a poisoned/corrupt file on the printer
        # must not grow unbounded structures — giant lines are skipped,
        # marker-dense files cap their layer blocks, and a one-layer
        # motion bomb truncates at the per-layer cap instead of loading
        # the whole print into RAM.
        from plugins.GCodeIndex import _MAX_LAYER_BLOCKS, _MAX_MOTIONS_PER_LAYER
        giant = build_index_from_bytes(b";LAYER:0\nG1 X1\n" + b"G1 X2 " * 300_000)
        self.assertEqual(giant.layer_count(), 1)
        dense = build_index_from_bytes(b"".join(b";LAYER:%d\nG1 X1\n" % layer
                                                for layer in range(_MAX_LAYER_BLOCKS + 200)))
        self.assertLessEqual(dense.layer_count(), _MAX_LAYER_BLOCKS)
        bomb = build_index_from_bytes(b";LAYER:0\n" + b"G1 X1\n" * (_MAX_MOTIONS_PER_LAYER + 5_000)
                                      + b";LAYER:1\nG1 X2\n")
        self.assertLessEqual(bomb.motion_count(0), _MAX_MOTIONS_PER_LAYER)

    def test_cache_rejects_same_uuid_with_changed_size_or_modified(self):
        data = b";LAYER:0\nG1 X1\n"
        index = build_index_from_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            original = RemoteFileIdentity("a.gcode", 100, 1.0, "path-uuid")
            cache.save(original, index)
            self.assertIsNotNone(cache.load(RemoteFileIdentity("a.gcode", 100, 1.0, "path-uuid")))
            self.assertIsNone(cache.load(RemoteFileIdentity("a.gcode", 200, 1.0, "path-uuid")))
            self.assertIsNone(cache.load(RemoteFileIdentity("a.gcode", 100, 2.0, "path-uuid")))
            self.assertIsNotNone(cache.load(RemoteFileIdentity("a.gcode", 100, 1.0, "other-uuid")),
                                 "a fresh extraction uuid refused the same content")


if __name__ == "__main__":
    unittest.main()
