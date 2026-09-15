"""The what's-new overlay's window owner: the once-per-version offer
and the Popup's creation on Cura's own engine.

The content and the marker logic live in WhatsNew.py, the QML in
WhatsNewOverlay.qml, and the state and slots on the monitor model.
This module owns only the window-side mechanics: finding the main
window and the monitor, waiting out the boot, and building the
overlay on Cura's QML engine.
"""
import os
import traceback

from PyQt6.QtCore import QMetaObject, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, qmlEngine
from UM.Logger import Logger


class WhatsNewOverlay:
    def __init__(self):
        self._overlay = None
        self._attempts = 0
        # The once-per-version offer: both the main window and the
        # monitor model exist only after Cura finishes booting, so
        # the check retries with a bound (a fresh session that never
        # finishes booting gets no popup — Cura's own failure, not
        # ours).
        QTimer.singleShot(1500, self._offer)

    def close(self):
        self._overlay = None

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
                    Logger.log("w", "Moonraker Print Follower: the what's-new offer gave up "
                                    "after %s attempts (window=%s, monitor=%s)",
                               self._attempts,
                               window is not None, model is not None)
                return
            # Connected every pass: a machine switch reinstalls the
            # monitor model, and Qt drops the stale connection with the
            # old object.
            model.whatsNewRequested.connect(self._show)
            model.checkWhatsNew()
        except Exception:
            Logger.log("e", "Moonraker Print Follower: the what's-new offer raised: %s",
                       traceback.format_exc(limit=4))

    def _show(self):
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
            path = os.path.join(os.path.dirname(__file__), "WhatsNewOverlay.qml")
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
