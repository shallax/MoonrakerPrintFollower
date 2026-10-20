# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Share typed follow choices and settings constraints independently of host UI.

"""Persisted print-follow choices shared by policy and settings."""
from enum import Enum


class FollowMode(str, Enum):
    EXACT = "exact"
    COMPLETED = "completed"
    LOOKAHEAD = "lookahead"
    WINDOW = "window"

