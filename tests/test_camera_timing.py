"""The T0-T9 camera-timing chain's contract: trace-gated marks,
mark_once deduplication per origin, and the begin() reset.

The 2026-09-19 live-run ruling: the periodic 30 s discovery poll must
not re-mark the chain for the session's lifetime — T3/T4 answer the
COLD start, so they are once per trace origin like the T9 first-frame
stamp. The module works with or without Uranium (Logger/Resources
fall back to None), so these tests assert the observable bookkeeping —
the armed flag, the origin and the once-set — never the log sink.
"""
from __future__ import annotations

import unittest

from plugins import CameraTiming


class CameraTimingContractTests(unittest.TestCase):

    def setUp(self):
        CameraTiming.begin(False)

    def tearDown(self):
        CameraTiming.begin(False)

    def test_marks_are_silent_until_armed(self):
        CameraTiming.mark("T0", "monitor active")
        CameraTiming.mark_once("T3", "webcam list requested")
        self.assertEqual(CameraTiming._once_marked, set())
        self.assertFalse(CameraTiming.enabled())

    def test_mark_once_fires_once_per_origin(self):
        CameraTiming.begin(True)
        CameraTiming.mark_once("T3", "webcam list requested")
        CameraTiming.mark_once("T3", "webcam list requested")
        CameraTiming.mark_once("T4", "webcam list landed")
        self.assertEqual(CameraTiming._once_marked, {"T3", "T4"})

    def test_begin_clears_the_once_set_for_a_fresh_origin(self):
        # A re-armed trace must re-mark T3/T4: the dedup window is
        # one origin, not the process lifetime.
        CameraTiming.begin(True)
        CameraTiming.mark_once("T3", "webcam list requested")
        CameraTiming.begin(True)
        self.assertEqual(CameraTiming._once_marked, set())

    def test_a_disabled_mark_once_does_not_consume_the_stage(self):
        # Disabled calls return before the dedup set: arming later
        # still marks the stage once.
        CameraTiming.mark_once("T3", "webcam list requested")
        CameraTiming.begin(True)
        CameraTiming.mark_once("T3", "webcam list requested")
        CameraTiming.mark_once("T3", "webcam list requested")
        self.assertEqual(CameraTiming._once_marked, {"T3"})

    def test_next_actor_id_is_monotonic(self):
        # The diagnostic labels' sequence: strictly increasing, so two
        # pane instances can never share a label.
        first = CameraTiming.next_actor_id()
        second = CameraTiming.next_actor_id()
        self.assertEqual(second, first + 1)

    def test_append_survives_a_write_failure(self):
        # The sink's write must never take the chain down: an
        # unwritable location falls into the except and the mark
        # proceeds silently.
        import importlib
        import os
        import sys
        import tempfile
        import types

        config_dir = tempfile.mkdtemp(prefix="mpf-timing-")
        um = types.ModuleType("UM")
        um.__path__ = []
        resources = types.ModuleType("UM.Resources")
        class Resources:
            @staticmethod
            def getConfigStoragePath():
                # A directory that does not exist: the open() fails.
                return os.path.join(config_dir, "missing", "nested")
        resources.Resources = Resources
        logger = types.ModuleType("UM.Logger")
        class Logger:
            @staticmethod
            def log(level, message, *args):
                pass
        logger.Logger = Logger
        sys.modules["UM"] = um
        sys.modules["UM.Resources"] = resources
        sys.modules["UM.Logger"] = logger
        try:
            import plugins.CameraTiming as timing
            timing = importlib.reload(timing)
            timing.begin(True)
            timing.mark("T3", "webcam list requested")  # must not raise
        finally:
            for name in ("UM", "UM.Resources", "UM.Logger"):
                sys.modules.pop(name, None)
            import plugins.CameraTiming as restored
            importlib.reload(restored)

    def test_the_sinks_write_when_uranium_exists(self):
        # The dedicated file sink and the Logger branches need
        # Uranium, absent from the test environment: stub UM, reload
        # the module, and assert the observable bookkeeping — the
        # log file's lines and the Logger calls.
        import importlib
        import os
        import sys
        import tempfile
        import types

        config_dir = tempfile.mkdtemp(prefix="mpf-timing-")
        log_calls = []

        um = types.ModuleType("UM")
        um.__path__ = []
        resources = types.ModuleType("UM.Resources")
        class Resources:
            @staticmethod
            def getConfigStoragePath():
                return config_dir
        resources.Resources = Resources
        logger = types.ModuleType("UM.Logger")
        class Logger:
            @staticmethod
            def log(level, message, *args):
                log_calls.append((level, message % args if args else message))
        logger.Logger = Logger
        sys.modules["UM"] = um
        sys.modules["UM.Resources"] = resources
        sys.modules["UM.Logger"] = logger
        try:
            import plugins.CameraTiming as timing
            timing = importlib.reload(timing)
            timing.begin(True)
            timing.mark("T3", "webcam list requested")
            timing.mark_once("T4", "webcam list landed")
            self.assertTrue(log_calls, "the Logger branch never ran")
            with open(os.path.join(config_dir, "moonraker-camera-timing.log"), encoding="utf-8") as handle:
                contents = handle.read()
            self.assertIn("camera timing T3", contents)
            self.assertIn("camera timing T4", contents)
            self.assertIn("camera timing T0-arm", contents)
        finally:
            for name in ("UM", "UM.Resources", "UM.Logger"):
                sys.modules.pop(name, None)
            import plugins.CameraTiming as restored
            importlib.reload(restored)
