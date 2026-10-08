"""Validated visual rotors and bounded phase integration; no printer commands."""
from __future__ import annotations
import math
import re
import time
from collections.abc import Mapping
import numpy as np

MAX_ROTORS = 8
MAX_RPM = 30000.
FAN = re.compile(r'(?:fan|(?:fan_generic|heater_fan|controller_fan|temperature_fan) [^\x00-\x1f]{1,120})\Z')


def finite(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)): return None
    try: value = float(value)
    except OverflowError: return None
    return value if math.isfinite(value) and low <= value <= high else None


def rotors(value, mesh=None):
    result, seen = [], set()
    if not isinstance(value, (list, tuple)): return result
    for row in value[:MAX_ROTORS]:
        if not isinstance(row, Mapping): continue
        body = row.get('body')
        if type(body) is not int or not 0 <= body < 4096 or body in seen: continue
        if mesh is not None and body not in mesh.present_bodies: continue
        centre, axis = row.get('centre'), row.get('axis')
        if not isinstance(centre, (list, tuple)) or not isinstance(axis, (list, tuple)) or len(centre) != 3 or len(axis) != 3: continue
        centre = [finite(v, -10000, 10000) for v in centre]
        axis = [finite(v, -1, 1) for v in axis]
        if None in centre or None in axis: continue
        length = math.sqrt(sum(v*v for v in axis))
        rpm = finite(row.get('rpm', 3000.), 0, MAX_RPM)
        if length < .001 or rpm is None: continue
        fan = row.get('fan', '')
        if not isinstance(fan, str) or (fan and not FAN.fullmatch(fan)): continue
        direction = row.get('direction', 1)
        if type(direction) is not int or direction not in (-1, 1): continue
        result.append(dict(body=body, centre=centre, axis=[v/length for v in axis],
            rpm=rpm, direction=direction, fan=fan, blur=bool(row.get('blur', True))))
        seen.add(body)
    return result


def body_axis(mesh, body):
    points = mesh.triangles[mesh.body_ids == body].reshape(-1, 3)
    if not len(points): raise ValueError('Selected body has no triangles')
    record = mesh.bodies[body]
    if record.axis is not None: return list(record.centre), list(record.axis), True
    # A proposal must be confirmed in the preview. A thin bbox is not proof.
    low, high = points.min(axis=0), points.max(axis=0)
    axis = [0., 0., 0.]; axis[int(np.argmin(high-low))] = 1.
    return list(map(float, (low+high)/2)), axis, False


def speed(row, readings):
    if not row['fan']: return row['rpm'], 'Manual visual speed'
    reading = readings.get(row['fan'])
    if not isinstance(reading, Mapping) or not reading.get('available'): return 0., 'Fan unavailable'
    rpm = finite(reading.get('rpm'), 0, math.inf)
    if rpm is not None: return rpm, 'Measured RPM'
    power = finite(reading.get('speed'), 0, 1)
    return (power*row['rpm'], 'Estimated from power') if power is not None else (0., 'Fan unavailable')


def rotation(centre, axis, angle):
    axis = np.asarray(axis, dtype=float)
    x, y, z = axis
    skew = np.array(((0,-z,y),(z,0,-x),(-y,x,0)))
    basis = np.eye(3)*math.cos(angle)+(1-math.cos(angle))*np.outer(axis,axis)+math.sin(angle)*skew
    result = np.eye(4); result[:3,:3] = basis
    result[:3,3] = np.asarray(centre)-basis @ centre
    return result


class RotorMotion:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock; self.last = None; self.phases = {}; self.rows = []; self.readings = {}

    def configure(self, rows, readings):
        self.sample()  # Advance using the OLD speed before replacing telemetry.
        self.rows, self.readings = rows, readings
        self.phases = {row['body']: self.phases.get(row['body'], 0.) for row in rows}

    @property
    def moving(self): return any(speed(row, self.readings)[0] > 0 for row in self.rows)

    def sample(self):
        now = self.clock()
        elapsed = 0. if self.last is None else max(0., min(.1, now-self.last))
        self.last = now
        result = []
        for row in self.rows:
            rpm, label = speed(row, self.readings)
            angular = row['direction']*(rpm/60)*math.tau
            phase = (self.phases.get(row['body'],0.)+elapsed*angular) % math.tau
            self.phases[row['body']] = phase
            # At most a sixth revolution in one 1/120-second exposure.
            blur = min(math.pi/3, abs(angular)/120) if row['blur'] else 0.
            result.append((row, phase, blur, label))
        return result
