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
    def test_the_device_backing_scales_everything_equally(self):
        # D: the bounded device-pixel backing multiplies the canvas
        # AND the stroke together — a DPR-2 render is exactly 2x the
        # logical size at 2x the stroke thickness, so the displayed
        # logical picture (the scene-graph's downsampled result)
        # keeps the one-stroke contract. A logical-resolution
        # implementation renders 400x300 and fails the dimensions.
        payload = _payload()
        plot = _plot()
        view = _view(lineScale=8.0, dpr=2.0)
        coloured, _grey, _travels = render_layer_raster(payload, plot, view)
        prefix = render_layer_prefix(payload, plot, view, 20)
        self.assertEqual((coloured.width(), coloured.height()), (800, 600),
                         "the DPR-2 raster is not the device size")
        self.assertEqual((prefix.width(), prefix.height()), (800, 600),
                         "the DPR-2 prefix is not the device size")
        full_width = _stroke_height(coloured, 200)
        prefix_width = _stroke_height(prefix, 200)
        self.assertEqual(full_width, prefix_width,
                         "the DPR-2 full and prefix strokes disagree")
        # The stroke thickens WITH the backing: the downsampled
        # logical stroke keeps its physical width.
        onex, _grey_1x, _travels_1x = render_layer_raster(payload, plot, _view(lineScale=8.0))
        self.assertGreater(full_width, _stroke_height(onex, 100),
                           "the DPR-2 stroke never widened with the backing")

    def test_the_navigation_composite_flattens_the_whole_scene(self):
        # The interaction raster: ONE flattened full-bed image at
        # the fixed 4x backing carrying the exact stack's content —
        # the ghosts at 0.30, the grey base, the printed prefix at
        # the partial split and the travels below the boundary —
        # with the same stroke as the exact assets.
        from plugins.PlateQt import render_navigation_layer
        payload = _payload()
        window = {"prev": payload, "next": payload, "current": payload}
        plot = _plot()
        view = _view(lineScale=8.0, backing=4.0)
        image = render_navigation_layer(window, plot, view, split=10)
        self.assertEqual((image.width(), image.height()), (1600, 1200),
                         "the navigation raster is not 4x the logical view")
        # The stroke rides the same scaled pen as the exact scene's:
        # a 4x backing widens the stroke fourfold, so the displayed
        # logical picture keeps the one-stroke contract.
        coloured, _grey, _travels = render_layer_raster(payload, plot, _view(lineScale=8.0))
        self.assertGreater(
            _stroke_height(image, 400), _stroke_height(coloured, 100),
            "the navigation stroke never scaled with the backing")

    def test_the_navigation_composite_honours_the_partial_split(self):
        # The interaction raster must show the CURRENT layer
        # progress, never a 100%-complete layer: below the live
        # split the printed portion carries the class colour, beyond
        # it only the grey base's silhouette remains.
        from plugins.PlateQt import render_navigation_layer
        payload = {
            "classes": {"SKIN": [[[float(i), 240.0, float(i)]
                                  for i in range(20)]]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 20,
        }
        window = {"prev": None, "next": None, "current": payload}
        plot = _plot()
        view = _view(lineScale=8.0, backing=4.0)
        image = render_navigation_layer(window, plot, view, split=10)
        # The stroke's row: bed y=10 -> the 4x-backed pixel row.
        row = int((plot["offsetY"] + (plot["bedYMax"] - 240.0) * plot["sy"]) * 4.0)
        def pixel(bed_x):
            col = int((plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"]) * 4.0)
            return image.pixelColor(col, row)
        printed = pixel(5.0)
        unprinted = pixel(15.0)
        self.assertGreater(printed.red(), printed.green() + 50,
                           "the printed portion never drew its colour")
        self.assertLess(abs(unprinted.red() - unprinted.green()), 20,
                        "the unprinted tail drew the class colour")
        self.assertGreater(unprinted.alpha(), 0,
                           "the base's silhouette never reached the tail")

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
        # At a 2.4 px stroke the antialiased painter covers several
        # rows; a reset default-width pen would still draw a hairline.
        self.assertGreater(full_width, 1,
                           "the full raster drew a hairline (the pen was reset)")
        self.assertEqual(full_width, prefix_width,
                         "the full and prefix strokes disagree")

    def test_subpixel_native_strokes_use_fractional_coverage(self):
        # Partial composition joins this native prefix to a QML Canvas
        # tail. QML uses fractional coverage, so the native side must not
        # snap a sub-pixel width to a binary one-pixel stroke.
        payload = _payload()
        plot = _plot()
        view = _view(lineScale=0.7)
        full = render_layer_raster(payload, plot, view)[0]
        prefix = render_layer_prefix(payload, plot, view, 20)
        for image, name in ((full, "full"), (prefix, "prefix")):
            alphas = {image.pixelColor(col, row).alpha()
                      for row in range(image.height())
                      for col in range(image.width())}
            self.assertTrue(any(0 < alpha < 255 for alpha in alphas),
                            name + " lost antialiased fractional coverage")

    def test_native_travel_width_reads_the_render_contract_ratio(self):
        payload = _payload()
        payload["travels"] = [payload["classes"]["WALL-OUTER"][0]]
        plot = _plot()
        narrow = render_layer_raster(
            payload, plot, _view(lineScale=20.0, travelVisualRatio=0.25))[2]
        wide = render_layer_raster(
            payload, plot, _view(lineScale=20.0, travelVisualRatio=0.9))[2]
        self.assertGreater(_stroke_height(wide, 100), _stroke_height(narrow, 100),
                           "native travels ignored the shared visual ratio")

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

    def test_a_late_worker_emit_against_a_deleted_bridge_never_aborts(self):
        # A teardown deletes the bridge's C++ side while a raster
        # worker still runs: the guarded emit drops the job instead
        # of raising (the aborting pool thread the full suite once
        # hit).
        import PyQt6.sip as sip

        from plugins.PlateQt import RasterBridge, _bridge_emit
        bridge = RasterBridge()
        sip.delete(bridge)  # the C++ side dies outright
        self.assertFalse(_bridge_emit(bridge, "done", ("nav",), ("ticket",)),
                         "a dead bridge's emit reported a landing")
        self.assertFalse(_bridge_emit(bridge, "started", ("ticket",)),
                         "a dead bridge's started emit reported a landing")

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
