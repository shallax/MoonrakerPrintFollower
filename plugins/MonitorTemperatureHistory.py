"""Pure temperature history: one ring buffer per sensor, fed by the
auxiliary poll.

The Monitor's temperature chart graphs every chartable temperature
object; the shared predicate in MonitorFormatting
(:func:`chart_temperature_objects`) decides which, so the pane's
temperature list and the chart can never disagree. Each series keeps a
bounded window of samples on a shared monotonic time origin. A feed gap
longer than ``GAP_RESET_SECONDS`` restarts the window (a printer switch
or a long monitor pause must not bridge stale data), and a fresh window
reports ``filling`` until enough samples exist for a meaningful plot.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from .MonitorFormatting import chart_label, chart_temperature_objects, number

# 30 minutes at the 1 s auxiliary cadence, with headroom for bursts.
WINDOW_SECONDS = 1800
MAX_SAMPLES = 1800

# A feed gap longer than this starts a new window.
GAP_RESET_SECONDS = 30.0
# A fresh window reports `filling` until this many seconds of samples.
FILLING_SECONDS = 15.0

# Default chart colours: measured to hold >= 3:1 contrast on Cura's
# light and dark themes (WCAG for data ink), 10 entries so common
# machines never force duplicate hues.
PALETTE = (
    "#d32f2f", "#1976d2", "#388e3c", "#e65100", "#ab47bc",
    "#00838f", "#00897b", "#0097a7", "#f4511e", "#689f38",
)


@dataclass(frozen=True)
class TemperatureSample:
    elapsed: float  # seconds since the first sample in the window
    temperature: float
    target: Optional[float]
    power: Optional[float]


def _is_primary(name: str) -> bool:
    """The mini widget shows primary sensors: extruders, the bed and a
    chamber heater."""
    lower = str(name).lower()
    if lower == "heater_bed" or re.fullmatch(r"extruder\d*", lower):
        return True
    return lower.startswith("heater_generic ") and "chamber" in lower


class TemperatureHistory:
    """Bounded per-sensor sample windows with a shared time origin."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self._window = float(window_seconds)
        self._series: Dict[str, deque] = {}
        self._start: Optional[float] = None
        self._revision = 0
        self._latest_elapsed = 0.0
        self._wall_origin: Optional[float] = None

    @property
    def revision(self) -> int:
        """Bumped on every append and reset; the model uses it to skip
        payload rebuilds when nothing was fed."""
        return self._revision

    @property
    def wall_origin(self) -> Optional[float]:
        """Wall-clock seconds corresponding to elapsed 0, when the model
        supplied one — the chart's HH:MM:SS readouts anchor to it."""
        return self._wall_origin

    @property
    def filling(self) -> bool:
        """True while a fresh window has fewer than FILLING_SECONDS of
        samples — the UI shows a collecting placeholder."""
        return self._start is not None and self._latest_elapsed < FILLING_SECONDS

    def observe(self, auxiliary: Mapping, now: float, wall: Optional[float] = None) -> None:
        """Append one sample per chartable object.

        ``now`` is a monotonic clock value; the first call anchors the
        window's time origin. ``wall`` is the matching wall clock, kept
        so the chart can label samples with clock times.
        """
        readings = chart_temperature_objects(auxiliary)
        if self._start is None:
            self._start = float(now)
        elapsed = float(now) - self._start
        if elapsed < 0:
            # The monotonic clock can step backwards (system suspend):
            # re-anchor instead of letting the trim eat everything.
            self._start = float(now)
            elapsed = 0.0
        if elapsed - self._latest_elapsed > GAP_RESET_SECONDS:
            # A long feed gap is a session break, not data: start a new
            # window so stale curves never bridge it.
            self.reset()
            self._start = float(now)
            elapsed = 0.0
        self._latest_elapsed = elapsed
        cutoff = max(0.0, elapsed - self._window)
        for name, temperature in readings.items():
            value = auxiliary.get(name)
            target = number(value.get("target"), None) if isinstance(value, Mapping) else None
            power = number(value.get("power"), None) if isinstance(value, Mapping) else None
            points = self._series.setdefault(name, deque())
            points.append(TemperatureSample(
                elapsed=elapsed,
                temperature=temperature,
                target=target,
                power=power,
            ))
            while points and points[0].elapsed < cutoff:
                points.popleft()
            if len(points) > MAX_SAMPLES:
                for _ in range(len(points) - MAX_SAMPLES):
                    points.popleft()
        # Trim EVERY series against the cutoff, not just the ones in
        # this reading: a sensor that stops reporting must age out too.
        # Then prune the emptied ones — a vanished sensor must not
        # linger in the legend as a "—" row for the rest of the session
        # (panel UX P3 — ghost legend rows).
        for points in self._series.values():
            while points and points[0].elapsed < cutoff:
                points.popleft()
            if len(points) > MAX_SAMPLES:
                for _ in range(len(points) - MAX_SAMPLES):
                    points.popleft()
        pruned = [name for name, points in self._series.items() if not points]
        for name in pruned:
            del self._series[name]
        if wall is not None:
            self._wall_origin = float(wall) - elapsed
        if readings or pruned:
            self._revision += 1

    def names(self) -> List[str]:
        return sorted(self._series)

    def series(self, name: str) -> List[TemperatureSample]:
        return list(self._series.get(str(name), ()))

    def points(self, name: str) -> List[List[float]]:
        """[[elapsed, temperature], ...] for the QML chart."""
        return [[sample.elapsed, sample.temperature] for sample in self._series.get(str(name), ())]

    def target_segments(self, name: str) -> List[List[List[float]]]:
        """Segments of [[elapsed, target], ...]; None-target gaps split
        the polyline so the chart draws literal breaks instead of
        bridging an off period."""
        return _segments([sample.elapsed, sample.target]
                         for sample in self._series.get(str(name), ()) if sample.target is not None)

    def power_segments(self, name: str) -> List[List[List[float]]]:
        """Segments of [[elapsed, power], ...]; None-power gaps split."""
        return _segments([sample.elapsed, sample.power]
                         for sample in self._series.get(str(name), ()) if sample.power is not None)

    def reset(self) -> None:
        self._series.clear()
        self._start = None
        self._latest_elapsed = 0.0
        self._wall_origin = None
        self._revision += 1  # a reset changes the payload: force rebuilds


