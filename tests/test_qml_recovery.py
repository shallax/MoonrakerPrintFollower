# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Share and verify canonical recovery actions across confirmation and execution.
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

"""Recovery review keeps real QML submissions and failures visible."""
import unittest
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtCore import QObject, QVariant, pyqtProperty, pyqtSignal, pyqtSlot

    class RecoveryPrinter(QObject):
        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.submitted = []
            self.bookmarks = []
            self.removed = []
            self.selection = dict(layer=3, completedMotions=1, offset=100, x=10, y=20, z=1)
            self.plan = dict(ready=True, active=False, phase="ready", detail="Ready",
                             unloaded=True, offset=100, resumeLayer=3, resumeMove=2, filename="part.gcode", x=10, y=20, z=1,
                             e=3, absoluteE=True, hotendTarget=215, bedTarget=60, suggestedTopZ=1,
                             currentX=0, currentY=0, currentZ=0, maximumZ=250)

        @pyqtProperty(int, notify=changed)
        def plateLayerCount(self): return 10
        @pyqtProperty(int, notify=changed)
        def plateLayerMotionCount(self): return 100
        @pyqtSlot(int)
        def setFollowerLayerAnchor(self, layer):
            self.selection.update(layer=layer + 1, completedMotions=0, offset=layer * 100, x=10, y=20, z=layer + 1)
            self.changed.emit()
        @pyqtSlot(int)
        def setFollowerLayerProgress(self, split):
            self.selection["completedMotions"] = split
            self.changed.emit()
        @pyqtProperty(QVariant, notify=changed)
        def recoverySelection(self): return dict(self.selection)
        @pyqtProperty(QVariant, notify=changed)
        def recoveryPlan(self): return dict(self.plan)
        @pyqtProperty(QVariant, notify=changed)
        def recoveryEvents(self): return list(self.bookmarks)
        @pyqtSlot(QVariant)
        def removeRecoveryStop(self, row):
            row = row.toVariant() if hasattr(row, "toVariant") else row
            self.removed.append(row)
            self.bookmarks.remove(row)
            self.changed.emit()
        @pyqtProperty(QVariant, notify=changed)
        def recoveryPreviousAttempt(self): return {}
        @pyqtProperty(str, notify=changed)
        def recoveryPauseNote(self): return ""
        @pyqtSlot(QVariant, result=QVariant)
        def reviewRecovery(self, supplied):
            import hashlib
            from mpf.printing.RecoveryReview import review_recovery
            supplied = supplied.toVariant() if hasattr(supplied, 'toVariant') else supplied
            review = review_recovery(self.plan, supplied)
            return dict(review.document(), token=hashlib.sha256(repr(review).encode()).hexdigest())
        @pyqtSlot(QVariant)
        def startRecovery(self, supplied):
            self.submitted.append(supplied.toVariant() if hasattr(supplied, 'toVariant') else supplied)
            self.plan.update(ready=False, phase="refused", detail="The printer reports a changed file.")
            self.changed.emit()


if harness.QT_AVAILABLE:
    class RecoveryFollowerPrinter(harness.PlatePrinterDouble):
        stateChanged = pyqtSignal()
        state = "Printing"

        @pyqtProperty(str, notify=stateChanged)
        def monitorState(self): return self.state


