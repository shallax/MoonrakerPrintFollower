"""The frame counter and the heartbeat: whether the window is painting.

The counter rides the window's own frameSwapped signal. The heartbeat is
the measurement: an item placed in the application's own scene graph,
driven on the GUI thread, verified by read-back, awaited with a bounded
deadline, and removed before the verb returns so no still can carry it.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QUrl
from PyQt6.QtQml import QQmlComponent, qmlEngine
from .scene import _settle


_WINDOW_SEQ = [0]


class _FrameCounter:
    """Counts the main window's delivered frames, and remembers how
    the window answered the heartbeats placed on it.

    frameSwapped is emitted on the render thread and delivered to the
    connectING thread, so a slot here is called on the driver's thread
    and a plain counter is enough — no lock, no cross-thread reads.
    The window is remembered because a leg's boot can replace it, and
    the heartbeat bookkeeping is the WINDOW's rather than the run's:
    a count with no history on this window says nothing about it, so a
    window the counter has just attached to is uncalibrated until a
    heartbeat of its own is answered.
    """

    def __init__(self):
        self.count = 0
        self.window = None
        self.window_id = 0
        self.heartbeats = 0
        self.answered = 0
        self.calibrated = False

    def _swapped(self):
        self.count += 1

    def attach(self, window):
        """Attach to `window`, and say whether the counter is on it:
        a window that never took the connection has no count to report,
        which is a different answer from a count of zero."""
        if self.window is window:
            return self.window is not None
        previous = self.window
        self.window = window
        if window is not None:
            try:
                window.frameSwapped.connect(self._swapped)
            except Exception:
                self.window = previous
                return False
        if previous is not None:
            try:
                previous.frameSwapped.disconnect(self._swapped)
            except Exception:
                pass
        self.count = 0
        _WINDOW_SEQ[0] += 1
        self.window_id = _WINDOW_SEQ[0]
        self.heartbeats = 0
        self.answered = 0
        self.calibrated = False
        return True


_FRAMES = _FrameCounter()


# ─── The visual heartbeat (the liveness proof) ───
# A passive frame count cannot tell an idle window from a frozen one:
# the render request that read it proves nothing about whether Qt had a
# reason to paint, and a scenario whose steps changed only model state
# answers zero correctly (measured: every flat span of the 4.6.0 release
# run, on windows that painted again later in the same leg). What
# separates the two is a change the scene graph cannot ignore, so the
# sample makes one: a temporary item is parented into the window's
# content item, its opacity is driven on Cura's GUI thread (this server
# is served on it), the change is read back off the item, and the
# sample then waits for the frameSwapped the change made due —
# event-driven and bounded, twice, because a software rasteriser's
# frame interval is of the same order as a fixed settle and one missed
# frame is not a stopped renderer.
#
# The item is 4x4 px and lives only for the heartbeat: it is out of the
# scene before the verb returns, so no step's still can carry it, and
# its footprint is three orders of magnitude below the static verdict's
# frame-mean tolerance (MAD 1.0 of 255).
HEARTBEAT_OBJECT = "mpfLivenessHeartbeat"
HEARTBEAT_DEADLINE_MS = 1500
HEARTBEAT_CHANGES = (("opacity", 1.0), ("opacity", 0.0))
HEARTBEAT_QML = b"""
import QtQuick 2.15
Rectangle {
    objectName: "mpfLivenessHeartbeat"
    x: 2
    y: 2
    width: 4
    height: 4
    z: 1e9
    color: "#ff00ff"
    opacity: 0.0
}
"""


def _heartbeat_engine(window, engine):
    """The engine the heartbeat item is built with: the app's own, so
    the item lands in the scene the frame must come from.

    The server discovered it at registration and owns it; it is handed
    in rather than read back off the server, and the fallbacks below
    still cover an engine it never found.
    """
    if engine is None:
        try:
            from UM.Qt.QtApplication import QtApplication
            engine = QtApplication.getInstance()._qml_engine
        except Exception:
            engine = None
    if engine is None and window is not None:
        try:
            engine = qmlEngine(window.contentItem())
        except Exception:
            engine = None
    return engine


def _place_heartbeat(window, engine):
    """The item, parented into the window's scene, or why not."""
    engine = _heartbeat_engine(window, engine)
    if engine is None:
        return None, "no qml engine was reachable to build the heartbeat item"
    component = QQmlComponent(engine)
    component.setData(HEARTBEAT_QML, QUrl())
    item = component.create()
    if item is None:
        errors = "; ".join(str(error.toString()) for error in component.errors())
        return None, ("the heartbeat item did not build"
                      + (f": {errors}" if errors else ""))
    try:
        item.setParentItem(window.contentItem())
    except Exception as exc:
        return None, f"the heartbeat item could not be placed in the window: {exc!r}"
    return item, ""


