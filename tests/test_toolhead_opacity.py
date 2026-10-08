"""Original alpha, sparse precedence, hidden picking and packed draw admission."""
import unittest

import numpy as np

from mpf.geometry.ToolheadGeometry import mesh_from_arrays, preview_buffer, pick_projected
from mpf.geometry.ToolheadMaterials import material_parameters, local_finish_parameters, local_finish_overrides, resolved_finishes
from mpf.geometry.ToolheadOpacity import opacity_overrides, opacity_colours, opacity_preview


class OpacityTests(unittest.TestCase):
    def setUp(self):
        self.mesh = mesh_from_arrays([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]]*3,
            [[.8, .1, .2, .35], [.8, .1, .2, 1], [.2, .3, .4, 1]], [4, 5, 6], body_ids=[0, 0, 1],
            metadata={'materials': [{'name': '', 'description': '', 'source': 'unknown'}], 'bodies': [{'name': 'Housing', 'source': 'unknown', 'centre': None, 'axis': None},
                                 {'name': 'Rotor', 'source': 'unknown', 'centre': None, 'axis': None}]})

    def test_invalid_finish_rows_do_not_consume_accepted_entry_budget(self):
        value = {str(index): None for index in range(2048)}
        value.update({'2048': {'roughness': .4}, '2049': {'reflectivity': 'automatic'}})
        self.assertEqual(local_finish_overrides(value), {'2048': {'roughness': .4}, '2049': {'reflectivity': 'automatic'}})
        value = {str(index): {'roughness': .3} for index in range(2050)}
        self.assertEqual(len(local_finish_overrides(value)), 2048)

    def test_face_wins_over_body_and_reset_retains_imported_colour_alpha(self):
        colours = opacity_colours(self.mesh, {'0': .2}, {'5': .8})
        np.testing.assert_allclose(colours[:, 3], [.2, .8, 1])
        np.testing.assert_array_equal(colours[:, :3], self.mesh.colours[:, :3])
        self.assertFalse(colours.flags.writeable)
        self.assertIs(opacity_colours(self.mesh), self.mesh.colours)
        np.testing.assert_allclose(self.mesh.colours[:, 3], [.35, 1, 1])

    def test_invalid_values_and_stale_asset_ids_never_apply(self):
        raw = {'0': 0, '1': 1, '2': .5, 'x': .5, '-1': .5, '00': False, '4': float('nan'), '5': -1, '6': 1.1}
        self.assertEqual(opacity_overrides(raw, self.mesh, bodies=True), {'0': 0., '1': 1.})
        self.assertEqual(opacity_overrides({'4': .1, '5': float('inf')}, self.mesh), {'4': .1})

    def test_effective_opacity_changes_packed_draw_ranges_before_upload(self):
        colours = opacity_colours(self.mesh, {'0': .25}, {'6': 0})
        packed = preview_buffer(self.mesh, colours=colours)
        self.assertEqual(packed[4][0], 0)
        data = np.frombuffer(packed[0], np.float32).reshape(-1, 3, 18)
        np.testing.assert_array_equal(data[:, 0, 9], colours[:, 3])

    def test_invisible_geometry_is_pickable_only_during_opacity_selection(self):
        colours = opacity_colours(self.mesh, {'0': 0, '1': 0})
        self.assertIsNone(pick_projected(self.mesh, self.mesh.triangles, .2, .2, nearest=True, colours=colours))
        ghost = opacity_preview(self.mesh, colours, [0], [6])
        self.assertEqual(pick_projected(self.mesh, self.mesh.triangles, .2, .2, nearest=True, body=True, colours=ghost), 0)
        np.testing.assert_allclose(ghost[:, 3], .12)
        np.testing.assert_array_equal(colours[:, 3], [0, 0, 0])

    def test_sparse_overrides_have_a_fixed_storage_bound(self):
        checked = opacity_overrides({str(index): .5 for index in range(5000)})
        self.assertEqual(len(checked), 2048)

    def test_imported_face_opacity_bypasses_a_body_override(self):
        colours = opacity_colours(self.mesh, {'0': .1}, {'4': 'imported'})
        np.testing.assert_allclose(colours[:, 3], [.35, .1, 1])

    def test_face_material_auto_bypasses_body_and_finish_fields_are_independent(self):
        classified = material_parameters(self.mesh, {'4': 'automatic', '5': 'petg'}, {'0': 'metal'})
        np.testing.assert_array_equal(classified[:, 3], [0, 10, 0])
        finishes = local_finish_parameters(self.mesh,
            {'0': {'roughness': .8, 'reflectivity': .9}}, {'4': {'roughness': 'automatic'}, '5': {'reflectivity': .1}})
        np.testing.assert_allclose(finishes, [[-1, .9], [.8, .1], [-1, -1]])
        resolved = resolved_finishes(self.mesh, {'5': 'petg'}, {'0': 'metal'},
            {'petg': {'roughness': .3}}, {'0': {'roughness': .8}}, {'5': {'roughness': 'automatic'}})
        np.testing.assert_allclose(resolved[:, 0], [.8, .3, .5])
        self.assertEqual(local_finish_overrides({'0': {'roughness': True, 'reflectivity': 2, 'opacity': .5}}), {})
