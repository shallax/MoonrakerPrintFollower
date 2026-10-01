"""Executable qml plate travels contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_the_layer_slider_handle_click_and_keyboard_steps_hold(self):
        # The reviewer's slider findings: a click on the handle must
        # NOT move the slider (movement is track clicks and drags
        # only), and a handle click focuses the slider so the arrows
        # nudge one step — the focus must survive the settle and the
        # apply.
        monitor, window, face, baseline = self._mount_empty()
        sliders = monitor.findChildren(harness.QQuickItem, "moonrakerFollowerLayerSlider")
        self.assertTrue(sliders, "the layer slider never mounted")
        slider = sliders[0]
        self.pump(10)
        self._printer.calls.clear()

        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent

        def handle_centre_x():
            return (slider.property("leftPadding")
                    + slider.property("visualPosition")
                    * (slider.property("availableWidth") - 16.0))

        def click(x):
            scene = slider.mapToItem(window.contentItem(),
                                     QPointF(x, slider.height() / 2))
            for kind in (QEvent.Type.MouseButtonPress,
                         QEvent.Type.MouseButtonRelease):
                QGuiApplication.sendEvent(window, QMouseEvent(
                    kind, QPointF(scene),
                    QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
                    else Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier))

        def press(key_value):
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QGuiApplication.sendEvent(window, QKeyEvent(
                    kind, key_value, Qt.KeyboardModifier.NoModifier))

        def seek_issued():
            return any(call[0] == "layer" for call in self._printer.calls)
        # 1: a click on the handle must not move the value.
        before = slider.property("value")
        click(handle_centre_x())
        self._pump_ms(100)
        self.assertEqual(slider.property("value"), before,
                         "a handle click moved the slider")
        self.assertFalse(seek_issued(), "a handle click issued a seek")
        # 2: a click on the track moves the slider there.
        track_x = slider.property("leftPadding") + 0.25 * slider.property("availableWidth")
        click(track_x)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and not seek_issued():
            self._pump_ms(30)
        self.assertTrue(seek_issued(), "a track click never issued a seek")
        self.assertGreater(slider.property("value"), before,
                           "the track click never moved the value")
        # 3: a handle click focuses the slider; the arrows nudge one
        # step per press, and the focus survives the settle and the
        # apply.
        self._printer.calls.clear()
        click(handle_centre_x())
        self._pump_ms(30)
        self.assertTrue(slider.property("activeFocus"),
                        "a handle click never focused the slider")
        # The handle's BOTH halves grab (the reviewer's finding: one
        # side of the grab handle moved the slider, the other never
        # grabbed).
        def drag_from(x, dx):
            scene = slider.mapToItem(window.contentItem(),
                                     QPointF(x, slider.height() / 2))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            scene2 = slider.mapToItem(window.contentItem(),
                                      QPointF(x + dx, slider.height() / 2))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseMove, QPointF(scene2),
                QPointF(window.mapToGlobal(QPoint(int(scene2.x()), int(scene2.y())))),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(scene2),
                QPointF(window.mapToGlobal(QPoint(int(scene2.x()), int(scene2.y())))),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
        for offset in (-6.0, 6.0):
            before_side = slider.property("value")
            drag_from(handle_centre_x() + offset, 40.0)
            self._pump_ms(300)
            self.assertGreater(slider.property("value"), before_side,
                               "the handle's far side never grabbed")
        anchor_at = slider.property("value")
        press(Qt.Key.Key_Right)
        self._pump_ms(30)
        self.assertEqual(slider.property("value"), anchor_at + 1,
                         "an arrow key never nudged one step")
        self._pump_ms(300)  # past the key debounce and the apply
        self.assertTrue(slider.property("activeFocus"),
                        "the apply stole the slider's focus")
        press(Qt.Key.Key_Right)
        self._pump_ms(30)
        self.assertEqual(slider.property("value"), anchor_at + 2,
                         "the slider lost its keyboard steps after the apply")
        self._pump_ms(300)
        self.pump(20)

    def test_a_focused_slider_reports_its_destruction_for_the_rebuild(self):
        # The reviewer's focus finding: a Moonraker republish replaces
        # a repeater's delegates while the slider holds the focus. The
        # shared component reports its own destruction with the
        # control identity (the dashboard's re-grant walk consumes it
        # — the token-pinned wiring), so the fresh delegate can take
        # the focus back — fans, LEDs and PWM alike.
        monitor, window, face, baseline = self._mount_empty()
        sliders = monitor.findChildren(harness.QQuickItem, "moonrakerFollowerLayerSlider")
        self.assertTrue(sliders, "the layer slider never mounted")
        slider = sliders[0]
        slider.setProperty("controlObject", "fan0")
        slider.setProperty("controlKind", "fan")
        recorded = []
        slider.focusLostByDestruction.connect(
            lambda object_, kind: recorded.append((object_, kind)))
        # The handle click takes the focus (the reviewer's flow).
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent
        centre = (slider.property("leftPadding")
                  + slider.property("visualPosition")
                  * (slider.property("availableWidth") - 16.0) + 8.0)
        scene = slider.mapToItem(window.contentItem(),
                                 QPointF(centre, slider.height() / 2))
        for kind in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease):
            QGuiApplication.sendEvent(window, QMouseEvent(
                kind, QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
                else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
        self.pump(10)
        self.assertTrue(slider.property("activeFocus"),
                        "the handle click never focused the slider")
        # The republish tears the delegate down: the dying slider
        # reports its identity while it still holds the focus.
        monitor.setProperty("openPopOver", "")
        self.pump(10)
        window.grabWindow()
        self.pump(10)
        self.assertEqual(recorded, [("fan0", "fan")],
                         "the dying slider never reported itself")
        self.pump(20)

    def test_full_progress_travels_render_without_the_vector(self):
        # : showTravels at 100% — the travel
        # lines ride their native sibling while scrubVector stays
        # null, so the travels never silently vanish with the
        # suppressed vector.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("showTravels", True)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [[[30.0, 30.0, 1.0], [90.0, 30.0, 2.0]]]},
            "travels": [[[30.0, 200.0, 2.0], [90.0, 200.0, 3.0]]],
            "travelStarts": [], "travelEnds": [], "motions": 4,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(4)
        _image, red = self._wait_red(window, face, want=True)
        self.assertGreater(red, 0, "the layer drew nothing")
        # The travel purple: nothing else on the face wears it, so
        # its presence proves the travel sibling blitted — the class
        # raster alone carries no purple.
        _image, purple = self._wait_purple(window, face)
        self.assertGreater(purple, 0, "the travel line's sibling never drew")

    def test_a_pan_moves_the_travels_with_the_walls(self):
        """The live pan symptom's own shape: after a camera move the
        travels must land where the walls are, not where the pane used
        to be. Both are baked into their rasters and the exact scene
        anchors both to fill the face, so the pan rides in the pixels —
        the wall and the travel have to move by the SAME amount. A
        travels raster carried over across a pan leaves the travel
        exactly where it was while the walls move, which is the reported
        tens-of-pixels miss (worst on the furthest move)."""
        monitor, window, face, _baseline = self._mount_empty()
        # The dot is a red mark of its own: left live it is what a red
        # census finds, and the wall it stands in for is never measured
        # at all. _mount_empty clears it.
        self.assertIsNone(face.property("dot"),
                          "the toolhead dot would be read as wall ink")
        face.setProperty("lineScale", 8.0)
        # Off by default, and the travels image is gated on it.
        face.setProperty("showTravels", True)
        self.pump(10)
        # The payload here is the LAYER payload — the same dict the
        # scrub vector carries and the renderer's painters read.
        payload = {"classes": {"WALL-OUTER": [self.PAN_WALL]},
                   "travels": [self.PAN_TRAVEL],
                   "travelStarts": [], "travelEnds": [], "motions": 26}
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        # plotWidth is already device px and sx is plotWidth/mm, so the
        # pan is a quarter of the plot width and NOT scaled again.
        pan = float(bed["plotWidth"]) * 0.25

        def wall_run(image):
            return self._longest_run(self._colour_runs(
                image, face, window,
                lambda p: self._matches(p, (0xD3, 0x2F, 0x2F))))

        def travel_run(image):
            return self._longest_run(
                self._colour_runs(image, face, window, self._is_travel))

        def both_runs(image):
            return wall_run(image) is not None and travel_run(image) is not None

        def install(pan_x, differs_from):
            # The pan is BAKED: the renderer paints the shifted picture
            # and the scene anchors it to fill the face, so the ink
            # moves by pan_x and the image itself never translates.
            layer = self._native_layer(payload, face, pan_x=pan_x)
            self._printer.setScrub(payload)
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            self._printer.setSplit(26)
            face.setProperty("viewPanX", pan_x)
            face.setProperty("viewPanY", 0.0)
            # The pan's own handover is sampled by the travels pin
            # below; this one waits for the picture the pan actually
            # settles at. A fixed beat after the install is not that
            # picture: the grab returns the last pass's, which on a host
            # whose frames trail the evaluation is the pre-pan one (the
            # scene anchors the raster to fill the face, so the stale
            # pane reads as a shift of zero), and `_wait_purple` clears
            # on the first purple pixel — which the held pane has too.
            return self._settled_frame(window, face, differs_from=differs_from,
                                       painted=both_runs)

        before = install(0.0, _baseline)
        wall_before, travel_before = wall_run(before), travel_run(before)
        self.assertIsNotNone(wall_before, "the walls never painted at pan 0")
        self.assertIsNotNone(travel_before, "the travels never painted at pan 0")

        after = install(pan, before)
        wall_after, travel_after = wall_run(after), travel_run(after)
        self.assertIsNotNone(wall_after, "the walls never painted after the pan")
        self.assertIsNotNone(travel_after, "the travels never painted after the pan")

        wall_shift = wall_after[0] - wall_before[0]
        travel_shift = travel_after[0] - travel_before[0]
        # The pan has to be big enough that a stale pane is unmistakable
        # rather than a pixel of resampling noise.
        self.assertAlmostEqual(float(wall_shift), pan, delta=2.0,
                               msg="the pan never moved the walls to where "
                                   "it was asked to (%d px for %.1f)"
                                   % (wall_shift, pan))
        # The translation control: the same stroke is the same length.
        # A threshold artifact or a re-clipped raster changes it.
        for label, run_before, run_after in (("wall", wall_before, wall_after),
                                             ("travel", travel_before,
                                              travel_after)):
            self.assertAlmostEqual(
                float(run_after[1] - run_after[0]),
                float(run_before[1] - run_before[0]), delta=2.0,
                msg="the %s changed length across the pan (%s -> %s) — it "
                    "was not translated, it was redrawn" % (label, run_before,
                                                            run_after))
        self.assertAlmostEqual(
            float(travel_shift), float(wall_shift), delta=2.0,
            msg="the travels moved %d px while the walls moved %d px — the "
                "two producers disagree about the pan"
                % (travel_shift, wall_shift))

    def test_the_travels_never_blank_as_the_split_reaches_full(self):
        """The entry the owner reported: the split reaches full and the
        travel ink LEAVES the picture while the walls stand.

        The two halves of the full state arrive on their own clocks: the
        class raster's texture is the walls and the travels raster's is
        the travel ink. The travels Image binds its source only once the
        split is full (a partial scrub shows no travels raster at all),
        so its decode STARTS on the flip that completes the layer — the
        walls are already standing while the travels are still decoding.
        A composition that retires the canvas on the class raster's
        readiness alone stands nothing there: for the whole decode the
        walls stand and the travels are gone.

        The travels' source here is the same PNG at an integer multiple
        of its size: `anchors.fill` with the default Stretch and
        `smooth: false` presents a nearest-neighbour downscale of a
        nearest-neighbour upscale, so the picture cannot change by a
        pixel and only the decode lengthens (a raster at the face's own
        size decodes in a few ms — no sample lands inside that). Every
        frame from the flip to the settle is censused, and each frame's
        ruling is the travels Image's own Loading state, read through
        QML as the face's gates read it.
        """
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showTravels", True)
        self.pump(10)
        payload = {"classes": {"WALL-OUTER": [self.PAN_WALL]},
                   "travels": [self.PAN_TRAVEL],
                   "travelStarts": [], "travelEnds": [], "motions": 26}
        plot = self._bed_point(face, 0.0, 0.0)

        def wall_run(image):
            return self._feature_run(image, face, window, plot, 200.0,
                                     lambda p: self._matches(p, (0xD3, 0x2F, 0x2F)))

        def travel_run(image):
            return self._feature_run(image, face, window, plot, 120.0,
                                     self._is_travel)

        def both_runs(image):
            return wall_run(image)[0] is not None and travel_run(image)[0] is not None

        # The partial state first — the shape production's own
        # `scrub_vector` publishes below the split: the prefix
        # raster owns the printed history, the canvas the tail, and the
        # travels raster is not shown at all.
        layer = self._native_layer(payload, face, prefix_split=13)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(13)
        before = self._settled_frame(window, face, differs_from=baseline,
                                     painted=both_runs)
        wall_before, travel_before = wall_run(before), travel_run(before)
        self.assertIsNotNone(wall_before, "the walls never painted at the partial split")
        self.assertIsNotNone(travel_before,
                             "the travels never painted at the partial split")

        # The slow source rides the SAME layer the flip completes: the
        # class raster is hot (its texture never leaves) and only the
        # travels' decode starts on the flip. The stretch widens the
        # window the first census sample has to land inside; wider
        # decodes are past the reader's allocation limit, and a much
        # longer one only starves the very host the census is measuring.
        layer.set_travels(layer.travelRaster, "fixture-key",
                          self._slow_raster(layer.travelData, "slow-travels"))
        self.assertIsNone(
            self._image_with_source(face, "slow-travels"),
            "the travels' decode had started before the split was full")

        # The premise the census judges: a settled partial split hands
        # the printed history to the prefix, and its shown record is the
        # delivered word for that. A trim still in flight at the flip
        # lands after it instead, dropping the printed ink mid-decode
        # and leaving the census nothing to judge.
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline and not face.property("_prefixWasShown"):
            self.pump(5)
        self.assertTrue(
            face.property("_prefixWasShown"),
            "the partial composition never handed the printed history to the "
            "prefix: the full split's hold had no history to stand")

        # The full split: the model publishes NO scrub vector here
        # (`scrub_vector` returns None at split == motions), so the
        # canvas has no geometry of its own to redraw — the picture it
        # holds is the only copy of the ink it was standing.
        status_of = self._status_probe()
        self._printer.setScrub(None)
        self._printer.setSplit(26)
        # The bind first, with NO event pass and NO sleep before it: the
        # flip binds the travels' source synchronously, and the sample
        # that has to land inside the decode is the FIRST one. An event
        # pass or a wall-clock sleep here costs tens of milliseconds on
        # a wide face, and a host whose scene work is slower than its
        # decode spends the whole window there — the first ruling then
        # reads Ready and the census has nothing left to judge. The
        # pump (never the sleep) is the hang guard for the bind itself.
        travels_item = self._image_with_source(face, "slow-travels")
        deadline = harness.time.monotonic() + 5.0
        while travels_item is None and harness.time.monotonic() < deadline:
            self.pump(2)
            travels_item = self._image_with_source(face, "slow-travels")
        self.assertIsNotNone(travels_item,
                             "the travels Image never bound the slow source")

        # The census is ANCHORED at the flip and runs until the travels'
        # own texture arrives: a fixed count of frames is a fixed budget
        # of TIME on a host whose grabs are slow, and a loaded one can
        # read every sample after the decode — passing by never having
        # looked at the handover it is about.
        frames = []
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            # No event pass before the FIRST grab either, for the same
            # reason: the frame the window opens on is the first one the
            # census can rule, and a pass spends window it cannot see.
            if frames:
                self.pump(5)
            image = window.grabWindow()
            # The status AFTER the grab: a texture that has not landed by
            # the read cannot have stood in the picture the grab returned.
            status = status_of.statusOf(travels_item)
            frames.append((wall_run(image), travel_run(image), status))
            if status == 1:  # Image.Ready
                break

        def completed(image):
            wall_run_now, travel_run_now = wall_run(image), travel_run(image)
            return (wall_run_now[0] is not None and travel_run_now[0] is not None
                    and travel_run_now[1] - travel_run_now[0]
                    > travel_before[1] - travel_before[0] + 20)

        after = self._settled_frame(window, face, differs_from=before,
                                    painted=completed)
        wall_after, travel_after = wall_run(after), travel_run(after)
        # The census' own status sequence in the message: a settle that
        # never completed is either a texture still in flight or a
        # composition that did not present it, and the two need
        # different answers.
        statuses = [frame[2] for frame in frames]
        self.assertIsNotNone(
            wall_after,
            "the walls never painted at the full split (travels' status %r)"
            % (statuses,))
        self.assertIsNotNone(
            travel_after,
            "the travels never painted at the full split (travels' status %r)"
            % (statuses,))

        # The liveness control: the flip the frames were sampled across
        # really happened — the same view origin, MORE ink. A census
        # that read one static picture would satisfy the invariant below
        # for the wrong reason.
        self.assertAlmostEqual(
            float(wall_after[0]), float(wall_before[0]), delta=2.0,
            msg="the walls moved across the flip (%s -> %s) although the view "
                "never changed" % (wall_before, wall_after))
        self.assertGreaterEqual(
            wall_after[1] - wall_before[1], 40,
            msg="the full split added no wall ink (%s -> %s): the layer never "
                "completed" % (wall_before, wall_after))
        self.assertGreaterEqual(
            travel_after[1] - travel_before[1], 40,
            msg="the full split added no travel ink (%s -> %s)"
                % (travel_before, travel_after))

        # The non-vacuity control: the handover WAS sampled — frames were
        # read while the travels' texture was still decoding. Without it
        # a host that sampled after the flip would pass the invariant by
        # never having looked at the state it is about.
        pending = [frame for frame in frames if frame[2] != 1]  # Image.Ready
        standing = [frame for frame in frames if frame[0][0] is not None]
        self.assertTrue(
            pending, "the travels' decode was never sampled: %r"
            % ([frame[2] for frame in frames],))
        self.assertTrue(standing, "no sampled frame stood the walls")

        # The invariant: while the walls stand and the travels' own
        # texture is not here, the travel ink the previous state was
        # standing must still be there — the same run, in the same
        # place. Neither a blank, nor the travels raster's texture at
        # some other view.
        for wall_run_now, travel_run_now, status in frames:
            if wall_run_now[0] is None or status == 1:  # Image.Ready
                continue
            self.assertIsNotNone(
                travel_run_now[0],
                "the walls stood with no travel ink at all while the travels "
                "were still decoding (status %d, wall %s)"
                % (status, wall_run_now))
            self.assertAlmostEqual(
                float(travel_run_now[0]), float(travel_before[0]), delta=2.0,
                msg="the held travel ink was displaced while the travels "
                    "decoded (%s -> %s, status %d)"
                    % (travel_before, travel_run_now, status))
            self.assertAlmostEqual(
                float(travel_run_now[1]), float(travel_before[1]), delta=2.0,
                msg="the held travel ink changed length while the travels "
                    "decoded (%s -> %s, status %d)"
                    % (travel_before, travel_run_now, status))

    def test_the_travels_never_present_with_the_previous_view_s_walls(self):
        """The travels Image presents ONLY on the exact pair.

        The reported displacement: the new view's travel ink standing
        over the previous view's walls — a shift that grows with the
        move, not a hole. Its premise is a travels texture that is
        available while the class raster's is not, and the guard is the
        travels Image's own presentation: it may present only when BOTH
        textures are here (`_exactFullStanding()`), never on the
        payload's presence alone. The pre-fix binding presented on
        presence (`opacity: 0.8` with a payload-only `visible`), which
        stands any travels texture that is in hand over whatever the
        canvas is holding.

        The walls' real asynchronous Image request is held until a
        pending frame has actually been sampled, then released. Qt may
        queue the travels behind it or load them independently: in
        either ordering, their presentation property must stay gated
        until the exact pair arrives. The held frame's two inks must
        remain coherent, and the new pair must eventually present.

        The pan is baked into both rasters, so the two views' ink
        differ by the pan in pixels: the census compares the travel
        ink's OFFSET to the wall ink against the offset the settled
        previous view held, which is a displacement of `pan` when the
        pair is mixed and zero when the picture is coherent.
        """
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showTravels", True)
        self.pump(10)
        payload = {"classes": {"WALL-OUTER": [self.PAN_WALL]},
                   "travels": [self.PAN_TRAVEL],
                   "travelStarts": [], "travelEnds": [], "motions": 26}
        plot = self._bed_point(face, 0.0, 0.0)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        pan = float(plot_value["bed"]["plotWidth"]) * 0.25

        def travel_run(image):
            return self._feature_run(image, face, window, plot, 120.0,
                                     self._is_travel)

        def wall_run(image):
            return self._feature_run(image, face, window, plot, 200.0,
                                     lambda p: self._matches(p, (0xD3, 0x2F,
                                                                 0x2F)))

        def travels_painted(image):
            return travel_run(image)[0] is not None

        # The view the pan starts from, landed whole: the held picture
        # the mixed ordering would stand over, and the offset the two
        # producers agree on while nothing is in flight.
        layer = self._native_layer(payload, face, pan_x=0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(26)
        self.pump(10)
        before = self._settled_frame(window, face, differs_from=baseline,
                                     painted=travels_painted)
        held_travel, held_wall = travel_run(before), wall_run(before)
        self.assertIsNotNone(held_travel[0], "the travels never painted at pan 0")
        self.assertIsNotNone(held_wall[0], "the walls never painted at pan 0")
        held_offset = held_travel[0] - held_wall[0]

        # The pan: both rasters are baked for the new view, and the
        # class raster's decode is the slow one — the ordering the
        # displacement needs is the travels texture arriving first.
        moved = self._native_layer(payload, face, pan_x=pan)
        # Hold a REAL asynchronous Image request until the pending frame
        # has been sampled. Inflating a PNG only lengthened its decode;
        # fast hosts could finish before the first grab and skip the very
        # state this regression must prove. No application is created by
        # this fixture (the domain's normal mount owns it).
        import threading
        from PyQt6.QtGui import QImage
        from PyQt6.QtQuick import QQuickImageProvider

        source = QImage(harness.QUrl(moved.rasterData).toLocalFile())
        self.assertFalse(source.isNull(), "the pending fixture needs real wall pixels")
        released = threading.Event()
        started = threading.Event()

        class PendingImage(QQuickImageProvider):
            def __init__(self):
                super().__init__(QQuickImageProvider.ImageType.Image)

            def requestImage(self, image_id, requested_size):
                started.set()
                released.wait(30.0)
                return source, source.size()

        provider_name = "pending-walls"
        provider = PendingImage()
        self.engine.addImageProvider(provider_name, provider)
        self.addCleanup(self.engine.removeImageProvider, provider_name)
        self.addCleanup(released.set)
        moved.set_raster(moved.raster, "fixture-key",
                         "image://%s/slow-class" % provider_name)
        status_of = self._status_probe()
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": moved, "next": None})
        self._printer.setSplit(26)
        face.setProperty("viewPanX", pan)
        face.setProperty("viewPanY", 0.0)

        # The travels Image's OWN texture: the slow class raster's
        # sibling, found by the source the payload published for it.
        travels_item = None
        deadline = harness.time.monotonic() + 5.0
        while travels_item is None and harness.time.monotonic() < deadline:
            self._pump_ms(2)
            travels_item = self._image_with_source(face, "-t.png")
        self.assertIsNotNone(travels_item, "the travels Image never bound a source")
        wall_item = self._image_with_source(face, "slow-class")
        self.assertIsNotNone(wall_item, "the class raster never bound the slow source")

        # Sampled from the install to the pair's own arrival: the
        # guard's whole window, both halves of the pair in flight and
        # the beat between them (Image.Ready is 1).
        frames = []
        deadline = harness.time.monotonic() + 8.0
        while harness.time.monotonic() < deadline:
            self.pump(3)
            image = window.grabWindow()
            frames.append((status_of.statusOf(wall_item),
                           status_of.statusOf(travels_item),
                           bool(travels_item.property("visible")),
                           float(travels_item.property("opacity") or 0.0),
                           travel_run(image), wall_run(image)))
            if frames[-1][0] != 1 and started.is_set():
                # The pending state is now actually observed. Let Qt
                # finish the request, then also sample the landed pair.
                released.set()
            if frames[-1][0] == 1 and frames[-1][1] == 1:
                break

        # The non-vacuity controls: the guard was sampled — frames were
        # read while the class raster's texture was still decoding, and
        # the travels Image was SHOWN through them (its payload belongs
        # to the view; only its ink is withheld). A pin that sampled
        # after the pair landed, or one whose travels Image was hidden,
        # would clear the invariant below by never having looked at the
        # state it is about.
        pending = [frame for frame in frames if frame[0] != 1]
        self.assertTrue(
            pending, "the walls' texture was never pending: %r"
            % ([frame[0] for frame in frames],))
        self.assertTrue(
            [frame for frame in pending if frame[2]],
            "the travels Image was never shown while the walls' texture "
            "was pending: %r" % ([(frame[0], frame[2]) for frame in pending],))

        # The invariant: the travels Image presents on the exact pair
        # and on nothing less — not on the payload's presence (the
        # pre-fix binding), and not on the walls' texture alone (the
        # beat measured here, where the class raster is Ready and the
        # travels are still decoding). The frame's own ruling rides
        # along: while either texture is in flight, the travel ink may
        # not stand at the new view's columns over the old walls, which
        # is the held offset broken by `pan`.
        for wall_status, travels_status, _shown, opacity, run, wall in frames:
            if wall_status == 1 and travels_status == 1:
                continue
            self.assertEqual(
                opacity, 0.0,
                "the travels Image presented at opacity %r with the pair "
                "not exact (wall status %d, travels status %d)"
                % (opacity, wall_status, travels_status))
            if run[0] is not None and wall[0] is not None:
                self.assertAlmostEqual(
                    float(run[0] - wall[0]), float(held_offset), delta=2.0,
                    msg="travel ink stood %d px from the wall ink (held %d, "
                        "pan %.1f px) with the pair not exact (wall status "
                        "%d, travels status %d): the two producers disagree "
                        "about the view" % (run[0] - wall[0], held_offset,
                                            pan, wall_status, travels_status))

        # The arrival: the travels DO present once the pair is exact —
        # the predicate is a gate, not a permanent hide, so the
        # invariant above cannot be cleared by never presenting at all.
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline:
            self._pump_ms(10)
            if float(travels_item.property("opacity") or 0.0) >= 0.79:
                break
        self.assertAlmostEqual(
            float(travels_item.property("opacity") or 0.0), 0.8, delta=0.01,
            msg="the travels never presented after the pair arrived (wall "
                "status %d, travels status %d)"
                % (status_of.statusOf(wall_item),
                   status_of.statusOf(travels_item)))


