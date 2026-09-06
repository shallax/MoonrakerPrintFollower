# Changelog

Moonraker Print Follower is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).

## 2.0.0

Version 2.0.0 makes Moonraker Print Follower feel like part of Cura rather than a separate utility.

### Highlights
- Configuration moves into **Settings → Printer → Manage Printers → Configure Moonraker Follower**.
- Full per-printer settings and single-active-printer behaviour.
- Targets Cura 5.x / SDK 8.x.
- Improved live nozzle handling in Preview using Cura's native nozzle model.
- Smoother monotonic within-layer following, avoiding visible rewind/retrace behaviour around ambiguous motion and layer changes.
- Retains multiple follow modes, resilient Moonraker polling and scalable large-G-code indexing.
- Existing 1.x settings are migrated automatically.

## 1.1.0

Version 1.1.0 moves Moonraker Print Follower from a single global setup to a proper per-printer Cura workflow.

### Highlights
- Separate Moonraker connection and following settings for each Cura printer.
- Automatic migration of existing 1.0.x settings.
- New follow modes: exact current layer, last completed layer, one-layer look-ahead and a layer window around the live layer.
- More resilient Moonraker polling with automatic retry backoff.
- Built-in connection testing and capability detection.
- Better handling of very large G-code files through compact indexing and on-demand detail loading.
- Refined Cura-styled Preview controls and clearer live status.

## 1.0.3

Version 1.0.3 is a performance and accuracy release aimed particularly at larger G-code files and long-running prints.

### Highlights
- Streams G-code downloads and indexing instead of holding the complete file in memory.
- Adds persistent, validated path indexes so repeated loads can be much faster.
- Uses Moonraker motion data when available to better match Cura's nozzle position to the physical printer.
- Improves layer mapping using information embedded in the G-code itself.
- Strengthens manual Preview override detection and stale-work protection.

## 1.0.2

Version 1.0.2 focuses on making following behave predictably while Cura is loading, slicing or changing scenes.

### Highlights
- Safer handling of Cura scene changes and slicing, reducing stale or out-of-order Preview updates.
- More reliable manual-override detection when Cura rebuilds its Preview components.
- Improved cleanup and cancellation of downloads, network requests and background indexing.
- Better protection against reusing stale data when the same G-code filename is printed again.
- Lower memory overhead while indexing large G-code files.

## 1.0.1

This release makes it much easier to inspect a print without fighting the follower.

### Highlights
- Moving Cura's layer or toolpath slider manually now suspends automatic following.
- Resuming following catches Preview back up to the live print.
- Plugin-driven Preview movement is distinguished from user interaction, avoiding false pauses.

## 1.0.0

Moonraker Print Follower brings a live Klipper/Moonraker print into Cura Preview.

### Highlights
- Follow the printer's current layer and progress through the active layer in Cura Preview.
- Load the G-code currently printing on Moonraker into Cura on demand.
- Pause and resume Preview following without pausing the printer itself.
- Configure Moonraker connection details, polling, layer handling and Preview behaviour.

This is the original 1.0 release of the plugin. The historical metadata has been corrected from an accidental `1.0.4` to `1.0.0`.

## Development history

