# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Verify isolated consumer imports, selective distribution boundaries and shared-source parity.

"""Install and exercise a built wheel outside the source checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
if __package__:
    from .library_manifest import source_files
else:
    from library_manifest import source_files

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = b"G90\nM82\n;LAYER:0\nG1 X10 Y10 Z0.2 F1800\n;TYPE:WALL-OUTER\nG1 X40 E1\nG1 Y40 E2\nG1 X10 E3\nG1 Y10 E4\n"

CORE_PROBE = r'''
import sys, math
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import moonraker_toolpath
from moonraker_toolpath.gcode import (build_index_from_bytes, build_index_from_file,
                       hydrate_layer_from_file, prepare_layer, IndexView, motion_edges, split_index)
from moonraker_toolpath.plate import prepare, pack_shader, stroke_geometry, marker_counts, marker_geometry
data = Path(sys.argv[2]).read_bytes()
from moonraker_toolpath import GCodeDocument
document = GCodeDocument.from_bytes(data)
assert document.layer_count == 1 and document.layer_info(0).complete
snapshot = document.geometry(0)
assert snapshot['motions'] == 5
assert not hasattr(moonraker_toolpath, 'register')
assert not hasattr(moonraker_toolpath, 'getMetaData')
index = build_index_from_bytes(data)
assert index.layer_count() == 1
payload = prepare_layer(index, 0)
assert payload and payload['motions'] == 5
view = IndexView(('sample.gcode', len(data), 1), index)
boundary = view.recovery_boundary(0, 2)
assert boundary is not None and all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(boundary.position, (40, 10, .2)))
assert split_index(index, 0, boundary.next_motion_offset) == 2
assert list(motion_edges(index, 0))
prepared = prepare((('current', payload),))
vertices = pack_shader(prepared)
assert vertices and any(row[3] for row in vertices)
assert stroke_geometry(prepared, 1, 2, 2)
assert marker_counts(vertices, 2) == ()
events = pack_shader(prepare((('current', {'retractions': [(1, 2, 1), (3, 4, 3)]}),)))
assert marker_counts(events, 2) == (('RETRACTION', 1),)
assert marker_counts(events, 4) == (('RETRACTION', 2),)
assert isinstance(marker_geometry(vertices, 2, 2, 1, False, True, True), bytes)
compact = build_index_from_file(sys.argv[2], compact=True)
assert prepare_layer(compact, 0) is None
assert hydrate_layer_from_file(compact, sys.argv[2], 0)
assert prepare_layer(compact, 0)['motions'] == payload['motions']
from tempfile import TemporaryDirectory
from moonraker_toolpath.gcode.cache import CacheIdentity, PersistentIndexCache, PreparedCache, IndexResidency
with TemporaryDirectory() as cache_directory:
    identity = CacheIdentity('consumer-content-sha256', 'sample.gcode', len(data), 123.0)
    cache = PersistentIndexCache(cache_directory)
    cache.save(identity, index)
    restored = cache.load(identity)
    assert restored is not None and restored.layer_count() == index.layer_count()
    assert prepare_layer(restored, 0)['motions'] == payload['motions']
    prepared = PreparedCache(cache_directory)
    from moonraker_toolpath.gcode.PlateProgress import encode_layer, decode_layer
    prepared.finalise(identity.stable_key(), [encode_layer(payload)])
    table = prepared.load_table(identity.stable_key())
    assert table is not None
    raw = prepared.read(identity.stable_key(), table['table'], 0)
    assert decode_layer(raw)['motions'] == payload['motions']
    layered_file = Path(cache_directory) / 'multi.gcode'
    layered_file.write_bytes(b''.join(b';LAYER:%d\nG1 X%d\n' % (n,n+1) for n in range(6)))
    compact_view = build_index_from_file(layered_file, compact=True)
    demands = IndexResidency()
    demands.set_window('first-reader', 0)
    demands.set_window('second-reader', 5)
    for layer in (0, 5, 3):
        assert hydrate_layer_from_file(compact_view, layered_file, layer, retention=demands.layers)
    assert compact_view.hydrated_layers == {0, 3, 5}
    demands.set_window('first-reader', None)
    assert hydrate_layer_from_file(compact_view, layered_file, 4, retention=demands.layers)
    assert compact_view.hydrated_layers == {3, 4, 5}
    assert not hasattr(compact_view, 'followed_layer') and not hasattr(compact_view, 'manual_anchor')
assert Path(moonraker_toolpath.__file__).is_relative_to(Path(sys.argv[1]))
assert not any(name.split('.')[0] in {'PyQt6', 'UM', 'cura', 'numpy', 'mpf'} for name in sys.modules)
print('Core: installed imports, exact boundaries, hydration and stdlib vertices passed')
'''

RASTER_PROBE = r'''
import sys
from pathlib import Path
from importlib.abc import MetaPathFinder
sys.path.insert(0, sys.argv[1])
class RasterOnly(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(('UM', 'cura', 'mpf', 'PyQt6.QtQuick', 'PyQt6.QtQml'))
                or fullname.rsplit('.', 1)[-1] in {'PlateQt', 'RasterJobs', 'RasterFiles', 'GpuFollower'}):
            raise ImportError('Raster consumer cannot import ' + fullname)
sys.meta_path.insert(0, RasterOnly())
from PyQt6.QtGui import QGuiApplication
from moonraker_toolpath import GCodeDocument
from moonraker_toolpath.plate.raster import render_layer_raster
app = QGuiApplication([])
payload = GCodeDocument.from_file(sys.argv[2]).geometry(0)
image, grey, _ = render_layer_raster(payload,
    dict(sx=4, sy=4, offsetX=0, offsetY=0, bedXMin=0, bedYMax=50),
    dict(width=200, height=200, dpr=1))
assert not image.isNull() and grey.size() == image.size()
assert any(image.pixelColor(x, y).alpha() for y in range(200) for x in range(200))
print('Raster: image rendering with scene graph, host and worker imports blocked passed')
'''

QT_PROBE = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQuick import QQuickWindow
from PyQt6 import sip
from moonraker_toolpath.plate.qt import GpuFollower, PlateLayer, render_layer_raster
app = QGuiApplication([])
from moonraker_toolpath import GCodeDocument
empty = PlateLayer(GCodeDocument.from_bytes(b';LAYER:0\n;LAYER:1\nG1 X1\n').geometry(0))
assert empty.property('fallbackVector')['motions'] == 0
sip.delete(empty)
payload = GCodeDocument.from_file(sys.argv[2]).geometry(0)
plot = dict(sx=4, sy=4, offsetX=0, offsetY=0, bedXMin=0, bedYMax=50)
image, grey, _ = render_layer_raster(payload, plot, dict(width=200, height=200, dpr=1))
assert not image.isNull() and grey.size() == image.size()
assert any(image.pixelColor(x, y).alpha() for y in range(200) for x in range(200))
window = QQuickWindow()
item = GpuFollower(window.contentItem())
layer = PlateLayer(payload)
item.settings = {'showGrid': False, 'split': 2}
item.layers = {'current': layer}
deadline = time.monotonic() + 10
while not item.ready and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.005)
assert item.ready, item.error
node = item.updatePaintNode(None, None)
assert node.childCount() > 0
sip.delete(node)
sip.delete(item)
sip.delete(window)
assert not any(name.split('.')[0] in {'UM', 'cura', 'mpf'} for name in sys.modules)
print('Qt: standalone raster pixels and retained geometry passed')
'''


def run_probe(code, installation, sample, *, pure=False, cwd=None):
    args = [sys.executable, "-I"] + (["-S"] if pure else [])
    subprocess.run([*args, "-c", code, str(installation), str(sample)],
                   cwd=cwd, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--qt", action="store_true", help="also exercise the installed optional Qt API")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    version = json.loads((ROOT / "mpf/plugin.json").read_text())["version"]
    assert json.loads((ROOT / "package.json").read_text())["package_version"] == version
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))).decode()
        assert f"\nVersion: {version}\n" in metadata
        notice = next(name for name in archive.namelist() if name.endswith("/licenses/docs/THIRDPARTYSOFTWARE.md"))
        assert archive.read(notice) == (ROOT / "docs/THIRDPARTYSOFTWARE.md").read_bytes()
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in archive.namelist())
        actual = {name for name in archive.namelist() if '.dist-info/' not in name}
        expected = source_files()
        assert actual == set(expected), (sorted(set(expected) - actual), sorted(actual - set(expected)))
        for member, source in expected.items():
            assert archive.read(member) == (ROOT / source).read_bytes(), member
    with tempfile.TemporaryDirectory(prefix="mpf-library-") as directory:
        work = Path(directory)
        installation = work / "installed"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                        "--target", str(installation), str(wheel)], check=True)
        sample = work / "sample.gcode"
        sample.write_bytes(SAMPLE)
        run_probe(CORE_PROBE, installation, sample, pure=True, cwd=work)
        if args.qt:
            run_probe(RASTER_PROBE, installation, sample, cwd=work)
            run_probe(QT_PROBE, installation, sample, cwd=work)
            example = ROOT / "examples/render_gcode.py"
            code = ("import sys, runpy; sys.path.insert(0, sys.argv[1]); "
                    f"sys.argv = [{str(example)!r}, sys.argv[2], 'example.png', '--bed', '50']; "
                    f"runpy.run_path({str(example)!r}, run_name='__main__')")
            run_probe(code, installation, sample, cwd=work)
            assert (work / "example.png").stat().st_size > 100
    print("Library wheel: version, selective source/resource parity and external consumers passed")


if __name__ == "__main__":
    main()
