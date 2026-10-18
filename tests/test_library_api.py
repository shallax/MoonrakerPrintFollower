# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Exercise public components in an isolated stdlib-only consumer.

"""The documented core API must remain independent of optional runtimes."""
from pathlib import Path
import tempfile
import unittest
from tools.check_library import CORE_PROBE, ROOT, SAMPLE, run_probe


class LibraryApiTests(unittest.TestCase):
    def test_core_pipeline_needs_neither_site_packages_nor_cura(self):
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "part.gcode"
            sample.write_bytes(SAMPLE)
            run_probe(CORE_PROBE, ROOT, sample, pure=True, cwd=directory)
