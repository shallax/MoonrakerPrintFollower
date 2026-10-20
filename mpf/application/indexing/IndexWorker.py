# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history:
# 2026-10-20 [326436d] Speed up Windows indexing and parallel layer preparation.

"""Isolated stdlib-only scanner entry point, independent of Cura's executable."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def main(arguments):
    from ...gcode.GCodeIndex import build_index_from_file
    from ...gcode.IndexCache import PersistentIndexCache
    from ...gcode.SourceIdentity import CacheIdentity

    path, destination, mode = arguments
    if mode == "prepare":
        return prepare(path, destination)
    output = Path(destination)
    last_report = 0.0

    def report(fraction):
        nonlocal last_report
        now = time.monotonic()
        if now - last_report < .25:
            return
        last_report = now
        temporary = output / "progress.tmp"
        temporary.write_text(str(fraction), encoding="ascii")
        os.replace(temporary, output / "progress")

    # A separate interpreter needs no voluntary GIL sleeps: cancellation is
    # owned by the parent, which kills and joins this disposable process.
    # Only boundaries and modal seeds cross the process boundary. Existing
    # demand hydration supplies visible layers; transferring every motion of
    # a full print would make the parent's restore another long GIL hold.
    index = build_index_from_file(path, progress=report, compact=mode == "compact", cooperative=False)
    if index:
        PersistentIndexCache(destination).save(CacheIdentity("worker"), index)
    else:
        (output / "empty").touch()


def prepare(path, destination):
    from ...gcode.IndexCache import PersistentIndexCache
    from ...gcode.IndexHydrator import hydrate_layer_from_file
    from ...gcode.PlateProgress import prepare_layer, encode_layer
    from ...gcode.SourceIdentity import CacheIdentity

    output = Path(destination)
    request = json.loads((output / "request.json").read_text(encoding="utf-8"))
    index = PersistentIndexCache(request["index_root"]).load(CacheIdentity("worker"))
    if index is None:
        raise ValueError("Invalid preparation boundary index")
    layers = request["layers"]
    if (not isinstance(layers, list) or len(layers) > 32 or layers != sorted(set(layers))
            or any(not isinstance(layer, int) or not 0 <= layer < len(index.ranges) for layer in layers)):
        raise ValueError("Invalid preparation layer batch")
    encoded, refused, processed, total = [], [], [], 0
    frontier = request["start"]
    for position, layer in enumerate(layers):
        if not hydrate_layer_from_file(index, path, layer, cooperative=False):
            raise ValueError("Could not hydrate preparation layer")
        payload = prepare_layer(index, layer)
        if payload is not None:
            try:
                raw = encode_layer(payload)
                if len(raw) > 64*1024*1024:
                    raise ValueError("Prepared layer exceeds transfer limit")
            except Exception:
                refused.append(layer)
            else:
                (output / f"layer-{layer}.ppl").write_bytes(raw)
                encoded.append(layer)
                total += len(raw)
        frontier = request["end"] if position == len(layers)-1 else layer+1
        processed.append(layer)
        if total >= 8*1024*1024:
            break
    (output / "result.json").write_text(json.dumps({
        "frontier": frontier, "encoded": encoded, "uncacheable": refused,
        "processed": processed}), encoding="utf-8")


if __name__ == "__main__":
    # Cura installs the package under its plugin ID. Resolve our own sources
    # explicitly under -I, without inheriting Cura, user-site or PYTHONPATH.
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("_mpf_index", root / "__init__.py",
                                                 submodule_search_locations=[str(root)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    try:
        importlib.import_module(".application.indexing.IndexWorker", spec.name).main(sys.argv[1:])
    except Exception as error:
        print(str(error)[:2048], file=sys.stderr)
        sys.exit(1)
