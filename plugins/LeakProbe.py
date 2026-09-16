"""TEMPORARY overnight-leak instrumentation (stripped before release).

One tick per minute appends a line to ~/moonraker_leak.log naming
WHAT grows, on three axes:

- the process RSS,
- tracemalloc's fastest-growing Python allocation tracebacks,
- the QML side's per-class item counts (diffed tick-to-tick), and
- the plugin runtime's own collection sizes (diffed tick-to-tick),
  so a growing ring, cache or pending dict is named by path.

A run whose RSS climbs while tracemalloc stays flat and the item
counts hold is a texture/GL-side accumulation, which narrows the hunt
to the rendering paths. Everything is wrapped: the probe must never
take the plugin down.
"""
from __future__ import annotations

import time
import tracemalloc
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QTimer

_LOG_PATH = Path.home() / "moonraker_leak.log"
_INTERVAL_MS = 60_000
_TOP_N = 8


def _rss_kb() -> int:
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def _qml_class_counts():
    """A per-class census of every QQuickItem across the engine's
    root windows. The tick diff names the accumulating item type."""
    try:
        from PyQt6.QtQuick import QQuickItem, QQuickWindow
    except Exception as exc:
        return {"qml-import-err": repr(exc)[:60]}
    from UM.Application import Application
    counts = {}
    try:
        for root in Application.getInstance().getQmlEngine().rootObjects():
            if isinstance(root, QQuickWindow):
                root = root.contentItem()
                if root is None:
                    continue
            if not isinstance(root, QQuickItem):
                continue
            stack = deque([root])
            while stack:
                child = stack.pop()
                name = type(child).__name__
                counts[name] = counts.get(name, 0) + 1
                stack.extend(child.childItems())
    except Exception as exc:
        return {"walk-err": repr(exc)[:60]}
    return counts


def _runtime_sizes(runtime):
    """Sizes of every collection reachable within two attribute hops
    of the runtime, keyed by path (e.g. 'console._transcript'). The
    component level deliberately includes private attributes — the
    interesting accumulators live there."""
    sizes = {}
    try:
        for name in dir(runtime):
            if name.startswith("_"):
                continue
            try:
                component = getattr(runtime, name)
            except Exception:
                continue
            for attr in dir(component):
                if attr.startswith("__"):
                    continue
                try:
                    value = getattr(component, attr)
                except Exception:
                    continue
                if isinstance(value, (list, dict, deque, tuple, str, set, bytes, bytearray)):
                    sizes[f"{name}.{attr}"] = len(value)
    except Exception as exc:
        sizes["walk-err"] = repr(exc)[:60]
    return sizes


def _diff(previous, current):
    """Positive-delta entries, largest first: (key, previous, current)."""
    rows = []
    for key, size in current.items():
        if not isinstance(size, int):
            continue
        before = previous.get(key, 0)
        if isinstance(before, int) and size > before:
            rows.append((key, before, size))
    rows.sort(key=lambda row: row[2] - row[1], reverse=True)
    return rows[: _TOP_N]


class LeakProbe:
    """The one-minute sampler. Started once from the plugin's
    register() hook for the diagnostic snapshot ONLY."""

    def __init__(self, runtime, parent=None):
        self.runtime = runtime
        self._timer = QTimer(parent)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        # NOTHING heavy here: the probe must not touch tracemalloc or
        # the QML engine during plugin load — the allocator tax and
        # the engine walk mid-initialization stalled Cura's startup
        # after "Loading plugins" (the author's report). Everything
        # defers to the first timed tick, a minute into the session.
        self._trace_snapshot = None
        self._qml_previous = {}
        self._sizes_previous = {}
        self._log("start")
        self._timer.start()

    def _top_traces(self) -> list:
        try:
            if self._trace_snapshot is None:
                # The first tick arms tracemalloc here — never during
                # plugin load — and its first diff covers the minute
                # since, not Cura's startup allocations.
                tracemalloc.start(25)
                self._trace_snapshot = tracemalloc.take_snapshot()
                return ["trace-armed"]
            snapshot = tracemalloc.take_snapshot()
            growth = snapshot.compare_to(self._trace_snapshot, "traceback")
            self._trace_snapshot = snapshot
            lines = []
            for stat in growth[:_TOP_N]:
                frames = stat.traceback.format()[-3:]
                short = " <- ".join(frame.splitlines()[0].strip() for frame in frames)
                lines.append(f"py +{stat.size_diff:+d}B {stat.count_diff:+d} {short[:220]}")
            return lines
        except Exception as exc:
            return [f"trace-err {exc!r}"]

    def _tick(self):
        try:
            self._log("rss=%dkb" % _rss_kb())
        except Exception:
            pass
        try:
            qml = _qml_class_counts()
            for row in _diff(self._qml_previous, qml):
                self._log(f"  qml {row[0]}: {row[1]} -> {row[2]}")
            self._qml_previous = qml
        except Exception as exc:
            self._log(f"  qml-err {exc!r}")
        try:
            sizes = _runtime_sizes(self.runtime)
            for row in _diff(self._sizes_previous, sizes):
                self._log(f"  size {row[0]}: {row[1]} -> {row[2]}")
            self._sizes_previous = sizes
        except Exception as exc:
            self._log(f"  size-err {exc!r}")
        for line in self._top_traces():
            self._log(line)

    def _log(self, message):
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.0f} {message}\n")
                handle.flush()
        except Exception:
            pass


_ACTIVE = None


def start_leak_probe(runtime, parent=None):
    """The single entry. The module global pins the instance: the
    parented timer alone was not enough — the first build's probe was
    collected before its first timed tick and logged nothing."""
    global _ACTIVE
    _ACTIVE = LeakProbe(runtime, parent)
    return _ACTIVE
