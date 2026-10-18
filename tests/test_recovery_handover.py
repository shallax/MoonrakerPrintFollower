"""Fail-closed paused-print return and modal handover policy."""
import unittest
from dataclasses import replace

from mpf.printing.RecoveryHandover import RecoveryRefused, heating_script, prepare_return
from mpf.application.RecoveryPlanning import RecoveryModalState, RecoverySourcePlan


def status():
    return {
        "print_stats": {"state": "paused", "filename": "part.gcode"},
        "virtual_sdcard": {"file_size": 1000, "file_position": 600, "is_active": False},
        "pause_resume": {"is_paused": True},
        "toolhead": {"homed_axes": "xyz", "position": [100, 100, 10],
                     "axis_minimum": [0, 0, 0], "axis_maximum": [250, 250, 250]},
        "gcode_move": {"position": [100, 100, 10], "gcode_position": [100, 100, 10, 0],
                       "homing_origin": [0, 0, 0], "speed_factor": 1.0, "extrude_factor": 1.0},
        "configfile": {"config": {"printer": {"kinematics": "corexy"},
                                  "extruder": {"min_extrude_temp": 170, "max_temp": 300},
                                  "heater_bed": {"max_temp": 120}}},
        "extruder": {"temperature": 210, "target": 210},
        "heater_bed": {"temperature": 60, "target": 60},
    }


def source():
    state = RecoveryModalState((40, 50, 2), 15.25, True, False, 1800,
                               80, 95, 128, 210, 60)
    return RecoverySourcePlan(300, 1000, "a" * 64, state)


