# v5.0.0 local failure detection: implementation handoff

**Status (2026-10-03): local setup and idle-state monitoring validated in Cura.**
The `release/v5.0.0` branch now connects opt-in setup, per-printer
enablement and already-decoded Monitor frames to a local inference worker
and live signal. Cura 5.13 on macOS arm64 installed the pinned wheel and
model after explicit opt-in and completed the real native benchmark; the
Voron2 250 per-printer checkbox was saved with explicit approval.
Its idle Monitor webcam reports "Waiting for an active print" rather than
green. An active-print camera trial and a live verdict remain open.
The development candidate uses 5.0.0 metadata; a branch push is not a
release, and no PR or release tag has been created.

`ROADMAP.md` (the 5.0.0 section) is the binding product scope;
`INSTRUCTIONS.md` defines the build/test/ship procedures; `ARCHITECTURE.md`
describes component ownership. Follow the `new-feature` skill's snapshot
loop. The design was approved and the implementation directed with
Obico's model, acknowledging that three supplied *timelapse* failure
examples are not detected. The earlier proposal to evaluate another model
was explicitly withdrawn.

## Review and fix programme (2026-10-03)

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
  indicator on the section header and the camera pill. Batch C.
- OS notification with the triggering frame (approved): a best-effort
  desktop notification (tray `showMessage` with the retained frame as
  its icon) fires when Cura is not the foreground window, falling back
  to the in-Cura message; it rides the existing per-printer notify
  opt-in. The image renders as the notification icon on Linux and
  Windows; macOS support is attempted and falls back. Batches C and D.
- Failure evidence and tuning record (pro-user, domain): the alert
  retains its triggering frame and a per-print score timeline in a
  bounded store, the measured benchmark milliseconds are surfaced, and
  Diagnostics reveals the evidence folder. Batch D.

External or live-test items, not code:

- AGPL obligations and the written Ultimaker Marketplace policy for
  post-install native downloads: still the release gate; the README
  now discloses the licence relationship where a reader sees it.
- The live active-print trial: still required before the colour claim
  in the README is proven; no permission-cleared recording exists.
- Per-OS verification of the desktop notification: the code path is
  pinned by tests on every OS (`DesktopAlert`'s four gates and the
  frame it carries), but no real desktop has SEEN one — this
  environment has no notification daemon — so Linux, Windows and
  macOS all need a live run before the channel is promised. The
  in-Cura message is shown regardless, and the README's wording
  ("where the platform supports it") promises nothing per OS.

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
  exposes the standing alert's severity, and both the section header
  (`sectionAlertDot`, amber/red by level, visible while collapsed) and
  the camera Live badge (`cameraAlertPendingDot`) mark it.

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
  notifying so the desktop alert has a real path to carry. Every
  analysed frame also lands in its print's timeline as
  `{at, score, raw}` from the policy's own state.
- `DesktopAlert.py` (new): the desktop notification for an alert raised
  while Cura is not the foreground window (`applicationState()` is not
  `ApplicationActive`). It uses Cura's own tray widget — the one Cura
  keeps for pop-up messages and shows when the main window is
  minimised — with the retained frame as its icon, mirroring Uranium's
  own (deprecated) toast path. It is best-effort by contract: no tray,
  a raising platform or a macOS daemon that ignores the image all fall
  through to the in-Cura `UM.Message`, which is shown either way.
- The measured benchmark is kept, not discarded: `_load_and_benchmark`
  stores `benchmark_ms` on the service, the action exposes it, and the
  Diagnostics tab reports "measured N ms per frame" beside the
  **Show alert evidence folder** button (`revealDetectionEvidence`,
  `QDesktopServices.openUrl`) and its status line.
- `WhatsNewOverlay` moved from the Cura facade to `FollowerRuntime`,
  beside the migration notice it now parallels: the facade stays under
  its line budget and both overlays have one owner that closes them.
- New pins: the store's bounds and refusals, `clear()`'s idempotence,
  the desktop alert's four gates (active Cura, inactive, no tray, a
  raising tray), an alert that keeps its frame and timeline and passes
  the frame's path to the notification, the benchmark measured on a
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

The programme's closing gate run, before the push:

