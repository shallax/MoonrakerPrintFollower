# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate structured recovery refusal evidence from setup navigation.

"""Operator guidance for structured recovery refusals."""


def refusal_text(error):
    message = str(error)
    if getattr(error, "code", None) != "unreviewed_commands":
        return message
    return (
        message + " "
        "For Klipper macros, open Settings > Printer > Manage Printers, select this printer, "
        "then Configure Moonraker > Recovery > G-code macros. Review every listed macro's definition: "
        "use 'Setup macro in the G-code' only for a start macro before printed geometry, "
        "or 'Transparent macro in the G-code' only if it leaves print state unchanged. "
        "Choose each macro and click Add. When all are classified, click Save and retry Check G-code. "
        "Leave other commands unlisted; recovery cannot safely skip them."
    )
