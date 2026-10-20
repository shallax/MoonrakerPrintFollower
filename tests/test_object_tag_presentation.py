# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify scene reads and tag projection without Cura or Qt.

from types import SimpleNamespace as Values
import unittest
from mpf.printing.ObjectTagPresentation import SceneTagObject, definition_center, object_tag_rows
from mpf.preview.ObjectTagScene import read_scene_objects


class ObjectTagProjectionTests(unittest.TestCase):
    def project(self, objects=(), definitions=(), metrics=None, **kwargs):
        options = dict(width=200, depth=100, centred=False, current_height=5, current_object='KLIPPER_PART')
        options.update(kwargs)
        return object_tag_rows(objects, definitions, metrics or {}, **options)

    def test_named_scene_matching_and_progress_fallback(self):
        obj = SceneTagObject(42, 'scene name', (0, 0, 0), 10, 0, 10)
        definitions = [dict(name='part', statusName='KLIPPER_PART', center=(100, 50))]
        rows, pickable = self.project([obj], definitions)
        self.assertTrue(pickable)
        self.assertEqual(rows, [dict(name='part', nodeId=42, position=(0, 10, 0), progress=.5, deadline=None)])
        rows, _ = self.project([obj], definitions, {'part': {'progress': .2, 'deadline': 'soon'}})
        self.assertEqual((rows[0]['progress'], rows[0]['deadline']), (.2, 'soon'))
        definitions[0]['excluded'] = True
        rows, _ = self.project([obj], definitions, {'part': {'progress': .2, 'deadline': 'soon'}})
        self.assertEqual((rows[0]['progress'], rows[0]['deadline']), (None, None))
        rows, _ = self.project([obj], [], {'scene name': {'progress': .8}})
        self.assertEqual(rows[0]['name'], 'scene name')
        self.assertEqual(rows[0]['progress'], .8)
        self.assertEqual(self.project([SceneTagObject(42, '', (0, 0, 0), 10, 0, 10)])[0], [])
        rows, _ = self.project([obj], [dict(name='part', center=(0, 0))], centred=True,
                               current_object='part', current_height=100)
        self.assertEqual(rows[0]['progress'], 1)

    def test_gcode_only_anchors_footprints_and_selected_height(self):
        definitions = [dict(name='part', polygon=[(90, 40), (110, 40), (110, 60), (90, 60)])]
        metrics = {'part': dict(progress=.3, deadline='soon', top=3)}
        rows, pickable = self.project(definitions=definitions, metrics=metrics, selected_height=2)
        self.assertFalse(pickable)
        self.assertEqual(rows[0]['position'], (0, 2, 0))
        self.assertEqual(rows[0]['footprint'], [(-10, 10), (10, 10), (10, -10), (-10, -10)])
        self.assertEqual((rows[0]['progress'], rows[0]['deadline']), (.3, 'soon'))
        definitions[0].update(polygon=[], center=(100, 50), bounds=(90, 40, 110, 60), excluded=True)
        rows, _ = self.project(definitions=definitions, metrics=metrics, selected_height=float('nan'))
        self.assertEqual(rows[0]['position'], (0, 3, 0))
        self.assertEqual((rows[0]['progress'], rows[0]['deadline']), (None, None))
        rows, _ = self.project(definitions=definitions, centred=True, current_height=None)
        self.assertEqual(rows[0]['position'], (100, 0, -50))
        self.assertEqual(rows[0]['footprint'][0], (90, -40))
        self.assertIsNone(definition_center({}))
        self.assertEqual(definition_center({'center': (0, 0, 2)}), (0, 0))

    def test_a_malformed_scene_value_does_not_hide_valid_fallback_definitions(self):
        malformed = SceneTagObject(42, 'bad', ('bad', 0, 0), 10, 0, 10)
        rows, pickable = self.project([malformed], [dict(name='fallback', center=(0, 0))])
        self.assertFalse(pickable)
        self.assertEqual(rows[0]['name'], 'fallback')

    def test_scene_adapter_only_reads_eligible_bounds(self):
        box = Values(isValid=lambda: True, center=Values(x=1, y=2, z=3), top=5, bottom=0, height=5)
        def node(**changes):
            fields = dict(isSelectable=lambda: True, isVisible=lambda: True,
                          getMeshData=lambda: object(), getBoundingBox=lambda: box, getName=lambda: 'part')
            fields.update(changes)
            return Values(**fields)
        wanted = node()
        nodes = [wanted, node(isSelectable=lambda: False), node(isVisible=lambda: False),
                 node(getMeshData=lambda: None), node(getBoundingBox=lambda: None),
                 node(getBoundingBox=lambda: Values(isValid=lambda: False)), object()]
        self.assertEqual(read_scene_objects(Values(getAllChildren=lambda: nodes)),
                         [SceneTagObject(id(wanted), 'part', (1, 2, 3), 5, 0, 5)])
