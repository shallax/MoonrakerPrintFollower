"""One optional CAD worker and a transactional settings draft per active printer."""
from __future__ import annotations

import os
import threading
import time
import numpy as np

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from ..geometry.ToolheadGeometry import default_mesh, valid_tip
from ..geometry.ToolheadMaterials import (material_profile, surface_detail, MATERIAL_TYPES, TYPE_PROFILES, MATERIAL_LABELS,
    MAX_PAINTED_FACES, material_overrides, painted_materials, unit_value, local_finish_overrides, resolved_finishes, material_parameters)
from ..geometry.ToolheadOpacity import opacity_overrides, opacity_colours, selection_mask, MAX_OPACITY_ENTRIES
from ..geometry.ToolheadRotors import rotors, body_axis, MAX_ROTORS, speed
from ..geometry.ToolheadLighting import MAX_LIGHTS, validated_lights
from .CadRuntime import install_runtime, runtime_assets, runtime_directory
from .ToolheadImport import read_step, read_stl


class ToolheadModels(QObject):
    changed = pyqtSignal()
    lightingPreviewChanged = pyqtSignal()
    fanReadingsChanged = pyqtSignal()
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
        self._surface_detail = .35
        self._material_overrides, self._surface_materials = {}, {}
        self._body_opacity, self._face_opacity = {}, {}
        self._body_materials, self._body_finishes, self._face_finishes = {}, {}, {}
        self._opacity_bodies, self._opacity_faces = set(), set()
        self._opacity_kind = "body"
        self._paint_kind = "plastic"
        self._lights = []
        self._rotors = []
        self._rotor_candidate = {}
        self._fan_readings = {}
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

    @pyqtProperty(float, notify=changed)
    def surfaceDetail(self): return self._surface_detail

    @pyqtProperty("QVariantMap", notify=changed)
    def materialOverrides(self): return material_overrides(self._material_overrides)

    @pyqtProperty("QVariantMap", notify=changed)
    def surfaceMaterials(self): return dict(self._surface_materials)

    @pyqtProperty("QVariantMap", notify=changed)
    def bodyOpacity(self): return dict(self._body_opacity)

    @pyqtProperty("QVariantMap", notify=changed)
    def faceOpacity(self): return dict(self._face_opacity)

    @pyqtProperty("QVariantList", notify=changed)
    def opacityBodies(self): return sorted(self._opacity_bodies)

    @pyqtProperty("QVariantList", notify=changed)
    def opacityFaces(self): return sorted(self._opacity_faces)

    @pyqtProperty(str, notify=changed)
    def opacityKind(self): return self._opacity_kind

    @pyqtProperty(int, notify=changed)
    def opacitySelectionCount(self): return len(self._opacity_bodies) + len(self._opacity_faces)

    @pyqtProperty(float, notify=changed)
    def selectedOpacity(self): return self._selection_summary()["opacity"]

    @pyqtProperty("QVariantMap", notify=changed)
    def selectedSources(self): return dict(self._selection_summary()["sources"])

    def _selection_summary(self):
        key = (id(self.mesh), repr((self._body_materials, self._surface_materials, self._body_finishes,
            self._face_finishes, self._material_overrides, self._body_opacity, self._face_opacity)),
            tuple(sorted(self._opacity_bodies)), tuple(sorted(self._opacity_faces)))
        cached = getattr(self, "_selection_cache", None)
        if cached is not None and cached[0] == key: return cached[1]
        if not self._opacity_bodies and not self._opacity_faces:
            result = dict(material="mixed", roughness=-1., reflectivity=-1., opacity=-1.,
                sources={name: "Automatic" for name in ("material", "roughness", "reflectivity", "opacity")})
            self._selection_cache = key, result
            return result
        mask = selection_mask(self.mesh, self._opacity_bodies, self._opacity_faces)
        parameters = material_parameters(self.mesh, self._surface_materials, self._body_materials)
        finishes = resolved_finishes(self.mesh, self._surface_materials, self._body_materials,
            self._material_overrides, self._body_finishes, self._face_finishes, parameters=parameters)
        opacity = opacity_colours(self.mesh, self._body_opacity, self._face_opacity)
        result = {}
        for name, values in (("material", parameters[:, 3]), ("roughness", finishes[:, 0]),
                             ("reflectivity", finishes[:, 1]), ("opacity", opacity[:, 3])):
            values = np.unique(values[mask])
            result[name] = float(values[0]) if len(values) == 1 else -1.
        result["material"] = MATERIAL_TYPES[int(result["material"])] if result["material"] >= 0 else "mixed"
        sources = {}
        for name, bodies, faces in (("material", self._body_materials, self._surface_materials),
                ("roughness", self._body_finishes, self._face_finishes),
                ("reflectivity", self._body_finishes, self._face_finishes),
                ("opacity", self._body_opacity, self._face_opacity)):
            source = np.zeros(len(self.mesh.triangles), dtype=np.uint8)
            for rank, identities, values in ((1, self.mesh.body_ids, bodies), (2, self.mesh.surfaces, faces)):
                explicit, automatic = [], []
                for identity, row in values.items():
                    value = row.get(name) if isinstance(row, dict) else row
                    if value is None: continue
                    (automatic if value in ("automatic", "imported") else explicit).append(int(identity))
                source[np.isin(identities, explicit)] = rank
                source[np.isin(identities, automatic)] = 0
            values = np.unique(source[mask])
            sources[name] = ("Automatic", "Body override", "Face override")[values[0]] if len(values) == 1 else "Mixed sources"
        result["sources"] = sources
        self._selection_cache = key, result
        return result

    @pyqtSlot(str)
    def selectOpacityKind(self, kind):
        if kind in ("body", "face"):
            self._opacity_kind = kind
            self.changed.emit()

    @pyqtSlot(int)
    def toggleOpacitySelection(self, identity):
        if self.busy: return
        present = self.mesh.present_bodies if self._opacity_kind == "body" else self.mesh.present_surfaces
        selected = self._opacity_bodies if self._opacity_kind == "body" else self._opacity_faces
        if identity not in present: return
        if identity in selected: selected.remove(identity)
        elif len(selected) < MAX_OPACITY_ENTRIES: selected.add(identity)
        else:
            self._status = "Select at most 2,048 bodies or faces at a time."
        self.changed.emit()

    @pyqtSlot()
    def clearOpacitySelection(self):
        self._opacity_bodies, self._opacity_faces = set(), set()
        self.changed.emit()

    def _apply_opacity(self, value):
        if self.busy or not self.opacitySelectionCount: return
        bodies, faces = dict(self._body_opacity), dict(self._face_opacity)
        descendants = set(map(int, self.mesh.surfaces[np.isin(self.mesh.body_ids, tuple(self._opacity_bodies))]))
        for identity in descendants: faces.pop(str(identity), None)
        for selected, target in ((self._opacity_bodies, bodies), (self._opacity_faces, faces)):
            for identity in selected:
                if value is None:
                    inherited = target is faces and any(str(int(body)) in bodies for body in self.mesh.body_ids[self.mesh.surfaces == identity])
                    if inherited: target[str(identity)] = "imported"
                    else: target.pop(str(identity), None)
                else: target[str(identity)] = value
        if max(len(bodies), len(faces)) > MAX_OPACITY_ENTRIES:
            self._status = "This edit exceeds the 2,048 body or face opacity overrides; nothing changed."
            self.changed.emit(); return
        self._body_opacity, self._face_opacity = bodies, faces
        self._status = "Imported transparency restored." if value is None else "Selected opacity updated."
        self.changed.emit()

    @pyqtSlot(float)
    def setSelectedOpacity(self, value):
        checked = unit_value(value)
        if checked is not None: self._apply_opacity(checked)

    @pyqtSlot()
    def resetSelectedOpacity(self): self._apply_opacity(None)

    @pyqtProperty("QVariantMap", notify=changed)
    def bodyMaterials(self): return dict(self._body_materials)

    @pyqtProperty("QVariantMap", notify=changed)
    def bodyFinishes(self): return local_finish_overrides(self._body_finishes, self.mesh, bodies=True)

    @pyqtProperty("QVariantMap", notify=changed)
    def faceFinishes(self): return local_finish_overrides(self._face_finishes, self.mesh)

    @pyqtProperty(str, notify=changed)
    def selectedMaterial(self): return self._selection_summary()["material"]

    @pyqtProperty(float, notify=changed)
    def selectedRoughness(self): return self._selection_summary()["roughness"]

    @pyqtProperty(float, notify=changed)
    def selectedReflectivity(self): return self._selection_summary()["reflectivity"]

    def _change_targets(self, bodies, faces, value, field=None, selected_bodies=None, selected_faces=None):
        if self.busy: return None
        selected_bodies = self._opacity_bodies if selected_bodies is None else selected_bodies
        selected_faces = self._opacity_faces if selected_faces is None else selected_faces
        if not selected_bodies and not selected_faces: return None
        bodies = {key: dict(row) if isinstance(row, dict) else row for key, row in bodies.items()}
        faces = {key: dict(row) if isinstance(row, dict) else row for key, row in faces.items()}
        descendants = set(map(int, self.mesh.surfaces[np.isin(self.mesh.body_ids, tuple(selected_bodies))]))
        for identity in descendants:
            key = str(identity)
            if field is None: faces.pop(key, None)
            elif key in faces:
                faces[key].pop(field, None)
                if not faces[key]: faces.pop(key)
        for selected, target in ((selected_bodies, bodies), (selected_faces, faces)):
            for identity in selected:
                key = str(identity)
                if field is None:
                    if target is bodies and value == "automatic": target.pop(key, None)
                    else: target[key] = value
                elif target is bodies and value == "automatic":
                    if key in target:
                        target[key].pop(field, None)
                        if not target[key]: target.pop(key)
                else: target.setdefault(key, {})[field] = value
        if max(len(bodies), len(faces)) > MAX_PAINTED_FACES:
            self._status = "This edit exceeds 2,048 body or face overrides; nothing changed."
            self.changed.emit(); return None
        self._status = ""
        return bodies, faces

    @pyqtSlot(str)
    def setSelectedMaterial(self, kind):
        if kind not in (*MATERIAL_TYPES, "automatic"): return
        result = self._change_targets(self._body_materials, self._surface_materials, kind)
        if result is not None:
            self._body_materials, self._surface_materials = result
            self.changed.emit()

    @pyqtSlot(str, float)
    def setSelectedFinish(self, field, value):
        number = unit_value(value)
        if field not in ("roughness", "reflectivity") or number is None: return
        result = self._change_targets(self._body_finishes, self._face_finishes, number, field)
        if result is not None:
            self._body_finishes, self._face_finishes = result
            self.changed.emit()

    @pyqtSlot(str)
    def resetSelectedFinish(self, field):
        if field not in ("roughness", "reflectivity"): return
        result = self._change_targets(self._body_finishes, self._face_finishes, "automatic", field)
        if result is not None:
            self._body_finishes, self._face_finishes = result
            self.changed.emit()

    @pyqtProperty(str, notify=changed)
    def paintKind(self): return self._paint_kind

    @pyqtProperty(int, notify=changed)
    def paintedFaceCount(self): return len(self._surface_materials)

    @pyqtProperty(int, notify=changed)
    def paintedBodyCount(self): return len(self._body_materials)

    @pyqtProperty("QVariantList", notify=changed)
    def materialTypes(self):
        return [dict(kind=kind, label=MATERIAL_LABELS[index],
            roughness=self._material_overrides.get(kind, {}).get("roughness", TYPE_PROFILES[index].roughness),
            reflectivity=self._material_overrides.get(kind, {}).get("reflectivity", TYPE_PROFILES[index].reflectivity),
            roughnessAuto="roughness" not in self._material_overrides.get(kind, {}),
            reflectivityAuto="reflectivity" not in self._material_overrides.get(kind, {}))
            for index, kind in enumerate(MATERIAL_TYPES)]

    @pyqtSlot(str, str, float)
    def setMaterialFinish(self, kind, field, value):
        number = unit_value(value)
        if self.busy or kind not in MATERIAL_TYPES or field not in ("roughness", "reflectivity") or number is None: return
        self._material_overrides = material_overrides(self._material_overrides)
        self._material_overrides.setdefault(kind, {})[field] = number
        self._status = ""
        self.changed.emit()

    @pyqtSlot(str, str)
    def resetMaterialFinish(self, kind, field):
        if self.busy: return
        row = self._material_overrides.get(kind)
        if row is not None: row.pop(field, None)
        self._material_overrides = material_overrides(self._material_overrides)
        self._status = ""
        self.changed.emit()

    @pyqtSlot(str)
    def selectMaterialPaint(self, kind):
        if kind in (*MATERIAL_TYPES, "automatic"):
            self._paint_kind = kind
            self.changed.emit()

    @pyqtSlot(int)
    def paintSurface(self, surface):
        if surface not in self.mesh.present_surfaces: return
        result = self._change_targets(self._body_materials, self._surface_materials,
            self._paint_kind, selected_bodies=set(), selected_faces={surface})
        if result is not None:
            self._body_materials, self._surface_materials = result
            self.changed.emit()

    @pyqtSlot(int)
    def paintBody(self, body):
        if body not in self.mesh.present_bodies: return
        result = self._change_targets(self._body_materials, self._surface_materials,
            self._paint_kind, selected_bodies={body}, selected_faces=set())
        if result is not None:
            self._body_materials, self._surface_materials = result
            self.changed.emit()

    @pyqtSlot()
    def clearMaterialPaint(self):
        if not self.busy:
            self._surface_materials, self._body_materials = {}, {}
            self._status = ""
            self.changed.emit()

    @pyqtProperty("QVariantList", notify=changed)
    def materials(self):
        return [dict(name=material.name, kind=material_profile(material).kind,
                     source=material.source) for material in self.mesh.materials]

    @pyqtProperty("QVariantList", notify=changed)
    def rotors(self): return rotors(self._rotors, self.mesh)

    @pyqtProperty("QVariantList", notify=changed)
    def bodies(self): return [dict(label=body.name, body=index) for index, body in enumerate(self.mesh.bodies) if index in self.mesh.present_bodies]

    @pyqtProperty("QVariantMap", notify=changed)
    def rotorCandidate(self): return dict(self._rotor_candidate)

    @pyqtProperty("QVariantList", notify=fanReadingsChanged)
    def fanOptions(self):
        return [dict(label="Manual visual speed", fan="")] + [dict(label=name, fan=name) for name in sorted(self._fan_readings)]

    @pyqtProperty(str, notify=fanReadingsChanged)
    def rotorReadout(self):
        row = self._rotor_candidate
        if not row: return "Select a body to configure rotation."
        rpm, label = speed(row, self._fan_readings)
        if label == "Fan unavailable": return label
        return ("Proposal · " if row not in self.rotors else "") + label + " · " + format(rpm, ".0f") + " RPM"

    def setFanReadings(self, values):
        if values != self._fan_readings:
            self._fan_readings = values
            self.fanReadingsChanged.emit()

    @pyqtSlot(int)
    def pickedBody(self, body):
        if self.busy or not 0 <= body < len(self.mesh.bodies): return
        if body not in self.mesh.present_bodies:
            self._status = "Selected body has no visible triangles."
            self.changed.emit(); return
        centre, axis, proven = body_axis(self.mesh, body)
        existing = next((row for row in self.rotors if row['body'] == body), None)
        self._rotor_candidate = existing or dict(body=body, centre=centre, axis=axis,
            rpm=3000., direction=1, fan="", blur=True)
        self.fanReadingsChanged.emit()
        self._status = "Analytic cylinder axis; verify it in the preview." if proven else "Proposed axis from body bounds. Confirm or adjust it before enabling rotation."
        self.changed.emit()

    @pyqtSlot("QVariantMap")
    def previewRotor(self, value):
        checked = rotors([value], self.mesh)
        if not self.busy and checked:
            self._rotor_candidate = checked[0]
            self._status = "Rotation proposal updated; confirm to enable it."
            self.fanReadingsChanged.emit()
            self.changed.emit()
        elif not self.busy:
            self._status = "Enter finite centre/axis values, a nonzero axis and visual RPM between 0 and 30,000."
            self.changed.emit()

    @pyqtSlot("QVariantMap")
    def setRotor(self, value):
        if self.busy: return
        checked = rotors([value], self.mesh)
        if not checked:
            self._status = "Enter finite centre/axis values, a nonzero axis and visual RPM between 0 and 30,000."
            self.changed.emit(); return
        alpha = opacity_colours(self.mesh, self._body_opacity, self._face_opacity)[:,3]
        if len(np.unique(self.mesh.body_ids[(alpha > 0) & (alpha < .999)])) > 64:
            self._status = "Fan animation supports at most 64 translucent bodies."
            self.changed.emit(); return
        row = checked[0]
        others = [old for old in self.rotors if old['body'] != row['body']]
        if len(others) >= MAX_ROTORS:
            self._status = "A toolhead supports at most eight animated bodies."
            self.changed.emit(); return
        self._rotors = others + [row]
        self._rotor_candidate = row
        self.fanReadingsChanged.emit()
        self._status = "Visual rotation configured. Printer fan bindings only read telemetry."
        self.changed.emit()

    @pyqtSlot(int)
    def removeRotor(self, body):
        if self.busy: return
        self._rotors = [row for row in self.rotors if row['body'] != body]
        self._rotor_candidate = {}
        self.fanReadingsChanged.emit()
        self.changed.emit()

    @pyqtSlot(float)
    def previewSurfaceDetail(self, value):
        if not self.busy:
            self._surface_detail = surface_detail(value)
            self.lightingPreviewChanged.emit()

    @pyqtSlot(float)
    def setSurfaceDetail(self, value):
        if not self.busy:
            self._surface_detail = surface_detail(value)
            self.changed.emit()

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
                                     self._manual, self.lights, self._surface_detail, self.rotors,
                                     self.materialOverrides, self.surfaceMaterials, self.bodyOpacity, self.faceOpacity, self.bodyMaterials, self.bodyFinishes, self.faceFinishes)

    @pyqtSlot(bool)
    def endEdit(self, accept):
        checkpoint, self._edit_checkpoint = self._edit_checkpoint, None
        if checkpoint is None or accept or self.busy: return
        identity, mesh, texts, manual, lights, detail, saved_rotors, finishes, painted, bodies, faces, body_materials, body_finishes, face_finishes = checkpoint
        if identity != self._identity() or mesh is not self._mesh: return
        self._texts, self._manual, self._lights = texts, manual, lights
        self._surface_detail = detail
        self._material_overrides, self._surface_materials = finishes, painted
        self._body_opacity, self._face_opacity = bodies, faces
        self._body_materials, self._body_finishes, self._face_finishes = body_materials, body_finishes, face_finishes
        self._opacity_bodies, self._opacity_faces = set(), set()
        self._rotors = saved_rotors
        self._rotor_candidate = {}
        self.fanReadingsChanged.emit()
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
        self._surface_detail = surface_detail(getattr(config, "toolhead_surface_detail", .35))
        self._material_overrides = material_overrides(getattr(config, "toolhead_material_overrides", {}))
        self._rotor_candidate = {}
        self._rotors = rotors(getattr(config, "toolhead_rotors", []))
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
        if missing: self._rotors, self._lights = [], []
        self._surface_materials = painted_materials(getattr(config, "toolhead_surface_materials", {}), self.mesh) if not missing else {}
        self._body_materials = painted_materials(getattr(config, "toolhead_body_materials", {}), self.mesh, bodies=True) if not missing else {}
        self._body_finishes = local_finish_overrides(getattr(config, "toolhead_body_finishes", {}), self.mesh, bodies=True) if not missing else {}
        self._face_finishes = local_finish_overrides(getattr(config, "toolhead_face_finishes", {}), self.mesh) if not missing else {}
        self._body_opacity = opacity_overrides(getattr(config, "toolhead_body_opacity", {}), self.mesh, bodies=True) if not missing else {}
        self._face_opacity = opacity_overrides(getattr(config, "toolhead_face_opacity", {}), self.mesh) if not missing else {}
        self._opacity_bodies, self._opacity_faces = set(), set()
        self._set_tip(tip or self.mesh.automatic_tip, tip is not None)

    @pyqtSlot()
    def useDefault(self):
        if self.busy: return
        self._pending = self._key = self._name = self._status = ""
        self._imported = False
        self._mesh = default_mesh()
        self._surface_materials = {}
        self._body_opacity, self._face_opacity = {}, {}
        self._body_materials, self._body_finishes, self._face_finishes = {}, {}, {}
        self._opacity_bodies, self._opacity_faces = set(), set()
        self._rotors, self._rotor_candidate = [], {}
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
            self._surface_materials = {}
            self._body_opacity, self._face_opacity = {}, {}
            self._body_materials, self._body_finishes, self._face_finishes = {}, {}, {}
            self._opacity_bodies, self._opacity_faces = set(), set()
            self._lights = []
            self._rotors, self._rotor_candidate = [], {}
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
                "toolhead_lights": self.lights, "toolhead_surface_detail": self._surface_detail, "toolhead_rotors": self.rotors,
                "toolhead_material_overrides": self.materialOverrides, "toolhead_surface_materials": self.surfaceMaterials,
                "toolhead_body_materials": self.bodyMaterials, "toolhead_body_finishes": self.bodyFinishes, "toolhead_face_finishes": self.faceFinishes,
                "toolhead_body_opacity": self.bodyOpacity, "toolhead_face_opacity": self.faceOpacity,
                "toolhead_tip": list(self.tip) if self._manual else []}

    def close(self):
        self._stop_elapsed()
        self._closed = True
        self._generation += 1
        self._cancelled.set()
