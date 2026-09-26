"""The zoom-precision raster contract (4.6.0): toolpath ink is
PHYSICAL bed-space geometry, never a screen-constant pixel width.

The live failure: at 100% the follower drew its 0.7 px strokes
unscaled, so one pixel represented ~0.48 mm of bed — the Day text's
nearby strokes merged and dense SKIN read as solid. The fix defines
the stroke in bed millimetres (nominal 0.2 mm, the slicer's line at
this print's scale, calibrated so ~300% keeps the previously-good
screen weight) multiplied by the live plot's px-per-mm and the view
zoom. At 100% the stroke is legitimately subpixel; there is no 1 px
floor. Ghost, pending and printed channels share ONE width helper;
screen-space annotations (the toolhead dot, the travel glyphs, the
grid) never read it.

The oracle is the product's OWN width calculation: the QML's
toolpathWidthPx / travelWidthPx helpers — the ONE physical
stroke-width formula every painter reads — invoked through the real
engine. ZoomInkMassTests pins the contract through it: the physical
subpixel value at 100% (a hidden max(1, w) floor would read 1 px),
the linear zoom law, the Day-scale gap never bridging at the
production lineScale, the travel's visual ratio, and the compact
boost's named constant.

The pixel-level zoom measurements the harness can and cannot make
are documented from the 2026-09-22 probes: the warm navigation
raster's zoomed picture lands in the grabbed frame only under an
order-dependent composition (a settled mount, THEN the property
flips and a re-scrub with a partial vector prefix — the probe rig),
and where it landed it measured the physical law exactly (a 20 px
stroke at 305% of an 8 px 100% stroke). The mass-over-a-fixed-region
oracle is defeated by the warm raster's washed ink, so the contract
rests on the helpers the painters themselves read.

Two mounts cover the contract:
- ZoomStrokeTests: a single small face-only window for the
  width-share and miter regressions (the window is coloured like the
  monitor's card: the face paints no backdrop, and the capture
  theme's class colours are near-white and invisible on a white
  window).
- ZoomInkMassTests: the MONITOR harness with the width helpers.

All geometry here is prepared payload data — the G-code walk, arc
tessellation and feature topology are covered by the geometry suites.
"""

import os
import pathlib
import tempfile

from qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from PyQt6.QtCore import QPointF, QRect
    from PyQt6.QtQuick import QQuickItem

try:
    from . import test_qml_real_engine as _parent
except ImportError:
    import test_qml_real_engine as _parent

# NOTE: never alias the parent's TestCase classes into this module's
# namespace — unittest collects every TestCase subclass found here,
# and aliases would drag the parent suite into this file's discovery.


