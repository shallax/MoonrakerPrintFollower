# Modularity implementation

This tracks the work authorized after the [2026-10-20 audit](MODULARITY_AUDIT.md).
The audit records the original revision; this checklist records subsequent work.
Version remains 6.0.0. Merge/tag and a live Cura restart are not authorized here.

- [x] Extract raster painting, QML assets, worker handoff and PNG publication;
  expose raster and scene-graph consumer APIs. Commit `fd47a54`; full `make all`
  passed, including 6,264 tests and installed raster-only consumers.
- [ ] Finish raster asset-lifetime and render-job ownership.
- [x] Share recovery itinerary between serialization and confirmation; reject
  changed travel after review. Full `make all` passed (6,271 tests); independent
  command replay covers 60 route combinations, and real-QML review tests pass.
- [ ] Separate index content, residency/demand and application tracking; neutral
  cache identity and diagnostics.
- [ ] Extract runout-pause, follower-selection and recovery-source workflows.
- [ ] Extract settings validation and shared constraints from the Cura adapter.
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
