# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Project canonical recovery actions into confirmation text and diagrams.

"""Pure recovery confirmation projection over reviewed source/printer values."""
from dataclasses import dataclass
from .RecoveryActions import RecoveryRoute, recovery_route
from ..geometry.PhysicalMotion import finite


def action_document(action):
    def n(value): return f'{value:.2f}'
    if action.kind == 'move':
        if action.axes == ('Z',):
            direction = 'Raise' if action.position[2] > action.start[2] else 'Lower'
            text = f'{direction} to Z{n(action.position[2])} at {n(action.speed)} mm/s (about {n(action.duration)} s).'
        else:
            text = f'Move to X{n(action.position[0])} Y{n(action.position[1])} at Z{n(action.position[2])} ({n(action.speed)} mm/s).'
    elif action.kind == 'purge':
        text = f'Purge {n(action.amount)} mm of filament at {n(action.speed)} mm/s.'
    else:
        text = f'Dwell for {n(action.seconds)} s.'
    return {'kind': action.kind, 'from': list(action.start), 'position': list(action.position),
            'text': text, 'seconds': action.duration, 'command': action.command}



@dataclass(frozen=True)
class RecoveryReview:
    route: RecoveryRoute
    actions: tuple
    nozzle: float
    bed: float
    reference: float

    def document(self):
        return {'actions': [dict(action) for action in self.actions],
                'primeZ': self.route.prime_target[2] if self.route.prime_target else None}


def review_recovery(plan, supplied):
    if plan.get('ready') is not True:
        raise ValueError('Check the G-code before reviewing recovery.')
    nozzle = finite(plan.get('hotendTarget') if plan.get('hotendTarget') is not None else supplied.get('hotend'))
    bed = finite(plan.get('bedTarget') if plan.get('bedTarget') is not None else supplied.get('bed'))
    reference = finite(supplied.get('zReference', 0))
    target = tuple(finite(plan[axis]) for axis in ('x', 'y', 'z'))
    if nozzle <= 0 or bed < 0:
        raise ValueError('Enter valid nozzle and bed targets.')
    if reference < 0 or reference > target[2] or reference > 0 and not plan.get('unloaded'):
        raise ValueError('Enter a Z reference between 0 and the selected restart Z.')
    current = tuple(finite(plan['current'+axis]) for axis in ('X', 'Y', 'Z'))
    current = (*current[:2], current[2] + reference)
    route = recovery_route(current, target, plan.get('maximumZ'),
        clearance=plan.get('clearance', 5), xy_speed=plan.get('xySpeed', 50),
        z_speed=plan.get('zSpeed', 5),
        prime_target=tuple(plan['prime'+axis] for axis in ('X', 'Y', 'Z')) if plan.get('primeEnabled') else None,
        prime_amount=plan.get('primeAmount', 50), prime_speed=plan.get('primeSpeed', 5),
        prime_dwell=plan.get('primeDwell', 5))
    actions = []
    point = current
    def note(text):
        actions.append({'kind': 'check', 'text': text, 'from': list(point), 'position': list(point)})
    if plan.get('unloaded'):
        note('Select the file without running its print-start macro.')
    note(f'Heat and wait for nozzle {nozzle:.2f} °C' +
         (f' and bed {bed:.2f} °C.' if bed > 0 else '; turn the bed off.') + ' Heating time varies.')
    if reference > 0:
        note(f'Adjust the Z reference without moving: file Z{target[2]:.2f} corresponds to printer Z{target[2]-reference:.2f}.')
    elif not plan.get('unloaded'):
        note('Restore the saved print coordinates without moving.')
    for action in (*route.prime, *route.returning):
        if action.kind == 'dwell':
            actions[-1]['text'] = actions[-1]['text'].rstrip('.') + f'; then dwell for {action.seconds:.2f} s.'
            actions[-1]['commands'] = [actions[-1]['command'], 'M400', action.command]
            actions[-1]['seconds'] += action.seconds
            continue
        actions.append(action_document(action))
        point = action.position
    note('Check the file position and restore extrusion, fan and motion settings while above the print.')
    actions.append(action_document(route.descent))
    point = route.descent.position
    layer, move, offset = plan['resumeLayer'], plan['resumeMove'], plan['offset']
    note(f'Immediately resume printing on layer {layer} at move {move} ({move-1} completed moves on that layer; byte {offset}).')
    return RecoveryReview(route, tuple(actions), nozzle, bed, reference)
