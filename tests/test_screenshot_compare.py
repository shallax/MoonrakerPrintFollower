# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-26 [c9839b9] Keep scrub frames stable and prepare current-layer rewind checkpoints

"""Cross-CPU rounding may pass; visible changes and missing captures may not."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from qt_runtime_support import QT_AVAILABLE


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ScreenshotComparisonTests(unittest.TestCase):
    def test_only_a_tiny_number_of_two_level_rounding_differences_can_pass(self):
        from PyQt6.QtGui import QColor, QImage
        spec = importlib.util.spec_from_file_location("compare_screenshots", Path(__file__).resolve().parents[1] /
                                                     "tools" / "compare_screenshots.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.png", Path(directory) / "b.png"
            image = QImage(100, 100, QImage.Format.Format_ARGB32)
            image.fill(QColor(128, 128, 128))
            image.save(str(a))
            image.save(str(b))
            self.assertTrue(module.matches(a, b))
            image.setPixelColor(0, 0, QColor(130, 130, 130))
            image.save(str(b))
            self.assertTrue(module.matches(a, b))
            image.setPixelColor(0, 0, QColor(131, 128, 128))
            image.save(str(b))
            self.assertFalse(module.matches(a, b))
            for x in range(33):
                image.setPixelColor(x, 0, QColor(129, 129, 129))
            image.save(str(b))
            self.assertFalse(module.matches(a, b))
            self.assertFalse(module.matches(a, Path(directory) / "missing.png"))
            image = QImage(101, 100, QImage.Format.Format_ARGB32)
            image.save(str(b))
            self.assertFalse(module.matches(a, b))
