"""The platform dispatch's pins, host-safe on any platform.

tests/harness/native_host.py chooses the capture device, the
recorder's output flags, and the native launch/window calls. All three
platforms are reachable from this one — the dispatch takes the platform
name as an argument — so every branch below is pinned by a test that
runs anywhere, and the Linux branch additionally against the literal
argv the harness used before the dispatch existed (a platform that
changes Linux's capture by a byte breaks the first tests here).

Run directly (like the other harness files):
`python3 tests/harness/test_harness_native.py`.
"""

import json
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import native_host

SIZE = "1920x1080"
DISPLAY = ":99"
STILL_PATH = "/run/00-boot.png"
VIDEO_PATH = "/run/scenario.mp4"

# The capture argv as it stood before the dispatch existed: the Linux
# run must be byte-identical to these.
LINUX_STILL = ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
               "-video_size", SIZE, "-i", DISPLAY, "-frames:v", "1", STILL_PATH]
LINUX_RECORDER = ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
                  "-video_size", SIZE, "-framerate", "15", "-i", DISPLAY,
                  VIDEO_PATH]


class SystemNameTests(unittest.TestCase):
    def test_sys_platform_maps_to_the_three_names(self):
        self.assertEqual(native_host.current_system("linux"), native_host.LINUX)
        self.assertEqual(native_host.current_system("darwin"), native_host.MACOS)
        self.assertEqual(native_host.current_system("win32"), native_host.WINDOWS)

    def test_unknown_system_is_refused(self):
        # A platform nobody dispatched for must not silently get the
        # Linux device.
        with self.assertRaises(ValueError):
            native_host.capture_input_args("plan9", SIZE, display=DISPLAY)


class LinuxIsUnchangedTests(unittest.TestCase):
    def test_still_argv_is_byte_identical(self):
        self.assertEqual(
            native_host.still_argv(native_host.LINUX, STILL_PATH, size=SIZE,
                                   display=DISPLAY),
            LINUX_STILL)

    def test_recorder_argv_is_byte_identical(self):
        self.assertEqual(
            native_host.recorder_argv(native_host.LINUX, VIDEO_PATH, size=SIZE,
                                      display=DISPLAY),
            LINUX_RECORDER)

    def test_the_recorder_keeps_its_framerate_argument(self):
        # scenario8 records at 5 fps; the framerate is an INPUT option
        # and must stay where it always was — between -video_size and -i.
        self.assertEqual(
            native_host.recorder_argv(native_host.LINUX, VIDEO_PATH, size=SIZE,
                                      display=DISPLAY, framerate=5),
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
             "-framerate", "5", "-i", DISPLAY, VIDEO_PATH])

    def test_linux_adds_no_recorder_flags(self):
        self.assertEqual(native_host.recorder_output_args(native_host.LINUX), [])

    def test_linux_without_a_display_is_refused(self):
        with self.assertRaises(ValueError):
            native_host.capture_input_args(native_host.LINUX, SIZE)


