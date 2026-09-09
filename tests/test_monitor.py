"""Monitor domain: model/QML contracts, formatting and real-Qt tuning/camera.

The Monitor owns one Qt model, deep snapshots, debounced tuning and camera
selection. Contract tests assert the source keeps those boundaries; the Qt
tests drive the real production components through the shared harness.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from plugins.MonitorFormatting import (
    core_values,
    estimate_remaining,
    infer_macro_parameters,
    parse_bed_mesh,
    parse_mcu_stats,
)
from plugins.PrintState import LayerResolver
from qt_runtime_support import QT_AVAILABLE, ROOT, ScriptedTransport, runtime

PLUGINS = ROOT / "plugins"
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
DATA = (PLUGINS / "MonitorData.py").read_text()
CONTROLS = (PLUGINS / "MonitorControls.py").read_text()
FORMATTING = (PLUGINS / "MonitorFormatting.py").read_text()
TYPED = "\n".join((PLUGINS / name).read_text() for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
BED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()
BED_MESH_MAP_QML = (PLUGINS / "BedMeshMap.qml").read_text()
POPOVER_QML = (PLUGINS / "MonitorPopOver.qml").read_text()
TEMP_CHART_QML = (PLUGINS / "TemperatureChart.qml").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()


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

    def test_toolhead_control_surface(self):
        policy = (PLUGINS / "ToolheadPolicy.py").read_text()
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
        for token in ("id: toolheadSection", 'title: "Toolhead"', 'jog("x", -1)', 'jog("z", 1)',
                      "setJogDistance(", 'home("x")', 'home("y")', 'home("z")', '"Motors off"',
                      '"Extrude"', '"Retract"', "setExtrudeDistance(", "setExtrudeSpeed(",
                      '"Cooldown"', "heatersOff",
                      'text: "↑ Y"', 'text: "← X"', 'text: "→ X"', 'text: "↓ Y"',
                      'text: "↑ Z"', 'text: "↓ Z"', 'text: "Centre toolhead"', 'text: "Z to 0"',
                      "Toolhead moves are disabled during a print", "root.printer.monitorPosition"):
            self.assertIn(token, DASHBOARD_QML)
        # The six directional buttons carry no +/- signs (the arrows are the
        # direction) and use the PreviewSecondaryButton idiom: Cura's
        # native button underneath (hover/tooltip), a centred theme-coloured
        # label on top — Cura's own label does not vertically centre.
        # Home-all lives in the Setup section only: the toolhead section
        # keeps per-axis home buttons, so no duplicate home-all controls.
        self.assertNotIn('home("")', DASHBOARD_QML[DASHBOARD_QML.index("id: toolheadSection"):DASHBOARD_QML.index("id: macroSection")])
        start = DASHBOARD_QML.index("id: toolheadSection")
        end = DASHBOARD_QML.index("id: macroSection", start)
        self.assertEqual(DASHBOARD_QML[start:end].count("PreviewSecondaryButton"), 6)
        self.assertNotIn("contentItem", DASHBOARD_QML[start:end])
        # The Z-offset nudges carry direction glyphs, up row first, and no
        # +/- signs: the arrows carry the direction.
        self.assertIn('"↓ " + Math.abs(modelData)', DASHBOARD_QML)
        self.assertIn('"↑ " + modelData', DASHBOARD_QML)
        self.assertLess(DASHBOARD_QML.index("model: [0.005"), DASHBOARD_QML.index("model: [-0.005"))
        # The toolhead block is gated by jogEnabled alone, never actionBusy:
        # taps must keep working while the queue drains.
        start = DASHBOARD_QML.index("id: toolheadSection")
        end = DASHBOARD_QML.index("id: macroSection", start)
        self.assertIn("jogEnabled", DASHBOARD_QML[start:end])
        self.assertNotIn("actionBusy", DASHBOARD_QML[start:end])

    def test_same_dashboard_chain_and_power_lock_explanation(self):
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorDashboard", BED_MESH_QML)
        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)
        self.assertIn("Power control is locked by Moonraker while this print is active.", DASHBOARD_QML)

    def test_output_plugin_selects_the_same_dashboard_through_one_model(self):
        self.assertIn("from .MoonrakerMonitorModel import MoonrakerMonitorModel", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorDashboard", BED_MESH_QML)

    def test_setup_and_save_commands_have_one_policy_owner(self):
        for command in ("G28", "QUAD_GANTRY_LEVEL", "BED_MESH_CALIBRATE", "SAVE_CONFIG", "SET_GCODE_OFFSET", "SET_FAN_SPEED", "SET_LED"):
            self.assertIn(command, CONTROLS)
        self.assertIn("self._commands.setup_allowed", CONTROLS)
        self.assertIn("configfile.get(\"save_config_pending\")", CONTROLS)

    def test_emergency_stop_requires_two_clicks_and_a_held_third_press(self):
        source = (PLUGINS / "MonitorCommands.py").read_text()
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
        self.assertIn('tooltipText: root.printer != null ? "Click for the full bed mesh map ("', MONITOR_QML)
        self.assertNotIn('id: mapButton', MONITOR_QML)
        # Cura-style collapsible sections, persisted per section, sharing
        # the CollapsibleSectionHeader type across all three panes.
        self.assertIn("sectionExpandedMap", DASHBOARD_QML)
        self.assertIn("setSectionExpanded", MONITOR_MODEL)
        self.assertIn('sectionId: "toolhead"', DASHBOARD_QML)
        # Direct instantiations must ASSIGN the type's properties: the old
        # Loader syntax ('property string sectionId: ...') declares a local
        # property instead, which silently un-wires every header.
        self.assertNotIn("property string sectionId:", DASHBOARD_QML)
        self.assertNotIn("property string title:", DASHBOARD_QML)
        self.assertNotIn("property string sectionIcon:", DASHBOARD_QML)
        self.assertIn('sectionIcon: "Nozzle"', DASHBOARD_QML)
        self.assertIn('sectionIcon: "Printer"', DASHBOARD_QML)
        self.assertIn('sectionId: "meshmap"', MONITOR_QML)
        self.assertIn('sectionId: "systeminfo"', MONITOR_QML)
        # Plugin-drawn glyphs feed the header through a url, and the
        # frontend launcher lives in the Printer status title row.
        self.assertIn('sectionIcon: "Fan"', MONITOR_QML)
        self.assertIn('sectionIconUrl: Qt.resolvedUrl("Power.svg")', DASHBOARD_QML)
        self.assertIn('text: "Open the Moonraker frontend."', MONITOR_QML)
        self.assertNotIn('text: "Open Moonraker frontend"', MONITOR_QML)
        self.assertEqual(DASHBOARD_QML.count("CollapsibleSectionHeader"), 12)
        self.assertEqual(MONITOR_QML.count("CollapsibleSectionHeader"), 9)
        self.assertEqual(DASHBOARD_QML.count('sectionIcon: "'), 11)
        self.assertEqual(MONITOR_QML.count('sectionIcon: "'), 9)
        # Filament state is colour-coded: green detected, orange runout.
        self.assertIn('"#43a047"', MONITOR_QML)
        self.assertIn('"#fb8c00"', MONITOR_QML)
        self.assertNotIn('id: powerOffDialog', MONITOR_QML)
        self.assertNotIn('id: cancelPrintDialog', MONITOR_QML)
        # The right column hosts the print actions, power and the lock.
        for token in ('text: "Pause"', 'text: "Resume"', 'text: "Cancel"', "cancelPrintDialog.open()",
                      'title: "Power"', "powerOffDialog.open()", "controlsCollapsed",
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
            self.assertIn(token, MONITOR_QML + MONITOR_MODEL)
        self.assertIn("infoCollapsed", MONITOR_MODEL)
        self.assertIn("statusCollapsed", MONITOR_MODEL)
        self.assertIn("cameraRefreshNonce", MONITOR_MODEL)
        self.assertIn("mpf_reload", MONITOR_QML)  # Refresh camera restarts the stream

    def test_temperature_chart_repaints_and_popovers_are_overlays(self):
        # A QML Canvas paints exactly once unless asked: the chart must
        # requestPaint on payload, geometry, visibility and hover
        # changes (it used to render one frame and freeze).
        self.assertIn("onChartChanged", TEMP_CHART_QML)
        self.assertIn("dataCanvas.requestPaint()", TEMP_CHART_QML)
        self.assertIn("overlay.requestPaint()", TEMP_CHART_QML)
        self.assertIn("onVisibleChanged", TEMP_CHART_QML)
        # One open pop-over at a time; the shells are overlay siblings
        # of the pane RowLayout, never layout children (anchored layout
        # children reflow every pane and log undefined-behavior
        # warnings).
        self.assertIn('property string openPopOver: ""', MONITOR_QML)
        self.assertNotIn("bedMeshPanelOpen", MONITOR_QML)
        self.assertNotIn("chartPanelOpen", MONITOR_QML)
        self.assertIn("id: outsideClickLayer", MONITOR_QML)
        self.assertIn("Keys.onEscapePressed", MONITOR_QML)
        # The legend binds to the legend property (notifies only on real
        # changes, so delegates are never rebuilt at the 1 Hz sample
        # cadence) and toggles on user intent only — re-bound checkboxes
        # used to rewrite the state file every second.
        self.assertIn("temperatureChartLegend.series", MONITOR_QML)
        self.assertIn("onToggled: root.printer.setTemperatureSensorVisible", MONITOR_QML)
        self.assertNotIn("onCheckedChanged: root.printer.setTemperatureSensorVisible", MONITOR_QML)
        # Target bands, not dashed lines (the author's ruling), and the
        # hover readout carries the clock.
        self.assertIn("Target bands", TEMP_CHART_QML)
        self.assertIn("hoverClock", TEMP_CHART_QML)
        self.assertIn('"wallOrigin"', TEMP_CHART_QML)

    def test_system_restart_surface(self):
        for token in ("firmwareRestart", "hostRestart", "FIRMWARE_RESTART", "machine/reboot"):
            self.assertIn(token, MONITOR_MODEL + CONTROLS)
        for token in ('text: "Firmware restart"', 'text: "Host restart"',
                      "System restarts are disabled during a print."):
            self.assertIn(token, DASHBOARD_QML)

    def test_emergency_stop_is_pinned_outside_scrollable_controls(self):
        # The dock lives at the bottom of the dashboard, spanning the whole
        # window width (including under the controls pane), outside the
        # scrollable panes, so it stays visible in every collapse state.
        self.assertIn("anchors.bottom: emergencyDock.top", DASHBOARD_QML)
        self.assertIn("id: emergencyDock", DASHBOARD_QML)
        self.assertNotIn("id: emergencyDock", MONITOR_QML)
        self.assertEqual(DASHBOARD_QML.count("id: emergencyButton\n"), 1)

    def test_emergency_stop_text_stays_black_during_click_sequence(self):
        self.assertIn('color: "black"', DASHBOARD_QML)
        self.assertNotIn('emergencyButton.clicks >= 2 ? "white"', DASHBOARD_QML)

    def test_dashboard_shows_current_z_offset_beside_nudges(self):
        self.assertIn('text: "Current Z offset"', DASHBOARD_QML)
        self.assertIn('"Current " + root.printer.zOffsetText', DASHBOARD_QML)
        self.assertIn("adjustZOffset", DASHBOARD_QML)

    def test_z_offset_buttons_are_opposites_with_equal_click_zones(self):
        self.assertIn("id: zOffsetGrid", DASHBOARD_QML)
        self.assertIn("model: [-0.005, -0.01, -0.025, -0.05]", DASHBOARD_QML)
        self.assertIn("model: [0.005, 0.01, 0.025, 0.05]", DASHBOARD_QML)
        # A two-column grid (up left, down right): both Repeater
        # delegates fill their cell equally, so click zones stay
        # matched and the labels cannot elide at narrow pane widths.
        grid = DASHBOARD_QML[DASHBOARD_QML.index("id: zOffsetGrid"):DASHBOARD_QML.index('text: "Clear Z offset"')]
        self.assertGreaterEqual(grid.count("Layout.fillWidth: true"), 2)
        self.assertIn("columns: 2", grid)
        self.assertGreaterEqual(grid.count("fixedWidthMode: true"), 2)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", DASHBOARD_QML)
        self.assertIn('modelData.active ? "Active — "', DASHBOARD_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", DASHBOARD_QML)
        self.assertNotIn("temperaturePresetSelector", DASHBOARD_QML)

    def test_pwm_controls_remain_in_dashboard(self):
        self.assertIn("pwmOutputItems", DASHBOARD_QML)
        self.assertIn("setPwmOutput", DASHBOARD_QML)
        self.assertIn('title: "PWM outputs"', DASHBOARD_QML)

    def test_monitor_layer_tracks_remote_print_not_cura_slider(self):
        resolver = (PLUGINS / "PrintState.py").read_text()
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
        self.assertIn("modelData.load", MONITOR_QML)
        self.assertIn("modelData.frequency", MONITOR_QML)
        self.assertIn("modelData.transport", MONITOR_QML)

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
        self.assertIn("function applyLedColour()", DASHBOARD_QML)
        self.assertGreaterEqual(DASHBOARD_QML.count("applyLedColour()"), 4)
        self.assertIn("root.tuningSliderPressed = pressed", DASHBOARD_QML)
        self.assertNotIn('text: "Set colour"', DASHBOARD_QML)
        self.assertIn("root.printer.setLedColor", DASHBOARD_QML)

    def test_live_tuning_slider_ranges_expand_from_accepted_value(self):
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)", DASHBOARD_QML)
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)", DASHBOARD_QML)
        self.assertIn('max(10 if kind == "speed" else 50, int(percent))', CONTROLS)
        self.assertNotIn("min(200, int(percent))", CONTROLS)
        self.assertNotIn("min(150, int(percent))", CONTROLS)

    def test_monitor_sliders_only_commit_on_release(self):
        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use
        # Qt Quick Controls' deferred-value mode. onMoved only previews/holds
        # the intended value; release queues the debounced Moonraker command.
        self.assertGreaterEqual(DASHBOARD_QML.count("live: false"), 9)
        for slider_id in (
            "speedSlider", "flowSlider", "fanSlider", "ledSlider",
            "redSlider", "greenSlider", "blueSlider", "whiteSlider",
            "pwmSlider",
        ):
            marker = "id: " + slider_id
            start = DASHBOARD_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", DASHBOARD_QML[start:start + 500], slider_id)
        self.assertGreaterEqual(DASHBOARD_QML.count("onMoved:"), 9)
        self.assertIn("previewSpeedFactor", DASHBOARD_QML)
        self.assertIn("previewFlowFactor", DASHBOARD_QML)
        self.assertIn("previewFanSpeed", DASHBOARD_QML)
        self.assertIn("previewLedBrightness", DASHBOARD_QML)
        self.assertIn("previewLedColor", DASHBOARD_QML)
        self.assertIn("previewPwmOutput", DASHBOARD_QML)
        self.assertIn("function sliderSelection(slider)", DASHBOARD_QML)
        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_QML)
        self.assertIn("root.sliderSelection(speedSlider) + \"%\"", DASHBOARD_QML)
        self.assertIn("setSpeedFactor(root.sliderSelection(speedSlider))", DASHBOARD_QML)
        self.assertIn("setFlowFactor(root.sliderSelection(flowSlider))", DASHBOARD_QML)

    def test_monitor_sliders_do_not_repeat_qml_properties(self):
        duplicate = "from: 0; to: 100; stepSize: 1\n                                        from: 0; to: 100; live: false"
        self.assertNotIn(duplicate, DASHBOARD_QML)

    def test_slider_qml_prevents_parent_flickable_from_stealing_drag(self):
        self.assertIn("property bool tuningSliderPressed: false", DASHBOARD_QML)
        self.assertIn("interactive: !root.tuningSliderPressed", DASHBOARD_QML)
        for slider_id in ("speedSlider", "flowSlider", "fanSlider", "ledSlider", "redSlider",
                          "greenSlider", "blueSlider", "whiteSlider", "pwmSlider"):
            self.assertIn("id: " + slider_id, DASHBOARD_QML)
        self.assertGreaterEqual(DASHBOARD_QML.count("root.tuningSliderPressed = pressed"), 9)

    def test_deferred_slider_and_monitor_ux_contracts(self):
        self.assertGreaterEqual(DASHBOARD_QML.count("live: false"), 9)
        self.assertGreaterEqual(DASHBOARD_QML.count("onMoved:"), 9)
        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_QML)
        self.assertIn("After release, the latest value is applied once it has been unchanged for 250 ms.", DASHBOARD_QML)
        self.assertIn('text: "Refresh Moonraker\'s webcam list."', MONITOR_QML)
        self.assertIn('title: "Exclude object?"', MONITOR_QML)
        tuning = (PLUGINS / "MonitorTuning.py").read_text()
        self.assertIn("DEBOUNCE_MS = 250", tuning)
        self.assertIn("current.revision != revision", tuning)

    def test_full_config_is_discovered_not_polled_every_second(self):
        self.assertIn('["save_config_pending", "save_config_pending_items"]', DATA)
        self.assertIn('"config-static"', DATA)
        self.assertIn('category="discovery"', DATA)

    def test_monitor_consumes_shared_session_poll_policy(self):
        self.assertIn("self._client.session.snapshot.printer_state", DATA)
        self.assertIn("poll_policy.interval_ms(category, 1000", DATA)
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
        self.assertIn("Layout.preferredWidth: 180 * screenScaleFactor", MONITOR_QML)

    def test_camera_qml_uses_the_activated_signal_index_not_bound_current_index(self):
        self.assertIn("onActivated: function (index)", MONITOR_QML)
        self.assertIn("selectWebcam(index)", MONITOR_QML)
        self.assertNotIn("selectWebcam(cameraSelector.currentIndex)", MONITOR_QML)


class MonitorFormattingTests(unittest.TestCase):
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

    def test_eta_prefers_slicer_time_for_early_and_resumed_prints(self):
        self.assertAlmostEqual(estimate_remaining(3600, 0.02, 7 * 3600, True), 6 * 3600, delta=1)
        self.assertAlmostEqual(estimate_remaining(3 * 3600, 0.10, 7 * 3600, True), 4 * 3600, delta=1)
        self.assertIsNone(estimate_remaining(120, 0.50, None, False))

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        self.assertEqual(parse_bed_mesh(None), {})
        for matrix, bounds in (([[0, 1], [2]], [0, 0]), ([[0, float("nan")], [1, 2]], [0, 0]), ([[0, 1], [1, 2]], [2, 0])):
            self.assertEqual(parse_bed_mesh({"mesh_matrix": matrix, "mesh_min": bounds, "mesh_max": [1, 1]}), {})
        self.assertEqual(parse_mcu_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123"), {"mcu_awake": 0.02, "bytes_read": 123.0})


class MonitorPolicyConsistencyTests(unittest.TestCase):
    """Behavior constants restated as QML prose must not drift."""

    def test_qml_prose_matches_policy_constants(self):
        import re as _re
        tuning = (PLUGINS / "MonitorTuning.py").read_text()
        debounce = int(_re.search(r"DEBOUNCE_MS\s*=\s*(\d+)", tuning).group(1))
        self.assertEqual(debounce, 250)
        window = f"unchanged for {debounce} ms" if debounce < 1000 else f"unchanged for {debounce // 1000} seconds"
        self.assertIn(window, DASHBOARD_QML)

        commands = (PLUGINS / "MonitorCommands.py").read_text()
        click_window = int(_re.search(r"_reset_timer\.setInterval\((\d+)\)", commands).group(1))
        self.assertEqual(click_window, 1000)
        self.assertIn(f"after {click_window // 1000} second", DASHBOARD_QML)

        follow = (PLUGINS / "FollowController.py").read_text()
        radius = int(_re.search(r"window_radius: int = (\d+)", follow).group(1))
        self.assertIn(f"(±{radius})", (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text())

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

    def test_consumers_use_the_shared_classification_tables(self):
        data = (PLUGINS / "MonitorData.py").read_text()
        controls = (PLUGINS / "MonitorControls.py").read_text()
        self.assertIn("wanted_object", data)
        self.assertIn("FAN_OBJECT_PREFIXES", controls)
        self.assertIn("LED_OBJECT_PREFIXES", controls)
        self.assertIn("PWM_OBJECT_PREFIXES", controls)
        self.assertNotIn("neopixel ", data)
        self.assertNotIn("fan_generic ", data)


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
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def deliver(self):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation)

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
        client._handle_http_status({"result": {"status": status}}, None, client._generation)

    def scripts(self):
        return [r for r in self.transport.requests if r.path == "printer/gcode/script"]

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
        self.assertEqual(next_script(model.jog, "z", -1), {"script": "G91\nG1 Z-10 F600\nG90"})
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
        model.jog("x", 1)  # merges into the queued move
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
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X2 F3000\nG90"})
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
        self.assertIn("outcome unknown", model.actionStatus)

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
        self.assertIn("!root.printer.printActive", DASHBOARD_QML)

    def test_system_restarts_are_queued_one_shot_commands(self):
        model = self.monitor()
        self.deliver_state("standby")
        model.firmwareRestart()
        self.assertEqual([r.options["body"] for r in self.scripts()], [{"script": "FIRMWARE_RESTART"}])
        self.scripts()[0].callback({}, None)
        self.qt.events(10)
        model.hostRestart()
        reboot = [r for r in self.transport.requests if r.path == "machine/reboot"]
        self.assertEqual(len(reboot), 1)
        # Restarts refuse while a print is active.
        self.deliver_state("printing")
        model.firmwareRestart()
        model.hostRestart()
        self.assertEqual(len([r for r in self.scripts() if "FIRMWARE_RESTART" in str(r.options["body"])]), 1)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "machine/reboot"]), 1)

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
        import UM.Resources as UMResourcesModule
        section_path = UMResourcesModule.Resources.getStoragePath(
            UMResourcesModule.Resources.Preferences,
            self.qt.load("MoonrakerMonitorModel").SECTIONS_FILE_NAME)
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {
                "sections": {"toolhead": False},
                "controlsCollapsed": True,
                "controlsLocked": True,
                "infoCollapsed": True,
                "statusCollapsed": True,
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

    def test_panel_state_migrates_the_legacy_flat_section_file(self):
        # The first shipped format stored the bare section map; it must
        # still hydrate into sections with default panel toggles.
        import UM.Resources as UMResourcesModule
        section_path = UMResourcesModule.Resources.getStoragePath(
            UMResourcesModule.Resources.Preferences,
            self.qt.load("MoonrakerMonitorModel").SECTIONS_FILE_NAME)
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"setup": False, "toolhead": True}, handle)
        model = self.monitor()
        self.assertEqual(model._sections, {"setup": False, "toolhead": True})
        self.assertFalse(model.controlsCollapsed)
        self.assertFalse(model.controlsLocked)

    def test_corrupt_panel_state_file_degrades_to_defaults(self):
        # A truncated or hand-edited file must never raise or hydrate
        # inverted: unreadable JSON yields defaults, and string flags like
        # 'false' must collapse (bool('false') is True).
        import UM.Resources as UMResourcesModule
        section_path = UMResourcesModule.Resources.getStoragePath(
            UMResourcesModule.Resources.Preferences,
            self.qt.load("MoonrakerMonitorModel").SECTIONS_FILE_NAME)
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

    def chart_of(self, model):
        chart = model.temperatureChart
        return chart if isinstance(chart, dict) else chart.value()

    def test_temperature_chart_config_persists_across_model_instances(self):
        model = self.monitor()
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
                     "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.2}}
        model._data._update(auxiliary=auxiliary)
        model._data.auxiliaryChanged.emit()  # the real feed path: _aux updates then emits
        # Defaults: everything visible, palette colours, toggles on.
        default = self.chart_of(model)
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
        second._data._update(auxiliary=auxiliary)
        second._data.auxiliaryChanged.emit()
        chart = self.chart_of(second)
        self.assertFalse(chart["showTargets"])
        self.assertFalse(chart["showPower"])
        extruder = next(item for item in chart["series"] if item["name"] == "extruder")
        bed = next(item for item in chart["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")

    def test_history_feeds_once_per_auxiliary_arrival_not_per_publish(self):
        model = self.monitor()
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}}
        model._data._update(auxiliary=auxiliary)
        model._data.auxiliaryChanged.emit()
        self.assertEqual(len(self.chart_of(model)["series"][0]["points"]), 1)
        # Core-only publishes (no aux reply) must not append samples:
        # the old per-publish feed duplicated samples and halved the
        # effective window.
        for _ in range(5):
            model._data._update(core={"print_stats": {"state": "printing"}})
        self.assertEqual(len(self.chart_of(model)["series"][0]["points"]), 1)
        # A second aux reply appends exactly one more sample.
        model._data._update(auxiliary=auxiliary)
        model._data.auxiliaryChanged.emit()
        self.assertEqual(len(self.chart_of(model)["series"][0]["points"]), 2)

    def test_history_resets_when_the_session_is_invalidated(self):
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        model._data.auxiliaryChanged.emit()
        self.assertEqual(len(self.chart_of(model)["series"]), 1)
        model._data.set_active(False)  # emits invalidated
        chart = self.chart_of(model)
        self.assertEqual(chart["series"], [])

    def test_chart_setters_are_idempotent_and_validate(self):
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        model._data.auxiliaryChanged.emit()
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
        chart = self.chart_of(model)
        extruder = next(item for item in chart["series"] if item["name"] == "extruder")
        self.assertEqual(extruder["color"], "#d32f2f")  # the palette default, unchanged
        model.setTemperatureSensorColor("extruder", "#123456")
        self.assertEqual(writes, [1, 1])

    def test_chart_config_prunes_vanished_sensors(self):
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0}})
        model._data.auxiliaryChanged.emit()
        model.setTemperatureSensorColor("ghost_sensor", "#123456")
        # Any later change prunes keys for sensors no longer present —
        # but never while the live set is empty.
        model.setTemperatureSensorVisible("extruder", False)
        chart = self.follower.current_printer_config().temperature_chart
        self.assertNotIn("ghost_sensor", chart["colors"])
        self.assertEqual(chart["colors"], {})

    def test_temperature_chart_defaults_when_the_block_is_missing(self):
        import UM.Resources as UMResourcesModule
        section_path = UMResourcesModule.Resources.getStoragePath(
            UMResourcesModule.Resources.Preferences,
            self.qt.load("MoonrakerMonitorModel").SECTIONS_FILE_NAME)
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": False}}, handle)
        model = self.monitor()
        chart = self.chart_of(model)
        self.assertTrue(chart["showTargets"])
        self.assertTrue(chart["showPower"])
        self.assertTrue(all(item["visible"] for item in chart["series"]))

    def test_legacy_global_chart_block_migrates_into_the_per_printer_record(self):
        import UM.Resources as UMResourcesModule
        section_path = UMResourcesModule.Resources.getStoragePath(
            UMResourcesModule.Resources.Preferences,
            self.qt.load("MoonrakerMonitorModel").SECTIONS_FILE_NAME)
        with open(section_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"setup": False},
                       "temperatureChart": {"visible": {"extruder": False},
                                            "colors": {"heater_bed": "#123456"},
                                            "showTargets": False, "showPower": False}}, handle)
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        model._data.auxiliaryChanged.emit()
        # The legacy block was adopted once into the per-printer record…
        self.assertEqual(self.follower.current_printer_config().temperature_chart, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        extruder = next(item for item in self.chart_of(model)["series"] if item["name"] == "extruder")
        bed = next(item for item in self.chart_of(model)["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")
        # …and the global file keeps chrome only afterwards.
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertNotIn("temperatureChart", payload)

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

    def test_rapid_jogs_while_paused_coalesce(self):
        model = self.monitor()
        self.deliver_state("paused")
        model.setJogDistance(1)
        model.jog("x", 1)  # sent immediately
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        model.jog("x", 1)  # queued behind the in-flight send
        model.jog("x", 1)  # merges into the queued move
        self.assertEqual(self.scripts(), scripts)  # nothing new in flight
        scripts[0].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[1].options["body"], {"script": "G91\nG1 X2 F3000\nG90"})

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
        self.assertEqual(len(webcam_changes), 1)
        self.assertEqual(control_changes, [])

    def test_selected_camera_is_flushed_immediately_and_restored_after_webcams_are_populated(self):
        model = self.monitor()
        cameras = [
            {"uid": "front-uid", "name": "Front", "stream_url": "/front"},
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},
        ]
        self.app.savePreferences = Mock()

        # The first camera publication deliberately populates the ComboBox model
        # without trying to restore a currentIndex into an empty model.
        model._data._update(webcams=cameras)
        self.assertEqual(model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(model.activeWebcamIndex, 0)

        model.selectWebcam(1)

        self.assertEqual(self.follower.current_printer_config().camera_selected, "rear-uid")
        self.assertEqual(model.activeWebcamIndex, 1)
        self.assertEqual(model.cameraName, "Rear")
        self.app.savePreferences.assert_called_once_with()

        config_module = self.qt.load("PrinterConfig")
        stored = json.loads(self.app.preferences.values[config_module.PrinterConfigStore.PREF_KEY])
        machine_id = self.follower.current_printer_identity()[0]
        self.assertEqual(stored[machine_id]["camera_selected"], "rear-uid")

        # Recreate the complete follower against the same Cura preference store,
        # rather than merely constructing another camera helper around the same
        # live configuration object. This exercises the persisted config path.
        app2 = self.qt.Application(preferences=self.app.preferences)
        transport2 = ScriptedTransport()
        runtime_module = self.qt.load("FollowerRuntime")
        real_client = runtime_module.MoonrakerClient
        with patch.object(runtime_module, "MoonrakerClient", lambda parent: real_client(parent, transport=transport2)):
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