class RecoveryHandoverTests(unittest.TestCase):
    def test_prime_height_policy_and_ceiling_apply_to_every_travel(self):
        for restart, configured, expected in ((2, 100, 100), (2, 2, 7), (2, 1, 7),
                                               (248, 100, 250), (250, 100, 250),
                                               (248, 249, 249), (248, 300, 250)):
            with self.subTest(restart=restart, configured=configured):
                original = source()
                chosen = replace(original, state=replace(original.state, position=(40, 50, restart)))
                plan = prepare_return(status(), chosen, filename="part.gcode", printed_top_z=restart,
                                      prime_enabled=True, prime_xyz=(10, 15, configured))
                self.assertEqual(plan.prime_target, (10, 15, expected))
                self.assertIn(f"G1 Z{expected} F300", plan.prime_script)
                for script in (plan.prime_script, plan.return_script):
                    for line in script.splitlines():
                        if line.startswith("G1 Z"):
                            self.assertLessEqual(float(line.split()[1][1:]), 250)

    def test_malformed_state_reference_and_prime_coordinates_are_refused(self):
        with self.assertRaisesRegex(RecoveryRefused, "Fresh printer status"):
            prepare_return(None, source(), filename="part.gcode", printed_top_z=8)
        for kwargs in ({"z_reference_mm": 1}, {"z_reference_mm": -1},
                       {"prime_enabled": True, "prime_xyz": (10, 15)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RecoveryRefused):
                prepare_return(status(), source(), filename="part.gcode", printed_top_z=8, **kwargs)

    def test_near_target_temperature_still_must_allow_extrusion(self):
        fresh = status()
        fresh["extruder"].update(temperature=168, target=170)
        planned = replace(source(), state=replace(source().state, hotend_target=170))
        with self.assertRaisesRegex(RecoveryRefused, "minimum extrusion"):
            prepare_return(fresh, planned, filename="part.gcode", printed_top_z=8)

    def test_cleanup_requires_a_numeric_normal_idle_timeout(self):
        fresh = status()
        config = fresh["configfile"]["config"]
        config["gcode_macro PAUSE"] = {"gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\nSET_IDLE_TIMEOUT TIMEOUT=9999"}
        config["idle_timeout"] = {"timeout": "not-a-number"}
        with self.assertRaisesRegex(RecoveryRefused, "normal idle timeout"):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8, saved_state="PAUSE")

    def test_invalid_prime_dwell_is_refused_before_motion(self):
        for dwell in (-1, 301, float("nan"), float("inf")):
            with self.subTest(dwell=dwell), self.assertRaises(RecoveryRefused):
                prepare_return(status(), source(), filename="part.gcode", printed_top_z=8,
                    prime_enabled=True, prime_xyz=(10, 15, 3), prime_dwell_s=dwell)

    def test_unloaded_object_top_reference_maps_file_z_to_physical_zero(self):
        fresh = status()
        fresh["print_stats"]["state"] = "standby"
        fresh["pause_resume"]["is_paused"] = False
        plan = prepare_return(fresh, source(), filename="part.gcode",
                              printed_top_z=12, idle=True, z_reference_mm=2)
        self.assertEqual(plan.z_reference_script, "G92 Z12")
        self.assertEqual(plan.target[2], 2)
        self.assertEqual(plan.clearance_z, 7)
        self.assertIn("G1 Z7", plan.return_script)
        with self.assertRaisesRegex(RecoveryRefused, "below the homed object-top"):
            prepare_return(fresh, source(), filename="part.gcode",
                           printed_top_z=12, idle=True, z_reference_mm=3)
        fresh["toolhead"]["position"][2] = 4
        fresh["gcode_move"]["gcode_position"][2] = 4
        fresh["gcode_move"]["position"][2] = 4
        fresh["toolhead"]["axis_maximum"][2] = 6
        capped = prepare_return(fresh, source(), filename="part.gcode",
                                printed_top_z=12, idle=True, z_reference_mm=2)
        self.assertEqual(capped.clearance_z, 6)

    def test_unloaded_z_reference_replaces_an_existing_g92_z_origin(self):
        fresh = status()
        fresh["print_stats"]["state"] = "standby"
        fresh["pause_resume"]["is_paused"] = False
        fresh["gcode_move"]["gcode_position"][2] = 8
        fresh["gcode_move"]["homing_origin"][2] = 1
        plan = prepare_return(fresh, source(), filename="part.gcode",
                              printed_top_z=12, idle=True, z_reference_mm=2)
        self.assertEqual(plan.z_reference_script, "G92 Z12")

    def test_staged_return_and_modal_state_are_explicit(self):
        plan = prepare_return(status(), source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(plan.clearance_z, 7)
        self.assertIn("G1 Z7 F600", plan.return_script)
        self.assertIn("G1 X40 Y50 F3000", plan.return_script)
        self.assertNotIn("G1 Z2 F300", plan.return_script)
        self.assertIn("G1 Z2 F300", plan.resume_script)
        self.assertNotIn("M400", plan.resume_script)
        self.assertNotIn("G4 ", plan.resume_script)
        self.assertIn("M221 S95", plan.modal_script)
        self.assertLess(plan.modal_script.index("M221 S95"), plan.modal_script.index("G92 E15.25"))
        self.assertIn("M82", plan.modal_script)
        self.assertNotIn("M24", plan.modal_script)

    def test_optional_prime_is_staged_above_resume_layer_and_bounded(self):
        fresh = status()
        fresh["configfile"]["config"]["extruder"]["max_extrude_only_distance"] = 40
        plan = prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8,
                              prime_enabled=True, prime_xyz=(10, 15, 3),
                              prime_amount_mm=30, prime_speed_mm_s=2.5)
        self.assertEqual(plan.prime_target, (10, 15, 3))
        self.assertLess(plan.prime_script.index("G1 Z10 F600"),
                        plan.prime_script.index("G1 X10 Y15 F3000"))
        self.assertLess(plan.prime_script.index("G1 X10 Y15 F3000"),
                        plan.prime_script.index("G1 Z3 F300"))
        self.assertIn("G1 E30 F150", plan.prime_script)
        self.assertIn("M83", plan.prime_script)
        self.assertEqual(prepare_return(fresh, source(), filename="part.gcode",
                                        printed_top_z=8).prime_script, "")
        for location, amount in (((300, 15, 3), 30),
                                 ((10, 15, 3), 41)):
            with self.subTest(location=location, amount=amount):
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode",
                                   printed_top_z=8, prime_enabled=True,
                                   prime_xyz=location, prime_amount_mm=amount)

    def test_refuses_unconfirmed_pause_custom_macros_and_lost_homing(self):
        for change in (
            lambda s: s["pause_resume"].update(is_paused=False),
            lambda s: s["virtual_sdcard"].update(is_active=True),
            lambda s: s["configfile"]["config"].update({"gcode_macro RESUME": {}}),
            lambda s: s["toolhead"].update(homed_axes="xy"),
            lambda s: s["print_stats"].update(filename="other.gcode"),
        ):
            with self.subTest(change=change):
                fresh = status()
                change(fresh)
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8)

    def test_refuses_cold_nozzle_and_insufficient_z_travel(self):
        cold = status()
        cold["extruder"]["temperature"] = 150
        with self.assertRaises(RecoveryRefused):
            prepare_return(cold, source(), filename="part.gcode", printed_top_z=8)
        tight = status()
        tight["toolhead"]["position"][2] = 4
        tight["gcode_move"]["gcode_position"][2] = 4
        tight["gcode_move"]["position"][2] = 4
        tight["toolhead"]["axis_maximum"][2] = 6
        capped = prepare_return(tight, source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(capped.clearance_z, 6)
        outside = replace(source(), state=replace(source().state, position=(40, 50, 7)))
        with self.assertRaises(RecoveryRefused):
            prepare_return(tight, outside, filename="part.gcode", printed_top_z=8)

    def test_custom_pause_uses_discovered_saved_state_and_restores_sensor(self):
        fresh = status()
        config = fresh["configfile"]["config"]
        config["gcode_macro PAUSE"] = {
            "gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
                     "SET_FILAMENT_SENSOR SENSOR=upper ENABLE=0\n"
                     "SET_IDLE_TIMEOUT TIMEOUT=43200"}
        config["gcode_macro RESUME"] = {"gcode": "BASE_RESUME"}
        config["idle_timeout"] = {"timeout": "600"}
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8)
        plan = prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8,
                              saved_state="PAUSE")
        self.assertEqual(plan.restore_script, "RESTORE_GCODE_STATE NAME=PAUSE MOVE=0")
        self.assertIn("SET_FILAMENT_SENSOR SENSOR=upper ENABLE=1", plan.finish_script)
        self.assertIn("SET_IDLE_TIMEOUT TIMEOUT=600", plan.finish_script)

    def test_preparation_macro_must_only_restore_without_movement(self):
        fresh = status()
        config = fresh["configfile"]["config"]
        config["gcode_macro MPF_PREPARE"] = {
            "gcode": "RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=0\nG1 X100"}
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8,
                           prepare_macro="MPF_PREPARE")

    def test_heater_targets_are_waited_for_and_bounded_by_printer_config(self):
        self.assertEqual(heating_script(status(), source())[0],
                         "M140 S60\nM104 S210\nM190 S60\nM109 S210")
        self.assertEqual(heating_script(status(), source(), bed_target=0)[0],
                         "M104 S210\nM109 S210")
        for targets in ({"hotend_target": 100}, {"hotend_target": 300},
                        {"bed_target": -1}, {"bed_target": 120}):
            with self.subTest(targets=targets):
                with self.assertRaises(RecoveryRefused):
                    heating_script(status(), source(), **targets)
        unavailable = status()
        unavailable["configfile"].pop("config")
        with self.assertRaises(RecoveryRefused):
            heating_script(unavailable, source())

    def test_recovery_refuses_unavailable_or_unstable_heater_status(self):
        for change in (
            lambda s: s["extruder"].update(temperature=None),
            lambda s: s["extruder"].update(target=190),
            lambda s: s["heater_bed"].update(temperature=40),
            lambda s: s["configfile"]["config"]["extruder"].update(min_extrude_temp=None),
        ):
            with self.subTest(change=change):
                fresh = status()
                change(fresh)
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8)

    def test_pause_macro_contract_and_cleanup_are_verified(self):
        for macro, saved_state, extra in (
            ("G1 X10", "PAUSE", {}),
            ("SAVE_GCODE_STATE NAME=PAUSE", "PAUSE", {}),
            ("BASE_PAUSE", "PAUSE", {}),
            ("SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
             "SET_FILAMENT_SENSOR SENSOR={name} ENABLE=0", "PAUSE", {}),
            ("SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
             "SET_IDLE_TIMEOUT TIMEOUT=9999", "PAUSE", {}),
        ):
            with self.subTest(macro=macro):
                fresh = status()
                fresh["configfile"]["config"]["gcode_macro PAUSE"] = {"gcode": macro}
                fresh["configfile"]["config"].update(extra)
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode",
                                   printed_top_z=8, saved_state=saved_state)

    def test_unloaded_handover_and_non_default_modal_state(self):
        fresh = status()
        fresh["print_stats"]["state"] = "standby"
        fresh["pause_resume"]["is_paused"] = False
        state = source().state
        from dataclasses import replace
        custom = replace(source(), state=replace(state, acceleration=700,
                         fan_speed=0, absolute_xyz=False, force_relative_e=True))
        plan = prepare_return(fresh, custom, filename="part.gcode", printed_top_z=8,
                              idle=True)
        self.assertEqual(plan.restore_script, "")
        self.assertIn("M204 S700", plan.modal_script)
        self.assertIn("M107", plan.modal_script)
        self.assertIn("G91\nM83", plan.modal_script)
        fresh["pause_resume"]["is_paused"] = True
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, custom, filename="part.gcode", printed_top_z=8,
                           idle=True)

    def test_prime_amount_and_velocity_respect_extruder_limits(self):
        for amount, speed, max_velocity in ((0, 2, None), (201, 2, None),
                                            (20, 0, None), (20, 21, None),
                                            (20, 3, 2)):
            with self.subTest(amount=amount, speed=speed, max_velocity=max_velocity):
                fresh = status()
                if max_velocity is not None:
                    fresh["configfile"]["config"]["extruder"]["max_extrude_only_velocity"] = max_velocity
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode",
                                   printed_top_z=8, prime_enabled=True,
                                   prime_xyz=(10, 15, 3), prime_amount_mm=amount,
                                   prime_speed_mm_s=speed)

    def test_source_macro_and_saved_state_choices_are_bounded_by_config(self):
        fresh = status()
        for kwargs in ({"setup_macros": ("UNLISTED",)},
                       {"saved_state": "invalid-state"},
                       {"prepare_macro": "invalid-state"},
                       {"prepare_macro": "UNLISTED"},
                       {"saved_state": "CUSTOM"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RecoveryRefused):
                    prepare_return(fresh, source(), filename="part.gcode",
                                   printed_top_z=8, **kwargs)
        fresh["configfile"]["config"]["extruder1"] = {}
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8)
        fresh = status()
        fresh["configfile"]["config"].pop("extruder")
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8)

    def test_invalid_clearance_and_unavailable_normal_idle_timeout_refuse(self):
        for top, clearance in ((-1, 5), (8, 0)):
            with self.subTest(top=top, clearance=clearance):
                with self.assertRaises(RecoveryRefused):
                    prepare_return(status(), source(), filename="part.gcode",
                                   printed_top_z=top, clearance_mm=clearance)
        fresh = status()
        fresh["configfile"]["config"]["gcode_macro PAUSE"] = {
            "gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
                     "SET_IDLE_TIMEOUT TIMEOUT=9999"}
        fresh["configfile"]["config"]["idle_timeout"] = {"timeout": 0}
        with self.assertRaises(RecoveryRefused):
            prepare_return(fresh, source(), filename="part.gcode", printed_top_z=8,
                           saved_state="PAUSE")


