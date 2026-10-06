"""UI hover delivery and pane geometry guard scene picking without eating input."""
from types import SimpleNamespace
from unittest.mock import Mock

from tests import qml_engine_support as harness
from mpf.preview.ViewportHover import ViewportHover


class ViewportHoverTests(harness.RealEngineTestCase):
    def test_qt_hover_delivery_blocks_foreign_panes_and_recovers_on_exit(self):
        from PyQt6.QtCore import QPoint, QUrl
        from PyQt6.QtQml import QQmlComponent
        from PyQt6.QtTest import QTest
        component = QQmlComponent(self.engine)
        component.setData(b'''
            import QtQuick
            import QtQuick.Window
            Window {
                width: 400; height: 300; visible: true
                MouseArea { anchors.fill: parent; hoverEnabled: true }
                Rectangle {
                    x: 180; y: 100; width: 180; height: 160
                    MouseArea { anchors.fill: parent; hoverEnabled: true }
                }
            }
        ''', QUrl())
        window = component.create()
        self.assertIsNotNone(window, [error.toString() for error in component.errors()])
        guard = ViewportHover()
        try:
            self.pump(20)
            QTest.mouseMove(window, QPoint(50, 50))
            self.pump(10)
            self.assertFalse(guard.blocked(window, (50, 50)))
            self.assertIsNotNone(guard._area)
            QTest.mouseMove(window, QPoint(200, 150))
            self.pump(10)
            self.assertTrue(guard.blocked(window, (200, 150)))
            QTest.mouseMove(window, QPoint(50, 50))
            self.pump(10)
            self.assertFalse(guard.blocked(window, (50, 50)))
        finally:
            window.close()
            window.deleteLater()
            self.pump(10)

    def test_owned_panes_block_including_empty_space_and_collapsed_headers(self):
        from PyQt6.QtQuick import QQuickItem
        content, pane = QQuickItem(), QQuickItem()
        pane.setParentItem(content)
        pane.setX(100); pane.setY(40)
        pane.setWidth(200); pane.setHeight(300)
        window = SimpleNamespace(contentItem=lambda: content)
        guard = ViewportHover()
        self.assertTrue(guard.blocked(window, (110, 250), (None, pane)))
        pane.setHeight(30)
        self.assertFalse(guard.blocked(window, (110, 250), (pane,)))
        self.assertTrue(guard.blocked(window, (110, 50), (pane,)))
        pane.setVisible(False)
        self.assertFalse(guard.blocked(window, (110, 50), (pane,)))
        self.assertFalse(guard.blocked(window, None, (pane,)))

    def test_destroyed_window_or_control_is_safe_and_retried(self):
        guard = ViewportHover()
        window = SimpleNamespace(contentItem=Mock(side_effect=RuntimeError('deleted')))
        self.assertFalse(guard.blocked(window, (10, 10)))
        self.assertIsNone(guard._content)
