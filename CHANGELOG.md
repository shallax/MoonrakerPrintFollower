# Changelog

Moonraker Print Follower is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).

## 3.3.1

Version 3.3.1 is an audit-driven hardening pass over 3.3.0: an adversarial
multi-agent review against the architecture contract confirmed and fixed the
following.

### Highlights
- The smoothing CSV trace is now opt-in via the
  `MOONRAKER_FOLLOWER_SMOOTHING_TRACE` environment variable (documented in
  `INSTRUCTIONS.md`) instead of writing to Cura's cache on every smoothed
  print.
- The display timer snaps to the target and stops when pure gap decay
  converges, instead of ticking at 30 Hz for the whole duration of a pause.
- Failed G-code downloads retry on a backoff ladder (2 s → 60 s) instead of
  wedging the file service for the rest of the print.
- Start-print power-on probes every configured power device; a powered
  socket can no longer mask a powered-down PSU.
- Lease lifetime was reviewed against the documented contract: an unrelated
  Cura file completion still must not release the current remote file
  (`ARCHITECTURE.md` section 6), so a superseded read intentionally retains
  its lease until shutdown.
- Failed layer hydration is latched until a new file or index arrives, so a
  broken file is not re-read in full on every poll.
- While a compact layer hydrates, the animation driver is reset so a stale
  target cannot fight the follower's own writes.
- The velocity window scales with the measured poll interval, keeping the
  glide honest at slow polling rates instead of degrading silently.
- Service failure messages are logged instead of being emitted with no
  listener.
- Corrected documentation that still described the removed lookahead and
  the old fallback behaviour, and added regression tests for every fix
  above.

## 3.3.0

Version 3.3.0 is the first feature release after the debt payoff: follower
quality.

### Highlights
- Smooths the Preview path head: the displayed position glides along the
  toolpath at the observed physical velocity, never gets ahead of the
  newest observation and never snaps back within a layer. Layer
  transitions are jumped, not animated.
- Reconstructs the physical trajectory between polls: the target ramps
  linearly over the measured poll interval, so the glide is equally smooth
  at any configured polling rate instead of stepping once per poll at slow
  rates.
- Fixes the physical observation itself for slow moves: the refinement
  search window is widened past Klipper's parser-chunk lead and the
  ambiguity fallback holds the last good value instead of jumping to the
  parser-position fraction, so the observed progress is a smooth
  sub-segment signal rather than a cm-apart staircase.
- The smoothing is display-only: the physical path fraction used for ETA is
  unchanged, and animated writes re-remember the plugin-written position so
  manual-override detection is unaffected.
- Adds a **Smooth path progress** option (enabled by default) in the
  Following tab.

## 3.2.0

Version 3.2.0 closes the remaining 3.1.0 architecture gaps and codifies how the repository changes. User-facing behaviour is unchanged.

