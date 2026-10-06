"""Core snapshot reuse preserves nested mutation isolation and job boundaries."""
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mpf.monitor.MonitorData import MonitorData, _CoreMemo, _status_copy, freeze


class CoreMemoTests(unittest.TestCase):
    def setUp(self):
        self.memo = _CoreMemo()
        self.status = {'motion_report': {'live_position': [1., 2., 3., 0.]},
            'exclude_object': {'objects': [{'name': 'part', 'center': [1., 2.],
                'polygon': [[0., 0.], [2., 0.], [2., 2.], [0., 2.]]}],
                'current_object': 'part', 'excluded_objects': []},
            'print_stats': {'filename': 'one.gcode', 'state': 'printing'}}

    def test_equal_defensive_copies_reuse_frozen_nested_geometry_without_recursing(self):
        first = self.memo.value(self.status)
        with patch('mpf.monitor.MonitorData.freeze', wraps=freeze) as freezing, \
                patch('mpf.monitor.MonitorData._status_copy', wraps=_status_copy) as copying:
            for _ in range(100):
                current = self.memo.value(deepcopy(self.status))
                self.assertIs(current['exclude_object'], first['exclude_object'])
                self.assertIs(current['motion_report'], first['motion_report'])
            freezing.assert_not_called()
            copying.assert_not_called()
        self.assertEqual(dict(current), dict(freeze(self.status)))
        with self.assertRaises(TypeError): current['print_stats'] = {}
        with self.assertRaises(TypeError): current['exclude_object']['objects'][0]['name'] = 'other'

    def test_live_position_changes_only_refreeze_motion_not_unchanged_large_polygons(self):
        first = self.memo.value(self.status)
        self.status['motion_report']['live_position'][0] = 19.
        current = self.memo.value(self.status)
        self.assertIs(current['exclude_object'], first['exclude_object'])
        self.assertIs(current['print_stats'], first['print_stats'])
        self.assertIsNot(current['motion_report'], first['motion_report'])
        self.assertEqual(current['motion_report']['live_position'], (19., 2., 3., 0.))
        self.assertEqual(first['motion_report']['live_position'][0], 1.)

    def test_in_place_polygon_and_status_mutation_cannot_change_previous_snapshot_or_escape_comparison(self):
        first = self.memo.value(self.status)
        self.status['exclude_object']['objects'][0]['polygon'][1][0] = 7.
        self.status['exclude_object']['excluded_objects'].append('part')
        self.status['print_stats']['filename'] = 'two.gcode'
        current = self.memo.value(self.status)
        self.assertIsNot(current['exclude_object'], first['exclude_object'])
        self.assertIs(current['motion_report'], first['motion_report'])
        self.assertEqual(first['exclude_object']['objects'][0]['polygon'][1], (2., 0.))
        self.assertEqual(current['exclude_object']['objects'][0]['polygon'][1], (7., 0.))
        self.assertEqual(first['exclude_object']['excluded_objects'], ())
        self.assertEqual(first['print_stats']['filename'], 'one.gcode')
        self.status['exclude_object']['objects'][0]['polygon'][1][0] = 8.
        self.assertEqual(current['exclude_object']['objects'][0]['polygon'][1], (7., 0.))

    def test_departed_objects_are_pruned_and_reappearing_same_names_use_new_geometry(self):
        first = self.memo.value(self.status)
        current = self.memo.value({'print_stats': self.status['print_stats']})
        self.assertEqual(set(self.memo._rows), {'print_stats'})
        self.assertNotIn('exclude_object', current)
        self.status['exclude_object']['objects'][0]['name'] = 'new part'
        restored = self.memo.value(self.status)
        self.assertIsNot(restored['exclude_object'], first['exclude_object'])
        self.assertEqual(restored['exclude_object']['objects'][0]['name'], 'new part')

    def test_lists_tuples_and_mapping_inputs_keep_private_comparison_shapes(self):
        value = {'row': MappingProxyType({'points': ([1, 2], (3, 4))})}
        first = self.memo.value(value)
        second = self.memo.value(value)
        self.assertIs(first['row'], second['row'])
        value['row']['points'][0][0] = 9
        third = self.memo.value(value)
        self.assertEqual(first['row']['points'][0], (1, 2))
        self.assertEqual(third['row']['points'][0], (9, 2))
        self.assertIsInstance(self.memo._rows['row'][0]['points'], tuple)
        self.assertIsInstance(self.memo._rows['row'][0]['points'][0], list)

    def test_observe_keeps_notifications_and_filtering_while_reusing_equal_frozen_rows(self):
        snapshots = []
        owner = SimpleNamespace(_active=True, _core_memo=self.memo, _intervals=Mock(),
            _update=lambda **patch: snapshots.append(freeze(patch['core'])))
        MonitorData.observe(owner, dict(self.status, malformed=42))
        MonitorData.observe(owner, deepcopy(self.status))
        self.assertEqual(len(snapshots), 2)
        self.assertNotIn('malformed', snapshots[0])
        self.assertIs(snapshots[0]['exclude_object'], snapshots[1]['exclude_object'])
        owner._intervals.assert_called()
        owner._active = False
        MonitorData.observe(owner, self.status)
        MonitorData.observe(owner, None)
        self.assertEqual(len(snapshots), 2)

    def test_clear_retires_frozen_rows_before_new_printer_snapshot(self):
        first = self.memo.value(self.status)
        owner = SimpleNamespace(_core_memo=self.memo, _observation=None, previewBlockChanged=Mock())
        MonitorData._clear(owner)
        self.assertEqual(dict(owner._snapshot.core), {})
        self.assertEqual(owner._core_memo._rows, {})
        second = owner._core_memo.value(self.status)
        self.assertEqual(dict(first), dict(second))
        self.assertIsNot(first['exclude_object'], second['exclude_object'])
        owner.previewBlockChanged.emit.assert_called_once()
