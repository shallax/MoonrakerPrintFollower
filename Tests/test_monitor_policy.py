"""Executable monitor policy contracts."""
from tests import monitor_test_support as harness

class MonitorPolicyConsistencyTests(harness.MonitorPolicyConsistencyTests):
    def test_qml_prose_matches_policy_constants(self):
        import re as _re
        tuning = (harness.PLUGINS / "MonitorTuning.py").read_text(encoding="utf-8")
        debounce = int(_re.search(r"DEBOUNCE_MS\s*=\s*(\d+)", tuning).group(1))
        self.assertEqual(debounce, 250)
        window = f"unchanged for {debounce} ms" if debounce < 1000 else f"unchanged for {debounce // 1000} seconds"
        self.assertIn(window, harness.TUNING_SECTION_QML)

        commands = (harness.PLUGINS / "MonitorCommands.py").read_text(encoding="utf-8")
        click_window = int(_re.search(r"_reset_timer\.setInterval\((\d+)\)", commands).group(1))
        self.assertEqual(click_window, 1000)
        # The helper prose that restated the arm-reset window was
        # removed by request; the constant lives in the
        # code alone now.

        follow = (harness.PLUGINS / "FollowController.py").read_text(encoding="utf-8")
        radius = int(_re.search(r"window_radius: int = (\d+)", follow).group(1))
        self.assertIn(f"(±{radius})", (harness.PLUGINS / "FollowingSettings.qml").read_text(encoding="utf-8"))

    def test_classification_table_is_the_single_object_policy(self):
        from mpf.Monitor.MonitorFormatting import object_kind, wanted_object
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
        from mpf.Monitor.MonitorFormatting import fan_writable
        for name in ("fan", "fan_generic nevermore"):
            self.assertTrue(fan_writable(name), name)
        for name in ("controller_fan controller_fan1", "controller_fan controller_fan2",
                     "temperature_fan chamber", "heater_fan hotend_fan"):
            self.assertFalse(fan_writable(name), name)
        # The dashboard renders the read-only row instead of a slider,
        # and the command lane refuses the regulated fans fail-closed.
        controls = (harness.PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn('"writable": fan_writable(name)', controls)
        self.assertIn("if not fan_writable(name):", controls)
        self.assertIn("visible: modelData.writable", harness.FANS_SECTION_QML)
        self.assertIn("Firmware-controlled — speed is read-only", harness.FANS_SECTION_QML)

    def test_led_channels_are_absolute_and_the_labels_hold_their_width(self):
        # A live report: the chroma normalisation made
        # every nudge re-scale all four channel sliders (a +1 nudge
        # of a zeroed channel jumped it to 100 and dragged the rest).
        # The sliders now read absolute channel values, and the
        # percentage labels hold a fixed width so the rows never
        # reflow.
        controls = (harness.PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn("ABSOLUTE channels", controls)
        # The chroma normalisation's code is gone: no peak division
        # remains in the channel derivation.
        self.assertNotIn("* 100 / peak", controls)
        self.assertIn("round(c * 100) for c in channels", controls)
        # The channel commit sends the absolutes WITHOUT the
        # brightness slider as a gain — passing it zeroed every
        # channel nudge while the LED was off (a live
        # report).
        self.assertIn("whiteSlider.selectedValue() : 0, -1);", harness.LEDS_SECTION_QML)
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
        self.assertEqual(harness.DASHBOARD_QML.count("width: 52 * screenScaleFactor"), 0)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("width: 52 * screenScaleFactor"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("width: 52 * screenScaleFactor"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("width: 52 * screenScaleFactor"), 4)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("width: 52 * screenScaleFactor"), 1)
        self.assertIn("width: 150 * screenScaleFactor", harness.LEDS_SECTION_QML)
        # The submit's rebuild must not kill the tuned slider's focus
        # (a live report): the dashboard remembers the
        # slider's object and re-grants focus on the new delegate.
        outline = (harness.PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertIn("property string controlObject", outline)
        # One LED row holds five sliders: the refocus must land on the
        # RIGHT one — the kind discriminates, and the walk recurses
        # into the channel grid (a live report: the nudge
        # went to the brightness slider instead of the channel).
        self.assertIn("property string controlKind", outline)
        self.assertIn("function focusSliderIn", harness.DASHBOARD_QML)
        self.assertIn("function focusTuningSliderOnce()", harness.DASHBOARD_QML)
        # The refocus retries until it lands and holds across the
        # confirm-time rebuild (a live report: fan/LED
        # sliders lost focus on the apply, the singletons never).
        self.assertIn("refocusTimer.attempts = 0", harness.DASHBOARD_QML)
        self.assertIn("focusHoldTimer.start()", harness.DASHBOARD_QML)
        for token in ("refocusTimer.start()",):
            self.assertIn(token, harness.DASHBOARD_QML)
        self.assertNotIn("root.tuningSliderObject = modelData.object", harness.DASHBOARD_QML)
        self.assertIn("id: fanRepeater", harness.FANS_SECTION_QML)
        self.assertIn("id: ledRepeater", harness.LEDS_SECTION_QML)
        self.assertIn("id: pwmRepeater", harness.PWM_SECTION_QML)
        self.assertIn('controlKind: "pwm"', harness.PWM_SECTION_QML)
        for kind in ("led-red", "led-brightness", "led-green", "led-blue", "led-white"):
            self.assertIn('controlKind: "%s"' % kind, harness.LEDS_SECTION_QML)

    def test_consumers_use_the_shared_classification_tables(self):
        data = (harness.PLUGINS / "MonitorData.py").read_text(encoding="utf-8")
        controls = (harness.PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
        self.assertIn("wanted_object", data)
        self.assertIn("FAN_OBJECT_PREFIXES", controls)
        self.assertIn("LED_OBJECT_PREFIXES", controls)
        self.assertIn("PWM_OBJECT_PREFIXES", controls)
        self.assertNotIn("neopixel ", data)
        self.assertNotIn("fan_generic ", data)


