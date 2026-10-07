"""Physical guard boundaries using invented Klipper status, never a printer."""
import unittest

from mpf.monitor.toolhead.PhysicalMotion import envelope, prepare, target_fields, UnsafeMotion
from mpf.monitor.toolhead.ToolheadPolicy import JogOp


def state(position=(100, 100, 10), base=(0, 0, 0), origin=(0, 0, 0), mesh=None):
    result = {
        'toolhead': {'position': list(position), 'homed_axes': 'xyz',
                     'axis_minimum': [0, 0, -5], 'axis_maximum': [200, 200, 200]},
        'gcode_move': {'position': list(position), 'gcode_position': [p-b for p, b in zip(position, base, strict=False)],
                       'homing_origin': list(origin), 'absolute_coordinates': False},
        'configfile': {'config': {'printer': {'kinematics': 'corexy'}}},
        'print_stats': {'state': 'paused'},
    }
    if mesh is not None:
        result['configfile']['config']['bed_mesh'] = {}
        result['bed_mesh'] = {'mesh_matrix': mesh}
    return result


class PhysicalMotionTests(unittest.TestCase):
    def test_each_travel_limit_and_negative_probe_floor(self):
        for axis, delta in (('x', -101), ('x', 101), ('y', -101), ('y', 101), ('z', -11), ('z', 191)):
            with self.subTest(axis=axis, delta=delta), self.assertRaises(UnsafeMotion):
                prepare(JogOp(kind='jog', axis=axis, distance=delta), state())
        self.assertIn('G1 Z-10 F600', prepare(JogOp(kind='jog', axis='z', distance=-10), state()))

    def test_g92_base_is_not_confused_with_homing_origin(self):
        values = state(base=(100, 0, 5), origin=(0, 0, .05))
        self.assertEqual(envelope(values).base, (100, 0, 5))
        self.assertIn('G1 X50 F600', prepare(JogOp(kind='move-to', targets=(50, None, None)), values))
        with self.assertRaises(UnsafeMotion):
            prepare(JogOp(kind='move-to', targets=(101, None, None)), values)

    def test_blanks_preserve_axes_and_numbers_cannot_inject_scripts(self):
        self.assertEqual(target_fields('', ' 10.25 ', ''), (None, 10.25, None))
        for fields in (('', '', ''), ('G28', '', ''), ('1\nM112', '', ''), ('nan', '', ''),
                       (True, '', ''), ('inf', '', '')):
            with self.subTest(fields=fields), self.assertRaises(UnsafeMotion):
                target_fields(*fields)

    def test_actual_mode_and_feedrate_are_saved_in_firmware_without_a_return_move(self):
        script = prepare(JogOp(kind='jog', axis='x', distance=1), state())
        self.assertEqual(script, 'SAVE_GCODE_STATE NAME=MPF_MANUAL_MOVE\nG91\nG1 X1 F3000\n'
                         'RESTORE_GCODE_STATE NAME=MPF_MANUAL_MOVE MOVE=0')
        script = prepare(JogOp(kind='move-to', targets=(None, 50, None)), state())
        self.assertIn('G90\nG1 Y50 F600', script)
        self.assertNotIn(' X', script)
        self.assertNotIn(' Z', script)

    def test_missing_unhomed_nonfinite_and_unsupported_transforms_fail_closed(self):
        changes = [(('toolhead', 'homed_axes'), 'xyw'), (('toolhead', 'position'), []),
                   (('gcode_move', 'position'), [100, 100, float('nan')]),
                   (('gcode_move', 'homing_origin'), [0, 0]),
                   (('toolhead', 'axis_maximum'), [200, 200, float('inf')])]
        for path, value in changes:
            values = state()
            values[path[0]][path[1]] = value
            with self.subTest(path=path), self.assertRaises(UnsafeMotion):
                envelope(values)
        for key in ('skew_correction', 'dual_carriage', 'bed_tilt', 'z_thermal_adjust',
                    'axis_twist_compensation', 'gcode_macro G1', 'gcode_macro SET_GCODE_OFFSET'):
            values = state()
            values['configfile']['config'][key] = {}
            with self.subTest(key=key), self.assertRaises(UnsafeMotion):
                envelope(values)
        values = state()
        values['configfile']['config']['printer']['kinematics'] = 'delta'
        with self.assertRaises(UnsafeMotion):
            envelope(values)

    def test_mesh_bounds_cover_entire_path_fade_offsets_and_rounding(self):
        values = state(mesh=[[-.1, .3], [.2, 0]])
        # Z=0.35 looks positive, but global path/compensation bounds cannot
        # prove clearance over a .3 high bed with a -.1 compensation.
        with self.assertRaises(UnsafeMotion):
            prepare(JogOp(kind='move-to', targets=(None, None, .35)), values)
        self.assertIn('G1 Z1 F600', prepare(JogOp(kind='move-to', targets=(None, None, 1)), values))
        bounds = envelope(values)
        self.assertLess(bounds.compensation_min, -.1)
        self.assertGreater(bounds.bed, .3)
        values['bed_mesh']['mesh_matrix'][0][0] = float('nan')
        with self.assertRaises(UnsafeMotion):
            envelope(values)

    def test_offsets_are_operator_calibration_independent_of_geometry(self):
        examples = [None, {}, state(position=(100, 100, 0)),
                    state(position=(100, 100, 200), mesh=[[-.1, .3], [.2, 0]])]
        for values in examples:
            with self.subTest(status=values):
                self.assertEqual(prepare(JogOp(kind='offset', distance=-.01), values),
                                 'SET_GCODE_OFFSET Z_ADJUST=-0.01 MOVE=1')
                self.assertEqual(prepare(JogOp(kind='offset', distance=.01), values),
                                 'SET_GCODE_OFFSET Z_ADJUST=+0.01 MOVE=1')
                self.assertEqual(prepare(JogOp(kind='offset', reset=True), values),
                                 'SET_GCODE_OFFSET Z=0 MOVE=1')
        for amount in (True, float('nan'), float('inf'), 'G28'):
            with self.subTest(amount=amount), self.assertRaises(UnsafeMotion):
                prepare(JogOp(kind='offset', distance=amount), None)

    def test_offset_bypasses_mesh_configuration_but_position_moves_remain_guarded(self):
        values = state(position=(100, 100, 15), mesh=[[-.9, .9], [.9, -.9]])
        values['configfile']['config']['bed_mesh'].update(fade_start=1, fade_end=2, fade_target=-.9)
        values['configfile']['config']['printer']['kinematics'] = 'delta'
        values['toolhead']['homed_axes'] = ''
        for amount in (-.05, .05):
            self.assertIn('Z_ADJUST=', prepare(JogOp(kind='offset', distance=amount), values))
        with self.assertRaises(UnsafeMotion):
            prepare(JogOp(kind='move-to', targets=(None, None, -.01)), values)

    def test_rounded_default_mesh_fade_target_is_included_in_position_bounds(self):
        values = state(position=(100, 100, 10), mesh=[[.0051, .0051], [.0051, .0051]])
        values['configfile']['config']['bed_mesh'].update(fade_start=1, fade_end=10)
        self.assertGreater(envelope(values).compensation_max, .01)
        with self.assertRaises(UnsafeMotion):
            prepare(JogOp(kind='move-to', targets=(None, None, 199.995)), values)

    def test_malformed_objects_and_physical_floor_fail_closed(self):
        for key in ('toolhead', 'gcode_move', 'configfile'):
            values = state()
            values[key] = ['malformed']
            with self.subTest(key=key), self.assertRaises(UnsafeMotion):
                envelope(values)
        values = state()
        values['configfile']['config']['printer'] = 'bad'
        with self.assertRaises(UnsafeMotion):
            envelope(values)
        values = state(position=(100, 100, 0))
        values['toolhead']['position'][2] = -.000001
        with self.assertRaises(UnsafeMotion):
            envelope(values)

    def test_emitted_fractional_target_does_not_round_across_boundary(self):
        values = state()
        values['toolhead']['axis_maximum'][0] = 199.99999996
        script = prepare(JogOp(kind='move-to', targets=(199.99999995, None, None)), values)
        emitted = float(script.split('G1 X')[1].split()[0])
        self.assertLessEqual(emitted, values['toolhead']['axis_maximum'][0])

    def test_absent_state_configuration_and_invalid_limits_fail_closed(self):
        for status in (None, [], 'unavailable'):
            with self.subTest(status=status), self.assertRaisesRegex(UnsafeMotion, 'Fresh printer state'):
                envelope(status)
        for config in (None, [], 'malformed'):
            values = state()
            values['configfile']['config'] = config
            with self.subTest(config=config), self.assertRaisesRegex(UnsafeMotion, 'Printer configuration'):
                envelope(values)
        for axis in range(3):
            values = state()
            values['toolhead']['axis_maximum'][axis] = values['toolhead']['axis_minimum'][axis]
            with self.subTest(axis=axis), self.assertRaisesRegex(UnsafeMotion, 'Invalid printer travel limits'):
                envelope(values)

    def test_axis_mapping_and_mixed_coordinate_frames_fail_closed(self):
        for mapping in ([], {'X': 1, 'Y': 0, 'Z': 2}, {'X': 0, 'Y': 1}):
            values = state()
            values['gcode_move']['axis_map'] = mapping
            with self.subTest(mapping=mapping), self.assertRaisesRegex(UnsafeMotion, 'axis mapping'):
                envelope(values)
        values = state()
        values['gcode_move']['axis_map'] = {'X': 0, 'Y': 1, 'Z': 2}
        self.assertEqual(envelope(values).position, (100, 100, 10))
        for axis in range(3):
            values = state()
            values['toolhead']['position'][axis] += .1
            with self.subTest(axis=axis), self.assertRaisesRegex(UnsafeMotion, 'coordinates do not agree'):
                envelope(values)

    def test_missing_or_malformed_mesh_cannot_authorize_position_moves(self):
        for mesh in (None, {}, [], {'mesh_matrix': None}, {'mesh_matrix': []},
                     {'mesh_matrix': [[0, 0]]}, {'mesh_matrix': [[0, 0], [0]]},
                     {'mesh_matrix': [[0, 0], 'bad']}):
            values = state(mesh=[[0, 0], [0, 0]])
            values['bed_mesh'] = mesh
            with self.subTest(mesh=mesh), self.assertRaises(UnsafeMotion):
                envelope(values)
        # Klipper publishes one empty row when compensation is cleared.
        cleared = envelope(state(mesh=[[]]))
        self.assertEqual((cleared.compensation_min, cleared.compensation_max, cleared.bed), (0, 0, 0))

    def test_center_uses_physical_midpoint_and_z_zero_uses_gcode_coordinates(self):
        values = state(base=(10, 20, 5))
        self.assertIn('G90\nG1 X90 Y80 Z45 F600', prepare(JogOp(kind='center'), values))
        self.assertIn('G90\nG1 Z0 F600', prepare(JogOp(kind='z0'), values))
        self.assertEqual(prepare(JogOp(kind='home', script='G28'), None), 'G28')



if __name__ == '__main__':
    unittest.main()
