# v5.0.0 local failure detection: implementation handoff

The `release/v5.0.0` branch connects opt-in setup, per-printer
enablement and already-decoded Monitor frames to a local inference worker
and live signal: the pinned wheel and model install only after explicit
opt-in, and the per-printer control is the user's to save. An idle
Monitor webcam reports "Waiting for an active print" rather than green.

`ROADMAP.md` (the 5.0.0 section) is the binding product scope;
`INSTRUCTIONS.md` defines the build/test/ship procedures; `ARCHITECTURE.md`
describes component ownership. Follow the `new-feature` skill's snapshot
loop. The design was approved and the implementation directed with
Obico's model, acknowledging that three supplied *timelapse* failure
examples are not detected. The earlier proposal to evaluate another model
was explicitly withdrawn.

## Review and fix programme (2026-10-03)

This section records the earlier remediation history. The current release
contracts below supersede its original alert-budget and action-state details.

`release/v5.0.0` reached a fully green CI run on the remediation range
`206919a..cab099b` (all nine jobs: lint, screenshots, package, CodeQL,
three Pythons, both native builds, the repeat-boot smoke). A full
seven-persona panel — architecture, UX, engineering, product,
security/hardening, Klipper/Moonraker/Cura domain, and the pro-user —
then reviewed that range read-only. Every substantive finding is
recorded here with its disposition; the fixes land in the batches
below, each with its own tests and a full-gate run before the push.

### Decisions on the panel's findings

Fixed in this programme:

- Coverage gate mask (security, engineering): `statements <= 0` skipped
  a file whose statements were all excluded (`# pragma: no cover`), and
  `has_code()` aborted on binary or NUL-byte input instead of judging
  it. Both closed, with pins. Batch A.
- Mirror pin could not fail (engineering): the paint test filled one
  colour, invariant under mirroring. Asymmetric fill, flip asserted.
  Rewriting the pin then exposed a latent product bug it had been
  hiding: Qt 6.9+ renamed `QImage.mirrored()` to `flipped()`, and the
  no-argument classic call is a **silent no-op on Qt 6.11** (the
  renderer came back unflipped), so the camera mirror setting would
  die on any Cura bundling that Qt. `MoonrakerMJPGImage` now flips via
  `flipped(Qt.Orientation.Horizontal)` where it exists and
  `mirrored(True, False)` on Cura 5.13's pinned Qt 6.6. Batch A.
- Capture settle asymmetry (engineering): the section-position loop now
  raises when it never settles, like its scroll sibling. Batch A.
- README honesty (UX, product): the tab caption lists Detection, the
  ~193 MiB size and the AGPL/GPL licence relationship are stated,
  thresholds are "configurable" (not "adaptive"), switches are "off by
  default", and the clipped Detection/Diagnostics captures are
  regenerated against a fitted canvas. Batches A and F.
