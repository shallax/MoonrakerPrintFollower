"""Fresh query/command-lane tests with real owners and no network transport."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.qt_runtime_support import QT_AVAILABLE, runtime
from tests.test_physical_motion import state

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal
    from mpf.monitor.MonitorPermissions import Observation
    from mpf.monitor.toolhead.ToolheadController import ToolheadController
    from mpf.monitor.controls.MonitorCommands import MonitorCommands

    class Data(QObject):
        changed = pyqtSignal()
        invalidated = pyqtSignal()
        commandChanged = pyqtSignal(object)
        active = connected = True

        def __init__(self):
            super().__init__()
            self.firmware = state(position=(100, 100, .03), base=(0, 0, .03), origin=(0, 0, .03))
            self.busy, self.locked = False, False
            self.requests = []
            self.delayed = []
            self.poll()

        def poll(self):
            self.snapshot = SimpleNamespace(core={**self.firmware, 'motion_report': {
                'live_position': list(self.firmware['toolhead']['position'])}},
                auxiliary={k: self.firmware[k] for k in ('toolhead', 'configfile')}, objects=())
            self.changed.emit()

        @property
        def observation(self):
            return Observation(active=self.active, connection='yes' if self.connected else 'no',
                               state=self.firmware['print_stats']['state'], homed_axes='xyz',
                               assumed_stopped=False, save_config_pending=False,
                               controls_locked=self.locked, busy=self.busy)

        def set_commands_busy(self, value): self.busy = value
        def set_toolhead_guard(self, value): pass
        def request(self, channel, method, path, callback, **kwargs):
            self.requests.append(SimpleNamespace(channel=channel, path=path, callback=callback, **kwargs))
            return True
        def later(self, delay, callback): self.delayed.append(callback)
        def refresh_all(self): pass


@unittest.skipUnless(QT_AVAILABLE, 'Qt runtime required')
class GuardedDispatchTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.data = Data()
        self.commands = MonitorCommands(self.data)
        self.controller = ToolheadController(self.data, self.commands)
        self.addCleanup(self.controller.close)
        self.addCleanup(self.commands.reset)

    def query(self, status=None, error=None):
        request = self.data.requests[-1]
        self.assertEqual(request.path, 'printer/objects/query')
        request.callback({'result': {'status': deepcopy(status or self.data.firmware)}}, error)

    def scripts(self):
        return [r.body['script'] for r in self.data.requests if r.path == 'printer/gcode/script']

    def ack(self, error=None):
        request = self.data.requests[-1]
        self.assertEqual(request.path, 'printer/gcode/script')
        request.callback({} if error else {'result': 'ok'}, error)

    def test_no_mutation_before_query_and_fresh_state_rechecks_after_each_ack(self):
        self.controller.jog('z', -1, .01)
        self.controller.z_offset(-.01)
        self.assertEqual(self.scripts(), [])
        self.query()
        self.assertEqual(len(self.scripts()), 1)
        # The printer's planned endpoint after the jog, independent of the
        # unchanged screen poll. The offset shares this same motion queue.
        self.data.firmware['toolhead']['position'][2] = .02
        self.data.firmware['gcode_move']['position'][2] = .02
        self.data.firmware['gcode_move']['gcode_position'][2] = -.01
        self.ack()
        self.assertEqual(len(self.scripts()), 2)
        self.assertEqual(self.scripts()[-1], 'SET_GCODE_OFFSET Z_ADJUST=-0.01 MOVE=1')

    def test_repeated_offsets_remain_operator_controlled_across_zero(self):
        self.data.firmware = state(position=(100, 100, .01), base=(0, 0, .01), origin=(0, 0, .01))
        self.data.poll()
        for _ in range(4):
            self.controller.z_offset(-.01)
            self.ack()
        self.assertEqual(self.scripts(), ['SET_GCODE_OFFSET Z_ADJUST=-0.01 MOVE=1'] * 4)
        self.assertFalse(any(r.path == 'printer/objects/query' for r in self.data.requests))
        self.assertEqual(self.controller._pending, ())

    def test_limit_or_origin_changes_between_click_and_query_refuse_the_move(self):
        self.controller.move_to('150', '', '')
        changed = state()
        changed['gcode_move']['gcode_position'][0] = 0  # G92 changed X base to +100
        self.query(changed)
        self.assertEqual(self.scripts(), [])
        self.assertIn('travel', self.controller.values['jogStatus'])

    def test_printing_allows_safe_offset_and_refuses_jog_without_auto_pause_from_preview(self):
        self.data.firmware = state(position=(100, 100, 15), base=(0, 0, .05), origin=(0, 0, .05))
        self.data.firmware['print_stats']['state'] = 'printing'
        self.data.poll()
        self.controller.z_offset(-.005)
        self.assertIn('Z_ADJUST=-0.005', self.scripts()[-1])
        self.assertFalse(any(r.path == 'printer/print/pause' for r in self.data.requests))

    def test_printing_offsets_ignore_mesh_floor_height_and_homing_telemetry(self):
        self.data.firmware = state(position=(100, 100, 200), base=(0, 0, .05), origin=(0, 0, .05),
                                   mesh=[[-.1, .3], [.2, 0]])
        self.data.firmware['print_stats']['state'] = 'printing'
        self.data.firmware['toolhead']['homed_axes'] = ''
        self.data.firmware['configfile']['config']['bed_mesh'].update(fade_start=1, fade_end=1.4)
        self.data.poll()
        for amount in (.01, -.01, None):
            self.controller.z_offset(amount)
            self.ack()
        self.assertEqual(self.scripts(), ['SET_GCODE_OFFSET Z_ADJUST=+0.01 MOVE=1',
                                         'SET_GCODE_OFFSET Z_ADJUST=-0.01 MOVE=1',
                                         'SET_GCODE_OFFSET Z=0 MOVE=1'])
        self.assertFalse(any(r.path in ('printer/objects/query', 'printer/print/pause')
                             for r in self.data.requests))

    def test_lock_disconnect_and_idle_only_state_changes_drop_pending_intent(self):
        for change in ('lock', 'disconnect', 'printing'):
            with self.subTest(change=change):
                self.controller._reset()
                self.data.locked = False
                self.data.connected = True
                self.data.firmware['print_stats']['state'] = 'standby'
                self.controller.home('z', idle_only=True)
                if change == 'lock': self.data.locked = True
                elif change == 'disconnect': self.data.connected = False
                else: self.data.firmware['print_stats']['state'] = 'printing'
                self.query()
                self.assertEqual(self.scripts(), [])

    def test_watchdog_and_session_reset_reject_late_query_replies(self):
        self.controller.move_to('150', '', '')
        callback = self.data.requests[-1].callback
        self.controller._query_expired()
        callback({'result': {'status': state()}}, None)
        self.assertEqual(self.scripts(), [])
        self.controller.move_to('120', '', '')
        callback = self.data.requests[-1].callback
        self.data.invalidated.emit()
        callback({'result': {'status': state()}}, None)
        self.assertEqual(self.scripts(), [])

    def test_other_command_round_trip_invalidates_a_delayed_query_even_when_idle_again(self):
        self.controller.move_to('150', '', '')
        callback = self.data.requests[-1].callback
        self.commands.send('External setup', 'printer/gcode/script', {'script': 'G28'})
        self.ack()
        callback({'result': {'status': state()}}, None)
        self.assertEqual(self.scripts(), ['G28'])
        for task in list(self.data.delayed): task()
        self.assertEqual(self.data.requests[-1].path, 'printer/objects/query')
        self.query()
        self.assertIn('G1 X150 F600', self.scripts()[-1])

    def test_bad_input_lock_disconnect_and_unknown_state_still_refuse_offsets(self):
        for amount in (float('nan'), float('inf'), True, 0, 6):
            self.controller.z_offset(amount)
        self.assertEqual(self.data.requests, [])
        for problem in ('lock', 'disconnect', 'unknown'):
            self.data.locked = problem == 'lock'
            self.data.connected = problem != 'disconnect'
            self.data.firmware['print_stats']['state'] = 'starting' if problem == 'unknown' else 'printing'
            self.controller.z_offset(.01)
            self.assertEqual(self.data.requests, [])

    def test_elapsed_deadline_refuses_reply_even_before_timer_event_runs(self):
        with patch('mpf.monitor.toolhead.ToolheadController.time.monotonic', return_value=10):
            self.controller.move_to('150', '', '')
        with patch('mpf.monitor.toolhead.ToolheadController.time.monotonic', return_value=13):
            self.query()
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.controller._pending, ())

    def test_malformed_and_unknown_replies_drop_intent_without_retry(self):
        for key in ('toolhead', 'print_stats', 'configfile'):
            self.controller.move_to('150', '', '')
            bad = state()
            bad[key] = ['bad']
            self.query(bad)
            self.assertEqual(self.scripts(), [])
            self.assertEqual(self.controller._pending, ())
        self.controller.move_to('150', '', '')
        bad = state()
        bad['print_stats']['state'] = 'starting'
        self.query(bad)
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.controller._pending, ())

    def test_offsets_share_the_lane_with_a_guarded_move_and_follow_its_ack(self):
        self.controller.jog('x', 1, 1)
        self.controller.z_offset(.01)
        self.assertEqual(self.scripts(), [])
        self.query()
        self.assertEqual(len(self.scripts()), 1)
        self.assertIn('G1 X1', self.scripts()[0])
        self.ack()
        self.assertEqual(self.scripts()[-1], 'SET_GCODE_OFFSET Z_ADJUST=+0.01 MOVE=1')
        self.assertEqual(sum(r.path == 'printer/objects/query' for r in self.data.requests), 1)

    def test_synchronous_offset_ack_never_duplicates_or_strands_the_queue(self):
        original = self.data.request

        def immediate(channel, method, path, callback, **kwargs):
            accepted = original(channel, method, path, callback, **kwargs)
            if path == 'printer/gcode/script':
                callback({'result': 'ok'}, None)
            return accepted

        self.data.request = immediate
        for amount in (-.01, .01, None):
            self.controller.z_offset(amount)
            self.assertEqual(self.controller._pending, ())
            self.assertFalse(self.controller._querying)
            self.assertFalse(self.commands.busy)
        self.assertEqual(len(self.scripts()), 3)
        self.assertFalse(any(r.path == 'printer/objects/query' for r in self.data.requests))

    def test_offset_queued_behind_a_move_is_dropped_on_owner_revocation(self):
        self.controller.jog('x', 1, 1)
        self.controller.z_offset(-.01)
        callback = self.data.requests[-1].callback
        self.data.invalidated.emit()
        callback({'result': {'status': deepcopy(self.data.firmware)}}, None)
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.controller._pending, ())

    def test_each_configured_calibration_is_idle_only_and_uses_shared_lane(self):
        self.data.firmware['print_stats']['state'] = 'standby'
        for key, script in (('quad_gantry_level', 'QUAD_GANTRY_LEVEL'), ('bed_mesh', 'BED_MESH_CALIBRATE'),
                            ('z_tilt', 'Z_TILT_ADJUST'), ('screws_tilt_adjust', 'SCREWS_TILT_CALCULATE'),
                            ('bed_screws', 'BED_SCREWS_ADJUST'), ('delta_calibrate', 'DELTA_CALIBRATE')):
            self.data.firmware['configfile']['config'][key] = {}
            self.data.poll()
            self.controller.calibration(key)
            self.query()
            self.assertEqual(self.scripts()[-1], script)
            self.ack()
        before = list(self.scripts())
        self.data.firmware['print_stats']['state'] = 'printing'
        self.controller.calibration('bed_mesh')
        self.assertEqual(self.scripts(), before)

    def test_refused_jogs_explain_missing_telemetry_and_each_limit(self):
        self.data.firmware = state(position=(0, 200, 200))
        self.data.poll()
        for axis, direction in (('x', -1), ('y', 1), ('z', 1)):
            self.controller.jog(axis, direction, 1)
            self.assertIn('travel limit', self.controller.values['jogStatus'])
        self.data.snapshot.core['motion_report']['live_position'] = []
        self.controller._reset()
        self.controller.jog('z', -1, 1)
        self.assertIn('unavailable', self.controller.values['jogStatus'])
        self.assertEqual(self.data.requests, [])


if __name__ == '__main__':
    unittest.main()
