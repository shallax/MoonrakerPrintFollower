"""The shared Cura Preview mode, theme colours and material swatches."""
import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor

from .PreviewColours import CLASS_THEME_KEYS, DEFAULT_CLASSES


class FollowerColourScheme(QObject):
    changed = pyqtSignal()
    PREFERENCE = "layerview/layer_view_type"
    PALETTE = "moonrakerprintfollower/last_preview_palette"

    def __init__(self, application, parent=None):
        super().__init__(parent)
        self._application = application
        self._preferences = application.getPreferences()
        self._preferences.addPreference(self.PREFERENCE, 1)
        self._preferences.addPreference(self.PALETTE, "")
        self._cached_classes, self._cached_materials = dict(DEFAULT_CLASSES), ["#888888"]
        try:
            raw = self._preferences.getValue(self.PALETTE)
            cached = json.loads(raw) if isinstance(raw,str) and len(raw) <= 16384 else {}
            classes, materials = cached.get("classes", {}), cached.get("materials", [])
            if (isinstance(classes,dict) and set(classes) <= set(CLASS_THEME_KEYS)
                    and all(isinstance(v,str) and QColor(v).isValid() for v in classes.values())
                    and isinstance(materials,list) and 0 < len(materials) <= 16
                    and all(isinstance(v,str) and QColor(v).isValid() for v in materials)):
                self._cached_classes.update(classes)
                self._cached_materials = materials
        except (KeyError, TypeError, ValueError, RuntimeError):
            pass
        changed = getattr(self._preferences, "preferenceChanged", None)
        if changed is not None:
            changed.connect(self._preference_changed)
        self._snapshot = {}
        self._theme = self._extruders = None
        self._host_available = getattr(application, "_qml_engine", None) is not None
        stack_signal = getattr(application, "globalContainerStackChanged", None)
        if stack_signal is not None:
            stack_signal.connect(self.refresh)
        self.refresh()

    def host_ready(self, *_args):
        self._host_available = True
        self.refresh()

    @property
    def snapshot(self):
        return self._snapshot

    def set_mode(self, mode):
        mode = int(mode)
        if 0 <= mode <= 5:
            self._preferences.setValue(self.PREFERENCE, mode)

    def _preference_changed(self, key):
        if key == self.PREFERENCE:
            self.refresh()

    def refresh(self, *_args):
        try:
            mode = int(self._preferences.getValue(self.PREFERENCE))
        except (TypeError, ValueError, OverflowError):
            mode = 1
        classes = dict(self._cached_classes)
        materials = list(self._cached_materials)
        host_read = False
        try:
            # Theme access is legal only after Cura creates its engine.
            if self._host_available:
                theme = self._application.getTheme()
                if theme is not None:
                    if theme is not self._theme:
                        self._theme = theme
                        theme.themeLoaded.connect(self.refresh)
                    host_read = True
                    for name, key in CLASS_THEME_KEYS.items():
                        colour = theme.getColor(key)
                        if colour.isValid():
                            classes[name] = colour.name(QColor.NameFormat.HexArgb)
            getter = getattr(self._application, "getExtrudersModel", None)
            extruders = getter() if self._host_available and getter is not None else None
            if extruders is not None:
                if extruders is not self._extruders:
                    self._extruders = extruders
                    extruders.itemsChanged.connect(self.refresh)
                rows = extruders.items
                if rows:
                    host_read = True
                    materials = ["#888888"] * min(16, max(1, max(int(row.get("index", 0)) for row in rows) + 1))
                    for row in rows:
                        index = int(row.get("index", 0))
                        colour = QColor(row.get("color", "#888888"))
                        if 0 <= index < len(materials) and colour.isValid():
                            materials[index] = colour.name(QColor.NameFormat.HexArgb)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            # Host colour APIs are optional presentation capabilities.
            # A changed host interface must never stop plugin registration.
            classes, materials = dict(self._cached_classes), list(self._cached_materials)
            host_read = False
        if host_read and (classes != self._cached_classes or materials != self._cached_materials):
            self._cached_classes, self._cached_materials = dict(classes), list(materials)
            self._preferences.setValue(self.PALETTE, json.dumps({"classes":classes,"materials":materials}, separators=(",", ":")))
        snapshot = {"mode": mode if 0 <= mode <= 5 else 1, "classes": classes, "materials": materials}
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self.changed.emit()
