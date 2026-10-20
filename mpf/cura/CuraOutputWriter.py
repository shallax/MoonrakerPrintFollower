# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-22 [7b897c0] 4.6.0: the live-fix wave — the ghost gate, the temp sweep, the resumed-session counts and
#   the 0% ghost
# 2026-09-07 [29b5fc3] Replace transitional runtime layers with composed components

"""Cura-affine file preparation. The output is a lease, not an in-memory buffer."""
from dataclasses import dataclass
import os
import shutil
import tempfile


@dataclass(frozen=True)
class PreparedOutput:
    path: str
    filename: str
    directory: str

    def close(self):
        shutil.rmtree(self.directory, ignore_errors=True)


class CuraOutputWriter:
    def __init__(self, application):
        self._application = application

    def prepare(self, config, filename=None):
        info = self._application.getPrintInformation()
        output_format = "ufp" if config.output_format == "ufp" and info is not None and not info.preSliced else "gcode"
        name = os.path.basename(str(filename or getattr(info, "jobName", "") or "print"))
        for suffix in (".gcode", ".ufp"):
            if name.lower().endswith(suffix): name = name[:-len(suffix)]; break
        source, target = config.filename_translate_input, config.filename_translate_output
        if source and len(source) == len(target):
            name = name.translate(str.maketrans(source, target, config.filename_translate_remove))
        name = (name.strip() or "print") + "." + output_format
        if any(c in name for c in ':*?"<>|\r\n'): raise ValueError("Invalid upload filename")
        directory = tempfile.mkdtemp(prefix="mpf-upload-%d-" % os.getpid())
        path = os.path.join(directory, name)
        try:
            writer = self._application.getPluginRegistry().getPluginObject("UFPWriter" if output_format == "ufp" else "GCodeWriter")
            if writer is None: raise RuntimeError("Cura output writer is unavailable")
            options = {} if output_format == "ufp" else {"encoding": "utf-8", "newline": ""}
            with open(path, "wb" if output_format == "ufp" else "w", **options) as stream:
                if not writer.write(stream, None):
                    raise RuntimeError(writer.getInformation())
            return PreparedOutput(path, name, directory)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

