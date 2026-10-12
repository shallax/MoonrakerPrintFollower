"""Isolated Toolhead presentation tests: no printer or command transport."""
import unittest

from qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from tools.capture_preview_toolhead import ToolheadScene
    from PyQt6.QtCore import QMetaObject, QPointF, Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtTest import QTest


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class PreviewToolheadQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def scene(self, **kwargs):
        scene = ToolheadScene(self.app, **kwargs)
        self.addCleanup(scene.close)
        return scene

    def test_disconnected_defaults_disable_every_motion_action(self):
        scene = self.scene()
        for name in ("JogXMinus", "JogXPlus", "JogYMinus", "JogYPlus",
                     "JogZMinus", "JogZPlus", "HomeAll", "HomeX", "HomeY",
                     "HomeZ", "MoveTo", "OffsetDown", "OffsetUp"):
            self.assertFalse(scene.find("previewToolhead" + name).property("enabled"), name)
        self.assertEqual(scene.find("previewToolheadStatus").property("text"), "No printer selected")
        self.assertEqual(scene.warnings, [])

    def test_exact_distance_drafts_do_not_commit_prefixes(self):
        scene = self.scene()
        scene.ready()
        field = scene.find("previewJogDistanceExact")
        slider = scene.find("previewJogDistanceSlider")
        field.forceActiveFocus()
        for text in ("7", "75"):
            field.setProperty("text", text)
            scene.pump()
            self.assertEqual(scene.pane.property("jogDistance"), 25)
        self.assertAlmostEqual(slider.property("value"), 6.5)
        QMetaObject.invokeMethod(field, "editingFinished")
        self.assertEqual(scene.pane.property("jogDistance"), 75)
        field.setProperty("text", "200")
        scene.pump()
        self.assertEqual(slider.property("value"), 8)
        QMetaObject.invokeMethod(field, "editingFinished")
        self.assertEqual(scene.pane.property("jogDistance"), 200)
        self.assertEqual(field.property("text"), "200")
        field.setProperty("text", "500")
        QMetaObject.invokeMethod(field, "editingFinished")
        self.assertEqual(scene.pane.property("jogDistance"), 200)
        self.assertEqual(field.property("text"), "200")
        self.assertIn("0.01–300", scene.find("previewJogDistanceValidation").property("text"))

    def test_slider_keyboard_chooses_neighbour_of_a_custom_distance(self):
        scene = self.scene()
        scene.ready()
        scene.pane.setProperty("jogDistance", 75)
        slider = scene.find("previewJogDistanceSlider")
        slider.forceActiveFocus()
        QTest.keyClick(scene.window, Qt.Key.Key_Left)
        self.assertEqual(scene.pane.property("jogDistance"), 50)
        QTest.keyClick(scene.window, Qt.Key.Key_Right)
        self.assertEqual(scene.pane.property("jogDistance"), 100)
        QTest.keyClick(scene.window, Qt.Key.Key_End)
        self.assertEqual(scene.pane.property("jogDistance"), 125)
        QTest.keyClick(scene.window, Qt.Key.Key_Home)
        self.assertEqual(scene.pane.property("jogDistance"), 0.1)

    def test_reconnect_cancels_distance_draft_and_retires_motion_focus(self):
        scene = self.scene()
        scene.ready()
        field = scene.find("previewJogDistanceExact")
        field.forceActiveFocus()
        field.setProperty("text", "75")
        QMetaObject.invokeMethod(scene.pane, "resetDrafts")
        scene.pane.setProperty("connected", False)
        scene.pump()
        scene.ready()
        self.assertEqual(scene.pane.property("jogDistance"), 25)
        self.assertEqual(field.property("text"), "25")
        self.assertFalse(field.property("activeFocus"))
        jog = scene.find("previewToolheadJogXPlus")
        jog.forceActiveFocus()
        QMetaObject.invokeMethod(scene.pane, "resetDrafts")
        scene.pane.setProperty("connected", False)
        scene.pane.setProperty("jogAllowed", False)
        scene.pump()
        scene.ready()
        self.assertFalse(jog.property("activeFocus"))
        self.assertTrue(scene.find("previewToolheadCollapse").property("activeFocus"))
        jog.forceActiveFocus()
        for gate in ("jogAllowed", "homeAllowed", "moveToAllowed", "offsetAllowed"):
            scene.pane.setProperty(gate, False)
        scene.pane.setProperty("connected", False)
        QMetaObject.invokeMethod(scene.pane, "resetDrafts")
        scene.pump()
        scene.ready()
        self.assertFalse(jog.property("activeFocus"))
        self.assertTrue(scene.find("previewToolheadCollapse").property("activeFocus"))
        field.forceActiveFocus()
        field.setProperty("text", "50")
        QMetaObject.invokeMethod(field, "editingFinished")
        self.assertEqual(scene.pane.property("jogDistance"), 50)

    def test_collapsing_moves_keyboard_focus_to_reopen_tab(self):
        scene = self.scene()
        scene.ready()
        QMetaObject.invokeMethod(scene.find("previewToolheadCollapse"), "clicked")
        scene.pump()
        self.assertTrue(scene.pane.property("collapsed"))
        self.assertEqual(scene.pane.width(), 38)
        self.assertTrue(scene.find("previewToolheadReopen").property("activeFocus"))
        QTest.keyClick(scene.window, Qt.Key.Key_Space)
        scene.pump()
        self.assertFalse(scene.pane.property("collapsed"))
        self.assertTrue(scene.find("previewToolheadCollapse").property("activeFocus"))

    def test_tab_reveals_the_focused_control_in_a_short_pane(self):
        scene = self.scene(available_height=220)
        scene.ready()
        scene.find("previewToolheadCollapse").forceActiveFocus()
        destination = scene.find("previewToolheadOffsetUp")
        for _ in range(40):
            QTest.keyClick(scene.window, Qt.Key.Key_Tab)
            scene.pump(0.02)
            if destination.property("activeFocus"):
                break
        self.assertTrue(destination.property("activeFocus"), "Tab must reach the offset control")
        scroller = scene.find("previewToolheadScroll")
        top = destination.mapToItem(scroller, QPointF(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + destination.height(), scroller.height())
        self.assertGreater(scroller.property("contentY"), 0)
        self.assertEqual(scene.warnings, [])

    def test_filled_targets_retain_axis_labels_and_offset_selection_is_checked(self):
        scene = self.scene()
        scene.ready()
        for axis in "XYZ":
            target = scene.find("previewToolheadTarget" + axis)
            target.setProperty("text", "100")
            self.assertGreaterEqual(target.width(), 55)
            self.assertEqual(scene.find("previewToolheadTarget" + axis + "Axis").property("text"), axis)
        scene.pane.setProperty("offsetStep", 0.05)
        scene.pump()
        for index in range(3):
            self.assertEqual(scene.find("previewToolheadOffsetStep" + str(index)).property("checked"), index == 2)
        self.assertEqual(scene.warnings, [])

    def test_both_themes_keep_preset_labels_clear_and_status_outside_scroller(self):
        for theme in ("cura-light", "cura-dark"):
            for scale in (1.0, 1.5):
                with self.subTest(theme=theme, scale=scale):
                    scene = self.scene(theme=theme, scale=scale, available_height=220)
                    scene.ready()
                    scene.pane.setProperty("statusText", "Waiting for fresh position and bed clearance data")
                    scene.pump()
                    left = scene.find("previewJogDistanceTick7")
                    right = scene.find("previewJogDistanceTick8")
                    self.assertGreater(right.x() - left.x() - left.width(), 4 * scale)
                    scroller = scene.find("previewToolheadScroll")
                    status = scene.find("previewToolheadStatus")
                    status_top = status.mapToItem(scene.pane, QPointF(0, 0)).y()
                    self.assertGreaterEqual(status_top, scroller.y() + scroller.height())
                    self.assertGreater(scroller.property("contentHeight"), scroller.height())
                    self.assertLessEqual(scene.pane.height(), 220 * scale)
                    self.assertEqual(scene.warnings, [])


if __name__ == "__main__":
    unittest.main()
