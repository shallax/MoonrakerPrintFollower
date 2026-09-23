"""The renderer-parity contract (4.6.0): the native rasterised
toolpath presents the SAME PHYSICAL stroke width the QML vector
sections paint — never a fixed-width floor.

The rejected floor (2026-09-22): an earlier fix floored the raster's
stroke to the engine's 2-device-row footprint to chase a perceived
fade. The floor widens the stroke by a FIXED amount, while the
camera-independent navigation raster is presented scaled by the
zoom — so the floor's footprint magnifies with the zoom. Live at
400% the nav raster's lines read 4x the QML canvas's physical
stroke (floor x zoom / backing logical px vs physical x zoom), and
at rest the floored prefix outweighed the vector sections, which
read ghostly beside it. The floor is gone: the raster shares the
QML's exact physical width at every zoom. The pins hold the PAINTED
stroke at the physical width (no widened footprint) and the
PRESENTED 400% view at the zoomed physical stroke (the live 4x
report), plus the Day-scale separation the floor destroyed."""

try:
    from . import test_qml_real_engine as _parent
except ImportError:
    import test_qml_real_engine as _parent

PROBE_PLOT = {"offsetX": 0.0, "offsetY": 0.0, "sx": 2.25, "sy": 2.25,
              "bedXMin": 0.0, "bedYMax": 250.0}

LINE = [[[20.0, 125.0, 0.0], [150.0, 125.0, 1.0]]]

PAYLOAD = {"classes": {"WALL-OUTER": LINE},
           "travels": [], "travelStarts": [], "travelEnds": [],
           "motions": 2}


