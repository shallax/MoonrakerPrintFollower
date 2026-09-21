"""The native renderer's stroke semantics: ONE configured geometry
pen shared by every asset, so the full layer, the prefix and the
grey base can never disagree on physical width."""
from __future__ import annotations

import unittest

from qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from PyQt6.QtGui import QImage

    from plugins.PlateQt import render_layer_prefix, render_layer_raster


def _payload():
    # A single straight horizontal run of 20 motions at bed y=100.
    points = [[10.0 + i * 5.0, 100.0, float(i)] for i in range(21)]
    return {"classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 20}


def _view(**overrides):
    view = {"width": 400, "height": 300, "scale": 1.0, "lineScale": 0.7,
            "compact": False, "panX": 0.0, "panY": 0.0}
    view.update(overrides)
    return view


def _plot(**overrides):
    plot = {"offsetX": 10.0, "offsetY": 10.0, "sx": 1.5, "sy": 1.5,
            "bedXMin": 0.0, "bedYMax": 250.0}
    plot.update(overrides)
    return plot


def _stroke_height(image: QImage, col: int) -> int:
    """The vertical ink run at one column — the stroke's screen
    thickness (round caps, so the middle column is flat)."""
    rows = []
    for row in range(image.height()):
        if image.pixelColor(col, row).alpha() > 0:
            rows.append(row)
    return len(rows)


def _stroke_row(image: QImage) -> int:
    for row in range(image.height()):
        for col in range(image.width()):
            if image.pixelColor(col, row).alpha() > 0:
                return row
    return -1


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeStrokeParityTests(unittest.TestCase):
    def test_the_full_raster_and_the_prefix_share_one_stroke(self):
        # The full layer and the prefix rendered at the same view
        # must stroke the SAME screen thickness — the prefix used
        # to keep its configured pen while the full layer reset to
        # a default-width one.
        payload = _payload()
        plot = _plot()
        view = _view(lineScale=8.0)  # a thick, measurable stroke
        coloured, _grey, _travels = render_layer_raster(payload, plot, view)
        prefix = render_layer_prefix(payload, plot, view, 20)
        full_width = _stroke_height(coloured, 100)
        prefix_width = _stroke_height(prefix, 100)
        # Antialiasing is off (the painter's deliberate setting):
        # a 2.4 px stroke snaps to 2-3 rows; a reset default-width
        # pen would draw ONE.
        self.assertGreater(full_width, 1,
                           "the full raster drew a hairline (the pen was reset)")
        self.assertEqual(full_width, prefix_width,
                         "the full and prefix strokes disagree")

    def test_line_scale_affects_both_equally(self):
        payload = _payload()
        plot = _plot()
        thin = render_layer_raster(payload, plot, _view(lineScale=0.7))[0]
        thick = render_layer_raster(payload, plot, _view(lineScale=8.0))[0]
        thin_prefix = render_layer_prefix(payload, plot, _view(lineScale=0.7), 20)
        thick_prefix = render_layer_prefix(payload, plot, _view(lineScale=8.0), 20)
        self.assertGreater(_stroke_height(thick, 100), _stroke_height(thin, 100),
                           "the full layer ignored lineScale")
        self.assertGreater(_stroke_height(thick_prefix, 100), _stroke_height(thin_prefix, 100),
                           "the prefix ignored lineScale")
        self.assertEqual(_stroke_height(thick_prefix, 100), _stroke_height(thick, 100),
                         "lineScale widened the prefix and the full layer differently")

    def test_zoom_affects_both_equally(self):
        payload = _payload()
        plot = _plot()
        # A taller canvas: at 2x the zoomed run sits at y ~470.
        at_1x = render_layer_raster(payload, plot,
                                    _view(scale=1.0, lineScale=8.0, height=600))[0]
        at_2x = render_layer_raster(payload, plot,
                                    _view(scale=2.0, lineScale=8.0, height=600))[0]
        prefix_2x = render_layer_prefix(payload, plot,
                                        _view(scale=2.0, lineScale=8.0, height=600), 20)
        self.assertGreater(_stroke_height(at_2x, 100), _stroke_height(at_1x, 100),
                           "the full layer ignored the zoom")
        self.assertEqual(_stroke_height(prefix_2x, 100), _stroke_height(at_2x, 100),
                         "the zoom widened the prefix and the full layer differently")

    def test_compact_boost_affects_both_equally(self):
        payload = _payload()
        plot = _plot()
        normal = render_layer_raster(payload, plot, _view(lineScale=8.0))[0]
        compact = render_layer_raster(payload, plot, _view(lineScale=8.0, compact=True))[0]
        prefix_compact = render_layer_prefix(payload, plot,
                                             _view(lineScale=8.0, compact=True), 20)
        self.assertGreater(_stroke_height(compact, 100), _stroke_height(normal, 100),
                           "the full layer ignored the compact boost")
        self.assertEqual(_stroke_height(prefix_compact, 100), _stroke_height(compact, 100),
                         "the boost widened the prefix and the full layer differently")

    def test_the_grey_base_inherits_the_same_geometry(self):
        payload = _payload()
        plot = _plot()
        coloured, grey, _travels = render_layer_raster(payload, plot, _view(lineScale=8.0))
        self.assertEqual(_stroke_row(coloured), _stroke_row(grey),
                         "the base's ink row differs from the raster's")
        self.assertEqual(_stroke_height(coloured, 100), _stroke_height(grey, 100),
                         "the base's stroke width differs from the raster's")

    def test_an_empty_image_never_publishes_a_url(self):
        # A failed/empty save must produce no URL — the transport
        # validity reads an empty source as invalid.
        from plugins.PlateQt import PlateLayer, png_file
        blank = QImage(0, 0, QImage.Format.Format_ARGB32_Premultiplied)
        self.assertEqual(png_file(blank, "/tmp/mpf/nowhere", "x"), "")
        layer = PlateLayer(_payload())
        layer.set_raster(blank, "key", "")
        layer.set_expected_key("key")
        self.assertFalse(layer.rasterValid,
                         "an empty raster read valid without a source")

    def test_dropping_the_wrapper_releases_the_images(self):
        # The wrapper is the ONLY owner of its rendered pixels (the
        # QML side holds file URLs, never the Python images), so
        # the wrapper's eviction must free all four siblings.
        import gc
        import weakref

        from plugins.PlateQt import PlateLayer
        payload = _payload()
        payload["travels"] = [[[10.0, 50.0, 0.0], [30.0, 50.0, 1.0]]]
        plot = _plot()
        view = _view(lineScale=8.0)
        coloured, base, travels = render_layer_raster(payload, plot, view)
        prefix = render_layer_prefix(payload, plot, view, 10)
        layer = PlateLayer(payload)
        layer.set_raster(coloured, "key", "file:///tmp/mpf/none.png")
        layer.set_base(base, "key", "file:///tmp/mpf/none.png")
        layer.set_travels(travels, "key", "file:///tmp/mpf/none.png")
        layer.set_prefix(prefix, "file:///tmp/mpf/none.png", 10, "key")
        self.assertGreater(layer.memory_bytes(), 0)
        refs = [weakref.ref(image) for image in (coloured, base, travels, prefix)]
        del layer, coloured, base, travels, prefix
        gc.collect()
        self.assertEqual([ref() for ref in refs], [None] * 4,
                         "an image survived the wrapper's drop")


if __name__ == "__main__":
    unittest.main()
