"""Executable qml plate interaction contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_software_event_glyphs_only_draw_after_playback_reaches_them(self):
        _monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("gpuRendering", False)
        face.setProperty("showRetractions", True)
        face.setProperty("showUnretractions", True)
        payload = {"classes": {}, "travels": [], "motions": 5,
                   "retractions": [(91, 97, 0)], "unretractions": [(151, 97, 2)]}
        layer = self._native_layer(payload, face, prefix_split=0)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(0)
        self._pump_ms(100)
        empty = self._settled_frame(window, face)
        # A hollow 1 px glyph can fall entirely between a four-pixel
        # census grid on another platform. Inspect every painted pixel.
        def diff(image, baseline):
            return self._pixel_diff(image, baseline, face, window, sample_step=1)
        self._printer.setSplit(1)
        self._pump_ms(100)
        retracted = self._settled_frame(window, face, painted=lambda image: diff(image, empty) > 0)
        self.assertGreater(diff(retracted, empty), 0)
        self._printer.setSplit(2)
        self._pump_ms(100)
        before_prime = self._settled_frame(window, face)
        self.assertEqual(diff(before_prime, retracted), 0)
        self._printer.setSplit(3)
        self._pump_ms(100)
        primed = self._settled_frame(window, face, painted=lambda image: diff(image, before_prime) > 0)
        self.assertGreater(diff(primed, before_prime), 0)
        self._printer.setSplit(0)
        self._pump_ms(100)
        self.assertEqual(diff(self._settled_frame(window, face, painted=lambda image: diff(image, empty) == 0), empty), 0)

    def test_the_native_prefix_and_the_qml_tail_match_intensity_at_production_width(self):
        # The review's finding #3, closed: at the production
        # lineScale (0.7 — subpixel strokes) the native prefix and
        # the QML canvas tail carry the SAME perceived intensity
        # over equivalent pieces of the same path, in every
        # orientation, at both backing factors. The mechanism (the
        # measured evidence): the two painters' subpixel coverage is
        # IDENTICAL — the apparent 2x deficit (peak 42 vs 21) was
        # the canvas's un-trimmed FULL bitmap stacked UNDER the
        # prefix, doubling the prefix region's ink. The prefix's
        # show now forces the settled single-owner trim, so the
        # canvas repaints to the tail alone (the settled ownership
        # record proves it: _vectorCoversFrom == prefixSplit). The
        # fix touches no alpha, no colour and no width — only the
        # composition's ownership.
        for dpr in (1.0, 2.0):
            for orientation in ("horizontal", "vertical", "diagonal"):
                self._parity_leg(dpr, orientation)

    def test_a_prefix_that_never_loads_releases_the_wheel_hold(self):
        # The exit barrier's failed-load escape. A prefix whose image
        # source does not exist can never reach Ready, so a barrier
        # that waits only on the model side can never pass: the warm
        # raster held the picture, and because an interaction freezes
        # the model's publications, the fresh prefix the barrier was
        # waiting for sat behind the very hold waiting for it. A
        # wheel-only gesture has no release to break that cycle, so
        # the face stayed on the warm raster for good (the live
        # report). Nothing but the wheel may end it here.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "backing": 4.0,
                "bedWidth": 250.0, "bedDepth": 250.0}
        from mpf.PlateQt import (png_file, render_layer_prefix,
                                     render_navigation_layer)
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot, view,
            split=18)
        self._printer.setNavigation(png_file(
            nav, "/tmp/mpf/raster-probe",
            "nav-wedge-%d" % harness.time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        self._wait_red(window, face, want=True)
        # The model's prefix is rendered and valid; its URL is the
        # part that is missing, so the scene-graph Image can never
        # reach Ready however long the barrier waits.
        prefix = render_layer_prefix(payload, plot, view, 10)
        layer.set_prefix(
            prefix, "file:///tmp/mpf/raster-probe/missing-%d.png"
            % harness.time.monotonic_ns(), 10, "fixture-key")
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self.pump(20)

        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        self._printer.calls.clear()

        def wheel(cx, cy, delta):
            # QTest's QWindow-level mouseWheel is unavailable in this
            # Qt build — post the real event, as the drag tests do.
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()),
                                                  int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)

        wheel(int(face.width() / 2), int(face.height() / 2), 120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the wheel never entered the interaction")
        # No release is ever sent and the prefix never loads: whatever
        # the barrier's verdict, the hold has to release by itself.
        deadline = harness.time.monotonic() + 15.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(
            face.property("_interactionActive"),
            "a prefix that can never load held the warm raster for good")
        self.assertIn(
            ("interacting", False), self._printer.calls,
            "the hold released without resuming the model's publications")

    def test_a_split_that_lands_mid_gesture_is_still_painted(self):
        # The deferral's DEMAND. While a gesture owns the picture the
        # progress repaint defers by design — the pan presents one
        # fixed frame, and new lines mid-pan read as jank — so the key
        # check that notices the advance has to leave the demand
        # standing rather than the record of it. Recording the key
        # along with the deferral told the settle's own key check the
        # advance had already been met, and every path that lands a
        # deferred repaint gates on the dirty flag that branch never
        # set, so a wheel zoom that outlived a poll issued its catch-up
        # paint never: the canvas stopped painting, its
        # delivered-coverage record froze where the gesture began, and
        # the exit barrier waited on a coverage no paint would ever
        # deliver (the live zoom stick, released only when some
        # unrelated paint happened to land). The painted split is the
        # canvas's own record that a paint ran.
        monitor, window, face, payload = self._mount_interaction_fixture()
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent

        def wheel(cx, cy, delta):
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()),
                                                  int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)

        wheel(int(face.width() / 2), int(face.height() / 2), 120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the wheel never entered the interaction")
        # The advance lands while the gesture still owns the picture.
        self._printer.setSplit(20)
        self._pump_ms(30)
        self.assertTrue(
            face.property("_interactionActive"),
            "the advance outlived the gesture — nothing was deferred")
        painted = False
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            if face.property("_lastSplit") == 20:
                painted = True
                break
            self._pump_ms(30)
        self.assertTrue(
            painted, "the deferred advance was never painted")

    def test_a_camera_gesture_leaves_the_hidden_mapping_canvas_alone(self):
        # The mapping canvas IS the gesture's own picture input: the pan
        # and the zoom are baked into its paint, so every camera step
        # requests a whole-canvas repaint — and the face switches the
        # canvas out (opacity 0, never visibility) for the whole gesture,
        # because the warm raster presents the grid. An opacity-zero
        # canvas still rasterises and re-uploads its entire texture for a
        # picture nothing composites, so the steps must leave it alone;
        # the restore must repaint it ONCE, or the grid returns at the
        # transform the gesture started from.
        monitor, window, face, payload = self._mount_interaction_fixture()
        from PyQt6.QtQuick import QQuickItem
        grid = face.findChild(QQuickItem, "moonrakerPlateCanvas")
        self.assertIsNotNone(grid, "the mapping canvas never mounted")
        # The pan is the zoomed-in gesture (the handler returns at the
        # 100% fit): the camera starts where a viewer would have it.
        face.setProperty("viewScale", 2.0)
        face.setProperty("displayScale", 2.0)
        self.pump(20)
        self.assertEqual(grid.property("opacity"), 1.0,
                         "the idle mapping is not on screen")
        # The baseline is a SETTLED word, not one read the instant the
        # pump ends: the threaded canvas services a request a frame later
        # (the harness's own lesson — two grabs of a canvas that has not
        # painted yet agree perfectly), so a count taken while the mount's
        # paints are still owed is a word the drag can then move without
        # the hidden canvas painting anything, and the restore's arrival
        # would be read off one of them. Quiet, bounded: a count that
        # stops advancing through a real pump is the serviced one.
        settle_deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < settle_deadline:
            settled = grid.property("_paints")
            self._pump_ms(120)
            if grid.property("_paints") == settled:
                break
        painted = grid.property("_paints")
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QGuiApplication, QMouseEvent

        def mouse(kind, x, y, buttons):
            faced = harness.Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove \
                else harness.Qt.MouseButton.LeftButton
            scene = face.mapToItem(window.contentItem(), harness.QPointF(x, y))
            event = QMouseEvent(
                kind, harness.QPointF(scene),
                harness.QPointF(window.mapToGlobal(harness.QPoint(int(scene.x()), int(scene.y())))),
                faced, buttons, harness.Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)

        # A drag that pans for real: every move writes viewPanX — the
        # mapping's own paint input — while the canvas is switched out.
        mouse(QEvent.Type.MouseButtonPress, cx, cy, harness.Qt.MouseButton.LeftButton)
        for step in range(1, 7):
            mouse(QEvent.Type.MouseMove, cx + step * 4, cy + step * 3,
                  harness.Qt.MouseButton.LeftButton)
            self._pump_ms(20)
        self.assertTrue(face.property("_interactionActive"),
                        "the drag never entered the interaction")
        self.assertEqual(grid.property("opacity"), 0.0,
                         "the mapping stayed on screen mid-gesture")
        self.assertEqual(grid.property("_paints"), painted,
                         "the hidden mapping repainted during the gesture")
        mouse(QEvent.Type.MouseButtonRelease, cx + 24, cy + 18,
              harness.Qt.MouseButton.NoButton)
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the pan gesture never settled back to the exact scene")
        self.assertEqual(grid.property("opacity"), 1.0,
                         "the restored mapping is not on screen")
        # The restore's repaint is REQUESTED when the opacity lands and
        # painted a frame later on the render thread, so the count is
        # waited for as an ARRIVAL with a bounded deadline — not read
        # inside the barrier's own window, where it was a claim about the
        # machine's speed: CI read exactly the baseline's 2 there, while
        # a quieter host serviced the paint inside the same beat. The pin
        # itself is unchanged: a restore that never repaints still fails
        # here, 5 s later.
        restore_deadline = harness.time.monotonic() + 5.0
        while (harness.time.monotonic() < restore_deadline
               and grid.property("_paints") <= painted):
            self._pump_ms(30)
        self.assertGreater(grid.property("_paints"), painted,
                           "the restored mapping never repainted the "
                           "transform the pan left")

    def test_the_pending_canvas_skips_a_paint_it_would_leave_empty(self):
        # With the native grey sibling up the vector base fallback draws
        # NOTHING — yet the split advances on every poll, and each
        # request cleared and re-uploaded the whole canvas texture for no
        # pixels. The request must skip that paint, and the composition's
        # arrival word (the key the pending canvas has painted) must
        # still advance: the exact scene's readiness barrier reads it.
        monitor, window, face, payload = self._mount_interaction_fixture()
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        # The fixture's layer carries a native grey base, so the fallback
        # has nothing to draw while it stands.
        self.assertTrue(face.property("showBase"),
                        "the fixture is not in the base state")
        self.assertTrue(QMetaObject.invokeMethod(face, "available",
                                                 Q_RETURN_ARG(QVariant)),
                        "the fixture is not available")
        self.assertFalse(QMetaObject.invokeMethod(
            face, "_pendingDraws", Q_RETURN_ARG(QVariant)),
            "the fixture's fallback has pixels to draw")
        painted = face.property("_pendingPaints")
        # The split advance is the per-poll key change that used to buy a
        # cleared canvas: the request skips the paint and records the key.
        self._printer.setSplit(19)
        self.pump(30)
        self.assertEqual(face.property("_pendingPaints"), painted,
                         "the empty fallback repainted on a split advance")
        asked = QMetaObject.invokeMethod(face, "_pendingKeyOf",
                                        Q_RETURN_ARG(QVariant))
        self.assertEqual(face.property("_lastPendingKey"), asked,
                         "the skipped paint never recorded its key — the "
                         "exact scene's barrier would wait forever")
        # The converse, so the skip cannot pass vacuously: a layer with no
        # native base must still DRAW the fallback (the pre-arrival path).
        empty = self._native_layer(
            {"classes": {"WALL-OUTER": [[[10.0 + i * 5.0, 100.0, float(i)]
                                         for i in range(4)]]},
             "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21},
            face, grey=False)
        self.assertFalse(empty.baseValid, "the empty fixture shipped a base")
        self._printer.setLayers({"prev": None, "current": empty, "next": None})
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline \
                and face.property("_pendingPaints") == painted:
            self.pump(10)
        self.assertGreater(face.property("_pendingPaints"), painted,
                           "the vector base fallback never painted")

    def test_the_interaction_raster_owns_the_camera_and_swaps_atomically(self):
        # The camera interaction: a warm navigation raster owns the
        # heavy scene during a smooth wheel zoom (the exact fades as
        # ONE unit), the display eases monotonically toward the
        # target with the focal bed point pinned, the exact scene
        # returns only once its commit barrier passes, and the swap
        # moves no geometry.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        # The warm interaction raster: a real flattened composite at
        # 4x, its URL on the double (the model's role).
        from mpf.PlateQt import render_navigation_layer, png_file
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        self._printer.setNavigation(png_file(
            nav, "/tmp/mpf/raster-probe", "nav-fixture-%d" % harness.time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()  # the idle exact scene is up

        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

        def wheel(cx, cy, delta):
            # QTest's QWindow-level mouseWheel is unavailable in this
            # Qt build — post the real event (the harness's own
            # pattern for the drag injection).
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)
        # The toolhead dot rides the camera the scene actually shows:
        # during the eased zoom it must track the DISPLAY transform
        # (the warm raster's picture), never the target (the live
        # report — the dot snapped against the raster).
        face.setProperty("attached", True)
        face.setProperty("dot", {"x": 60.0, "y": 100.0, "valid": True})
        self.pump(10)
        from PyQt6.QtQuick import QQuickItem
        dot_item = face.findChild(QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNotNone(dot_item, "the toolhead dot never mounted")
        plot_bed = plot_value["bed"]

        def dot_screen_x(scale, pan_x):
            return (pan_x + (float(plot_bed["offsetX"])
                             + (60.0 - float(plot_bed["bedXMin"])) * float(plot_value["sx"])) * scale
                    - dot_item.width() / 2)

        def dot_screen_y(scale, pan_y):
            return (pan_y + (float(plot_bed["offsetY"])
                             + (float(plot_bed["bedYMax"]) - 100.0) * float(plot_value["sy"])) * scale
                    - dot_item.height() / 2)
        # Wheel-zoom in at the centre: the interaction owns the scene
        # immediately and the display eases toward the 125% target.
        wheel(cx, cy, 120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the camera gesture never entered the interaction")
        first = self._wait_display_scale(face, 1.0)
        self.assertGreater(first, 1.0, "the first frame never moved")
        self.assertLess(first, 1.25, "the display snapped, never eased")
        # The eased frames: monotonic, no overshoot, the focal bed
        # point pinned, and the exact scene hidden throughout.
        last = first
        before_swap = window.grabWindow()
        while face.property("_interactionActive"):
            self._pump_ms(20)
            now = face.property("displayScale")
            self.assertGreaterEqual(now, last - 1e-6,
                                    "the display eased backwards")
            self.assertLessEqual(now, 1.25 + 1e-6,
                                 "the display overshot the target")
            last = now
            bed_x = (cx - face.property("displayPanX")) / now
            self.assertAlmostEqual(bed_x, float(cx), delta=2.0,
                                   msg="the focal point wandered")
            self.assertAlmostEqual(
                dot_item.x(),
                dot_screen_x(now, face.property("displayPanX")),
                delta=1.5, msg="the toolhead dot snapped off the display")
            self.assertAlmostEqual(
                dot_item.y(),
                dot_screen_y(now, face.property("displayPanY")),
                delta=1.5, msg="the toolhead dot snapped off the display")
            before_swap = window.grabWindow()  # the last interaction frame
        self.assertEqual(face.property("displayScale"), 1.25,
                         "the display never converged exactly")
        # The atomic swap: the frames immediately before and after
        # it are registered identically — only the fidelity changes.
        after = window.grabWindow()
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def differs(pixel_a, pixel_b):
            return any(abs(((pixel_a >> shift) & 0xFF)
                          - ((pixel_b >> shift) & 0xFF)) > 60
                       for shift in (0, 8, 16))
        moved = sum(
            1 for row in range(0, int(face.height()), 4)
            for col in range(0, int(face.width()), 4)
            if differs(after.pixel(int(origin.x()) + col,
                                   int(origin.y()) + row),
                       before_swap.pixel(int(origin.x()) + col,
                                         int(origin.y()) + row)))
        self.assertLessEqual(moved, 8,
                             "the swap moved the geometry (%d pixels)" % moved)

        # The direct pan: the display follows the pointer exactly —
        # no easing, no camera lag, the interaction stays live.
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent
        def mouse(kind, x, y, buttons):
            # A move is about NO button: carrying one makes Qt read it
            # as a fresh press, which re-selects the target mid-drag and
            # hands the grab to whatever animated control arrived under
            # the pointer. The held mask still rides `buttons`.
            faced = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove \
                else Qt.MouseButton.LeftButton
            scene = face.mapToItem(window.contentItem(), QPointF(x, y))
            event = QMouseEvent(kind, QPointF(scene),
                                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                                faced, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)
        pan_before = face.property("displayPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 40, cy + 20, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 40, cy + 20,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertAlmostEqual(face.property("displayPanX"), pan_before + 40.0,
                               delta=1.5, msg="the drag pan lagged the pointer")
        self.assertTrue(face.property("_interactionActive"),
                        "the pan never entered the interaction")
        # The retarget from the CURRENT display: the next wheel keeps
        # the eased motion continuous (no restart jump backwards).
        wheel(cx, cy, 120)
        self._pump_ms(20)
        retargeted = self._wait_display_scale(face, 1.25)
        self.assertGreater(retargeted, 1.25, "the retarget snapped backwards")
        self.assertLess(retargeted, 1.5625 + 1e-6,
                        "the retarget overshot its new target")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the retargeted zoom never swapped back")
        # A drag DURING the eased zoom: the grab pans directly while
        # the zoom keeps easing, and the eased pan carries the drag's
        # delta — the ease lands exactly on the dragged target, no
        # pause, no snap, no permanent offset.
        wheel(cx, cy, 120)
        self._pump_ms(10)
        pressed_scale = face.property("displayScale")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 30, cy + 15, Qt.MouseButton.LeftButton)
        self._pump_ms(40)  # ticks fire while the button stays down
        # The tick rides the host's frame clock: the fixed beat read the
        # pressed scale exactly on the macOS CI's two cores, so the read
        # waits for the tick. A zoom that really paused never passes the
        # wait and still fails.
        held = self._wait_display_scale(face, pressed_scale)
        self.assertGreater(held, pressed_scale,
                           "the zoom paused while the pointer held the scene")
        mouse(QEvent.Type.MouseButtonRelease, cx + 30, cy + 15,
              Qt.MouseButton.NoButton)
        released = face.property("displayScale")
        self._pump_ms(50)  # a few animator ticks past the release
        self.assertGreater(self._wait_display_scale(face, released), released,
                           "the zoom stopped gliding after the release")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the drag-interrupted zoom never swapped back")
        self.assertEqual(face.property("viewScale"), 1.953125,
                         "the zoom never reached its wheel target")
        self.assertAlmostEqual(face.property("displayPanX"),
                               face.property("viewPanX"), delta=1.5,
                               msg="the eased pan never converged to the "
                                   "dragged target")
        # A pan-only gesture: the release's re-check drives the
        # barrier — the interaction ends without any zoom.
        pan_before = face.property("viewPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 25, cy + 10, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 25, cy + 10,
              Qt.MouseButton.NoButton)
        self.assertAlmostEqual(face.property("viewPanX"), pan_before + 25.0,
                               delta=1.5, msg="the pan-only drag lost its delta")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the pan-only gesture never settled back to the "
                         "exact scene")
        # A wheel DURING a held drag zooms properly: the target
        # updates and the display eases about the wheel's cursor —
        # the focal bed point stays pinned even with the button down
        # (the live report's held origin-zoom).
        before_wheel = face.property("viewScale")
        held_cx = cx - 60
        held_cy = cy - 40
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, held_cx, held_cy, Qt.MouseButton.LeftButton)
        wheel(held_cx, held_cy, 120)
        self.assertGreater(face.property("viewScale"), before_wheel,
                           "the wheel was inert while the drag was held")
        display_before_move = face.property("displayPanX")
        target_before_move = face.property("viewPanX")
        mouse(QEvent.Type.MouseMove, held_cx + 25, held_cy + 10,
              Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(face.property("displayPanX"),
                               display_before_move + 25.0, delta=1.5,
                               msg="the held drag snapped to the zoom target")
        self.assertAlmostEqual(face.property("viewPanX"),
                               target_before_move + 25.0, delta=1.5,
                               msg="the held drag revived the pre-zoom pan")
        bed_x0 = ((held_cx + 25 - face.property("displayPanX"))
                  / face.property("displayScale"))
        for _beat in range(20):  # the ease runs while the button stays down
            self._pump_ms(20)
            bed_x = ((held_cx + 25 - face.property("displayPanX"))
                     / face.property("displayScale"))
            self.assertAlmostEqual(bed_x, bed_x0, delta=2.0,
                                   msg="the held wheel zoomed from the origin")
        mouse(QEvent.Type.MouseButtonRelease, held_cx + 25, held_cy + 10,
              Qt.MouseButton.NoButton)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the held-wheel gesture never settled back")
        self.assertAlmostEqual(face.property("displayPanX"),
                               face.property("viewPanX"), delta=1.0,
                               msg="the camera snapped after the held wheel settled")
        # Repeated drags: the display pan and the target pan stay in
        # lockstep at every boundary — a drag must never snap at its
        # start, and never snap back at its end.
        for dx, dy in ((30, 15), (-30, -15), (20, -10), (-20, 10)):
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the camera drifted out of lockstep "
                               "between drags")
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's press snapped the camera")
            mouse(QEvent.Type.MouseMove, cx + dx, cy + dy, Qt.MouseButton.LeftButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's move broke the lockstep")
            mouse(QEvent.Type.MouseButtonRelease, cx + dx, cy + dy,
                  Qt.MouseButton.NoButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's release snapped the camera")
            deadline = harness.time.monotonic() + 3.0
            while harness.time.monotonic() < deadline and face.property("_interactionActive"):
                self._pump_ms(30)
            self.assertFalse(face.property("_interactionActive"),
                             "a repeated drag never settled back")
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the settled camera ended out of lockstep")
        # The delayed exact: invalidate the exact scene's key, wheel
        # again — the interaction stays on the navigation raster
        # until the barrier can pass.
        layer.set_expected_key("invalidated")
        wheel(cx, cy, 120)
        self._pump_ms(120)
        self.assertTrue(face.property("_interactionActive"),
                        "the incomplete exact scene revealed itself")
        layer.set_expected_key("fixture-key")
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the completed exact scene never swapped back")
        # A corner-panned camera must NOT snap on the next wheel: the
        # pan follows the focal computation exactly — the bed's edge
        # may leave the viewport; only the 100% fit recentres (the
        # live ruling). The harness's synthetic moves dispatch by
        # POSITION (no grab routing), and the docked scope covers
        # the face's right edge — so each gesture moves +200, three
        # times: +600 lands the pan decisively past the bed's
        # coverage.
        for _drag in range(3):
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx + 200, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx + 200, cy,
                  Qt.MouseButton.NoButton)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the corner pan never settled")
        self.assertGreater(face.property("viewPanX"), 0.0,
                           "the corner pan never left the clamp range")
        pan_before = face.property("viewPanX")
        scale_before = face.property("viewScale")
        wheel(cx, cy, 120)
        expected_pan = (cx - (cx - pan_before) / scale_before
                        * face.property("viewScale"))
        self.assertAlmostEqual(face.property("viewPanX"), expected_pan,
                               delta=1e-3,
                               msg="the wheel clamped the corner-panned camera")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the corner zoom never settled")
        # The soft clamp: once the bed would leave the viewport
        # wholly, the pan stops with 100 px of it visible on every
        # side — dragging and zooming alike (the live ruling). Two
        # inside-face drags overshoot the boundary.
        scale = face.property("viewScale")
        hard_x = face.width() - 100.0 - float(plot_bed["offsetX"]) * scale
        for _drag in range(2):
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx + 200, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx + 200, cy,
                  Qt.MouseButton.NoButton)
        self.assertAlmostEqual(face.property("viewPanX"), hard_x, delta=1e-3,
                               msg="the drag never stopped at the soft clamp")
        self.assertAlmostEqual(face.property("displayPanX"),
                               face.property("viewPanX"), delta=1.0,
                               msg="the clamp broke the drag's lockstep")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the clamped drag never settled")
        # The wheel's side: a programmatic far camera (the harness
        # cannot drag the pointer that far), then a wheel — the
        # focal pan stops at the same soft boundary on both axes.
        hard_y = face.height() - 100.0 - float(plot_bed["offsetY"]) * scale
        face.setProperty("viewPanX", hard_x + 2000.0)
        face.setProperty("viewPanY", hard_y + 2000.0)
        face.setProperty("displayPanX", hard_x + 2000.0)
        face.setProperty("displayPanY", hard_y + 2000.0)
        self.pump(20)
        wheel(cx, cy, 120)
        # The clamp's boundary rides the NEW target's scale.
        new_scale = face.property("viewScale")
        hard_x2 = face.width() - 100.0 - float(plot_bed["offsetX"]) * new_scale
        hard_y2 = face.height() - 100.0 - float(plot_bed["offsetY"]) * new_scale
        self.assertAlmostEqual(face.property("viewPanX"), hard_x2, delta=1e-3,
                               msg="the wheel broke the soft clamp")
        self.assertAlmostEqual(face.property("viewPanY"), hard_y2, delta=1e-3,
                               msg="the wheel broke the soft clamp")
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the clamped wheel never settled")
        # A release OUTSIDE the face (the pointer left the area
        # mid-drag) cancels the grab: the exit drive must fire all
        # the same (the live wedge — the scene once stayed on the
        # warm raster forever).
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx - 200, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 400, cy,
              Qt.MouseButton.NoButton)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "an outside-face release never left the warm raster")
        # Zooming back out to 100% is the ONE snap the camera
        # allows: the fit fills the viewport, centred.
        for _down in range(8):
            wheel(cx, cy, -120)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertEqual(face.property("viewScale"), 1.0,
                         "the zoom-out never landed on the fit")
        self.assertEqual(face.property("viewPanX"), 0.0,
                         "the fit never recentred")
        self.assertEqual(face.property("viewPanY"), 0.0,
                         "the fit never recentred")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_gesture_latches_its_navigation_source_against_mid_gesture_retirement(self):
        # The review's lifecycle: the model retires the published URL
        # the moment the demand moves. The face must keep presenting
        # the raster the gesture ENTERED with (a mid-gesture
        # retirement never unloads the scene in hand), and the idle
        # binding must not admit the stale URL for the NEXT gesture.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        from mpf.PlateQt import render_navigation_layer, png_file
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        nav_url = png_file(nav, "/tmp/mpf/raster-probe",
                           "nav-latch-%d" % harness.time.monotonic_ns())
        self._printer.setNavigation(nav_url)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()

        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

        def wheel(delta):
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)

        def settle():
            deadline = harness.time.monotonic() + 3.0
            while harness.time.monotonic() < deadline and face.property("_interactionActive"):
                self._pump_ms(30)
            self.assertFalse(face.property("_interactionActive"),
                             "the gesture never settled")

        wheel(120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the ready raster never entered the interaction")
        self.assertEqual(face.property("_gestureNavSource"), nav_url,
                         "the entry never latched its source")
        # The model retires the URL mid-gesture (a demand change):
        self._printer.setNavigation("")
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the mid-gesture retirement ended the interaction")
        self.assertEqual(face.property("_gestureNavSource"), nav_url,
                         "the mid-gesture retirement unlatched the scene")
        settle()
        # The idle binding now reads the retired URL: the next
        # gesture must NOT enter the warm raster.
        wheel(120)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a retired URL admitted the stale warm raster")


