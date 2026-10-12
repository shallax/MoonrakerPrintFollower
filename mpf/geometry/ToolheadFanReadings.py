"""Read-only raw fan observations; full samples replace, deltas merge."""
from __future__ import annotations
from collections.abc import Mapping
import math
from .ToolheadRotors import FAN, finite


class FanReadings:
    def __init__(self): self.rows = {}

    def accept(self, incoming, stamp, *, full=False):
        if full: self.rows = {}
        stamp = finite(stamp, 0, math.inf)
        if stamp is None or stamp <= 0 or not isinstance(incoming, Mapping): return
        for name, value in incoming.items():
            if not isinstance(name, str) or not FAN.fullmatch(name) or not isinstance(value, Mapping): continue
            if name not in self.rows and len(self.rows) >= 128: continue
            previous = self.rows.get(name, {})
            state = {} if full else dict(previous)
            for key, upper in (('rpm', math.inf), ('speed', 1.)):
                if full or key in value: state[key] = finite(value.get(key), 0, upper)
            state['stamp'] = stamp
            self.rows[name] = state

    def retain(self, names):
        self.rows = {name: row for name, row in self.rows.items() if name in names}

    def values(self, now, *, connected, streaming=False):
        return {name: dict(rpm=value.get('rpm'), speed=value.get('speed'),
            available=bool(connected and value['stamp'] > 0 and
                (streaming or 0 <= now-value['stamp'] <= 10.))) for name, value in self.rows.items()}
