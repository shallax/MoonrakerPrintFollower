# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Verify operation ownership is invalidated before cancellation.
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-07 [e2b963e] Complete service migration and reconcile architecture

import pathlib
import unittest
from tests.source_root import SourceRoot

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = SourceRoot(ROOT / "mpf")
PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
LIFECYCLE = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
BASE = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")


class OutputRebindLifecycleTests(unittest.TestCase):
    def test_output_device_is_deactivated_before_losing_active_ownership(self):
        self.assertIn("def _deactivate_device", PLUGIN)
        self.assertIn("sessionInvalidated.connect(self._invalidate_devices)", PLUGIN)
        self.assertIn("self._set_monitor_active(device, False)", PLUGIN)
        self.assertIn("deactivate()", PLUGIN)
        self.assertGreaterEqual(PLUGIN.count("self._deactivate_device(self._current)"), 3)

    def test_deactivation_cancels_transport_and_streaming_upload_work(self):
        cleanup = LIFECYCLE[LIFECYCLE.index("def _finish"):]
        self.assertIn("transport.cancel_owner(self.owner)", cleanup)
        self.assertLess(cleanup.index("operation, self._upload_operation = self._upload_operation, None"), cleanup.index("operation.cancel(notify=False)"))
        self.assertIn("self._generation += 1", cleanup)
        self.assertIn("QTimer.singleShot(0, terminal)", cleanup)
        self.assertIn("self._upload.terminal_delivered()", BASE)


if __name__ == "__main__":
    unittest.main()