class ZoomStrokeTests(_parent.RealEngineTestCase):
    """The physical-width contract, measured through the real painter."""

    # A single horizontal WALL-OUTER line across the bed's top-left
    # corner region: bed-x 10..45 mm stays visible at every zoom the
    # suite sweeps (the face shows bed-x 0..83 mm at 5x).
    LINE_PAYLOAD = {
        "available": True, "reason": "",
        "layers": {
            "prev": None,
            "current": {
                "classes": {"WALL-OUTER": [[[10.0, 203.0, 1.0], [45.0, 203.0, 1.0]]]},
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": 2,
            },
            "next": None,
        },
        "split": 2, "method": "motion index", "anchor": 0,
    }

    # The empty layer: the grid's own ink, for subtraction.
    EMPTY_PAYLOAD = {
        "available": True, "reason": "",
        "layers": {
            "prev": None,
            "current": {
                "classes": {}, "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": 0,
            },
            "next": None,
        },
        "split": 0, "method": "motion index", "anchor": 0,
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._printer = _parent.PlateFaceRenderTests._printer()  # retained: the GC trap
        from PyQt6.QtQml import QQmlComponent
        from PyQt6.QtCore import QUrl
        import pathlib
        ROOT = pathlib.Path(__file__).resolve().parents[1]
        comp = QQmlComponent(cls.engine)
        comp.loadUrl(QUrl.fromLocalFile(str(ROOT / "plugins" / "PlateProgressFace.qml")))
        cls.face = comp.create()
        assert cls.face is not None, _parent.qml_error_report(comp)
        # The model must precede the sizing: PlateCanvas replots on
        # every resize and needs the bed dimensions at that moment.
        cls.face.setProperty("printerModel", cls._printer)
        cls.window = cls._window_for(cls.face)
        cls.face.setProperty("dot", None)
        cls.face.setProperty("showBase", False)
        cls.face.setProperty("showPrevious", False)
        cls.face.setProperty("showNext", False)
        cls.face.setProperty("showTravels", False)
        cls.face.setProperty("lineScale", 1.0)
        cls.face.setProperty("viewScale", 1.0)
        cls._pump_settle()
        cls._plot = cls.face.findChild(QQuickItem, "moonrakerPlateCanvas").property("_plot")
        assert cls._plot is not None, "the bed mapping never built"
        cls.mapping = _parent.PlateFaceRenderTests._mapping(cls._plot)

    @staticmethod
    def _window_for(face):
        from PyQt6.QtGui import QColor
        from PyQt6.QtQuick import QQuickWindow
        window = QQuickWindow()
        # The face paints no backdrop; the monitor's dark card does.
        # On a white window the capture theme's near-white class
        # colours are invisible (delta 7), so the window stands in
        # for the card.
        window.setColor(QColor(30, 30, 30))
        window.resize(420, 420)
        face.setParentItem(window.contentItem())
        face.setWidth(420)
        face.setHeight(420)
        window.show()
        return window

    @classmethod
    def _pump_settle(cls):
        for _ in range(40):
            cls.app.processEvents()

    def setUp(self):
        # Reset the face to the default view state between tests.
        self.face.setProperty("dot", None)
        self.face.setProperty("showBase", False)
        self.face.setProperty("showPrevious", False)
        self.face.setProperty("showNext", False)
        self.face.setProperty("showTravels", False)
        self.face.setProperty("lineScale", 1.0)
        self.face.setProperty("viewScale", 1.0)
        # The grid flag is the FACE's (setUpClass shares one mount), so
        # a test that turns it off must not leave the next one without
        # a grid it never asked to lose.
        canvas = self.face.findChild(QQuickItem, "moonrakerPlateCanvas")
        if canvas is not None:
            canvas.setProperty("showGrid", True)
        self._pump_settle()

    @staticmethod
    def _delta(value, background):
        return max(abs(((value >> shift) & 0xFF) - ((background >> shift) & 0xFF))
                   for shift in (0, 8, 16))

    def _ink_mass(self, image, rect):
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        total = 0
        for row in range(rect.top(), rect.bottom() + 1):
            for col in range(rect.left(), rect.right() + 1):
                total += self._delta(image.pixel(ox + col, oy + row), self._background)
        return total

    def _set_payload(self, payload, arrived=None):
        # A different anchor forces the face's full stack reset, so a
        # payload swap never paints over stale pixels. The settled
        # frame is RETURNED: a caller that grabs again gets a frame the
        # predicate never cleared, which is how a baseline ends up
        # without the ink its diff assumes.
        self.face.setProperty("progress", dict(payload, anchor=-1))
        self._pump_settle()
        self.face.setProperty("progress", payload)
        return self._settled_grab(arrived)

    def _grid_off(self):
        """Take the bed graphic out of the frame.

        The stroke measurement should see the stroke. The oracle this
        replaces subtracted one asynchronously painted frame from
        another, so it measured whatever else the face was drawing that
        moved between the two — the zoom scope's slide, most of all.
        Removing the grid takes the largest such element out and leaves
        the chrome boxes to cover the rest."""
        canvas = self.face.findChild(QQuickItem, "moonrakerPlateCanvas")
        self.assertIsNotNone(canvas, "the face has no plate canvas")
        # Checked, not discarded: setProperty on a property the engine
        # does not know (a stale compiled QML) returns False and warns,
        # and the test would then measure a grid it believes is gone.
        self.assertTrue(canvas.setProperty("showGrid", False),
                        "the canvas has no showGrid property")
        self._pump_settle()
        return canvas

    @staticmethod
    def _segment_distance(px, py, ax, ay, bx, by):
        """The distance from (px, py) to the segment (ax, ay)-(bx, by),
        clamped to the segment's ends: the round-join envelope of a
        polyline is exactly "within half the stroke width of it"."""
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span <= 0.0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
        return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5

    def _distance_to_path(self, px, py, path):
        return min(self._segment_distance(px, py, path[i][0], path[i][1],
                                          path[i + 1][0], path[i + 1][1])
                   for i in range(len(path) - 1))

    def _device_origin(self):
        """The face's origin in DEVICE pixels, and the ratio that maps
        scene to device. A grab returns device pixels while the plot's
        mapping is in logical ones, so at any ratio but 1.0 sampling
        the two as one reads the wrong region entirely."""
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        ratio = float(self.window.devicePixelRatio())
        return (origin.x() * ratio, origin.y() * ratio, ratio)

    def _chrome_boxes(self):
        """The rectangles of chrome the stroke measurement must not
        read as ink, in device pixels.

        The zoom scope is a 38 px rail down the face with its own
        readout and marker, and it carries a `Behavior on x` — so it
        is both ink and MOVING ink. It has nothing to do with the
        stroke, and the box comes from the LIVE item rather than a
        constant, so the exclusion follows it wherever it is parked."""
        ox, oy, ratio = self._device_origin()
        boxes = []
        for name in ("moonrakerPlateZoomScope",):
            item = self.face.findChild(QQuickItem, name)
            if item is None or not item.isVisible():
                continue
            origin = item.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
            # Two pixels of margin: the item's own border and the
            # antialiasing of its edge are outside its box, and the
            # isolation predicate asks for zero stray samples — a
            # half-covered edge pixel would hold it off forever.
            pad = 2 * ratio
            boxes.append((int(origin.x() * ratio - pad), int(origin.y() * ratio - pad),
                          int((origin.x() + item.width()) * ratio + pad),
                          int((origin.y() + item.height()) * ratio + pad)))
        return boxes

    def _in_chrome(self, col, row, boxes):
        return any(left <= col <= right and top <= row <= bottom
                   for left, top, right, bottom in boxes)

    def _stray_sample(self, image, step=4):
        """Off-backdrop pixels outside the chrome, sampled coarsely.

        This is the ARRIVAL predicate for the isolated frame, so it
        runs on every candidate and must stay cheap — the precise
        _stroke_ink does the asserting. It exists because the grid is
        drawn on a THREADED canvas: clearing the flag leaves the last
        painted image on screen until the worker's repaint lands, and
        on a starved machine that window is wide enough to grab, so
        the frame reads as a full grid while every property says the
        grid is off."""
        ox, oy, ratio = self._device_origin()
        backdrop, _ = self._backdrop_of(image)
        chrome = self._chrome_boxes()
        count = 0
        for row in range(0, image.height(), step):
            for col in range(0, image.width(), step):
                if self._in_chrome(col, row, chrome):
                    continue
                if self._delta(image.pixel(col, row), backdrop) >= 40:
                    count += 1
        return count

    def _envelope(self, ratio):
        """The round-join envelope in LOGICAL scene pixels: half the
        stroke the QML publishes (already logical) plus the
        antialiasing allowance, which is a DEVICE quantity and so
        divides by the ratio. Every distance this test measures is in
        logical scene pixels, and this is the one place the two
        systems meet — multiplying the width by the ratio here instead
        is what made the envelope nearly twice as generous at DPR 2,
        wide enough for a real miter to sit inside it."""
        return (ZoomInkMassTests._publish_width(self.face) / 2.0
                + self.AA_DEVICE_PX / ratio)

    def _stroke_ink(self, image):
        """Every pixel of the frame that stands off its own backdrop,
        in scene coordinates, with the chrome taken out. With the grid
        off the face paints nothing else on the bare window, so this is
        the stroke."""
        ox, oy, ratio = self._device_origin()
        backdrop, _ = self._backdrop_of(image)
        # The boxes the frame was GRABBED with, never a fresh read: the
        # backdrop scan above takes long enough for the rail to travel
        # out from under a freshly-read box.
        chrome = getattr(self, "_frame_chrome", None) or self._chrome_boxes()
        pixels = []
        for row in range(image.height()):
            for col in range(image.width()):
                if self._in_chrome(col, row, chrome):
                    continue
                if self._delta(image.pixel(col, row), backdrop) >= 40:
                    pixels.append(((col + 0.5 - ox) / ratio, (row + 0.5 - oy) / ratio))
        return pixels

    def _bbox_ink(self, image, path, margin=3.0):
        """Ink inside the wedge's own bounding box, and nothing else.

        This is the ARRIVAL predicate, so it runs on every settled
        candidate frame — it must not walk the whole picture the way
        _stroke_ink does, or the wait spends its own deadline
        measuring. The margin covers the stroke's half-width and its
        antialiasing. The backdrop is the frame's top-left corner: the
        wedge is 25 scene px away from it at this mapping, and with the
        grid off nothing else paints there."""
        ox, oy, ratio = self._device_origin()
        backdrop = image.pixel(int(ox) + 2, int(oy) + 2)
        xs = [point[0] for point in path]
        ys = [point[1] for point in path]
        left = int((min(xs) - margin) * ratio + ox)
        right = int((max(xs) + margin) * ratio + ox)
        top = int((min(ys) - margin) * ratio + oy)
        bottom = int((max(ys) + margin) * ratio + oy)
        count = 0
        chrome = self._chrome_boxes()
        for row in range(max(0, top), min(image.height(), bottom + 1)):
            for col in range(max(0, left), min(image.width(), right + 1)):
                if self._in_chrome(col, row, chrome):
                    continue
                if self._delta(image.pixel(col, row), backdrop) >= 40:
                    count += 1
        return count

    @staticmethod
    def _backdrop_of(image):
        """The frame's own backdrop — its most common value, and how
        many samples it holds. The isolation check reads both: a frame
        with the grid off and nothing painted is one value, entirely."""
        from collections import Counter
        counts = Counter(image.pixel(col, row)
                         for row in range(0, image.height(), 3)
                         for col in range(0, image.width(), 3))
        return counts.most_common(1)[0][0], counts

    def _grab_from(self, image):
        """The background comes from the SAME frame as the measurement:
        _ink_mass reads every pixel against it, so a background sampled
        from another frame turns that frame's ink into backdrop. The
        chrome boxes ride along for the same reason."""
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        self._background = image.pixel(int(origin.x()) + 8, int(origin.y()) + 8)
        self._frame_chrome = self._chrome_boxes()
        return image

    def _grab(self):
        return self._grab_from(self.window.grabWindow())

    def _settled_grab(self, arrived=None):
        """Grab once the threaded rasters have landed: two consecutive
        sampled frames identical AND, when given, *arrived* satisfied.
        Agreement alone is not arrival — two grabs taken before the
        canvases paint anything agree perfectly, so a settle test on
        its own hands the census an empty picture (the loaded runner's
        "the composed stroke vanished"). The deadline is a hang guard,
        not a budget: the rasters on a starved machine take as long as
        they take, and the caller's own assertion is what fails when
        they never land.

        The frame the predicate cleared IS the arrival evidence, so it
        is the frame returned: re-grabbing here measures a frame nothing
        validated, which is the same "vanished" signature one frame
        later (the CI 3.10 leg took this path — its seven-test file
        finished in 5.0 s, so the predicate had been satisfied and the
        fresh grab, not the wait, was what read empty)."""
        import time as _time

        deadline = _time.monotonic() + 15.0
        previous = None
        previous_chrome = None
        # The chrome must be still AS WELL as the canvas, and still for
        # a while: the zoom scope slides to its parked x over a 180 ms
        # Behavior, and a frame grabbed mid-slide carries a full-height
        # edge wherever it happened to be. Agreement on two frames is
        # not enough on its own — the rail rests at x=6 until the
        # face's width binding re-evaluates and then leaves, so a pair
        # of frames can agree during that rest and the very next moment
        # be mid-slide. Which is what the Windows census reported: 16 px
        # in one column at (85,38), then 120 px across eight columns
        # from (78,38), both the rail's left edge inside its band.
        started = _time.monotonic()
        quiet_until = started + self.CHROME_QUIET_S
        while _time.monotonic() < deadline:
            self._pump_settle()
            image = self.window.grabWindow()
            chrome = self._chrome_boxes()
            now = _time.monotonic()
            if previous_chrome is not None and chrome != previous_chrome:
                quiet_until = now + self.CHROME_QUIET_S
            previous_chrome = chrome
            if (arrived is None or arrived(image)) and previous is not None and (
                    now >= quiet_until
                    and _parent.PlateFaceRenderTests._sample(image)
                    == _parent.PlateFaceRenderTests._sample(previous)):
                # The chrome is captured WITH the frame: reading it
                # later put the box where the rail had moved to, not
                # where the frame drew it, and the measurement then
                # excluded an empty rectangle and counted 32,586 px of
                # rail as stroke ink.
                self._frame_chrome = chrome
                return self._grab_from(image)
            previous = image
        # The deadline is a hang guard, and a hang guard that RETURNS
        # instead of failing turns "the raster never arrived" into an
        # empty picture the caller then measures — the census blamed the
        # painter for a frame that had simply not been painted yet.
        self.fail("the raster never arrived: the settle predicate was "
                  "unsatisfied for 15 s%s"
                  % ("" if arrived is not None else " (no predicate given)"))

    def _scene_row(self, bed_y, scale):
        return int(self.mapping["offsetY"] + (self.mapping["bedYMax"] - bed_y)
                   * self.mapping["sy"] * scale)

    @staticmethod
    def _offending_columns(offenders, ratio):
        """The failing pixels as a compact NUMERIC report: one line per
        column that carries any, with its row span and its worst
        distance past the envelope. A count says how much; the columns
        say which element — a graduation is one column over every row,
        a miter spike is a wedge.

        The offenders arrive in logical scene pixels and the report is
        in DEVICE pixels — the grid the reader sees in the PNG. The
        conversion happens HERE, once, so a distance is never compared
        against an envelope measured in the other system."""
        by_column = {}
        for (sx, sy), distance in offenders:
            col = int(round(sx * ratio))
            row = int(round(sy * ratio))
            past = distance * ratio
            low, high, worst = by_column.get(col, (row, row, 0.0))
            by_column[col] = (min(low, row), max(high, row), max(worst, past))
        return "\n".join("  col %4d: rows %d..%d (%d px), worst %.2f device px "
                         "past the envelope"
                         % (col, low, high, high - low + 1, worst)
                         for col, (low, high, worst) in sorted(by_column.items()))

    def _write_evidence(self, stem, frames, report):
        """The frames and the numeric report, beside the mapping they
        were measured through.

        HARNESS_SHOT_DIR first: CI sets it and already publishes the
        directory as the harness-mounts artefact, so the pictures
        arrive with everything else. The fallback is the platform's
        own temp directory — never a literal /tmp, which a Windows
        runner does not have, so the old "frames written to /tmp/..."
        line named a directory that did not exist and two Windows
        failures produced no retrievable evidence at all."""
        from pathlib import Path
        root = os.environ.get("HARNESS_SHOT_DIR")
        folder = Path(root) if root else Path(tempfile.gettempdir()) / "mpf"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return "the evidence directory could not be created: %s" % exc
        written, failed = [], []
        for name, frame in frames:
            path = folder / ("%s-%s.png" % (stem, name))
            # save() returns False rather than raising: an unchecked
            # call reports evidence that is not on disk.
            (written if frame.save(str(path)) else failed).append(str(path))
        (folder / ("%s-report.txt" % stem)).write_text(
            "%s\nmapping %r\n" % (report, self.mapping))
        if failed:
            return "%s (COULD NOT WRITE: %s)" % (", ".join(written),
                                                 ", ".join(failed))
        return ", ".join(written)

    def _band_mass(self, image, bed_y, scale, span):
        """The ink mass over the FIXED bed region bed-x 15..40 mm and
        the rows around *bed_y*: the same bed content at every zoom,
        so uniform scaling grows the mass as scale squared."""
        sx = self.mapping["sx"]
        rect = QRect(int(15.0 * sx * scale), self._scene_row(bed_y, scale) - span,
                     int(25.0 * sx * scale), 2 * span)
        return self._ink_mass(image, rect)

    # Two arms meeting at an acute corner at bed (35, 215), symmetric
    # about it so the join points exactly along +x. The corner is the
    # RIGHTMOST point, so a spike is the only thing that can put ink to
    # its right.
    #
    # The angle is 18 degrees, and that is a MEASUREMENT, not a taste:
    # Canvas's miterLimit defaults to 10, and it falls back to a BEVEL
    # when width/sin(angle/2) exceeds that — so a miter only exists at
    # angles where 1/sin(angle/2) < 10, i.e. above ~11.5 degrees. The
    # wedge this replaced met at 2.5 degrees, where the default join is
    # bevelled and the test could not have caught a miter at all: it
    # passed with lineJoin set to "miter", which is how the mistake
    # surfaced. At 18 degrees the ratio is 6.4, the miter is drawn, and
    # it reaches ~12.7 px past the path where a round join reaches 1.
    MITER_WEDGE = [[15.0, 211.8, 1.0], [35.0, 215.0, 1.0], [15.0, 218.2, 1.0]]
    # The antialiasing allowance, in DEVICE pixels: Qt spreads partial
    # coverage over the pixel the geometric edge crosses, so ink
    # reaches at most one device pixel past the envelope, plus the
    # half-pixel between a pixel's INDEX and its CENTRE. It is divided
    # by the ratio where it is used, because every distance in this
    # test is measured in logical scene pixels — the allowance is a
    # property of the rasteriser's grid, not of the scene.
    AA_DEVICE_PX = 1.5
    # The ink a wedge this size must produce: two arms of ~20 mm at
    # 1.664 px/mm and ~4 px wide is ~270 px of ink, so 40 is positive
    # evidence the stroke rendered without being a width assertion.
    WEDGE_FLOOR = 40
    # How long the chrome must hold still before a frame counts as
    # settled: comfortably more than the zoom scope's 180 ms slide, so
    # a rest that is merely a pause mid-transition cannot pass.
    CHROME_QUIET_S = 0.35

    def test_no_miter_spike_on_acute_corners(self):
        """The miter-join regression (the live report's spikes on tight
        joins): an acute wedge drawn with a THICK stroke must not poke
        ink past the corner. The default Canvas miter extends
        width/sin(angle/2) — ~12.7 px for this wedge at this width —
        while a round join stays within width/2 of the path.

        The oracle is the round-join envelope itself: EVERY inked pixel
        must lie within half the stroke width of the polyline. It needs
        no baseline and no subtraction, because the grid is off. The
        oracle this replaces DIFFED one asynchronously painted frame
        against another, and so measured whatever else the face was
        drawing: the zoom scope slides to its parked x over a 180 ms
        Behavior, and a grab taken mid-slide carried its full-height
        edge through the census band — 16 px in one column at (85,38),
        then 120 px across eight from (78,38), neither of them stroke
        ink at all. Isolating the stroke removes that whole class
        rather than widening a tolerance around it."""
        wedge = {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {
                    "classes": {"WALL-OUTER": [self.MITER_WEDGE]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 3,
                },
                "next": None,
            },
            "split": 3, "method": "motion index", "anchor": 0,
        }
        self._grid_off()
        # Thick: ~4 px at this face. The margin is the point — a miter
        # reaches (width/2)/sin(angle/2) past the vertex, so the
        # overshoot past the envelope grows with the width while the
        # antialiasing allowance does not. At ~2 px the mutant cleared
        # the envelope by 2.77 px; at ~4 px it clears it by ~10, which
        # no platform's rasteriser is going to swallow.
        self.face.setProperty("lineScale", 12.0)
        self._pump_settle()

        # Isolation, asserted rather than assumed: with the grid off and
        # an empty layer the bare face paints nothing but its own
        # chrome. Anything else would be measured as stroke ink below —
        # which is exactly how the census failed, reading a rail that
        # was mid-slide as a join.
        empty = self._set_payload(
            self.EMPTY_PAYLOAD,
            arrived=lambda frame: self._stray_sample(frame) == 0)
        stray = self._stroke_ink(empty)
        # assertTrue, not assertEqual against []: a stray list runs to
        # tens of thousands of pixel pairs, and unittest prints the
        # whole diff — 600 KB of log for one failure.
        if stray:
            _, _, ratio = self._device_origin()
            columns = self._offending_columns(
                [(pixel, 0.0) for pixel in stray], ratio)
            wrote = self._write_evidence(
                "miter-isolation",
                (("isolated", empty),),
                "with the grid off and an empty layer %d px stand off the "
                "backdrop, and only the chrome may:\n%s"
                % (len(stray), columns))
            self.fail(
                "the face paints more than the stroke with the grid off: "
                "%d stray px, first at scene %s.\ngrabbed chrome boxes %s, "
                "live %s\n%s\nframes: %s"
                % (len(stray), stray[0], getattr(self, "_frame_chrome", None),
                   self._chrome_boxes(), columns, wrote))

        # ONE coordinate system: logical scene pixels. That is what
        # _stroke_ink returns and what _scene builds the path in, and
        # the width the QML publishes is already logical. Multiplying
        # the width by the ratio mixed the two — at DPR 2 the envelope
        # came out nearly twice as generous, wide enough for a real
        # miter to sit inside it. Only the antialiasing allowance is a
        # DEVICE quantity, and it converts by division.
        ratio = float(self.window.devicePixelRatio())
        width = ZoomInkMassTests._publish_width(self.face)
        half = width / 2.0
        envelope = self._envelope(ratio)
        path = [_parent.PlateFaceRenderTests._scene(self.mapping, x, y)
                for x, y, _ in self.MITER_WEDGE]

        # Positive evidence: the wedge's own ink, and the wait for it.
        # A hang guard, not a budget — the assertion below is what fails
        # when the ink never lands.
        image = self._set_payload(
            wedge, arrived=lambda frame: self._bbox_ink(frame, path) >= self.WEDGE_FLOOR)
        inked = self._stroke_ink(image)
        self.assertGreaterEqual(
            len(inked), self.WEDGE_FLOOR,
            "the wedge never painted: %d px of ink, the floor is %d. "
            "Stroke width %.2f px, envelope %.2f px, mapping %r"
            % (len(inked), self.WEDGE_FLOOR, width, envelope, self.mapping))

        # The envelope: a round join is exactly "no ink further from the
        # path than half the stroke". Every inked pixel is measured
        # against it — there is no region to sample and no column to
        # exclude.
        offenders = [(pixel, self._distance_to_path(pixel[0], pixel[1], path) - envelope)
                     for pixel in inked
                     if self._distance_to_path(pixel[0], pixel[1], path) > envelope]
        if offenders:
            # Reported in device pixels, converted once, so every number
            # in the message is in the grid the reader sees in the PNG.
            wrote = self._write_evidence(
                "miter",
                (("measured", image), ("isolated", empty)),
                "ratio %.2f, stroke width %.2f device px, envelope %.2f "
                "device px, %d px of ink, %d outside:\n%s"
                % (ratio, width * ratio, envelope * ratio, len(inked),
                   len(offenders), self._offending_columns(offenders, ratio)))
            self.fail(
                "miter spike: %d of %d inked px lie outside the round-join "
                "envelope (half the stroke's %.2f device px, plus %.1f "
                "device px of antialiasing = %.2f device px, at ratio %.2f)."
                "\ncorner at scene %s, arms from %s to %s\nmapping %r\n%s\n"
                "frames: %s"
                % (len(offenders), len(inked), half * ratio, self.AA_DEVICE_PX,
                   envelope * ratio, ratio, path[1], path[0], path[2],
                   self.mapping,
                   self._offending_columns(offenders, ratio), wrote))

    def test_the_envelope_stays_logical_at_every_device_ratio(self):
        """The envelope's two inputs are in different systems, and this
        is where they meet.

        The stroke width the QML publishes is LOGICAL; the
        antialiasing allowance is DEVICE. The envelope must come out
        logical at every ratio. The arithmetic this replaced —
        `width * ratio / 2 + 1.5` — doubles the width term at DPR 2
        while leaving the allowance alone, so the permitted region
        nearly doubles and a real miter fits inside it. The
        assertions below are the two ratios and the exact value at
        each; the last one is the shape of the bug."""
        half = ZoomInkMassTests._publish_width(self.face) / 2.0
        self.assertGreater(half, 0.0, "no stroke width to build an envelope from")
        for ratio in (1.0, 2.0):
            expected = half + self.AA_DEVICE_PX / ratio
            self.assertAlmostEqual(self._envelope(ratio), expected, places=9)
        # The specific defect: at DPR 2 the allowance is half a logical
        # pixel, so the envelope must stay close to the half-width
        # rather than doubling with it.
        self.assertLess(self._envelope(2.0), half + 1.0)
        self.assertLess(self._envelope(2.0), self._envelope(1.0))

    def test_the_miter_holds_when_the_device_is_scaled(self):
        """The whole census again at DPR 2, in its own process.

        The ratio is fixed when the QGuiApplication is built, so a
        second process is the only honest way to move it —
        QT_SCALE_FACTOR is what the offscreen platform honours, and
        the child reports the ratio it actually ran at so this cannot
        pass by having the variable ignored."""
        import subprocess
        import sys
        import textwrap
        root = pathlib.Path(__file__).resolve().parents[1]
        # The driver must NOT build the application itself: the suite's
        # _start_application raises SkipTest when one already exists,
        # so a probe that creates it turns the whole child into
        # "Ran 0 tests ... OK (skipped=1)" — a green that ran nothing.
        # Measured: that is exactly what this test did before the
        # assertions below, and it passed with the miter restored.
        # The ratio is read AFTER the run, from the application the
        # suite built.
        driver = textwrap.dedent("""
            import sys, unittest
            import test_zoom_raster_real_engine as module
            suite = unittest.TestSuite([module.ZoomStrokeTests(
                "test_no_miter_spike_on_acute_corners")])
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            from PyQt6.QtGui import QGuiApplication
            print("DPR %.2f" % QGuiApplication.primaryScreen().devicePixelRatio(),
                  flush=True)
            sys.exit(0 if result.wasSuccessful() else 1)
            """)
        script = pathlib.Path(tempfile.gettempdir()) / "mpf-miter-dpr2.py"
        script.write_text(driver)
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_SCALE_FACTOR"] = "2"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root / "tests"), str(root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        result = subprocess.run([sys.executable, str(script)], cwd=str(root),
                                env=env, capture_output=True, text=True, timeout=300)
        # Both streams: TextTestRunner writes its report to stderr, so
        # a check that reads stdout alone sees the driver's own line
        # and nothing else.
        said = (result.stdout or "") + (result.stderr or "")
        # All three, or the child can report success without having
        # measured anything: a skipped test passes, and so does a
        # child that quietly ran at the ambient ratio.
        self.assertIn("Ran 1 test", said,
                      "the child did not EXECUTE the census, so it proves "
                      "nothing:\n%s" % said[-3000:])
        self.assertNotIn("skipped", said,
                         "the child skipped the census:\n%s" % said[-3000:])
        self.assertIn("DPR 2.00", said,
                      "the child did not run at DPR 2, so it proves nothing:"
                      "\n%s" % said[-3000:])
        self.assertEqual(result.returncode, 0,
                         "the census failed at DPR 2:\n%s" % said[-3000:])

    def test_ghost_pending_printed_share_one_width(self):
        """Test 7: the same geometry renders at one physical width in
        all three channels — only alpha differs."""
        payload = {
            "available": True, "reason": "",
            "layers": {
                "prev": self.LINE_PAYLOAD["layers"]["current"],
                "current": self.LINE_PAYLOAD["layers"]["current"],
                "next": self.LINE_PAYLOAD["layers"]["current"],
            },
            "split": 2, "method": "motion index", "anchor": 0,
        }
        self._set_payload(payload)
        self.face.setProperty("showPrevious", True)
        self.face.setProperty("showNext", True)
        # The arrival evidence IS the assertion: wait until the
        # composed stroke is in the band, then measure it.
        image = self._settled_grab(
            lambda frame: self._band_mass(frame, 203.0, 1.0, 4) > 0)
        mass = self._band_mass(image, 203.0, 1.0, 4)
        self.assertGreater(mass, 0, "the composed stroke vanished")


