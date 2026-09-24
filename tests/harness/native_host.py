#!/usr/bin/env python3
"""The harness's platform dispatch — the ONE place a host platform is
chosen.

runner.py, the driver-side files and any native launcher ask this
module for the argv a given platform needs, instead of branching on
sys.platform at each call site. Everything above the CLI section is a
pure function — a platform name in, argv or parsed values out — so all
three platforms stay testable from any one of them
(tests/harness/test_harness_native.py).

Three things genuinely differ between the platforms:

  * the capture DEVICE — x11grab / avfoundation / gdigrab. The region
    is the WHOLE DISPLAY on every platform (the Linux capture is a
    full display whose only occupant is the app), because no ffmpeg
    input device re-reads its region once running: a window-bounded
    recording would clip the window as a resize scenario grew it,
    silently. It also means the still's declared size still holds on
    the natives — avfoundation ignores -video_size and records the
    display it is given, so the native launcher must have put the
    display AT the harness geometry or shot()'s size check fails the
    frame, which is the honest reading of a frame that is not the one
    the run asked for.
  * the recorder's OUTPUT FLAGS. A native recorder is stopped by a
    kill when a run tears down, and a plain mp4 keeps its index at the
    end, so a killed recorder leaves an mdat with no moov — a file no
    reader can open. +frag_keyframe flushes on a keyframe, and without
    -g that keyframe is up to the default 250-frame interval away
    (16.7 s at 15 fps), so a kill would lose everything after the last
    one. Linux stops its recorder gracefully and keeps its flags.
  * LAUNCHING Cura, FINDING its window and STOPPING it. Under Xvfb the
    harness launches through the container and reads the window
    in-process through the driver's Qt; the natives need the platform's
    own call for each (System Events / the DWM frame rectangle), and
    the same holds for the process itself — Windows has no pgrep, and
    its absence reads as "still running" if it is not dispatched.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

LINUX = "linux"
MACOS = "macos"
WINDOWS = "windows"
SYSTEMS = (LINUX, MACOS, WINDOWS)

# avfoundation's device ordering is not guaranteed, so the screen index
# is read from ffmpeg's own device probe; 1 is the conventional screen
# device and the fallback when the probe names none.
DEFAULT_SCREEN_INDEX = "1"

_CAPTURE_DEVICE = {LINUX: "x11grab", MACOS: "avfoundation", WINDOWS: "gdigrab"}

# The recorder's output flags where the recorder is stopped by a kill
# (see the module docstring). Inserted after the input spec, where
# ffmpeg's output options belong.
_FRAGMENTED = ["-g", "30", "-movflags", "+frag_keyframe+empty_moov+default_base_moof"]


def current_system(plat=None):
    """The harness's platform name for sys.platform (the parameter
    exists so the mapping itself is testable)."""
    plat = sys.platform if plat is None else plat
    if plat.startswith("darwin"):
        return MACOS
    if plat.startswith("win"):
        return WINDOWS
    return LINUX


def _checked(system):
    if system not in SYSTEMS:
        raise ValueError("unknown system %r (expected one of %s)"
                         % (system, ", ".join(SYSTEMS)))
    return system


def capture_input_args(system, size, *, display=None, screen_index=None, framerate=None):
    """The ffmpeg input spec for one capture on this platform.

    `size` is the requested geometry (HARNESS_GEOMETRY). avfoundation
    ignores it — the probe frame is the authority there — but the argv
    is kept identical in shape across the platforms so the recordings
    stay comparable. `framerate` is part of the INPUT options, which is
    where the harness has always put it, so it is carried here rather
    than by the callers.
    """
    _checked(system)
    args = ["-f", _CAPTURE_DEVICE[system], "-video_size", str(size)]
    if framerate is not None:
        args += ["-framerate", str(framerate)]
    if system == LINUX:
        if not display:
            raise ValueError("the Linux capture needs its X display")
        target = display
    elif system == MACOS:
        target = "%s:none" % (screen_index or DEFAULT_SCREEN_INDEX)
    else:
        target = "desktop"
    return args + ["-i", target]


def recorder_output_args(system):
    """The output options a RECORDING needs on this platform (never a
    still's: a still is finished by its own -frames:v)."""
    _checked(system)
    return [] if system == LINUX else list(_FRAGMENTED)


def recorder_argv(system, path, *, size, display=None, screen_index=None, framerate=15):
    """The full argv for a scenario recording."""
    return (["ffmpeg", "-y", "-loglevel", "error"]
            + capture_input_args(system, size, display=display,
                                 screen_index=screen_index, framerate=framerate)
            + recorder_output_args(system)
            + [path])


def still_argv(system, path, *, size, display=None, screen_index=None):
    """The full argv for one frame (the harness's shot())."""
    return (["ffmpeg", "-y", "-loglevel", "error"]
            + capture_input_args(system, size, display=display,
                                 screen_index=screen_index)
            + ["-frames:v", "1", path])


def avfoundation_probe_argv():
    """The device-list probe. ffmpeg writes it to stderr and exits
    non-zero, which is the call's normal shape."""
    return ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""]


