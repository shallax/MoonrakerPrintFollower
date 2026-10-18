# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover

"""Run-out transitions and manual stop estimates bound to one print run."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math


SENSOR_PREFIXES = ("filament_switch_sensor ", "filament_motion_sensor ")
MAX_EVENTS = 64


@dataclass(frozen=True)
class StopEvidence:
    kind: str  # sensor or manual
    job_key: tuple
    sensor: str
    observed_at: float
    layer: int | None
    split: int | None
    file_position: int | None
    precision: str  # motion, parser, or estimate

    def document(self):
        row = asdict(self)
        row["job_key"] = list(self.job_key)
        return row


class RunoutEvents:
    """Transition recorder; an initial false sample is never an event.

    Moonraker sensor status has no historical file offset. An event's motion
    boundary is the best physical observation available at notification time,
    not a claim that extrusion stopped there. Parser offsets are labelled as
    read-ahead evidence. Reconnects establish a new baseline.
    """

    def __init__(self):
        self._job = None
        self._sensor_state = {}
        self._events = []

    @property
    def events(self):
        return tuple(self._events)

    def bind(self, job_key):
        key = tuple(job_key) if job_key is not None else None
        if key != self._job:
            self._job = key
            self._sensor_state = {}
            self._events = []

    def observe(self, job_key, sensors, *, at, selected=(), layer=None, split=None, file_position=None):
        self.bind(job_key)
        if self._job is None or not isinstance(sensors, Mapping):
            return ()
        try:
            at = float(at)
        except (TypeError, ValueError, OverflowError):
            return ()
        if not math.isfinite(at):
            return ()
        new = []
        seen = set()
        selected = set(selected)
        for name, value in sorted(sensors.items()):
            if not str(name).lower().startswith(SENSOR_PREFIXES) or not isinstance(value, Mapping):
                continue
            if name not in selected:
                continue
            seen.add(name)
            detected = value.get("filament_detected")
            enabled = value.get("enabled", True)
            if not isinstance(detected, bool) or not isinstance(enabled, bool):
                self._sensor_state.pop(name, None)
                continue
            previous = self._sensor_state.get(name)
            if enabled:
                self._sensor_state[name] = detected
            else:
                self._sensor_state.pop(name, None)
            if enabled and previous is True and detected is False:
                exact = isinstance(layer, int) and not isinstance(layer, bool) \
                    and isinstance(split, int) and not isinstance(split, bool) and split >= 0
                offset = file_position if type(file_position) is int and file_position >= 0 else None
                event = StopEvidence("sensor", self._job, str(name), at,
                                     layer if exact else None, split if exact else None,
                                     offset, "motion" if exact else "parser" if offset is not None else "estimate")
                self._events.append(event)
                new.append(event)
        for name in tuple(self._sensor_state):
            if name not in seen:
                self._sensor_state.pop(name, None)
        if len(self._events) > MAX_EVENTS:
            self._events = self._events[-MAX_EVENTS:]
        return tuple(new)

    def mark_manual(self, job_key, *, at, layer, split):
        self.bind(job_key)
        if self._job is None or isinstance(layer, bool) or isinstance(split, bool) \
                or not isinstance(layer, int) or not isinstance(split, int) \
                or layer < 0 or split < 0:
            return None
        try:
            at = float(at)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(at):
            return None
        event = StopEvidence("manual", self._job, "", at, layer, split, None, "estimate")
        self._events.append(event)
        if len(self._events) > MAX_EVENTS:
            self._events = self._events[-MAX_EVENTS:]
        return event

    def remove(self, job_key, row):
        if job_key is None or tuple(job_key) != self._job or not isinstance(row, Mapping):
            return False
        for index, event in enumerate(self._events):
            if event.document() == dict(row):
                del self._events[index]
                return True
        return False

    def restore(self, job_key, rows):
        """Merge bounded, validated saved evidence into this live print."""
        self.bind(job_key)
        if self._job is None or not isinstance(rows, list):
            return
        restored = []
        for row in rows[-MAX_EVENTS:]:
            if not isinstance(row, Mapping):
                continue
            kind, precision = row.get("kind"), row.get("precision")
            if kind not in {"sensor", "manual"} or precision not in {"motion", "parser", "estimate"}:
                continue
            sensor = row.get("sensor")
            if not isinstance(sensor, str) or len(sensor) > 128:
                continue
            try:
                at = float(row.get("observed_at"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(at) or at < 0:
                continue
            layer, split, position = (row.get("layer"), row.get("split"),
                                      row.get("file_position"))
            if layer is not None and (type(layer) is not int or layer < 0):
                continue
            if split is not None and (type(split) is not int or split < 0):
                continue
            if position is not None and (type(position) is not int or position < 0):
                continue
            if (layer is None) != (split is None):
                continue
            restored.append(StopEvidence(kind, self._job, sensor, at,
                                          layer, split, position, precision))
        key = lambda item: (item.kind, item.sensor, item.observed_at, item.layer,
                            item.split, item.file_position, item.precision)
        merged = {key(item): item for item in restored + self._events}
        self._events = sorted(merged.values(), key=lambda item: item.observed_at)[-MAX_EVENTS:]


def pause_owner(config, sensor):
    """Return 'klipper' or 'follower' only with explicit sensor configuration."""
    if not isinstance(config, Mapping) or not isinstance(sensor, str):
        return None
    section = config.get(sensor)
    if not isinstance(section, Mapping):
        return None
    raw = section.get("pause_on_runout")
    if raw is None:
        # Klipper's documented default for both switch and motion
        # sensors is True when the option is omitted.
        return "klipper"
    if raw is True or str(raw).strip().lower() in {"true", "1"}:
        return "klipper"
    if raw is False or str(raw).strip().lower() in {"false", "0"}:
        return "follower"
    return None
