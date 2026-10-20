# Reusing the components

MoonrakerPrintFollower 6.0.0 includes a Python library as well as the Cura
plugin. The G-code index, geometry preparation and vertex generation work
without Cura, Qt, a printer connection or application settings. Optional Qt
renderers use the same implementation and shader assets as the plugin.

Build a wheel from this checkout with `make library`. This also installs the
wheel into a temporary directory and exercises it outside the checkout.
Install the resulting wheel in your own project's environment:

```sh
python -m pip install dist/library/moonraker_toolpath-6.0.0-py3-none-any.whl
# Optional raster and scene-graph rendering:
python -m pip install 'dist/library/moonraker_toolpath-6.0.0-py3-none-any.whl[qt]'
```

CI includes this wheel in its package artifacts. The release workflow also
attaches the verified wheel when a release is explicitly published; no PyPI
publication is required to install a downloaded wheel.

Python 3.10 or newer is required. The default installation has no runtime
dependencies. The `qt` extra uses PyQt6 6.11 or newer within major version 6;
the `fast` extra adds optional NumPy acceleration. The standard-library vertex
packer remains available without NumPy. Install these into a separate project
environment; Cura continues to use its bundled dependencies. The distribution
name is `moonraker-toolpath`, and its Python package is `moonraker_toolpath`.

## Public modules

| Module | Supported exports | Dependencies |
| --- | --- | --- |
| `moonraker_toolpath` | `GCodeDocument`, `LayerInfo` | Standard library |
| `moonraker_toolpath.gcode` (advanced) | `build_index_from_file`, `build_index_from_bytes`, `hydrate_layer_from_file`, `LayerMotionIndex`, `IndexView`, `RecoveryBoundary`, `prepare_layer`, `motion_edges`, `split_index` | Standard library |
| `moonraker_toolpath.plate` | `prepare`, `stroke_geometry`, `marker_counts`, `marker_geometry`, `pack_shader` | Standard library; optional NumPy |
| `moonraker_toolpath.plate.qt` | `GpuFollower`, `PlateLayer`, `render_layer_raster`, `render_layer_prefix`, `render_navigation_layer` | PyQt6 |
| `moonraker_toolpath.geometry.Polygons` | `point_in_polygon`, `polygon_bounds`, `segment_in_polygon` | Standard library |

These entry points are the supported reuse surface. Internal modules,
underscore-prefixed names, Cura adapters and workflow controllers may change.
The library makes no network requests and loads no Cura services when these
entry points are imported. The wheel contains only the shared parser/geometry/rendering dependency closure
and its stroke shaders. Cura registration, networking, cameras, runtime installers
and application workflows are excluded. The build maps these modules directly
from their production source files; there is no copied implementation or alias layer.

## Index and inspect a layer

```python
from moonraker_toolpath import GCodeDocument
from moonraker_toolpath.plate import prepare, pack_shader

document = GCodeDocument.from_file("part.gcode")
info = document.layer_info(0)
payload = document.geometry(0)
vertices = pack_shader(prepare((("current", payload),)))
print(document.layer_count, info.motion_count, len(vertices))
print(document.boundary(0, completed_motions=2))
```

`GCodeDocument.from_bytes(data)` accepts bytes. Both constructors retain the full
index in memory; memory use grows with file complexity. Keep the source unchanged
while loading it. Once loading finishes, it may be changed or deleted: the document
never reopens it. Loading is synchronous; applications should call it on their own
worker for large inputs. A supplied `cancel_event` causes `CancelledError` instead
of returning a partial document.

`layer_info(i)` returns frozen metadata: original byte range, total motion count,
retained motion count, completeness and deposited `layer_height_mm` (thickness,
not absolute nozzle Z). The scanner retains at most 200,000 motions per layer;
`geometry(i)` raises `ValueError` for an incomplete layer. Invalid layer inputs
raise `IndexError`; invalid completed-motion counts raise `ValueError`.
`boundary(i, count)` returns a frozen exact boundary or `None` when there is no
complete next motion, including the end of the document.

