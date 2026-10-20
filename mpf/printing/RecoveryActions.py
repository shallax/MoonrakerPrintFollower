# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Share typed recovery actions between commands and review diagrams.

"""Immutable recovery travel actions; no transport, Qt or printer-state ownership."""
from dataclasses import dataclass
import math
from ..geometry.PhysicalMotion import command_number, finite


@dataclass(frozen=True)
class RecoveryAction:
    kind: str
    start: tuple
    position: tuple
    axes: tuple = ()
    speed: float = 0.0
    amount: float = 0.0
    seconds: float = 0.0

    @property
    def command(self):
        if self.kind == 'move':
            words = ' '.join(f'{axis}{command_number(self.position["XYZ".index(axis)])}' for axis in self.axes)
            return f'G1 {words} F{command_number(self.speed * 60)}'
        if self.kind == 'purge':
            return f'G1 E{command_number(self.amount)} F{command_number(self.speed * 60)}'
        if self.kind == 'dwell':
            return f'G4 P{command_number(self.seconds * 1000)}'
        raise ValueError('Unknown recovery action')

    @property
    def duration(self):
        if self.kind == 'move':
            return math.dist(self.start, self.position) / self.speed
        return self.amount / self.speed if self.kind == 'purge' else self.seconds



@dataclass(frozen=True)
class RecoveryRoute:
    clearance_z: float
    prime_target: tuple | None
    prime: tuple[RecoveryAction, ...]
    returning: tuple[RecoveryAction, ...]
    descent: RecoveryAction


def recovery_route(current, target, maximum_z, *, clearance=5.0, xy_speed=50.0,
                   z_speed=5.0, prime_target=None, prime_amount=50.0,
                   prime_speed=5.0, prime_dwell=5.0):
    current = tuple(finite(v) for v in current)
    target = tuple(finite(v) for v in target)
    maximum_z = finite(maximum_z)
    clearance, xy_speed, z_speed = map(finite, (clearance, xy_speed, z_speed))
    if len(current) != 3 or len(target) != 3 or min(clearance, xy_speed, z_speed) <= 0:
        raise ValueError('Invalid recovery travel inputs')
    clear_z = min(target[2] + clearance, maximum_z)
    prime = []
    point = current
    def move(destination, axes, speed, output):
        nonlocal point
        output.append(RecoveryAction('move', point, destination, axes, speed))
        point = destination
    if prime_target is not None:
        prime_target = tuple(finite(v) for v in prime_target)
        if len(prime_target) != 3: raise ValueError('Prime XYZ must contain three coordinates')
        prime_z = prime_target[2] if prime_target[2] > target[2] else target[2] + 5.0
        prime_target = (*prime_target[:2], min(prime_z, maximum_z))
        prime_clear = min(max(point[2], target[2], prime_target[2]), maximum_z)
        move((*point[:2], prime_clear), ('Z',), z_speed * 2, prime)
        move((*prime_target[:2], prime_clear), ('X', 'Y'), xy_speed, prime)
        move(prime_target, ('Z',), z_speed, prime)
        amount, speed, dwell = map(finite, (prime_amount, prime_speed, prime_dwell))
        if amount <= 0 or speed <= 0 or not 0 <= dwell <= 300:
            raise ValueError('Invalid recovery priming inputs')
        prime.extend((RecoveryAction('purge', point, point, speed=speed, amount=amount),
                      RecoveryAction('dwell', point, point, seconds=dwell)))
    returning = []
    if point[2] >= clear_z:
        move((*target[:2], point[2]), ('X', 'Y'), xy_speed, returning)
        move((*target[:2], clear_z), ('Z',), z_speed * 2, returning)
    else:
        move((*point[:2], clear_z), ('Z',), z_speed * 2, returning)
        move((*target[:2], clear_z), ('X', 'Y'), xy_speed, returning)
    descent = RecoveryAction('move', point, target, ('Z',), z_speed)
    return RecoveryRoute(clear_z, prime_target, tuple(prime), tuple(returning), descent)


def same_actions(left, right, tolerance=0.05):
    if len(left) != len(right): return False
    for a, b in zip(left, right, strict=True):
        if (a.kind != b.kind or a.axes != b.axes or a.command != b.command
                or any(abs(x-y) > tolerance for x, y in zip(a.start + a.position, b.start + b.position, strict=True))):
            return False
    return True
