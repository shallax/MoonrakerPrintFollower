"""Executable qml plate zoom contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_a_cached_zoom_bake_never_stands_over_the_full_layer(self):
        # The stale-zoom report: a prefix baked at another zoom is
        # invalid for the current view key, and at a FULL layer the
        # model demands no replacement — the hold that kept the old
        # picture during a partial repaint could arm there and never
        # release, leaving the oversized bake standing over the
        # valid full raster. The hold is partial-only now: the full
        # layer must show exactly the full raster's own band.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(21, 10.0)
        plot = self._bed_plot(face)
        # A 2x bake: the prefix rendered at scale 2 (a previous
        # zoom's cached asset).
        from plugins.PlateQt import PlateLayer, render_layer_prefix, png_file
        view2x = {"width": int(face.width()), "height": int(face.height()),
                  "scale": 2.0, "lineScale": 8.0, "compact": False,
                  "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        layer = PlateLayer(payload)
        prefix2x = render_layer_prefix(payload, plot, view2x, 10)
        layer.set_prefix(prefix2x, png_file(
            prefix2x, "/tmp/mpf/raster-probe", "zoom-bake-%d" % harness.time.monotonic_ns()),
            10, "zoom-key")
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the partial prefix never drew")
        # The view key changes (the zoom back to 100%): the bake is
        # invalid, and the split reaches the FULL layer — no hold may
        # stand the stale bake over the full picture.
        layer.setProperty("prefixValid", False)
        self._printer.setSplit(21)
        self._pump_ms(300)
        for _ in range(20):
            self._pump_ms(30)
            window.grabWindow()
            # The full raster owns the whole picture; the 2x bake's
            # doubled-height band must never appear above it. The
            # frame is judged complete when the prefix is gone and
            # the full raster's own band stands.
            if face.property("_prefixHold") is False and not face.property("_prefixWasShown"):
                break
        grab = window.grabWindow()
        self.assertFalse(face.property("_prefixHold"),
                         "the full layer armed the stale bake's hold")
        self.assertFalse(face.property("_prefixWasShown"),
                         "the stale bake's shown record survived the full layer")
        # The 2x bake draws its stroke at DOUBLE the vertical offset:
        # the full picture must carry exactly ONE red band, never the
        # bake's second band below it.
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"]
                  + (20.0 + 5 * 10.0 - plot["bedXMin"]) * plot["sx"])
        bands = 0
        in_band = False
        for r in range(int(origin.y()), int(origin.y() + face.height())):
            red = self._matches(grab.pixel(col, r), (0xD3, 0x2F, 0x2F))
            if red and not in_band:
                bands += 1
            in_band = red
        self.assertEqual(bands, 1,
                         "the picture carries %d bands — the 2x bake "
                         "stands over the full layer" % bands)

    def test_the_production_scheduler_never_drops_history_during_a_forward_scrub(self):
        # The live-report test: the REAL model's scheduler renders the
        # prefixes (no hand-fed replacement) while the split advances
        # through a dense layer — every intermediate frame must keep
        # the committed printed history's interior ink.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(motions=120000, dx=0.0002)
        plot = self._bed_plot(face)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": payload, "next": None})
        self._printer.setSplit(30000)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial partial never drew")
        # A fast forward scrub: ten steps of 9000 motions, one frame
        # per step — the printed interior behind the boundary must
        # never vanish (the gap report), and the picture must settle
        # at the final boundary.
        last_split = 30000
        for step in range(1, 11):
            last_split = 30000 + step * 9000
            self._printer.setSplit(last_split)
            for _ in range(4):
                self._pump_ms(30)
                grab = window.grabWindow()
                self.assertTrue(
                    self._red_in_band(grab, face, window, plot,
                                      20.0 + 15000 * 0.0002, 125.0),
                    "step %d dropped the committed printed history" % step)
        # The final position converges without further input.
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            self._pump_ms(50)
            grab = window.grabWindow()
            if self._red_in_band(grab, face, window, plot,
                                 20.0 + (last_split - 5000) * 0.0002, 125.0):
                break
        else:
            self.fail("the final requested position never converged")

    def test_a_zoom_then_forward_scrub_never_resurrects_the_old_scale_history(self):
        # The out-of-scale ghost: the incremental render seeds its
        # copy from the previous picture only under the SAME render
        # key. A zoom between the commit and the refresh must fall
        # back to the full walk — the stale half would otherwise
        # stay at the old scale while the new half strokes the new
        # one, one print drawn twice, two sizes.
        #
        # The probe is POSITIONAL and per frame: the strict-colour
        # ink's column SPAN in the wall's own row band, sampled
        # through the whole handover. It counted strict-colour ROWS
        # at bed x=35 before, and that probe could not see the defect
        # at all: the wheel zoom is anchored at the face's CENTRE, so
        # it holds bed y=125 — the row the wall occupies — fixed,
        # which leaves the column invariant under the gesture the
        # test performs, and a row count at a fixed column moves only
        # with the PEN's device width (the parity policy scales the
        # stroke with the view: 2 core rows at scale 1.0, 4 at 1.25)
        # against a tolerance of 3. It passed on the stale mapping
        # and failed on the committed one. Do not re-point this back
        # at a row count on a fixed bed point.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(motions=2000, dx=0.05)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": payload, "next": None})
        self._printer.setSplit(1000)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial partial never drew")
        # Zoom in at the face's centre: the interaction settles and
        # the view commits at a new render key.
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)
        scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
        event = QWheelEvent(
            QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the zoom gesture never settled")
        self.assertGreater(face.property("viewScale"), 1.0,
                           "the zoom never moved the view")
        # The forward scrub past the refresh threshold forces the
        # prefix job to re-run under the new view. Any frame that
        # resurrects ink at the OLD scale's bed position is the
        # ghost — the settled picture must keep the history only at
        # the new view's mapping.
        self._printer.setSplit(1150)
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        row = int(origin.y() + plot["offsetY"]
                  + (plot["bedYMax"] - 125.0) * plot["sy"])

        def span(image):
            # The strict-colour ink's column range along the wall's
            # own row band, in face-local pixels.
            cols = [col - int(origin.x())
                    for col in range(int(origin.x()),
                                     int(origin.x()) + int(face.width()))
                    if any(self._matches(image.pixel(col, py),
                                         (0xD3, 0x2F, 0x2F), tolerance=20)
                           for py in range(row - 3, row + 4))]
            return (cols[0], cols[-1]) if cols else None

        def end_col(bed_x, scale, pan_x):
            return ((plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
                    * scale + pan_x)

        def current_view():
            value = face.property("_view")
            return value.toVariant() if hasattr(value, "toVariant") else value

        def delivered():
            # The face's own record that the canvas delivered THIS
            # demand's paint: until it lands, the scene still stands the
            # previous serving (the composition transaction's own
            # design), and a loaded host's grabs land inside that
            # window. Those frames are the old picture held while the
            # replacement decodes, never the ghost — the ghost is a
            # DELIVERED composition carrying the old scale's history.
            return (bool(face.property("_textureReady"))
                    and face.property("_lastSplit") == 1150)

        worst_end = None
        leftmost = None
        judged = 0
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            self._pump_ms(50)
            image = window.grabWindow()
            # Read AFTER the grab, as the records above are: a delivery
            # that landed during the grab is what the grab rendered.
            if not delivered():
                continue
            judged += 1
            current = span(image)
            if current is None:
                continue
            worst_end = current[1] if worst_end is None else max(worst_end, current[1])
            leftmost = current[0] if leftmost is None else min(leftmost, current[0])
        # The non-vacuity control: the handover WAS sampled at the
        # committed view. A census that only ever read the held frames
        # would clear the mapping below by never having looked at the
        # picture it is about.
        self.assertGreater(
            judged, 0,
            "no delivered composition was ever sampled: the ghost cannot "
            "be judged against a picture the canvas never delivered")
        self.assertIsNotNone(worst_end, "the zoomed picture never drew")
        view = current_view()
        committed_end = end_col(77.5, view["scale"], view["panX"])
        committed_head = end_col(20.0, view["scale"], view["panX"])
        # The re-render's own arrival: the printed boundary is at bed
        # 77.5 under the committed view, and no frame may end short
        # of it (that would be a lost tail, not a ghost).
        self.assertGreaterEqual(
            float(worst_end), committed_end - 4.0,
            "no delivered frame re-rendered the history at the "
            "committed view (the furthest ink ends at %s, the committed "
            "view ends at %.1f): the zoomed prefix never re-rendered"
            % (worst_end, committed_end))
        # The ghost, in both directions. The picture's HEAD is the
        # sharpest witness: the committed view maps the printed
        # history's start (bed 20) far to the LEFT of where the old
        # view had it, so a frame still carrying the old-scale
        # picture starts tens of pixels right of the committed head.
        self.assertLessEqual(
            float(leftmost), committed_head + 4.0,
            "a delivered frame still starts at the OLD view's "
            "mapping (the leftmost ink is at %s, the committed view "
            "starts at %.1f): the out-of-scale ghost"
            % (leftmost, committed_head))
        self.assertLessEqual(
            float(worst_end), committed_end + 4.0,
            "a delivered frame still ends at the OLD view's "
            "mapping (the furthest ink is at %s, the committed view "
            "ends at %.1f): the out-of-scale ghost"
            % (worst_end, committed_end))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_zoom_never_leaves_the_history_at_the_old_views_mapping(self):
        # The out-of-scale ghost's own measurement, positional. The
        # sibling test counts strict-colour ROWS in a band at bed
        # y=125 — the row the wheel zoom holds fixed, since that bed
        # row maps to the face's centre — so its count moves only with
        # the PEN's device width, which the parity policy scales with
        # the view. The printed line's right END does not sit still: it
        # lands at 20 + split*dx on the bed and the committed view maps
        # it to a column per scale. The settled picture must end there,
        # with nothing of the old view standing beyond it.
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(motions=2000, dx=0.05)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": payload, "next": None})
        self._printer.setSplit(1000)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial partial never drew")
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        row = int(origin.y() + plot["offsetY"]
                  + (plot["bedYMax"] - 125.0) * plot["sy"])

        def span(image):
            # The strict-colour ink's column range along the wall's
            # own row band, in face-local pixels.
            cols = [col - int(origin.x())
                    for col in range(int(origin.x()),
                                     int(origin.x()) + int(face.width()))
                    if any(self._matches(image.pixel(col, py),
                                         (0xD3, 0x2F, 0x2F), tolerance=20)
                           for py in range(row - 3, row + 4))]
            return (cols[0], cols[-1]) if cols else None

        def end_col(bed_x, scale, pan_x):
            return ((plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
                    * scale + pan_x)

        def view():
            # The live view the canvas walks with (a JS object; PyQt
            # hands it back as a QJSValue on some builds).
            value = face.property("_view")
            return value.toVariant() if hasattr(value, "toVariant") else value

        before = span(image)
        self.assertIsNotNone(before, "the printed wall never drew")
        self.assertAlmostEqual(
            float(before[1]), end_col(70.0, view()["scale"], view()["panX"]),
            delta=4.0, msg="the unzoomed history does not end where it was asked to")
        # Zoom in at the face's centre, the sibling test's own gesture.
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)
        scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
        event = QWheelEvent(
            QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)
        deadline = harness.time.monotonic() + 3.0
        while harness.time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the zoom gesture never settled")
        self.assertGreater(face.property("viewScale"), 1.0,
                           "the zoom never moved the view")
        # The forward scrub past the refresh threshold re-renders the
        # history under the committed view.
        self._printer.setSplit(1150)
        settled = None
        stable = 0
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            self._pump_ms(50)
            grab = window.grabWindow()
            current = span(grab)
            stable = stable + 1 if current == settled else 0
            settled = current
            if stable >= 3:
                break
        self.assertIsNotNone(settled, "the zoomed wall never drew")
        expected = end_col(77.5, view()["scale"], view()["panX"])
        self.assertEqual(
            span(window.grabWindow()), settled,
            "the picture was still moving at the assertion")
        self.assertLessEqual(
            float(settled[1]), expected + 4.0,
            "the history still ends at the OLD view's mapping (ends at %s, "
            "the committed view ends at %.1f): the out-of-scale ghost"
            % (settled[1], expected))
        self.assertGreaterEqual(
            float(settled[1]), expected - 4.0,
            "the history ends short of the committed view's mapping (ends at "
            "%s, the committed view ends at %.1f): the re-render lost ink"
            % (settled[1], expected))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_prefix_boundary_advance_never_exposes_a_gap(self):
        # The review's composition-transaction repro: a prefix
        # refresh lands at a new boundary while the canvas still
        # covers the OLD one — the readiness gate holds the standing
        # composition (the retained picture plus the canvas bitmap)
        # until the replacement's joint delivery, and the printed
        # history must never lose ink in any frame of the handover.
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
        from plugins.PlateQt import render_layer_prefix, png_file
        layer = self._native_layer(payload, face)
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
                "panX": 0.0, "panY": 0.0}
        prefix_10 = render_layer_prefix(payload, plot, view, 10)
        prefix_15 = render_layer_prefix(payload, plot, view, 15)
        url_10 = png_file(prefix_10, "/tmp/mpf/raster-probe",
                          "fixture-txn-a-%d" % harness.time.monotonic_ns())
        url_15 = png_file(prefix_15, "/tmp/mpf/raster-probe",
                          "fixture-txn-b-%d" % harness.time.monotonic_ns())
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        # The model's view publishes race the hand-fed claim (a
        # publish overwrites the wrapper's expected key), so the
        # claim is re-asserted until the composition settles with
        # the prefix OWNING [0, 10) and the canvas covering
        # [10, 18).
        deadline = harness.time.monotonic() + 5.0
        settled = False
        image = None
        while harness.time.monotonic() < deadline:
            layer.set_prefix(prefix_10, url_10, 10, "fixture-key")
            layer.set_expected_key("fixture-key")
            self.pump(5)
            image = window.grabWindow()
            if face.property("_vectorCoversFrom") == 10 \
                    and self._stroke_ink(image, face, window, census_plot,
                                         75.0, 125.0) > 0:
                settled = True
                break
        self.assertTrue(settled, "the settled composition never formed")
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the settled prefix never drew its history")
        # Delivery notifications with no intervening paint do not
        # replace pixels. They must preserve the standing receipt.
        for _ in range(100):
            transaction = face.property("_canvasTransaction").toVariant()
            standing = face.property("_deliveredComposition").toVariant()
            if transaction["count"] == 0 and standing["from"] == 10:
                break
            self._pump_ms(20)
            window.grabWindow()
        self.assertEqual(face.property("_canvasTransaction").toVariant()["count"], 0)
        receipt = face.property("_deliveredComposition").toVariant()
        harness.QMetaObject.invokeMethod(face, "_deliverProgressPaint")
        harness.QMetaObject.invokeMethod(face, "_deliverProgressPaint")
        self.assertEqual(face.property("_deliveredComposition").toVariant(), receipt)
        # The boundary advance: the replacement lands while the
        # canvas still covers [10, 18). Every frame of the handover
        # must keep the interior ink — the standing composition is
        # never torn down before the joint swap.
        layer.set_prefix(prefix_15, url_15, 15, "fixture-key")
        deadline = harness.time.monotonic() + 8.0
        swapped = False
        while harness.time.monotonic() < deadline:
            # A late view publish (the previous test's settle) may
            # still overwrite the claim — re-assert it through the
            # handover so the transition completes.
            layer.set_expected_key("fixture-key")
            # One pump per frame: the gap this census exists to catch
            # is one evaluation wide (the source swap blanking the
            # live prefix), so a coarser cadence samples straight past
            # it. Every beat of the handover is examined.
            self.pump(1)
            grab = self._handover_frame(window)
            # Ownership first, ink second: the ownership invariant is
            # deterministic on every host, while the pixels a wrong
            # owner leaves depend on whether the canvas's texture
            # happened to commit before the grab (this container's
            # cadence often sampled the good frame and passed).
            #
            # A delivered full-history Canvas is a complete owner too;
            # it must exclude the prefix, but never the pixel assertion.
            canvas_owner = bool(face.property("_textureReady")) \
                and face.property("_vectorCoversShown") == 0
            if not canvas_owner:
                record_owner, live_owner = self._prefix_stack_owners(face)
                self.assertTrue(
                    record_owner or live_owner,
                    "no prefix image owned this beat of the advance — "
                    "record %s, live %s — the interior was left to the "
                    "canvas's delivered bitmap alone (covers %s/%s, "
                    "textureReady %s, shown %s, statusReady %s, retained "
                    "source %r)" % (
                        record_owner, live_owner,
                        face.property("_vectorCoversFrom"),
                        face.property("_vectorCoversShown"),
                        face.property("_textureReady"),
                        face.property("_prefixWasShown"),
                        face.property("_prefixStatusReady"),
                        face.property("_retainedPrefixSource")))
            self.assertGreater(
                self._stroke_ink(grab, face, window, census_plot,
                                 75.0, 125.0),
                0, "a frame lost the printed history mid-transition")
            if face.property("_vectorCoversFrom") == 15 \
                    and self._stroke_ink(grab, face, window, census_plot,
                                         115.0, 125.0) > 0:
                swapped = True
                break
        self.assertTrue(swapped,
                        "the boundary advance never reached the joint "
                        "composition — covers %s, prefixSplit %s, "
                        "prefixValid %s" % (
                            face.property("_vectorCoversFrom"),
                            layer.prefixSplit, layer.prefixValid))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_inert_gestures_never_flip_the_warm_raster(self):
        # The inertness ruling: the warm raster enters ONLY when a
        # movement actually pans. A click at any zoom, a drag attempt
        # at 100% and a boundary-blocked drag never activate it; a
        # genuine drag, the wheel and the release settle keep their
        # existing behaviour.
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
        from plugins.PlateQt import render_navigation_layer, png_file
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
            nav, "/tmp/mpf/raster-probe", "nav-inert-%d" % harness.time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()

        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

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

        # 1 + 2: at the 100% fit a click and a drag attempt do
        # nothing — no warm raster, no pan (the wheel still zooms).
        self.assertFalse(face.property("_interactionActive"))
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx, cy, Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a click at 100% activated the warm raster")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 30, cy + 15, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 30, cy + 15,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a drag attempt at 100% activated the warm raster")
        self.assertEqual(face.property("displayPanX"), 0.0,
                         "a 100% drag moved the camera")

        # 6: the wheel from the 100% fit still enters the warm
        # raster, and the ease settles back to the exact scene.
        wheel(120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the wheel never entered the interaction")
        settle()
        self.assertGreater(face.property("viewScale"), 1.0,
                           "the wheel never zoomed past the fit")

        # 3: at zoom, a click still does not activate — the entry
        # waits for an actual movement.
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx, cy, Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a click at zoom activated the warm raster")

        # 4: a genuine drag enters and pans normally.
        pan_before = face.property("displayPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 40, cy + 20, Qt.MouseButton.LeftButton)
        self._pump_ms(20)
        self.assertTrue(face.property("_interactionActive"),
                        "a genuine drag never entered the interaction")
        mouse(QEvent.Type.MouseButtonRelease, cx + 40, cy + 20,
              Qt.MouseButton.NoButton)
        self._pump_ms(20)
        self.assertAlmostEqual(face.property("displayPanX"), pan_before + 40.0,
                               delta=1.5, msg="the drag pan lagged the pointer")
        # 7: the release keeps the settle behaviour: the exit drive
        # returns the exact scene.
        settle()

        # 5: a drag blocked by the soft clamp applies zero pan and
        # must not activate the raster. Push the camera to the
        # boundary first — the press grabs at the face's right edge
        # and the move sweeps to the window's left, which the clamp
        # binds (the harness drops moves sent outside the window, so
        # the pointer stays in bounds). The clamp binds each move's
        # own delta, so that first sweep lands short of the bound and
        # sweeps from inside the face drive the pan onto it (the
        # corner-pan leg's idiom below). The sweep count is a
        # MEASUREMENT, not the CI stack's two: the soft clamp's bound
        # follows the face's own width, so a box whose panes lay out a
        # little differently needs more sweeps to reach it. A fresh
        # drag attempt against the bound stays inert.
        mouse(QEvent.Type.MouseButtonPress, int(face.width()) - 10, cy,
              Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, 5, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, 5, cy, Qt.MouseButton.NoButton)
        settle()
        bound_pan = face.property("viewPanX")
        for _sweep in range(10):
            mouse(QEvent.Type.MouseButtonPress, cx, cy,
                  Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx - 200, cy,
                  Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx - 200, cy,
                  Qt.MouseButton.NoButton)
            settle()
            moved_to = face.property("viewPanX")
            if moved_to == bound_pan:
                # A sweep that changed nothing is the bound; the pin
                # below is about the drag that follows it.
                break
            bound_pan = moved_to
        else:
            self.fail("the pan never reached its soft clamp in ten sweeps")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx - 200, cy, Qt.MouseButton.LeftButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a boundary-blocked drag activated the warm raster")
        mouse(QEvent.Type.MouseButtonRelease, cx - 200, cy,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertEqual(face.property("viewPanX"), bound_pan,
                         "the blocked drag moved the camera")
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)


