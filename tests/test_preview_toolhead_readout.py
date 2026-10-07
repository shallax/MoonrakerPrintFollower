"""Physical readouts never invent zeros or confer a movement permission."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

PATH = Path(__file__).resolve().parents[1] / 'mpf/monitor/toolhead/ToolheadReadout.py'
SPEC = importlib.util.spec_from_file_location('toolhead_readout', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ToolheadReadoutTests(unittest.TestCase):
    def snapshot(self, position=(1.25, 2.5, 3.75), origin=(0, 0, -.075)):
        return SimpleNamespace(core={'motion_report': {'live_position': position},
                                     'gcode_move': {'homing_origin': origin}},
                               auxiliary={'configfile': {'config': {'bed_mesh': {}, 'z_tilt': {}}}},
                               objects=('quad_gantry_level',))

    def test_physical_position_and_offset_are_independent_of_preview_toolpath(self):
        values = MODULE.readout(self.snapshot(), True, 'printing')
        self.assertEqual([values['position' + a] for a in 'XYZ'], ['1.25', '2.50', '3.75'])
        self.assertEqual(values['offsetText'], '-0.075')
        self.assertEqual(values['statusText'], 'Printing')
        self.assertEqual([r['key'] for r in values['actionRows']],
                         ['quad_gantry_level', 'z_tilt', 'bed_mesh', 'motors'])
        self.assertTrue(all(not r['allowed'] for r in values['actionRows']))

    def test_disconnection_clears_previously_valid_readings_and_capabilities(self):
        values = MODULE.readout(self.snapshot(), False, 'printing')
        self.assertEqual([values['position' + a] for a in 'XYZ'], ['—'] * 3)
        self.assertEqual(values['offsetText'], '—')
        self.assertEqual(values['actionRows'], [])
        self.assertFalse(values['connected'])

    def test_capability_menu_includes_bed_screws_and_delta_calibration(self):
        snapshot = self.snapshot()
        snapshot.auxiliary['configfile']['config'].update(bed_screws={}, delta_calibrate={})
        values = MODULE.readout(snapshot, True, 'standby')
        self.assertIn('bed_screws', [r['key'] for r in values['actionRows']])
        self.assertIn('delta_calibrate', [r['key'] for r in values['actionRows']])

    def test_absent_partial_and_nonfinite_readings_remain_absent(self):
        for position, expected in (((), ['—'] * 3), ((1,), ['1.00', '—', '—']),
                                   ((float('nan'), float('inf'), True), ['—'] * 3),
                                   ('123', ['—'] * 3)):
            with self.subTest(position=position):
                values = MODULE.readout(self.snapshot(position, (0, 0, float('nan'))), True, 'standby')
                self.assertEqual([values['position' + a] for a in 'XYZ'], expected)
                self.assertEqual(values['offsetText'], '—')

    def test_undiscovered_auxiliary_lane_does_not_block_core_readings(self):
        values = MODULE.readout(SimpleNamespace(core={}), True, 'standby')
        self.assertEqual([values['position' + a] for a in 'XYZ'], ['—'] * 3)
        self.assertEqual([r['key'] for r in values['actionRows']], ['motors'])


if __name__ == '__main__':
    unittest.main()