class RendererParityTests(_parent.RealEngineTestCase):
    """The raster's presented ink vs the vector's physical stroke."""

    _follower_popover = _parent.PlateFaceRenderTests._follower_popover
    _open = _parent.PlateFaceRenderTests._open
    _popover_faces = staticmethod(_parent.PlateFaceRenderTests._popover_faces)
    _printer = staticmethod(_parent.PlateFaceRenderTests._printer)
    _matches = staticmethod(_parent.PlateFaceRenderTests._matches)
    _native_layer = _parent.PlateFaceRenderTests._native_layer

    def test_the_navigation_raster_paints_the_physical_stroke(self):
        """The paint pin: the nav raster's stroke is the PHYSICAL
        width at the backing grid — nominal x plot scale x lineScale
        x backing (~1.3 px at 4x). The removed floor painted
        2 x backing rows (8 at 4x) and read 4x-wide live. The pin
        holds the footprint to the physical stroke plus the
        engine's own AA spread, on any engine."""
        from plugins.PlateQt import render_navigation_layer
        nav_view = {"width": 563, "height": 563, "scale": 1.0,
                    "lineScale": 0.7, "backing": 4.0}
        raster = render_navigation_layer(
            {"prev": None, "current": PAYLOAD, "next": None},
            PROBE_PLOT, nav_view)
        backing = 4.0
        sx = float(PROBE_PLOT["sx"]) * 1.0 * backing
        row = int((float(PROBE_PLOT["bedYMax"]) - 125.0) * sx)
        col0 = int((20.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        col1 = int((150.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        per_row = []
        for r in range(row - 8, row + 9):
            per_row.append(sum(raster.pixelColor(c, r).alphaF()
                               for c in range(col0, col1 + 1)))
        peak = max(per_row)
        self.assertGreater(peak, 0, "the nav raster drew nothing")
        inked = [v for v in per_row if v > 0.05 * peak]
        self.assertLessEqual(len(inked), 4,
                             "the nav raster carries a widened floor "
                             "footprint (%d rows): %r" % (len(inked), per_row))

    def test_the_navigation_raster_presents_the_physical_stroke_at_400_percent(self):
        """The live 4x report as a presented-pixels pin: the camera-
        independent raster presented at 400% must show the physical
        stroke scaled by the zoom (~1.3 logical rows), never the old
        floor's fixed footprint magnified to 8 presented rows."""
        from plugins.PlateQt import render_navigation_layer, png_file
        from PyQt6.QtCore import QPointF
        monitor, window, face = self._follower_popover()
        face.setProperty("lineScale", 0.7)
        face.setProperty("dot", None)
        self.pump(30)
        window.grabWindow()
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        plot = {"offsetX": float(bed["offsetX"]), "offsetY": float(bed["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(bed["bedXMin"]), "bedYMax": float(bed["bedYMax"])}
        nav_view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": 1.0, "lineScale": 0.7, "backing": 4.0,
                    # The production state at a 400% zoom: the raster
                    # re-baked for the zoom carries the device-floor
                    # stroke (backing x min(2/dpr, 1) / zoom), which
                    # presents at min(2/dpr, 1) — never magnified.
                    "zoom": 4.0, "dpr": 1.0}
        raster = render_navigation_layer(
            {"prev": None, "current": PAYLOAD, "next": None},
            plot, nav_view)
        png_file(raster, "/tmp/mpf", "parity-nav-zoom")
        self._printer.setNavigation("/tmp/mpf/parity-nav-zoom.png")
        self.pump(40)
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        sx = float(plot["sx"])
        # The bed centre's scene pixel at the 100% fit, then at the
        # 400% presentation (the source's 4x backing shows 1:1).
        fx = float(plot["offsetX"]) + (125.0 - float(plot["bedXMin"])) * sx
        fy = float(plot["offsetY"]) + (float(plot["bedYMax"]) - 125.0) * sx
        face.setProperty("viewScale", 4.0)
        face.setProperty("viewPanX", 0.0)
        face.setProperty("viewPanY", 0.0)
        face.setProperty("displayScale", 4.0)
        # The pan puts the bed centre at the window's (300, 300) so
        # the zoomed stroke stays inside the grab.
        face.setProperty("displayPanX", 300 - (ox + 4.0 * fx))
        face.setProperty("displayPanY", 300 - (oy + 4.0 * fy))
        face.setProperty("_interactionActive", True)
        # Arrival BEFORE agreement. Two grabs of a canvas that has not
        # painted yet agree perfectly, so a settle test on its own
        # accepts the blank frame and the census then reads an empty
        # scene — the CI's "drew nothing" under load. The band is the
        # evidence: agreement counts only once the stroke is in it.
        import time

        def band(frame):
            base = frame.pixel(ox + 8, oy + 8)
            return [max(abs(((frame.pixel(300, r) >> s) & 0xFF)
                            - ((base >> s) & 0xFF)) for s in (0, 8, 16))
                    for r in range(292, 309)]

        deadline = time.monotonic() + 15.0
        previous = None
        image = window.grabWindow()
        per_row = band(image)
        while time.monotonic() < deadline:
            self._pump_ms(30)
            image = window.grabWindow()
            per_row = band(image)
            if max(per_row) > 0 and previous is not None and (
                    _parent.PlateFaceRenderTests._sample(image)
                    == _parent.PlateFaceRenderTests._sample(previous)):
                break
            previous = image
        peak = max(per_row)
        self.assertGreater(peak, 0, "the presented zoomed raster drew nothing")
        inked = [v for v in per_row if v > 0.05 * peak]
        self.assertLessEqual(len(inked), 4,
                             "the 400%% presentation magnifies a fixed "
                             "floor footprint (%d rows): %r"
                             % (len(inked), per_row))

    def test_the_warm_raster_carries_the_baked_grid(self):
        """The single-flat-raster ruling: the grid is baked INTO the
        warm raster — the interaction scene is ONE composite, never
        a separately-painted grid layer (a second layer pans at its
        own pace). The 10 mm graduation's ink must sit in the
        raster itself."""
        from plugins.PlateQt import render_navigation_layer
        nav_view = {"width": 563, "height": 563, "scale": 1.0,
                    "lineScale": 0.7, "backing": 4.0,
                    "bedWidth": 250.0, "bedDepth": 250.0}
        raster = render_navigation_layer(
            {"prev": None, "current": PAYLOAD, "next": None},
            PROBE_PLOT, nav_view)
        backing = 4.0
        sx = float(PROBE_PLOT["sx"]) * 1.0 * backing
        # The 10 mm graduation at bed_x=110, sampled over a bed row
        # well away from the stroke's band (bed_y 125).
        col = int((110.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        row = int((float(PROBE_PLOT["bedYMax"]) - 60.0) * sx)
        ink = max(raster.pixelColor(c, row).alphaF()
                  for c in range(col - 2, col + 3))
        self.assertGreater(ink, 0.0,
                           "the warm raster lost its baked grid")

    def test_the_grid_and_the_geometry_pan_as_one_flat_raster(self):
        """The live report's pin: the warm raster is ONE flat image —
        the grid and the toolpaths shift by EXACTLY the same pixels
        through a pan. Two layers (a canvas grid over the raster)
        lag and shift differently."""
        from plugins.PlateQt import render_navigation_layer, png_file
        from PyQt6.QtCore import QPointF
        monitor, window, face = self._follower_popover()
        face.setProperty("lineScale", 0.7)
        face.setProperty("dot", None)
        self.pump(30)
        window.grabWindow()
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        plot = {"offsetX": float(bed["offsetX"]), "offsetY": float(bed["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(bed["bedXMin"]), "bedYMax": float(bed["bedYMax"])}
        nav_view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": 1.0, "lineScale": 0.7, "backing": 4.0,
                    "bedWidth": 250.0, "bedDepth": 250.0}
        # A short stroke right of the bed centre, clear of every
        # graduation: its right edge is the geometry's landmark.
        short = {"classes": {"WALL-OUTER": [
            [[152.0, 125.0, 0.0], [158.0, 125.0, 1.0]],
        ]}, "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 2}
        raster = render_navigation_layer(
            {"prev": None, "current": short, "next": None},
            plot, nav_view)
        png_file(raster, "/tmp/mpf", "parity-nav-grid")
        self._printer.setNavigation("/tmp/mpf/parity-nav-grid.png")
        self.pump(40)
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        sx = float(plot["sx"])
        fx = float(plot["offsetX"]) + (125.0 - float(plot["bedXMin"])) * sx
        fy = float(plot["offsetY"]) + (float(plot["bedYMax"]) - 125.0) * sx
        face.setProperty("viewScale", 4.0)
        face.setProperty("displayScale", 4.0)
        face.setProperty("_interactionActive", True)
        back = None

        def grab(pan_x):
            face.setProperty("displayPanX", pan_x)
            face.setProperty("displayPanY", 300 - (oy + 4.0 * fy))
            self._pump_ms(60)
            return window.grabWindow()

        def inked(image, col, row):
            nonlocal back
            if back is None:
                back = image.pixel(ox + 8, oy + 8)
            px = image.pixel(col, row)
            return max(abs(((px >> s) & 0xFF) - ((back >> s) & 0xFF))
                       for s in (0, 8, 16)) > 60

        def grid_col(image, pan_x):
            # The 50 mm graduation at bed_x=150, on a row off the
            # stroke's band; the scan follows THIS grab's pan.
            expected = ox + pan_x + 4.0 * (float(plot["offsetX"])
                                           + (150.0 - float(plot["bedXMin"])) * sx)
            for c in range(int(expected) - 14, int(expected) + 15):
                if inked(image, c, 310):
                    return c
            return None

        def stroke_right(image, pan_x):
            # The stroke's rightmost ink at the bed centre's row.
            expected = ox + pan_x + 4.0 * (float(plot["offsetX"])
                                           + (158.0 - float(plot["bedXMin"])) * sx)
            for c in range(int(expected) + 30, int(expected) - 40, -1):
                if inked(image, c, 300):
                    return c
            return None

        def painted(image, pan_x, timeout=15.0):
            """The same frame once the raster's own ink is on it. The
            warm raster is read off the file system, so a fixed pump is
            a host assumption: the macOS CI's slower read handed the
            censuses a scene with no raster at all. The stroke is the
            landmark — it is in the fixture at either pan — and a raster
            that never arrives still fails, on its own assertion."""
            import time
            deadline = time.monotonic() + timeout
            while stroke_right(image, pan_x) is None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
                image = window.grabWindow()
            return image

        pan_a = 300 - (ox + 4.0 * fx)
        first = painted(grab(pan_a), pan_a)
        col_grid_a = grid_col(first, pan_a)
        col_stroke_a = stroke_right(first, pan_a)
        pan_b = pan_a - 40.0
        second = painted(grab(pan_b), pan_b)
        col_grid_b = grid_col(second, pan_b)
        col_stroke_b = stroke_right(second, pan_b)
        self.assertIsNotNone(col_grid_a, "the grid never painted")
        self.assertIsNotNone(col_stroke_a, "the stroke never painted")
        self.assertAlmostEqual(col_grid_b - col_grid_a, -40.0, delta=2.0,
                               msg="the grid panned at its own pace")
        self.assertAlmostEqual(col_stroke_b - col_stroke_a, -40.0, delta=2.0,
                               msg="the stroke panned off the camera")
        self.assertEqual(col_grid_b - col_grid_a, col_stroke_b - col_stroke_a,
                         "the grid and the geometry are two layers")

    def test_the_navigation_raster_respects_every_split_state(self):
        """The review's finding: split=0 fell through to the whole
        layer painted as printed — scrubbing to 0% and panning showed
        the full layer as already printed. At 0% the warm raster must
        show NO printed geometry (only the grey base when enabled);
        the full and intermediate states keep their pictures, and a
        complete split draws the full layer."""
        from plugins.PlateQt import render_navigation_layer
        # A 21-motion stroke: a mid split paints a real prefix.
        stroke = [[20.0 + motion * 6.5, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [stroke]},
                   "travels": [], "travelStarts": [], "travelEnds": [],
                   "motions": 21}
        sx = float(PROBE_PLOT["sx"]) * 4.0
        row = int((float(PROBE_PLOT["bedYMax"]) - 125.0) * sx)
        col0 = int((20.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        col1 = int((150.0 - float(PROBE_PLOT["bedXMin"])) * sx)

        def render(split, show_base=True, show_travels=False):
            view = {"width": 563, "height": 563, "scale": 1.0,
                    "lineScale": 0.7, "backing": 4.0,
                    "bedWidth": 250.0, "bedDepth": 250.0,
                    "showBase": show_base, "showTravels": show_travels}
            return render_navigation_layer(
                {"prev": None, "current": payload, "next": None},
                PROBE_PLOT, view, split=split)

        def printed_excess(raster):
            # The WALL-OUTER red over the grey base/background:
            # printed ink carries a red excess, the base never does.
            excess = 0
            alpha = 0
            for r in range(row - 3, row + 4):
                for c in range(col0, col1 + 1):
                    px = raster.pixelColor(c, r)
                    alpha += px.alphaF()
                    excess += max(0, px.red() - px.green())
            return excess, alpha

        full = printed_excess(render(None))
        self.assertGreater(full[0], 0, "the full layer drew no printed ink")
        # 0% with the base: the grey silhouette remains (more ink
        # than the grid alone), NO printed ink.
        zero_base = printed_excess(render(0))
        zero_bare = printed_excess(render(0, show_base=False))
        self.assertGreater(zero_base[1], zero_bare[1],
                           "the 0% base never drew")
        self.assertEqual(zero_base[0], 0,
                         "0% paints the layer as printed")
        # 0% without the base: no printed ink either — the grid's
        # grey contributes no red excess.
        self.assertEqual(zero_bare[0], 0,
                         "0% without the base drew printed geometry")
        # An intermediate split draws the prefix only: less printed
        # ink than the full layer, more than none.
        partial = printed_excess(render(10))
        self.assertGreater(partial[0], 0, "the partial prefix never drew")
        self.assertLess(partial[0], full[0],
                        "the partial prefix drew the whole layer")
        # A complete split is the full layer's picture (the model
        # only ever passes a partial or None — the complete case
        # resolves to the full representation).
        complete = printed_excess(render(payload["motions"]))
        self.assertGreaterEqual(complete[0], full[0] * 0.9,
                                "the complete split lost the layer")

    def test_the_baked_grid_keeps_its_thickness_at_zoom(self):
        """The adaptive width: the grid pen painted at backing /
        zoom presents as the canvas's constant 1 px at this zoom —
        a zoom-4 raster's 10 mm graduation paints ~1-2 columns,
        never the fixed 4 the old baked grid left at 400% (the live
        report's thick-grid complaint)."""
        from plugins.PlateQt import render_navigation_layer
        nav_view = {"width": 563, "height": 563, "scale": 1.0,
                    "lineScale": 0.7, "backing": 4.0, "zoom": 4.0,
                    "bedWidth": 250.0, "bedDepth": 250.0}
        raster = render_navigation_layer(
            {"prev": None, "current": PAYLOAD, "next": None},
            PROBE_PLOT, nav_view)
        sx = float(PROBE_PLOT["sx"]) * 1.0 * 4.0
        # The vertical 10 mm graduation at bed_x=110, on a row
        # clear of the horizontal graduations and the stroke.
        col = int((110.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        row = int((float(PROBE_PLOT["bedYMax"]) - 63.0) * sx)
        per_col = [raster.pixelColor(c, row).alphaF()
                   for c in range(col - 6, col + 7)]
        peak = max(per_col)
        self.assertGreater(peak, 0, "the grid never painted")
        inked = [v for v in per_col if v > 0.05 * peak]
        self.assertLessEqual(len(inked), 3,
                             "the zoomed grid line kept the fixed width "
                             "(%d cols): %r" % (len(inked), per_col))

    def test_the_zoom_bar_keeps_the_viewport_centre_anchored(self):
        """The live ruling: the zoom bar's drag zooms around the
        viewport CENTRE — the scene point under the centre before
        the scale change maps back to the centre after (the wheel
        keeps its own cursor anchor). The bar's scopeApply retargets
        the pan through _barZoomPan; the pin drives the real scope
        drag and checks the invariant on the presented camera."""
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent
        monitor, window, face = self._follower_popover()
        face.setProperty("lineScale", 0.7)
        face.setProperty("dot", None)
        self.pump(30)
        window.grabWindow()
        cx = int(face.width() / 2)
        # A settled zoomed state (no ease, no pan).
        face.setProperty("viewScale", 1.5)
        face.setProperty("displayScale", 1.5)
        face.setProperty("viewPanX", 0.0)
        face.setProperty("viewPanY", 0.0)
        face.setProperty("displayPanX", 0.0)
        face.setProperty("displayPanY", 0.0)
        self.pump(30)

        def mouse(kind, x, y, buttons):
            scene = face.mapToItem(window.contentItem(), QPointF(x, y))
            event = QMouseEvent(kind, QPointF(scene),
                                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                                Qt.MouseButton.LeftButton, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)

        # The zoom scope docks with an 180 ms slide when the zoom
        # changes — wait out the slide, then read the REAL geometry
        # (the bar's position is what the press must hit).
        from PyQt6.QtQuick import QQuickItem
        scope = None
        for _ in range(30):
            self._pump_ms(30)
            candidates = [i for i in face.findChildren(QQuickItem)
                          if i.property("width") is not None
                          and 36.0 < float(i.property("width")) < 40.0
                          and i.property("height") is not None
                          and float(i.property("height")) > 40.0]
            if candidates:
                scope = candidates[0]
                break
        self.assertIsNotNone(scope, "the zoom scope never mounted")
        previous = float(scope.property("x"))
        for _ in range(30):
            self._pump_ms(30)
            now = float(scope.property("x"))
            if abs(now - previous) < 0.5:
                break
            previous = now
        # The docked scope's bar geometry: the marker rides the log
        # fraction, so a press on the marker's own y is a no-op zoom
        # and the move to the 20^0.2 fraction is the drag.
        import math
        scope_x = float(scope.property("x"))
        bar_h = float(scope.property("height")) - 24.0
        bar_top = 20.0

        def marker_y(scale):
            return bar_h * (1.0 - math.log(scale) / math.log(20.0)) - 1.5

        mouse(QEvent.Type.MouseButtonPress, scope_x + 19.0,
              bar_top + marker_y(1.5), Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, scope_x + 19.0,
              bar_top + marker_y(1.82), Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, scope_x + 19.0,
              bar_top + marker_y(1.82), Qt.MouseButton.NoButton)
        self._pump_ms(60)
        target = face.property("viewScale")
        self.assertAlmostEqual(target, 20.0 ** 0.2, delta=0.1,
                               msg="the bar drag never zoomed")
        # The centre anchor invariant: the scene point that sat under
        # the viewport centre at 1.5x must still sit under it now —
        # exact for any landed scale (the retarget's identity).
        scene_cx = cx / 1.5
        col = face.property("viewPanX") + scene_cx * target
        self.assertAlmostEqual(col, float(cx), delta=2.0,
                               msg="the bar zoom drifted off the centre "
                                   "anchor (col %s)" % col)
        for _ in range(60):
            self._pump_ms(30)
            if not face.property("_interactionActive"):
                break
        self.assertFalse(face.property("_interactionActive"),
                         "the bar drag never settled")

    def test_nearby_strokes_stay_separate_through_the_raster(self):
        """The Day-scale fixture: two strokes 0.5 mm apart must stay
        separated once the zoom resolves the gap beyond the stroke —
        the rejected floor widened the subpixel line into the
        neighbouring stroke's gap."""
        from plugins.PlateQt import render_navigation_layer
        two = {"classes": {"WALL-OUTER": [
            LINE[0],
            [[20.0, 125.5, 2.0], [150.0, 125.5, 3.0]],
        ]}, "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 4}
        # The view's image must hold the zoomed content: the bed at
        # 6x spans 250 mm x 13.5 px/mm = 3375 px, so the canvas is
        # sized to fit (the production pipeline either paints the
        # bed fit or culls off-view segments — this fixture must
        # never paint out of the image's bounds).
        nav_view = {"width": 3380, "height": 3380, "scale": 6.0,
                    "lineScale": 0.7, "backing": 1.0}
        raster = render_navigation_layer(
            {"prev": None, "current": two, "next": None},
            PROBE_PLOT, nav_view)
        backing = 1.0
        sx = float(PROBE_PLOT["sx"]) * 6.0 * backing
        row = lambda bed_y: int((float(PROBE_PLOT["bedYMax"]) - bed_y) * sx)
        col0 = int((20.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        col1 = int((150.0 - float(PROBE_PLOT["bedXMin"])) * sx)
        lower_row = row(125.0)
        upper_row = row(125.5)
        gap_row = (lower_row + upper_row) // 2

        def row_ink(r):
            return sum(raster.pixelColor(c, r).alphaF()
                       for c in range(col0, col1 + 1))

        self.assertGreater(row_ink(lower_row), 0,
                           "the lower stroke never painted")
        self.assertGreater(row_ink(upper_row), 0,
                           "the upper stroke never painted")
        self.assertLess(row_ink(gap_row), row_ink(lower_row) * 0.5,
                        "the 0.5 mm gap reads as ink through the raster")
