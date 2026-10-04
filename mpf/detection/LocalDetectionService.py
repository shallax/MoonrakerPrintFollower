"""One consent-gated, CPU-only local detection lane per Cura process."""

import os
import shutil
import threading
import queue
import time

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from . import AssetInstaller, EvidenceStore
from .DetectionAssets import (
    ASSET_VERSION, MODEL_SHA256, MODEL_SIZE, host_wheel, installed_paths,
)
from .DetectionObservation import DetectionSample, DetectionResult, ModelDetections, FRESH_SECONDS
from .DetectionMask import RegionResolutionError
from .LocalFailureModel import LocalFailureModel, foreign_runtime


_lane = threading.Lock()
_last_inference = 0.0
_CADENCE = 10.0
_BENCHMARK_LIMIT = 5.0
_RECORD = "localDetection"


class DetectionPersistenceError(OSError):
    """Settings failures remain visible even when setup was cancelled."""


class _MailboxSignal:
    def __init__(self, mailbox, kind):
        self._mailbox, self._kind = mailbox, kind

    def emit(self, *args):
        self._mailbox.put((self._kind, args))


class _Updates:
    """Workers only post Python data; they never touch deleted Qt objects."""
    def __init__(self):
        self.mailbox = queue.SimpleQueue()
        self.status = _MailboxSignal(self.mailbox, "status")
        self.result = _MailboxSignal(self.mailbox, "result")


