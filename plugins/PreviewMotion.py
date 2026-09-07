"""Qt tick driver for the smoothed Preview path; the policy stays pure.

The physical path fraction (0..1 within the layer) arrives from
PreviewFollower via `write()`; this object owns the displayed fraction,
advances it on a timer using the pure policy, and writes the view through the
typed adapters, converting to path units against the view's live max paths.
After every write it re-remembers the plugin-written position so Cura's
change watcher never mistakes the animation for a manual override.

Layer changes are jumped, never smoothed: the head moves to the new layer's
start exactly as the unsmoothed follower would.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer

from .CuraAdapter import preview_max_paths, set_preview_minimum_path, set_preview_path
from .PreviewSmoothing import advance_display

TICK_MS = 33


class PreviewMotion(QObject):
    def __init__(self, cura, remember, parent=None):
        super().__init__(parent)
        self._cura = cura
        self._remember = remember
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._layer = None
        self._target = None
        self._displayed = None
        self._last = 0.0

    def write(self, layer: int, fraction: float) -> None:
        """Record the newest observed path fraction for a layer."""
        fraction = max(0.0, min(1.0, float(fraction)))
        if layer != self._layer or self._displayed is None:
            # A layer transition (or the first observation): jump, never
            # animate across layers.
            self._layer = layer
            self._target = fraction
            self._displayed = fraction
            self._last = time.monotonic()
            self._timer.stop()
            self._write(fraction)
            return
        self._target = fraction
        self._last = time.monotonic()
        if self._displayed < fraction:
            self._timer.start()

    def reset(self) -> None:
        """Stop animating; the next write() re-synchronises from the view."""
        self._timer.stop()
        self._layer = self._target = self._displayed = None

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last))
        self._last = now
        if self._displayed is None or self._target is None:
            self._timer.stop()
            return
        displayed = advance_display(displayed=self._displayed, target=self._target, dt=dt)
        self._displayed = displayed
        self._write(displayed)
        if displayed >= self._target:
            self._timer.stop()

    def _write(self, fraction: float) -> None:
        view = self._cura.view
        if view is None:
            return
        maximum = preview_max_paths(view)
        if maximum is None or maximum <= 0:
            return
        with self._cura.writing_preview():
            set_preview_path(view, fraction * maximum)
            set_preview_minimum_path(view, 0)
        self._remember()

    def close(self) -> None:
        self._timer.stop()
