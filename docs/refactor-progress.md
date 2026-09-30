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
| H | camera arithmetic | +`PlateViewPolicy.js` 150; one bed transform | `1e52833` | yes |
| H | Canvas drawing | +`PlatePainter.js` 258; face 3128 -> 2927 | `fd08779` | yes |
| H | asset validity | 4 predicates to the compositor; face -> 2883 | `c39c5e0` | yes |
| H | the face's visual leaves | dot 42, glyphs 40, scope 133; face -> 2735 | `25899b7` | yes |
| I | the scenario suite | `scenarios.py` 3377 -> a 18-module package | `3e2ab6b` | yes |
| I | the runner's instruments | +`liveness.py` 344, `static_leg.py` 229; runner 4886 -> 4391 | `ce5efde` | yes |
| I | the driver's families | driver 2801 -> 1957; +scene/foreground/frames/interaction/qt_test | `c23e1a5` | yes |
| I | the staged package's refresh | the repeat-boot smoke leg's own failure, fixed | `1cf8056` | yes |

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

Five substeps. Leaves under `mpf/Files/Browser/`: toolbar, recents, directory
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

## Batch G — the file manager's backend, the coordinator's pass and the prepared session

Three commits, one per part of the brief.

The file manager's second lifecycle left it. `ThumbnailCache.py` (264 lines)
owns the listing thumbnails whole: the bounded fetch queue, the in-flight reply
registry with its per-request identity, the generation that invalidates queue
and replies together, the temp tree the bodies land in and the cache the listing
rekeys on rename. The cache mutations the callers used to spell themselves —
rekey, adopt, drop — went with it, because those three were reachable from four
FileManager methods; the manager keeps only the three it publishes: request,
payload, clear (1267 -> 1026).

Two couplings the brief did not name. The first was a behaviour inconsistency,
not a move: the fetch that cannot start published on `FileManager.changed` — a
full listing rebuild, rows and all — while the same logical failure at the reply
published on the thumbnail channel alone. A landing is not a listing change:
both paths now publish on the thumbnail channel, and the new pin asserts the row
channel stayed silent. The second: the bounded reply-body drain was a copy in
two places, not one. The upload acknowledgement and the thumbnail fetches had
each grown the same reader, and moving only the thumbnails would have kept the
duplicate. `ReplyBodyReader.py` (56 lines) is the one reader both now take,
injected: a hard byte cap, one drain per readyRead, an overflow that aborts the
transfer and drops the bytes, one disposal per reply, and a readyRead slot that
stays a bound method of a live QObject.

The coordinator's pass is a sequence now, not a scope. `refresh()` was one
318-line body in which every derivation, every collaborator read and every
publication shared one scope, so the ordering the snapshot depends on was only
visible by reading the whole thing; it is 49 lines over ten phase methods —
`_sync_toolpath`, `_resolve_face`, `_resolve_motion`, `_resolve_totals`,
`_resolve_pause`, `_build_plate_payloads`, `_compose_snapshot`, `_trace_layer`,
`_observe_frame`, `_serve_preview` — each reading the frame `refresh()` pinned
and handing the next a record (`_PrintFace`, `_Motion`, `_Totals`, `_Pause`,
`_Plate`), so no two values in one snapshot can come from two polls (1075 ->
1199). The coupling the brief did not name: the slicer estimate is derived from
the same metadata as the face, and is only read at composition — nothing between
its old position and the snapshot read it — so it moved into `_resolve_face`
rather than becoming a record of its own. Physical-state ownership is untouched:
these are methods on the one coordinator, not new collaborators, and the
statement order is the order it was — the observed job transition, the metadata
confirmation, the load and replace state, index readiness, layer resolution,
pause handling, Preview projection and publication, with the layer ETA still the
last write before the publication reads the snapshot. Two contracts cover the
boundaries the split touches: a reconnect re-observes the frame the pass pinned
(and asks for no hydration while the link is down), and an index landing mid-load
readies the snapshot without ending the load or running the lease handoff.

