"""Local STL and XCAF STEP conversion; no scene or configuration mutations."""
from __future__ import annotations

import os
import io
from array import array
import stat
import subprocess
import tempfile
import time
from pathlib import Path
import struct
import numpy as np
from ..geometry.ToolheadGeometry import MAX_TRIANGLES, mesh_from_arrays, mesh_from_payload
from ..geometry.ToolheadMeshFormat import MAX_METADATA_BYTES, read_payload

MAX_FILE_BYTES = 128 * 1024 * 1024


def read_stl(path, cancelled=None):
    body = read_model_bytes(path)
    if cancelled is not None and cancelled.is_set(): raise ValueError("Model import cancelled")
    if len(body) > MAX_FILE_BYTES: raise ValueError("Model file exceeds 128 MiB")
    if len(body) >= 84:
        count = struct.unpack_from("<I", body, 80)[0]
        if len(body) == 84 + count * 50:
            if count > MAX_TRIANGLES: raise ValueError("Model exceeds 1,000,000 triangles")
            dtype = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
            return mesh_from_arrays(np.frombuffer(body, dtype=dtype, count=count, offset=84)["vertices"])
    # Parse one facet at a time: never build a Python tuple for every
    # vertex before checking limits. Yield between bounded chunks so the
    # settings UI can repaint and cancellation remains responsive.
    vertices = array("f")
    state, count, ended = "solid", 0, False
    try:
        for index, raw in enumerate(io.BytesIO(body)):
            if index % 1024 == 0:
                if cancelled is not None and cancelled.is_set(): raise ValueError("Model import cancelled")
                time.sleep(0)
            tokens = raw.decode("ascii").strip().lower().split()
            if not tokens: continue
            word = tokens[0]
            if state == "solid" and word == "solid": state = "facet"
            elif state == "facet" and word == "endsolid": ended = True; state = "done"
            elif state == "facet" and tokens[:2] == ["facet", "normal"] and len(tokens) == 5:
                if len(vertices)//9 >= MAX_TRIANGLES: raise ValueError("Model exceeds 1,000,000 triangles")
                state = "loop"
            elif state == "loop" and tokens == ["outer", "loop"]: state, count = "vertex", 0
            elif state == "vertex" and word == "vertex" and len(tokens) == 4 and count < 3:
                vertices.extend(float(value) for value in tokens[1:])
                count += 1
                if count == 3: state = "endloop"
            elif state == "endloop" and tokens == ["endloop"]: state = "endfacet"
            elif state == "endfacet" and tokens == ["endfacet"]: state = "facet"
            else: raise ValueError("Invalid STL facet structure")
    except (UnicodeDecodeError, OverflowError):
        raise ValueError("Invalid STL data") from None
    if not ended or not vertices: raise ValueError("Invalid STL facets")
    return mesh_from_arrays(np.frombuffer(vertices, dtype=np.float32))


def read_model_bytes(path):
    # Opening nonblocking prevents FIFO/device input from hanging before fstat.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Choose a regular model file")
        body = source.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES: raise ValueError("Model file exceeds 128 MiB")
    return body


def read_step(path, runtime, cancelled, *, progress=None):
    body = read_model_bytes(path)
    if b"ISO-10303-21" not in body[:4096]: raise ValueError("Expected a STEP/STP Part 21 model")
    with tempfile.TemporaryDirectory(prefix="mpf-step-") as private:
        snapshot, output, log = (os.path.join(private, name) for name in ("model.step", "model.mesh", "reader.log"))
        Path(snapshot).write_bytes(body)
        del body
        executable = os.path.join(runtime, "python", "python.exe") if os.name == "nt" else os.path.join(runtime, "python", "bin", "python3.12")
        worker = str(Path(__file__).with_name("StepWorker.py"))
        # Isolated mode refuses user-site and PYTHONPATH; strip loader and host
        # Python/Qt state as well so Cura's bundled libraries cannot contaminate
        # the independent helper's ABI.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PYTHON", "DYLD_", "LD_", "QT_"))}
        env["PYTHONNOUSERSITE"] = "1"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = None
        with open(log, "wb") as diagnostics:
            try:
                if cancelled.is_set(): raise ValueError("Model import cancelled")
                process = subprocess.Popen([executable, "-I", "-B", worker, snapshot, runtime, output],
                    stdin=subprocess.DEVNULL, stdout=diagnostics, stderr=diagnostics, cwd=private, env=env, **options)
                next_report, previous = 0., ""
                while process.poll() is None:
                    if cancelled.wait(.05): raise ValueError("Model import cancelled")
                    now = time.monotonic()
                    if progress is not None and now >= next_report:
                        next_report = now + .25
                        try:
                            with open(output + ".progress", "r", encoding="utf-8") as status:
                                text = status.read(513).strip()
                        except (OSError, UnicodeError):
                            text = ""
                        if text and len(text) <= 512 and text != previous:
                            progress(text)
                            previous = text
                    if os.path.getsize(log) > 64*1024: raise ValueError("STEP reader produced excessive diagnostics")
                    if os.path.exists(output) and os.path.getsize(output) > MAX_TRIANGLES*64+16+MAX_METADATA_BYTES:
                        raise ValueError("STEP conversion exceeded its mesh limit")
                if process.returncode:
                    with open(log, "rb") as handle:
                        detail = handle.read(1024).decode("utf-8", errors="replace").strip()
                    raise ValueError(detail or "STEP reader could not convert this model")
                if os.path.getsize(log) > 64*1024:
                    raise ValueError("STEP reader produced excessive diagnostics")
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        if cancelled.is_set(): raise ValueError("Model import cancelled")
        if progress is not None: progress("Checking display mesh…")
        with open(output, "rb") as handle:
            _header, body, count, stride, metadata = read_payload(handle, MAX_TRIANGLES)
        return mesh_from_payload(body, count, stride, metadata)
