"""Real-engine region gestures, transform mapping and transaction boundaries."""
import json
from PyQt6.QtCore import QVariant, Qt, pyqtProperty, pyqtSlot, pyqtSignal
from PyQt6.QtQml import QQmlExpression
from PyQt6.QtTest import QTest
from mpf.geometry.DetectionRegions import validate_regions
from tests import qml_engine_support as harness


class RegionCamera(harness.CameraModelDouble):
    detectionChanged = pyqtSignal()
    cameraRotationChanged = pyqtSignal()
    cameraFlipChanged = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.editing = False
        self.regions = []
        self.saved = []
        self.angle = 0
        self.mirror = False

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionEditingRegions(self): return self.editing
    @pyqtProperty(QVariant, notify=detectionChanged)
    def detectionRegions(self): return self.regions
    @pyqtProperty(int, notify=cameraRotationChanged)
    def cameraRotation(self): return self.angle
    @pyqtProperty(bool, notify=cameraFlipChanged)
    def cameraFlipHorizontal(self): return self.mirror
    @pyqtProperty(str, notify=detectionChanged)
    def actionStatus(self): return ""

    @pyqtSlot(bool)
    def setDetectionEditingRegions(self, editing):
        self.editing = editing
        self.detectionChanged.emit()

    @pyqtSlot(QVariant, result=str)
    def validateDetectionRegions(self, value):
        try:
            validate_regions(value.toVariant() if hasattr(value, "toVariant") else value)
            return ""
        except ValueError as error:
            return str(error)

    @pyqtSlot(QVariant, result=bool)
    def saveDetectionRegions(self, value):
        if self.validateDetectionRegions(value): return False
        raw = value.toVariant() if hasattr(value, "toVariant") else value
        self.saved.append(raw)
        self.regions = raw
        self.setDetectionEditingRegions(False)
        return True


