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
from itertools import islice
from typing import Dict, List, Mapping, Optional

from .MonitorFormatting import chart_label, chart_temperature_objects, number

# 30 minutes at the chart's fixed 1 s sampling cadence (Mainsail's
# temperature store cadence — decoupled from the auxiliary delivery
# slider, which drives the pane readouts only). The cap carries one
# boundary sample of headroom so the elapsed trim always owns the
# domain; the cap is pure memory insurance.
WINDOW_SECONDS = 1800
MAX_SAMPLES = 1801

# The mini sparkline's render budget: the compact chart is at most
# ~250 px wide at the plugin's smallest supported scale, so 240 kept
# points is one per pixel — a fuller reduction could not be seen. The
# reduction keeps each bucket's minimum AND maximum, so no spike is
# erased, and the raw 30-minute window it draws from is never touched.
MINI_RENDER_BUDGET = 240

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
            # re-anchor instead of letting the trim eat everything. The
            # samples already kept belong to the old timebase, so the
            # window restarts — the same session break a long gap is —
            # and every series stays ordered by elapsed, which the
            # chart's nearest-sample search relies on.
            self.reset()
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

    def points_and_bounds(self, name: str):
        """[[elapsed, temperature], ...] and the temperature/elapsed
        render domain, built in ONE pass over the window — the payload
        needs both, and the split form walked the same samples twice."""
        samples = self._series.get(str(name))
        points: List[List[float]] = []
        if not samples:
            return points, {}
        temp_min = temp_max = samples[0].temperature
        for sample in samples:
            points.append([sample.elapsed, sample.temperature])
            if sample.temperature < temp_min:
                temp_min = sample.temperature
            elif sample.temperature > temp_max:
                temp_max = sample.temperature
        # Elapsed ascends: the ends ARE the domain's ends.
        return points, {"tempMin": temp_min, "tempMax": temp_max,
                        "elapsedMin": samples[0].elapsed, "elapsedMax": samples[-1].elapsed}

    def target_segments(self, name: str) -> List[List[List[float]]]:
        """Segments of [[elapsed, target], ...]; None-target gaps split
        the polyline so the chart draws literal breaks instead of
        bridging an off period. Constant runs are compressed to their
        first and last sample: a setpoint holds for minutes at a time,
        and the drawn band, its fill and the values the chart's render
        domain scans are all unchanged by dropping the samples between."""
        return [_compress_steps(segment) for segment in _segments(
            [sample.elapsed, sample.target]
            for sample in self._series.get(str(name), ()) if sample.target is not None)]

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


def _compress_steps(segment: List[List[float]]) -> List[List[float]]:
    """Keep only the samples a step-shaped track needs: the first and
    last sample of every run of equal values. The plot joins the kept
    samples with straight lines, and the dropped ones all sat on the
    horizontal line between their neighbours, so the drawn shape — and
    the set of values drawn — is bit-for-bit what the full track drew.
    """
    compressed: List[List[float]] = []
    for index, point in enumerate(segment):
        if not compressed or compressed[-1][1] != point[1]:
            compressed.append(point)
        elif index + 1 == len(segment) or segment[index + 1][1] != point[1]:
            compressed.append(point)
    return compressed


def _segments(track) -> List[List[List[float]]]:
    """Split a [[elapsed, value], ...] track into contiguous segments at
    elapsed jumps larger than 4 s (a None-gap or a feed pause). The 2.5 s
    auxiliary cadence (flat in all states) stays well inside that
    threshold, so only real gaps split."""
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


def _target_bounds(segments) -> dict:
    """The lit-setpoint extremes over already-compressed segments — one
    walk, no per-sample lists. Setpoints at or below zero are heater-off
    markers, not data, so they stay out."""
    target_min = target_max = None
    for segment in segments:
        for point in segment:
            if point[1] > 0:
                if target_min is None or point[1] < target_min:
                    target_min = point[1]
                if target_max is None or point[1] > target_max:
                    target_max = point[1]
    if target_min is None:
        return {}
    return {"targetMin": target_min, "targetMax": target_max}


