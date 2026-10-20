# Modularity implementation

This tracks the work authorized after the [2026-10-20 audit](MODULARITY_AUDIT.md).
The audit records the original revision; this checklist records subsequent work.
Version remains 6.0.0. Merge/tag and a live Cura restart are not authorized here.

- [x] Extract raster painting, QML assets, worker handoff and PNG publication;
  expose raster and scene-graph consumer APIs. Commit `fd47a54`; full `make all`
  passed, including 6,264 tests and installed raster-only consumers.
- [x] Finish raster asset-lifetime and render-job ownership. `RasterAssets` owns
  publication, references, holds and scoped cleanup; named receipts retain the
  existing queued-signal values. Late publication cannot recreate a closed
  owner's directory. Full `make all` passed with 6,287 tests, deterministic
  captures, package verification and installed consumers.
- [x] Share recovery itinerary between serialization and confirmation; reject
  changed travel after review. Full `make all` passed (6,271 tests); independent
  command replay covers 60 route combinations, and real-QML review tests pass.
- [x] Separate index content, residency/demand and application tracking.
  Host runtime modules live in `application/indexing`; cache identity and
  diagnostics are portable. Parsed indexes no longer carry live/manual anchors;
  workers receive generation-scoped `IndexResidency` capabilities. Live matching
  belongs to `IndexMotionTracking`. Full `make all` passed with 6,280 tests;
  coverage is 97.57%, with every production file at least 95%. Cache API exports
  use `gcode.cache` to preserve unambiguous implementation-module imports.
- [x] Extract runout-pause, follower-selection and recovery-source workflows.
  The three owners accept explicit capabilities and can be exercised without
  constructing the Monitor; adapters retain rendering epochs, publication and
  index/download service adoption.
- [x] Extract settings validation and shared constraints from the Cura adapter.
  `ConfigValidation` accepts a draft, current values and discovered choices;
  field refusals precede publication/persistence. Shared recovery limits and
  follow modes have portable owners; strict drafts retain their own refusal
  policy, independently of tolerant stored-setting migration.
- [x] Separate follower gesture, render-delivery and overlay QML ownership.
  View state owns transforms, input and focal animation; delivery state owns
  transactions, receipts, standing assets and file references; the overlay owns
  its activation and playback. Components use explicit ports/methods/signals,
  with no shared face/root reads. Public view properties remain facade aliases
  to their owner. Independent component and existing pixel/lifetime tests pass.
- [x] Separate toolhead import jobs and typed appearance edit transactions.
  `ToolheadImportJobs` owns worker occupancy, cancellation and named results;
  `AppearanceDraft` owns editable values, model reset and identity/mesh-bound
  checkpoints. Publication and active-printer adoption remain with the Qt
  presenter. Standalone owner and real-QML editor tests preserve both paths.
- [x] Separate detection action state and effects from host notifications.
  `DetectionRun` owns attestation and history request fences. `DetectionActions`
  owns per-run latches/alerts and emits named effects with durable-save barriers.
  The Monitor bridge applies effects and owns Qt notifications; portable tests
  cover repeat/escalation, acknowledgement, uncertainty, failed persistence and
  reentrant refresh without constructing Uranium UI.
- [x] Consolidate process-liveness, streaming multipart uploads, printer object
  classification and recovery lexical/refusal interfaces. `MultipartUpload` owns
  streaming file devices, bounded response bytes, cancellation/progress and
  terminal results for both consumers; readiness/start-print policy remains
  with its workflow. Reply-body reading now has one network-domain owner.
- [x] Extract Preview object-tag presentation and scene reads. Plain-value
  matching, progress, fallback heights and footprints are independent of host
  nodes and vectors. Bed-mesh sampling/geometry now
  belongs to a host-independent NumPy array builder, used by the scene adapter
  and the installed library consumer.
- [x] Add cross-language preview-colour conformance checks. Real QML runs the
  production JavaScript against shared reference values and Python boundary
  cases. Package-qualified architectural/consumer enforcement accompanies the
  extractions.
- [x] Synchronize architecture, README, roadmap, What's New and changed source
  headers; retain 6.0.0 throughout.
- [ ] Full local checks, coverage at least 95%, PR CI and UI sweep on final head.

Every code extraction retains behaviour tests and adds the narrower boundary
proof appropriate to it. Moving functions without transferring state ownership
or replacing private/shared-owner access does not satisfy an item.

Shared-primitives progress: object families/fan writability now live in
`moonraker.PrinterObjects`; source lexical definitions are public; recovery
refusals carry ordered command evidence independently of setup navigation.
Streaming multipart ownership is shared by both consumers and independently
qualified for bounded replies, reentrant aborts, shutdown and start failures.

The settings/workflow batch passed full `make all` with 6,300 tests. Coverage
is 97.72%; every production file clears 95%, and the new validation, workflow,
residency/tracking and raster-lifetime modules have 100% statement coverage.

Combined local qualification (2026-10-20): full `make all JOBS=8` passed
6,341 tests in 295 isolated processes, lint, deterministic capture pairs,
verified plugin/source packages and isolated installed core/raster/Qt consumers.
`make coverage JOBS=6` passed at 97.7719% (39,668/40,572 statements). Every
regular production module clears its 95% floor; the existing isolated native
STEP worker retains its separate mandatory CAD-runtime coverage gate in CI.
Final-head PR CI and supported-version UI sweep remain pending.

The cold-start harness follow-up passed full `make all JOBS=8` with 6,342
tests in 295 isolated processes and the same capture/package/library gates.
Only the initial read-only handshake uses the 90-second window-startup budget;
normal action deadlines and no-replay failure handling remain qualified.
Production code and the coverage measurement above are unchanged.
