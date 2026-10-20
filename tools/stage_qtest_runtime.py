# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Stage the missing Windows QtTest runtime without replacing Cura libraries.

"""Extract only QtTest's runtime from the exact bundled Qt version's wheel."""
from __future__ import annotations

import pathlib
import sys
import zipfile


def stage_windows_runtime(wheel: pathlib.Path, package_dir: pathlib.Path) -> pathlib.Path:
    destination = package_dir / "Qt6" / "bin" / "Qt6Test.dll"
    if destination.is_file():
        return destination
    # Read the required member before creating anything. Never unpack the
    # entire wheel over Cura's own Qt libraries or trust archive paths.
    with zipfile.ZipFile(wheel) as archive:
        data = archive.read("PyQt6/Qt6/bin/Qt6Test.dll")
    if not data:
        raise ValueError("the QtTest runtime in the wheel is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage_qtest_runtime.py <runtime-wheel> <PyQt6-directory>")
    print(stage_windows_runtime(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])))
