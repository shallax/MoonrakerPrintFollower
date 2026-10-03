"""The shared pixel settle, tested without a Qt install.

The settle runs inside the capture legs (the dev container), but its
contract is pure Python over an application-like pumper and a
grabber — so the rules that decide whether a capture is taken at all
are pinned here on the host: the span, the frame count, the pending
veto, and the refusal when the scene never goes quiet.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import capture_settle  # noqa: E402


class FakeApp:
    def __init__(self):
        self.pumps = 0

    def processEvents(self):
        self.pumps += 1


class FakeScene:
    """Frames from a script; the last one repeats forever."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


class CaptureSettleTests(unittest.TestCase):
    def test_a_moving_scene_never_settles_and_says_so(self):
        app = FakeApp()
        scene = FakeScene(["a", "b", "c"])
        with self.assertRaisesRegex(RuntimeError, "never settled"):
            capture_settle.settle(app, scene.grab, label="moving",
                                  span_seconds=0.05, timeout_ms=120)

    def test_the_first_frame_that_proves_the_run_is_returned(self):
        app = FakeApp()
        # Three frames that differ, then a run of identical ones: the
        # proof is a 3-frame run spanning the settle span, and the frame
        # returned is the one that proved it — never a later grab.
        scene = FakeScene(["a", "b", "c", "d", "d", "d", "d", "e"])
        image = capture_settle.settle(app, scene.grab, label="proving",
                                      span_seconds=0.0, timeout_ms=2000)
        self.assertEqual(image, "d")
        self.assertLessEqual(scene.grabs, 6, "the settle grabbed past its proof")

    def test_a_pending_one_shot_refuses_a_settled_frame(self):
        app = FakeApp()
        scene = FakeScene(["a", "a", "a", "a", "a"])
        state = {"pending": True}

        def pending():
            return state["pending"]

        class Flip:
            """The scene's own one-shot clears after a few pumps."""

            def __init__(self, inner):
                self.inner = inner
                self.calls = 0

            def __call__(self):
                self.calls += 1
                if self.calls > 3:
                    state["pending"] = False
                return self.inner()

        image = capture_settle.settle(app, Flip(scene.grab), label="pending",
                                      span_seconds=0.0, timeout_ms=2000, pending=pending)
        self.assertEqual(image, "a")
        self.assertFalse(state["pending"])

    def test_a_head_start_pumps_before_the_first_grab(self):
        app = FakeApp()
        scene = FakeScene(["only"])
        capture_settle.settle(app, scene.grab, label="head-start",
                              head_start_seconds=0.02, span_seconds=0.0, timeout_ms=2000)
        self.assertTrue(app.pumps > 0)


if __name__ == "__main__":
    unittest.main()
