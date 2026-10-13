"""Fail-closed paused-print return and modal handover policy."""
import unittest

from mpf.printing.RecoveryHandover import RecoveryRefused, prepare_return
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
    def test_staged_return_and_modal_state_are_explicit(self):
        plan = prepare_return(status(), source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(plan.clearance_z, 15)
        self.assertIn("G1 Z15 F600", plan.return_script)
        self.assertIn("G1 X40 Y50 F3000", plan.return_script)
        self.assertIn("G1 Z2 F300", plan.return_script)
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
        self.assertLess(plan.prime_script.index("G1 Z15 F600"),
                        plan.prime_script.index("G1 X10 Y15 F3000"))
        self.assertLess(plan.prime_script.index("G1 X10 Y15 F3000"),
                        plan.prime_script.index("G1 Z3 F300"))
        self.assertIn("G1 E30 F150", plan.prime_script)
        self.assertIn("M83", plan.prime_script)
        self.assertEqual(prepare_return(fresh, source(), filename="part.gcode",
                                        printed_top_z=8).prime_script, "")
        for location, amount in (((10, 15, 1), 30), ((300, 15, 3), 30),
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
        tight["toolhead"]["axis_maximum"][2] = 12
        with self.assertRaises(RecoveryRefused):
            prepare_return(tight, source(), filename="part.gcode", printed_top_z=8)

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


if __name__ == "__main__":
    unittest.main()
