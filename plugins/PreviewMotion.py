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

from collections import deque
import math
import time

from PyQt6.QtCore import QObject, QTimer

from .CuraAdapter import preview_max_paths, set_preview_minimum_path, set_preview_path
from .PreviewSmoothing import advance_display

TICK_MS = 33
# The physical rate is derived from a sliding window of observations, not
# from consecutive polls: per-poll deltas are tiny and quantised at fast
# polling rates, and differencing them makes the glide speed wobble.
VELOCITY_WINDOW = 2.0
# A window shorter than this produces no rate update; the previous estimate
# is kept until enough history accumulates.
MIN_RATE_SPAN = 0.5
# Mild additional smoothing of the windowed rate; sized in time so the
# behaviour is identical at any polling rate.
VELOCITY_TAU = 1.5
# Cap on the instantaneous rate in layer-fractions per second. Extrusion
# rates are far below this; travel moves spike above it and are clipped so
# the head does not race to the newest observation and stall there.
MAX_VELOCITY = 0.5
# Fraction of the previous layer's velocity kept when a new layer starts,
# so the head does not begin every layer from a standstill.
VELOCITY_WARM_START = 0.8
# The observations are stale samples of the true trajectory (they only
# update near path ends/starts). Treating the newest one as a hard wall
# makes the head dwell at every stale step. The head may glide past the
# newest observation by at most this many seconds of estimated progress;
# if the next observation shows reality behind the head, the head waits
# there — it never snaps back.
LOOKAHEAD_S = 0.3
class PreviewMotion(QObject):
    def __init__(self, cura, remember, parent=None, trace_path=None):
        super().__init__(parent)
        self._cura = cura
        self._remember = remember
        self._trace_path = trace_path
        self._trace_next = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._layer = None
        self._target = None
        self._displayed = None
        self._velocity = 0.0
        self._history = deque()
        self._last = 0.0

    def write(self, layer: int, fraction: float, method: str = "") -> None:
        """Record the newest observed path fraction for a layer."""
        fraction = max(0.0, min(1.0, float(fraction)))
        now = time.monotonic()
        self._trace("obs", now, layer, fraction, method)
        if layer != self._layer or self._displayed is None:
            # A layer transition (or the first observation): jump, never
            # animate across layers. Layers print at similar rates, so the
            # velocity estimate is warm-started rather than reset.
            self._layer = layer
            self._target = fraction
            self._displayed = fraction
            self._velocity *= VELOCITY_WARM_START
            self._history.clear()
            self._history.append((now, fraction))
            self._last = now
            self._timer.stop()
            self._write(fraction)
            return
        self._history.append((now, fraction))
        while self._history and now - self._history[0][0] > VELOCITY_WINDOW:
            self._history.popleft()
        span = now - self._history[0][0]
        if span >= MIN_RATE_SPAN:
            instant = max(0.0, min(MAX_VELOCITY, (fraction - self._history[0][1]) / span))
            if instant > 0.0:
                alpha = 1.0 - math.exp(-span / VELOCITY_TAU)
                self._velocity += (instant - self._velocity) * alpha
            # A flat stretch (corner dwell, a pause, an ambiguous poll) carries
            # no rate information. Keep the previous estimate instead of letting
            # it decay toward zero: collapsing it here makes the head lag at
            # every path end and then surge to catch up.
        self._target = fraction
        self._last = now
        if self._displayed < fraction:
            self._timer.start()

    def reset(self) -> None:
        """Stop animating; the next write() re-synchronises from the view."""
        self._timer.stop()
        self._layer = self._target = self._displayed = None
        self._velocity = 0.0
        self._history.clear()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last))
        self._last = now
        if self._displayed is None or self._target is None:
            self._timer.stop()
            return
        ceiling = min(1.0, self._target + self._velocity * LOOKAHEAD_S)
        displayed = advance_display(displayed=self._displayed, target=ceiling,
                                    velocity=self._velocity, dt=dt)
        self._displayed = displayed
        self._trace("tick", now, self._layer, displayed)
        self._write(displayed)
        if displayed >= ceiling:
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

    def _trace(self, event: str, now: float, layer, fraction: float, method: str = "") -> None:
        if not self._trace_path:
            return
        try:
            import os
            if now >= self._trace_next:
                self._trace_next = now + 0.5  # sample ticks at ~2 Hz
            elif event == "obs":
                pass
            else:
                return
            try:
                size = os.path.getsize(self._trace_path)
            except OSError:
                size = 0
            if size > 512 * 1024:
                mode = "w"
            else:
                mode = "a"
            with open(self._trace_path, mode, encoding="utf-8") as handle:
                if mode == "w":
                    handle.write("time,event,layer,fraction,displayed,velocity,method\n")
                handle.write(f"{now:.3f},{event},{layer},{fraction:.6f},{self._displayed if self._displayed is not None else -1:.6f},"
                             f"{self._velocity:.6f},{method}\n")
        except Exception:
            pass

    def close(self) -> None:
        self._timer.stop()