def _segments(track) -> List[List[List[float]]]:
    """Split a [[elapsed, value], ...] track into contiguous segments at
    elapsed jumps larger than 4 s (a None-gap or a feed pause). The 1 s
    printing cadence and the 2.5 s idle cadence both stay well inside
    that threshold, so only real gaps split."""
    segments: List[List[List[float]]] = []
    current: List[List[float]] = []
    previous_elapsed = None
    for point in track:
        if previous_elapsed is not None and point[0] - previous_elapsed > 4.0:
            if len(current) >= 2:
                segments.append(current)
            current = []
        current.append(point)
        previous_elapsed = point[0]
    if len(current) >= 2:
        segments.append(current)
    return segments


def chart_payload(history: "TemperatureHistory", config: Mapping) -> dict:
    """The QML-facing chart model: one entry per series with its points,
    gap-split target/power segments, the primary flag for the mini
    widget, plus the display toggles and palette. Colours fall back to
    the palette by sorted-name index, so the defaults are stable across
    restarts."""
    config = config if isinstance(config, Mapping) else {}
    visible = config.get("visible") if isinstance(config.get("visible"), Mapping) else {}
    colors = config.get("colors") if isinstance(config.get("colors"), Mapping) else {}
    series = []
    for index, name in enumerate(history.names()):
        series.append({
            "name": name,
            "label": chart_label(name),
            "color": str(colors.get(name) or PALETTE[index % len(PALETTE)]),
            "visible": bool(visible.get(name, True)),
            "primary": _is_primary(name),
            "points": history.points(name),
            "targets": history.target_segments(name),
            "powers": history.power_segments(name),
        })
    return {
        "series": series,
        "showTargets": bool(config.get("showTargets", True)),
        "showPower": bool(config.get("showPower", True)),
        "palette": list(PALETTE),
        "filling": history.filling,
        "wallOrigin": history.wall_origin,
    }