def _remove_heartbeat(item):
    """Out of the scene and gone: a test-only item that outlived its
    heartbeat would sit in every later still."""
    try:
        item.setParentItem(None)
        item.deleteLater()
        return True
    except Exception:
        return False


def _await_frames(before, deadline_ms):
    """Wait for a frame past `before`, event-driven and bounded.

    qWait runs the event loop — the render loop lives in it, and on a
    software rasteriser so does the paint — in short slices, so the
    deadline is checked against the clock rather than trusted to one
    sleep, and the GUI thread keeps serving the loop it must."""
    started = time.monotonic()
    deadline = started + max(0.0, float(deadline_ms) / 1000.0)
    while _FRAMES.count <= before:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        _settle(min(50.0, left * 1000.0))
    return _FRAMES.count - before, int((time.monotonic() - started) * 1000.0)


def _plain(value):
    """A value the evidence can hold: json will not take a QVariant."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _same_number(left, right):
    try:
        return abs(float(left) - float(right)) < 1e-6
    except (TypeError, ValueError):
        return False


def _drive_heartbeat(item, window, name, value, deadline_ms):
    """One change to the item, read back off it, then awaited."""
    attempt = {"property": name, "to": value, "verified": False,
               "in_scene": False, "is_visible": False, "answered": False,
               "gained": 0, "waited_ms": 0}
    try:
        attempt["from"] = _plain(item.property(name))
        item.setProperty(name, value)
        attempt["read_back"] = _plain(item.property(name))
        attempt["verified"] = _same_number(attempt["read_back"], value)
        attempt["in_scene"] = item.parentItem() is window.contentItem()
        attempt["is_visible"] = bool(item.isVisible())
    except Exception as exc:
        attempt["error"] = repr(exc)
        return attempt
    if not (attempt["verified"] and attempt["in_scene"] and attempt["is_visible"]):
        # A change that did not land is a measurement that could not be
        # taken, never a renderer that did not paint.
        return attempt
    attempt["swapped_before"] = _FRAMES.count
    gained, waited = _await_frames(_FRAMES.count, deadline_ms)
    attempt["swapped_after"] = _FRAMES.count
    attempt["gained"] = gained
    attempt["waited_ms"] = waited
    attempt["answered"] = gained > 0
    return attempt


def _heartbeat_reason(record):
    """What the heartbeat saw, in one line: the change it made, the
    count it moved and how long it took, or that no frame answered."""
    plot = "; ".join(
        f"{attempt.get('property')} {attempt.get('from')}->{attempt.get('to')} "
        f"gained {attempt.get('gained')} in {attempt.get('waited_ms')}ms"
        for attempt in record.get("attempts") or [])
    if record.get("answered"):
        return f"the window answered the heartbeat ({plot})"
    if not record.get("asked"):
        return record.get("reason") or "the heartbeat was not placed"
    if any(attempt.get("error") or not attempt.get("verified")
           for attempt in record.get("attempts") or []):
        return ("the heartbeat change did not verifiably land on the item "
                f"({plot}), so no frame was due from it")
    return (f"the window answered no frame to {len(record.get('attempts') or [])} "
            f"forced scene change(s) inside {record.get('deadline_ms')}ms each "
            f"({plot})")


def _heartbeat(window, engine, deadline_ms=HEARTBEAT_DEADLINE_MS):
    """One sample's forced scene change, awaited and recorded.

    The record rides the evidence whether or not a frame arrived, and
    it names what could not be measured (an item that would not build,
    a change that did not land) as its own reason, so a missing
    measurement can never read as a renderer that painted."""
    record = {"asked": False, "item": HEARTBEAT_OBJECT,
              "sequence": _FRAMES.heartbeats + 1,
              "window": f"win#{_FRAMES.window_id}",
              "calibrated": bool(_FRAMES.calibrated),
              "answered": False, "deadline_ms": int(deadline_ms),
              "waited_ms": 0, "attempts": [], "cleaned": False}
    item, error = _place_heartbeat(window, engine)
    if item is None:
        record["reason"] = error
        return record
    record["asked"] = True
    _FRAMES.heartbeats += 1
    try:
        for name, value in HEARTBEAT_CHANGES:
            attempt = _drive_heartbeat(item, window, name, value, deadline_ms)
            record["attempts"].append(attempt)
            record["waited_ms"] += attempt["waited_ms"]
            if attempt["answered"]:
                record["answered"] = True
                break
    finally:
        record["cleaned"] = _remove_heartbeat(item)
    if record["answered"]:
        _FRAMES.calibrated = True
        _FRAMES.answered += 1
    record["reason"] = _heartbeat_reason(record)
    return record
