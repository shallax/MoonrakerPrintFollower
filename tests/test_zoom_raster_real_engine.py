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
        # without the grid its diff assumes.
        self.face.setProperty("progress", dict(payload, anchor=-1))
        self._pump_settle()
        self.face.setProperty("progress", payload)
        return self._settled_grab(arrived)

    def _grid_inked(self, image):
        """The grid's own ink: the 40 mm graduation's column, sampled
        where the miter census looks. That census subtracts a baseline
        frame, so a baseline still waiting for its grid reads as a
        spike on this column. The graduation sits at col 68 on this
        harness (1.664 px/mm, a 2 px face origin) — which is NOT the
        column the Windows failure reports: that begins at 78, where
        this harness draws no grid element at all, so the two are not
        the same fault and this docstring cannot name the cause. The
        census map in the failure message is what does."""
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        col = ox + int(self.mapping["offsetX"]
                       + 40.0 * self.mapping["sx"])
        row = oy + self._scene_row(228.0, 1.0)

        def inked(sample_row):
            off = image.pixel(col + 6, sample_row)  # clear of the line's width
            return max(self._delta(image.pixel(col + step, sample_row), off)
                       for step in (-1, 0, 1)) > 30

        if not inked(row):
            return False
        # EVERY row the spike census reads on this column, not one of
        # them. This predicate is the BASELINE's arrival proof, and the
        # census diffs a 16x24 region: a baseline accepted while the
        # graduation was drawn at the sampled row alone leaves the rest
        # of that region un-inked, and the diff against the measured
        # frame then reads the GRID as spike ink — the (78,38) report is
        # this column. One point cannot establish a region.
        corner_row = self._scene_row(215.0, 1.0)
        return all(inked(oy + probe) for probe in range(corner_row - 22, corner_row - 6))

    def _grab_from(self, image):
        """The background comes from the SAME frame as the measurement:
        _ink_mass reads every pixel against it, so a background sampled
        from another frame turns that frame's ink into backdrop."""
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        self._background = image.pixel(int(origin.x()) + 8, int(origin.y()) + 8)
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
        image = self.window.grabWindow()
        while _time.monotonic() < deadline:
            self._pump_settle()
            image = self.window.grabWindow()
            if (arrived is None or arrived(image)) and previous is not None and (
                    _parent.PlateFaceRenderTests._sample(image)
                    == _parent.PlateFaceRenderTests._sample(previous)):
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
    def _evidence_dir():
        """Where a failing census drops its frames.

        HARNESS_SHOT_DIR first: CI sets it and already publishes the
        directory as the harness-mounts artefact, so the pictures
        arrive with everything else. The fallback is the dev
        container's scratch dir, NOT a Windows path — a Windows runner
        has no /tmp, so the "frames written to /tmp/mpf/..." line
        named a directory that does not exist and two Windows miter
        failures produced no retrievable evidence at all."""
        folder = os.environ.get("HARNESS_SHOT_DIR") or os.path.join(
            tempfile.gettempdir(), "mpf")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return None
        return folder

    def _census_report(self, diff, rect, threshold=40):
        """The census as ASCII, carried in the failure message.

        A count says how MUCH ink disagreed; the map says WHERE, which
        is what separates a miter spike (a wedge up-right of the
        corner) from a baseline accepted in a different state (whole
        rows, or one column). It travels in the runner's log, which
        survives where a PNG on a Windows path does not."""
        top, left, bottom, right = rect
        header = ["      " + "".join(str((left + i) // 100)
                                     for i in range(right - left + 1)),
                  "      " + "".join(str(((left + i) // 10) % 10)
                                     for i in range(right - left + 1)),
                  "      " + "".join(str((left + i) % 10)
                                     for i in range(right - left + 1))]
        body = []
        for row in range(top, bottom + 1):
            cells = "".join("." if diff(c, row) < threshold
                            else str(min(9, diff(c, row) // 28))
                            for c in range(left, right + 1))
            body.append("%5d %s" % (row, cells))
        return "\n".join(header + body)

    def _ink_map(self, image, rect, backdrop, threshold=40):
        """One frame's own ink over the census region: '#' where a pixel
        stands off *backdrop*, '.' where it is backdrop. The diff map
        says the frames DISAGREE; these say which frame carries the
        ink, which is the difference between a spike the painter drew
        and a baseline that had not painted its grid yet."""
        top, left, bottom, right = rect

        def stands_off(col, row):
            pixel = image.pixel(col, row)
            return max(abs(((pixel >> s) & 0xFF) - ((backdrop >> s) & 0xFF))
                       for s in (0, 8, 16)) >= threshold

        return "\n".join(
            "%5d %s" % (row, "".join("#" if stands_off(col, row) else "."
                                     for col in range(left, right + 1)))
            for row in range(top, bottom + 1))

    @staticmethod
    def _backdrop(image, rect):
        """The region's most common value: the grid's cell fill, which
        both frames share, so their ink maps are comparable."""
        from collections import Counter
        top, left, bottom, right = rect
        counts = Counter(image.pixel(col, row) for row in range(top, bottom + 1)
                         for col in range(left, right + 1))
        return counts.most_common(1)[0][0]

    def _write_census_frames(self, diff, image, baseline, stem):
        """Measured, baseline and their difference, beside the mapping
        the census read. The difference is the picture the oracle
        actually sees; a reader comparing the other two by eye is
        re-deriving it."""
        folder = self._evidence_dir()
        if folder is None:
            return "the evidence directory could not be created"
        from PyQt6.QtGui import QColor, QImage
        probe = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
        probe.fill(QColor(0, 0, 0))
        for row in range(image.height()):
            for col in range(image.width()):
                delta = diff(col, row)
                probe.setPixelColor(col, row, QColor(min(255, delta * 3), 0, 0))
        written = []
        for name, frame in ((stem + "-measured.png", image),
                            (stem + "-baseline.png", baseline),
                            (stem + "-difference.png", probe)):
            path = os.path.join(folder, name)
            if frame.save(path):
                written.append(path)
        with open(os.path.join(folder, stem + "-mapping.txt"), "w") as handle:
            handle.write("mapping %r\n" % (self.mapping,))
        return ", ".join(written)

    def _band_mass(self, image, bed_y, scale, span):
        """The ink mass over the FIXED bed region bed-x 15..40 mm and
        the rows around *bed_y*: the same bed content at every zoom,
        so uniform scaling grows the mass as scale squared."""
        sx = self.mapping["sx"]
        rect = QRect(int(15.0 * sx * scale), self._scene_row(bed_y, scale) - span,
                     int(25.0 * sx * scale), 2 * span)
        return self._ink_mass(image, rect)

    def test_no_miter_spike_on_acute_corners(self):
        """The miter-join regression (the live report's spikes on
        tight joins): a near-parallel wedge drawn with a THICK stroke
        must not poke ink past the corner. The default Canvas miter
        extends width/sin(angle/2) — ~160 px for this wedge at this
        width — while a round join stays within width/2 of the path.
        The oracle is a per-pixel DIFF against a grid-and-backdrop
        baseline: the harness backdrop is not uniform and the grid
        lines cross every region. Both frames must therefore CARRY the
        grid for it to cancel — a baseline still waiting for its own
        grid reads as a spike on the graduation's column. The Windows
        report begins at (78,38), ten columns right of the graduation
        at 68 — so the failure message carries the census diff and both
        frames' own ink, which is what tells the two apart."""
        wedge = {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {
                    "classes": {"WALL-OUTER": [
                        [[15.0, 205.0, 1.0], [35.0, 215.0, 1.0], [16.0, 205.0, 1.0]],
                    ]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 3,
                },
                "next": None,
            },
            "split": 3, "method": "motion index", "anchor": 0,
        }
        self.face.setProperty("lineScale", 6.0)  # thick: ~2 px at this face
        self._pump_settle()
        origin = self.face.mapToItem(self.window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        arm_row = self._scene_row(205.0, 1.0)
        # The baseline is the settled frame the grid predicate cleared,
        # never a bare grab: the diff subtracts it pixel for pixel.
        baseline = self._set_payload(self.EMPTY_PAYLOAD, arrived=self._grid_inked)

        def diff(image, col, row):
            a = image.pixel(ox + col, oy + row)
            b = baseline.pixel(ox + col, oy + row)
            return max(abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) for s in (0, 8, 16))

        def arm_ink(image):
            return max(diff(image, col, row) for row in range(arm_row - 4, arm_row + 5)
                       for col in range(30, 60))

        # The threaded raster's landing is not deterministic in the
        # offscreen harness: wait for the wedge's own ink AND for the
        # grid the diff subtracts, rather than sampling for a stable
        # (possibly stale) frame. A hang guard, not a budget: the
        # rasters on a starved machine take as long as they take, and
        # the assertion below is what fails when the ink never lands.
        image = self._set_payload(
            wedge, arrived=lambda frame: arm_ink(frame) > 60
            and self._grid_inked(frame))
        self.assertGreater(arm_ink(image), 60, "the wedge never painted in the harness")
        # The spike's landing zone: both arms descend left-down, so a
        # miter spike would point up-right of the corner — rows above,
        # columns right of it — flooding this arm-free region for
        # ~160 px. A round join leaves it at baseline.
        corner_row = self._scene_row(215.0, 1.0)
        corner_col = int(35.0 * self.mapping["sx"])
        spiked = [(col, row) for row in range(corner_row - 22, corner_row - 6)
                  for col in range(corner_col + 4, corner_col + 28)
                  if not diff(image, col, row) < 40]
        if spiked:
            # The pictures, so the next reader can tell a genuine spike
            # from a baseline that was accepted before the grid it
            # subtracts had painted. The census subtracts one and reads
            # the other, so both belong in the evidence — and the map
            # belongs in the MESSAGE, because the artefact lane and the
            # runner's filesystem are not the same place.
            wrote = self._write_census_frames(
                lambda col, row: diff(image, col, row), image, baseline, "miter")
            region = (corner_row - 22, corner_col + 4,
                      corner_row - 7, corner_col + 27)
            backdrop = self._backdrop(image, region)

            def cell(col, row):
                return diff(image, col, row)

            self.fail(
                "miter spike ink at %s (%d px) of the %dx%d census; the "
                "corner is at col %d row %d, the arms at row %d, the 40 mm "
                "graduation at col %d.\nmapping %r\n"
                "census DIFF (measured vs baseline):\n%s\n"
                "baseline's own ink:\n%s\nmeasured's own ink:\n%s\n"
                "frames: %s"
                % (spiked[0], len(spiked), 24, 16, corner_col, corner_row,
                   arm_row, int(self.mapping["offsetX"] + 40.0 * self.mapping["sx"]),
                   self.mapping,
                   self._census_report(cell, region),
                   self._ink_map(baseline, region, backdrop),
                   self._ink_map(image, region, backdrop),
                   wrote))

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

    def _publish_width(self, face):
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
