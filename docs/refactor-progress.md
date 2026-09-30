# Refactor progress ledger

One row per batch. "Pushed" means the remote branch carries it, not that a
local tree contains it. A CI conclusion is quoted for the SHA it ran against;
a later push invalidates it.

Branch: `chore/v4.6.2`. Base when this ledger opened: `905c628`.

| batch | responsibility | result | commit | pushed |
|---|---|---|---|---|
| A | baseline repair | CI green | `a8ded46` | yes |
| B | file-browser QML | `FileManager.qml` 2949 -> 725 | `f6688b3` | yes |
| C | Monitor composition | `MoonrakerMonitor.qml` 3129 -> 892 | `0025b3c` | yes |
| D | camera QML | `CameraPane.qml` 1403 -> 221 | `bf5ff8d` | yes |
| D | toolhead QML | `ToolheadSection.qml` 993 -> 401 | `f491998` | yes |
| D | Preview card + dashboard leaves | card 1211 -> 354, dashboard 1181 -> 980 | `1b58cbc` | yes |
| E | renderer subsystem | model 4486 -> 3015; +`PlateRenderController.py` 1626, `RenderSurface.py` 94 | `e60823f` | yes |

## Batch A — baseline repair

Two defects stood between the branch and a trustworthy baseline. Neither was
the failure the handoff predicted.

**The Linux blocker was a hard abort, not a test failure.** All three Python
matrix jobs exited 1 with no `FAIL` block because the interpreter aborted:
`test_qml_dashboard_interaction` mounted the information pane and Qt asserted
on the way into a slot. The double declared `setConsoleExpanded` as
`@harness.pyqtSlot()` with no arguments while the model declares
`@pyqtSlot(bool)` and the extracted console pane calls it with a bool from six
sites. `Q_ASSERT` is compiled out of release Qt, so Windows and macOS ran the
same broken call silently — the abort is Linux-only.

**The predicted index failure did not reproduce.** It passes on Linux in
isolation and in its batch, and it passed on Windows and macOS in the run that
fixed the abort. It is load-sensitive. The test already wraps
`IndexTasks.passive_yield`, which is the correct target; it was not edited.

## Batch B — file-browser QML

Five substeps. Leaves under `mpf/files/browser/`: toolbar, recents, directory
strip, search, filters, filter-option row, column chooser, grid, pagination.
`FileGridHeader.qml` and `FileRowDelegate.qml` were deliberately NOT split out:
the header and delegate consume the grid's own column geometry, so as separate
documents each would need ~15 handed-in bindings. The selection summary stayed
with the page strip for the same reason.

Verified by a pixel oracle: capture then `cmp` against the committed
screenshots, byte-exact after every step. 0 test methods removed or added.

## Batch C — Monitor composition

Five commits. The root keeps stage-level coordination — one active popover,
outside-click routing, cross-pane width decisions, the shared overlay frame —
and now only instantiates panes and popovers. Extracted: `TemperatureDetailPopover`,
`BedMeshDetail`, `ObjectPickerPopover`, `PrintFollowerPopover`,
`PauseScheduleView` (view only; scheduling stayed with the print model),
`InfoPane`, `InfoCollapsedReadout`, `StatusPane`, `StatusCollapsedReadout`.

The frozen narrow-window contract was not restructured: `cameraViewportWidth`,
the 226/366 costs, the locks, `applyNarrowWindowRules()` and the fit passes are
verbatim on the host.

Verified by the same pixel oracle, six scenes SAME.

## Batch D — camera, toolhead, Preview card, dashboard

Four commits. The camera pane handed the picture and its control bar to
`CameraViewport` and `CameraControlBar` (1403 -> 221); the toolhead section
handed the compass, the extrusion cluster and the z nudges to `JogPad`,
`ExtrusionControls` and `ZOffsetControls` (993 -> 401). Authorization and
interlock policy stayed with the controller: homed-axis requirements,
cold-extrusion refusals and live-print restrictions are its own.

The Preview card's sections became leaves: `PreviewStatusStrip`,
`PauseAtLayerSection`, `BedMeshLegend` and `ReplacePromptDialog` (1211 -> 354).
The card keeps its published values, its signals and its replacement
lifecycle; the leaves take declared inputs and report back through one
narrow signal each. The pause state machine, the strip's state machine and
the prompt's escape handling moved whole, so no state has two owners.

The controls pane's collapsed strip became `ControlsCollapsedReadout`
(1181 -> 980): the vertical title, the fixed-width position and z row, the
availability gates and the whole-pair fit, with the pane handed in as the
frame the fit measures against. Two candidates were deliberately left on the
dashboard root. Section configuration owns `configurePaneOpen`, the scrim's
outside-click, the escape ladder's rung and the one-at-a-time popover rule —
splitting it would put a second owner on the active popover, which the batch
forbids. The tuning-slider freeze latch is read by `controlFlick.interactive`
and by every section instance through `freezeRepeaters`/`frozenItems`, and its
refocus walk roots at the three section instances by id, so a leaf would need
the same bindings handed back plus three more — no ownership gained, one
single-owner invariant put at risk.

Couplings the batch text did not name, fixed in the same pass: the strip's
`layerHeightRowsVisible` moved with the rows that read it; the pause section
hides whole on `implicitHeight > 0` because an empty visible Column still
takes a row gap; the readout leaf carries its asset URLs one directory deeper
(`../../resources/svg/`); its pane-height hook became a `Connections` on the
declared frame; and the dashboard's `onPrinterChanged` gate call moved into the
leaf as its own handler, so a printer switch still re-reads the gates. The
pause-menu pin in `tests/test_pause_at_layer.py` reads the card together with
the section document it renders, because the toggle, the summary and the
scheduled rows it pins moved whole.

