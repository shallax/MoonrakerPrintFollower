# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Render through the documented optional Qt library interface.

"""A consumer can render directly through public entry points without Cura."""
import unittest
from tests.qt_runtime_support import QT_AVAILABLE


@unittest.skipUnless(QT_AVAILABLE, 'Qt required')
class LibraryQtTests(unittest.TestCase):
    def test_public_optional_renderer_produces_nonempty_pixels(self):
        from PyQt6.QtGui import QGuiApplication
        from mpf.gcode import build_index_from_bytes, prepare_layer
        from mpf.plate.qt import GpuFollower, PlateLayer, render_layer_raster
        from tools.check_library import SAMPLE
        app = QGuiApplication.instance() or QGuiApplication([])
        payload = prepare_layer(build_index_from_bytes(SAMPLE), 0)
        layer = PlateLayer(payload)
        item = GpuFollower()
        try:
            item.layers = {'current': layer}
            image, grey, _ = render_layer_raster(payload,
                dict(sx=4, sy=4, offsetX=0, offsetY=0, bedXMin=0, bedYMax=50),
                dict(width=200, height=200, dpr=1))
            self.assertFalse(image.isNull())
            self.assertEqual(image.size(), grey.size())
            self.assertTrue(any(image.pixelColor(x, y).alpha() for y in range(200) for x in range(200)))
        finally:
            item.deleteLater()
            app.processEvents()
