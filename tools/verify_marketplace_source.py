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
    from build_curapackage import verify_archive
    verify_archive(
        path,
        label="Cura Marketplace source layout",
        expected_entries=expected_archive_entries(package_id),
        archive_names=lambda source: archive_name(source, package_id),
        package_meta_path=f"{package_id}/plugin.json",
        license_name=f"{package_id}/LICENSE",
        changelog_name=f"{package_id}/CHANGELOG.md",
        max_bytes=MAX_MARKETPLACE_BYTES,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Cura Marketplace source ZIP")
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    verify(args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
