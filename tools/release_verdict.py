# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0
# 2026-10-04 [e67283c] Require release gate success before publication

"""Fail a tag release if any required reusable-CI job was skipped or failed."""
from __future__ import annotations

import os


REQUIRED = (
    ("package", "MPF_RELEASE_PACKAGE_RESULT"),
    ("artifact scan", "MPF_RELEASE_SCAN_RESULT"),
    ("Cura gate", "MPF_RELEASE_GATE_RESULT"),
    ("CAD import", "MPF_RELEASE_CAD_RESULT"),
)


def main() -> int:
    results = [(name, os.environ.get(variable, "")) for name, variable in REQUIRED]
    incomplete = [(name, result or "missing") for name, result in results
                  if result != "success"]
    if incomplete:
        details = ", ".join(f"{name}={result}" for name, result in incomplete)
        print(f"::error::Release checks incomplete: {details}")
        return 1
    print("Release package, artifact scan, Cura gate and CAD import all succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
