from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

from build_curapackage import (
    CHANGELOG_FILE,
    LICENSE_FILE,
    PACKAGE_JSON,
    PLUGIN_ROOT,
    archive_name,
    expected_archive_entries,
    iter_plugin_sources,
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