def mini_names(names: List[str], visible: Mapping) -> List[str]:
    """The preview's series selection: visible primaries first, topped
    up to two with visible others; without any primary, the first two
    visible others. The one policy — the mini payload's points and the
    legend's mini row list both resolve from it."""
    visible_names = [name for name in names if bool(visible.get(name, True))]
    primaries = [name for name in visible_names if _is_primary(name)]
    others = [name for name in visible_names if not _is_primary(name)]
    if primaries:
        return primaries + others[:max(0, 2 - len(primaries))]
    return others[:2]


def series_metadata(history: "TemperatureHistory", config: Mapping) -> List[dict]:
    """Identity, label, colour and visibility for EVERY series — the
    legend's source. The data payloads omit hidden series; this never
    does, so a hidden sensor can always be re-enabled."""
    config = config if isinstance(config, Mapping) else {}
    visible = config.get("visible") if isinstance(config.get("visible"), Mapping) else {}
    colors = config.get("colors") if isinstance(config.get("colors"), Mapping) else {}
    return [{"name": name, "label": chart_label(name),
             "color": str(colors.get(name) or PALETTE[index % len(PALETTE)]),
             "visible": bool(visible.get(name, True)),
             "primary": _is_primary(name)}
            for index, name in enumerate(history.names())]


def latest_values(history: "TemperatureHistory") -> Dict[str, float]:
    """Each series' current temperature — one scalar per name, so a
    label never searches a full payload to read its live value."""
    return {name: points[-1].temperature
            for name, points in history._series.items() if points}


def _emit_bucket(points, bucket_min, bucket_max) -> None:
    """Append one bucket's two extremes in elapsed order; a bucket whose
    minimum and maximum are the same sample contributes once."""
    if bucket_min is None:
        return
    if bucket_min is bucket_max:
        points.append([bucket_min.elapsed, bucket_min.temperature])
    elif bucket_min.elapsed <= bucket_max.elapsed:
        points.append([bucket_min.elapsed, bucket_min.temperature])
        points.append([bucket_max.elapsed, bucket_max.temperature])
    else:
        points.append([bucket_max.elapsed, bucket_max.temperature])
        points.append([bucket_min.elapsed, bucket_min.temperature])


