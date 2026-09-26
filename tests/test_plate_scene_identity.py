"""Pure scene-key tests: changing one identity term invalidates old work."""

import unittest

from plugins.PlateSceneIdentity import (
    NavigationSceneKey, navigation_hard_key, navigation_zoom,
)


def scene(**changes):
    args = dict(surface="popover", job_epoch=2, payload_ids=(11, 12, 13),
                split=20, show_previous=True, show_next=True,
                show_base=True, show_travels=False, line_scale=0.7,
                width=400, height=300, bed_width=250.0, bed_depth=250.0,
                plot=(("sx", 2.0),), dpr=2.0, zoom=1.0)
    args.update(changes)
    return NavigationSceneKey(**args)


class NavigationSceneKeyTests(unittest.TestCase):
    def test_named_identity_retains_the_existing_tuple_contract(self):
        key = scene()
        self.assertEqual(len(key), 16)
        self.assertEqual(key[3], key.split)
        self.assertEqual(key[-1], key.zoom)
        self.assertEqual(navigation_zoom(key), 1.0)

    def test_numeric_split_drift_preserves_the_static_scene(self):
        self.assertEqual(navigation_hard_key(scene(split=20)),
                         navigation_hard_key(scene(split=60)))
        self.assertNotEqual(scene(split=20), scene(split=60))

    def test_missing_split_is_not_a_partial_print(self):
        self.assertNotEqual(navigation_hard_key(scene(split=None)),
                            navigation_hard_key(scene(split=0)))
        self.assertEqual(navigation_hard_key(scene(split=None)),
                         navigation_hard_key(scene(split=None)))

    def test_context_changes_invalidate_the_scene(self):
        original = navigation_hard_key(scene())
        for variant in (dict(job_epoch=3), dict(payload_ids=(11, 14, 13)),
                        dict(show_travels=True), dict(width=420),
                        dict(zoom=1.5), dict(dpr=1.0),
                        dict(plot=(("sx", 3.0),))):
            self.assertNotEqual(original, navigation_hard_key(scene(**variant)))

    def test_short_synthetic_tickets_remain_entirely_opaque(self):
        self.assertEqual(navigation_hard_key(("a", "b")), ("a", "b"))
        self.assertEqual(navigation_zoom(("a", "b")), ("a", "b"))
        self.assertIsNone(navigation_hard_key(None))
        self.assertIsNone(navigation_zoom(None))


if __name__ == "__main__":
    unittest.main()
