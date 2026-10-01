"""Exercise extracted modal components with real Qt focus and key delivery."""
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtTest import QTest
    from Tools.capture_filemanager import FileManagerModelStub

    class DialogModel(FileManagerModelStub):
        def __init__(self):
            super().__init__()
            self.calls = []
            self._values.update(
                filePrintConfirm={"relpath": "part.gcode", "name": "part.gcode", "est": "2 min", "filament": "1 m", "printerName": "Test", "ready": True, "homed": True, "readyText": "Printer ready."},
                fileDeleteConfirm={"kind": "file", "first": "part.gcode", "count": 1, "blocked": 0},
                fileRenameTarget={"kind": "file", "path": "part.gcode", "name": "part.gcode"},
                fileUploadConfirm={"path": "part.gcode", "filename": "part.gcode"},
                fileUploadProgress={"name": "part.gcode", "state": "uploading", "percent": 40, "error": ""},
            )

        @harness.pyqtSlot()
        def fileCancelPrint(self): self.calls.append("print")
        @harness.pyqtSlot()
        def fileCancelDelete(self): self.calls.append("delete")
        @harness.pyqtSlot()
        def fileCancelRename(self): self.calls.append("rename")
        @harness.pyqtSlot()
        def fileCancelUpload(self): self.calls.append("upload")


class FileDialogTests(harness.RealEngineTestCase):
    def open_modal(self, name):
        dialog = self.mount(name + ".qml")
        window = harness.QQuickWindow()
        window.resize(600, 450)
        model = DialogModel()
        self.addCleanup(lambda: model.deleteLater())
        self.addCleanup(self._destroy_window, dialog, window)
        self.assertTrue(dialog.setProperty("parent", window.contentItem()))
        self.assertTrue(dialog.setProperty("printerModel", model))
        window.show()
        window.requestActivate()
        harness.QMetaObject.invokeMethod(dialog, "open")
        self._wait_until(window, lambda image: bool(dialog.property("opened")), timeout=3)
        self.assertTrue(dialog.property("opened"))
        return dialog, window, model

    def cancel_modal(self, name, action):
        dialog, window, model = self.open_modal(name)
        QTest.keyClick(window, harness.Qt.Key.Key_Escape)
        self._wait_until(window, lambda image: not dialog.property("opened"), timeout=3)
        self.assertEqual(model.calls, [action])
        self.assertFalse(dialog.property("opened"))

    def test_print_escape_cancels_the_owned_operation(self):
        self.cancel_modal("PrintConfirmDialog", "print")

    def test_delete_escape_cancels_the_owned_operation(self):
        self.cancel_modal("DeleteConfirmDialog", "delete")

    def test_rename_escape_cancels_the_owned_operation(self):
        self.cancel_modal("RenameDialog", "rename")

    def test_upload_escape_cancels_the_owned_operation(self):
        self.cancel_modal("UploadConfirmDialog", "upload")

    def test_print_permission_remains_a_live_input_while_confirmation_is_open(self):
        dialog, window, model = self.open_modal("PrintConfirmDialog")
        button = self.find(dialog.property("contentItem"), "printConfirmStartButton")
        self.assertFalse(button.isEnabled())
        dialog.setProperty("startAllowed", True)
        self.pump()
        self.assertTrue(button.isEnabled())
        dialog.setProperty("startAllowed", False)
        self.pump()
        self.assertFalse(button.isEnabled())
        self.assertEqual(model.calls, [])

    def test_rename_owns_its_prefilled_field_and_selects_only_the_stem(self):
        dialog, _, _model = self.open_modal("RenameDialog")
        fields = [item for item in dialog.property("contentItem").findChildren(harness.QQuickItem)
                  if item.property("text") == "part.gcode" and item.property("selectionStart") is not None]
        self.assertEqual(len(fields), 1)
        field = fields[0]
        self.assertEqual((field.property("selectionStart"), field.property("selectionEnd")), (0, 4))
        self.assertTrue(field.hasActiveFocus())
