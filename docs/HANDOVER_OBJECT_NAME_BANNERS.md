# Preview object-name banners handover

Status: implementation is on `release/v5.1.0`.
The long-G-code late-completion fix is installed in the local Cura 5.13 plugin
copy and Cura was restarted on 2026-10-05. Live banner display after that
restart has not yet been confirmed: Cura is parsing the current large G-code.

## Requested behaviour

- Upright object-name banners in Cura Preview, anchored to objects through
  Cura's supported camera projection and separated in screen space.
- A Preview dock with an enable checkbox and an all-banners/hovered-only choice.
- Hover raises the front-most visible scene object's banner and fades nearby
  banners. Picking must be depth-tested, never inferred from a 2D footprint.
- Per-object printed progress, countdown and projected finish time when source
  data supports them.

## Implementation

- `mpf/preview/PreviewPresentation.py` owns the dock, saved preferences, camera
  projection, scene-object selection-pass hover and fallback G-code anchors.
- `mpf/preview/PreviewObjectTagsHost.qml` renders the dock, leaders, dots,
  banners, progress strips, countdowns and finish times.
- `mpf/preview/ObjectNameProjection.py` places banners above anchors and moves
  crowded labels without moving their object endpoints.
- `mpf/gcode/ObjectWork.py` gathers bounded per-object filament checkpoints,
  last-work offsets, height and XY bounds from `EXCLUDE_OBJECT_START/END` or
  `;MESH:` markers during the existing one-pass scan. `IndexView` publishes
  centres, progress and a slicer-time-scaled ETA. The persistent index cache is
  version 15; earlier index caches rebuild.
- `PrintCoordinator._object_tag_values()` publishes object definitions and
  metrics only for the current print's exact plugin-loaded G-code path and
  matching index job key. Index XY bounds fill in missing Moonraker centres.
- `CuraIntegration.plugin_loaded_path` tracks that exact loaded path.

## Live finding and fix

The current 305 MB, 528-layer G-code took about 8.5 minutes for Cura 5.13 to
parse. The existing five-minute load watchdog released the busy state but kept
the file lease as `_load_watch_lease`; a later `fileCompleted` was correctly
absorbed. It did **not** set `plugin_loaded_path`, so the exact-file gate
withheld all object definitions and metrics. The index itself had 20 named
objects and valid XY bounds. The reported symptom was an enabled dock with no
banners.

`CuraIntegration._file_completed()` now sets `plugin_loaded_path` for both
on-time and watched late completions. Regression assertions were added to
`test_qt_cura_integration.py` and
`test_gcode_index_integration_coverage.py`. The late watcher already remains
active until completion; it is not a one-time abandonment of the load. The
five-minute watchdog still reports a timeout and clears the busy state so a
silently refused Cura load cannot lock out retry forever. The UI wording for
this slow-but-still-parsing state may need improvement.

## Verification and next check

- `make lint` passed all nine steps after the late-completion fix.
- `make run_tests` passed 4,804 tests after the fix.
- Cura was restarted with the updated `CuraIntegration.py`. The running load
  started at approximately 09:07 local time on 2026-10-05. Inspect Cura's
  `cura.log` for `G-code loading finished`, then check Preview for banners.
- If banners still do not appear, inspect `PreviewPresentation._update_tags()`
  gates in order: `preview_active`, `tags_enabled`, `has_toolpath`, projection
  support, non-empty definitions/metrics, and `place_banners()` output. The
  20-object index data is present, so the remaining issue would be in the
  publication, coordinate projection, or QML display path.
- The native UI harness does not yet provide a matching object-marked print
  with selectable model meshes. Real-engine QML and selection-pass boundary
  tests cover the overlay; a live Cura visual check is still required.

## Supported hover boundary

Uranium's `selection` pass depth-picks selectable scene model meshes. Cura's
G-code-only `SimulationPass` does not expose per-object toolpath IDs through
that pass. G-code-only Preview therefore shows all banners and disables
hovered-only mode. Do not replace this with a footprint hit test: it would
choose hidden objects from oblique views.
