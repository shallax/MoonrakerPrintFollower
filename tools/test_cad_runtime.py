"""Download the pinned CAD reader and exercise real STEP units, instances and colours."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from mpf.toolhead.CadRuntime import install_runtime
from mpf.toolhead.ToolheadImport import read_step


WORKER_COVERAGE_BOOTSTRAP = '''
import json, os, pathlib, runpy, sys, tempfile, trace
worker_path, runtime, fixture, counts_path = sys.argv[1:]
fixture = pathlib.Path(fixture)
def exercise():
    worker = runpy.run_path(worker_path)
    with tempfile.TemporaryDirectory() as scratch:
        output = str(pathlib.Path(scratch) / "model.mesh")
        for name in ("assembly.step", "assembly-inch.step"):
            worker["convert"](str(fixture / name), runtime, output)
        invalid = pathlib.Path(scratch) / "invalid.step"
        invalid.write_text("not a STEP model")
        try:
            worker["convert"](str(invalid), runtime, output)
        except ValueError:
            pass
        else:
            raise AssertionError("Missing Part 21 header was accepted")
        invalid.write_text("ISO-10303-21;\\ninvalid syntax")
        try:
            worker["convert"](str(invalid), runtime, output)
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed native STEP was accepted")
        sys.argv = [worker_path]
        try:
            runpy.run_path(worker_path, run_name="__main__")
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("Invalid CLI arguments were accepted")
        sys.argv = [worker_path, str(fixture / "assembly.step"), runtime, output]
        runpy.run_path(worker_path, run_name="__main__")
counter = trace.Trace(count=True, trace=False, ignoredirs=[runtime, sys.base_prefix])
counter.runfunc(exercise)
target = os.path.normcase(os.path.abspath(worker_path))
lines = sorted({line for (filename, line), count in counter.results().counts.items()
                if count and os.path.normcase(os.path.abspath(filename)) == target})
pathlib.Path(counts_path).write_text(json.dumps(lines))
'''


def measure_worker_coverage(runtime, fixture, output):
    """Trace actual native calls in the pinned helper; coverage stays in host Python."""
    import coverage
    worker = ROOT / "mpf" / "toolhead" / "StepWorker.py"
    executable = (Path(runtime) / "python" / "python.exe" if os.name == "nt"
                  else Path(runtime) / "python" / "bin" / "python3.12")
    with tempfile.TemporaryDirectory(prefix="mpf-worker-coverage-") as scratch:
        bootstrap = Path(scratch) / "trace-worker.py"
        counts = Path(scratch) / "executed-lines.json"
        bootstrap.write_text(WORKER_COVERAGE_BOOTSTRAP)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PYTHON", "DYLD_", "LD_", "QT_"))}
        subprocess.run([str(executable), "-I", "-B", str(bootstrap), str(worker),
                        runtime, str(fixture), str(counts)],
                       stdin=subprocess.DEVNULL, check=True, timeout=180, cwd=scratch, env=env)
        lines = json.loads(counts.read_text())
        if not lines or not all(isinstance(line, int) and line > 0 for line in lines):
            raise AssertionError("STEP helper returned invalid execution counts")
    measured = coverage.Coverage(data_file=str(output), source=[str(ROOT / "mpf")])
    measured.get_data().add_lines({str(worker): lines})
    measured.save()
    percent = measured.report(include=[str(worker)], show_missing=True)
    if percent < 95:
        raise AssertionError(f"StepWorker coverage {percent:.2f}% is below its 95% gate")
    print(f"PASS: isolated StepWorker coverage {percent:.2f}% (95% required); data: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=Path(tempfile.gettempdir()) / "mpf" / "cad-smoke")
    parser.add_argument("--coverage-worker", action="store_true",
                        help="measure the real isolated STEP helper and require at least 95%% coverage")
    parser.add_argument("--coverage-output", type=Path,
                        help="helper coverage data file (default: runtime-root/step-worker.coverage)")
    args = parser.parse_args()
    cancelled = threading.Event()
    runtime = install_runtime(str(args.runtime_root), cancelled, print)
    fixture = ROOT / "tests" / "fixtures" / "toolhead"
    mm = read_step(str(fixture / "assembly.step"), runtime, cancelled)
    inch = read_step(str(fixture / "assembly-inch.step"), runtime, cancelled)
    for name, mesh in (("millimetres", mm), ("inches", inch)):
        points = mesh.triangles.reshape(-1, 3)
        colours = np.unique(np.round(mesh.colours[:, :3], 5), axis=0)
        if len(colours) != 4:
            raise AssertionError(f"{name}: expected four preserved STEP face/instance colours")
        print(f"{name}: {len(mesh.triangles)} triangles, {len(colours)} colours, bounds {points.min(axis=0)} .. {points.max(axis=0)}")
    # Fixed fixture extent proves that repeated instances retain their placement.
    np.testing.assert_allclose(mm.triangles.max(axis=(0, 1)), (22, 3, 9), atol=0.01)
    np.testing.assert_allclose(mm.triangles.min(axis=(0, 1))[[0, 2]], (-1.5, 0), atol=0.01)
    np.testing.assert_allclose(inch.triangles.min(axis=(0, 1)), mm.triangles.min(axis=(0, 1)), atol=0.01)
    np.testing.assert_allclose(inch.triangles.max(axis=(0, 1)), mm.triangles.max(axis=(0, 1)), atol=0.01)
    np.testing.assert_allclose(np.sort(np.unique(inch.colours, axis=0), axis=0),
                               np.sort(np.unique(mm.colours, axis=0), axis=0), atol=0.001)
    print("PASS: pinned CAD runtime, STEP millimetre/inch units, assembly transforms and source colours")
    if args.coverage_worker:
        output = args.coverage_output or args.runtime_root / "step-worker.coverage"
        measure_worker_coverage(runtime, fixture, output)


if __name__ == "__main__":
    main()
