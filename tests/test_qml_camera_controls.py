"""Executable qml camera controls contracts."""
from tests import qml_engine_support as harness

class CameraFpsControlTests(harness.CameraFpsControlTests):
    def test_green_snapshot_range_requires_a_supported_url(self):
        pane, window, model, image, frame = self._fps_pane(700, 700, fps=5.0)
        self._fps_face(window, frame)
        region = self.find(pane, "cameraSnapshotRegion")
        bar = self.find(pane, "cameraFpsBar")
        self.assertFalse(region.property("visible"))
        model.set_snapshot_available(True)
        self.pump()
        self.assertTrue(region.property("visible"))
        self.assertTrue(region.height() > 0)
        self.assertAlmostEqual(region.height() / bar.height(), (5.0 - 0.5) / (30.0 - 0.5), delta=0.01)
        model.set_snapshot_available(False)
        self.pump()
        self.assertFalse(region.property("visible"))

    def test_same_path_snapshot_switch_replaces_the_stream(self):
        from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant

        pane, _window, model, image, _frame = self._fps_pane(700, 700)
        model.set_snapshot_available(True)

        def apply(url):
            QMetaObject.invokeMethod(pane, "applyCamera",
                                     Q_ARG(QVariant, harness.QUrl(url)), Q_ARG(QVariant, True))

        apply("http://127.0.0.1:59999/webcam/?action=stream")
        before = image.property("stopCount")
        self.assertFalse(image.property("snapshotMode"))
        model.setCameraFps(5.0)
        apply("http://127.0.0.1:59999/webcam/?action=snapshot")
        self.assertGreater(image.property("stopCount"), before)
        self.assertTrue(image.property("snapshotMode"))
        self.assertIn("action=snapshot", image.property("source").toString())
        apply("http://127.0.0.1:59999/webcam/?action=snapshot&quality=50")
        self.assertIn("quality=50", image.property("source").toString())

    def test_touchpad_camera_zoom_matches_follower_scroll_distance(self):
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame, pixels=18)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25 ** 0.1, delta=0.002)
        for _ in range(9):
            self._wheel(window, frame, pixels=18)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25, delta=0.002,
                               msg="180 touchpad pixels equal one follower zoom notch")
        self._wheel(window, frame)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25 * 1.25, delta=0.002,
                               msg="a mouse wheel still moves one discrete notch")

    def test_touchpad_fps_uses_the_same_180_pixel_travel(self):
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        shift = harness.Qt.KeyboardModifier.ShiftModifier
        self._wheel(window, frame, modifiers=shift, pixels=18)
        self.assertAlmostEqual(pane.property("cameraFps"), 15.1, delta=0.01)
        for _ in range(9):
            self._wheel(window, frame, modifiers=shift, pixels=18)
        self.assertAlmostEqual(pane.property("cameraFps"), 16.0, delta=0.01)
        self._wheel(window, frame, modifiers=shift)
        self.assertAlmostEqual(pane.property("cameraFps"), 17.0, delta=0.01,
                               msg="a mouse wheel still moves one FPS step")
        self.assertEqual(model.fps_calls[-1], 17.0)
        self._wheel(window, frame, modifiers=shift, horizontal_pixels=18)
        self.assertAlmostEqual(pane.property("cameraFps"), 17.1, delta=0.01,
                               msg="Shift-remapped horizontal touchpad pixels still drive FPS")

    def test_the_gesture_surface_survives_a_stream_off_and_on(self):
        # The live report: disabling then re-enabling the stream left
        # zoom and FPS dead. The blank is a real size change (the
        # renderer announces it), the picture returns, and the control
        # surface must come back with it — liveness that latches on a
        # value no later signal re-reads is the bug.
        pane, window, model, image, _frame = self._fps_pane(700, 700)
        gesture = self.find(pane, "cameraGestureArea")

        def apply_camera(url, visible):
            # Exactly what the host calls: the URL (nonce included) and
            # the configured flag, applied through the pane's own slot.
            from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant
            QMetaObject.invokeMethod(pane, "applyCamera",
                                     Q_ARG(QVariant, harness.QUrl(url)), Q_ARG(QVariant, visible))

        apply_camera("http://127.0.0.1:59999/webcam2/?mpf_reload=1", True)
        self.pump(30)
        self.assertTrue(pane.property("cameraControlLive"), "the surface starts live")
        self.assertTrue(gesture.property("enabled"))
        self._wheel(window, gesture)
        self.pump(30)
        zoomed = pane.property("cameraZoom")
        self.assertGreater(zoomed, 1.0, "the wheel zooms the live picture")

        # The stream OFF: the model's flag drops with its URL, and the
        # pane blanks the painted frame.
        model.set_stream_enabled(False)
        pane.setProperty("configured", False)
        apply_camera("", False)
        self.pump(30)
        self.assertEqual(image.property("clearCount"), 1, "the stream-off blanks the frame")
        self.assertEqual(image.property("imageWidth"), 0, "and paints no size")
        self.assertFalse(pane.property("cameraControlLive"))
        self.assertFalse(gesture.property("enabled"), "the blank disarms the gestures")

        # The stream ON again: the URL returns on a new nonce and the
        # bytes resume. Nothing here re-applies the gestures by hand.
        model.set_stream_enabled(True)
        pane.setProperty("configured", True)
        apply_camera("http://127.0.0.1:59999/webcam2/?mpf_reload=3", True)
        self.pump(30)
        image.setProperty("imageWidth", 640)
        image.setProperty("imageHeight", 480)
        self.pump(30)
        self.assertTrue(pane.property("cameraControlLive"),
                        "the resumed stream's frame re-arms the control surface")
        self.assertTrue(gesture.property("enabled"))
        self._wheel(window, gesture)
        self.pump(30)
        self.assertNotEqual(pane.property("cameraZoom"), zoomed, "the wheel zooms again")
        self._wheel(window, gesture, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0], "and the rate gesture is back with it")

    def test_the_status_chip_reports_the_decode_rate(self):
        # The top-right chip carries the rate the renderer is actually
        # decoding at — the frame's own readout, so a throttled stream
        # is visibly throttled. No frames yet means no rate to claim.
        pane, _window, _model, image, _frame = self._fps_pane(700, 640)
        chip = self.find(pane, "cameraStreamChip")
        label = self.find(pane, "cameraStreamChipText")
        self.assertTrue(chip.property("visible"), "the chip has room on a wide frame")
        self.assertIn("640×480", label.property("text"))
        self.assertNotIn("fps", label.property("text"),
                         "no rate is claimed before a frame lands")
        image.setProperty("recentDisplayedFPS", 15)
        self.pump()
        self.assertIn("15 fps", label.property("text"))
        image.setProperty("recentDisplayedFPS", 0.5)
        self.pump()
        self.assertIn("0.50 fps", label.property("text"),
                      "the low rates the idle load lives at stay readable")

    def test_the_rate_field_holds_still_at_the_sub_one_fps_floor(self):
        # The live report: at the 0.5 FPS floor the one-second sample
        # cannot see the rate at all — a window with no frame reads 0
        # and the next reads 1 — so the field appeared and disappeared
        # with every sample. At and below one frame per sample the
        # throttle's own rate IS what the renderer decodes at, and the
        # field holds it.
        pane, _window, model, image, _frame = self._fps_pane(700, 640, fps=0.5, maximum=30.0)
        label = self.find(pane, "cameraStreamChipText")
        for sample in (0, 0.5, 1, 0):
            image.setProperty("recentDisplayedFPS", sample)
            self.pump()
            self.assertIn("0.50 fps", label.property("text"),
                          "the field must not move with the sample noise")
        # Above the sample's resolution the measured rate is the honest
        # one again.
        model.set_camera_ceiling(30.0, fps=12.0)
        image.setProperty("recentDisplayedFPS", 11.6)
        self.pump()
        self.assertIn("12 fps", label.property("text"),
                      "the measured rate reports once it can be measured")

    def test_the_chip_yields_to_the_live_badge_and_returns_when_the_frame_grows(self):
        # Two pills on a narrow frame read as one smear: the chip
        # yields the moment it would occlude Live (the request) and
        # returns as soon as the frame is wide enough to carry both.
        pane, window, _model, image, _frame = self._fps_pane(700, 640, displayed_fps=15.0)
        chip = self.find(pane, "cameraStreamChip")
        badge = self.find(pane, "cameraLiveBadge")
        self.assertTrue(chip.property("visible"),
                        "the wide frame must carry both pills")
        # The narrow width is DERIVED from the pills, not pinned to a
        # magic window size: the pills' widths are font metrics, so 190
        # is narrow on one host's fonts and exactly wide enough on
        # another's — which turned this precondition into a failure
        # rather than a proof (the Windows leg's fonts, 2026-09-26).
        # The pills are measured once, while both are whole, and the
        # frame is then taken down until it is provably narrower than
        # they are, so the guard holds wherever the suite runs.
        needed = badge.width() + chip.width() + 3 * chip.property("badgeGap")
        narrow = 190
        self.resize_window(pane, window, narrow, 640)
        self._pump_ms(300)
        while image.width() >= needed and narrow > 96:
            narrow -= max(8, int(image.width() - needed) + 8)
            self.resize_window(pane, window, narrow, 640)
            self._pump_ms(300)
        # The precondition IS the regression pin: a chip that grows
        # (this release added the rate to it) must be reported as
        # no-longer-provable rather than silently passing.
        self.assertLess(image.width(), needed,
                        "the narrow mount must actually be too narrow")
        self.assertFalse(chip.property("visible"),
                         "the chip must yield rather than touch the Live badge")
        self.resize_window(pane, window, 700, 640)
        self._pump_ms(300)
        self.assertTrue(chip.property("visible"),
                        "the chip returns once the frame has room for it")

    def test_the_scale_docks_with_a_rate_change_and_parks_after_five_idle_seconds(self):
        # The zoom scope's rhythm, five seconds on both (the request
        # moved the zoom's own two-second park to five): a rate change
        # slides the bar into view on the rate's own face, five idle
        # seconds slide it back out of the picture entirely.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self.assertGreaterEqual(frame.width(), 200, "the scale needs the room it tests for")
        self.assertGreaterEqual(frame.height(), 150, "the scale needs the room it tests for")
        self.assertTrue(control.property("visible"), "the full scale has room here")
        self.assertFalse(pane.property("cameraBarDocked"), "the bar starts parked")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the scale starts parked out of the frame")
        self._wheel(window, image, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0],
                         "one notch is one linear step of the camera's own range")
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate's own gesture brings the rate's own face up")
        self._pump_ms(300)
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "the docked scale rides inside the CAMERA VIEW, not the pane frame")
        self._wait_until(window, lambda _image: control.x() >= frame.width(),
                         timeout=8.0)
        self.assertFalse(pane.property("cameraBarDocked"), "five idle seconds park it again")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the parked scale clears the picture's own edge")

    def test_the_two_faces_trade_places_with_a_slide(self):
        # The live report: the cards changed content without trading
        # places. A rate the gesture has just set echoes back through the
        # model, and that second dock used to cut the slide-out short —
        # so the bar must be out on the face that is LEAVING before the
        # new one lands, and back in on the new face.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertEqual(pane.property("cameraBarMode"), "zoom",
                         "the plain wheel's own face is up")
        self.assertTrue(pane.property("cameraBarDocked"), "and it is docked")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        # Mid-turn-over, before any timer can run: still the zoom face,
        # with the bar on its way out of the picture.
        self.assertEqual(pane.property("cameraBarMode"), "zoom",
                         "the face that is leaving stays up for the slide-out")
        self.assertTrue(pane.property("_cameraBarTurning"), "the turn-over is under way")
        self.assertFalse(pane.property("cameraBarShown"),
                         "the turn-over takes the bar off the picture")
        self._wait_until(window,
                         lambda _image: pane.property("cameraBarMode") == "fps"
                         and not pane.property("_cameraBarTurning")
                         and pane.property("cameraBarShown")
                         and control.x() + control.width() <= frame.width() + 0.5,
                         timeout=3.0)
        self.assertEqual(pane.property("cameraBarMode"), "fps", "the rate face lands")
        self.assertFalse(pane.property("_cameraBarTurning"))
        self.assertTrue(pane.property("cameraBarShown"), "and rides back in")
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "the landed face is docked inside the picture")
        self.assertEqual(model.fps_calls, [16.0], "one notch, one rate")

    def test_a_rate_change_leaves_the_zoomed_scale_in_place(self):
        # The live report: with the view zoomed, a rate change replaced
        # the zoom scale for good. A view held past the fit PINS its own
        # scale — the rate card borrows the bar and hands it back once its
        # idle five seconds are up — and the scale then stays up rather
        # than parking, since a parked scale is the control the zoomed
        # picture is about.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self.assertFalse(pane.property("cameraBarPinned"), "the fit pins nothing")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertTrue(pane.property("cameraBarPinned"), "a held zoom pins the scale")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        # Both waits are the states the assertions are about. Read off
        # a fixed instant instead, they measured how much wall clock the
        # event loop had eaten — how the macOS leg failed, with the bar
        # mid-turn-over. The timeouts are hang guards, an order of
        # magnitude above what the chain owes.
        self._wait_until(window, lambda _image: pane.property("cameraBarMode") == "fps",
                         timeout=2.0)
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate card has the bar for now")
        self._wait_until(window,
                         lambda _image: pane.property("cameraBarMode") == "zoom"
                         and pane.property("cameraBarShown")
                         and control.x() + control.width() <= frame.width() + 0.5,
                         timeout=8.0)
        self.assertEqual(pane.property("cameraBarMode"), "zoom", "the scale comes back")
        self.assertTrue(pane.property("cameraBarShown"), "and it is on screen")
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "docked, not parked")
        self._pump_ms(6000)
        self.assertTrue(pane.property("cameraBarShown"),
                        "the pinned scale outlasts the idle park")
        self.assertEqual(pane.property("cameraBarMode"), "zoom")
        self._double_click(window, frame)
        self._pump_ms(400)
        self.assertFalse(pane.property("cameraBarPinned"), "the fit releases the pin")
        self.assertFalse(pane.property("cameraBarShown"),
                         "and the bar has nothing left to read")
        self.assertEqual(model.fps_calls, [16.0], "the rate is not a view state")

    def test_a_held_handle_holds_the_park_off_and_keeps_the_grab(self):
        # The live report: a handle held still through the park's five
        # seconds let the card slide out from under the hand, and the
        # press went on driving a scale that was no longer on the picture.
        # A held handle is not an idle card — the park waits for the
        # release, the drag keeps tracking, and the rate is set where the
        # pointer is let go.
        from PyQt6.QtCore import QEvent
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._fps_face(window, frame)
        bar = self.find(pane, "cameraFpsBar")
        handles = [item for item in bar.childItems() if item.property("pressed") is not None]
        self.assertEqual(len(handles), 1, "the scale carries its own handle")
        handle = handles[0]
        self._mouse(window, bar, QEvent.Type.MouseButtonPress, bar.width() / 2, bar.height() / 4,
                    harness.Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertTrue(handle.property("pressed"), "the handle is held")
        self.assertAlmostEqual(pane.property("cameraFps"), 22.63, delta=0.05,
                               msg="the press takes the rate to the pointer")
        self._pump_ms(5400)
        self.assertTrue(handle.property("pressed"), "the grab survives the stillness")
        self.assertTrue(pane.property("cameraBarShown"),
                        "a held handle holds the park off")
        self._mouse(window, bar, QEvent.Type.MouseMove, bar.width() / 2, bar.height() / 2,
                    harness.Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraFps"), 15.25, delta=0.05,
                               msg="the held handle goes on tracking the pointer")
        self._mouse(window, bar, QEvent.Type.MouseMove, bar.width() / 2, bar.height() * 3 / 4,
                    harness.Qt.MouseButton.LeftButton)
        self._mouse(window, bar, QEvent.Type.MouseButtonRelease, bar.width() / 2,
                    bar.height() * 3 / 4)
        self.pump(20)
        self.assertFalse(handle.property("pressed"), "the release is a real release")
        self.assertAlmostEqual(pane.property("cameraFps"), 7.88, delta=0.05,
                               msg="the rate is set where the release occurred")
        self._wait_until(window, lambda _image: not pane.property("cameraBarShown"),
                         timeout=8.0)
        self.assertFalse(pane.property("cameraBarShown"),
                         "the idle five seconds run from the release")

    def test_the_bar_carries_the_rate_floor_without_cutting_it_off(self):
        # The live report: at the floor the readout "0.50 fps" ran out of
        # the bar. That readout — the widest either face shows — is what
        # sets the bar's width, and the zoom face rides the same box.
        pane, _window, model, _image, _frame = self._fps_pane(700, 700)
        model.setCameraFps(0.5)
        self.pump(30)
        bar = self.find(pane, "cameraBar")
        readout = self.find(pane, "cameraFpsReadout")
        self.assertEqual(readout.property("text"), "0.50 fps", "the floor's own readout")
        self.assertGreaterEqual(bar.width() - readout.property("contentWidth"), 4.0,
                                "the widest readout must sit inside the bar")
        zoom_readout = self.find(pane, "cameraZoomReadout")
        self.assertLessEqual(zoom_readout.property("contentWidth") + 4.0, bar.width(),
                             "the zoom face rides the same width and must fit too")

    def test_the_shift_wheel_changes_the_rate_with_no_scale_on_screen(self):
        # The spec's own line: the control must not show up when the
        # view cannot carry it, but the throttle must still be
        # reachable. Where the scale has no room the rate rides the
        # compact chip instead — vertically centred on the picture.
        pane, window, model, _image, frame = self._fps_pane(190, 400)
        control = self.find(pane, "cameraBar")
        chip = self.find(pane, "cameraBarChip")
        self.assertLess(frame.width(), 200,
                        "the narrow mount must actually be too narrow for the scale")
        self.assertFalse(control.property("visible"), "no room, no scale")
        self.assertGreaterEqual(chip.x(), frame.width(),
                                "the parked chip clears the picture too")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0],
                         "the shift wheel must reach the rate with no control shown")
        self._pump_ms(300)
        self.assertTrue(chip.property("visible"), "the compact chip stands in for the scale")
        chip_rect, picture = self.rect(chip, pane), self.rect(frame, pane)
        self.assertAlmostEqual(chip_rect.center().y(), picture.center().y(), delta=1.5,
                               msg="the chip is vertically centred on the camera view")
        self.assertLessEqual(chip.x() + chip.width(), frame.width() + 0.5,
                             "the docked chip rides inside the camera view")
        self.assertEqual(self.find(pane, "cameraBarChipText").property("text"), "16 fps",
                         "the chip carries the rate itself")

    def test_no_fps_surface_at_all_when_the_view_has_no_room(self):
        # The floor of the same rule: a frame too small for even the
        # chip shows no FPS surface — the rate is still the shift
        # wheel's.
        # The mount must leave the frame under the chip's own 96 px
        # threshold on EVERY platform, and the frame does not track the
        # mount the same way on each: on Windows it comes out at the
        # mount less its 2 px of chrome (108 at a 110 mount, which is
        # the reported premise failure), where this container leaves it
        # ~15 px narrower. 80 is clear of the threshold either way.
        pane, window, model, _image, frame = self._fps_pane(80, 400)
        self.assertLess(frame.width(), 96,
                        "the tiny mount must actually be too small for the chip")
        self.assertFalse(self.find(pane, "cameraBar").property("visible"))
        self.assertFalse(self.find(pane, "cameraBarChip").property("visible"))
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0])

    def test_the_plain_wheel_is_the_pictures_gesture_not_the_throttles(self):
        # The two gestures share the wheel and must not cross: a plain
        # notch zooms the picture and leaves the decode rate alone, a
        # Shift notch throttles the stream and leaves the view alone.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self.pump(30)
        self.assertEqual(model.fps_calls, [],
                         "the plain wheel is the picture's own gesture")
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25, delta=1e-6,
                               msg="one plain notch is one 1.25x zoom step")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0], "the shift wheel is the throttle's")
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25, delta=1e-6,
                               msg="a shift notch must not move the view")

    def test_the_wheel_zooms_about_the_pointer(self):
        # The zoom rides the frame centre, so the point under the
        # pointer would slide away from it; the pan is re-solved to
        # hold that point still (the plate scope's own ruling).
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        left = harness.QPointF(frame.width() / 4, frame.height() / 2)
        self._wheel(window, frame, position=left)
        # Observe the first delivered animation tick. A loaded/offscreen
        # macOS runner may not deliver any timer event within a fixed 50 ms.
        self._wait_until(window, lambda _image: pane.property("cameraDisplayZoom") > 1.0,
                         timeout=5.0)
        # (W/2 - x) / 4 is where the pointer's own point lands once the
        # picture is a quarter bigger: the edge it was over stays put,
        # so a wheel to the left of the centre walks the pan right.
        self.assertAlmostEqual(pane.property("cameraPanX"),
                               (frame.width() / 2 - left.x()) * 0.25, delta=1.0,
                               msg="the point under the pointer drifted")
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1.0)
        displayed = pane.property("cameraDisplayZoom")
        self.assertGreater(displayed, 1.0, "the image must start gliding")
        self.assertLess(displayed, pane.property("cameraZoom"),
                        "a wheel notch must not snap the image to its target")
        anchor = left.x() - frame.width() / 2
        self.assertAlmostEqual(pane.property("cameraDisplayOffsetX"),
                               anchor * (1 - displayed), delta=1.0,
                               msg="the pointer's image point must stay fixed during the glide")
        # Qt timers can be coalesced on a busy/offscreen macOS runner.
        # Wait for the observable endpoint rather than assuming a tick count
        # within 350 ms; the intermediate assertions above still prove easing.
        self._wait_until(window, lambda _image:
                         pane.property("cameraDisplayZoom") == pane.property("cameraZoom")
                         and pane.property("cameraDisplayOffsetX") == pane.property("cameraPanOffsetX"),
                         timeout=5.0)
        self.assertEqual(pane.property("cameraDisplayZoom"), pane.property("cameraZoom"))
        self.assertEqual(pane.property("cameraDisplayOffsetX"), pane.property("cameraPanOffsetX"))
        # Zooming out past the fit stops at the fit and re-centres.
        for _ in range(8):
            self._wheel(window, frame, delta=-120)
            self.pump(5)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.0, delta=1e-6,
                               msg="the fit is the floor")
        self.assertAlmostEqual(pane.property("cameraPanX"), 0.0, delta=1e-6)
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1e-6)
        self._wait_until(window, lambda _image:
                         pane.property("cameraDisplayZoom") == 1.0
                         and pane.property("cameraDisplayOffsetX") == 0.0, timeout=5.0)
        self.assertEqual(pane.property("cameraDisplayZoom"), 1.0)
        self.assertEqual(pane.property("cameraDisplayOffsetX"), 0.0)

    def test_the_drag_pans_the_picture_and_stops_at_the_pictures_edge(self):
        # The pan follows the pointer exactly while there is picture to
        # move, and the clamp owns the limit: no drag may open a gap
        # between the frame and the picture it is supposed to show.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self._wheel(window, frame)
        self.pump(30)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.5625, delta=1e-6)
        self._drag(window, frame, 40, 20)
        self.assertAlmostEqual(pane.property("cameraPanX"), 40.0, delta=1.5,
                               msg="the drag pan lagged the pointer")
        self.assertAlmostEqual(pane.property("cameraPanY"), 20.0, delta=1.5)
        self.assertEqual(pane.property("cameraPanOffsetX"), pane.property("cameraPanX"),
                         "a pan inside the limit is applied as it is")
        # A drag past the picture's edge is held AT the edge as it is
        # stored, not only on the way to the transform.
        from PyQt6.QtCore import QEvent
        limit = pane.property("cameraPanLimitX")
        self.assertAlmostEqual(limit,
                               frame.width() * (pane.property("cameraZoom") - 1) / 2, delta=1.5,
                               msg="the limit is the picture's own overhang")
        self.assertLess(limit, 260.0, "the mount must leave room for an overshoot")
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    harness.Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + 260, cy,
                    harness.Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit, delta=1.5,
                               msg="a drag past the edge stops at the edge")
        self.assertAlmostEqual(pane.property("cameraPanOffsetX"),
                               pane.property("cameraPanLimitX"), delta=1.5,
                               msg="the applied pan is clamped to the picture's edge")
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + 260, cy,
                    harness.Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, cx + 260, cy)
        self.pump(20)
        # A resize re-clamps through the binding, with no timer.
        self.resize_window(pane, window, 400, 400)
        self._pump_ms(200)
        self.assertAlmostEqual(pane.property("cameraPanOffsetX"),
                               pane.property("cameraPanLimitX"), delta=1.5,
                               msg="a shrunken pane re-clamps the applied pan")
        self.assertLess(pane.property("cameraPanOffsetX"),
                        frame.width() * (pane.property("cameraZoom") - 1) / 2 + 1.0)

    def test_a_drag_past_the_edge_never_has_to_be_undone(self):
        # The live report: past the picture's edge the pan stopped, but
        # the drag kept counting and the first stretch of the way back
        # moved nothing — the overshoot had to be undone first. The pan
        # is held to the limit as it is STORED, so the picture answers
        # the very first move of the return leg.
        pane, window, _model, _image, frame = self._fps_pane(700, 400)
        self._wheel(window, frame)
        self._wheel(window, frame)
        self.pump(30)
        limit = pane.property("cameraPanLimitX")
        self.assertGreater(limit, 40.0, "the mount must zoom into a real overhang")
        self.assertLess(limit, 200.0, "the overshoot must fit inside the window")
        from PyQt6.QtCore import QEvent
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    harness.Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + limit + 90, cy,
                    harness.Qt.MouseButton.LeftButton)
        # Both legs wait for the pan the assertion is about rather than
        # for a number of event rounds: the drag is delivered to the
        # gesture area and the pan applied on the Qt side, so a starved
        # leg read 0.0 for a drag that had simply not landed yet. The
        # VALUES are what carry the claim — an overshoot that had to be
        # undone first would still read short here.
        self._wait_until(window,
                         lambda _image: abs(pane.property("cameraPanX") - limit) <= 1.5,
                         timeout=8.0)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit, delta=1.5,
                               msg="the overshoot is discarded, not banked")
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + limit + 50, cy,
                    harness.Qt.MouseButton.LeftButton)
        self._wait_until(window,
                         lambda _image: abs(pane.property("cameraPanX") - (limit - 40)) <= 1.5,
                         timeout=8.0)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit - 40, delta=1.5,
                               msg="the return leg moves the picture at once")
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, cx + limit + 50, cy)
        self.pump(20)

    def test_a_drag_across_the_settled_zoom_control_keeps_the_camera(self):
        # The malformed move: a MOVE that names a button reads as a
        # fresh press, so Qt re-selects the target mid-drag and the
        # settled zoom control takes the grab the camera gesture was
        # holding. The pan then stops dead and the rest of the drag is
        # lost — which is what the macos leg saw as a pan that never
        # moved at all. The control has to be IN the pointer's path and
        # settled for this to bite, so the wait is its own docked
        # geometry and its turn-over, never a sleep.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        bar = self.find(pane, "cameraBar")
        self._wheel(window, frame)
        self._wait_until(window,
                         lambda _image: pane.property("cameraBarDocked")
                         and not pane.property("_cameraBarTurning")
                         and bar.x() + bar.width() <= frame.width() + 0.5,
                         timeout=8.0)
        limit = pane.property("cameraPanLimitX")
        self.assertGreater(limit, 40.0, "the mount must zoom into a real overhang")
        from PyQt6.QtCore import QEvent
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    harness.Qt.MouseButton.LeftButton)
        # A first move that clears the overshoot, then a second that
        # crosses the control: the grab has to survive the crossing.
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + limit, cy,
                    harness.Qt.MouseButton.LeftButton)
        self._wait_until(window,
                         lambda _image: abs(pane.property("cameraPanX") - limit) <= 1.5,
                         timeout=8.0)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit, delta=1.5,
                               msg="the first move never panned")
        across = bar.x() + bar.width() / 2
        self._mouse(window, frame, QEvent.Type.MouseMove, across, cy,
                    harness.Qt.MouseButton.LeftButton)
        self.pump(20)
        out = pane.property("cameraPanX")
        # The return leg is the claim: the grab is the camera gesture's
        # until the release, so the picture tracks the pointer even
        # though the outward leg ended over the control.
        self._mouse(window, frame, QEvent.Type.MouseMove, across - 60, cy,
                    harness.Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraPanX"), out - 60, delta=2.0,
                               msg="the drag lost the camera's grab to the control")
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, across - 60, cy)

    def test_a_move_is_an_update_whatever_button_it_names(self):
        # The malformed-event contract, for BOTH buttons. A move that
        # names a button reads as a fresh press (isBeginEvent), which
        # re-selects the target mid-drag; the helper used to take the
        # caller's button at face value, and _rate_drag passes
        # RightButton for the event's button AND the held mask, so every
        # right drag was still built malformed.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        from PyQt6.QtCore import QEvent, Qt
        cx, cy = frame.width() / 2, frame.height() / 2
        default_left = self._mouse(window, frame, QEvent.Type.MouseMove, cx, cy)
        explicit_left = self._mouse(window, frame, QEvent.Type.MouseMove, cx, cy,
                                    Qt.MouseButton.LeftButton)
        right = self._mouse(window, frame, QEvent.Type.MouseMove, cx, cy,
                            Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        for name, event in (("default left", default_left),
                            ("explicit left", explicit_left),
                            ("explicit right", right)):
            self.assertEqual(event.type(), QEvent.Type.MouseMove,
                             "%s was not a move" % name)
            self.assertTrue(event.isUpdateEvent(),
                            "%s was not an update event" % name)
            self.assertFalse(event.isBeginEvent(),
                             "%s was built as a begin event" % name)
            self.assertEqual(event.button(), Qt.MouseButton.NoButton,
                             "%s named a button on a move" % name)
        # The held mask still rides the event — it is what classifies a
        # move, and the right drag's grab depends on it.
        self.assertEqual(right.buttons(), Qt.MouseButton.RightButton,
                         "the right drag's held mask was dropped")
        # A press keeps its own changed button: that is what it is about.
        press = self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                            Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        self.assertEqual(press.button(), Qt.MouseButton.RightButton)
        self.assertFalse(press.isUpdateEvent(), "a press was filed as an update")

    def test_a_right_drag_across_the_settled_zoom_control_keeps_the_rate(self):
        # The same grab the left drag's regression pins, on the button
        # that still bypassed the fix: the rate's own gesture runs a
        # right drag across the picture, and the control has to be IN
        # the pointer's path and settled for the malformed move to steal
        # it. The wait is the control's own docked geometry.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        bar = self.find(pane, "cameraBar")
        self._wheel(window, frame)
        self._wait_until(window,
                         lambda _image: pane.property("cameraBarDocked")
                         and not pane.property("_cameraBarTurning")
                         and bar.x() + bar.width() <= frame.width() + 0.5,
                         timeout=8.0)
        from PyQt6.QtCore import QEvent
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    harness.Qt.MouseButton.RightButton, harness.Qt.MouseButton.RightButton)
        self.pump(20)
        across = bar.x() + bar.width() / 2
        # Cross the control first, then drive the rate from ON it: the
        # rate's own axis is vertical, so the leg that has to track is
        # the one taken while the pointer sits over the control.
        self._mouse(window, frame, QEvent.Type.MouseMove, across, cy,
                    harness.Qt.MouseButton.RightButton, harness.Qt.MouseButton.RightButton)
        self.pump(20)
        out = pane.property("cameraFps")
        self._mouse(window, frame, QEvent.Type.MouseMove, across, cy + 30,
                    harness.Qt.MouseButton.RightButton, harness.Qt.MouseButton.RightButton)
        self.pump(20)
        self.assertNotEqual(pane.property("cameraFps"), out,
                            "the right drag lost its grab to the control")
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, across, cy + 30,
                    harness.Qt.MouseButton.NoButton, harness.Qt.MouseButton.RightButton)
        self.assertIn(len(model.fps_calls), (1, 2), "the release never committed a rate")

    def test_a_double_click_returns_the_fit(self):
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self._drag(window, frame, 60, 30)
        self.pump(20)
        self.assertGreater(pane.property("cameraZoom"), 1.0)
        self.assertNotEqual(pane.property("cameraPanX"), 0.0)
        self.assertGreater(pane.property("cameraBarPinned"), False,
                           "the zoomed view is the pin's own case")
        self._double_click(window, frame)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.0, delta=1e-6,
                               msg="the double click returns the fit")
        self.assertAlmostEqual(pane.property("cameraPanX"), 0.0, delta=1e-6)
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1e-6)
        self.assertFalse(pane.property("cameraBarPinned"),
                         "the fit leaves the pin nothing to hold")

    def test_the_parked_control_is_clipped_by_the_picture_not_the_pane(self):
        # The live request: the control must vanish as it leaves the
        # PICTURE. On a letterboxed view the pane has room beside the
        # picture, so a pane-level clip would keep painting it there.
        pane, window, model, _image, frame = self._fps_pane(900, 400)
        control = self.find(pane, "cameraBar")
        viewport = self.find(pane, "cameraViewport")
        self.assertTrue(frame.property("clip"), "the picture owns the clip")
        self.assertFalse(viewport.property("clip"),
                         "the pane must not clip in the picture's place")
        self.assertLess(frame.width(), viewport.width(),
                        "the mount must letterbox, or the pin proves nothing")
        self.assertTrue(control.property("visible"), "the scale has room on the picture")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the control starts parked out of the picture")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self._pump_ms(300)
        picture = self.rect(frame, pane)
        docked = self.rect(control, pane)
        self.assertLessEqual(docked.right(), picture.right() + 0.5,
                             "a rate change docks the control inside the picture")
        self._wait_until(window,
                         lambda _image: self.rect(control, pane).left() > picture.right(),
                         timeout=8.0)
        parked = self.rect(control, pane)
        self.assertGreater(parked.left(), picture.right(),
                           "five idle seconds park it past the picture's edge")
        self.assertLess(parked.left(), picture.right() + 40 * 3,
                        "it is parked just past that edge, not off in the pane")
        self.assertEqual(model.fps_calls, [16.0])

    def test_the_scale_range_follows_the_selected_camera_ceiling(self):
        # The ceiling is the CAMERA's, not a product constant: the bar
        # tops out at the selected camera's configured target_fps and
        # re-reads it when the selection changes. The marker sits at
        # the fraction the rate now holds of that range.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=30.0, maximum=60.0)
        self._fps_face(window, frame)
        self.assertEqual(pane.property("cameraBarMode"), "fps", "the rate face is up")
        marker = self.find(pane, "cameraFpsMarker")
        self.assertTrue(self.find(pane, "cameraFpsScale").property("visible"),
                        "the rate face is the visible one")
        self.assertEqual(pane.property("cameraFpsMax"), 60.0,
                         "the camera reports its own ceiling")
        slower = marker.y()
        model.set_camera_ceiling(15.0)
        self.pump(30)
        self.assertEqual(pane.property("cameraFpsMax"), 15.0,
                         "a slower camera lowers the bar's range")
        self.assertEqual(pane.property("cameraFps"), 15.0,
                         "the published rate follows the camera down with it")
        self.assertLess(marker.y(), slower,
                        "at the ceiling the marker rides the top of the bar")
        self.assertAlmostEqual(marker.y(), -marker.height() / 2, delta=1.5,
                               msg="the ceiling is the bar's own top")

    def test_the_ruler_ticks_every_five_and_thickens_every_ten(self):
        # The graduations the bar draws: a thin double tick at every 5
        # FPS (a quarter in from each side), a thick line at every 10,
        # and both ends of the range marked — the camera's ceiling at
        # the top, the 0.5 floor at the bottom. The spacing is EVEN: a
        # rate reads as a rate, not as a zoom's log scale (the live
        # request — the log bar crowded every low rate into its foot).
        pane, window, _model, _image, frame = self._fps_pane(700, 700, fps=15.0, maximum=30.0)
        self._fps_face(window, frame)
        bar = self.find(pane, "cameraFpsBar")
        self.assertTrue(self.find(pane, "cameraFpsScale").property("visible"),
                        "the graduations must be the face that is up")
        low, high = 0.5, 30.0
        # Three weights (the live request): a double tick at every 2.5
        # FPS, a continuous thin line at every 5, a thick line at every
        # 10 — and both ends of the range carry the thick line. A mark
        # is coded 2*major + line: a 10 is BOTH (a thick line is a line
        # like any other), a 5 is a thin one, a 2.5 is a tick.
        expected = {round((value - low) / (high - low), 4): level
                    for value, level in
                    ((0.5, 3), (2.5, 0), (5, 1), (7.5, 0), (10, 3), (12.5, 0),
                     (15, 1), (17.5, 0), (20, 3), (22.5, 0), (25, 1), (27.5, 0),
                     (30, 3))}
        marks = {}
        for item in bar.childItems():
            if item.property("major") is None:
                continue  # not a graduation delegate
            marks[round(item.property("fraction"), 4)] = item
        self.assertEqual({fraction: 2 * int(bool(item.property("major")))
                          + int(bool(item.property("line")))
                          for fraction, item in marks.items()},
                         expected, "the ruler's marks and their three weights")
        # Every 2.5 FPS sits the same distance from its neighbour: the
        # ruler is evenly spaced from the first tick up. The one gap
        # that is not a 2.5 is the foot's, and it cannot be: the ticks
        # stand on the 2.5 grid while the floor is the 0.5 the throttle
        # bottoms out at, so 2.0 FPS of track separate them.
        steps = sorted(marks)
        self.assertEqual(len(steps), len(expected), "no mark may be missed or doubled")
        # The distances come from the delegates' own y — the fractions
        # are read back rounded, and a hundredth of a fraction is wider
        # than the tolerance this check needs.
        positions = [marks[fraction].y() for fraction in steps]
        gaps = [abs(later - earlier) for earlier, later in zip(positions, positions[1:], strict=False)]
        reference = gaps[1]
        self.assertGreater(reference, 0)
        for gap in gaps[1:]:
            self.assertAlmostEqual(gap, reference, delta=0.05,
                                   msg="equal rate steps must be equal distances: %r" % (gaps,))
        self.assertAlmostEqual(gaps[0] / reference, 2.0 / 2.5, delta=1e-4,
                               msg="only the foot's own 2 FPS gap differs: %r" % (gaps,))
        self.assertAlmostEqual(reference, bar.height() * 2.5 / (high - low), delta=0.05,
                               msg="a 2.5 FPS step is 2.5 of the range's own width")
        for item in marks.values():
            left, right = item.childItems()
            if item.property("major") or item.property("line"):
                self.assertFalse(right.property("visible"),
                                 "a line is continuous, never an edge pair")
                self.assertAlmostEqual(left.width(), bar.width(), delta=0.5,
                                       msg="a line spans the bar")
                expected_height = 2 if item.property("major") else 1
                self.assertAlmostEqual(item.height(), expected_height, delta=0.1,
                                       msg="10 FPS lines are thick, 5 FPS lines thin")
            else:
                self.assertAlmostEqual(item.height(), 1, delta=0.1, msg="a tick is thin")
                self.assertTrue(right.property("visible"), "a tick is a double tick")
                self.assertAlmostEqual(left.width(), bar.width() * 0.25, delta=0.5,
                                       msg="the ticks reach a quarter in from the left")
                self.assertAlmostEqual(right.width(), bar.width() * 0.25, delta=0.5,
                                       msg="... and a quarter in from the right")

    def test_the_wheel_never_asks_for_more_than_the_camera_ceiling(self):
        # The ceiling is the camera's, so the wheel stops there: a
        # 15 FPS camera is never asked for more, and the clamp costs
        # no spurious commit at the limit. The step is the camera's own
        # slice of its range, so a narrow camera gets a narrow step.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=12.0, maximum=15.0)
        self.assertEqual(pane.property("fpsStep"), 0.5,
                         "the narrow range steps in halves, not in 1.25x jumps")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [12.5], "one notch is one step of the range")
        for _ in range(4):
            self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
            self.pump(30)
        self.assertEqual(model.fps_calls[-1], 14.5, "equal steps all the way up")
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls[-1], 15.0, "the ceiling is the last stop")
        calls = len(model.fps_calls)
        self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(len(model.fps_calls), calls,
                         "a notch past the ceiling commits nothing above it")
        self.assertEqual(pane.property("cameraFps"), 15.0)

    def test_a_popover_blocks_camera_gestures_but_the_uncovered_webcam_still_works(self):
        pane, window, model, _image, _frame = self._fps_pane(700, 700)
        component = harness.QQmlComponent(self.engine)
        component.loadUrl(harness.QUrl.fromLocalFile(str(harness.qml_source("MonitorPopOver.qml"))))
        card = component.create()
        self.assertIsNotNone(card, "\n".join(error.toString() for error in component.errors()))
        card.setParentItem(window.contentItem())
        card.setProperty("height", 300)
        card.setProperty("contentWidth", 300)
        card.setX(200)
        card.setY(200)
        self.addCleanup(card.deleteLater)
        self.pump(20)
        self._rate_drag(window, card, -12, steps=3)
        self.assertEqual(model.fps_calls, [], "right drag passed through the floating card")
        self._wheel(window, card, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
        self.pump(20)
        self.assertEqual(model.fps_calls, [], "shift-wheel passed through the floating card")
        area = self.find(pane, "cameraGestureArea")
        self._rate_drag(window, area, -12, x_ratio=.1, y_ratio=.5)
        self.assertEqual(model.fps_calls, [16.0], "the uncovered webcam lost its right drag")

    def test_the_right_drag_drives_the_rate_from_anywhere_over_the_picture(self):
        # The live request: the rate must be adjustable by dragging,
        # not only by a shifted wheel — and dragging UP raises it. The
        # travel is measured in the wheel's own steps, so the two
        # gestures cross the range alike, and a drag is a grab: the
        # rate face docks and the control stays where the hand is.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self.assertFalse(pane.property("cameraBarDocked"), "the bar starts parked")
        self._rate_drag(window, area, -12)
        self.assertEqual(model.fps_calls, [16.0], "12 px up is one step of the range")
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate's own gesture brings the rate's own face up")
        self.assertTrue(pane.property("cameraBarDocked"), "a drag docks the face")
        self.assertEqual(pane.property("cameraZoom"), 1.0,
                         "the rate drag must never move the picture")
        self._rate_drag(window, area, -12, steps=3)
        self.assertEqual(model.fps_calls, [16.0, 17.0, 18.0, 19.0],
                         "and the steps up are equal ones")
        self._rate_drag(window, area, 12)
        self.assertEqual(model.fps_calls[-1], 18.0, "down the same way")
        self.assertEqual(pane.property("cameraZoom"), 1.0)

    def test_the_steps_are_linear_at_every_point_on_the_scale(self):
        # The live report: the old 1.25x ladder accelerated towards the
        # top end, where one notch was worth several FPS. The step is
        # one constant slice of the selected camera's own range, so the
        # same flick moves the rate the same amount at the foot and at
        # the ceiling — and the slice scales with the camera.
        for maximum, step in ((15.0, 0.5), (30.0, 1.0), (60.0, 2.0), (120.0, 4.0)):
            with self.subTest(maximum=maximum):
                pane, window, model, _image, frame = self._fps_pane(
                    700, 700, fps=1.0, maximum=maximum)
                self.assertEqual(pane.property("fpsStep"), step,
                                 "the camera's range sets the step")
                for _ in range(4):  # the first steps of the range
                    self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
                    self.pump(30)
                low_end = [later - earlier for earlier, later
                           in zip(model.fps_calls, model.fps_calls[1:], strict=False)]
                for _ in range(3):  # and the steps further up the scale
                    self._wheel(window, frame, modifiers=harness.Qt.KeyboardModifier.ShiftModifier)
                    self.pump(30)
                high_end = [later - earlier for earlier, later
                            in zip(model.fps_calls, model.fps_calls[1:], strict=False)][len(low_end):]
                self.assertTrue(low_end and high_end, "both ends must have been walked")
                self.assertEqual(set(low_end + high_end), {step},
                                 "equal steps everywhere: %r then %r"
                                 % (low_end, high_end))

    def test_a_right_drag_past_the_bound_never_banks_the_overrun(self):
        # The live report: dragging far past the floor or the ceiling
        # left the whole overrun banked, and the pointer had to be
        # walked back that same distance before the rate answered
        # again. The bound is absolute — the travel spent against it is
        # thrown away, so the first step back moves the rate at once.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=26.0, maximum=30.0)
        area = self.find(pane, "cameraGestureArea")
        self.assertEqual(pane.property("fpsStep"), 1.0, "a one-FPS step here")
        # The whole picture is the drag's track, so a drag can run the
        # range and then some: from the foot of the picture upward.
        # The first steps land the ceiling, everything after is overrun.
        self._rate_drag(window, area, -12, steps=40, y_ratio=0.95)
        self.assertEqual(model.fps_calls[-1], 30.0, "the drag stops at the ceiling")
        self.assertEqual(pane.property("cameraFps"), 30.0)
        calls = len(model.fps_calls)
        self._rate_drag(window, area, 12, y_ratio=0.95)
        self.assertEqual(model.fps_calls[-1], 29.0,
                         "one step back from the ceiling answers at once — "
                         "the 480 px of overrun must not have to be undone first")
        self.assertEqual(len(model.fps_calls), calls + 1)
        # And the floor behaves the same way, from the top downward.
        self._rate_drag(window, area, 12, steps=40, y_ratio=0.05)
        self.assertEqual(model.fps_calls[-1], 0.5, "the drag stops at the floor")
        calls = len(model.fps_calls)
        self._rate_drag(window, area, -12, y_ratio=0.05)
        self.assertEqual(model.fps_calls[-1], 1.5,
                         "one step back from the floor answers at once")
        self.assertEqual(len(model.fps_calls), calls + 1)
        # The range's own ends are where the drags stopped, never past.
        self.assertTrue(all(0.5 <= call <= 30.0 for call in model.fps_calls),
                        "no commit ever leaves the camera's own range: %r" % (model.fps_calls,))

    def test_a_held_right_press_holds_the_park_off_until_the_release(self):
        # The rate drag is a grab like the scale's handle, and a grab
        # holds the five idle seconds off: parking the face out from
        # under a held pointer would leave the drag driving a control
        # that is no longer on the picture (the same live report the
        # scale handle answered).
        pane, window, _model, _image, _frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._rate_drag(window, area, -12, release=False)
        self.assertTrue(pane.property("cameraBarDocked"), "the drag docked the face")
        self.assertIsNotNone(pane.property("cameraBarHandle"),
                             "the picture reports the grab the scales report")
        self._pump_ms(5400)
        self.assertTrue(pane.property("cameraBarDocked"),
                        "five still seconds with the button held must not park it")
        from PyQt6.QtCore import QEvent, Qt
        x, y = area.width() / 2, area.height() / 2 - 12
        self._mouse(window, area, QEvent.Type.MouseButtonRelease, x, y,
                    Qt.MouseButton.NoButton, Qt.MouseButton.RightButton)
        self.pump(30)
        self.assertIsNone(pane.property("cameraBarHandle"), "the release lets go")
        self._wait_until(window, lambda _image: not pane.property("cameraBarDocked"),
                         timeout=8.0)
        self.assertFalse(pane.property("cameraBarDocked"),
                         "and the idle clock starts again at the release")

    def test_the_left_drag_still_pans_and_leaves_the_rate_alone(self):
        # The two drags must not trade places: the left button pans a
        # zoomed picture — vertically as well as horizontally — and
        # never touches the rate.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertGreater(pane.property("cameraZoom"), 1.0, "the wheel zoomed in")
        before = (pane.property("cameraPanX"), pane.property("cameraPanY"))
        self._drag(window, area, 0, -30)
        self.assertEqual(model.fps_calls, [], "a left drag commits no rate")
        self.assertNotEqual((pane.property("cameraPanX"), pane.property("cameraPanY")), before,
                            "a left drag still pans")
        # And a right press with no travel leaves the rate where it was.
        self._rate_drag(window, area, 0)
        self.assertEqual(model.fps_calls, [], "a still right press changes nothing")
        self.assertEqual(pane.property("cameraZoom"), 1.25,
                         "and never returns the fit")

    def test_a_right_double_click_never_returns_the_fit(self):
        # The left double click is the fit; the right button's second
        # press must not steal it, or a rate drag doubled by a nervous
        # hand would throw the view away.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertGreater(pane.property("cameraZoom"), 1.0, "the wheel zoomed in")
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        scene = area.mapToItem(window.contentItem(),
                               harness.QPointF(area.width() / 2, area.height() / 2))
        QTest.mouseDClick(window, harness.Qt.MouseButton.RightButton, harness.Qt.KeyboardModifier.NoModifier,
                          QPoint(int(scene.x()), int(scene.y())))
        self.pump(30)
        self.assertEqual(pane.property("cameraZoom"), 1.25,
                         "a right double click must leave the view where it is")
        self._double_click(window, area)
        self.assertEqual(pane.property("cameraZoom"), 1.0,
                         "the left double click is still the fit")