The prepared store's session has an owner. `PreparedSession.py` (360 lines)
holds the adopted table, the identity strength gate, the incremental writer with
its retirement, the coverage census and the completeness and published latches —
eight fields plus the store itself, written from twelve different methods, and
163 lines of contiguous ones (1608 -> 1399; `_finish` 154 -> 132, `_advance`
unchanged at 107). What stayed behind is what is not the session's: the PASS
FRONTIER is scheduling state and the RAM tiers are residency, so a complete
clean table still stands the frontier down — through the service's own
`_adopt_prepared`, who owns the frontier. A worker's store is still captured at
submission, and the session refuses an append to a retired writer rather than
deciding who may write. The extraction also surfaced a method with no production
caller at all: `_prepared_persist` was pinned by tests and called by nothing
(the demand path appends from the worker that produced the encoding). It
survives as the session's `persist`, because deleting it would delete the
pinned statement of that contract rather than a duplicate of anything. Two
members with no reader anywhere — the store's owner-thread read, and the retry
count's accessor — were removed instead, and the one-rebuild bound gained its
own pin.

The pin retargets were mechanical but not cosmetic. The index and persistence
pins now read the session's own state (`_prepared.saved`, `.complete`,
`.writer`, `.coverage`) instead of the fields the service used to carry, and the
two tests that take the store away from a live session (`self.service._prepared
= None` before the extraction) call `_prepared.rebind(None)` — the same
statement about the store being absent, now through the owner that holds it.
`tests/test_qt_follower_integration.py` asserted the configured cache budget on
`index._prepared.max_bytes`, which is the STORE's cap, not the session's, and
retargets to `index._prepared.store.max_bytes`. `tests/test_index_prepared_reopen.py`
gained the layer count its direct `persist` calls now pass explicitly: the
session takes the view's own count as an argument because a session may hold a
table from before the view landed. No assertion was weakened, skipped, xfailed
or deleted.

Verified by the focused modules — file manager 80, file manager coverage 101,
monitor files runtime 4, upload lifecycle 15, coordinator coverage 89, remote
job service 16, print state 20, qt follower 56, runtime monitor composition 8,
index refresh throttle 4 — by the index and persistence set — prepared reopen
54, runtime index composition 28, runtime lifecycle composition 24, persistence
integration 46, index hydration 20, prepared store 79, index components 3 — and
by each commit's own full gate, which runs the whole suite including the four
`tests/harness/test_harness_*.py` legs `tools/run_some.sh` does not glob.

## Batch H — the progress face, conservatively and in the brief's order

Four commits, in the order the brief set: the pure calculations, the pure
painters, the presentation predicates, then the visual leaves.
`PlateProgressFace.qml` 3234 -> 2735.

`PlateViewPolicy.js` (150 lines) takes the camera's whole arithmetic: the
bed transform, the soft pan clamp, the zoom's focal/eased/inverse terms,
the scope's track-to-scale pair, its graduations and the physical stroke
width. The face keeps the camera STATE, the gestures that write it and
every Item that reads it. `PlateCanvas.plateToScene` resolves through the
same transform now, so the bed mapping has ONE implementation rather than
three — the canvas' function, the face's travel walk and the face's
inlined stroke loop. `PlatePainter.drawLayer` keeps its arithmetic
inlined and says why: a call per vertex over hundreds of thousands of
points was the follower's dominant cost.

`PlatePainter.js` (258 lines) takes every stroke: the edge rule and its
two binary-search bounds, the batched per-class walk with its
true-thickness and gradient branches, the travel families and the
retraction glyphs. Each painter takes the context, the payload, the
boundaries and one style record. `classColour` crosses as a capability
rather than a scalar because the theme it reads is a QML singleton a JS
library cannot import — one function of one string, the shape the
renderer's injected `trace_requested` already takes.

The four layer-asset predicates (`rasterOf`, `baseOf`, `travelsOf`,
`motionsOf`) went to `PlateExactComposition.js` rather than to a new
module: every decision taken over them already lived there, and the brief
forbids a second owner of which frame may be displayed.

