# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Prove recovery diagram positions agree with serialized printer travel.

"""Confirmation and execution share the same route, independently replayed as G-code."""
from dataclasses import replace
import re
import unittest

from mpf.printing.RecoveryActions import RecoveryAction, recovery_route, same_actions
from mpf.printing.RecoveryHandover import prepare_return
from mpf.printing.RecoveryReview import review_recovery, action_document
from tests.test_recovery_handover import source, status


class RecoveryActionsTests(unittest.TestCase):
    def test_reviewed_moves_match_replayed_commands_across_prime_and_travel_branches(self):
        for idle in (False, True):
            for restart in (2, 248):
                for prime_z in (None, 1, 3, 100, 280):
                    for parked_z in (0, 50, 250):
                        with self.subTest(idle=idle, restart=restart, prime_z=prime_z, parked_z=parked_z):
                            fresh = status()
                            fresh['gcode_move']['gcode_position'][:3] = [10, 20, parked_z]
                            fresh['gcode_move']['position'][:3] = [10, 20, parked_z]
                            fresh['toolhead']['position'][:3] = [10, 20, parked_z]
                            if idle:
                                fresh['print_stats']['state'] = 'standby'
                                fresh['pause_resume']['is_paused'] = False
                            chosen = replace(source(), state=replace(source().state, position=(40, 50, restart)))
                            options = dict(prime_enabled=prime_z is not None,
                                prime_xyz=(0, 200, prime_z or 1), prime_amount_mm=20,
                                prime_speed_mm_s=2, prime_dwell_s=12.5)
                            plan = prepare_return(fresh, chosen, filename='part.gcode',
                                printed_top_z=restart, idle=idle, **options)
                            values = dict(ready=True, unloaded=idle, x=40, y=50, z=restart,
                                currentX=10, currentY=20, currentZ=parked_z, maximumZ=250,
                                hotendTarget=210, bedTarget=60, primeEnabled=options['prime_enabled'],
                                primeX=0, primeY=200, primeZ=prime_z or 1, primeAmount=20,
                                primeSpeed=2, primeDwell=12.5, resumeLayer=8, resumeMove=4, offset=300)
                            review = review_recovery(values, {})
                            self.assertEqual(review.route, plan.route)
                            expected = [action for action in review.actions if action['kind'] == 'move']
                            point = [10, 20, parked_z]
                            replayed = []
                            for command in (plan.prime_script+'\n'+plan.return_script+'\n'+plan.resume_script).splitlines():
                                if not command.startswith('G1 '): continue
                                axes = dict((axis, float(value)) for axis, value in
                                    re.findall(r'([XYZ])(-?[0-9.]+)', command))
                                if not axes: continue
                                before = list(point)
                                for axis, value in axes.items(): point['XYZ'.index(axis)] = value
                                replayed.append((before, list(point)))
                            self.assertEqual(replayed, [(row['from'], row['position']) for row in expected])
                            self.assertNotIn('G4 ', plan.resume_script)
                            self.assertTrue(all(row['position'][2] <= 250 for row in expected))

    def test_unloaded_reference_and_offset_preview_use_the_same_file_frame(self):
        fresh = status()
        fresh['print_stats']['state'] = 'standby'
        fresh['pause_resume']['is_paused'] = False
        fresh['gcode_move'].update(position=[100, 100, 10], gcode_position=[90, 80, 8, 0])
        plan = prepare_return(fresh, source(), filename='part.gcode', printed_top_z=8,
                              idle=True, z_reference_mm=2)
        review = review_recovery(dict(ready=True, unloaded=True, x=40, y=50, z=2,
            currentX=90, currentY=80, currentZ=10, maximumZ=250,
            hotendTarget=210, bedTarget=60, resumeLayer=1, resumeMove=2, offset=300), {'zReference':2})
        self.assertEqual(review.route, plan.route)
        self.assertIn('G92 Z12', plan.z_reference_script)

    def test_prime_step_duration_includes_dwell_and_actions_keep_their_command_values(self):
        values = dict(ready=True, unloaded=True, x=40, y=50, z=2,
            currentX=0, currentY=0, currentZ=10, maximumZ=250,
            hotendTarget=210, bedTarget=60, resumeLayer=1, resumeMove=2, offset=300,
            primeEnabled=True, primeX=0, primeY=200, primeZ=100,
            primeAmount=20, primeSpeed=2, primeDwell=12.5)
        review = review_recovery(values, {})
        prime = next(row for row in review.actions if row['kind'] == 'purge')
        self.assertEqual(prime['seconds'], 22.5)
        self.assertEqual(prime['commands'], ['G1 E20 F120', 'M400', 'G4 P12500'])
        dwell = action_document(review.route.prime[-1])
        self.assertEqual(dwell['seconds'], 12.5)
        self.assertEqual(dwell['text'], 'Dwell for 12.50 s.')

    def test_invalid_review_inputs_never_produce_a_route(self):
        values = dict(ready=True, unloaded=True, x=40, y=50, z=2,
            currentX=0, currentY=0, currentZ=10, maximumZ=250,
            hotendTarget=210, bedTarget=60, resumeLayer=1, resumeMove=2, offset=300)
        for changes, supplied in (({'ready':False}, {}), ({'maximumZ':None}, {}),
                                  ({'hotendTarget':0}, {}), ({'bedTarget':-1}, {}),
                                  ({}, {'zReference':3}), ({'unloaded':False}, {'zReference':1})):
            with self.subTest(changes=changes, supplied=supplied), self.assertRaises(ValueError):
                review_recovery(dict(values, **changes), supplied)
        for options in ({'clearance':0}, {'prime_target':(0,0)},
                        {'prime_target':(0,0,100), 'prime_dwell':301}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                recovery_route((0,0,10), (1,2,3), 250, **options)

    def test_action_admission_checks_start_position_and_command_not_only_target(self):
        route = recovery_route((0,0,10), (40,50,2), 250)
        self.assertTrue(same_actions(route.returning, route.returning))
        self.assertFalse(same_actions(route.returning, ()))
        for changed in (replace(route.returning[0], start=(5,0,10)),
                        replace(route.returning[0], speed=60)):
            self.assertFalse(same_actions(route.returning, (changed, route.returning[1])))
        self.assertEqual(RecoveryAction('purge', (0,0,0), (0,0,0), speed=2, amount=20).duration, 10)
        with self.assertRaises(ValueError):
            _ = RecoveryAction('invalid', (0,0,0), (0,0,0)).command
