"""Filament total sourcing: the client-side header parse and the honest guard.

R5-12: the plugin computes the print's filament TOTAL itself from the
downloaded gcode's FIRST ';Filament used:' line — summing its comma-
separated metre values and scaling to mm — instead of trusting
Moonraker's metadata, which undercounts multi-extruder prints until
v0.10 (its Cura parser read only the first value). The coordinator
prefers that client-side total and keeps the Moonraker metadata total
as the fallback; here the parse itself and the "—" guard (used over
total must never clamp to a confident "0.00 m") are pinned.
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from plugins.MonitorFormatting import (
    core_values,
    filament_total_mm_from_file,
    filament_total_mm_from_gcode,
)


def _remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)


class FilamentTotalHeaderTests(unittest.TestCase):
    """The ';Filament used:' header line is summed across all extruders."""

    def test_two_material_line_sums_all_values_to_mm(self):
        gcode = b";TIME:823\n;Filament used: 2.157m, 1.095m, 0.0m\n;LAYER_COUNT:20\n"
        self.assertAlmostEqual(filament_total_mm_from_gcode(gcode), 3252.0, places=6)

    def test_single_material_line_scales_metres_to_mm(self):
        self.assertAlmostEqual(filament_total_mm_from_gcode(b";Filament used: 2.157m\n"), 2157.0)

    def test_values_without_unit_suffix_are_still_metres(self):
        # Moonraker's own parser never demands the 'm' either — the
        # number alone is the metre value.
        self.assertAlmostEqual(filament_total_mm_from_gcode(b";Filament used: 2.157, 1.095\n"), 3252.0)

    def test_line_found_after_other_headers_and_commands(self):
        gcode = (b";FLAVOR:Marlin\n;TIME:823\n;LAYER_COUNT:20\n"
                 b"G28 ; home\n;Filament used: 1.0m\nG1 X0 Y0\n")
        self.assertAlmostEqual(filament_total_mm_from_gcode(gcode), 1000.0)

    def test_first_declaration_line_wins_over_later_ones(self):
        # Post-processors may re-emit a header; the FIRST declaration is
        # the file's own, exactly as Moonraker's parser treats it.
        gcode = b";Filament used: 1.0m\n;Filament used: 99.0m\n"
        self.assertAlmostEqual(filament_total_mm_from_gcode(gcode), 1000.0)

    def test_zero_lengths_parse_to_a_zero_total(self):
        # A genuine zero is a parseable total; the coordinator's
        # positive-total gate decides whether it is displayable.
        self.assertAlmostEqual(filament_total_mm_from_gcode(b";Filament used: 0m\n"), 0.0)

    def test_spacing_and_case_are_tolerated(self):
        self.assertAlmostEqual(filament_total_mm_from_gcode(b"  ;FILAMENT USED : 2.157m\r\n"), 2157.0)

    def test_no_filament_line_yields_none(self):
        gcode = b";TIME:823\n;LAYER_COUNT:20\nG1 X0 Y0\nM117 ;Filament used: 1.0m\n"
        # The M117 mention is NOT a header declaration: only comment
        # lines can declare the slicer's total.
        self.assertIsNone(filament_total_mm_from_gcode(gcode))

    def test_declaration_without_values_yields_none(self):
        self.assertIsNone(filament_total_mm_from_gcode(b";Filament used:\n"))

    def test_hostile_value_makes_the_whole_line_untrustworthy(self):
        # An overflowing digit run cannot poison the sum into a wrong
        # finite total: the line yields None and the metadata fallback
        # stands in.
        self.assertIsNone(filament_total_mm_from_gcode(b";Filament used: " + b"9" * 400 + b"m, 1.0m\n"))

    def test_empty_input_yields_none(self):
        self.assertIsNone(filament_total_mm_from_gcode(b""))


class FilamentTotalFileTests(unittest.TestCase):
    """The file scan reads the head once, bounded, and only complete lines."""

    def _write(self, data, limit=None):
        directory = tempfile.mkdtemp(prefix="mpf-filament-test-")
        path = os.path.join(directory, "print.gcode")
        with open(path, "wb") as handle:
            handle.write(data)
        self.addCleanup(_remove_tree, directory)
        if limit is None:
            return filament_total_mm_from_file(path)
        return filament_total_mm_from_file(path, limit=limit)

    def test_parses_the_head_of_a_downloaded_file(self):
        gcode = b";FLAVOR:Marlin\n;Filament used: 2.157m, 1.095m\n" + b"G1 X0 Y0\n" * 20
        self.assertAlmostEqual(self._write(gcode), 3252.0, places=6)

    def test_marker_as_last_line_without_trailing_newline(self):
        # A file that ends exactly at its declaration still parses: no
        # newline at EOF means the line is complete, not truncated.
        gcode = b"G1 X0 Y0\n;Filament used: 1.0m"
        self.assertAlmostEqual(self._write(gcode), 1000.0)

    def test_file_ending_exactly_at_the_window_is_fully_parsed(self):
        # size == limit: the window holds the whole file, final newline
        # included, so nothing is dropped.
        content = b";Filament used: 1.0m\n"
        self.assertAlmostEqual(self._write(content, limit=len(content)), 1000.0)

    def test_declaration_beyond_the_window_is_not_seen(self):
        # The bounded scan never reads the whole file: a declaration
        # past the window is invisible and the metadata fallback stands
        # in (today's behaviour for files never scanned).
        gcode = b"G1 X0 Y0\n" * 40 + b";Filament used: 1.0m\n"
        self.assertIsNone(self._write(gcode, limit=64))

    def test_line_truncated_by_the_window_is_dropped(self):
        # A partial declaration at the window edge must never parse: the
        # cut-off "1.0" would masquerade as the whole "1.0m, 5.0m".
        gcode = b"G1 X0 Y0\n;Filament used: 1.0m, 5.0m\n"
        self.assertIsNone(self._write(gcode, limit=len(b"G1 X0 Y0\n") + 6))

    def test_missing_file_yields_none(self):
        self.assertIsNone(filament_total_mm_from_file("/nonexistent/print.gcode"))

    def test_empty_file_yields_none(self):
        self.assertIsNone(self._write(b""))


class FilamentRemainingGuardTests(unittest.TestCase):
    """'Filament remaining' counts down to the total and never clamps past it."""

    def _values(self, used_mm=None, physical_total=None, snapshot_total=None):
        core = {"print_stats": {"state": "printing", "print_duration": 30}}
        if used_mm is not None:
            core["print_stats"]["filament_used"] = used_mm
        monitor = SimpleNamespace(core=core, auxiliary={}, server={}, filament_total=snapshot_total)
        # The coordinator's snapshot is the physical arg: it carries the
        # total the coordinator chose (client-side parse preferred).
        coordinator = SimpleNamespace(layer=SimpleNamespace(index=1, total=20, thickness=None),
                                      estimated_time=None, metadata_complete=True, layer_eta=None,
                                      filament_total=physical_total)
        return core_values(monitor, coordinator, True)

    def test_remaining_counts_down_against_the_total(self):
        values = self._values(used_mm=3500.0, physical_total=42000.0)
        self.assertEqual(values["filamentUsed"], "3.50 m")
        self.assertEqual(values["filamentRemaining"], "38.50 m")

    def test_remaining_reaches_zero_only_at_the_declared_total(self):
        self.assertEqual(self._values(used_mm=42000.0, physical_total=42000.0)["filamentRemaining"], "0.00 m")

    def test_used_over_total_is_dash_not_a_confident_zero(self):
        # The R5-12 bug: on a 2-material print the Moonraker metadata
        # total holds material 0 only, so a correct used length overtakes
        # it mid-print. The old clamp then showed "0.00 m" for the rest
        # of the job; the honest readout is "—".
        values = self._values(used_mm=3000.0, physical_total=2157.0)
        self.assertEqual(values["filamentUsed"], "3.00 m")
        self.assertEqual(values["filamentRemaining"], "—")

    def test_coordinator_client_total_wins_over_the_monitor_snapshot_total(self):
        # The coordinator's snapshot (physical) carries the client-side
        # header sum; the monitor snapshot's field is only the legacy
        # home. Prefer the client-side total exactly as the display
        # contract promises.
        values = self._values(used_mm=1000.0, physical_total=3252.0, snapshot_total=2157.0)
        self.assertEqual(values["filamentRemaining"], "2.25 m")

    def test_snapshot_total_is_the_legacy_fallback(self):
        values = self._values(used_mm=1000.0, physical_total=None, snapshot_total=2157.0)
        self.assertEqual(values["filamentRemaining"], "1.16 m")

    def test_missing_total_is_dash(self):
        self.assertEqual(self._values(used_mm=1000.0, physical_total=None)["filamentRemaining"], "—")

    def test_missing_used_length_is_dash_everywhere(self):
        values = self._values(used_mm=None, physical_total=42000.0)
        self.assertEqual(values["filamentUsed"], "—")
        self.assertEqual(values["filamentRemaining"], "—")


if __name__ == "__main__":
    unittest.main()
