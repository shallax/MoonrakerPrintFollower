# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

"""Recovery protocol tests with queued replies and a command-level printer model.

The model interprets G-code axes and modes, not expected recovery scripts.
No transport or physical printer is used.
"""
import copy
from collections import deque
import unittest

from mpf.printing.RecoveryExecution import RecoveryExecution
from tests.test_recovery_handover import source, status


class ProtocolPrinter:
    def __init__(self, *, idle=True, mesh=.18):
        self.state = status()
        self.state['print_stats'].update(state='standby' if idle else 'paused', filename='' if idle else 'part.gcode')
        self.state['pause_resume']['is_paused'] = not idle
        self.mesh = mesh
        self.xyz = [100., 100., 10.]
        self.base = [0., 0., .05]
        self.e = 0.
        self.absolute = True
        self.absolute_e = True
        self.feed = 600.
        self.queue = deque()
        self.scripts = []
        self.trace = []
        self.dwells = []
        self.sync()

    def sync(self):
        move = self.state['gcode_move']
        move.update(position=list(self.xyz),
                    gcode_position=[v-b for v, b in zip(self.xyz, self.base, strict=True)] + [self.e],
                    absolute_coordinates=self.absolute, absolute_extrude=self.absolute_e,
                    speed=self.feed / 60 * move['speed_factor'])
        self.state['toolhead']['position'] = [*self.xyz[:2], self.xyz[2] + self.mesh]

    def query(self, callback):
        self.queue.append(('query', callback, None))
        return True

    def send(self, script, callback):
        self.scripts.append(script)
        self.queue.append(('script', callback, script))
        return True

    def step(self, error=None):
        kind, callback, script = self.queue.popleft()
        if error:
            callback(None, error) if kind == 'query' else callback(error)
            return
        if kind == 'query':
            self.sync()
            callback(copy.deepcopy(self.state), None)
        else:
            for line in script.splitlines():
                self.command(line)
            callback(None)

    def command(self, line):
        words = line.split()
        command = words[0]
        if command == 'M23':
            self.state['print_stats'].update(filename=' '.join(words[1:]), state='standby')
            self.state['virtual_sdcard'].update(file_size=1000, file_position=0)
            return
        params = {w[0]: float(w[1:]) for w in words[1:] if '=' not in w}
        if command == 'G90': self.absolute = True
        elif command == 'G91': self.absolute = False
        elif command == 'M82': self.absolute_e = True
        elif command == 'M83': self.absolute_e = False
        elif command == 'G92':
            for i, axis in enumerate('XYZ'):
                if axis in params: self.base[i] = self.xyz[i] - params[axis]
            if 'E' in params: self.e = params['E']
        elif command == 'G1':
            for i, axis in enumerate('XYZ'):
                if axis in params:
                    self.xyz[i] = params[axis] + self.base[i] if self.absolute else self.xyz[i] + params[axis]
            if 'E' in params: self.e = params['E'] if self.absolute_e else self.e + params['E']
            if 'F' in params: self.feed = params['F']
            self.trace.append(tuple(self.xyz))
        elif command in {'M104', 'M109', 'M140', 'M190'}:
            heater = 'extruder' if command in {'M104', 'M109'} else 'heater_bed'
            self.state[heater]['target'] = params['S']
            if command in {'M109', 'M190'}: self.state[heater]['temperature'] = params['S']
        elif command == 'G4': self.dwells.append((params['P'] / 1000, tuple(self.xyz), self.e))
        elif command == 'M220': self.state['gcode_move']['speed_factor'] = params['S'] / 100
        elif command == 'M221': self.state['gcode_move']['extrude_factor'] = params['S'] / 100
        elif command == 'M204': self.state['toolhead']['max_accel'] = params['S']
        elif command == 'M26': self.state['virtual_sdcard']['file_position'] = int(params['S'])
        elif command == 'CLEAR_PAUSE': self.state['pause_resume']['is_paused'] = False
        elif command == 'M24':
            self.state['print_stats']['state'] = 'printing'
            self.state['virtual_sdcard']['is_active'] = True
        elif command not in {'M400', 'M106', 'M107', 'RESTORE_GCODE_STATE'}:
            raise AssertionError('Unsupported test command: ' + line)
        self.sync()


