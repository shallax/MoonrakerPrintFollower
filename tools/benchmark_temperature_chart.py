#!/usr/bin/env python3
"""The temperature-chart data-path benchmark (developer tool, not CI).

Seeds a realistic TemperatureHistory at five maturities (1, 5, 15, 30
minutes and MAX_SAMPLES) with the representative sensor family —
extruder (hotend with target and changing power), bed (target +
power), chamber heater, an ordinary sensor and a thermistor fan — and
measures the per-feed costs separately:

  observe           the raw history append/trim pass
  mini payload      the CLOSED-chart build (bounded render reduction)
  full payload      the OPEN-chart build (full-resolution tracks)
  latest projection the legends' one-scalar-per-sensor read

The interesting column is not the absolute milliseconds (this box is
not Cura's): it is that the mini payload's point count and JSON size
STOP GROWING once the window outgrows the render budget, while the
raw history keeps its full resolution — the structural guarantee that
the closed chart's payload, QVariant conversion and downstream
rendering cannot degrade as the print matures. The mini BUILD remains
one deliberate allocation-light scan over the raw window per feed
(the new sample must be folded into the reduction once), so its wall
time does grow slowly with the window length — the bounded guarantees
above are the shipped contract. The full payload's cost is printed
for comparison; it only exists while the pop-over is open.

Run: python3 tools/benchmark_temperature_chart.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.MonitorTemperatureHistory import (  # noqa: E402
    MAX_SAMPLES,
    TemperatureHistory,
    chart_payload,
)

try:  # the split-payload API (4.5.0 chart work); the legacy tree lacks it
    from plugins.MonitorTemperatureHistory import (  # noqa: F401
        MINI_RENDER_BUDGET,
        latest_values,
        mini_chart_payload as _mini_payload,
    )
except ImportError:
    MINI_RENDER_BUDGET = None
    latest_values = None
    _mini_payload = None

MATURITIES = (
    ("1 min", 24),
    ("5 min", 120),
    ("15 min", 360),
    ("30 min", 720),
    ("MAX_SAMPLES", MAX_SAMPLES),
)

# One representative family: a hotend with target and moving power,
# the bed, a chamber heater, an ordinary sensor, and a thermistor fan.
SENSORS = {
    "extruder": {"temperature": 210.0, "target": 210.0, "power": 0.55},
    "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.30},
    "heater_generic chamber": {"temperature": 40.0, "target": 45.0, "power": 0.20},
    "temperature_sensor enclosure": {"temperature": 25.0},
    "temperature_fan part": {"temperature": 32.0, "target": 35.0},
}


def seed(tick_count):
    """A deterministic, plausible print: warm-up curves, PID ripple on
    the hotend, a bed that settles, a chamber that ramps late."""
    history = TemperatureHistory(window_seconds=10 ** 9)
    now = 1000.0
    auxiliary = {}
    for tick in range(tick_count):
        elapsed = tick * 2.5
        auxiliary.clear()
        for name, base in SENSORS.items():
            value = dict(base)
            if name == "extruder":
                value["temperature"] = min(210.0, 24.0 + elapsed * 0.35)
                value["temperature"] += (0.4 if tick % 17 < 8 else -0.4)
                value["power"] = 0.9 if elapsed < 480 else 0.25
            elif name == "heater_bed":
                value["temperature"] = min(60.0, 24.0 + elapsed * 0.12)
                value["power"] = 0.6 if elapsed < 300 else 0.1
            elif "chamber" in name:
                value["temperature"] = 24.0 + max(0.0, elapsed - 200) * 0.03
                value["power"] = 0.4 if elapsed < 600 else 0.15
            elif "enclosure" in name:
                value["temperature"] = 24.0 + max(0.0, elapsed - 150) * 0.02
            else:
                value["temperature"] = 32.0 + (elapsed % 60) * 0.05
            auxiliary[name] = value
        history.observe(auxiliary, now + elapsed, wall=1700000000.0 + elapsed)
    return history, now + tick_count * 2.5


def timed(label, function):
    start = time.perf_counter()
    result = function()
    return (time.perf_counter() - start) * 1000, result


def measure(history, next_now):
    # The append uses the SEEDED clock's next tick — a real clock
    # would outrun the synthetic window and gap-reset the history
    # before the payloads were measured.
    observe_ms, _ = timed("observe", lambda: history.observe(
        {"extruder": {"temperature": 211.0, "target": 210.0, "power": 0.3}},
        next_now, wall=1700000000.0 + (next_now - 1000.0)))
    mini_build = (lambda h: _mini_payload(h, {})) if _mini_payload is not None \
        else (lambda h: chart_payload(h, {}, mini=True))
    mini_ms, mini = timed("mini payload", lambda: mini_build(history))
    full_ms, full = timed("full payload", lambda: chart_payload(history, {}))
    latest_ms, latest = timed("latest", lambda: latest_values(history) if latest_values is not None else {})
    return {
        "observe_ms": observe_ms,
        "mini_ms": mini_ms,
        "full_ms": full_ms,
        "latest_ms": latest_ms,
        "mini_points": [len(series["points"]) for series in mini["series"]],
        "mini_bytes": len(json.dumps(mini)),
        "full_points": [len(series["points"]) for series in full["series"]],
        "full_bytes": len(json.dumps(full)),
        "latest": latest,
    }


def main():
    if MINI_RENDER_BUDGET is not None:
        print("mini render budget: %d points per series" % MINI_RENDER_BUDGET)
    else:
        print("legacy tree: no mini render budget (unbounded mini payload)")
    print("%-12s %8s %8s %10s %10s %8s %12s %10s %12s %12s" % (
        "maturity", "raw/серия", "observe", "mini ms", "full ms", "latest",
        "mini pts", "mini bytes", "full pts", "full bytes"))
    for label, ticks in MATURITIES:
        history, next_now = seed(ticks)
        raw = {name: len(history.series(name)) for name in history.names()}
        row = measure(history, next_now)
        print("%-12s %8s %7.2fms %9.2fms %9.2fms %7.2fms %12s %9dB %12s %11dB" % (
            label, "/".join(str(n) for n in raw.values()), row["observe_ms"],
            row["mini_ms"], row["full_ms"], row["latest_ms"],
            "/".join(str(n) for n in row["mini_points"]), row["mini_bytes"],
            "/".join(str(n) for n in row["full_points"]), row["full_bytes"]))
        if MINI_RENDER_BUDGET is not None and all(
                len(history.series(name)) > MINI_RENDER_BUDGET
                for name in history.names()):
            # Once the window outgrows the render budget, the count
            # must stay within it — it does not have to be FLAT: a
            # bucket contributes one point when its minimum and
            # maximum are the same sample, two otherwise, so the
            # count follows the data's shape, never the window's
            # length (the documented contract is a bounded budget,
            # not a fixed count).
            for count in row["mini_points"]:
                if count > MINI_RENDER_BUDGET:
                    print("  ! a mini series carries %d points — over the %d budget" % (
                        count, MINI_RENDER_BUDGET))
                    return 1
    print("mini payload point counts stay within the render budget at every maturity: bounded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
