#!/usr/bin/env python3
"""Native Windows and macOS development procedures (no Docker).

Linux runs the procedures as tools/*.sh inside the pinned dev container
(INSTRUCTIONS.md). This driver runs the same Makefile procedures on
Windows and macOS using native host processes. It aligns versions with
the Dockerfile's Ubuntu 26.04 image where native builds are available.
The parity table reports differences: Python 3.14 patch versions and
Homebrew's native hadolint may differ. The shared Qt pins are
PyQt6 6.11.0 on Qt 6.11.2, Qt 6.10.2's qmlformat, ruff 0.16.6,
shellcheck 0.11.0, gitleaks 8.30.1, actionlint 1.7.12
and the fonts-dejavu-core 2.37 faces the offscreen platform renders
with (without a font directory it reports zero families and every text
metric pin drifts). `make dev_install` installs what is missing and
prints the version table.

    python tools/native/dev.py bootstrap    create .venv with the pins
    python tools/native/dev.py lint         compile, QML structure, ruff, format
    python tools/native/dev.py test         the suite, one process per file
    python tools/native/dev.py coverage     the same, measured, with the 95% bars
    python tools/native/dev.py captures     render the capture scenes
    python tools/native/dev.py determinism  two capture runs, byte compared
    python tools/native/dev.py package      build + verify both artifacts
    python tools/native/dev.py snapshot     package, then copy ready to SCP
    python tools/native/dev.py gates        lint + test + captures
    python tools/native/dev.py format       rewrite the plugin QML canonically
    python tools/native/dev.py hooks        install the pre-commit hook
    python tools/native/dev.py clean        build outputs and editor backups

Run it through `make` (which picks .venv up when it exists) or directly
with the venv's interpreter. JOBS overrides the worker count, --jobs the
same per invocation. Exit status is non-zero if any step failed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# --- the toolchain, pinned to the container's -----------------------------
# The Dockerfile pins Ubuntu 26.04 packages. Keep native pins aligned
# where available and report actual differences during bootstrap.
# `make dev_install` fetches anything missing and prints the table.
TOOLCHAIN = {
    "python": "3.14.3",
    "PyQt6": "6.11.0",
    "PyQt6-Qt6": "6.11.2",
    "qmlformat": "6.10.2",
    "ruff": "0.16.6",
    "shellcheck": "0.11.0",
    "hadolint": "2.12.0",
    "gitleaks": "8.30.1",
    "actionlint": "1.7.12",
    "fonts-dejavu-core": "2.37",
}
PYPI_PINS = ("PyQt6==%(PyQt6)s", "PyQt6-Qt6==%(PyQt6-Qt6)s", "ruff==%(ruff)s",
             "coverage")
# PySide6 6.10.2 ships Qt 6.10.2's qmlformat and qsb on native hosts.
QMLFORMAT_PIN = "PySide6-Essentials==%(qmlformat)s"
QSB_PIN = "PySide6-Addons==%(qmlformat)s"
OPTIONAL_PINS = ("numpy",)
PYTHON_INSTALLER = "https://www.python.org/ftp/python/%(v)s/python-%(v)s-amd64.exe"
CLI_TOOLS = {
    "shellcheck": {
        "url": "https://github.com/koalaman/shellcheck/releases/download/v%(v)s/shellcheck-v%(v)s.zip",
        # The zip's member is plain shellcheck.exe; only the zip is
        # version-named.
        "member": "shellcheck.exe",
        "exe": "shellcheck.exe",
    },
    "hadolint": {
        "url": "https://github.com/hadolint/hadolint/releases/download/v%(v)s/hadolint-Windows-x86_64.exe",
        "member": None,
        "exe": "hadolint.exe",
    },
    "gitleaks": {
        "url": "https://github.com/gitleaks/gitleaks/releases/download/v%(v)s/gitleaks_%(v)s_windows_x64.zip",
        "member": "gitleaks.exe",
        "exe": "gitleaks.exe",
    },
    "actionlint": {
        "url": "https://github.com/rhysd/actionlint/releases/download/v%(v)s/actionlint_%(v)s_windows_amd64.zip",
        "member": "actionlint.exe",
        "exe": "actionlint.exe",
    },
}
if IS_MACOS:
    arch = "arm64" if platform.machine() == "arm64" else "amd64"
    shell_arch = "aarch64" if arch == "arm64" else "x86_64"
    CLI_TOOLS = {
        "shellcheck": {
            "url": "https://github.com/koalaman/shellcheck/releases/download/v%(v)s/"
                   "shellcheck-v%(v)s.darwin." + shell_arch + ".tar.gz",
            "member": "shellcheck", "exe": "shellcheck",
        },
        "gitleaks": {
            "url": "https://github.com/gitleaks/gitleaks/releases/download/v%(v)s/"
                   "gitleaks_%(v)s_darwin_" + ("arm64" if arch == "arm64" else "x64") + ".tar.gz",
            "member": "gitleaks", "exe": "gitleaks",
        },
        "actionlint": {
            "url": "https://github.com/rhysd/actionlint/releases/download/v%(v)s/"
                   "actionlint_%(v)s_darwin_" + arch + ".tar.gz",
            "member": "actionlint", "exe": "actionlint",
        },
    }
elif not IS_WINDOWS:
    # The optional desktop Docker backend executes this same driver in
    # the Linux image, whose binaries have no .exe suffix.
    CLI_TOOLS = {name: {"exe": name} for name in CLI_TOOLS}
FONTS_URL = ("https://github.com/dejavu-fonts/dejavu-fonts/releases/download/"
             "version_2_37/dejavu-fonts-ttf-2.37.zip")

HARNESS_MODULES = (
    "tests/harness/test_harness_specs.py",
    "tests/harness/test_harness_runner.py",
    "tests/harness/test_harness_seed.py",
    "tests/harness/test_harness_native.py",
)
DETERMINISM_SCRIPTS = ("capture_monitor.py", "capture_preview.py",
                       "capture_settings.py", "capture_upload.py")
CAPTURE_SCRIPTS = DETERMINISM_SCRIPTS + ("capture_whatsnew.py",
                                         "capture_filemanager.py")
COVERAGE_BAR = 95.0
# The per-file coverage bar is the container's. One file carries reader
# branches that can only RUN on the platform they read — Linux's
# /proc/self/status, macOS's mach task_info and libproc rusage — and the
# suite guards those tests behind the Unix-only `resource` module, so
# this leg measures the file lower than CI does (93.2% against 95.0).
# Only the files named here may be excused, only with the reason printed,
# and only when every other file clears the bar; the container remains
# the authority for the figure itself.
PLATFORM_BOUND_COVERAGE = {
    "mpf/Diagnostics/LeakProbe.py": "its /proc, mach and libproc readers are Unix-only; "
                            "the pinned container is the authority for this bar",
}

RESULTS = []


# --- the shared ground ----------------------------------------------------
def checkout_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "package.json").is_file() and (candidate / "mpf").is_dir():
            return candidate
    raise SystemExit("tools/native/dev.py is not inside the checkout (no package.json above it)")


def default_jobs() -> int:
    """The fan-out width, matching the legs CI runs.

    tools/run_tests.sh defaults to 16 workers, and the suite carries
    wall-clock budgets (test_runtime_monitor_composition' seek matrix holds a
    cold seek under 5 s) that measure the MACHINE, not just the code:
    doubling the width doubles the contention and the budget flakes.
    More cores do not buy a wider fan-out here — JOBS overrides it when
    a run does want it.
    """
    override = os.environ.get("JOBS")
    if override:
        return max(1, int(override))
    return max(1, min(16, os.cpu_count() or 4))


def tool_env() -> dict:
    """The environment every Qt step runs in.

    The offscreen platform is what makes a headless run possible at
    all, and it only finds fonts when QT_QPA_FONTDIR names them: the
    container gets fonts-dejavu-core from apt; native hosts install the
    same faces into .venv/fonts. Controls use Basic for predictable
    offscreen ComboBox rendering on both desktop platforms.
    """
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONFAULTHANDLER", "1")
    if not env.get("QT_QPA_FONTDIR"):
        # The container's fonts-dejavu-core first: the capture and
        # metric pins were taken with those faces, so a box carrying
        # them renders what CI renders. The system fonts are the
        # fallback that keeps a fresh clone usable.
        fallback = (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
                    if IS_WINDOWS else Path("/System/Library/Fonts") if IS_MACOS
                    else Path("/usr/share/fonts/truetype/dejavu"))
        fonts = (pinned_fonts_dir() if IS_WINDOWS or IS_MACOS else None) or fallback
        if fonts.is_dir():
            env["QT_QPA_FONTDIR"] = fonts.as_posix()
    # The pinned tools win over anything else on PATH.
    bins = tool_bin_dir(checkout_root())
    if (IS_WINDOWS or IS_MACOS) and bins.is_dir():
        env["PATH"] = str(bins) + os.pathsep + env.get("PATH", "")
    return env


def scratch_dir() -> Path:
    base = os.environ.get("MPF_SCRATCH")
    path = Path(base) if base else Path(tempfile.gettempdir()) / "mpf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tool_bin_dir(root: Path) -> Path:
    """Where the pinned command line tools land (.venv is gitignored)."""
    return root / ".venv" / "tools" / "bin"


def pinned_tool(name: str):
    """The pinned binary for `name`, falling back to whatever PATH has."""
    if not (IS_WINDOWS or IS_MACOS):
        return shutil.which(name)
    candidate = tool_bin_dir(checkout_root()) / (CLI_TOOLS[name]["exe"] if name in CLI_TOOLS else name)
    if candidate.exists():
        return str(candidate)
    return shutil.which(CLI_TOOLS[name]["exe"] if name in CLI_TOOLS else name)


def pinned_fonts_dir():
    fonts = checkout_root() / ".venv" / "fonts"
    return fonts if any(fonts.glob("*.ttf")) else None


def run(argv, cwd=None, env=None, timeout=None) -> int:
    print("$ " + " ".join(str(a) for a in argv), flush=True)
    try:
        # stdin is /dev/null, never the console: a tool that decides to
        # ask something must fail rather than hang a gate (actionlint's
        # shellcheck pipe is exactly that failure on this platform).
        return subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print("  timed out after %ss: %s" % (timeout, argv[0]), flush=True)
        return 1
    except OSError as error:
        print("  cannot run %s: %s" % (argv[0], error))
        return 1


def capture(argv, cwd=None, env=None):
    try:
        result = subprocess.run(
            [str(a) for a in argv], cwd=cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except OSError as error:
        return 1, str(error)
    return result.returncode, result.stdout


def step(label: str) -> None:
    print("\n== %s ==" % label, flush=True)


def record(label: str, ok: bool, note: str = "") -> int:
    """Print a step's verdict and return it as an exit status."""
    RESULTS.append((label, "OK" if ok else "FAIL", note))
    print("[%s] %s%s" % ("OK" if ok else "FAIL", label, " - " + note if note else ""))
    return 0 if ok else 1


