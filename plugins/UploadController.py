"""One upload-operation owner: identity, folder discovery, readiness and streaming.

No Cura application or QML references. The adapter supplies a prepared file lease
and translates signals to Cura's write lifecycle and dialogs.
"""
from __future__ import annotations
from dataclasses import asdict
import os
from urllib.parse import urlencode

from PyQt6.QtCore import QByteArray, QFile, QIODevice, QObject, QTimer, QVariant, pyqtSignal
from PyQt6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkReply, QNetworkRequest

from .PrinterConfig import PrinterConfig


class UploadController(QObject):
    changed = pyqtSignal()
    dialogClosed = pyqtSignal()
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    choicesAccepted = pyqtSignal(str, bool)
    MAX_READY_ATTEMPTS = 21
    MAX_DIRECTORIES = 256

    def __init__(self, client, machine_id, active_identity, parent=None):
        super().__init__(parent)
        self._client, self._machine_id, self._active_identity = client, machine_id, active_identity
        self._generation = 0
        self._session = None
        self._active = self._terminal_pending = self._choice_pending = False
        self._reply = self._file = self._source = None
        self._config = PrinterConfig()
        self._filename, self._path = "", ""
        self._start_print = False
        self._directories, self._queue, self._seen = set(), [], set()
        self._power = []
        self._attempts = 0
        client.sessionInvalidated.connect(self.abort)

    @property
    def busy(self): return self._active or self._terminal_pending
    @property
    def filename(self): return self._filename
    @property
    def path(self): return self._path
    @property
    def start_print(self): return self._start_print
    @property
    def paths(self): return ["<root>"] + sorted(self._directories, key=str.casefold)
    @property
    def config(self): return PrinterConfig.from_dict(asdict(self._config))
    @property
    def owner(self): return "upload:" + self._machine_id

    def begin(self, config, filename):
        if self.busy: raise RuntimeError("Upload already in progress")
        if self._machine_id != self._active_identity()[0]: raise ValueError("Only the active Cura printer can receive an upload")
        if (config.url.strip().rstrip("/"), config.api_key) != self._client.transport.identity:
            raise ValueError("Moonraker is being reconfigured; retry the upload")
        self._generation += 1
        self._session = self._client.session.generation
        self._config = PrinterConfig.from_dict(asdict(config))
        self._filename = filename
        self._path = self.normalise_path(config.upload_path)
        self._start_print = config.upload_start_print
        self._active, self._choice_pending = True, False
        self._directories = {self.normalise_path(path) for path in config.upload_paths if self.valid_path(path)}
        if self._path and self.valid_path(self._path): self._directories.add(self._path)
        self._directories.discard("")
        self.changed.emit()

    def prepared(self, source):
        if not self._current():
            source.close()
            return
        if self._source is not None:
            source.close()
            raise RuntimeError("This upload already owns a prepared file")
        self._source = source
        self._filename = source.filename
        self.changed.emit()

    def _current(self, generation=None):
        return (self._active and (generation is None or generation == self._generation)
            and self._session == self._client.session.generation
            and self._machine_id == self._active_identity()[0]
            and (self._config.url.strip().rstrip("/"), self._config.api_key) == self._client.transport.identity)

    def _later(self, delay, callback):
        generation = self._generation
        def run():
            if self._current(generation): callback()
        QTimer.singleShot(delay, run)

    def _request(self, method, path, callback, *, body=None):
        if not self._current(): return
        generation = self._generation
        def finished(payload, error):
            if self._current(generation): callback(payload, error)
        self._client.transport.send_json(self.owner, "json", method, path, finished,
            body=body, replace=True, timeout_ms=15000,
            category="command" if method == "POST" else "discovery")

    @staticmethod
    def normalise_path(path):
        path = str(path or "").strip().strip("/")
        return "" if path == "<root>" else path

    @staticmethod
    def valid_path(path):
        return all(not part.startswith(".") for part in str(path or "").replace("\\", "/").split("/") if part)

    @staticmethod
    def normalise_filename(name):
        name = os.path.basename(str(name or "").strip())
        if name in {"", ".", ".."} or any(c in name for c in ':*?"<>|\r\n'): return ""
        return name

    def discover(self):
        self._queue, self._seen = ["gcodes"], {"gcodes"}
        self._scan()

    def _scan(self):
        if not self._current() or not self._queue or self._choice_pending: return
        path = self._queue.pop(0)
        def received(payload, error):
            value = (payload or {}).get("result") or {}
            if not error and isinstance(value, dict):
                for item in value.get("dirs") or ():
                    if not isinstance(item, dict): continue
                    name = str(item.get("dirname") or "").strip("/\\ ")
                    if not name or not self.valid_path(name) or "w" not in str(item.get("permissions", "rw")): continue
                    full = path + "/" + name
                    if full in self._seen or len(self._seen) >= self.MAX_DIRECTORIES: continue
                    self._seen.add(full)
                    self._directories.add(full[len("gcodes/"):])
                    self._queue.append(full)
            self.changed.emit()
            self._scan()
        self._request("GET", "server/files/directory?" + urlencode({"path": path}), received)

    def accept(self, path, filename, start_print):
        filename = self.normalise_filename(filename)
        if not self._current() or self._choice_pending or not filename: return
        if "." not in filename:
            filename += os.path.splitext(self._filename)[1]
        self._choice_pending = True
        path = self.normalise_path(path)
        if not self.valid_path(path):
            self._choice_pending = False
            return
        self._path, self._filename, self._start_print = path, filename, bool(start_print)
        def finish():
            self._choice_pending = False
            self._queue.clear()
            self._client.transport.cancel_owner(self.owner)
            self.choicesAccepted.emit(self._path, self._start_print)
            self.dialogClosed.emit()
            self.start()
        self._later(0, finish)

    def cancel(self):
        if not self._current() or self._choice_pending: return
        self._choice_pending = True
        self._later(0, lambda: self._finish(False, ""))

    def start(self):
        if not self._current() or self._source is None: return
        self._attempts = 0
        self._power = [name.strip() for name in self._config.power_devices.split(",") if name.strip()]
        if self._start_print and self._power:
            name = self._power[0]
            def received(payload, error):
                if error: self.fail("Could not query power device: " + error); return
                result = (payload or {}).get("result") or {}
                if str(next(iter(result.values()), "")).lower() == "off": self._power_on()
                else: self._ready()
            self._request("GET", "machine/device_power/device?" + urlencode({"device": name}), received)
        elif self._start_print: self._ready()
        else: self._upload()

    def _power_on(self):
        if not self._power:
            self._client.force_refresh()
            self._ready()
            return
        name = self._power.pop(0)
        self._request("POST", "machine/device_power/device?" + urlencode({"device": name, "action": "on"}),
            lambda payload, error: self.fail("Could not turn on power: " + error) if error else self._power_on(), body={})

    def _ready(self):
        if not self._current(): return
        if self._client.connected and isinstance(self._client.status.get("print_stats"), dict):
            self._upload()
            return
        if self._attempts >= self.MAX_READY_ATTEMPTS:
            self.fail("Klippy did not become ready in time")
            return
        self._attempts += 1
        def received(payload, error):
            result = (payload or {}).get("result") or {}
            if not error and result.get("klippy_state") == "ready": self._upload()
            else:
                self.status.emit(f"Waiting for printer readiness ({self._attempts}/{self.MAX_READY_ATTEMPTS})")
                self._later(max(100, int(self._config.ready_retry_interval_s * 1000)), self._ready)
        self._request("GET", "server/info", received)

    def _upload(self):
        if not self._current() or self._reply is not None or self._source is None: return
        try:
            file = QFile(self._source.path)
            if not file.open(QIODevice.OpenModeFlag.ReadOnly): raise OSError(file.errorString())
            self._file = file
            multipart = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
            part = QHttpPart()
            part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader, QVariant(f'form-data; name="file"; filename="{self._filename}"'))
            part.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, QVariant("application/octet-stream"))
            part.setBodyDevice(file)
            multipart.append(part)
            fields = {"root": "gcodes"}
            if self._path: fields["path"] = self._path
            if self._start_print: fields["print"] = "true"
            for name, value in fields.items():
                part = QHttpPart()
                part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader, QVariant(f'form-data; name="{name}"'))
                part.setBody(QByteArray(value.encode()))
                multipart.append(part)
            request = self._client.transport.request("server/files/upload", timeout_ms=30000)
            reply = self._client.transport.network.post(request, multipart)
            multipart.setParent(reply)
            file.setParent(reply)
            self._reply = reply
            generation = self._generation
            reply.uploadProgress.connect(lambda sent, total:
                self.progress.emit(max(0, min(100, int(sent * 100 / total)))) if total > 0 and self._current(generation) else None)
            reply.finished.connect(lambda: self._uploaded(reply, generation))
            self.status.emit("Uploading " + self._filename)
        except Exception as error:
            self.fail(str(error))

    def _uploaded(self, reply, generation):
        if reply is not self._reply or not self._current(generation):
            reply.deleteLater()
            return
        error = reply.errorString() if reply.error() != QNetworkReply.NetworkError.NoError else ""
        if not error:
            import json
            try:
                payload = json.loads(bytes(reply.readAll()).decode())
                if isinstance(payload, dict) and payload.get("error"): error = str(payload["error"])
            except (ValueError, UnicodeError): pass  # successful legacy non-JSON response
        self._finish(not bool(error), error)

    def fail(self, message):
        if self._active: self._finish(False, str(message))

    def abort(self):
        if self._active: self._finish(False, "")

    def _finish(self, success, error):
        if not self._active: return
        self._generation += 1
        self._active, self._terminal_pending, self._choice_pending = False, True, False
        self._client.transport.cancel_owner(self.owner)
        reply, self._reply = self._reply, None
        if reply is not None:
            if reply.isRunning(): reply.abort()
            reply.deleteLater()
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._source is not None:
            self._source.close()
            self._source = None
        self._queue.clear()
        self.dialogClosed.emit()
        self.changed.emit()
        def terminal():
            self.finished.emit(success, error)
        QTimer.singleShot(0, terminal)

    def terminal_delivered(self):
        """Adapter acknowledges completion immediately before writeFinished."""
        self._terminal_pending = False



