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
| Signal policy | `mpf/detection/DetectionPolicy.py` | Obico's ten-second cadence, configurable elapsed-print-time safe start (five-minute default), span-12 EWM, 310/7200-sample streaming short/long baselines, 3.8 short-mean multiple, 1.75 escalation, adaptive 0.38/0.78 low/high defaults and 90-second alert/ack cooldown. Threshold or safe-period edits retire old evidence and in-flight results; long-term baselines persist separately per printer and camera. The old mixed-camera baseline is ignored. The displayed two-decimal signal maps warning/failure to the selected slider positions, but is not calibrated model confidence. Waiting for the first analysed frame keeps a grey camera border, "Wait" in the scale and no score marker. `state()` reports idle/waiting/stale/normal/warning/failure. |
| Model adapter | `mpf/detection/LocalFailureModel.py` | CPU-only ONNX Runtime session with one intra-/inter-op thread. `QImage` becomes NCHW float32 RGB [0,1], resized to the model's input shape; Obico's 0.08 score filter and 0.45 IoU non-max suppression retain boxes whose confidences are summed, potentially above 1. It uses Qt smooth resize, **not** exact OpenCV INTER_LINEAR; results differ measurably. Do not assert bit-for-bit upstream preprocessing equivalence. |
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
