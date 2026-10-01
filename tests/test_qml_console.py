"""A console may be mounted independently and rebound without foreign state."""
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtTest import QTest

    class ConsoleModel(harness.QObject):
        consoleChanged = harness.pyqtSignal()

        def __init__(self, text):
            super().__init__()
            self.lines = [{"kind": "response", "text": text}]
            self.history = ["G28", "M105"]
            self.connected = True
            self.accept = False
            self.calls = []
            self.expanded = True

        @harness.pyqtProperty("QVariant", notify=consoleChanged)
        def consoleLines(self): return self.lines
        @harness.pyqtProperty("QVariant", notify=consoleChanged)
        def consoleHistory(self): return self.history
        @harness.pyqtProperty("QVariant", notify=consoleChanged)
        def sectionExpandedMap(self): return {"console": self.expanded}
        @harness.pyqtProperty(int, notify=consoleChanged)
        def consoleDropped(self): return 0
        @harness.pyqtProperty(int, notify=consoleChanged)
        def consoleRevisions(self): return 0
        @harness.pyqtProperty(int, notify=consoleChanged)
        def consoleHeight(self): return 250
        @harness.pyqtProperty(bool, notify=consoleChanged)
        def consoleErrorBell(self): return False
        @harness.pyqtProperty(bool, notify=consoleChanged)
        def monitorConnected(self): return self.connected
        @harness.pyqtSlot(bool)
        def setConsoleExpanded(self, value): pass
        @harness.pyqtSlot(str, bool)
        def setSectionExpanded(self, key, value):
            self.expanded = value
            self.consoleChanged.emit()
        @harness.pyqtSlot(int)
        def setConsoleHeight(self, value): self.calls.append(("height", value))
        @harness.pyqtSlot(str, result=bool)
        def sendConsoleCommand(self, text):
            self.calls.append(("send", text))
            return self.accept
        @harness.pyqtSlot()
        def clearConsoleHistory(self):
            self.lines = []
            self.consoleChanged.emit()


class ConsolePaneTests(harness.RealEngineTestCase):
    def open_console(self):
        self.models = [ConsoleModel("first printer")]
        # Keep models alive until the window and QML document are gone.
        self.addCleanup(lambda: [model.deleteLater() for model in self.models])
        pane, window = self.mount_window("ConsolePane.qml", 800, 260)
        pane.setProperty("resizeFrame", window.contentItem())
        pane.setProperty("printerModel", self.models[0])
        window.requestActivate()
        self.pump(20)
        return pane, window, self.models[0]

    def test_rebind_replaces_transcript_and_disconnects_the_retired_model(self):
        pane, _, old = self.open_console()
        output = self.find(pane, "moonrakerConsoleOutput")
        self.assertIn("first printer", output.property("text"))
        new = ConsoleModel("second printer")
        self.models.append(new)
        pane.setProperty("printerModel", new)
        self.pump(10)
        self.assertIn("second printer", output.property("text"))
        self.assertNotIn("first printer", output.property("text"))
        old.lines.append({"kind": "note", "text": "retired message"})
        old.consoleChanged.emit()
        self.pump(10)
        self.assertNotIn("retired message", output.property("text"))

    def test_refused_send_preserves_the_draft_and_success_clears_it(self):
        pane, window, model = self.open_console()
        field = self.find(pane, "moonrakerConsoleInput")
        field.setProperty("text", "M105")
        field.forceActiveFocus()
        QTest.keyClick(window, harness.Qt.Key.Key_Return)
        self.pump(10)
        self.assertEqual(model.calls, [("send", "M105")])
        self.assertEqual(field.property("text"), "M105")
        model.accept = True
        QTest.keyClick(window, harness.Qt.Key.Key_Return)
        self.pump(10)
        self.assertEqual(model.calls, [("send", "M105"), ("send", "M105")])
        self.assertEqual(field.property("text"), "")

    def test_history_navigation_returns_to_the_unsent_draft(self):
        pane, window, _model = self.open_console()
        field = self.find(pane, "moonrakerConsoleInput")
        field.setProperty("text", "draft")
        field.forceActiveFocus()
        for key, expected in ((harness.Qt.Key.Key_Up, "M105"),
                              (harness.Qt.Key.Key_Up, "G28"),
                              (harness.Qt.Key.Key_Down, "M105"),
                              (harness.Qt.Key.Key_Down, "draft")):
            QTest.keyClick(window, key)
            self.pump(5)
            self.assertEqual(field.property("text"), expected)

    def test_disconnect_disables_input_but_keeps_transcript_selectable(self):
        pane, _, model = self.open_console()
        model.connected = False
        model.consoleChanged.emit()
        self.pump(10)
        self.assertFalse(self.find(pane, "moonrakerConsoleInput").isEnabled())
        self.assertFalse(self.find(pane, "moonrakerConsoleSend").isEnabled())
        self.assertFalse(self.find(pane, "moonrakerConsoleClear").isEnabled())
        output = self.find(pane, "moonrakerConsoleOutput")
        self.assertTrue(output.isEnabled())
        self.assertTrue(output.property("selectByMouse"))
        self.assertIn("first printer", output.property("text"))
