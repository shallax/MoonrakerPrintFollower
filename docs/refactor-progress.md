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

The extraction itself is two commits, because the surface record stands alone and the lifecycle does not.
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

The extraction also concentrated a measurement blind spot, and the per-file
coverage gate caught it. Under coverage's ctrace core the controller records
55 missed statements of 856; under sysmon, on the same revision, the same
module set and the same test set, 1. Every line in that difference sits inside
a job body started on the thread pool — the navigation bake, the rewind walk
and the checkpoint walk — nothing flips the other way, and the package total
moves from 333 missed statements to 276. ctrace is the `sys.settrace` core,
and coverage selects it on every interpreter below 3.14; it never sees Python
that Qt's own threads call into. Those 55 statements were about one percent of
the model's 4486 and the model passed the bar; the same 55 are 6.4% of the
controller's 856, so CI failed the file it had been passing all along. The
coverage job now sets `COVERAGE_CORE: sysmon` — available on 3.12, and the
runner measures statements only, so the branch gate sysmon cannot satisfy
below 3.14 does not apply. The 95% per-file bar is unchanged, and sysmon
records a superset of ctrace's lines here.

`tests/test_plate_render_controller.py` (13 tests) closes what stays open
deterministically: the presentation restamp across both live surfaces, the
decoded cache's consumer key, the pixel-width stroke on the warm composite, the
teardown's pin and tier release, the repeated gesture hold, the discard sweep's
reference guard, the navigation wake's popover guard, the checkpoint walk's
terminal paths (cancel, vanished asset, failed write, a worker that throws), the
checkpoint file that seeds a refreshed prefix, and the seek trace's debounce —
whose only other driver is `tests.test_follower_seek_performance`, the wall-clock
module `tools/run_some.py` deliberately runs uninstrumented.
`PlateRenderController.asset_owner_files` had no caller and went with the pass.

That suite spelled its cache URLs as `"file://"` plus a path, which round trips
through `QUrl(...).toLocalFile()` on POSIX only; on Windows it resolves to
nothing, so the sweep's unlink raised into the guard that keeps a vanished
asset harmless and the discarded file outlived its job. The tests now build the
`QUrl.fromLocalFile` form `png_file` itself returns, which is the spelling the
sibling raster suites already used — the same defect the raster prune keys its
reference set for.

## Batch F — the publication transaction, the camera recovery and the pause block

Three commits. `MonitorPublication.py` (166 lines) is the owner the brief asked
for by name: the committed value map, the per-value QVariant conversion cache
and the notification-group table with its emit order. The model's `_publish()`
was the 438-line body that read the config, ran the state transitions, built
every value and then decided what to notify; it is now a transaction naming its
phases — previous frame, observe, compose, apply the camera transition,
compare, emit — over `_observe()`, `_compose()` and `_apply_camera_url()`
(3015 -> 2941). The store and the comparison are two calls rather than one
`commit()` on purpose: `_apply_camera_url` amends the committed dict in place,
so the comparison has to run after the amendment and before the emission.
Moving the store after the emit fails three of the five ordering tests in
`tests/test_monitor_publish_transaction.py`; `tests/test_monitor_publication.py`
(10 tests) takes the owner's value identity, its one-per-rebuild QVariant
conversion and its group selection to 100%.

`CameraRecovery.py` (151 lines) takes the webcam stream's freshness policy —
five attributes written from six handlers, every one of them resolving to the
same reload nonce. Each transition answers whether the published frame changed
and the facade keeps its own dispatch, so the synchronous user-action publish
and the coalesced one stay where they were. The first-failure retry, the 10 s
cadence, the query-only URL rule, the both-directions toggle and the
ACTIVE-monitor-only wake moved whole (2941 -> 2892). The nonce had a second
writer: `self._camera_refresh_nonce = 0` sat eighty lines above the rest of the
camera state, inside the follower-view rehydration, and would have survived the
extraction as a duplicate owner. `PreviewFormatting` left the model's declared
dependencies at the next step, because the popover projection was its only
consumer — the table is exact, so an unused entry is a lie even where the gate
allows a superset.

`PauseAtLayerPresentation.py` (74 lines) takes the popover's pause block out of
`_published_pause_block` and `_pause_at_layer_values`. The schedule and its rows
stay with the coordinator; what is derived here is only the candidate at the
follower's own layer and the button's gates, over the same rows and with the
same helpers the card's own gates use (2892 -> 2798). The facade hands it an
already-coerced plate anchor, because `_coerce_anchor` is pinned by
`tests/test_monitor_model_coverage.py` and read from four other places in the
model.

The QML contract that pinned the candidate's source to the follower's layer
scanned the model for `index = self._follower_layer_anchor`. That statement
moved, so the pin was retargeted rather than dropped: the model is asserted to
wire `anchor=lambda: self._follower_layer_anchor`, and the projection to read
it live and to fall back to the plate's anchor. The camera throttle pins in
`tests/test_monitor_camera_runtime.py` retarget to
`_camera_recovery._last_refresh_at`, and the camera-state pins in
`tests/test_monitor_model_coverage.py` to `application_state`,
`seed_application_state` and `nonce`. No assertion was weakened, skipped,
xfailed or deleted.

Deliberately left in the facade: the follower-view settings bundle, the UI
layout state, and the job/status and migration/What's New projections. The
renderer results already integrate through the transaction rather than as an
independent stream, so nothing there is unsettled. UI layout state and the
job/status and migration/What's New presentations have owners already —
`UiStateStore` with `SectionLayoutPolicy`, `MonitorFormatting` with
`MonitorPermissions`, `MigrationPresentation`, `WhatsNew` — and the facade only
calls them. The follower-view bundle is ten Qt slots that guard, store, save and
publish: API delegation over the facade's own chrome state, which is what the
acceptance describes the facade as being. An owner for it would need either ten
named fields, no shorter than what it replaces, or a string-keyed settings bag,
which is the context bag the rules forbid.

Verified by the focused modules — publication 10, publish transaction 5, camera
recovery 13, camera runtime 10, pause presentation 12, pause at layer 34, model
coverage 138, model runtime 114, monitor contracts 44, ownership 9, architecture
34 — and by each commit's own full gate, which runs the whole suite including
the four `tests/harness/test_harness_*.py` legs `tools/run_some.sh` does not
glob.

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
- Batches G-I not started.
