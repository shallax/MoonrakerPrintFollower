"""Bounded STL parsing and real disposable-child STEP failure handling."""

import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from mpf.toolhead import ToolheadImport


ASCII_STL = b"""solid nozzle

facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 2 0 0
vertex 0 2 0
endloop
endfacet
endsolid nozzle
"""

# This is intentionally a separate Python process, not a fake Popen object.
# It exercises opaque conversion stalls and arbitrary failures independently
# of installing OpenCASCADE into the CI runner.
CHILD_SCRIPT = '''
from pathlib import Path
import os
import struct
import sys
import time
snapshot, runtime, output = sys.argv[1:]
mode = Path(snapshot).read_text().splitlines()[1]
Path(runtime, "started").write_text(str(os.getpid()))
if mode == "progress":
    Path(output + ".progress").write_text("Building CAD geometry…", encoding="utf-8")
    deadline = time.monotonic() + 5
    while not Path(runtime, "continue").exists():
        if time.monotonic() > deadline: raise RuntimeError("Parent did not receive progress")
        time.sleep(.01)
if mode == "stall": time.sleep(30)
if mode == "diagnostic-stall":
    sys.stdout.write("x" * 70000); sys.stdout.flush(); time.sleep(30)
if mode == "diagnostic-exit":
    sys.stdout.write("x" * 70000); sys.stdout.flush(); sys.exit(0)
if mode == "oversized":
    with open(output, "wb") as handle:
        handle.seek(56000012); handle.write(b"x")
    time.sleep(30)
if mode == "error":
    print("CAD conversion failed precisely", file=sys.stderr); sys.exit(2)
if mode == "crash": os._exit(42)
if mode == "short-header": Path(output).write_bytes(b"MPF"); sys.exit(0)
magic, count = b"MPFHEAD1", 1
if mode == "wrong-magic": magic = b"WRONG000"
if mode == "zero-count": count = 0
if mode == "excess-count": count = 1000001
values = [0,0,0, 2,0,0, 0,2,0, .2,.4,.6,1]
if mode == "nan": values[0] = float("nan")
body = struct.pack("<13f", *values)
if mode == "short-body": body = body[:-1]
if mode == "long-body": body += b"x"
Path(output).write_bytes(struct.pack("<8sI", magic, count) + body)
'''


class ToolheadImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model = self.root / "input.stl"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.child = self.root / "fixture-worker.py"
        self.child.write_text(CHILD_SCRIPT)
        self.spawned = []
        self.calls = []

    def stl(self, body, cancelled=None):
        self.model.write_bytes(body)
        return ToolheadImport.read_stl(self.model, cancelled)

    def child_popen(self, args, **options):
        self.calls.append((args, options))
        # Keep the production flags, argument layout, environment, private cwd
        # and stream ownership. Replace only the interpreter and converter so
        # this test can run offline on any supported development host.
        # Windows venv python.exe launches another process; killing its wrapper
        # races that child's file-handle cleanup. Production uses a direct helper.
        executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
        process = self.real_popen([executable, *args[1:3], str(self.child), *args[4:]], **options)
        self.spawned.append(process)
        if "diagnostic-exit" in Path(args[4]).read_text():
            process.wait(timeout=5)  # deterministic completed-before-first-poll case
        return process

    def step(self, mode, cancelled=None, progress=None):
        self.model.write_text("ISO-10303-21;\n" + mode + "\n")
        self.real_popen = subprocess.Popen
        with patch.object(ToolheadImport.subprocess, "Popen", side_effect=self.child_popen):
            return ToolheadImport.read_step(self.model, str(self.runtime),
                                            cancelled or threading.Event(), progress=progress)

    def assert_reaped(self):
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(int((self.runtime / "started").read_text()), self.spawned[0].pid,
                         "the fixture must launch the worker directly, without a wrapper")
        self.assertIsNotNone(self.spawned[0].returncode)
        self.assertIsNotNone(self.spawned[0].poll())
        self.assertFalse(Path(self.calls[0][1]["cwd"]).exists())

    def test_ascii_and_binary_stl_preserve_millimetre_coordinates(self):
        expected = np.array([[[0, 0, 0], [2, 0, 0], [0, 2, 0]]], dtype=np.float32)
        binary = b"Moonraker nozzle".ljust(80, b"\0") + struct.pack("<I", 1)
        binary += struct.pack("<12fH", 0, 0, 1, *expected.reshape(-1), 0)
        for source in (ASCII_STL, binary):
            with self.subTest(kind=source[:5]):
                mesh = self.stl(source)
                np.testing.assert_array_equal(mesh.triangles, expected)
                self.assertEqual(mesh.automatic_tip, (1, 1, 0))

    def test_ascii_stl_refuses_malformed_truncated_and_nonfinite_facets(self):
        cases = (b"not an STL", ASCII_STL.replace(b"endloop", b"wrong"),
                 ASCII_STL.replace(b"endsolid nozzle", b""), b"solid empty\nendsolid empty\n",
                 ASCII_STL.replace(b"vertex 2 0 0", b"vertex nan 0 0"),
                 ASCII_STL.replace(b"vertex 2 0 0", b"vertex 1e100 0 0"),
                 ASCII_STL.replace(b"nozzle", b"\xff"),
                 ASCII_STL + b"facet normal 0 0 1\n")
        for body in cases:
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                self.stl(body)

    def test_binary_and_ascii_triangle_limits_are_enforced_before_mesh_adoption(self):
        binary = b"binary STL".ljust(80, b"\0") + struct.pack("<I", 2) + bytes(100)
        with patch.object(ToolheadImport, "MAX_TRIANGLES", 1):
            with self.assertRaisesRegex(ValueError, "triangle"):
                self.stl(binary)
            two = ASCII_STL.replace(b"endsolid nozzle", ASCII_STL.split(b"\n", 1)[1])
            with self.assertRaisesRegex(ValueError, "triangle"):
                self.stl(two)

    def test_cancelled_stl_never_publishes_a_mesh(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.stl(ASCII_STL, cancelled)

    def test_ascii_stl_cancellation_between_chunks_is_observed(self):
        checks = iter((False, True))
        cancellation = type("Cancellation", (), {"is_set": lambda _self: next(checks)})()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.stl(ASCII_STL, cancellation)

    def test_source_size_is_bounded_even_if_file_grows(self):
        self.model.write_bytes(b"x" * 65)
        with patch.object(ToolheadImport, "MAX_FILE_BYTES", 64):
            with self.assertRaisesRegex(ValueError, "128 MiB"):
                ToolheadImport.read_model_bytes(self.model)
            with patch.object(ToolheadImport, "read_model_bytes", return_value=b"x" * 65):
                with self.assertRaisesRegex(ValueError, "128 MiB"):
                    ToolheadImport.read_stl(self.model)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files unavailable")
    def test_fifo_source_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.root / "model-fifo.stl"
        os.mkfifo(fifo)
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "regular model file"):
            ToolheadImport.read_model_bytes(fifo)
        self.assertLess(time.monotonic() - started, 1)

    def test_step_header_is_checked_before_spawning(self):
        self.model.write_bytes(ASCII_STL)
        with patch.object(ToolheadImport.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "Part 21"):
                ToolheadImport.read_step(self.model, str(self.runtime), threading.Event())
        popen.assert_not_called()

    def test_child_returns_canonical_mesh_with_isolated_flags_and_environment(self):
        with patch.dict(os.environ, {"PYTHONPATH": "untrusted", "DYLD_LIBRARY_PATH": "untrusted",
                                     "LD_PRELOAD": "untrusted", "QT_PLUGIN_PATH": "untrusted",
                                     "MPF_TEST_MARKER": "retained"}):
            mesh = self.step("success")
        self.assertEqual(len(mesh.triangles), 1)
        np.testing.assert_allclose(mesh.colours[0], (.2, .4, .6, 1))
        args, options = self.calls[0]
        self.assertEqual(args[1:3], ["-I", "-B"])
        self.assertTrue(args[3].endswith("StepWorker.py"))
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["env"]["MPF_TEST_MARKER"], "retained")
        for name in ("PYTHONPATH", "DYLD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH"):
            self.assertNotIn(name, options["env"])
        self.assertNotEqual(args[4], str(self.model))  # immutable private snapshot
        self.assert_reaped()

    def test_pre_cancelled_step_does_not_spawn(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.step("stall", cancelled)
        self.assertEqual(self.spawned, [])

    def test_cancellation_terminates_and_reaps_an_opaque_native_stall(self):
        cancelled = threading.Event()

        def cancel_after_started():
            deadline = time.monotonic() + 5
            while not (self.runtime / "started").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            cancelled.set()

        thread = threading.Thread(target=cancel_after_started)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, "cancelled"):
                self.step("stall", cancelled)
        finally:
            thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assert_reaped()

    def test_conversion_survives_elapsed_time_and_reports_real_stage_before_completion(self):
        seen = []
        clock = iter(range(0, 1000000, 600))
        def progress(text):
            seen.append(text)
            if text == "Building CAD geometry…": (self.runtime / "continue").touch()
        # Advancing the parent's clock by ten minutes per poll must not kill
        # healthy work. The child has its own test-only completion deadline.
        with patch.object(ToolheadImport.time, "monotonic", side_effect=lambda: next(clock)):
            mesh = self.step("progress", progress=progress)
        self.assertEqual(len(mesh.triangles), 1)
        self.assertEqual(seen, ["Building CAD geometry…", "Checking display mesh…"])
        self.assert_reaped()

    def test_excessive_diagnostics_are_bounded_for_running_and_exited_children(self):
        for mode in ("diagnostic-stall", "diagnostic-exit"):
            with self.subTest(mode=mode):
                self.spawned.clear()
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, "excessive diagnostics"):
                    self.step(mode)
                self.assert_reaped()

    def test_oversized_mesh_terminates_the_running_child(self):
        with self.assertRaisesRegex(ValueError, "mesh limit"):
            self.step("oversized")
        self.assert_reaped()

    def test_parser_error_and_abnormal_exit_are_reported_without_adoption(self):
        for mode, error in (("error", "CAD conversion failed precisely"),
                            ("crash", "could not convert")):
            with self.subTest(mode=mode):
                self.spawned.clear()
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, error):
                    self.step(mode)
                self.assert_reaped()

    def test_child_output_is_validated_before_constructing_a_mesh(self):
        cases = (("short-header", "truncated mesh"), ("wrong-magic", "invalid mesh"),
                 ("zero-count", "invalid mesh"), ("excess-count", "invalid mesh"),
                 ("short-body", "invalid mesh length"), ("long-body", "invalid mesh length"),
                 ("nan", "out-of-range"))
        for mode, error in cases:
            with self.subTest(mode=mode):
                self.spawned.clear()
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, error):
                    self.step(mode)
                self.assert_reaped()

    @unittest.skipUnless(os.environ.get("MPF_CAD_INTEGRATION_PYTHON")
                         and os.environ.get("MPF_CAD_INTEGRATION_RUNTIME"),
                         "Native CAD check needs a cp312 interpreter and the verified optional runtime")
    def test_real_step_worker_units_colours_and_cli_with_separate_coverage(self):
        # The normal suite uses its host ABI. This explicit integration leg
        # instead runs cp312 with the real upstream native libraries. When the
        # parent suite is measured, save a distinct child coverage part using
        # the native runner's existing cov.*.coverage collection convention.
        repository = Path(__file__).resolve().parents[1]
        driver = self.root / "native-worker-check.py"
        driver.write_text('''
import pathlib, runpy, struct, sys, tempfile
repository, runtime, coverage_file = sys.argv[1:]
repository = pathlib.Path(repository)
if coverage_file:
    import coverage
    measured = coverage.Coverage(data_file=coverage_file, source=[str(repository / "mpf")])
    measured.start()
worker_path = str(repository / "mpf/toolhead/StepWorker.py")
worker = runpy.run_path(worker_path)
with tempfile.TemporaryDirectory() as scratch:
    output = pathlib.Path(scratch) / "model.mesh"
    results = []
    for name in ("assembly.step", "assembly-inch.step"):
        worker["convert"](str(repository / "tests/fixtures/toolhead" / name), runtime, str(output))
        status = pathlib.Path(str(output) + ".progress").read_text(encoding="utf-8")
        assert status.startswith("Writing display mesh") and "triangles" in status, status
        body = output.read_bytes()
        magic, count = struct.unpack_from("<8sI", body)
        assert magic == b"MPFHEAD2" and count > 0 and len(body) == 12 + count * 56
        values = struct.unpack_from("<" + str(count * 13) + "f", body, 12)
        xyz, colours = values[:count * 9], values[count * 9:]
        bounds = tuple((min(xyz[i::3]), max(xyz[i::3])) for i in range(3))
        palette = {tuple(round(v, 5) for v in colours[i:i+4]) for i in range(0, len(colours), 4)}
        assert len(palette) == 4, palette
        assert abs(bounds[0][1] - 22) < .01 and abs(bounds[2][1] - 9) < .01, bounds
        results.append((bounds, palette))
    for mm, inch in zip(results[0][0], results[1][0]):
        assert all(abs(a-b) < .01 for a,b in zip(mm, inch)), (mm, inch)
    assert results[0][1] == results[1][1]
    invalid = pathlib.Path(scratch) / "invalid.step"
    invalid.write_text("ISO-10303-21;\\ninvalid syntax")
    try:
        worker["convert"](str(invalid), runtime, str(output))
    except ValueError:
        pass
    else:
        raise AssertionError("Native parser accepted malformed STEP")
    sys.argv = [worker_path]
    try:
        runpy.run_path(worker_path, run_name="__main__")
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("Invalid CLI arguments were accepted")
    sys.argv = [worker_path, str(repository / "tests/fixtures/toolhead/assembly.step"), runtime, str(output)]
    runpy.run_path(worker_path, run_name="__main__")
if coverage_file:
    measured.stop()
    measured.save()
print("Native STEP units, instance placement, colours, parser failure and CLI checks passed")
''')
        parent_coverage = os.environ.get("COVERAGE_FILE", "")
        child_coverage = ""
        if parent_coverage:
            path = Path(parent_coverage)
            child_coverage = str(path.with_name(path.stem + "-step-worker.coverage"))
        result = subprocess.run([os.environ["MPF_CAD_INTEGRATION_PYTHON"], "-I", "-B", str(driver),
                                 str(repository), os.environ["MPF_CAD_INTEGRATION_RUNTIME"], child_coverage],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=30, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CLI checks passed", result.stdout)
        if child_coverage:
            self.assertTrue(Path(child_coverage).is_file())


if __name__ == "__main__":
    unittest.main()