def _mini_points(samples) -> tuple:
    """One series' bounded render representation for the sparkline, in a
    single pass over the raw window — no intermediate full-history
    arrays. Beyond the budget, the interior samples collapse into index
    buckets and each bucket keeps its temperature minimum AND maximum,
    so a narrow spike always survives as its extremes; the first and
    last samples are kept outright. The bounds come from the same
    pass: every sample is scanned, so the reduction's domain is the
    raw window's domain."""
    if not samples:
        return [], {}
    first = samples[0]
    last = samples[-1]
    if len(samples) <= MINI_RENDER_BUDGET:
        points = [[sample.elapsed, sample.temperature] for sample in samples]
        temp_min = temp_max = first.temperature
        for sample in samples:
            if sample.temperature < temp_min:
                temp_min = sample.temperature
            elif sample.temperature > temp_max:
                temp_max = sample.temperature
        return points, {"tempMin": temp_min, "tempMax": temp_max,
                        "elapsedMin": first.elapsed, "elapsedMax": last.elapsed}
    points = [[first.elapsed, first.temperature]]
    temp_min = temp_max = first.temperature
    bucket_count = (MINI_RENDER_BUDGET - 2) // 2
    step = (len(samples) - 2) / bucket_count
    bucket_min = bucket_max = None
    current = -1
    # islice, not a slice: a slice would materialise the whole interior
    # as a throwaway list, and the point of the reduction is to never
    # allocate at history size.
    for index, sample in enumerate(islice(samples, 1, len(samples) - 1)):
        if sample.temperature < temp_min:
            temp_min = sample.temperature
        elif sample.temperature > temp_max:
            temp_max = sample.temperature
        bucket = int(index // step)
        if bucket != current:
            _emit_bucket(points, bucket_min, bucket_max)
            current = bucket
            bucket_min = bucket_max = sample
        else:
            if sample.temperature < bucket_min.temperature:
                bucket_min = sample
            elif sample.temperature > bucket_max.temperature:
                bucket_max = sample
    _emit_bucket(points, bucket_min, bucket_max)
    if last.temperature < temp_min:
        temp_min = last.temperature
    elif last.temperature > temp_max:
        temp_max = last.temperature
    points.append([last.elapsed, last.temperature])
    return points, {"tempMin": temp_min, "tempMax": temp_max,
                    "elapsedMin": first.elapsed, "elapsedMax": last.elapsed}


def mini_chart_payload(history: "TemperatureHistory", config: Mapping) -> dict:
    """The compact-preview payload (temperatureChartMini): ONLY the
    selected mini series ride along, each as its bounded render
    reduction — never targets or power, which the compact chart does
    not draw. The payload's SIZE therefore stops growing once the
    window outgrows the render budget (the build stays one
    allocation-light scan over the raw window per feed), and hidden
    sensors contribute nothing."""
    config = config if isinstance(config, Mapping) else {}
    visible = config.get("visible") if isinstance(config.get("visible"), Mapping) else {}
    colors = config.get("colors") if isinstance(config.get("colors"), Mapping) else {}
    selected = set(mini_names(history.names(), visible))
    series = []
    for index, name in enumerate(history.names()):
        if name not in selected:
            continue
        points, bounds = _mini_points(history._series.get(name))
        series.append({
            "name": name,
            "label": chart_label(name),
            "color": str(colors.get(name) or PALETTE[index % len(PALETTE)]),
            "visible": True,
            "primary": _is_primary(name),
            "points": points,
            "bounds": bounds,
        })
    return {
        "series": series,
        "showTargets": False,
        "showPower": False,
        "palette": list(PALETTE),
        "filling": history.filling,
        "wallOrigin": history.wall_origin,
    }


def chart_payload(history: "TemperatureHistory", config: Mapping) -> dict:
    """The full pop-over payload (temperatureChartFull): every visible
    series' complete window, with target and power segments built ONLY
    while their toggles are on. Hidden series stay out entirely — their
    metadata lives in series_metadata, and re-enabling one rebuilds
    this payload through the model's config-keyed cache."""
    config = config if isinstance(config, Mapping) else {}
    visible = config.get("visible") if isinstance(config.get("visible"), Mapping) else {}
    colors = config.get("colors") if isinstance(config.get("colors"), Mapping) else {}
    show_targets = bool(config.get("showTargets", True))
    show_power = bool(config.get("showPower", True))
    series = []
    for index, name in enumerate(history.names()):
        if not bool(visible.get(name, True)):
            continue
        points, bounds = history.points_and_bounds(name)
        targets = history.target_segments(name) if show_targets else []
        if targets:
            bounds.update(_target_bounds(targets))
        series.append({
            "name": name,
            "label": chart_label(name),
            "color": str(colors.get(name) or PALETTE[index % len(PALETTE)]),
            "visible": True,
            "primary": _is_primary(name),
            "points": points,
            "targets": targets,
            "powers": history.power_segments(name) if show_power else [],
            "bounds": bounds,
        })
    return {
        "series": series,
        "showTargets": show_targets,
        "showPower": show_power,
        "palette": list(PALETTE),
        "filling": history.filling,
        "wallOrigin": history.wall_origin,
    }


# The closed pop-over's full payload: one shared object, so the model
# republishes the same identity every feed and the QML property never
# re-converts while no full chart exists to draw the data.
DORMANT_CHART = {
    "series": [],
    "showTargets": True,
    "showPower": True,
    "palette": list(PALETTE),
    "filling": False,
    "wallOrigin": None,
}
