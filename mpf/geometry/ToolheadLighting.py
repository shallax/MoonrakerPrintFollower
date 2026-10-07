"""Bounded, model-local surface lights shared by settings and render adapters."""
from __future__ import annotations

import math
import re

MAX_LIGHTS = 8


def validated_lights(value):
    if not isinstance(value, list): return []
    result = []
    for row in value[:MAX_LIGHTS]:
        if not isinstance(row, dict): continue
        try:
            point = tuple(float(v) for v in row['position'])
            direction = tuple(float(v) for v in row['direction'])
            brightness = float(row.get('brightness', 1))
            reach = float(row.get('range', 80))
            colour = str(row.get('colour', '#ffffff')).lower()
            surface = int(row.get('surface', 0))
            if not 0 <= surface <= 1_000_000: continue
            if len(point) != 3 or len(direction) != 3: continue
            if not all(math.isfinite(v) and abs(v) <= 10000 for v in point): continue
            if not all(math.isfinite(v) for v in direction): continue
            length = math.sqrt(sum(v*v for v in direction))
            if length < 1e-9 or not math.isfinite(length): continue
            if not math.isfinite(brightness) or not math.isfinite(reach): continue
            if not re.fullmatch(r'#[0-9a-f]{6}', colour): continue
            result.append(dict(position=list(point), direction=[v/length for v in direction],
                surface=surface, paint=bool(row.get('paint', True)), colour=colour,
                brightness=max(0, min(5, brightness)), range=max(1, min(500, reach))))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return result


def light_values(row):
    # Offset into free space, never into the selected surface. The shader's
    # hemisphere gate rejects every ray pointing behind this outward normal.
    position = tuple(p + d*.15 for p, d in zip(row['position'], row['direction'], strict=True))
    colour = tuple(int(row['colour'][offset:offset+2], 16)/255 * row['brightness'] for offset in (1, 3, 5))
    return position, tuple(row['direction']), colour, row['range']