_SCREEN_DEVICE = "Capture screen"


def avfoundation_screen_index(text):
    """The screen device's index in ffmpeg's device list ('[1] Capture
    screen at ...'), or the conventional 1 when the list names none."""
    for line in (text or "").splitlines():
        marker = line.find(_SCREEN_DEVICE)
        if marker < 0:
            continue
        opening = line.rfind("[", 0, marker)
        closing = line.find("]", opening, marker)
        if opening < 0 or closing < 0:
            continue
        digits = line[opening + 1:closing].strip()
        if digits.isdigit():
            return digits
    return DEFAULT_SCREEN_INDEX


def screen_index(probe=None):
    """The avfoundation screen index for this host (None elsewhere),
    read from the device probe and cached — the device list cannot
    change inside a run. Impure by design: it runs ffmpeg."""
    if current_system() != MACOS:
        return None
    global _SCREEN_INDEX
    if _SCREEN_INDEX is None:
        run = probe or _run_probe
        _SCREEN_INDEX = avfoundation_screen_index(run())
    return _SCREEN_INDEX


_SCREEN_INDEX = None


def _run_probe():
    try:
        done = subprocess.run(avfoundation_probe_argv(), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60)
        return done.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


# ─── launching Cura ──────────────────────────────────────────────


def _powershell(script):
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def _quote(value):
    # PowerShell single-quoted string; an embedded quote doubles.
    return "'" + str(value).replace("'", "''") + "'"


def launch_command(system, binary, *, working_dir=None, stdout=None, stderr=None):
    """The argv that starts Cura, and the directory it must run in.

    macOS: a direct exec from the directory the binary lives in — the
    app bundle's Contents/MacOS. Windows: Start-Process with both
    streams redirected, because CreateProcess is what gives the child
    this process's own session, window station and desktop — the very
    ones the capture sees. Linux's own launch is the container boot in
    tools/ui_test.sh (Xvfb, the AppImage loader, the GL environment)
    and does not come through here; the direct exec is returned for
    completeness only.
    """
    _checked(system)
    if system == WINDOWS:
        parts = ["Start-Process -FilePath", _quote(binary), "-PassThru"]
        if working_dir:
            parts += ["-WorkingDirectory", _quote(working_dir)]
        if stdout:
            parts += ["-RedirectStandardOutput", _quote(stdout)]
        if stderr:
            parts += ["-RedirectStandardError", _quote(stderr)]
        script = "$p = %s; Write-Output ('pid=' + $p.Id)" % " ".join(parts)
        return {"argv": _powershell(script), "cwd": None}
    if system == MACOS:
        return {"argv": [binary], "cwd": working_dir or os.path.dirname(binary)}
    return {"argv": [binary], "cwd": working_dir}


def parse_launch_pid(system, text):
    """The launched pid, from the native launcher's own output."""
    _checked(system)
    if system == WINDOWS:
        for line in (text or "").splitlines():
            if line.startswith("pid="):
                value = line[4:].strip()
                if value.isdigit():
                    return int(value)
        return None
    value = (text or "").strip()
    return int(value) if value.isdigit() else None


# The app, by the name its process carries. pgrep matches the full
# command line with the bracket so it can never match its own argv (the
# harness's standing idiom); Windows has no pgrep, and Get-Process
# knows the image name instead.
_PROCESS_NAME = "UltiMaker-Cura"
_PROCESS_MATCH = "UltiMaker-Cur[a]"


