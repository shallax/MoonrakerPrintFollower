"""Whole light-set admission and cube-depth geometry, without a GL context."""
from dataclasses import FrozenInstanceError
import math
import unittest

import numpy as np

from mpf.toolhead.ToolheadShadowValues import (
    ShadowLight, ShadowMapPlan, ShadowProjection, attached_light, head_lights, certify_key,
)


class ShadowValuesTests(unittest.TestCase):
    def test_source_key_refuses_mutable_wrappers_and_nonfinite_values(self):
        certify_key(('head', (9, b'pose', True, None, 1.25)))
        for key in ([1, 2], ('scene', object()), ('pose', math.nan), {'geometry': 1}):
            with self.assertRaises(ValueError):
                certify_key(key)
    def light(self, index=0, **fields):
        values = dict(kind='attached', index=index, position=(1, 2, 3),
                      direction=(0, 0, -1), colour=(1, .5, 0), reach=40)
        values.update(fields)
        return ShadowLight(**values)

    def test_frozen_delivery_owns_values_without_normalizing_original_directions(self):
        position = [1, 2, 3]
        light = self.light(position=position, direction=(0, 0, -2))
        position[0] = 900
        self.assertEqual(light.position, (1., 2., 3.))
        self.assertEqual(light.direction, (0., 0., -2.))
        with self.assertRaises(FrozenInstanceError):
            light.position = (0, 0, 0)
        self.assertFalse(self.light(colour=(0, 0, 0)).active)

    def test_actual_cad_delivery_is_bit_identical_to_prior_shader_arithmetic(self):
        rng = np.random.default_rng(100)
        for _ in range(100):
            position, direction, tip, world = rng.normal(size=(4, 3)) * 350
            values = position, direction, [2., .125, 0.], 75.
            delivered = attached_light(0, values, tip, world)
            local = np.asarray(position) - tip
            old = np.asarray([float(local[0]+world[0]), float(local[2]+world[1]), float(-local[1]+world[2])])
            self.assertEqual(np.asarray(delivered.position).tobytes(), old.tobytes())
            self.assertEqual(delivered.direction, (direction[0], direction[2], -direction[1]))
            self.assertEqual(delivered.colour, (2., .125, 0.))

    def test_white_sources_keep_delivered_world_positions_not_head_relative_positions(self):
        lights = head_lights((350, 300, 400))
        self.assertEqual(tuple(light.position for light in lights),
                         ((-175., 400., 0.), (175., 400., 0.), (0., 400., -150.), (0., 400., 150.)))
        self.assertEqual(tuple(light.direction for light in lights),
                         ((1., -1., 0.), (-1., -1., 0.), (0., -1., 1.), (0., -1., -1.)))

    def test_invalid_consumer_numeric_and_range_inputs_refuse(self):
        for fields in (dict(kind='spot'), dict(index=True), dict(index=8), dict(index=-1),
                       dict(position=[1, 2]), dict(position=[1, math.nan, 3]),
                       dict(direction=(0, 0, 0)), dict(colour=(-1, 0, 0)),
                       dict(reach=0), dict(reach=math.inf)):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.light(**fields)
        self.assertIsNone(self.light(reach=None).reach)

    def test_complete_map_admission_charges_both_sets_work_and_retained_storage(self):
        lights = head_lights((350, 350, 350)) + tuple(self.light(i) for i in range(6))
        plan = ShadowMapPlan(lights, 28 * 1024**2)
        self.assertEqual(plan.nbytes, 512 * 1024**2)
        self.assertEqual(plan.lights, lights)
        with self.assertRaisesRegex(ValueError, 'exceeds budget'):
            ShadowMapPlan(lights, 28 * 1024**2 + 1)
        # Refusing all 12 sources is preferable to silently selecting ten.
        with self.assertRaisesRegex(ValueError, 'exceeds budget'):
            ShadowMapPlan(head_lights((350, 350, 350)) + tuple(self.light(i) for i in range(8)))

    def test_only_zero_energy_can_skip_a_map_and_duplicate_consumers_refuse(self):
        lit, dark = self.light(), self.light(1, colour=(0, 0, 0))
        plan = ShadowMapPlan([lit, dark])
        self.assertEqual(plan.lights, (lit,))
        self.assertEqual(ShadowMapPlan((dark,)).nbytes, 0)
        self.assertEqual(ShadowMapPlan((), 40).nbytes, 40)
        for values, retained in (((lit, lit), 0), ((object(),), 0), ((lit,)*2062, 0), ((), -1), ((), True)):
            with self.subTest(values=values, retained=retained), self.assertRaises(ValueError):
                ShadowMapPlan(values, retained)

    def test_platform_recipe_sources_are_distinct_from_path_camera_source(self):
        lights = tuple(self.light(kind=kind, index=index, direction=None, reach=None)
                       for kind, index in (('path', 0), ('platform', 0), ('platform', 1)))
        self.assertEqual(ShadowMapPlan(lights).lights, lights)
        with self.assertRaises(ValueError):
            self.light(kind='path', direction=(1, 0, 0))

    def test_gpu_admission_refuses_overflow_underflow_and_collapsed_projection(self):
        for fields in (dict(position=(1e40, 0, 0)), dict(direction=(1e-30, 0, 0)),
                       dict(direction=(1e30, 0, 0)), dict(colour=(1e-35, 0, 0)),
                       dict(colour=(1e40, 0, 0)), dict(reach=1e-60)):
            light = self.light(**fields)  # Existing ordinary delivery remains available.
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                ShadowMapPlan((light,))
        for near, far in ((1, 1+1e-8), (1e-60, 1)):
            with self.assertRaisesRegex(ValueError, 'collapses'):
                ShadowProjection((0, 0, 0), near, far)
        with self.assertRaises(ValueError):
            ShadowProjection((1e40, 0, 0), 1, 10)

    def test_depth_matches_actual_perspective_matrix_on_all_six_faces_and_seams(self):
        projection = ShadowProjection((3, -7, 9), .01, 1000)
        # Independent standard GL perspective homogeneous divide. At a cube
        # seam the dominant-axis distance is shared by both adjacent faces.
        matrix = np.zeros((4, 4))
        matrix[0, 0] = matrix[1, 1] = 1
        matrix[2, 2] = -(projection.far+projection.near)/(projection.far-projection.near)
        matrix[2, 3] = -2*projection.far*projection.near/(projection.far-projection.near)
        matrix[3, 2] = -1
        for offset in ((20, 3, 1), (-20, 3, 1), (1, 20, 3), (1, -20, 3),
                       (1, 3, 20), (1, 3, -20), (20, 20, 0), (20, 20, 20)):
            distance = max(map(abs, offset))
            clip = matrix @ [0, 0, -distance, 1]
            depth = clip[2]/clip[3]*.5+.5
            point = tuple(o+d for o, d in zip(projection.origin, offset, strict=True))
            self.assertAlmostEqual(projection.depth(point), depth, places=14)
        radial = math.sqrt(20**2*3)
        wrong = projection.far/(projection.far-projection.near)*(1-projection.near/radial)
        self.assertNotEqual(projection.depth((23, 13, 29)), wrong)

    def test_outside_projection_is_unavailable_not_silently_bright_or_dark(self):
        projection = ShadowProjection([0, 0, 0], 1, 10)
        self.assertEqual(projection.depth((1, 0, 0)), 0)
        self.assertEqual(projection.depth((10, 0, 0)), 1)
        for point in ((.9, 0, 0), (0, 10.1, 0), (0, 0, 0)):
            with self.assertRaises(ValueError):
                projection.depth(point)
        for near, far in ((0, 10), (1, 1), (2, 1), (1, 10001), (math.nan, 10), (1, math.inf)):
            with self.assertRaises(ValueError):
                ShadowProjection((0, 0, 0), near, far)

    def test_projected_rounding_allowance_requires_physical_precision_certificate(self):
        projection = ShadowProjection((0, 0, 0), .01, 1000)
        error = 2**-24
        with self.assertRaisesRegex(ValueError, 'physical precision'):
            projection.certify_precision(400, error, .01, measurement_error=error)
        better = ShadowProjection((0, 0, 0), 10, 1000)
        self.assertEqual(better.certify_precision(400, error, .01, measurement_error=error), error)
        self.assertEqual(projection.certify_precision(.31002700270027, 0, 0, measurement_error=0), 0)
        for args in ((0, error, .01), (1001, error, .01), (400, -1, .01),
                     (400, error, -1), (400, math.nan, .01), (400, 1, 1)):
            with self.assertRaises(ValueError):
                better.certify_precision(*args, measurement_error=0)
        with self.assertRaisesRegex(ValueError, 'rounds downward'):
            better.certify_precision(400, error*(1+1e-8), .01, measurement_error=0)
        for measurement in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                better.certify_precision(400, error, .01, measurement_error=measurement)
        with self.assertRaisesRegex(ValueError, 'self-shadow'):
            better.certify_precision(400, 0, .01, measurement_error=error)
        # Exactly representable .75ULP rounds stored+allowance up to a whole
        # ULP; the former nominal allowance would incorrectly accept .8mm.
        allowance = 3*2**-26
        stored = np.float32(.9999850392341614)
        self.assertGreater(float(np.float32(stored+allowance)-stored), allowance)
        with self.assertRaisesRegex(ValueError, 'physical precision'):
            projection.certify_precision(400, allowance, .8, measurement_error=0)
        # The same forward-axis budget cannot be mislabeled Euclidean: rays
        # at the cube corner have sqrt3 times the forward distance error.
        k = -better.depth_coefficients[1]/2
        total = error+2**-24
        forward_error = total*400**2/(k-total*400)
        with self.assertRaisesRegex(ValueError, 'physical precision'):
            better.certify_precision(400, error, forward_error*1.1, measurement_error=0)


if __name__ == '__main__':
    unittest.main()
