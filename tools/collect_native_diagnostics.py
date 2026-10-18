# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Preserve native crash reports and logs after interrupted UI runs.

"""Collect native CI evidence independently of the scenario runner's finally."""
import argparse
import os
from pathlib import Path
import shutil


def collect(work, config, reports, output):
    output.mkdir(parents=True, exist_ok=True)
    sources = [(work / name, output / name) for name in
               ("cura_run.log", "native_harness.log", "simulator.log")]
    if config is not None and config.is_dir():
        sources.extend((path, output / "cura" / path.name) for path in config.glob("cura.log*"))
    # Frozen macOS Cura redirects Python streams before enabling its fatal
    # traceback handler, so launcher stdout alone can miss the useful stack.
    if reports.parent.is_dir():
        sources.extend((directory / name, output / "python" / directory.name / name)
                       for directory in reports.parent.iterdir()
                       if directory.is_dir() and "cura" in directory.name.casefold()
                       for name in ("stdout.log", "stderr.log"))
    if reports.is_dir():
        sources.extend((path, output / "crashes" / path.relative_to(reports))
                       for path in reports.rglob("*")
                       if "cura" in path.name.casefold() and path.suffix in (".ips", ".crash"))
    copied = []
    for source, destination in sources:
        if not source.is_file():
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(destination)
        except OSError as exc:
            print(f"native diagnostics: could not preserve {source}: {exc}")
    print(f"native diagnostics: preserved {len(copied)} file(s) in {output}")
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = os.environ.get("HARNESS_CURA_CONFIG")
    collect(Path(os.environ["MPF_WORK_DIR"]), Path(config) if config else None,
            Path.home() / "Library" / "Logs" / "DiagnosticReports", args.output)


if __name__ == "__main__":
    main()