def process_alive_argv(system, *, name=_PROCESS_NAME):
    """The argv that exits 0 while Cura's process is alive.

    Windows is the reason this exists: a bare `pgrep` raises OSError
    there, which reads as "alive" and makes a clean quit look failed.
    """
    _checked(system)
    if system == WINDOWS:
        return _powershell("if (Get-Process -Name %s -ErrorAction SilentlyContinue) "
                           "{ exit 0 } else { exit 1 }" % _quote(name))
    return ["pgrep", "-f", _PROCESS_MATCH]


def kill_command(system, *, name=_PROCESS_NAME):
    """The argv that force-stops Cura, exiting 0 either way.

    The two-boot legs need the first app GONE before the second one
    starts: a survivor holds the config tree and the rendezvous file,
    and the second boot would race it. The natives' own setup scripts
    kill through the same two calls.
    """
    _checked(system)
    if system == WINDOWS:
        return _powershell(
            "Get-Process -Name %s -ErrorAction SilentlyContinue | "
            "Stop-Process -Force -ErrorAction SilentlyContinue; exit 0" % _quote(name))
    return ["pkill", "-9", "-f", _PROCESS_MATCH]


# ─── finding the window ──────────────────────────────────────────


# DWMWA_EXTENDED_FRAME_BOUNDS (9): the rectangle the compositor draws,
# which is the one a capture sees. A window that is not DWM-composited
# has none, and GetWindowRect is then the right answer.
_WINDOWS_SOURCE = "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)"

