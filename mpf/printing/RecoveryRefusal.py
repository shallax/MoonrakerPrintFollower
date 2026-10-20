# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate structured recovery refusal evidence from setup navigation.

"""Recovery refusal values; presentation decides how to guide the operator."""


class RecoveryPlanError(ValueError):
    """A refusal with optional machine-readable evidence, never a partial plan."""
    def __init__(self, message, *, code="unsupported_source", commands=()):
        super().__init__(message)
        self.code = code
        self.commands = tuple(commands)
