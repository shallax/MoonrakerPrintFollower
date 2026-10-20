# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own workflow state independently of the Monitor property surface.

"""Follower attachment and manual selection with explicit demand capabilities."""

def coerce_anchor(value):
    """A valid integer ZERO is a layer (the P0 zero-index bug: the
    old `value or -1` read the first layer as missing and refused the
    detach/scrub on it). Only None and unparseable values mean
    missing."""
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1



class FollowerSelection:
    def __init__(self, *, anchor, split):
        self._anchor = anchor
        self._split = split
        self.attached = True
        self.anchor = -1
        self.split = None
        self.job = None

    def observe_job(self, job):
        if job == self.job:
            return
        self.job = job
        if not self.attached:
            self.set_attached(True)

    def set_attached(self, attached, *, live_anchor=-1, presented=None,
                     layer_count=0, index_ready=False, live_split=None):
        attached = bool(attached)
        if attached == self.attached:
            return False
        if attached:
            self.attached = True
            self.anchor, self.split = -1, None
            self._anchor(None)
            self._split(None)
        else:
            frozen = self.anchor
            if frozen < 0:
                frozen = coerce_anchor(live_anchor)
            if frozen < 0 and presented is not None:
                frozen = coerce_anchor(presented)
            if frozen < 0 and (layer_count > 0 or index_ready):
                frozen = 0
            if frozen < 0:
                return False
            self.attached = False
            self.anchor = frozen
            self.split = int(live_split) if live_split is not None and frozen == live_anchor else None
            self._anchor(frozen)
            self._split(self.split)
        return True

    def seek(self, layer):
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return False
        if layer < 0:
            return False
        self.attached = False
        if self.anchor != layer or self.split != -1:
            self.anchor, self.split = layer, -1
            self._anchor(layer)
            self._split(-1)
        return True

    def scrub(self, motions, *, total, live_anchor):
        try:
            motions = int(motions)
        except (TypeError, ValueError):
            return False
        motions = max(0, min(motions, int(total or 0)))
        if self.attached:
            frozen = coerce_anchor(live_anchor)
            if frozen < 0:
                return False
            self.attached = False
            self.anchor = frozen
            self._anchor(frozen)
        if self.split != motions:
            self.split = motions
            self._split(motions)
        return True
