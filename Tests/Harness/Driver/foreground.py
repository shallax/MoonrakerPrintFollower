"""Whether the DISPLAY shows this application, and how to ask for it.

The evidence records the display, so a step that hands the foreground to
another process makes every later frame a picture of something else. A
platform with no foreground authority is RECORDED, never failed.
"""
from __future__ import annotations

import os

from PyQt6.QtGui import QGuiApplication
from .scene import _window_handle


def _os_foreground_owner():
    """The display's foreground window and the process that owns it,
    where the OS answers. The OWNER is what the guard reads: Cura's
    own modal dialogs are Cura on screen, and raising the main window
    over one of them would break the step that is driving it."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        value = int(user32.GetForegroundWindow())
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(value, ctypes.byref(owner))
        return value, int(owner.value)
    except Exception:
        return None, None


# A platform that never granted the window activation — the WM-less
# Xvfb — has no baseline to read a theft against, so the guard records
# "no authority" instead of failing every step of the run. One
# observation of the window active is enough to arm it.
_FOREGROUND_SEEN = [False]


# A per-run number for the window the counter is attached to, so the
# evidence can name which window a heartbeat was placed on.

def _foreground_state(window):
    """Whether the DISPLAY shows this application — its own dialogs
    included: the evidence records the display, so a step that hands
    the foreground to another process makes every later frame
    evidence of that other process instead."""
    focus = QGuiApplication.focusWindow()
    handle = _window_handle(window)
    if handle is not None:
        authority = "os"
        value, owner = _os_foreground_owner()
        # The owning process is the honest test: a modal dialog of
        # this app is this app on screen.
        held = (owner == os.getpid()) if owner else (value == handle)
    else:
        authority = "qt"
        held = bool(window.isActive() or focus is not None)
        if not _FOREGROUND_SEEN[0]:
            authority = "none"
    state = {"authority": authority, "foreground": bool(held),
             "active": bool(window.isActive()),
             "focus": (focus.title() or focus.objectName()) if focus is not None else None,
             "app_windows": len([w for w in QGuiApplication.topLevelWindows()
                                 if w.isVisible()]),
             "platform": QGuiApplication.platformName()}
    if state["active"] or held:
        _FOREGROUND_SEEN[0] = True
    return state


def _raise_main_window(window):
    """Reclaim the display for Cura: Qt's raise+activate, then — on
    Windows, where a process that lost the foreground cannot take it
    back with SetForegroundWindow alone (the foreground lock) — the
    topmost round-trip that clears it. Nothing here is assumed: the
    caller reads the state back and fails the step when the display
    did not come home."""
    try:
        window.raise_()
        window.requestActivate()
    except Exception:
        pass
    handle = _window_handle(window)
    if handle is None:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        flags = 0x0001 | 0x0002  # SWP_NOSIZE | SWP_NOMOVE
        user32.SetWindowPos(handle, -1, 0, 0, 0, 0, flags)   # HWND_TOPMOST
        user32.SetWindowPos(handle, -2, 0, 0, 0, 0, flags)   # HWND_NOTOPMOST
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    except Exception:
        pass
