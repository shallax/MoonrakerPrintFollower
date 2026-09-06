from __future__ import annotations

import argparse
import json
import pathlib
import zipfile
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins"
PACKAGE_JSON = ROOT / "package.json"
LICENSE_FILE = ROOT / "LICENSE"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

FORBIDDEN_SUFFIXES = {".curapackage", ".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"}
FORBIDDEN_NAMES = {".DS_Store"}

# Fixed ZIP metadata makes release archives byte-for-byte reproducible from the
# same source tree. Stored entries also avoid depending on the host zlib build.
DETERMINISTIC_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DETERMINISTIC_FILE_MODE = 0o100644


def is_packaged_source(path: pathlib.Path) -> bool:
    relative = path.relative_to(PLUGIN_ROOT)
    if not path.is_file():
        return False
    if "__pycache__" in relative.parts:
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    return True


def iter_plugin_sources() -> Iterable[pathlib.Path]:
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if is_packaged_source(path):
            yield path


def archive_name(path: pathlib.Path, package_id: str) -> str:
    relative = path.relative_to(PLUGIN_ROOT).as_posix()
    return f"files/plugins/{package_id}/{relative}"


def expected_archive_entries(package_id: str) -> set[str]:
    return {"package.json", "LICENSE", "CHANGELOG.md"} | {
        archive_name(path, package_id) for path in iter_plugin_sources()
    }


def write_deterministic_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
    info.create_system = 3
    info.external_attr = DETERMINISTIC_FILE_MODE << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, data)


def write_deterministic_file(archive: zipfile.ZipFile, source: pathlib.Path, name: str) -> None:
    write_deterministic_bytes(archive, name, source.read_bytes())


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}.curapackage"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w") as archive:
        write_deterministic_file(archive, PACKAGE_JSON, "package.json")
        write_deterministic_file(archive, LICENSE_FILE, "LICENSE")
        write_deterministic_file(archive, CHANGELOG_FILE, "CHANGELOG.md")
        for path in iter_plugin_sources():
            write_deterministic_file(archive, path, archive_name(path, package_id))

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Moonraker Print Follower Cura package")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
