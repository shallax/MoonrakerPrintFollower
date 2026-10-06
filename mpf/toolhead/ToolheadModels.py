"""One optional CAD worker and a transactional settings draft per active printer."""
from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from ..geometry.ToolheadGeometry import default_mesh, valid_tip
from ..geometry.ToolheadLighting import MAX_LIGHTS, validated_lights
from .CadRuntime import install_runtime, runtime_assets, runtime_directory
from .ToolheadImport import read_step, read_stl


class ToolheadModels(QObject):
    changed = pyqtSignal()
    lightingPreviewChanged = pyqtSignal()
    completed = pyqtSignal(int, object, str, str)
    progress = pyqtSignal(int, str)
    elapsedChanged = pyqtSignal()

    def __init__(self, store, runtime_root, config_source, identity_source, parent=None):
        super().__init__(parent)
        self.store, self.runtime_root = store, runtime_root
        self._config, self._identity = config_source, identity_source
        self._generation = 0
        self._worker = None
        self._cancelled = threading.Event()
        self._closed = False
        self._pending = ""
        self._status = ""
        self._started = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self.elapsedChanged.emit)
        self._key = self._name = ""
        self._mesh = default_mesh()
        self._manual = False
        self._imported = False
        self._texts = ["0", "0", "0"]
        self._lights = []
        self._edit_checkpoint = None
        self.completed.connect(self._complete)
        self.progress.connect(self._progress)
        self.reset()

    @property
    def mesh(self): return self._mesh

    @property
    def tip(self): return valid_tip(self._texts)

    @pyqtProperty("QVariantList", notify=changed)
    def lights(self): return validated_lights(self._lights)

    def pickedLight(self, position, direction, surface=0):
        if self.busy or len(self._lights) >= MAX_LIGHTS: return
        self._lights = validated_lights(self._lights + [dict(position=position, direction=direction, surface=surface)])
        self.changed.emit()

    @pyqtSlot(int, bool)
    def setLightPaint(self, index, paint):
        if self.busy or not 0 <= index < len(self._lights): return
        self._lights[index]['paint'] = bool(paint)
        self.changed.emit()

    @pyqtSlot(int)
    def removeLight(self, index):
        if not self.busy and 0 <= index < len(self._lights):
            del self._lights[index]
            self.changed.emit()

    @pyqtSlot(int, str)
    def setLightColour(self, index, colour):
        if self.busy or not 0 <= index < len(self._lights): return
        updated = validated_lights([dict(self._lights[index], colour=colour)])
        if updated:
            self._lights[index] = updated[0]
            self.changed.emit()

    def _brightness(self, index, brightness):
        if self.busy or not 0 <= index < len(self._lights): return False
        updated = validated_lights([dict(self._lights[index], brightness=brightness)])
        if not updated: return False
        self._lights[index] = updated[0]
        return True

    @pyqtSlot(int, float)
    def previewLightBrightness(self, index, brightness):
        if self._brightness(index, brightness):
            # Only the renderer observes this. Publishing the list would
            # replace the QML delegate while it owns the mouse grab.
            self.lightingPreviewChanged.emit()

    @pyqtSlot(int, float)
    def setLightBrightness(self, index, brightness):
        if self._brightness(index, brightness): self.changed.emit()

    @pyqtSlot()
    def beginEdit(self):
        if not self.busy:
            self._edit_checkpoint = (self._draft_identity, self._mesh, list(self._texts),
                                     self._manual, self.lights)

    @pyqtSlot(bool)
    def endEdit(self, accept):
        checkpoint, self._edit_checkpoint = self._edit_checkpoint, None
        if checkpoint is None or accept or self.busy: return
        identity, mesh, texts, manual, lights = checkpoint
        if identity != self._identity() or mesh is not self._mesh: return
        self._texts, self._manual, self._lights = texts, manual, lights
        self.changed.emit()

    @pyqtProperty(str, notify=changed)
    def name(self): return self._name or "Default indicator"

    @pyqtProperty(str, notify=changed)
    def status(self): return self._status

    @pyqtProperty(str, notify=elapsedChanged)
    def elapsedText(self):
        if self._started is None: return ""
        seconds = max(0, int(time.monotonic() - self._started))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        duration = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        return "Elapsed: " + duration

    def _stop_elapsed(self):
        self._elapsed_timer.stop()
        self._started = None
        self.elapsedChanged.emit()

    @pyqtProperty(bool, notify=changed)
    def busy(self): return self._worker is not None

    @pyqtProperty(bool, notify=changed)
    def needsDownload(self): return bool(self._pending)

    @pyqtProperty(bool, notify=changed)
    def valid(self): return not self.busy and self.tip is not None and not self._pending

    @pyqtProperty(bool, notify=changed)
    def manual(self): return self._manual

    @pyqtProperty(str, notify=changed)
    def tipX(self): return self._texts[0]

    @pyqtProperty(str, notify=changed)
    def tipY(self): return self._texts[1]

    @pyqtProperty(str, notify=changed)
    def tipZ(self): return self._texts[2]

    def _set_tip(self, point, manual):
        self._texts = [format(v, ".6g") for v in point]
        self._manual = manual
        self.changed.emit()

    @pyqtSlot(int, str)
    def setTip(self, axis, text):
        if axis not in (0, 1, 2) or self.busy: return
        self._texts[axis] = text
        self._manual = True
        self.changed.emit()

    def picked(self, point):
        if point is not None and not self.busy: self._set_tip(point, True)

    @pyqtSlot()
    def automatic(self): self._set_tip(self.mesh.automatic_tip, False)

    @pyqtSlot()
    def reset(self):
        self._stop_elapsed()
        self._edit_checkpoint = None
        self._generation += 1
        self._cancelled.set()
        self._pending = ""
        self._imported = False
        config = self._config()
        self._lights = validated_lights(getattr(config, "toolhead_lights", []))
        self._draft_identity = self._identity()
        self._key = getattr(config, "toolhead_model", "")
        self._name = getattr(config, "toolhead_model_name", "")
        self._status = ""
        self._mesh = default_mesh()
        missing = False
        if self._key:
            try: self._mesh = self.store.load(self._key)
            except (OSError, ValueError) as error:
                self._status = "Saved model unavailable: " + str(error)
                self._key = self._name = ""
                missing = True
        tip = valid_tip(getattr(config, "toolhead_tip", [])) if not missing else None
        self._set_tip(tip or self.mesh.automatic_tip, tip is not None)

    @pyqtSlot()
    def useDefault(self):
        if self.busy: return
        self._pending = self._key = self._name = self._status = ""
        self._imported = False
        self._mesh = default_mesh()
        self.automatic()
        self._lights = []
        self.changed.emit()

    @pyqtSlot(str)
    def choose(self, url):
        if self.busy: return
        self._pending = ""
        parsed = QUrl(url)
        path = parsed.toLocalFile() if parsed.isLocalFile() else ""
        if not path:
            self._status = "Choose a local STL, STEP or STP file."
            self.changed.emit()
            return
        extension = os.path.splitext(path)[1].lower()
        if extension in (".step", ".stp"):
            try:
                assets = runtime_assets()
                size = sum(pin["size"] for _, pin in assets)
                # Consent is explicit on the first STEP use, even though installing
                # the reader does not upload the selected model.
                marker = os.path.join(runtime_directory(self.runtime_root, assets), "verified.json")
                if not os.path.isfile(marker):
                    self._pending = path
                    self._status = f"STEP needs a local CAD reader and isolated helper ({size / 1048576:.0f} MiB from PyPI and GitHub). Your model stays on this computer."
                    self.changed.emit()
                    return
            except ValueError as error:
                self._status = str(error)
                self.changed.emit()
                return
        elif extension != ".stl":
            self._status = "Choose an STL, STEP or STP file."
            self.changed.emit()
            return
        self._start(path, extension != ".stl")

    @pyqtSlot()
    def downloadAndImport(self):
        if self._pending and not self.busy:
            path, self._pending = self._pending, ""
            self._start(path, True)

    @pyqtSlot()
    def cancel(self):
        self._generation += 1
        self._cancelled.set()
        self._pending = ""
        self._status = "Finishing cancelled import…" if self.busy else ""
        self.changed.emit()

    def _start(self, path, step):
        self._generation += 1
        generation = self._generation
        self._cancelled = cancel = threading.Event()
        self._status = "Reading model…"
        self._started = time.monotonic()
        self._elapsed_timer.start()
        self.elapsedChanged.emit()
        self._draft_identity = self._identity()

        def run():
            mesh, key, error = None, "", ""
            try:
                if step:
                    runtime = install_runtime(self.runtime_root, cancel,
                        lambda text: self.progress.emit(generation, text))
                    self.progress.emit(generation, "Converting STEP assembly…")
                    mesh = read_step(path, runtime, cancel,
                        progress=lambda text: self.progress.emit(generation, text))
                else:
                    mesh = read_stl(path, cancel)
            except Exception as failure:
                error = str(failure) or type(failure).__name__
            try: self.completed.emit(generation, (mesh, key, os.path.basename(path)), error, path)
            except RuntimeError: pass  # host shutdown retires the QObject

        self._worker = threading.Thread(target=run, name="MPF toolhead import", daemon=True)
        self._worker.start()
        self.changed.emit()

    def _progress(self, generation, text):
        if generation == self._generation and not self._closed:
            self._status = text
            self.changed.emit()

    def _complete(self, generation, result, error, _path):
        self._worker = None
        self._stop_elapsed()
        if self._closed: return
        if generation != self._generation or self._identity() != self._draft_identity:
            self._status = ""
            self.changed.emit()
            return
        if error:
            self._status = "Import failed: " + error
        else:
            self._mesh, self._key, self._name = result
            self._lights = []
            self._imported = True
            self._status = f"Ready · {len(self.mesh.triangles):,} triangles"
            self.automatic()
        self.changed.emit()

    def fields(self):
        if not self.valid or self._identity() != self._draft_identity:
            raise ValueError("Toolhead model is not ready to save")
        # Cancelled imports never publish assets. Explicit Save publishes the
        # immutable mesh first; the existing settings write remains adoption.
        if self._imported:
            self._key = self.store.publish(self.mesh)
            self._imported = False
        return {"toolhead_model": self._key, "toolhead_model_name": self._name,
                "toolhead_lights": self.lights,
                "toolhead_tip": list(self.tip) if self._manual else []}

    def close(self):
        self._stop_elapsed()
        self._closed = True
        self._generation += 1
        self._cancelled.set()