class DetectionRegionGestureTests(harness.RealEngineTestCase):
    def assert_no_qml_errors(self):
        messages = harness._APPLICATION["messages"][self._message_start:]
        self.assertFalse([m for m in messages if ("TypeError" in m or "ReferenceError" in m) and "Cura/Widgets/ComboBox.qml" not in m], messages)

    def scene(self, width=700, height=700, *, angle=0, mirror=False):
        pane, window = self.mount_window("CameraPane.qml", width, height)
        pane.setProperty("configured", True)
        model = RegionCamera()
        model.angle, model.mirror = angle, mirror
        pane.setProperty("printerModel", model)
        self.pump(30)
        image = self.find(pane, "cameraImage")
        image.setProperty("visible", True)
        image.setProperty("imageWidth", 640)
        image.setProperty("imageHeight", 480)
        model.setDetectionEditingRegions(True)
        self._pump_ms(150)
        return pane, window, model, image, self.find(pane, "detectionOverlay")

    def js(self, item, code):
        expression = QQmlExpression(self.engine.rootContext(), item, code)
        result = expression.evaluate()
        self.assertFalse(expression.hasError(), expression.error().toString())
        return result[0] if isinstance(result, tuple) else result

    def regions(self, overlay):
        return json.loads(self.js(overlay, "JSON.stringify(draftRegions)"))

    def point(self, image, window, x, y):
        return image.mapToItem(window.contentItem(), harness.QPointF(image.width()*x, image.height()*y)).toPoint()

    def drag(self, image, window, start, end):
        QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.point(image, window, *start))
        self.pump()
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QMouseEvent
        position = self.point(image, window, *end)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(position), QPointF(window.mapToGlobal(position)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(window, event)
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.point(image, window, *end))
        self.pump()

    def test_rectangles_drag_insert_delete_and_cancel_are_real_pointer_transactions(self):
        pane, window, model, image, overlay = self.scene()
        self.js(overlay, "addRectangle()")
        self.assertEqual(len(self.regions(overlay)[0]), 4)
        self.drag(image, window, (.2,.2), (.12,.28))
        self.assertAlmostEqual(self.regions(overlay)[0][0][0], .12, delta=.01)
        # A midpoint press inserts a fifth vertex; drag deforms it.
        points = self.regions(overlay)[0]
        mid = [(points[0][axis]+points[1][axis])/2 for axis in (0,1)]
        self.drag(image, window, mid, (mid[0], .12))
        self.assertEqual(len(self.regions(overlay)[0]), 5)
        self.js(overlay, "deleteVertex()")
        self.assertEqual(len(self.regions(overlay)[0]), 4)
        self.js(overlay, "addRectangle()")
        self.assertEqual(len(self.regions(overlay)), 2)
        self.js(overlay, "deleteRegion()")
        self.assertEqual(len(self.regions(overlay)), 1)
        self.js(overlay, "undo()")
        self.assertEqual(len(self.regions(overlay)), 2)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.pump()
        self.assertFalse(model.editing)
        self.assertEqual(model.saved, [])
        self.assertEqual(model.regions, [])
        self.assert_no_qml_errors()

    def test_rotated_mirrored_zoomed_drag_maps_to_raw_image_coordinates(self):
        pane, window, model, image, overlay = self.scene(angle=90, mirror=True)
        self.js(overlay, "draftRegions = [[[.4,.4],[.6,.4],[.6,.6],[.4,.6]]]; selectedRegion = 0")
        viewport = self.find(pane, "cameraViewport")
        self.js(viewport, "setCameraZoom(2)")
        self._pump_ms(350)
        self.drag(image, window, (.4,.4), (.36,.43))
        points = self.regions(overlay)[0]
        self.assertAlmostEqual(points[0][0], .36, delta=.012)
        self.assertAlmostEqual(points[0][1], .43, delta=.012)
        self.assert_no_qml_errors()

    def test_narrow_dock_does_not_cover_any_fitted_image_corner_or_handle(self):
        pane, window, model, image, overlay = self.scene(180,400)
        editor = self.find(pane, "detectionRegionEditor")
        frame = self.find(pane, "cameraFrame")
        top = frame.mapToItem(window.contentItem(), harness.QPointF(0,frame.height()))
        dock = editor.mapToItem(window.contentItem(), harness.QPointF(0,0))
        self.assertLessEqual(top.y(), dock.y())
        self.js(overlay, "addRectangle()")
        self.drag(image, window, (.65,.65), (.8,.8))
        self.assertAlmostEqual(self.regions(overlay)[0][2][0], .8, delta=.02)
        self.js(overlay, "resetRegions()")
        self.assertEqual(self.regions(overlay), [])
        self.assert_no_qml_errors()

    def test_self_intersections_are_rejected_and_selection_clicks_do_not_fill_undo(self):
        pane, window, model, image, overlay = self.scene()
        self.js(overlay, "addRectangle()")
        count = self.js(overlay, "undoHistory.length")
        self.drag(image, window, (.2,.2), (.2,.2))
        self.assertEqual(self.js(overlay, "undoHistory.length"), count)
        before = self.regions(overlay)
        self.drag(image, window, (.2,.2), (.7,.5))
        self.assertEqual(self.regions(overlay), before)
        self.assertNotEqual(overlay.property("errorText"), "")
        self.assert_no_qml_errors()

    def test_selected_midpoint_wins_over_an_overlapping_unselected_vertex(self):
        pane, window, model, image, overlay = self.scene()
        self.js(overlay, "draftRegions = [[[.2,.2],[.6,.2],[.6,.6],[.2,.6]], [[.4,.2],[.8,.2],[.8,.8],[.4,.8]]]; selectedRegion = 0")
        self.drag(image, window, (.4,.2), (.4,.15))
        self.assertEqual(len(self.regions(overlay)[0]), 5)
        self.assertEqual(self.regions(overlay)[1][0], [.4,.2])

    def test_rotated_center_zoom_uses_displayed_axes(self):
        pane, window, model, image, overlay = self.scene(angle=270)
        viewport = self.find(pane, "cameraViewport")
        frame = self.find(pane, "cameraFrame")
        self.js(viewport, "zoomCamera(1.25, cameraPictureWidth/2, cameraPictureHeight/2)")
        self.assertAlmostEqual(viewport.property("cameraPanX"), 0, delta=.01)
        self.assertAlmostEqual(viewport.property("cameraPanY"), 0, delta=.01)
        self.assertAlmostEqual(viewport.property("cameraPanLimitX"), frame.width()*.25/2, delta=.01)
        self.assertAlmostEqual(viewport.property("cameraPanLimitY"), frame.height()*.25/2, delta=.01)
