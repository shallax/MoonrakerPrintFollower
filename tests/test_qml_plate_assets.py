"""Executable qml plate assets contracts."""
from tests import qml_engine_support as harness

class PlateFaceRenderTests(harness.PlateFaceRenderTests):
    def test_a_full_seek_renders_the_native_raster_with_no_scrub_vector(self):
        # The raster-only full seek: split == motions and
        # scrubVector == null — a real PlateLayer must still paint,
        # through the raster alone (the vector never exists to draw).
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"])
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the full layer drew nothing with no scrub vector")
        # The corner geometry: every bed corner's band must carry the
        # wall-outer red — a wrong or missing transform hides at an
        # extreme corner.
        for bed_x, bed_y in ((0.0, 0.0), (250.0, 0.0), (250.0, 250.0), (0.0, 250.0)):
            self.assertTrue(self._red_in_band(image, face, window, plot, bed_x, bed_y),
                            "the bed corner (%s, %s) never drew" % (bed_x, bed_y))

    def test_a_zero_percent_layer_paints_nothing_without_the_vector(self):
        # The 0% case: split == 0 with
        # scrubVector == null — the raster must not leak the whole
        # layer when nothing has printed. The proof first confirms
        # the same layer's raster DID draw at 100%, then waits for
        # the picture to return to the empty-mount baseline (a
        # single grab races the render thread).
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"])
        _inked, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the fixture's own raster never drew")
        self._printer.setSplit(0)
        _image, count = self._wait_red(window, face, want=False)
        self.assertEqual(count, 0, "the 0% layer leaked the full raster")

    def test_the_partial_printed_portion_renders_through_the_native_prefix(self):
        # The partial states' prefix: the printed portion blits from
        # the native asset — the vector walk covers only the tail,
        # and here (a PlateLayer, no .classes, split < motions)
        # every red pixel provably came from the prefix.
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=12)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(12)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the partial prefix never drew")

    def test_a_failed_full_asset_uses_complete_vector_geometry_without_a_scrub_payload(self):
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showTravels", True)
        self.pump(10)
        for failed in ("class", "travel", "unpublished", "unpublished-travel"):
            with self.subTest(asset=failed):
                payload = {
                    "classes": {"WALL-OUTER": [[[20.0 + i * 10.0, 125.0, float(i)]
                                                for i in range(21)]]},
                    "travels": [[[20.0 + i * 10.0, 200.0, float(i)] for i in range(21)]],
                    "travelStarts": [], "travelEnds": [], "motions": 21}
                layer = self._native_layer(payload, face)
                missing = "file:///tmp/mpf/missing-%s-%d.png" % (failed, harness.time.monotonic_ns())
                if failed == "class":
                    layer.set_raster(layer.raster, "fixture-key", missing)
                elif failed == "travel":
                    layer.set_travels(layer.travelRaster, "fixture-key", missing)
                elif failed == "unpublished":
                    layer.set_raster(layer.raster, "fixture-key", "")
                else:
                    layer.set_travels(layer.travelRaster, "fixture-key", "")
                self._printer.setScrub(None)
                self._printer.setLayers({"prev": None, "current": layer, "next": None})
                self._printer.setSplit(21)
                deadline = harness.time.monotonic() + 5.0
                image = None
                while harness.time.monotonic() < deadline:
                    self._pump_ms(20)
                    image = window.grabWindow()
                    if face.property("_vectorCoversShown") == 0:
                        break
                self.assertEqual(face.property("_vectorCoversShown"), 0,
                                 "failed transport never delivered a full fallback")
                plot = self._bed_point(face, 0.0, 0.0)
                for x in (25.0, 105.0, 215.0):
                    self.assertGreater(self._stroke_ink(image, face, window, plot, x, 125.0), 0,
                                       "fallback lost printed history")
                self.assertGreater(self._purple_pixels(image, face, window), 0,
                                   "fallback omitted required travel moves")
                from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
                self.assertTrue(QMetaObject.invokeMethod(face, "_exactReady", Q_RETURN_ARG(QVariant)))

    def test_failed_base_and_ghost_assets_complete_the_exact_background(self):
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showPrevious", True)
        self.pump(10)
        payload = {"classes": {"WALL-OUTER": [[[20.0, 125.0, 0.0],
                                                [200.0, 125.0, 20.0]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        ghost_payload = {"classes": {"FILL": [[[20.0, 60.0, 0.0],
                                               [200.0, 60.0, 20.0]]]},
                         "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        current = self._native_layer(payload, face)
        ghost = self._native_layer(ghost_payload, face)
        current.set_base(current.baseRaster, "fixture-key",
                         "file:///tmp/mpf/missing-base-%d.png" % harness.time.monotonic_ns())
        ghost.set_raster(ghost.raster, "fixture-key",
                         "file:///tmp/mpf/missing-ghost-%d.png" % harness.time.monotonic_ns())
        self._printer.setSplit(0)
        self._printer.setScrub(None)
        self._printer.setLayers({"prev": ghost, "current": current, "next": None})
        plot = self._bed_point(face, 0.0, 0.0)
        image = self._wait_until(window, lambda shot:
            self._band_changed(shot, baseline, face, window, plot, 100.0, 125.0, radius=6)
            and self._band_changed(shot, baseline, face, window, plot, 100.0, 60.0, radius=6)
            and bool(QMetaObject.invokeMethod(face, "_exactReady", Q_RETURN_ARG(QVariant))))
        for y in (60.0, 125.0):
            self.assertTrue(self._band_changed(image, baseline, face, window, plot,
                                               100.0, y, radius=6),
                            "failed background transport left missing geometry")
        self.assertTrue(QMetaObject.invokeMethod(face, "_exactReady", Q_RETURN_ARG(QVariant)))

    def test_ordinary_full_rasters_never_convert_the_fallback_geometry(self):
        from PyQt6.QtCore import pyqtProperty
        from mpf.PlateQt import PlateLayer

        class CountingLayer(PlateLayer):
            def __init__(self, payload):
                super().__init__(payload)
                self.fallback_reads = 0

            @pyqtProperty("QVariantMap", constant=True)
            def fallbackVector(self):
                self.fallback_reads += 1
                return self._payload

        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = {"classes": {"WALL-OUTER": [[[20.0, 125.0, 0.0],
                                                [200.0, 125.0, 20.0]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        layer = self._native_layer(payload, face, layer_type=CountingLayer)
        self._printer.setScrub(None)
        # Establish full demand before replacing the scene; publishing the
        # wrapper at the fixture's previous partial split legitimately asks
        # the recovery producer for its missing tail.
        self._printer.setSplit(21)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0)
        self._pump_ms(150)
        window.grabWindow()
        self.assertEqual(layer.fallback_reads, 0,
                         "a capability check wrapped geometry on the normal raster path")


