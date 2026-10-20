# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own consumer residency demands outside parsed motion content.

"""Generation-scoped layer residency shared by independent readers and workers."""
import threading


class IndexResidency:
    def __init__(self):
        self._windows = {}
        self._lock = threading.RLock()

    def set_window(self, owner, anchor, radius=1):
        if anchor is not None and (isinstance(anchor, bool) or not isinstance(anchor, int) or anchor < 0):
            raise ValueError('A residency anchor must be a nonnegative layer index')
        if isinstance(radius, bool) or not isinstance(radius, int) or radius < 0:
            raise ValueError('A residency radius must be a nonnegative integer')
        with self._lock:
            if anchor is None:
                self._windows.pop(owner, None)
            else:
                self._windows[owner] = (anchor, radius)

    def anchor(self, owner):
        with self._lock:
            window = self._windows.get(owner)
            return window[0] if window is not None else None

    def layers(self):
        with self._lock:
            return frozenset(layer for anchor, radius in self._windows.values()
                             for layer in range(max(0, anchor-radius), anchor+radius+1))
