"""The what's-new overlay's window owner: the once-per-version offer
and the Popup's creation on Cura's own engine.

The content and the marker logic live in WhatsNew.py, the QML in
WhatsNewOverlay.qml, and the state and slots on the monitor model.
This module owns only the window-side mechanics: finding the main
window and the monitor, waiting out the boot, and building the
overlay on Cura's QML engine.
"""

import traceback

from PyQt6.QtCore import QMetaObject, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, qmlEngine
from UM.Logger import Logger

from ..resources.PluginPaths import plugin_path


class WhatsNewOverlay:
    def __init__(self, detection=None):
        self._overlay = None
        self._detection = detection
        self._detection_overlay = None
        self._attempts = 0
        self._closed = False
        self._gave_up = False
        self._model = None
        self._wired_signals = ()
        # The once-per-version offer: both the main window and the
        # monitor model exist only after Cura finishes booting, so
        # the check retries with a bound (a fresh session that never
        # finishes booting gets no popup — Cura's own failure, not
        # ours).
        QTimer.singleShot(1500, self._offer)

    def attach_model(self, model):
        """The current Monitor model, (re)taken on every monitor
        install — the shape the migration notice already uses. Qt drops
        the stale connection with a replaced model, so the offer must
        be wired to the live one: a machine switch would otherwise
        leave it with no dismiss signal to ride. A model whose What's
        New has already been seen owes only the offer, which is
        re-announced here so a monitor arriving after the boot retries
        have stopped still gets it."""
        if model is self._model:
            return
        self._drop_model()
        self._model = model
        # getattr, like the migration notice's own attach: a model that
        # does not carry the surface (a cached device, a test double)
        # is wired for what it has rather than refused.
        signals = [(getattr(model, "whatsNewRequested", None), self._show)]
        if self._detection is not None:
            signals.append((getattr(model, "whatsNewDismissed", None), self._show_detection))
        signals = [(signal, slot) for signal, slot in signals if signal is not None]
        for signal, slot in signals:
            signal.connect(slot)
        self._wired_signals = tuple(signals)
        from .WhatsNew import should_show
        if not should_show(getattr(model, "_whats_new_seen", "")):
            QTimer.singleShot(0, self._show_detection)

    def offer_state(self) -> dict:
        """The offer's wiring, for the harness's first-install leg: a
        step that sees no popup must be able to tell a never-wired
        offer from one whose retries timed out. Wired means the
        DISMISS path is attached — that is what reveals the offer, so
        a model attached without it is not wired for this purpose."""
        return {"wired": any(slot == self._show_detection
                             for _signal, slot in self._wired_signals),
                "attempts": self._attempts, "gave_up": self._gave_up}

    def _drop_model(self):
        for signal, slot in self._wired_signals:
            try:
                signal.disconnect(slot)
            except (TypeError, AttributeError):
                pass
        self._wired_signals = ()
        self._model = None

    def close(self):
        # Deinitialization (the 2026-09-19 review's F1): every queued
        # callback must bail, no retry may be scheduled, and a live
        # popup is destroyed — never merely un-referenced.
        self._closed = True
        self._drop_model()
        if self._overlay is not None:
            try:
                self._overlay.deleteLater()
            except Exception:
                pass
        self._overlay = None
        if self._detection_overlay is not None:
            self._detection_overlay.deleteLater()
            self._detection_overlay = None

    def _find_main_window(self):
        # The main editor window among Cura's windows: the largest
        # QQuickWindow. Cura keeps popup windows alive, and native
        # QWindows exist too (a splash, a dialog) — only a QML
        # window carries the engine on its content item, so the
        # plain-QWindow filter is the discriminator.
        best = None
        best_area = 0
        for window in QGuiApplication.allWindows():
            if not hasattr(window, "contentItem"):
                continue
            area = window.width() * window.height()
            if area > best_area:
                best_area = area
                best = window
        return best

    def _find_monitor(self):
        from UM.Application import Application
        for device in Application.getInstance().getOutputDeviceManager().getOutputDevices():
            if "Moonraker" in type(device).__name__:
                monitor = getattr(device, "activePrinter", None)
                if monitor is not None:
                    return monitor
        return None

    def _offer(self):
        # Never fatal: the offer runs on a timer inside Cura's event
        # loop, and an unhandled raise in a timer callback takes the
        # whole application down (the capture environment's stubbed
        # Cura proved it — the offer fired into a stub application).
        if self._closed:
            return
        try:
            self._attempts += 1
            window = self._find_main_window()
            model = self._find_monitor()
            # The window must be VISIBLE, not merely created: the offer
            # fired during Cura's boot on macOS and the component load
            # crashed inside the boot's QML import storm.
            if window is None or model is None or not window.isVisible():
                if self._attempts == 1:
                    Logger.log("i", "Moonraker Print Follower: the what's-new offer waits "
                                   "(window=%s, visible=%s, monitor=%s)",
                               window is not None,
                               bool(window is not None and window.isVisible()),
                               model is not None)
                if self._attempts < 300:
                    QTimer.singleShot(1000, self._offer)
                else:
                    self._gave_up = True
                    Logger.log("w", "Moonraker Print Follower: the what's-new offer gave up "
                                    "after %s attempts (window=%s, monitor=%s)",
                               self._attempts,
                               window is not None, model is not None)
                return
            # The wiring belongs to attach_model: the plugin hands it
            # the model on every monitor install, so a switch re-wires
            # it there as well.
            self.attach_model(model)
            model.checkWhatsNew()
        except Exception:
            Logger.log("e", "Moonraker Print Follower: the what's-new offer raised: %s",
                       traceback.format_exc(limit=4))

    def _show(self):
        if self._closed:
            return
        window = self._find_main_window()
        model = self._find_monitor()
        if window is None or model is None:
            return
        try:
            # Cura's own stored engine is the reliable handle: the
            # qmlEngine() lookup returns null for the main window's
            # content item on Cura's Qt (the harness's in-Cura probe
            # proved it), and a null engine made the component load a
            # silent no-op. The harness's own QML mounts read the
            # application engine first for the same reason.
            engine = None
            try:
                from UM.Qt.QtApplication import QtApplication
                engine = QtApplication.getInstance()._qml_engine
            except Exception:
                engine = None
            if engine is None:
                engine = qmlEngine(window.contentItem())
            if engine is None:
                Logger.log("e", "Moonraker Print Follower: the what's-new overlay has no QML engine")
                return
            # The URL constructor crashed inside QQmlComponent::
            # loadUrl on macOS (the boot's import storm) — build from
            # the source string instead. The base URL keeps the
            # component's own directory on the engine's path for its
            # imports.
            path = plugin_path("whatsnew", "WhatsNewOverlay.qml")
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            component = QQmlComponent(engine)
            component.setData(source.encode("utf-8"), QUrl.fromLocalFile(path))
            overlay = component.createWithInitialProperties({"model": model})
            if overlay is None:
                Logger.log("e", "Moonraker Print Follower: the what's-new overlay failed to load: %s / %s",
                           component.errorString(),
                           [str(error) for error in component.errors()])
                return
            # The Popup root is a QObject, not an Item — it joins the
            # window through its parent property, positions via x/y,
            # and opens through the meta-object (none of setParentItem,
            # width() or open() exist on the Python wrapper). Centered
            # on the WINDOW's geometry (the content item's size lags
            # its first layout pass) — a Popup carries no anchors —
            # with Cura's theme and Cura's own modal dimmer.
            root = window.contentItem()
            overlay.setProperty("parent", root)
            overlay.setProperty("x", max(0, round((window.width() - overlay.property("width")) / 2)))
            overlay.setProperty("y", max(0, round((window.height() - overlay.property("height")) / 2)))
            QMetaObject.invokeMethod(overlay, "open")
            # The Esc dismissal is the popup's close policy, and a
            # key reaches the window's focus item — the popup must
            # hold focus the moment it opens, before any click
            # inside it (the harness's key press proved the
            # focus-less popup swallows no Esc).
            QMetaObject.invokeMethod(overlay, "forceActiveFocus")
            self._overlay = overlay
            Logger.log("i", "Moonraker Print Follower: the what's-new overlay opened")
        except Exception:
            Logger.log("e", "Moonraker Print Follower: the what's-new overlay raised: %s",
                       traceback.format_exc(limit=4))
            return

    def _show_detection(self):
        if self._closed or self._detection is None or not self._detection.should_offer \
                or self._detection_overlay is not None:
            return
        window = self._find_main_window()
        if window is None or not window.isVisible():
            return
        try:
            from UM.Qt.QtApplication import QtApplication
            engine = QtApplication.getInstance()._qml_engine
            path = plugin_path("detection", "DetectionOffer.qml")
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            component = QQmlComponent(engine)
            component.setData(source.encode("utf-8"), QUrl.fromLocalFile(path))
            overlay = component.createWithInitialProperties({"detection": self._detection})
            if overlay is None:
                Logger.log("e", "Moonraker Print Follower: detection offer failed to load: %s",
                           component.errorString())
                return
            overlay.setProperty("parent", window.contentItem())
            overlay.setProperty("x", max(0, round((window.width() - overlay.property("width")) / 2)))
            overlay.setProperty("y", max(0, round((window.height() - overlay.property("height")) / 2)))
            QMetaObject.invokeMethod(overlay, "open")
            QMetaObject.invokeMethod(overlay, "forceActiveFocus")
            self._detection_overlay = overlay
        except Exception:
            Logger.log("e", "Moonraker Print Follower: detection offer raised: %s",
                       traceback.format_exc(limit=4))