class ZoomInkMassTests(_parent.RealEngineTestCase):
    """The physical-width contract, measured through the product's
    own width helpers (toolpathWidthPx / travelWidthPx) invoked on
    the real engine — the same formula every painter reads. The
    mount lives in the MONITOR harness; the probe rig (a settled
    mount, then the property flips and a partial-prefix re-scrub)
    is the composition the 2026-09-22 probes validated for the
    pixel-level zoom measurements the docstrings cite."""

    # Plain-function aliases of the parent's painter helpers — never
    # a TestCase alias (that would drag the parent suite into this
    # module's discovery).
    _native_layer = _parent.PlateFaceRenderTests._native_layer
    _wait_red = _parent.PlateFaceRenderTests._wait_red
    _red_pixels = _parent.PlateFaceRenderTests._red_pixels
    _purple_pixels = _parent.PlateFaceRenderTests._purple_pixels
    _matches = staticmethod(_parent.PlateFaceRenderTests._matches)
    _follower_popover = _parent.PlateFaceRenderTests._follower_popover
    _open = _parent.PlateFaceRenderTests._open
    _popover_faces = staticmethod(_parent.PlateFaceRenderTests._popover_faces)
    _printer = staticmethod(_parent.PlateFaceRenderTests._printer)

    # The bed-centre line: the wheel's focal pinning keeps the bed
    # centre at the face centre at every zoom, so the stroke stays in
    # the grabbed frame from 100% to ~400%. The 21-motion polyline is
    # deliberate — the zoomed raster renders only a sliver of a
    # 2-motion path in the offscreen harness, while the multi-point
    # path repaints whole (the 2026-09-22 fixture-matrix probe).
    LINE = [[[20.0 + motion * 10.0, 125.0, float(motion)]
             for motion in range(21)]]

    @staticmethod
    def _payload(classes, travels=(), motions=2):
        # The scrub payload's own shape (top-level classes/travels),
        # the format setScrub and _native_layer both consume.
        return {
            "classes": classes,
            "travels": travels,
            "travelStarts": travels[:1],
            "travelEnds": travels[-1:],
            "motions": motions,
        }

    def _mount_line(self, payload, line_scale=8.0):
        previous = _parent.PlateFaceRenderTests.PAYLOAD
        _parent.PlateFaceRenderTests.PAYLOAD = {
            "available": True, "reason": "",
            "layers": {"prev": None,
                       "current": {"classes": {}, "travels": [], "travelStarts": [],
                                   "travelEnds": [], "motions": 0},
                       "next": None},
            "split": 0, "method": "motion index", "anchor": 0,
        }
        self.addCleanup(setattr, _parent.PlateFaceRenderTests, "PAYLOAD", previous)
        # _open caches the constructed printer on the instance; a
        # second mount in the same test must see the factory again.
        self._printer = _parent.PlateFaceRenderTests._printer
        monitor, window, face = self._follower_popover()
        self._window = window
        face.setProperty("dot", None)
        face.setProperty("showBase", False)
        face.setProperty("showPrevious", False)
        face.setProperty("showNext", False)
        face.setProperty("lineScale", line_scale)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        layer = self._native_layer(payload, face, prefix_split=1)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"])
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the 1x scene never drew")
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        return monitor, window, face, plot_value

    def _probe_rig(self, payload):
        """The probe-validated zoom rig (2026-09-22): the settled
        mount FIRST, then the property flips and the re-scrub with a
        partial vector prefix and a near-full split — the only order
        and composition whose zoomed raster repaints whole in the
        offscreen grabber."""
        monitor, window, face, plot = self._mount_line(payload)
        face.setProperty("lineScale", 20.0)
        face.setProperty("showBase", True)
        self.pump(10)
        # The prefix raster bakes the same lineScale the face shows,
        # so the painted stroke is the width the helper names.
        layer = self._native_layer(payload, face, prefix_split=10,
                                   line_scale=float(face.property("lineScale")))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"] - 1)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the 1x scene never drew")
        window.grabWindow()
        self.pump(20)
        return monitor, window, face, plot

    def _wheel(self, window, face, delta=120):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)
        scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
        event = QWheelEvent(
            QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)

    def _zoom_to(self, window, face, wheels, want_red=True):
        """Wheel-zoom in *wheels* steps and wait for the exact scene's
        commit: the settled picture the rasters baked at the TARGET
        scale (the warm raster already shows the target while the
        display eases). Production-width strokes are too thin for the
        red census, so want_red=False settles on two identical grabs
        instead."""
        for _ in range(wheels):
            self._wheel(window, face)
            self._pump_ms(60)
        # The warm navigation raster owns the scene through the eased
        # zoom and paints the toolpath at the target scale; settle
        # the ease, then measure its picture (the exact-scene commit
        # rebuilds the canvas raster and is not part of this probe).
        self._pump_ms(600)
        if want_red:
            image, count = self._wait_red(window, face, want=True, timeout=15.0)
            self.assertGreater(count, 0, "the zoomed scene never drew")
        else:
            image = self._settled_grab()
            count = self._red_pixels(image, face, window)
        return (image, float(face.property("viewScale")),
                float(face.property("viewPanX")), float(face.property("viewPanY")),
                count)

    def _settled_grab(self, arrived=None):
        """Grab once the threaded rasters have landed: two consecutive
        sampled frames identical AND, when given, *arrived* satisfied —
        agreement alone accepts a canvas that has not painted yet. The
        deadline is a hang guard, not a budget."""
        import time as _time

        deadline = _time.monotonic() + 15.0
        previous = None
        image = self._window.grabWindow()
        while _time.monotonic() < deadline:
            self._pump_ms(30)
            image = self._window.grabWindow()
            if (arrived is None or arrived(image)) and previous is not None and (
                    _parent.PlateFaceRenderTests._sample(image)
                    == _parent.PlateFaceRenderTests._sample(previous)):
                return image
            previous = image
        # Same rule as the sibling above: exhaustion is a failure, not a
        # frame to measure.
        self.fail("the raster never settled: two sampled frames never "
                  "agreed AND the arrival predicate held, for 15 s")

    def _bed_pixel(self, face, window, plot, bed_x, bed_y, scale, pan_x, pan_y):
        bed = plot["bed"]
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(round(int(origin.x()) + pan_x + (float(bed["offsetX"])
                  + (bed_x - float(bed["bedXMin"])) * float(plot["sx"])) * scale))
        row = int(round(int(origin.y()) + pan_y + (float(bed["offsetY"])
                  + (float(bed["bedYMax"]) - bed_y) * float(plot["sy"])) * scale))
        return col, row

    @staticmethod
    def _publish_width(face):
        """The ONE physical stroke-width the painters read (the QML's
        toolpathWidthPx), forced through the view carrier so the
        property flips land deterministically."""
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        QMetaObject.invokeMethod(face, "_publishView")
        width = QMetaObject.invokeMethod(face, "toolpathWidthPx",
                                         Q_RETURN_ARG(QVariant))
        return float(width)

    def _sx(self, plot):
        return float(plot["sx"])

    def test_the_stroke_width_is_physical_and_floored_at_100_percent(self):
        """Test 1: at 100% and the production lineScale the width is
        the physical value — nominal bed mm through the plot's
        px-per-mm and the line scale — presented at the parity
        floor's footprint: the device-coverage floor (min(2/dpr, 1)
        logical px) presents sub-floor strokes at the same
        full-intensity width both painters stroke (the native
        raster's backed downscale faded thin strokes without it)."""
        monitor, window, face, plot = self._probe_rig(
            self._payload({"WALL-OUTER": self.LINE}))
        face.setProperty("lineScale", 0.7)
        self.pump(10)
        width = self._publish_width(face)
        nominal = 0.2 * self._sx(plot) * 0.7
        self.assertLess(nominal, 1.0,
                        "the fixture lost its subpixel nominal")
        self.assertAlmostEqual(width, 1.0, delta=0.05,
                               msg=f"width {width:.3f}px vs the 1.0px floor")

    def test_the_width_helper_scales_with_the_view_zoom(self):
        """Test 2: the width helper multiplies the physical width by
        the view zoom at every step — the structural law behind the
        zoomed paint (the 2026-09-22 probes measured it in pixels
        where the warm raster landed: 16px at 195%, 24px at 305% of
        an 8px 100% stroke)."""
        monitor, window, face, plot = self._probe_rig(
            self._payload({"WALL-OUTER": self.LINE}))
        widths = {}
        for scale in (1.0, 2.0, 3.0, 5.0):
            face.setProperty("viewScale", scale)
            self.pump(10)
            widths[scale] = self._publish_width(face)
        self.assertGreater(widths[1.0], 0, "the helper never produced a width")
        for scale in (2.0, 3.0, 5.0):
            self.assertAlmostEqual(widths[scale] / widths[1.0], scale,
                                   delta=scale * 0.1,
                                   msg=f"scale {scale}: width ratio "
                                       f"{widths[scale] / widths[1.0]:.2f}")

    def test_the_production_stroke_never_bridges_the_day_scale_gap(self):
        """Test 3: once the stroke clears the parity floor, it stays
        thinner than HALF the Day-scale gap — the old screen-constant
        0.7 px stroke exceeded half the gap at every zoom and fused
        the Day strokes. The 100% leg is the floor's accepted trade
        (the sub-gap nominal presents at the floor's 1 px); the 300%
        leg is where the physical width genuinely governs."""
        monitor, window, face, plot = self._probe_rig(
            self._payload({"WALL-OUTER": self.LINE}))
        face.setProperty("lineScale", 0.7)
        self.pump(10)
        for scale in (3.0, 5.0):
            face.setProperty("viewScale", scale)
            self.pump(10)
            width = self._publish_width(face)
            gap_px = 0.5 * self._sx(plot) * scale
            self.assertLess(width, gap_px / 2.0,
                            f"scale {scale}: the {width:.2f}px stroke bridges "
                            f"the {gap_px:.2f}px gap")

    def test_the_travel_width_keeps_its_visual_ratio(self):
        """Test 4: the travel stroke reads the visual ratio over the
        same physical basis — the width factor over the extrusion
        stroke, never a screen-pixel count."""
        monitor, window, face, plot = self._probe_rig(
            self._payload({"WALL-OUTER": self.LINE}))
        face.setProperty("lineScale", 1.0)
        self.pump(10)
        face.setProperty("viewScale", 3.0)
        self.pump(10)
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        QMetaObject.invokeMethod(face, "_publishView")
        ext = float(QMetaObject.invokeMethod(face, "toolpathWidthPx",
                                             Q_RETURN_ARG(QVariant)))
        trav = float(QMetaObject.invokeMethod(face, "travelWidthPx",
                                              Q_RETURN_ARG(QVariant)))
        ratio = float(face.property("travelVisualRatio"))
        self.assertGreater(ext, 0, "no extrusion width to ratio against")
        self.assertAlmostEqual(trav / ext, ratio, delta=0.05,
                               msg=f"travel ratio {trav / ext:.2f} vs {ratio:.2f}")

    def test_the_compact_boost_widens_the_stroke_deliberately(self):
        """Test 5: the compact thumbnail boost multiplies the physical
        width by the named constant — a deliberate, documented
        visibility adjustment. The multiplier rides the NOMINAL; the
        parity floor (1 px) masks the base at the production scale,
        so the boosted value is the nominal times the boost, floored."""
        monitor, window, face, plot = self._probe_rig(
            self._payload({"WALL-OUTER": self.LINE}))
        face.setProperty("lineScale", 1.0)
        self.pump(10)
        face.setProperty("viewScale", 1.0)
        self.pump(10)
        normal = self._publish_width(face)
        face.setProperty("compact", True)
        self.pump(10)
        boosted = self._publish_width(face)
        self.assertGreater(normal, 0, "the helper never produced a width")
        nominal = 0.2 * self._sx(plot) * 1.0
        self.assertAlmostEqual(boosted, max(nominal * 7.0, 1.0), delta=0.3,
                               msg=f"compact boost {boosted:.2f} vs "
                                   f"{max(nominal * 7.0, 1.0):.2f}")
