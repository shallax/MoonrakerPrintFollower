"""Surface-light validation and outward-only model-local emitter placement."""
import unittest
from mpf.geometry.ToolheadLighting import MAX_LIGHTS, light_values, validated_lights


class SurfaceLightingTests(unittest.TestCase):
    def row(self, **values):
        return dict(position=[1, 2, 3], direction=[0, 0, -2], **values)

    def test_invalid_saved_rows_are_rejected_without_discarding_valid_neighbours(self):
        cases = [None, {}, {'position': None, 'direction': [0, 0, 1]},
            {'position': [0, 'bad', 0], 'direction': [0, 0, 1]},
            self.row(surface=float('inf')), self.row(brightness='bad')]
        for bad in cases:
            with self.subTest(bad=bad):
                self.assertEqual(len(validated_lights([bad, self.row()])), 1)
        self.assertEqual(validated_lights(None), [])
        self.assertEqual(validated_lights({}), [])

    def test_nonfinite_or_degenerate_geometry_and_colour_are_not_emitters(self):
        base = self.row()
        for values in [dict(position=[1, 2]), dict(direction=[0, 0, 0]), dict(direction=[0, 1]),
                dict(position=[0, float('nan'), 0]), dict(position=[10001, 0, 0]),
                dict(direction=[float('inf'), 0, 0]), dict(direction=[1e300, 0, 0]),
                dict(brightness=float('nan')), dict(range=float('inf')), dict(colour='purple'),
                dict(surface=-1), dict(surface=1000001)]:
            with self.subTest(values=values): self.assertEqual(validated_lights([dict(base, **values)]), [])

    def test_normalization_limits_and_paint_preference_are_retained(self):
        row = validated_lights([self.row(colour='#A0B0C0', brightness=10, range=1000, paint=False, surface=17)])[0]
        self.assertEqual(row['direction'], [0, 0, -1])
        self.assertEqual(row['colour'], '#a0b0c0')
        self.assertEqual((row['brightness'], row['range'], row['surface'], row['paint']), (5, 500, 17, False))
        dark = validated_lights([self.row(brightness=-1, range=-1)])[0]
        self.assertEqual((dark['brightness'], dark['range'], dark['paint']), (0, 1, True))
        self.assertEqual(len(validated_lights([self.row()] * 100)), MAX_LIGHTS)

    def test_emitter_offset_points_outward_and_rgb_scales_without_mutating_settings(self):
        row = validated_lights([self.row(colour='#ff0080', brightness=2, range=45)])[0]
        position, direction, rgb, reach = light_values(row)
        self.assertEqual(position, (1, 2, 2.85))
        self.assertEqual(direction, (0, 0, -1))
        self.assertEqual(rgb, (2, 0, 256 / 255))
        self.assertEqual(reach, 45)
        self.assertEqual(row['position'], [1, 2, 3])


if __name__ == '__main__': unittest.main()
