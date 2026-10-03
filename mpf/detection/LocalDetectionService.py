"""One consent-gated, CPU-only local detection lane per Cura process."""

import os
import shutil
import threading
import time

from PyQt6.QtCore import QObject, Qt, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from . import AssetInstaller
from .DetectionAssets import (
    ASSET_VERSION, MODEL_SHA256, MODEL_SIZE, host_wheel, installed_paths,
)
from .LocalFailureModel import LocalFailureModel


_lane = threading.Lock()
_last_inference = 0.0
_CADENCE = 10.0
_BENCHMARK_LIMIT = 5.0
_RECORD = "localDetection"


class _Updates(QObject):
    status = pyqtSignal(int, object)
    result = pyqtSignal(int, object, float)


class LocalDetectionService(QObject):
    stateChanged = pyqtSignal()
    resultReady = pyqtSignal(object, float)

    def __init__(self, root: str, persistence, parent=None):
        super().__init__(parent)
        self._root = root
        self._persistence = persistence
        self._ready = False
        self._busy = False
        self._phase = "checking"
        self._received = 0
        self._total = 0
        self._error = ""
        self._closed = False
        self._generation = 0
        self._cancel = threading.Event()
        self._condition = threading.Condition()
        self._setup_requested = False
        self._remove_requested = False
        self._removal_generation = None
        self._removal_outcome = None
        self._sample = None
        self._model = None
        try:
            self._wheel = host_wheel()
            self._host_error = ""
        except ValueError as exc:
            self._wheel = None
            self._host_error = str(exc)
        self._record = self._read_record()
        self._updates = _Updates(self)
        self._updates.status.connect(self._on_status, Qt.ConnectionType.QueuedConnection)
        self._updates.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        if self._wheel is None:
            self._phase = "unsupported"
        else:
            self._worker = threading.Thread(target=self._run, name="mpf-local-detection", daemon=True)
            self._worker.start()

    def _read_record(self):
        section = self._persistence.settings_document().get("global", {})
        record = section.get(_RECORD) if isinstance(section, dict) else None
        return dict(record) if isinstance(record, dict) else {}

    @pyqtProperty(bool, notify=stateChanged)
    def ready(self) -> bool:
        return self._ready

    @pyqtProperty(bool, notify=stateChanged)
    def enabled(self) -> bool:
        return self._record.get("enabled", True) is True

    @pyqtSlot(bool, result=bool)
    def set_enabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise ValueError("Global detection enablement must be a checkbox value")
        if self._closed or not self._ready:
            self._error = "Set up the local model before changing global detection"
            self.stateChanged.emit()
            return False
        if enabled == self.enabled:
            return True
        try:
            self._persist({"enabled": enabled})
        except OSError as exc:
            self._error = str(exc)
            self.stateChanged.emit()
            return False
        if not enabled:
            self.reset()
        self._error = ""
        self.stateChanged.emit()
        return True

    @pyqtProperty(str, notify=stateChanged)
    def host_error(self) -> str:
        return self._host_error

    @pyqtProperty(int, constant=True)
    def runtime_size(self) -> int:
        return self._wheel.size if self._wheel is not None else 0

    @pyqtProperty(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._busy

    @pyqtProperty(str, notify=stateChanged)
    def phase(self) -> str:
        return self._phase

    @pyqtProperty(int, notify=stateChanged)
    def received(self) -> int:
        return self._received

    @pyqtProperty(int, notify=stateChanged)
    def total(self) -> int:
        return self._total

    @pyqtProperty(str, notify=stateChanged)
    def error(self) -> str:
        return self._error

    @pyqtProperty(bool, notify=stateChanged)
    def should_offer(self) -> bool:
        return not self._closed and not self._host_error \
            and not self._record.get("offer_seen", False)

    def _persist(self, patch):
        record = {**self._record, **patch}
        if not self._persistence.set_global({_RECORD: record}):
            raise OSError("Could not save local detection settings; check Cura's preferences directory")
        self._record = record

    @pyqtSlot()
    def setup(self):
        if self._remove_requested:
            self._error = "Cannot set up local detection while asset removal is in progress"
            self._phase = "error"
            self.stateChanged.emit()
            return
        if self._host_error and not self._closed:
            self._error = self._host_error
            self._phase = "unsupported"
            self.stateChanged.emit()
            return
        if self._closed or self._busy or self._ready:
            return
        self._cancel.clear()
        self._busy = True
        self._error = ""
        self._phase = "preparing"
        self._received = self._total = 0
        with self._condition:
            self._setup_requested = True
            self._condition.notify()
        self.stateChanged.emit()

    @pyqtSlot()
    def cancel(self):
        if not self._busy or self._closed or self._remove_requested:
            return
        self._cancel.set()
        with self._condition:
            self._sample = None
            self._condition.notify()
        self._phase = "cancelling"
        self.stateChanged.emit()

    @pyqtSlot()
    def decline_offer(self):
        if self._closed or self._host_error:
            return
        try:
            self._persist({"offer_seen": True})
            self._error = ""
        except OSError as exc:
            self._error = str(exc)
        self.stateChanged.emit()

    def reset_offer(self):
        self._persist({"offer_seen": False})
        self.stateChanged.emit()

    @pyqtSlot(result=bool)
    def remove_assets(self) -> bool:
        if self._closed or self._wheel is None or self._busy:
            self._error = ("Cannot remove local detection assets during setup or removal"
                           if self._busy else
                           "Cannot remove local detection assets: service is closed or host is unsupported")
            self._phase = "error"
            self.stateChanged.emit()
            return False
        with self._condition:
            self._generation += 1
            self._removal_generation = self._generation
            self._sample = None
            self._model = None
            self._cancel.clear()
            self._remove_requested = True
            self._removal_outcome = None
            self._busy = True
            self._ready = False
            self._phase = "removing"
            self._error = ""
            self._received = self._total = 0
            self._condition.notify()
        self.stateChanged.emit()
        return True

    def sample(self, image: QImage, context: tuple):
        if self._closed or not self._ready or not self.enabled or image.isNull():
            return
        with self._condition:
            self._sample = (QImage(image), context, self._generation)
            self._condition.notify()

    @pyqtSlot()
    def reset(self):
        with self._condition:
            self._generation += 1
            self._sample = None
            self._condition.notify()

    def close(self):
        if self._closed:
            return
        with self._condition:
            self._closed = True
            self._cancel.set()
            self._generation += 1
            self._sample = None
            self._condition.notify()
        if self._wheel is not None:
            self._worker.join(timeout=6)
            if self._worker.is_alive():
                self._error = "Local detection worker did not stop within six seconds"
                self._phase = "error"
                self.stateChanged.emit()
                raise RuntimeError(self._error)
        if self._busy and self._removal_outcome is not None:
            self._busy = False
            self._ready = False
            self._phase = self._removal_outcome["phase"]
            self._error = self._removal_outcome["error"]
            self._remove_requested = False
            self.stateChanged.emit()
        self._model = None

    def _send(self, generation, **status):
        self._updates.status.emit(generation, status)

    def _on_status(self, generation, status):
        if self._closed or (self._removal_generation is not None
                            and generation < self._removal_generation):
            return
        if self._busy and status.get("phase") == "uninstalled" \
                and status.get("busy") is not False:
            return
        if self._remove_requested and generation == self._removal_generation:
            if status.get("busy") is False:
                self._remove_requested = False
            elif status.get("phase") != "removing":
                return
        for key, value in status.items():
            setattr(self, "_" + key, value)
        self.stateChanged.emit()

    def _on_result(self, generation, context, score):
        if not self._closed and generation == self._generation and self._ready and self.enabled:
            self.resultReady.emit(context, score)

    def _acquire_lane(self, removal=False):
        while not self._closed and (removal or not self._cancel.is_set()):
            if _lane.acquire(timeout=.1):
                if removal:
                    with self._condition:
                        if self._closed:
                            _lane.release()
                            return False
                return True
        return False

    def _load_and_benchmark(self, runtime, model):
        self._model = LocalFailureModel.load(model, runtime)
        image = QImage(416, 416, QImage.Format.Format_RGB888)
        image.fill(0)
        self._model.score(image)  # warm up the session before measuring the actual inference
        start = time.monotonic()
        self._model.score(image)
        elapsed = time.monotonic() - start
        if elapsed > _BENCHMARK_LIMIT:
            raise ValueError(f"Local inference took {elapsed:.1f}s (limit: 5s per frame)")

    def _verified(self):
        runtime, model = installed_paths(self._root)
        archive = os.path.join(os.path.dirname(model), self._wheel.filename)
        return (AssetInstaller.verify_file(archive, self._wheel.size, self._wheel.sha256)
                and AssetInstaller.verify_file(model, MODEL_SIZE, MODEL_SHA256)
                and AssetInstaller.verify_runtime(archive, runtime))

    def _startup(self):
        generation = self._generation
        if self._record.get("ready_version") != ASSET_VERSION:
            self._send(generation, phase="uninstalled")
            return False
        if not self._acquire_lane():
            return False
        try:
            if not self._verified():
                raise ValueError("Installed local detection assets failed integrity checks; reinstall them")
            runtime, model = installed_paths(self._root)
            self._load_and_benchmark(runtime, model)
            self._send(generation, ready=True, phase="ready", error="")
            return True
        except Exception as exc:
            self._model = None
            self._send(generation, phase="error", error=str(exc))
            return False
        finally:
            _lane.release()

    def _install(self):
        generation = self._generation
        if not self._acquire_lane():
            self._send(generation, busy=False, phase="cancelled")
            return
        runtime, model = installed_paths(self._root)
        archive = os.path.join(os.path.dirname(model), self._wheel.filename)
        try:
            already_verified = self._verified()
        except (OSError, ValueError):
            already_verified = False
        try:
            AssetInstaller._check_cancelled(self._cancel)
            self._persist({"offer_seen": True, "consent": True, "ready_version": ""})
            self._send(generation, phase="runtime")
            runtime, model = AssetInstaller.install(
                self._root, self._wheel, self._cancel,
                lambda phase, received, total:
                    self._send(generation, phase=phase, received=received, total=total),
            )
            AssetInstaller._check_cancelled(self._cancel)
            self._send(generation, phase="benchmark")
            self._load_and_benchmark(runtime, model)
            AssetInstaller._check_cancelled(self._cancel)
            self._persist({"ready_version": ASSET_VERSION})
            if self._cancel.is_set():
                self._persist({"ready_version": ""})
                raise AssetInstaller.DownloadCancelled("Local detection setup cancelled")
            self._send(generation, ready=True, busy=False, phase="ready", error="")
        except Exception as exc:
            self._model = None
            if not already_verified:
                try:
                    for path in (model, archive):
                        if os.path.isfile(path):
                            os.unlink(path)
                    if os.path.isdir(runtime):
                        shutil.rmtree(runtime)
                except OSError as cleanup_error:
                    exc = RuntimeError(f"{exc}; cleanup failed: {cleanup_error}")
            cancelled = isinstance(exc, AssetInstaller.DownloadCancelled)
            self._send(generation, ready=False, busy=False,
                       phase="cancelled" if cancelled else "error",
                       error="" if cancelled else str(exc))
        finally:
            _lane.release()

    def _finish_removal(self, generation, phase, error=""):
        status = {"busy": False, "ready": False, "phase": phase, "error": error}
        with self._condition:
            self._removal_outcome = status
        self._send(generation, **status)

    def _remove_assets(self):
        generation = self._removal_generation
        if not self._acquire_lane(removal=True):
            self._finish_removal(
                generation, "error",
                "Local detection asset removal stopped: service closed before the worker lane was available",
            )
            return
        try:
            runtime, model = installed_paths(self._root)
            directory = os.path.dirname(model)
            archive = os.path.join(directory, self._wheel.filename)
            if (os.path.basename(self._wheel.filename) != self._wheel.filename
                    or any(os.path.islink(path) for path in
                           (self._root, os.path.join(self._root, "detection"),
                            directory, runtime))):
                raise ValueError("Unsafe local detection asset path (symbolic link)")
            for path in (model, archive):
                if os.path.lexists(path):
                    if not os.path.isfile(path) and not os.path.islink(path):
                        raise ValueError(f"Local detection asset is not a file: {path}")
                    os.unlink(path)
            if os.path.lexists(runtime):
                if not os.path.isdir(runtime):
                    raise ValueError(f"Local detection runtime is not a directory: {runtime}")
                shutil.rmtree(runtime)
            self._persist({"ready_version": "", "consent": False, "offer_seen": False})
            self._finish_removal(generation, "uninstalled")
        except Exception as exc:
            self._finish_removal(generation, "error", str(exc))
        finally:
            self._model = None
            _lane.release()

    def _run(self):
        if self._startup():
            with self._condition:
                if self._setup_requested:
                    self._setup_requested = False
                    self._send(self._generation, busy=False)
        global _last_inference
        while True:
            with self._condition:
                while not self._closed and not self._setup_requested \
                        and not self._remove_requested and self._sample is None:
                    self._condition.wait()
                if self._closed and not self._remove_requested:
                    return
                if self._remove_requested:
                    remove = True
                    setup = False
                    self._sample = None
                elif self._setup_requested:
                    remove = False
                    self._setup_requested = False
                    self._sample = None
                    setup = True
                else:
                    remove = False
                    setup = False
                    delay = max(0.0, _CADENCE - (time.monotonic() - _last_inference))
                    if delay:
                        self._condition.wait(timeout=delay)
                        continue
                    image, context, generation = self._sample
                    self._sample = None
            if remove:
                self._remove_assets()
                with self._condition:
                    self._remove_requested = False
                continue
            if setup:
                self._install()
                continue
            if not self._acquire_lane():
                continue
            try:
                remaining = _CADENCE - (time.monotonic() - _last_inference)
                if remaining > 0:
                    with self._condition:
                        if self._sample is None and generation == self._generation:
                            self._sample = (image, context, generation)
                        self._condition.wait(timeout=remaining)
                    continue
                if generation == self._generation and self._model is not None:
                    _last_inference = time.monotonic()
                    self._updates.result.emit(generation, context, self._model.score(image))
            except Exception as exc:
                self._model = None
                with self._condition:
                    self._sample = None
                self._send(generation, ready=False, phase="error", error=str(exc))
            finally:
                _lane.release()
