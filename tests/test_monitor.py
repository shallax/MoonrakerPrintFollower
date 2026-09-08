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
        for token in ("G91", "G28", "M18", "jog_gate", "push_op", "JogOp"):
            self.assertIn(token, policy)
        # Motion scripts have exactly one owner: MonitorControls gains none.
        self.assertNotIn("G91", CONTROLS)
        self.assertNotIn("M18", CONTROLS)
        for token in ("jogEnabled", "jogDistance", "homedAxes", "positionMode", "jogStatus",
                      "toolheadChanged", "def jog(", "def setJogDistance(", "def home(",
                      "def motorsOff(", "def extrude("):
            self.assertIn(token, MONITOR_MODEL)
        for token in ("id: toolheadSection", 'text: "Toolhead"', 'jog("x", -1)', 'jog("z", 1)',
                      "setJogDistance(", 'home("")', '"Motors off"', '"Extrude 5 mm"',
                      '"Retract 5 mm"', "Jogging pauses the print first", "root.printer.monitorPosition"):
            self.assertIn(token, DASHBOARD_QML)
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

    def test_emergency_stop_remains_three_clicks_with_progress(self):
        source = (PLUGINS / "MonitorCommands.py").read_text()
        self.assertIn("self._clicks == 3", source)
        self.assertIn("self._reset_timer.setInterval(1000)", source)
        self.assertIn('"printer/emergency_stop"', source)
        self.assertIn("emergencyButton.clicks / 3.0", DASHBOARD_QML)
        self.assertIn("EMERGENCY STOP", DASHBOARD_QML)
        self.assertNotIn("Emergency stop?", DASHBOARD_QML)

    def test_emergency_stop_is_pinned_outside_scrollable_controls(self):
        self.assertIn("anchors.bottom: emergencyDock.top", DASHBOARD_QML)
        self.assertIn("id: emergencyDock", DASHBOARD_QML)
        self.assertEqual(DASHBOARD_QML.count("id: emergencyButton"), 1)

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
        self.assertEqual(DASHBOARD_QML.count("width: zOffsetGrid.buttonWidth"), 2)
        self.assertGreaterEqual(DASHBOARD_QML.count("fixedWidthMode: true"), 2)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", DASHBOARD_QML)
        self.assertIn('modelData.active ? "Active — "', DASHBOARD_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", DASHBOARD_QML)
        self.assertNotIn("temperaturePresetSelector", DASHBOARD_QML)

    def test_pwm_controls_remain_in_dashboard(self):
        self.assertIn("pwmOutputItems", DASHBOARD_QML)
        self.assertIn("setPwmOutput", DASHBOARD_QML)
        self.assertIn('text: "PWM outputs"', DASHBOARD_QML)

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
        self.assertGreaterEqual(DASHBOARD_QML.count("if (!pressed) applyLedColour()"), 4)
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
        self.assertIn('text: "Refresh camera"', MONITOR_QML)
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
        self.assertIn("Layout.preferredWidth: 260 * screenScaleFactor", MONITOR_QML)

    def test_camera_qml_uses_the_activated_signal_index_not_bound_current_index(self):
        self.assertIn("onActivated: function(index)", MONITOR_QML)
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
        self.assertIn(f"within {click_window // 1000} second", DASHBOARD_QML)

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
        self.assertEqual(next_script(model.extrude, 5), {"script": "G91\nG1 E5 F300\nG90"})
        self.assertEqual(next_script(model.extrude, -5), {"script": "G91\nG1 E-5 F300\nG90"})

    def test_pause_first_jog_waits_for_paused_confirmation_then_drains(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.assertTrue(model.jogEnabled)  # pause-first keeps the controls live
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

    def test_rapid_jogs_while_paused_coalesce(self):
        model = self.monitor()
        self.deliver_state("paused")
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