### Highlights
- Reorganises the test suite by domain; version/regression-named test files are gone and coverage is unchanged.
- Codifies change procedures in `INSTRUCTIONS.md`, including the version bump checklist; the architecture document references it.
- Extracts pure `PreviewFormatting` (status text/icon, pause-item and ETA projections) out of `PrintCoordinator`, which now publishes a domain projection instead of formatting UI strings.
- Dissolves the mixed-domain `Core` module: `RemoteFileIdentity` joins `MoonrakerProtocol`, end-of-layer pause crossing joins `PauseScheduleService`, Preview override detection joins `PreviewFollower`, and the unused `OperationContext`/`OperationPhase` runtime is removed rather than relocated.
- Removes the unused `MoonrakerSessionState.rebind()` path; rebinding goes through the production `configure()`/`reset()` flow only.
- Extends the import/ownership contract tests to every domain module, including the new formatting module.
- Version metadata test now checks `package.json` and `plugin.json` stay in sync; the release workflow still validates both against the git tag.
- Publishes fully detached status snapshots from the session boundary; consumers can no longer mutate session internals through nested values.
- Separates metadata completeness from download identity: a failed metadata request installs a fallback identity so downloads proceed, then retries with backoff instead of permanently degrading the run.
- Moves Preview view reads/writes into typed `CuraAdapter` accessors; `PreviewFollower` no longer calls view methods by name.
- Consolidates URL normalisation into one `PrinterConfig.normalise_url` rule used by configuration parsing, binding and the Machine Action.
- Fixes the G-code layer map for files whose start-gcode emits `CURRENT_LAYER=0` before the first layer marker; scheduled pauses could previously fire one layer early.
- Keeps print run identity stable across transient `file_size` gaps (reconnect/restart), so a mid-print poll gap no longer restarts Preview tracking, pause schedules or downloads.
- Treats pre-print `current_layer=0` in one-based mode as no-layer rather than layer zero, and stops Z-fallback from jumping layers across pause/resume Z-lifts.
- Rejects non-finite or out-of-range Z tolerance values and caps the polling interval.
- Validates persistent index caches against size/modified time alongside the uuid, and recognises `G01`/`G00` motion forms.
- Guards the Monitor camera restore against webcam-set changes, prunes renamed/removed objects from the auxiliary snapshot, deactivates the outgoing Monitor before the incoming one activates, and removes a phantom signal key.
- Consolidates printer-object classification into one shared policy table, narrows Monitor controller capabilities instead of passing the whole client, and moves the bed-mesh observation to the print coordinator (Monitor can no longer write follower mesh state).
- Completes the `ARCHITECTURE.md` ownership map, overhauls the README (release header, feature and structure sections), and makes the tag-release CI run the full Qt suite; verifier tools share one checks implementation.
- Applies released Monitor slider values after a 250 ms unchanged window instead of 2 seconds.

## 3.1.0

Version 3.1.0 is primarily an internal architecture and reliability release. It preserves the v3.0 user-facing workflow while reducing duplicated state/transport ownership and making the high-risk parts independently testable.

### Highlights
- Introduces a shared HTTP-only Moonraker session/state layer for the active Cura printer.
- Coalesces overlapping core refreshes and adapts polling cadence by printer state and request category.
- Removes Monitor's duplicate core-status fallback poller; Preview and Monitor consume the same generation-guarded core snapshot.
- Distinguishes command HTTP acceptance from observed printer-state confirmation for pause/resume/cancel.
- Replaces the follower mixin runtime and Monitor inheritance chain with explicit composed components and immutable print/Preview observations.
- Shares one physical-layer resolver between Preview, Monitor and scheduled PAUSE; manual Preview selection cannot change printer observation.
- Moves index restoration, building, hydration and persistence to a bounded worker with explicit file leases and stale-result guards.
- Streams prepared G-code/UFP uploads from temporary files and gives each write one cancellation/terminal-signal owner.
- Preserves configuration migration before connection startup and invalidates old work on profile or credential changes.
- Adds real-Qt component and loopback HTTP tests, QML interface checks, and dependency contracts that reject retired implementations and private follower coupling.
- Keeps HTTP as the sole Moonraker transport; WebSockets are intentionally not introduced.

## 3.0.0

Version 3.0.0 turns Moonraker Print Follower into a much more complete Cura-side companion for Klipper/Moonraker while preserving the core live Preview follower.

### Highlights
- Integrates Moonraker connection/output functionality into one plugin, including G-code upload and printer-aware file handling.
- Adds a live printer dashboard with temperatures, print state, macros, power controls, Z offset, speed/flow tuning, fans, LEDs, PWM outputs and an emergency stop.
- Adds rich bed-mesh support including a 3D Preview overlay and mesh controls.
- Adds end-of-layer PAUSE scheduling directly from Cura Preview, including multiple scheduled pauses and ETA display.
- Restores and improves selected-layer ETA while inspecting future layers.
- Renames Preview following controls to **Detach / Attach** so they cannot be confused with pausing the printer.
- Improves multi-printer behaviour, large-print performance, polling efficiency and stale-response protection.

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
