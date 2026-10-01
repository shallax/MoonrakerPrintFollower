"""The application's windows and its visual tree.

One walk, one visibility rule, one geometry read and one settle, shared by
every probe and every interaction family. The harness addresses the plugin
through QQuickItem.childItems() — the VISUAL tree — so this is where the
reach of every step is decided.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QGuiApplication
from .window_geometry import fit_content_rectangle
from .qt_test import _import_qtest


def _window_target(window, width, height):
    # On the macOS CI desktop, 1840x1040 produced stale native pixels
    # despite responsive QML/frameSwapped; 1840x900 remained live across
    # Prepare, sliced Preview and Monitor changes. Resize recovery also
    # reduced the actual window to fit. Keep its full frame inside the
    # available area; the failing OS/Qt/Cura component is not identified.
    if QGuiApplication.platformName() != "cocoa" or os.environ.get("HARNESS_FIT_AVAILABLE") == "off":
        return [0, 0, width, height], None
    screen = window.screen() or QGuiApplication.primaryScreen()
    available = list(screen.availableGeometry().getRect())
    margins = window.frameMargins()
    minimum = window.minimumSize()
    target = fit_content_rectangle(
        [width, height], available,
        [margins.left(), margins.top(), margins.right(), margins.bottom()],
        [minimum.width(), minimum.height()])
    return target, available


def _apply_window_target(window, width, height, set_origin=False):
    target, available = _window_target(window, width, height)
    if available is not None or set_origin:
        window.setGeometry(*target)
    else:
        window.resize(width, height)


def _window_size_evidence(window, width, height):
    target, available = _window_target(window, width, height)
    return {"requested": [width, height], "wanted": target[2:],
            "fit_available": available is not None, "available": available,
            "frame": list(window.frameGeometry().getRect())}

def _lookup_windows():
    # The main window first, then every other visible QML window by
    # size: the file manager and the dialogs are separate windows,
    # and a main-window-only walk never finds their items. Widget
    # widget windows have no contentItem — the QML walks died on
    # them with AttributeError whenever one happened to be up.
    from PyQt6.QtQuick import QQuickWindow
    windows = [w for w in QGuiApplication.topLevelWindows()
               if isinstance(w, QQuickWindow)]
    visible = [w for w in windows if w.isVisible()]
    visible.sort(key=lambda w: (w is _main_window(), w.width() * w.height()),
                 reverse=True)
    _WALK_STATS["windows"] = len(visible)
    return visible


def _effectively_visible(item):
    # Qt's isVisible() lies for deeply nested repeater content (the
    # rendered label reports invisible); walk the parent chain and AND
    # the visible flags ourselves — the collapse checks depend on it.
    # Opacity joins the chain: the fit hides whole groups through
    # opacity, and an opacity-0 item is every bit as absent (the x7
    # short-window gate).
    node = item
    while node is not None:
        try:
            if not bool(node.property("visible")):
                return False
            # The None guard, not the falsy fallback: 0.0 IS falsy,
            # so `or 1.0` read an opacity-0 item as fully visible and
            # every absence check on the fit's hiding passed nothing
            # (the panel's catch — the x7 root cause).
            opacity = node.property("opacity")
            if opacity is not None and float(opacity) <= 0.0:
                return False
        except Exception:
            pass
        node = node.parentItem()
    return True


# The walk provenance (the review's C9): every resolving reply states
# WHICH walk resolved its target. "click" is the unfiltered depth-96
# walk over every QQuickWindow; "lookup" is the visibility-filtered
# depth-64 walk. The evidence layer records the mode with each entry.
_WALK_STATS = {"mode": "", "depth": 0, "items": 0, "windows": 0}


def _begin_walk(mode, depth):
    _WALK_STATS.update({"mode": mode, "depth": depth, "items": 0, "windows": 0})


def _geometry_of(item):
    # The item's SCREEN rect — what the capture frame outlines. The
    # window's origin is added to the scene rect: a moved window (the
    # real-printer mode, a WM repositioning) must not silently shift
    # the outline off its element.
    try:
        window = item.window()
        origin = window.position() if window is not None else QPointF(0, 0)
    except Exception:
        origin = QPointF(0, 0)
    scene = item.mapToScene(QPointF(0, 0))
    return [round(origin.x() + scene.x()), round(origin.y() + scene.y()),
            round(item.width()), round(item.height())]


def _walk(root, depth=24):
    # Depth-first over QQuickItem.childItems() — the VISUAL tree.
    # findChildren(QQuickItem) instead walks the whole QObject graph
    # (every QML-created object under the root) and stalls Cura's
    # GUI thread for minutes on the main window.
    stack = [(root, 0)]
    while stack:
        item, level = stack.pop()
        _WALK_STATS["items"] += 1
        yield item
        if level < depth:
            stack.extend((child, level + 1) for child in item.childItems())


def _main_window():
    # The harness targets the main editor window; Cura keeps popup and
    # dialog windows alive (11 windows total). Walking every window's
    # tree in one verb stalls the GUI thread for minutes.
    best = None
    best_area = 0
    for window in QGuiApplication.allWindows():
        area = window.width() * window.height()
        if area > best_area:
            best_area = area
            best = window
    return best


def _settle(milliseconds):
    """Let the display catch up after a raise — on the GUI thread, so
    the events that carry the activation are actually processed."""
    try:
        qtest = _import_qtest()
        if qtest:
            qtest.QTest.qWait(int(milliseconds))
            return
    except Exception:
        pass
    time.sleep(max(0.0, float(milliseconds) / 1000.0))


def _window_handle(window):
    """The window's native handle where the OS can be asked about it:
    Windows is the one platform whose foreground this process reads
    back directly. Elsewhere the answer is None and the guard falls
    back to Qt's own activation state."""
    try:
        import ctypes
        if not hasattr(ctypes, "windll"):
            return None
        return int(window.winId())
    except Exception:
        return None

def _enum_name(value):
    """A Qt enum as a name, never as int(value).

    PyQt6 hands back the enum wrapper (QWindow.Visibility), and int()
    on it raises — the first live run of the frame probe answered every
    sample with that TypeError and so measured nothing. The name is
    also the readable half in the evidence.
    """
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)
