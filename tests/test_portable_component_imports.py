# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Verify independently reusable owners with all host UI imports blocked.

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PortableComponentTests(unittest.TestCase):
    def test_workflow_values_and_job_owners_import_without_qt_cura_or_uranium(self):
        code = '''
import importlib, importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class NoHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'UM','cura','PyQt6'}:
            raise AssertionError('Portable component imported host UI: ' + fullname)
sys.meta_path.insert(0, NoHost())
for name in (
 'application.FollowerSelection', 'application.RecoverySources', 'application.RunoutPause',
 'settings.ConfigValidation', 'settings.ConfigConstraints', 'printing.FollowMode',
 'printing.RecoveryRefusal', 'printing.ObjectTagPresentation', 'preview.ObjectTagScene',
 'detection.DetectionActions', 'detection.DetectionRun', 'toolhead.AppearanceDraft',
 'toolhead.ToolheadImportJobs', 'geometry.BedMeshGeometry', 'moonraker.PrinterObjects'):
    importlib.import_module('mpf.' + name)
assert not any(name.split('.')[0] in {'UM','cura','PyQt6'} for name in sys.modules)
'''
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-I', '-c', code, str(Path(__file__).resolve().parents[1])],
                                    cwd=cwd, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
