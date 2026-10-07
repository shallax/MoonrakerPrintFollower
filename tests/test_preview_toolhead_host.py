"""Real QML hosting and lifecycle, isolated from Cura and every transport."""
import unittest
from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, runtime

if QT_AVAILABLE:
    from tools.capture_preview_toolhead import ToolheadScene, ROOT
    from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QUrl, pyqtSignal
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtQml import QQmlComponent
    from PyQt6.QtQuick import QQuickItem
else:
    QObject = object
    def pyqtSignal(): return None


class Cura(QObject):
    changed = pyqtSignal()
    preview_active = True


class HostApplication(QObject):
    initializationFinished = pyqtSignal()
    started = True

    def __init__(self, scene):
        super().__init__()
        self.scene = scene
        self.components = []

    def getMainWindow(self):
        return self.scene.window

    def createQmlComponent(self, path):
        component = QQmlComponent(self.scene.engine, QUrl.fromLocalFile(path))
        self.components.append(component)
        if component.isError():
            raise AssertionError([e.toString() for e in component.errors()])
        return component.create()


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ToolheadHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def host(self, theme='cura-light'):
        context = runtime()
        qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        scene = ToolheadScene(self.app, theme)
        self.addCleanup(scene.close)
        # Retire the design-only pane, and supply a synthetic Cura viewport
        # with exactly the lexical IDs used by native Cura. No camera action
        # or printer model is created by this host.
        scene.pane.setVisible(False)
        scene.window.resize(1100, 820)
        component = QQmlComponent(scene.engine)
        component.setData(b'''import QtQuick 2.15
Item {
    anchors.fill: parent
    Item { id: main; objectName: "fakeViewport"; y: 100; width: parent.width; height: parent.height-y }
    Rectangle { id: stageMenu; objectName: "fakeStageMenu"; y: 80; width: parent.width; height: 40; color: "#666666" }
    Rectangle { id: viewOrientationControls; objectName: "fakePerspective"; x: 18; y: parent.height-48; width: 220; height: 30; color: "#999999" }
    Rectangle { id: jobSpecs; objectName: "fakeJobSpecs"; visible: false; x: 18; y: viewOrientationControls.y-45; width: 220; height: 35; color: "#888888" }
    Rectangle { id: objectSelector; objectName: "fakeObjectSelector"; visible: false; x: 18; y: jobSpecs.y-45; width: 220; height: 35; color: "#777777" }
}''', QUrl.fromLocalFile(str(ROOT / 'mpf/preview/synthetic-host.qml')))
        viewport = component.create()
        self.assertIsNotNone(viewport, [e.toString() for e in component.errors()])
        viewport.setParentItem(scene.window.contentItem())
        viewport.setParent(scene.window.contentItem())
        application, cura = HostApplication(scene), Cura()
        saved = {'previewToolheadOpacity': .7}
        persistence = SimpleNamespace(state_global_document=lambda: dict(saved), merge_state_global=saved.update)
        presenter = qt.load('PreviewToolheadPresentation').PreviewToolheadPresentation(application, cura, persistence=persistence)
        def retire():
            presenter.close()
            viewport.setParentItem(None)
            viewport.deleteLater()
            for owned in application.components:
                owned.deleteLater()
            application.components.clear()
            component.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(retire)
        scene.pump()
        self.assertIsNotNone(presenter._pane)
        return scene, viewport, cura, presenter, saved

    def test_top_left_placement_reserves_stage_menu_object_list_and_perspective_controls(self):
        scene, viewport, cura, presenter, _ = self.host()
        shell, pane = presenter._shell, presenter._pane
        for height, summary in ((820, False), (460, False), (460, True), (820, True)):
            scene.window.resize(1100, height)
            for name in ('fakeJobSpecs', 'fakeObjectSelector'):
                viewport.findChild(QQuickItem, name).setVisible(summary)
            scene.pump()
            bottom = viewport.findChild(QQuickItem, 'fakeObjectSelector' if summary else 'fakePerspective').y()
            self.assertLess(pane.y() + pane.height(), bottom)
            self.assertGreaterEqual(pane.y(), viewport.findChild(QQuickItem, 'fakeViewport').y())
            self.assertEqual(pane.y(), viewport.findChild(QQuickItem, 'fakeStageMenu').y() +
                             viewport.findChild(QQuickItem, 'fakeStageMenu').height() + shell.property('gap'))
            self.assertEqual(pane.x(), 18)
            self.assertTrue(shell.isVisible())
            pane.setProperty('collapsed', True)
            scene.pump()
            self.assertEqual(pane.y(), shell.property('dockTop'))
            self.assertLess(pane.y() + pane.height(), bottom)
            pane.setProperty('collapsed', False)
        cura.preview_active = False
        cura.changed.emit()
        self.assertFalse(shell.isVisible())
        cura.preview_active = True
        cura.changed.emit()
        self.assertIs(presenter._shell, shell, 'stage changes must not churn the host')
        self.assertTrue(shell.isVisible())
        self.assertEqual(scene.warnings, [])

    def test_printing_offset_uses_its_own_gate_and_reset_disables_it(self):
        scene, _, _, presenter, _ = self.host()
        presenter.publish({'connected': True, 'positionX': '101.25', 'positionY': '99.00',
                           'positionZ': '1.00', 'offsetText': '+0.010',
                           'offsetAllowed': True, 'jogAllowed': False,
                           'actionRows': [{'key': 'motors', 'label': 'Disable motors', 'allowed': True}],
                           'statusText': 'Printing · Z-offset available'})
        pane = presenter._pane
        self.assertEqual(pane.property('positionX'), '101.25')
        for name in ('jogAllowed', 'homeAllowed', 'moveToAllowed'):
            self.assertFalse(pane.property(name))
        self.assertFalse(pane.property('offsetAllowed'), 'Unbound hosts must never advertise mutations')
        requests = []
        presenter.bind_controls(lambda kind, args: requests.append((kind, args)))
        self.assertTrue(pane.property('offsetAllowed'))
        # Synthetic intents only: no printer/transport exists in this fixture.
        pane.offsetRequested.emit(.01)
        self.assertEqual(requests, [('offset', (.01,))])
        pane.jogRequested.emit('x', 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(pane.property('offsetText'), '+0.010')
        self.assertEqual(pane.property('positionZ'), '1.00')
        rows = pane.property('actionRows')
        self.assertTrue(rows[0]['allowed'])
        target = pane.findChild(QQuickItem, 'previewToolheadTargetX')
        target.setProperty('text', '42')
        presenter.reset()
        self.assertEqual(target.property('text'), '')
        self.assertEqual(pane.property('positionX'), '—')
        self.assertFalse(pane.property('connected'))
        self.assertFalse(pane.property('offsetAllowed'))
        self.assertEqual(scene.warnings, [])

    def test_all_intents_and_distance_are_routed_once_and_old_routes_are_fenced(self):
        _, _, cura, presenter, _ = self.host()
        first, second = [], []
        presenter.bind_controls(lambda kind, args: first.append((kind, args)))
        presenter.publish({'connected': True, 'jogAllowed': True, 'homeAllowed': True,
                           'moveToAllowed': True, 'offsetAllowed': True,
                           'actionRows': [{'key': 'bed_mesh', 'label': 'Mesh', 'allowed': True}]})
        pane = presenter._pane
        pane.jogDistanceRequested.emit(75)
        pane.jogRequested.emit('y', -1)
        pane.homeRequested.emit('z')
        pane.moveToRequested.emit('', '100', '')
        pane.offsetRequested.emit(-.005)
        pane.actionRequested.emit('bed_mesh')
        pane.actionRequested.emit('motors')  # unsupported/disabled intent cannot route
        self.assertEqual(first, [('jog', ('y', -1, 75)), ('home', ('z',)),
                                 ('move-to', ('', '100', '')), ('offset', (-.005,)),
                                 ('action', ('bed_mesh',))])
        old_slot = presenter._intent_connections[0][1]
        presenter.bind_controls(lambda kind, args: second.append((kind, args)))
        old_slot('x', 1)
        self.assertEqual(len(first), 5)
        self.assertEqual(second, [])
        cura.preview_active = False
        pane.offsetRequested.emit(.01)
        self.assertEqual(second, [])
        cura.preview_active = True
        pane.offsetRequested.emit(.01)
        self.assertEqual(second, [('offset', (.01,))])
        presenter.bind_controls(None)
        pane.offsetRequested.emit(.01)
        self.assertEqual(len(second), 1)
        self.assertFalse(pane.property('offsetAllowed'))

    def test_collapse_preference_retains_other_view_settings_and_close_removes_host(self):
        _, _, cura, presenter, saved = self.host('cura-dark')
        presenter._pane.collapseRequested.emit(True)
        self.assertTrue(presenter._pane.property('collapsed'))
        self.assertTrue(saved['previewToolheadPaneCollapsed'])
        self.assertEqual(saved['previewToolheadOpacity'], .7)
        shell = presenter._shell
        presenter.close()
        self.assertIsNone(presenter._shell)
        self.assertIsNone(shell.parentItem())
        cura.changed.emit()
        self.assertIsNone(presenter._shell)

    def test_retired_shell_cannot_clear_its_replacement_and_destroyed_host_recreates(self):
        _, _, _, presenter, _ = self.host()
        presenter._retire_shell()
        presenter.refresh()
        replacement = presenter._shell
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIs(presenter._shell, replacement)
        replacement.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(presenter._shell)
        self.assertIsNone(presenter._pane)
        presenter.refresh()
        self.assertIsNotNone(presenter._shell)
        self.assertIsNot(presenter._shell, replacement)


if __name__ == '__main__':
    unittest.main()
