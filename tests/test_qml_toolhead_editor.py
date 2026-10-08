"""Production Cura editor: scrollbar gutter, themed intensity and draft cancellation."""
from __future__ import annotations

import copy
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import time

from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtCore import Qt, Q_ARG
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
        self.find("toolheadEditorSection").setProperty("currentIndex", 1)
        self.dialog.grabWindow()
        self.pump()

    def find(self, name):
        # Repeater delegates have visual ownership, not QObject parentage.
        pending = [self.dialog.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name: return item
            pending.extend(item.childItems())
        self.fail("Missing editor item: " + name)

    def _other_owner(self):
        config = SimpleNamespace(toolhead_model='', toolhead_model_name='',
                                 toolhead_tip=[], toolhead_lights=[])
        other = ToolheadModels(ToolheadAssetStore(self.directory.name),
                               self.directory.name, lambda: config,
                               lambda: 'synthetic-other-printer')
        self.addCleanup(other.close)
        # Restore the original owner before the parent dialog cleanup runs.
        self.addCleanup(self.dialog.setProperty, 'model', self.draft)
        return other

    def _reveal_and_nudge(self, slider):
        scroll = self.find('toolheadAppearanceScroll')
        point = slider.mapToItem(scroll.property('contentItem'), harness.QPointF())
        scroll.setProperty('contentY', max(0, min(
            scroll.property('contentHeight') - scroll.height(), point.y() - 100)))
        self.dialog.grabWindow()
        self.pump(20)
        slider.forceActiveFocus()
        QTest.keyClick(self.dialog, Qt.Key.Key_Right)
        self.assertTrue(slider.property('interacting'))

    def test_equal_valued_owner_rebind_cancels_and_resynchronises(self):
        self.find('toolheadEditorSection').setProperty('currentIndex', 0)
        other = self._other_owner()
        for owner in (self.draft, other):
            owner.toggleOpacitySelection(0)
            owner.setSelectedFinish('roughness', .8)
            owner.setSelectedFinish('reflectivity', .75)
            owner.setSelectedOpacity(.8)
            kind = owner.materialTypes[0]['kind']
            owner.setMaterialFinish(kind, 'roughness', .8)
            owner.setMaterialFinish(kind, 'reflectivity', .75)
        for name in ('toolheadSelectedroughness', 'toolheadSelectedreflectivity',
                     'toolheadSelectedOpacity', 'toolheadMaterialroughness',
                     'toolheadMaterialreflectivity'):
            with self.subTest(slider=name):
                self.dialog.setProperty('model', self.draft)
                self._pump_ms(60)
                slider = self.find(name)
                original = slider.property('modelValue')
                before = copy.deepcopy(other.fields())
                self._reveal_and_nudge(slider)
                self.assertGreater(slider.property('value'), original)
                # The new owner has the SAME value and SAME selected IDs.
                # Only editOwner changes, so no other binding can mask failure.
                self.dialog.setProperty('model', other)
                self._pump_ms(400)
                self.assertFalse(slider.property('interacting'))
                self.assertAlmostEqual(slider.property('value'), original, places=4)
                self.assertAlmostEqual(slider.property('value'),
                                       slider.property('modelValue'), places=4)
                self.assertEqual(other.fields(), before)

    def test_surface_detail_pending_key_does_not_apply_to_new_owner(self):
        self.find('toolheadEditorSection').setProperty('currentIndex', 0)
        other = self._other_owner()
        self.draft.setSurfaceDetail(.35)
        other.setSurfaceDetail(.35)
        self._pump_ms(60)
        slider = self.find('toolheadSurfaceDetail')
        before = copy.deepcopy(other.fields())
        self._reveal_and_nudge(slider)
        self.assertAlmostEqual(self.draft.surfaceDetail, .36, places=4)
        self.assertAlmostEqual(other.surfaceDetail, .35, places=4)
        self.dialog.setProperty('model', other)
        self._pump_ms(400)
        self.assertFalse(slider.property('interacting'))
        self.assertAlmostEqual(other.surfaceDetail, .35, places=4)
        self.assertAlmostEqual(slider.property('value'), 35, places=4)
        self.assertAlmostEqual(slider.property('value'),
                               slider.property('modelValue'), places=4)
        self.assertEqual(other.fields(), before)


    def test_held_groove_gesture_cannot_write_replacement_owner(self):
        self.find('toolheadEditorSection').setProperty('currentIndex',0)
        other=self._other_owner()
        for owner in (self.draft,other):
            owner.toggleOpacitySelection(0);owner.setSelectedFinish('roughness',.8)
        self._pump_ms(60)
        slider=self.find('toolheadSelectedroughness')
        scroll=self.find('toolheadAppearanceScroll')
        point=slider.mapToItem(scroll.property('contentItem'),harness.QPointF())
        scroll.setProperty('contentY',max(0,min(scroll.property('contentHeight')-scroll.height(),point.y()-100)))
        self.dialog.grabWindow();self.pump(20)
        press=slider.mapToScene(harness.QPointF(slider.width()*.15,slider.height()/2)).toPoint()
        move=slider.mapToScene(harness.QPointF(slider.width()*.4,slider.height()/2)).toPoint()
        before=copy.deepcopy(other.fields())
        QTest.mousePress(self.dialog,Qt.MouseButton.LeftButton,pos=press)
        QTest.mouseMove(self.dialog,move)
        self.pump(2)
        self.dialog.setProperty('model',other);self.pump(2)
        move=slider.mapToScene(harness.QPointF(slider.width()*.55,slider.height()/2)).toPoint()
        QTest.mouseMove(self.dialog,move);self.pump(2)
        QTest.mouseRelease(self.dialog,Qt.MouseButton.LeftButton,pos=move)
        self._pump_ms(400)
        self.assertEqual(other.fields(),before,'Old native groove gesture wrote replacement owner')
        self.assertEqual(slider.property("value"),slider.property("modelValue"))

    def _reveal_appearance(self, item, offset=140):
        scroll = self.find('toolheadAppearanceScroll')
        p = item.mapToItem(scroll.property('contentItem'), harness.QPointF())
        scroll.setProperty('contentY', max(0, min(
            scroll.property('contentHeight') - scroll.height(), p.y() - offset)))
        self.dialog.grabWindow()
        self.pump(20)


    def _nudge_appearance(self, slider):
        self._reveal_appearance(slider)
        slider.forceActiveFocus()
        QTest.keyClick(self.dialog, Qt.Key.Key_Right)
        self.assertTrue(slider.property('interacting'))


    def _click_appearance(self, item):
        self._reveal_appearance(item)
        p = item.mapToScene(harness.QPointF(item.width()/2, item.height()/2)).toPoint()
        QTest.mouseClick(self.dialog, Qt.MouseButton.LeftButton, pos=p)
        self.pump(20)


    def test_pending_nudges_cancel_on_reset_selection_and_profile_changes(self):
        """Eight subcases: 3 immediate mouse resets, 3 target switches, 2 profiles."""
        self.find('toolheadEditorSection').setProperty('currentIndex', 0)
        draft = self.draft
        draft.toggleOpacitySelection(0)
        for name, reset, field in (
            ('toolheadSelectedroughness', 'toolheadSelectedAutoroughness', 'roughness'),
            ('toolheadSelectedreflectivity', 'toolheadSelectedAutoreflectivity', 'reflectivity'),
            ('toolheadSelectedOpacity', 'toolheadResetOpacity', 'opacity'),
        ):
            with self.subTest(immediate_reset=field):
                if field == 'opacity': draft.setSelectedOpacity(.8)
                else: draft.setSelectedFinish(field, .8)
                self._pump_ms(60)
                slider = self.find(name)
                self._nudge_appearance(slider)
                self._click_appearance(self.find(reset))
                immediate = slider.property('value')
                self._pump_ms(400)
                self.assertAlmostEqual(slider.property('value'),
                                       slider.property('modelValue'), places=4)
                self.assertAlmostEqual(slider.property('value'), immediate, places=4)
                self.assertIn(draft.selectedSources[field], ('Automatic', 'Imported'))
                self.assertFalse(slider.property('interacting'))
        for name, field in (
            ('toolheadSelectedroughness', 'roughness'),
            ('toolheadSelectedreflectivity', 'reflectivity'),
            ('toolheadSelectedOpacity', 'opacity'),
        ):
            with self.subTest(target_switch=field):
                draft.clearOpacitySelection()
                draft.selectOpacityKind('body')
                draft.toggleOpacitySelection(0)
                if field == 'opacity': draft.setSelectedOpacity(.8)
                else: draft.setSelectedFinish(field, .8)
                self._pump_ms(60)
                slider = self.find(name)
                self._nudge_appearance(slider)
                draft.clearOpacitySelection()
                draft.selectOpacityKind('face')
                draft.toggleOpacitySelection(0)
                self.pump(20)
                before = copy.deepcopy((draft.faceFinishes, draft.faceOpacity))
                self._pump_ms(400)
                self.assertEqual((draft.faceFinishes, draft.faceOpacity), before)
                self.assertAlmostEqual(slider.property('value'),
                                       slider.property('modelValue'), places=4)
        combo = self.find('toolheadMaterialType')
        for field in ('roughness', 'reflectivity'):
            with self.subTest(profile_switch=field):
                combo.setProperty('currentIndex', 0)
                self._pump_ms(60)
                slider = self.find('toolheadMaterial' + field)
                self._nudge_appearance(slider)
                before = copy.deepcopy(draft._material_overrides)
                combo.forceActiveFocus()
                # Native ComboBox keyboard input changes the actual profile.
                # Do not render/scroll between the nudge and this switch: a
                # slow grabWindow can legitimately deliver the old profile's
                # 250ms commit before any target change has been requested.
                QTest.keyClick(self.dialog, Qt.Key.Key_Down, delay=0)
                self.assertFalse(slider.property('interacting'))
                self.pump(20)
                self.assertEqual(combo.property('currentIndex'), 1)
                self._pump_ms(400)
                self.assertEqual(draft._material_overrides, before)
                self.assertAlmostEqual(slider.property('value'),
                                       slider.property('modelValue'), places=4)


    def test_dragged_appearance_sliders_resolve_automatic_and_imported_values(self):
        """Three native drags break the value binding before real reset clicks."""
        self.find('toolheadEditorSection').setProperty('currentIndex', 0)
        self.draft.toggleOpacitySelection(0)
        self._pump_ms(80)
        for name, reset, field in (
            ('toolheadSelectedroughness', 'toolheadSelectedAutoroughness', 'roughness'),
            ('toolheadSelectedreflectivity', 'toolheadSelectedAutoreflectivity', 'reflectivity'),
            ('toolheadSelectedOpacity', 'toolheadResetOpacity', 'opacity'),
        ):
            with self.subTest(native_drag_then_reset=field):
                slider = self.find(name)
                self._reveal_appearance(slider, offset=100)
                handle = slider.property('handle')
                start = handle.mapToScene(harness.QPointF(
                    handle.width()/2, handle.height()/2)).toPoint()
                end = slider.mapToScene(harness.QPointF(
                    slider.property('leftPadding') + .72*slider.property('availableWidth'),
                    slider.height()/2)).toPoint()
                QTest.mousePress(self.dialog, Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(self.dialog, end, 20)
                QTest.mouseRelease(self.dialog, Qt.MouseButton.LeftButton, pos=end)
                self._pump_ms(80)
                self.assertGreater(slider.property('value'), 65)
                self.assertLess(slider.property('value'), 80)
                self._click_appearance(self.find(reset))
                self._pump_ms(80)
                self.assertAlmostEqual(slider.property('value'),
                                       slider.property('modelValue'), places=4)
        # Idle target update must still follow the model after a native drag.
        self.draft.selectOpacityKind('face')
        self.draft.toggleOpacitySelection(0)
        self._pump_ms(80)
        for name in ('toolheadSelectedroughness', 'toolheadSelectedreflectivity',
                     'toolheadSelectedOpacity'):
            slider = self.find(name)
            self.assertAlmostEqual(slider.property('value'),
                                   slider.property('modelValue'), places=4)

    def assert_always_visible(self, bar):
        expression = QQmlExpression(qmlContext(bar), bar, "policy === 2")
        self.assertEqual(expression.evaluate(), (True, False))

    def test_colour_picker_accept_reject_reset_and_cancel_are_transactional(self):
        from PyQt6.QtGui import QColor
        self.find("toolheadEditorSection").setProperty("currentIndex", 0)
        self.draft.toggleOpacitySelection(0)
        self.pump()
        scroll = self.find("toolheadAppearanceScroll")
        choose = self.find("toolheadChooseColour")
        self.assertTrue(choose.isEnabled())
        harness.QMetaObject.invokeMethod(choose, "clicked")
        self.pump()
        picker = scroll.property("colourDialog")
        self.assertIsNotNone(picker)
        self.assertTrue(self.draft.colourChoiceActive)
        picker.setProperty("selectedColor", QColor("#12ab34"))
        harness.QMetaObject.invokeMethod(picker, "accepted")
        self.pump()
        self.assertEqual(self.draft.bodyColours, {'0': '#12ab34'})
        self.assertFalse(self.draft.colourChoiceActive)
        self.assertIn('#12AB34', self.find('toolheadSelectedColourLabel').property('text'))
        self.assertEqual(self.find('toolheadSelectedColourSwatch').property('color'), QColor('#12ab34'))
        self.assertFalse(self.find('toolheadModelPreview').property('picking'))
        harness.QMetaObject.invokeMethod(choose, "clicked")
        self.pump()
        self.assertEqual(picker.property('selectedColor').name(), '#12ab34')
        picker.setProperty('selectedColor', QColor('#ff0000'))
        harness.QMetaObject.invokeMethod(picker, 'rejected')
        self.pump()
        self.assertEqual(self.draft.bodyColours, {'0': '#12ab34'})
        harness.QMetaObject.invokeMethod(self.find('toolheadRestoreColour'), 'clicked')
        self.pump()
        self.assertEqual(self.draft.bodyColours, {})
        harness.QMetaObject.invokeMethod(choose, 'clicked')
        self.pump()
        self.draft.endEdit(False)
        self.pump()
        self.assertFalse(picker.property('visible'))
        self.assertFalse(self.draft.acceptColourChoice('#ffffff'))

    def test_selected_body_finish_keyboard_and_imported_opacity_reset(self):
        self.find("toolheadEditorSection").setProperty("currentIndex", 0)
        self.draft.toggleOpacitySelection(0)
        self.pump()
        slider = self.find("toolheadSelectedroughness")
        before = self.draft.selectedRoughness
        scroll = self.find("toolheadAppearanceScroll")
        point = slider.mapToItem(scroll.property("contentItem"), harness.QPointF(0, 0))
        scroll.setProperty("contentY", max(0, point.y()-100))
        self.dialog.grabWindow()
        self.pump()
        self.assertTrue(slider.isEnabled())
        label=self.find("toolheadSelectedLabelroughness")
        reset=self.find("toolheadSelectedAutoroughness")
        right=reset.mapToItem(scroll,harness.QPointF(reset.width(),0)).x()
        self.assertLessEqual(right,scroll.width())
        self.assertGreater(label.height(),20, 'source provenance wraps beside the reset control')
        slider.forceActiveFocus()
        self.assertTrue(slider.hasActiveFocus())
        QTest.keyClick(self.dialog, Qt.Key.Key_Right)
        deadline = time.monotonic() + 3
        while self.draft.selectedRoughness == before and time.monotonic() < deadline:
            QTest.qWait(10)
        self.pump()
        self.assertAlmostEqual(self.draft.selectedRoughness, round(before*100+1)/100, places=6)
        self.draft.setSelectedOpacity(.2)
        self.draft.clearOpacitySelection()
        self.draft.selectOpacityKind("face")
        self.draft.toggleOpacitySelection(0)
        self.draft.resetSelectedOpacity()
        self.pump()
        self.assertEqual(self.find("toolheadOpacityPickKind").property("currentIndex"), 1)
        self.assertEqual(self.draft.selectedOpacity, 1.)
        self.assertEqual(self.find('toolheadSelectedOpacity').property('value'),100.)
        # Safe synthetic editor capture: no CuraApplication or printer.
        import os
        folder = os.environ.get("MPF_APPEARANCE_RENDER_DIR")
        if folder:
            from pathlib import Path
            Path(folder).mkdir(parents=True, exist_ok=True)
            self.dialog.grabWindow().save(str(Path(folder)/"appearance-selected-parts.png"))

    def test_clear_body_and_face_selections_preserves_edits_and_selection_mode(self):
        self.find("toolheadEditorSection").setProperty("currentIndex", 0)
        self.draft.toggleOpacitySelection(0)
        self.draft.setSelectedColour("#12ab34")
        self.draft.setSelectedOpacity(.4)
        self.draft.setSelectedFinish("roughness", .7)
        self.draft.setSelectedMaterial("petg")
        self.draft.selectOpacityKind("face")
        self.draft.toggleOpacitySelection(0)
        self.draft.setSelectedColour("#aabbcc")
        self.find("toolheadModelPreview").setProperty("selectingOpacity", True)
        before = self.draft.fields()
        self.pump()
        clear = self.find("toolheadClearOpacitySelection")
        material = self.find("toolheadSelectedMaterial")
        self.assertTrue(clear.isEnabled())
        self.assertLess(clear.y(), material.y())
        scroll = self.find("toolheadAppearanceScroll")
        point = clear.mapToItem(scroll.property("contentItem"), harness.QPointF())
        scroll.setProperty("contentY", max(0, point.y() - 100))
        self.dialog.grabWindow()
        self.pump()
        click = clear.mapToScene(harness.QPointF(clear.width()/2, clear.height()/2)).toPoint()
        QTest.mouseClick(self.dialog, Qt.MouseButton.LeftButton, pos=click)
        self.pump()
        self.assertEqual(self.draft.opacitySelectionCount, 0)
        self.assertEqual(self.draft.opacityBodies, [])
        self.assertEqual(self.draft.opacityFaces, [])
        self.assertEqual(self.draft.fields(), before)
        self.assertEqual(self.draft.opacityKind, "face")
        self.assertTrue(self.find("toolheadModelPreview").property("selectingOpacity"))
        self.assertFalse(clear.isEnabled())
        self.assertTrue(clear.isVisible())
        self.assertFalse(self.find("toolheadChooseColour").isEnabled())

    def test_body_only_material_paint_enables_restore_all_and_success_clears_old_error(self):
        self.find("toolheadEditorSection").setProperty("currentIndex",0)
        self.draft._status="This edit exceeds the limit; nothing changed."
        self.draft.selectMaterialPaint("petg")
        self.draft.paintBody(0)
        self.pump()
        self.assertEqual(self.draft.status,"")
        self.assertEqual(self.draft.paintedBodyCount,1)
        self.assertEqual(self.draft.paintedFaceCount,0)
        self.assertTrue(self.find("toolheadResetPaintMaterials").isEnabled())
        self.draft.clearMaterialPaint();self.pump()
        self.assertFalse(self.find("toolheadResetPaintMaterials").isEnabled())

    def test_surface_detail_keyboard_preserves_intermediate_strengths(self):
        self.find("toolheadEditorSection").setProperty("currentIndex", 0)
        self.pump()
        slider = self.find("toolheadSurfaceDetail")
        self.assertAlmostEqual(slider.property("value"), 35)
        slider.forceActiveFocus()
        QTest.keyClick(self.dialog, Qt.Key.Key_Right)
        self.pump()
        self.assertAlmostEqual(self.draft.surfaceDetail, .36)
        self.assertAlmostEqual(slider.property("value"), 36)
        QTest.keyClick(self.dialog, Qt.Key.Key_Left)
        self.pump()
        self.assertAlmostEqual(self.draft.surfaceDetail, .35)

    def test_fan_settings_scroll_without_overlapping_fixed_footer_at_minimum_size(self):
        self.dialog.setWidth(760)
        self.dialog.setHeight(520)
        self.find("toolheadEditorSection").setProperty("currentIndex", 2)
        self.draft.pickedBody(0)
        self.pump()
        self.dialog.grabWindow()
        scroll = self.find("toolheadFansScroll")
        self.assertTrue(scroll.property("clip"))
        self.assertGreater(scroll.property("contentHeight"), scroll.height())
        self.assertLessEqual(scroll.mapToScene(harness.QPointF(0,scroll.height())).y(), 450)
        self.find("toolheadRotorCentreX").setProperty("text", "8")
        harness.QMetaObject.invokeMethod(self.find("toolheadUpdateRotorPreview"), "clicked")
        self.pump()
        self.assertEqual(self.draft.rotorCandidate['centre'][0],8.)
        self.assertEqual(self.draft.rotors, [])

    def test_fan_confirmation_collects_axis_direction_and_actual_fan_without_commands(self):
        self.find("toolheadEditorSection").setProperty("currentIndex", 2)
        self.draft.pickedBody(0)
        self.draft.setFanReadings({'fan': dict(available=True, speed=.5)})
        self.pump()
        self.find("toolheadRotorAxisX").setProperty("text", "0")
        self.find("toolheadRotorAxisY").setProperty("text", "1")
        self.find("toolheadRotorAxisZ").setProperty("text", "0")
        self.find("toolheadRotorRPM").setProperty("text", "1200")
        self.find("toolheadRotorDirection").setProperty("currentIndex", 1)
        self.find("toolheadRotorFan").setProperty("currentIndex", 1)
        harness.QMetaObject.invokeMethod(self.find("toolheadRotorFan"), "activated", Q_ARG(int,1))
        harness.QMetaObject.invokeMethod(self.find("toolheadConfirmRotor"), "clicked")
        self.pump()
        self.assertEqual(self.draft.rotors[0]['axis'], [0.,1.,0.])
        self.assertEqual(self.draft.rotors[0]['direction'], -1)
        self.assertEqual(self.draft.rotors[0]['fan'], 'fan')
        self.assertIn('600 RPM', self.draft.rotorReadout)
        self.find("toolheadRotorAxisY").setProperty("text", "0")
        harness.QMetaObject.invokeMethod(self.find("toolheadConfirmRotor"), "clicked")
        self.pump()
        self.assertIn('nonzero axis', self.find("toolheadRotorValidation").property("text"))
        self.assertEqual(self.draft.rotors[0]['axis'], [0.,1.,0.])

    def test_pending_fan_binding_survives_discovery_and_missing_telemetry(self):
        self.find('toolheadEditorSection').setProperty('currentIndex',2)
        self.draft.pickedBody(0)
        self.draft.setFanReadings({'fan':dict(available=True,speed=.2),'heater_fan hotend':dict(available=True,rpm=1000)})
        self.pump()
        combo=self.find('toolheadRotorFan')
        combo.setProperty('currentIndex',2)
        harness.QMetaObject.invokeMethod(combo,'activated',Q_ARG(int,2))
        self.draft.setFanReadings({'controller_fan electronics':dict(available=True,speed=.5),'fan':dict(available=True,speed=.2),'heater_fan hotend':dict(available=True,rpm=1000)})
        self.pump()
        self.assertEqual(combo.property('currentIndex'),3)
        self.draft.setFanReadings({})
        self.pump()
        self.assertEqual(combo.property('currentIndex'),1)
        harness.QMetaObject.invokeMethod(self.find('toolheadConfirmRotor'),'clicked')
        self.pump()
        self.assertEqual(self.draft.rotors[0]['fan'],'heater_fan hotend')
        self.assertEqual(self.draft.rotorReadout,'Fan unavailable')

    def test_invalid_confirmation_preserves_other_fields_until_corrected(self):
        self.find('toolheadEditorSection').setProperty('currentIndex',2)
        self.draft.pickedBody(0)
        self.pump()
        self.find('toolheadRotorCentreX').setProperty('text','8')
        self.find('toolheadRotorRPM').setProperty('text','1234')
        self.find('toolheadRotorDirection').setProperty('currentIndex',1)
        self.find('toolheadRotorBlur').setProperty('checked',False)
        for name in ('X','Y','Z'):
            self.find('toolheadRotorAxis'+name).setProperty('text','0')
        harness.QMetaObject.invokeMethod(self.find('toolheadConfirmRotor'),'clicked')
        self.pump()
        self.assertEqual(self.find('toolheadRotorCentreX').property('text'),'8')
        self.assertEqual(self.find('toolheadRotorRPM').property('text'),'1234')
        self.assertEqual(self.find('toolheadRotorDirection').property('currentIndex'),1)
        self.assertFalse(self.find('toolheadRotorBlur').property('checked'))
        self.assertEqual(self.draft.rotors,[])
        self.find('toolheadRotorAxisY').setProperty('text','1')
        harness.QMetaObject.invokeMethod(self.find('toolheadConfirmRotor'),'clicked')
        self.pump()
        row=self.draft.rotors[0]
        self.assertEqual(row['centre'][0],8)
        self.assertEqual(row['rpm'],1234)
        self.assertEqual(row['direction'],-1)
        self.assertFalse(row['blur'])

    def test_tip_and_section_entry_retire_other_picker_modes(self):
        preview=self.find('toolheadModelPreview')
        preview.setProperty('addingRotor',True)
        preview.setProperty('picking',True)
        harness.QMetaObject.invokeMethod(self.find('toolheadPickTip'),'clicked')
        self.assertFalse(preview.property('addingRotor'))
        self.assertTrue(preview.property('picking'))
        self.find('toolheadEditorSection').setProperty('currentIndex',2)
        self.assertFalse(preview.property('picking'))

    def test_sparse_body_ids_resolve_the_visible_combo_index(self):
        from mpf.geometry.ToolheadGeometry import default_mesh, mesh_from_arrays
        metadata=dict(materials=[dict(name='ABS',description='',source='step-material')],
            bodies=[dict(name=name,source='unknown',centre=None,axis=None) for name in ('Hidden','Hidden too','Rotor')])
        self.draft._mesh=mesh_from_arrays(default_mesh().triangles, body_ids=[2]*len(default_mesh().triangles), metadata=metadata)
        self.draft.changed.emit()
        self.find('toolheadEditorSection').setProperty('currentIndex',2)
        self.draft.pickedBody(2)
        self.pump()
        combo=self.find('toolheadRotorBody')
        self.assertEqual(combo.property('count'),1)
        self.assertEqual(combo.property('currentIndex'),0)
        self.assertEqual(self.draft.rotorCandidate['body'],2)

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
