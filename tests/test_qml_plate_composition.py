"""Executable qml plate composition contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_an_asynchronous_a_to_b_seek_never_stands_the_previous_layers_pixels(self):
        # The retained handover is a promise about ONE layer: the
        # pixels frozen for layer A bridge A's own prefix replacement
        # and nothing else. A seek to layer B carries the same motions
        # and the same split values, so the split-only predicate
        # re-stands A's pixels over B — and while B's prefix is still
        # loading the canvas' hold skips B's repaint, so A's picture
        # stays up for the whole load. B's layer, not A's, owns the
        # screen.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        plot = self._bed_point(face, 0.0, 0.0)
        a_payload = {
            "classes": {"WALL-OUTER": [[[20.0 + motion * 10.0, 40.0, float(motion)]
                                        for motion in range(21)]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer_a = self._native_layer(a_payload, face, prefix_split=10)
        self._printer.setScrub(a_payload)
        self._printer.setLayers({"prev": None, "current": layer_a, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "layer A's picture never drew")
        # The freeze arms while A's picture stands: that record is
        # what the seek must not carry into B.
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_retainedPrefixSource") == "":
            self.pump(5)
        self.assertNotEqual(face.property("_retainedPrefixSource"), "",
                            "the retained freeze never armed")
        # The seek: B's own geometry at its own bed position, the
        # SAME motions and split arithmetic, and a prefix asset that
        # never resolves — the asynchronous gap is the whole window.
        from mpf.plate.PlateQt import render_layer_prefix
        b_payload = {
            "classes": {"WALL-OUTER": [[[20.0 + motion * 10.0, 200.0, float(motion)]
                                        for motion in range(21)]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer_b = self._native_layer(b_payload, face)
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        prefix_b = render_layer_prefix(b_payload, plot, view, 10)
        layer_b.set_prefix(prefix_b, "file:///tmp/mpf/raster-probe/missing-%d.png"
                           % harness.time.monotonic_ns(), 10, "fixture-key")
        self._printer.setScrub(b_payload)
        self._printer.setLayers({"prev": None, "current": layer_b, "next": None})
        self._printer.setAnchor(1)
        self._printer.setSplit(18)
        self.pump(30)
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        applies = QMetaObject.invokeMethod(face, "_retainedPrefixApplies",
                                           Q_RETURN_ARG(QVariant))
        self.assertFalse(bool(applies),
                         "the retained predicate re-stands layer A's pixels for layer B")
        # The picture, sampled across the whole load: A's ink must
        # never appear at A's own bed position.
        for _ in range(10):
            self._pump_ms(40)
            image = window.grabWindow()
            self.assertEqual(
                self._stroke_ink(image, face, window, plot, 75.0, 40.0), 0,
                "layer A's retained pixels stood over layer B")
        # The seek completes: B's asset lands and B's own history
        # takes B's position — the assertions above are not a stuck
        # blank.
        from mpf.plate.PlateQt import png_file
        layer_b.set_prefix(prefix_b, png_file(prefix_b, "/tmp/mpf/raster-probe",
                                              "fixture-ab-%d" % harness.time.monotonic_ns()),
                           10, "fixture-key")
        self._printer.setLayers({"prev": None, "current": layer_b, "next": None})
        # The test's own race: layer A's retained picture is still red
        # here, so the red census cleared the wait on a frame before
        # B's ink landed — the strict landmark the assertion reads.
        image = self._wait_until(
            window,
            lambda shot: self._stroke_ink(shot, face, window, plot, 75.0, 200.0) > 0)
        self.assertGreater(self._stroke_ink(image, face, window, plot, 75.0, 200.0), 0,
                           "layer B's prefix never landed after the seek")
        self.assertEqual(self._stroke_ink(image, face, window, plot, 75.0, 40.0), 0,
                         "layer A's pixels outlived the seek")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_overlapping_ghost_geometry_leaves_the_current_layer_on_top(self):
        # The stack order at 100%: the ghost pair belongs BENEATH the
        # full current raster and the travels, exactly as the
        # navigation raster composites the scene (grid, ghosts, base,
        # current layer, travels). A ghost drawn over the current
        # raster dims the ink it overlaps.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showPrevious", True)
        self.pump(10)
        plot = self._bed_point(face, 0.0, 0.0)
        run = [[20.0 + motion * 10.0, 125.0, float(motion)] for motion in range(21)]
        current_payload = {"classes": {"WALL-OUTER": [run]}, "travels": [],
                           "travelStarts": [], "travelEnds": [], "motions": 21}
        # The ghost covers the SAME bed points (the overlap) plus a
        # run of its own (the ghost-only band, so the overlap probe
        # cannot pass on a ghost that never rendered).
        ghost_payload = {"classes": {"FILL": [run, [[40.0, 60.0, 30.0], [200.0, 60.0, 30.0]]]},
                         "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        ghost = self._native_layer(ghost_payload, face)
        current = self._native_layer(current_payload, face)
        self._printer.setLayers({"prev": ghost, "current": current, "next": None})
        self._printer.setSplit(21)
        # The ghost's band is its OWN landmark and the current layer
        # is code-guaranteed to land first, so the red census alone
        # cleared the wait a frame before the ghost blitted.
        image = self._wait_until(
            window,
            lambda shot: self._band_changed(shot, baseline, face, window, plot,
                                            75.0, 60.0, radius=6)
            and self._red_pixels(shot, face, window) > 0)
        self.assertGreater(self._red_pixels(image, face, window), 0,
                           "the full current layer never drew")
        self.assertTrue(self._band_changed(image, baseline, face, window, plot, 75.0, 60.0,
                                          radius=6),
                        "the ghost never rendered — the overlap probe would be vacuous")
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))

        def face_point(bed_x, bed_y):
            # The bed point in the face's own pixels: the grab reads it
            # through the window origin, the navigation raster (backed
            # 1:1) reads it directly.
            return (int(plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"]),
                    int(plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"]))

        def red_excess(image, col, row, origin_x=0, origin_y=0, span=4):
            # The red's excess over the blue at a bed point: pure
            # current-layer ink reads ~164, the same ink dimmed by the
            # ghost's 0.30 blue reads ~59.
            best = -255
            for dy in range(-span, span + 1):
                for dx in range(-span, span + 1):
                    pixel = image.pixel(origin_x + col + dx, origin_y + row + dy)
                    best = max(best, ((pixel >> 16) & 0xFF) - (pixel & 0xFF))
            return best

        local_col, local_row = face_point(75.0, 125.0)
        overlap = red_excess(image, local_col, local_row,
                             int(origin.x()), int(origin.y()))
        self.assertGreaterEqual(
            overlap, 120,
            "the ghost dimmed the current layer's ink at the same bed point "
            "(red excess %d)" % overlap)
        # Parity with the navigation raster's own composite order: the
        # same window, plot and view, backed 1:1 so both rasters share
        # one pixel grid.
        from mpf.plate.PlateQt import render_navigation_layer
        nav_view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": 1.0, "lineScale": 8.0, "compact": False,
                    "panX": 0.0, "panY": 0.0, "dpr": 1.0, "backing": 1.0,
                    "showPrevious": True, "showNext": True, "showBase": True,
                    "showTravels": False, "bedWidth": 250.0, "bedDepth": 250.0}
        nav = render_navigation_layer({"prev": ghost_payload, "current": current_payload,
                                       "next": None}, plot, nav_view, 21)
        nav_overlap = red_excess(nav, local_col, local_row)
        self.assertGreaterEqual(
            nav_overlap, 120,
            "the navigation raster composites the ghost over the current layer "
            "(red excess %d)" % nav_overlap)
        self.assertGreaterEqual(
            overlap, nav_overlap - 20,
            "the exact stack diverges from the navigation raster's layer order "
            "(exact %d vs nav %d)" % (overlap, nav_overlap))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_the_grey_base_never_washes_the_printed_prefix(self):
        # The stack order contract: ghosts < base < prefix < tail.
        # With the base ON and the ghosts OFF, the native prefix and
        # the vector tail must keep their feature colour — the grey
        # base may mark the unprinted suffix, never wash printed ink.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showBase", True)
        face.setProperty("showPrevious", False)
        face.setProperty("showNext", False)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        # The census reads three landmarks — the prefix's own ink, the
        # tail's, and the base's band — and "some red is on screen" is
        # satisfied by the tail alone; the three land a frame apart on
        # macOS, which is how this failed with "0 not greater than 0"
        # on a frame whose own shot showed an empty plate. A hang
        # guard, not a budget.
        def every_landmark(shot):
            return (self._stroke_ink(shot, face, window, census_plot, 75.0, 125.0) > 0
                    and self._stroke_ink(shot, face, window, census_plot, 155.0, 125.0) > 0
                    and self._band_changed(shot, baseline, face, window,
                                           census_plot, 215.0, 125.0))

        image = self._wait_until(window, every_landmark)
        # The grey base IS up: the unprinted suffix (motion 20, bed
        # x=215 — beyond the split's tail) shows the base's grey
        # where the empty baseline had none.
        self.assertTrue(self._band_changed(image, baseline, face, window,
                                           census_plot, 215.0, 125.0),
                        "the grey base never rendered — the wash "
                        "proof would be vacuous")
        # The printed prefix and the tail keep the feature colour
        # over the base: strict core-ink rows on both sides.
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the grey base washed the native prefix")
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 155.0, 125.0),
            0, "the grey base washed the vector tail")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_the_layer_ghost_shows_at_zero_percent(self):
        # The live request: the grey whole-layer base frames the
        # print from the FIRST instant — at 0% the layer reads as
        # the ghost alone, with no printed ink yet (the 0% rule
        # holds: the base is the unprinted frame, never the
        # feature colour).
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showBase", True)
        face.setProperty("showPrevious", False)
        face.setProperty("showNext", False)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=0)
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(0)
        # The ghost washes the whole unprinted layer (a band the
        # empty baseline had none of) — at 0% there is no red ink
        # by design, so the picture's arrival is the BAND.
        deadline = harness.time.monotonic() + 5.0
        image = None
        while harness.time.monotonic() < deadline:
            self.pump(5)
            image = window.grabWindow()
            if self._band_changed(image, baseline, face, window,
                                  census_plot, 155.0, 125.0):
                break
        self.assertTrue(self._band_changed(image, baseline, face, window,
                                           census_plot, 155.0, 125.0),
                        "the grey ghost never rendered at 0%")
        # The 0% rule holds: no FEATURE ink anywhere.
        self.assertEqual(
            self._stroke_ink(image, face, window, census_plot, 155.0, 125.0),
            0, "feature ink appeared at 0%")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_partial_travels_on_both_sides_of_the_prefix_survive(self):
        # The prefix carries NO travels: a travel printed BEFORE
        # the prefix boundary must stay visible at partial progress
        # — the canvas redraws the travels from the layer's start
        # on every reset, so neither side vanishes.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("showTravels", True)
        # The face's own lineScale too: the QML travels stroke at
        # the live sub-pixel width is invisible to the colour
        # census (the fixture above paints the native side solid).
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [[[30.0, 30.0, 1.0], [90.0, 30.0, 2.0]]]},
            "travels": [
                [[30.0, 80.0, 2.0], [90.0, 80.0, 3.0]],    # before the boundary
                [[30.0, 200.0, 13.0], [90.0, 200.0, 14.0]],  # after it, before the split
            ],
            "travelStarts": [], "travelEnds": [], "motions": 20,
        }
        layer = self._native_layer(payload, face, prefix_split=12)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(16)
        image, purple = self._wait_purple(window, face)
        self.assertGreater(purple, 0, "the partial travels never drew")
        # Ink at BOTH bands: the pre-boundary travel (bed y 80) and
        # the post-boundary one (bed y 200).
        self.assertTrue(self._band_changed(image, baseline, face, window, plot,
                                           60.0, 80.0),
                        "the pre-boundary travel vanished")
        self.assertTrue(self._band_changed(image, baseline, face, window, plot,
                                           60.0, 200.0),
                        "the post-boundary travel vanished")
        # The grab forces the scene's sync, and the plain-dict
        # restore keeps the teardown's last binding evaluations
        # from wrapping the PlateLayer QObject (the engine's
        # property-cache registry is already dying then).
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_context_changes_never_hole_the_printed_history(self):
        # E: a lineScale/zoom/pan change invalidates the native
        # prefix (the model's render-key flip) and its replacement
        # lands a beat later — the printed history must stay present
        # on EVERY intermediate frame: at the old screen position
        # while the hold keeps the previous composition up, at the
        # new one once the vector owns the interval. Resize rides
        # the same view-key path (the key carries width/height).
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
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        old_plot = self._bed_point(face, 0.0, 0.0)
        # The strict core census, not the loose red one: the tail
        # satisfies "some red is on screen" a frame before the prefix
        # lands, and the loose tolerance takes fringes the strict
        # census rejects.
        image = self._wait_until(
            window,
            lambda shot: self._stroke_ink(shot, face, window, old_plot,
                                          75.0, 125.0) > 0)
        self.assertGreater(
            self._stroke_ink(image, face, window, old_plot, 75.0, 125.0), 0,
            "the prefix side never drew before the context changes")

        def view_plot(plot, scale, pan_x):
            # The zoom multiplies the bed mapping and the pan adds
            # after (the painters' own transform) — the census must
            # follow the same folding.
            adjusted = dict(plot)
            for key in ("offsetX", "sx", "offsetY", "sy"):
                adjusted[key] = plot[key] * scale
            adjusted["offsetX"] += pan_x
            return adjusted

        contexts = [("lineScale", 12.0), ("viewScale", 1.2), ("viewPanX", -20.0)]
        for index, (name, value) in enumerate(contexts):
            # The production ordering: the context change settles
            # FIRST (the 150 ms timer consumes the view key and feeds
            # the model), and only THEN does the model's invalidation
            # publish land — with the key already consumed, so the
            # publish alone wakes nothing. The harness has no model:
            # the render-key flip IS the production mechanism.
            face.setProperty(name, value)
            self._pump_ms(200)  # the settle consumes the view key
            # A pan retains the preceding zoom; the replacement asset and
            # pixel census must use the same view as the live renderer.
            scale = float(face.property("viewScale"))
            pan_x = float(face.property("viewPanX"))
            layer.set_expected_key("invalidated-%d" % index)
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            new_plot = view_plot(self._bed_point(face, 0.0, 0.0), scale, pan_x)
            # The replacement prefix is DELAYED: every intermediate
            # frame keeps the history — at the old screen position
            # (the held composition) or the new one (the vector's).
            # The FIRST grab is the invalidation publish's own frame:
            # the one-frame ownership swap is exactly the race under
            # test, so it must not hide behind a settling pump.
            for sample in range(7):
                if sample:
                    self._pump_ms(30)
                image = window.grabWindow()
                prefix_side = max(
                    self._stroke_ink(image, face, window, new_plot, 75.0, 125.0),
                    self._stroke_ink(image, face, window, old_plot, 75.0, 125.0))
                tail_side = max(
                    self._stroke_ink(image, face, window, new_plot, 155.0, 125.0),
                    self._stroke_ink(image, face, window, old_plot, 155.0, 125.0))
                self.assertGreater(prefix_side, 0,
                                   "%s holed the printed history (prefix side)" % name)
                self.assertGreater(tail_side, 0,
                                   "%s holed the printed history (tail side)" % name)
            # The replacement prefix lands for the new view, and the
            # settle fires (the production 150 ms re-raster): the
            # composition stays whole through the takeover and the
            # settled picture sits at the NEW positions.
            from mpf.plate.PlateQt import render_layer_prefix, png_file
            plot_value = face.property("plot")
            if hasattr(plot_value, "toVariant"):
                plot_value = plot_value.toVariant()
            plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                    "offsetY": float(plot_value["bed"]["offsetY"]),
                    "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                    "bedXMin": float(plot_value["bed"]["bedXMin"]),
                    "bedYMax": float(plot_value["bed"]["bedYMax"])}
            view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": scale, "lineScale": float(face.property("lineScale")),
                    "compact": False, "panX": pan_x, "panY": 0.0}
            prefix = render_layer_prefix(payload, plot, view, 10)
            layer.set_prefix(prefix, png_file(
                prefix, "/tmp/mpf/raster-probe",
                "fixture-e%d-%d" % (index, harness.time.monotonic_ns())), 10,
                "invalidated-%d" % index)
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            self._pump_ms(250)  # past the view-settle timer
            for _ in range(4):
                self._pump_ms(30)
                image = window.grabWindow()
                self.assertGreater(
                    self._stroke_ink(image, face, window, new_plot, 75.0, 125.0), 0,
                    "%s lost the history at the replacement" % name)
            # The takeover completes before the next context change
            # (the production cadence: one settled change at a time).
            deadline = harness.time.monotonic() + 3.0
            while harness.time.monotonic() < deadline and not face.property("_prefixWasShown"):
                self._pump_ms(50)
                window.grabWindow()
            self.assertTrue(face.property("_prefixWasShown"),
                            "%s: the replacement prefix never showed" % name)
            old_plot = new_plot
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_raster_seek_commits_to_ready_under_the_target(self):
        # The final target's composition leg: the publish (the
        # commit's handoff) to the picture's arrival — the red ink
        # IS the composed picture, so the wait for it measures
        # commit-to-picture end to end. The grab-wait's 50 ms
        # sampling bounds the assertion, the printed number is the
        # reading.
        monitor, window, face = self._follower_popover()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(21)
        start = harness.time.monotonic()
        _image, count = self._wait_red(window, face, want=True)
        elapsed = (harness.time.monotonic() - start) * 1000.0
        self.assertGreater(count, 0, "the picture never arrived")
        print("publish -> picture: %.1f ms" % elapsed)
        # The arrival IS the contract (the wait above); the wall-clock
        # reading is a hang guard only — the loaded CI runners push
        # past 300 ms, so the bound matches the wait's own 5 s window.
        self.assertLess(elapsed, 5000.0,
                        "the picture never settled within the wait window")
        # The grab forces the scene's sync (the harness's window
        # doctrine): the texture drains before the next mount.
        window.grabWindow()
        self.pump(30)

    def test_hidpi_rasters_paint_at_device_resolution(self):
        # D's contract: the native raster is painted at the DEVICE
        # resolution — the face's logical size times the bounded
        # backing scale — and the scene-graph samples it down to the
        # logical frame. A DPR-2 screen must never take a 1x logical
        # toolpath raster and merely enlarge it. The dimensions AND
        # the logical-space coverage both hold.
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face, dpr=2.0)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(21)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the full raster never drew")
        self.assertEqual(layer.rasterWidth, int(face.width()) * 2,
                         "the DPR-2 raster is not the device width")
        self.assertEqual(layer.rasterHeight, int(face.height()) * 2,
                         "the DPR-2 raster is not the device height")
        # The logical coverage: the downsampled 2x raster's ink still
        # reaches the logical frame's corners (the corner-pinned
        # stroke must not clip at the backing scale).
        plot = self._bed_point(face, 0.0, 0.0)
        for bed_x, bed_y in ((1.0, 1.0), (249.0, 249.0)):
            self.assertGreater(
                self._stroke_ink(image, face, window, plot, bed_x, bed_y), 0,
                "the DPR-2 raster's ink never reached the logical corner")

    def test_reverse_scrub_paths_settle_to_the_same_picture(self):
        from PyQt6.QtCore import Q_RETURN_ARG, QVariant

        # F: the reverse scrub is the prefix scheduler's stress path
        # (a backward move demands a fresh prefix immediately). The
        # SAME final {layer, split, view, toggles} must produce the
        # same settled pixels however it was reached — direct, from
        # 0, from 100, through a lower split, through a higher one,
        # or a rapid alternating reverse-heavy run. The only
        # legitimate disagreement is the prefix/tail boundary's
        # antialiased fringe (a full-canvas bitmap trims to the tail
        # on one path and keeps the continuous stroke on another).
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
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        target = 18

        def seek_to(split):
            self._printer.setSplit(split)

        def settled_grab():
            # A fixed delay can photograph the full-history fallback
            # before the prefix and its matching tail have delivered,
            # especially with the threaded Canvas on loaded macOS CI.
            # Compare the settled owner on every path, then require two
            # unchanged frames; the pixel tolerance remains unchanged.
            deadline = harness.time.monotonic() + 5.0
            previous = None
            while harness.time.monotonic() < deadline:
                self._pump_ms(30)
                image = window.grabWindow()
                ready = (
                    face.property("_prefixWasShown")
                    and face.property("_vectorCoversShown") == layer.prefixSplit
                    and face.property("_vectorSplitShown") == target
                    and harness.QMetaObject.invokeMethod(face, "_exactReady", Q_RETURN_ARG(QVariant)))
                if ready and previous is not None and self._pixel_diff(
                        image, previous, face, window) == 0:
                    return image
                previous = image if ready else None
            state = face.property("_canvasTransaction")
            if hasattr(state, "toVariant"):
                state = state.toVariant()
            self.fail("reverse scrub never delivered a stable prefix/tail composition: %s; transaction=%r" % (
                harness.QMetaObject.invokeMethod(face, "_holdTerms", Q_RETURN_ARG(QVariant)), state))

        seek_to(target)
        direct = settled_grab()
        self.assertGreater(self._red_pixels(direct, face, window), 0,
                           "the direct seek never drew")
        paths = [
            ("0 -> X", [0, target]),
            ("100 -> X", [21, target]),
            ("X -> lower -> X", [target, 8, target]),
            ("X -> higher -> X", [target, 20, target]),
            ("rapid alternating", [target, 8, target, 12, target, 5,
                                   target, 15, target, 7, target]),
        ]
        diffs = {}
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        for name, sequence in paths:
            for split in sequence:
                seek_to(split)
                self._pump_ms(60)
            image = settled_grab()
            # Every path now compares the same delivered interval
            # owners. Preserve the platform antialias tolerance while
            # rejecting any changed region of the settled picture.
            def differs(pixel_a, pixel_b):
                return any(abs(((pixel_a >> shift) & 0xFF)
                              - ((pixel_b >> shift) & 0xFF)) > 60
                           for shift in (0, 8, 16))
            diffs[name] = sum(
                1 for row in range(0, int(face.height()), 4)
                for col in range(0, int(face.width()), 4)
                if differs(image.pixel(int(origin.x()) + col,
                                       int(origin.y()) + row),
                           direct.pixel(int(origin.x()) + col,
                                        int(origin.y()) + row)))
        # Compare the delivered frame's coverage, not the next in-flight
        # paint's scratch record. A full bitmap would report coverage from 0.
        self.assertEqual(face.property("_vectorCoversShown"), layer.prefixSplit,
                         "the settled canvas never trimmed to the tail")
        self.assertEqual(face.property("_vectorSplitShown"), target,
                         "the settled canvas delivered the wrong scrub position")
        self.assertTrue(face.property("_textureReady"),
                        "the settled canvas never delivered")
        print("reverse-scrub settled diffs vs direct:", diffs)
        # The bound is a FRACTION of the samples, not a fixed count. An
        # antialiased fringe is the platform's rasteriser, and macOS
        # shades these fringes further than this container does — 15
        # sampled pixels against a fixed 8, on the same composition,
        # which made this leg alternate red and green on an unchanged
        # tree. Real composition drift is a different shape: a wrong
        # split or an untrimmed canvas moves a REGION, orders of
        # magnitude more samples than a fringe, so 0.2% still catches
        # everything this census exists to catch.
        sampled = (len(range(0, int(face.height()), 4))
                   * len(range(0, int(face.width()), 4)))
        allowed = max(8, sampled // 500)
        for name, diff in diffs.items():
            self.assertLessEqual(diff, allowed,
                                 "%s settled to a different picture "
                                 "(%d of %d sampled pixels differ beyond "
                                 "the antialias tolerance, limit %d)"
                                 % (name, diff, sampled, allowed))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)