The leaves are `PlateToolheadDot.qml`, `PlateExtruderMarkers.qml` and
`PlateZoomScope.qml`. The scope is the sharper boundary: it reports a
requested scale through one signal and writes no camera state, which the
contract holds by asserting its document mentions none of `viewPanX`,
`viewPanY`, `displayScale`, `displayPanX`, `displayPanY` or
`_enterInteraction`.

Couplings the brief did not name, fixed in the same pass:

- `_clampPan` had no caller anywhere in the tree. The soft clamp replaced
  the hard one and the dead function went with its rule paragraph, as did
  `zoomGraduations` once the scope owned its own model.
- The zoom cap was spelled `20.0` in five places — the wheel's ceiling,
  the scope's log base twice, the graduation loop and the track's power.
  They read `MAX_SCALE`, and a pin holds the wheel and the scope to it.
- The `scaleOverride`/`widthScale` pair threaded through four painter
  functions is gone; `_paintStyle()` and `_carryStyle()` answer "which
  camera is this baked at" once at the call. That also dropped a
  recomputation: every travel class called `travelWidthPx()`, which calls
  `toolpathWidthPx()`, so a layer with several travel families
  recomputed the width per family. The style resolves both once per
  paint.
- The glyph overlay reached its sibling `mapping` item for
  `plateToScene` while its own view record already carried the plot. The
  transform runs through that plot now and the sibling read is gone.
- `PlateZoomScope` takes its frame as a declared input rather than
  reading `parent`. The engine gate loads every document standalone,
  where a root item has no parent and a `parent.height` binding fails —
  the gate caught it on the first lint.

Two splits were considered and declined. The GPU stack's `gpuFollower` is
read by `_exactReady()`, `_updateMotion`, `_updateDot`, the
`gpuRendering` binding, the preparing cover and `_startHandoff`; a leaf
would need all of those handed back and would put the ready state in two
places. The `exactScene` raster stack's Images each write a texture
mirror on the face and call `_requestProgressPaint`, so extracting them
would move texture-readiness state out of the one owner the brief
requires keep it. The unavailable-state placeholder was left too: two
sibling items with no state and no ownership to transfer.

The four scene-identity keys (`_viewKey`, `_worldKeyOf`, `_pendingKeyOf`,
`_progressKeyOf`) stay on the face. `_pendingKeyOf` reads an Image's
status by id and the others read fifteen payload and view values each;
handing those in would be the context bag the rules forbid, and they are
the world/anchor epoch state the brief says to keep whole.

Verified by the focused modules — view policy 18, painter 23, exact
composition 24, plate raster mapping 9, plate geometry 10, plate
interaction 8, plate navigation 16, prefix ownership 5, zoom raster 9,
renderer parity 8, follower dpr 5, gpu canvas isolation 2, monitor
contracts 45, model runtime 114, resource references 7, sdk compatibility
11 — by each commit's own full gate, and by the pixel oracle: all
fourteen scenes byte-identical to the committed screenshots after every
commit, the print-follower scene included. CI's own Screenshot sync job
agreed independently.

Two mutation checks. The painter's edge comparison changed from `<` to
`<=` fails three painter tests plus the real-engine geometry suite;
the tree was restored. And the batch's own gate caught a real defect
before it was pushed: the painter move's block deletion took
`_paintCarry` with it, because that function sat between the pure
helpers it was extracting, and `test_qml_plate_raster_mapping` — a file
the focused run had not included — failed deterministically on the
gesture's tail painting no wall. The function was restored and the
face's whole property, id, objectName and function inventory diffed
against the previous commit to prove nothing else had gone with it.

## Batch I — the suite, the runner's instruments and the driver's families

### The scenario suite

`tests/harness/scenarios.py` held 126 specs and 54 probe bodies in one
3377-line module. It is a package now: one module per group, the shared probe
bodies in `probe_source.py`, and an `__init__` that is the assembly point. The
spec text moved by character range and was never reformatted, so every step
order, payload, budget, probe reference and comment is the same bytes.

