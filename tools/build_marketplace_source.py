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
    ROOT,
    iter_plugin_sources,
    reproducible_zip_timestamp,
    write_deterministic_file,
)


def archive_name(path: pathlib.Path, package_id: str) -> str:
    relative = path.relative_to(PLUGIN_ROOT).as_posix()
    return f"{package_id}/{relative}"


def expected_archive_entries(package_id: str) -> set[str]:
    return {
        f"{package_id}/LICENSE",
        f"{package_id}/CHANGELOG.md",
    } | {archive_name(path, package_id) for path in iter_plugin_sources()}


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}-source.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = reproducible_zip_timestamp()

    with zipfile.ZipFile(output, "w") as archive:
        write_deterministic_file(archive, LICENSE_FILE, f"{package_id}/LICENSE", timestamp)
        write_deterministic_file(archive, CHANGELOG_FILE, f"{package_id}/CHANGELOG.md", timestamp)
        for path in iter_plugin_sources():
            write_deterministic_file(archive, path, archive_name(path, package_id), timestamp)

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Cura Marketplace source ZIP")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
