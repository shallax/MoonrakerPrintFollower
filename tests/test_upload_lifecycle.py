from __future__ import annotations

from http.server import ThreadingHTTPServer
import os
import pathlib
import threading
import unittest

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedTransport, runtime

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"


class UploadContractTests(unittest.TestCase):
    """Source-level contracts for the upload dialog and its controller."""

    def test_upload_dialog_teardown_is_queued_out_of_qml_callbacks(self):
        controller = (PLUGINS / "UploadController.py").read_text()
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
        self.assertIn("self._later(0, finish)", controller)
        self.assertIn('self._later_owned(0, lambda: self._finish(False, ""))', controller)
        self.assertIn("dialog.deleteLater()", adapter)

    def test_upload_folders_are_discovered_and_hidden_paths_excluded(self):
        controller = (PLUGINS / "UploadController.py").read_text()
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
        self.assertIn("server/files/directory?", controller)
        self.assertIn('part.startswith(".")', controller)
        self.assertIn("MAX_DIRECTORIES", controller)
        self.assertIn("manager.uploadPathOptions", qml)
        self.assertIn("UM.I18nCatalog", qml)

    def test_cancel_is_deferred_and_not_an_error(self):
        controller = (PLUGINS / "UploadController.py").read_text()
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
        self.assertIn('self._later_owned(0, lambda: self._finish(False, ""))', controller)
        self.assertIn("elif error:", adapter)
        self.assertIn("self.writeFinished.emit(self)", adapter)
        self.assertIn("self._upload.terminal_delivered()", adapter)

    def test_accept_and_folder_discovery_remain_nonblocking(self):
        source = (PLUGINS / "UploadController.py").read_text()
        self.assertIn("self._later(0, finish)", source)
        self.assertIn("server/files/directory?", source)
        self.assertIn("MAX_DIRECTORIES", source)
        self.assertNotIn("time.sleep", source)

    def test_upload_root_has_a_human_readable_label(self):
        controller = (PLUGINS / "UploadController.py").read_text()
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
        self.assertIn('if path == "<root>"', controller + adapter)
        self.assertIn('self._upload.path or "<root>"', controller + adapter)
        self.assertIn('if (path === "<root>")', qml)
        self.assertIn("<root> is Moonraker's gcodes directory", qml)

    def test_upload_dialog_contract(self):
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
        self.assertIn('property variant catalog: UM.I18nCatalog {', qml)
        self.assertIn("manager.uploadPathOptions", qml)
        self.assertIn("id: form", qml)
        self.assertIn("form.implicitHeight", qml)
        self.assertIn("height: minimumHeight", qml)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class UploadLifecycleTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app = self.qt.Application()
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.device_module = self.qt.load("MoonrakerOutputDevice")
        self.client_module = self.qt.load("MoonrakerClient")

    @staticmethod
    def install_dialog_factory(app):
        dialogs = []

        class Dialog:
            def __init__(self):
                self.shown = False
                self.deleted = False

            def show(self):
                self.shown = True

            def deleteLater(self):
                self.deleted = True

        def create(_path, _context):
            dialog = Dialog()
            dialogs.append(dialog)
            return dialog

        app.createQmlComponent = create
        return dialogs

    def test_cancel_then_retry_releases_device_busy(self):
        transport = ScriptedTransport()
        client = self.client_module.MoonrakerClient(transport=transport)
        client.configure("http://printer-a", "", 750)
        self.addCleanup(client.stop)
        config = self.config_type(url="http://printer-a", upload_dialog=True, upload_start_print=False)
        dialogs = self.install_dialog_factory(self.app)
        device = self.device_module.MoonrakerOutputDevice(
            self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None, active_identity=lambda: ("A", "A"),
        )
        self.addCleanup(device.deactivate)
        finished, errors, successes = [], [], []
        device.writeFinished.connect(finished.append)
        device.writeError.connect(errors.append)
        device.writeSuccess.connect(successes.append)

        device.requestWrite(None, "first.gcode")
        self.assertTrue(device._upload.busy)
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(dialogs[0].shown)

        device.cancelUpload()
        self.qt.events(10)
        self.assertFalse(device._upload.busy)
        self.assertEqual(finished, [device])
        self.assertEqual(errors, [])
        self.assertEqual(successes, [])
        self.assertTrue(dialogs[0].deleted)

        device.requestWrite(None, "second.gcode")
        self.assertTrue(device._upload.busy)
        self.assertEqual(len(dialogs), 2)
        self.assertTrue(dialogs[1].shown)
        device.cancelUpload()
        self.qt.events(10)
        self.assertFalse(device._upload.busy)
        self.assertEqual(finished, [device, device])

    def test_cancel_retires_operation_even_if_binding_went_stale(self):
        transport = ScriptedTransport()
        client = self.client_module.MoonrakerClient(transport=transport)
        client.configure("http://printer-a", "", 750)
        self.addCleanup(client.stop)
        config = self.config_type(url="http://printer-a", upload_dialog=True, upload_start_print=False)
        self.install_dialog_factory(self.app)
        active_machine = ["A"]
        device = self.device_module.MoonrakerOutputDevice(
            self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None,
            active_identity=lambda: (active_machine[0], active_machine[0]),
        )
        self.addCleanup(device.deactivate)
        finished, errors = [], []
        device.writeFinished.connect(finished.append)
        device.writeError.connect(errors.append)

        device.requestWrite(None, "stale.gcode")
        source_path = device._upload._source.path
        self.assertTrue(os.path.exists(source_path))
        active_machine[0] = "B"
        self.assertFalse(device._upload._current())

        device.cancelUpload()
        self.qt.events(10)
        self.assertFalse(device._upload.busy)
        self.assertFalse(os.path.exists(source_path))
        self.assertEqual(finished, [device])
        self.assertEqual(errors, [])

    def test_dialog_accept_starts_real_http_upload(self):
        received = []

        class Handler(PipeSafeHandler):
            def do_POST(self):
                received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                body = b'{"result":{"item":{"path":"renamed.gcode"}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)

        client = self.client_module.MoonrakerClient()
        client.configure(url, "", 750)
        self.addCleanup(client.stop)
        current = [self.config_type(
            url=url,
            upload_dialog=True,
            upload_start_print=False,
            upload_remember_state=True,
        )]
        dialogs = self.install_dialog_factory(self.app)

        def apply_config(value):
            current[0] = value

        device = self.device_module.MoonrakerOutputDevice(
            self.app, "A", client=client, config=lambda: current[0],
            apply_config=apply_config, active_identity=lambda: ("A", "A"),
        )
        self.addCleanup(device.deactivate)
        finished, successes, errors = [], [], []
        device.writeFinished.connect(finished.append)
        device.writeSuccess.connect(successes.append)
        device.writeError.connect(errors.append)

        device.requestWrite(None, "part.gcode")
        self.assertEqual(len(dialogs), 1)
        device.acceptUpload("<root>", "renamed.gcode", False)
        for _ in range(200):
            if finished:
                break
            self.qt.events(10)

        self.assertEqual(finished, [device])
        self.assertEqual(successes, [device])
        self.assertEqual(errors, [])
        self.assertFalse(device._upload.busy)
        self.assertTrue(dialogs[0].deleted)
        self.assertEqual(len(received), 1)
        path, body = received[0]
        self.assertEqual(path, "/server/files/upload")
        self.assertIn(b"G1 X0", body)
        self.assertIn(b'filename="renamed.gcode"', body)
        self.assertIn(b'name="root"', body)
        self.assertIn(b"gcodes", body)
        self.assertNotIn(b'name="print"', body)
        self.assertEqual(current[0].upload_path, "")
        self.assertFalse(current[0].upload_start_print)


if __name__ == "__main__":
    unittest.main()