_WINDOWS_WINDOW_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public struct MpfRect { public int Left; public int Top; public int Right; public int Bottom; }
public static class MpfWindow {
  public delegate bool EnumProc(IntPtr hwnd, IntPtr param);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr param);
  [DllImport("user32.dll", EntryPoint = "GetWindowTextLengthW", CharSet = CharSet.Unicode)] public static extern int GetWindowTextLength(IntPtr hwnd);
  [DllImport("user32.dll", EntryPoint = "GetWindowTextW", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
  [DllImport("user32.dll", EntryPoint = "GetClassNameW", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int max);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hwnd, uint cmd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out MpfRect rect);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out MpfRect value, int size);
  public static string Title(IntPtr hwnd) { int n = GetWindowTextLength(hwnd); StringBuilder sb = new StringBuilder(n + 2); GetWindowText(hwnd, sb, sb.Capacity); return sb.ToString(); }
  public static string Class(IntPtr hwnd) { StringBuilder sb = new StringBuilder(256); GetClassName(hwnd, sb, sb.Capacity); return sb.ToString(); }
  public static bool Frame(IntPtr hwnd, out MpfRect rect, out string source) {
    rect = new MpfRect();
    source = "GetWindowRect";
    if (hwnd == IntPtr.Zero) { return false; }
    try {
      if (DwmGetWindowAttribute(hwnd, 9, out rect, Marshal.SizeOf(typeof(MpfRect))) == 0
          && rect.Right > rect.Left && rect.Bottom > rect.Top) {
        source = "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)";
        return true;
      }
    } catch { }
    rect = new MpfRect();
    source = "GetWindowRect";
    return GetWindowRect(hwnd, out rect);
  }
  public static string Report(uint pid, string hint) {
    IntPtr pick = IntPtr.Zero;
    int best = 0;
    EnumWindows(delegate(IntPtr hwnd, IntPtr param) {
      string title = Title(hwnd);
      uint wpid = 0;
      GetWindowThreadProcessId(hwnd, out wpid);
      bool visible = IsWindowVisible(hwnd);
      bool iconic = IsIconic(hwnd);
      bool owned = GetWindow(hwnd, 4) != IntPtr.Zero;
      bool named = title.Length > 0 && hint.Length > 0 && title.IndexOf(hint, StringComparison.OrdinalIgnoreCase) >= 0;
      int score = 0;
      if (title.Length > 0) {
        if (pid != 0 && wpid == pid) { score += 4; }
        if (named) { score += 4; }
        if (visible) { score += 1; }
        if (!iconic) { score += 1; }
        if (!owned) { score += 1; }
      }
      if (score > best) { best = score; pick = hwnd; }
      return true;
    }, IntPtr.Zero);
    if (pick == IntPtr.Zero) { return "rect=none"; }
    MpfRect r;
    string source;
    if (!Frame(pick, out r, out source)) { return "rect=none"; }
    return string.Format("rect={0},{1},{2},{3} source={4} minimized={5} title=[{6}]",
                         r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top, source, IsIconic(pick), Title(pick));
  }
}
'@
Write-Output ([MpfWindow]::Report([uint32]PID, HINT))
"""

_MACOS_WINDOW_SOURCE = "System Events (osascript)"


def window_rect_commands(system, *, process_name="UltiMaker-Cura", pid=0, title_hint="Cura"):
    """The commands that report Cura's window rectangle, for the
    platforms that need one. Linux returns none: the harness reads its
    window in-process, through the driver's Qt, on every platform.

    macOS reads the three properties from one process's window 1 —
    position and size in screen points, which is the same space the
    whole-display capture covers. Windows enumerates the top-level
    windows and picks one by score (this pid, then the title hint,
    then visible, unminimised, unowned), so a launcher that owns the
    window's handle is not mistaken for the window itself.
    """
    _checked(system)
    if system == LINUX:
        return []
    if system == MACOS:
        tell = 'tell application "System Events" to tell process "%s" to ' % process_name
        return [["osascript", "-e", tell + "get position of window 1"],
                ["osascript", "-e", tell + "get size of window 1"],
                ["osascript", "-e", tell + 'get value of attribute "AXMinimized" of window 1']]
    script = _WINDOWS_WINDOW_SCRIPT.replace("[uint32]PID", "[uint32]%d" % int(pid or 0))
    script = script.replace("HINT", _quote(title_hint))
    return [_powershell(script)]


def _ints(text):
    found = []
    for chunk in (text or "").replace("x", " ").replace(",", " ").split():
        if chunk.lstrip("-").isdigit():
            found.append(int(chunk))
    return found


def parse_window_rect(system, outputs):
    """The window rectangle from the native command's output, or None
    when it reported none. `source` names the call the rectangle came
    from, exactly as the platform's own report does."""
    _checked(system)
    if system == LINUX:
        return None
    if system == MACOS:
        if len(outputs) < 2:
            return None
        position = _ints(outputs[0])
        size = _ints(outputs[1])
        if len(position) < 2 or len(size) < 2:
            return None
        minimized = False
        if len(outputs) > 2:
            minimized = outputs[2].strip().lower() == "true"
        return {"x": position[0], "y": position[1], "w": size[0], "h": size[1],
                "minimized": minimized, "source": _MACOS_WINDOW_SOURCE}
    text = outputs[0] if outputs else ""
    if "rect=none" in text:
        return None
    found = None
    for line in text.splitlines():
        if "rect=" not in line:
            continue
        numbers = _ints(line.split("rect=", 1)[1].split("source=")[0])
        if len(numbers) == 4:
            found = numbers
            break
    if found is None:
        return None
    source = _WINDOWS_SOURCE if _WINDOWS_SOURCE in text else "GetWindowRect"
    minimized = "minimized=True" in text
    return {"x": found[0], "y": found[1], "w": found[2], "h": found[3],
            "minimized": minimized, "source": source}


def window_rect(system=None, *, process_name="UltiMaker-Cura", pid=0, title_hint="Cura"):
    """Run the native window query and return the parsed rectangle
    (None when this platform needs no native query — Linux — or when
    the platform reported no window). Impure by design."""
    system = current_system() if system is None else _checked(system)
    outputs = []
    for command in window_rect_commands(system, process_name=process_name, pid=pid,
                                        title_hint=title_hint):
        try:
            done = subprocess.run(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=60)
            outputs.append(done.stdout.decode("utf-8", "replace"))
        except (OSError, subprocess.SubprocessError):
            outputs.append("")
    return parse_window_rect(system, outputs)


# ─── the display the capture covers ──────────────────────────────


