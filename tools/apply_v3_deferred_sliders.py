from pathlib import Path

QML = Path("plugins/MoonrakerMonitorDashboard.qml")
TESTS = Path("tests/test_v3_followup_regressions.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"Expected block not found for {label}")
    return text.replace(old, new, 1)


qml = QML.read_text()

# Qt Quick Controls Slider.live=false means the thumb/position can move while
# dragging but the actual value property is committed only on release. Since all
# Moonraker writes are triggered from onPressedChanged when pressed becomes
# false, this guarantees one printer update per completed drag rather than live
# updates while the user is still moving the control.
for slider_id in ("speedSlider", "flowSlider", "fanSlider", "ledSlider", "pwmSlider"):
    marker = f"id: {slider_id}"
    start = qml.find(marker)
    if start < 0:
        raise SystemExit(f"Missing slider {slider_id}")
    end = qml.find("value:", start)
    if end < 0:
        raise SystemExit(f"Missing value for {slider_id}")
    step = qml.rfind("stepSize: 1", start, end)
    if step < 0:
        raise SystemExit(f"Missing stepSize for {slider_id}")
    line_end = qml.find("\n", step)
    indent = qml[qml.rfind("\n", 0, step) + 1:step]
    insertion = f"{indent}live: false\n"
    if "live: false" not in qml[line_end + 1:end]:
        qml = qml[:line_end + 1] + insertion + qml[line_end + 1:]

inline_sliders = {
    "redSlider": 'Slider { id: redSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.redPercent; onPressedChanged: if (!pressed) applyLedColour() }',
    "greenSlider": 'Slider { id: greenSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.greenPercent; onPressedChanged: if (!pressed) applyLedColour() }',
    "blueSlider": 'Slider { id: blueSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.bluePercent; onPressedChanged: if (!pressed) applyLedColour() }',
    "whiteSlider": 'Slider { id: whiteSlider; visible: modelData.hasWhite; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.whitePercent; onPressedChanged: if (!pressed) applyLedColour() }',
}
for slider_id, old in inline_sliders.items():
    new = old.replace("stepSize: 1; value:", "stepSize: 1; live: false; value:")
    qml = replace_once(qml, old, new, slider_id)

QML.write_text(qml)

tests = TESTS.read_text()
anchor = '''    def test_emergency_stop_text_stays_black_during_click_sequence(self):\n        self.assertIn('color: "black"', DASHBOARD_QML)\n        self.assertNotIn('emergencyButton.clicks >= 2 ? "white"', DASHBOARD_QML)\n'''
addition = anchor + '''\n    def test_monitor_sliders_only_commit_on_release(self):\n        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use\n        # Qt Quick Controls' deferred-value mode. Their existing\n        # onPressedChanged handlers therefore send exactly once on release.\n        self.assertGreaterEqual(DASHBOARD_QML.count("live: false"), 9)\n        for slider_id in (\n            "speedSlider", "flowSlider", "fanSlider", "ledSlider",\n            "redSlider", "greenSlider", "blueSlider", "whiteSlider",\n            "pwmSlider",\n        ):\n            marker = "id: " + slider_id\n            start = DASHBOARD_QML.find(marker)\n            self.assertGreaterEqual(start, 0, slider_id)\n            self.assertIn("live: false", DASHBOARD_QML[start:start + 500], slider_id)\n        self.assertNotIn("onMoved:", DASHBOARD_QML)\n'''
if "def test_monitor_sliders_only_commit_on_release" not in tests:
    tests = replace_once(tests, anchor, addition, "deferred slider regression test")
TESTS.write_text(tests)
