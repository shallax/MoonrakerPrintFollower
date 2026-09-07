"""Qt tick driver for the smoothed Preview path; the policy stays pure.

The physical path fraction (0..1 within the layer) arrives from
PreviewFollower via `write()`; this object estimates the physical velocity
from consecutive observations, owns the displayed fraction, and advances it
on a timer using the pure policy. It converts to path units against the
view's live max paths at write time. After every write it re-remembers the
plugin-written position so Cura's change watcher never mistakes the
animation for a manual override.

Layer changes are jumped, never smoothed: the head moves to the new layer's
start exactly as the unsmoothed follower would.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QObject, QTimer

from .CuraAdapter import preview_max_paths, set_preview_minimum_path, set_preview_path
from .PreviewSmoothing import advance_display

TICK_MS = 33
# Velocity smoothing time constant, in seconds. Sized in time (not per
# observation) so the behaviour is identical at any polling rate; long
# slow moves produce tiny quantised per-poll deltas, and a faster
# response would make the glide speed wobble visibly between polls.
VELOCITY_TAU = 8.0
# Floor for the observation interval so an immediate second observation
# cannot produce a huge instantaneous velocity.
MIN_OBSERVATION_DT = 0.05
# Cap on the instantaneous rate in layer-fractions per second. Extrusion
# rates are far below this; travel moves spike above it and are clipped so
# the head does not race to the newest observation and stall there.
MAX_VELOCITY = 0.5


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
        self._velocity = 0.0
        self._prev_fraction = None
        self._prev_time = 0.0
        self._last = 0.0

    def write(self, layer: int, fraction: float) -> None:
        """Record the newest observed path fraction for a layer."""
        fraction = max(0.0, min(1.0, float(fraction)))
        now = time.monotonic()
        if layer != self._layer or self._displayed is None:
            # A layer transition (or the first observation): jump, never
            # animate across layers. The velocity estimate restarts too.
            self._layer = layer
            self._target = fraction
            self._displayed = fraction
            self._velocity = 0.0
            self._prev_fraction = fraction
            self._prev_time = now
            self._last = now
            self._timer.stop()
            self._write(fraction)
            return
        if self._prev_fraction is not None:
            dt = max(MIN_OBSERVATION_DT, now - self._prev_time)
            instant = max(0.0, min(MAX_VELOCITY, (fraction - self._prev_fraction) / dt))
            alpha = 1.0 - math.exp(-dt / VELOCITY_TAU)
            self._velocity += (instant - self._velocity) * alpha
        self._prev_fraction = fraction
        self._prev_time = now
        self._target = fraction
        self._last = now
        if self._displayed < fraction:
            self._timer.start()

    def reset(self) -> None:
        """Stop animating; the next write() re-synchronises from the view."""
        self._timer.stop()
        self._layer = self._target = self._displayed = None
        self._velocity = 0.0
        self._prev_fraction = None
        self._prev_time = 0.0

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last))
        self._last = now
        if self._displayed is None or self._target is None:
            self._timer.stop()
            return
        displayed = advance_display(displayed=self._displayed, target=self._target,
                                    velocity=self._velocity, dt=dt)
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
