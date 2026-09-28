"""Executable qml plate navigation contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_loading_placeholder_transitions_do_not_create_polish_loops(self):
        monitor, window, face = self._follower_popover()
        start = len(harness._APPLICATION["messages"])
        for width in (900, 650, 1100):
            window.setWidth(width)
            for busy, reason in ((False, ""), (True, ""),
                                 (True, "Preparing layer…"), (False, "")):
                self._printer.setImprovingEta(busy)
                face.setProperty("progress", {"available": False, "reason": reason})
                self._pump_ms(80)
                self.assertFalse(window.grabWindow().isNull())
            face.setProperty("progress", self.PAYLOAD)
            face.setProperty("_fullRasterSeen", True)
            face.setProperty("_standingFull", None)
            self._pump_ms(80)
        messages = harness._APPLICATION["messages"][start:]
        self.assertEqual([], [line for line in messages
                              if "polish" in line.lower() or "binding loop" in line.lower()
                              or "TypeError" in line])

    def test_colour_keys_preserve_canvas_geometry_and_paint_gradients(self):
        from PyQt6.QtCore import QPointF
        monitor, window, face = self._follower_popover()
        controls = self.find(monitor, "moonrakerFollowerColourControls")
        face.setProperty("colourRanges", {
            "speed": [10, 100], "height": [0.1, 0.3],
            "width": [0.2, 0.8], "flow": [1, 20],
        })
        geometries = []
        for mode in range(6):
            face.setProperty("colourScheme", {
                "mode": mode, "materials": ["#ff0000", "#00ff00", "#0000ff"],
            })
            self._pump_ms(80)
            geometries.append((face.width(), face.height(), controls.height()))
            if mode >= 2:
                bar = self.find(controls, "moonrakerFollowerColourGradient")
                self.assertGreater(bar.width(), 100)
                self.assertGreater(bar.height(), 0)
                image = window.grabWindow()
                origin = bar.mapToItem(window.contentItem(), QPointF(0, 0))
                ratio = image.devicePixelRatio()
                y = int((origin.y() + bar.height() / 2) * ratio)
                colours = {image.pixel(int((origin.x() + bar.width() * fraction) * ratio), y)
                           for fraction in (0.1, 0.3, 0.6, 0.9)}
                self.assertGreaterEqual(len(colours), 3,
                                        "the gradient strips did not paint distinct colours")
        self.assertEqual(1, len(set(geometries)),
                         "selecting a colour scheme reflowed the canvas: %r" % geometries)

    def test_the_picker_canvas_never_reflows_on_hover(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plate")
        face = self.find(monitor, "moonrakerPlateExcludeFace")
        height = self._settled_height(face)
        face.setProperty("hoveredName", "Window_Support_Material_0")
        self.assertEqual(self._settled_height(face), height,
                         "hovering reflowed the canvas")
        # A very long name elides into the same single line.
        face.setProperty("hoveredName", "Window_Support_Material_0" * 6)
        self.assertEqual(self._settled_height(face), height,
                         "a long hover name reflowed the canvas")
        face.setProperty("hoveredName", "")
        self.assertEqual(self._settled_height(face), height,
                         "un-hovering reflowed the canvas")

    def test_popover_retains_geometry_across_split_and_raster_updates(self):
        monitor, window, face = self._follower_popover()
        printer = self._printer
        printer.setScrub(self.PAYLOAD["layers"]["current"])
        self.pump(30)
        reads = printer.scrub_reads
        for split in (3, 5, 7, 9):
            printer._split = split
            printer.plateSplitChanged.emit()
            printer.plateProgressChanged.emit()
            printer.plateLayersChanged.emit()
            self.pump(10)
        self.assertEqual(printer.scrub_reads, reads,
                         "volatile progress fetched the full Python geometry again")
        printer.setScrub(dict(self.PAYLOAD["layers"]["current"], motions=22))
        self.pump(10)
        self.assertGreater(printer.scrub_reads, reads)

    def test_a_mounted_face_animates_only_while_a_load_is_busy(self):
        """The download action's animations hang off ONE model gate.

        `PlateDownloadAction.busy()` reads the model's `improvingEta`
        and its hourglass and sweep animations run while that is true.
        The gate is a model property, so the rig's printer double has to
        publish it the way the model does: a property a binding cannot
        resolve reads as undefined, the `running:` binding never turns
        the animation off, and a mounted but idle face then ticks the
        animation driver at ~62 Hz (and forces ~18 window frames a
        second) for as long as the popover is open. Both halves are
        pinned: idle animates nothing, a busy load still animates."""
        monitor, window, face = self._follower_popover()
        self.pump(60)
        # The idle half is read from a quiet driver: the mount's own
        # layout passes tick it while they settle (see the helper).
        self._wait_for_a_quiet_driver(window)
        printer = self._printer
        self.assertFalse(printer.property("improvingEta"),
                         "the fixture starts with nothing loading")
        self.assertEqual(self._running_animations(face), [],
                         "the face animates while nothing loads")
        busy_ticks = self._driver_ticks(window, 600)
        self.assertEqual(busy_ticks, 0,
                         "the idle face ticked the animation driver %d times"
                         % busy_ticks)
        # The gate itself is live: a load in progress still animates, so
        # the pin cannot pass by the animations being broken outright.
        printer.setImprovingEta(True)
        self.pump(30)
        self.assertTrue(self._running_animations(face),
                        "a busy load left the action without its animations")
        self.assertGreater(self._driver_ticks(window, 600), 0,
                           "the busy action never ticked the driver")
        printer.setImprovingEta(False)
        self.pump(30)
        self.assertEqual(self._running_animations(face), [],
                         "the finished load left the action animating")

    def test_the_jump_button_recentres_the_view_on_the_toolhead(self):
        monitor, window, face = self._follower_popover()
        plot = face.findChild(harness.QQuickItem, "moonrakerPlateCanvas").property("_plot")
        self.assertIsNotNone(plot, "the bed mapping never built")
        self._fill_zoom(face, plot)
        # The toolhead controls hide in place on a row that persists —
        # the zoom no longer reflows the face (the live request), so
        # the geometry settles immediately. A grab forces the sync
        # (the harness's window doctrine).
        window.grabWindow()
        self.pump(30)
        # The toolhead at the bed's centre: the view is unpanned, so at
        # this zoom the dot stands off the view entirely.
        self._printer.setDot(125.0, 125.0)
        self.pump(30)
        dot = face.findChild(harness.QQuickItem, "moonrakerPlateToolheadDot")
        self.assertTrue(dot.property("visible"), "no toolhead dot to jump to")
        before = (face.property("viewPanX"), face.property("viewPanY"))
        jump = self.find(monitor, "moonrakerFollowerJump")
        self.assertTrue(jump.property("enabled"), "the jump is dead with a live dot")
        self._click(window, jump)
        self.assertNotEqual((face.property("viewPanX"), face.property("viewPanY")), before,
                            "the jump never panned the view")
        self.assertAlmostEqual(dot.x() + dot.width() / 2, face.width() / 2, delta=2.0,
                               msg="the jump did not land the toolhead on the view's centre")
        self.assertAlmostEqual(dot.y() + dot.height() / 2, face.height() / 2, delta=2.0,
                               msg="the jump did not land the toolhead on the view's centre")

    def test_the_jump_button_is_disabled_without_a_live_toolhead(self):
        monitor, window, face = self._follower_popover()
        self._printer.setDot(0.0, 0.0, valid=False)
        self.pump(30)
        self.assertFalse(self.find(monitor, "moonrakerFollowerJump").property("enabled"),
                         "the jump is live with no valid toolhead position")

    def test_a_pan_settles_before_it_re_rasters(self):
        # The native-raster era's pan: the pan stays a paint input
        # (the middle-canvas ghosting killed the translate), but the
        # re-raster fires at the SETTLE — after the last pan change —
        # never per drag tick (the awful-panning report).
        monitor, window, face = self._follower_popover()
        face.setProperty("dot", None)
        rows = self._ink_rows(self._grab_when_inked(window, face), face, window)
        self.assertTrue(rows, "the follower painted nothing")
        # The counter's own arrival, not the ink's: the wait above
        # clears on ANY canvas's ink and the grid covers the same rect,
        # so it says nothing about this accumulation.
        deadline = harness.time.monotonic() + 15.0
        while face.property("_paintsSinceReset") < 1 and harness.time.monotonic() < deadline:
            self.app.processEvents()
            harness.time.sleep(0.05)
        self.assertGreaterEqual(face.property("_paintsSinceReset"), 1,
                                "the accumulation never painted")
        self.assertEqual(self._settle(face, "interval"), 60,
                         "the settle's own interval")
        latch = face.property("_progressKey")
        paints = face.property("_paintsSinceReset")
        face.setProperty("viewPanY", 36.0)
        # The window the pan has to survive is CLOSED, not timed: with
        # the settle stopped, no length of pumping can let it in, so
        # the counter below can only have moved if the PAN re-rastered
        # — which is the claim. Read off a fixed window it was a
        # machine-speed question, and a loaded run re-rastered inside
        # it.
        self._settle(face, "stop()")
        self.pump(30)
        self.assertEqual(face.property("_paintsSinceReset"), paints,
                         "a pan re-rasters before the settle")
        self.assertEqual(face.property("_view").property("panY").toNumber(), 36.0,
                         "the painter's carrier lost the pan")
        # The reset is the timer's synchronous effect, and the read is
        # in the turn that fired it: _lastSplit is -1 only until that
        # reset's own re-raster lands, and no paint lands inside a JS
        # evaluation — so the reset's work is what is read, not
        # whatever the machine had time for.
        self._settle(face, "triggered()")
        self.assertNotEqual(face.property("_progressKey"), latch,
                            "the settle never reset the stack")
        self.assertEqual(face.property("_lastSplit"), -1,
                         "the settle's reset left the stack split")

    def test_detaching_freezes_the_layer_and_hides_the_dot(self):
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        dot = face.findChild(harness.QQuickItem, "moonrakerPlateToolheadDot")
        slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        attach = self.find(monitor, "moonrakerFollowerAttach")
        self.assertTrue(dot.property("visible"))
        self.assertTrue(attach.property("enabled"))
        # The slider is LIVE while attached — a seek is itself the
        # detach (the live request: it must never sit dead).
        self.assertTrue(slider.property("enabled"), "the slider sits dead while attached")
        self.assertEqual(slider.property("value"), 9.0, "the slider did not follow the live layer")
        self._click(window, attach)
        self.assertIn(("attached", False), self._printer.calls)
        self.assertEqual(attach.property("text"), "Attach")
        self.assertFalse(face.property("attached"))
        self.assertFalse(dot.property("visible"), "the detached face kept its toolhead dot")
        self.assertEqual(self._printer.followerLayerAnchor, 9,
                         "the detach did not hold the layer it showed")
        self.assertTrue(slider.property("enabled"), "the detached slider cannot seek")
        self.assertEqual(slider.property("value"), 9.0, "the frozen layer left the slider")
        self._click(window, attach)
        self.assertIn(("attached", True), self._printer.calls)
        self.assertEqual(attach.property("text"), "Detach")
        self.assertTrue(face.property("attached"))
        self.assertTrue(dot.property("visible"), "re-attaching lost the toolhead dot")
        self.assertTrue(slider.property("enabled"))

    def test_detach_is_enabled_before_the_live_layer_resolves(self):
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(-1)
        self.pump(20)
        attach = self.find(monitor, "moonrakerFollowerAttach")
        self.assertEqual(attach.property("text"), "Detach")
        self.assertTrue(attach.property("enabled"),
                        "the indexed print's detach is disabled before layer entry")
        self._click(window, attach)
        self.assertIn(("attached", False), self._printer.calls)
        self.assertEqual(attach.property("text"), "Attach")
        self.assertFalse(face.property("attached"))
        self.assertEqual(self._printer.followerLayerAnchor, 0)
        slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        self.assertTrue(slider.property("enabled"))
        self.assertEqual(slider.property("value"), 0.0)

    def test_the_layer_ghost_stays_available_on_every_layer_detached(self):
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        attach = self.find(monitor, "moonrakerFollowerAttach")
        boxes = [item for item in monitor.findChildren(harness.QQuickItem)
                 if item.property("text") == "Layer ghost"
                 and not item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(boxes), "no Layer ghost checkbox mounted")
        box = boxes[0]
        self.assertTrue(box.property("visible"), "the ghost box hides while attached")
        self.assertTrue(face.property("showBase"), "the live face lost its base")
        # The partial default split frames the base: the probe reads
        # the composition's own predicate, not a pixel.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant

        def partial_base():
            return bool(QMetaObject.invokeMethod(face, "_partialBase",
                                                 Q_RETURN_ARG(QVariant)))

        self.assertTrue(partial_base(), "the base never framed the partial layer")
        self._click(window, attach)
        self.assertFalse(face.property("attached"))
        self.assertEqual(self._printer.followerLayerAnchor,
                         self._printer.plateProgressAnchor,
                         "the detach did not hold the live layer")
        self.assertTrue(box.property("visible"),
                        "the ghost box hid while detached on the live layer")
        self.assertTrue(face.property("showBase"),
                        "the detached-on-live face lost its base")
        self.assertTrue(partial_base(), "the detached-on-live base never framed")
        # The toggle drives the base through the model, detached or not.
        self._click(window, box)
        self.assertIn(("showBase", False), self._printer.calls)
        self.assertFalse(face.property("showBase"),
                         "unchecking the ghost box kept the base")
        self.assertFalse(partial_base(), "the unchecked base kept framing")
        self._click(window, box)
        self.assertIn(("showBase", True), self._printer.calls)
        self.assertTrue(face.property("showBase"), "re-checking lost the base")
        # The ghost is available for ALL layers: the print advancing
        # past the frozen layer changes nothing.
        self._printer.setAnchor(12)
        self.pump(20)
        self.assertTrue(box.property("visible"),
                        "the ghost box hid while detached on an old layer")
        self.assertTrue(face.property("showBase"),
                        "the base hid while detached on an old layer")
        # The split-brain guard: the ghost frames the SELECTED layer's
        # own partial progress — the same view the scrub shows, never
        # the live print's.
        self._printer.setFollowerLayerAnchor(5)
        self._printer.setSplit(6)
        self.pump(20)
        self.assertEqual(self._printer.plateProgressAnchor, 5,
                         "the scrub did not land on the selected layer")
        self.assertTrue(partial_base(),
                        "the ghost does not frame the selected layer's partial")

    def test_detach_and_scrub_work_from_layer_zero(self):
        # The P0 zero-index bug: the production model read anchor 0
        # as missing through `0 or -1`, so the detach was refused and
        # the progress scrub never detached on the first layer. The
        # popover must detach on layer 0 and keep the anchor at 0 —
        # never -1.
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(0)
        self.pump(20)
        attach = self.find(monitor, "moonrakerFollowerAttach")
        layer_slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the live layer zero never reached the slider")
        self.assertEqual(attach.property("text"), "Detach")
        self._click(window, attach)
        self.assertIn(("attached", False), self._printer.calls)
        self.assertEqual(attach.property("text"), "Attach")
        self.assertFalse(face.property("attached"))
        self.assertEqual(self._printer.followerLayerAnchor, 0,
                         "the layer-zero detach did not hold the layer it showed")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the detach turned the anchor into -1")
        # Reattach, then scrub the ACTUAL progress control: the scrub
        # is itself the detach on the current layer.
        self._click(window, attach)
        self.assertIn(("attached", True), self._printer.calls)
        self._printer.calls = []
        progress = self.find(monitor, "moonrakerFollowerLayerProgress")
        self.assertTrue(progress.property("enabled"),
                        "the progress control sits dead in the harness")
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import QPointF, Qt
        seek = progress.mapToScene(
            QPointF(progress.width() * 0.5, progress.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(30)
        self.assertIn(("progress", round(progress.property("value"))),
                      self._printer.calls)
        self.assertFalse(self._printer.followerAttached,
                         "the layer-zero scrub never detached")
        self.assertEqual(self._printer.followerLayerAnchor, 0,
                         "the scrub froze the wrong anchor")
        self.assertEqual(attach.property("text"), "Attach")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the scrub turned the anchor into -1")

    def test_the_layer_slider_commits_only_after_the_seek_settles(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        self._click(window, self.find(monitor, "moonrakerFollowerAttach"))
        slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        self.assertTrue(slider.property("enabled"))
        self._printer.calls = []
        seek = slider.mapToScene(harness.QPointF(slider.width() * 0.5, slider.height() / 2)).toPoint()
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(20)
        requested = round(slider.property("value"))
        self.assertEqual([call for call in self._printer.calls if call[0] == "layer"], [],
                         "the seek committed a layer before it settled")
        # The settle: the request lands while the handle is still held
        # (nothing is committed by the hold itself).
        deadline = harness.time.monotonic() + 1.5
        while (not [call for call in self._printer.calls if call[0] == "layer"]
               and harness.time.monotonic() < deadline):
            self.app.processEvents()
            harness.time.sleep(0.02)
        layers = [call for call in self._printer.calls if call[0] == "layer"]
        self.assertEqual(layers, [("layer", requested)],
                         "the settled seek did not request its own layer")
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(20)
        self.assertEqual(self._printer.followerLayerAnchor, requested)
        self.assertEqual(slider.property("value"), float(requested))
        # A release commits at once: the click path needs no settle.
        self._printer.calls = []
        far = slider.mapToScene(harness.QPointF(slider.width() * 0.85, slider.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=far)
        self.pump(30)
        seeks = [call for call in self._printer.calls if call[0] == "layer"]
        self.assertEqual(len(seeks), 1, "a settled click did not commit exactly once")
        self.assertGreater(seeks[0][1], requested, "the second seek did not move forward")

    def test_the_picker_draws_no_toolhead_dot(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plate")
        picker_dot = self.find(monitor, "moonrakerPlateExcludeFace").findChild(
            harness.QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNone(picker_dot,
                          "the picker draws the toolhead — the map is the control")
        monitor.setProperty("openPopOver", "plateprogress")
        self.pump(30)
        follower_dot = self._popover_faces(
            monitor, "moonrakerPlateProgressFace")[0].findChild(
            harness.QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNotNone(follower_dot)
        self.assertTrue(follower_dot.property("visible"),
                        "the follower lost its toolhead dot")