A before/after serialisation under one controlled scratch environment compares
equal on all 126 ids, every spec body, all 54 constants and the scratch path
construction. Each group's own sequence is preserved exactly — the visual group
still runs v19 v1 … v18 v13 v16 — and `GROUPS` spells the order out rather
than globbing the directory.

The one thing that did change: each group is now contiguous, which the flat
list was not. Two groups had grown a last scenario after the following block
was already written (visual's v13/v16 after the probe block, probe's z4 after
that). The runner never reads the global order — `suite_specs()` and
`real_run()` are its only readers of `SCENARIOS`, and both filter by group or
by exact id — so the change is invisible to every consumer, and a pin now
asserts that selecting a group yields exactly its module's list, in order,
with no group assembled twice.

Couplings the brief did not name:

- `dir(scenarios)` is how `test_harness_specs` finds every probe constant to
  compile, and `getattr(scenarios, code)` is how a template step resolves. The
  assembly point therefore re-exports all 54 by an enumerated list — never a
  wildcard — and a new pin holds that list equal to the probe module's own
  constants and identical object-for-object. Without it a constant added to one
  and forgotten in the other would quietly shrink the compile check.
- `tests/harness/test_harness_native.py` read `scenarios.py` as a FILE, which
  no import-shaped grep finds. Its capture-device scan now walks every module
  in the package, discovered from the directory with a floor, so it reads more
  files than before rather than fewer. The same treatment covers the driver's
  new families.
- The two classification ratchets counted ops in that file's text. They read
  the package's own modules — a named, bounded set, not the source tree and not
  a basename lookup — and refuse to report a census over fewer than sixteen
  modules.

### The runner's two measurement instruments

`liveness.py` (344 lines) owns the presentation record whole: the samples, the
sample vocabulary, the verdict that separates a stalled scene graph from a
stalled capture, and the platform gating. `static_leg.py` (229) owns the
still-frame analysis: the decode, the longest near-identical stretch, whether
an input step fell inside it, and the verdict written into the leg's artifacts.
`runner.py` 4886 -> 4391.

Both take what they do not own explicitly: `liveness.probe()` is handed the
runner's rpc callable, and both take the capture gate as an argument rather
than reading a global. The reading functions still carry no capture term at
all, which `test_harness_runner` holds at the new owner — together with the
other half, that the runner may not call `liveness.gating()` itself, so no leg
can take a reading without declaring its own gate.

Nine static aliases and three liveness ones were dropped rather than kept. An
alias that exists only so a test can find the old name is compatibility
forwarding, and it hides where the name now lives; those tests address the
owners. What stayed is what the runner calls.

### The driver's probe and interaction families

