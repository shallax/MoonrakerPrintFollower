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
- [ ] Separate follower gesture, render-delivery and overlay QML ownership.
- [ ] Separate toolhead import jobs and typed appearance edit transactions.
- [ ] Separate detection action state and effects from host notifications.
- [ ] Consolidate process-liveness, streaming multipart uploads, printer object
  classification and recovery lexical/refusal interfaces.
- [ ] Extract Preview object-tag presentation and bed-mesh sampling/geometry.
- [ ] Add cross-language preview-colour conformance checks and package-qualified
  architectural/consumer enforcement with each extraction.
- [ ] Synchronize architecture, README, roadmap, What's New and changed source
  headers; retain 6.0.0 throughout.
- [ ] Full local checks, coverage at least 95%, PR CI and UI sweep on final head.

Every code extraction retains behaviour tests and adds the narrower boundary
proof appropriate to it. Moving functions without transferring state ownership
or replacing private/shared-owner access does not satisfy an item.
