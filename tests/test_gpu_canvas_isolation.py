"""The real QML GPU face must never activate its hidden software producers."""
from tests import qml_engine_support as harness


class GpuCanvasIsolationTests(harness.PlateFaceRenderTests):
    def test_layer_ghost_and_reverse_scrubbing_never_read_software_geometry(self):
        from PyQt6.QtCore import pyqtProperty
        from plugins.PlateQt import PlateLayer

        class CountingLayer(PlateLayer):
            def __init__(self, payload):
                super().__init__(payload)
                self.reads = 0

            @pyqtProperty("QVariantMap", constant=True)
            def fallbackVector(self):
                self.reads += 1
                return self._payload

        monitor, window, face, baseline = self._mount_empty()
        # The capture harness supplies an inert GPU item: changing its
        # capability exercises real QML policy without needing a GL context.
        candidates = [item for item in face.childItems()
                      if item.metaObject().indexOfProperty("supported") >= 0
                      and item.property("dataSource") is None]
        self.assertEqual(len(candidates), 1)
        gpu = candidates[0]
        self.assertTrue(gpu.setProperty("supported", True))
        gpu.setProperty("ready", True)
        self.pump(30)
        self.assertTrue(face.property("gpuRendering"))
        payload = {"classes": {"WALL-OUTER": [[[20., 125., 0.], [200., 125., 20.]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        layer = CountingLayer(payload)
        self._printer.setScrub(None)
        self._printer.setSplit(21)
        self._printer.setLayers({"prev": layer, "current": layer, "next": layer})
        self.pump(30)
        for split in (20, 15, 5, 21, 19):
            self._printer.setSplit(split)
            for ghost in (False, True):
                face.setProperty("showBase", ghost)
                face.setProperty("showPrevious", ghost)
                face.setProperty("showNext", ghost)
                face.setProperty("viewScale", 1.8 if ghost else 1.5)
                face.setProperty("viewPanX", -30. if ghost else -20.)
                self._pump_ms(30)
                window.grabWindow()
        self.assertEqual(layer.reads, 0,
                         "GPU navigation activated full software QVariant conversion")
        # Switching the Diagnostics fallback on must restore the producers,
        # even when no layer/progress/toggle input changes with that switch.
        face.setProperty("softwareRendering", True)
        self._pump_ms(100)
        window.grabWindow()
        self.assertFalse(face.property("gpuRendering"))
        self.assertGreater(layer.reads, 0, "software recovery was disabled with the GPU path")