The server — the loopback socket, the RPC lifecycle, the token rendezvous and
the GUI-thread dispatcher with all 53 of its verbs — stays in
`driver/__init__.py` (2801 -> 1957), which is the one driver lifecycle and
GUI-thread interaction owner the brief requires. Beside it: `scene.py` (the
windows and the visual tree — one walk, one visibility rule, one geometry read,
one settle), `foreground.py` (whether the display shows this application, and
the raise), `frames.py` (the frame counter and the heartbeat item's lifecycle),
`interaction.py` (the click walks' window union, control resolution, the aim
point, press delivery, the delivery filter) and `qt_test.py`.

Each module imports exactly the names it uses, enumerated, so no call site
inside the dispatcher changed and no family reaches back into the server.

Couplings the brief did not name:

- The heartbeat read its QML engine off `HarnessServer._engine` from inside the
  measurement. The server discovers it at registration and still owns it, so it
  is handed in now, with the module's own fallbacks intact behind it.
- `qt_test.py` exists because the first cut had `scene` importing the QTest
  loader from `interaction` while `interaction` imported the walk from `scene`.
  The staged import found the cycle immediately. The loader belongs to neither
  family: the settle and the click paths both need it, and it caches the module
  it found or the failure it hit. The server reads that failure through an
  accessor rather than importing a name the loader rebinds.
- Ten source pins moved to the family that owns what they hold, through one
  helper that refuses to read a family that is not there. The verbs and the
  reply fields stay pinned on the dispatcher, which is where they are.

### What the gate caught, and the one defect that reached CI

The staged tree is what a run imports, so every step was proven by staging it
into a scratch work dir and importing from there: `suite_specs("visual")`
returns the group in its original order; both instruments resolve and both
delegates answer; the driver package imports with Uranium's one name stubbed
and its verb set is the same 53 names, none lost and none gained.

That was not enough. **Both pushed Batch I commits failed the repeat-boot smoke
leg**, for one reason: the staging replaced the whole staged `scenarios`
directory. The container imports the package as root, so Python writes
root-owned `__pycache__` beside the sources, and the host's `rm -rf` then fails
on every `.pyc` — the second unit could not stage at all. Every other tree the
script removes under the work dir is host-owned or removed through the
container's own root; this one was neither. The modules are copied into the
directory now.

This is exactly the failure class that leg exists for — its own comment names
it, from Cura's owner-only config writes breaking the next unit's cleanup — and
it is the sharpest statement of the brief's own warning: a unit test importing
a module in the checkout, and even a hand-staged import, is not proof that a
REPEATED run can stage it. The fix was reproduced in isolation first (stage,
import as root in a container, watch the `rm -rf` report the same per-file
"Permission denied"; then restage with the copy form over that same bytecode
and get 126 specs back), then confirmed on the real leg with both smoke units
passing.

### The test census

Batch I's acceptance asks that every old executable test be accounted for, so
the methods were counted by AST at both ends of the whole session — by AST, not
by running them, because a module that fails to import must not be able to drop
out of the census silently.

`a6f86ff`: 167 modules, 4386 test methods. `bd0b6ac`: 169 modules, 4435. **No
module lost a method.** The 49 additions are exactly: the painter's 23 and the
view policy's 18 as new modules, the compositor's 4 asset-predicate tests, the
harness specs' 2 (the enumerated re-export equals the probe module's own
constants; selecting a group yields its module's list in order), the harness
paths' 1 (the staging form and the no-remove rule) and the QML contracts' 1 (the
gates the face hands each visual leaf, and the scope handler's ordering). Every
retarget in this session moved an assertion to a new owner; none was weakened,
skipped, xfailed or deleted.

### Deliberately left

The runner's legs and its step dispatch stay with the runner. `suite_step` and
the step templates are the op vocabulary the whole suite is written against,
and `first_install1/2`, `two_boot_run`, `migration1/2` and `scenario1..11` are
procedures over the runner's own session primitives — `rpc`, `ensure_ready`,
`shot`, `scenario`, `write_evidence`, `harvest_cura_log`. Extracting them needs
either those primitives injected one by one or a module that imports the runner
back, and the second is the circular fragmentation the rules forbid. The
instruments were the leaf-ward half: the runner calls them and they never call
back. A coherent runner is preferable to legs that import their own caller.

`CAPTURE`, `CAPTURE_REASON` and `CAPTURE_T0` stay on the runner for the same
reason, and for one more: the tests set `runner.CAPTURE` directly, and moving
the name would leave them patching an alias the delegates no longer read —
which is the fixture-patching landmine, silently passing.

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
- The runner's legs and its step dispatch, and the capture gate, stay with the
  runner by decision rather than by omission — the reasoning is in Batch I's
  "Deliberately left".
- `tests/harness/runner.py` is 4391 lines and `tests/harness/driver/__init__.py`
  1957. Both are single coherent owners now (the legs over their session
  primitives; the socket, the RPC lifecycle and the GUI-thread dispatcher), so
  neither is a monolith waiting to be cut — but neither is small, and a future
  pass that wants the legs separated needs the session primitives to become an
  owner FIRST. That ordering is the whole of it: extracting legs before their
  primitives produces modules that import their own caller.
