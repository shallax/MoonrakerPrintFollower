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

FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"}
FORBIDDEN_NAMES = {".DS_Store"}


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
    return {"package.json", "LICENSE"} | {archive_name(path, package_id) for path in iter_plugin_sources()}


def build(output: pathlib.Path | None = None) -> pathlib.Path:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package_id = str(package["package_id"])
    version = str(package["package_version"])
    if output is None:
        output = ROOT / "dist" / f"MoonrakerPrintFollower-v{version}.curapackage"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(PACKAGE_JSON, "package.json")
        archive.write(LICENSE_FILE, "LICENSE")
        for path in iter_plugin_sources():
            archive.write(path, archive_name(path, package_id))

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Moonraker Print Follower Cura package")
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