class MacosCaptureTests(unittest.TestCase):
    def test_the_input_spec_is_avfoundation_on_the_probed_index(self):
        self.assertEqual(
            native_host.capture_input_args(native_host.MACOS, SIZE,
                                           screen_index="3", framerate=15),
            ["-f", "avfoundation", "-video_size", SIZE,
             "-framerate", "15", "-i", "3:none"])

    def test_the_screen_index_is_required_to_be_the_probed_one(self):
        self.assertEqual(
            native_host.capture_input_args(native_host.MACOS, SIZE,
                                           screen_index="0"),
            ["-f", "avfoundation", "-video_size", SIZE, "-i", "0:none"])

    def test_a_missing_index_falls_back_to_the_conventional_screen(self):
        self.assertEqual(
            native_host.capture_input_args(native_host.MACOS, SIZE),
            ["-f", "avfoundation", "-video_size", SIZE,
             "-i", native_host.DEFAULT_SCREEN_INDEX + ":none"])

    def test_the_recorder_is_fragmented_because_it_is_killed(self):
        self.assertEqual(
            native_host.recorder_argv(native_host.MACOS, VIDEO_PATH, size=SIZE,
                                      screen_index="1"),
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation",
             "-video_size", SIZE, "-framerate", "15", "-i", "1:none",
             "-g", "30", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
             VIDEO_PATH])

    def test_the_still_carries_no_fragmentation(self):
        argv = native_host.still_argv(native_host.MACOS, STILL_PATH, size=SIZE,
                                      screen_index="1")
        self.assertNotIn("-movflags", argv)
        self.assertEqual(argv[-3:], ["-frames:v", "1", STILL_PATH])

    def test_the_device_probe_reads_the_screen_index(self):
        # ffmpeg writes the device list to stderr and exits non-zero —
        # the listing here is that stream.
        probe = "\n".join([
            "[AVFoundation indev @ 0x14f7058b0] AVFoundation video devices:",
            "[AVFoundation indev @ 0x14f7058b0] [0] FaceTime HD Camera",
            "[AVFoundation indev @ 0x14f7058b0] [1] Capture screen 0",
            "[AVFoundation indev @ 0x14f7058b0] AVFoundation audio devices:",
            "[AVFoundation indev @ 0x14f7058b0] [0] MacBook Pro Microphone",
        ])
        self.assertEqual(native_host.avfoundation_screen_index(probe), "1")
        self.assertEqual(native_host.avfoundation_screen_index(""), "1")
        self.assertEqual(
            native_host.avfoundation_screen_index("[2] Capture screen 1"), "2")

    def test_the_device_probe_argv_is_ffmpegs_own_listing(self):
        self.assertEqual(native_host.avfoundation_probe_argv(),
                         ["ffmpeg", "-f", "avfoundation", "-list_devices",
                          "true", "-i", ""])


class WindowsCaptureTests(unittest.TestCase):
    def test_the_input_spec_is_gdigrab_over_the_desktop(self):
        self.assertEqual(
            native_host.capture_input_args(native_host.WINDOWS, SIZE, framerate=15),
            ["-f", "gdigrab", "-video_size", SIZE, "-framerate", "15",
             "-i", "desktop"])

    def test_the_recorder_is_fragmented_because_it_is_killed(self):
        self.assertEqual(
            native_host.recorder_argv(native_host.WINDOWS, VIDEO_PATH, size=SIZE),
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "gdigrab",
             "-video_size", SIZE, "-framerate", "15", "-i", "desktop",
             "-g", "30", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
             VIDEO_PATH])


class WindowRectTests(unittest.TestCase):
    def test_linux_needs_no_native_query(self):
        # The harness reads the window in-process, through the driver's
        # Qt, on every platform.
        self.assertEqual(native_host.window_rect_commands(native_host.LINUX), [])
        self.assertIsNone(native_host.parse_window_rect(native_host.LINUX, []))

    def test_windows_reads_the_dwm_frame_bounds(self):
        # The rectangle the compositor draws; DWMWA_EXTENDED_FRAME_BOUNDS
        # is 9. GetWindowRect is only the fallback for a window DWM does
        # not compose.
        script = native_host.window_rect_commands(native_host.WINDOWS, pid=4242,
                                                  title_hint="Cura")[0][-1]
        self.assertIn("DwmGetWindowAttribute", script)
        self.assertIn("9, out rect", script)
        self.assertIn("GetWindowRect", script)
        self.assertIn("Report([uint32]4242, 'Cura')", script)

    def test_windows_parses_the_reported_rectangle(self):
        # The report is x,y,width,height — not right/bottom — and the
        # DWM frame bounds are the OUTSIDE of the window: a full-screen
        # window on a 1920-wide display starts at -8 and measures 1928.
        text = ("rect=-8,0,1928,1048 "
                "source=DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) "
                "minimized=False title=[UltiMaker Cura]")
        self.assertEqual(native_host.parse_window_rect(native_host.WINDOWS, [text]),
                         {"x": -8, "y": 0, "w": 1928, "h": 1048, "minimized": False,
                          "source": "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)"})

    def test_windows_reports_a_minimised_window_and_a_missing_one(self):
        text = ("rect=120,60,1840,1040 source=GetWindowRect "
                "minimized=True title=[UltiMaker Cura]")
        parsed = native_host.parse_window_rect(native_host.WINDOWS, [text])
        self.assertTrue(parsed["minimized"])
        self.assertEqual(parsed["source"], "GetWindowRect")
        self.assertIsNone(
            native_host.parse_window_rect(native_host.WINDOWS, ["rect=none"]))

    def test_macos_reads_position_size_and_minimised(self):
        commands = native_host.window_rect_commands(native_host.MACOS,
                                                    process_name="UltiMaker-Cura")
        self.assertEqual([command[0] for command in commands], ["osascript"] * 3)
        self.assertIn('process "UltiMaker-Cura"', commands[0][-1])
        self.assertIn("position of window 1", commands[0][-1])
        self.assertIn("size of window 1", commands[1][-1])
        self.assertIn("AXMinimized", commands[2][-1])

    def test_macos_parses_the_three_readings(self):
        parsed = native_host.parse_window_rect(
            native_host.MACOS, ["0, 0\n", "1840, 1040\n", "false\n"])
        self.assertEqual(parsed, {"x": 0, "y": 0, "w": 1840, "h": 1040,
                                  "minimized": False,
                                  "source": "System Events (osascript)"})
        self.assertTrue(native_host.parse_window_rect(
            native_host.MACOS, ["0, 0", "800, 600", "true"])["minimized"])
        # An ungranted System Events answers with prose, not a point.
        self.assertIsNone(native_host.parse_window_rect(
            native_host.MACOS, ["execution error: not allowed", "", ""]))


