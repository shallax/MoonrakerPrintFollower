"""Executable qml plate raster mapping contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_no_split_moves_the_ink_vertically(self):
        """The judder probe the split sweep could not be.

        The renderer-level sweep held the view fixed and swept the
        split: the canvas size and the prefix centroid were constant,
        so the split cannot resize a raster or move a stroke inside
        one. That says nothing about the COMPOSED scene, where a
        partial scrub is a native prefix raster under a QML canvas
        tail and the same scrub at full progress is one raster — two
        different producers of the same ink that must place it at the
        same device row. Hold the view fixed, sweep the split across
        its whole range, and require the red ink's vertical placement
        to be invariant: a step here is the judder."""
        monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]},
                   "travels": [], "travelStarts": [], "travelEnds": [],
                   "motions": 21}
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})

        def red_rows(image):
            origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
            rows = []
            for row in range(0, int(face.height())):
                for col in range(0, int(face.width())):
                    if self._matches(image.pixel(int(origin.x()) + col,
                                                 int(origin.y()) + row),
                                     (0xD3, 0x2F, 0x2F)):
                        rows.append(row)
                        break
            return rows

        # Split 1 composes no segment at all (a one-motion tail is
        # degenerate), so there is no placement to compare there. The
        # range below has ink on every value and crosses both
        # handovers: the prefix boundary at 10 and the full raster at
        # motions.
        measured = []
        previous = _baseline
        for split in list(range(2, 22)):
            self._printer.setSplit(split)
            # The split's own picture is the landmark, not a beat: a
            # fixed pump after the advance reads whatever the last
            # completed pass painted, and at a handover that is the
            # composition's own BLANK beat — a stable frame of its
            # own, which the assertion below then reports as the wall
            # never having painted. The read waits for the picture to
            # leave the previous split's, for the wall's ink to stand,
            # and for two grabs to agree. The assertion is unchanged,
            # so a split that paints nothing at all still fails.
            image = self._settled_frame(
                window, face, differs_from=previous,
                painted=lambda picture: red_rows(picture))
            previous = image
            rows = red_rows(image)
            self.assertTrue(rows, "the wall never painted at split %d" % split)
            measured.append((split, sum(rows) / float(len(rows)), len(rows),
                             min(rows), max(rows)))

        # The probe's own liveness control: the row set moves with the
        # geometry, so a constant one means something. At lineScale 8
        # the bed maps 1 mm to two device rows, so a wall one
        # millimetre further up the bed must read two rows up.
        probe = [[20.0 + motion * 10.0, 126.0, float(motion)]
                 for motion in range(21)]
        shifted = self._native_layer(
            {"classes": {"WALL-OUTER": [probe]}, "travels": [],
             "travelStarts": [], "travelEnds": [], "motions": 21},
            face, prefix_split=10)
        self._printer.setSplit(21)
        self._printer.setLayers({"prev": None, "current": shifted, "next": None})
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        # The install's decode is off-thread: the gate holds the
        # standing composition until the raster's texture is here, so
        # the sample waits for that landmark before reading the frame.
        self.assertTrue(self._settle_picture(window, face),
                        "the full raster's texture never arrived")
        moved = red_rows(window.grabWindow())
        self.assertTrue(moved, "the liveness control painted nothing")
        self.assertAlmostEqual(
            sum(moved) / float(len(moved)), measured[-1][1] - 2.0, delta=0.5,
            msg="the ink probe is blind: a wall 1 mm up the bed did not "
                "read two rows up, so a constant centroid proves nothing")

        centroids = [m[1] for m in measured]
        spread = max(centroids) - min(centroids)
        self.assertLessEqual(
            spread, 0.5,
            "the ink's vertical placement moved %.2f px across the split "
            "sweep (%s) — the prefix, the canvas tail and the full raster "
            "do not place the same stroke at the same row"
            % (spread, ", ".join("s%d=%.2f" % (m[0], m[1]) for m in measured)))

    def test_the_full_entry_never_blanks_the_standing_picture(self):
        """The async install's own hole, and the gate that closes it.

        The full raster's decode is off-thread: at the entry to the
        full state the model's validity already names the raster as
        the picture's owner while no texture is there yet, and the
        model publishes no scrub vector at 100%, so the canvas has
        nothing to redraw the interval from. Without the retention gate
        the canvas cleared on that null vector and the prefix hid on
        the split arithmetic, and the whole printed history went blank
        for the decode's length. The gate keeps the standing picture —
        the prefix' head over the canvas' accumulated tail — until the
        texture is here, so the entry frame is the same wall. The
        cadence is the sweep's own (measured here: the decode lands
        several hundred ms in, so this window is inside the hole).
        """
        monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [],
                   "travelStarts": [], "travelEnds": [], "motions": 21}

        def red_span(image):
            origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
            columns = []
            for row in range(0, int(face.height())):
                for column in range(0, int(face.width())):
                    if self._matches(image.pixel(int(origin.x()) + column,
                                                 int(origin.y()) + row),
                                     (0xD3, 0x2F, 0x2F)):
                        columns.append(column)
            return None if not columns else (min(columns), max(columns))

        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(15)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        # The partial composition has to SETTLE before the entry: the
        # prefix' own pixels decode off-thread too, and until they are
        # up the canvas owns the whole interval untrimmed — which would
        # hide the head's own producer behind the tail's and leave this
        # pin's prefix half vacuous.
        deadline = harness.time.monotonic() + 20.0
        while not face.property("_prefixWasShown") and harness.time.monotonic() < deadline:
            self.app.processEvents()
            harness.time.sleep(0.05)
            window.grabWindow()
        self.assertTrue(face.property("_prefixWasShown"),
                        "the partial composition never settled: the "
                        "prefix' pixels never came up")
        partial = red_span(window.grabWindow())
        self.assertIsNotNone(partial, "the partial composition never painted")

        # A full seek publishes no scrub vector (the model nulls it at
        # 100%), so the canvas can only hold its bitmap, never redraw
        # the interval: the standing picture is the only owner there is.
        self._printer.setScrub(None)
        self._printer.setSplit(21)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        entry = red_span(window.grabWindow())
        self.assertIsNotNone(
            entry, "the full state's entry blanked the standing picture "
                   "while the raster's texture was still decoding")
        self.assertLessEqual(
            entry[0], partial[0] + 1,
            "the entry lost the wall's low-motion end (%s -> %s): the "
            "prefix' head did not hold" % (partial, entry))
        self.assertGreaterEqual(
            entry[1], partial[1] - 1,
            "the entry lost the wall's high-motion end (%s -> %s): the "
            "canvas' accumulated tail did not hold" % (partial, entry))

    def test_a_camera_move_never_holds_the_previous_views_ink(self):
        """The hold's boundary: the retained ink was painted for one
        view, so a camera move must repaint it, never stand on it.

        Mutation: dropping the view clause from the hold's predicate
        keeps the old view's bitmap (the accumulation's view key never
        advances) while the raster decodes, and this pin fails.
        """
        monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [],
                   "travelStarts": [], "travelEnds": [], "motions": 21}
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        pan = float(plot_value["bed"]["plotWidth"]) * 0.25
        layer = self._native_layer(payload, face, prefix_split=10)
        # The hold's window IS the raster's decode, and at the face's
        # own size that decode lasts a few ms — less than the fixed
        # pumps below cost on a loaded host, which then read the
        # texture already landed and the hold dismissed. The same PNG
        # at an integer multiple decodes for hundreds of ms and
        # presents the same pixels (nearest-neighbour identity).
        layer.set_raster(layer.raster, "fixture-key",
                         self._slow_raster(layer.rasterData, "slow-view-hold",
                                           factor=8))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(15)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        painted = face.property("_accumViewKey")
        self.assertTrue(painted, "the canvas never recorded a view key")

        # The full state and the camera move arrive together: the
        # texture is still decoding, so the hold is the only thing that
        # could keep the old view's ink on screen.
        self._printer.setSplit(21)
        self.assertFalse(
            face.property("_rasterStatusReady"),
            "the raster's texture landed before the camera move: the hold "
            "is no longer the picture's owner")
        face.setProperty("viewPanX", pan)
        # The production view feed invalidates the old raster key.
        layer.set_expected_key("panned-fixture")
        # The split's own paint request is still pending — nothing has
        # rendered since it was made — so ONE render is the camera
        # move's first paint, and the read follows it with no event
        # turn in between. A waited-on read cannot hold this boundary:
        # the view settle 60 ms behind the pan repaints the new view
        # too, so a wait passes on the settle's repaint whether or not
        # the camera move's own paint kept the old view's ink.
        window.grabWindow()
        if face.property("_accumViewKey") == painted:
            # An older actual upload can still own the transaction's
            # delivery slot. Its bitmap is masked, never presented as
            # the new view, while the coalesced current paint waits.
            cover = self.find(face, "moonrakerPlatePreparingCover")
            self.assertTrue(cover.property("visible"),
                            "the obsolete view's bitmap remained visible")
            self._wait_until(window, lambda _image: face.property("_accumViewKey") != painted)
        self.assertNotEqual(
            face.property("_accumViewKey"), painted,
            "the camera move's own paint kept the previous view's ink: "
            "the hold stood on a bitmap baked for another view")
        self.assertFalse(
            face.property("_rasterStatusReady"),
            "the raster's texture landed before the new view's ink: the "
            "hold's window closed under the read")

    def test_a_camera_move_never_holds_the_prefix_at_the_previous_view(self):
        """The prefix hold's boundary, the canvas hold's own pin.

        The prefix' standing pixels are a bake of ONE view (the model
        re-bakes them per view), so a camera move must drop the hold —
        else the entry keeps the previous view's scale on screen while
        the raster decodes. Mutation: dropping the view clause from
        ``_prefixHoldsFull`` keeps the prefix standing at the old view
        and this pin fails.
        """
        monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [],
                   "travelStarts": [], "travelEnds": [], "motions": 21}
        prefix = face.findChild(harness.QQuickItem, "moonrakerPlatePrefixImage")
        self.assertIsNotNone(prefix, "the prefix image never mounted")
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        pan = float(plot_value["bed"]["plotWidth"]) * 0.25
        layer = self._native_layer(payload, face, prefix_split=10)
        # The hold's window IS the raster's decode, and at the face's own
        # size that decode lasts a few ms — less than the fixed pumps
        # below cost on a loaded host, which then read the texture
        # already landed and the hold dismissed. The same PNG at an
        # integer multiple decodes for hundreds of ms and presents the
        # same pixels (nearest-neighbour identity).
        layer.set_raster(layer.raster, "fixture-key",
                         self._slow_raster(layer.rasterData, "slow-class-hold"))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(15)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        deadline = harness.time.monotonic() + 20.0
        while not face.property("_prefixWasShown") and harness.time.monotonic() < deadline:
            self.app.processEvents()
            harness.time.sleep(0.05)
            window.grabWindow()
        self.assertTrue(face.property("_prefixWasShown"),
                        "the partial composition never settled: the "
                        "prefix' pixels never came up")
        # The full seek: no scrub vector, and the raster decoding, so
        # the hold is the only thing that can stand the head. The
        # window opens when the raster's source binds and the decode
        # starts, and the reads below take no event turn after it: the
        # hold is judged on the camera move alone, never on whatever a
        # busy host got to in the meantime.
        self._printer.setScrub(None)
        self._printer.setSplit(21)
        raster_item = None
        deadline = harness.time.monotonic() + 5.0
        while raster_item is None and harness.time.monotonic() < deadline:
            self._pump_ms(2)
            raster_item = self._image_with_source(face, "slow-class-hold")
        self.assertIsNotNone(raster_item,
                             "the full state never bound a raster to decode")
        self.assertFalse(
            face.property("_rasterStatusReady"),
            "the raster's texture landed before the assertion: the hold is "
            "no longer the picture's owner")
        self.assertTrue(
            prefix.property("visible"),
            "the entry's hold never stood: the prefix' head was not the "
            "picture while the raster decoded")
        face.setProperty("viewPanX", pan)
        self.assertFalse(
            face.property("_rasterStatusReady"),
            "the raster's texture landed between the camera move and the "
            "read: the hold's drop could not be judged")
        self.assertFalse(
            prefix.property("visible"),
            "the hold kept the prefix standing at the previous view while "
            "the raster decoded")

    def test_the_gesture_overlay_places_the_ink_where_the_exact_scene_does(self):
        """Two presentations of one geometry, measured against each other.

        A gesture presents the warm 4x composite (``navigationImage``)
        with the carried tail's vector canvas over it; idle presents
        the exact scene's own raster. Same bed, same split, same view
        — the ink has to land on the same device row, because the flip
        between the two is a visibility change and nothing else.

        It does not, quite: measured, the overlay sits up to 0.5 px
        lower and draws a narrower band (at 100% zoom the exact
        scene's wall is rows 262-265 and the overlay's is 263-264).
        The 0.5 px is a defect to be driven to zero, not a contract —
        this pin exists so it cannot GROW, and a 1 px disagreement
        fails it.

        Matching the two sampling modes does NOT remove it: forcing
        ``navigationImage.smooth: false`` clears the 0.5 px at zoom
        1.37 and leaves or introduces it at 1.00 and 2.00. Whatever
        places the overlay's ink, it is not the sampling mode alone.

        A progress-slider scrub never enters this state, so nothing
        here can explain a judder seen during a scrub: the only
        entries are the wheel and drag handlers, the scope's drag and
        the centre-on-toolhead button."""
        from mpf.Plate.PlateQt import render_navigation_layer, png_file
        monitor, window, face, _baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [],
                   "travelStarts": [], "travelEnds": [], "motions": 21}
        self._printer.setScrub(payload)
        self._printer.setSplit(21)
        pv = face.property("plot")
        if hasattr(pv, "toVariant"):
            pv = pv.toVariant()
        plot = {"offsetX": float(pv["bed"]["offsetX"]),
                "offsetY": float(pv["bed"]["offsetY"]),
                "sx": float(pv["sx"]), "sy": float(pv["sy"]),
                "bedXMin": float(pv["bed"]["bedXMin"]),
                "bedYMax": float(pv["bed"]["bedYMax"])}
        nav_view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": 1.0, "lineScale": 8.0, "compact": False,
                    "panX": 0.0, "panY": 0.0, "dpr": 4.0}
        nav = render_navigation_layer({"prev": None, "current": payload,
                                       "next": None}, plot, nav_view, 21)
        url = png_file(nav, "/tmp/mpf/raster-probe", "nav-overlay")
        self._printer.setNavigation(url)

        def rows(image):
            origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
            out = []
            for row in range(0, int(face.height())):
                for col in range(0, int(face.width())):
                    if self._matches(image.pixel(int(origin.x()) + col,
                                                 int(origin.y()) + row),
                                     (0xD3, 0x2F, 0x2F)):
                        out.append(row)
                        break
            return out

        def settle(differs_from):
            # The presentation's own ink is the landmark, not a beat: a
            # fixed pump after the flip reads whatever the last
            # completed pass painted, and at a handover that is the
            # composition's own BLANK beat — a stable frame of its own,
            # which the assertions below then report as the
            # presentation having painted nothing. The read waits for
            # the picture to leave the one it replaces, for this
            # presentation's ink to stand, and for two grabs to agree;
            # the assertions are unchanged.
            return self._settled_frame(window, face, differs_from=differs_from,
                                       painted=rows)

        def centroid(rs):
            return sum(rs) / float(len(rs)) if rs else None

        measured = []
        previous = _baseline
        for zoom in (1.0, 1.37, 1.5, 1.79, 2.0):
            face.setProperty("_interactionActive", False)
            face.setProperty("viewScale", zoom)
            face.setProperty("displayScale", zoom)
            # The exact scene bakes the zoom into its own raster; the
            # warm composite is camera-independent and never re-bakes.
            layer = self._native_layer(payload, face, scale=zoom)
            self._printer.setLayers({"prev": None, "current": layer,
                                     "next": None})
            self.pump(20)
            exact_image = settle(previous)
            exact = rows(exact_image)
            face.setProperty("_gestureNavSource", url)
            face.setProperty("_interactionActive", True)
            self.pump(30)
            gesture_image = settle(exact_image)
            gesture = rows(gesture_image)
            self.assertTrue(exact, "the exact scene painted nothing at %s" % zoom)
            self.assertTrue(gesture,
                            "the gesture overlay painted nothing at %s" % zoom)
            measured.append((zoom, centroid(exact), centroid(gesture),
                             len(exact), len(gesture)))
            previous = gesture_image

        for zoom, exact, gesture, exact_rows, gesture_rows in measured:
            self.assertLessEqual(
                abs(gesture - exact), 0.5,
                "the gesture overlay places the wall %.2f px off the exact "
                "scene at zoom %.2f (%s vs %s)"
                % (gesture - exact, zoom, gesture, exact))
            self.assertLessEqual(
                gesture_rows, exact_rows + 1,
                "the overlay's stroke is thicker than the exact scene's at "
                "zoom %.2f (%d rows vs %d) — a resampled blur, not the same "
                "stroke" % (zoom, gesture_rows, exact_rows))

    def test_the_carried_tail_lands_on_the_warm_rasters_own_pixels(self):
        """The gesture's carried tail, measured at the camera it runs at.

        The warm navigation raster is camera-free: its content is the
        100%-fit plot at the backing, and the flip into a gesture changes
        only the presentation (`displayScale / backing`, the pan on the
        item's translation). ``carryCanvas`` paints the lines printed
        since that raster's own split over it through the same
        presentation, so a tail painted camera-free lands on the
        raster's pixels — and a tail that is not does not.

        Measured, it did neither, and both defects are in this one item.
        The canvas scales about QML's default ``Item.Center`` origin
        while its presentation is authored for a top-left one (the
        raster grows from a fixed corner; the canvas swung its content by
        ``size * (1 - displayScale / backing)`` — more than the face is
        wide at 4x backing, so no tail was EVER visible during a zoomed
        gesture, on any host). And ``_paintCarry`` authored at
        ``viewScale * backing`` while the item presents at
        ``displayScale / backing``, magnifying the tail by the live zoom
        against the raster it completes.

        The existing overlay pin cannot see either: it drives
        ``_interactionActive`` directly, and a tail that lands outside
        the face leaves the warm raster's own ink standing — exactly the
        picture that pin asserts. This one enters through the real gate
        (a press and a panning move, the only entries production has) and
        paints the warm raster EMPTY, so the only fixture ink in the
        frame is the carrier's.

        The fixture is a ten-motion tail over a twenty-motion layer, near
        the bed's centre so the centred zoom the wheel reaches keeps both
        strokes on the face. The reference frame is the same geometry at
        the same camera through the native renderer — the producer nobody
        questions — and it validates the camera arithmetic the tail is
        then measured against. The tail's run centre, its length, its rows
        and the travel-to-wall offset all move when it arrives at the
        wrong scale or off its corner."""
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent
        from mpf.Plate.PlateQt import png_file, render_navigation_layer

        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showTravels", True)
        self.pump(10)
        wall = [[90.0 + motion * 4.0, 155.0, float(motion)]
                for motion in range(21)]
        travel = [[90.0 + motion * 4.0, 115.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [wall]}, "travels": [travel],
                   "travelStarts": [], "travelEnds": [], "motions": 21}
        empty = {"classes": {}, "travels": [], "travelStarts": [],
                 "travelEnds": [], "motions": 21}
        zoom = 1.5625
        raster_split = 10

        def red(pixel):
            return self._matches(pixel, (0xD3, 0x2F, 0x2F))

        def mouse(kind, x, y, buttons):
            """A real mouse event at face-local (x, y). A move is about
            NO button: carrying one makes Qt read it as a fresh press,
            which re-selects the target mid-drag and hands the grab to
            whatever arrived under the pointer."""
            faced = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove \
                else Qt.MouseButton.LeftButton
            scene = face.mapToItem(window.contentItem(), QPointF(x, y))
            event = QMouseEvent(
                kind, QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()),
                                                  int(scene.y())))),
                faced, buttons, Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)

        def settle_size():
            """Wait for the face's own size to stop moving.

            The scope docks on a viewScale change and the face resizes
            with it, so a raster baked against one width and a camera
            read against another disagree by half the difference — a
            fixture error that would read as a placement defect."""
            previous = None
            deadline = harness.time.monotonic() + 6.0
            while harness.time.monotonic() < deadline:
                size = (int(face.width()), int(face.height()))
                if size == previous:
                    return size
                previous = size
                self._pump_ms(120)
            return previous or (int(face.width()), int(face.height()))

        def camera():
            """The centred zoom the wheel reaches: the bed stays over the
            face on both axes, and the warm raster presents it with the
            pan riding the item's translation.

            The pan is re-set once the dock has resized the face: the
            zoom is what triggers the dock, so the pan computed before
            it belongs to the face the mount started with."""
            for name, value in (("viewScale", zoom), ("displayScale", zoom),
                                ("viewPanX", 0.0), ("displayPanX", 0.0),
                                ("viewPanY", 0.0), ("displayPanY", 0.0)):
                face.setProperty(name, value)
            self._pump_ms(500)
            width, height = settle_size()
            pan_x = (1.0 - zoom) * width / 2.0
            pan_y = (1.0 - zoom) * height / 2.0
            for name, value in (("viewPanX", pan_x), ("displayPanX", pan_x),
                                ("viewPanY", pan_y), ("displayPanY", pan_y)):
                face.setProperty(name, value)
            self._pump_ms(200)

        def expectation(bed_x, bed_y):
            plot = self._bed_point(face, 0.0, 0.0)
            scale = float(face.property("displayScale") or 1.0)
            return ((plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
                    * scale + float(face.property("displayPanX") or 0.0),
                    (plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
                    * scale + float(face.property("displayPanY") or 0.0))

        def install(split, prefix_split):
            """The face's state at the camera: an EMPTY warm raster (so
            the carrier is the gesture frame's only fixture producer), a
            warm split of ten motions that the layer's split runs ahead
            of, and the exact scene's own rasters at the same camera."""
            plot = self._bed_point(face, 0.0, 0.0)
            width, height = float(face.width()), float(face.height())
            nav = render_navigation_layer(
                {"prev": None, "current": empty, "next": None}, plot,
                {"width": int(width), "height": int(height), "scale": 1.0,
                 "lineScale": 8.0, "compact": False, "panX": 0.0, "panY": 0.0,
                 "backing": 4.0, "bedWidth": 250.0, "bedDepth": 250.0,
                 "showTravels": True},
                split)
            self._printer.setNavigation(png_file(nav, "/tmp/mpf/raster-probe",
                                                 "carry-nav-%d" % split))
            self._printer.setNavigationSplit(raster_split, 4.0)
            layer = self._native_layer(
                payload, face, prefix_split=prefix_split, scale=zoom,
                pan_x=(1.0 - zoom) * width / 2.0,
                pan_y=(1.0 - zoom) * height / 2.0)
            self._printer.setScrub(payload)
            self._printer.setLayers({"prev": None, "current": layer,
                                     "next": None})
            self._printer.setSplit(split)
            self._pump_ms(400)

        def placement(image, predicate):
            """The longest fixture-bearing run: its columns, and the row
            band it opens with.

            Runs shorter than twenty columns are dropped — this face
            carries two isolated chrome specks that wear the travel's
            colour, and a fixture stroke is hundreds of columns long. The
            row band stops at the first empty row because a drawn stroke
            is one contiguous band, which those specks are not."""
            runs = [run for run in self._colour_runs(image, face, window,
                                                     predicate)
                    if run[1] - run[0] >= 20]
            if not runs:
                return None
            run = self._longest_run(runs)
            origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
            top = bottom = None
            for row in range(0, int(face.height())):
                hit = False
                for col in range(run[0], run[1] + 1):
                    if predicate(image.pixel(int(origin.x()) + col,
                                             int(origin.y()) + row)):
                        hit = True
                        break
                if hit:
                    bottom = row
                    if top is None:
                        top = row
                elif top is not None:
                    break
            return {"run": run, "top": top, "bottom": bottom,
                    "length": run[1] - run[0],
                    "centre": (top + bottom) / 2.0}

        def painted(image):
            """A coarse census for the settle poll: the full run census
            costs ~0.2 s a call and the poll runs every 10 ms, so the
            predicate samples on a stride. The fixture's strokes are
            horizontal and hundreds of px long, so neither can slip
            between samples."""
            origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
            for row in range(0, int(face.height()), 2):
                for col in range(0, int(face.width()), 2):
                    pixel = image.pixel(int(origin.x()) + col,
                                        int(origin.y()) + row)
                    if red(pixel) or self._is_travel(pixel):
                        return True
            return False

        def frame(differs_from):
            return self._settled_frame(window, face, differs_from=differs_from,
                                       painted=painted, timeout=5.0)

        # 1: the reference. The same geometry at the same camera through
        # the native renderer, whose rasters are not in question — so a
        # failure here is the fixture's or the camera arithmetic's, and
        # the tail's own measurement rests on both.
        camera()
        install(21, None)
        reference = frame(baseline)
        ref_wall = placement(reference, red)
        ref_travel = placement(reference, self._is_travel)
        self.assertIsNotNone(ref_wall, "the reference never painted the wall")
        self.assertIsNotNone(ref_travel,
                             "the reference never painted the travel")
        wall_x, wall_y = expectation(90.0, 155.0)
        _travel_x, travel_y = expectation(90.0, 115.0)
        self.assertLessEqual(
            abs(ref_wall["centre"] - wall_y), 3.0,
            "the reference's own wall sits on row %.1f where the live "
            "camera puts row %.1f — the fixture or the camera arithmetic "
            "is wrong, not the carrier" % (ref_wall["centre"], wall_y))
        self.assertLessEqual(
            abs(ref_travel["centre"] - travel_y), 3.0,
            "the reference's own travel sits on row %.1f where the live "
            "camera puts row %.1f — the fixture or the camera arithmetic "
            "is wrong, not the carrier" % (ref_travel["centre"], travel_y))
        # The round cap pulls the first inked column left of the stroke's
        # first vertex and never right of it: the camera arithmetic's own
        # check, on the producer that is not in question.
        self.assertLessEqual(
            ref_wall["run"][0], wall_x + 1.0,
            "the reference's wall starts right of its first vertex")
        self.assertGreaterEqual(
            ref_wall["run"][0], wall_x - 12.0,
            "the reference's wall starts %d px left of its first vertex — "
            "further than the stroke's round cap reaches"
            % (wall_x - ref_wall["run"][0]))
        # Both strokes are the same geometry with the same first and last
        # vertex, so one frame's two runs are one length: a run that was
        # clipped or stretched changes it.
        self.assertLessEqual(
            abs(ref_wall["length"] - ref_travel["length"]), 4,
            "the reference's own two strokes disagree about their length "
            "(%d vs %d) — a producer is clipping ink"
            % (ref_wall["length"], ref_travel["length"]))

        # 2: the tail's frame, entered through the real gate. The idle
        # frame first, so the entry has a picture to differ from.
        install(18, raster_split)
        idle = frame(reference)
        cx, cy = int(face.width() / 2), int(face.height() / 2)
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        self._pump_ms(60)
        mouse(QEvent.Type.MouseMove, cx + 4, cy, Qt.MouseButton.LeftButton)
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline \
                and not face.property("_interactionActive"):
            self._pump_ms(20)
        self.assertTrue(face.property("_interactionActive"),
                        "the panning move never entered the interaction")
        # Back to the camera the fixture was baked at: the carrier is
        # only measurable against the reference at the SAME camera.
        mouse(QEvent.Type.MouseMove, cx, cy, Qt.MouseButton.LeftButton)
        self._pump_ms(400)
        carried = frame(idle)
        carry_wall = placement(carried, red)
        carry_travel = placement(carried, self._is_travel)
        self.assertIsNotNone(
            carry_wall,
            "the carried tail painted no wall at zoom %.2f: the tail is the "
            "only producer of the lines the split ran past, so a gesture "
            "frame without it stands the walls and hides the print's newest "
            "half. A tail authored at the live zoom instead of the raster's "
            "own lands magnified past the face's edge, and one scaled about "
            "the item's centre lands further off again" % zoom)
        self.assertIsNotNone(
            carry_travel,
            "the carried tail painted no travel at zoom %.2f — the same two "
            "placements carry the travel channel with the walls" % zoom)

        # The tail's own span of the layer, against the reference's.
        # The reference draws the layer whole — its vertices run from
        # motion 0 to motion 20, so its ink's midpoint is motion 10's
        # vertex — while the tail draws the warm raster's split to the
        # live one, motions raster_split - 1 to split - 1, midpoint
        # motion 13. The two midpoints are therefore three motions, 12
        # mm, apart, and a run's MIDPOINT carries neither a round cap
        # nor a resampling fringe at its ends: it is the placement
        # measure that survives the two frames' different regimes.
        plot = self._bed_point(face, 0.0, 0.0)
        span = 12.0 * plot["sx"] * zoom
        carry_centre = (carry_wall["run"][0] + carry_wall["run"][1]) / 2.0
        ref_centre = (ref_wall["run"][0] + ref_wall["run"][1]) / 2.0
        self.assertLessEqual(
            abs(carry_centre - ref_centre - span), 2.0,
            "the carried tail's centre stands %.1f px from the reference's "
            "where its own span of the layer puts it %.1f px — the carrier "
            "and the raster disagree about where the layer is"
            % (carry_centre - ref_centre, span))
        self.assertLessEqual(
            carry_wall["length"], ref_wall["length"] - 60,
            "the carrier painted %d columns against the reference's %d — "
            "that is the whole layer, not the tail the split left it"
            % (carry_wall["length"], ref_wall["length"]))
        self.assertLessEqual(
            abs(carry_wall["length"] - carry_travel["length"]), 4,
            "the carrier's two strokes disagree about their length (%d vs "
            "%d) — one of them is clipped"
            % (carry_wall["length"], carry_travel["length"]))
        for label, carry_one, ref_one in (("wall", carry_wall, ref_wall),
                                          ("travel", carry_travel, ref_travel)):
            self.assertLessEqual(
                abs(carry_one["centre"] - ref_one["centre"]), 2.0,
                "the carried %s sits on row %.1f against the reference's "
                "%.1f at the same camera — the tail is drawn at the wrong "
                "scale or off its corner"
                % (label, carry_one["centre"], ref_one["centre"]))
        self.assertLessEqual(
            abs((carry_travel["centre"] - carry_wall["centre"])
                - (ref_travel["centre"] - ref_wall["centre"])), 2.0,
            "the carried travel stands %.1f px off the carried wall where "
            "the reference's stands %.1f — the two carriers of one frame "
            "disagree"
            % (carry_travel["centre"] - carry_wall["centre"],
               ref_travel["centre"] - ref_wall["centre"]))

        # 3: the pan. The live complaint's own shape (worst on the
        # furthest move): the carried tail must ride the item's
        # translation with the raster, by the pointer's own delta.
        mouse(QEvent.Type.MouseMove, cx + 12, cy, Qt.MouseButton.LeftButton)
        self._pump_ms(250)
        panned = frame(carried)
        panned_wall = placement(panned, red)
        panned_travel = placement(panned, self._is_travel)
        self.assertIsNotNone(panned_wall,
                             "the carried wall left the frame across the pan")
        self.assertIsNotNone(panned_travel,
                             "the carried travel left the frame across the pan")
        for label, before, after in (("wall", carry_wall, panned_wall),
                                     ("travel", carry_travel, panned_travel)):
            self.assertAlmostEqual(
                float(after["run"][0] - before["run"][0]), 12.0, delta=1.5,
                msg="the pan moved the carried %s by %d px for a 12 px "
                    "pointer move" % (label, after["run"][0] - before["run"][0]))
            self.assertLessEqual(
                abs(after["centre"] - before["centre"]), 1.5,
                "the pan moved the carried %s off its row by %.1f px — the "
                "tail was redrawn, not translated"
                % (label, after["centre"] - before["centre"]))
            self.assertLessEqual(
                abs(after["length"] - before["length"]), 3,
                "the carried %s changed length across the pan (%d -> %d)"
                % (label, before["length"], after["length"]))
        self.assertLessEqual(
            abs((panned_travel["centre"] - panned_wall["centre"])
                - (carry_travel["centre"] - carry_wall["centre"])), 1.5,
            "the pan moved the carried travel against the carried wall")

        mouse(QEvent.Type.MouseButtonRelease, cx + 12, cy,
              Qt.MouseButton.NoButton)
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline \
                and face.property("_interactionActive"):
            self._pump_ms(30)

    def test_the_worker_raster_covers_the_faces_device_rect(self):
        """The worker's canvas against the rect the face shows it in.

        The face presents these rasters across its own device rect,
        `logical * dpr`, and presents them 1:1 — the worker paints at
        the screen's physical resolution so the scene graph has
        nothing to resample. That holds only if the canvas IS that
        extent: `int(logical * dpr)` truncates a product that is whole
        only at whole scale factors, leaving the texture short of its
        target, and the graph stretches it into the larger rect — the
        ink moves further the further it sits from the raster's origin.
        Measured on this rig at 1.75, against the raster the face
        actually presented on the 530x581 face this pin was first
        written against: the shortfall was 0.75 device px on the
        height and 0.5 on the width, and a band census at 1.5, where
        that width was exact, confined the extra ink to the bands away
        from the anchor — 164 px in the middle band and 142 in the
        bottom against 0 in the top. The bound here is half a device
        pixel, which is half a logical pixel at every scale factor of
        1 or more, and the rounding canvas holds it at every ratio up
        to the backing clamp.

        Two halves, because the suite's ambient scale factor is 1.0,
        where every candidate rounds alike: the LIVE raster is measured
        at whatever ratio this process runs at (the sibling test runs
        this same method at 1.375), and the canvas factory is measured
        at the fractional ratios directly, which is where truncation
        breaks the bound. The factory loop calls `_new_canvas` rather
        than the rounding helper, so the sibling's failure is a
        measurement of the extent and not an import error.
        """
        from mpf.Plate.PlateQt import _new_canvas

        payload = {"classes": {"WALL-OUTER": [
                       [[12.0 + i * 3.0, 12.0, float(i)] for i in range(6)]]},
                   "travels": [], "travelStarts": [], "travelEnds": [],
                   "motions": 6}
        # The popover the census was measured on: its face is 565x581
        # logical in this fixture's window, and the ratios below are
        # derived from that face rather than written down — a fixture
        # pinned to literals goes stale the moment the face's width
        # moves, and the pin then measures rects every canvas satisfies.
        _monitor, window, face = self._follower_popover(1400, 1000)
        self.pump(30)
        dpr = float(window.devicePixelRatio())
        width, height = float(face.width()), float(face.height())
        self.assertGreater(width, 0.0, "the face has no size to measure")
        # Three quarters of the way up to the next whole device pixel:
        # the worst residue a truncating canvas can leave on each axis,
        # and more than the bound a rounding one must hold — so every
        # ratio here discriminates, and the guard below says so.
        ratios = []
        for axis in (width, height):
            axis_ratios = []
            for scale in (1.25, 1.5, 1.75):
                whole = int(axis * scale)
                ratio = (whole + 0.75) / axis
                if 1.1 <= ratio <= 2.0:
                    axis_ratios.append(round(ratio, 6))
            self.assertGreaterEqual(
                len(axis_ratios), 2,
                "the %.1f px axis yields no ratio inside the backing "
                "clamp's range that a truncating canvas could fail" % axis)
            ratios.extend(axis_ratios)
        for ratio in sorted(set(ratios)):
            target_w, target_h = width * ratio, height * ratio
            self.assertGreater(
                max(abs(target_w - int(target_w)),
                    abs(target_h - int(target_h))), 0.5,
                "the fixture's device rect at %.3f is %.2fx%.2f on a "
                "%.1fx%.1f face, and neither axis is fractional by more "
                "than the bound, so no canvas can fail this ratio"
                % (ratio, target_w, target_h, width, height))
            canvas = _new_canvas({"width": width, "height": height,
                                  "dpr": ratio})
            self.assertGreater(canvas.width(), 0,
                               "no canvas at %.3f" % ratio)
            for axis, made, target in (("width", canvas.width(), target_w),
                                       ("height", canvas.height(), target_h)):
                self.assertLessEqual(
                    abs(target - made), 0.5,
                    "at %.3f the canvas is %d device px %s for the %.2f px "
                    "rect the face presents it in: the scene graph stretches "
                    "the texture and the ink moves with the distance from "
                    "the raster's origin (truncating the extent would leave "
                    "%.2f px)" % (ratio, made, axis, target,
                                  abs(target - int(target))))
        # The backing clamp's ceiling, where the product is whole again.
        canvas = _new_canvas({"width": width, "height": height, "dpr": 2.0})
        self.assertEqual((canvas.width(), canvas.height()),
                         (int(width * 2.0), int(height * 2.0)),
                         "a whole ratio must stay exact (%d, %d for %.1fx%.1f)"
                         % (canvas.width(), canvas.height(), width, height))

        # The live raster, at the ratio this process runs at, through
        # the real renderer on the real face.
        layer = self._native_layer(payload, face, dpr=dpr)
        raster_w = int(layer.property("rasterWidth"))
        raster_h = int(layer.property("rasterHeight"))
        self.assertGreater(raster_w, 0, "the worker painted no raster")
        for axis, made, target in (("width", raster_w, width * dpr),
                                   ("height", raster_h, height * dpr)):
            self.assertLessEqual(
                abs(target - made), 0.5,
                "the worker's raster is %d device px %s for the %.2f px rect "
                "the face presents it in at dpr %.3f: the scene graph "
                "stretches the texture and the ink moves with the distance "
                "from the raster's origin" % (made, axis, target, dpr))

    def test_the_raster_covers_the_device_rect_at_a_fractional_ratio(self):
        """The same measurement at 1.375, in its own process.

        Qt fixes the scale factor when the application is built, so a
        fractional ratio needs a child — QT_SCALE_FACTOR is what the
        offscreen platform honours, and the child reports the ratio it
        actually ran at, so this cannot pass by having the variable
        ignored. 1.375 is where the truncation is furthest outside the
        bound on BOTH axes on this face: 0.75 px wide and 0.875 px
        high, against 0.25 and 0.125 for the rounding canvas.
        """
        import subprocess
        import textwrap

        root = harness.pathlib.Path(__file__).resolve().parents[1]
        # The driver must NOT build the application itself: the suite's
        # _start_application raises SkipTest when one already exists, so
        # a probe that creates it turns the whole child into "Ran 0
        # tests ... skipped=1" — a green that ran nothing. The ratio is
        # read after the run, from the application the suite built.
        driver = textwrap.dedent("""
            import sys, unittest
            import tests.test_qml_plate_raster_mapping as module
            suite = unittest.TestSuite([module.PlateFaceRenderTests(
                "test_the_worker_raster_covers_the_faces_device_rect")])
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            from PyQt6.QtGui import QGuiApplication
            print("DPR %.3f" % QGuiApplication.primaryScreen().devicePixelRatio(),
                  flush=True)
            sys.exit(0 if result.wasSuccessful() else 1)
            """)
        script = harness.pathlib.Path(harness.tempfile.gettempdir()) / "mpf-raster-dpr.py"
        script.write_text(driver)
        env = dict(harness.os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_SCALE_FACTOR"] = "1.375"
        env["PYTHONPATH"] = harness.os.pathsep.join(
            [str(root / "tests"), str(root),
             env.get("PYTHONPATH", "")]).rstrip(harness.os.pathsep)
        result = subprocess.run([harness.sys.executable, str(script)], cwd=str(root),
                                env=env, capture_output=True, text=True,
                                timeout=300)
        # Both streams: TextTestRunner writes its report to stderr, so
        # a check that reads stdout alone sees the driver's own line
        # and nothing else.
        said = (result.stdout or "") + (result.stderr or "")
        # All three, or the child can report success without having
        # measured anything: a skipped test passes, and so does a child
        # that quietly ran at the ambient ratio.
        self.assertIn("Ran 1 test", said,
                      "the child did not EXECUTE the census, so it proves "
                      "nothing:\n%s" % said[-3000:])
        self.assertNotIn("skipped", said,
                         "the child skipped the census:\n%s" % said[-3000:])
        self.assertIn("DPR 1.375", said,
                      "the child did not run at DPR 1.375, so it proves "
                      "nothing:\n%s" % said[-3000:])
        self.assertEqual(result.returncode, 0,
                         "the raster did not cover the device rect at 1.375:"
                         "\n%s" % said[-3000:])

    def test_the_raster_and_the_vector_place_the_ink_identically(self):
        """The two producers of the printed prefix, measured against
        each other at the same view and split.

        A partial composition is either the Ready prefix raster over a
        delivered canvas, or the delivered VECTOR owning the whole
        interval when the prefix raster has not arrived — both are
        named in _fullReleaseReady(). If those two disagree about
        where a stroke lies, then which one a scrub lands on decides
        the ink's position, and the choice is arrival timing: a
        per-render race inside the exact scene with no view input
        changing. It would look exactly like a sub-render that cannot
        decide where a pixel lies.

        Measured with a redness centroid, not a colour census — at the
        live lineScale of 0.7 the stroke is sub-pixel and a threshold
        census sees nothing at all. They agree to 0.000 px in every
        configuration: lineScale 0.7 to 3.0, at the fit view and at a
        fractional zoom. So the producers do not disagree, and the
        handover between them is not a step either.

        The 0.5 px bound is the tolerance, not the measurement: a
        one-pixel displacement of the vector canvas reads 0.998 px and
        fails this, so the measurement would see a disagreement.

        Getting a real comparison out of this fixture is itself the
        finding. The first version compared the raster-served prefix
        against a serving with no prefix at all and PASSED, and the
        canvas mutation did not fail it: the second serving was not
        drawing the vector. The retained prefix was still standing,
        because _retainedPrefixApplies() holds it on split and anchor
        alone and the second serving's split was still past its
        boundary — so the "vector" band measured a retained copy of the
        first serving's raster, and the comparison was the raster with
        itself. The retained record is armed at four sites (progress,
        the prefix image's handlers, the canvas paint) and none of them
        records the VIEW, so a retained frame baked at another zoom,
        pan or lineScale can stand in for the current composition.
        Serving the vector below the prefix boundary is what disarms
        it; the interval is shorter there and a horizontal wall's row
        does not depend on the interval's length."""
        monitor, window, face, _baseline = self._mount_empty()
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {"classes": {"WALL-OUTER": [points]}, "travels": [],
                   "travelStarts": [], "travelEnds": [], "motions": 21}
        self._printer.setScrub(payload)
        self._printer.setSplit(15)
        pv = face.property("plot")
        if hasattr(pv, "toVariant"):
            pv = pv.toVariant()
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        ox = float(pv["bed"]["offsetX"])
        sx = float(pv["sx"])

        def centroid(image, bed_x0, bed_x1):
            c0 = int(origin.x() + ox + bed_x0 * sx)
            c1 = int(origin.x() + ox + bed_x1 * sx)
            total = 0.0
            weighted = 0.0
            for row in range(0, int(face.height())):
                weight = 0.0
                for col in range(c0, c1):
                    px = image.pixel(col, int(origin.y()) + row)
                    weight += max(0.0, float((px >> 16) & 0xFF)
                                  - float((px >> 8) & 0xFF))
                if weight > 0.0:
                    total += weight
                    weighted += weight * row
            return None if total <= 0.0 else weighted / total

        def delivered(split, coverage, prefix):
            """The scene's DELIVERED record names this serving.

            The canvas writes its delivered coverage and the split it
            walked to in the paint's own onPainted handler, and its
            texture flag beside them: all three together are the
            picture the scene holds, not a paint that was requested.
            """
            if split != face.property("_lastSplit"):
                return False
            if coverage != face.property("_vectorCoversShown"):
                return False
            if not face.property("_textureReady"):
                return False
            return not prefix or bool(face.property("_prefixStatusReady"))

        def settle(bed_x0, bed_x1, split, coverage, prefix):
            """The first frame the serving's composition is IN.

            This was a frame count — pump, grab, pump, grab — which
            reads the picture a fixed distance after the install: the
            prefix raster decodes off-thread, and on a loaded runner
            the census ran before it landed and measured the standing
            picture instead. The parity assertion then compared the
            raster with itself at the previous serving's view — the
            97.7 px at zoom 1.37, which is one serving's zoom (0.37)
            applied to a ~265 px plate.

            Two waits, because a texture is consumed a beat after its
            painted signal: the first clears on the delivered record,
            the second on that same record plus the ink the caller is
            about to measure in the frame. The centroid comparison
            below is untouched — a real disagreement still fails it,
            and a composition that never arrives fails the caller's
            own assertIsNotNone.
            """
            self._wait_until(window,
                             lambda _image: delivered(split, coverage, prefix))
            return self._wait_until(
                window,
                lambda image: delivered(split, coverage, prefix)
                and centroid(image, bed_x0, bed_x1) is not None)

        # The two servings must not share a retained record: the
        # retained prefix stands on split and anchor alone, so a split
        # at or past its boundary keeps serving the PREVIOUS serving's
        # raster and the "vector" row measured would be that raster
        # again — a comparison of the raster with itself, which no
        # canvas mutation can fail. The vector owns the interval only
        # where the retained record is disarmed, so its serving runs a
        # split BELOW the prefix boundary: the rendered interval is
        # shorter, and a horizontal wall's ROW does not depend on the
        # interval's length.
        measured = []
        for line_scale in (0.7, 1.0, 1.5, 3.0):
            for scale in (1.0, 1.37):
                face.setProperty("lineScale", line_scale)
                face.setProperty("viewScale", scale)
                self.pump(10)
                # Serving A: a Ready prefix raster over the canvas tail.
                served = self._native_layer(payload, face, prefix_split=10,
                                            line_scale=line_scale, scale=scale)
                self._printer.setLayers({"prev": None, "current": served,
                                         "next": None})
                self._printer.setSplit(15)
                with_raster = settle(25.0, 110.0, 15, 10, prefix=True)
                # Serving B: below the prefix boundary nothing applies,
                # so the canvas owns the whole rendered interval.
                vector_only = self._native_layer(payload, face,
                                                 line_scale=line_scale,
                                                 scale=scale)
                self._printer.setLayers({"prev": None, "current": vector_only,
                                         "next": None})
                self._printer.setSplit(5)
                by_vector = settle(25.0, 60.0, 5, 0, prefix=False)
                a = centroid(with_raster, 25.0, 110.0)
                b = centroid(by_vector, 25.0, 60.0)
                self.assertIsNotNone(
                    a, "the raster-served prefix painted nothing at lineScale "
                       "%.1f zoom %.2f" % (line_scale, scale))
                self.assertIsNotNone(
                    b, "the vector-served prefix painted nothing at lineScale "
                       "%.1f zoom %.2f" % (line_scale, scale))
                measured.append((line_scale, scale, a, b))
                self.assertLessEqual(
                    abs(a - b), 0.5,
                    "the printed prefix's stroke sits %.3f px differently "
                    "when the vector owns the interval instead of the raster "
                    "(lineScale %.1f zoom %.2f) — which of the two a scrub "
                    "lands on is arrival timing, so the ink would move with "
                    "it" % (b - a, line_scale, scale))
        self._printer.setSplit(15)


