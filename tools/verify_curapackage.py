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
    version = str(package["package_version"])
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("curapackage contains duplicate archive entries")
        actual = set(names)
        expected = expected_archive_entries(package_id)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            raise RuntimeError(f"curapackage file set mismatch; missing={missing}, unexpected={unexpected}")

        embedded_package = json.loads(archive.read("package.json").decode("utf-8"))
        if embedded_package != package:
            raise RuntimeError("embedded package.json differs from source")
        if archive.read("LICENSE") != LICENSE_FILE.read_bytes():
            raise RuntimeError("embedded LICENSE differs from source")
        if archive.read("CHANGELOG.md") != CHANGELOG_FILE.read_bytes():
            raise RuntimeError("embedded CHANGELOG.md differs from source")
        if f"## {version}" not in CHANGELOG_FILE.read_text(encoding="utf-8"):
            raise RuntimeError(f"CHANGELOG.md does not contain a {version} release section")

        plugin_meta_path = f"files/plugins/{package_id}/plugin.json"
        plugin_meta = json.loads(archive.read(plugin_meta_path).decode("utf-8"))
        if str(plugin_meta.get("version")) != version:
            raise RuntimeError("plugin.json version does not match package version")

        for source in iter_plugin_sources():
            name = archive_name(source, package_id)
            if archive.read(name) != source.read_bytes():
                raise RuntimeError(f"packaged bytes differ from source: {source.relative_to(PLUGIN_ROOT)}")

        forbidden = [
            name for name in names
            if "__pycache__" in name
            or name.lower().endswith(".curapackage")
            or name.endswith((".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"))
            or "/." in name
        ]
        if forbidden:
            raise RuntimeError(f"curapackage contains forbidden build debris: {forbidden}")
    print(f"Verified exact source/package parity for {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Moonraker Print Follower curapackage")
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    verify(args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
