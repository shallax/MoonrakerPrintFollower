"""The native renderer's stroke semantics: ONE configured geometry
pen shared by every asset, so the full layer, the prefix and the
grey base can never disagree on physical width.

Plus the rungs under that parity: the transport's publish rule, the
cooperative cancel's own boundaries and the travels overlay the
navigation raster carries."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest

from qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen

    from plugins.PlateQt import (PlateLayer, _paint_segments, png_file,
                                 render_layer_prefix, render_layer_raster,
                                 render_navigation_layer, stamped)


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


def _device_pixel(plot: dict, view: dict, bed_x: float, bed_y: float) -> tuple:
    """The bed point in the rendered device frame — the worker's own
    transform, so the assertions read the pixels it actually painted."""
    backing = float(view["backing"]) if view.get("backing") else min(
        2.0, max(1.0, float(view.get("dpr", 1.0))))
    scale = float(view.get("scale", 1.0)) * backing
    col = int(float(plot["offsetX"]) * scale
              + float(view.get("panX", 0.0)) * backing
              + (bed_x - float(plot["bedXMin"])) * float(plot["sx"]) * scale)
    row = int(float(plot["offsetY"]) * scale
              + float(view.get("panY", 0.0)) * backing
              + (float(plot["bedYMax"]) - bed_y) * float(plot["sy"]) * scale)
    return col, row


def _ink_weight(image: QImage, col: int, row: int, reach: int = 8) -> int:
    """One stroke's total ink: the alphas summed over the rows its
    spread can reach, so two strokes sharing a column stay apart."""
    return sum(image.pixelColor(col, r).alpha()
               for r in range(max(0, row - reach), min(image.height(), row + reach + 1)))


def _inked_pixels(image: QImage, row: int) -> int:
    return sum(1 for col in range(image.width())
               if image.pixelColor(col, row).alpha() > 0)


def _inked_total(image: QImage) -> int:
    return sum(image.pixelColor(col, row).alpha()
               for row in range(image.height())
               for col in range(image.width()))


def _raw_bytes(image: QImage) -> bytes:
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    return bytes(bits)


def _byte_diffs(a: QImage, b: QImage) -> list:
    """The pixels two renders disagree on, as (col, row) pairs.

    The 4x canvas is 1.92 M pixels, so the scan compares raw rows and
    refines only the rows that differ; the delta's anti-aliased seam is
    a real, bounded deviation, never a blanket "approximately equal"."""
    if a.size() != b.size() or a.format() != b.format():
        raise AssertionError("the two canvases are not comparable")
    width, line = a.width(), a.bytesPerLine()
    raw_a, raw_b = _raw_bytes(a), _raw_bytes(b)
    out = []
    for row in range(a.height()):
        start = row * line
        head_a = raw_a[start:start + 4 * width]
        head_b = raw_b[start:start + 4 * width]
        if head_a == head_b:
            continue
        for col in range(width):
            off = 4 * col
            if head_a[off:off + 4] != head_b[off:off + 4]:
                out.append((col, row))
    return out


def _travel_scene() -> dict:
    """The travels scene: one run crossing the live split, one lying
    entirely beyond it and one degenerate single point."""
    run = [[10.0 + i * 5.0, 230.0, float(i)] for i in range(21)]
    crossing = [[10.0 + i * 5.0, 200.0, float(i)] for i in range(20)]
    beyond = [[10.0 + i * 5.0, 150.0, float(10 + i)] for i in range(10)]
    return {"classes": {"WALL-OUTER": [run]},
            "travels": [crossing, [[5.0, 150.0, 0.0]], beyond],
            "travelStarts": [], "travelEnds": [], "motions": 20}


def _travel_view(**overrides) -> dict:
    # The legend toggles are part of the scene's content: an unset
    # showTravels is the "turned off" state, never a default to ride.
    view = _view(lineScale=20.0, showTravels=True)
    view.update(overrides)
    return view


def _ghost(cls: str, bed_y: float, segments: int = 2) -> dict:
    """A ghost layer: short runs of one class, stepped down the bed so
    each ghost's own row stays separately readable."""
    return {"classes": {cls: [[[10.0 + i * 5.0, bed_y - step * 15.0, float(i)]
                               for i in range(6)]
                              for step in range(segments)]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 6}


class _CancelAfter(threading.Event):
    """A cancel that trips on its Nth poll.

    The production cancel is a threading.Event another thread sets,
    and the renders only ever observe it at their own poll points —
    a poll-counted event pins those points deterministically where a
    real second thread would race the assertions. ``once`` trips for
    a single poll: a sticky flag neutralises every later pass by
    itself, so only a one-shot trip shows the render STOPPING at its
    boundary rather than walking on and discarding the ink.
    """

    def __init__(self, polls: int, once: bool = False) -> None:
        super().__init__()
        self.remaining = polls
        self.once = once

    def is_set(self) -> bool:
        self.remaining -= 1
        return self.remaining == 0 if self.once else self.remaining <= 0


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

    def test_a_backward_render_never_copies_the_larger_previous_picture(self):
        # The review's backward-scrub repro: a prefix rendered at 6
        # then re-requested at 2 must not copy the 6-motion image
        # (painting cannot erase the future motions) — the target
        # render starts clean, and the future strokes are gone.
        points_a = [[10.0, 100.0, 0.0], [30.0, 100.0, 1.0],
                    [50.0, 100.0, 2.0], [70.0, 100.0, 3.0]]
        points_b = [[90.0, 100.0, 4.0], [110.0, 100.0, 5.0],
                    [130.0, 100.0, 6.0]]
        payload = {"classes": {"WALL-OUTER": [points_a, points_b]},
                   "travels": [], "travelStarts": [], "travelEnds": [],
                   "motions": 6}
        plot, view = _plot(), _view()
        full = render_layer_prefix(payload, plot, view, 6)
        def alpha_at(bed_x):
            col = int(plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
            row = int(plot["offsetY"] + (plot["bedYMax"] - 100.0) * plot["sy"])
            return full.pixelColor(col, row).alpha()
        self.assertGreater(alpha_at(100.0), 0,
                           "the full render never drew the future stroke")
        backward = render_layer_prefix(payload, plot, view, 2,
                                       previous=full, previous_split=6)
        col = int(plot["offsetX"] + (100.0 - plot["bedXMin"]) * plot["sx"])
        row = int(plot["offsetY"] + (plot["bedYMax"] - 100.0) * plot["sy"])
        self.assertEqual(backward.pixelColor(col, row).alpha(), 0,
                         "the backward render kept a future motion's ink")
        col = int(plot["offsetX"] + (20.0 - plot["bedXMin"]) * plot["sx"])
        self.assertGreater(backward.pixelColor(col, row).alpha(), 0,
                           "the backward render lost the requested history")

    def test_a_forward_extension_leaves_completed_pixels_unchanged(self):
        # The review's alpha-accumulation repro: an incremental
        # extension must equal a fresh render of the same target
        # pixel for pixel — the old bisect re-stroked a completed
        # segment's trailing edge whenever that segment's motions
        # ended before the previous boundary.
        points_a = [[10.0, 100.0, 0.0], [30.0, 100.0, 1.0],
                    [50.0, 100.0, 2.0], [70.0, 100.0, 3.0]]
        points_b = [[90.0, 100.0, 4.0], [110.0, 100.0, 5.0],
                    [130.0, 100.0, 6.0]]
        payload = {"classes": {"WALL-OUTER": [points_a, points_b]},
                   "travels": [], "travelStarts": [], "travelEnds": [],
                   "motions": 6}
        plot, view = _plot(), _view()
        previous = render_layer_prefix(payload, plot, view, 5)
        incremental = render_layer_prefix(payload, plot, view, 6,
                                          previous=previous, previous_split=5)
        fresh = render_layer_prefix(payload, plot, view, 6)
        self.assertTrue(incremental == fresh,
                        "the incremental extension differs from the "
                        "fresh render — completed pixels were "
                        "re-composited")

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


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeRasterTransportTests(unittest.TestCase):
    """The PNG transport's publish rule: a URL exists only for a
    raster that actually reached its immutable name."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="plate-native-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(QColor(255, 0, 0, 255))

    def test_a_failed_save_never_publishes_or_replaces_the_file(self):
        # The write is atomic: the PNG lands under its final name only
        # once the save returned True, so a temp path the writer cannot
        # write must publish nothing, leave no debris behind AND leave
        # the previously published file intact (the reader's immutable
        # source).
        first = png_file(self.image, self.directory, "raster")
        self.assertTrue(first.startswith("file://"),
                        "the transport published no URL for a good save")
        published = QUrl(first).toLocalFile()
        with open(published, "rb") as handle:
            before = handle.read()
        # The writer's own temp name as a symlink onto a directory:
        # QImage cannot open it for writing and reports the failed
        # save, while the clean-up can still remove the temp path.
        temp = os.path.join(self.directory, "raster.png.tmp-%d" % os.getpid())
        os.symlink(self.directory, temp)
        self.assertEqual(png_file(self.image, self.directory, "raster"), "",
                         "a failed save published a URL")
        self.assertFalse(os.path.lexists(temp),
                         "a failed save left its temp path behind")
        with open(published, "rb") as handle:
            self.assertEqual(handle.read(), before,
                             "a failed save replaced the published file")

    def test_a_failed_save_survives_an_uncleanable_temp(self):
        # The clean-up itself can fail (a temp path that is a directory
        # cannot be unlinked): the job still reports the empty
        # transport rather than raising into the pool thread.
        first = png_file(self.image, self.directory, "raster")
        published = QUrl(first).toLocalFile()
        with open(published, "rb") as handle:
            before = handle.read()
        temp = os.path.join(self.directory, "raster.png.tmp-%d" % os.getpid())
        os.makedirs(temp)
        self.assertEqual(png_file(self.image, self.directory, "raster"), "",
                         "an uncleanable failed save published a URL")
        with open(published, "rb") as handle:
            self.assertEqual(handle.read(), before,
                             "an uncleanable failed save replaced the published file")

    def test_an_uncreatable_directory_never_publishes(self):
        # The OSError leg: a directory path under a regular file cannot
        # be created, and the failed encode's clean-up races that same
        # impossibility — the job reports the empty transport instead
        # of raising into the pool thread.
        blocker = os.path.join(self.directory, "blocker")
        with open(blocker, "w") as handle:
            handle.write("x")
        self.assertEqual(png_file(self.image, os.path.join(blocker, "sub"), "raster"), "",
                         "an uncreatable directory published a URL")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeLayerFallbackTests(unittest.TestCase):
    def test_an_unrendered_wrapper_hands_out_null_stand_ins(self):
        # The typed properties are read by the face BEFORE a raster
        # lands: a QImage-typed one must never hand QML None (the live
        # crash) and the width gates must read 0, not raise.
        layer = PlateLayer({"classes": {}, "travels": [], "motions": 0})
        self.assertEqual(layer.travelWidth, 0, "an unrendered travels width read non-zero")
        self.assertEqual(layer.prefixWidth, 0, "an unrendered prefix width read non-zero")
        for name in ("raster", "baseRaster", "travelRaster", "prefixRaster"):
            image = getattr(layer, name)
            self.assertIsNotNone(image, name + " handed QML a None")
            self.assertTrue(image.isNull(), name + " handed out a non-null stand-in")

    def test_an_unrendered_wrapper_reads_invalid(self):
        # The face gates every draw on these: a slot with no image (or
        # no transport behind it) must read invalid, never a str-typed
        # truthiness the engine's bool converter cannot digest.
        layer = PlateLayer({"classes": {}, "travels": [], "motions": 0})
        for name in ("rasterValid", "baseValid", "travelValid", "prefixValid"):
            self.assertIs(getattr(layer, name), False, name + " read valid without a raster")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeSegmentPainterTests(unittest.TestCase):
    """The shared segment walk's own contracts: the class filter, the
    cooperative cancel's verdict and the degenerate segment."""

    def _paint(self, payload: dict, **kwargs):
        image = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        pen = QPen()
        pen.setWidthF(6.0)
        landed = _paint_segments(painter, pen, payload, _plot(), _view(), **kwargs)
        painter.end()
        return landed, image

    @staticmethod
    def _scene() -> dict:
        return {"classes": {"WALL-OUTER": [[[10.0 + i * 5.0, 100.0, float(i)]
                                            for i in range(11)]],
                            "FILL": [[[10.0 + i * 5.0, 200.0, float(i)]
                                      for i in range(11)]]},
                "travels": [], "motions": 11}

    def test_the_class_filter_paints_only_the_named_classes(self):
        plot, view = _plot(), _view()
        wall = _device_pixel(plot, view, 30.0, 100.0)
        fill = _device_pixel(plot, view, 30.0, 200.0)
        landed, image = self._paint(self._scene(), class_names={"WALL-OUTER"})
        self.assertTrue(landed, "the filtered walk reported a cancel")
        self.assertGreater(image.pixelColor(*wall).alpha(), 0,
                           "the named class was never walked")
        self.assertEqual(image.pixelColor(*fill).alpha(), 0,
                         "the walk painted a class outside its filter")
        # The control: unfiltered, both classes reach the canvas.
        _landed, both = self._paint(self._scene())
        self.assertGreater(both.pixelColor(*fill).alpha(), 0,
                           "the control walk never painted the second class")

    def test_a_cooperative_cancel_paints_nothing_and_reports_it(self):
        # The walk's verdict is the caller's cancellation signal: a
        # superseded job must report False AND leave no ink behind.
        cancel = threading.Event()
        cancel.set()
        landed, image = self._paint(self._scene(), cancel=cancel)
        self.assertFalse(landed, "a cancelled walk reported a landing")
        self.assertEqual(_inked_total(image), 0, "a cancelled walk painted ink")
        landed, image = self._paint(self._scene())
        self.assertTrue(landed, "an uncancelled walk reported a cancel")
        self.assertGreater(_inked_total(image), 0, "the control walk painted nothing")

    def test_a_degenerate_segment_paints_no_ink(self):
        # A one-point run is a move, not a stroke: it must add nothing
        # to the picture (the shortest path is a no-op here).
        scene = self._scene()
        scene["classes"]["SUPPORT"] = [[[10.0, 150.0, 0.0]]]
        _landed, with_point = self._paint(scene)
        _landed, without = self._paint(self._scene())
        self.assertGreater(_inked_total(without), 0, "the control painted nothing")
        self.assertEqual(with_point, without,
                         "a one-point segment changed the painted picture")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeIncrementalPrefixTests(unittest.TestCase):
    """The prefix's interval rule (first <= edge.motion < split) and
    the cooperative cancel that rides it."""

    @staticmethod
    def _segments() -> dict:
        return {"classes": {"WALL-OUTER": [[[10.0, 100.0, 0.0], [60.0, 100.0, 1.0]],
                                           [[10.0, 80.0, 0.0], [60.0, 80.0, 1.0]]]},
                "travels": [], "motions": 2}

    def test_a_cancel_between_segments_keeps_the_completed_ones(self):
        # The cancel is polled at each segment boundary: tripping at the
        # second segment's gate keeps the first segment's strokes and
        # drops the second, so a superseded job never merges a partial
        # segment into the committed picture.
        plot, view = _plot(), _view()
        first = _device_pixel(plot, view, 30.0, 100.0)
        second = _device_pixel(plot, view, 30.0, 80.0)
        kept = render_layer_prefix(self._segments(), plot, view, 10, cancel=_CancelAfter(3))
        self.assertGreater(kept.pixelColor(*first).alpha(), 0,
                           "the completed segment was lost with the cancelled one")
        self.assertEqual(kept.pixelColor(*second).alpha(), 0,
                         "the cancelled segment still drew")
        # One poll earlier the cancel lands on the FIRST segment's gate:
        # nothing is painted at all.
        dropped = render_layer_prefix(self._segments(), plot, view, 10, cancel=_CancelAfter(2))
        self.assertEqual(_inked_total(dropped), 0,
                         "a cancel at the first segment's gate still painted")

    def test_a_degenerate_segment_leaves_no_ink(self):
        scene = self._segments()
        scene["classes"]["WALL-OUTER"].append([[40.0, 60.0, 0.0]])
        plot, view = _plot(), _view()
        with_point = render_layer_prefix(scene, plot, view, 10)
        plain = render_layer_prefix(self._segments(), plot, view, 10)
        self.assertGreater(_inked_total(plain), 0, "the control painted nothing")
        self.assertEqual(with_point, plain,
                         "a one-point segment changed the prefix's picture")

    def test_an_edge_below_the_incremental_lower_bound_is_never_stroked(self):
        # The incremental walk re-strokes nothing below its lower bound
        # even where the segment's motion indices are not monotone: the
        # edge INTO a below-bound motion stays out of the delta rather
        # than re-compositing completed geometry.
        payload = {"classes": {"WALL-OUTER": [[[10.0, 100.0, 7.0], [30.0, 100.0, 6.0],
                                               [50.0, 100.0, 4.0]]]},
                   "travels": [], "motions": 8}
        plot, view = _plot(), _view()
        blank = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
        blank.fill(QColor(0, 0, 0, 0))
        # A hand-built base must carry the context stamp: the copy is
        # refused without it, and a refused copy is a whole walk (which
        # would stroke the below-bound edge and read as a re-stroke).
        # `stamped` is the supported way to assert that a picture of
        # one's own was drawn at this plot and this view.
        stamped(blank, plot, view)
        delta = render_layer_prefix(payload, plot, view, 10,
                                    previous=blank, previous_split=5)
        above = _device_pixel(plot, view, 20.0, 100.0)
        below = _device_pixel(plot, view, 40.0, 100.0)
        self.assertGreater(delta.pixelColor(*above).alpha(), 0,
                           "the edge inside the lower bound was never stroked")
        self.assertEqual(delta.pixelColor(*below).alpha(), 0,
                         "an edge below the lower bound was re-stroked")
        # The control: the full walk reaches both edges.
        full = render_layer_prefix(payload, plot, view, 10)
        self.assertGreater(full.pixelColor(*below).alpha(), 0,
                           "the control walk never painted the second edge")

    def test_a_copy_from_another_view_never_composites_into_the_new_one(self):
        # The stale-previous repro, and the live symptom's own shape:
        # a prefix baked at one view, then a second bake at a PANNED
        # view handed the first. The copy carries the old pane's ink,
        # so a delta taken here leaves the printed history behind by
        # the whole pan while the fresh tail draws at the new one, so
        # the ink lands tens of pixels from where the view puts it. The
        # assertion is on the composite's own pixels, never on a
        # helper's return.
        payload = _payload()
        plot = _plot()
        before, after = _view(lineScale=8.0), _view(lineScale=8.0, panX=40.0)
        held = render_layer_prefix(payload, plot, before, 12)
        clean = render_layer_prefix(payload, plot, after, 16)
        mixed = render_layer_prefix(payload, plot, after, 16,
                                    previous=held, previous_split=12)
        # The two views genuinely disagree about where the ink lies,
        # or the parity below would prove nothing.
        self.assertNotEqual(_byte_diffs(clean,
                                        render_layer_prefix(payload, plot,
                                                            before, 16)), [],
                            "the two views paint the same picture")
        self.assertEqual(_byte_diffs(clean, mixed), [],
                         "the panned bake kept the old pane's ink")
        old = _device_pixel(plot, before, 10.0, 100.0)
        new = _device_pixel(plot, after, 10.0, 100.0)
        self.assertEqual(mixed.pixelColor(*old).alpha(), 0,
                         "the printed history stayed at the old pane")
        self.assertGreater(mixed.pixelColor(*new).alpha(), 0,
                           "the panned picture lost the printed history")

    def test_a_long_segment_stops_at_the_cancel_poll(self):
        # A segment longer than the poll interval is abandoned whole
        # when the cancel trips inside it — the superseded job publishes
        # no half-drawn run (the control proves the tail was reachable).
        points = [[10.0 + i, 100.0, float(i)] for i in range(600)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [], "motions": 600}
        plot, view = _plot(), _view(width=1300)
        head = _device_pixel(plot, view, 450.0, 100.0)
        tail = _device_pixel(plot, view, 590.0, 100.0)
        fresh = render_layer_prefix(payload, plot, view, 1000)
        self.assertGreater(fresh.pixelColor(*tail).alpha(), 0,
                           "the control walk never reached the tail")
        cancelled = render_layer_prefix(payload, plot, view, 1000,
                                        cancel=_CancelAfter(3))
        self.assertEqual(cancelled.pixelColor(*head).alpha(), 0,
                         "the abandoned segment left its already-walked head")
        self.assertEqual(cancelled.pixelColor(*tail).alpha(), 0,
                         "the abandoned segment reached its tail")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeNavigationTravelTests(unittest.TestCase):
    """The navigation scene's travels pass and its three early
    exits."""

    def test_the_travels_ride_the_visual_ratio_and_the_split(self):
        plot, view = _plot(), _travel_view()
        image = render_navigation_layer({"current": _travel_scene()}, plot, view, split=10)
        # Below the live split the crossing travel draws its own token.
        centre = image.pixelColor(*_device_pixel(plot, view, 40.0, 200.0))
        self.assertEqual(centre.alpha(), 255, "the split-crossing travel drew no ink")
        self.assertGreater(centre.blue(), centre.red() + 30,
                           "the travel drew a foreign colour")
        self.assertGreater(centre.red(), centre.green() + 20,
                           "the travel drew a foreign colour")
        # The motion at the split's boundary ends the travel: its edge
        # into the next motion never draws.
        self.assertEqual(
            image.pixelColor(*_device_pixel(plot, view, 70.0, 200.0)).alpha(), 0,
            "a travel drew past the live split")
        # The beyond-split travel and the one-point travel draw nothing:
        # their row stays empty while the full-layer render (no split)
        # inks the same row.
        row = _device_pixel(plot, view, 0.0, 150.0)[1]
        self.assertEqual(_inked_pixels(image, row), 0,
                         "a travel beyond the split reached the canvas")
        full = render_navigation_layer({"current": _travel_scene()}, plot, _travel_view())
        self.assertGreater(_inked_pixels(full, row), 0,
                           "the control scene never drew the travel the split cut")
        # The pen: the travel stroke carries the geometry pen's width
        # times the shared visual ratio, and the ratio rides the view.
        class_col, class_row = _device_pixel(plot, view, 30.0, 230.0)
        class_ink = _ink_weight(image, class_col, class_row)
        travel_col, travel_row = _device_pixel(plot, view, 30.0, 200.0)
        travel_ink = _ink_weight(image, travel_col, travel_row)
        self.assertGreater(class_ink, 0, "the printed layer drew no ink")
        self.assertAlmostEqual(travel_ink, class_ink * 0.7, delta=0.15 * class_ink,
                               msg="the travel stroke ignored the visual ratio")
        forced = render_navigation_layer(
            {"current": _travel_scene()}, plot, _travel_view(travelVisualRatio=1.0), split=10)
        self.assertAlmostEqual(_ink_weight(forced, travel_col, travel_row), class_ink,
                               delta=0.15 * class_ink,
                               msg="a forced visual ratio never reached the travel pen")

    def test_a_cancelled_ghost_pass_stops_before_the_next_ghost(self):
        # The cancel is polled between the ghost passes: tripping after
        # the previous ghost leaves that ghost's faint ink in place and
        # returns before the next ghost or the current layer is walked.
        plot, view = _plot(), _view()
        cancel = _CancelAfter(3, once=True)
        image = render_navigation_layer(
            {"prev": _ghost("FILL", 180.0), "next": _ghost("SKIN", 130.0),
             "current": _travel_scene()}, plot, view, split=10, cancel=cancel)
        self.assertEqual(cancel.remaining, 0, "the cancel never reached the ghost boundary")
        self.assertGreater(image.pixelColor(*_device_pixel(plot, view, 25.0, 180.0)).alpha(), 0,
                           "the completed ghost pass was dropped with the render")
        self.assertEqual(image.pixelColor(*_device_pixel(plot, view, 25.0, 130.0)).alpha(), 0,
                         "the next ghost was walked after the cancel")
        self.assertEqual(image.pixelColor(*_device_pixel(plot, view, 25.0, 230.0)).alpha(), 0,
                         "the current layer was walked after the cancel")

    def test_a_cancelled_next_pass_stops_before_the_current_layer(self):
        plot, view = _plot(), _view()
        cancel = _CancelAfter(3, once=True)
        image = render_navigation_layer(
            {"prev": None, "next": _ghost("SKIN", 130.0), "current": _travel_scene()},
            plot, view, split=10, cancel=cancel)
        self.assertEqual(cancel.remaining, 0, "the cancel never reached the ghost boundary")
        self.assertGreater(image.pixelColor(*_device_pixel(plot, view, 25.0, 130.0)).alpha(), 0,
                           "the completed next pass was dropped with the render")
        self.assertEqual(image.pixelColor(*_device_pixel(plot, view, 25.0, 230.0)).alpha(), 0,
                         "the current layer was walked after the cancel")

    def test_a_navigation_scene_without_a_current_layer_returns_the_grid_alone(self):
        # The interaction scene resolves before the layer exists: the
        # render hands back its canvas, never a partially-walked one.
        plot, view = _plot(), _travel_view()
        image = render_navigation_layer({"prev": None, "next": None, "current": None},
                                        plot, view, split=10)
        self.assertEqual((image.width(), image.height()), (view["width"], view["height"]),
                         "the empty scene returned a differently-sized raster")
        self.assertEqual(_inked_total(image), 0,
                         "a scene without a current layer painted toolpath")

    def test_a_cancelled_render_keeps_the_printed_layer_and_drops_the_travels(self):
        # The travels are the LAST pass: a cancel tripped at their own
        # gate returns the printed layer with not one travel line on it.
        plot, view = _plot(), _travel_view()
        image = render_navigation_layer({"current": _travel_scene()}, plot, view,
                                        cancel=_CancelAfter(2))
        self.assertEqual(image.pixelColor(*_device_pixel(plot, view, 25.0, 230.0)).alpha(), 255,
                         "the cancelled render lost the printed layer")
        self.assertEqual(image.pixelColor(*_device_pixel(plot, view, 25.0, 200.0)).alpha(), 0,
                         "a cancelled render drew its travels")

    def test_a_render_that_raises_ends_every_painter_it_opened(self):
        # The fourth early exit: an exception. A painter left active
        # when its frame dies takes the canvas with it — the locals
        # die in assignment order, the canvas first, so the device is
        # destroyed under a live painter and the process dumps core
        # (the worker's failure lane).
        import plugins.PlateQt as plate

        original = plate.QPainter
        opened = []

        class _RecordingPainter(original):
            def __init__(self, image):
                super().__init__(image)
                self.ended = False
                opened.append(self)

            def end(self):
                self.ended = True
                return super().end()

        # A plot without bed bounds: the transform's KeyError, raised
        # with the painter already open on the canvas.
        plate.QPainter = _RecordingPainter
        try:
            with self.assertRaises(KeyError):
                render_navigation_layer({"current": _payload()},
                                        {"offsetX": 0.0, "offsetY": 0.0, "sx": 1.0, "sy": 1.0},
                                        _view())
        finally:
            plate.QPainter = original
        self.assertTrue(opened, "the render opened no painter to check")
        self.assertTrue(all(recorder.ended for recorder in opened),
                        "a raised render left its painter active on the canvas")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeIncrementalNavigationTests(unittest.TestCase):
    """The incremental nav bake: what the copied composite must
    reproduce, what it may never lose, and the demands that bake
    whole instead."""

    # The resume vertex's own footprint: the delta's start cap
    # re-covers a few anti-aliased fringe pixels the copy already
    # inked at partial coverage (measured 6 per fringe row per seam
    # at the 4x backing).
    SEAM_PIXELS = 16

    def _bounds(self, image, whole, diffs, allowance):
        """Assert the delta's deviation is the seam and nothing else:
        bounded in count, never LESS ink, and always inside the
        stroke's own fringe (a deviation out in clear space would be a
        mark the picture never had)."""
        self.assertLessEqual(len(diffs), allowance,
                             "the incremental bake diverged from the full one")
        for col, row in diffs:
            self.assertGreaterEqual(
                image.pixelColor(col, row).alpha(),
                whole.pixelColor(col, row).alpha(),
                "the incremental bake took ink away at (%d, %d)" % (col, row))
            neighbours = [(col - 1, row), (col + 1, row),
                          (col, row - 1), (col, row + 1)]
            self.assertTrue(
                any(0 <= c < whole.width() and 0 <= r < whole.height()
                    and whole.pixelColor(c, r).alpha() > 0
                    for c, r in neighbours),
                "the deviation at (%d, %d) reached clear space" % (col, row))

    def test_the_incremental_bake_reproduces_the_full_composite(self):
        # The delta's parity: the copied picture already carries the
        # grid, the ghosts, the grey base and the printed prefix, so the
        # strokes it adds are the full bake's own — but for the resume
        # vertex, whose re-covered fringe is the whole measured
        # deviation (22 pixels of this canvas's 1.92 M).
        plot, view = _plot(), _travel_view(lineScale=8.0, backing=4.0)
        window = {"prev": None, "next": None, "current": _travel_scene()}
        base = render_navigation_layer(window, plot, view, split=10)
        incremental = render_navigation_layer(window, plot, view, split=15,
                                              previous=base, previous_split=10)
        whole = render_navigation_layer(window, plot, view, split=15)
        diffs = _byte_diffs(whole, incremental)
        # Liveness: the resume vertex's re-covered fringe is the delta's
        # own signature. A refused copy bakes whole, which reproduces
        # the full bake EXACTLY and would slip past the upper bound
        # below -- so without this the context guard could refuse every
        # delta in the codebase and this test would still pass.
        self.assertGreater(len(diffs), 0,
                           "the bake never took the delta: the copy was "
                           "refused, so every refresh is a whole bake")
        self._bounds(incremental, whole, diffs, 4 * self.SEAM_PIXELS)
        # The picture the delta must carry, not merely its parity with
        # the full bake: the class colour below the live split, the grey
        # silhouette beyond it, and the travel token ending at the split.
        tail = incremental.pixelColor(*_device_pixel(plot, view, 100.0, 230.0))
        self.assertGreater(tail.alpha(), 0,
                           "the delta lost the grey base's silhouette")
        self.assertLess(abs(tail.red() - tail.green()), 20,
                        "the delta printed past the live split")
        under = incremental.pixelColor(*_device_pixel(plot, view, 40.0, 200.0))
        self.assertEqual(under.alpha(), 255, "the delta lost the travel below the split")
        self.assertGreater(under.blue(), under.red() + 30,
                           "the delta's travel drew a foreign colour")
        self.assertEqual(
            incremental.pixelColor(*_device_pixel(plot, view, 100.0, 200.0)).alpha(), 0,
            "the delta drew a travel past the live split")

    def test_a_completed_layer_bakes_whole(self):
        # The grey base lies UNDER the printed prefix, so its ink shows
        # through the coloured stroke's fringes. The completed layer is
        # the full-layer branch — no grey base — and that picture cannot
        # be reached by adding strokes: the copy would keep the tint
        # (measured 1412 fringe pixels of this canvas when it did). The
        # completion bakes whole and lands the full bake's pixels
        # exactly, with no seam at all.
        plot, view = _plot(), _view(lineScale=8.0, backing=4.0)
        window = {"prev": None, "next": None, "current": _payload()}
        base = render_navigation_layer(window, plot, view, split=16)
        completion = render_navigation_layer(window, plot, view, split=20,
                                             previous=base, previous_split=16)
        whole = render_navigation_layer(window, plot, view, split=20)
        self.assertEqual(_byte_diffs(whole, completion), [],
                         "the completed layer's picture kept the copied "
                         "grey base's tint instead of baking whole")
        # The control: the same copy under a PARTIAL demand is a
        # stroke-add and does differ — the demand's own branch decides,
        # not the presence of a copy.
        partial = render_navigation_layer(window, plot, view, split=19,
                                          previous=base, previous_split=16)
        self.assertTrue(_byte_diffs(render_navigation_layer(
            window, plot, view, split=19), partial) != [],
            "the partial demand never took the copy at all")

    def test_a_print_ordered_graze_band_bakes_exactly(self):
        # The payload shape the model builds: motion indices are GLOBAL
        # and monotone in print order (motion_edges numbers a layer's
        # motions continuously), so a class's strokes are added after
        # the geometry they overlap — which is exactly the order the
        # delta composites in. The pin is the one shape where the order
        # could disagree: two classes whose presented strokes graze,
        # the later-printed one carrying the delta's range. Its new
        # strokes land over the copy's pixels of the earlier class,
        # and the full bake's later class wins the band in both.
        #
        # Where a class's LATER run grazes a class the paint loop
        # strokes after it, the copy can hold pixels the new strokes
        # would have gone under and the band takes the earlier class's
        # colour until the next whole bake. That shape needs runs
        # interleaved against the class order; it is not reachable here
        # and is recorded in the commit message rather than pinned as
        # an accepted deviation.
        plot, view = _plot(), _view(lineScale=8.0, backing=4.0)
        wall = [[20.0 + i * 20.0, 200.0, float(i)] for i in range(10)]
        fill = [[20.0 + i * 20.0, 200.3, float(10 + i)] for i in range(10)]
        window = {"prev": None, "next": None,
                  "current": {"classes": {"WALL-INNER": [wall], "FILL": [fill]},
                              "travels": [], "travelStarts": [],
                              "travelEnds": [], "motions": 20}}
        base = render_navigation_layer(window, plot, view, split=12)
        incremental = render_navigation_layer(window, plot, view, split=18,
                                              previous=base, previous_split=12)
        whole = render_navigation_layer(window, plot, view, split=18)
        self._bounds(incremental, whole, _byte_diffs(whole, incremental),
                     4 * self.SEAM_PIXELS)
        # The band where both strokes cover: the later-printed class
        # (FILL, #1976d2) wins it in the full bake, and the delta must
        # land that same pixel — the copy's green wall is what an
        # ordering slip would leave showing.
        band = _device_pixel(plot, view, 100.0, 200.0)
        held = incremental.pixelColor(*band)
        self.assertEqual(held.getRgb(), whole.pixelColor(*band).getRgb(),
                         "the delta's graze band holds a foreign colour")
        self.assertGreater(held.blue(), held.green() + 30,
                           "the graze band kept the class the copy held")

    def test_a_backward_move_bakes_whole(self):
        # Painting cannot erase: a copy baked BEYOND the demand holds
        # the printed colour on motions the demand's picture must show
        # as the grey silhouette only. The guard refuses the delta (the
        # prefix path's own rule), so the backward move lands the full
        # bake's pixels exactly.
        plot, view = _plot(), _view(lineScale=8.0, backing=4.0)
        window = {"prev": None, "next": None, "current": _travel_scene()}
        base = render_navigation_layer(window, plot, view, split=19)
        held = base.pixelColor(*_device_pixel(plot, view, 100.0, 230.0))
        self.assertGreater(held.red(), held.green() + 50,
                           "the control's tail was never coloured")
        backward = render_navigation_layer(window, plot, view, split=12,
                                           previous=base, previous_split=19)
        whole = render_navigation_layer(window, plot, view, split=12)
        self.assertEqual(_byte_diffs(whole, backward), [],
                         "the backward move kept a painted motion's ink")
        tail = backward.pixelColor(*_device_pixel(plot, view, 100.0, 230.0))
        self.assertGreater(tail.alpha(), 0,
                           "the backward move dropped the silhouette")
        self.assertLess(abs(tail.red() - tail.green()), 20,
                        "the backward move kept the printed colour")

    def test_a_foreign_copy_bakes_whole(self):
        # The delta's canvas is the same render context at the same
        # size: a copy from another view or a null one is refused, so a
        # resize can never composite a stale-sized picture into the new
        # canvas at the wrong places.
        plot, view = _plot(), _view(lineScale=8.0, backing=4.0)
        window = {"prev": None, "next": None, "current": _payload()}
        other = _view(width=200, height=150, lineScale=8.0, backing=4.0)
        foreign = render_navigation_layer(window, plot, other, split=10)
        whole = render_navigation_layer(window, plot, view, split=12)
        mixed = render_navigation_layer(window, plot, view, split=12,
                                        previous=foreign, previous_split=10)
        self.assertEqual(_byte_diffs(whole, mixed), [],
                         "a differently-sized copy was composited anyway")
        null_copy = render_navigation_layer(window, plot, view, split=12,
                                            previous=QImage(), previous_split=10)
        self.assertEqual(_byte_diffs(whole, null_copy), [],
                         "a null copy was composited anyway")

    def test_a_same_sized_copy_from_another_view_bakes_whole(self):
        # The size check above cannot see a camera change: a pan keeps
        # the canvas identical in size while moving every pixel the
        # copy holds, so a delta taken across it leaves the printed
        # history and the travels at the old pane, tens of pixels from
        # where the view puts them (the furthest move accumulates the
        # whole shift). The context stamp is what refuses it.
        plot = _plot()
        view = _travel_view(lineScale=8.0, backing=4.0)
        moved = _travel_view(lineScale=8.0, backing=4.0, panX=30.0)
        window = {"prev": None, "next": None, "current": _travel_scene()}
        held = render_navigation_layer(window, plot, view, split=12)
        clean = render_navigation_layer(window, plot, moved, split=16)
        mixed = render_navigation_layer(window, plot, moved, split=16,
                                        previous=held, previous_split=12)
        # The travel raster's own pixels: the assertion is worthless
        # unless the two views really do disagree about where they lie.
        old = _device_pixel(plot, view, 60.0, 200.0)
        new = _device_pixel(plot, moved, 60.0, 200.0)
        settled = render_navigation_layer(window, plot, view, split=16)
        self.assertGreater(clean.pixelColor(*new).blue(), 0,
                           "the moved view never drew the travels")
        self.assertNotEqual(_byte_diffs(clean, settled), [],
                            "the two views paint the same picture")
        self.assertEqual(_byte_diffs(clean, mixed), [],
                         "the panned composite kept the old pane's ink")
        self.assertGreater(mixed.pixelColor(*new).blue(), 0,
                           "the moved composite lost the travels")
        self.assertEqual(mixed.pixelColor(*old).getRgb(),
                         clean.pixelColor(*old).getRgb(),
                         "the travels stayed at the old pane")

    def test_a_chain_of_deltas_never_takes_ink_away(self):
        # The live cadence is a CHAIN: every window's bake resumes from
        # the composite the last one committed. Each seam re-covers a
        # few fringe pixels, so the chain's ink may only ever GROW — a
        # pixel that lost ink is a hole in the warm raster, and a
        # deviation far from the strokes would be a mark the scene never
        # had.
        steps = 18
        plot, view = _plot(), _view(lineScale=8.0, backing=4.0)
        window = {"prev": None, "next": None, "current": _payload()}
        chain = render_navigation_layer(window, plot, view, split=1)
        for step in range(2, steps + 1):
            chain = render_navigation_layer(window, plot, view, split=step,
                                            previous=chain,
                                            previous_split=step - 1)
        whole = render_navigation_layer(window, plot, view, split=steps)
        self._bounds(chain, whole, _byte_diffs(whole, chain),
                     self.SEAM_PIXELS * steps)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeRasterCancelTests(unittest.TestCase):
    """The three-sibling job's travels pass: cancelled and degenerate."""

    @staticmethod
    def _scene() -> dict:
        # The travel sits inside the canvas, so a job that published it
        # is visibly distinct from one that dropped it.
        payload = _payload()
        payload["travels"] = [[[10.0, 150.0, 0.0], [60.0, 150.0, 1.0]]]
        return payload

    def test_a_cancelled_raster_job_publishes_no_travels(self):
        # Every sibling answers to the same cancel: the coloured raster
        # stops at its first segment and the travels pass returns the
        # blank canvas it had just allocated.
        cancel = threading.Event()
        cancel.set()
        coloured, _grey, travels = render_layer_raster(
            self._scene(), _plot(), _view(), cancel=cancel)
        self.assertEqual((travels.width(), travels.height()), (400, 300),
                         "the cancelled travels pass published a foreign canvas")
        self.assertEqual(_inked_total(travels), 0,
                         "a cancelled job drew its travels")
        self.assertEqual(_inked_total(coloured), 0,
                         "a cancelled job drew its toolpath")

    def test_a_degenerate_travel_leaves_the_travel_canvas_empty(self):
        payload = _payload()
        payload["travels"] = [[[10.0, 150.0, 0.0]]]
        _coloured, _grey, travels = render_layer_raster(payload, _plot(), _view())
        self.assertEqual(_inked_total(travels), 0,
                         "a one-point travel drew ink into the travel raster")
        _coloured, _grey, inked = render_layer_raster(self._scene(), _plot(), _view())
        self.assertGreater(_inked_total(inked), 0,
                           "the control travel never reached the travel raster")


if __name__ == "__main__":
    unittest.main()