class LocalDetectionService(QObject):
    stateChanged = pyqtSignal()
    resultReady = pyqtSignal(object, float)
    observationReady = pyqtSignal(object)

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
        self._load_requested = False
        self._release_requested = False
        self._io_tasks = {}
        self._model = None
        self._inferencing = False
        self._benchmark_ms = 0
        try:
            self._wheel = host_wheel()
            self._host_error = ""
        except ValueError as exc:
            self._wheel = None
            self._host_error = str(exc)
        self._record = self._read_record()
        self._updates = _Updates()
        self._mailbox_timer = QTimer(self)
        self._mailbox_timer.setInterval(50)
        self._mailbox_timer.timeout.connect(self._drain_mailbox)
        self._mailbox_timer.start()
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
        if self._closed or (not self._ready and self._record.get("ready_version") != ASSET_VERSION):
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
        self.reset()
        if enabled:
            self._ready = False
            self._phase = "checking"
        with self._condition:
            self._release_requested = not enabled
            self._load_requested = enabled
            self._condition.notify()
        self._error = ""
        self.stateChanged.emit()
        return True

    @pyqtProperty(str, notify=stateChanged)
    def host_error(self) -> str:
        return self._host_error

    @pyqtProperty(int, constant=True)
    def runtime_size(self) -> int:
        return self._wheel.size if self._wheel is not None else 0

    @pyqtProperty(int, notify=stateChanged)
    def benchmark_ms(self) -> int:
        """What one frame's inference cost here when it was checked, in
        milliseconds; 0 until a check has run."""
        return self._benchmark_ms

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
        try:
            saved = self._persistence.set_global({_RECORD: record})
        except Exception as exc:
            raise DetectionPersistenceError(f"Could not save local detection settings: {exc}") from exc
        if not saved:
            raise DetectionPersistenceError("Could not save local detection settings; check Cura's preferences directory")
        self._record = record

    @pyqtSlot()
    def setup(self):
        if self._record.get("cleanup_pending"):
            self._error = "Restart Cura to finish removing the loaded inference runtime"
            self.stateChanged.emit()
            return
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
        # Refuse a foreign already-imported runtime BEFORE the ~193 MiB
        # download: the load-time guard can only tell the user after the
        # whole install has been paid for.
        foreign = foreign_runtime(self._root)
        if foreign is not None:
            self._error = ("Another plugin has already imported its own ONNX Runtime (%s); "
                           "local detection needs its pinned build loaded first. Restart "
                           "Cura with detection as the only ONNX Runtime user." % foreign)
            self._phase = "error"
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
        model = self._model
        if model is not None:
            model.cancel_current()
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
        model = self._model
        if model is not None:
            model.cancel_current()
        try:
            self._persist({"cleanup_pending": True, "ready_version": "", "consent": False})
        except OSError as exc:
            self._error = str(exc)
            self._phase = "error"
            self.stateChanged.emit()
            return False
        with self._condition:
            self._generation += 1
            self._removal_generation = self._generation
            self._sample = None
            self._io_tasks.clear()
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

    def sample(self, image: QImage, context: tuple, captured_at=None, regions=()):
        if self._closed or not self._ready or not self.enabled or image.isNull():
            return
        with self._condition:
            self._sample = (DetectionSample(QImage(image), context, time.monotonic() if captured_at is None else captured_at, regions), self._generation)
            self._condition.notify()

    def enqueue_io(self, key, callback):
        with self._condition:
            if self._closed or self._remove_requested or self._record.get("cleanup_pending"):
                return False
            if len(self._io_tasks) >= 16 and key not in self._io_tasks:
                return False
            self._io_tasks[key] = callback
            self._condition.notify()
        return True

    def _drain_mailbox(self):
        while not self._closed:
            try:
                kind, args = self._updates.mailbox.get_nowait()
            except queue.Empty:
                return
            if kind == "status":
                self._on_status(*args)
            else:
                self._on_result(*args)

    @pyqtSlot()
    def reset(self):
        with self._condition:
            self._generation += 1
            self._sample = None
            self._condition.notify()
        model = self._model
        if self._inferencing and model is not None:
            model.cancel_current()

    def close(self):
        if self._closed:
            return
        with self._condition:
            self._closed = True
            self._cancel.set()
            self._generation += 1
            self._sample = None
            self._condition.notify()
        self._mailbox_timer.stop()
        self._ready = False
        self._busy = False
        self._phase = "closed"
        model = self._model
        if model is not None:
            model.cancel_current()
        if self._wheel is not None:
            self._worker.join(timeout=.15)
        # Native import/session construction may still be returning. The
        # daemon owns its model and posts to a Python mailbox, never QObject.


    def _send(self, generation, **status):
        self._updates.status.emit(generation, status)

    def _on_status(self, generation, status):
        if self._closed or (generation != self._generation and not self._busy) or (self._removal_generation is not None
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

    def _on_result(self, generation, result):
        if (not self._closed and generation == self._generation and self._ready and self.enabled
                and 0 <= time.monotonic() - result.sample.captured_at <= FRESH_SECONDS):
            self.observationReady.emit(result)
            self.resultReady.emit(result.sample.context, result.detections.score)

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
        # Inference has a cooperative RunOptions watchdog. Import/session
        # construction has no portable hard abort; model ownership stays
        # on this daemon and shutdown retires its mailbox delivery.
        if elapsed > _BENCHMARK_LIMIT:
            raise ValueError(f"Local inference took {elapsed:.1f}s (limit: 5s per frame)")
        # The measured cost, kept rather than discarded: the Diagnostics
        # tab reports what this computer actually spends per frame, and
        # the cadence's headroom is read from it.
        self._benchmark_ms = round(elapsed * 1000)

    def evidence_root(self):
        """The folder holding alert frames and per-print timelines."""
        return EvidenceStore.evidence_directory(self._root)

    def storage_root(self):
        return self._root

    def _verified(self):
        runtime, model = installed_paths(self._root)
        archive = os.path.join(os.path.dirname(model), self._wheel.filename)
        return (AssetInstaller.verify_file(archive, self._wheel.size, self._wheel.sha256)
                and AssetInstaller.verify_file(model, MODEL_SIZE, MODEL_SHA256)
                and AssetInstaller.verify_runtime(archive, runtime))

    def _startup(self):
        generation = self._generation
        if self._record.get("cleanup_pending"):
            self._removal_generation = generation
            self._remove_assets()
            if self._record.get("cleanup_pending"):
                return False
        if self._record.get("ready_version") != ASSET_VERSION:
            self._send(generation, phase="uninstalled")
            return False
        if not self.enabled:
            self._send(generation, ready=True, phase="disabled", error="")
            return False
        if not self._acquire_lane():
            return False
        try:
            if not self._verified():
                raise ValueError("Installed local detection assets failed integrity checks; reinstall them")
            runtime, model = installed_paths(self._root)
            self._load_and_benchmark(runtime, model)
            if self._closed or not self.enabled:
                self._model = None
                self._send(self._generation, ready=not self._closed, phase="disabled", error="")
                return False
            self._send(self._generation, ready=True, phase="ready", error="")
            return True
        except Exception as exc:
            self._model = None
            self._send(generation, ready=False, phase="error", error=str(exc))
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
            if not self.enabled:
                self._model = None
            self._send(generation, ready=True, busy=False, phase="ready" if self.enabled else "disabled", error="")
        except Exception as exc:
            self._model = None
            cancelled = isinstance(exc, AssetInstaller.DownloadCancelled) or (self._cancel.is_set() and not isinstance(exc, DetectionPersistenceError))
            if not already_verified:
                try:
                    for path in (model, archive):
                        if os.path.isfile(path):
                            os.unlink(path)
                    if os.path.isdir(runtime):
                        shutil.rmtree(runtime)
                except OSError as cleanup_error:
                    cancelled = False
                    try:
                        self._persist({"cleanup_pending": True, "ready_version": "", "consent": False})
                    except OSError as save_error:
                        cleanup_error = RuntimeError(f"{cleanup_error}; cleanup record could not be saved: {save_error}")
                    exc = RuntimeError(f"{exc}; restart Cura to finish cleanup: {cleanup_error}")
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
            # Tombstone is durable BEFORE deleting any part of an install.
            self._model = None
            self._persist({"cleanup_pending": True, "ready_version": "", "consent": False})
            EvidenceStore.clear(self._root)
            for path in (model, archive):
                if os.path.lexists(path):
                    if not os.path.isfile(path) and not os.path.islink(path):
                        raise ValueError(f"Local detection asset is not a file: {path}")
                    os.unlink(path)
            if os.path.lexists(runtime):
                if not os.path.isdir(runtime):
                    raise ValueError(f"Local detection runtime is not a directory: {runtime}")
                try:
                    shutil.rmtree(runtime)
                except PermissionError:
                    self._finish_removal(generation, "restart_required", "Restart Cura to finish removing the loaded inference runtime")
                    return
            # The alert frames and timelines go with the downloads: a
            # removal is the user asking for nothing of detection's to
            # stay on this computer.
            EvidenceStore.clear(self._root)
            self._benchmark_ms = 0
            self._persist({"ready_version": "", "consent": False, "offer_seen": False, "cleanup_pending": False})
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
            callback = None
            with self._condition:
                while not self._closed and not self._setup_requested \
                        and not self._remove_requested and not self._load_requested \
                        and not self._release_requested and not self._io_tasks and self._sample is None:
                    self._condition.wait()
                if self._closed and not self._remove_requested:
                    return
                if self._release_requested:
                    self._release_requested = False
                    self._model = None
                    self._send(self._generation, phase="disabled")
                    continue
                if self._load_requested:
                    self._load_requested = False
                    self._cancel.clear()
                    load = True
                else:
                    load = False
                if load:
                    pass
                elif self._remove_requested:
                    remove = True
                    setup = False
                    self._sample = None
                elif self._setup_requested:
                    remove = False
                    self._setup_requested = False
                    self._sample = None
                    setup = True
                elif self._io_tasks:
                    _, callback = self._io_tasks.popitem()
                else:
                    remove = False
                    setup = False
                    delay = max(0.0, _CADENCE - (time.monotonic() - _last_inference))
                    if delay:
                        self._condition.wait(timeout=delay)
                        continue
                    sample, generation = self._sample
                    self._sample = None
            if callback is not None:
                try:
                    callback()
                except Exception:
                    pass
                continue
            if load:
                self._startup()
                continue
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
                            self._sample = (sample, generation)
                        self._condition.wait(timeout=remaining)
                    continue
                if generation == self._generation and self._model is not None and self.enabled \
                        and 0 <= time.monotonic() - sample.captured_at <= FRESH_SECONDS:
                    _last_inference = time.monotonic()
                    self._inferencing = True
                    try:
                        detections = self._model.detect(sample.image, sample.regions)
                    finally:
                        self._inferencing = False
                    self._updates.result.emit(generation, DetectionResult(sample, detections))
            except RegionResolutionError as exc:
                self._updates.result.emit(generation, DetectionResult(sample, ModelDetections(0, ()), str(exc)))
            except Exception as exc:
                if self._closed or generation != self._generation or not self.enabled:
                    continue  # intentional retirement retains the usable session
                self._model = None
                with self._condition:
                    self._sample = None
                self._send(generation, ready=False, phase="error", error=str(exc))
            finally:
                _lane.release()
