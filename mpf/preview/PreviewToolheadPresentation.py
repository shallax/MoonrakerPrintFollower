"""Preview dock hosting and intents, bound to the selected Monitor owner."""
from __future__ import annotations

import math

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtQml import QQmlEngine
from PyQt6.QtQuick import QQuickItem
from UM.Logger import Logger

from ..resources.PluginPaths import plugin_path


class PreviewToolheadPresentation(QObject):
    def __init__(self, application, cura, parent=None, persistence=None):
        super().__init__(parent)
        self._application, self._cura, self._persistence = application, cura, persistence
        saved = persistence.state_global_document() if persistence is not None else {}
        self._collapsed = bool(saved.get("previewToolheadPaneCollapsed", False))
        self._distance = 25.0
        self._shell = self._pane = None
        self._closed = False
        self._booted = bool(getattr(application, "started", False))
        self._boot_signal = None
        self._values = {}
        self._control_epoch = 0
        self._dispatch = None
        self._intent_connections = []
        self.reset()
        cura.changed.connect(self.refresh)
        finished = getattr(application, "initializationFinished", None)
        if finished is not None and not self._booted:
            self._boot_signal = finished
            finished.connect(self._boot_finished)
        else:
            self._booted = True
        self.refresh()

    def _boot_finished(self):
        self._booted = True
        self.refresh()

    @staticmethod
    def _host_items(content):
        pending = [content]
        while pending:
            item = pending.pop()
            context = QQmlEngine.contextForObject(item)
            if context is not None:
                orientation = context.objectForName("viewOrientationControls")
                viewport = context.objectForName("main")
                if isinstance(orientation, QQuickItem) and isinstance(viewport, QQuickItem) \
                        and orientation.parentItem() is viewport.parentItem():
                    return {"orientationControls": orientation, "viewport": viewport,
                            "stageMenu": context.objectForName("stageMenu"),
                            "jobSpecs": context.objectForName("jobSpecs"),
                            "objectSelector": context.objectForName("objectSelector")}
            pending.extend(item.childItems())
        return None

    def refresh(self):
        if self._closed or not self._booted:
            return
        window = self._application.getMainWindow()
        content = window.contentItem() if window is not None else None
        items = self._host_items(content) if content is not None else None
        if items is None:
            self._retire_shell()
            return
        if self._shell is not None and self._shell.parentItem() is not items["viewport"].parentItem():
            self._retire_shell()
        if self._shell is None:
            shell = self._application.createQmlComponent(plugin_path("preview", "PreviewToolheadHost.qml"))
            if shell is None:
                Logger.log("w", "Moonraker Preview Toolhead host unavailable")
                return
            self._shell = shell
            self._pane = shell.findChild(QQuickItem, "previewToolheadPane")
            shell.setParentItem(items["viewport"].parentItem())
            shell.setParent(items["viewport"].parentItem())
            shell.destroyed.connect(self._shell_destroyed)
            if self._pane is not None:
                self._pane.collapseRequested.connect(self._set_collapsed)
                self._pane.jogDistanceRequested.connect(self._set_distance)
                self._pane.setProperty("collapsed", self._collapsed)
                self._pane.setProperty("jogDistance", self._distance)
                self._wire_intents()
        for name, item in items.items():
            self._shell.setProperty(name, item)
        self._shell.setProperty("previewActive", bool(self._cura.preview_active))
        self._publish()

    @pyqtSlot()
    def _shell_destroyed(self):
        # Retired shells disconnect first. Only the active host can deliver
        # this callback; Qt may wrap a destructing sender as a new QObject.
        self._shell = self._pane = None

    def publish(self, values):
        self._values = dict(values)
        self._publish()

    def reset(self):
        self._values = {"connected": False, "positionX": "—", "positionY": "—",
                        "positionZ": "—", "offsetText": "—", "actionRows": [],
                        "jogAllowed": False, "homeAllowed": False, "offsetAllowed": False,
                        "moveToAllowed": False, "statusText": "No printer selected"}
        if self._pane is not None:
            self._pane.resetDrafts()
        self._publish()

    def _publish(self):
        if self._closed or self._pane is None:
            return
        for name, value in self._values.items():
            if name == "actionRows":
                value = [{**row, "allowed": self._dispatch is not None and bool(row.get("allowed"))} for row in value]
            self._pane.setProperty(name, value)
        for name in ("jogAllowed", "homeAllowed", "moveToAllowed", "offsetAllowed"):
            self._pane.setProperty(name, self._dispatch is not None and bool(self._values.get(name)))

    def bind_controls(self, dispatch):
        self._control_epoch += 1
        if self._pane is not None:
            self._pane.resetDrafts()
        self._dispatch = dispatch
        self._unwire_intents()
        self._wire_intents()
        self._publish()

    def _unwire_intents(self):
        for signal, slot in self._intent_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._intent_connections.clear()

    def _wire_intents(self):
        if self._pane is None or self._dispatch is None:
            return
        epoch, pane, dispatch = self._control_epoch, self._pane, self._dispatch
        for signal_name, kind, gate in (("jogRequested", "jog", "jogAllowed"),
                                       ("homeRequested", "home", "homeAllowed"),
                                       ("moveToRequested", "move-to", "moveToAllowed"),
                                       ("offsetRequested", "offset", "offsetAllowed"),
                                       ("actionRequested", "action", None)):
            def route(*args, kind=kind, gate=gate):
                if self._closed or epoch != self._control_epoch or pane is not self._pane \
                        or not self._cura.preview_active or not self._values.get("connected"):
                    return
                if gate is not None and not self._values.get(gate):
                    return
                if kind == "action" and not any(row["key"] == args[0] and row.get("allowed")
                                                for row in self._values.get("actionRows", ())):
                    return
                dispatch(kind, (*args, self._distance) if kind == "jog" else args)
            signal = getattr(pane, signal_name)
            signal.connect(route)
            self._intent_connections.append((signal, route))

    def _set_collapsed(self, collapsed):
        self._collapsed = bool(collapsed)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewToolheadPaneCollapsed": self._collapsed})
        if self._pane is not None:
            self._pane.setProperty("collapsed", self._collapsed)

    def _set_distance(self, distance):
        if math.isfinite(distance) and 0.01 <= distance <= 300:
            self._distance = distance
            if self._pane is not None:
                self._pane.setProperty("jogDistance", distance)

    def _retire_shell(self):
        self._unwire_intents()
        shell, self._shell, self._pane = self._shell, None, None
        if shell is not None:
            shell.destroyed.disconnect(self._shell_destroyed)
            shell.setProperty("previewActive", False)
            shell.setParentItem(None)
            shell.deleteLater()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.bind_controls(None)
        self._cura.changed.disconnect(self.refresh)
        disconnect = getattr(self._boot_signal, "disconnect", None)
        if callable(disconnect):
            disconnect(self._boot_finished)
        self._boot_signal = None
        self._retire_shell()
