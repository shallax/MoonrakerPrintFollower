# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [52df354] Verify recovery safety paths and release gates
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover

"""Sensor evidence is selected, print-bound, and never claims exact runout."""
import unittest

from mpf.printing.RunoutEvents import RunoutEvents, pause_owner


class RunoutEventsTests(unittest.TestCase):
    def test_remove_exact_bookmark_rejects_stale_jobs_and_preserves_others(self):
        events = RunoutEvents()
        job = ("part", 100, 1)
        first = events.mark_manual(job, at=1, layer=0, split=2).document()
        second = events.mark_manual(job, at=2, layer=0, split=3).document()
        self.assertFalse(events.remove(("part", 100, 2), first))
        self.assertTrue(events.remove(job, first))
        self.assertFalse(events.remove(job, first))
        self.assertEqual([e.document() for e in events.events], [second])

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

    def test_event_history_is_bounded_and_bad_observations_reset_baselines(self):
        events = RunoutEvents()
        job = ("x", 1)
        name = "filament_switch_sensor inlet"
        self.assertEqual(events.observe(None, {}, at=1), ())
        self.assertEqual(events.observe(job, [], at=1), ())
        self.assertEqual(events.observe(job, {}, at="bad"), ())
        events.observe(job, {"temperature_sensor chamber": {}, name: {"filament_detected": True}},
                       at=1, selected=[name])
        events.observe(job, {name: {"filament_detected": "unknown"}}, at=2,
                       selected=[name])
        self.assertEqual(events.observe(job, {name: {"filament_detected": False}},
                                        at=3, selected=[name]), ())
        for i in range(70):
            events.mark_manual(job, at=i + 10, layer=0, split=i)
        self.assertEqual(len(events.events), 64)
        self.assertEqual(events.events[0].split, 6)
        self.assertIsNone(events.mark_manual(job, at="bad", layer=0, split=0))

    def test_removed_sensor_resets_baseline_and_transition_history_is_bounded(self):
        events = RunoutEvents()
        job, name = ("x", 1), "filament_switch_sensor inlet"
        events.observe(job, {name: {"filament_detected": True}}, at=1, selected=[name])
        events.observe(job, {}, at=2, selected=[name])
        self.assertEqual(events.observe(job, {name: {"filament_detected": False}}, at=3, selected=[name]), ())
        for i in range(70):
            events.observe(job, {name: {"filament_detected": True}}, at=10 + i * 2, selected=[name])
            events.observe(job, {name: {"filament_detected": False}}, at=11 + i * 2, selected=[name])
        self.assertEqual(len(events.events), 64)
        self.assertEqual(events.events[0].observed_at, 23)

    def test_restored_history_filters_missing_fields_and_caps_records(self):
        events = RunoutEvents()
        base = {"kind": "manual", "precision": "estimate", "sensor": "",
                "observed_at": 1, "layer": 0, "split": 0, "file_position": None}
        bad = [None, dict(base, observed_at="bad"), dict(base, sensor="x" * 129)]
        events.restore(None, [base])
        self.assertEqual(events.events, ())
        events.restore(("x", 1), bad + [base])
        self.assertEqual(len(events.events), 1)
        events.restore(("x", 1), [dict(base, observed_at=i) for i in range(70)])
        self.assertEqual(len(events.events), 64)
        self.assertEqual(events.events[0].observed_at, 6)


if __name__ == "__main__":
    unittest.main()