if __name__ == "__main__":
    unittest.main()

class SelectedLayerClearanceTests(unittest.TestCase):
    def test_parked_nozzle_and_dry_print_height_do_not_inflate_return(self):
        for clearance in (1, 5, 12, 30):
            with self.subTest(clearance=clearance):
                fresh = status()
                fresh["toolhead"]["position"][2] = 50
                fresh["gcode_move"]["position"][2] = 50
                fresh["gcode_move"]["gcode_position"][2] = 50
                plan = prepare_return(fresh, source(), filename="part.gcode",
                                      printed_top_z=100, clearance_mm=clearance)
                self.assertEqual(plan.clearance_z, 2 + clearance)
                moves = [line for line in plan.return_script.splitlines() if line.startswith("G1")]
                self.assertEqual(moves, [f"G1 Z{2+clearance} F600",
                                        "G1 X40 Y50 F3000"])

    def test_high_prime_keeps_its_own_travel_but_returns_at_layer_plus_clearance(self):
        plan = prepare_return(status(), source(), filename="part.gcode", printed_top_z=100,
                              prime_enabled=True, prime_xyz=(0, 200, 100))
        self.assertIn("G1 Z100 F600", plan.prime_script)
        self.assertIn("G1 Z7 F600", plan.return_script)
        self.assertEqual(plan.clearance_z, 7)
