from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import zipfile
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins"
PACKAGE_JSON = ROOT / "package.json"
LICENSE_FILE = ROOT / "LICENSE"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

FORBIDDEN_SUFFIXES = {".curapackage", ".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"}
FORBIDDEN_NAMES = {".DS_Store"}

# Release archives use stable metadata so rebuilding the same commit produces
# identical bytes. SOURCE_DATE_EPOCH is preferred; local Git checkouts derive
# it from HEAD automatically. ZIP timestamps have two-second resolution.
DETERMINISTIC_FILE_MODE = 0o100644
ZIP_MIN_YEAR = 1980
ZIP_MAX_YEAR = 2107


def source_date_epoch() -> int:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        try:
            value = subprocess.check_output(
                ["git", "show", "-s", "--format=%ct", "HEAD"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "Cannot determine reproducible archive timestamp; set SOURCE_DATE_EPOCH "
                "or build from a Git checkout."
            ) from exc

    try:
        epoch = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from exc
    if epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must not be negative")
    return epoch


def zip_timestamp_for_epoch(epoch: int) -> tuple[int, int, int, int, int, int]:
    try:
        stamp = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"Invalid archive timestamp epoch: {epoch}") from exc

    if not ZIP_MIN_YEAR <= stamp.year <= ZIP_MAX_YEAR:
        raise ValueError(
            f"Archive timestamp year {stamp.year} is outside ZIP's supported "
            f"range {ZIP_MIN_YEAR}-{ZIP_MAX_YEAR}"
        )

    second = stamp.second - (stamp.second % 2)
    return (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, second)


def reproducible_zip_timestamp() -> tuple[int, int, int, int, int, int]:
    return zip_timestamp_for_epoch(source_date_epoch())


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


def write_deterministic_bytes(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    timestamp: tuple[int, int, int, int, int, int],
) -> None:
    info = zipfile.ZipInfo(name, date_time=timestamp)
    info.create_system = 3
    info.external_attr = DETERMINISTIC_FILE_MODE << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, data)


def write_deterministic_file(
    archive: zipfile.ZipFile,
    source: pathlib.Path,
    name: str,
    timestamp: tuple[int, int, int, int, int, int],
) -> None:
    write_deterministic_bytes(archive, name, source.read_bytes(), timestamp)


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}.curapackage"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = reproducible_zip_timestamp()

    with zipfile.ZipFile(output, "w") as archive:
        write_deterministic_file(archive, PACKAGE_JSON, "package.json", timestamp)
        write_deterministic_file(archive, LICENSE_FILE, "LICENSE", timestamp)
        write_deterministic_file(archive, CHANGELOG_FILE, "CHANGELOG.md", timestamp)
        for path in iter_plugin_sources():
            write_deterministic_file(archive, path, archive_name(path, package_id), timestamp)

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Moonraker Print Follower Cura package")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