- `make coverage`: the project total at 99%, every file over the 95%
  per-file bar (`EvidenceStore` needed two best-effort-branch pins; the
  alert-evidence test needed its context and camera seams pinned, or a
  mid-drive policy rebuild emptied its timeline under the coverage
  leg's slower clock).
- Harness legs: first-install 9/9 then 5/5 (verified twice, before and
  after the ownership move), suite webcams 41/41, suite settings
  22/22 — all on an idle machine, each gallery's steps read from its
  own evidence file rather than the summary line.
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
  with a flipping hourglass and working Cancel. Cancellation leaves all
  printers disabled and no incomplete installed asset.
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
  scale matches the existing right-side zoom/FPS bar, uses linear marks
  with a center gap at 0.25 and 0.75, and never resizes the picture. On a
  small picture, the vertically centered in-image pill includes a two-decimal score and
  the state. The Diagnostics settings tab is last; every scrollable
  settings tab has a persistent visible scrollbar.

## Implemented in the worktree

| Area | Files | Current contract |
| --- | --- | --- |
| Settings and controls | `mpf/settings/DetectionSettings.qml`, `mpf/monitor/controls/FailureDetectionSection.qml`, `mpf/cura/MoonrakerFollowerMachineAction.py`, `mpf/settings/PrinterConfig.py` | Detection settings owns shared setup, progress, Cancel, a global enable switch that defaults on after setup, and the safety, signal, and safe-period explanations. Turning the switch off stops analysis/alerts and hides the Printer controls section without erasing per-printer choices. The compact Printer controls section owns per-printer enable, ordered adaptive bounds displayed from 0.00 to 1.00 in hundredths (Obico 0.38/0.78 defaults), a safe-period slider (0–15 minutes in ten-second steps; default five minutes), off-by-default notify/pause opt-ins and alert acknowledgment. Three solid slider segments reuse bed-mesh gestures without changing its rainbow default; no controls are enabled until the shared model is ready. Save validates bounds and ordering; load repairs a corrupt pair together. Diagnostics separately resets onboarding markers or removes shared assets and disables detection/actions for all saved printers. |
| Actual camera | `mpf/monitor/camera/CameraPane.qml`, `CameraViewport.qml`, `FailureSignalBar.qml`, `MoonrakerMJPGImage.py` | The existing decoded-frame seam feeds inference; typed model state colours the real camera. No sample selector or extra camera connection. |
| Settings usability | Five `mpf/settings/*Settings.qml` pages | Themed attached `UM.ScrollBar`, automatically always visible when scrolling is possible; fixed reserved gutter. |
| Signal policy | `mpf/detection/DetectionPolicy.py` | Obico's ten-second cadence, configurable elapsed-print-time safe start (five-minute default), span-12 EWM, 310/7200-sample streaming short/long baselines, 3.8 short-mean multiple, 1.75 escalation, and the 0.38/0.78 low/high defaults. The 90-second alert/acknowledgement cooldown and the one-pause-per-print latch are MPF's own, in the Monitor model's alert path, not in this policy. Threshold or safe-period edits retire old evidence and in-flight results; long-term baselines persist separately per printer and camera. The old mixed-camera baseline is ignored. The displayed two-decimal signal maps warning/failure to the selected slider positions, but is not calibrated model confidence. Waiting for the first analysed frame keeps a grey camera border, "Wait" in the scale and no score marker. `state()` reports idle/waiting/stale/normal/warning/failure. |
| Model adapter | `mpf/detection/LocalFailureModel.py` | CPU-only ONNX Runtime session with one intra-/inter-op thread. `QImage` becomes NCHW float32 RGB [0,1], resized to the model's input shape; Obico's 0.08 score filter and 0.45 IoU non-max suppression retain boxes whose confidences are summed, potentially above 1. It uses Qt smooth resize, **not** exact OpenCV INTER_LINEAR, and its frames are decoded by Cura's `QImageReader` rather than Obico's `cv2.imdecode` — both differ measurably from upstream, so scores are not bit-comparable with a self-hosted Obico's. Do not assert bit-for-bit upstream preprocessing equivalence. |
| Asset metadata | `mpf/detection/DetectionAssets.py` | Model URL, upstream-verified SHA-256, 202,223,918-byte size; SHA-256 and sizes for 15 ONNX Runtime 1.23.2 wheels (Python 3.10–3.12, macOS 13+ arm64/x86_64, glibc 2.27+ Linux aarch64/x86_64, Windows 10+ amd64). Reject unsupported host and less than 4 GiB physical RAM. |
| Asset operations | `mpf/detection/AssetInstaller.py`, `LocalDetectionService.py` | Consent-only pinned downloads, worker-owned installation, startup integrity checks, real inference benchmark and global readiness persistence. |
| Boot offer | `mpf/detection/DetectionOffer.qml`, `mpf/whatsnew/WhatsNewOverlay.py` | One-time global opt-in after What's New; later settings setup remains available. Diagnostics can reset both offer markers for the next launch without downloading the already-installed model again. |
| Executable proof | `tests/test_detection_policy.py`, `test_detection_model.py`, `test_detection_assets.py`, `test_detection_service.py`, `test_monitor_model_runtime.py`, `test_qml_camera_controls.py`, `test_qml_settings.py` | Pure backend and real-QML tests, including live model state and camera/print/staleness transitions. The integrated detection controls, asset-removal, monitor, and QML gate passed 247 tests in seven independent processes. |

The first live setup uncovered two network assumptions: Cura's Python
needed its bundled `certifi` CA file for verified HTTPS, and Obico's model
URL redirected to a CDN asset. Model/runtime downloads now accept any
HTTPS redirect host (never an HTTP downgrade), while retaining verified
certificate chains, the pinned byte lengths and SHA-256 checks before
installation. The latest snapshot, including the
adaptive controls, configurable safe period and worker-safe asset reset, was verified against the
package and installed in quit Cura before relaunch. The prior plugin is
backed up in the Cura configuration directory. Local inference previously
benchmarked successfully in Cura; an active-print trial is still pending.

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

## Remaining gates as of this integration

1. Cura 5.13 on macOS arm64 passed installation and two real model inference
   calls; other supported operating systems and Python ABIs remain unqualified.
   The in-process benchmark reports a completed call over five seconds but
   cannot forcibly stop a native call that hangs.
2. Test cancellation and active-print live camera response in a live Cura session;
   regenerate screenshots from that snapshot. No normal-to-failure recording
   has been cleared for redistribution, so the simulator video-replay gate
   remains open. Private timelapses are not test fixtures and are known
   false negatives.
3. Resolve Obico's AGPL obligations and written Ultimaker Marketplace policy
   on native post-install downloads before shipping. Once the live build is accepted,
   bump all version surfaces together, complete release
   re-review and gates, and create a PR only on explicit request.

## Archived pre-wiring checklist (completed in source except noted above)

1. **Harden the asset installer.** The current extractor accepts an
   existing runtime against the pinned wheel, but there is not yet a
   startup readiness gate or a demonstrated import in real Cura. Avoid
   a live import of a different system ONNX Runtime and
   demonstrate a clean import of the pinned wheel in a *real Cura* Python
   3.12 process. Check NumPy availability/ABI and OS shared libraries,
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
   the local Moonraker MJPEG simulator; the private timelapses can be
   used only for local manual scoring pending rights confirmation.
   Run targeted `make test_files FILES="tests.test_detection_policy
   tests.test_detection_model tests.test_detection_assets
   tests.test_qml_camera_controls tests.test_qml_settings"` and then
   `make all` before any commit/push. Keep one process per test file.
   Regenerate the real Monitor and settings screenshots and
   `make snapshot_package`; obtain a live Cura verdict
   before committing or shipping. No PR until explicitly requested.

## Release gates and cautions

- Obico's maintainer stated that weights carry the server's AGPL-3.0
  licence (see `ROADMAP.md` for the upstream issue). Both setup offers
  link to Obico's AGPLv3 text and explain that GPLv3 section 13 permits
  combining it with MPF's GPLv3, but AGPL obligations still apply.
  Legal compliance, licence notices/source obligations and redistribution permissions
  need explicit review. The plugin must not silently bundle the model
  into a Marketplace package.
- Written Ultimaker Marketplace acceptance of post-install native
  downloads is pending. Do not infer approval from local success.
- Performance and native runtime must be tested in real Cura versions
  and supported operating systems, not only the Python 3.14 dev venv.
- Cura 5.13 now runs the functional snapshot with adaptive controls, a
  configurable safe period and asset-reset hardening; it was installed while Cura was quit. Future
  code changes require another verified package and a fresh, safe install
  while Cura is quit. An active-print trial has not happened.
