"""The desktop alert for a detection failure raised while Cura is not
the foreground window: Cura's own tray widget carries the message, with
the retained frame as its icon where the platform shows one.

Best-effort by contract. The widget exists to show pop-up messages
(Cura makes it visible when the main window is minimised), a machine
without a usable tray falls through, and the caller's in-Cura message
is shown regardless — so a refusal here costs the user nothing.
"""


def notify(title: str, body: str, frame_path: str = "", *, timeout_ms: int = 10000) -> bool:
    """Hand a desktop message to the platform; True when it was sent.

    Every refusal is logged with its reason: a live report of "I saw no
    notification" is otherwise indistinguishable from the alert never
    firing, and Cura's log is where that gets answered.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication, QIcon
        if QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
            # Cura is in front: its own message is already on screen and
            # a desktop popup on top of it would be noise.
            _log("Cura is the active window")
            return False
        from UM.Qt.QtApplication import QtApplication
        tray = getattr(QtApplication.getInstance(), "_tray_icon_widget", None)
        if tray is None:
            # Cura creates this widget only when its own tray icon is
            # enabled (Preferences > General > "Use tray icon").
            _log("Cura has no tray widget (its tray icon is disabled)")
            return False
        if not tray.isSystemTrayAvailable():
            # Cura's widget exists even where no tray does (measured:
            # False in a container): handing it a message there would
            # report a delivery that never happened.
            _log("the system tray is unavailable")
            return False
        icon = QIcon(frame_path) if frame_path else tray.icon()
        tray.showMessage(title, body, icon, timeout_ms)
        _log("shown: " + title)
        return True
    except Exception as exc:
        _log("raised: %r" % (exc,))
        return False


def _log(message: str) -> None:
    try:
        from UM.Logger import Logger
        Logger.log("i", "Moonraker Print Follower: desktop alert — %s", message)
    except Exception:
        pass
