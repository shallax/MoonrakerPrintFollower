"""Executable qml plate prefix refresh contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_every_plate_raster_with_a_png_source_loads_asynchronously(self):
        """The plate's rasters arrive as file:// PNGs, so every
        published one is a decode on whichever thread holds the bind.
        Measured at the transport's own sizes (the 4x interaction
        raster, 2252x2324): 66 ms to decode, on top of the ~225 ms
        the worker spends ENCODING the same picture and the 5-25 ms
        the render itself costs. The decode is the one part of that
        handover the face owns, and an Image that is not asynchronous
        decodes it inline — a stall the size of a frame, once per
        bake, in the middle of a live print.

        The pin reads the MOUNTED face, so what it checks is the
        images actually carrying rasters, not a property name in the
        file: every item whose own source is one of those files must
        have handed its decode to the image thread. The set is the
        plate's own published PNGs — the theme's SVG icons carry a
        file:// source too and are not this transport.
        """
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showPrevious", True)
        face.setProperty("showNext", True)
        self.pump(10)
        payload = self._stroke_payload()
        ghost = self._native_layer(payload, face, prefix_split=100)
        layer = self._native_layer(payload, face, prefix_split=100)
        _nav, nav_url = self._nav_raster_for(payload, face, 120, "decode-async")
        self._printer.setScrub(payload)
        self._printer.setNavigation(nav_url)
        self._printer.setLayers({"prev": ghost, "current": layer, "next": ghost})
        self._printer.setSplit(120)
        deadline = harness.time.monotonic() + 5.0
        sourced = []
        while harness.time.monotonic() < deadline:
            self._pump_ms(50)
            sourced = [item for item in face.findChildren(harness.QQuickItem)
                       if "/raster-probe/" in str(item.property("source") or "")]
            if len(sourced) >= 5:
                break
        self.assertGreaterEqual(
            len(sourced), 5,
            "the face never bound its plate rasters: %r"
            % [str(item.property("source") or "") for item in
               face.findChildren(harness.QQuickItem)])
        for item in sourced:
            self.assertTrue(
                item.property("asynchronous"),
                "%s loads %s on the GUI thread"
                % (item.metaObject().className(), item.property("source")))

    def test_a_navigation_raster_load_never_decodes_on_the_gui_thread(self):
        """The 4x raster's decode runs off the GUI thread — pinned as
        the Loading state itself.

        A synchronous Image is Ready on the turn its source binds:
        there is nothing for the engine to do but decode inline, so
        no Loading state is ever observable. An asynchronous one
        shows Loading until its own thread finishes. The observation
        is taken through QML (the face's own comparison), so it is
        the state the gates see, not a proxy.
        """
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        _nav, nav_url = self._nav_raster_for(payload, face, 120, "decode-window")
        stem = nav_url.rsplit("/", 1)[-1]
        probe = self._status_probe()
        self._printer.setNavigation(nav_url)
        image = None
        seen = []
        deadline = harness.time.monotonic() + 10.0
        while harness.time.monotonic() < deadline:
            self.pump(1)
            if image is None:
                for item in face.findChildren(harness.QQuickItem):
                    if stem in str(item.property("source") or ""):
                        image = item
                        break
                if image is None:
                    continue
            seen.append(probe.statusOf(image))
            if seen[-1] == 1:  # Image.Ready
                break
        self.assertIsNotNone(image, "the interaction raster never bound")
        self.assertIn(2, seen,  # Image.Loading
                      "the raster was never seen loading: %r" % (seen,))
        self.assertEqual(seen[-1], 1, "the raster never became ready: %r" % (seen,))

    def test_a_forward_scrub_through_prefix_refreshes_never_drops_the_history(self):
        # The critical's core: a refresh renders AT the requested
        # split (the equality edge) while the previous prefix is
        # visible. Every frame through the handover must keep the
        # printed history's interior ink — the extrusion paths never
        # disappear while the replacement's image lands and the
        # canvas repaints the tail.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial prefix never drew")
        # The refresh: the new prefix AT the demand arrives while the
        # old one is visible.
        prefix, url = self._prefix_for(payload, face, 160, "dense-handover")
        layer.set_prefix(prefix, url, 160, "fixture-key")
        self._printer.setSplit(160)
        deadline = harness.time.monotonic() + 3.0
        frames = 0
        interior = 20.0 + 90 * 0.6
        tail = 20.0 + 150 * 0.6
        while harness.time.monotonic() < deadline:
            grab = window.grabWindow()
            self.assertTrue(self._red_in_band(grab, face, window, plot,
                                              interior, 125.0),
                            "the printed history dropped mid-handover")
            frames += 1
            if self._red_in_band(grab, face, window, plot, tail, 125.0):
                break
            self._pump_ms(20)
        self.assertGreater(frames, 0, "the handover never delivered")
        self.assertTrue(self._red_in_band(grab, face, window, plot,
                                          tail, 125.0),
                        "the new prefix's own interior never arrived")

    def test_repeated_forward_refreshes_keep_every_frame_complete(self):
        # Three successive refreshes (100 -> 160 -> 240): each
        # replacement lands while the previous picture stands, and
        # no frame ever loses the committed history.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        self._wait_red(window, face, want=True)
        previous_split = 120
        for target in (160, 240):
            prefix, url = self._prefix_for(payload, face, target, "dense-multi")
            layer.set_prefix(prefix, url, target, "fixture-key")
            self._printer.setSplit(target)
            deadline = harness.time.monotonic() + 3.0
            interior = 20.0 + (target - 5) * 0.6
            while harness.time.monotonic() < deadline:
                grab = window.grabWindow()
                # The PREVIOUS committed history (well inside the old
                # boundary) must stand on every frame.
                self.assertTrue(self._red_in_band(grab, face, window, plot,
                                                  20.0 + (previous_split - 20) * 0.6,
                                                  125.0),
                                "a frame lost the committed history at %d" % target)
                if face.property("_vectorSplitShown") == target and self._red_in_band(
                        grab, face, window, plot, interior, 125.0):
                    break
                self._pump_ms(20)
            self._pump_ms(60)
            self.assertEqual(face.property("_vectorSplitShown"), target,
                             "the refresh never delivered the requested interval")
            previous_split = target

        self.assertEqual([message for message in harness._APPLICATION["messages"][self._message_start:]
                          if "Binding loop" in message], [],
                         "composition ownership formed a QML binding cycle")

    def test_reverse_checkpoint_handover_never_presents_less_than_the_requested_split(self):
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("attached", False)
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face, prefix_split=200)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(220)
        deadline = harness.time.monotonic() + 3
        while face.property("_vectorSplitShown") != 220 and harness.time.monotonic() < deadline:
            self._pump_ms(20)
        self.assertEqual(face.property("_vectorSplitShown"), 220)
        prefix, url = self._prefix_for(payload, face, 150, "reverse-checkpoint")
        layer.set_prefix(prefix, url, 150, "fixture-key")
        self._printer.setSplit(160)
        deadline = harness.time.monotonic() + 3
        while harness.time.monotonic() < deadline:
            image = window.grabWindow()
            self.assertTrue(self._red_in_band(image, face, window, plot,
                                              20 + 155 * 0.6, 125),
                            "a checkpoint presented below the requested progress")
            if face.property("_vectorSplitShown") == 160:
                break
            self._pump_ms(10)
        self.assertEqual(face.property("_vectorSplitShown"), 160)

    def test_pending_native_prefix_holds_the_frame_without_a_full_history_qml_walk(self):
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        deadline = harness.time.monotonic() + 3
        while face.property("_vectorSplitShown") != 120 and harness.time.monotonic() < deadline:
            self._pump_ms(10)
        self.assertEqual(face.property("_vectorSplitShown"), 120)
        layer.set_prefix_pending(True)
        self._printer.setSplit(160)
        self._pump_ms(100)
        self.assertEqual(face.property("_vectorSplitShown"), 120,
                         "the standing frame was withdrawn while native work was pending")
        self.assertEqual(face.property("_lastSplit"), 120,
                         "QML walked history while a native prefix was on its way")
        layer.set_prefix_pending(False)
        deadline = harness.time.monotonic() + 3
        while face.property("_vectorSplitShown") != 160 and harness.time.monotonic() < deadline:
            self._pump_ms(10)
        self.assertEqual(face.property("_vectorSplitShown"), 160,
                         "worker completion never woke the coalesced painter")

    def test_zero_to_forward_with_a_delayed_prefix_never_flickers_or_overshoots(self):
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(0)
        self._pump_ms(100)
        prefix, url = self._prefix_for(payload, face, 100, "zero-forward")
        url = self._slow_raster(url, "zero-forward", factor=6)
        layer.set_prefix(prefix, url, 100, "fixture-key")
        self._printer.setSplit(160)
        seen = False
        deadline = harness.time.monotonic() + 2
        while harness.time.monotonic() < deadline:
            image = window.grabWindow()
            complete = self._red_in_band(image, face, window, plot, 20 + 155 * 0.6, 125)
            if seen:
                self.assertTrue(complete, "a complete frame lost history during the prefix handover")
            seen = seen or complete
            self.assertFalse(self._red_in_band(image, face, window, plot, 20 + 200 * 0.6, 125),
                             "a forward scrub overshot the requested split")
            self._pump_ms(10)
        self.assertTrue(seen, "the requested frame never arrived")

    def test_the_standing_grid_survives_zero_and_forward_preparation(self):
        monitor, window, face, baseline = self._mount_empty()
        self.pump(20)
        baseline = window.grabWindow()
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        samples = []
        for y in range(5, 100, 2):
            for x in range(5, 100, 2):
                px, py = int(origin.x()) + x, int(origin.y()) + y
                colour = baseline.pixelColor(px, py)
                if 50 < colour.red() < 200 and abs(colour.red() - colour.green()) < 3:
                    samples.append((px, py, baseline.pixel(px, py)))
        self.assertGreater(len(samples), 5, "the baseline never drew a grid")
        for split in (0, 1, 3, 0, 5):
            self._printer.setSplit(split)
            face.setProperty("_deliveredComposition", None)
            for _ in range(3):
                image = window.grabWindow()
                self.assertTrue(all(image.pixel(x, y) == pixel for x, y, pixel in samples),
                                "the preparation cover hid the standing grid")
                self._pump_ms(10)

    def test_a_backward_scrub_to_zero_clears_all_printed_geometry(self):
        # The 0% state owns NOTHING printed: the prefix hides, the
        # canvas clears, and no held picture persists — then a
        # forward scrub repaints the history.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial prefix never drew")
        self._printer.setSplit(0)
        image, count = self._wait_red(window, face, want=False)
        self.assertEqual(count, 0,
                         "printed geometry persisted at 0%")
        # Forward again: the history repaints from the empty state.
        self._printer.setSplit(60)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0,
                           "the 0% state never recovered its history")