class RecoveryProtocolTests(unittest.TestCase):
    def attempt(self, printer, **kwargs):
        events = []
        execution = RecoveryExecution(printer.query, printer.send, events.append)
        execution.start(source=source(), filename='part.gcode', printed_top_z=12, **kwargs)
        return execution, events

    def drain(self, printer):
        for _ in range(100):
            if not printer.queue: return
            printer.step()
        self.fail('Recovery did not terminate')

    def test_mesh_and_reference_matrix_reaches_exact_xyz_e_and_file_position(self):
        for mesh in (-.23, 0., .18):
            for reference in (0., 2.):
                for prime in (False, True):
                    with self.subTest(mesh=mesh, reference=reference, prime=prime):
                        printer = ProtocolPrinter(mesh=mesh)
                        execution, events = self.attempt(printer, z_reference_mm=reference,
                            prime_enabled=prime, prime_xyz=(10, 15, 3), prime_amount_mm=20)
                        self.drain(printer)
                        self.assertEqual(execution.phase, 'complete', events[-1])
                        self.assertEqual(printer.state['gcode_move']['gcode_position'], [40., 50., 2., 15.25])
                        self.assertAlmostEqual(printer.xyz[2], 2-reference)
                        self.assertAlmostEqual(printer.state['toolhead']['position'][2], 2-reference+mesh)
                        self.assertEqual(printer.state['virtual_sdcard']['file_position'], 300)
                        self.assertTrue(printer.scripts[-1].endswith('M24'))
                        self.assertTrue(printer.absolute_e)
                        self.assertEqual(printer.feed, 1800)

    def test_dwell_occurs_after_purge_at_prime_position_before_return(self):
        for dwell in (0, 5, 12.5, 300):
            printer = ProtocolPrinter(mesh=0)
            execution, events = self.attempt(printer, prime_enabled=True,
                prime_xyz=(10, 15, 3), prime_amount_mm=20, prime_dwell_s=dwell)
            self.drain(printer)
            self.assertEqual(execution.phase, "complete", events[-1])
            self.assertEqual(printer.dwells, [(dwell, (10., 15., 3.), 20.)])
            self.assertEqual(printer.xyz, [40., 50., 2.])
        printer = ProtocolPrinter()
        self.attempt(printer, prime_enabled=False, prime_dwell_s=300)
        self.drain(printer)
        self.assertEqual(printer.dwells, [])

    def test_return_keeps_purge_height_until_xy_arrives_and_only_lifts_when_needed(self):
        for idle, reference in ((False, 0), (True, 0), (True, 2)):
            for prime_z in (3, 7, 100, 300):
                with self.subTest(idle=idle, reference=reference, prime_z=prime_z):
                    printer = ProtocolPrinter(idle=idle, mesh=.18)
                    execution, events = self.attempt(printer, z_reference_mm=reference,
                        prime_enabled=True, prime_xyz=(10, 15, prime_z))
                    while printer.queue and not printer.dwells:
                        printer.step()
                    self.assertTrue(printer.dwells, events)
                    after_prime = len(printer.trace)
                    self.drain(printer)
                    self.assertEqual(execution.phase, "complete", events[-1])
                    height = printer.dwells[-1][1][2]
                    self.assertLessEqual(height, 250)
                    clearance = 7 + printer.base[2]
                    expected = ([(40, 50, height), (40, 50, clearance)]
                                if height >= clearance else
                                [(10, 15, clearance), (40, 50, clearance)])
                    self.assertEqual(printer.trace[after_prime:after_prime + 2], expected)
                    self.assertEqual(printer.xyz, [40, 50, 2 + printer.base[2]])

    def test_all_network_checks_finish_above_print_before_atomic_descent_and_start(self):
        for idle in (False, True):
            printer = ProtocolPrinter(idle=idle, mesh=0)
            execution, events = self.attempt(printer, prime_enabled=True,
                prime_xyz=(10, 15, 3), prime_dwell_s=5)
            while printer.queue:
                kind, _, script = printer.queue[0]
                if script and script.endswith("M24"):
                    self.assertAlmostEqual(printer.state["gcode_move"]["gcode_position"][2], 7)
                    self.assertIn("G1 Z2 F300", script)
                    self.assertNotIn("G4", script)
                    self.assertNotIn("M400", script)
                printer.step()
                if printer.xyz[2] == 2:
                    self.assertEqual(printer.state["print_stats"]["state"], "printing")
            self.assertEqual(execution.phase, "complete", events[-1])

    def test_every_transport_failure_stops_without_later_commands(self):
        baseline = ProtocolPrinter()
        self.attempt(baseline, prime_enabled=True, prime_xyz=(10, 15, 3))
        count = 0
        while baseline.queue:
            baseline.step()
            count += 1
        for fail_at in range(count):
            with self.subTest(operation=fail_at):
                printer = ProtocolPrinter()
                execution, events = self.attempt(printer, prime_enabled=True, prime_xyz=(10, 15, 3))
                for _ in range(fail_at): printer.step()
                submitted = len(printer.scripts)
                printer.step(error='reply lost')
                self.assertEqual(execution.phase, 'uncertain')
                self.assertFalse(printer.queue)
                self.assertEqual(len(printer.scripts), submitted)
                self.assertEqual(sum(e.terminal for e in events), 1)

    def test_late_status_after_abort_cannot_restart_execution(self):
        printer = ProtocolPrinter()
        execution, events = self.attempt(printer)
        execution.abort_uncertain('disconnected')
        printer.step()
        self.assertEqual(execution.phase, 'uncertain')
        self.assertEqual(printer.scripts, [])
        self.assertEqual(sum(e.terminal for e in events), 1)

    def test_late_command_reply_after_abort_cannot_advance_execution(self):
        printer = ProtocolPrinter()
        execution, events = self.attempt(printer)
        printer.step()  # query -> M23 pending
        execution.abort_uncertain('disconnected')
        printer.step()  # command acted, but its reply arrived after abort
        self.assertEqual(execution.phase, 'uncertain')
        self.assertFalse(printer.queue)
        self.assertEqual(sum(e.terminal for e in events), 1)

    def test_old_attempt_reply_cannot_become_new_attempt_reply(self):
        printer = ProtocolPrinter()
        execution, _ = self.attempt(printer)
        execution.abort_uncertain('disconnected')
        execution.start(source=source(), filename='part.gcode', printed_top_z=12)
        printer.step()  # reply from the abandoned initial query
        self.assertEqual(execution.phase, 'checking')
        self.assertEqual(printer.scripts, [])
        self.drain(printer)
        self.assertEqual(execution.phase, 'complete')

    def test_duplicate_status_reply_does_not_submit_duplicate_file_selection(self):
        printer = ProtocolPrinter()
        execution, _ = self.attempt(printer)
        _, callback, _ = printer.queue[0]
        printer.step()
        callback(copy.deepcopy(printer.state), None)
        self.assertEqual(len(printer.scripts), 1)
        self.drain(printer)
        self.assertEqual(execution.phase, 'complete')

    def test_invalid_final_file_offsets_never_report_success(self):
        for value in (None, "300", float("nan"), float("inf"), True, 1001):
            with self.subTest(position=value):
                printer = ProtocolPrinter()
                execution, _ = self.attempt(printer)
                while execution.phase != 'checking_resume': printer.step()
                printer.state['virtual_sdcard']['file_position'] = value
                printer.step()
                self.assertEqual(execution.phase, 'uncertain')

    def test_cancel_at_each_pending_operation_never_dispatches_another_command(self):
        baseline = ProtocolPrinter()
        self.attempt(baseline)
        count = 0
        while baseline.queue:
            baseline.step()
            count += 1
        for cancel_at in range(count):
            with self.subTest(operation=cancel_at):
                printer = ProtocolPrinter()
                execution, events = self.attempt(printer)
                for _ in range(cancel_at): printer.step()
                execution.cancel()
                submitted = len(printer.scripts)
                printer.step()
                self.assertFalse(printer.queue)
                self.assertEqual(len(printer.scripts), submitted)
                self.assertEqual(execution.phase, 'cancelled')
                self.assertEqual(sum(e.terminal for e in events), 1)

    def test_resume_requires_sd_worker_and_pause_state_to_agree(self):
        for broken in ('sd', 'pause'):
            with self.subTest(broken=broken):
                printer = ProtocolPrinter()
                execution, _ = self.attempt(printer)
                while execution.phase != 'checking_resume': printer.step()
                if broken == 'sd': printer.state['virtual_sdcard']['is_active'] = False
                else: printer.state['pause_resume']['is_paused'] = True
                printer.step()
                self.assertEqual(execution.phase, 'uncertain')

    def test_abort_from_phase_notification_dispatches_no_operation(self):
        for phase in ('checking', 'loading_file'):
            with self.subTest(phase=phase):
                self.assert_abort_from_notification(phase)

    def assert_abort_from_notification(self, phase):
        printer = ProtocolPrinter()
        events = []
        def changed(event):
            events.append(event)
            if event.phase == phase:
                execution.abort_uncertain('connection changed')
        execution = RecoveryExecution(printer.query, printer.send, changed)
        execution.start(source=source(), filename='part.gcode', printed_top_z=12)
        self.drain(printer)
        self.assertEqual(execution.phase, 'uncertain')
        self.assertEqual(printer.scripts, [])
        self.assertEqual(sum(e.terminal for e in events), 1)
