"""Production Cura editor: scrollbar gutter, themed intensity and draft cancellation."""
from __future__ import annotations

import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import time

from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtQml import QQmlExpression, qmlContext
    from mpf.toolhead.ToolheadAssetStore import ToolheadAssetStore
    from mpf.toolhead.ToolheadModels import ToolheadModels


class ToolheadImportSettingsTests(harness.RealEngineTestCase):
    def test_elapsed_and_triangle_progress_stay_visible_and_cancel_preserves_model(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = ToolheadModels(ToolheadAssetStore(folder), folder,
                lambda: SimpleNamespace(), lambda: "printer")
            host, window = self.mount_window("ToolheadModelSettings.qml", 760, 650)
            host.setProperty("model", draft)
            original = draft.mesh
            clock = [1000.]
            def convert(_path, _runtime, cancelled, *, progress):
                progress("Building display mesh · 4,096 triangles")
                if not cancelled.wait(5): raise AssertionError("UI did not cancel import")
                raise ValueError("Model import cancelled")
            def wait_for(predicate):
                deadline = time.monotonic() + 3
                while not predicate() and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(predicate())
            try:
                with patch('mpf.toolhead.ToolheadModels.install_runtime', return_value=folder), \
                        patch('mpf.toolhead.ToolheadModels.read_step', side_effect=convert), \
                        patch('mpf.toolhead.ToolheadModels.time', SimpleNamespace(monotonic=lambda: clock[0])):
                    draft._start("fixture.step", True)
                    status = self.find(host, "toolheadImportStatus")
                    elapsed = self.find(host, "toolheadImportElapsed")
                    cancel = self.find(host, "toolheadCancelImport")
                    wait_for(lambda: status.property("text") == "Building display mesh · 4,096 triangles")
                    clock[0] += 185
                    draft._elapsed_timer.timeout.emit()
                    self.pump(10)
                    self.assertEqual(elapsed.property("text"), "Elapsed: 3:05")
                    self.assertTrue(elapsed.isVisible())
                    self.assertTrue(cancel.isVisible() and cancel.isEnabled())
                    self.assertFalse(self.find(host, "toolheadChooseModel").isEnabled())
                    # Visibility updates precede the Column's next polish pass.
                    # Deliver the click at the rendered, settled button position.
                    window.grabWindow()
                    point = cancel.mapToScene(harness.QPointF(cancel.width()/2, cancel.height()/2)).toPoint()
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
                    self.assertTrue(draft._cancelled.is_set(), "Cancel did not receive the click")
                    wait_for(lambda: not draft.busy)
                    self.pump(10)
                    self.assertIs(draft.mesh, original)
                    self.assertFalse(elapsed.isVisible())
                    self.assertFalse(cancel.isVisible())
                    self.assertTrue(self.find(host, "toolheadChooseModel").isEnabled())
            finally:
                draft.close()
                wait_for(lambda: not draft.busy)
                host.setProperty("model", None)


class ToolheadEditorTests(harness.RealEngineTestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = SimpleNamespace(toolhead_model="", toolhead_model_name="", toolhead_tip=[],
            toolhead_lights=[dict(position=[0, 0, i], direction=[0, 0, 1],
                                 colour="#0000ff", brightness=5) for i in range(8)])
        self.draft = ToolheadModels(ToolheadAssetStore(self.directory.name), self.directory.name,
                                    lambda: config, lambda: "printer")
        self.addCleanup(self.draft.close)
        component = harness.QQmlComponent(self.engine)
        component.loadUrl(harness.QUrl.fromLocalFile(str(harness.ROOT / "mpf/settings/ToolheadModelEditorDialog.qml")))
        self.dialog = component.createWithInitialProperties({"model": self.draft})
        self.assertIsNotNone(self.dialog, harness.qml_error_report(component))
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.close)
        harness.QMetaObject.invokeMethod(self.dialog, "openEditor")
        self.pump()

    def find(self, name):
        # Repeater delegates have visual ownership, not QObject parentage.
        pending = [self.dialog.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name: return item
            pending.extend(item.childItems())
        self.fail("Missing editor item: " + name)

    def assert_always_visible(self, bar):
        expression = QQmlExpression(qmlContext(bar), bar, "policy === 2")
        self.assertEqual(expression.evaluate(), (True, False))

    def test_eight_lights_scroll_with_permanent_themed_gutter(self):
        scroll = self.find("toolheadLightsScroll")
        bar = self.find("toolheadLightsScrollbar")
        self.assert_always_visible(bar)
        self.assertGreater(bar.property("width"), 0)
        self.assertGreater(scroll.property("contentHeight"), scroll.property("height"))
        self.assertEqual(scroll.property("contentWidth"), scroll.property("width"))
        slider = self.find("toolheadLightBrightness0")
        self.assertLess(slider.property("width") + bar.property("width"), scroll.property("width"))
        self.draft._lights = []
        self.draft.changed.emit()
        self.pump()
        self.assertGreater(bar.property("width"), 0)
        self.assert_always_visible(bar)

    def test_intensity_keeps_blue_hue_and_native_circular_handle(self):
        slider = self.find("toolheadLightBrightness0")
        handle = slider.property("handle")
        colour = slider.property("fillColor")
        self.assertEqual((colour.red(), colour.green(), colour.blue()), (0, 0, 255))
        slider.setProperty("value", 0)
        self.pump()
        colour = slider.property("fillColor")
        self.assertEqual((colour.red(), colour.green()), (0, 0))
        self.assertGreater(colour.blue(), 0)
        self.assertLess(colour.blue(), 255)
        self.assertEqual(handle.property("width"), handle.property("height"))
        self.assertEqual(handle.property("radius"), round(handle.property("width") / 2))

    def test_brightness_percentage_maps_to_existing_intensity_without_scroll_stealing(self):
        slider = self.find("toolheadLightBrightness0")
        self.assertEqual(slider.property("from"), 0)
        self.assertEqual(slider.property("to"), 100)
        self.assertEqual(slider.property("value"), 100)
        handle = slider.property("handle")
        start = handle.mapToScene(harness.QPointF(handle.width() / 2, handle.height() / 2)).toPoint()
        end = slider.mapToScene(harness.QPointF(slider.width() / 2, slider.height() / 2)).toPoint()
        previews, publications = [], []
        self.draft.lightingPreviewChanged.connect(lambda: previews.append(self.draft.lights[0]["brightness"]))
        self.draft.changed.connect(lambda: publications.append(True))
        QTest.mousePress(self.dialog, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.dialog, pos=end)
        self.pump()
        self.assertFalse(self.find("toolheadLightsScroll").property("interactive"))
        self.assertTrue(previews, "lighting must change before releasing the handle")
        self.assertFalse(publications, "list publication would destroy the active slider")
        self.assertAlmostEqual(self.draft.lights[0]["brightness"], 2.5, delta=.15)
        self.assertIs(slider, self.find("toolheadLightBrightness0"))
        QTest.mouseRelease(self.dialog, Qt.MouseButton.LeftButton, pos=end)
        self.pump()
        self.assertAlmostEqual(self.draft.lights[0]["brightness"], 2.5, delta=.15)
        self.assertTrue(self.find("toolheadLightsScroll").property("interactive"))

    def test_right_drag_orbits_middle_pans_and_left_only_picks(self):
        interaction = self.find("toolheadModelInteraction")
        preview = self.find("toolheadModelPreview")
        start = interaction.mapToScene(harness.QPointF(100, 100)).toPoint()
        end = interaction.mapToScene(harness.QPointF(130, 110)).toPoint()
        for button, property_name in ((Qt.MouseButton.LeftButton, None),
                                      (Qt.MouseButton.RightButton, "orbitCalls"),
                                      (Qt.MouseButton.MiddleButton, "panCalls")):
            QTest.mousePress(self.dialog, button, pos=start)
            QTest.mouseMove(self.dialog, pos=end)
            QTest.mouseRelease(self.dialog, button, pos=end)
            self.pump()
            if property_name:
                self.assertGreater(preview.property(property_name), 0)
            else:
                self.assertEqual(preview.property("orbitCalls"), 0)
                self.assertEqual(preview.property("panCalls"), 0)
                self.assertEqual(preview.property("pickCalls"), 0)
        preview.setProperty("picking", True)
        QTest.mouseClick(self.dialog, Qt.MouseButton.LeftButton, pos=start)
        self.pump()
        self.assertEqual(preview.property("pickCalls"), 1)
        QTest.mouseClick(self.dialog, Qt.MouseButton.RightButton, pos=start)
        self.pump()
        self.assertEqual(preview.property("pickCalls"), 1)

    def test_cancel_restores_nozzle_and_lights_but_done_keeps_draft(self):
        original = self.draft.fields()
        self.draft.setTip(0, "12.5")
        self.draft.removeLight(0)
        harness.QMetaObject.invokeMethod(self.dialog, "reject")
        self.pump()
        self.assertEqual(self.draft.fields(), original)
        harness.QMetaObject.invokeMethod(self.dialog, "openEditor")
        self.draft.setTip(0, "4.5")
        self.draft.setLightColour(0, "#ff00ff")
        harness.QMetaObject.invokeMethod(self.dialog, "accept")
        self.pump()
        self.assertEqual(self.draft.tipX, "4.5")
        self.assertEqual(self.draft.lights[0]["colour"], "#ff00ff")
        self.assertFalse(self.dialog.isVisible())
        messages = harness._APPLICATION["messages"][self._message_start:]
        self.assertFalse([message for message in messages if "TypeError" in message or "ReferenceError" in message], messages)
