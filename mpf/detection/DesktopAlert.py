"""The desktop alert for a detection failure raised while Cura is not
the foreground window: Cura's own tray widget carries the message, with
the retained frame as its icon where the platform shows one.

Best-effort by contract. The widget exists to show pop-up messages
(Cura makes it visible when the main window is minimised), a machine
without a usable tray falls through, and the caller's in-Cura message
is shown regardless — so a refusal here costs the user nothing.
"""


def notify(title: str, body: str, frame_path: str = "", *, timeout_ms: int = 10000) -> bool:
    """Hand a desktop message to the platform; True when it was sent."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication, QIcon
        if QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
            # Cura is in front: its own message is already on screen and
            # a desktop popup on top of it would be noise.
            return False
        from UM.Qt.QtApplication import QtApplication
        tray = getattr(QtApplication.getInstance(), "_tray_icon_widget", None)
        if tray is None:
            return False
        icon = QIcon(frame_path) if frame_path else tray.icon()
        tray.showMessage(title, body, icon, timeout_ms)
        return True
    except Exception:
        return False
