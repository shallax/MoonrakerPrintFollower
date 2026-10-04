"""The suite scenario specs (TESTING.md 3, the full functional
surface). Each spec is a short composition of the step vocabulary the
gates established — the runner's suite mode executes one group per
boot with a simulator reset between scenarios. The coverage gate
(tests/test_coverage.py) proves every surface maps here.

This is the assembly point. One module per group, and the order is
SPELLED OUT below rather than discovered: a filesystem glob would
make the suite depend on directory order, and the runner executes a
group's scenarios in the order they are listed.
"""
from __future__ import annotations

from . import (configure, connection, console, files, motion, preview,
               printing, probe, real, settings, smoke, status, stress,
               temperatures, visual, webcams)

# The probe bodies the groups share, re-exported here because the
# driver resolves a step's template by name off this package and
# test_harness_specs compiles every ALL-CAPS str it exports. The
# re-export is enumerated, never a wildcard, and a pin in
# test_harness_specs holds this list equal to the probe module's own.
from .probe_source import (  # noqa: F401
    WINDOW_FLOOR_W, WINDOW_FLOOR_H, RECT_PROBE, CONTROLS_PROBE,
    MODEL_POWER_PROBE, POWER_STATE_PROBE, LANE_CENSUS_PROBE,
    LOAD_WINDOW_PROBE, CENSUS_PROBE, FM_POPUP_PROBE, FM_BUTTON_PROBE,
    SETTINGS_PROBE, PRESETS_PROBE, P1_PCT_PROBE, P_PAUSE_CLICK,
    P_PAUSE_SCHEDULED, P_ROW_PASSED, P_MISSED, SCROLL_CONTROLS,
    P_TOGGLE_STATE, P_POWER_FLIP, POWER_STATUS_PROBE, CAM_FRAMES, CAM_STARTED,
    CLICK_GESTURE, SCENE_PROBE, WHATS_NEW_CLEAR_CODE, VERDICT_SCAN,
    E_STOP_SEQUENCE, CARD_EXCLUSIVE_PROBE, CARD_GATE_PROBE, P_SLIDER_DRAG,
    P_SLIDER_CLICK, STRIP_PAUSE_READY, P_FOLLOW_READ, P_PAUSE_BLOCK_READ,
    P_ANCHOR_ETA_READ, P_ATTACH_EMIT, SCENE_RECT, CONFIGURE_DRAG_PROBE, CONFIGURE_GEOM_PROBE,
    CONFIGURE_CONTROLS_FIELD_PROBE, CONFIGURE_CROSSTALK_PROBE,
    CONFIGURE_DISMISS_PROBE, CONFIGURE_TRI_PROBE, CONFIGURE_FM_CLICK,
    CONFIGURE_FM_STATE, CONFIGURE_MUTUAL_PROBE, CONFIGURE_FM_OPEN,
    CONFIGURE_FM_OUTSIDE, CONFIGURE_FM_PROBE, P1_RENDER_PROBE,
    P1_FOLLOW_BUTTON, PENGUIN_MONITOR_READ, PENGUIN_PREVIEW_READ)
from .probe_source import _scratch  # noqa: F401

# The group order the suite is assembled in. Two groups appear twice:
# their last scenarios were written after the following block already
# existed, and the runner selects by group, so a group's own order is
# what it executes. Listing the groups once each keeps every group
# contiguous without changing any group's internal sequence.
GROUPS = (
    connection,
    status,
    temperatures,
    console,
    webcams,
    files,
    motion,
    printing,
    settings,
    stress,
    visual,
    preview,
    smoke,
    probe,
    real,
    configure,
)

SCENARIOS = [spec for group in GROUPS for spec in group.SCENARIOS]

__all__ = ["SCENARIOS", "GROUPS"]