@unittest.skipUnless(harness.QT_AVAILABLE, "Qt required")
class RecoveryReviewTests(harness.RealEngineTestCase):
    def find(self, document, name):
        pending = [document]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        return super().find(document, name)

    def click(self, window, item):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtTest import QTest
        window.grabWindow()
        self.pump(20)
        point = item.mapToItem(window.contentItem(), QPointF(item.width()/2, item.height()/2))
        self.assertTrue(item.isVisible())
        self.assertTrue(item.isEnabled())
        self.assertTrue(0 <= point.x() < window.width() and 0 <= point.y() < window.height(),
                        f"Control outside window: {point}")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        self.pump(20)

    def test_recovery_and_layer_slider_have_independent_live_state_gates(self):
        printer = RecoveryFollowerPrinter()
        document, window = self.mount_window("PrintFollowerPopover.qml", 940, 950)
        document.setProperty("printerModel", printer)
        document.setProperty("open", True)
        for state, count, recover, scrub in (
            ("Printing", 240, False, True), ("Paused", 240, True, True),
            ("Idle", 240, True, True), ("Idle", 0, False, False),
            ("Printing", 0, False, False), ("Paused", 240, True, True),
        ):
            with self.subTest(state=state, count=count):
                printer.state = state
                printer.setIndexState(count > 0, count)
                printer.stateChanged.emit()
                self.pump(20)
                self.assertEqual(self.find(document, "moonrakerFollowerOpenRecovery").isEnabled(), recover)
                self.assertEqual(self.find(document, "moonrakerFollowerLayerSlider").isEnabled(), scrub)

    def test_real_click_opens_confirmation_from_small_overfull_panel(self):
        printer = RecoveryPrinter()
        printer.plan["detail"] = "Long detail " * 100
        document, window = self.mount_window("RecoveryControls.qml", 560, 450)
        document.setProperty("printerModel", printer)
        self.pump(30)
        self.click(window, self.find(document, "moonrakerRecoveryReview"))
        popup = document.findChild(QObject, "moonrakerRecoveryConfirmation")
        self.assertTrue(popup.property("visible"))
        self.assertEqual(printer.submitted, [])

    def test_a_restart_point_can_be_chosen_inside_the_panel_without_a_prior_selection(self):
        printer = RecoveryPrinter()
        printer.selection = {}
        document, window = self.mount_window("RecoveryControls.qml", 560, 800)
        document.setProperty("printerModel", printer)
        self.pump(20)
        picker = self.find(document, "moonrakerRecoveryLayer")
        picker.setProperty("value", 4)
        self.click(window, self.find(document, "moonrakerRecoveryChooseLayer"))
        self.pump(20)
        self.assertEqual(printer.selection["layer"], 4)
        self.assertEqual(printer.selection["completedMotions"], 0)
        self.assertEqual(printer.submitted, [])
        self.find(document, "moonrakerRecoveryMove").setProperty("value", 42)
        self.click(window, self.find(document, "moonrakerRecoveryChooseMove"))
        self.pump(20)
        self.assertEqual(printer.selection["layer"], 4)
        self.assertEqual(printer.selection["completedMotions"], 42)

    def test_late_model_prepopulates_editable_layer_and_move_fields(self):
        document, _window = self.mount_window("RecoveryControls.qml", 560, 450)
        self.pump(10)
        printer = RecoveryPrinter()
        document.setProperty("printerModel", printer)
        self.pump(20)
        self.assertEqual(self.find(document, "moonrakerRecoveryLayer").property("value"), 3)
        self.assertEqual(self.find(document, "moonrakerRecoveryMove").property("value"), 1)
        printer.selection.update(layer=8, completedMotions=42)
        printer.changed.emit()
        self.pump(20)
        self.assertEqual(self.find(document, "moonrakerRecoveryLayer").property("value"), 8)
        self.assertEqual(self.find(document, "moonrakerRecoveryMove").property("value"), 42)
        self.assertEqual(printer.submitted, [])

    def test_confirmation_explains_coordinates_dwell_and_descent_in_order(self):
        printer = RecoveryPrinter()
        printer.plan.update(primeEnabled=True, primeX=0, primeY=200, primeZ=10,
                            primeAmount=20, primeSpeed=2, primeDwell=12.5,
                            clearance=5, currentZ=50, zSpeed=5, xySpeed=50)
        document, window = self.mount_window("RecoveryControls.qml", 560, 450)
        document.setProperty("printerModel", printer)
        self.pump(20)
        self.click(window, self.find(document, "moonrakerRecoveryReview"))
        text = self.find(document, "moonrakerRecoverySteps").property("text")
        phrases = ["Heat and wait", "Z50.00", "X0.00 Y200.00", "Lower to Z10.00",
                   "Purge 20.00", "dwell for 12.50 s", "X10.00 Y20.00 at Z10.00", "Lower to Z6.00",
                   "Lower to Z1.00", "about 1.00 s", "resume printing on layer 3 at move 2 (1 completed moves on that layer; byte 100)"]
        previous = -1
        for phrase in phrases:
            index = text.index(phrase, previous + 1)
            self.assertGreater(index, previous)
            previous = index
        printer.plan.update(primeEnabled=False, clearance=12, skipsForward=True)
        printer.changed.emit()
        self.pump(10)
        text = self.find(document, "moonrakerRecoverySteps").property("text")
        self.assertNotIn("Purge", text)
        self.assertNotIn("dwell", text)
        self.assertIn("skips the intervening toolpath", text)
        self.assertIn("Z13.00", text)
        self.assertIn("about 2.40 s", text)

    def test_confirmation_caps_prime_and_travel_at_reported_ceiling(self):
        printer = RecoveryPrinter()
        printer.plan.update(primeEnabled=True, primeX=0, primeY=200, primeZ=100,
                            primeAmount=20, primeSpeed=2, z=248, maximumZ=250,
                            clearance=5, currentZ=50, zSpeed=5, xySpeed=50)
        document, window = self.mount_window("RecoveryControls.qml", 560, 450)
        document.setProperty("printerModel", printer)
        self.pump(20)
        self.click(window, self.find(document, "moonrakerRecoveryReview"))
        text = self.find(document, "moonrakerRecoverySteps").property("text")
        self.assertIn("Lower to Z250.00", text)
        self.assertIn("Lower to Z248.00", text)
        self.assertIn("about 0.40 s", text)
        self.assertNotIn("Z253", text)
        printer.plan.update(primeZ=249)
        printer.changed.emit()
        self.pump(10)
        self.assertIn("Lower to Z249.00", self.find(document, "moonrakerRecoverySteps").property("text"))

    def test_preview_returns_at_purge_height_before_lowering_or_lifts_to_clearance(self):
        from PyQt6.QtQml import QQmlEngine, QQmlExpression
        printer = RecoveryPrinter()
        printer.plan.update(primeEnabled=True, primeX=0, primeY=200, primeZ=100,
                            primeAmount=20, primeSpeed=2, clearance=5, currentZ=50)
        document, _ = self.mount_window("RecoveryControls.qml", 560, 450)
        document.setProperty("printerModel", printer)
        self.pump(20)
        expression = QQmlExpression(QQmlEngine.contextForObject(document), document,
                                    "recoveryActions()")
        for height, expected in ((100, [[10, 20, 100], [10, 20, 6]]),
                                 (3, [[0, 200, 6], [10, 20, 6]]),
                                 (6, [[10, 20, 6], [10, 20, 6]])):
            with self.subTest(prime_z=height):
                printer.plan.update(primeZ=height)
                printer.changed.emit()
                self.pump(10)
                value, undefined = expression.evaluate()
                self.assertFalse(expression.hasError(), expression.error().toString())
                self.assertFalse(undefined)
                actions = value.toVariant() if hasattr(value, "toVariant") else value
                purge = next(i for i, action in enumerate(actions) if action["text"].startswith("Purge"))
                moves = actions[purge + 1:purge + 3]
                self.assertEqual([action["position"] for action in moves], expected)
                self.assertEqual(moves[0]["from"], [0, 200, height])
                self.assertEqual(moves[1]["from"], expected[0])

    def test_bookmark_use_and_remove_do_not_start_printing(self):
        printer = RecoveryPrinter()
        row = dict(kind="manual", layer=2, split=7, observed_at=123)
        printer.bookmarks = [row]
        document, window = self.mount_window("RecoveryControls.qml", 560, 1000)
        document.setProperty("printerModel", printer)
        self.pump(20)
        self.click(window, self.find(document, "recoveryUseBookmark"))
        self.assertEqual((printer.selection["layer"], printer.selection["completedMotions"]), (3, 7))
        self.assertEqual(printer.bookmarks, [row])
        self.click(window, self.find(document, "recoveryRemoveBookmark"))
        self.assertEqual(printer.removed, [row])
        self.assertEqual(printer.bookmarks, [])
        self.assertEqual((printer.selection["layer"], printer.selection["completedMotions"]), (3, 7))
        self.assertEqual(printer.submitted, [])

    def test_step_preview_draws_print_context_and_updates_nozzle_coordinates(self):
        document, window = self.mount_window("RecoveryStepPreview.qml", 560, 700)
        actions = [
            dict(text="Heat at the parked position", position=[220, 220, 20], **{"from": [220, 220, 20]}),
            dict(text="Travel above the restart point", position=[120, 125, 15], **{"from": [220, 220, 15]}),
            dict(text="Descend, then immediately resume layer 50 at move 301", position=[120, 125, 10], **{"from": [120, 125, 15]})]
        segments = [[100, 100, z, 140, 100, z] for z in range(1, 11)]
        segments += [[140, 100, z, 140, 140, z] for z in range(1, 11)]
        segments += [[140, 140, z, 100, 140, z] for z in range(1, 11)]
        segments += [[100, 140, z, 100, 100, z] for z in range(1, 11)]
        # Use the production scanner and publication, not a hand-built QML list.
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from mpf.application.RecoveryController import RecoveryController
        from mpf.application.RecoveryPlanning import scan_source
        lines = ["G90", "M83", "G1 X100 Y100 Z0 F1800"]
        for z in range(1, 11):
            lines += [f"G1 Z{z}", "G1 X140 Y100 E1", "G1 X140 Y140 E1",
                      "G1 X100 Y140 E1", "G1 X100 Y100 E1"]
        prefix = ("\n".join(lines) + "\n").encode()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cube.gcode"
            path.write_bytes(prefix + b"G1 X120 E1\n")
            source = scan_source(path, SimpleNamespace(next_motion_offset=len(prefix), position=(100, 100, 10)))
        printer = RecoveryPrinter()
        controller = RecoveryController(SimpleNamespace(sessionInvalidated=printer.changed), lambda: None)
        self.addCleanup(controller.close)
        controller._source = source
        segments = controller.state["previewSegments"]
        self.assertGreater(len(segments), 30)
        self.assertIsInstance(segments, list)
        self.assertIsInstance(segments[0], list)
        from PyQt6.QtQml import QQmlEngine, QQmlExpression
        printer.plan.update(previewSegments=segments)
        context = QQmlEngine.contextForObject(document).engine().rootContext()
        context.setContextProperty("geometryPrinter", printer)
        expression = QQmlExpression(context, document,
            "geometryPrinter.recoveryPlan.previewSegments.length > 30 && "
            "geometryPrinter.recoveryPlan.previewSegments[0].slice(0, 3).length === 3")
        result, undefined = expression.evaluate()
        self.assertFalse(expression.hasError(), expression.error().toString())
        self.assertFalse(undefined)
        self.assertTrue(result)

        document.setProperty("actions", actions)
        document.setProperty("segments", segments)
        document.setProperty("restartPosition", [120, 125, 10])
        document.setProperty("bedBounds", [0, 0, 0, 250, 250])
        document.setProperty("caption", "Recorded G-code progress; actual material may be missing")
        picker = self.find(document, "recoveryPreviewStep")
        for index, action in enumerate(actions, 1):
            picker.setProperty("value", index)
            self.pump(20)
            self.assertEqual(self.find(document, "recoveryPreviewDescription").property("text"), action["text"])
            coordinates = self.find(document, "recoveryPreviewCoordinates").property("text")
            for axis, value in zip("XYZ", action["position"], strict=True):
                self.assertIn(f"{axis}{value:.2f}", coordinates)
            self.assertEqual(document.property("segments"), segments)
        frame = window.grabWindow()
        self.assertFalse(frame.isNull())
        from PyQt6.QtCore import QPointF
        plot = self.find(document, "recoveryPreviewPlot")
        origin = plot.mapToItem(document, QPointF(0, 0))
        # Each actual projection must contain visible printed-material pixels.
        ratio = frame.devicePixelRatio()
        for row in range(2):
            for column in range(2):
                count = 0
                bed_count = 0
                axis_counts = [0, 0, 0]
                for y in range(int((origin.y() + row * plot.height() / 2) * ratio),
                               int((origin.y() + (row + 1) * plot.height() / 2) * ratio)):
                    for x in range(int((origin.x() + column * plot.width() / 2) * ratio),
                                   int((origin.x() + (column + 1) * plot.width() / 2) * ratio)):
                        color = frame.pixelColor(x, y)
                        if abs(color.red() - 87) < 12 and abs(color.green() - 156) < 12 and abs(color.blue() - 131) < 12:
                            count += 1
                        if abs(color.red() - 119) < 12 and abs(color.green() - 130) < 12 and abs(color.blue() - 142) < 12:
                            bed_count += 1
                        for axis, rgb in enumerate(((239, 83, 80), (102, 187, 106), (66, 165, 245))):
                            if all(abs(actual - expected) < 10 for actual, expected in zip((color.red(), color.green(), color.blue()), rgb, strict=True)):
                                axis_counts[axis] += 1
                hidden_axis = {(0, 1): 2, (1, 0): 1, (1, 1): 0}.get((row, column))
                for axis, pixels in enumerate(axis_counts):
                    if axis == hidden_axis:
                        self.assertEqual(pixels, 0, (row, column, "XYZ"[axis], "depth axis visible"))
                    else:
                        self.assertGreater(pixels, 5, (row, column, "XYZ"[axis], "axis missing"))
                self.assertGreater(bed_count, 30, (row, column, "bed outline missing"))
                self.assertGreater(count, 30, (row, column, "printed model missing"))

        import os
        if os.environ.get("MPF_RECOVERY_PREVIEW_CAPTURE"):
            frame.save(os.environ["MPF_RECOVERY_PREVIEW_CAPTURE"])

    def test_long_ready_plan_keeps_review_inside_small_panel(self):
        from PyQt6.QtCore import QPointF
        printer = RecoveryPrinter()
        printer.plan.update(detail="Long recovery details " * 80, primeEnabled=True,
                            primeX=0, primeY=250, primeZ=100, primeAmount=50, primeSpeed=5)
        document, window = self.mount_window("RecoveryControls.qml", 560, 450)
        document.setProperty("printerModel", printer)
        self.pump(30)
        review = self.find(document, "moonrakerRecoveryReview")
        position = review.mapToItem(document, QPointF(0, 0))
        self.assertGreaterEqual(position.y(), 0)
        self.assertLessEqual(position.y() + review.height(), document.height())
        self.assertTrue(review.isEnabled())

    def test_dismiss_after_submission_closes_both_modals_and_keeps_follower_open(self):
        follower = RecoveryFollowerPrinter()
        follower.state = "Paused"
        follower.setIndexState(True, 240)
        document, window = self.mount_window("PrintFollowerPopover.qml", 940, 950)
        document.setProperty("printerModel", follower)
        document.setProperty("open", True)
        self.pump(20)
        self.click(window, self.find(document, "moonrakerFollowerOpenRecovery"))
        dialog = document.findChild(QObject, "moonrakerRecoveryDialog")
        controls = dialog.property("contentItem")
        printer = RecoveryPrinter()
        controls.setProperty("printerModel", printer)
        self.pump(20)
        self.click(window, self.find(controls, "moonrakerRecoveryReview"))
        confirmation = controls.findChild(QObject, "moonrakerRecoveryConfirmation")
        def scroll_bottom():
            scroll = self.find(controls, "moonrakerRecoveryConfirmationScroll").property("contentItem")
            scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.height()))
            self.pump(20)
        self.find(controls, "recoveryPreviewStep").setProperty("value", 3)
        scroll_bottom()
        back = self.find(controls, "moonrakerRecoveryDismiss")
        self.assertEqual(back.property("text"), "Back")
        self.click(window, back)
        self.assertFalse(confirmation.property("visible"))
        self.assertTrue(dialog.property("visible"))
        self.click(window, self.find(controls, "moonrakerRecoveryReview"))
        self.assertEqual(self.find(controls, "recoveryPreviewStep").property("value"), 1)
        self.assertEqual(self.find(controls, "moonrakerRecoveryConfirmationScroll").property("contentItem").property("contentY"), 0)
        scroll_bottom()
        self.click(window, self.find(controls, "moonrakerRecoveryReviewed"))
        self.click(window, self.find(controls, "moonrakerRecoveryStart"))
        # Also retain Dismiss while an asynchronous handover is active.
        printer.plan.update(active=True, phase="returning", detail="Returning")
        printer.changed.emit()
        self.pump(20)
        scroll_bottom()
        self.assertEqual(back.property("text"), "Dismiss")
        self.click(window, back)
        self.assertFalse(confirmation.property("visible"))
        self.assertFalse(dialog.property("visible"))
        self.assertTrue(document.property("open"))
        self.assertEqual(len(printer.submitted), 1)
        self.assertTrue(printer.plan["active"])
        main_scroll = self.find(controls, "moonrakerRecoveryScroll").property("contentItem")
        main_scroll.setProperty("contentY", 100)
        self.find(controls, "recoveryPreviewStep").setProperty("value", 3)
        self.click(window, self.find(document, "moonrakerFollowerOpenRecovery"))
        self.pump(20)
        self.assertTrue(dialog.property("visible"))
        self.assertEqual(main_scroll.property("contentY"), 0)
        self.assertEqual(self.find(controls, "recoveryPreviewStep").property("value"), 1)
        self.assertTrue(printer.plan["active"])


    def test_submission_and_refusal_stay_in_the_review_with_unchanged_selection(self):
        for height in (450, 800):
            with self.subTest(height=height):
                self.assert_submission_and_refusal(height)

    def assert_submission_and_refusal(self, height):
        printer = RecoveryPrinter()
        document, window = self.mount_window("RecoveryControls.qml", 560, height)
        document.setProperty("printerModel", printer)
        self.pump(20)
        review = self.find(document, "moonrakerRecoveryReview")
        self.assertTrue(review.isEnabled())
        self.click(window, review)
        self.pump(20)
        popup = document.findChild(QObject, "moonrakerRecoveryConfirmation")
        self.assertTrue(popup.property("visible"))
        # Publishing another plan map does not change the selected point.
        printer.changed.emit()
        self.pump(10)
        self.assertTrue(popup.property("visible"))
        check = self.find(document, "moonrakerRecoveryReviewed")
        window.grabWindow()
        self.pump(20)
        scroll = self.find(document, "moonrakerRecoveryConfirmationScroll").property("contentItem")
        scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.height()))
        self.pump(20)
        self.click(window, check)
        start = self.find(document, "moonrakerRecoveryStart")
        self.assertTrue(start.isEnabled())
        self.click(window, start)
        self.pump(20)
        self.assertEqual(len(printer.submitted), 1)
        self.assertTrue(printer.submitted[0].pop("reviewToken"))
        self.assertEqual(printer.submitted[0], {"hotend": 215, "bed": 60, "zReference": 0})
        self.assertTrue(popup.property("visible"))
        status = self.find(document, "moonrakerRecoveryAttemptStatus")
        self.assertEqual(status.property("text"), "The printer reports a changed file.")
        self.assertTrue(status.isVisible())
        self.assertFalse(start.isEnabled())
        # A real change of point invalidates the previous confirmation.
        printer.selection["offset"] = 120
        printer.changed.emit()
        self.pump(10)
        self.assertFalse(popup.property("visible"))
