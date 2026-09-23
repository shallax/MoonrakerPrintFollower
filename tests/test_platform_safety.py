"""The cross-platform gate (the directive: the plugin MUST
run on Windows, macOS and Linux). The O_NOFOLLOW lesson — one
POSIX-only constant silently broke every state write on Windows —
becomes a permanent source sweep: each banned token must carry its
platform guard in the same file, so the class cannot be
reintroduced.

The second half (RC-01) is behavioural: the boot sweep's owner
liveness read. On Windows `os.kill(pid, 0)` does not probe, it
TERMINATES — a concurrent Cura instance or a recycled pid would be
killed outright — so the sweep's verdicts are pinned here against the
native probe, with the signal path mocked out."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"

# token -> the guard that must appear in the same file when the token
# does (a file that never uses the token needs no guard).
GUARDED = {
    "os.O_NOFOLLOW": 'hasattr(os, "O_NOFOLLOW")',
    "os.O_CLOEXEC": 'hasattr(os, "O_CLOEXEC")',
    "os.O_NONBLOCK": 'hasattr(os, "O_NONBLOCK")',
    "os.O_DIRECTORY": 'hasattr(os, "O_DIRECTORY")',
    "/proc/": "try:\n        with open",
    "import resource": "except ImportError",
    "from resource import": "except ImportError",
    "ctypes.windll": "sys.platform == \"win32\"",
    # A liveness probe via the POSIX signal idiom is a TERMINATION on
    # Windows: every file that signals a pid must branch to the native
    # process API first.
    "os.kill": "sys.platform == \"win32\"",
}

try:  # the sweep's module is Qt-bound; the stdlib suite runs without it
    from plugins import RemoteFileService as sweep_module
except Exception:  # noqa: BLE001 — any import failure means no Qt runtime here
    sweep_module = None

# tokens banned outright: Unix-only facilities with no portable
# equivalent that the plugin may use.
BANNED = (
    "os.fork", "os.geteuid", "os.setuid", "os.chown", "os.symlink",
    "AF_UNIX", "shell=True", "import pwd", "import grp", "import fcntl",
    "import termios",
)


class PlatformSafetyTests(unittest.TestCase):
    def test_every_posix_token_carries_its_guard(self):
        failures = []
        for path in sorted(PLUGINS.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token, guard in GUARDED.items():
                if token in source and guard not in source:
                    failures.append(f"{path.name}: {token} used without its guard")
        self.assertEqual(failures, [])

    def test_no_unix_only_facilities(self):
        failures = []
        for path in sorted(PLUGINS.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token in BANNED:
                if token in source:
                    failures.append(f"{path.name}: {token}")
        self.assertEqual(failures, [])

    def test_every_pid_signal_carries_its_platform_guard(self):
        # The sweep's own guard, named separately so a failure reads as
        # the Windows kill rather than as a generic token sweep.
        failures = []
        for path in sorted(PLUGINS.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "os.kill" in source and 'sys.platform == "win32"' not in source:
                failures.append("{}: os.kill without the Windows branch".format(path.name))
        self.assertEqual(failures, [])

    def test_binary_paths_open_in_binary_mode(self):
        # Downloads and UFP/gcode leases must never pass through text
        # mode (Windows newline translation corrupts bytes); the gcode
        # text path pins newline="" to keep the bytes identical.
        failures = []
        gcode = (PLUGINS / "CuraOutputWriter.py").read_text(encoding="utf-8")
        if '"newline": ""' not in gcode:
            failures.append("CuraOutputWriter: the gcode text open lost its newline='' pin")
        download = (PLUGINS / "DownloadStream.py").read_text(encoding="utf-8")
        if 'open(path, "wb")' not in download:
            failures.append("DownloadStream: the download target is not binary")
        self.assertEqual(failures, [])


@unittest.skipUnless(sweep_module is not None, "PyQt6 required for the sweep module")
class TempRootSweepTests(unittest.TestCase):
    """The boot sweep's owner-liveness verdict (RC-01).

    On Windows `os.kill(pid, 0)` calls TerminateProcess: it does not
    ask whether the owner is alive, it kills it. Sweeping another
    `mpf-*` root whose pid belongs to a second live Cura instance — or
    to a recycled pid — therefore killed that instance at plugin load.
    The sweep must read the native process API there and never signal.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mpfsweep-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.current = os.path.join(self.root, "mpf-files-{}-current".format(os.getpid()))

    def _root(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        with open(os.path.join(path, "part.gcode"), "w", encoding="utf-8") as handle:
            handle.write("G1 X0\n")
        return path

    def test_the_windows_sweep_never_signals_a_live_owner(self):
        # The reproduction: a second live Cura instance's root sits
        # beside this session's. Every os.kill here is modelled with
        # the Windows semantics — it succeeds AND the process is gone —
        # so a sweep that probes by signal is a sweep that kills.
        second_pid = 4242
        live = self._root("mpf-files-{}-live".format(second_pid))
        stale = self._root("mpf-files-99999-dead")
        killed = []

        def windows_kill(pid, signal):
            killed.append((pid, signal))

        with patch.object(sys, "platform", "win32"), \
                patch.object(os, "kill", windows_kill), \
                patch.object(sweep_module, "_windows_liveness",
                             side_effect=lambda pid: pid == second_pid):
            sweep_module._sweep_stale_roots(self.root, self.current)

        self.assertEqual(killed, [], "the sweep signalled a pid on Windows")
        self.assertTrue(os.path.isdir(live), "a live second instance lost its temp root")
        self.assertFalse(os.path.exists(stale), "an abandoned root survived the sweep")

    def test_the_windows_probe_verdicts_are_conservative(self):
        # The probe's own failure semantics, mirrored from the
        # PreparedStore probe (the architecture rule keeps this module
        # to its two imports, so the probe is duplicated): only a
        # provably dead owner releases a root; access-denied, an
        # unknown native failure and an unreadable exit code all keep
        # it. The Win32 calls are mocked — no host process is touched.
        import ctypes

        def verdict(open_handle, open_error, exit_ok, exit_code):
            closed = []

            def fake_open(*_args):
                return open_handle

            def fake_exit(handle, ref):
                if ref is not None and hasattr(ref, "_obj"):
                    ref._obj.value = exit_code
                return exit_ok

            def fake_close(handle):
                closed.append(handle)

            class FakeDll:
                # Instance attributes keep the functions PLAIN: a class
                # body would bind them, and bound methods refuse the
                # argtypes/restype assignments the wrapper makes.
                def __init__(self):
                    self.OpenProcess = fake_open
                    self.GetExitCodeProcess = fake_exit
                    self.CloseHandle = fake_close

            with patch.object(ctypes, "WinDLL", return_value=FakeDll(), create=True), \
                    patch.object(ctypes, "get_last_error", return_value=open_error, create=True):
                return sweep_module._windows_liveness(1234), closed

        self.assertFalse(verdict(0, 87, True, 259)[0], "no-such-process read alive")
        self.assertTrue(verdict(0, 5, True, 259)[0], "access-denied read dead")
        self.assertTrue(verdict(0, 999, True, 259)[0], "an unknown native error read dead")
        self.assertTrue(verdict(0x1234, None, False, 259)[0], "an exit-code read failure read dead")
        self.assertTrue(verdict(0x1234, None, True, 259)[0], "a running owner read dead")
        self.assertFalse(verdict(0x1234, None, True, 42)[0], "an exited owner read alive")
        self.assertEqual(verdict(0x1234, None, True, 42)[1], [0x1234], "the handle never closed")

    def test_the_posix_liveness_verdicts_are_conservative(self):
        # The POSIX branch, pinned explicitly rather than on the host's
        # own platform: ALIVE and PROVABLY DEAD pass through, while
        # access-denied and any unexplained failure keep the root —
        # a pid that cannot be disproved is not swept.
        def alive(side_effect, name="mpf-files-4242-live"):
            path = self._root(name)
            with patch.object(sys, "platform", "linux"), \
                    patch.object(os, "kill", side_effect=side_effect):
                sweep_module._sweep_stale_roots(self.root, self.current)
            return os.path.isdir(path)

        self.assertTrue(alive(None), "a live owner's root was swept")
        self.assertFalse(alive(ProcessLookupError(), "mpf-files-5555-dead"),
                         "a dead owner's root survived")
        self.assertTrue(alive(PermissionError(), "mpf-files-6666-denied"),
                        "an access-denied owner's root was swept")
        self.assertTrue(alive(OSError("unexplained"), "mpf-files-7777-odd"),
                        "an indeterminate probe swept a root")

    def test_an_impossible_pid_never_reaches_the_signal(self):
        # A name stamping pid 0 or a pid beyond the platform's range is
        # not an owner: signalling 0 would reach the whole process
        # group, and a truncated 64-bit pid can name an unrelated
        # process. Both read dead without a probe.
        zero = self._root("mpf-files-0-nobody")
        huge = self._root("mpf-files-4294967296-nobody")
        signalled = []
        with patch.object(sys, "platform", "linux"), \
                patch.object(os, "kill",
                             side_effect=lambda pid, signal: signalled.append(pid)):
            sweep_module._sweep_stale_roots(self.root, self.current)
        self.assertEqual(signalled, [], "an impossible pid was signalled")
        self.assertFalse(os.path.exists(zero), "a pid-less root survived the sweep")
        self.assertFalse(os.path.exists(huge), "an out-of-range pid's root survived")