Verified by the same pixel oracle — all fourteen scenes byte-identical after
each half — plus the focused modules: preview 45, preview presentation 34,
bed mesh 12, camera gestures 34, dashboard layout 17, dashboard interaction 17,
monitor contracts 44, model runtime 114, monitor controls 86, SDK compatibility
11, harness specs 18, resource references 7, gate summary 9. One round trip on
the asset URLs: a reference broken on purpose fails
`test_every_referenced_svg_resolves_from_its_document` on that exact line, and
the restored file passes.

## Batch E — the renderer subsystem

Two commits, because the surface record stands alone and the lifecycle does not.
`RenderSurface.py` (94 lines) takes the per-surface record — view/plot context,
retained layer wrappers, demand, tokens, generation, anchors, the navigation slot
— and `PlateRenderController.py` (1626) takes the rest: both surfaces' render
contexts, their one-job-at-a-time demand schedulers, the raster workers and the
tickets that identify them, the per-instance raster cache directory, the
asset-reference owners and the gesture hold, the decoded payload pins the
retained wrappers hold, and the navigation raster's double buffer.
`MoonrakerMonitorModel.py` 4486 -> 3015. Every public property and slot keeps its
name, type and default; the model's declarations are now delegates over
snapshots, so no QML document changed.

The field/callback/teardown map was written before anything moved: 47 moved
names, every call site, and the six invalidation paths (print switch, model
destruction with its reverse-connect order, popover close and mini collapse, GPU
backend switch, supersede cancellation, discard unlink). The state separated
cleanly along one line — renderer-owned lifecycle versus physical print state and
user presentation settings — which is the line the extraction follows.

Couplings the batch text did not name, fixed in the same pass:

- The four cadence constants (`_NAV_FOLLOW_BAKE_S`, `_NAV_ZOOM_SETTLE_S`,
  `_NAV_KEY_FIELDS`, `_PREFIX_CHECKPOINT_S`) moved with the code that reads them.
  The pin that guards the navigation key's shape now reads `_NAV_KEY_FIELDS` off
  the module that holds it.
- The moved methods reached back into the model for the legend toggles and the
  bed bounds through `getattr(self, ...)`. That borrowing became one `SceneInputs`
  snapshot taken at the call, injected — the renderer no longer reads a model
  property.
- `_seek_trace_enabled`'s config read is the injected `trace_requested` callable,
  read at the call rather than snapshotted: the switch is a live debug toggle.
  `followerHoldReport` keeps its own `Logger` call and delegates only its gate.
- `set_gpu_rendering`'s consumer key is now `(id(controller), surface.name)`.
  The index service treats the consumer as an opaque hashable and nothing else
  keyed on the model's identity, so the decoded tier still follows the consumer
  count across a backend switch.
- The coverage matrix keys `@pyqtSlot` surfaces by module basename, so
  `_raster_started`, `_raster_committed` and `_trace` became
  `PlateRenderController.*` in `tests/harness/scenario_map.py`.
- Tests that patched `render_navigation_layer`, `render_layer_prefix`,
  `render_layer_raster`, `_RasterJob`, `png_file` or `QThreadPool` on the MODEL
  module were patching a namespace the scheduler no longer reads. They now patch
  the module whose code calls those names. Several were multi-line
  `patch.object(` calls the first pass missed, and one helper read the fake
  item's own `_navigation_backing`, which the retarget had wrongly claimed.
- `ARCHITECTURE.md` gains the two ownership rows plus the render-path paragraph,
  and `tests/test_architecture.py` names both modules in the ownership table's
  discovery check and in the model's declared dependencies.

Two splits were considered and declined as forced. The asset registry would need
a live-URL provider that walks the surfaces' navigation slots, layers and the
gesture hold before it could decide what to prune — the same records the
scheduler owns — and prefix and navigation scheduling share one job slot and one
in-flight rule per surface, so splitting them would put two owners on one frame.
`PlateQt.py` was reviewed alongside: its painting is already concrete top-level
helpers (`_paint_segments`, `_paint_below_split`, `_paint_travels`, `_paint_grid`,
`_geometry_pen`, `_new_canvas`, `_derive_grey`) over `(payload, plot, view)`, and
the one class there, `PlateLayer`, is cohesive. Nothing was moved and no second
raster framework was introduced.

## Outstanding

- `tests/test_gpu_canvas_isolation.py::test_live_layer_handoff_fades_previous_geometry_then_retires_it`
  asserts a QML animation advanced inside a fixed pump window; it failed once on
  macOS and passed on re-run. Load-sensitive assertion, not fixed. A green that
  needs a second attempt is not a fixed test.
- One unit-leg run aborted on a segmentation fault instead of a test failure:
  `PlateQt._derive_grey` and `PlateQt.png_file` crashed on the raster worker
  threads through `sip_api_convert_to_enum`. This ledger first wrote it up as
  load sensitivity; the artifact is a crash, so that reading is not established
  and this is not a flake to dismiss. It sits outside this batch's modules, and
  the four unit-leg runs since have not reproduced it. The raster path already
  carries teardown-segfault guards.
- Batches F-I not started.
