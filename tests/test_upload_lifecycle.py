from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import os
import pathlib
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedTransport, runtime

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"


class UploadContractTests(unittest.TestCase):
    """Source-level contracts for the upload dialog and its controller."""

    def test_upload_dialog_teardown_is_queued_out_of_qml_callbacks(self):
        controller = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        self.assertIn("self._later(0, finish)", controller)
        self.assertIn('self._later_owned(0, lambda: self._finish(False, ""))', controller)
        self.assertIn("dialog.deleteLater()", adapter)

    def test_upload_folders_are_discovered_and_hidden_paths_excluded(self):
        controller = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text(encoding="utf-8")
        self.assertIn("server/files/directory?", controller)
        self.assertIn('part.startswith(".")', controller)
        self.assertIn("MAX_DIRECTORIES", controller)
        self.assertIn("manager.uploadPathOptions", qml)
        self.assertIn("UM.I18nCatalog", qml)

    def test_cancel_is_deferred_and_not_an_error(self):
        controller = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        self.assertIn('self._later_owned(0, lambda: self._finish(False, ""))', controller)
        self.assertIn("elif error:", adapter)
        self.assertIn("self.writeFinished.emit(self)", adapter)
        self.assertIn("self._upload.terminal_delivered()", adapter)

    def test_accept_and_folder_discovery_remain_nonblocking(self):
        source = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        self.assertIn("self._later(0, finish)", source)
        self.assertIn("server/files/directory?", source)
        self.assertIn("MAX_DIRECTORIES", source)
        self.assertNotIn("time.sleep", source)

    def test_upload_root_has_a_human_readable_label(self):
        controller = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        adapter = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text(encoding="utf-8")
        self.assertIn('if path == "<root>"', controller + adapter)
        self.assertIn('self._upload.path or "<root>"', controller + adapter)
        self.assertIn('if (path === "<root>")', qml)
        self.assertIn("<root> is Moonraker's gcodes directory", qml)

    def test_upload_dialog_contract(self):
        qml = (PLUGINS / "MoonrakerUploadDialog.qml").read_text(encoding="utf-8")
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

    def _stop_client(self, client):
        client.stop()
        client.transport.close()  # the manager's pooled sockets close with it

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
        self.addCleanup(self._stop_client, client)
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
        self.addCleanup(self._stop_client, client)
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
        self.addCleanup(self._stop_client, client)
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

    def _upload_reply(self, error=False, body=None, sync_abort=False):
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtNetwork import QNetworkReply

        class FakeReply(QObject):
            uploadProgress = pyqtSignal(int, int)
            finished = pyqtSignal()
            def __init__(self):
                super().__init__()
                self._deleted = 0
                self._aborts = 0
            def error(self):
                return QNetworkReply.NetworkError.ContentNotFoundError if error else QNetworkReply.NetworkError.NoError
            def errorString(self): return "simulated"
            def readAll(self): return json.dumps(body).encode() if body is not None else b""
            def abort(self):
                # The faithful Qt 6.6 encoding: abort() emits finished
                # SYNCHRONOUSLY.
                self._aborts += 1
                if sync_abort:
                    self.finished.emit()
            def deleteLater(self): self._deleted += 1
            def isRunning(self): return False
        return FakeReply()

    def _file_manager(self, client):
        fm = self.qt.load("FileManager").FileManager(client, None)
        self.addCleanup(fm.bind)
        directory = tempfile.mkdtemp(prefix="f04-upload-")
        source = os.path.join(directory, "f04-upload.gcode")
        with open(source, "wb") as handle:
            handle.write(b"G1 X0\n")
        self.addCleanup(shutil.rmtree, directory, True)
        return fm, source

    def test_file_manager_upload_terminal_disposes_exactly_once(self):
        client = self.client_module.MoonrakerClient(transport=ScriptedTransport())
        client.configure("http://printer-a", "", 750)
        self.addCleanup(self._stop_client, client)
        fm, source = self._file_manager(client)
        verdicts = []
        fm.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        for error, body, ok in [
            (False, None, True),
            (True, {"message": "No space left on device"}, False),
        ]:
            double = self._upload_reply(error=error, body=body)
            client.transport.network = SimpleNamespace(post=lambda request, multipart, d=double: d)
            self.assertTrue(fm.upload_file(source))
            double.finished.emit()
            self.assertEqual(verdicts, [(ok, "f04-upload.gcode" if ok else "No space left on device")])
            self.assertEqual(double._deleted, 1)
            self.assertFalse(fm._upload_replies)
            verdicts.clear()

    def test_file_manager_abort_uploads_clears_ownership_before_abort(self):
        client = self.client_module.MoonrakerClient(transport=ScriptedTransport())
        client.configure("http://printer-a", "", 750)
        self.addCleanup(self._stop_client, client)
        fm, source = self._file_manager(client)
        verdicts = []
        fm.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        double = self._upload_reply(sync_abort=True)
        client.transport.network = SimpleNamespace(post=lambda request, multipart: double)
        self.assertTrue(fm.upload_file(source))
        fm.bind()  # bumps the generation, then aborts: the synchronous finished re-enters the handler
        self.assertEqual(verdicts, [(False, "The printer changed during the upload.")])
        self.assertFalse(fm._upload_replies)

    def test_file_manager_refuses_a_second_same_name_upload(self):
        client = self.client_module.MoonrakerClient(transport=ScriptedTransport())
        client.configure("http://printer-a", "", 750)
        self.addCleanup(self._stop_client, client)
        fm, source = self._file_manager(client)
        replies = [self._upload_reply(), self._upload_reply()]
        client.transport.network = SimpleNamespace(post=lambda request, multipart: replies.pop(0))
        self.assertTrue(fm.upload_file(source))
        verdicts = []
        fm.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertFalse(fm.upload_file(source))
        self.assertEqual(len(verdicts), 1)
        self.assertFalse(verdicts[0][0])
        self.assertIn("already running", verdicts[0][1])
        self.assertEqual(len(fm._upload_replies), 1)  # the first upload still owns its op

    def test_upload_refusal_words_win_over_qt_error_text(self):
        client = self.client_module.MoonrakerClient(transport=ScriptedTransport())
        client.configure("http://printer-a", "", 750)
        self.addCleanup(self._stop_client, client)
        controller = self.qt.load("UploadController").UploadController(client, "A", lambda: ("A", "A"))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))
        double = self._upload_reply(error=True, body={"message": "Move out of range"})
        controller._reply = double
        controller._active = True
        controller._current = lambda generation=None: True
        controller._uploaded(double, controller._generation)
        self.qt.events(10)
        self.assertEqual(results, [(False, "Move out of range")])
        self.assertEqual(double._deleted, 1)

    def test_upload_start_refusal_reads_the_body_verdict(self):
        class Handler(PipeSafeHandler):
            def do_GET(self):
                body = b'{"result": {"klippy_state": "ready"}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"result":{"item":{"path":"renamed.gcode"}}, "print_started": false, "print_queued": false}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        client = self.client_module.MoonrakerClient()
        client.configure(url, "", 750)
        self.addCleanup(self._stop_client, client)
        config = self.config_type(url=url, upload_dialog=True, upload_start_print=True)
        self.install_dialog_factory(self.app)
        device = self.device_module.MoonrakerOutputDevice(
            self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None, active_identity=lambda: ("A", "A"),
        )
        self.addCleanup(device.deactivate)
        results = []
        device._upload.finished.connect(lambda ok, detail: results.append((ok, detail)))
        device.requestWrite(None, "part.gcode")
        device.acceptUpload("<root>", "renamed.gcode", True)
        for _ in range(200):
            if results:
                break
            self.qt.events(10)
        self.assertEqual(results, [(False, "Uploaded; the printer refused to start the print")])

    def test_upload_start_queued_is_a_distinct_outcome(self):
        class Handler(PipeSafeHandler):
            def do_GET(self):
                body = b'{"result": {"klippy_state": "ready"}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"result":{"item":{"path":"renamed.gcode"}}, "print_started": false, "print_queued": true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        client = self.client_module.MoonrakerClient()
        client.configure(url, "", 750)
        self.addCleanup(self._stop_client, client)
        config = self.config_type(url=url, upload_dialog=True, upload_start_print=True)
        self.install_dialog_factory(self.app)
        device = self.device_module.MoonrakerOutputDevice(
            self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None, active_identity=lambda: ("A", "A"),
        )
        self.addCleanup(device.deactivate)
        results = []
        device._upload.finished.connect(lambda ok, detail: results.append((ok, detail)))
        device.requestWrite(None, "part.gcode")
        device.acceptUpload("<root>", "renamed.gcode", True)
        for _ in range(200):
            if results:
                break
            self.qt.events(10)
        self.assertEqual(results, [(True, "")])
        self.assertEqual(device._upload.print_outcome, "queued")


if __name__ == "__main__":
    unittest.main()