def report(title: str) -> int:
    failed = [entry for entry in RESULTS if entry[1] == "FAIL"]
    print("\n%s: %d step(s), %d failed"
          % (title, len(RESULTS), len(failed)))
    for label, _, note in failed:
        print("  FAILED %s%s" % (label, " - " + note if note else ""))
    return 1 if failed else 0


def ruff_exe():
    beside = Path(sys.executable).parent / ("ruff.exe" if os.name == "nt" else "ruff")
    if beside.exists():
        return str(beside)
    return shutil.which("ruff")


def qmlformat_exe():
    override = os.environ.get("MPF_QMLFORMAT")
    if override:
        return override
    wheel = Path(sysconfig.get_paths()["purelib"]) / "PySide6"
    for name in ("qmlformat.exe", "qmlformat"):
        candidate = wheel / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("qmlformat")


def qml_files(root: Path):
    # Recursive: the documents nest by domain, and a top-level glob
    # would find none of them and format nothing.
    return sorted((root / "mpf").rglob("*.qml"))


def first_difference(current: str, formatted: str) -> str:
    current_lines = current.splitlines()
    formatted_lines = formatted.splitlines()
    for number, (mine, theirs) in enumerate(
            zip(current_lines, formatted_lines, strict=False), 1):
        if mine != theirs:
            return "line %d differs" % number
    if len(current_lines) != len(formatted_lines):
        return "%d lines, qmlformat writes %d" % (len(current_lines), len(formatted_lines))
    return "trailing whitespace or final newline differs"


