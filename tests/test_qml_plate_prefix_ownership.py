"""Executable qml plate prefix ownership contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_partial_prefix_and_canvas_tail_keep_one_stroke_width(self):
        # The live partial composition is TWO render engines: QPainter
        # owns the native prefix and QML Canvas owns the vector tail.
        # Native-vs-native parity cannot catch a seam whose Canvas half
        # is a different thickness. Measure the real composed pixels on
        # a horizontal run before, at and after the prefix boundary.
        monitor, window, face, baseline = self._mount_empty()
        if getattr(self, "_pixel_width_contract", False):
            face.setProperty("pixelLineWidth", True)
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        prefix_split = 10
        layer = self._native_layer(payload, face, prefix_split=prefix_split)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)

        # Do not let the native prefix alone satisfy the wait: x=155 is
        # beyond the prefix (which ends at x=110), so red ink there
        # proves the Canvas tail has actually landed.
        origin = face.mapToItem(window.contentItem(), harness.QPointF(0.0, 0.0))
        row = int(origin.y() + plot["offsetY"]
                  + (plot["bedYMax"] - 125.0) * plot["sy"])

        def red_height(image, bed_x, tolerance=20):
            col = int(origin.x() + plot["offsetX"]
                      + (bed_x - plot["bedXMin"]) * plot["sx"])
            return sum(
                1 for py in range(max(0, row - 12), min(image.height(), row + 13))
                if self._matches(image.pixel(col, py), (0xD3, 0x2F, 0x2F),
                                 tolerance=tolerance)
            )

        # The census holds in EVERY frame of the settle, not only in
        # the settled one: the Canvas's trim commits a frame before the
        # scene shows the trimmed texture, and a prefix admitted inside
        # that beat stacks its ink over the bitmap it replaces — the
        # prefix body then reads a full core row deeper than the Canvas
        # body (the CI signature: [4, 2]). Both bodies are measured on
        # every frame; x=155 also proves the Canvas tail landed.
        frames = 0

        def tail_landed(image):
            nonlocal frames
            frames += 1
            bodies = [red_height(image, 75.0), red_height(image, 155.0)]
            if min(bodies) > 0:
                self.assertLessEqual(
                    max(bodies) - min(bodies), 1,
                    "native prefix / Canvas tail stroke widths diverge in "
                    "frame %d: %r" % (frames, bodies))
            return self._red_in_band(image, face, window, plot, 155.0, 125.0,
                                     radius=6)

        image = self._wait_until(window, tail_landed, timeout=15.0)
        self.assertTrue(tail_landed(image), "the Canvas tail never landed")

        self.pump(20)
        image = window.grabWindow()

        # x=75 is native-prefix body, x=110 is the engine boundary,
        # x=155 is Canvas-tail body. One physical pixel is the maximum
        # acceptable body-width disagreement. The census counts only
        # CORE stroke ink: the two round caps meeting at the boundary
        # column legitimately add half-intensity antialiased fringes
        # there, and the grey base's wash over the prefix must not
        # read as the feature colour — tolerance 60 accepted both.
        # The bodies are held to the core census; the boundary column
        # takes the same 60 — the caps' overlap deepens its fringe into
        # the core band (one column wide), so a core census there would
        # count the joint, not the stroke. A Canvas half drawn at a
        # different thickness diverges in both censuses.
        bodies = [red_height(image, 75.0), red_height(image, 155.0)]
        seam = [red_height(image, 75.0, tolerance=60), red_height(image, 110.0, tolerance=60),
                red_height(image, 155.0, tolerance=60)]
        self.assertGreater(min(bodies), 0, "one side of the partial stroke vanished")
        self.assertLessEqual(max(bodies) - min(bodies), 1,
                             "native prefix / Canvas tail stroke widths diverge: %r" % bodies)
        # Each side can contribute one antialias fringe row where round
        # caps overlap. The body comparison above still permits only one
        # pixel of disagreement between the two render engines.
        self.assertLessEqual(max(seam) - min(seam), 2,
                             "the seam column's stroke diverges: %r" % seam)
        # The grab forces the scene's sync (the harness's window
        # doctrine): the threaded canvas's last frame drains here,
        # before the teardown.
        window.grabWindow()
        self.pump(30)
        # The teardown's own binding evaluations must never wrap a
        # QObject: the engine's property-cache registry is already
        # dying when the document's last var reads happen, and a
        # PlateLayer in progress.layers.current is exactly the wrap
        # that segfaults it (QObjectWrapper::wrap -> propertyCache).
        # Restore the plain-dict payload: dicts wrap inertly.
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_pixel_width_prefix_and_canvas_tail_keep_one_stroke_width(self):
        self._pixel_width_contract = True
        self.test_partial_prefix_and_canvas_tail_keep_one_stroke_width()

    def test_a_prefix_that_never_loads_leaves_the_vector_owning_the_history(self):
        # The prefix's model-side validity is NOT the scene's: while
        # the prefix Image is not Ready (here its file never
        # exists), the Canvas must draw the FULL printed interval —
        # never a frame in which neither renderer owns the history.
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
        prefix_split = 10
        from mpf.plate.PlateQt import render_layer_prefix, png_file
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
        prefix = render_layer_prefix(payload, plot, view, prefix_split)
        # The prefix image exists — its URL deliberately does not,
        # so the scene-graph Image stays not-Ready forever.
        layer.set_prefix(prefix, "file:///tmp/mpf/raster-probe/missing-%d.png"
                         % harness.time.monotonic_ns(), prefix_split, "fixture-key")
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        # Every sampled frame while the prefix stays un-Ready: the
        # printed history (bed x=75, inside the prefix interval)
        # must stay on screen — the vector owns it all.
        for _ in range(10):
            self.pump(5)
            image = window.grabWindow()
            self.assertGreater(
                self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                0, "a frame lost the printed history while the prefix "
                   "image was not Ready")
        # The real file lands: the loading gap must also hold ink,
        # and once Ready the prefix takes over — the boundary column
        # gains the two caps' fringes (loose census >= 3 rows).
        layer.set_prefix(prefix, png_file(
            prefix, "/tmp/mpf/raster-probe",
            "fixture-ready-%d" % harness.time.monotonic_ns()), prefix_split, "fixture-key")
        deadline = harness.time.monotonic() + 3.0
        takeover = False
        while harness.time.monotonic() < deadline:
            self.pump(5)
            image = window.grabWindow()
            self.assertGreater(
                self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                0, "the loading gap lost the printed history")
            if self._stroke_ink(image, face, window, census_plot,
                                110.0, 125.0, tolerance=60) >= 3:
                takeover = True
                break
        self.assertTrue(takeover, "the ready prefix never took over")
        # The handover's own beat: the takeover is read a sync before
        # the scene presents the composed texture, and the strict
        # census belongs to the composed frame — sampled a beat after
        # it (the seam must not stay swollen, and the history must
        # still be there).
        self._pump_ms(30)
        image = window.grabWindow()
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the composed frame lost the printed history")
        self.assertLessEqual(
            max(self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                self._stroke_ink(image, face, window, census_plot, 155.0, 125.0)),
            3, "the composed stroke swelled beyond the prefix/tail seam")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_face_leases_held_assets_and_releases_its_model_on_destruction(self):
        from PyQt6.QtCore import QEvent
        component = harness.QQmlComponent(self.engine)
        component.loadUrl(harness.QUrl.fromLocalFile(str(harness.qml_source("PlateProgressFace.qml"))))
        first = harness.PlatePrinterDouble()
        second = harness.PlatePrinterDouble()
        face = component.createWithInitialProperties({"printerModel": first})
        self.assertIsNotNone(face, harness.qml_error_report(component))
        self.pump(10)
        owner = face.property("_assetOwner")
        self.assertIn(owner, first.asset_owners)
        prefix = "file:///tmp/mpf/standing-prefix.png"
        full = "file:///tmp/mpf/held-full.png"
        warm = "file:///tmp/mpf/gesture-entry.png"
        face.setProperty("_standingPrefix", {"source": prefix, "from": 10,
                         "anchor": -1, "world": "lease-test", "view": ""})
        face.setProperty("_standingFull", {"source": full, "travels": "",
                         "anchor": -1, "world": "lease-test"})
        face.setProperty("_gestureNavSource", warm)
        self.pump(10)
        self.assertTrue({prefix, full, warm}.issubset(first.asset_owners[owner]))
        face.setProperty("_standingPrefix", None)
        self.pump(5)
        self.assertNotIn(prefix, first.asset_owners[owner])
        face.setProperty("printerModel", second)
        self.pump(10)
        self.assertFalse(first.asset_owners)
        self.assertTrue({full, warm}.issubset(
            second.asset_owners[face.property("_assetOwner")]))
        face.deleteLater()
        harness.QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.pump(10)
        self.assertFalse(second.asset_owners)

    def test_a_view_change_retires_the_retained_prefix_picture(self):
        # The retained handover's frozen pixels bake the view
        # transform: a zoom or pan after the freeze must retire the
        # picture, or the next loading gap would draw the print
        # displaced (the wrong-place ghost).
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
        prefix_split = 10
        from mpf.plate.PlateQt import render_layer_prefix, png_file
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
        prefix = render_layer_prefix(payload, plot, view, prefix_split)
        layer.set_prefix(prefix, png_file(
            prefix, "/tmp/mpf/raster-probe",
            "fixture-retire-%d" % harness.time.monotonic_ns()), prefix_split, "fixture-key")
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        # The freeze arms while the live picture stands.
        deadline = harness.time.monotonic() + 3.0
        armed = False
        while harness.time.monotonic() < deadline:
            self.pump(5)
            if face.property("_retainedPrefixSource") != "":
                armed = True
                break
        self.assertTrue(armed, "the retained freeze never armed")
        # The view change retires the frozen picture.
        face.setProperty("viewScale", 1.5)
        self.pump(5)
        self.assertEqual(face.property("_retainedPrefixSource"), "",
                         "a zoom left the stale-view picture armed")
        self.assertEqual(face.property("_retainedPrefixSplit"), -1,
                         "the retired picture kept its split")
        # The full-picture hold retires with it.
        self.assertEqual(face.property("_heldFullSource"), "",
                         "the held full frame survived the view change")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(harness.PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)
