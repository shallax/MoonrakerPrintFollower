from pathlib import Path

qml_path = Path('plugins/MoonrakerMonitorDashboard.qml')
test_path = Path('tests/test_v3_followup_regressions.py')

qml = qml_path.read_text()

bad = '                                        from: 0; to: 100; stepSize: 1\n                                        from: 0; to: 100; live: false\n'
good = '                                        from: 0; to: 100; stepSize: 1\n                                        live: false\n'
count = qml.count(bad)
if count != 2:
    raise SystemExit(f'Expected exactly 2 duplicated slider property blocks, found {count}')
qml = qml.replace(bad, good)
qml_path.write_text(qml)

tests = test_path.read_text()
anchor = '''    def test_monitor_sliders_only_commit_on_release(self):\n        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use\n        # Qt Quick Controls' deferred-value mode. Their existing\n        # onPressedChanged handlers therefore send exactly once on release.\n        self.assertGreaterEqual(DASHBOARD_QML.count("live: false"), 9)\n        for slider_id in (\n            "speedSlider", "flowSlider", "fanSlider", "ledSlider",\n            "redSlider", "greenSlider", "blueSlider", "whiteSlider",\n            "pwmSlider",\n        ):\n            marker = "id: " + slider_id\n            start = DASHBOARD_QML.find(marker)\n            self.assertGreaterEqual(start, 0, slider_id)\n            self.assertIn("live: false", DASHBOARD_QML[start:start + 500], slider_id)\n        self.assertNotIn("onMoved:", DASHBOARD_QML)\n'''
addition = anchor + '''\n    def test_monitor_sliders_do_not_repeat_qml_properties(self):\n        duplicate = "from: 0; to: 100; stepSize: 1\\n                                        from: 0; to: 100; live: false"\n        self.assertNotIn(duplicate, DASHBOARD_QML)\n'''
if 'def test_monitor_sliders_do_not_repeat_qml_properties' not in tests:
    if anchor not in tests:
        raise SystemExit('Could not locate deferred-slider regression test anchor')
    tests = tests.replace(anchor, addition, 1)
    test_path.write_text(tests)