class DisplaySizeTests(unittest.TestCase):
    def test_windows_reads_the_virtual_screen(self):
        # PrimaryScreen.Bounds is cached for the process lifetime and
        # does not follow a display change, so it must never be the
        # reading; VirtualScreen returns integers and refreshes.
        script = native_host.display_size_commands(native_host.WINDOWS)[0][-1]
        self.assertIn("SystemInformation]::VirtualScreen", script)
        self.assertNotIn("PrimaryScreen", script)
        self.assertEqual(
            native_host.parse_display_size(native_host.WINDOWS, "display=1920x1080\n"),
            (1920, 1080))
        self.assertIsNone(
            native_host.parse_display_size(native_host.WINDOWS, "display=unreadable"))

    def test_macos_reads_the_displays_reported_resolution(self):
        text = "\n".join([
            "Graphics/Displays:",
            "",
            "    Apple M1:",
            "",
            "      Displays:",
            "        Color LCD:",
            "          Display Type: Built-In Retina LCD",
            "          Resolution: 3024 x 1964 Retina",
        ])
        self.assertEqual(native_host.parse_display_size(native_host.MACOS, text),
                         (3024, 1964))
        self.assertEqual(
            native_host.display_size_commands(native_host.MACOS)[0][0], "system_profiler")

    def test_linux_reads_none_because_its_launcher_sets_the_screen(self):
        self.assertEqual(native_host.display_size_commands(native_host.LINUX), [])
        self.assertIsNone(
            native_host.parse_display_size(native_host.LINUX, "1920x1080"))


