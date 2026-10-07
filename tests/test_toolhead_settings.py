"""Settings draft cancellation and capture of the production model preview."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from PyQt6.QtCore import QCoreApplication
    from mpf.toolhead.ToolheadModels import ToolheadModels
    from mpf.toolhead.ToolheadAssetStore import ToolheadAssetStore
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 is required")
class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.identity = "first"
        self.config = SimpleNamespace(toolhead_model="", toolhead_model_name="", toolhead_tip=[])
        self.model = ToolheadModels(ToolheadAssetStore(self.directory.name), self.directory.name,
                                    lambda: self.config, lambda: self.identity)
        self.addCleanup(self.model.close)

    def test_cancel_restores_saved_anchor_and_default_model(self):
        self.model.setTip(0, "12.5")
        self.assertTrue(self.model.manual)
        self.model.reset()
        self.assertFalse(self.model.manual)
        self.assertEqual(self.model.fields()["toolhead_tip"], [])
        self.assertEqual(self.model.tip, self.model.mesh.automatic_tip)

    def test_printer_switch_cannot_save_previous_draft(self):
        self.model.setTip(1, "4")
        self.identity = "second"
        with self.assertRaises(ValueError):
            self.model.fields()
        self.model.reset()
        self.assertEqual(self.model.fields()["toolhead_tip"], [])

    def test_invalid_tip_prevents_save_until_automatic_reset(self):
        self.model.setTip(2, "nan")
        self.assertFalse(self.model.valid)
        with self.assertRaises(ValueError):
            self.model.fields()
        self.model.automatic()
        self.assertTrue(self.model.valid)


class CaptureSurfaceTests(unittest.TestCase):
    def test_capture_uses_production_preview_and_real_draft(self):
        source = (Path(__file__).resolve().parents[1] / "tools" / "capture_settings.py").read_text()
        self.assertIn("qmlRegisterType(ToolheadModelPreview", source)
        self.assertIn("self._toolhead = ToolheadModels", source)
