# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-08 [7341f82] Development tooling: pinned container, quality gates and the Makefile
# 2026-09-07 [d02d883] Fix review findings and finish the 3.2.0 docs/tools pass
# 2026-09-06 [21ffa13] Harden v3.0.0 release candidate

from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

from build_curapackage import (
    PACKAGE_JSON,
    archive_name,
    expected_archive_entries,
)


def verify(path: pathlib.Path) -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    from build_curapackage import verify_archive
    verify_archive(
        path,
        label="exact source/package parity",
        expected_entries=expected_archive_entries(package_id),
        archive_names=lambda source: archive_name(source, package_id),
        package_meta_path=f"files/plugins/{package_id}/plugin.json",
        license_name="LICENSE",
        changelog_name="CHANGELOG.md",
        extra_forbidden=(".curapackage",),
    )
    with zipfile.ZipFile(path, "r") as archive:
        embedded_package = json.loads(archive.read("package.json").decode("utf-8"))
        if embedded_package != package:
            raise RuntimeError("embedded package.json differs from source")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Moonraker Print Follower curapackage")
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    verify(args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