def formatted_text(root: Path, env: dict, exe: str, path: Path):
    """qmlformat's canonical text, or (None, reason)."""
    rc, out = capture([exe, str(path)], cwd=root, env=env)
    if rc != 0:
        return None, "qmlformat exited %d" % rc
    # The canonical form is compared line-by-line: qmlformat emits the
    # platform's line endings, and the checkout is LF everywhere.
    return out.replace("\r\n", "\n"), ""


# --- the suite ------------------------------------------------------------
def run_jobs(root: Path, jobs, log_dir: Path, workers: int):
    """One process per job. Returns (label, rc, ran tests, log, failures, secs)."""
    # A hard wall-clock renderer budget measures the host as well as the
    # implementation. Keep it isolated after the parallel correctness lane.
    measured = [job for job in jobs if job[0] == "test_follower_seek_performance"]
    concurrent = [job for job in jobs if job not in measured]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(_one_job, [(root, job, log_dir) for job in concurrent]))
    results.extend(_one_job((root, job, log_dir)) for job in measured)
    return results


def elapsed_text(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return "%ds" % total
    if total < 3600:
        return "%dm %02ds" % (total // 60, total % 60)
    return "%dh %02dm %02ds" % (total // 3600, (total % 3600) // 60, total % 60)


def selected_tests(root: Path, files):
    names = sorted(path.name for path in (root / "tests").glob("test_*.py"))
    if not files:
        return names
    # The same spellings the POSIX leg takes: a dotted module path
    # (tests.test_monitor_qml_contracts, the Makefile's own usage line), a bare
    # module name, or a file name.
    wanted = set()
    for name in files:
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1]
        if leaf.endswith(".py"):
            leaf = leaf[:-3]
        elif "." in leaf:
            leaf = leaf.rsplit(".", 1)[-1]
        wanted.add(leaf + ".py")
    chosen = [name for name in names if name in wanted]
    missing = wanted - set(chosen)
    if missing:
        raise SystemExit("no such test file(s): %s" % ", ".join(sorted(missing)))
    if not chosen:
        raise SystemExit("no test files selected - refusing to report a green run")
    return chosen


def suite_jobs(python: str, root: Path, names, env: dict, coverage: bool,
               cov_dir=None, include_harness=True):
    jobs = []
    for name in names:
        job_env = dict(env)
        argv = [python, "-m"]
        if coverage and name != "test_follower_seek_performance.py":
            job_env["COVERAGE_FILE"] = str(cov_dir / ("cov.%s.coverage" % name[:-3]))
            argv += ["coverage", "run", "-m"]
        argv += ["unittest", "discover", "-v", "-s", "tests", "-p", name]
        jobs.append((name[:-3], argv, job_env))
    if include_harness:
        jobs += [(Path(module).stem, [python, module.replace("/", os.sep)], dict(env))
                 for module in HARNESS_MODULES]
    return jobs


def run_suite(title: str, names, jobs_count: int, coverage: bool = False,
              include_harness=True) -> int:
    root = checkout_root()
    env = tool_env()
    python = sys.executable
    rc, qt_error = capture([python, "-c", "import PyQt6.QtGui, PyQt6.QtQml"],
                           cwd=root, env=env)
    if rc:
        print(qt_error.rstrip())
        return record(title, False, "PyQt6 is required for the full suite; run make dev_install")
    scratch = scratch_dir()
    log_dir = Path(tempfile.mkdtemp(dir=scratch, prefix="mpf-tests."))
    cov_dir = None
    if coverage:
        cov_dir = scratch / "coverage"
        shutil.rmtree(cov_dir, ignore_errors=True)
        cov_dir.mkdir(parents=True, exist_ok=True)

    jobs = suite_jobs(python, root, names, env, coverage, cov_dir, include_harness)
    print("discovered %d test file(s) under tests/ (+ %d harness module(s)), "
          "jobs=%d" % (len(names), len(HARNESS_MODULES) if include_harness else 0,
                       jobs_count), flush=True)
    started = time.monotonic()
    results = run_jobs(root, jobs, log_dir, jobs_count)

    failed = 0
    for label, rc, ran, log_path, failing, took in sorted(results):
        if rc != 0 or ran == 0:
            failed = 1
            why = "ran no tests" if ran == 0 else "%d failure(s)" % len(failing)
            print("%-46s FAIL  %5d test(s)  %s  (%s)  log: %s"
                  % (label, ran, why, elapsed_text(took), log_path))
            for line in failing[:6]:
                print("    %s" % line)
        else:
            print("%-46s OK    %5d test(s)  (%s)" % (label, ran, elapsed_text(took)))

    total = sum(entry[2] for entry in results)
    print("\n%d test(s) in %d process(es), %s wall clock"
          % (total, len(results), elapsed_text(time.monotonic() - started)))
    if failed:
        print("logs under %s" % log_dir)
        record(title, False, "%d file(s) failed" % sum(1 for entry in results if entry[1] != 0))
        return 1
    record(title, True, "%d test(s)" % total)
    return 0


def _one_job(packed):
    root, job, log_dir = packed
    label, argv, env = job
    log_path = log_dir / ("%s.log" % label)
    started = time.monotonic()
    with open(log_path, "wb") as log:
        rc = subprocess.call([str(a) for a in argv], cwd=root, stdout=log,
                             stderr=subprocess.STDOUT, env=env)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    ran = 0
    failing = []
    for line in text.splitlines():
        if line.startswith("Ran ") and " test" in line:
            ran = int(line.split()[1])
        if line.startswith(("FAIL: ", "ERROR: ")):
            failing.append(line)
        if label == "test_platform_safety" and line.startswith("OK (skipped="):
            rc = 1
            failing.append("ERROR: platform safety skipped Qt tests")
    return label, rc, ran, log_path, failing, time.monotonic() - started


# --- the pinned toolchain -------------------------------------------------
def cache_dir() -> Path:
    path = scratch_dir() / "toolchain"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download(url: str, dest: Path) -> bool:
    """Fetch to a cache, reusing what is already there."""
    if dest.exists() and dest.stat().st_size:
        print("  cached %s" % dest.name)
        return True
    print("  fetching %s" % url)
    try:
        with urllib.request.urlopen(url, timeout=300) as response:
            with open(dest, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except (OSError, urllib.error.URLError, ValueError) as error:
        print("  download failed: %s" % error)
        dest.unlink(missing_ok=True)
        return False
    return True


def extract_member(archive: Path, member: str, dest: Path) -> bool:
    """Copy one member out of a zip (the releases put it in a folder)."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            leaf = member.rsplit("/", 1)[-1]
            pick = member if member in names else next(
                (name for name in names if name.rsplit("/", 1)[-1] == leaf), None)
            if pick is None:
                print("  %s carries no %s" % (archive.name, member))
                return False
            with bundle.open(pick) as source, open(dest, "wb") as handle:
                shutil.copyfileobj(source, handle)
    except (OSError, zipfile.BadZipFile) as error:
        print("  cannot unpack %s: %s" % (archive.name, error))
        return False
    return True


def python_version(exe) -> str:
    rc, out = capture([str(exe), "-c",
                       "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    return out.strip() if rc == 0 else ""


def find_pinned_python(pin: str):
    """The interpreter that IS the pin, or nothing.

    Prefer the exact patch pin, then accept the same minor version and
    report the patch difference in the parity table. WindowsApps aliases
    are skipped — running one opens the Store.
    """
    parts = pin.split(".")
    candidates = []
    override = os.environ.get("MPF_PYTHON")
    if override:
        candidates.append(override)
    local = os.environ.get("LOCALAPPDATA") if IS_WINDOWS else None
    if local:
        candidates.append(str(Path(local) / "Programs" / "Python"
                              / ("Python%s%s" % (parts[0], parts[1])) / "python.exe"))
    for name in ("python%s.%s" % (parts[0], parts[1]), "python3", "python"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:
            candidates.append(found)
    # The launcher can name the exact build.
    launcher = shutil.which("py") if IS_WINDOWS else None
    if launcher:
        rc, out = capture([launcher, "-%s" % pin, "-c",
                           "import sys; print(sys.executable)"])
        if rc == 0 and out.strip():
            candidates.append(out.strip().splitlines()[-1])
    fallback = None
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        version = python_version(path)
        if version == pin:
            return path
        if version.startswith(".".join(parts[:2]) + ".") and fallback is None:
            fallback = path
    return fallback


def install_pinned_python(pin: str):
    """Fetch and install the pinned Python per-user (no PATH, no admin)."""
    if IS_MACOS:
        brew = shutil.which("brew")
        if not brew:
            print("  Homebrew is required to install Python 3.14; see https://brew.sh/")
            return None
        if run([brew, "install", "python@3.14"]) != 0:
            return None
        rc, out = capture([brew, "--prefix", "python@3.14"])
        candidate = Path(out.strip()) / "bin" / "python3.14"
        return candidate if rc == 0 and candidate.exists() else None
    installer = cache_dir() / ("python-%s-amd64.exe" % pin)
    if not download(PYTHON_INSTALLER % {"v": pin}, installer):
        return None
    parts = pin.split(".")
    target = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
              / ("Python%s%s" % (parts[0], parts[1])))
    print("  installing per-user into %s (no PATH change, no elevation)" % target)
    if run([str(installer), "/quiet", "InstallAllUsers=0", "PrependPath=0",
            "Include_launcher=0", "Include_test=0", "SimpleInstall=1"]) != 0:
        return None
    candidate = target / "python.exe"
    return candidate if candidate.exists() else None


def ensure_venv(root: Path, interpreter: Path, pin: str):
    """A COMPLETE .venv made by the pinned interpreter, rebuilt if not.

    The completeness check is pyvenv.cfg, not the interpreter's
    presence: a rebuild that could not delete every file of the old
    tree leaves Scripts/python.exe behind with no config, and every
    later command then fails with "No pyvenv.cfg file" far from the
    cause. venv --clear is what repairs that tree.
    """
    venv_dir = root / ".venv"
    venv_python = (venv_dir / "Scripts" / "python.exe" if IS_WINDOWS
                   else venv_dir / "bin" / "python")
    wanted = python_version(interpreter)
    stale = venv_python.exists() and python_version(venv_python) != wanted
    if stale:
        print("  .venv is Python %s, the pin is %s - rebuilding it"
              % (python_version(venv_python) or "?", wanted))
        shutil.rmtree(venv_dir, ignore_errors=True)
    if not (venv_dir / "pyvenv.cfg").exists():
        argv = [str(interpreter), "-m", "venv"]
        if venv_dir.exists():
            argv.append("--clear")
        argv.append(str(venv_dir))
        if run(argv, cwd=root) != 0 or not (venv_dir / "pyvenv.cfg").exists():
            return None
    return venv_python


def install_cli_tools(root: Path) -> bool:
    bins = tool_bin_dir(root)
    bins.mkdir(parents=True, exist_ok=True)
    success = True
    for name, spec in CLI_TOOLS.items():
        target = bins / spec["exe"]
        version = TOOLCHAIN[name]
        if target.exists():
            print("  %-11s %s (already installed)" % (name, version))
            continue
        url = spec["url"] % {"v": version}
        if spec["member"] is None:
            ok = download(url, target)
        elif url.endswith(".tar.gz"):
            archive = cache_dir() / url.rsplit("/", 1)[-1]
            ok = download(url, archive)
            if ok:
                try:
                    with tarfile.open(archive, "r:gz") as bundle:
                        member = next((item for item in bundle.getmembers()
                                       if Path(item.name).name == spec["member"] and item.isfile()), None)
                        if member is None:
                            raise ValueError("archive has no %s" % spec["member"])
                        with bundle.extractfile(member) as source, open(target, "wb") as handle:
                            shutil.copyfileobj(source, handle)
                    target.chmod(0o755)
                except (OSError, tarfile.TarError, ValueError) as error:
                    print("  cannot unpack %s: %s" % (archive.name, error))
                    target.unlink(missing_ok=True)
                    ok = False
        else:
            archive = cache_dir() / url.rsplit("/", 1)[-1]
            ok = download(url, archive) and extract_member(
                archive, spec["member"] % {"v": version}, target)
        print("  %-11s %s" % (name, version if ok else "FAILED"))
        success = success and ok
    if IS_MACOS and not shutil.which("hadolint"):
        brew = shutil.which("brew")
        if not brew or run([brew, "install", "hadolint"]) != 0:
            print("  hadolint FAILED (install Homebrew, then run make dev_install)")
            success = False
    return success


def install_fonts(root: Path) -> int:
    """The container's fonts-dejavu-core, for the same Qt metrics."""
    fonts = root / ".venv" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    if not any(fonts.glob("*.ttf")):
        archive = cache_dir() / FONTS_URL.rsplit("/", 1)[-1]
        if download(FONTS_URL, archive):
            try:
                with zipfile.ZipFile(archive) as bundle:
                    for name in bundle.namelist():
                        if not name.lower().endswith(".ttf"):
                            continue
                        with bundle.open(name) as source, \
                                open(fonts / name.rsplit("/", 1)[-1], "wb") as handle:
                            shutil.copyfileobj(source, handle)
            except (OSError, zipfile.BadZipFile) as error:
                print("  cannot unpack the fonts: %s" % error)
    return len(list(fonts.glob("*.ttf")))


def observed_version(name: str, root: Path) -> str:
    """What this machine actually has, for the parity table."""
    venv_python = (root / ".venv" / "Scripts" / "python.exe" if IS_WINDOWS
                   else root / ".venv" / "bin" / "python")
    python = str(venv_python) if venv_python.exists() else sys.executable
    if name == "python":
        return python_version(python) or "missing"
    if name in ("PyQt6", "PyQt6-Qt6"):
        # PYQT_VERSION_STR is the binding's build version and
        # QT_VERSION_STR the Qt it was built against; qVersion() is the
        # Qt runtime actually loaded, which is the PyQt6-Qt6 pin.
        code = ("import PyQt6.QtCore as core; print(core.PYQT_VERSION_STR)"
                if name == "PyQt6" else
                "import PyQt6.QtCore as core; print(core.qVersion())")
        rc, out = capture([python, "-c", code])
        return out.strip() if rc == 0 else "missing"
    if name == "qmlformat":
        wheels = list((root / ".venv").glob("lib/python*/site-packages/PySide6/qmlformat"))
        wheels += list((root / ".venv").glob("Lib/site-packages/PySide6/qmlformat.exe"))
        exe = str(wheels[0]) if wheels else qmlformat_exe()
        rc, out = capture([exe, "--version"]) if exe else (1, "")
    elif name == "ruff":
        beside = venv_python.parent / ("ruff.exe" if IS_WINDOWS else "ruff")
        exe = str(beside) if beside.exists() else ruff_exe()
        rc, out = capture([exe, "--version"]) if exe else (1, "")
    elif name == "fonts-dejavu-core":
        count = len(list((root / ".venv" / "fonts").glob("*.ttf")))
        return "%d face(s)" % count if count else "missing"
    else:
        exe = pinned_tool(name)
        rc, out = capture([exe, "--version"]) if exe else (1, "")
    if rc != 0:
        return "present"
    # The FIRST version in the output: actionlint prints its own number
    # first and then the Go toolchain it was built with (go1.26.1),
    # which would otherwise be read as the tool's version.
    text = out.strip()
    found = re.search(r"\d+\.\d+(\.\d+)?", text)
    return found.group(0) if found else (text or "present")


def print_toolchain(root: Path) -> None:
    print("\n%-22s %-10s %-12s %s" % ("component", "container", "this machine", ""))
    for name, pin in TOOLCHAIN.items():
        got = observed_version(name, root)
        if name == "fonts-dejavu-core":
            mark = "match" if got != "missing" else "MISSING"
        elif name == "python" and got.split(".")[:2] == pin.split(".")[:2]:
            mark = "patch differs" if got != pin else "match"
        else:
            mark = "match" if got == pin else "DIFFERS"
        print("%-22s %-10s %-12s %s" % (name, pin, got, mark))


# --- the subcommands ------------------------------------------------------
def cmd_bootstrap(args) -> int:
    root = checkout_root()
    pin = TOOLCHAIN["python"]
    interpreter = find_pinned_python(pin)
    if interpreter is None and not args.no_install:
        step("no Python %s on this machine - installing the pinned build" % pin)
        interpreter = install_pinned_python(pin)
    if interpreter is None and IS_MACOS:
        return record("bootstrap", False, "Python 3.14 unavailable; install Homebrew or set MPF_PYTHON")
    if interpreter is None:
        interpreter = Path(sys.executable)
        print("WARNING: Python %s is not on this machine; using %s (%s)."
              % (pin, interpreter, python_version(interpreter) or "?"))
        print("         `make dev_install` fetches the pinned Windows build unless")
        print("         --no-install is passed; install Python 3.14 to align with CI.")

    step("the venv")
    venv_python = ensure_venv(root, Path(interpreter), pin)
    if venv_python is None:
        return record("bootstrap", False, "could not create .venv")
    print("  %s" % venv_python)

    step("installing the pinned packages")
    pins = [template % TOOLCHAIN for template in PYPI_PINS]
    pins.append(QSB_PIN % TOOLCHAIN)
    if not args.no_qmlformat:
        pins.append(QMLFORMAT_PIN % TOOLCHAIN)
    if run([str(venv_python), "-m", "pip", "install", "--disable-pip-version-check",
            "-q", *pins], cwd=root) != 0:
        return record("bootstrap", False, "pip install failed")
    for optional in OPTIONAL_PINS:
        if run([str(venv_python), "-m", "pip", "install", "--disable-pip-version-check",
                "-q", optional], cwd=root) != 0:
            print("  %s is not available for this interpreter - only the benchmark"
                  " tool needs it" % optional)

    step("the pinned command line tools")
    tools_ok = install_cli_tools(root)

    step("the container's fonts (fonts-dejavu-core %s)"
         % TOOLCHAIN["fonts-dejavu-core"])
    print("  %d DejaVu face(s) under .venv/fonts" % install_fonts(root))

    print_toolchain(root)
    print("\nthe venv is %s - `make` picks it up on its own" % venv_python)
    return record("bootstrap", tools_ok and pinned_fonts_dir() is not None,
                  "tool download or fonts missing" if not tools_ok or pinned_fonts_dir() is None else "")


def cmd_lint(args) -> int:
    root = checkout_root()
    env = tool_env()
    python = sys.executable

    step("python: compile, QML structure, QML engine")
    record("compile", run([python, "-m", "compileall", "-q", "mpf", "tools", "tests"],
                          cwd=root, env=env) == 0)
    record("qml-structure", run([python, "tools/check_qml.py", "mpf"], cwd=root, env=env) == 0)
    record("qml-engine", run([python, "tools/check_qml_engine.py"], cwd=root, env=env) == 0)

    step("linters")
    ruff = ruff_exe()
    if ruff:
        record("ruff", run([ruff, "check", "mpf", "tools", "tests"], cwd=root, env=env) == 0)
    else:
        record("ruff", False, "not installed - run: make dev_install")

    fmt = qmlformat_exe()
    files = qml_files(root)
    if not fmt:
        record("qmlformat", False, "not installed - run: make dev_install")
    else:
        offenders = []
        for path in files:
            text, why = formatted_text(root, env, fmt, path)
            if text is None:
                offenders.append("%s (%s)" % (path.relative_to(root), why))
            elif text != path.read_text(encoding="utf-8").replace("\r\n", "\n"):
                offenders.append("%s (%s)" % (
                    path.relative_to(root),
                    first_difference(path.read_text(encoding="utf-8"), text)))
        for offender in offenders:
            print("  not qmlformat-canonical: %s" % offender)
        record("qmlformat", not offenders, "%d file(s)" % len(files))

    step("the scripts around the plugin")
    shell_scripts = sorted(str(path) for path in (root / "tools").glob("*.sh"))
    workflows = sorted(str(path) for path in (root / ".github" / "workflows").glob("*.yml"))
    for label, argv in (
        ("shellcheck", shell_scripts),
        ("hadolint", ["Dockerfile"]),
        # The pinned actionlint, at the version tools/check_workflows.sh
        # fetches on the POSIX leg. -shellcheck= disables its shellcheck
        # integration on this platform: the Windows shellcheck binary
        # deadlocks the pipe actionlint feeds it (reproduced: with the
        # pinned shellcheck on PATH the lint hangs; off it returns in a
        # second). The shell scripts are still checked — by shellcheck
        # itself, on the line above.
        ("actionlint", (["-shellcheck="] if IS_WINDOWS else []) + workflows),
        ("gitleaks", ["detect", "--no-git", "--no-banner", "--redact"]),
    ):
        # The pinned build when bootstrap fetched it, PATH otherwise -
        # never a different version of the same linter.
        if label == "actionlint" and sys.platform == "linux" and not pinned_tool(label):
            record(label, run(["sh", "tools/check_workflows.sh"], cwd=root, env=env) == 0)
            continue
        exe = pinned_tool(label)
        if not exe:
            # CI owns these: they lint the shell/Docker/CI plumbing, not
            # the plugin, and a Windows dev box has no reason to carry
            # them. `make dev_install` installs the pinned builds.
            record(label, False, "not installed - run: make dev_install")
            continue
        record(label, run([exe, *argv], cwd=root, env=env) == 0)

    return report("lint")


def cmd_format(args) -> int:
    root = checkout_root()
    env = tool_env()
    fmt = qmlformat_exe()
    if not fmt:
        return record("format", False, "qmlformat not found - run: make dev_install")
    changed = 0
    for path in qml_files(root):
        text, why = formatted_text(root, env, fmt, path)
        if text is None:
            return record("format", False, "%s: %s" % (path.relative_to(root), why))
        if text != path.read_text(encoding="utf-8").replace("\r\n", "\n"):
            # Written back as LF: qmlformat emits the platform's line
            # endings, and rewriting every line of a file for that
            # alone would bury the real change in the diff.
            path.write_text(text, encoding="utf-8", newline="\n")
            print("  rewrote %s" % path.relative_to(root))
            changed += 1
    return record("format", True, "%d file(s) rewritten" % changed)


def cmd_test(args) -> int:
    names = selected_tests(checkout_root(), args.files)
    return run_suite("test", names, args.jobs, include_harness=not args.files)


def cmd_coverage(args) -> int:
    root = checkout_root()
    names = selected_tests(root, args.files)
    status = run_suite("coverage suite", names, args.jobs, coverage=True,
                       include_harness=not args.files)
    if status:
        return status
    env = tool_env()
    python = sys.executable
    scratch = scratch_dir()
    cov_dir = scratch / "coverage"
    combined = cov_dir / ".coverage"
    combined_env = dict(env)
    combined_env["COVERAGE_FILE"] = str(combined)
    part_files = sorted(str(path) for path in cov_dir.glob("cov.*.coverage"))

    step("combining %d worker file(s)" % len(part_files))
    if run([python, "-m", "coverage", "combine", *part_files], cwd=root,
           env=combined_env) != 0:
        return record("coverage", False, "combine failed")
    report_ok = run([python, "-m", "coverage", "report", "--include=mpf/*",
                     "--fail-under=%s" % COVERAGE_BAR], cwd=root, env=combined_env) == 0
    record("coverage total (%.0f%% bar)" % COVERAGE_BAR, report_ok)
    json_path = scratch / "coverage.json"
    if run([python, "-m", "coverage", "json", "-o", str(json_path)], cwd=root,
           env=combined_env) != 0:
        return record("coverage", False, "coverage json failed")
    rc, out = capture([python, "tools/check_per_file_coverage.py", str(json_path)],
                      cwd=root, env=env)
    if rc == 0:
        record("coverage per file", True)
    else:
        failing = [line.split()[0] for line in out.splitlines()
                   if line.strip().startswith("mpf/")]
        excused = [name for name in failing if name in PLATFORM_BOUND_COVERAGE]
        if IS_WINDOWS and failing and len(excused) == len(failing):
            print(out.rstrip())
            for name in excused:
                print("  platform-bound: %s - %s" % (name, PLATFORM_BOUND_COVERAGE[name]))
            record("coverage per file", True,
                   "every unexcused file cleared the %.0f%% bar" % COVERAGE_BAR)
        else:
            print(out.rstrip())
            record("coverage per file", False,
                   "%d file(s) below the %.0f%% bar" % (len(failing), COVERAGE_BAR))
    return report("coverage")


def cmd_captures(args) -> int:
    root = checkout_root()
    env = tool_env()
    out = Path(args.out) if args.out else root / "dist" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    if args.theme:
        env["CAPTURE_THEME"] = args.theme
    step("capture scenes into %s" % out)
    for script in CAPTURE_SCRIPTS:
        if run([sys.executable, "tools/%s" % script, str(out)], cwd=root, env=env,
               timeout=120) != 0:
            return record("captures", False, "%s failed" % script)
    rendered = sorted(path.name for path in out.glob("*.png"))
    for name in rendered:
        print("  %s" % name)
    return record("captures", bool(rendered), "%d scene(s)" % len(rendered))


def cmd_refresh(args) -> int:
    for old in (checkout_root() / "dist" / "screenshots").glob("*.png"):
        old.unlink()
    status = cmd_captures(args)
    if status:
        return status
    # Only the pinned Linux image defines the canonical committed pixels.
    if sys.platform != "linux":
        print("dist/screenshots is fresh; committed screenshots remain container-canonical")
        return 0
    root = checkout_root()
    for source in (root / "dist" / "screenshots").glob("*.png"):
        shutil.copyfile(source, root / "screenshots" / source.name)
    return record("refresh", True)


def cmd_shaders(args) -> int:
    argv = [sys.executable, "tools/build_shaders.py"]
    if args.qsb:
        argv += ["--qsb", args.qsb]
    return record("shaders", run(argv, cwd=checkout_root(), env=tool_env()) == 0)


def cmd_container_only(args) -> int:
    return record(args.command, False, "requires BACKEND=docker")


def cmd_ui_test(args) -> int:
    """Stage and run the existing native Cura harness from one Make target."""
    root = checkout_root()
    mode = args.mode
    runner_mode = mode if mode in ("scenario", "suite", "firstinstall", "migration",
                                   "discover", "real") or re.fullmatch(r"scenario([0-9]|1[01])", mode) else "suite"
    runner_args = [runner_mode] + ([mode] if runner_mode == "suite" and mode != "suite" else [])
    runner = str(root / "tests" / "harness" / "runner.py")
    if IS_MACOS:
        work = Path(os.environ.get("MPF_WORK_DIR", "/tmp/mpf-native"))
        if run(["bash", "tools/native_harness.sh", "macos", args.cura_version, mode], cwd=root) != 0:
            return record("ui_test", False, "native harness setup failed")
        env_file = work / "harness_env.sh"
        command = ["bash", "-c", 'source "$1"; shift; exec "$@"', "--",
                   str(env_file), sys.executable, runner, *runner_args]
    elif IS_WINDOWS:
        work = Path(os.environ.get("MPF_WORK_DIR") or
                    Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "mpf-native")
        if run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                "tools/native_harness.ps1", "-CuraVersion", args.cura_version,
                "-Scenario", mode], cwd=root) != 0:
            return record("ui_test", False, "native harness setup failed")
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        env_file = work / "harness_env.ps1"
        script = ". %s; & %s %s %s" % (
            quote(env_file), quote(sys.executable), quote(runner),
            " ".join(quote(item) for item in runner_args))
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-Command", script]
    else:
        return record("ui_test", False, "native harness requires macOS or Windows")
    return record("ui_test", run(command, cwd=root) == 0)


def cmd_ui_release_gate(args) -> int:
    if cmd_gates(args):
        return 1
    primary = os.environ.get("CURA_PRIMARY", "5.13.0")
    secondary = os.environ.get("CURA_SECONDARY", "5.12.0")
    groups = ("connection", "status", "temperatures", "console", "webcams",
              "files", "motion", "printing", "settings", "visual", "preview",
              "probe", "configure", "stress")
    for version, mode in [(primary, "smoke"),
                          *((primary, group) for group in groups),
                          (primary, "firstinstall"), (secondary, "smoke")]:
        args.cura_version, args.mode = version, mode
        if cmd_ui_test(args):
            return 1
    return 0


def cmd_determinism(args) -> int:
    root = checkout_root()
    env = tool_env()
    tmp = root / ".determinism-check"
    shutil.rmtree(tmp, ignore_errors=True)
    legs = (("run1", "cura-light"), ("run2", "cura-light"),
            ("run1-dark", "cura-dark"), ("run2-dark", "cura-dark"))
    for leg, _theme in legs:
        (tmp / leg).mkdir(parents=True, exist_ok=True)

    def render(leg, theme):
        leg_env = dict(env)
        leg_env["CAPTURE_THEME"] = theme
        leg_env["CAPTURE_THEME_TREE"] = str(tmp / leg / ".theme")
        leg_env["QML_DISK_CACHE_PATH"] = str(tmp / leg / ".qmlcache")
        log = tmp / ("%s.log" % leg)
        with open(log, "wb") as handle:
            for script in DETERMINISM_SCRIPTS:
                rc = subprocess.call(
                    [sys.executable, "tools/%s" % script, str(tmp / leg)],
                    cwd=root, env=leg_env, stdout=handle, stderr=subprocess.STDOUT)
                if rc != 0:
                    return leg, rc
        return leg, 0

    step("two capture runs, light and dark, side by side")
    with ThreadPoolExecutor(max_workers=len(legs)) as pool:
        results = list(pool.map(lambda pair: render(*pair), legs))
    for leg, rc in results:
        print("  %-10s %s" % (leg, "OK" if rc == 0 else "FAILED (log: %s.log)" % leg))
    if any(rc != 0 for _leg, rc in results):
        return record("determinism", False, "a capture leg failed - logs in %s" % tmp)

    stale = 0
    compared = 0
    for first, second, label in (("run1", "run2", "light"),
                                 ("run1-dark", "run2-dark", "dark")):
        names = {path.name for path in (tmp / first).glob("*.png")}
        others = {path.name for path in (tmp / second).glob("*.png")}
        if names != others:
            only_first = sorted(names - others)
            only_second = sorted(others - names)
            print("NON-DETERMINISTIC (%s): the runs produced different scene lists" % label)
            for name in only_first:
                print("  only in %s: %s" % (first, name))
            for name in only_second:
                print("  only in %s: %s" % (second, name))
            stale = 1
        for name in sorted(names & others):
            compared += 1
            mine = (tmp / first / name).read_bytes()
            theirs = (tmp / second / name).read_bytes()
            if mine != theirs:
                print("NON-DETERMINISTIC (%s): %s differs between two runs on this host"
                      % (label, name))
                run([sys.executable, "tools/image_diff.py", str(tmp / first / name),
                     str(tmp / second / name)], cwd=root, env=env)
                stale = 1
    if stale:
        return record("determinism", False, "evidence kept in %s" % tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return record("determinism", True, "%d scene(s) byte-identical across two runs" % compared)


def _version(root: Path) -> str:
    return json.loads((root / "package.json").read_text(encoding="utf-8"))["package_version"]


def _build_artifacts(root: Path, env: dict):
    """Both artifacts, side by side then verified (the Makefile's ruling)."""
    step("compiling the packaged GPU shaders")
    if run([sys.executable, "tools/build_shaders.py"], cwd=root, env=env) != 0:
        return False
    step("building both artifacts")
    with ThreadPoolExecutor(max_workers=2) as pool:
        built = list(pool.map(
            lambda script: run([sys.executable, "tools/%s" % script], cwd=root, env=env),
            ("build_curapackage.py", "build_marketplace_source.py")))
    if any(built):
        return False
    version = _version(root)
    ok = run([sys.executable, "tools/verify_curapackage.py",
              str(root / "dist" / ("MoonrakerPrintFollower-v%s.curapackage" % version))],
             cwd=root, env=env) == 0
    ok = run([sys.executable, "tools/verify_marketplace_source.py",
              str(root / "dist" / ("MoonrakerPrintFollower-v%s-source.zip" % version))],
             cwd=root, env=env) == 0 and ok
    return ok


def cmd_package(args) -> int:
    root = checkout_root()
    return record("package", _build_artifacts(root, tool_env()))


def cmd_snapshot(args) -> int:
    root = checkout_root()
    if not _build_artifacts(root, tool_env()):
        return record("snapshot", False, "the package did not build")
    return cmd_copy_snapshot(args)


def cmd_copy_snapshot(args) -> int:
    root = checkout_root()
    target = Path(args.to) if args.to else Path(tempfile.gettempdir()) / "mpf.curapackage"
    source = root / "dist" / ("MoonrakerPrintFollower-v%s.curapackage" % _version(root))
    if not source.is_file():
        return record("snapshot", False, "%s is missing" % source)
    shutil.copyfile(source, target)
    print("wrote %s" % target)
    return record("snapshot", True, str(target))


def cmd_gates(args) -> int:
    status = cmd_lint(args)
    status = cmd_test(args) or status
    status = cmd_captures(args) or status
    return status


def cmd_hooks(args) -> int:
    """Install a pre-commit hook that runs this leg's checks.

    Git supplies the hook interpreter on Windows; macOS has /bin/sh.
    """
    root = checkout_root()
    hooks = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root,
                           capture_output=True, text=True)
    if hooks.returncode != 0:
        return record("hooks", False, "git rev-parse --git-path hooks failed")
    hook_path = (root / hooks.stdout.strip()).resolve() / "pre-commit"
    python = sys.executable
    body = (
        "#!/bin/sh\n"
        "# Installed by `make install_hooks` (the native leg).\n"
        "exec \"%s\" \"%s\" hook-check\n" % (
            python.replace("\\", "/"),
            str(root / "tools" / "native" / "dev.py").replace("\\", "/"))
    )
    hook_path.write_text(body, encoding="utf-8", newline="\n")
    if not IS_WINDOWS:
        hook_path.chmod(0o755)
    print("installed %s" % hook_path)
    return record("hooks", True, str(hook_path))


def cmd_hook_check(args) -> int:
    """The pre-commit slice: the cheap checks, then the suite."""
    status = cmd_lint(args)
    names = selected_tests(checkout_root(), [])
    return run_suite("hook tests", names, args.jobs) or status


def cmd_clean(args) -> int:
    root = checkout_root()
    removed = []
    for name in ("dist", ".determinism-check"):
        target = root / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(name)
    for path in root.rglob("__pycache__"):
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(str(path.relative_to(root)))
    for path in root.rglob("*~"):
        if ".git" in path.parts or ".venv" in path.parts or not path.is_file():
            continue
        path.unlink(missing_ok=True)
        removed.append(str(path.relative_to(root)))
    for name in removed:
        print("  removed %s" % name)
    return record("clean", True, "%d item(s)" % len(removed))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=(
        "bootstrap", "lint", "format", "test", "coverage", "captures", "refresh", "shaders",
        "determinism", "package", "snapshot", "copy_snapshot", "gates", "hooks", "hook-check", "clean",
        "dev_up", "dev_down", "exec", "ui_test", "ui_release_gate"))
    parser.add_argument("--jobs", type=int, default=default_jobs(),
                        help="parallel test/diagnostic workers (default: JOBS or 16)")
    parser.add_argument("--file", action="append", default=[], dest="files",
                        help="run only this test file (repeatable; test/coverage)")
    parser.add_argument("--out", default="", help="capture output directory")
    parser.add_argument("--theme", default="", help="capture theme (cura-light/cura-dark)")
    parser.add_argument("--to", default="", help="snapshot destination")
    parser.add_argument("--qsb", default="", help="Qt Shader Tools qsb executable")
    parser.add_argument("--mode", default="scenario", help="native UI harness mode")
    parser.add_argument("--cura-version", default="5.13.0", help="native Cura harness version")
    parser.add_argument("--no-qmlformat", action="store_true",
                        help="bootstrap without the PySide6 wheel qmlformat comes in")
    parser.add_argument("--no-install", action="store_true",
                        help="bootstrap without fetching the pinned Python build")
    args = parser.parse_args(argv)
    handler = {
        "bootstrap": cmd_bootstrap, "lint": cmd_lint, "format": cmd_format,
        "test": cmd_test, "coverage": cmd_coverage, "captures": cmd_captures,
        "refresh": cmd_refresh, "shaders": cmd_shaders,
        "determinism": cmd_determinism, "package": cmd_package,
        "snapshot": cmd_snapshot, "copy_snapshot": cmd_copy_snapshot,
        "gates": cmd_gates, "hooks": cmd_hooks,
        "hook-check": cmd_hook_check, "clean": cmd_clean,
        "dev_up": cmd_container_only, "dev_down": cmd_container_only,
        "exec": cmd_container_only,
        "ui_test": cmd_ui_test, "ui_release_gate": cmd_ui_release_gate,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
