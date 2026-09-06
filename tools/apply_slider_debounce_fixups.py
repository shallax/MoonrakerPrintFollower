from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


# Keep the long-standing helper name so older contracts remain meaningful: the
# function now queues/debounces the colour rather than sending it immediately.
qml_path = ROOT / "plugins" / "MoonrakerMonitorDashboard.qml"
qml = qml_path.read_text(encoding="utf-8")
qml = qml.replace("queueLedColour", "applyLedColour")
qml_path.write_text(qml, encoding="utf-8")


# The isolated typed-model unit harness stubs its parent class. Teach that stub
# the new parent helpers so the test still exercises scaled SET_PIN generation.
hotfix_path = ROOT / "tests" / "test_hotfix_regressions.py"
hotfix = hotfix_path.read_text(encoding="utf-8")
hotfix = replace_once(
    hotfix,
    '''            {"_want_aux_object": staticmethod(lambda _name: False)},\n''',
    '''            {\n                "_want_aux_object": staticmethod(lambda _name: False),\n                "_slider_value_from_poll": lambda self, _key, actual, _tolerance=1.0: actual,\n                "_preview_slider_value": lambda self, _key, _desired: None,\n                "_queue_slider_gcode": lambda self, _key, _desired, channel, script: self._send_quick_gcode(channel, script),\n            },\n''',
    "typed model parent stub",
)
hotfix_path.write_text(hotfix, encoding="utf-8")


followup_path = ROOT / "tests" / "test_v3_followup_regressions.py"
followup = followup_path.read_text(encoding="utf-8")
followup = replace_once(
    followup,
    '''        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use\n        # Qt Quick Controls' deferred-value mode. Their existing\n        # onPressedChanged handlers therefore send exactly once on release.\n''',
    '''        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use\n        # Qt Quick Controls' deferred-value mode. onMoved only previews/holds\n        # the intended value; release queues the debounced Moonraker command.\n''',
    "slider regression comment",
)
followup = replace_once(
    followup,
    '''        self.assertNotIn("onMoved:", DASHBOARD_QML)\n        self.assertIn("function sliderSelection(slider)", DASHBOARD_QML)\n''',
    '''        self.assertGreaterEqual(DASHBOARD_QML.count("onMoved:"), 9)\n        self.assertIn("previewSpeedFactor", DASHBOARD_QML)\n        self.assertIn("previewFlowFactor", DASHBOARD_QML)\n        self.assertIn("previewFanSpeed", DASHBOARD_QML)\n        self.assertIn("previewLedBrightness", DASHBOARD_QML)\n        self.assertIn("previewLedColor", DASHBOARD_QML)\n        self.assertIn("previewPwmOutput", DASHBOARD_QML)\n        self.assertIn("function sliderSelection(slider)", DASHBOARD_QML)\n''',
    "onMoved regression expectation",
)
followup_path.write_text(followup, encoding="utf-8")

print("Applied slider debounce compatibility test fixups")
