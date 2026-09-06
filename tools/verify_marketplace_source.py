from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

from build_curapackage import LICENSE_FILE, PACKAGE_JSON, PLUGIN_ROOT, iter_plugin_sources
from build_marketplace_source import CHANGELOG_FILE, archive_name, expected_archive_entries

MAX_MARKETPLACE_BYTES = 50 * 1024 * 1024


def verify(path: pathlib.Path) -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])

    if path.stat().st_size > MAX_MARKETPLACE_BYTES:
        raise RuntimeError("Marketplace source ZIP exceeds Cura's 50 MB package limit")

    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Marketplace source ZIP contains duplicate archive entries")

        actual = set(names)
        expected = expected_archive_entries(package_id)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            raise RuntimeError(
                f"Marketplace source ZIP file set mismatch; missing={missing}, unexpected={unexpected}"
            )

        prefix = f"{package_id}/"
        if any(not name.startswith(prefix) for name in names):
            raise RuntimeError(f"every Marketplace source entry must live below {prefix}")

        plugin_meta_path = f"{package_id}/plugin.json"
        plugin_meta = json.loads(archive.read(plugin_meta_path).decode("utf-8"))
        if str(plugin_meta.get("version")) != version:
            raise RuntimeError("Marketplace plugin.json version does not match package version")

        if archive.read(f"{package_id}/LICENSE") != LICENSE_FILE.read_bytes():
            raise RuntimeError("Marketplace LICENSE differs from repository LICENSE")
        if archive.read(f"{package_id}/CHANGELOG.md") != CHANGELOG_FILE.read_bytes():
            raise RuntimeError("Marketplace CHANGELOG.md differs from repository changelog")

        changelog_text = CHANGELOG_FILE.read_text(encoding="utf-8")
        if f"## {version}" not in changelog_text:
            raise RuntimeError(f"CHANGELOG.md does not contain a {version} release section")

        for source in iter_plugin_sources():
            name = archive_name(source, package_id)
            if archive.read(name) != source.read_bytes():
                raise RuntimeError(
                    f"Marketplace packaged bytes differ from source: {source.relative_to(PLUGIN_ROOT)}"
                )

        forbidden = [
            name for name in names
            if "__pycache__" in name
            or name.endswith((".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"))
            or "/." in name
        ]
        if forbidden:
            raise RuntimeError(f"Marketplace source ZIP contains forbidden build debris: {forbidden}")

    print(f"Verified Cura Marketplace source layout for {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Cura Marketplace source ZIP")
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    verify(args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