- 2026-09-06: Run pause management polish
- 2026-09-06: Stage pause management polish
- 2026-09-06: Remove temporary pause-at-layer workflow
- 2026-09-06: Add Preview pause-at-layer scheduling
- 2026-09-06: Run Preview pause at layer
- 2026-09-06: Fix pause-at-layer patch transport
- 2026-09-06: Run Preview pause at layer
- 2026-09-06: Stage Preview pause-at-layer implementation
- 2026-09-06: Remove temporary slider debounce workflow
- 2026-09-06: Debounce Monitor sliders before apply
- 2026-09-06: Run slider debounce
- 2026-09-06: Stage slider debounce test fixups
- 2026-09-06: Run slider debounce
- 2026-09-06: Stage slider debounce workflow
- 2026-09-06: Stage slider debounce implementation
- 2026-09-06: Remove temporary release UX workflow
- 2026-09-06: Complete v3 release UX and architecture audit
- 2026-09-06: Run final release UX refactor
- 2026-09-06: Run final release UX refactor
- 2026-09-06: Add release UX refactor fixups
- 2026-09-06: Run final release UX refactor
- 2026-09-06: Stage release UX and architecture audit
- 2026-09-06: Remove temporary release-hardening workflow
- 2026-09-06: Remove temporary release-hardening workflow
- 2026-09-06: Remove temporary release-hardening workflow
- 2026-09-06: Harden v3 release CI
- 2026-09-06: Harden v3.0.0 release candidate
- 2026-09-06: Apply v3 hardening without workflow-token writes
- 2026-09-06: Retry v3 release hardening with staged hygiene
- 2026-09-06: Run v3 release hardening audit
- 2026-09-06: Stage v3 release hardening audit
- 2026-09-06: Remove corrected slider QML CI trigger
- 2026-09-06: Trigger corrected slider QML package build
- 2026-09-06: Fix duplicate slider QML properties
- 2026-09-06: Run slider QML hotfix
- 2026-09-06: Add helper to fix duplicate slider QML properties
- 2026-09-06: Remove deferred slider CI trigger
- 2026-09-06: Trigger deferred slider package CI
- 2026-09-06: Defer Monitor slider commits until release
- 2026-09-06: Run deferred slider patch
- 2026-09-06: Add deferred slider patch helper
- 2026-09-06: Remove final control-fix CI trigger
- 2026-09-06: Trigger final control-fix package CI
- 2026-09-06: Fix dynamic tuning and pin emergency controls
- 2026-09-06: Run v3 control fix helper
- 2026-09-06: Stage v3 control fix helper
- 2026-09-06: Remove failed control-fix workflow
- 2026-09-06: Apply v3 control fixes
- 2026-09-06: Remove final v3 polish CI trigger
- 2026-09-06: Trigger final v3 polish package verification
- 2026-09-06: Polish tuning LED and emergency controls
- 2026-09-06: Fix slider polish workflow runner
- 2026-09-06: Apply v3 tuning and LED slider polish
- 2026-09-06: Describe neon orange mesh boundary in empty Preview
- 2026-09-06: Describe neon orange mesh boundary in Preview
- 2026-09-06: Use neon orange bed mesh boundary
- 2026-09-06: Keep empty Preview scrub ETA layout fixed
- 2026-09-06: Keep Preview scrub ETA layout fixed
- 2026-09-06: Remove temporary v3 CI trigger
- 2026-09-06: Trigger final v3 package verification
- 2026-09-06: Fix layer tracking ETA and Preview scrub timing
- 2026-09-06: Apply layer ETA and Preview UX fixes
- 2026-09-06: Remove failed staged patch
- 2026-09-06: Remove failed temporary apply workflow
- 2026-09-06: Apply staged layer and ETA corrections
- 2026-09-06: Stage layer and ETA corrections
- 2026-09-06: Outline Klipper bed mesh bounds in Preview
- 2026-09-06: Run v3 follow-up regressions in CI
- 2026-09-06: Fix bed mesh lifecycle and expand Monitor controls
- 2026-09-06: Apply v3 follow-up fixes
- 2026-09-06: Stage v3 follow-up patch part 5
- 2026-09-06: Stage v3 follow-up patch part 4
- 2026-09-06: Stage v3 follow-up patch part 3
- 2026-09-06: Stage v3 follow-up patch part 2
- 2026-09-06: Stage v3 follow-up patch part 1
- 2026-09-06: Retain Monitor dashboard compatibility markers
- 2026-09-06: Run bed mesh tests and verify package files
- 2026-09-06: Test Klipper bed mesh rendering contracts
- 2026-09-06: Use bed mesh Monitor wrapper
- 2026-09-06: Add Monitor bed mesh height map
- 2026-09-06: Expose bed mesh control in empty Preview
- 2026-09-06: Add Preview bed mesh visibility control
- 2026-09-06: Expose live Klipper bed mesh to Monitor and Preview
- 2026-09-06: Render Klipper bed mesh in Cura Preview
- 2026-09-06: Test hidden upload folder filtering
- 2026-09-06: Hide dot-directories from upload folder prompt
- 2026-09-06: Cover PWM output-pin Monitor controls
- 2026-09-06: Show PWM output sliders in Monitor
- 2026-09-06: Add PWM output-pin controls to Monitor
- 2026-09-06: Keep source contract at version 3.0.0
- 2026-09-06: Run hotfix regression suite in CI
- 2026-09-06: Add upload and Monitor hotfix regression tests
- 2026-09-06: Retain Monitor compatibility contract markers
- 2026-09-06: Align Monitor regression tests with dashboard view
- 2026-09-06: Update contracts for 3.0.1 hotfix
- 2026-09-06: Document Monitor dashboard composition
- 2026-09-06: Keep plugin version at 3.0.0
- 2026-09-06: Keep release version at 3.0.0
- 2026-09-06: Verify Monitor dashboard in Cura package
- 2026-09-06: Verify typed Monitor and upload dialog in package
- 2026-09-06: Test upload lifecycle and Moonraker folder discovery
- 2026-09-06: Set plugin version to 3.0.1
- 2026-09-06: Bump hotfix package to 3.0.1
- 2026-09-06: Activate typed Monitor dashboard
- 2026-09-06: Add typed Monitor dashboard controls
- 2026-09-05: Use typed Monitor controls
- 2026-09-05: Infer macro argument types and temperature preset state
- 2026-09-05: Fix upload dialog QML context
- 2026-09-05: Fix upload dialog lifecycle and discover Moonraker folders
- 2026-09-05: Restore upload device before hotfix
- 2026-09-05: Fix upload dialog lifecycle and discover folders
- 2026-09-05: Test Monitor controls class definition
- 2026-09-05: Fix Monitor controls class import
- 2026-09-05: CI: verify enhanced Monitor package contents
- 2026-09-05: Build: require enhanced Monitor files in Cura package
- 2026-09-05: Test enhanced Monitor controls and safety contracts
- 2026-09-05: Monitor: retain base view contract for enhanced dashboard
- 2026-09-05: Monitor: activate enhanced control surface
- 2026-09-05: Monitor: add advanced control surface
- 2026-09-05: Monitor: add advanced Klipper controls
- 2026-09-05: Describe integrated Monitor in plugin metadata
- 2026-09-05: Describe integrated Monitor in package metadata
- 2026-09-05: Document Upload settings and expanded Monitor dashboard
- 2026-09-05: Test indexed file-position layer fallback in Monitor
- 2026-09-05: Monitor: resolve current layer from indexed G-code position
- 2026-09-05: Monitor: align dialogs and scrolling with Cura QML patterns
- 2026-09-05: Update contracts for Upload naming and current Monitor implementation
- 2026-09-05: Remove release-number wording from Upload settings
- 2026-09-05: Remove release-number wording from upload device code
- 2026-09-05: Remove release-number wording from configuration code
- 2026-09-05: CI: cover Monitor and upload regressions in package build
- 2026-09-05: Add Monitor and upload lifecycle regression contracts
- 2026-09-05: Monitor: use follower-resolved layer model
- 2026-09-05: Monitor: use follower layer resolution for live layer display
- 2026-09-05: v3: rename Output settings tab to Upload
- 2026-09-05: v3: use fixed upload lifecycle and refresh expanded Monitor
- 2026-09-05: v3: fix upload cancellation and terminal write lifecycle
- 2026-09-05: v3: build full Moonraker Monitor dashboard
- 2026-09-05: v3: expand Monitor with controls, peripherals, ETA, power and health
- 2026-09-05: v3: build and publish curapackage from CI
- 2026-09-05: v3: add reproducible curapackage builder
- 2026-09-05: v3: document unified Monitor and Moonraker webcam support
- 2026-09-05: v3: extend SDK compatibility contracts to Monitor QML
- 2026-09-05: v3: cover webcam migration and camera config normalisation
- 2026-09-05: v3: harden Monitor QML compatibility and camera switching
- 2026-09-05: v3: keep Monitor connected when automatic following is disabled
- 2026-09-05: v3: replace no-webcam contract with unified Monitor contracts
- 2026-09-05: v3: wire unified webcam Monitor into Cura output device
- 2026-09-05: v3: add webcam and live status Monitor tab
- 2026-09-05: v3: add unified Moonraker Monitor model and webcam discovery
- 2026-09-05: v3: migrate legacy webcam settings for unified Monitor
- 2026-09-05: ci: add Python compile and core unit test gate
- 2026-09-05: v3: document unified Moonraker connection and output workflow
- 2026-09-05: v3: update source contracts for unified Moonraker integration
- 2026-09-05: v3: test unified output config and standalone migration
- 2026-09-05: v3: bump plugin metadata for unified Moonraker integration
- 2026-09-05: v3: bump package metadata for unified Moonraker integration
- 2026-09-05: v3: migrate standalone Moonraker Connection settings at startup
- 2026-09-05: v3: register unified Moonraker output device
- 2026-09-05: v3: add integrated Moonraker output settings tab
- 2026-09-05: v3: unify follower and Moonraker output settings
- 2026-09-05: v3: add integrated Moonraker upload dialog
- 2026-09-05: v3: register integrated Moonraker output device
- 2026-09-05: v3: add integrated Moonraker upload and print output device
- 2026-09-05: v3: extend per-printer config for integrated Moonraker output and migration
- 2026-09-05: Fix jitter, still v2.0.0.
- 2026-09-05: Fix package ID for v2.0.0.
- 2026-09-04: Fix v2.0.0.
- 2026-09-04: Fix v2.0.0.
- 2026-09-04: Fix v2.0.0.
- 2026-09-04: Update to v2.0.0.
- 2026-09-04: Update to v1.1.0.
- 2026-09-03: Fix v1.0.3.
- 2026-09-03: Update to v1.0.3.
- 2026-09-03: Update to v1.0.2.
- 2026-09-01: Update to v1.0.1.
- 2026-09-01: Fixups
- 2026-09-01: Initial release
