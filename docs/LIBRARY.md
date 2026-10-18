# Reusing the components

MoonrakerPrintFollower 6.0.0 includes a Python library as well as the Cura
plugin. The G-code index, geometry preparation and vertex generation work
without Cura, Qt, a printer connection or application settings. Optional Qt
renderers use the same implementation and shader assets as the plugin.

Build a wheel from this checkout with `make library`. This also installs the
wheel into a temporary directory and exercises it outside the checkout.
Install the resulting wheel in your own project's environment:

```sh
python -m pip install dist/library/moonraker_print_follower-6.0.0-py3-none-any.whl
# Optional raster and scene-graph rendering:
python -m pip install 'dist/library/moonraker_print_follower-6.0.0-py3-none-any.whl[qt]'
```

CI includes this wheel in its package artifacts. The release workflow also
attaches the verified wheel when a release is explicitly published; no PyPI
publication is required to install a downloaded wheel.

Python 3.10 or newer is required. The default installation has no runtime
dependencies. The `qt` extra uses PyQt6 6.11 or newer within major version 6;
the `fast` extra adds optional NumPy acceleration. The standard-library vertex
packer remains available without NumPy. Install these into a separate project
environment; Cura continues to use its bundled dependencies. The distribution
name is `moonraker-print-follower`, and its Python package is `mpf`.

## Public modules

| Module | Supported exports | Dependencies |
| --- | --- | --- |
| `mpf.gcode` | `build_index_from_file`, `build_index_from_bytes`, `hydrate_layer_from_file`, `LayerMotionIndex`, `IndexView`, `RecoveryBoundary`, `prepare_layer`, `motion_edges`, `split_index` | Standard library |
| `mpf.plate` | `prepare`, `stroke_geometry`, `marker_counts`, `marker_geometry`, `pack_shader` | Standard library; optional NumPy |
| `mpf.plate.qt` | `GpuFollower`, `PlateLayer`, `render_layer_raster`, `render_layer_prefix`, `render_navigation_layer` | PyQt6 |
| `mpf.geometry.Polygons` | `point_in_polygon`, `polygon_bounds`, `segment_in_polygon` | Standard library |

These entry points are the supported reuse surface. Internal modules,
underscore-prefixed names, Cura adapters and workflow controllers may change.
The library makes no network requests and loads no Cura services when these
entry points are imported. The wheel includes the remaining plugin modules
and resources, but importing Cura adapters still requires Cura.

## Index and inspect a layer

```python
from mpf.gcode import build_index_from_file, hydrate_layer_from_file, prepare_layer
from mpf.plate import prepare, pack_shader

index = build_index_from_file("part.gcode", compact=True)
layer = 0
if not hydrate_layer_from_file(index, "part.gcode", layer):
    raise RuntimeError("Layer could not be loaded")
payload = prepare_layer(index, layer)
if payload is None:
    raise RuntimeError("Layer is unavailable")
geometry = prepare((("current", payload),))
vertices = pack_shader(geometry)
print(index.layer_count(), payload["motions"], len(vertices))
```

`build_index_from_bytes(data)` is convenient for small files and keeps the
motion arrays in memory. File indexing supports a compact index: hydrate a
layer before asking for its geometry or exact motion boundary. Keep the file
unchanged between indexing and hydration. A cancellation event can be supplied
to the index and vertex builders; do not treat cancelled or unavailable
geometry as a completed result.

Coordinates use millimetres with Z upward. Layer indices are zero-based.
Motion counts represent original motion commands, including non-extruding
moves; an arc can produce several display edges with the same motion index.
`split_index(index, layer, file_position)` maps an original byte position to
completed motions. `IndexView.recovery_boundary(layer, completed_motions)`
returns an exact indexed inspection boundary, or `None` when unavailable.
That boundary alone is not an executable printer recovery plan.

Prepared payloads are borrowed, cached values. Treat them as immutable and
keep them alive while another thread renders them. The `classes` and `travels`
paths carry XY and original motion indices. Display geometry may be simplified;
use the index and exact boundary APIs for command positions. The indexer is
designed for slicer-produced 3D-printing G-code and the documented Klipper
coordinate modes, not arbitrary CNC dialects or execution of custom macros.

### Geometry return values

All packed coordinate arrays use native float32 bytes. Keep payloads and returned
rows immutable. `sx`/`sy` rendering scales must be finite and nonzero.

| Function | Input and returned rows |
| --- | --- |
| `prepare` | `(role, layer_payload)` pairs become `(role, feature, motion_ids, xy_edge_bytes, widths, metrics)`; event rows contain only the first four fields. Roles are `current`, `prev`, `next`. Metrics pair speed and tool values. |
| `pack_shader` | Prepared rows become `(role, feature, motion_ids, vertex_bytes)` for shader strokes; event rows retain their XY pairs. NumPy and stdlib paths have the same layout. |
| `stroke_geometry` | Prepared rows and pixel width/scales become `(role, feature, motion_ids, triangle_xy_bytes)` for CPU-expanded strokes. |
| `marker_counts` | Consumes `pack_shader` rows and completed-motion progress; returns a tuple of `(event_name, completed_count)` pairs. |
| `marker_geometry` | Consumes `pack_shader` rows, scales and marker options; returns packed XY line-segment bytes. Optional limits come from `marker_counts`. |

## Render without Cura

The runnable [render example](../examples/render_gcode.py) takes an input file,
output PNG, zero-based layer and square bed size:

```sh
QT_QPA_PLATFORM=offscreen python examples/render_gcode.py part.gcode layer.png --layer 0 --bed 250
```

It creates a `QGuiApplication`, prepares one layer and uses
`render_layer_raster(payload, plot, view)`. The return value contains coloured,
grey and travel `QImage` assets. The plot specifies pixels per millimetre,
offsets and bed extents; the view specifies image width/height and optional
device pixel ratio, zoom and line styling. Prefix rendering adds a completed
motion split. Images may be composed using normal Qt painting.

For a Qt Quick scene, instantiate `GpuFollower` under a `QQuickWindow` item,
retain `PlateLayer(payload)` objects and assign
`follower.layers = {"current": layer_object}`. Optional `prev` and `next` roles
show neighbouring layers. `follower.settings` supplies plot, progress split,
colours and view options. Listen to `readyChanged`, check `ready`/`error`, and
let Qt invoke scene-graph painting on its render thread. The GPU renderer
requires OpenGL; use the raster API when that backend is unavailable. Raw
payload dictionaries do not replace `PlateLayer` objects in this interface.

The core imports, installed-wheel resources, raster pixels and retained
geometry are checked by `make library`; the normal test suite additionally
covers geometry parity, cancellation, cache reuse and Qt scene-graph behaviour.

## Licence

Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>.
The library and plugin are GPLv3-only; see [LICENSE](../LICENSE). Reusing or
redistributing them remains subject to that licence. Upstream notices are
retained on the camera components that incorporate LGPLv3-or-later code.
