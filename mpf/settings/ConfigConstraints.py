# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Share typed follow choices and settings constraints independently of host UI.

"""Shared numeric limits; strict drafts and tolerant loading retain their own refusal policy."""

RECOVERY_NUMBER_LIMITS = (
    ("recovery_lead_in_mm", 0.1, 50.0),
    ("recovery_clearance_mm", 1.0, 30.0),
    ("recovery_xy_speed_mm_s", 1.0, 200.0),
    ("recovery_z_speed_mm_s", 0.5, 20.0),
    ("recovery_prime_x", 0.0, 1000.0),
    ("recovery_prime_y", 0.0, 1000.0),
    ("recovery_prime_z", 0.0, 1000.0),
    ("recovery_prime_amount_mm", 0.1, 200.0),
    ("recovery_prime_speed_mm_s", 0.1, 20.0),
    ("recovery_prime_dwell_s", 0.0, 300.0),
)