- Doc drift (architecture, domain): the harness-seed list, the capture
  output set and this document's model row (the decode divergence, not
  only the resize) and policy row (the 90-second cooldown is MPF's, in
  the Monitor model's alert path) corrected in Batch A; the pixel
  settle gets one owner beside the shared capture module, and the
  coverage gate becomes the single owner of both "what must be judged"
  and "what may be excused" in Batch E.
- Runtime probe before download (domain): a foreign `onnxruntime`
  already imported is now refused before the ~193 MiB fetch, with an
  actionable health reason. Batch B.
- Index-fetch redirect handler (security): the PyPI index fetch uses
  the same HTTPS-every-hop handler as the model download. Batch B.
- Unpinned extraction guards (security): symlink members, backslash
  names, the per-member cap, the foreign top-level package, directory
  modes, `load(runtime_directory=None)` and `existing.__file__ is
  None` are pinned or made fail-closed. Batch B.
- Temp-dir hygiene (security, engineering): detection tests stop
  creating scratch trees inside the checkout. Batch B.
- Safe-period banding (domain): the displayed score is clamped into
  the normal band while the safe period suppresses warnings, so a
  green "Normal" can no longer carry a 0.97 score. Batch C.
- Camera seam guard (domain): the per-frame receiver call is guarded
  against a raising or deleted receiver, which otherwise aborts Cura
  from inside a Qt slot; tested both ways. Batch C.
- Offer wiring (pro-user, domain): the one-time offer re-attaches its
  model connections on every monitor (re)install — the pattern the
  migration notice already uses — and the give-up path is observable,
  so the first-install leg can distinguish never-wired from
  timed-out. Batch C.
- Stale visibility (pro-user): a stale signal renders as a present,
  neutral state with its reason, never as nothing at all — staying
  within the neutral-not-green contract. Batch C.
- Notification reach (pro-user): the alert names the printer, re-arms
  in a bounded way while unacknowledged (escalation or a five-minute
  interval, at most three per print, persisted with the per-print
  record), carries an Acknowledge action, and shows a pending-alert
  indicator on the Failure Detection section header. Batch C. (The
  camera's Live badge was tried and REMOVED on review: that pill is
  about the stream being live and nothing else.)
- OS notification with the triggering frame (approved, then REMOVED
  on the live trial): it was built — Cura's own tray `showMessage` with
  the retained frame as its icon, gated on Cura not being the active
  window — and the live Windows run measured its limits: the first
  alert logged "Cura is the active window" (correct), the later ones
  logged "shown" while no balloon appeared, and Cura's tray path is not
  a channel users can rely on. Removed at the review's direction; the
  in-Cura `UM.Message` is the alert, and the evidence folder keeps the
  frame. Batches C and D, removed in G.
- Failure evidence and tuning record (pro-user, domain): the alert
  retains its triggering frame and a per-print score timeline in a
  bounded store, the measured benchmark milliseconds are surfaced, and
  Diagnostics reveals the evidence folder. Batch D.

Left as found, deliberately:

- The Monitor QML comment naming `INSTRUCTIONS.md` stays: it is inside
  the locked collapse/lock machinery and its text is pinned by a
  contract test; the name still resolves from `docs/`.
- Name-only document mentions in code comments and operator echoes are
  not updated: only path-resolving and linking references were in the
  move's contract, and the names still resolve.

### Implementation details

Batch A — this round's defects and text honesty:

- `tools/check_per_file_coverage.py`: `has_code()` catches `ValueError`
  (binary and NUL-byte input is judged, as its docstring promised); a
  file with code but zero measured statements now fails the gate
  (closing the blanket-`# pragma: no cover` mask); only
  `mpf/__main__.py` is skipped, so a subpackage entry point is judged;
  the docstring and the `EXCLUSIONS` comment now describe this gate's
  own audited table rather than the scenario map's schema.
- `tests/test_coverage_gate.py`: four new pins — the pragma mask, a
  Windows-separator json key finding its file, binary input judged
  rather than crashed, and the entry-point anchoring (the package's
  own skipped, a subpackage's judged).
- `tests/test_moonraker_mjpg.py`: the paint pin uses two colour halves
  and asserts the flipped pixel, so a dropped or inverted mirror fails.
- `tools/capture_monitor.py`: the section-position settle raises when
  it never settles (symmetry with the scroll loop).
- `.coveragerc`: the comment names `coverage json` as the aborting
  step, not `combine`.
- `README.md`, `docs/TESTING.md`, `docs/INSTRUCTIONS.md`,
  `tools/capture_settings.py`, `tests/qml_engine_support.py`: the text
  and scope corrections listed in the decisions above.

Batch B — small hardening:

- `AssetInstaller.wheel_url` fetches the PyPI index through the same
  HTTPS-at-every-hop opener as the wheel download (a named `_open_index`
  seam), so an intermediate hop can never downgrade the scheme; the
  existing CA and redirect pins drive that seam unchanged.
- `LocalFailureModel.foreign_runtime()` is the single check for an
  already-imported onnxruntime that is not the pinned install. It runs
  in `LocalDetectionService.setup()` **before** the download as well as
  at load, so a user with another runtime loaded is refused in
  milliseconds instead of after ~193 MiB. `load()` requires its runtime
  directory (fail-closed) and refuses a nameless imported module rather
  than raising TypeError from `__file__ is None`.
- New pins: symlink members, backslash and dot-dot member names, a
  foreign top-level package, the per-member cap (declared metadata via a
  fake archive — no bytes), the owner-only (0700) install modes, the
  required-directory refusal, the nameless-runtime refusal, and the
  pre-download probe (asserting no install was attempted).
- The detection tests create their scratch trees outside the checkout
  (`TemporaryDirectory()`), so a killed run leaves no `tmp*` debris in
  the working tree.
- One Windows nuance the native leg taught us: `zipfile.ZipInfo.__init__`
  rewrites `os.sep` to `/`, so on Windows a member name can never reach
  the extraction guard carrying a backslash — the pin now writes
  `.filename` back after construction to exercise that branch, and on a
  real Windows archive the dot-dot and top-level-package checks still
  catch the same hostile member. The owner-only (0700) pin skips on
  Windows, where `st_mode` is synthetic and ownership is an ACL.


Batch C — product fixes:

- Safe-period banding: `DetectionPolicy.observe` bands the displayed
  score by the level in **both** periods, not only after the safe
  period. A suppressed warning reports "normal", and a green state can
  no longer carry a 0.97. Pinned with a failure-sized signal inside the
  safe period (normal, score < warning) that then escalates once the
  period passes.
- Camera seam: `MoonrakerMJPGImage._deliver_detection_frame` contains
  the per-frame hand-off. An exception escaping the Qt install slot
  aborts Cura, so a raising receiver is logged once (a `_detection_
  faulted` latch) and stays attached — a skipped frame is worse for
  detection than a repeated call — while a receiver whose C++ side is
  gone (`sip.isdeleted`) is detached and the binding clears. Pinned both
  ways in `test_moonraker_mjpg`; the fixture receiver touches its C++
  side, because a pure-Python body on a deleted wrapper still runs and
  would test nothing.
- Offer wiring: `WhatsNewOverlay.attach_model(model)` takes the model's
  What's-New signals on every monitor install — the shape
  `MigrationNotice.attach_model` already uses — so a machine switch
  re-wires the offer instead of leaving it on a deposed model, and a
  model whose notes were already seen re-announces only the offer. The
  plugin calls it from `_grant_monitor_routing` beside the notice's own
  attach. `offer_state()` publishes `{wired, attempts, gave_up}` and the
  give-up latches `_gave_up`, so the leg can tell never-wired from
  timed-out — `wired` meaning the DISMISS path specifically, since
  that is what reveals the offer (a model attached without it reads
  unwired, not timed out).
- The first-install probe looked for the **Popup root's** objectName
  ("detectionFirstRunOffer"). A Popup is a QObject whose content
  reparents into the window overlay, so no visual-tree walk can ever
  see it: the offer was on screen (the 03b capture showed it) while the
  probe reported `named: []`. The witness is now
  `detectionOfferDismiss`, an item inside the popup — with the probe
  also reporting the offer's wiring. Verified on the leg: boot 1 9/9
  and boot 2 5/5, with `diag.named: [['detectionOfferDismiss', True]]`
  and `wiring {'wired': True, 'attempts': 56, 'gave_up': False}`.
- Stale visibility: the camera signal no longer disappears when the
  analysis goes stale. `signalStale` joins `signalShown`, the colour is
  the neutral `text_inactive` (never the live states' green), the bar
  shows "Stale" with no score marker, and the compact pill names the
  reason ("Camera analysis is stale"). The old pin that asserted a
  blank frame for stale was replaced by the present-and-neutral one.
- Notification reach: the alert names the printer ("Possible print
  failure on <printer> — check the camera"); it carries an
  **Acknowledge** action (`detectionAcknowledge`, delivered through
  `pyQtActionTriggered`) that runs the model's own acknowledgement and
  hides the toast; and while unacknowledged it re-raises on a 300 s
  interval up to 3 times per print (`DETECTION_ALERT_REPEAT_SECONDS`,
  `DETECTION_ALERT_MAX_PER_PRINT`), with the count and level persisted
  in the per-print record beside `alertedAt`/`acknowledgedAt`, so a
  Cura restart does not restart the budget. `detectionAlertLevel`
  exposes the standing alert's severity, and the section header
  (`sectionAlertDot`, amber/red by level, visible while collapsed)
  marks it. The camera's Live badge carries no alert mark: the review
  was explicit that the pill is about the stream being live and
  nothing else.

Batch D — failure evidence and the tuning record:

- `EvidenceStore.py` (new): the alert's evidence on disk, in
  `<detection root>/detection/evidence/`. `save_frame` writes the
  triggering frame as a JPEG named for its print (both identities are
  hash tokens, so a print's name can never shape a path) and keeps the
  newest `MAX_FRAMES` 12; `append_sample` appends one JSONL line per
  analysed frame to the print's timeline, rewriting the file only past
  twice its bound to trim back to `MAX_SAMPLES` 360 (an hour at the
  ten-second cadence), and keeps the newest `MAX_TIMELINES` 6 prints.
  Every writer is best-effort: a read-only or full disk returns "" and
  the alert proceeds. `clear()` empties the folder, and the asset
  removal path calls it — a removal is the user asking for nothing of
  detection's to stay on this computer.
- The model retains the newest frame it handed to detection (a
  reference, not a copy) and, when an alert fires, saves it *before*
  showing the message. Every
  analysed frame also lands in its print's timeline as
  `{at, score, raw}` from the policy's own state.
- The measured benchmark is kept, not discarded: `_load_and_benchmark`
  stores `benchmark_ms` on the service, the action exposes it, and the
  Diagnostics tab reports "measured N ms per frame" beside the
  **Show alert evidence folder** button (`revealDetectionEvidence`,
  `QDesktopServices.openUrl`) and its status line.
- `WhatsNewOverlay` moved from the Cura facade to `FollowerRuntime`,
  beside the migration notice it now parallels: the facade stays under
  its line budget and both overlays have one owner that closes them.
- New pins: the store's bounds and refusals, `clear()`'s idempotence,
  an alert that keeps its frame and timeline, the benchmark measured on a
  deliberately slow stand-in model, the removal emptying the folder,
  and the Diagnostics page reading the milliseconds and driving the
  reveal. Verified on the leg afterwards: boot 1 9/9, boot 2 5/5.

Batch E — the two refactors:

- `tools/capture_settle.py` (new) is the single owner of the pixel
  settle: pump, grab, require consecutive identical frames across a
  span, and RETURN the frame that proved it (so the proven frame and
  the saved frame cannot diverge). `capture_monitor.py`'s
  `settled_window` and `capture_settings.py`'s bespoke
  `detection-ready` transaction both call it now — the monitor passes
  its pending-layout-retry veto, the settings page its 0.5 s head
  start and 0.4 s span. Determinism re-verified after the hoist:
  "17 scenes byte-identical across two runs".
- `tools/exclusion_schema.py` (new) is the single audited exclusion
  path: reason / evidence / date / recheck, with the date now required
  to be a real ISO-8601 day. The per-file coverage gate validates its
  own table through it and `tests/test_coverage.py` validates the
  scenario map's through the same function, so both tables are judged
  by one code path instead of two copies of the field loop.

Batch F — the captures and the last gates:

- Why the Detection and Diagnostics captures were clipped: the
  settings capture's canvas fit had never run. `StackLayout.currentItem`
  is **null** on Cura's bundled engine (Qt writes it during the
  layout's own rearrange, which the offscreen pass skips), so the page
  lookup returned nothing and every capture kept the fixed 600-px
  canvas. The active page is now selected by `currentIndex` — the
  layout's children ARE the declared pages, in order.
- And why the measurement could not be trusted once it ran: the first
  reads after a tab switch report the PREVIOUS page's geometry, and
  two processEvents-only reads can agree while both are stale (the
  detection page measured 123 px twice, then settled at 598 px once
  real time passed). The fit now requires the value to hold across
  real-time pumps, and an unmeasurable or out-of-bounds height is a
  failure rather than the silent skip that produced the clipped
  images. `CAPTURE_HEIGHT_LIMIT` (1200) documents the ceiling: the
  Diagnostics page fits at 813.
- The fit then revealed Cura's placeholder text to the contrast
  census, which held it to the strict text floor and failed three
  upload-page labels at 1.87:1. Placeholders are muted by the theme
  exactly as disabled control text is, so `_is_placeholder` (text
  equal to an ancestor's `placeholderText` AND colour equal to that
  ancestor's `placeholderTextColor`) joins `_is_inactive` in the
  inactive class — the pair, not the string alone, so a real label
  repeating the string keeps the strict floor.
- All nine committed captures regenerated at their fitted heights
  (connection 657, following 697, upload 796, detection 598,
  detection-ready 598, diagnostics 813); the determinism gate still
  reports 17 scenes byte-identical across two runs.
- The plate prefix-ownership contract reads its takeover from the
  composition's own admission, never from a fringe count in the
  boundary column: that column carries the same three loose rows while
  the vector still owns the history, and the bed mapping's sub-pixel
  rounding takes it to two on a native rasteriser while the composition
  is admitted all the same. The composed-frame assertions are
  unchanged, and a genuinely missing prefix still fails the test.

Batch G — the detached anchor's ETA (the follow-up request):

- The follower popover now answers the question a detached user actually
  has: how long until the print reaches the point they are looking at.
  `NextPausePipeline.anchor_eta(layer, current, fraction)` reads it
  through the SAME collaborators a pause row reads — the index's
  per-layer timing, the observed speed ratio, `PreviewFormatting.
  pause_eta`'s countdown plus wall clock — and refuses in every case the
  pause rows refuse: a point behind the print, no anchor, no index
  timing.
- The point is the SELECTED one, not the layer's end (the review's
  ask): the two sliders read in the same units, so the layer slider
  picks the layer and the progress slider picks a share of its motions.
  `_anchor_point_fraction` turns that pair into the share of the layer's
  time still ahead — for a layer the print is already inside, measured
  from where the print IS, which is what makes the current layer read as
  a deadline. The pipeline interpolates between the layer's own two
  boundaries (start 0.0, end 1.0), clamped and never extrapolated, and a
  fraction of zero on the live layer means "where the print already is":
  nothing left to wait for.
- The coordinator computes it while the popover is DETACHED
  (`plate_anchor_eta` on the snapshot); the model publishes
  `plateAnchorEta` (cleared while attached — the live layer is the
  anchor then, and there is nothing to count down to); the popover
  renders it in the toolbar row's free slot — the same space the
  toolhead controls use — showing an em dash when there is no estimate.
  Detaching therefore cannot reflow the popover: those controls are
  attached-only and this is detached-only, so the row never grows (the
  first cut gave the ETA its own row and was corrected on review). The
  two slider tracks also moved into one group with no spacing between
  them, so the layer and its within-layer scrub read as one control.
- Pins: the pipeline's three refusals and its reading (pure, in
  `test_pause_at_layer.py`), the model's publish-while-detached /
  clear-while-attached contract (same file, end to end), and the
  popover row driven on the real engine (present with the estimate,
  em dash without, gone when attached) in
  `test_qml_plate_navigation.py`. Verified in real Cura: the status
  group ran 87/87 steps with the new detach and Layer ETA steps. The harness's b11 scenario — which
  previously exercised the popover only while attached — now detaches
  through the popover's own toggle and waits for the row and the
  model key, so the surface map keeps execution evidence rather than a
  bookkeeping entry.
- No capture changes: every capture scene renders the follower
  ATTACHED, and the row is detached-only, so the committed set is
  untouched (the QML test and the scenario are the evidence).

The programme's closing gate run, before the push:

- `make coverage`: the project total at 99%, every file over the 95%
  per-file bar (`EvidenceStore` needed two best-effort-branch pins; the
  alert-evidence test needed its context and camera seams pinned, or a
  mid-drive policy rebuild emptied its timeline under the coverage
  leg's slower clock).
- Harness legs: first-install 9/9 then 5/5 (verified twice, before and
  after the ownership move — run-2026-10-03-172446), suite webcams
  41/41 (run-2026-10-03-182116), suite settings 22/22
  (run-2026-10-03-182202) — all on an idle machine, each gallery's
  steps read from its own evidence file rather than the summary line.
- `make lint` green; `tools/compare_screenshots.py` reports only the
  six settings captures as changed (their fit is the change) and every
  other capture within the antialias tolerance.

## Non-negotiable behavior

- Run on the Cura computer. No account, cloud upload, manual server, Docker
  prerequisite, or extra camera connection. Detection is observation-only
  by default; notification and a guarded automatic pause require separate
  per-printer opt-ins. An explicit re-arm after cleanup can allow another
  pause during the same print. Never automatically cancel a print.
- Installation and eligibility are global/shared; enablement is per
  printer. A one-time eligible-host offer follows dismissal of What's New,
  but declining it leaves a settings entry point. No sizeable download
  before explicit consent. Show exact model/runtime sizes, modal progress
  with a flipping hourglass and working Cancel. Cancellation leaves detection unavailable; a locked loaded runtime is tombstoned and cleaned on the next restart.
- Check host support before offering; benchmark a *real model inference*
  after installation before enabling any checkbox. Failed checks have
  explicit, actionable health reasons. Manage any helper's lifecycle
  completely if a helper becomes necessary; in-process inference is
  preferable.
- Use sampled frames **already decoded** by the Monitor MJPEG renderer,
  a single global CPU budget, a latest-wins queue, and no inference on the
  GUI thread. Green requires an active print and a fresh analysed frame;
  idle, disabled, missing, not-yet-analysed, stale, or failed states are
  neutral, not green. Evidence needs hysteresis. The 0.00–1.00 score is a
  relative signal, **not** a calibrated probability.
- Put the green/amber/red border on the *actual Monitor webcam*. Its left
  scale uses a narrower two-decimal readout, uses linear marks
  with a center gap at 0.25 and 0.75, and never resizes the picture. On a
  small picture, the vertically centered in-image pill includes a two-decimal score and
  the state. The Diagnostics settings tab is last; every scrollable
  settings tab has a persistent visible scrollbar.

## Implemented in the worktree

Download progress is polled every 250 ms. The detection worker coalesces
consecutive byte-count updates and the GUI drains at most 16 mailbox entries
per tick, preserving lifecycle and result ordering. File-manager progress has
its own timer and amends only the progress payload, without composing file rows.
Removing detection downloads clears every printer's saved regions and closes
the active region editor. The detection scale supports full, line-only and
number-pill modes; leftward dragging collapses it, rightward dragging expands
the line, and clicking the pill restores the full scale.
The camera baseline readout shows the raw long-term mean. The confirmed Webcam
reset clears the selected camera's training across its region variants; the
confirmed Detection settings reset clears every camera on the selected printer.
Both clear means and sample counts, retire in-flight observations, and persist
through the same ordered worker lane as baseline updates. Regions, tuning and
other printers remain unchanged. Untrained cameras average their first six
analysed frames with an explicit neutral Learning baseline state and no score
or alerts. Subsequently the long-term mean uses a fixed 1/7200 update weight,
so a sudden change cannot immediately become the new normal. Partial learning
progress persists per camera across restarts and switches.
Baselines still span prints: different print geometry can shift the model's
background score, and a failure present during learning can contaminate the
mean. The manual reset does not establish automatic per-print calibration.

| Area | Files | Current contract |
| --- | --- | --- |
| Ownership | `mpf/monitor/MonitorDetection.py`, `MoonrakerMonitorModel.py` | A dedicated QObject owns detection. The monitor forwards settings, observations and intents through explicit camera, transport, storage and publication capabilities. |
| Tuning | `PrinterConfig.py`, `FailureDetectionSection.qml`, `DetectionPolicy.py` | Sensitivity is the primary control: 0.80–1.20 in 0.05 steps, default 1.00. It scales the adaptive EWM/long-baseline gap in classification and display. Advanced retains ordered lower/upper bounds (0.38/0.78 defaults). Reset restores sensitivity and both bounds. Safe start remains 0–900 seconds in ten-second steps, default 300. Each printer independently opts into analysis, notification and pause. |
| Regions | `geometry/DetectionRegions.py`, `detection/DetectionMask.py`, `monitor/camera/DetectionOverlay.qml` | Up to four simple polygons of 3–32 normalized raw-image points per camera. Empty regions mean full frame; malformed persisted regions inhibit analysis. Regions form a union. Source pixels outside it are blacked out **before resizing/inference**, and outside-only boxes are removed before confidence aggregation. One inference handles the union. The editor starts with rectangles, has draggable vertex handles and midpoint insertion, vertex/shape deletion and bounded Undo. It is a Save/Cancel transaction; editing suspends detection, Escape cancels, invalid polygons retain their vertices and show a reason. |
| Camera geometry and boxes | `CameraViewport.qml`, `MoonrakerMJPGImage.py`, `DetectionObservation.py` | The exact decoded frame carries its network-arrival age and source URL. An immutable result contains that frame and up to 50 retained normalized boxes. The overlay is a child of the actual image, inheriting rotation, mirroring, zoom and pan. Pointer coordinates map back through that same image. Boxes are clipped to monitored polygons and disappear when analysis is stale. An age pill distinguishes sampled analysis from live tracking. Showing boxes is a presentation preference. |
| Policy and baseline | `DetectionPolicy.py`, `MonitorDetection.py` | Obico-derived ten-second cadence, span-12 EWM, 310/7200-sample short/long streaming means, 3.8 short-mean multiple and 1.75 escalation. Baselines belong to printer + stable upstream camera identity (independent of bridge ports and snapshot mode; upstream query selectors are retained, with only known rotating transport parameters excluded) + canonical region fingerprint + mask preprocessing version; at most 32 variants are retained. Sensitivity/bounds edits retain the raw baseline but retire short-term evidence. Region changes start or restore their own baseline. A monotonic epoch rejects A→B→A, re-arm and disable/re-enable results. Freshness is measured from acquisition, not completion. |
| Model adapter | `LocalFailureModel.py` | Pinned CPU ONNX Runtime, one intra-/inter-op thread. NCHW float32 RGB [0,1], Obico's confidence >0.08 filter and 0.45 IoU non-max suppression; retained confidences are summed and may exceed one. Qt smooth resize and QImage JPEG decoding differ from Obico's OpenCV preprocessing. Shared weights and policy do **not** establish identical pixels or accuracy. The polygon union is masked and cropped to its outward-rounded pixel bounds before resize; model boxes are mapped back to full-frame coordinates before region filtering and NMS. No regions retains full-frame inference. ROI masking/cropping changes the input distribution and can create border artefacts; cropped regions use a new baseline namespace so full-frame-mask baselines are not reused. |
| Print identity and actions | `printing/PrintRunIdentity.py`, `MonitorDetection.py`, `MoonrakerSession.py`, `MonitorCommands.py` | The latest active Moonraker history row must match filename, `status=in_progress`, absent end time, server job ID and finite start time; printer binding scopes the durable identity. Local follower serials never form the persisted pause/mute key. History is re-attested every ten seconds; a new pause requires proof no more than two seconds old, measured from request issuance, and the usual fresh command permission. An in-flight request or failed/negative response revokes proof. A pending attempt with a unique command ID is saved **before dispatch**. A lost reply/timeout remains guarded across restarts; actual server refusal releases it. Re-arm must save before retiring old results and never commands the printer itself. Late callbacks cannot settle another attempt. |
| Mute | `MonitorDetection.py`, `FailureDetectionSection.qml` | “Mute for the rest of this print” suppresses notifications and new automatic pauses while analysis, baseline learning and boxes continue. It survives reconnect/restart after the same server run is attested and clears for a different run. Unmute requires fresh analysis. Re-arm does not unmute. Mute cannot recall an already dispatched pause. A potentially applicable saved mute is respected while identity is being resolved. |
| Alerts and evidence | `MonitorDetection.py`, `EvidenceStore.py` | One owned 60-second Cura toast, bound to its original run. Warning→failure upgrades immediately. Unacknowledged alerts repeat every 300 seconds, with at most three notifications per acknowledgement episode; acknowledging allows a fresh episode after the 90-second cooldown. Severity never downgrades while unacknowledged. Missing history may still permit notifications when no saved mute could apply; pause and persistent mute remain unavailable with an explicit reason. Exact triggering frames and timelines are submitted to a bounded worker queue. Evidence names use attested identity or a unique session fallback, preventing same-filename collisions. Storage uses one root, confined recursive cleanup, no-follow timeline opens and atomic frame replacement. |
| Service lifecycle | `LocalDetectionService.py`, `DetectionDownloadTransport.py`, `AssetInstaller.py` | Workers post to a Python mailbox drained by the GUI. Shutdown retires delivery promptly. Disabled startup does not verify/load/benchmark assets; disable releases the worker-owned session. Re-enable verifies and benchmarks before becoming operational. ORT RunOptions have a five-second cooperative termination watchdog; intentional retirement does not destroy readiness. Native import/session construction has no portable hard abort in frozen Cura. HTTPS receipt uses 100 ms socket polling plus active shutdown for cancellation and deadlines, including proxy CONNECT, TLS negotiation, initial/chunk headers and bodies. Polling avoids Windows pending-receive shutdown behaviour and retries healthy slow reads without poisoning SocketIO. Standard makefile references keep response sockets alive until their buffers close. The HTTPS handler preserves older Python hostname overrides and uses the SSL context on Python 3.12+, where that override was removed. Removal intent is saved before acceptance; locked runtime cleanup is retried on the next startup before importing ORT. Evidence cleanup is independent of locked DLLs. |
| Setup and diagnostics | `DetectionSettings.qml`, `MoonrakerFollowerMachineAction.py`, `DiagnosticsSettings.qml` | Shared consent-based pinned installation and host checks remain. No new account, cloud upload, Docker prerequisite or camera connection. Failure detection remains an assistant and never automatically cancels a print. Diagnostics reveals the evidence directory and measured benchmark cost. |

The release-hardening regression tests include durable print identity, pending/unknown pause outcomes, A→B→A results, out-of-order/stale observations, mute restoration, transactional polygons, masked input and box filtering, deliberate native cancellation, disabled startup, deferred deletion, symlink confinement and actively aborted header receipt. Release validation requires the complete suite, verified artifacts, canonical capture comparisons and the author's live snapshot test.

### Obico integration comparison

The local detector provides the core detection controls: per-printer analysis,
notification and pause opt-ins, sensitivity, safe start, acknowledgement and
current-print mute. Monitored polygons and sampled boxes are local additions.
It requires Cura to stay running and the selected camera feed to remain enabled.

| Area | Relationship to Obico |
| --- | --- |
| Analysis cadence | One selected frame every ten seconds (0.1 FPS), independently of live display FPS. Obico documents 0.1 FPS as the intended failure-detection cadence. A busy worker drops intermediate frames rather than building a backlog. |
| Model and temporal policy | Shared pinned Obico weights, confidence filtering/NMS and Obico-derived temporal adaptation. MPF exposes a 0.80–1.20 multiplier on the adaptive signal and retains its Advanced bounds. |
| Exact algorithm/accuracy | Not identical end to end: Qt decoding/resizing replaces OpenCV, the exported CPU ONNX execution differs, and polygon masking/cropping changes the input. The raw sum and displayed adaptive signal are not failure probabilities. Shared weights do not establish accuracy parity. |
| Local actions | Cura notifications, exact-frame evidence, acknowledgement, guarded automatic pause and persistent current-run mute. No automatic cancellation or heater changes. |
| Remote service features | Email/SMS/mobile push, always-on server monitoring, remote accounts, timelapse cloud analysis and first-layer AI are separate Obico service capabilities; this local Cura feature does not provide them. |

Obico's [cadence documentation](https://www.obico.io/docs/user-guides/webcam-stream-stuck-at-1-10-fps/)
and [failure-detection testing guide](https://www.obico.io/docs/user-guides/how-to-test-failure-detection/)
describe the sampling and adaptive warm-up expectations. The model adapter and
policy contracts above document MPF's implementation differences.


The first live setup uncovered two network assumptions: Cura's Python
needed its bundled `certifi` CA file for verified HTTPS, and Obico's model
URL redirected to a CDN asset. Model/runtime downloads now accept any
HTTPS redirect host (never an HTTP downgrade), while retaining verified
certificate chains, the pinned byte lengths and SHA-256 checks before
installation. Local inference has benchmarked successfully in Cura.

Three private MP4 failure timelapses were provided. Keep the footage
and extracted frames in the session/`/tmp/mpf` only; do not commit,
redistribute, upload, or use them as CI fixtures without confirming rights.
At two sampled frames/second, Obico's **exact upstream OpenCV**
preprocessing and pinned ONNX weight produced peak confidences of 30%, 15%,
and 5%. Denser sampling yielded maxima of 34% (all 101 frames at 10 fps),
14% and 13% (the other two at 5 fps). This is model evidence, not a
threshold-tuning target. The first clip's earlier frames often scored
higher than later frames; thresholds low enough to turn the other two red
would be misleading. Obico was nevertheless chosen because an
accelerated timelapse is not the same as monitoring a live feed.

Development-only artifacts are outside Git: `/tmp/mpf/model-weights.onnx`
(SHA-256 `0a6ebd8e30dbf6a450c50f9c0a5406f04ba7eb1c99fd5996e888c78bb383b9aa`),
private extracted frames under `/tmp/mpf/failure-clip-frames`, and a
development ONNX Runtime in `.venv` (1.30.0 for Python 3.14, not the
intended Cura runtime 1.23.2). The upstream model URL and matching SHA
are at `TheSpaghettiDetective/obico-server:ml_api/model/` in the
`model-weights.onnx.url` and `.sha256` sidecars. Its ONNX path is
`ml_api/lib/onnx.py`; it resizes BGR camera data with OpenCV bilinear,
converts to RGB NCHW float32/255 and reads single-class confidences.

## Archived pre-wiring checklist (completed in source)

1. **Harden the asset installer.** The extractor accepts an existing
   runtime against the pinned wheel, so a startup readiness gate and a
   demonstrated import of the pinned wheel in a *real Cura* Python 3.12
   process are required. Avoid a live import of a different system ONNX
   Runtime. Check NumPy availability/ABI and OS shared libraries,
   not just wheel filenames. Cancel must interrupt or promptly end an
   active read. Model corruption and exhausted disk must surface errors;
   never treat them as successful setup. Add platform/preflight and
   ZIP/redirect/cancellation tests. Host wheel coverage alone does not
   prove native runtime compatibility.
2. **Make one global service.** Construct it in `FollowerRuntime`,
   expose it through `MoonrakerPrintFollower` to the machine action and
   output-device monitor, and close it from `FollowerRuntime.close()`.
   Keep assets under Cura Preferences app-data, not the package or a
   per-printer cache. Drive installation in a bounded background worker
   with Qt queued progress/completion signals; keep one process-wide
   install/inference lane. Persist one-time offer/decline and successful
   benchmark globally through `PluginPersistence.set_global`, and record
   health reasons. Benchmark with an actual model input after verified
   setup; time and cap CPU use. Do not instantiate/import native
   libraries merely to render the settings page.
3. **First-run and settings entry points.** `WhatsNewOverlay.py` owns
   the boot-ready main-window/QML-engine route; the monitor model emits
   `whatsNewDismissed`. Show the offer after its dismissal, or at boot
   when What's New is already seen, once per installation and only on
   eligible hosts. A declined offer remains available from settings.
   Wire the dedicated tab to live service health, consent, progress and
   Cancel; the progress should follow the existing file-manager
   `DownloadProgressDialog.qml` presentation and not share its
   printer-specific transfer lane. Remove misleading sample labels
   and indicators for prerequisites not actually required.
4. **Per-printer persistence.** Add `detection_enabled: bool = False`
   to `PrinterConfig` with safe `from_dict` coercion. Include it in the
   settings action's `saveConfig` validation and the Detection tab's
   `values` block; update the field-ownership/union tests and settings
   serialization tests. Refuse enabling until global model+benchmark
   health and a selected working camera exist; display the refusal,
   do not silently save an ineffective checkmark.
5. **Camera input and output.** `MoonrakerMJPGImage._on_decoded`
   installs already-decoded `QImage` values on the GUI thread. Sample
   that point no faster than the global cadence, pass an implicitly
   shared read-only image to a worker, and do any resize/inference in
   that worker. Do **not** create a second camera HTTP connection.
   Use the active monitor's selected-camera identity from
   `MonitorCamera`, printer identity, `print_stats.state == "printing"`
   and coordinator job identity; reset policy and discard late work
   on camera/printer/job/session change. Results cross a queued Qt
   signal to `MoonrakerMonitorModel` with a generation check. Publish
   score/state/freshness by typed model properties and connect
   `CameraPane`/`CameraViewport` to them, replacing the sample selector.
   A timer must demote a stale signal to neutral even when no frames
   arrive. Disablement, idle prints and Cura close retire outstanding
   work and native resources without GUI-thread stalls.
6. **Prove the actual behavior.** Unit-test healthy/warning/failure
   hysteresis, print/camera switches, stopped frames, cancellation,
   corrupt hashes, unsupported hosts, install reuse and errors. Add
   real-QML tests of live model properties rather than synthetic
   `previewSignalState`. Replay a permission-cleared recording through
   the local Moonraker MJPEG simulator; the private timelapses are not
   redistributable fixtures and are for local manual scoring only.
   Run targeted `make test_files FILES="tests.test_detection_policy
   tests.test_detection_model tests.test_detection_assets
   tests.test_qml_camera_controls tests.test_qml_settings"` and then
   `make all` before any commit/push. Keep one process per test file.
   Regenerate the real Monitor and settings screenshots and
   `make snapshot_package`. PRs are created only on explicit request.

## Release gates and cautions

- Obico's maintainer stated that weights carry the server's AGPL-3.0
  licence (see `ROADMAP.md` for the upstream issue). Both setup offers
  link to Obico's AGPLv3 text and explain that GPLv3 section 13 permits
  combining it with MPF's GPLv3, but AGPL obligations still apply.
  Licence notices, source obligations and redistribution permissions are
  explicit decisions, never inferred. The plugin must not silently bundle
  the model into a Marketplace package.
- Post-install native downloads require Ultimaker's written acceptance
  for the Marketplace; local success is not approval.
- Performance and native runtime must be tested in real Cura versions
  and supported operating systems, not only the Python 3.14 dev venv.
- Install into Cura only from a verified package, with Cura quit.
