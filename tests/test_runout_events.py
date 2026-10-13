"""Sensor evidence is selected, print-bound, and never claims exact runout."""
import unittest

from mpf.printing.RunoutEvents import RunoutEvents, pause_owner


class RunoutEventsTests(unittest.TestCase):
    def test_selected_sensor_records_only_true_to_false_transition(self):
        events = RunoutEvents()
        one = "filament_switch_sensor toolhead"
        two = "filament_motion_sensor spool"
        selected = [one]
        sensors = {one: {"filament_detected": False, "enabled": True},
                   two: {"filament_detected": True, "enabled": True}}
        self.assertEqual(events.observe(("x", 10, 1), sensors, at=1, selected=selected), ())
        sensors[one]["filament_detected"] = True
        events.observe(("x", 10, 1), sensors, at=2, selected=selected)
        sensors[one]["filament_detected"] = False
        found = events.observe(("x", 10, 1), sensors, at=3, selected=selected,
                               layer=2, split=5, file_position=100)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].sensor, found[0].precision, found[0].layer, found[0].split),
                         (one, "motion", 2, 5))
        self.assertEqual(events.observe(("x", 10, 1), sensors, at=4, selected=selected), ())

    def test_new_print_and_manual_estimate(self):
        events = RunoutEvents()
        mark = events.mark_manual(("x", 10, 1), at=5, layer=0, split=0)
        self.assertEqual(mark.precision, "estimate")
        self.assertEqual(len(events.events), 1)
        events.bind(("x", 10, 2))
        self.assertEqual(events.events, ())

    def test_pause_owner_comes_from_printer_config(self):
        name = "filament_switch_sensor toolhead"
        self.assertEqual(pause_owner({name: {"pause_on_runout": "True"}}, name), "klipper")
        self.assertEqual(pause_owner({name: {"pause_on_runout": False}}, name), "follower")
        self.assertEqual(pause_owner({name: {}}, name), "klipper")
        self.assertIsNone(pause_owner({}, name))

    def test_restore_rebinds_saved_event_to_current_session_job(self):
        old = RunoutEvents()
        saved = old.mark_manual(("x", 10, 1), at=5, layer=2, split=7).document()
        current = RunoutEvents()
        current.restore(("x", 10, 9), [saved, saved, {"kind": "manual", "observed_at": "nan"}])
        self.assertEqual(len(current.events), 1)
        self.assertEqual(current.events[0].job_key, ("x", 10, 9))

    def test_missing_sensors_and_bad_samples_never_create_false_runout(self):
        events = RunoutEvents()
        name = "filament_switch_sensor inlet"
        job = ("x", 1)
        self.assertEqual(events.observe(job, {}, at=0, selected=[name]), ())
        self.assertEqual(events.observe(job, {name: {"filament_detected": True}},
                                        at=1, selected=[name]), ())
        self.assertEqual(events.observe(job, {name: {"filament_detected": False,
                                                   "enabled": False}},
                                        at=2, selected=[name]), ())
        self.assertEqual(events.observe(job, {name: {"filament_detected": False}},
                                        at=3, selected=[name]), ())
        self.assertEqual(events.observe(job, {name: {"filament_detected": True}},
                                        at=float("nan"), selected=[name]), ())
        self.assertEqual(events.events, ())

    def test_parser_only_transition_and_invalid_manual_estimates(self):
        events = RunoutEvents()
        name = "filament_motion_sensor spool"
        job = ("x", 1)
        events.observe(job, {name: {"filament_detected": True}}, at=1, selected=[name])
        found = events.observe(job, {name: {"filament_detected": False}}, at=2,
                               selected=[name], file_position=123)
        self.assertEqual((found[0].precision, found[0].file_position,
                          found[0].layer), ("parser", 123, None))
        self.assertIsNone(events.mark_manual(job, at=3, layer=True, split=1))
        self.assertIsNone(events.mark_manual(job, at=float("inf"), layer=1, split=1))
        self.assertIsNone(events.mark_manual(None, at=3, layer=1, split=1))

    def test_restore_discards_malformed_evidence(self):
        events = RunoutEvents()
        base = {"kind": "sensor", "precision": "motion", "sensor": "a",
                "observed_at": 1, "layer": 1, "split": 2, "file_position": 3}
        rows = [dict(base, **change) for change in (
            {"kind": "other"}, {"precision": "unknown"}, {"sensor": 123},
            {"observed_at": -1}, {"layer": True}, {"split": -1},
            {"file_position": "3"}, {"layer": None},
        )]
        events.restore(("x", 1), rows + [base])
        self.assertEqual(len(events.events), 1)
        self.assertEqual(events.events[0].file_position, 3)

    def test_pause_owner_refuses_unknown_configuration(self):
        name = "filament_switch_sensor inlet"
        self.assertIsNone(pause_owner(None, name))
        self.assertIsNone(pause_owner({name: {"pause_on_runout": "perhaps"}}, name))
        self.assertIsNone(pause_owner({name: []}, name))


if __name__ == "__main__":
    unittest.main()
