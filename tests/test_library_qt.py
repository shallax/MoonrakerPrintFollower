# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Prove immutable Qt snapshot parity with headless setup for isolated CI reports.

"""A consumer can render directly through public entry points without Cura."""
import os
import unittest
from tests.qt_runtime_support import QT_AVAILABLE

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(QT_AVAILABLE, 'Qt required')
class LibraryQtTests(unittest.TestCase):
    def test_empty_and_retraction_only_snapshots_cross_the_real_qvariant_boundary(self):
        from PyQt6.QtGui import QGuiApplication
        from mpf.gcode.GCodeDocument import GCodeDocument
        from mpf.plate.qt import PlateLayer
        app = QGuiApplication.instance() or QGuiApplication([])
        for data in (b';LAYER:0\n;LAYER:1\nG1 X1 Y1 Z0.2 F1800\n',
                     b'M83\n;LAYER:0\nG1 E-1 F1800\nG1 E1\n'):
            payload = GCodeDocument.from_bytes(data).geometry(0)
            layer = PlateLayer(payload)
            # QObject.property crosses PyQt's declared QVariantMap conversion;
            # reading the Python property directly would miss an abort here.
            actual = layer.property('fallbackVector')
            self.assertIsInstance(actual, dict)
            self.assertEqual(actual['motions'], payload['motions'])
            self.assertEqual(actual['classes'], {})
            for key in ('retractions', 'unretractions'):
                if payload.get(key):
                    self.assertEqual(actual[key], [list(point) for point in payload[key]])
            layer.deleteLater()
            app.processEvents()

    def test_public_optional_renderer_produces_nonempty_pixels(self):
        from PyQt6.QtGui import QGuiApplication
        from mpf.gcode import build_index_from_bytes, prepare_layer
        from mpf.gcode.GCodeDocument import GCodeDocument
        from mpf.plate.scenegraph import GpuFollower, PlateLayer
        from mpf.plate.raster import render_layer_raster
        from tools.check_library import SAMPLE
        app = QGuiApplication.instance() or QGuiApplication([])
        baseline = prepare_layer(build_index_from_bytes(SAMPLE), 0)
        payload = GCodeDocument.from_bytes(SAMPLE).geometry(0)
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
            reference, reference_grey, _ = render_layer_raster(baseline,
                dict(sx=4, sy=4, offsetX=0, offsetY=0, bedXMin=0, bedYMax=50),
                dict(width=200, height=200, dpr=1))
            self.assertEqual(image, reference)
            self.assertEqual(grey, reference_grey)
        finally:
            item.deleteLater()
            app.processEvents()