# [System.Windows.Forms.Screen]::PrimaryScreen.Bounds is cached for the
# process lifetime and does NOT follow a display change, so it is never
# read here: SystemInformation.VirtualScreen returns integers and
# refreshes.
_WINDOWS_DISPLAY_SCRIPT = ("Add-Type -AssemblyName System.Windows.Forms; "
                           "$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
                           "Write-Output ('display={0}x{1}' -f $vs.Width, $vs.Height)")


def display_size_commands(system):
    """The commands that read the display size, where a platform has
    one. Linux returns none: its display is the Xvfb screen the
    launcher created at HARNESS_GEOMETRY, so the reading would only
    repeat the setting."""
    _checked(system)
    if system == LINUX:
        return []
    if system == MACOS:
        return [["system_profiler", "SPDisplaysDataType"]]
    return [_powershell(_WINDOWS_DISPLAY_SCRIPT)]


def parse_display_size(system, text):
    """The display's pixel size, or None when the reading gave none.
    On macOS the reading is advisory — avfoundation records the display
    it is given, so the recorded frame is what the run is actually
    sized by."""
    _checked(system)
    text = text or ""
    if system == WINDOWS:
        for line in text.splitlines():
            if line.startswith("display="):
                numbers = _ints(line.split("display=", 1)[1])
                if len(numbers) >= 2:
                    return (numbers[0], numbers[1])
        return None
    if system == MACOS:
        for line in text.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("resolution"):
                numbers = _ints(line.split(":", 1)[1])
                if len(numbers) >= 2:
                    return (numbers[0], numbers[1])
        return None
    return None


# ─── the CLI bridge ──────────────────────────────────────────────
# A native launcher is a shell script: these subcommands are how it
# reaches the same single source of truth, never a second branch of
# its own.


def _build_parser():
    parser = argparse.ArgumentParser(description="the harness's platform dispatch")
    parser.add_argument("--system", choices=SYSTEMS, default=None,
                        help="the platform to dispatch for (default: this host)")
    sub = parser.add_subparsers(dest="command", required=True)

    still = sub.add_parser("capture-input", help="print the ffmpeg input spec as JSON")
    still.add_argument("--size", required=True)
    still.add_argument("--display", default=None)
    still.add_argument("--screen-index", default=None)
    still.add_argument("--framerate", default=None)

    sub.add_parser("screen-index", help="print the avfoundation screen device index")

    window = sub.add_parser("window-rect", help="print Cura's window rectangle as JSON")
    window.add_argument("--process", dest="process_name", default="UltiMaker-Cura")
    window.add_argument("--pid", type=int, default=0)
    window.add_argument("--title-hint", default="Cura")

    sub.add_parser("display-size", help="print the display size as WxH")

    launch = sub.add_parser("launch", help="start Cura and print pid=N")
    launch.add_argument("--binary", required=True)
    launch.add_argument("--working-dir", default=None)
    launch.add_argument("--stdout", default=None)
    launch.add_argument("--stderr", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    system = current_system() if args.system is None else args.system
    if args.command == "capture-input":
        print(json.dumps(capture_input_args(system, args.size, display=args.display,
                                            screen_index=args.screen_index,
                                            framerate=args.framerate)))
        return 0
    if args.command == "screen-index":
        index = screen_index()
        print(DEFAULT_SCREEN_INDEX if index is None else index)
        return 0
    if args.command == "window-rect":
        rect = window_rect(system, process_name=args.process_name, pid=args.pid,
                           title_hint=args.title_hint)
        print(json.dumps(rect))
        return 0 if rect else 1
    if args.command == "display-size":
        size = parse_display_size(system, "".join(
            _capture(command) for command in display_size_commands(system)))
        if size is None:
            return 1
        print("%dx%d" % size)
        return 0
    command = launch_command(system, args.binary, working_dir=args.working_dir,
                             stdout=args.stdout, stderr=args.stderr)
    # Detached: the launcher is a shell script that must not wait for
    # Cura, and the run's teardown stops it through the pid printed
    # here.
    child = subprocess.Popen(command["argv"], cwd=command["cwd"],
                             start_new_session=True)
    print("pid=%d" % child.pid)
    return 0


def _capture(command):
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=120)
        return done.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


if __name__ == "__main__":
    sys.exit(main())
