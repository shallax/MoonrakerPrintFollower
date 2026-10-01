"""Fast-path parity and modal-state preservation through compact hydration."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from mpf.GCode import GCodeParser
from mpf.GCode.GCodeIndex import build_index_from_file
from mpf.GCode.IndexHydrator import hydrate_layer_from_file


class TokenRecognitionTests(unittest.TestCase):
    def test_bare_axis_words_fall_back_without_losing_other_axes(self):
        for code in (b"G1 X", b"G1 Y 2", b"G1 Z", b"G1 E", b"G1 F"):
            with self.subTest(code=code):
                self.assertIsNone(GCodeParser._fast_motion_line(code))
        self.assertEqual(GCodeParser._parse_axes(b"G1 Y 2 X"), {"Y": 2.0})

    def test_bad_tokens_do_not_discard_following_valid_tokens(self):
        # Exercise the recogniser/converter boundary directly: a future
        # recogniser accepting a malformed word must retain valid siblings.
        for function, regex, good in ((GCodeParser._parse_axes, "_AXIS", b"X"),
                                      (GCodeParser._parse_arc_words, "_ARC_WORD", b"I")):
            matches = [Mock(group=Mock(side_effect=lambda n, pair=pair: pair[n - 1]))
                       for pair in ((b"\xff", b"1"), (good, b"invalid"), (good, b"2.5"))]
            with self.subTest(function=function.__name__), patch.object(GCodeParser, regex) as recogniser:
                recogniser.finditer.return_value = matches
                self.assertEqual(function(b"fixture"), {good.decode(): 2.5})


class HydrationModalStateTests(unittest.TestCase):
    def test_plane_extrusion_and_firmware_retraction_match_full_scan(self):
        data = (b";LAYER:0\nG90\nG21\nG17\nM82\nG1 X1 Y2 Z0.2 E1 F1200\n"
                b"M83\nG10\nG11\nG19\nG1 Y3 Z0.3 E0.2\nG17\nM82\nG1 X2 E2\n")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "modal.gcode"
            path.write_bytes(data)
            full = build_index_from_file(str(path), compact=False)
            compact = build_index_from_file(str(path), compact=True)
            self.assertTrue(hydrate_layer_from_file(compact, str(path), 0))
            for name in ("motion_offsets", "motion_x", "motion_y", "motion_z",
                         "motion_extrusion", "extruder_events", "firmware_retractions"):
                with self.subTest(column=name):
                    self.assertEqual(getattr(compact, name)[0], getattr(full, name)[0])
