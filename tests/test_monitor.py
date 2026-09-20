"""Monitor domain: model/QML contracts, formatting and real-Qt tuning/camera.

The Monitor owns one Qt model, deep snapshots, debounced tuning and camera
selection. Contract tests assert the source keeps those boundaries; the Qt
tests drive the real production components through the shared harness.
"""
from __future__ import annotations

import time

import ast
from dataclasses import replace
import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from plugins.MonitorFormatting import (
    core_values,
    estimate_remaining,
    file_row_payload,
    height_readout,
    infer_macro_parameters,
    layer_readout,
    parse_bed_mesh,
    parse_mcu_stats,
    preview_block,
    preview_temperature_pair,
    print_job_caption,
)
from plugins.MonitorPermissions import Observation
from plugins.PrintState import LayerResolver, PhysicalLayer
from qt_runtime_support import QT_AVAILABLE, ROOT, ScriptedSocket, ScriptedTransport, runtime

PLUGINS = ROOT / "plugins"
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
DATA = (PLUGINS / "MonitorData.py").read_text(encoding="utf-8")
CONTROLS = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
FORMATTING = (PLUGINS / "MonitorFormatting.py").read_text(encoding="utf-8")
POLICY = (PLUGINS / "MonitorPermissions.py").read_text(encoding="utf-8")
TYPED = "\n".join((PLUGINS / name).read_text(encoding="utf-8") for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text(encoding="utf-8")
CAMERA_PANE_QML = (PLUGINS / "CameraPane.qml").read_text(encoding="utf-8")
PRINT_SECTION_QML = (PLUGINS / "PrintSection.qml").read_text(encoding="utf-8")
SETUP_SECTION_QML = (PLUGINS / "SetupSection.qml").read_text(encoding="utf-8")
TOOLHEAD_SECTION_QML = (PLUGINS / "ToolheadSection.qml").read_text(encoding="utf-8")
PROFILES_SECTION_QML = (PLUGINS / "ProfilesSection.qml").read_text(encoding="utf-8")
TUNING_SECTION_QML = (PLUGINS / "TuningSection.qml").read_text(encoding="utf-8")
FANS_SECTION_QML = (PLUGINS / "FansSection.qml").read_text(encoding="utf-8")
LEDS_SECTION_QML = (PLUGINS / "LedsSection.qml").read_text(encoding="utf-8")
PWM_SECTION_QML = (PLUGINS / "PwmSection.qml").read_text(encoding="utf-8")
POWER_SECTION_QML = (PLUGINS / "PowerSection.qml").read_text(encoding="utf-8")
SYSTEM_SECTION_QML = (PLUGINS / "SystemSection.qml").read_text(encoding="utf-8")
SAVE_SECTION_QML = (PLUGINS / "SaveSection.qml").read_text(encoding="utf-8")
FILE_MANAGER_SECTION_QML = (PLUGINS / "FileManagerSection.qml").read_text(encoding="utf-8")
MESH_SECTION_QML = (PLUGINS / "MeshSection.qml").read_text(encoding="utf-8")
TEMP_HISTORY_SECTION_QML = (PLUGINS / "TempHistorySection.qml").read_text(encoding="utf-8")
FANS_INFO_SECTION_QML = (PLUGINS / "FansInfoSection.qml").read_text(encoding="utf-8")
FILAMENT_SECTION_QML = (PLUGINS / "FilamentSection.qml").read_text(encoding="utf-8")
OBJECTS_SECTION_QML = (PLUGINS / "ObjectsSection.qml").read_text(encoding="utf-8")
TEMPS_SECTION_QML = (PLUGINS / "TempsSection.qml").read_text(encoding="utf-8")
SYSTEM_INFO_SECTION_QML = (PLUGINS / "SystemInfoSection.qml").read_text(encoding="utf-8")
MCUS_SECTION_QML = (PLUGINS / "McusSection.qml").read_text(encoding="utf-8")
JOB_SECTION_QML = (PLUGINS / "JobSection.qml").read_text(encoding="utf-8")
MACROS_SECTION_QML = (PLUGINS / "MacrosSection.qml").read_text(encoding="utf-8")
PREVIEW_CONTROLS_QML = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
BED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text(encoding="utf-8")
BED_MESH_MAP_QML = (PLUGINS / "BedMeshMap.qml").read_text(encoding="utf-8")
POPOVER_QML = (PLUGINS / "MonitorPopOver.qml").read_text(encoding="utf-8")
TEMP_CHART_QML = (PLUGINS / "TemperatureChart.qml").read_text(encoding="utf-8")
FILE_MANAGER_QML = (PLUGINS / "FileManager.qml").read_text(encoding="utf-8")
QMLDIR = (PLUGINS / "qmldir").read_text(encoding="utf-8")
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
CAPTURE_HARNESS = (ROOT / "tools" / "capture_monitor.py").read_text(encoding="utf-8")

# The 23 section ids are the persistence keys (INSTRUCTIONS: the stored
# map only records touched sections; unknown keys default to expanded).
# 22 exist as sectionId: literals across the panes; the console pane's
# id is not a sectionId: property — it is pinned separately.
SECTION_IDS = {
    # Controls pane
    "print", "setup", "toolhead", "macros", "profiles", "tuning",
    "fans", "leds", "pwm", "power", "system", "save",
    # Information and Printer status panes
    "meshmap", "job", "temps", "fansinfo", "filament", "objects",
    "systeminfo", "mcus", "temphistory",
    # The Monitor's own surface
    "console", "fileManager",
}


class MonitorModelContractTests(unittest.TestCase):
    def test_single_qt_model_exposes_dashboard_features(self):
        for token in ("monitorEta", "monitorFinish", "temperatureItems", "fanItems", "filamentSensorItems",
            "excludeObjectItems", "powerDevices", "pausePrint", "resumePrint", "cancelPrint", "excludeObject",
            "setPowerDevice", "hostLoad", "memoryAvailable", "cpuTemperature", "klipperVersion", "moonrakerVersion",
            "mcuSummary", "macroNames", "runMacro", "temperaturePresetNames", "applyTemperaturePreset",
            "homeAll", "runQuadGantryLevel", "calibrateBedMesh", "macroParameterDefinitions", "temperaturePresetItems",
            "setSpeedFactor", "setFlowFactor", "adjustZOffset", "clearZOffset", "setFanSpeed", "setLedBrightness",
            "speedFactorPercent", "flowFactorPercent", "fanControlItems", "ledItems", "zOffsetText", "canSaveConfig"):
            self.assertIn(token, MONITOR_MODEL)
        self.assertIn("class MoonrakerMonitorModel(PrinterOutputModel)", MONITOR_MODEL)
        self.assertNotIn("_BaseMoonrakerMonitorModel", MONITOR_MODEL)

    def test_section_content_insets_pair_left_and_right(self):
        # The 4.6.0 right inset (the author's item): every section
        # content column with the left inset carries the SAME
        # expression on the right — the pair pin, never one side.
        left = 'Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2'
        right = 'Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2'
        carried = []
        for path in sorted(PLUGINS.glob("*.qml")):
            text = path.read_text(encoding="utf-8")
            if left in text:
                carried.append(path.name)
                self.assertIn(right, text, path.name)

    def test_toolhead_control_surface(self):
        policy = (PLUGINS / "ToolheadPolicy.py").read_text(encoding="utf-8")
        for token in ("G91", "G28", "M18", "jog_gate", "push_op", "JogOp",
                      "JOG_DISTANCE_DEFAULT", "EXTRUDE_SPEEDS_MM_PER_MIN", "extrude_distance_ok"):
            self.assertIn(token, policy)
        # Motion scripts have exactly one owner: MonitorControls gains none.
        self.assertNotIn("G91", CONTROLS)
        self.assertNotIn("M18", CONTROLS)
        for token in ("jogEnabled", "jogDistance", "extrudeDistance", "extrudeSpeed", "homedAxes",
                      "positionMode", "jogStatus", "toolheadChanged", "def jog(",
                      "def setJogDistance(", "def setExtrudeDistance(", "def setExtrudeSpeed(",
                      "def home(", "def motorsOff(", "def extrude(", "def heatersOff(",
                      "def centerToolhead(", "def zToZero("):
            self.assertIn(token, MONITOR_MODEL)
        # The toolhead block rides its component (4.3.0) — the pins
        # follow it there.
        for token in ("id: toolheadSection", 'title: "Toolhead"', 'jog("x", -1)', 'jog("z", 1)',
                      "setJogDistance(", 'home("x")', 'home("y")', 'home("z")', '"Motors off"',
                      '"Extrude"', '"Retract"', "setExtrudeDistance(", "setExtrudeSpeed(",
                      'text: "↑ Y"', 'text: "← X"', 'text: "→ X"', 'text: "↓ Y"',
                      'text: "↑ Z"', 'text: "↓ Z"', 'text: "Centre toolhead"', 'text: "Z to 0"',
                      "root.printerModel.monitorPosition"):
            self.assertIn(token, TOOLHEAD_SECTION_QML)
        for token in ('"Cooldown"', "heatersOff"):
            self.assertIn(token, PROFILES_SECTION_QML)
        # The safety clause lives in the policy's copy (4.2.0): the QML
        # reads the published caption, never builds the sentence.
        self.assertIn("Toolhead moves are disabled during a print", POLICY)
        # The six directional buttons carry no +/- signs (the arrows are the
        # direction) and use the PreviewSecondaryButton idiom: Cura's
        # native button underneath (hover/tooltip), a centred theme-coloured
        # label on top — Cura's own label does not vertically centre.
        # Home-all lives in the Setup section only: the toolhead section
        # keeps per-axis home buttons, so no duplicate home-all controls.
        self.assertNotIn('home("")', TOOLHEAD_SECTION_QML)
        self.assertEqual(TOOLHEAD_SECTION_QML.count("PreviewSecondaryButton"), 6)
        self.assertNotIn("contentItem", TOOLHEAD_SECTION_QML)
        # The Z-offset nudges carry direction glyphs, up row first, and no
        # +/- signs: the arrows carry the direction.
        self.assertIn('"↓ " + Math.abs(modelData)', TUNING_SECTION_QML)
        self.assertIn('"↑ " + modelData', TUNING_SECTION_QML)
        self.assertLess(TUNING_SECTION_QML.index("model: [0.005"), TUNING_SECTION_QML.index("model: [-0.005"))
        # The toolhead block is gated by jogEnabled alone, never actionBusy:
        # taps must keep working while the queue drains. The block rides
        # its component (4.3.0) — the pins follow it there.
        self.assertIn("jogEnabled", TOOLHEAD_SECTION_QML)
        self.assertNotIn("actionBusy", TOOLHEAD_SECTION_QML)
        # The compass is a 3×3 grid (9 cells) with the empty centre: the
        # four arrows must appear in north-west-east-south order so the
        # south button sits under north, never under west.
        grid = TOOLHEAD_SECTION_QML[TOOLHEAD_SECTION_QML.index('text: "↑ Y"'):TOOLHEAD_SECTION_QML.index('text: "↓ Y"') + len('text: "↓ Y"')]
        positions = [grid.index(token) for token in ('text: "↑ Y"', 'text: "← X"', 'text: "→ X"', 'text: "↓ Y"')]
        self.assertEqual(positions, sorted(positions))
        compass = TOOLHEAD_SECTION_QML[TOOLHEAD_SECTION_QML.index('columns: 3'):TOOLHEAD_SECTION_QML.index('ColumnLayout {', TOOLHEAD_SECTION_QML.index('text: "↑ Y"'))]
        self.assertEqual(compass.count('PreviewSecondaryButton {'), 4)
        self.assertEqual(compass.count('Item {'), 5)

    def test_same_dashboard_chain_and_power_lock_explanation(self):
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn('Qt.createComponent("MoonrakerMonitorDashboard.qml"', BED_MESH_QML)  # the shell's async load
        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)
        self.assertIn("Power control is locked by Moonraker while this print is active.", POWER_SECTION_QML)

    def test_output_plugin_selects_the_same_dashboard_through_one_model(self):
        self.assertIn("from .MoonrakerMonitorModel import MoonrakerMonitorModel", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn('Qt.createComponent("MoonrakerMonitorDashboard.qml"', BED_MESH_QML)

    def test_setup_and_save_commands_have_one_policy_owner(self):
        for command in ("G28", "QUAD_GANTRY_LEVEL", "BED_MESH_CALIBRATE", "SAVE_CONFIG", "SET_GCODE_OFFSET", "SET_FAN_SPEED", "SET_LED"):
            self.assertIn(command, CONTROLS)
        self.assertIn("self._commands.setup_allowed", CONTROLS)
        self.assertIn("configfile.get(\"save_config_pending\")", CONTROLS)

    def test_emergency_stop_requires_two_clicks_and_a_held_third_press(self):
        source = (PLUGINS / "MonitorCommands.py").read_text(encoding="utf-8")
        self.assertIn("HOLD_MS = 600", source)
        self.assertIn("self._reset_timer.setInterval(1000)", source)
        self.assertIn('"printer/emergency_stop"', source)
        self.assertIn("def emergency_hold_started", source)
        self.assertIn("def emergency_hold_released", source)
        # Firing the stop clears every pending item and releases busy.
        self.assertIn("emergencyStopped.emit()", source)
        self.assertIn("self.reset()", source)
        self.assertIn("emergencyButton.clicks +", DASHBOARD_QML)
        self.assertIn('"EMERGENCY STOP — press and hold to fire"', DASHBOARD_QML)
        self.assertIn("EMERGENCY STOP", DASHBOARD_QML)
        self.assertNotIn("Emergency stop?", DASHBOARD_QML)

    def test_section_ids_are_pinned_as_the_persistence_keys(self):
        # The 22 sectionId: literals are pinned as an exact set and the
        # console pane — whose id is not a sectionId: property — by its
        # expansion reference: a renamed id or an unlisted section must
        # not slip through silently (an unknown key defaults to expanded,
        # so the pin is the only guard on the persistence vocabulary).
        # The section-id literals ride their components (4.3.0): the
        # extraction scans the hosts AND every extracted section file.
        literals = set(re.findall(r'sectionId: "([^"]+)"', DASHBOARD_QML + MONITOR_QML + PRINT_SECTION_QML + SETUP_SECTION_QML + TOOLHEAD_SECTION_QML + MACROS_SECTION_QML + PROFILES_SECTION_QML + TUNING_SECTION_QML + FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML + POWER_SECTION_QML + SYSTEM_SECTION_QML + SAVE_SECTION_QML + FILE_MANAGER_SECTION_QML + MESH_SECTION_QML + TEMP_HISTORY_SECTION_QML + FANS_INFO_SECTION_QML + FILAMENT_SECTION_QML + OBJECTS_SECTION_QML + TEMPS_SECTION_QML + SYSTEM_INFO_SECTION_QML + MCUS_SECTION_QML + JOB_SECTION_QML))
        self.assertEqual(literals, SECTION_IDS - {"console"})
        self.assertEqual(len(SECTION_IDS), 23)
        self.assertIn('sectionExpandedMap["console"]', MONITOR_QML)
        # The extraction's header contract (the re-reviews' zero-width
        # catch, probe-verified): every section component's root is a
        # ColumnLayout (a plain Column counts invisible children in
        # its implicit height — a collapsed section kept its hidden
        # content in the pane's scroll length), and the header rides
        # Layout.fillWidth — a width: parent.width binding inside the
        # layout root breaks and renders 0 px, losing the click
        # target entirely.
        for section_qml in (PRINT_SECTION_QML, SETUP_SECTION_QML, TOOLHEAD_SECTION_QML,
                            MACROS_SECTION_QML, PROFILES_SECTION_QML, TUNING_SECTION_QML,
                            FANS_SECTION_QML, LEDS_SECTION_QML, PWM_SECTION_QML,
                            POWER_SECTION_QML, SYSTEM_SECTION_QML, SAVE_SECTION_QML,
                            FILE_MANAGER_SECTION_QML, MESH_SECTION_QML,
                            TEMP_HISTORY_SECTION_QML, FANS_INFO_SECTION_QML,
                            FILAMENT_SECTION_QML, OBJECTS_SECTION_QML, TEMPS_SECTION_QML,
                            SYSTEM_INFO_SECTION_QML, MCUS_SECTION_QML, JOB_SECTION_QML):
            self.assertIn("ColumnLayout {\n    id: root\n    spacing: 0", section_qml)
            header = section_qml[section_qml.index("CollapsibleSectionHeader {"):
                                 section_qml.index("CollapsibleSectionHeader {") + 400]
            self.assertIn("Layout.fillWidth: true", header)
            self.assertNotIn("width: parent.width", header)
        # The system/mcu sections sit in the STATUS PANE, not inside
        # the chart pop-over's legend repeater (the adversarial
        # critic's misplaced-insertion catch). Objects moved to the
        # controls pane in 4.6.0 — its own ordering pin lives with
        # the dashboard pins below.
        system_at = MONITOR_QML.index("SystemInfoSection {")
        mcus_at = MONITOR_QML.index("McusSection {")
        self.assertLess(system_at, mcus_at)
        pane_close = MONITOR_QML.index("                    }\n                }\n", system_at)
        self.assertLess(mcus_at, pane_close)
        # The moved readout leads the controls pane's print section
        # in the DASHBOARD (the 4.6.0 placement).
        objects_at = DASHBOARD_QML.index("ObjectsSection {")
        print_at = DASHBOARD_QML.index("PrintSection {")
        self.assertLess(objects_at, print_at)
        # The mesh section's refresh rides an accessor — the monitor's
        # handler calls it through the instantiation id, never the
        # component's own id (the dangling-id fix).
        self.assertIn("function refreshMap()", MESH_SECTION_QML)
        self.assertIn("meshSection.refreshMap()", MONITOR_QML)

    def test_the_narrow_window_rule_hinges_on_the_camera_pane(self):
        # The frozen contract (INSTRUCTIONS.md, "Standing UI rules"): the
        # narrow-window rule reads the WEBCAM pane's own width — never the
        # stage's — and refuses an expansion the camera could not survive.
        # Whitespace is flattened before matching: qmlformat owns the
        # wrapping, the pins own the arithmetic.
        flat = re.sub(r"\s+", " ", MONITOR_QML)
        self.assertIn("The narrow-window collapse/lock contract — see INSTRUCTIONS.md", flat)
        self.assertIn("readonly property real cameraViewportWidth: cameraPane.viewportWidth", flat)
        squeeze = re.search(
            r'webcamSqueezed: ([A-Za-z0-9_.]+) > 0 && \1 < ([0-9]+) \* screenScaleFactor',
            flat,
        )
        self.assertIsNotNone(squeeze, "the squeeze threshold changed shape")
        self.assertEqual((squeeze.group(1), int(squeeze.group(2))),
                         ("root.cameraViewportWidth", 220),
                         "the squeeze must read the camera at its comfort minimum")
        # The expansion costs: each pane's expanded width less the
        # collapsed strip it replaces — the same strip on both panes.
        costs = re.findall(
            r'readonly property real (info|status)ExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor',
            flat,
        )
        self.assertEqual([(name, int(wide) - int(strip)) for name, wide, strip in costs],
                         [("info", 226), ("status", 366)],
                         "an expansion cost changed")
        self.assertEqual(len({strip for _, _, strip in costs}), 1,
                         "the two panes must share the collapsed strip's width")
        # The forward check: the pane's own cost against the comfort
        # minimum, on the camera as it stands.
        for pane, cost in (("info", "infoExpandCost"), ("status", "statusExpandCost")):
            forward = re.search(
                r'readonly property bool %sExpandBlocked: root\.cameraViewportWidth - root\.%s '
                r'< ([0-9]+) \* screenScaleFactor' % (pane, cost),
                flat,
            )
            self.assertIsNotNone(forward, "the %s forward check changed shape" % pane)
            self.assertEqual(int(forward.group(1)), 220,
                             "the %s forward check left the comfort minimum" % pane)
        # The open-width measures: the camera seen with the pane's own
        # auto fold credited back out, so no decision depends on how many
        # layout passes the fold needed — and the release is the forward
        # check read backwards.
        for pane, flag in (("info", "infoAutoCollapsed"), ("status", "statusAutoCollapsed")):
            measure = re.search(
                r'readonly property real %sOpenWidth: root\.cameraViewportWidth - \(root\.%s && '
                r'!root\.%sPersistedCollapsed \? root\.%sExpandCost : 0\)' % (pane, flag, pane, pane),
                flat,
            )
            self.assertIsNotNone(measure, "the %s open-width measure changed shape" % pane)
        # The fold-landed gate: the pane's own width is where the fold
        # LANDS, and where the camera is pinned at its floor it is the
        # only signal there is.
        self.assertIn(
            "readonly property bool infoFoldLanded: infoPanel.width < 200 * screenScaleFactor", flat)
        # The LIFO guard and the cascade gate: the information pane folds
        # under comfort and reclaims its room LAST — while the status
        # pane's fold stands it holds, because reopening it would spend
        # the room that fold is holding; the status pane folds only once
        # the information pane's fold has landed. The old
        # simultaneous-release form (both flags dropped in the same
        # evaluation) is pinned out: it landed the pair back on both
        # folded wherever one pane alone would have fit.
        self.assertIn(
            "root.infoAutoCollapsed = infoOpen < comfort || statusWasFolded;",
            flat,
        )
        self.assertNotIn("statusWasFolded && statusOpen < comfort", flat,
                         "the information pane releases with the status pane's fold again")
        self.assertIn(
            "root.statusAutoCollapsed = statusOpen < comfort && root.infoFoldLanded "
            "&& (infoWasCollapsed || statusWasFolded);", flat)
        # One evaluation, driven by the camera and by each pane's own
        # width, and every measure is read before either flag moves: the
        # cascade's order is the flags as they stand, never a value this
        # evaluation is about to write.
        self.assertIn("onCameraViewportWidthChanged: root.applyNarrowWindowRules()", flat)
        self.assertEqual(flat.count("onWidthChanged: root.applyNarrowWindowRules()"), 3,
                         "the rule must run on the stage and on both panes' own widths")
        self.assertLess(flat.index("var statusOpen = root.statusOpenWidth"),
                        flat.index("root.infoAutoCollapsed = infoOpen"),
                        "the measures must be read before either flag moves")
        self.assertLess(flat.index("root.infoAutoCollapsed = infoOpen"),
                        flat.index("root.statusAutoCollapsed = statusOpen"),
                        "the cascade must fold the information pane first")
        # The retired latch: the stage-width constants and the single
        # camera measure they shared are gone, and so is the squeeze-edge
        # handler the camera-hinged rule replaced.
        for retired in ("infoComfortWidth", "statusComfortWidth", "cameraOpenWidth",
                        "onWebcamSqueezedChanged"):
            self.assertNotIn(retired, flat, "%s outlived the camera-hinged rule" % retired)

    def test_the_costs_ride_the_panes_own_layout_widths(self):
        # The costs are derived, not free-standing: each is a pane's
        # expanded width less its collapsed strip, and the pane layout
        # carries both numbers. A width changed on either side has to be
        # reflected in the cost in the same pass, or the camera
        # arithmetic drifts from the layout it is deciding about — and
        # the status pane's cost must stay the larger one, or the fold
        # order (and with it the reclaim-last guard) is inverted.
        flat = re.sub(r"\s+", " ", MONITOR_QML)
        expanded = {}
        for pane in ("info", "status"):
            layout = re.search(
                r'objectName: "%sPanel".*?Layout\.preferredWidth: \(root\.%sCollapsed \? '
                r'%sCollapseButton\.width \+ 2 \* UM\.Theme\.getSize\("thin_margin"\)\.width : '
                r'([0-9]+) \* screenScaleFactor\).*?Layout\.minimumWidth: \(root\.%sCollapsed \? '
                r'%sCollapseButton\.width \+ 2 \* UM\.Theme\.getSize\("thin_margin"\)\.width : '
                r'([0-9]+) \* screenScaleFactor\)' % (pane, pane, pane, pane, pane),
                flat,
            )
            self.assertIsNotNone(layout, "the %s pane's layout widths changed shape" % pane)
            cost = re.search(
                r'readonly property real %sExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor'
                % pane, flat)
            self.assertIsNotNone(cost, "the %s expansion cost changed shape" % pane)
            self.assertEqual((int(cost.group(1)), int(cost.group(2))),
                             (int(layout.group(1)), 44),
                             "the %s cost stopped riding the pane's own widths" % pane)
            self.assertLess(int(layout.group(2)), int(layout.group(1)),
                            "the %s pane's minimum must stay under its expanded width" % pane)
            expanded[pane] = int(layout.group(1))
        self.assertGreater(expanded["status"], expanded["info"],
                           "the status pane must fold after the information pane")

    def test_every_expand_path_refuses_while_the_camera_has_no_room(self):
        # The narrow-window lock: a collapsed pane refuses on the fold's
        # own reasons (the auto collapse, a camera already under its
        # comfort) and on the forward check — all three named in the
        # expression — and every expand path carries the guard with the
        # tooltip that explains the refusal (the console's too-narrow
        # precedent).
        flat = re.sub(r"\s+", " ", MONITOR_QML)
        for lock, pane in (("infoExpandLocked", "info"), ("statusExpandLocked", "status")):
            lock_line = re.search(
                r'readonly property bool %s: root\.%sCollapsed && \(root\.%sAutoCollapsed \|\| '
                r'root\.webcamSqueezed \|\| root\.%sExpandBlocked\)' % (lock, pane, pane, pane),
                flat,
            )
            self.assertIsNotNone(lock_line, "the %s lock changed shape" % pane)
            self.assertGreaterEqual(flat.count("if (root.%s) {" % lock), 2,
                                    "the %s pane needs the guard on its strip and its toggle" % pane)
        self.assertIn("The window is too narrow — widen it to show the information.", flat)
        self.assertIn("The window is too narrow — widen it to show the printer status.", flat)
        # The dashboard's controls pane follows the same rule through the
        # loaded monitor document's camera: its own cost and forward
        # check, the same guard on both expand paths, and the same
        # widen-first message. It carries no auto fold of its own, so the
        # user's collapse is the only state the lock ever meets.
        dash = re.sub(r"\s+", " ", DASHBOARD_QML)
        self.assertIn("readonly property real cameraViewportWidth: baseMonitorLoader.item !== null ? "
                      "baseMonitorLoader.item.cameraViewportWidth : 0", dash)
        self.assertIn("property bool controlsCollapsed: root.printer != null ? "
                      "root.printer.controlsCollapsed : false", dash)
        controls_cost = re.search(
            r'readonly property real controlsExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor',
            dash,
        )
        self.assertIsNotNone(controls_cost, "the controls expansion cost changed shape")
        self.assertEqual(int(controls_cost.group(1)) - int(controls_cost.group(2)), 342,
                         "the controls expansion cost changed")
        self.assertIn("readonly property bool controlsExpandBlocked: root.cameraViewportWidth > 0 && "
                      "root.cameraViewportWidth - root.controlsExpandCost < 220 * screenScaleFactor", dash)
        self.assertIn("readonly property bool webcamSqueezed: root.cameraViewportWidth > 0 && "
                      "root.cameraViewportWidth < 220 * screenScaleFactor", dash)
        self.assertIn("readonly property bool controlsExpandLocked: root.controlsCollapsed && "
                      "(root.webcamSqueezed || root.controlsExpandBlocked)", dash)
        self.assertGreaterEqual(dash.count("if (root.controlsExpandLocked) {"), 2,
                                "the controls pane needs the guard on its strip and its toggle")
        self.assertIn("The window is too narrow — widen it to show the printer controls.", dash)

    def test_controls_live_in_the_collapsible_column_and_the_left_is_read_only(self):
        # The left panel carries no printer commands: only the camera list,
        # the read-outs and view configuration remain there.
        self.assertNotIn("root.printer.pausePrint", MONITOR_QML)
        self.assertNotIn("root.printer.setPowerDevice", MONITOR_QML)
        self.assertIn("root.printer.emergencyStopClick", DASHBOARD_QML)  # the one permitted command
        # The information pane sits left of the webcam with the mesh map.
        self.assertIn("id: infoPanel", MONITOR_QML)
        # The mesh section hosts the mini map; a click opens the shared
        # pop-over. The button is gone; the mini map's tooltip remains.
        self.assertIn('tooltipText: root.printerModel != null ? "Click for the full bed mesh map ("', MESH_SECTION_QML)
        self.assertNotIn('id: mapButton', MONITOR_QML)
        # Cura-style collapsible sections, persisted per section, sharing
        # the CollapsibleSectionHeader type across all three panes. The
        # sections ride their components (4.3.0) — the expansion map is
        # read by every one of them.
        self.assertIn("sectionExpandedMap", PRINT_SECTION_QML + SETUP_SECTION_QML + TOOLHEAD_SECTION_QML + MACROS_SECTION_QML + PROFILES_SECTION_QML + TUNING_SECTION_QML + FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML + POWER_SECTION_QML + SYSTEM_SECTION_QML + SAVE_SECTION_QML + FILE_MANAGER_SECTION_QML)
        self.assertIn("setSectionExpanded", MONITOR_MODEL)
        self.assertIn('sectionId: "toolhead"', TOOLHEAD_SECTION_QML)
        # Direct instantiations must ASSIGN the type's properties: the old
        # Loader syntax ('property string sectionId: ...') declares a local
        # property instead, which silently un-wires every header.
        self.assertNotIn("property string sectionId:", DASHBOARD_QML)
        self.assertNotIn("property string title:", DASHBOARD_QML)
        self.assertNotIn("property string sectionIcon:", DASHBOARD_QML)
        self.assertIn('sectionIcon: "Nozzle"', TOOLHEAD_SECTION_QML)
        self.assertIn('sectionIcon: "Printer"', PRINT_SECTION_QML)
        self.assertIn('sectionId: "meshmap"', MESH_SECTION_QML)
        self.assertIn('sectionId: "systeminfo"', SYSTEM_INFO_SECTION_QML)
        # Plugin-drawn glyphs feed the header through a url, and the
        # frontend launcher lives in the Printer status title row.
        self.assertIn('sectionIcon: "Fan"', FANS_INFO_SECTION_QML)
        self.assertIn('sectionIconUrl: Qt.resolvedUrl("Thermometer.svg")', TEMP_HISTORY_SECTION_QML)
        self.assertIn('Qt.resolvedUrl("Download.svg")', JOB_SECTION_QML)
        self.assertIn('sectionIconUrl: Qt.resolvedUrl("Power.svg")', POWER_SECTION_QML)
        # The Position row's axis-coloured cells (the 4.5.0 ruling):
        # three fixed cells in the axis tokens, no-wrap — the row
        # must never reflow per poll (the status stack's polish-loop
        # class), and the cells carry objectNames so a future
        # scenario can assert the colour mapping.
        for axis in ("X", "Y", "Z"):
            self.assertIn(f'objectName: "jobPositionCell{axis}"', JOB_SECTION_QML)
            self.assertIn(f"MoonrakerTheme.axis{axis}", JOB_SECTION_QML)
        self.assertEqual(JOB_SECTION_QML.count("wrapMode: Text.WordWrap"), 0)
        self.assertIn('text: "Open the Moonraker frontend."', MONITOR_QML)
        # The tooltip discipline (the live ruling): every tooltip is a
        # UM.ToolTip child in the Cura placement pattern (below the
        # control, arrow at its top-centre, hover-driven) — never the
        # native tooltip property or a TooltipArea over a control,
        # either of which can swallow a click when the pointer crosses
        # the popup.
        for path in PLUGINS.glob("*.qml"):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if line.lstrip().startswith("tooltip:"):
                    self.fail("%s carries a native tooltip property: %s"
                              % (path.name, line.strip()[:60]))
        for path in PLUGINS.glob("*.qml"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"onClicked[\s\S]{0,600}?UM\.TooltipArea",
                "%s holds a TooltipArea inside a clickable control" % path.name,
            )
        self.assertNotIn('text: "Open Moonraker frontend"', MONITOR_QML)
        # The section machinery (4.3.0): per-file counts PLUS the
        # totals — a moved section decrements one file and increments
        # another, and the totals catch a dropped section that a
        # per-file pin alone would read as "moved".
        self.assertEqual(DASHBOARD_QML.count("CollapsibleSectionHeader"), 0)
        # Every extracted section is a SIBLING instantiation in the
        # pane — a section nested inside another's instantiation is
        # valid QML and loads, but renders inside the wrong Column.
        self.assertIn("                        }\n                        SaveSection {", DASHBOARD_QML)
        self.assertEqual(PRINT_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(SETUP_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(TOOLHEAD_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(MACROS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(PROFILES_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(TUNING_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(FANS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(LEDS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(PWM_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(POWER_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(SYSTEM_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(SAVE_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(FILE_MANAGER_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(MESH_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(TEMP_HISTORY_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(FANS_INFO_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(FILAMENT_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(OBJECTS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(TEMPS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(SYSTEM_INFO_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(MCUS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(JOB_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(MONITOR_QML.count("CollapsibleSectionHeader"), 0)
        self.assertEqual(DASHBOARD_QML.count("CollapsibleSectionHeader")
                         + PRINT_SECTION_QML.count("CollapsibleSectionHeader")
                         + SETUP_SECTION_QML.count("CollapsibleSectionHeader")
                         + TOOLHEAD_SECTION_QML.count("CollapsibleSectionHeader")
                         + MACROS_SECTION_QML.count("CollapsibleSectionHeader")
                         + PROFILES_SECTION_QML.count("CollapsibleSectionHeader")
                         + TUNING_SECTION_QML.count("CollapsibleSectionHeader")
                         + FANS_SECTION_QML.count("CollapsibleSectionHeader")
                         + LEDS_SECTION_QML.count("CollapsibleSectionHeader")
                         + PWM_SECTION_QML.count("CollapsibleSectionHeader")
                         + POWER_SECTION_QML.count("CollapsibleSectionHeader")
                         + SYSTEM_SECTION_QML.count("CollapsibleSectionHeader")
                         + SAVE_SECTION_QML.count("CollapsibleSectionHeader")
                         + FILE_MANAGER_SECTION_QML.count("CollapsibleSectionHeader")
                         + MESH_SECTION_QML.count("CollapsibleSectionHeader")
                         + TEMP_HISTORY_SECTION_QML.count("CollapsibleSectionHeader")
                         + FANS_INFO_SECTION_QML.count("CollapsibleSectionHeader")
                         + FILAMENT_SECTION_QML.count("CollapsibleSectionHeader")
                         + OBJECTS_SECTION_QML.count("CollapsibleSectionHeader")
                         + TEMPS_SECTION_QML.count("CollapsibleSectionHeader")
                         + SYSTEM_INFO_SECTION_QML.count("CollapsibleSectionHeader")
                         + MCUS_SECTION_QML.count("CollapsibleSectionHeader")
                         + JOB_SECTION_QML.count("CollapsibleSectionHeader")
                         + MONITOR_QML.count("CollapsibleSectionHeader"), 22)
        self.assertEqual(DASHBOARD_QML.count('sectionIcon: "'), 0)
        self.assertEqual(PRINT_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(SETUP_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(TOOLHEAD_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(MACROS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(PROFILES_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(TUNING_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(FANS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(LEDS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(PWM_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(POWER_SECTION_QML.count('sectionIcon: "'), 0)  # Power uses the plugin glyph url
        self.assertEqual(SYSTEM_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(SAVE_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(FILE_MANAGER_SECTION_QML.count('sectionIcon: "'), 0)  # The plugin glyph url
        self.assertEqual(MESH_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(TEMP_HISTORY_SECTION_QML.count('sectionIcon: "'), 0)  # The plugin glyph url
        self.assertEqual(FANS_INFO_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(FILAMENT_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(OBJECTS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(TEMPS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(SYSTEM_INFO_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(MCUS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(JOB_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(MONITOR_QML.count('sectionIcon: "'), 0)
        self.assertEqual(DASHBOARD_QML.count('sectionIcon: "')
                         + PRINT_SECTION_QML.count('sectionIcon: "')
                         + SETUP_SECTION_QML.count('sectionIcon: "')
                         + TOOLHEAD_SECTION_QML.count('sectionIcon: "')
                         + MACROS_SECTION_QML.count('sectionIcon: "')
                         + PROFILES_SECTION_QML.count('sectionIcon: "')
                         + TUNING_SECTION_QML.count('sectionIcon: "')
                         + FANS_SECTION_QML.count('sectionIcon: "')
                         + LEDS_SECTION_QML.count('sectionIcon: "')
                         + PWM_SECTION_QML.count('sectionIcon: "')
                         + POWER_SECTION_QML.count('sectionIcon: "')
                         + SYSTEM_SECTION_QML.count('sectionIcon: "')
                         + SAVE_SECTION_QML.count('sectionIcon: "')
                         + MESH_SECTION_QML.count('sectionIcon: "')
                         + FANS_INFO_SECTION_QML.count('sectionIcon: "')
                         + FILAMENT_SECTION_QML.count('sectionIcon: "')
                         + OBJECTS_SECTION_QML.count('sectionIcon: "')
                         + TEMPS_SECTION_QML.count('sectionIcon: "')
                         + SYSTEM_INFO_SECTION_QML.count('sectionIcon: "')
                         + MCUS_SECTION_QML.count('sectionIcon: "')
                         + JOB_SECTION_QML.count('sectionIcon: "')
                         + MONITOR_QML.count('sectionIcon: "'), 19)
        # The File manager section (Snapshot 0) leads the controls pane
        # and opens the popup; it uses the plugin glyph, so the
        # sectionIcon: count is unchanged.
        self.assertIn('sectionId: "fileManager"', FILE_MANAGER_SECTION_QML)
        self.assertIn('text: "File manager"', FILE_MANAGER_SECTION_QML)
        self.assertIn("fileManagerOpen", DASHBOARD_QML)
        self.assertIn("FileManager 1.0 FileManager.qml", QMLDIR)
        # Opening the popup must trigger the walk (the Snapshot 1
        # live-test regression: the button flipped the flag but
        # nothing fetched, and the grid sat on "Loading files…").
        self.assertIn("onOpenChanged", FILE_MANAGER_QML)
        self.assertIn("openFileManager()", FILE_MANAGER_QML)
        # The live-test rulings: the 250 ms search settle,
        # the refresh button, the circled search clear, folders as a
        # strip (never in the metadata list).
        self.assertIn("interval: 250", FILE_MANAGER_QML)
        self.assertIn("refreshFileManager()", FILE_MANAGER_QML)
        self.assertIn('text: "⟳"', FILE_MANAGER_QML)
        self.assertIn("restoreMode: Binding.RestoreBinding", FILE_MANAGER_QML)
        self.assertIn("id: searchClear", FILE_MANAGER_QML)
        self.assertIn("activeDirectories", FILE_MANAGER_QML)
        self.assertIn('UM.Theme.getIcon("Folder")', FILE_MANAGER_QML)
        self.assertIn("filterOptionRow", FILE_MANAGER_QML)
        # Probe-proven engine traps: a Repeater with two bare
        # children keeps only the last as its delegate, and
        # Component ids must never be reached through an object
        # reference (a Loader's sourceComponent silently loads
        # nothing) — both were live reports.
        self.assertIn('text: " / "', FILE_MANAGER_QML)
        # The filter dropdowns (the live rulings): radios
        # for Modified/Print time (single-value model semantics —
        # the engine's exclusive group only unchecks visually), no
        # auto-dismiss on selection (Qt menus close on item
        # activation regardless of closePolicy, so the dropdowns are
        # Popups — probe-proven), the dropdown below its button, the
        # faces toggled by visibility (a Loader swap would destroy
        # the open dropdown), and the up-directory chip in the
        # folder strip.
        self.assertIn("setFilterValue", FILE_MANAGER_QML)
        self.assertIn("modelData.radio", FILE_MANAGER_QML)
        # The toggle dropdowns close on RELEASE outside (the press
        # still opens state capture — a press-outside policy closed
        # before the opener could record it and every dismissal click
        # re-opened the popup); the dialogs close on Escape only.
        self.assertIn("closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside", FILE_MANAGER_QML)
        self.assertIn("closePolicy: Popup.CloseOnEscape\n", FILE_MANAGER_QML)
        self.assertIn("y: parent.height", FILE_MANAGER_QML)
        self.assertIn("visible: !root.filterActive(\"slicer\")", FILE_MANAGER_QML)
        self.assertIn('text: ".."', FILE_MANAGER_QML)
        self.assertIn('text: "<root>"', FILE_MANAGER_QML)
        # Snapshot 2 live refinements: double-click-to-print, the
        # themed confirmation background.
        self.assertIn("onDoubleClicked", FILE_MANAGER_QML)
        self.assertIn("fileRequestPrint(modelData.relpath)", FILE_MANAGER_QML)
        self.assertIn('id: printConfirmDialog', FILE_MANAGER_QML)
        self.assertIn('background: Rectangle', FILE_MANAGER_QML)
        # The confirmation's large thumbnail and the metadata-scan
        # gate (the live reports: the dialog's thumbnail
        # request, and a scan entry offered where it cannot work).
        self.assertIn('id: confirmThumb', FILE_MANAGER_QML)
        # The dialog reads the LARGE variant (the list cells use the
        # small one) and the thumbnail Images decode off the UI
        # thread.
        self.assertIn("thumbUrlLarge(root.confirmRelpath())", FILE_MANAGER_QML)
        self.assertIn("asynchronous: true", FILE_MANAGER_QML)
        # The dialogs own their Esc: a popup-held focus swallows the
        # key into the overlay (live-proven), so the content FocusScope
        # answers it.
        self.assertIn("focus: false", FILE_MANAGER_QML)
        # Esc CANCELS, not just closes: the payload must not survive
        # the dismissal (a dismissed confirmation used to resurrect).
        # Pinned INSIDE the Esc handlers — a bare substring would
        # also match the dialogs' Cancel buttons (the adversarial
        # round's pin-strength point).
        self.assertIn('''            Keys.onEscapePressed: {
                printConfirmDialog.close();
                if (root.printerModel != null) {
                    root.printerModel.fileCancelPrint();''', FILE_MANAGER_QML)
        self.assertIn('''            Keys.onEscapePressed: {
                deleteConfirmDialog.close();
                if (root.printerModel != null) {
                    root.printerModel.fileCancelDelete();''', FILE_MANAGER_QML)
        self.assertIn('''            Keys.onEscapePressed: {
                renameDialog.close();
                if (root.printerModel != null) {
                    root.printerModel.fileCancelRename();''', FILE_MANAGER_QML)
        self.assertIn('''            Keys.onEscapePressed: {
                uploadConfirmDialog.close();
                if (root.printerModel != null) {
                    root.printerModel.fileCancelUpload();''', FILE_MANAGER_QML)
        self.assertIn("onOpened: printConfirmDialogFocus.forceActiveFocus()", FILE_MANAGER_QML)
        # Closing the popup closes its dialogs (a surviving dialog
        # stays painted over the dashboard with dead buttons — the
        # adversarial round's live repro).
        self.assertIn('''    Connections {
        target: root
        function onOpenChanged() {
            if (!root.open) {
                printConfirmDialog.close();''', FILE_MANAGER_QML)
        # The walk-error banner's dismiss (the live ruling:
        # it overlays the first row, so it must be closable).
        self.assertIn('text: "✕"', FILE_MANAGER_QML)
        self.assertIn("root.printerModel.fileClearWalkError()", FILE_MANAGER_QML)
        # The New-folder dialog (the live request).
        self.assertIn("id: createFolderDialog", FILE_MANAGER_QML)
        self.assertIn('text: "New folder…"', FILE_MANAGER_QML)
        # The left columns are FROZEN (the live ruling);
        # the trailing half slides inside a clip wrapper at the
        # frozen edge, and the header mirrors it: sticky frozen,
        # the flick following the strip's contentX. A horizontal
        # wheel anywhere over the grid scrolls the strip (the wheel
        # used to work only over the scrollbar).
        self.assertIn("contentWidth: root.stickyWidth + root.trailingWidth", FILE_MANAGER_QML)
        self.assertIn("ScrollBar.horizontal: ScrollBar {", FILE_MANAGER_QML)
        self.assertIn('''                            x: root.stickyWidth
                            width: root.trailingWidth
                            height: root.rowHeight
                            clip: true''', FILE_MANAGER_QML)
        self.assertIn("x: -gridHorizontal.contentX", FILE_MANAGER_QML)
        self.assertIn("contentX: gridHorizontal.contentX", FILE_MANAGER_QML)
        self.assertIn("WheelHandler {", FILE_MANAGER_QML)
        self.assertIn("orientation: Qt.Horizontal", FILE_MANAGER_QML)
        self.assertIn("wheel.angleDelta.x", FILE_MANAGER_QML)
        # Search shows the folder breadcrumb under the name (the
        # live request — same-named files in different
        # folders must be tellable).
        self.assertIn('visible: root.printerModel != null && root.printerModel.fileManagerSearch.length > 0 && modelData.folder !== ""', FILE_MANAGER_QML)
        # The title floors hold from the first frame (static seed) —
        # headers never elide, wrap or overflow.
        self.assertIn('"thumb": 70', FILE_MANAGER_QML)
        # The content cells elide through a width cap (an uncapped
        # label keeps its implicit width and overflows).
        self.assertIn('width: Math.min(implicitWidth, parent.width - (modelData[0] === "Status"', FILE_MANAGER_QML)
        self.assertIn("root.rowNeedsMetadata(modelData)", FILE_MANAGER_QML)
        # Snapshot 3 mutations: the delete confirmation, the rename
        # dialog with its live collision line, and the wiring.
        self.assertIn('id: deleteConfirmDialog', FILE_MANAGER_QML)
        self.assertIn('id: renameDialog', FILE_MANAGER_QML)
        self.assertIn("fileRequestDelete()", FILE_MANAGER_QML)
        self.assertIn("fileRequestDeleteFile(modelData.relpath)", FILE_MANAGER_QML)
        self.assertIn("fileRequestRename(modelData.relpath)", FILE_MANAGER_QML)
        # The dialogs are modal over the manager and the rename field
        # pre-selects the stem (the live reports).
        self.assertEqual(FILE_MANAGER_QML.count("modal: true"), 6)
        self.assertIn("renameField.select(0, root.renameStemLength(target.name))", FILE_MANAGER_QML)
        # The helper the open handler calls must be DEFINED — a
        # ReferenceError inside onOpened only fires on open, which
        # the engine gate (closed popovers) cannot see; the missing
        # helper was the live "no pre-populated name" report.
        self.assertIn("function renameTarget()", FILE_MANAGER_QML)
        self.assertIn('palette.highlight: UM.Theme.getColor("primary")', FILE_MANAGER_QML)
        # The field takes focus on open and Return confirms (the
        # live requests).
        self.assertIn("renameField.forceActiveFocus()", FILE_MANAGER_QML)
        self.assertIn("Keys.onReturnPressed: root.confirmRename()", FILE_MANAGER_QML)
        # Tab-focus cues: the field's outline flips blue on focus and
        # the six popup buttons take tab focus (the live
        # report — no cue while tabbing).
        self.assertIn('border.color: renameField.activeFocus ? UM.Theme.getColor("primary")', FILE_MANAGER_QML)
        self.assertEqual(FILE_MANAGER_QML.count("focusPolicy: Qt.StrongFocus"), 9)
        # Snapshot 3 finish: the upload affordance, the local-file
        # picker, and the folder context menu.
        self.assertIn('text: "Upload file…"', FILE_MANAGER_QML)
        self.assertIn('id: filePicker', FILE_MANAGER_QML)
        self.assertIn('id: dirActionsMenu', FILE_MANAGER_QML)
        self.assertIn("fileRequestRenameDir(dirActionsMenu.dirPath)", FILE_MANAGER_QML)
        self.assertIn("fileRequestDeleteDir(dirActionsMenu.dirPath)", FILE_MANAGER_QML)
        self.assertIn('id: uploadProgressDialog', FILE_MANAGER_QML)
        self.assertIn("root.uploadProgressState() === \"uploading\"", FILE_MANAGER_QML)
        # The recents strip: a scrolling 50, no dismissal glyph, and
        # the strip's own thumbnails (the live requests).
        self.assertIn('id: recentsScroller', FILE_MANAGER_QML)
        self.assertIn('id: recentsThumb', FILE_MANAGER_QML)
        self.assertNotIn('fileHideRecent', FILE_MANAGER_QML)
        self.assertNotIn('text: "×"', FILE_MANAGER_QML)
        # The console grab bar hides under the auto-collapse width
        # (the live request).
        self.assertIn("visible: !consolePanel.tooNarrow", MONITOR_QML)
        # Esc on the Monitor page leaves the stage (the
        # live request): Preview when sliced, Prepare otherwise. The
        # popover and chart close on the same key first. The ONE
        # ladder lives in the DASHBOARD document now — it hosts every
        # layer, so no other claimant can fire the stage-exit branch
        # from under a popup it cannot see.
        self.assertIn("leaveMonitorStage", DASHBOARD_QML)
        # The console error bell (the live request): a red
        # bell beside the Console header while collapsed until
        # expanded.
        self.assertIn("consoleErrorBell", MONITOR_QML)
        self.assertIn('Qt.resolvedUrl("Bell.svg")', MONITOR_QML)
        self.assertIn("consoleErrorBell", MONITOR_MODEL)
        # The extrude distance/speed rows keep their selection
        # highlighted (the live report).
        self.assertIn("extrudeDistance === 5", TOOLHEAD_SECTION_QML)
        self.assertIn("extrudeSpeed === 1500", TOOLHEAD_SECTION_QML)
        # The abs/rel toggle (the live request) and the
        # dropped 15 mm distance button.
        self.assertIn("setPositionMode", TOOLHEAD_SECTION_QML)
        self.assertNotIn('"15"', DASHBOARD_QML)
        # The mode text is the toggle control (the live
        # ruling), now on its own "Moves" row under the Position
        # readout (the 2026-09-17 ruling), and the Move distance
        # combo restores the persisted selection.
        self.assertIn('text: "Moves"', TOOLHEAD_SECTION_QML)
        self.assertIn("jogPresets.indexOf", TOOLHEAD_SECTION_QML)
        # Filament state is colour-coded: green detected, orange runout.
        self.assertIn("MoonrakerTheme.filamentDetected", FILAMENT_SECTION_QML)
        self.assertIn("MoonrakerTheme.warningOrange", FILAMENT_SECTION_QML)
        self.assertNotIn('id: powerOffDialog', MONITOR_QML)
        self.assertNotIn('id: cancelPrintDialog', MONITOR_QML)
        # The right column hosts the print actions, power and the lock.
        # The print actions ride their component (4.3.0).
        for token in ('text: "Pause"', 'text: "Resume"', 'text: "Cancel"', "cancelRequested()"):
            self.assertIn(token, PRINT_SECTION_QML)
        self.assertIn('title: "Power"', POWER_SECTION_QML)
        self.assertIn("onPowerOffConfirmRequested", DASHBOARD_QML)
        self.assertIn("powerOffConfirmRequested(modelData.name)", POWER_SECTION_QML)
        self.assertIn("property bool anyPowerLocked", POWER_SECTION_QML)
        self.assertNotIn("anyPowerLocked", DASHBOARD_QML)
        for token in ("powerOffDialog.open()", "controlsCollapsed",
                      "id: collapsedTitle", "rotation: 90",
                      '"Lock all controls."', '"Unlock all controls."', "PadlockLocked.svg", "PadlockUnlocked.svg",
                      "setControlsLocked", "setControlsCollapsed"):
            self.assertIn(token, DASHBOARD_QML)
        self.assertIn("controlsLocked", MONITOR_MODEL)
        self.assertIn("controlsCollapsed", MONITOR_MODEL)
        # The Information and Printer status panes collapse and persist too.
        # Collapsed titles must anchor to their header ROW: anchoring to
        # the toggle inside it is illegal in QML and silently drops the
        # anchor, which is what un-pinned the titles for so long.
        self.assertIn("anchors.top: controlHeader.bottom", DASHBOARD_QML)
        self.assertIn("anchors.top: infoHeader.bottom", MONITOR_QML)
        self.assertIn("anchors.top: statusHeader.bottom", MONITOR_QML)
        for token in ('text: "Printer status"', 'title: "Print job"', 'title: "Bed mesh"',
                      "id: infoCollapseButton", "id: statusCollapseButton",
                      "id: infoCollapsedTitle", "id: statusCollapsedTitle",
                      "setInfoCollapsed", "setStatusCollapsed"):
            self.assertIn(token, MONITOR_QML + MONITOR_MODEL + MESH_SECTION_QML + JOB_SECTION_QML)
        self.assertIn("infoCollapsed", MONITOR_MODEL)
        self.assertIn("statusCollapsed", MONITOR_MODEL)
        self.assertIn("cameraRefreshNonce", MONITOR_MODEL)
        self.assertIn("mpf_reload", MONITOR_QML)  # Refresh camera restarts the stream

    def test_temperature_chart_repaints_and_popovers_are_overlays(self):
        # A QML Canvas paints exactly once unless asked: the data
        # canvas must requestPaint on payload, geometry and visibility
        # changes (it used to render one frame and freeze) — and it
        # paints OFF the main thread (Image target + Threaded
        # strategy). The hover surface is scene-graph geometry: the
        # overlay Canvas is gone, and NOTHING in the hover path may
        # request a paint.
        self.assertIn("onChartChanged", TEMP_CHART_QML)
        self.assertIn("dataCanvas.requestPaint()", TEMP_CHART_QML)
        self.assertIn("onVisibleChanged", TEMP_CHART_QML)
        self.assertIn("renderTarget: Canvas.Image", TEMP_CHART_QML)
        self.assertIn("renderStrategy: Canvas.Threaded", TEMP_CHART_QML)
        self.assertNotIn("overlay.requestPaint()", TEMP_CHART_QML)
        self.assertNotIn("id: overlay", TEMP_CHART_QML)
        self.assertIn("id: hoverCursorLine", TEMP_CHART_QML)
        self.assertIn("id: hoverMarkers", TEMP_CHART_QML)
        # One open pop-over at a time; the shells are overlay siblings
        # of the pane RowLayout, never layout children (anchored layout
        # children reflow every pane and log undefined-behavior
        # warnings).
        self.assertIn('property string openPopOver: ""', MONITOR_QML)
        self.assertNotIn("bedMeshPanelOpen", MONITOR_QML)
        self.assertNotIn("chartPanelOpen", MONITOR_QML)
        self.assertIn("id: outsideClickLayer", MONITOR_QML)
        # Esc closes the popovers through the same window-level
        # Shortcut that leaves the stage (the Keys handler died with
        # focus — the live report). The shortcut and the
        # ladder live in the dashboard document — one claimant for
        # the key across every layer.
        self.assertIn('sequence: "Esc"', DASHBOARD_QML)
        self.assertIn("leaveMonitorStage", DASHBOARD_QML)
        # The legend binds to the legend property (notifies only on real
        # changes, so delegates are never rebuilt at the 1 Hz sample
        # cadence) and toggles on user intent only — re-bound checkboxes
        # used to rewrite the state file every second.
        self.assertIn("temperatureChartLegend.series", MONITOR_QML)
        self.assertIn("onToggled: root.printer.setTemperatureSensorVisible", MONITOR_QML)
        self.assertNotIn("onCheckedChanged: root.printer.setTemperatureSensorVisible", MONITOR_QML)
        # Target bands, not dashed lines (the ruling), and the
        # hover readout carries the clock.
        self.assertIn("Target bands", TEMP_CHART_QML)
        self.assertIn("hoverClock", TEMP_CHART_QML)
        self.assertIn('"wallOrigin"', TEMP_CHART_QML)
        # The mini chart carries a live legend row (dot, name, value) so
        # the unlabelled sparklines stay readable, and the mesh detail's
        # readout row is permanent so the map never resizes on hover.
        self.assertIn("modelData.label + \" \" + value", TEMP_HISTORY_SECTION_QML)
        self.assertIn("Hover the map for probe coordinates", MONITOR_QML)
        # The chart's hover values live in a cursor-following tooltip
        # OUTSIDE the clipped card (allowed to overflow any boundary) —
        # there is no in-card readout row to stretch the pop-up, and the
        # chart is declared exactly once in the pop-over.
        self.assertIn("id: chartHoverTooltip", MONITOR_QML)
        self.assertNotIn("Hover the chart for per-series values", MONITOR_QML)
        self.assertEqual(MONITOR_QML.count("id: chartPanelChart"), 1)
        self.assertIn("mapToItem(root, chartPanelChart.hoverCursor", MONITOR_QML)
        self.assertIn("hoverCursor", TEMP_CHART_QML)
        # The top gridline's temperature label must be clamped by the
        # FONT ASCENT into the canvas (it used to baseline at y = -3,
        # always off-screen, and the fixed 10 px clamp shaved digit
        # tops on larger desktop fonts). The clamp now reads the paint
        # job's snapshot (the threaded paint must not reach the theme).
        self.assertIn("Math.ceil(job.fontPixels * 0.8) + 3", TEMP_CHART_QML)
        # Units are explicit (°C — Cura has no temperature-unit
        # preference, so the plugin follows Cura) on the axis, the
        # tooltip rows and both legend live values; the X-axis tick
        # strip keeps breathing room below the plot.
        self.assertIn('toFixed(0) + "°C"', TEMP_CHART_QML)
        self.assertIn('points[index][1].toFixed(1) + "°C"', TEMP_CHART_QML)
        # The collapsed readout builds the pair form through the
        # printer-side infoReadoutText (the 2026-09-17 ruling), which
        # no longer shares the legend's exact expression — the chart's
        # own form is the one occurrence.
        self.assertEqual(MONITOR_QML.count('toFixed(1) + "°C"'), 1)
        self.assertEqual(TEMP_HISTORY_SECTION_QML.count('toFixed(1) + "°C"'), 1)
        self.assertIn("Math.max(1, height - 22)", TEMP_CHART_QML)
        # The console history lives in a terminal-styled pane: dark,
        # fixed-width, newest line pinned to the bottom, with a prompt
        # glyph on the input row.
        for token in ('color: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline', "No commands yet — lines you send appear here.", 'text: ">"'):
            self.assertIn(token, MONITOR_QML)
        # Both pop-overs open at the same offset over the camera column
        # so a second click on the opener dismisses without moving the
        # mouse (the chosen position, mesh-style).
        self.assertEqual(MONITOR_QML.count("x: cameraArea.x + UM.Theme.getSize(\"default_margin\").width"), 4)
        # meshDetail is component-scoped: exactly one in-scope refresh
        # (inside meshContent) may reference it, or the outer handler
        # throws and kills the pop-over auto-close.
        self.assertEqual(MONITOR_QML.count("meshDetail.refresh()"), 1)
        self.assertGreater(MONITOR_QML.index("meshDetail.refresh()"), MONITOR_QML.index("id: meshContent"))
        # The snapped-second gate must WRAP the publications, and the
        # chart-changed re-snap must clear the snap first so a gap
        # reset or legend toggle republishes even on a snap collision.
        self.assertGreater(TEMP_CHART_QML.index("hoverClock = _clockText(snapped);"),
                           TEMP_CHART_QML.index("if (snapped !== _hoverSnap) {"))
        on_chart = TEMP_CHART_QML[TEMP_CHART_QML.index("onChartChanged: {"):]
        self.assertIn("_hoverSnap = -1;", on_chart[:800])
        self.assertIn("_updateHover(root.hoverX);", on_chart[:800])
        # The freshly created detail map rehydrates the persisted
        # probe-points toggle at creation (never after a toggle event).
        self.assertIn("showProbePoints: root.printer != null ? root.printer.showProbePoints : false", MONITOR_QML)
        # A refused console send keeps the typed draft.
        self.assertIn("if (root.printer.sendConsoleCommand(consoleInput.text)) {", MONITOR_QML)
        # When every primary sensor is hidden, up to two visible
        # non-primary sensors stand in for the mini chart. The policy
        # lives ONCE in the model (mini_names), and the legend carries
        # the selection as a stable row list — the section's chart
        # reads the bounded mini payload directly.
        self.assertIn("mini_names(", MONITOR_MODEL)
        self.assertIn("legend.miniSeries", MONITOR_QML)
        self.assertIn("temperatureChartMini", TEMP_HISTORY_SECTION_QML)
        # The Layer row discloses which source produced the value, and
        # the terminal picks an installed monospace face at runtime
        # (the generic and comma lists do not resolve everywhere).
        self.assertIn("monitorLayerSource", JOB_SECTION_QML)
        self.assertIn("monitorLayerSource !== undefined", JOB_SECTION_QML)
        self.assertIn("Layer source: ", JOB_SECTION_QML)
        # The model DECLARES the source (a dynamic setProperty would be
        # undefined at QML creation and the .length read would throw).
        self.assertIn('value_property(str, "monitorLayerSource", monitorChanged, "")', MONITOR_MODEL)

    def test_the_chart_paints_each_point_without_re_reading_its_geometry(self):
        # The chart is open while a print runs, and every payload landing
        # repaints it: the data layers map each sample with the geometry
        # resolved once per paint (a scale and offset per axis) rather
        # than calling root._xFor/root._yFor per coordinate, which
        # re-reads six QML properties each time.
        self.assertIn("var plotWidth = width - gutter;", TEMP_CHART_QML)
        self.assertIn("points[j][0] * mapScaleX + mapOffsetX", TEMP_CHART_QML)
        self.assertIn("targetSeg[u][1] * mapScaleY + mapOffsetY", TEMP_CHART_QML)
        self.assertIn("powerSeg[q][1] * plotBottom", TEMP_CHART_QML)
        # The hover search runs once per mouse move, not again in any
        # repaint: the markers it published are what the scene-graph
        # Repeater draws directly. One call site plus the definition.
        self.assertEqual(TEMP_CHART_QML.count("_nearestIndex("), 2)
        self.assertIn("model: root._hoverMarks", TEMP_CHART_QML)
        self.assertIn("_hoverMarks = marks;", TEMP_CHART_QML)
        # The render domain comes off the payload, with the scan kept for
        # a payload that predates it.
        self.assertIn("if (series.bounds !== undefined) {", TEMP_CHART_QML)

    def test_every_value_property_rides_its_signal_group(self):
        # The panel's catch: a key declared with a notify signal but
        # absent from that signal's group can never notify — the
        # next-pause readout went stale while PAUSED (the other keys
        # in the group masked it while printing). Every declaration
        # must appear in its signal's group; the allowlist holds the
        # two pre-existing gaps the round did not add.
        import ast
        module = ast.parse(MONITOR_MODEL)
        groups = {}
        for node in ast.walk(module):
            if isinstance(node, ast.Assign) and any(
                    getattr(target, "id", "") == "_SIGNAL_KEYS" for target in node.targets):
                for element in ast.walk(node.value):
                    if isinstance(element, ast.Tuple) and len(element.elts) >= 2 \
                            and isinstance(element.elts[0], ast.Constant) \
                            and isinstance(element.elts[1], ast.Tuple):
                        groups[str(element.elts[0].value)] = {
                            str(entry.value) for entry in element.elts[1].elts
                            if isinstance(entry, ast.Constant)}
        allowlist = {"britishSpelling", "monitorLoading"}
        missing = []
        for name, signal in re.findall(r'value_property\([^,]+,\s*"([A-Za-z0-9]+)",\s*(\w+)', MONITOR_MODEL):
            if name in allowlist:
                continue
            if name not in groups.get(signal, set()):
                missing.append(f"{name} ({signal})")
        self.assertEqual(missing, [],
                         "value properties outside their signal groups: %s" % missing)
        self.assertIn('"monitorLayerSource"', MONITOR_MODEL)
        # A slim bar under the layer value shows the within-layer
        # progress; it hides while the layer has no height anchor.
        self.assertIn("monitorLayerProgress >= 0", JOB_SECTION_QML)
        self.assertIn("Layer progress — how far through the current layer.", JOB_SECTION_QML)
        self.assertIn("without loading it into the preview", JOB_SECTION_QML)
        # The glyph's in-progress state: a non-clickable hourglass.
        self.assertIn('Qt.resolvedUrl("Hourglass.svg")', JOB_SECTION_QML)
        self.assertIn("root.printerModel.improvingEta", JOB_SECTION_QML)
        # Both progress figures carry two decimals.
        self.assertIn("monitorProgress.toFixed(2)", JOB_SECTION_QML)
        self.assertIn("(root.printerModel.monitorLayerProgress * 100).toFixed(2)", JOB_SECTION_QML)
        # The Improve-ETA bar: determinate during the download, a
        # plugin-owned sweep while resolving/indexing (Cura's themed
        # indeterminate renders as a static full bar).
        self.assertIn("improveEtaProgress", JOB_SECTION_QML)
        self.assertIn("NumberAnimation on sweepPhase", JOB_SECTION_QML)
        self.assertIn("(1 - Math.abs(2 * improveEtaBar.sweepPhase - 1))", JOB_SECTION_QML)
        self.assertIn("The spacer keeps the glyph hugging", JOB_SECTION_QML)
        self.assertIn("SequentialAnimation on rotation", JOB_SECTION_QML)
        self.assertIn("PauseAnimation", JOB_SECTION_QML)
        self.assertIn("root.printerModel.improveEtaPhase", JOB_SECTION_QML)
        self.assertIn("download_fraction", MONITOR_MODEL + (PLUGINS / "RemoteFileService.py").read_text(encoding="utf-8"))
        self.assertIn('"monitorLayerProgress"', MONITOR_MODEL)
        self.assertIn("function monoFamily()", MONITOR_QML)
        self.assertIn("Qt.fontFamilies()", MONITOR_QML)
        # The tooltip sizes to its content (no width cap: the ruling
        # lets it overflow any boundary) and flips above only when
        # there is no room below the cursor.
        self.assertIn("width: tooltipColumn.implicitWidth + 2", MONITOR_QML)
        self.assertIn("y: chartPanel.hoverCursor.y + height + 16 > root.height", MONITOR_QML)
        # Send and Clear share one row beside the input (the
        # side-by-side request) — no RowLayout may open between them.
        send_clear = MONITOR_QML[MONITOR_QML.index('text: "Send"'):MONITOR_QML.index('text: "Clear"')]
        self.assertNotIn("RowLayout {", send_clear)
        # The mesh readout says Height, not a third coordinate, and a
        # live refresh re-snaps a parked cursor.
        self.assertIn('Height " + value.toFixed(3)', BED_MESH_MAP_QML)
        self.assertIn("root.snap(root._hoverMouseX", BED_MESH_MAP_QML)
        self.assertIn('"Probe points"', MONITOR_QML)
        self.assertIn("showProbePoints", BED_MESH_MAP_QML)
        # The colour row offers a full picker beside the quick swatches.
        self.assertIn('import QtQuick.Dialogs', MONITOR_QML)
        self.assertIn("chartColorDialog", MONITOR_QML)
        self.assertIn('text: "Custom…"', MONITOR_QML)
        self.assertIn("setShowProbePoints", MONITOR_QML)
        # Terminal order: the history sits above the input row.
        self.assertLess(MONITOR_QML.index("id: consoleText"), MONITOR_QML.index("id: consoleInput"))
        self.assertIn("All sensors hidden — click to re-enable one in the chart.", TEMP_HISTORY_SECTION_QML)

    def test_system_section_has_the_manual_reconnect(self):
        # A live request: a Reconnect in the System
        # section for a UI stuck after a printer error.
        self.assertIn('text: "Reconnect"', SYSTEM_INFO_SECTION_QML)
        self.assertIn("root.printerModel.reconnect()", SYSTEM_INFO_SECTION_QML)

    def test_system_restart_surface(self):
        for token in ("firmwareRestart", "hostRestart", "FIRMWARE_RESTART", "machine/reboot"):
            self.assertIn(token, MONITOR_MODEL + CONTROLS)
        for token in ('text: "Firmware restart"', 'text: "Host restart"', 'text: "Klipper restart"'):
            self.assertIn(token, SYSTEM_SECTION_QML)
        # The reason copy lives in the policy (4.2.0): the row reads
        # the published restartReason — the old QML sentence was
        # superseded by the policy's short form.
        self.assertIn('"A print is running"', POLICY)
        self.assertIn("restartReason", SYSTEM_SECTION_QML)

    def test_emergency_stop_is_pinned_outside_scrollable_controls(self):
        # The dock lives at the bottom of the dashboard, spanning the whole
        # window width (including under the controls pane), outside the
        # scrollable panes, so it stays visible in every collapse state.
        self.assertIn("anchors.bottom: emergencyDock.top", DASHBOARD_QML)
        self.assertIn("id: emergencyDock", DASHBOARD_QML)
        self.assertNotIn("id: emergencyDock", MONITOR_QML)
        self.assertEqual(DASHBOARD_QML.count("id: emergencyButton\n"), 1)

    def test_emergency_stop_remainder_copy_follows_the_theme_text_colour(self):
        # The 4.5.0 dark-mode ruling: the idle copy was hardcoded
        # black and unreadable on dark mode's grey ground. The
        # remainder follows the theme's text colour; the white
        # over-the-red-fill sweep copy stays white.
        self.assertIn('color: UM.Theme.getColor("text")', DASHBOARD_QML)
        self.assertIn('color: "white"', DASHBOARD_QML)

    def test_dashboard_shows_current_z_offset_beside_nudges(self):
        self.assertIn('text: "Current Z offset"', PRINT_SECTION_QML)
        self.assertIn('"Current " + root.printerModel.zOffsetText', TUNING_SECTION_QML)
        self.assertIn("adjustZOffset", TUNING_SECTION_QML)

    def test_z_offset_buttons_are_opposites_with_equal_click_zones(self):
        self.assertIn("id: zOffsetGrid", TUNING_SECTION_QML)
        self.assertIn("model: [-0.005, -0.01, -0.025, -0.05]", TUNING_SECTION_QML)
        self.assertIn("model: [0.005, 0.01, 0.025, 0.05]", TUNING_SECTION_QML)
        # A two-column grid (up left, down right): both Repeater
        # delegates fill their cell equally, so click zones stay
        # matched and the labels cannot elide at narrow pane widths.
        grid = TUNING_SECTION_QML[TUNING_SECTION_QML.index("id: zOffsetGrid"):TUNING_SECTION_QML.index('text: "Clear Z offset"')]
        # Up row first, down row second, each four-across with equal
        # layout cells; the up model must precede the down model.
        self.assertLess(grid.index('text: "↑ "'), grid.index('text: "↓ "'))
        self.assertEqual(grid.count("RowLayout {"), 2)
        self.assertGreaterEqual(grid.count("Layout.fillWidth: true"), 2)
        # fixedWidthMode must NOT combine with Layout.fillWidth: the two
        # fight on every layout pass and sent the grid into an endless
        # invalidate loop that froze pane collapses.
        self.assertNotIn("fixedWidthMode: true", grid)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", PROFILES_SECTION_QML)
        self.assertIn('modelData.active ? "Active — "', PROFILES_SECTION_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", PROFILES_SECTION_QML)
        self.assertNotIn("temperaturePresetSelector", DASHBOARD_QML)

    def test_pwm_controls_ride_their_component(self):
        self.assertIn("pwmOutputItems", PWM_SECTION_QML)
        self.assertIn("setPwmOutput", PWM_SECTION_QML)
        self.assertIn('title: "PWM outputs"', PWM_SECTION_QML)

    def test_monitor_layer_tracks_remote_print_not_cura_slider(self):
        resolver = (PLUGINS / "PrintState.py").read_text(encoding="utf-8")
        self.assertIn("class LayerResolver", resolver)
        self.assertIn("total = len(index.ranges)", resolver)
        self.assertNotIn("getCurrentLayer", resolver)
        self.assertIn("self._print_state()", MONITOR_MODEL)

    def test_eta_anchors_to_slicer_metadata_instead_of_gcode_bytes(self):
        self.assertIn("def estimate_remaining", FORMATTING)
        self.assertIn("remaining = max(0, estimate - elapsed)", FORMATTING)
        self.assertIn("0.60 * estimate <= elapsed + by_file <= 1.75 * estimate", FORMATTING)
        self.assertIn("physical.metadata_complete", FORMATTING)
        self.assertNotIn("self._metadata_estimated_time * (1.0 - progress)", MONITOR_MODEL)

    def test_mcu_stats_are_exposed_individually(self):
        for token in (
            "parse_mcu_stats",
            "mcu_awake",
            "mcu_task_avg",
            "bytes_retransmit",
            "mcuItems",
            '"Main MCU"',
        ):
            self.assertIn(token, TYPED)
        self.assertIn("modelData.load", MCUS_SECTION_QML)
        self.assertIn("modelData.frequency", MCUS_SECTION_QML)
        self.assertIn("modelData.transport", MCUS_SECTION_QML)

    def test_addressable_led_colour_is_controllable(self):
        for token in (
            "redPercent",
            "greenPercent",
            "bluePercent",
            "whitePercent",
            "hasWhite",
            "setLedColor",
            "SET_LED LED=",
        ):
            self.assertIn(token, CONTROLS + MONITOR_MODEL)
        self.assertIn("function applyLedColour()", LEDS_SECTION_QML)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("applyLedColour()"), 4)
        self.assertIn('root.interactionSink(interacting, modelData.object, "led-red")', LEDS_SECTION_QML)
        self.assertNotIn('text: "Set colour"', DASHBOARD_QML)
        self.assertIn("root.printerModel.setLedColor", LEDS_SECTION_QML)

    def test_live_tuning_slider_ranges_expand_from_accepted_value(self):
        self.assertIn("to: Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.speedFactorPercent * 2) : 200)", TUNING_SECTION_QML)
        self.assertIn("to: Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.flowFactorPercent * 2) : 200)", TUNING_SECTION_QML)
        self.assertIn('max(10 if kind == "speed" else 50, int(percent))', CONTROLS)
        self.assertNotIn("min(200, int(percent))", CONTROLS)
        self.assertNotIn("min(150, int(percent))", CONTROLS)

    def test_monitor_sliders_only_commit_on_release(self):
        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use
        # Qt Quick Controls' deferred-value mode. The control itself
        # funnels every interaction path (groove, handle drag, keyboard)
        # into valueTuning (the live preview) and valueCommitted (the
        # apply on completion) — the usage sites never re-derive the
        # interaction state. The tuning pair rides its component
        # (4.3.0); per-file counts plus the total.
        self.assertGreaterEqual(TUNING_SECTION_QML.count("live: false"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("live: false"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("live: false"), 5)
        self.assertGreaterEqual(PWM_SECTION_QML.count("live: false"), 1)
        self.assertEqual(DASHBOARD_QML.count("live: false"), 0)
        self.assertGreaterEqual(TUNING_SECTION_QML.count("live: false") + FANS_SECTION_QML.count("live: false") + LEDS_SECTION_QML.count("live: false") + PWM_SECTION_QML.count("live: false"), 9)
        for slider_id in ("speedSlider", "flowSlider"):
            marker = "id: " + slider_id
            start = TUNING_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", TUNING_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("fanSlider",):
            marker = "id: " + slider_id
            start = FANS_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", FANS_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("ledSlider", "redSlider", "greenSlider", "blueSlider", "whiteSlider"):
            marker = "id: " + slider_id
            start = LEDS_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", LEDS_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("pwmSlider",):
            marker = "id: " + slider_id
            start = PWM_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", PWM_SECTION_QML[start:start + 700], slider_id)
        self.assertGreaterEqual(TUNING_SECTION_QML.count("onValueCommitted:"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("onValueCommitted:"), 5)
        self.assertGreaterEqual(PWM_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertEqual(DASHBOARD_QML.count("onValueCommitted:"), 0)
        self.assertIn("previewSpeedFactor", TUNING_SECTION_QML)
        self.assertIn("previewFlowFactor", TUNING_SECTION_QML)
        self.assertIn("previewFanSpeed", FANS_SECTION_QML)
        self.assertIn("previewLedBrightness", LEDS_SECTION_QML)
        self.assertIn("previewLedColor", LEDS_SECTION_QML)
        self.assertIn("previewPwmOutput", PWM_SECTION_QML)
        # The four component-local copies of the slider value helper
        # are gone (the engineering re-review): every call site reads
        # the slider's own selectedValue() — one definition in
        # OutlineSlider, the components cannot drift.
        self.assertNotIn("function sliderSelection", TUNING_SECTION_QML + FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML)
        self.assertIn("pwmSlider.selectedValue() + \"%\"", PWM_SECTION_QML)
        self.assertIn("speedSlider.selectedValue() + \"%\"", TUNING_SECTION_QML)
        self.assertIn("fanSlider.selectedValue() + \"%\"", FANS_SECTION_QML)
        self.assertIn('controlKind: "fan"', FANS_SECTION_QML)
        self.assertIn("root.printerModel.setSpeedFactor(value)", TUNING_SECTION_QML)
        self.assertIn("root.printerModel.setFlowFactor(value)", TUNING_SECTION_QML)

    def test_monitor_sliders_do_not_repeat_qml_properties(self):
        # The sliders ride their components now: the same duplicate
        # can creep back in any of them, so the union is swept.
        haystack = DASHBOARD_QML + TUNING_SECTION_QML + FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML
        self.assertIsNone(re.search(r"from: 0; to: 100; stepSize: 1\s*from: 0;", haystack))

    def test_slider_qml_prevents_parent_flickable_from_stealing_drag(self):
        self.assertIn("property bool tuningSliderPressed: false", DASHBOARD_QML)
        self.assertIn("function receiveSliderInteraction(interacting, object, kind)", DASHBOARD_QML)
        self.assertIn("interactive: !root.tuningSliderPressed", DASHBOARD_QML)
        for slider_id in ("speedSlider", "flowSlider"):
            self.assertIn("id: " + slider_id, TUNING_SECTION_QML)
        self.assertIn("id: fanSlider", FANS_SECTION_QML)
        for slider_id in ("ledSlider", "redSlider",
                          "greenSlider", "blueSlider", "whiteSlider"):
            self.assertIn("id: " + slider_id, LEDS_SECTION_QML)
        self.assertIn("id: pwmSlider", PWM_SECTION_QML)
        self.assertNotIn("root.tuningSliderPressed = interacting", DASHBOARD_QML)
        self.assertGreaterEqual(TUNING_SECTION_QML.count('root.interactionSink(interacting, "", "")'), 2)
        self.assertIn('root.interactionSink(interacting, modelData.object, "fan")', FANS_SECTION_QML)
        for kind in ("led-brightness", "led-red", "led-green", "led-blue", "led-white"):
            self.assertIn('root.interactionSink(interacting, modelData.object, "%s")' % kind, LEDS_SECTION_QML)
        self.assertIn('root.interactionSink(interacting, modelData.object, "pwm")', PWM_SECTION_QML)

    def test_monitor_uses_plugin_outline_bars_and_sliders(self):
        # The themed ProgressBar/Slider render a black slab in the
        # inactive-window palette (a screenshot) — the monitor's
        # bars and sliders are all plugin-owned outline components now,
        # so a bare themed control may not creep back in.
        for file_text in (MONITOR_QML, DASHBOARD_QML, TUNING_SECTION_QML, FANS_SECTION_QML, LEDS_SECTION_QML, PWM_SECTION_QML, JOB_SECTION_QML):
            # Every "ProgressBar {"/"Slider {" token must be the plugin
            # outline components (the substring check covers both) —
            # the range-filter bar is the other plugin-owned
            # slider-shaped component (its name embeds "Slider {").
            self.assertEqual(file_text.count("ProgressBar {"), file_text.count("OutlineProgressBar {"))
            self.assertEqual(file_text.count("Slider {"),
                file_text.count("OutlineSlider {") + file_text.count("BedMeshRangeSlider {"))
        # The Print-job section's bar is the stacked Rectangle (the
        # 2026-09-17 ruling) — no themed bar, the rule's intent.
        self.assertIn("THE STACKED BAR", JOB_SECTION_QML)
        self.assertEqual(DASHBOARD_QML.count("OutlineSlider {"), 0)
        self.assertGreaterEqual(TUNING_SECTION_QML.count("OutlineSlider {"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("OutlineSlider {"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("OutlineSlider {"), 5)
        self.assertGreaterEqual(PWM_SECTION_QML.count("OutlineSlider {"), 1)
        indicator = (PLUGINS / "LoadProgressIndicator.qml").read_text(encoding="utf-8")
        # The indicator bar's track is an outline too: transparent
        # interior, lining border, Cura-blue fill.
        self.assertIn('color: "transparent"', indicator)
        self.assertIn('border.color: UM.Theme.getColor("lining")', indicator)
        self.assertIn('border.width: UM.Theme.getSize("default_lining").width', indicator)
        self.assertIn('UM.Theme.getColor("primary")', indicator)
        # The outline components fill in Cura's brand blue (the same
        # accent as buttons and slider handles), never the text colour.
        bar = (PLUGINS / "OutlineProgressBar.qml").read_text(encoding="utf-8")
        slider = (PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertIn('UM.Theme.getColor("primary")', bar)
        self.assertNotIn('color: UM.Theme.getColor("text")', bar)
        self.assertNotIn('border.color: UM.Theme.getColor("text")', slider)
        self.assertIn('UM.Theme.getColor("primary")', slider)
        # Every bar corner uses Cura's own progressbar radius ("little
        # rounded ends"), never a full pill — and every bar/slider
        # radius needs cornerSide, because Cura.RoundedRectangle forces
        # radius 0 without it (the corners silently render square).
        for text in (bar, indicator, JOB_SECTION_QML):
            self.assertIn('UM.Theme.getSize("progressbar_radius")', text)
        for text, corners in ((bar, 2), (slider, 3), (indicator, 3), (JOB_SECTION_QML, 3)):
            self.assertEqual(text.count("cornerSide:"), corners, text[:40])
        # The pop-over shell must tolerate instantiation without a
        # parent (the engine gate creates every document standalone):
        # an unguarded parent.width read is a TypeError there.
        self.assertIn("parent != null ?", POPOVER_QML)
        # The preview load indicator must stay OUT of the buttons Row
        # (panel UX P1: as the Row's third child it painted off-card),
        # the pane collapse must close the pop-over, the ETA tooltip
        # must not claim a basis for a paused/absent value, and the
        # chart's filling state must actually render its copy.
        panel = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
        self.assertIn("The indicator is a SIBLING of the buttons Row", panel)
        self.assertIn("Collapsing the pane hides the pop-over's", MONITOR_QML)
        self.assertIn('monitorEta === "Paused" ? ""', JOB_SECTION_QML)
        # The "Collecting temperature history…" placeholder stays ABSENT:
        # the waiting state read as annoying and was dropped
        # before; the changelog quote was struck instead (the filling
        # flag remains plumbed, unused by the UI).
        self.assertNotIn("Collecting temperature history", MONITOR_QML + CHANGELOG)
        self.assertIn("_clockTextMinutes", TEMP_CHART_QML)
        # Disabled sliders grey the fill and the handle ring.
        slider_source = (PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertGreaterEqual(slider_source.count("control.enabled ? UM.Theme.getColor(\"primary\") : UM.Theme.getColor(\"text_disabled\")"), 2)
        # The colour/colour strings follow the user's locale.
        self.assertIn("britishSpelling", MONITOR_QML)
        self.assertIn("britishSpelling", MONITOR_MODEL)
        self.assertIn("Accessible.name: \"Show \"", MONITOR_QML)
        # The console's input row lives inside the dark well.
        self.assertIn("Layout.preferredHeight: 190 * screenScaleFactor", MONITOR_QML)

    def test_deferred_slider_and_monitor_ux_contracts(self):
        self.assertGreaterEqual(TUNING_SECTION_QML.count("live: false"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("live: false"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("live: false"), 5)
        self.assertGreaterEqual(PWM_SECTION_QML.count("live: false"), 1)
        self.assertEqual(DASHBOARD_QML.count("live: false"), 0)
        self.assertGreaterEqual(TUNING_SECTION_QML.count("onValueCommitted:"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("onValueCommitted:"), 5)
        self.assertGreaterEqual(PWM_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertEqual(DASHBOARD_QML.count("onValueCommitted:"), 0)
        self.assertNotIn("function sliderSelection(slider)", DASHBOARD_QML)
        # The refocus walk roots at the section instantiations (the
        # architecture re-review's dangling-id fix) — the dashboard
        # never names a section's repeater id.
        for repeater_id in ("fanRepeater", "ledRepeater", "pwmRepeater"):
            self.assertNotIn(repeater_id, DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(fansSection, target, kind)", DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(ledsSection, target, kind)", DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(pwmSection, target, kind)", DASHBOARD_QML)
        self.assertIn("After release, the latest value is applied once it has been unchanged for 250 ms.", TUNING_SECTION_QML)
        self.assertIn('text: "Refresh Moonraker\'s webcam list."', CAMERA_PANE_QML)
        # The exclude dialog died with the 4.6.0 rework: the gesture
        # is the confirmation, and the stale "cannot be undone" copy
        # must never survive anywhere in the monitor document.
        self.assertNotIn("Exclude object?", MONITOR_QML)
        self.assertNotIn("cannot be undone", MONITOR_QML)
        tuning = (PLUGINS / "MonitorTuning.py").read_text(encoding="utf-8")
        self.assertIn("DEBOUNCE_MS = 250", tuning)
        self.assertIn("current.revision != revision", tuning)

    def test_full_config_is_discovered_not_polled_every_second(self):
        self.assertIn('["save_config_pending", "save_config_pending_items"]', DATA)
        self.assertIn('"config-static"', DATA)
        self.assertIn('category="discovery"', DATA)

    def test_monitor_consumes_shared_session_poll_policy(self):
        self.assertIn("self._client.session.snapshot.printer_state", DATA)
        self.assertIn("poll_policy.interval_ms(", DATA)
        self.assertIn("if timer.interval() != interval:", DATA)
        for category in (
            "RequestCategory.AUXILIARY",
            "RequestCategory.POWER",
            "RequestCategory.SYSTEM",
            "RequestCategory.DISCOVERY",
        ):
            self.assertIn(category, DATA)

    def test_monitor_has_one_timer_policy_owner(self):
        owners = [source for source in (MONITOR_MODEL, DATA) if any(
            isinstance(node, ast.FunctionDef) and node.name == "_intervals"
            for node in ast.walk(ast.parse(source))
        )]
        self.assertEqual(len(owners), 1)
        self.assertIs(owners[0], DATA)

    def test_camera_identity_and_selection_are_typed_and_sized(self):
        self.assertIn("def identity(camera", TYPED)
        self.assertIn("camera_selected", TYPED)
        # The camera bar's final shape (the 2026-09-19 live-run
        # ruling): the controls ride LEVEL with the pane title with
        # no separate "Camera" label — the selector carries the name.
        # The bar lives in CameraPane.
        self.assertIn("Layout.preferredWidth: 180 * screenScaleFactor", CAMERA_PANE_QML)
        self.assertIn("Layout.minimumWidth: 60 * screenScaleFactor", CAMERA_PANE_QML)
        self.assertEqual(CAMERA_PANE_QML.count('text: "Camera"'), 0)

    def test_camera_qml_uses_the_activated_signal_index_not_bound_current_index(self):
        self.assertIn("onActivated: function (index)", CAMERA_PANE_QML)
        self.assertIn("selectWebcam(index)", CAMERA_PANE_QML)
        self.assertNotIn("selectWebcam(cameraSelector.currentIndex)", CAMERA_PANE_QML)

    def test_the_first_camera_apply_is_coalesced_through_one_callback(self):
        # The camera-delay fix's second cause: a first discovery
        # changes BOTH the url and the nonce, and each handler used to
        # apply the camera separately — two stream starts per first
        # entry. One callLater coalescer collapses the pair into one
        # apply; the initial attach, an explicit refresh and a camera
        # switch all still apply exactly once.
        self.assertIn('property bool _cameraApplyPending: false', MONITOR_QML)
        self.assertIn("Qt.callLater(function () {", MONITOR_QML)
        self.assertIn("root.scheduleCameraApply();", MONITOR_QML)
        self.assertEqual(MONITOR_QML.count("root.scheduleCameraApply();"), 2)

    def test_camera_render_watchdogs_are_wired(self):
        # The live reports: a stream that CONNECTED but never
        # painted a frame raises no error signal — the pane's stall
        # watchdog watches the frame size; and a suspend/wake leaves a
        # frozen frame whose size is already set — the model's wake
        # hook reloads the source.
        self.assertIn("cameraStallWatchdog", CAMERA_PANE_QML)
        self.assertIn("cameraRenderStalled()", CAMERA_PANE_QML)
        self.assertIn("def cameraRenderStalled", MONITOR_MODEL)
        self.assertIn("applicationStateChanged.connect(self._on_app_state_changed)", MONITOR_MODEL)

    def test_slider_click_behaviours_are_wired(self):
        # A live report: a click on a slider's grab handle
        # must not move it, and a click focuses the slider so the
        # arrow keys nudge one step.
        config = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text(encoding="utf-8")
        for token in ("handlePress", "pressIsOnHandle", "parent.value = parent.valueBeforePress",
                      "focusPolicy: Qt.StrongFocus", "Keys.onUpPressed: increase()", "forceActiveFocus()", "mouse.accepted = parent.handlePress"):
            self.assertIn(token, config)
        # The dashboard's OutlineSliders carry the same behaviours in
        # the shared component (4.2.0): the handle path drives the
        # value through the overlay, and every interaction path
        # (groove, handle, keyboard) funnels into the component's own
        # valueTuning/valueCommitted signals — the usage sites never
        # re-derive the interaction state.
        outline = (PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        for token in ("handlePress", "pressIsOnHandle", "tuningActive", "focusPolicy: Qt.StrongFocus",
                      "forceActiveFocus()", "control.value = control.valueBeforePress",
                      "signal valueTuning", "signal valueCommitted", "readonly property bool interacting",
                      "keyDebounce.restart()", "control.tuningActive = true"):
            self.assertIn(token, outline)
        self.assertIn("onValueCommitted", DASHBOARD_QML + TUNING_SECTION_QML + FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML)
        # The keyboard nudge holds the interaction state until its
        # value submits, and the fan/LED/PWM repeaters freeze while a
        # tuning slider is mid-gesture — without the hold, the
        # commit's publish rebuilt the repeaters mid-nudge and killed
        # the focused delegate (a live report).
        for token in ("root.frozenFanItems = root.printer.fanControlItems",):
            self.assertIn(token, DASHBOARD_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", FANS_SECTION_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", LEDS_SECTION_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", PWM_SECTION_QML)
        # The bed-mesh range filter's keyboard half: focus + arrow keys.
        range_slider = (PLUGINS / "BedMeshRangeSlider.qml").read_text(encoding="utf-8")
        for token in ("Keys.onLeftPressed", "Keys.onRightPressed", "Keys.onUpPressed", "forceActiveFocus()"):
            self.assertIn(token, range_slider)

    def test_mesh_rainbow_bar_matches_the_preview_scale(self):
        # A live request: the expanded bed-mesh view shows
        # the SAME blue-to-red min/max bar as the Preview's overlay —
        # the shared dual-ended range-filter component (4.2.0) owns
        # the stops now, so the two surfaces cannot drift.
        slider = (PLUGINS / "BedMeshRangeSlider.qml").read_text(encoding="utf-8")
        for stop in ("MoonrakerTheme.bandBlue", "MoonrakerTheme.bandCyan", "MoonrakerTheme.bandGreen", "MoonrakerTheme.bandYellow", "MoonrakerTheme.bandRed"):
            self.assertIn(stop, slider)
        for qml in (MONITOR_QML, PREVIEW_CONTROLS_QML):
            self.assertIn("BedMeshRangeSlider", qml)
        self.assertIn('text: root.printer != null ? "Low " + root.printer.bedMeshMinimum.toFixed(3)', MONITOR_QML)

    def test_mesh_map_bed_space_visualisation_is_klipper_faithful(self):
        # The accuracy ruling: the expanded map draws the
        # probed cells within the real bed, extends the BOUNDARY
        # values to the bed edges (Klipper clamps its lookup to the
        # boundary cells — _get_linear_index constrains index and t),
        # and outlines the measured bounds in the Preview's neon
        # orange. The extension is fainter because it is the
        # boundary's continuation, not a measurement.
        for token in ("MoonrakerTheme.neonOrange", "measured ? 0.58 : 0.28", "clampedValue",
                      "bedMeshMachineWidth", "bedMeshMachineDepth", "bedMeshCenterIsZero",
                      "printerToWidget", "constrains both", "hoverClamped",
                      'root.hoverClamped ? MoonrakerTheme.neonOrange'):
            self.assertIn(token, BED_MESH_MAP_QML + MONITOR_MODEL)
        # The popover carries the same clamped disclaimer the Preview's
        # legend makes (the request).
        self.assertIn("Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them", MONITOR_QML)


class MonitorFormattingTests(unittest.TestCase):
    def test_preview_temperature_pair_renders_the_fixed_pair(self):
        # The strip's fixed pair: hotend and bed with the
        # current→target form. target 0.0 = no setpoint — the arrow
        # is omitted; a 0.0 reading on a heater with no target is
        # "—" (Klipper's not-measured convention); missing objects
        # render "—".
        hotend, bed = preview_temperature_pair({
            "extruder": {"temperature": 205.2, "target": 210.0},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
        })
        self.assertEqual(hotend, "205.2 → 210.0 °C")
        self.assertEqual(bed, "60.0 → 60.0 °C")
        hotend, bed = preview_temperature_pair({
            "extruder": {"temperature": 23.4, "target": 0.0},
            "heater_bed": {"temperature": 0.0, "target": 0.0},
        })
        self.assertEqual(hotend, "23.4 °C")
        self.assertEqual(bed, "—")
        hotend, bed = preview_temperature_pair({"extruder": {"temperature": None}})
        self.assertEqual(hotend, "—")
        self.assertEqual(bed, "—")

    def test_layer_and_height_readouts_render_the_printer_side(self):
        # The status bar's cells: the HUMAN layer number and the
        # absolute Z with its unit — '—' while the resolver has
        # nothing. Zero-based indexes shift; None cells read absent.
        layer = PhysicalLayer()
        self.assertEqual(layer_readout(layer), "—")
        self.assertEqual(height_readout(layer), "—")
        layer = replace(layer, index=11, height=12.34)
        self.assertEqual(layer_readout(layer), "12")
        self.assertEqual(height_readout(layer), "12.34 mm")
        layer = replace(layer, total=345)
        self.assertEqual(layer_readout(layer), "12/345")
        layer = replace(layer, index=0, height=0.0, total=None)
        self.assertEqual(layer_readout(layer), "1")
        self.assertEqual(height_readout(layer), "0.00 mm")

    def test_print_job_caption_names_every_state(self):
        # The caption's vocabulary (F18): disconnected and unknown
        # name themselves (never "Idle" while the socket is down), the
        # controls lock names itself so the dead action band keeps its
        # context, and the job state word maps once.
        self.assertEqual(print_job_caption(None), "")
        self.assertEqual(print_job_caption(Observation(active=True, connection="no", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Disconnected")
        self.assertEqual(print_job_caption(Observation(active=True, connection="unknown", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Printer state unknown")
        self.assertEqual(print_job_caption(Observation(active=True, connection="yes", state="printing",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=True, busy=False)),
                         "Locked")
        self.assertEqual(print_job_caption(Observation(active=True, connection="yes", state="printing",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Printing")
        self.assertEqual(print_job_caption(Observation(active=True, connection="yes", state="paused",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Paused")
        self.assertEqual(print_job_caption(Observation(active=True, connection="yes", state="idle",
                                                      homed_axes="", assumed_stopped=False,
                                                      save_config_pending=False, controls_locked=False, busy=False)),
                         "Idle")

    def test_preview_block_carries_the_verdicts_and_the_sentinel(self):
        # The block rides the aux clock: the stamp passes through
        # untouched, the verdicts come from the same policy rows the
        # Dashboard reads (one derivation — the surfaces cannot
        # disagree), and absence is an explicit shape.
        observation = Observation(active=True, connection="yes", state="printing",
                                  homed_axes="xyz", assumed_stopped=False,
                                  save_config_pending=False, controls_locked=False, busy=False,
                                  pause_resume_supported=True)
        block = preview_block({"extruder": {"temperature": 205.2, "target": 210.0}},
                              observation, stamp=12.5)
        self.assertEqual(block["stamp"], 12.5)
        self.assertEqual(block["state"], "printing")
        self.assertTrue(block["canPause"])
        self.assertFalse(block["canResume"])
        self.assertEqual(block["pauseReason"], "")
        self.assertEqual(block["resumeReason"], "Print is not paused")
        self.assertIn("Resume applies to a paused print", block["resumeReasonDetail"])
        self.assertFalse(block["inactive"])
        # The sentinel shape: no observation and no aux — everything
        # reads absent, nothing is omitted.
        block = preview_block({}, None, stamp=0.0, inactive=True)
        self.assertTrue(block["inactive"])
        self.assertFalse(block["canPause"])
        self.assertEqual(block["hotend"], "—")
        self.assertEqual(block["bed"], "—")

    def test_macro_parameter_inference_types_defaults(self):
        definitions = infer_macro_parameters("""
            {% set enabled = params.ENABLED|default(True) %}
            {% set count = params.COUNT|default(5)|int %}
            {% set scale = params.SCALE|default(0.25)|float %}
            {% set label = params.LABEL|default('test') %}
            {% set required = params.REQUIRED|int %}
        """)
        by_name = {item["name"]: item for item in definitions}
        self.assertEqual(by_name["ENABLED"]["type"], "bool")
        self.assertEqual(by_name["ENABLED"]["default"], "True")
        self.assertEqual(by_name["COUNT"]["type"], "int")
        self.assertEqual(by_name["COUNT"]["default"], "5")
        self.assertEqual(by_name["SCALE"]["type"], "float")
        self.assertEqual(by_name["LABEL"]["type"], "string")
        self.assertTrue(by_name["REQUIRED"]["required"])

    def test_monitor_layer_height_reports_current_thickness(self):
        config = SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        layer = LayerResolver().resolve(
            {"print_stats": {"info": {"current_layer": 2, "total_layer": 3}}},
            config,
            metadata={"first_layer_height": 0.2, "layer_height": 0.2},
            heights=(0.2, 0.35, 0.55),
        )
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = SimpleNamespace(
            layer=layer,
            estimated_time=None,
            metadata_complete=False,
        )
        self.assertEqual(core_values(snapshot, physical, True)["monitorLayerHeight"], "0.150 mm")

    def test_monitor_progress_reports_two_decimals(self):
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.655766},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        self.assertEqual(core_values(snapshot, physical, True)["monitorProgress"], 65.58)

    def test_speed_factor_rename_is_mechanically_pinned(self):
        # The 4.2.0 rename: the multiplier row reads "Speed factor"
        # and no plain "Speed" caption survives in the Monitor card
        # (the UX re-review's ask for a mechanical pin).
        self.assertIn('text: "Speed factor"', JOB_SECTION_QML)
        self.assertNotIn('text: "Speed"', MONITOR_QML)

    def test_motion_rows_report_the_live_values(self):
        # The motion cluster (4.2.0): Velocity is Klipper's scalar
        # speed magnitude; Flow rate is the commanded volumetric flow
        # — live_extruder_velocity × π·(d/2)²; Accel limit is the
        # configured ceiling from the aux poll's toolhead.
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": 0.34},
        }, auxiliary={
            "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
        })
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "20.0 mm/s")
        self.assertEqual(values["monitorFlowRate"], "0.8 mm³/s")
        self.assertEqual(values["monitorAccelLimit"], "5000 mm/s²")
        self.assertEqual(values["monitorFlowDiameter"], "1.75 mm")

    def test_motion_rows_read_dashes_without_the_objects(self):
        # "—" only when the printer reports no motion object — the
        # existing core-only snapshots (no auxiliary) must not break.
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "idle", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "—")
        self.assertEqual(values["monitorFlowRate"], "—")
        self.assertEqual(values["monitorAccelLimit"], "—")

    def test_flow_rate_keeps_the_retraction_sign_and_clamps_epsilon(self):
        # A retraction reads negative; a cancellation artifact
        # (-3.6e-15 was caught live) clamps to zero before the sign
        # decision, never "-0.00 mm³/s".
        def values_with(ev):
            snapshot = SimpleNamespace(core={
                "print_stats": {"state": "printing", "print_duration": 30},
                "virtual_sdcard": {"progress": 0.5},
                "gcode_move": {},
                "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": ev},
            }, auxiliary={
                "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
                "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
            })
            physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                       estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
            return core_values(snapshot, physical, True)
        self.assertEqual(values_with(-23.6)["monitorFlowRate"], "-56.8 mm³/s")
        # The display threshold (the re-review): a cancellation
        # artifact (-3.6e-15 was caught live) AND any magnitude that
        # would round to "-0.0" at %.1f clamp to zero.
        self.assertEqual(values_with(-3.552713678800501e-15)["monitorFlowRate"], "0.0 mm³/s")
        self.assertEqual(values_with(-0.01)["monitorFlowRate"], "0.0 mm³/s")

    def test_motion_rows_read_zero_when_idle_and_connected(self):
        # The idle state (the re-review's pin): a connected printer
        # with motion_report present reads 0, never "—".
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "standby", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {"live_velocity": 0.0, "live_extruder_velocity": 0.0},
        }, auxiliary={
            "toolhead": {"extruder": "extruder", "max_accel": 5000.0},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
        })
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorVelocity"], "0.0 mm/s")
        self.assertEqual(values["monitorFlowRate"], "0.0 mm³/s")
        self.assertEqual(values["monitorAccelLimit"], "5000 mm/s²")

    def test_flow_rate_uses_the_active_tools_diameter_only(self):
        # Per-tool: the ACTIVE tool's section supplies the diameter;
        # [extruder_stepper] sections never match (the prefix-sweep
        # hazard), and the tool name is validated before the lookup.
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {"live_velocity": 20.0, "live_extruder_velocity": 0.5},
        }, auxiliary={
            "toolhead": {"extruder": "extruder1", "max_accel": 5000.0},
            "configfile": {"settings": {
                "extruder": {"filament_diameter": 1.75},
                "extruder1": {"filament_diameter": 2.85},
                "extruder_stepper main": {"foo": 1},
            }},
        })
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
                                   estimated_time=None, metadata_complete=False, layer_eta=None, layer_progress=None)
        values = core_values(snapshot, physical, True)
        # 0.5 × π × (2.85/2)² = 3.19 mm³/s, from extruder1's diameter.
        self.assertEqual(values["monitorFlowDiameter"], "2.85 mm")
        self.assertEqual(values["monitorFlowRate"], "3.2 mm³/s")

    def test_layer_progress_comes_from_the_snapshot_byte_fraction(self):
        # The within-layer fraction comes from the index's byte ranges
        # (the nozzle's Z never moves within a layer, so Z cannot
        # express it); without an index the UI hides the bar.
        from plugins.PrintState import PhysicalLayer
        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 30},
            "virtual_sdcard": {"progress": 0.5},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = SimpleNamespace(layer=PhysicalLayer(1, 10), estimated_time=None,
                                   metadata_complete=False, layer_eta=None, layer_progress=0.5)
        self.assertAlmostEqual(core_values(snapshot, physical, True)["monitorLayerProgress"], 0.5, places=6)
        physical = SimpleNamespace(layer=PhysicalLayer(1, 10), estimated_time=None,
                                   metadata_complete=False, layer_eta=None, layer_progress=None)
        self.assertEqual(core_values(snapshot, physical, True)["monitorLayerProgress"], -1.0)

    def test_eta_prefers_slicer_time_for_early_and_resumed_prints(self):
        self.assertAlmostEqual(estimate_remaining(3600, 0.02, 7 * 3600, True), 6 * 3600, delta=1)
        self.assertAlmostEqual(estimate_remaining(3 * 3600, 0.10, 7 * 3600, True), 4 * 3600, delta=1)
        self.assertIsNone(estimate_remaining(120, 0.50, None, False))

    def test_eta_appears_as_soon_as_moonraker_reports_a_little_progress(self):
        # The connect-time expectation: the unoptimised blend
        # shows up with only a small progress signal, not a minute into
        # the print (the old 60 s / 2% floor left the readout empty).
        self.assertAlmostEqual(estimate_remaining(15, 0.01, None, True), 1485, delta=1)
        self.assertIsNone(estimate_remaining(5, 0.01, None, True))       # too early
        self.assertIsNone(estimate_remaining(15, 0.001, None, True))    # no progress signal

    def test_layer_resolver_bounds_the_estimate_by_the_total_print_height(self):
        # An underestimated step (noise, a mis-cancelled hop) must not
        # claim a layer above the object's own height.
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        resolver = LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5}}
        for z, e in ((9.8, 1.0), (9.8, 2.0), (9.9, 3.0), (9.9, 4.0)):
            status["gcode_move"] = {"gcode_position": [100, 100, z, e]}
            resolver.resolve(status, config, metadata={"object_height": 10})
        # step measured as 0.1 (the only ascent) -> round((9.9-0.1)/0.1)
        # = 98 layers, inside the 100-layer object-height bound.
        layer = resolver.resolve(status, config, metadata={"object_height": 10})
        self.assertEqual(layer.index, 98)

    def test_layer_resolver_measures_the_layer_height_when_the_header_never_says(self):
        # Some slicer headers declare no layer height at all; the
        # resolver measures it from the observed Z increments (layer
        # changes dominate positive deltas while extrusion advances).
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        resolver = LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5}}
        for z, e in ((0.3, 1.0), (0.3, 2.0), (0.5, 3.0), (0.5, 4.0), (0.7, 5.0), (0.7, 6.0)):
            status["gcode_move"] = {"gcode_position": [100, 100, z, e]}
            resolver.resolve(status, config, metadata={})
        layer = resolver.resolve(status, config, metadata={})
        self.assertEqual(layer.index, 2)  # step measured as 0.2, first assumed 0.2
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_seeds_a_mid_print_connect_and_heals_a_wipe_seed(self):
        # The trace: a mid-print connect at z=9.75 and 65%
        # progress reported no current_layer. At significant progress
        # the position IS the real print height — seed freely — and a
        # wipe above the print self-heals when the nozzle descends
        # (printing never descends, so a lower candidate is the
        # correction; three consecutive observations avoid boundary
        # jitter oscillation).
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        metadata = {"layer_height": 0.2, "first_layer_height": 0.3}
        resolver = LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5},
                  "gcode_move": {"gcode_position": [100, 100, 10, 1.0]}}
        self.assertIsNone(resolver.resolve(status, config, metadata=metadata).index)  # baseline only
        status["gcode_move"] = {"gcode_position": [100, 100, 9.75, 2.0]}
        self.assertEqual(resolver.resolve(status, config, metadata=metadata).index, 47)  # mid-print height seeds
        for e in (3.0, 4.0, 5.0):
            status["gcode_move"] = {"gcode_position": [100, 100, 0.3, e]}
            resolver.resolve(status, config, metadata=metadata)
        layer = resolver.resolve(status, config, metadata=metadata)
        self.assertEqual(layer.index, 0)  # the descent corrected the wipe seed
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_ignores_the_parked_z_before_the_file_starts(self):
        # The Z estimate must not seed from the parked height (z=10
        # while heating used to read as "Layer 50" for the whole
        # print); it engages only once the virtual SD has started.
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        metadata = {"layer_height": 0.2, "first_layer_height": 0.3}
        resolver = LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.0},
                  "gcode_move": {"gcode_position": [100, 100, 10, 0.0]}}
        self.assertEqual(resolver.resolve(status, config, metadata=metadata).source, "print start")
        # The file starts at the first-layer height and extrusion
        # advances: the estimate tracks from there.
        status["virtual_sdcard"] = {"progress": 0.05}
        status["gcode_move"] = {"gcode_position": [100, 100, 0.3, 0.5]}
        layer = resolver.resolve(status, config, metadata=metadata)
        self.assertEqual(layer.index, 0)
        self.assertEqual(layer.source, "extrusion-guarded Z height")

    def test_layer_resolver_does_not_claim_layer_1_for_a_stuck_current_layer(self):
        # A printer whose G-code never emits per-layer stats reports
        # current_layer=0 forever; past ~3% progress claiming "Layer 1"
        # would be a stuck lie — the honest value is None.
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=False, z_tolerance=0.05)
        stuck = LayerResolver().resolve(
            {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
             "virtual_sdcard": {"progress": 0.5}}, config)
        self.assertIsNone(stuck.index)

    def test_layer_resolver_shows_layer_1_at_print_start(self):
        # An ACTIVE print at current_layer=0 (before the first
        # SET_PRINT_STATS_INFO) reads as layer 1 — values must show
        # as soon as Moonraker reports them, not "—" until the
        # print advances a layer.
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=False, z_tolerance=0.05)
        active = LayerResolver().resolve(
            {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}}}, config)
        self.assertEqual(active.index, 0)
        self.assertEqual(active.source, "print start")
        idle = LayerResolver().resolve(
            {"print_stats": {"state": "standby", "info": {"current_layer": 0, "total_layer": 12}}}, config)
        self.assertIsNone(idle.index)  # pre-print suppression stays

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        self.assertEqual(parse_bed_mesh(None), {})
        for matrix, bounds in (([[0, 1], [2]], [0, 0]), ([[0, float("nan")], [1, 2]], [0, 0]), ([[0, 1], [1, 2]], [2, 0])):
            self.assertEqual(parse_bed_mesh({"mesh_matrix": matrix, "mesh_min": bounds, "mesh_max": [1, 1]}), {})
        self.assertEqual(parse_mcu_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123"), {"mcu_awake": 0.02, "bytes_read": 123.0})

    def test_file_row_payload_carries_the_folder_breadcrumb(self):
        # The search face shows the folder under the name (the
        # live request: same-named files in different
        # folders must be tellable); root-level files carry "".
        row = SimpleNamespace(filename="a.gcode", relpath="prints/sub/a.gcode", modified=None,
                              size=None, attempts=None, last_status=None, print_start_time=None,
                              object_height=None, layer_height=None, estimated_time=None,
                              last_print=None, slicer=None, extruder=None, bed=None, filament=None)
        self.assertEqual(file_row_payload(row, 0)["folder"], "prints/sub")
        row.relpath = "a.gcode"
        self.assertEqual(file_row_payload(row, 0)["folder"], "")


class MonitorPolicyConsistencyTests(unittest.TestCase):
    """Behavior constants restated as QML prose must not drift."""

    def test_qml_prose_matches_policy_constants(self):
        import re as _re
        tuning = (PLUGINS / "MonitorTuning.py").read_text(encoding="utf-8")
        debounce = int(_re.search(r"DEBOUNCE_MS\s*=\s*(\d+)", tuning).group(1))
        self.assertEqual(debounce, 250)
        window = f"unchanged for {debounce} ms" if debounce < 1000 else f"unchanged for {debounce // 1000} seconds"
        self.assertIn(window, TUNING_SECTION_QML)

        commands = (PLUGINS / "MonitorCommands.py").read_text(encoding="utf-8")
        click_window = int(_re.search(r"_reset_timer\.setInterval\((\d+)\)", commands).group(1))
        self.assertEqual(click_window, 1000)
        # The helper prose that restated the arm-reset window was
        # removed by request; the constant lives in the
        # code alone now.

        follow = (PLUGINS / "FollowController.py").read_text(encoding="utf-8")
        radius = int(_re.search(r"window_radius: int = (\d+)", follow).group(1))
        self.assertIn(f"(±{radius})", (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text(encoding="utf-8"))

    def test_classification_table_is_the_single_object_policy(self):
        from plugins.MonitorFormatting import object_kind, wanted_object
        self.assertEqual(object_kind("fan"), "system")
        self.assertEqual(object_kind("heater_bed"), "system")
        self.assertEqual(object_kind("gcode_macro START_PRINT"), "macro")
        self.assertEqual(object_kind("fan_generic Chamber"), "fan")
        self.assertEqual(object_kind("heater_fan hotend"), "fan")
        self.assertEqual(object_kind("neopixel case"), "led")
        self.assertEqual(object_kind("output_pin pwm1"), "pwm")
        self.assertEqual(object_kind("heater_generic chamber"), "temperature")
        self.assertEqual(object_kind("temperature_sensor board"), "temperature")
        self.assertEqual(object_kind("filament_switch_sensor runout"), "filament")
        self.assertEqual(object_kind("mcu rpi"), "mcu")
        self.assertEqual(object_kind("unknown object"), "")
        self.assertTrue(wanted_object("fan"))
        self.assertTrue(wanted_object("heater_fan hotend"))
        self.assertFalse(wanted_object("gcode_macro START_PRINT"))
        self.assertFalse(wanted_object("unknown object"))

    def test_fan_writability_follows_the_klipper_fan_types(self):
        # The live reports (controller_fan1, hotend_fan):
        # Klipper's controller_fan, temperature_fan and heater_fan are
        # temperature-regulated — SET_FAN_SPEED never sticks — so
        # their rows render read-only. The other fan types register
        # the command.
        from plugins.MonitorFormatting import fan_writable
        for name in ("fan", "fan_generic nevermore"):
            self.assertTrue(fan_writable(name), name)
        for name in ("controller_fan controller_fan1", "controller_fan controller_fan2",
                     "temperature_fan chamber", "heater_fan hotend_fan"):
            self.assertFalse(fan_writable(name), name)
        # The dashboard renders the read-only row instead of a slider,
        # and the command lane refuses the regulated fans fail-closed.
        controls = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn('"writable": fan_writable(name)', controls)
        self.assertIn("if not fan_writable(name):", controls)
        self.assertIn("visible: modelData.writable", FANS_SECTION_QML)
        self.assertIn("Firmware-controlled — speed is read-only", FANS_SECTION_QML)

    def test_led_channels_are_absolute_and_the_labels_hold_their_width(self):
        # A live report: the chroma normalisation made
        # every nudge re-scale all four channel sliders (a +1 nudge
        # of a zeroed channel jumped it to 100 and dragged the rest).
        # The sliders now read absolute channel values, and the
        # percentage labels hold a fixed width so the rows never
        # reflow.
        controls = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn("ABSOLUTE channels", controls)
        # The chroma normalisation's code is gone: no peak division
        # remains in the channel derivation.
        self.assertNotIn("* 100 / peak", controls)
        self.assertIn("round(c * 100) for c in channels", controls)
        # The channel commit sends the absolutes WITHOUT the
        # brightness slider as a gain — passing it zeroed every
        # channel nudge while the LED was off (a live
        # report).
        self.assertIn("whiteSlider.selectedValue() : 0, -1);", LEDS_SECTION_QML)
        # The brightness slider is the USER'S GAIN, unlinked from the
        # channel peak (the ruling): the channel sliders
        # hold the set percentages (seeded once from the first-seen
        # colour), the gain composes into the SEND only, and neither
        # slider's value moves the other.
        self.assertIn("self._remembered_gain", controls)
        self.assertIn("self._remembered_channels", controls)
        self.assertIn("round(gain * 100)", controls)
        self.assertIn('self._remembered_gain[name] = percent / 100.0', controls)
        self.assertIn("self._remembered_gain.get(name, 1.0)", controls)
        self.assertEqual(DASHBOARD_QML.count("width: 52 * screenScaleFactor"), 0)
        self.assertGreaterEqual(TUNING_SECTION_QML.count("width: 52 * screenScaleFactor"), 2)
        self.assertGreaterEqual(FANS_SECTION_QML.count("width: 52 * screenScaleFactor"), 1)
        self.assertGreaterEqual(LEDS_SECTION_QML.count("width: 52 * screenScaleFactor"), 4)
        self.assertGreaterEqual(PWM_SECTION_QML.count("width: 52 * screenScaleFactor"), 1)
        self.assertIn("width: 150 * screenScaleFactor", LEDS_SECTION_QML)
        # The submit's rebuild must not kill the tuned slider's focus
        # (a live report): the dashboard remembers the
        # slider's object and re-grants focus on the new delegate.
        outline = (PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertIn("property string controlObject", outline)
        # One LED row holds five sliders: the refocus must land on the
        # RIGHT one — the kind discriminates, and the walk recurses
        # into the channel grid (a live report: the nudge
        # went to the brightness slider instead of the channel).
        self.assertIn("property string controlKind", outline)
        self.assertIn("function focusSliderIn", DASHBOARD_QML)
        self.assertIn("function focusTuningSliderOnce()", DASHBOARD_QML)
        # The refocus retries until it lands and holds across the
        # confirm-time rebuild (a live report: fan/LED
        # sliders lost focus on the apply, the singletons never).
        self.assertIn("refocusTimer.attempts = 0", DASHBOARD_QML)
        self.assertIn("focusHoldTimer.start()", DASHBOARD_QML)
        for token in ("refocusTimer.start()",):
            self.assertIn(token, DASHBOARD_QML)
        self.assertNotIn("root.tuningSliderObject = modelData.object", DASHBOARD_QML)
        self.assertIn("id: fanRepeater", FANS_SECTION_QML)
        self.assertIn("id: ledRepeater", LEDS_SECTION_QML)
        self.assertIn("id: pwmRepeater", PWM_SECTION_QML)
        self.assertIn('controlKind: "pwm"', PWM_SECTION_QML)
        for kind in ("led-red", "led-brightness", "led-green", "led-blue", "led-white"):
            self.assertIn('controlKind: "%s"' % kind, LEDS_SECTION_QML)

    def test_consumers_use_the_shared_classification_tables(self):
        data = (PLUGINS / "MonitorData.py").read_text(encoding="utf-8")
        controls = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn("wanted_object", data)
        self.assertIn("FAN_OBJECT_PREFIXES", controls)
        self.assertIn("LED_OBJECT_PREFIXES", controls)
        self.assertIn("PWM_OBJECT_PREFIXES", controls)
        self.assertNotIn("neopixel ", data)
        self.assertNotIn("fan_generic ", data)


class EndstopAndEtaBasisTests(unittest.TestCase):
    def test_endstop_values_projects_axes_and_the_not_homed_state(self):
        from plugins.MonitorFormatting import endstop_values
        snapshot = SimpleNamespace(endstops={"x": "TRIGGERED", "y": "open", "z": "open"})
        items, summary = endstop_values(snapshot)["endstopItems"], endstop_values(snapshot)["endstopSummary"]
        self.assertEqual([(item["name"], item["state"], item["triggered"]) for item in items],
                         [("X", "TRIGGERED", True), ("Y", "open", False), ("Z", "open", False)])
        self.assertEqual(summary, "")
        empty = SimpleNamespace(endstops={})
        self.assertEqual(endstop_values(empty)["endstopItems"], [])
        self.assertIn("No endstop states reported", endstop_values(empty)["endstopSummary"])

    def test_core_values_prefers_the_layer_anchored_eta_and_names_the_basis(self):
        from plugins.MonitorFormatting import core_values
        snapshot = SimpleNamespace(core={"print_stats": {"state": "printing", "print_duration": 30},
                                        "virtual_sdcard": {"progress": 0.1}},
                                   auxiliary={}, server={})
        physical = SimpleNamespace(layer=SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=600.0, metadata_complete=True, layer_eta=420.0)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorEtaBasis"], "index")
        self.assertEqual(values["monitorEta"], "00:07:00")
        physical = SimpleNamespace(layer=SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=600.0, metadata_complete=True, layer_eta=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorEtaBasis"], "blend")

    def test_core_values_reports_filament_used_and_remaining(self):
        from plugins.MonitorFormatting import core_values
        # Real Klipper shape (Status_Reference + klippy/print_stats.py):
        # filament_used is a TOP-LEVEL print_stats field, always present
        # while printing; the info dict only ever carries the layer
        # counters a slicer's SET_PRINT_STATS_INFO wrote. The slicer
        # total rides the COORDINATOR snapshot (the physical arg); the
        # monitor snapshot has no such field.
        snapshot = SimpleNamespace(core={"print_stats": {"state": "printing", "print_duration": 30,
                                                         "filament_used": 3500.0,
                                                         "info": {"current_layer": 1, "total_layer": 20}},
                                        "virtual_sdcard": {}},
                                   auxiliary={}, server={})
        physical = SimpleNamespace(layer=SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None,
                                   filament_total=42000.0)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "3.50 m")
        self.assertEqual(values["filamentRemaining"], "38.50 m")
        # Without the metadata total the remaining length is honest "—".
        physical.filament_total = None
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentRemaining"], "—")
        # Without the polled used length both read "—".
        snapshot.core = {"print_stats": {"state": "printing"}}
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "—")
        # The legacy shapes still parse (info-dict used, monitor-
        # snapshot total) — they are fallbacks, not the live path.
        snapshot.core = {"print_stats": {"state": "printing",
                                         "info": {"filament_used": 1200.0}}}
        snapshot.filament_total = 42000.0
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "1.20 m")
        self.assertEqual(values["filamentRemaining"], "40.80 m")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MonitorQtTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=ScriptedSocket())):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def feed_chart(self, model, auxiliary):
        """The chart's feed path (the 4.6.0 decoupling): the fixed 1 s
        tick samples the latest aux snapshot while connected — aux
        arrivals alone never feed the history."""
        self.deliver()  # the client's connect transition
        model._data._update(auxiliary=auxiliary)
        model._on_chart_tick()
        self.qt.events()  # the publish coalescer flushes on the next turn

    def stored_transcript(self):
        """The persisted transcript's home (4.5.0): the per-machine
        state shard under the persistence folder."""
        machine_id = self.follower.current_printer_identity()[0]
        shard = self.follower.persistence.get_machine_state(machine_id) or {}
        return shard.get("consoleTranscript", [])

    def deliver(self):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

    def deliver_state(self, state):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": state, "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        import time
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

    def scripts(self):
        return [r for r in self.transport.requests if r.path == "printer/gcode/script"]

    def test_improve_eta_hourglass_survives_the_registration_gap(self):
        # The red-run catch: improveEta publishes its own flag-set,
        # and the coordinator's load_active flips only on its NEXT
        # snapshot rebuild — the improve's own publish must not clear
        # the hourglass with the stale snapshot (the flag cleared
        # instantly and the improve silently no-opped whenever a
        # previous load's tail was not still holding load_active set).
        model = self.monitor()
        self.deliver()
        model.improveEta()
        self.assertTrue(model.improvingEta,
                        "the hourglass must survive the improve's own publish")

    def test_toolhead_slots_send_exact_scripts(self):
        model = self.monitor()
        self.deliver_state("standby")
        self.assertTrue(model.jogEnabled)
        self.assertEqual(model.positionMode, "Absolute")
        model.setJogDistance(10)
        self.assertEqual(model.jogDistance, 10.0)

        def next_script(action, *args):
            before = len(self.scripts())
            action(*args)
            self.qt.events(10)
            scripts = self.scripts()
            self.assertEqual(len(scripts), before + 1)
            scripts[-1].callback({}, None)
            self.qt.events(10)
            return scripts[-1].options["body"]
        self.assertEqual(next_script(model.jog, "x", 1), {"script": "G91\nG1 X10 F3000\nG90"})
        # The Z jog from 0.4 by -10 crosses zero with no configured
        # minimum: forbidden outright (a live report —
        # the head must never microstep below 0.00 Z).
        before = len(self.scripts())
        model.jog("z", -1)
        self.qt.events(10)
        self.assertEqual(len(self.scripts()), before)
        self.assertEqual(next_script(model.home, "y"), {"script": "G28 Y"})
        self.assertEqual(next_script(model.home, ""), {"script": "G28"})
        self.assertEqual(next_script(model.motorsOff), {"script": "M18"})
        # Extrude uses the configured distance and speed (defaults 5 mm, 5 mm/s).
        self.assertEqual(next_script(model.extrude, 1), {"script": "G91\nG1 E5 F300\nG90"})
        self.assertEqual(next_script(model.extrude, -1), {"script": "G91\nG1 E-5 F300\nG90"})
        model.setExtrudeDistance(10)
        model.setExtrudeSpeed(120)
        self.assertEqual(next_script(model.extrude, 1), {"script": "G91\nG1 E10 F120\nG90"})
        model.setExtrudeDistance(10)
        model.setExtrudeSpeed(300)
        model.setJogDistance(42.5)
        self.assertEqual(next_script(model.jog, "x", 1), {"script": "G91\nG1 X42.5 F3000\nG90"})
        model.setJogDistance(10)

    def test_negative_free_text_distances_are_rejected(self):
        # The free-text fields hold magnitudes; the buttons carry the
        # direction. A negative entry must never invert the arrows.
        model = self.monitor()
        self.deliver_state("standby")
        model.setJogDistance(10)
        model.setJogDistance(-25)
        self.assertEqual(model.jogDistance, 10.0)
        model.setExtrudeDistance(-5)
        self.assertEqual(model.extrudeDistance, 5.0)
        model.setExtrudeSpeed(-120)
        self.assertEqual(model.extrudeSpeed, 300.0)
        model.jog("x", 1)
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X10 F3000\nG90"})

    def test_toolhead_guard_releases_and_polls_do_not_rearm_it(self):
        # The guard drops the poll floor while moves run and for a short
        # settle afterwards. Polls arriving during the settle must NOT
        # re-arm the cooldown — at poll cadence that would make the
        # release unreachable and hold the urgent floor forever.
        model = self.monitor()
        self.deliver_state("standby")
        model._toolhead._guard_cooldown.setInterval(500)
        model.setJogDistance(1)
        model.jog("x", 1)
        client = self.follower.client
        self.assertTrue(client._session.toolhead_guard)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        scripts[0].callback({}, None)
        self.qt.events(10)
        for _ in range(3):
            self.deliver_state("standby")
            self.qt.events(10)
        self.assertTrue(client._session.toolhead_guard)  # settling, not re-armed
        self.qt.events(700)
        self.assertFalse(client._session.toolhead_guard)  # released on schedule

    def test_jog_keeps_its_place_behind_queued_one_shots(self):
        # The toolhead sends on the shared command lane. A jog tapped
        # while one-shots are queued must run AFTER them, not jump the
        # queue when the in-flight command completes.
        model = self.monitor()
        self.deliver_state("standby")
        model.setJogDistance(1)
        model.jog("x", 1)          # in flight on the lane
        model.homeAll()            # queues behind the in-flight jog
        model.jog("y", 1)          # waits in the toolhead queue
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({}, None)  # jog X completes; Home must go next
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual([s.options["body"]["script"] for s in scripts[-2:]],
                         ["G91\nG1 X1 F3000\nG90", "G28"])
        scripts[-1].callback({}, None)  # Home completes; now the Y jog runs
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(scripts[-1].options["body"],
                         {"script": "G91\nG1 Y1 F3000\nG90"})

    def test_endpoint_change_invalidates_subscribers_once(self):
        # PrinterBinding tears the poller down silently before the rebind;
        # the client's configure emits exactly one invalidation wave on
        # the old identity.
        client = self.follower.client
        waves = []
        client.sessionInvalidated.connect(lambda: waves.append(True))
        self.follower.apply_printer_config(
            self.config_type(url="http://printer-b", api_key="new-key", path_follow=False))
        self.qt.events(10)
        self.assertEqual(len(waves), 1)

    def test_pause_first_jog_waits_for_paused_confirmation_then_drains(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.assertFalse(model.jogEnabled)  # moves need an explicit pause first
        model.setJogDistance(1)
        model.jog("x", 1)
        model.jog("x", 1)  # queues as its own move (no coalescing)
        pauses = [r for r in self.transport.requests if r.path == "printer/print/pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(self.scripts(), [])
        self.assertIn("Waiting for the printer to pause", model.jogStatus)
        # The harness must ack the pause HTTP request: acceptance precedes
        # state confirmation, exactly as in production.
        pauses[0].callback({}, None)
        self.deliver_state("paused")
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # Each queued move drains on its own completion cycle (the
        # no-coalescing ruling: the queue holds separate ops).
        scripts[0].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[1].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        self.assertEqual(model.jogStatus, "")

    def test_pause_timeout_drops_queued_jogs(self):
        controller = self.qt.load("ToolheadController")
        with patch.object(controller, "PAUSE_WAIT_TIMEOUT_S", 0.05):
            model = self.monitor()
            self.deliver_state("printing")
            model.jog("x", 1)
            self.qt.events(200)
        self.assertEqual(self.scripts(), [])
        self.assertIn("did not pause", model.jogStatus)

    def test_resume_during_drain_drops_remaining_moves(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.setJogDistance(1)
        model.jog("x", 1)
        model.jog("y", 1)  # different axis: two distinct ops
        pauses = [r for r in self.transport.requests if r.path == "printer/print/pause"]
        pauses[0].callback({}, None)  # HTTP acceptance before state confirmation
        self.deliver_state("paused")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)  # the first op drains
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # The print resumes before the first move completes: the remaining
        # move must be dropped, never force-executed mid-print.
        self.deliver_state("printing")
        self.assertEqual(self.scripts(), scripts)
        self.assertIn("resumed", model.jogStatus)

    def test_emergency_stop_clears_queued_scripts_and_busy(self):
        model = self.monitor()
        self.deliver_state("standby")
        commands = model._commands
        commands.script("Home", "G28")
        commands.script("QGL", "QUAD_GANTRY_LEVEL")  # queued behind the in-flight send
        self.assertEqual(len(commands._queue), 1)
        model.emergencyStopClick()
        model.emergencyStopClick()
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(commands._queue, [])
        self.assertFalse(commands.busy)
        self.assertFalse(model.actionBusy)  # power toggles and restarts unlock

    def test_early_release_cancels_the_hold(self):
        model = self.monitor()
        self.deliver_state("standby")
        model.emergencyStopClick()
        model.emergencyStopClick()
        model.emergencyHoldStarted()
        model.emergencyHoldReleased()
        self.qt.events(200)
        self.assertEqual([r for r in self.transport.requests if r.channel == "emergency-stop"], [])
        # The arm persists: a second, completed hold fires.
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(sum(r.channel == "emergency-stop" for r in self.transport.requests), 1)

    def test_emergency_stop_during_a_print_releases_the_print_guards(self):
        # A live report: an e-stop mid-print left the
        # "disabled during print" guards up and the printer
        # unrecoverable. The stop ASSUMES the print was cancelled:
        # the snapshot-derived guards release immediately, and the
        # snapshot's real state supersedes the assumption when it
        # lands.
        model = self.monitor()
        self.deliver_state("printing")
        self.assertTrue(model.printActive)
        self.assertFalse(model.jogEnabled)
        model._commands._clicks = 2
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        estops = [r for r in self.transport.requests if "emergency_stop" in r.path]
        estops[-1].callback({}, None)
        self.qt.events(10)
        # The snapshot still says printing — the ASSUMPTION releases
        # the guards. (The automatic reconnect lands on its own 1.5 s
        # delay, outside this window — its own test below.)
        self.assertFalse(model.printActive)
        self.assertTrue(model.jogEnabled)
        self.assertFalse(model.canPausePrint)
        # The printer's real state supersedes the assumption (the
        # client-level flag clears when the observation lands).
        self.deliver_state("standby")
        self.assertFalse(self.follower.client._session.state.assume_print_stopped)
        self.assertFalse(model.printActive)

    def test_queued_restart_revalidates_at_dispatch(self):
        # 4.2.0 N1: a restart queued behind an in-flight command was
        # valid when clicked; a print starting in the window must
        # drop it at dispatch, with the policy's reason.
        model = self.monitor()
        self.deliver_state("standby")
        model.jog("x", 1)  # in flight on the lane
        model.klipperRestart()  # a valid click (standby), queued
        self.deliver_state("printing")
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        scripts[0].callback({}, None)  # the jog completes; the pump runs
        self.qt.events(20)
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/restart"], [])
        self.assertIn("Klipper restart cancelled", model.actionStatus)

    def test_print_start_dispatch_gate_refuses_a_print_that_started(self):
        # 4.2.0 S3/N2: the dialog's click was valid when it opened;
        # the dispatch re-checks — a print running by confirm time
        # refuses with the policy's reason, nothing reaches the wire.
        model = self.monitor()
        self.deliver_state("standby")
        model._file_print_confirm = {"relpath": "part.gcode"}
        self.deliver_state("printing")
        model.fileConfirmPrint()
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/print/start"], [])
        self.assertIn("Print start refused", model.actionStatus)

    def test_emergency_stop_reconnects_once_automatically(self):
        # The ruling (2026-09-10, live-proven on a real
        # printer): after the stop the host refuses commands until
        # the connection is cycled — the plugin cycles the client
        # once (stop + start = two generation bumps) and the monitor
        # comes back armed, reading a fresh status.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(10)
        generation_before = self.follower.client._generation
        commands_module = self.qt.load("MonitorCommands")
        with patch.object(commands_module.MonitorCommands, "RECONNECT_DELAY_MS", 0):
            model._commands._clicks = 2
            model._commands._hold_timer.setInterval(30)
            model.emergencyHoldStarted()
            self.qt.events(100)
        self.assertEqual(self.follower.client._generation, generation_before + 2)
        self.assertTrue(model._data.active)
        self.deliver_state("standby")
        self.qt.events(10)
        self.assertTrue(model.jogEnabled)

    def test_emergency_stop_ignores_the_pre_stop_command_reply(self):
        # A live request: after the stop the plugin
        # assumes the print was cancelled — the in-flight command's
        # terminal reply must not overwrite "Emergency stop issued".
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        model._commands._clicks = 2
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        estops = [r for r in self.transport.requests if "emergency_stop" in r.path]
        self.assertEqual(len(estops), 1)
        estops[-1].callback({}, None)
        self.qt.events(10)
        self.assertIn("Emergency stop", model.actionStatus)
        # The pre-stop command's reply lands late: it must be ignored.
        scripts[0].callback({}, None)
        self.qt.events(10)
        self.assertIn("Emergency stop", model.actionStatus)
        self.assertNotIn("TEST_MACRO", model.actionStatus)

    def test_rapid_z_nudges_cannot_walk_the_head_below_zero(self):
        # A live report: nudge taps outran the poll, each
        # clamped against the STALE position, and the queue walked
        # the head below 0.00 Z. The client-side estimate advances
        # per accepted move: four 0.1 nudges from 0.4 land at zero;
        # the fifth is forbidden outright.
        model = self.monitor()
        self.deliver_state("standby")  # live_position z = 0.4
        model.setJogDistance(0.1)
        for _ in range(5):
            model.jog("z", -1)
        self.qt.events(10)
        # Four nudges are accepted (0.4 → 0.0); the fifth is
        # forbidden by the estimate — the head never goes below zero.
        # (The drain merges queued taps, so the script COUNT is not
        # pinned; the estimate is the guard.)
        self.assertAlmostEqual(model._toolhead._z_estimate, 0.0)
        z_scripts = [r for r in self.scripts() if "G1 Z" in str(r.options.get("body"))]
        self.assertGreaterEqual(len(z_scripts), 1)

    def test_unchanged_projections_are_not_rebuilt_on_heartbeats(self):
        # I (the 2026-09-19 performance review): the console's
        # transcript projection, the controls' deep copy and the
        # peripheral scan all rebuild only when their inputs actually
        # changed — an unchanged heartbeat serves the same objects.
        from unittest.mock import patch
        model = self.monitor()
        self.qt.events(1)
        # Warm-up: the connection transition's note is a REAL
        # transcript change and must land before the window.
        self.deliver_state("standby")
        self.qt.events(1)
        console_first = model._console.values
        controls_first = model._controls.values
        module = self.qt.load("MoonrakerMonitorModel")
        scans = []
        original = module.peripheral_values
        def counting(snapshot):
            scans.append(1)
            return original(snapshot)
        with patch.object(module, "peripheral_values", counting):
            for _ in range(5):
                self.deliver_state("standby")
                self.qt.events(1)
            self.assertEqual(scans, [], "core-only landings must not rescan peripherals")
            model._data._merge_aux({"extruder": {"temperature": 200.0, "target": 210.0}})
            self.qt.events(1)
            self.assertEqual(len(scans), 1, "an aux landing rescans exactly once")
        self.assertIs(console_first["consoleLines"], model._console.values["consoleLines"],
                      "an unchanged transcript must keep its projection")
        self.assertIs(controls_first, model._controls.values,
                      "unchanged controls must keep their copy")

    def test_stale_polls_cannot_raise_the_z_projection_between_dispatched_moves(self):
        # B (the 2026-09-19 review): an op leaves the queue at
        # dispatch, so a stale poll between the dispatch and the
        # physical move's reflection re-synced the projection upward
        # and every following tap was accepted against the old
        # position again — repeated stale polls could walk the
        # accepted downward distance past the 0.40 mm of real
        # headroom.
        model = self.monitor()
        self.deliver_state("standby")  # live_position z = 0.4
        model.setJogDistance(0.1)
        for _ in range(4):
            model.jog("z", -1)
            self.qt.events(1)
            # The stale poll: the head has not moved yet, the report
            # still reads 0.40.
            self.deliver_state("standby")
            self.qt.events(1)
        # Four accepted moves cover exactly the 0.40 headroom; the
        # fifth must be forbidden — the projection floors at zero
        # instead of re-arming against each stale poll.
        model.jog("z", -1)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._z_estimate, 0.0)
        self.assertIn("rejected", model._toolhead._status)

    def test_z_projection_follows_fresh_telemetry_and_upward_motion(self):
        # B's catch-up half: once the poll reports the commanded
        # level (or below), the projection adopts the truth again —
        # and an upward jog followed by a downward one tracks both
        # ways instead of freezing at a stale floor.
        model = self.monitor()
        self.deliver_state("standby")  # z = 0.4
        model.setJogDistance(0.1)
        model.jog("z", -1)  # projection 0.3
        self.qt.events(1)
        def deliver_z(z):
            import time
            status = {"print_stats": {"state": "standby"},
                      "gcode_move": {"gcode_position": [0, 0, z, 0]},
                      "motion_report": {"live_position": [0, 0, z, 0]}}
            self.follower.client._handle_http_status({"result": {"status": status}}, None,
                                                     self.follower.client._generation, time.monotonic())
        deliver_z(0.3)  # the head arrived: the poll adopts
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._z_estimate, 0.3)
        model.jog("z", 1)  # upward: projection 0.4
        self.qt.events(1)
        deliver_z(0.4)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._z_estimate, 0.4)
        model.jog("z", -1)  # downward again: projection 0.3
        self.qt.events(1)
        deliver_z(0.3)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._z_estimate, 0.3)

    def test_emergency_stop_clears_pending_jog_queue(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.jog("x", 1)  # pause-first cycle starts; the pause holds busy
        model.emergencyStopClick()
        model.emergencyStopClick()
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(model._toolhead._pending, ())
        self.assertFalse(model._toolhead._pause_waiting)
        self.assertFalse(model._toolhead._pause_in_flight)
        self.assertFalse(model.actionBusy)

    def test_command_reply_errors_are_reported_as_outcome_unknown(self):
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        # A timed-out reply must not claim the command failed: the printer
        # may well have executed it.
        scripts[0].callback(None, "Operation canceled")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("outcome unknown", model.actionStatus)

    def test_extrude_refusal_reads_the_servers_words_in_the_status(self):
        # A live report: a cold extrude showed a bare 400
        # in the Printer status field. The REAL toolhead path — the
        # extrude rides the shared commands lane — must surface the
        # server's words, not a status code.
        model = self.monitor()
        self.deliver_state("standby")
        model.setExtrudeDistance(5)
        model.extrude(1)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"code": 400, "message": "Unknown",
                             "traceback": "... HTTPError: HTTP 400: Extrude below minimum temp\n"
                                          "See the 'min_extrude_temp' config option for details"},
                            "Extrude below minimum temp")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("refused", model.actionStatus)
        self.assertIn("Extrude below minimum temp", model.actionStatus)
        self.assertNotIn("400", model.actionStatus)

    def test_extrude_and_jog_selection_persists(self):
        # A live report: the chosen extrude options were
        # not saved between sessions.
        from plugins.MoonrakerMonitorModel import _read_state
        model = self.monitor()
        self.deliver_state("standby")
        model.setExtrudeDistance(25)
        model.setExtrudeSpeed(120)
        model.setJogDistance(10)
        stored = _read_state(self.follower.persistence)["toolhead"]
        self.assertEqual(stored["extrudeDistance"], 25.0)
        self.assertEqual(stored["extrudeSpeed"], 120.0)
        self.assertEqual(stored["jogDistance"], 10.0)

    def test_collapsed_console_keeps_a_slow_error_poll(self):
        # The error bell's feed (the live request): the
        # store fetch stops while expanded-only, so a SLOW watch
        # keeps the bell able to ring while collapsed.
        model = self.monitor()
        self.deliver_state("standby")
        model.setConsoleExpanded(False)
        self.assertTrue(model._data._console_watch.isActive())
        stores = lambda: [r for r in self.transport.requests if "gcode_store" in r.path]
        before = len(stores())
        model._data.refresh_console_store()  # gated: a no-op while collapsed
        self.assertEqual(len(stores()), before)
        model._data._refresh_console_watch()  # the bell's slow poll
        self.assertEqual(len(stores()), before + 1)
        model.setConsoleExpanded(True)
        self.assertFalse(model._data._console_watch.isActive())

    def test_command_reply_with_error_body_is_reported_as_refused(self):
        # A server ANSWER with an error body is a refusal, not an
        # unknown: the command did not run (a live
        # report — a cold extrude showed a bare 400).
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"error": "Extrude below minimum temp"}, "Extrude below minimum temp")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("refused", model.actionStatus)
        self.assertIn("Extrude below minimum temp", model.actionStatus)
        self.assertNotIn("outcome unknown", model.actionStatus)

    def test_console_error_bell_rings_while_collapsed_and_clears_on_expand(self):
        # A live request: an error line landing while the
        # console is collapsed rings a red bell next to its header
        # until the console expands. Restored lines never ring.
        model = self.monitor()
        self.deliver_state("standby")
        model._sections["console"] = False
        model._console._append_entries([{"kind": "command", "text": "!! cold",
                                         "error": False, "success": False, "restored": True}])
        model._console._emit_changed()  # the real error path emits through the send callback
        self.assertFalse(model.consoleErrorBell)
        model._console._append_entries([{"kind": "command", "text": "!! cold",
                                         "error": True, "success": False, "restored": False}])
        model._console._emit_changed()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertTrue(model.consoleErrorBell)
        # Expanding clears it.
        model._sections["console"] = True
        model._console._emit_changed()
        self.qt.events()
        self.assertFalse(model.consoleErrorBell)
        # Old errors never re-ring after collapsing again.
        model._sections["console"] = False
        model._console._emit_changed()
        self.qt.events()
        self.assertFalse(model.consoleErrorBell)

    def test_action_status_receipt_overlays_then_reverts_to_durable(self):
        # The lane's completion receipts are transient: "X sent"
        # overlays for RECEIPT_MS, then the row reverts to the durable
        # value beneath — never to nothing (the ruling: age
        # out to the previous durable value).
        model = self.monitor()
        self.deliver_state("standby")
        model._commands._status = "Pause: paused"
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.qt.events()  # the publish coalescer flushes on the next turn
        # In flight: the lifecycle text overlays the durable status.
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO requested…")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback(None, None)
        self.qt.events()  # the publish coalescer flushes on the next turn
        # Completed: the receipt, with send-family copy — never
        # "accepted", which would claim an outcome the POST ack cannot
        # vouch for.
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO sent")
        self.assertNotIn("accepted", model.actionStatus)
        # Aged out: the row falls to "—" under its permanent caption —
        # never back to a stale durable claim from an earlier action
        # (the panel ruling: "Pause: paused" resurfacing after a newer
        # action reads as fresh printer activity).
        self.qt.events(model._commands.RECEIPT_MS + 500)
        # Under the parallel coverage wave's load the receipt's timer
        # can lag the simulated window: poll within a budget instead
        # of asserting once (the assert_model pattern).
        budget = 5000
        while model.actionStatus != "" and budget > 0:
            self.qt.events(100)
            budget -= 100
        self.assertEqual(model.actionStatus, "")

    def test_console_sends_never_touch_the_action_status(self):
        # Console traffic left the card ticker: the feed carries
        # console feedback, and the card row keeps showing whatever
        # durable value it had (the panel UX ruling).
        model = self.monitor()
        model._commands._status = "Pause: paused"
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Pause: paused")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback(None, None)
        # The completion is a console lane cycle, not a card event.
        self.assertEqual(model.actionStatus, "Pause: paused")

    def test_console_error_lines_speak_in_the_feed(self):
        # The live ruling: no status banners or labels outside
        # the feed — a live "!!" line is its own red signal, and
        # nothing asserts "Klipper reported an error" anywhere.
        model = self.monitor()
        self.assertFalse(hasattr(model, "consoleStatus"))
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.assertEqual(model.actionStatus, "")
        model._console.append_responses([{"text": "!! Must home first", "error": True,
                                          "success": False, "time": model._console._store_time + 1.0}])
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertTrue(lines[-1]["error"])
        self.assertEqual(model.actionStatus, "")
        self.assertTrue(model.sendConsoleCommand("G28"))

    def test_last_action_rows_are_labelled_and_always_visible(self):
        # The permanent caption row (the ruling): a label so
        # the row explains itself before first use, "—" until the first
        # event, and no visibility gate to make it pop in and out. It
        # lives ONLY in the Monitor's Print job grid (first row, so its
        # columns are the grid's columns — a separate row read as
        # misaligned); the Dashboard's print section does not repeat it.
        self.assertIn('text: "Last action"', JOB_SECTION_QML)
        self.assertIn('root.printerModel.actionStatus.length > 0 ? root.printerModel.actionStatus : "—"', JOB_SECTION_QML)
        self.assertNotIn("visible: root.printer != null && root.printer.actionStatus.length > 0", MONITOR_QML)
        self.assertLess(JOB_SECTION_QML.index('text: "Last action"'), JOB_SECTION_QML.index('text: "Layer"'))
        self.assertNotIn('text: "Last action"', DASHBOARD_QML)

    def test_macros_refuse_while_printing(self):
        model = self.monitor()
        # observe() rebuilds the macro table from the snapshot on every
        # delivery, so re-seed it after each state change.
        self.deliver_state("printing")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.assertEqual(self.scripts(), [])
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.assertEqual([r.options["body"] for r in self.scripts()], [{"script": "TEST_MACRO"}])
        # The Run button carries the same gate in the UI.
        self.assertIn("!root.printerModel.printActive", MACROS_SECTION_QML)

    def test_system_restarts_are_queued_one_shot_commands(self):
        model = self.monitor()
        self.deliver_state("standby")
        model.firmwareRestart()
        # The host's own endpoint (the ruled route): the gcode form
        # disconnects immediately, so its ack never arrives.
        restarts = [r for r in self.transport.requests if r.path == "printer/firmware_restart"]
        self.assertEqual(len(restarts), 1)
        restarts[0].callback({}, None)
        self.qt.events(10)
        model.hostRestart()
        reboot = [r for r in self.transport.requests if r.path == "machine/reboot"]
        self.assertEqual(len(reboot), 1)
        reboot[0].callback({}, None)
        self.qt.events(10)
        # A full Klipper restart hits Moonraker's RESTART endpoint (the
        # request — heavier than FIRMWARE_RESTART).
        model.klipperRestart()
        self.qt.events(10)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/restart"]), 1)
        # Restarts refuse while a print is active.
        self.deliver_state("printing")
        model.firmwareRestart()
        model.hostRestart()
        model.klipperRestart()
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/firmware_restart"]), 1)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "machine/reboot"]), 1)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/restart"]), 1)

    def test_panel_state_persists_across_model_instances(self):
        model = self.monitor()
        self.assertNotEqual(model._sections.get("toolhead"), False)  # default expanded
        model.setSectionExpanded("toolhead", False)
        model.setControlsCollapsed(True)
        model.setControlsLocked(True)
        model.setInfoCollapsed(True)
        model.setStatusCollapsed(True)
        # The write really lands in the plugin-owned JSON file, so a Cura
        # restart round-trips through the file rather than any model state
        # or Uranium preference-store behaviour.
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {
                # A fresh model stores no what's-new marker — the
                # overlay shows until a dismissal records one.
                "whatsNewSeen": "",
                "sections": {"toolhead": False},
                "controlsCollapsed": True,
                "controlsLocked": True,
                "infoCollapsed": True,
                "statusCollapsed": True,
                # An undragged console writes 0 — "never dragged", so
                # the pane renders at its own default size.
                "consoleHeight": 0,
                # The column config (Snapshot 3): widths only hold
                # user-set values; the order is the pinned sequence
                # until changed.
                "fileManagerColumns": {
                    "widths": {},
                    "order": ["Modified", "Size", "Attempts", "Status", "Object height",
                              "Layer height", "Est. time", "Last print", "Slicer",
                              "Extruder", "Bed", "Filament"],
                    "hidden": [],
                },
                # The jog/extrude selection persists too (the
                # live report: the chosen options were not
                # saved between sessions).
                "toolhead": {"jogDistance": 25.0, "extrudeDistance": 5.0, "extrudeSpeed": 300.0},
                # The follower view settings are global (the live
                # ruling) — the defaults ride the fresh document.
                "followerView": {"showPrevious": True, "showNext": True,
                                 "showBase": True, "showTravels": False,
                                 "lineScale": 1.0},
            })
            # The chart config is per-printer now: the global file must
            # not carry it, and the per-printer record defaults empty.
            self.assertEqual(self.follower.current_printer_config().temperature_chart, {})
        # A fresh model reads the stored file back.
        second = self.monitor()
        self.assertEqual(second._sections["toolhead"], False)
        self.assertTrue(second.controlsCollapsed)
        self.assertTrue(second.controlsLocked)
        self.assertTrue(second.infoCollapsed)
        self.assertTrue(second.statusCollapsed)
        second.setSectionExpanded("toolhead", True)
        self.assertEqual(second._sections["toolhead"], True)

    def test_section_layout_persists_across_model_instances(self):
        # 4.4.0: the configure popups' committed reorder and hidden
        # set round-trip through the plugin-owned JSON file — a Cura
        # restart rehydrates the layout before any popup opens. The
        # fresh model's EFFECTIVE layout (sectionLayoutFor) reads the
        # stored order and the hidden set, never the pane default.
        model = self.monitor()
        order = ["job", "temps", "fansinfo", "filament", "systeminfo", "mcus"]
        self.assertIsNone(model.setSectionLayout("status", order, ["mcus"]))
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
            self.assertEqual(payload["sectionLayout"], {"status": {"order": order, "hidden": ["mcus"]}})
        second = self.monitor()
        effective = second.sectionLayoutFor("status")
        self.assertEqual(effective["order"], order)
        self.assertEqual(effective["hidden"], ["mcus"])

    def test_resetting_one_pane_does_not_touch_anothers_layout(self):
        # The live report: the controls popup's reset-to-defaults
        # visibly reset the information pane's customised sections.
        # The model must keep every other pane's entry untouched —
        # the reset re-normalises the whole document with ONE pane's
        # entry cleared.
        model = self.monitor()
        info_order = list(self.qt.load("SectionLayoutPolicy").PANE_SECTION_ORDER["information"])
        custom = [info_order[1], info_order[0]] + info_order[2:]
        model.setSectionLayout("information", custom, [info_order[0]])
        model.setSectionLayout("controls", [], [])
        effective = model.sectionLayoutFor("information")
        self.assertEqual(effective["order"], custom)
        self.assertEqual(effective["hidden"], [info_order[0]])

    def test_console_height_persists_and_clamps_across_model_instances(self):
        # 3.6.0: the console's drag handle sets a pane height the model
        # owns. It round-trips through the plugin-owned JSON file (a Cura
        # restart rehydrates it before the pane exists), never hydrates
        # negative, and an unchanged height is not rewritten — a drag
        # riding its clamp must stop touching the disk.
        model_module = self.qt.load("MoonrakerMonitorModel")
        section_path = self.follower.persistence.state_global_path
        model = self.monitor()
        self.assertEqual(model.consoleHeight, 0)  # never dragged
        model.setConsoleHeight(240)
        self.assertEqual(model.consoleHeight, 240)
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["consoleHeight"], 240)
        # A fresh model rehydrates it.
        second = self.monitor()
        self.assertEqual(second.consoleHeight, 240)
        # No negative height can ever land, whatever the drag reports.
        second.setConsoleHeight(-40)
        self.assertEqual(second.consoleHeight, 0)
        # Absurd values clamp to the model's ceiling (the pane bounds are
        # the QML's clamp — it is the only side that can see them).
        second.setConsoleHeight(999999)
        self.assertEqual(second.consoleHeight, model_module.CONSOLE_HEIGHT_MAX)
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["consoleHeight"], model_module.CONSOLE_HEIGHT_MAX)
        # The unchanged-height pin: an unchanged height is not
        # rewritten — a drag riding its clamp must stop touching the
        # disk. The 4.5.0 store slot is the facade; the spy rides the
        # facade's global merge.
        with patch.object(second._store, "merge_state_global",
                          wraps=second._store.merge_state_global) as write:
            second.setConsoleHeight(180)
            second.setConsoleHeight(180)
            self.assertEqual(write.call_count, 1)

    def test_panel_state_migrates_the_legacy_flat_section_file(self):
        # The first shipped format stored the bare section map; it must
        # still hydrate into sections with default panel toggles.
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"setup": False, "toolhead": True}, handle)
        model = self.monitor()
        self.assertEqual(model._sections, {"setup": False, "toolhead": True})
        self.assertFalse(model.controlsCollapsed)
        self.assertFalse(model.controlsLocked)

    def test_a_sections_less_document_with_sibling_keys_is_not_a_flat_map(self):
        # The flat-map legacy shape is recognised ONLY when every value
        # is a bool: a document that lacks `sections` and carries the
        # UI-state store's sibling keys must not hydrate them as
        # sections (the silent collapse-state reset, 4.3.0).
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sectionSizes": {"info": 240.0}, "setup": False}, handle)
        model = self.monitor()
        self.assertEqual(model._sections, {})

    def test_the_ui_state_store_owns_the_sections_writes(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": False},
                       "controlsLocked": True,
                       "sectionSizes": {"info": 240.0}}, handle)
        model = self.monitor()
        model.setSectionExpanded("toolhead", False)
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        # The section write merged: the sibling keys survive untouched.
        self.assertEqual(payload["sections"], {"setup": False, "toolhead": False})
        self.assertTrue(payload["controlsLocked"])
        self.assertEqual(payload["sectionSizes"], {"info": 240.0})

    def test_the_store_delete_drops_only_the_named_keys(self):
        from plugins.StateStore import StateStore
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            store = StateStore(path)
            store.write({"sections": {"setup": False}, "temperatureChart": {"visible": {}},
                         "controlsLocked": True})
            store.write({}, delete=("temperatureChart",))
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertNotIn("temperatureChart", payload)
            self.assertEqual(payload["sections"], {"setup": False})
            self.assertTrue(payload["controlsLocked"])

    def test_corrupt_panel_state_file_degrades_to_defaults(self):
        # A truncated or hand-edited file must never raise or hydrate
        # inverted: unreadable JSON yields defaults, and string flags like
        # 'false' must collapse (bool('false') is True).
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        model = self.monitor()
        self.assertEqual(model._sections, {})
        self.assertFalse(model.controlsCollapsed)
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": "false", "toolhead": 0},
                       "controlsLocked": "true"}, handle)
        model = self.monitor()
        self.assertIs(model._sections["setup"], False)
        self.assertIs(model._sections["toolhead"], False)
        self.assertIs(model.controlsLocked, True)
        # The console height goes through the same discipline: a missing
        # key is the unset default, junk never raises, and a negative or
        # non-numeric height can never hydrate.
        self.assertEqual(model.consoleHeight, 0)
        for stored in ("-120", "", "tall", None, [], float("inf"), float("nan")):
            with open(section_path, "w", encoding="utf-8") as handle:
                json.dump({"consoleHeight": stored}, handle)
            model = self.monitor()
            self.assertEqual(model.consoleHeight, 0, stored)
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"consoleHeight": "412"}, handle)
        model = self.monitor()
        self.assertEqual(model.consoleHeight, 412)

    def chart_of(self, model):
        chart = model.temperatureChartFull
        return chart if isinstance(chart, dict) else chart.value()

    def mini_of(self, model):
        chart = model.temperatureChartMini
        return chart if isinstance(chart, dict) else chart.value()

    def legend_of(self, model):
        legend = model.temperatureChartLegend
        return legend if isinstance(legend, dict) else legend.value()

    def test_the_full_chart_payload_hydrates_only_while_the_popover_is_open(self):
        # K (the 2026-09-19 review): closed serves the dormant empty
        # object — the full payload, its QVariant conversion and its
        # signal all stay asleep per feed; open hydrates the full
        # payload; closing returns it to dormancy on the very next
        # publish. The mini preview is a separate, bounded payload
        # that keeps serving while the pop-over is closed.
        model = self.monitor()
        self.feed_chart(model, {
            "extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
            "temperature_sensor chamber": {"temperature": 40.0},
        })
        self.assertEqual(self.chart_of(model)["series"], [], "closed: the full payload is dormant")
        mini = self.mini_of(model)
        self.assertLessEqual(len(mini["series"]), 2, "the mini payload carries only its own series")
        self.assertEqual(len(self.legend_of(model)["series"]), 3, "the legend keeps every series")
        model.setChartOpen(True)
        full = self.chart_of(model)
        self.assertEqual([s["name"] for s in full["series"] if s.get("points")],
                         ["extruder", "heater_bed", "temperature_sensor chamber"])
        model.setChartOpen(False)
        self.assertEqual(self.chart_of(model)["series"], [], "closing returns the full payload to dormancy")

    def test_the_full_payload_stays_dormant_across_ticks_while_closed(self):
        # The same dormant object across chart ticks: the STORED
        # value's identity is stable, so the full-chart property never
        # re-converts and its signal never fires while the pop-over is
        # closed. (The property read itself crosses QVariant, so the
        # identity is asserted on the stored value, not the read.)
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0}})
        fired = []
        model.temperatureChartFullChanged.connect(lambda: fired.append(1))
        self.assertEqual(self.chart_of(model)["series"], [])
        dormant = model._values["temperatureChartFull"]
        for tick in range(5):
            model._data._update(auxiliary={"extruder": {"temperature": 200.0 + tick}})
            model._on_chart_tick()
            self.qt.events()
        self.assertIs(model._values["temperatureChartFull"], dormant,
                      "a feed must not rebuild the closed full payload")
        self.assertEqual(fired, [], "the full-chart signal fired while closed")

    def test_toggles_hydrate_the_open_chart_immediately(self):
        # Targets/power toggled while the pop-over is ALREADY open
        # must hydrate on the toggle's own publish — never wait for
        # the next auxiliary sample. The history is seeded directly:
        # the harness's data lane drops target/power from injected
        # auxiliary (probe-proven, old code included), so the lane is
        # bypassed and the payload plumbing under test is the toggle's.
        model = self.monitor()
        # Two samples: a one-sample track never forms a drawable
        # segment (the lone-setpoint rule), and the toggle contract
        # needs a real one.
        model._history.observe({"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}},
                               1000.0, 1000000.0)
        model._history.observe({"extruder": {"temperature": 200.5, "target": 210.0, "power": 0.5}},
                               1002.5, 1000002.5)
        model._schedule_publish()
        self.qt.events(1)
        model.setShowTemperatureTargets(True)
        model.setShowTemperaturePower(True)
        model.setChartOpen(True)
        self.assertTrue(self.chart_of(model)["series"][0]["targets"])
        model.setShowTemperatureTargets(False)
        self.assertEqual(self.chart_of(model)["series"][0]["targets"], [])
        self.assertTrue(self.chart_of(model)["series"][0]["powers"])
        model.setShowTemperaturePower(False)
        self.assertEqual(self.chart_of(model)["series"][0]["powers"], [])
        model.setShowTemperatureTargets(True)
        self.assertTrue(self.chart_of(model)["series"][0]["targets"],
                        "re-enabling hydrates on the same toggle")

    def test_temperature_chart_config_persists_across_model_instances(self):
        model = self.monitor()
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
                     "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.2}}
        self.feed_chart(model, auxiliary)
        # Defaults: everything visible, palette colours, toggles on.
        default = self.legend_of(model)
        self.assertTrue(default["showTargets"])
        self.assertTrue(default["showPower"])
        model.setTemperatureSensorVisible("extruder", False)
        model.setTemperatureSensorColor("heater_bed", "#123456")
        model.setShowTemperatureTargets(False)
        model.setShowTemperaturePower(False)
        # The chart config persists per printer (sensor names differ
        # between machines), never in the global chrome file.
        self.assertEqual(self.follower.current_printer_config().temperature_chart, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        # A fresh model restores the config from the file.
        second = self.monitor()
        self.feed_chart(second, auxiliary)
        legend = self.legend_of(second)
        self.assertFalse(legend["showTargets"])
        self.assertFalse(legend["showPower"])
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")

    def test_history_feeds_once_per_chart_tick_not_per_publish(self):
        model = self.monitor()
        self.deliver()  # the client's connect transition
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}}
        model._data._update(auxiliary=auxiliary)
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"]), 0,
                         "an aux arrival alone never feeds the chart")
        # Core-only publishes must not append samples either: the old
        # per-publish feed duplicated samples and halved the effective
        # window.
        for _ in range(5):
            model._data._update(core={"print_stats": {"state": "printing"}})
        self.assertEqual(len(self.mini_of(model)["series"]), 0)
        # The fixed 1 s tick samples the latest snapshot once.
        model._on_chart_tick()
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"][0]["points"]), 1)
        # A second tick appends exactly one more sample.
        model._on_chart_tick()
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"][0]["points"]), 2)

    def test_history_resets_when_the_session_is_invalidated(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        self.assertEqual(len(self.mini_of(model)["series"]), 1)
        model._data.set_owner_active(False)  # emits invalidated
        self.assertEqual(self.mini_of(model)["series"], [])
        # Every render cache empties with the reset — the legend and
        # the full payload included.
        self.assertEqual(self.legend_of(model)["series"], [])
        self.assertEqual(self.chart_of(model)["series"], [])

    def test_chart_setters_are_idempotent_and_validate(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        writes = []
        model._apply_chart_config = lambda: writes.append(1)
        # Re-applying the same value must not rewrite the state file
        # (the legend re-binds every second and used to re-save each
        # time).
        model.setTemperatureSensorVisible("extruder", True)
        model.setTemperatureSensorColor("extruder", "#d32f2f")  # the palette default: a no-op
        self.assertEqual(writes, [])
        model.setTemperatureSensorVisible("extruder", False)
        self.assertEqual(writes, [1])
        model.setTemperatureSensorVisible("extruder", False)
        self.assertEqual(writes, [1])
        # Invalid colours are rejected outright.
        model.setTemperatureSensorColor("extruder", "#fff")
        self.assertEqual(writes, [1])
        legend = self.legend_of(model)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        self.assertEqual(extruder["color"], "#d32f2f")  # the palette default, unchanged
        model.setTemperatureSensorColor("extruder", "#123456")
        self.assertEqual(writes, [1, 1])

    def test_chart_config_prunes_vanished_sensors(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0}})
        model.setTemperatureSensorColor("ghost_sensor", "#123456")
        # Any later change prunes keys for sensors no longer present —
        # but never while the live set is empty.
        model.setTemperatureSensorVisible("extruder", False)
        chart = self.follower.current_printer_config().temperature_chart
        self.assertNotIn("ghost_sensor", chart["colors"])
        self.assertEqual(chart["colors"], {})

    def test_chart_config_set_before_history_arrives_still_persists(self):
        # The suspicion: changing colours before the first aux
        # reply must survive — the history loads AFTER the config.
        model = self.monitor()
        model.setTemperatureSensorVisible("extruder", False)
        model.setTemperatureSensorColor("heater_bed", "#123456")
        self.assertEqual(self.follower.current_printer_config().temperature_chart,
                         {"visible": {"extruder": False}, "colors": {"heater_bed": "#123456"},
                          "showTargets": True, "showPower": True})
        self.feed_chart(model, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        legend = self.legend_of(model)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")
        second = self.monitor()
        self.feed_chart(second, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        legend = self.legend_of(second)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")

    def test_temperature_chart_defaults_when_the_block_is_missing(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": False}}, handle)
        model = self.monitor()
        legend = self.legend_of(model)
        self.assertTrue(legend["showTargets"])
        self.assertTrue(legend["showPower"])
        self.assertTrue(all(item["visible"] for item in legend["series"]))

    def test_legacy_global_chart_block_migrates_into_the_per_printer_record(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": False},
                       "temperatureChart": {"visible": {"extruder": False},
                                            "colors": {"heater_bed": "#123456"},
                                            "showTargets": False, "showPower": False}}, handle)
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        # The legacy block was adopted once into the per-printer record…
        self.assertEqual(self.follower.current_printer_config().temperature_chart, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        extruder = next(item for item in self.legend_of(model)["series"] if item["name"] == "extruder")
        bed = next(item for item in self.legend_of(model)["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")
        # …and the global file keeps chrome only afterwards.
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertNotIn("temperatureChart", payload)

    def test_console_sends_scripts_and_persists_history_per_printer(self):
        model = self.monitor()
        model.sendConsoleCommand("  M104 S200  ")
        scripts = [request for request in self.transport.requests
                   if request.path == "printer/gcode/script"]
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].options["body"], {"script": "M104 S200"})
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, ["M104 S200"])
        # The TRANSCRIPT persists per printer, never in the global file
        # (the typed history is now derived from it).
        # The persisted record carries kind/text/error/success; the
        # controller stamps restored=True on load (everything loaded
        # predates this session — the pane greys it).
        self.assertEqual(self.stored_transcript()[-1],
                         {"kind": "command", "text": "M104 S200", "error": False, "success": False})
        second = self.monitor()
        self.assertEqual(second.consoleHistory, ["M104 S200"])
        # The pane list serves the transcript; restored lines carry the
        # stamped flag (everything persisted predates this session).
        self.assertEqual(second.consoleLines.value(), [{"kind": "command", "text": "M104 S200",
                                                        "error": False, "success": False, "restored": True}])
        # No "sent to Klipper" caption (the ruling): the typed
        # line's verdict colouring carries the feedback, and nothing
        # else speaks on a successful send.

    def test_console_persist_keeps_commands_against_chatty_responses(self):
        # A chatty Klipper fills the 50-entry persist window with
        # responses; the newest commands must be retained in the
        # persisted record (the "none of my requests are
        # restored" report — the window had trimmed them away).
        model = self.monitor()
        model._console._transcript = [
            {"kind": "command", "text": "C1", "error": False, "success": False, "restored": False},
            *[{"kind": "response", "text": "B:%d.0" % i, "error": False, "success": False, "restored": False}
              for i in range(60)],
            {"kind": "command", "text": "C2", "error": False, "success": False, "restored": False},
        ]
        model._console._persist()
        stored = self.stored_transcript()
        commands = [entry["text"] for entry in stored if entry["kind"] == "command"]
        self.assertIn("C1", commands)
        self.assertIn("C2", commands)
        # The retained commands must survive the ROUND TRIP: the load
        # once trimmed the record back to MAX_TRANSCRIPT and cut the
        # commands at the front (the "my requests are missing
        # from the restore").
        second = self.monitor()
        restored = [entry["text"] for entry in second.consoleLines.value() if entry["kind"] == "command"]
        self.assertIn("C1", restored)
        self.assertIn("C2", restored)

    def test_console_persist_keeps_the_success_flag(self):
        # The restored "ok" renders green only if the success flag
        # survives the config cleaning (it was dropped once, greying
        # every restored response — the "never seen a
        # coloured line" report).
        model = self.monitor()
        model._console._transcript = [
            {"kind": "command", "text": "G28", "error": False, "success": False, "restored": False},
            {"kind": "response", "text": "ok", "error": False, "success": True, "restored": False},
        ]
        model._console._persist()
        stored = self.stored_transcript()
        self.assertEqual(stored[-1]["success"], True)

    def test_console_empty_input_and_clear_and_refused_sends(self):
        model = self.monitor()
        # The slot reports acceptance so the UI can keep the draft on
        # a refusal instead of destroying an unsent G-code line.
        self.assertFalse(model.sendConsoleCommand("   "))
        self.assertEqual(model.consoleHistory, [])
        # An empty Enter is simply nothing — no note, no banner (the
        # ruling: nothing sent carries no information).
        self.assertEqual(model.consoleLines.value(), [])
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, ["G28"])
        model.clearConsoleHistory()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, [])
        self.assertEqual(self.stored_transcript(), [])
        # A refused send (lane full / Moonraker down) explains itself
        # as a neutral "//" feed line and does not enter the history.
        model._console._data.request = lambda *args, **kwargs: False
        self.assertFalse(model.sendConsoleCommand("G1 X10"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, [])
        self.assertIn("try again", model.consoleLines.value()[-1]["text"])
        # The note is the plugin's own feed line — kind "note", not a
        # Moonraker response.
        self.assertEqual(model.consoleLines.value()[-1]["kind"], "note")
        self.assertFalse(model.consoleLines.value()[-1]["error"])

    def test_console_reloads_the_transcript_when_it_constructed_empty(self):
        # The plugin constructs the console before the active machine
        # exists, so the early load reads an empty record; by the time
        # the pane attaches the identity is real and the console must
        # re-load the per-printer transcript (the "completely
        # empty at app start" report).
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("M104 S200"))
        model._console._transcript = []  # the early, empty construction
        model._console._loaded_identity = None  # the latch dies with it
        model.setConsoleExpanded(True)
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertTrue(any(entry["kind"] == "command" and entry["text"] == "M104 S200"
                            for entry in lines))

    def test_console_replay_of_the_record_keeps_commands(self):
        # The real record: 53 entries, 8 commands scattered,
        # 3 pinned at the head (the retention's shape). A session that
        # loads it, backfills responses and persists must NOT drop the
        # commands (the "it's just a bunch of responses").
        kinds = (["command"] * 3
                 + ["response"] * 12
                 + ["command", "response"] * 3
                 + ["response"] * 5
                 + ["command", "response"] * 2
                 + ["response"] * 8
                 + ["command", "response", "response", "response"])
        transcript = [{"kind": kind, "text": f"{kind}@{i}", "error": False,
                       "success": False, "restored": True}
                      for i, kind in enumerate(kinds)]
        model = self.monitor()
        model._console._transcript = [dict(entry) for entry in transcript]
        # A backfill of two fresh server responses arrives.
        model._console.append_responses([
            {"text": "B:55.0 /55.0", "error": False, "success": True,
             "time": model._console._store_time + 1.0},
            {"text": "// Unknown command:\"123\"", "error": False, "success": False,
             "time": model._console._store_time + 2.0},
        ])
        model._console._persist()
        stored = self.stored_transcript()
        commands = [entry for entry in stored if entry["kind"] == "command"]
        self.assertGreaterEqual(len(commands), 8)
        # The three head commands survive the window as the record head.
        self.assertEqual([entry["text"] for entry in stored[:3]],
                         ["command@0", "command@1", "command@2"])

    def test_console_send_verdict_colours_the_typed_line(self):
        # The send POST's own result is the execution verdict (the
        # endpoint returns "ok" on completion); the store feed cannot
        # pair, but this callback belongs to THIS request — the entry
        # is captured at send time, so identical commands in flight can
        # never swap verdicts.
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("G28"))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"result": "ok"}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[0]["error"])
        # A server ANSWER with an error body is a real refusal: red.
        self.assertTrue(model.sendConsoleCommand("M999"))
        self.scripts()[-1].callback({"error": {"message": "Command refused"}}, "Command refused")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[1]["error"])
        self.assertFalse(lines[1]["success"])
        # A transport-level failure (timeout, network) is NO verdict:
        # the command may still be executing — a client timeout must
        # never paint a running command red (the domain panel: blocking
        # commands legitimately outlast the 30 s client timeout). The
        # status note says so, honestly.
        self.assertTrue(model.sendConsoleCommand("M190 S60"))
        self.scripts()[-1].callback(None, "Connection timed out")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertFalse(lines[2]["error"])
        self.assertFalse(lines[2]["success"])
        # The honest note lands as the plugin's own feed line.
        self.assertIn("may still be running", lines[-1]["text"])
        self.assertEqual(lines[-1]["kind"], "note")
        self.assertFalse(lines[-1]["error"])
        self.assertFalse(lines[-1]["success"])

    def test_console_verdicts_pair_by_captured_entry_not_text(self):
        # Two identical commands in flight: the older request's verdict
        # must land on the OLDER line, never on the newest twin with
        # the same text (the old text+recency scan swapped them).
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.assertTrue(model.sendConsoleCommand("G28"))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        # The OLDER request completes first, successfully.
        scripts[0].callback({"result": "ok"}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[1]["success"])
        # The NEWER request then fails at the server.
        scripts[1].callback({"error": {"message": "refused"}}, "refused")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertFalse(lines[0]["error"])
        self.assertTrue(lines[1]["error"])
        self.assertFalse(lines[1]["success"])

    def test_emergency_stop_pending_tokens_survive_stale_completions(self):
        # The empirical drift: 3 sends → emergency stop → 2 fresh sends →
        # 3 stale completions → pending 0 (should be 2). In-flight tokens
        # fix it: completions belong to a specific entry, and dead
        # requests' entries were dropped with the stop.
        model = self.monitor()
        for text in ("G28", "M105", "G90"):
            self.assertTrue(model.sendConsoleCommand(text))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 3)
        model._commands.emergencyStopped.emit()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 0)
        for text in ("M105", "G1 X0"):
            self.assertTrue(model.sendConsoleCommand(text))
        self.qt.events()
        self.assertEqual(model.consolePending, 2)
        # The three dead requests complete late — nothing drains.
        for script in scripts:
            script.callback(None, "aborted")
        self.qt.events(1)
        self.assertEqual(model.consolePending, 2)

    def test_fresh_tracked_outcome_survives_a_stale_receipt_timer(self):
        # The engineering panel's receipt resurrection: a macro banner
        # arms, then a tracked Pause completes inside the window — the
        # old "Macro sent" banner must never resurface over the fresh
        # terminal status (it did: send() never stopped the timer).
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.scripts()[0].callback(None, None)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO sent")
        model._commands.send("Pause", "printer/print/pause")
        self.qt.events(1)
        # The harness's command tracker delivers a "pending" event
        # synchronously on track; the live text resolves immediately.
        self.assertEqual(model.actionStatus, "Pause: pending")
        model._commands._command_changed({"name": "Pause", "outcome": "confirmed",
                                          "detail": "paused", "terminal": True})
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Pause: paused")
        self.qt.events(model._commands.RECEIPT_MS + 500)
        self.assertEqual(model.actionStatus, "Pause: paused")

    def test_mark_saved_only_claims_the_persisted_window(self):
        # The engineering panel's over-promise: mark_saved once greys
        # every live command "on disk", including entries beyond the
        # persisted window that die with the session. Only the window's
        # entries (last 50 + up to 10 pinned commands) may claim saved.
        model = self.monitor()
        model._console._transcript = [
            *[{"kind": "command", "text": "OLD%d" % i, "error": False, "success": False,
               "restored": False, "saved": False} for i in range(12)],
            *[{"kind": "response", "text": "B:%d.0" % i, "error": False,
               "success": False, "restored": False} for i in range(60)],
        ]
        model._console._persist()  # only a successful write may be claimed
        model._console.mark_saved()
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertFalse(lines[0]["saved"])  # OLD0: beyond the pin reach
        self.assertTrue(lines[2]["saved"])   # OLD2: pinned into the window

    def test_endstop_and_eta_surfaces(self):
        # The Improve-ETA action is a small download glyph beside the
        # Remaining value, not a full-width button row.
        improve = JOB_SECTION_QML[JOB_SECTION_QML.index('Qt.resolvedUrl("Download.svg")'):JOB_SECTION_QML.index("onClicked: root.printerModel.improveEta()")]
        self.assertIn("Download.svg", improve)
        self.assertNotIn("Improve ETA — download", MONITOR_QML)
        for token in ("endstopItems", "endstopSummary",
                      "modelData.name + \": \" + modelData.state", "modelData.triggered"):
            self.assertIn(token, TOOLHEAD_SECTION_QML)  # the readout lives in the Toolhead section
        self.assertNotIn('title: "Endstops"', MONITOR_QML)
        self.assertNotIn('sectionId: "endstops"', MONITOR_QML)
        # The empty-set copy lives in the projection, not the QML —
        # and it makes no causal claim (the live report: homed axes
        # with no endstop pins, e.g. sensorless homing, read "not
        # homed yet").
        self.assertIn("No endstop states reported", FORMATTING)
        for token in ("endstopItems", "endstopSummary", "endstopsChanged",
                      "printer/query_endstops/status", "refresh_endstops"):
            self.assertIn(token, MONITOR_MODEL + (PLUGINS / "MonitorData.py").read_text(encoding="utf-8"))
        for token in ("improveEta()", "monitorEtaBasis === \"blend\"", "monitorEtaBasis === \"index\""):
            self.assertIn(token, JOB_SECTION_QML)
        for token in ("monitorEtaBasis", "def improveEta(", "layer_eta", "remaining_end",
                      "request_monitor_download"):
            self.assertIn(token, MONITOR_MODEL + (PLUGINS / "MonitorFormatting.py").read_text(encoding="utf-8")
                          + (PLUGINS / "PreviewFollower.py").read_text(encoding="utf-8") + (PLUGINS / "PrintState.py").read_text(encoding="utf-8"))
        self.assertIn("confirmDownloadForMonitor", (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8"))

    def test_console_burst_drains_pending_per_completion(self):
        # Each console send posts its own request (the shared lane is
        # the card's ticker, off-limits for console traffic), and each
        # request's own callback drains exactly one pending slot — the
        # per-idle-epoch accounting that once leaked phantoms on the
        # lane is gone with the lane.
        model = self.monitor()
        for i in range(3):
            self.assertTrue(model.sendConsoleCommand(f"G1 X{i}"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 3)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        for i in range(3):
            scripts[i].callback({"result": "ok"}, None)
            self.qt.events()  # the publish coalescer flushes on the next turn
            self.assertEqual(model.consolePending, 3 - (i + 1))
        self.assertEqual(model.consolePending, 0)

    def test_send_console_command_slot_registers_a_bool_for_qml(self):
        # Without result=bool the metaobject registers the slot as void
        # and QML receives undefined — falsy — so the console draft
        # would never clear on an accepted send and Enter would re-send
        # the same command. Python calls cannot see this; only the
        # registered metaobject can.
        model = self.monitor()
        meta = model.metaObject()
        method = meta.method(meta.indexOfMethod("sendConsoleCommand(QString)"))
        self.assertGreaterEqual(meta.indexOfMethod("sendConsoleCommand(QString)"), 0)
        self.assertEqual(method.typeName(), "bool")

    def test_improve_eta_downloads_for_the_monitor_without_a_preview_load(self):
        # The optimisation: the Monitor's Improve-ETA action
        # downloads and indexes the print WITHOUT loading it into the
        # preview; the index service pulls the file itself.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        # The capability gates on a configured binding, which needs an
        # attached machine — the harness applies config directly, so
        # simulate the attachment and re-apply the config against it.
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        self.qt.events(1)
        requests = [request for request in self.transport.requests if request.owner == "files"]
        self.assertTrue(requests)  # metadata pull for the index build

    def test_improve_eta_flips_to_hourglass_until_the_index_lands(self):
        # The glyph turns into a non-clickable hourglass while the
        # monitor-only download runs; the state ends when the snapshot
        # reports the index ready.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        # The latch is the contract: the harness's index fixture
        # reports ready, which zeroes the published value — the
        # hourglass flag itself must survive the stale publishes.
        self.assertTrue(model._improving_eta)
        # While the index builds the phase reads Indexing… and the bar
        # goes indeterminate (-1); then the index lands and the state
        # ends. The fake snapshot needs the full core_values shape —
        # the poll-driven publishes during teardown keep reading it.
        original = model._print_state
        model._print_state = lambda: SimpleNamespace(
            layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=False,
            download_fraction=None, indexing=True, load_active=True,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertTrue(model.improvingEta)
        self.assertEqual(model.improveEtaPhase, "Indexing…")
        self.assertEqual(model.improveEtaProgress, -1.0)
        model._print_state = lambda: SimpleNamespace(
            layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=True,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertFalse(model.improvingEta)
        model._print_state = original

    def test_publish_coerces_every_optional_snapshot_field(self):
        # The live crash: an Optional snapshot field fed None into a
        # typed value_property raised "unable to convert a Python
        # 'NoneType' object to a C++ 'double' instance" every poll.
        # Every Optional rides a sentinel through the values dict —
        # this test feeds None for ALL of them at once.
        model = self.monitor()
        original = model._print_state
        model._print_state = lambda: SimpleNamespace(
            layer=SimpleNamespace(index=None, total=None, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=False,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False,
            filament_total=None)
        model._publish()
        self.assertEqual(model.nextPauseFraction, -1.0)
        self.assertEqual(model.nextPauseLayer, -1)
        self.assertFalse(model.nextPauseBaked)
        self.assertEqual(model.improveEtaProgress, -1.0)
        self.assertFalse(model.improvingEta)
        model._print_state = original

    def test_improve_eta_hourglass_ends_when_the_download_fails(self):
        # Panel P1-1: a failed monitor-only download used to strand the
        # hourglass forever (_monitor_requested only cleared when the
        # index landed or the BUILD failed — the download's error phase
        # was not terminal, the retry ladder is consumer-driven, and the
        # disabled glyph removed the only retry affordance). Now the
        # files-service error phase is terminal and the model's flag
        # clears as soon as nothing is in flight.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        self.qt.events(1)
        self.qt.events()  # the publish coalescer flushes on the next turn
        # The LATCH is the contract here: the harness's index fixture
        # reports ready, which zeroes the published value, but the
        # hourglass latch must survive every stale publish until the
        # coordinator's own termination (the coalescer exposed the
        # premature clear).
        self.assertTrue(model._improving_eta)
        files = [request for request in self.transport.requests if request.owner == "files"]
        self.assertTrue(files)
        for request in files:
            request.callback(None, "boom")
        self.deliver_state("printing")  # refresh recomputes the snapshot
        self.qt.events(1)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertFalse(model._improving_eta)
        coordinator = self.follower._runtime.coordinator
        self.assertFalse(coordinator._loads.monitor_requested)
        # The glyph stays the retry affordance: the QML no longer gates
        # it on the busy flag.
        self.assertNotIn("!root.printer.improvingEta", MONITOR_QML)

    def test_binding_reset_clears_the_monitor_download_flag(self):
        # Panel P1-1 (session-interruption wedge): reset_binding must
        # not leak _monitor_requested into the next binding.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        coordinator = self.follower._runtime.coordinator
        self.assertTrue(coordinator._loads.monitor_requested)
        coordinator.reset_binding()
        self.assertFalse(coordinator._loads.monitor_requested)
        model._publish()  # the model clears its flag on the next snapshot
        self.assertFalse(model.improvingEta)

    def test_metadata_fetch_latches_after_success_and_retries_after_failure(self):
        # A successful fetch is terminal for the job; a same-name
        # RESTART with a failed fetch never serves the previous job's
        # payload and retries after the throttle window — reachable
        # without poking private state (the old test's `_mr_meta = {}`
        # poke was itself proof the retry was unreachable in
        # production).
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client

        def deliver(position, duration=30):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": position},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)
        # The coordinator's clock is patched from the START: the fetch
        # timestamps must live on the same fake clock as the retry
        # window, or the real/fake mix blocks the throttle forever.
        with patch.object(module, "time", fake_time):
            deliver(20)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 1)
            meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600}}, None)
            self.qt.events(1)
            # A null job id needs no history cross-check.
            self.assertEqual([r for r in self.transport.requests if r.channel == "mr-history"], [])
            self.assertEqual(coordinator._mr_meta_key, ("part.gcode", coordinator._files.job_key))
            for step in range(3):
                tick[0] += 31.0
                deliver(20 + step)  # each delivery differs so the poll always refreshes
                self.qt.events(1)
            self.assertEqual(len([r for r in self.transport.requests if r.channel == "metadata-only"]), 1)
            # A same-name restart (the duration reset is a new job):
            # its failed fetch never latches, the old payload is never
            # served, and the retry fires on its own after the window.
            deliver(5, duration=5)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 2)
            meta[1].callback({}, "boom")
            self.qt.events(1)
            self.assertEqual(coordinator._mr_metadata_for("part.gcode", coordinator._files.job_key), {})
            tick[0] += 31.0
            deliver(6, duration=5)
            self.qt.events(1)
            self.assertEqual(len([r for r in self.transport.requests if r.channel == "metadata-only"]), 3)

    def test_metadata_with_job_id_cross_checks_the_current_print(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client

        def deliver(duration):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": 20},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

        with patch.object(module, "time", SimpleNamespace(monotonic=lambda: 1000.0, time=lambda: 1700000000.0)):
            deliver(30)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 1)
            meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600, "job_id": "1A2B"}}, None)
            self.qt.events(1)
            history = [r for r in self.transport.requests if r.channel == "mr-history"]
            self.assertEqual(len(history), 1)
            # The newest history row matches: the payload latches.
            history[0].callback({"result": {"count": 1, "jobs": [{"job_id": "1A2B", "status": "in_progress"}]}}, None)
            self.qt.events(1)
            key = ("part.gcode", coordinator._files.job_key)
            self.assertEqual(coordinator._mr_meta_key, key)
            self.assertEqual(coordinator._mr_metadata_for(*key).get("layer_height"), 0.2)
            # A same-name restart whose row mismatches never latches.
            deliver(5)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 2)
            meta[1].callback({"result": {"layer_height": 0.3, "job_id": "9Z9Z"}}, None)
            self.qt.events(1)
            history = [r for r in self.transport.requests if r.channel == "mr-history"]
            self.assertEqual(len(history), 2)
            history[1].callback({"result": {"count": 1, "jobs": [{"job_id": "1A2B", "status": "finished"}]}}, None)
            self.qt.events(1)
            new_key = ("part.gcode", coordinator._files.job_key)
            self.assertEqual(coordinator._mr_metadata_for(*new_key), {})

    def test_files_view_model_keeps_stable_identities_behind_the_list(self):
        # The view model (4.3.0): the QAbstractListModel behind the
        # list-valued projection — the relpath is the row identity,
        # so a rebuild re-anchors delegates by identity, never by
        # position.
        from plugins.FilesViewModel import FilesViewModel
        view = FilesViewModel()
        rows = [{"relpath": "a.gcode", "name": "a"},
                {"relpath": "b.gcode", "name": "b"},
                {"relpath": "c.gcode", "name": "c"}]
        view.set_rows(rows)
        self.assertEqual(view.rowCount(), 3)
        self.assertEqual(view.data(view.index(1)), "b.gcode")
        # A reordered rebuild keeps each row's identity attached to
        # its file.
        view.set_rows([rows[2], rows[0], rows[1]])
        self.assertEqual(view.data(view.index(0)), "c.gcode")
        self.assertEqual(view.data(view.index(2)), "b.gcode")

    def test_metadata_cross_check_refuses_a_mismatched_job_without_latching(self):
        # The bounded give-up (4.3.0): a cross-check that can never
        # pass (the history stays empty) is silent and permanent
        # otherwise — after MR_META_CHECK_LIMIT failures for the same
        # key the payload latches with the failure flagged in the log.
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client
        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)

        def deliver(duration):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": 20},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

        with patch.object(module, "time", fake_time):
            deliver(30)
            self.qt.events(1)
            for step in range(coordinator.MR_META_CHECK_LIMIT):
                meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
                meta[-1].callback({"result": {"layer_height": 0.2, "job_id": "1A2B"}}, None)
                self.qt.events(1)
                history = [r for r in self.transport.requests if r.channel == "mr-history"]
                # A MISMATCHED job id (not an empty history — an empty
                # history is unattestable and give-up-able by the
                # contract): the refusal is permanent.
                history[-1].callback({"result": {"count": 1, "jobs": [{"job_id": "DIFFERENT"}]}}, None)
                self.qt.events(1)
                key = ("part.gcode", coordinator._files.job_key)
                if step < coordinator.MR_META_CHECK_LIMIT - 1:
                    self.assertEqual(coordinator._mr_metadata_for(*key), {})
                    tick[0] += 31.0
                    deliver(31 + step)  # each delivery differs so the poll refreshes
                    self.qt.events(1)
            # A mismatched job id is PROOF the payload describes a
            # different job — the give-up must never latch it (the
            # identity bleed the cross-check exists to prevent). The
            # anchors stay empty for the whole print.
            self.assertEqual(coordinator._mr_metadata_for(*key), {})

    def test_metadata_cross_check_gives_up_only_for_unattestable_replies(self):
        # The bounded give-up applies to the causes that cannot ATTEST
        # (the history request failed, the reply was unattestable) —
        # after the limit the payload latches with the failure flagged.
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client
        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1,
                           "extrude_factor": 1, "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        with patch.object(module, "time", fake_time):
            client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())
            self.qt.events(1)
            for step in range(coordinator.MR_META_CHECK_LIMIT):
                meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
                meta[-1].callback({"result": {"layer_height": 0.2, "job_id": "1A2B"}}, None)
                self.qt.events(1)
                history = [r for r in self.transport.requests if r.channel == "mr-history"]
                history[-1].callback(None, "boom")
                self.qt.events(1)
                key = ("part.gcode", coordinator._files.job_key)
                if step < coordinator.MR_META_CHECK_LIMIT - 1:
                    self.assertEqual(coordinator._mr_metadata_for(*key), {})
                    tick[0] += 31.0
                    client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())
                    self.qt.events(1)
            # After the limit: the payload latched, flagged.
            self.assertEqual(coordinator._mr_metadata_for(*key).get("layer_height"), 0.2)

    def test_metadata_reply_after_reset_never_latches(self):
        # A reply landing after a binding reset must not latch the old
        # job's payload (the stale-request guard) — and must not fire
        # a pointless history cross-check.
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())
        self.qt.events(1)
        meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
        self.assertEqual(len(meta), 1)
        coordinator.reset_binding()
        meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600, "job_id": "1A2B"}}, None)
        self.qt.events(1)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_metadata_for("part.gcode", ("part.gcode", 100, 1)), {})
        self.assertEqual([r for r in self.transport.requests if r.channel == "mr-history"], [])

    def test_metadata_request_keeps_subfolder_slashes(self):
        # Panel DOM-P2-3: the coordinator's URL escaped subfolder
        # separators to %2F while MoonrakerProtocol.metadata_endpoint
        # does not; picky proxies 404 the escaped form.
        self.monitor()
        client = self.follower.client
        status = {
            "print_stats": {"filename": "PLA/part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())
        self.qt.events(1)
        meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
        self.assertEqual(len(meta), 1)
        self.assertIn("PLA/part.gcode", meta[0].path)
        self.assertNotIn("%2F", meta[0].path)

    def test_one_core_landing_produces_at_most_one_publish(self):
        # G (the 2026-09-19 performance review): one data.changed
        # fans out through controls/toolhead/camera/console changes
        # into three or four full model projections. After the
        # coalescer, one core landing must publish at most once.
        # The counter patches the RUNTIME's class before construction:
        # the collaborator signals bind _publish at connect time, so
        # an instance patch would let the bound methods slip past it,
        # and the harness's package namespace is the only class
        # object the runtime uses.
        from unittest.mock import patch
        model_class = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel
        publishes = []
        original = model_class._publish
        def counting(self):
            publishes.append(1)
            return original(self)
        with patch.object(model_class, "_publish", counting):
            self.monitor()
            self.qt.events(1)
            self.deliver_state("standby")  # warm-up: the transition publishes
            self.qt.events(2)
            publishes.clear()
            # A representative HEARTBEAT: the same state again — the
            # observers run but nothing outward changed, so the whole
            # landing must collapse into one publish.
            self.deliver_state("standby")
            self.qt.events(2)
        self.assertGreaterEqual(len(publishes), 1, "the landing still publishes")
        self.assertLessEqual(len(publishes), 1,
                             "one core landing fans out into %d publishes" % len(publishes))

    def test_warm_heartbeats_make_no_persistence_reads(self):
        # H (the 2026-09-19 performance review): after hydration, the
        # monitor heartbeat must not parse settings.json or the
        # machine shard — the migration record is cached, the binding
        # serves its cached config, and an empty-but-loaded console
        # must not re-read its shard forever.
        from unittest.mock import patch
        model = self.monitor()
        self.qt.events(1)
        # Hydrate the console (an empty transcript is authoritative
        # once loaded) before the counting window.
        model.setConsoleExpanded(True)
        self.qt.events(1)
        state_store = self.qt.load("PluginPersistence").StateStore
        reads = []
        original = state_store.read
        def counting_read(self):
            reads.append(1)
            return original(self)
        with patch.object(state_store, "read", counting_read):
            for _ in range(20):
                self.deliver_state("standby")
                self.qt.events(1)
            for i in range(5):
                model._data._merge_aux({"extruder": {"temperature": 200.0 + i, "target": 210.0}})
                self.qt.events(1)
        self.assertEqual(reads, [], "warm heartbeats must not read the persistence files")

    def test_one_auxiliary_landing_produces_at_most_one_publish(self):
        # G: an auxiliary landing additionally fires auxiliaryChanged
        # -> _on_auxiliary -> _publish() on top of the changed
        # fanout; the coalescer must collapse the whole landing.
        from unittest.mock import patch
        model_class = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel
        publishes = []
        original = model_class._publish
        def counting(self):
            publishes.append(1)
            return original(self)
        with patch.object(model_class, "_publish", counting):
            model = self.monitor()
            self.qt.events(1)
            publishes.clear()
            model._data._merge_aux({"extruder": {"temperature": 200.0, "target": 210.0}})
            self.qt.events(2)
        self.assertGreaterEqual(len(publishes), 1, "the landing still publishes")
        self.assertLessEqual(len(publishes), 1,
                             "one auxiliary landing fans out into %d publishes" % len(publishes))

    def test_webcam_list_survives_a_failed_poll(self):
        # Panel ARCH-P3-1: endstops retain last-known states on error;
        # webcams used to blank on ANY failed poll ("no camera" during a
        # printer reboot). Now they follow the same retention principle.
        model = self.monitor()
        self.qt.events(1)
        webcams = [r for r in self.transport.requests if r.channel == "webcams"]
        self.assertEqual(1, len(webcams), "one in-flight webcam RPC at a time (the coalescer)")
        webcams[0].callback({"result": {"webcams": [{"name": "Front", "stream_url": "/webcam", "enabled": True}]}}, None)
        self.qt.events(1)
        self.assertEqual(model.webcamNames, ["Front"])
        # The landed reply reopens the gate: the next poll issues again.
        model.refreshWebcams()
        self.qt.events(1)
        later = [r for r in self.transport.requests if r.channel == "webcams"][1:]
        self.assertTrue(later)
        later[-1].callback(None, "boom")
        self.qt.events(1)
        self.assertEqual(model.webcamNames, ["Front"])

    def test_gcode_store_feed_appends_klippers_output_without_duplicates(self):
        # The console echo (the ruling): the store is polled
        # ONLY while the console is expanded, Klipper's response entries
        # land in the transcript feed, "!!" lines carry the error flag,
        # commands from the store are ignored (ours are already in the
        # pane), and a re-poll never repeats an entry.
        model = self.monitor()
        self.qt.events(1)
        self.assertEqual([r for r in self.transport.requests if r.channel == "console-store"], [])
        # The store polls at 1 s while PRINTING; an idle printer gets
        # the idle floor (5 s), so seed a printing state for the 1 s
        # cadence this test pumps.
        self.deliver_state("printing")
        model.setConsoleExpanded(True)
        store = [r for r in self.transport.requests if r.channel == "console-store"]
        self.assertEqual(len(store), 1)
        self.assertIn("server/gcode_store", store[0].path)
        store[0].callback({"result": {"gcode_store": [
            {"message": "M104 S200", "type": "command", "time": 9.0},
            {"message": "ok", "type": "response", "time": 10.0},
            {"message": "!! Heater extruder not heating", "type": "response", "time": 11.0},
        ]}}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertEqual([entry["text"] for entry in lines if entry["kind"] != "note"],
                         ["ok", "!! Heater extruder not heating"])
        feed = [entry for entry in lines if entry["kind"] != "note"]
        self.assertFalse(feed[0]["error"])
        self.assertTrue(feed[0]["success"])
        self.assertTrue(feed[1]["error"])
        self.assertFalse(feed[1]["success"])
        # The next poll repeats the old entries with one new line: the
        # last-seen stamp dedups and only the new line lands. (1200 ms:
        # the 1 s timer was started a hair before this pump, so a
        # 1000 ms window can end just short of its due point.)
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        self.assertTrue(later)
        later[-1].callback({"result": {"gcode_store": [
            {"message": "ok", "type": "response", "time": 10.0},
            {"message": "!! Heater extruder not heating", "type": "response", "time": 11.0},
            {"message": "Target reached", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertEqual([entry["text"] for entry in lines if entry["kind"] != "note"],
                         ["ok", "!! Heater extruder not heating", "Target reached"])
        # The transcript persists with the responses (after the
        # response-churn debounce's window).
        model._console._flush_debounced()
        transcript = self.stored_transcript()
        self.assertEqual([entry["text"] for entry in transcript], ["ok", "!! Heater extruder not heating", "Target reached"])
        # The store holds Klipper's output VERBATIM — Moonraker strips
        # nothing (data_store.py stores the payload as delivered) — and
        # modern Klipper's response lines carry no "ok" prefix at all
        # (the "ok" is the RPC result, never console output). So the
        # store can never attest success: a line that is neither "!!"
        # nor a "//" echo is inferred success.
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        later[-1].callback({"result": {"gcode_store": [
            {"message": "B:55.0 /55.0 T0:200.3 /200.0", "type": "response", "time": 13.0},
            {"message": "// Unknown command:\"HELLO\"", "type": "response", "time": 14.0},
        ]}}, None)
        self.qt.events(1)
        lines = [entry for entry in model.consoleLines.value() if entry["kind"] != "note"][-2:]
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[0]["error"])
        self.assertFalse(lines[1]["success"])
        self.assertFalse(lines[1]["error"])

    def test_console_store_seed_skips_the_stale_buffer_beyond_the_first_poll(self):
        # The expand seed is one-shot by design — but the entries it
        # skips must stay skipped for the whole session. The regression:
        # the second poll re-delivered Moonraker's entire stale buffer
        # (a live report of the console re-fetching the
        # printer's history on load).
        model = self.monitor()
        self.deliver_state("printing")
        model._data.set_console_expanded(True, 11.0)
        store = [r for r in self.transport.requests if r.channel == "console-store"]
        self.assertEqual(len(store), 1)
        store[0].callback({"result": {"gcode_store": [
            {"message": "old one", "type": "response", "time": 10.0},
            {"message": "old two", "type": "response", "time": 11.0},
            {"message": "fresh", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        # Plugin notes ("# Connected…") may interleave the feed — the
        # store feed itself must be exactly the fresh line.
        self.assertEqual([entry["text"] for entry in model.consoleLines.value() if entry["kind"] != "note"], ["fresh"])
        # The next poll repeats the same buffer: nothing new may land.
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        self.assertTrue(later)
        later[-1].callback({"result": {"gcode_store": [
            {"message": "old one", "type": "response", "time": 10.0},
            {"message": "old two", "type": "response", "time": 11.0},
            {"message": "fresh", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        self.assertEqual([entry["text"] for entry in model.consoleLines.value() if entry["kind"] != "note"], ["fresh"])

    def test_sweep_phase_advances_on_the_real_engine(self):
        # The sweep's position is a binding on the bar's sweepPhase; a
        # bare unqualified reference did NOT resolve through the visual
        # parent (ReferenceError, sweep frozen). Instantiate the exact
        # pattern on the real engine and pin that the phase moves.
        from PyQt6.QtQml import QQmlEngine, QQmlComponent
        from PyQt6.QtCore import QUrl
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData("""
import QtQuick 2.15
Item {
    id: probeRoot
    width: 300
    height: 40
    property bool improving: true
    property real progress: -1
    Item {
        id: probeBar
        objectName: "probeBar"
        anchors.fill: parent
        property real sweepPhase: 0
        NumberAnimation on sweepPhase {
            running: probeRoot.improving && probeRoot.progress < 0
            from: 0
            to: 1
            duration: 1000
            loops: Animation.Infinite
        }
        Rectangle {
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: parent.width / 3
            visible: probeRoot.improving && probeRoot.progress < 0
            x: (1 - Math.abs(2 * probeBar.sweepPhase - 1)) * (parent.width - width)
        }
    }
}
""".encode(), QUrl("sweep-pin.qml"))
        self.assertFalse(component.isError(), [str(e) for e in component.errors()])
        item = component.create()
        self.assertIsNotNone(item)
        from PyQt6.QtCore import QObject
        bar = item.findChild(QObject, "probeBar")
        before = bar.property("sweepPhase")
        # The harness pumps a timed event loop; 300 ms of animation
        # must move the phase.
        self.qt.events(300)
        after = bar.property("sweepPhase")
        self.assertNotEqual(after, before)  # the phase advances

    def test_bed_mesh_visibility_signal_chain_toggles_and_publishes(self):
        # The "Hide bed mesh does nothing in the empty
        # preview" report: the overlay's signal was never connected to the
        # presentation (pre-3.4.0 regression). Pin the presenter side
        # of the chain — the signal must flip the flag and republish.
        mesh = self.follower._runtime.bed_mesh
        presentation = self.follower._runtime.presentation
        self.assertTrue(mesh.visible)
        presentation.bedMeshVisibilityRequested.emit(False)
        self.assertFalse(mesh.visible)
        self.assertFalse(presentation._values.get("bedMeshVisible"))
        presentation.bedMeshVisibilityRequested.emit(True)
        self.assertTrue(mesh.visible)

    def test_preview_load_lights_the_monitor_improving_state(self):
        # The shared load state: a load kicked off from the PREVIEW
        # must show the Monitor's hourglass too (the sync
        # report), and both clear when the index lands.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        coordinator.request_load()
        coordinator.refresh()
        model._publish()
        self.assertTrue(model.improvingEta)
        self.assertEqual(model.improveEtaPhase, "Resolving…")
        # The index lands: both surfaces clear.
        model._print_state = lambda: SimpleNamespace(
            layer=SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=True,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertFalse(model.improvingEta)

    def test_preview_load_state_is_busy_until_terminal(self):
        # The Load current print button must stay disabled through the
        # whole download+index+render; the presentation carries the
        # busy state for the indicator.
        self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        presentation = self.follower._runtime.presentation
        coordinator.request_load()
        coordinator.refresh()
        self.assertTrue(presentation._values.get("loadBusy"))
        self.assertIn("Resolving current print…", presentation._values.get("loadPhase", ""))

    def test_load_request_clears_when_no_print_exists(self):
        # The stuck-"Resolving" report: a standby printer sends no
        # status frame, so observe() never clears the request — the
        # refresh-side clearing settles it from the known snapshot
        # state once the request is past its grace.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        presentation = self.follower._runtime.presentation
        coordinator.request_load()
        coordinator._loads._load_requested_at = 0.0  # an aged request
        coordinator.refresh()
        self.assertFalse(coordinator._loads.load_requested)
        self.assertFalse(presentation._values.get("loadBusy"))
        self.assertNotIn("Resolving current print…", presentation._values.get("loadPhase", ""))
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_preview_load_feedback_surfaces(self):
        card = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
        indicator = (PLUGINS / "LoadProgressIndicator.qml").read_text(encoding="utf-8")
        self.assertIn("enabled: !base.loadBusy", card)
        self.assertIn("LoadProgressIndicator {", card)
        # Declared on the root: undeclared dynamic names read as
        # undefined at load time and the bindings were dropped.
        self.assertIn("property bool loadBusy: false", card)
        self.assertIn("property real loadProgress: -1", card)
        self.assertIn("property string loadPhase: \"\"", card)
        # The Attach/Detach button HIDES without a toolpath (the
        # 2026-09-17 ruling — the follower has nothing to drive),
        # and the load button takes the whole row then.
        self.assertIn("visible: base.hasToolpath", card)
        self.assertIn("enabled: base.followingEnabled || base.followingPaused", card)
        self.assertIn("width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width", card)
        self.assertIn("indicatorBar.sweepPhase", indicator)
        self.assertIn("busy: false", indicator)
        presentation = (PLUGINS / "PreviewPresentation.py").read_text(encoding="utf-8")
        self.assertIn('("bedMeshVisibilityRequested", self.bedMeshVisibilityRequested.emit)', presentation)
        dialog = (PLUGINS / "MoonrakerUploadDialog.qml").read_text(encoding="utf-8")
        # Enter resolves through the dialog's own accepted signal; the
        # unresolved-close wedge heals in the device's requestWrite.
        self.assertIn("onAccepted: {", dialog)
        self.assertIn("onClicked: base.accept()", dialog)
        device = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        self.assertIn("A closed-but-unresolved dialog resets here", device)

    def test_console_qml_surface(self):
        for token in ("id: consoleSection", '"G-code command…"', "sendConsoleCommand(",
                      "clearConsoleHistory()", 'text: "Console"',
                      "Keys.onReturnPressed", "Keys.onUpPressed", "Keys.onDownPressed",
                      '"monospace"', "id: consoleText",
                      "consoleRecallIndex", "consoleDraft",
                      # The pane is ONE rich TextEdit (multi-line
                      # selection) with the terminal-feed colours and
                      # the append-with-selection-restore sync; the face
                      # itself is picked at runtime (monoFamily pin).
                      "consoleSyncLines", "consoleLineHtml",
                      "textFormat: TextEdit.RichText", "selectionStart",
                      "wasAtEnd", "consoleFlick",
                      # Appends must land on fresh lines, the ring
                      # rotation must rebuild, and the rebuild SETS the
                      # document directly (clear+insert produced an
                      # empty pane in the real engine).
                      "consoleText.text = html",
                      'html = "<br>" + html',
                      "consoleDroppedSeen",
                      "consoleRevisionsSeen",
                      "root.printer.setConsoleExpanded(expanding)",
                      'setConsoleExpanded(root.printer.sectionExpandedMap["console"] !== false)',
                      # The console is a collapsing pane beneath the
                      # webcam: a top-left chevron toggle, a "Console"
                      # title in the panes' style, and the camera fills
                      # the pane only while it is collapsed.
                      'sectionExpandedMap["console"]',
                      # The toggle keeps the other panes' button
                      # style with the theme's up/down chevrons inside
                      # it (the rulings).
                      "ChevronSingleUp",
                      "ChevronSingleDown",
                      "fixedWidthMode: true",
                      "consoleCollapseButton",
                      # Terminal ethics: follow the tail ONLY while at it
                      # and not selecting.
                      "consoleLines", "selectByMouse",
                      "server/gcode_store?count=100"):
            self.assertIn(token, MONITOR_QML + (PLUGINS / "MonitorData.py").read_text(encoding="utf-8"))
        # The input row's hit-region contract (the 5.11/5.12 sweep): the
        # field shrinks and clips INSIDE its own cell, so Send and Clear
        # keep theirs and the presses aimed at them land on them.
        input_cell = MONITOR_QML[MONITOR_QML.index('objectName: "moonrakerConsoleInput"'):
                                 MONITOR_QML.index('objectName: "moonrakerConsoleSend"')]
        self.assertIn("Layout.minimumWidth: 0", input_cell)
        self.assertIn("clip: true", input_cell)
        self.assertIn('objectName: "moonrakerConsoleClear"', MONITOR_QML)
        # The webcam pane's title moved with the card (CameraPane.qml).
        self.assertIn('text: "Webcam"', CAMERA_PANE_QML)
        # The poll gate opens on printer attach — never wired to the
        # info pane's collapse (infoCollapsed defaults to false, which
        # left the feed dead in the default layout). The collapse
        # handlers that exist (4.4.0) drive ONLY the readout fits —
        # their bodies are pinned to the update calls.
        self.assertNotIn("setConsoleExpanded(!root.infoCollapsed)", MONITOR_QML)
        self.assertIn("onInfoCollapsedChanged: {", MONITOR_QML)
        self.assertIn("Qt.callLater(root.updateInfoReadoutFits)", MONITOR_QML)
        self.assertIn("fitInfoRetry.restart()", MONITOR_QML)
        self.assertIn("onStatusCollapsedChanged: {", MONITOR_QML)
        self.assertIn("Qt.callLater(root.updateStatusReadoutFits)", MONITOR_QML)
        self.assertIn("fitStatusRetry.restart()", MONITOR_QML)
        # The feed's three voices (the live rulings):
        # commands carry ">", Moonraker's responses carry "<", and the
        # plugin's notes carry "#" in amber. Responses render bright
        # red/green; saved commands keep their text light grey and put
        # the verdict on the ">" prompt only — green matches the input
        # row's prompt, red is a failure — and the restored hues are
        # contrast-bumped (the old muted family sat near 2:1).
        for token in ('MoonrakerTheme.errorRed', 'MoonrakerTheme.consoleSuccess', 'MoonrakerTheme.consolePromptError', 'MoonrakerTheme.successGreen', 'MoonrakerTheme.consoleWarn',
                      '&gt; "', '&lt; "', '# "', 'MoonrakerTheme.consoleCommand', 'MoonrakerTheme.consoleHistoryError', 'MoonrakerTheme.consoleHistorySuccess'):
            self.assertIn(token, MONITOR_QML)
        # The selection is captured BEFORE the rebuild wipe (the old
        # order made the restore a silent no-op); the rotation rebuild
        # compensates the content dropped above the viewport.
        self.assertLess(MONITOR_QML.index("var selStart"), MONITOR_QML.index("consoleDroppedSeen = dropped;"))
        for token in ("prevTextHeight", "rotationDrop"):
            self.assertIn(token, MONITOR_QML)
        # The scrollbar's handle drag cancels the pending restore (it
        # drives contentY directly and never fires onMovementStarted).
        self.assertIn("onPressedChanged:", MONITOR_QML)
        self.assertIn("if (pressed)", MONITOR_QML)
        for token in ("consoleHistory", "consolePending", "consoleChanged",
                      "consoleLines", "consoleDropped", "def setConsoleExpanded(",
                      "def sendConsoleCommand(", "def clearConsoleHistory(",
                      "filamentUsed", "filamentRemaining"):
            self.assertIn(token, MONITOR_MODEL)
        self.assertNotIn("consoleStatus", MONITOR_MODEL)
        # The filament rows are caption/value grid rows placed AFTER
        # the Finish row (the chosen placement). They outlive the
        # print through complete/cancelled until the next job starts
        # (the UX panel): the gate is the model's readout flag, not
        # printActive.
        self.assertIn('text: "Filament used"', JOB_SECTION_QML)
        self.assertIn('text: "Filament remaining"', JOB_SECTION_QML)
        self.assertLess(JOB_SECTION_QML.index('text: "Finish"'), JOB_SECTION_QML.index('text: "Filament used"'))
        # NO-REFLOW RULE: the rows are permanent — the values read "—"
        # until Klipper reports them; nothing hides them any more, and
        # the readout-visibility gate is gone from the model too.
        self.assertNotIn('visible: root.printer != null && root.printer.filamentReadoutVisible', MONITOR_QML)
        self.assertNotIn("filamentReadoutVisible", MONITOR_MODEL)
        # The z-offset nudge buttons take an exact quarter of the row
        # (a bound preferred width, not layout distribution): fillWidth
        # alone left "↑ 0.005" wider than "↑ 0.05" (the report).
        self.assertIn("Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4", TUNING_SECTION_QML)
        # The expanded chart's power axis carries its 0-100% legend,
        # pinned (never scaled), drawn INSIDE the plot's right edge
        # (the live ruling — the outside gutter's last glyph clipped
        # at the card edge), painted last so the data never covers it.
        self.assertIn("function _rightGutter()", TEMP_CHART_QML)
        self.assertIn('ctx.fillText("100%", plotWidth - 4, 4 + ascent)', TEMP_CHART_QML)
        self.assertIn('ctx.fillText("0%", plotWidth - 4, plotBottom - descent - 1)', TEMP_CHART_QML)
        self.assertNotIn("fillRect(chipX", TEMP_CHART_QML)

    def test_console_resize_handle_surface(self):
        # 3.6.0: the console card's TOP edge is a drag handle. The
        # load-bearing semantics are pinned here — the pane-bounds clamp
        # window, the pane-frame drag measurement, the single commit on
        # release, and the collapse behaviour (the request: pull
        # the pane down to the collapse position and it collapses; drag
        # back out and it expands).
        for token in ("id: consoleResizeHandle",
                      'objectName: "consoleResizeHandle"',
                      'objectName: "consoleResizeArea"',
                      "cursorShape: Qt.SizeVerCursor",
                      # The handle holds its OWN strip: an overlay across
                      # the header row would eat the collapse toggle's
                      # hit area.
                      "Layout.preferredHeight: consolePanel.consoleHandleHeight",
                      # The pointer is read in the PANE's frame, never the
                      # handle's own: the handle rides the edge it moves,
                      # so a local measurement is self-referential (the
                      # run-away-the-pointer bug).
                      "mapToItem(cameraArea, mouse.x, mouse.y)",
                      "consolePanel.consoleResizeStartHeight = consolePanel.height",
                      "consoleResizeStartHeight + (consoleResizeStartY - paneY)",
                      "consoleResizeTo",
                      "consoleResizeCommit",
                      "onCanceled: consolePanel.consoleResizeCommit()",
                      # The collapse position is the drag FLOOR, not a
                      # jump: the height is continuous across it, so the
                      # edge never detaches from the pointer.
                      "Math.max(consoleCollapsedHeight, Math.min(consoleMaxHeight, height))",
                      "consoleSetExpanded(height > consoleCollapsedHeight + 0.5)",
                      "root.printer.setConsoleHeight(Math.round(height))",
                      # The clamp window and the stored/effective heights.
                      "consoleCollapseButton.height + 2 * UM.Theme.getSize(\"thin_margin\").height + consoleHandleHeight",
                      "root.printer.consoleHeight > 0 ? root.printer.consoleHeight : consoleDefaultHeight",
                      "Math.max(consoleMinHeight, Math.min(consoleMaxHeight, consoleStoredHeight))",
                      "consoleDragHeight > 0 ? consoleDragHeight : consoleSettledHeight",
                      "Layout.preferredHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight",
                      # The tail stays pinned through a resize (a reader
                      # scrolled up is never yanked — the golden rule).
                      "consoleFlick.restoreScrollPending = true",
                      "Drag to resize the console.",
                      # The grip reads as a grab bar at a glance (the
                      # live ruling: the first one was too
                      # subtle), and the closing pane fades its body out
                      # instead of crushing it through the transition.
                      "width: 72 * screenScaleFactor",
                      "height: 5 * screenScaleFactor",
                      "opacity: consolePanel.consoleBodyOpacity",
                      "consoleBodyOpacity",
                      # The fade starts where the body stops fitting, not
                      # at the collapse position: a gradual fade left the
                      # crushed input row fully opaque for most of the
                      # travel (a second live report).
                      "readonly property real consoleBodyFadeSpan: 24 * screenScaleFactor",
                      "(height - (consoleMinHeight - consoleBodyFadeSpan)) / consoleBodyFadeSpan"):
            self.assertIn(token, MONITOR_QML)
        for token in ("consoleHeight", "consoleHeightChanged", "def setConsoleHeight(",
                      "CONSOLE_HEIGHT_MAX", "def _state_height("):
            self.assertIn(token, MONITOR_MODEL)
        # The handle sits ABOVE the header row in the layout, never over
        # it, and the card's height binding owns both states.
        handle_start = MONITOR_QML.index("id: consoleResizeHandle")
        button_start = MONITOR_QML.index("id: consoleCollapseButton")
        self.assertLess(handle_start, button_start)
        self.assertIn("consoleResizeCommit", MONITOR_QML[handle_start:button_start])
        # The card clips: mid-drag it is SHORTER than its inner column's
        # minimum, and its content must never paint over the webcam card.
        card_start = MONITOR_QML.index("id: consolePanel")
        self.assertIn("clip: true", MONITOR_QML[card_start:handle_start])
        # The well clips too, and that is a live report: the
        # prompt, the input and its buttons live INSIDE the black border,
        # so a squeezed column must cut them at the well's own edge
        # rather than letting them float outside the terminal's
        # background. The slice ends at the flick's own clip.
        well_start = MONITOR_QML.index("id: consoleWell")
        flick_start = MONITOR_QML.index("id: consoleFlick")
        self.assertLess(well_start, flick_start)
        self.assertIn("clip: true", MONITOR_QML[well_start:flick_start])
        # The pane frame is not optional: a local-coordinate delta is the
        # bug this pin exists to prevent.
        self.assertNotIn("consoleResizeStartY = mouse.y", MONITOR_QML)
        self.assertNotIn("consoleResizeStartY - mouse.y", MONITOR_QML)
        # The drag commits ONCE, on release — a commit per move would
        # rewrite the state file at pointer rate.
        self.assertNotIn("setConsoleHeight(Math.round(height))", MONITOR_QML[handle_start:button_start])

    def test_capture_harness_mocks_every_live_input(self):
        # Determinism discipline: the captures must not read ANY live
        # input. The wall clock slipped through once — the formatter's
        # monitorFinish called datetime.now() and captures made in
        # different minutes differed by one clock glyph, failing CI's
        # byte-compare. The harness must freeze BOTH wall-clock readers
        # (the formatter's finish clock and PreviewFollower's ETA
        # finish), and because the Qt runtime registers plugin modules
        # under synthetic names (the same trap as the model below), it
        # must patch EVERY module object loaded from each frozen source
        # file, after the plugin tree has loaded.
        self.assertIn("class FrozenDatetime", CAPTURE_HARNESS)
        self.assertIn("def now(cls, tz=None)", CAPTURE_HARNESS)
        self.assertIn("MonitorFormatting.py", CAPTURE_HARNESS)
        self.assertIn("PreviewFollower.py", CAPTURE_HARNESS)
        self.assertIn("_freeze_plugin_clocks()", CAPTURE_HARNESS)
        # The model's own time reference stays patched module-scoped, so
        # the synthetic history seeds from a fixed clock.
        self.assertIn('patch.object(model_module, "time", fake_time)', CAPTURE_HARNESS)
        # The console pane renders no caret: a blinking cursor made the
        # captures phase-dependent.
        self.assertIn("cursorVisible: false", MONITOR_QML)

    def test_no_controls_disappear_controls_disable(self):
        # NO-REFLOW RULE (the ruling, 2026-09-10): no control
        # ever disappears — it disables. Nothing reflows unless the
        # user asked for it (section collapse, resize, and the 4.4.0
        # section hide/reorder rulings). The jog-reflow
        # hazard came from pause/cancel (and other state-gated controls)
        # vanishing and returning, shifting the pane under the pointer.
        #
        # STRUCTURAL pin (the panel's upgrade): every `visible:` in
        # every plugin QML whose expression is not whitelisted must be
        # on the explicit allow-list — so a new state-gated visibility
        # cannot slip through a reformat or a new file.
        # The file-manager popup joins the carve-out by the
        # round-2 ruling ("Reflowing the file manager is fine, there's
        # nothing critical on that") — but ONLY its own file: the
        # Monitor files must never be exempt, and the set must not
        # grow silently (round-2 security F13). Inside the popup the
        # chrome still uses enabled/opacity, never visible:.
        exempt_files = {"MoonrakerFollowerConfiguration.qml", "MoonrakerUploadDialog.qml"}
        self.assertEqual(exempt_files, {"MoonrakerFollowerConfiguration.qml", "MoonrakerUploadDialog.qml"})
        for monitor_file in ("MoonrakerMonitor.qml", "MoonrakerMonitorDashboard.qml", "MoonrakerPreviewCard.qml"):
            self.assertNotIn(monitor_file, exempt_files)
        # The camera's configured gate moved into CameraPane as
        # `configured` (read there as root.configured): the token
        # follows the code, so the pane's veil and Live badge stay
        # reviewed under this rule.
        whitelist = (
            "openPopOver", "sectionExpandedMap", "sectionHiddenMap", "Collapsed", "platformActivity",
            "previewStageActive", "configuredForFollowing", "modelData.type", "hasWhite",
            "root.configured", "tooltipText", "sectionIcon", "macroParameters",
            "webcamNames", "root.busy", "root.progress", "improveEtaProgress",
            "temperatureChartLegend.series", "allChartSensorsHidden", "selectedChartSensor",
            "hoverClockProxy", "root.compact",
        )
        # The whitelist is itself frozen (round-2 security F13: the
        # set must not grow silently) — an addition is a visible diff.
        self.assertEqual(whitelist, (
            "openPopOver", "sectionExpandedMap", "sectionHiddenMap", "Collapsed", "platformActivity",
            "previewStageActive", "configuredForFollowing", "modelData.type", "hasWhite",
            "root.configured", "tooltipText", "sectionIcon", "macroParameters",
            "webcamNames", "root.busy", "root.progress", "improveEtaProgress",
            "temperatureChartLegend.series", "allChartSensorsHidden", "selectedChartSensor",
            "hoverClockProxy", "root.compact",
        ))
        allowed = {
            # Capability-static gates (the UX panel's ruling): these
            # only change on a printer switch, which is user-initiated.
            "visible: root.printerModel != null && root.printerModel.hasQuadGantryLevel",
            "visible: root.printerModel != null && root.printerModel.hasBedMesh",
            # The plate's toolhead dot: scene-graph decoration INSIDE
            # the canvas's reserved slot — it can never shift layout,
            # only its own marker can appear inside the fixed map.
            "visible: root._plot != null && root.dot != null && root.dot.valid === true",
            "visible: mapping._plot != null && root.dot != null && root.dot.valid === true",
            # The follower's unavailable state: an overlay INSIDE the
            # face's own slot (the popover is a transient surface),
            # never a layout shift.
            "visible: !root.available()",
            "visible: !root.available() && !root.compact",
            "visible: !root.available() && root.compact",
            # The mapping hides while the index is unavailable: the
            # bed grid must not sit under the download offer's text —
            # inside the face's own slot, never a layout shift.
            "visible: root.available()",
            # The objects list's current-row bar: a highlight behind
            # the text, never a layout shift.
            "visible: modelData.current",
            # The picker's printed-legend row and its index offer:
            # the printed state derives from the index, so both gate
            # on its availability — inside the transient card.
            "visible: root.printer != null && root.printer.plateProgressAvailable",
            "visible: root.printer != null && root.printer.plateHasObjects && !root.printer.plateProgressAvailable",
            # The scope's right-edge tick: the majors draw one full
            # line, the halves and quarters an edge pair — scene-graph
            # decoration inside the scope.
            "visible: !major",
            # The picker's gate (the live ruling): during a print the
            # section shows the plate when the data exists, the
            # download offer otherwise; it clears with the job epoch.
            "visible: root.printer != null && root.printer.sectionHiddenMap[\"plate\"] !== true && (root.printer.printActive || root.printer.plateHasObjects)",
            # The picker's download offer: inside the transient card,
            # shown while the plate is empty.
            "visible: root.printer != null && !root.printer.plateHasObjects",
            # The follower's dot rides the layers: no index, no dot
            # (scene decoration inside the canvas slot).
            "visible: root.available() && mapping._plot != null && root.dot != null && root.dot.valid === true",
            # Firmware-regulated fans swap the slider for a read-only
            # row (a live report): the model's writable
            # flag picks the face.
            "visible: modelData.writable",
            "visible: !modelData.writable",
            # Carve-outs awaiting the ruling (DECISIONS round 6):
            "visible: base.followingEnabled && base.pauseAtLayerActive && base.pauseAtLayerItems.length > 0 && (base.hasToolpath || base.pauseAtLayerHasBaked)",
            # The toolpath-gated faces (the 2026-09-17 rulings): the
            # attach control, the pause button and its selection line
            # hide without a toolpath; the clear-all hides while only
            # baked rows are listed; the layer/height readout row
            # hides whole while the resolver has no layer.
            "visible: base.hasToolpath",
            "visible: base.pauseAtLayerHasClearable",
            "visible: base.layerReadoutAvailable",
            # The preview card's layer/height row pair (the readout
            # ruling) — written imperatively from the readout's own
            # availability signal. The height is independently
            # optional, so it carries its own gate.
            "visible: layerHeightRowsVisible",
            "visible: heightReadoutAvailable",
            # The availability gates (the live ruling): unavailable
            # values hide their glyphs and cells whole; the X/Y/Z
            # tuple hides when any one axis is absent.
            "visible: root.positionAvailable",
            "visible: root.zOffsetAvailable",
            "visible: root.flowAvailable",
            "visible: root.etaAvailable",
            "visible: root.finishAvailable",
            "visible: root.layerCountAvailable",
            "visible: root.infoHotendText !== \"—\"",
            "visible: root.infoBedText !== \"—\"",
            # The status strip's progress group (the stacked bar's
            # glyph, label and track share the print gate).
            "visible: root.printer != null && root.printer.printActive",
            "visible: root.printer != null && root.printer.printActive && root.printer.monitorEta !== \"—\"",
            "visible: root.printer != null && root.printer.printActive && root.printer.monitorFinish !== \"—\"",
            "visible: root.printer != null && root.printer.monitorLayer !== \"—\"",
            "visible: root.printerModel != null && root.printerModel.monitorPositionX !== \"—\" && root.printerModel.monitorPositionX !== \"\" && root.printerModel.monitorPositionY !== \"—\" && root.printerModel.monitorPositionY !== \"\" && root.printerModel.monitorPositionZ !== \"—\" && root.printerModel.monitorPositionZ !== \"\"",
            # The next-pause row is a PERMANENT slot (the M117
            # precedent): its visibility flip reflowed the section
            # stack and fed a layout polish loop (the live report).
            # Its labels read empty while no pause lies ahead.
            "visible: root.printerModel != null && root.printerModel.nextPauseFraction >= 0",
            "visible: root.printer != null && root.printer.nextPauseFraction >= 0",
            # The preview strip's own derived validity (computed in
            # updateStrip, not a model value).
            "visible: stripValid",
            # The Endstops summary row yields to the chips once they
            # exist (the live ruling — the chips ARE the
            # readout); it sits below the jog pad.
            "visible: root.printerModel == null || root.printerModel.endstopItems.length === 0",
            "visible: root.miniHasSeries",
            # The collapsed status readout's relevance gates (the bars
            # show only while they mean something), plus the fit
            # conjunction the imperative update writes (4.4.0).
            "visible: root.printer != null && root.printer.printActive && fitVisible",
            "visible: root.printer != null && root.printer.monitorLayerProgress >= 0 && fitVisible",
            # The controls configure pop-up's own switch and its scrim
            # (the one-pane dashboard has no openPopOver family).
            "visible: root.configurePaneOpen !== \"\"",
            "visible: root.configurePaneOpen === \"controls\"",
            # The filter rows' marker pair: radio-ness is static per
            # category, so the circle and the native checkbox swap by
            # it (the Uranium-controls ruling) — the selection state
            # lives inside the marker.
            "visible: modelData.radio",
            "visible: !modelData.radio",
            "visible: root.printerModel != null && !root.miniHasSeries",
            # The console error bell (the live request) is a
            # presence signal, not a session gate: it shows only
            # while an unseen error waits and the console is
            # collapsed.
            "visible: root.printer != null && root.printer.consoleErrorBell",
            # The Objects section's empty-state line (the
            # live request): the list arrives mid-print, an empty one
            # says so.
            "visible: root.printerModel != null && root.printerModel.excludeObjectItems.length === 0",
            # The console grab bar hides under the auto-collapse
            # width (the live ruling — a resize handle for
            # an expansion that cannot happen is a lie).
            "visible: !consolePanel.tooNarrow",
            # The file manager's popup: reflow is fine there, nothing critical on it (the ruling, ROADMAP 3.6.0) — each state-gated entry lands here by name.
            "visible: open",
            # The chart hover tooltip's rows follow the legend's
            # visibility: a floating pop-over whose reflow is its
            # nature (the file manager's carve-out precedent) — the
            # rows must not linger as "—" ghosts for hidden sensors.
            "visible: modelData.visible",
            # The what's-new overlay: the pre-collapsed sections ARE
            # the feature (the latest entry open, previous versions
            # gated behind their headers) — each gate lands here by
            # name.
            "visible: !modelData.isLatest",
            "visible: modelData.isLatest || entry.open",
            # The overlay's scroll chevrons (the file manager's
            # idiom): they appear only while more content hides off
            # the scrolled edge.
            "visible: flick.height > 0 && flick.contentY > 2",
            "visible: flick.height > 0 && flick.contentY < flick.contentHeight - flick.height - 2",
            # The preview card's pause-list chevrons: the same idiom,
            # on the capped five-row ListView.
            "visible: pauseListView.height > 0 && pauseListView.contentY > 2",
            "visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2",
            # The bed-mesh legend collapses when the mesh is hidden —
            # the reflow was granted (the card reflows instead
            # of keeping a faded gap).
            "visible: base.bedMeshAvailable && base.bedMeshVisible",
            # The monitor's loading prompt: the printer binding not
            # resolved yet (the entry window) or connected with no
            # data landed (the 2026-09-16 request).
            "visible: !root.statusCollapsed && (root.printer == null || root.printer.monitorLoading)",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"ready\" && confirmThumb.status !== Image.Error",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"loading\"",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"failed\" || root.thumbStateLarge(root.confirmRelpath()) === \"none\"",
            "visible: root.deleteBlockedCount() > 0",
            "visible: root.printerModel != null && root.printerModel.fileRenameConflict",
            "visible: root.uploadProgressState() === \"uploading\"",
            "visible: root.uploadProgressState() === \"failed\"",
            "visible: root.thumbState(modelData.relpath) === \"ready\" && recentsThumb.status !== Image.Error",
            "visible: root.thumbState(modelData.relpath) === \"loading\"",
            "visible: root.thumbState(modelData.relpath) === \"failed\" || root.thumbState(modelData.relpath) === \"none\"",
            "visible: !root.narrowMode",
            "visible: root.printerModel == null || root.printerModel.fileManagerSearch.length === 0",
            'visible: root.printerModel != null && root.printerModel.fileManagerSearch.length > 0 && modelData.folder !== ""',
            "visible: root.printerModel != null && root.printerModel.fileManagerHistoryLoaded > 0 && !root.printerModel.fileManagerHistoryExhausted",
            "visible: searchField.text.length > 0",
            "visible: root.filterActive(\"slicer\")",
            "visible: !root.filterActive(\"slicer\")",
            "visible: root.filterActive(\"modified\")",
            "visible: !root.filterActive(\"modified\")",
            "visible: root.filterActive(\"print_time\")",
            "visible: !root.filterActive(\"print_time\")",
            "visible: root.filterActive(\"never_printed\")",
            "visible: !root.filterActive(\"never_printed\")",
            "visible: !root.narrowMode && root.printerModel != null && root.printerModel.fileManagerSearch.length === 0 && (root.printerModel.fileManagerDirectory.length > 0 || root.activeDirectories.length > 0)",
            "visible: root.printerModel != null && root.printerModel.fileManagerDirectory.length > 0",
            "visible: root.narrowMode",
            "visible: modelData.printing === true",
            "visible: root.thumbState(modelData.relpath) === \"ready\" && thumbImage.status !== Image.Error",
            "visible: modelData[0] === \"Status\" && root.rowChecked(rowDelegate.rowData)",
            "visible: gridVertical.height > 0 && gridVertical.contentY > 2",
            "visible: gridVertical.height > 0 && gridVertical.contentY < gridVertical.contentHeight - gridVertical.height - 2",
            "visible: root.printerModel != null && root.printerModel.fileManagerWalkError !== \"\"",
            "visible: root.printerModel != null && root.activeRows.length === 0",
            "visible: root.printerModel != null && (root.walkErrorText() !== \"\" || (root.printerModel.fileManagerRefreshedAt !== \"Not yet refreshed\" && root.printerModel.fileManagerEmptyKind === \"over_filtered\"))",
            "visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0",
            "visible: root.printerModel == null || root.activeRows.length > 0",
            # The camera image's static hidden default (the duplicate-
            # start fix): visible arrives ONLY through applyCamera, so
            # the false default is the owned state, not a disappearing
            # control.
            "visible: false",
        }
        for path in sorted(PLUGINS.glob("*.qml")):
            if path.name in exempt_files:
                continue  # settings dialogs carve-out (user-opened surfaces)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = re.search(r"(visible:\s*.+)$", line)
                if not match:
                    continue
                expression = match.group(1).rstrip()
                if any(token in expression for token in whitelist):
                    continue
                # The extrude distance/speed rows highlight their
                # SELECTION by swapping button faces (the
                # live report: the boxes never stayed highlighted) —
                # one family, one carve-out, not ten near-identical
                # whitelist entries.
                if re.match(r"visible: (root\.printerModel == null \|\| root\.printerModel\.(extrudeDistance|extrudeSpeed) !== \d+|root\.printerModel != null && root\.printerModel\.(extrudeDistance|extrudeSpeed) === \d+)$", expression):
                    continue
                # The tooltip-popup family (the tooltip rule): the
                # popup's hover-driven visibility is not a control
                # disappearing — one pattern covers every unique
                # HoverHandler id.
                if re.match(r"visible: (parent\.hovered|tooltipHover\d+\.hovered( && root\.tooltipText\.length > 0)?)$", expression):
                    continue
                self.assertIn(expression, allowed,
                              f"{path.name}:{number}: state-gated visible: {expression}")
        # The hide masks: one sectionHiddenMap occurrence per section
        # (Dashboard 14 controls — objects joined in 4.6.0; Monitor 4
        # information + 6 status after the move and the follower's own
        # section).
        # A new adopter trips the count — the whitelist's substring
        # blessing must not cover an unbounded family.
        for monitor_file, expected in (("MoonrakerMonitorDashboard.qml", 14),
                                       ("MoonrakerMonitor.qml", 10)):
            self.assertEqual(
                (PLUGINS / monitor_file).read_text(encoding="utf-8").count("sectionHiddenMap["),
                expected, monitor_file)
        # The replacement: every SESSION state lives in `enabled`.
        for enabled in (
            "enabled: root.printerModel != null && root.printerModel.canPausePrint",
            "enabled: root.printerModel != null && root.printerModel.canResumePrint",
            "enabled: root.printerModel != null && root.printerModel.canCancelPrint",
            "enabled: root.printerModel != null && root.printerModel.monitorConnected && !root.printerModel.actionBusy && root.printerModel.printActive && root.printerModel.sectionReason === \"\" && root.printerModel.currentObjectName !== \"\"",
            "enabled: root.printer != null && root.printer.monitorConnected && root.printer.consoleLines.length > 0",
            "enabled: base.bedMeshAvailable",
        ):
            self.assertIn(enabled, MONITOR_QML + DASHBOARD_QML + PREVIEW_CONTROLS_QML + PRINT_SECTION_QML + OBJECTS_SECTION_QML)
        # The Preview load button keeps its full width: the follow button
        # no longer vanishes to widen it. The attach-gate round made the
        # width conditional on the toolpath (the hidden follow button
        # leaves the load button the whole row).
        self.assertIn("width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width", PREVIEW_CONTROLS_QML)

    def test_disconnected_disables_every_monitor_control(self):
        # The ruling (2026-09-10): while DISCONNECTED no
        # Monitor-page control is enabled — the emergency stop included.
        # The model publishes the connection state; the section gates,
        # the console, the camera refresh and the emergency stop all
        # disable on it.
        self.assertIn("monitorConnected", MONITOR_MODEL)
        self.assertIn("enabled: root.printerModel != null && root.printerModel.monitorConnected", FILE_MANAGER_SECTION_QML)
        self.assertIn("enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)", FANS_SECTION_QML + LEDS_SECTION_QML + PWM_SECTION_QML + POWER_SECTION_QML + SYSTEM_SECTION_QML + SAVE_SECTION_QML + TUNING_SECTION_QML + PRINT_SECTION_QML + SETUP_SECTION_QML + TOOLHEAD_SECTION_QML + MACROS_SECTION_QML + PROFILES_SECTION_QML + FILE_MANAGER_SECTION_QML)
        # The abs/rel word's CLICK obeys the same gate as its styling —
        # a locked control must not act (caught in testing).
        self.assertIn("root.printerModel != null && root.printerModel.jogEnabled", TOOLHEAD_SECTION_QML)
        self.assertIn("enabled: root.printer != null && root.printer.monitorConnected", MONITOR_QML)
        # The console is special: the SECTION stays enabled while
        # disconnected (scrolling, selecting and copying the restored
        # history keep working — the ruling); only the input,
        # Send and Clear disable. The well itself turns grey so the
        # disconnected state is obvious.
        self.assertIn("enabled: root.printer != null\n                                property int consoleRecallIndex", MONITOR_QML)
        self.assertIn('color: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline', MONITOR_QML)
        self.assertIn("anchors.bottom: parent.bottom", MONITOR_QML)
        # The connection DOT rides the Printer status pane's title in
        # BOTH pane states (expanded header and the collapsed strip) —
        # the chosen spot. Plus the camera's Live badge and
        # the disconnected grey veil over stale frames.
        self.assertIn("connectionDotColour", MONITOR_QML)
        self.assertIn('text: root.printer != null && root.printer.monitorConnected ? (root.printer.connectionDetail.length > 0 ? "Connected to Moonraker — " + root.printer.connectionDetail + "." : "Connected to Moonraker.") : "Disconnected from Moonraker."', MONITOR_QML)
        self.assertIn("id: statusCollapsedTitle", MONITOR_QML)
        self.assertIn('text: "Live"', CAMERA_PANE_QML)
        self.assertIn('color: MoonrakerTheme.cameraVeil', CAMERA_PANE_QML)
        self.assertIn('text: (root.printerModel != null && root.printerModel.cameraRecovering) ? "Camera recovering…" : "Camera offline"', CAMERA_PANE_QML)
        model = self.monitor()
        # The harness may connect asynchronously during construction —
        # pin the TRANSITIONS, which are synchronous.
        self.follower.client.connectionChanged.emit(False, "offline")
        self.assertFalse(model.monitorConnected)
        # A flapping link re-emits the same state — the console notes
        # only real transitions, never repeats.
        self.follower.client.connectionChanged.emit(False, "offline")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(len([entry for entry in model.consoleLines.value()
                              if entry["kind"] == "note" and "Disconnected" in entry["text"]]), 1)
        self.deliver_state("standby")
        self.assertTrue(model.monitorConnected)

    def test_no_bisect_debris_and_the_console_is_visible(self):
        # The 3.5.0 release shipped with the console behind a
        # "visible: false // BISECT" flag and six labels stripped of
        # their elide — both invisible to token pins. Behavioural pins:
        # no BISECT markers may exist, and the console section must not
        # carry a visibility gate.
        self.assertNotIn("BISECT", MONITOR_QML)
        self.assertNotIn("BISECT", DASHBOARD_QML)
        console = MONITOR_QML[MONITOR_QML.index("id: consoleSection"):MONITOR_QML.index("id: consoleInput")]
        self.assertNotIn("visible: false", console)

    def test_publishes_without_aux_do_not_append_history(self):
        # The feed is revision-gated: a publish with no auxiliary
        # arrival must leave the history revision untouched (the old
        # per-publish feed duplicated samples and halved the window).
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0}})
        model._data.auxiliaryChanged.emit()
        revision = model._history.revision
        model._data._update(core={"print_stats": {"state": "printing"}})
        self.assertEqual(model._history.revision, revision)

    def test_connect_transition_fires_every_lane_immediately(self):
        # A live report: after the connect the aux lanes
        # stayed unpopulated until their timers' next ticks. The
        # connection transition itself must fire every lane — the
        # request traffic grows the moment the connection lands.
        self.monitor()
        self.qt.events(2)
        before = len(self.transport.requests)
        self.deliver_state("standby")
        self.qt.events(2)
        self.assertGreater(len(self.transport.requests), before)

    def test_endstop_query_uses_the_documented_get(self):
        model = self.monitor()
        # The poll is readiness-gated; seed a ready Klippy so the
        # request fires (see test_endstop_poll_waits_for_klippy_ready).
        model._data._update(server={"klippy_state": "ready"})
        model._data.refresh_endstops()
        self.qt.events(3)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        # refresh_all fires one at activation too, so at least one —
        # and every endstop poll must use the documented GET.
        self.assertGreaterEqual(len(requests), 1)
        self.assertTrue(all(request.method == "GET" for request in requests))

    def test_moonraker_file_metadata_populates_layer_height_and_estimate(self):
        # Klipper never reports a per-layer thickness. Moonraker's file
        # metadata parses the slicer header SERVER-SIDE (a tiny JSON
        # query, not a gcode download), so the layer-height readout and
        # the slicer estimate populate even for prints the user never
        # loaded — the "no proactive downloads" ruling is
        # untouched.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if "server/files/metadata" in request.path]
        self.assertTrue(requests)
        requests[-1].callback({"result": {"layer_height": 0.2, "first_layer_height": 0.3, "estimated_time": 3600}},
                              None)
        # The coordinator re-publishes its snapshot on the callback; the
        # model re-reads it on its own publish (its poll timers are far
        # too slow for a test event-loop spin).
        model._data.changed.emit()
        self.qt.events()  # the publish coalescer flushes on the next turn
        # current_layer=2 (one-based) -> layer index 1 -> step 0.2 mm.
        self.assertEqual(model.monitorLayerHeight, "0.200 mm")
        # 3600 s slicer estimate - 30 s elapsed.
        self.assertEqual(model.monitorEta, "00:59:30")
        self.assertEqual(model.monitorEtaBasis, "blend")

    def test_endstop_failed_poll_keeps_last_known_states(self):
        # A transient poll failure must not erase last-known pin states
        # into a false "Not homed yet" while connected; the states
        # blank only on invalidation/disconnect.
        model = self.monitor()
        model._data._update(server={"klippy_state": "ready"})
        model._data._update(endstops={"x": "TRIGGERED", "y": "open"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertTrue(requests)
        requests[-1].callback(None, "network blip")
        self.qt.events(1)
        self.assertEqual(model._data.snapshot.endstops, {"x": "TRIGGERED", "y": "open"})

    def test_endstop_poll_waits_for_klippy_ready(self):
        # During a Klippy restart every endstop poll landed in the
        # gcode store as "!! Internal Error on WebRequest" (the
        # live report) — the poll must hold until server/info
        # reports ready, and resume on the next readiness.
        model = self.monitor()
        model._data._update(server={"klippy_state": "startup"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertEqual(len(requests), 0)
        model._data._update(server={"klippy_state": "ready"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertGreaterEqual(len(requests), 1)

    def test_controls_lock_and_camera_refresh_nonce(self):
        model = self.monitor()
        self.assertFalse(model.controlsLocked)
        model.setControlsLocked(True)
        self.assertTrue(model.controlsLocked)
        model.setControlsLocked(False)
        self.assertFalse(model.controlsLocked)
        before = model.cameraRefreshNonce
        model.refreshWebcams()
        self.assertEqual(model.cameraRefreshNonce, before + 1)

    def test_webcam_watchdog_veils_and_bumps_the_refresh_nonce(self):
        # A dead bridge relay bumps the refresh nonce (a URL change is
        # the only thing that restarts Cura's loader), throttled so a
        # dead stream cannot spin the loader, with the veil until the
        # stream restarts.
        model = self.monitor()
        model._camera_last_refresh_at = 0.0
        before = model.cameraRefreshNonce
        model._on_stream_failed()
        self.assertTrue(model.cameraRecovering)
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        # A second failure inside the throttle window does not bump.
        model._on_stream_failed()
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        model._on_stream_recovered()
        self.assertFalse(model.cameraRecovering)

    def test_camera_render_stall_rides_the_same_recovery(self):
        # The render watchdog (the live report): a stream
        # that connected but never painted a frame reports the stall
        # through the same nonce-bump recovery as a stream failure,
        # with the same 10 s throttle.
        model = self.monitor()
        model._camera_last_refresh_at = 0.0
        before = model.cameraRefreshNonce
        model.cameraRenderStalled()
        self.assertTrue(model.cameraRecovering)
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        model.cameraRenderStalled()
        self.assertEqual(model.cameraRefreshNonce, before + 1)

    def test_setup_scripts_queue_behind_the_in_flight_command(self):
        model = self.monitor()
        self.deliver_state("standby")
        commands = model._commands
        model.homeAll()  # the real setup path
        # While the first script is in flight, further one-shot scripts
        # queue instead of being dropped.
        self.assertTrue(commands.script("QGL", "QUAD_GANTRY_LEVEL"))
        self.assertTrue(commands.script("Mesh", "BED_MESH_CALIBRATE"))
        self.assertTrue(model.canRunSetup)  # the gate stays open for queueing
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        # Stateful commands still refuse while busy: they never queue.
        self.assertFalse(commands.send("Pause", "printer/print/pause"))
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/print/pause"], [])
        scripts[0].callback({}, None)
        self.qt.events(10)
        self.assertEqual(len(self.scripts()), 2)  # the next queued script drains automatically
        self.scripts()[1].callback({}, None)
        self.qt.events(10)
        bodies = [r.options["body"] for r in self.scripts()]
        self.assertEqual(bodies, [{"script": "G28"}, {"script": "QUAD_GANTRY_LEVEL"}, {"script": "BED_MESH_CALIBRATE"}])
        self.assertEqual(model._commands._queue, [])

    def test_rapid_jogs_while_paused_queue_separately(self):
        model = self.monitor()
        self.deliver_state("paused")
        model.setJogDistance(1)
        model.jog("x", 1)  # sent immediately
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        model.jog("x", 1)  # queued behind the in-flight send
        model.jog("x", 1)  # queues as its own move (no coalescing)
        self.assertEqual(self.scripts(), scripts)  # nothing new in flight
        scripts[0].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[1].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # Each queued move drains on its own completion cycle.
        scripts[1].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        self.assertEqual(scripts[2].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})

    def test_monitor_device_is_registered_with_output_manager(self):
        # The Monitor stage shows Cura's "connect the printer" placeholder when
        # no output device is registered; refresh() must register the incoming
        # device with the output-device manager on every transition, including
        # the very first one at startup.
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        manager = output.getOutputDeviceManager()
        self.assertIsNotNone(output._current)
        manager.addOutputDevice.assert_called_once_with(output._current)

    def test_regrabbing_slider_keeps_last_released_value_until_next_release(self):
        model = self.monitor()
        self.deliver()
        model._tuning.DEBOUNCE_MS = 1000

        # First release is published immediately, while its G-code remains
        # debounced. This is the position the next gesture must start from.
        model.setSpeedFactor(137)
        self.assertEqual(model.speedFactorPercent, 137)

        # Re-grab before the command has been sent. A status poll still reports
        # the printer's old 100% value, but must not push the bound Slider back.
        model.previewSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 137)
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 137)

        # Releasing the second gesture replaces the cancelled 137% command and
        # sends only the new value after the debounce interval.
        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 145)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S145"})

    def test_slider_preview_does_not_publish_during_drag_and_commit_still_sends(self):
        model = self.monitor()
        self.deliver()
        tuning_changes = []
        control_changes = []
        model._tuning.changed.connect(lambda: tuning_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        for value in range(110, 121):
            model.previewSpeedFactor(value)
        self.assertEqual(tuning_changes, [])
        self.assertEqual(model._tuning.value("speed-factor", 100), 100)
        self.assertEqual(model.speedFactorPercent, 100)

        # A normal Moonraker poll during the active gesture must not publish the
        # preview value back through the bound Qt property and reset the Slider.
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 100)
        self.assertEqual(control_changes, [])

        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(137)
        self.assertEqual(len(tuning_changes), 1)
        self.assertEqual(model.speedFactorPercent, 137)
        self.assertEqual(len(control_changes), 1)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S137"})

    def test_monitor_does_not_broadcast_unrelated_ui_signals_on_every_poll(self):
        model = self.monitor()
        self.deliver()
        webcam_changes = []
        control_changes = []
        model.webcamsChanged.connect(lambda: webcam_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        self.deliver()
        self.assertEqual(webcam_changes, [])
        self.assertEqual(control_changes, [])

        model._data._update(webcams=[{"uid": "front", "name": "Front", "stream_url": "/front"}])
        self.qt.events()  # the publish coalescer flushes on the next turn
        # The webcam family's grouped signal fires again when the
        # one-turn restore adopts the index; the important half is
        # that controls stay silent.
        self.assertGreaterEqual(len(webcam_changes), 1)
        self.assertEqual(control_changes, [])

    def test_selected_camera_persists_through_the_settings_document_and_restores_after_webcams(self):
        model = self.monitor()
        cameras = [
            {"uid": "front-uid", "name": "Front", "stream_url": "/front"},
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},
        ]

        # The first camera publication deliberately populates the ComboBox model
        # without trying to restore a currentIndex into an empty model.
        model._data._update(webcams=cameras)
        self.assertEqual(model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(model.activeWebcamIndex, 0)

        model.selectWebcam(1)
        self.qt.events()  # the publish coalescer flushes on the next turn

        self.assertEqual(self.follower.current_printer_config().camera_selected, "rear-uid")
        self.assertEqual(model.activeWebcamIndex, 1)
        self.assertEqual(model.cameraName, "Rear")
        # The 4.5.0 world: the camera selection persists through the
        # facade's settings document (SaveFile's fsync makes the save
        # durable) — the preference-flush pin retired with the
        # transcript.
        document = self.follower.persistence.settings_document()
        machine_id = self.follower.current_printer_identity()[0]
        self.assertEqual(document["machines"][machine_id]["camera_selected"], "rear-uid")

        # Recreate the complete follower against the same Cura preference store,
        # rather than merely constructing another camera helper around the same
        # live configuration object. This exercises the persisted config path.
        app2 = self.qt.Application(preferences=self.app.preferences)
        transport2 = ScriptedTransport()
        runtime_module = self.qt.load("FollowerRuntime")
        real_client = runtime_module.MoonrakerClient
        with patch.object(runtime_module, "MoonrakerClient", lambda parent: real_client(parent, transport=transport2, socket=ScriptedSocket())):
            follower2 = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app2)
        self.addCleanup(follower2.deinitialize)
        self.assertEqual(follower2.current_printer_config().camera_selected, "rear-uid")

        output2 = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app2, follower2)
        output2.start()
        self.addCleanup(output2.stop)
        restored_model = output2._current.activePrinter

        # On Monitor startup/load, publish the dropdown contents first. Only on
        # the next Qt event turn should the saved UID be resolved and selected.
        restored_model._data._update(webcams=cameras)
        self.assertEqual(restored_model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(restored_model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(restored_model.activeWebcamIndex, 1)
        self.assertEqual(restored_model.cameraName, "Rear")

        # Older configurations/frontends may have persisted the unique camera
        # name rather than Moonraker's UID. That must continue to restore the
        # same webcam instead of silently falling back to the first entry.
        follower2.apply_printer_config(replace(follower2.current_printer_config(), camera_selected="Rear"))
        restored_model._camera._key = None
        restored_model._camera.observe()
        self.assertEqual(restored_model.activeWebcamIndex, 1)
        self.assertEqual(restored_model.cameraName, "Rear")


if __name__ == "__main__":
    unittest.main()
