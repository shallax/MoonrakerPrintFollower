"""The cross-platform gate (the directive: the plugin MUST
run on Windows, macOS and Linux). The O_NOFOLLOW lesson — one
POSIX-only constant silently broke every state write on Windows —
becomes a permanent source sweep: each banned token must carry its
platform guard in the same file, so the class cannot be
reintroduced."""

import unittest
from pathlib import Path

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
}

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
