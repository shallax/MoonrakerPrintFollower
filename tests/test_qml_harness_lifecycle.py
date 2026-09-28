"""QML fixture import must never pre-empt the native application's owner."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests.qt_runtime_support import QT_AVAILABLE


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class HarnessApplicationOwnershipTests(unittest.TestCase):
    def test_importing_every_qml_domain_creates_no_application(self):
        root = Path(__file__).resolve().parents[1]
        driver = """
import importlib
import pathlib
import unittest
from PyQt6.QtCore import QCoreApplication
from tests import qml_engine_support as harness
assert QCoreApplication.instance() is None
count = 0
for path in pathlib.Path('tests').glob('test_qml_*.py'):
    if path.stem == 'test_qml_harness_lifecycle':
        continue
    module = importlib.import_module('tests.' + path.stem)
    count += unittest.defaultTestLoader.loadTestsFromModule(module).countTestCases()
assert count == 208, count
assert QCoreApplication.instance() is None
from tests.test_qml_dashboard_layout import ConsoleInputRowTests
case = ConsoleInputRowTests('test_the_input_keeps_the_buttons_in_their_own_cells')
result = unittest.TextTestRunner().run(unittest.TestSuite([case]))
assert result.testsRun == 1 and not result.skipped and result.wasSuccessful()
assert QCoreApplication.instance() is harness._APPLICATION['app']
"""
        result = subprocess.run([sys.executable, "-c", driver], cwd=root,
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
