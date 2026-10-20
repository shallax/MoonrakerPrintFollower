# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Wait for Cura to restore the exact seeded machine.

"""Startup selection for the fixture machine, without creating replacements."""

SEED_NAME = "FDM Printer Base Description"


def activate_seed(app, registry):
    if not getattr(app, "started", False):
        return {"ok": False, "created": False, "error": "Cura is still initializing"}
    manager = app.getMachineManager()
    for stack in registry.findContainerStacks():
        if stack.getMetaDataEntry("type") == "machine" and stack.getName() == SEED_NAME:
            if manager.activeMachine is None or manager.activeMachine.getId() != stack.getId():
                manager.setActiveMachine(stack.getId())
            active = manager.activeMachine.getName() if manager.activeMachine else None
            return {"ok": active == SEED_NAME, "active": active, "created": False}
    return {"ok": False, "created": False, "error": "seeded machine has not been restored"}