`geometry(i)` returns a deeply immutable renderer mapping. It copies mutable
containers while reusing immutable point/polyline tuples. Snapshots remain valid
after another layer is queried or the internal prepared cache evicts an entry.
Document reads are safe across threads. The private index and its cache/retention
state are not part of this interface.

### Advanced compact indexing

For files too large to retain in memory, `moonraker_toolpath.gcode` exposes the
lower-level mutable index API. Its caller owns file identity, cancellation,
hydration, locking and retention. Keep the file unchanged from initial indexing
through the last hydration; the API does not enforce source identity for you.

```python
from moonraker_toolpath.gcode import build_index_from_file, hydrate_layer_from_file, prepare_layer

index = build_index_from_file("part.gcode", compact=True)
if not hydrate_layer_from_file(index, "part.gcode", 0):
    raise RuntimeError("Layer could not be loaded")
payload = prepare_layer(index, 0)
if payload is None:
    raise RuntimeError("Layer is unavailable")
```

Coordinates use millimetres with Z upward. Layer indices are zero-based.
Motion counts represent original motion commands, including non-extruding
moves; an arc can produce several display edges with the same motion index.
`split_index(index, layer, file_position)` maps an original byte position to
completed motions. `IndexView.recovery_boundary(layer, completed_motions)`
returns an exact indexed inspection boundary, or `None` when unavailable.
That boundary alone is not an executable printer recovery plan.

Advanced `prepare_layer` payloads are borrowed, cached values. Treat them as immutable and
keep them alive while another thread renders them. The `classes` and `travels`
paths carry XY and original motion indices. Display geometry may be simplified;
use the index and exact boundary APIs for command positions. The indexer is
designed for slicer-produced 3D-printing G-code and the documented Klipper
coordinate modes, not arbitrary CNC dialects or execution of custom macros.

### Geometry return values

All packed coordinate arrays use native float32 bytes. Do not mutate advanced payloads or returned
rows. `sx`/`sy` rendering scales must be finite and nonzero.

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

The exact distribution boundary, shared-source parity, installed imports,
raster pixels and retained geometry are checked by `make library`; the normal test suite additionally
covers geometry parity, cancellation, cache reuse and Qt scene-graph behaviour.

## Licence

Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>.
The library and plugin are GPLv3-only; see [LICENSE](../LICENSE). Reusing or
redistributing them remains subject to that licence. Upstream notices are
retained in the repository’s third-party notice file; camera components are not
part of this standalone distribution.

Raster-only consumers can import `moonraker_toolpath.plate.raster` without loading Qt Quick, QML asset wrappers or worker scheduling. The retained renderer and its layer adapter are available from `moonraker_toolpath.plate.scenegraph`; `plate.qt` continues to provide the combined public API.

The standard-library API also provides `CacheIdentity`, `PersistentIndexCache`,
`PreparedCache` and `RawSourceCache` from `moonraker_toolpath.gcode.cache`. A
`CacheIdentity` takes an explicit stable key supplied by its caller, with optional
filename, size, modification time and revision UUID metadata. Custom identities
can implement the read-only `SourceIdentity` contract. No Moonraker client is
needed. `PreparedCache(..., log=callback)` accepts a diagnostics callback with
`(message, *args)`; logging is silent unless supplied. Application-specific
scheduling and printer cache binding are excluded from the wheel.

For compact indexing, create an `IndexResidency` from `moonraker_toolpath.gcode.cache`, call
`set_window(reader_token, layer, radius=1)` for each independent reader, and pass
`retention=residency.layers` to `hydrate_layer_from_file`. The callback is read at
hydration completion. `set_window(reader_token, None)` releases only that reader;
the freshly hydrated layer's neighbouring window also survives long enough to
prepare its geometry. Capture one residency owner per source generation so an
old worker cannot adopt another file's demands. Parsed indexes do not contain
printer/follower selection fields.