class LaunchTests(unittest.TestCase):
    def test_macos_launches_from_the_bundle_directory(self):
        binary = "/Applications/UltiMaker Cura.app/Contents/MacOS/UltiMaker-Cura"
        command = native_host.launch_command(native_host.MACOS, binary)
        self.assertEqual(command["argv"], [binary])
        self.assertEqual(command["cwd"], "/Applications/UltiMaker Cura.app/Contents/MacOS")

    def test_windows_launches_through_start_process(self):
        command = native_host.launch_command(
            native_host.WINDOWS, r"C:\Program Files\UltiMaker Cura\UltiMaker-Cura.exe",
            stdout=r"C:\run\cura-stdout.txt", stderr=r"C:\run\cura-stderr.txt")
        self.assertEqual(command["argv"][:3], ["powershell", "-NoProfile", "-NonInteractive"])
        script = command["argv"][-1]
        self.assertIn("Start-Process -FilePath", script)
        # Single-quoted so a path with a space or an apostrophe survives
        # PowerShell's own parsing.
        self.assertIn("-FilePath 'C:\\Program Files\\UltiMaker Cura\\UltiMaker-Cura.exe'", script)
        self.assertIn("-RedirectStandardOutput 'C:\\run\\cura-stdout.txt'", script)
        self.assertIn("-RedirectStandardError 'C:\\run\\cura-stderr.txt'", script)
        self.assertTrue(script.endswith("Write-Output ('pid=' + $p.Id)"))

    def test_a_quoted_path_cannot_break_out_of_the_script(self):
        script = native_host.launch_command(
            native_host.WINDOWS, r"C:\a'b\Cura.exe")["argv"][-1]
        self.assertIn(r"'C:\a''b\Cura.exe'", script)

    def test_linux_launches_the_binary_directly(self):
        command = native_host.launch_command(native_host.LINUX, "/opt/cura/UltiMaker-Cura")
        self.assertEqual(command["argv"], ["/opt/cura/UltiMaker-Cura"])

    def test_the_launched_pid_is_read_from_the_report(self):
        self.assertEqual(native_host.parse_launch_pid(native_host.WINDOWS, "pid=4242"),
                         4242)
        self.assertIsNone(native_host.parse_launch_pid(native_host.WINDOWS, "no pid"))


class DispatchIsInOnePlaceTests(unittest.TestCase):
    """The static half: the platform choice lives in native_host.py, and
    the two sides of the RPC rendezvous agree on one variable."""

    def _read(self, *parts):
        return (HERE.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_runner_carries_no_platform_branch_of_its_own(self):
        source = self._read("runner.py")
        for token in ("x11grab", "avfoundation", "gdigrab", "-video_size",
                      "-movflags", "frag_keyframe"):
            self.assertNotIn(token, source, f"runner.py still names {token}")

    def test_no_harness_file_but_the_dispatch_names_a_capture_device(self):
        for name in ("runner.py", "scenarios.py", "simulator.py", "surface_coverage.py",
                     "scenario_map.py", "driver/__init__.py"):
            source = self._read(*name.split("/"))
            for token in ("x11grab", "avfoundation", "gdigrab"):
                self.assertNotIn(token, source, f"{name} still names {token}")

    def test_both_sides_derive_the_rpc_paths_from_one_variable(self):
        for name in ("runner.py", "driver/__init__.py"):
            source = self._read(*name.split("/"))
            self.assertIn('os.environ.get("HARNESS_RPC_DIR", "/tmp/mpf")', source)
            self.assertIn('os.path.join(RPC_DIR, "harness_port.txt")', source)
            self.assertIn('os.path.join(RPC_DIR, "harness_token.txt")', source)
            # No other spelling of the rendezvous may survive, or the
            # two sides can drift apart again.
            self.assertNotIn('"/tmp/mpf/harness_port.txt"', source)
            self.assertNotIn('"/tmp/mpf/harness_token.txt"', source)

    def test_the_container_stages_the_dispatch_beside_the_runner(self):
        # The staged runner is a FLAT copy: without this the container
        # run dies on the import.
        script = HERE.parents[1].joinpath("tools", "ui_test.sh").read_text(encoding="utf-8")
        self.assertIn('cp "$root/tests/harness/runner.py" "$WORK_DIR"/harness_runner.py', script)
        self.assertIn('cp "$root/tests/harness/native_host.py" "$WORK_DIR"/native_host.py', script)


class CliTests(unittest.TestCase):
    def test_the_cli_answers_for_a_named_platform(self):
        done = subprocess.run(
            [sys.executable, str(HERE / "native_host.py"), "--system", "windows",
             "capture-input", "--size", SIZE, "--framerate", "15"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(done.stdout.decode("utf-8")),
                         ["-f", "gdigrab", "-video_size", SIZE, "-framerate", "15",
                          "-i", "desktop"])

    def test_the_cli_dispatches_for_this_host_by_default(self):
        done = subprocess.run(
            [sys.executable, str(HERE / "native_host.py"), "capture-input",
             "--size", SIZE, "--display", DISPLAY],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(done.stdout.decode("utf-8")),
                         ["-f", "x11grab", "-video_size", SIZE, "-i", DISPLAY])


if __name__ == "__main__":
    unittest.main()
