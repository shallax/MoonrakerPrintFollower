"""Heater discovery, real limits and command validation."""
from tests.test_monitor_controls import ControlsCase, record


class HeaterTargetTests(ControlsCase):
    def load(self, low=5, high=285, name="extruder"):
        self.data.rebuild(auxiliary={
            "configfile": {"settings": {name: {"min_temp": low, "max_temp": high}}},
            name: {"temperature": 23, "target": 210},
            "temperature_sensor ambient": {"temperature": 23, "target": 10}})
        return self.controls._values["heaterControlItems"]

    def test_inventory_uses_settings_and_excludes_sensors(self):
        rows = self.load()
        self.assertEqual([(r["object"], r["minimum"], r["maximum"]) for r in rows],
                         [("extruder", 5, 285)])
        for name in ("extruder1", "heater_bed", "heater_generic chamber"):
            self.assertEqual(self.load(name=name)[0]["object"], name)
            self.controls.set_heater_temperature(name, 80)
            self.assertEqual(self.tuning.queued[-1][-1],
                             f"SET_HEATER_TEMPERATURE HEATER={name.split()[-1]} TARGET=80")

    def test_config_fallback_and_fractional_limits(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {
            "heater_bed": {"min_temp": "5.2", "max_temp": "120.9"}}},
            "heater_bed": {"target": 60}})
        row = self.controls._values["heaterControlItems"][0]
        self.assertEqual((row["minimum"], row["maximum"]), (6, 120))

    def test_missing_invalid_and_untrusted_limits_never_enable_controls(self):
        for low, high in ((None, 300), (0, None), (0, "nan"), (0, float("inf")),
                          (True, 300), (100, 50), (1.1, 1.2)):
            with self.subTest(low=low, high=high):
                self.assertEqual(self.load(low, high), [])
        self.assertEqual(self.load(name="heater_generic x\nM112"), [])

    def test_only_off_or_in_range_targets_are_dispatched(self):
        self.load()
        for value in (0, 5, 285):
            self.controls.set_heater_temperature("extruder", value)
        self.assertEqual(len(self.tuning.queued), 3)
        for value in (-1, 1, 286, None, True, float("nan"), float("inf")):
            self.controls.set_heater_temperature("extruder", value)
        self.controls.set_heater_temperature("temperature_sensor ambient", 40)
        self.assertEqual(len(self.tuning.queued), 3)
        # Revalidate against current configuration, not a stale published row.
        self.load(high=220)
        self.controls.set_heater_temperature("extruder", 250)
        self.assertEqual(len(self.tuning.queued), 3)

    def test_printing_and_paused_allow_tuning_but_offline_and_locked_do_not(self):
        self.load()
        for state in ("printing", "paused", "standby"):
            self.data.observation = record(state=state)
            self.controls.set_heater_temperature("extruder", 200)
        self.assertEqual(len(self.tuning.queued), 3)
        for fields in (dict(connection="no"), dict(controls_locked=True),
                       dict(state=""), dict(active=False)):
            self.data.observation = record(**fields)
            self.controls.set_heater_temperature("extruder", 200)
        self.assertEqual(len(self.tuning.queued), 3)

    def test_malformed_configuration_and_status_are_not_writable(self):
        from mpf.monitor.temperature.HeaterTargets import heater_targets
        for configfile in ("invalid", {"config": []}, {"settings": ["invalid"]}):
            self.assertEqual(heater_targets({"configfile": configfile,
                                            "extruder": {"target": 200}}), [])
        for section in ("invalid", {"min_temp": "bad", "max_temp": 300}):
            self.assertEqual(heater_targets({"configfile": {"settings": {"extruder": section}},
                                            "extruder": {"target": 200}}), [])
        for status in ("invalid", {}, {"target": "nan"}):
            self.assertEqual(heater_targets({"configfile": {"settings": {
                "extruder": {"min_temp": 5, "max_temp": 285}}}, "extruder": status}), [])
