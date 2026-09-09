# Changelog

Moonraker Print Follower is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).

## 3.5.0

Version 3.5.0 is the informational half of Mainsail parity: the Monitor
tab becomes the at-a-glance view of a running printer, with temperature
history, a bed-mesh map, a console with Klipper's live output, endstop
readouts and a better remaining-time estimate.

### Highlights
- **Temperature history chart** in the Information pane: a 30-minute
  rolling window at the 1 s auxiliary cadence, solid actuals in
  per-sensor colours, translucent target bands whose top edge is the
  setpoint marker, and heater power as a translucent 0-100% area on a
  second axis — all toggleable from a legend (per-sensor visibility,
  setpoints, power) that persists per printer, with the axis labelled
  in °C (the plugin follows Cura, which has no temperature-unit
  preference). Hovering snaps a cursor
  to the samples and the clock plus per-series readouts follow the
  mouse as a floating tooltip that never resizes the chart; a compact
  mini-chart of the primary sensors (extruders, bed, chamber heater)
  sits in the pane, and a click opens the full chart.
- **Bed-mesh mini map** in the Information pane; the enlarged detail
  view gains a crosshair that snaps to probe points with coordinates
  and Z offsets.
- **G-code console** below the webcam, a collapsing pane with the
  panes' uniform chevron toggle (the camera grows while it is
  collapsed): arbitrary commands, Enter-to-send, up/down recall and a
  per-printer persisted transcript (~50 lines of commands AND output
  plus the most recent commands survive across sessions, restored
  lines greyed). Sends return Klipper's execution verdict, and
  Klipper's replies stream in from Moonraker's command store —
  polled while the console is expanded (once a second while printing,
  an idle floor otherwise) — so the pane is a real terminal feed:
  typed lines carry the terminal `>` prompt and render blue until
  saved, then the verdict in muted hues (green ok / red failed);
  responses render in the bright hues (`!!` errors in red); a client
  timeout stays honest ("no response" — a blocking command may still
  be running); and no line claims an attribution the store cannot
  support (it pairs by recency, not per command). Deliberately
  unrestricted: typing a command is intent, so there is no
  command-safety table.
- **"Last action" row** in the Print job grid: transient "sent"
  receipts for macros and setup scripts age out to "—" under the
  permanent caption, a newer action always supersedes the old banner,
  and tracked commands show their confirmed outcome.
- **Filament used/remaining readouts** under the progress block:
  Klipper's real `filament_used` against the slicer's total — parsed
  from the downloaded file's own header so multi-extruder prints read
  correctly on every Moonraker version, with an honest "—" whenever
  the numbers disagree. The rows stay visible after the print
  completes until the next job starts.
- **Endstop readouts**: live pin states from a one-shot
  `printer/query_endstops/status` poll, with an explicit
  not-homed-yet state.
- **Layer-anchored ETA**: the remaining time prefers the estimate from
  the G-code index's per-layer timing scaled by the observed speed
  ratio (routed through the one PreviewFollower owner), falling back to
  the previous blend when the print was never downloaded and indexed.
  The blend itself uses the slicer's estimate from Moonraker's file
  metadata (a server-side header parse, not a download) when the print
  was never loaded, and the layer-height readout uses the same source.
  The ETA readout shows the active basis (colour and tooltip), and a
  small download glyph beside the estimate runs the download-and-index
  flow WITHOUT loading the preview (the author's optimisation: the
  render is only paid when the print is loaded in the Preview, which
  then reuses the already-downloaded file).
- One shared pop-over shell for the Information pane's glanceable
  widgets; each pop-over dismisses with its Close button, Escape, or
  an outside click, and no longer disturbs the pane layout.

### Notes
- Chart history is session-scoped: a Cura restart starts the chart
  empty, and a monitor pause longer than half a minute starts a fresh
  window.
- The 1 s chart cadence applies while printing or paused; the idle
  auxiliary poll stays at 2.5 s.
- Chart colours, visibility and the console transcript (commands and
  Klipper's output) persist per printer; the pane chrome (collapsed
  sections, pane collapse, lock) stays global.
- Known issue — jog reflow (fix planned for a 3.5.x patch): with some
  Printer-controls sections open, expanding or collapsing another
  section mid-jog can reflow the controls, and the nudge button under
  the pointer can move mid-click.

## 3.4.0

Version 3.4.0 is the first Monitor-parity release: manual control of the
physical toolhead from the Monitor tab, and a full overhaul of the Monitor
layout into Cura-style panes and collapsible sections.

### Highlights
- Adds a **Toolhead** section to the Monitor tab: an X/Y/Z compass with
  directional arrows, 0.1 / 0.5 / 1 / 5 / 10 / 25 / 50 / 100 / 125 mm
  distance presets plus free-text entry, per-axis home and home-all,
  motors off, centre-toolhead and Z-to-0 parking moves, and a readout of
  homed axes, absolute or relative move mode and the live position.
- KlipperScreen-style extrusion controls: separate distance presets
  (5 / 10 / 15 / 25 / 75 / 100 mm) and speed presets (1 / 2 / 5 / 25 mm/s).
- Jogs are clamped to the axis limits and can never cross the axis
  minimum — a move that would end negative is forbidden outright.
- Moves are never force-executed mid-print: the jog controls are disabled
  while printing (pause first), and a tap that lands while the print is
  starting pauses the print through the same tracked Pause command as the
  pause button, with the queued moves running only once the printer
  reports paused. Queued moves are dropped if the pause is not confirmed
  within ten seconds or the print resumes mid-drain; rapid taps while
  paused coalesce into single relative moves. The live position tracks the
  head at the urgent poll floor while the queue drains.
- All motion G-code, the print-state safety gate and the queue coalescing
  rules are pure policy (`ToolheadPolicy`), fully unit-tested without Qt;
  the queue and pause sequencing live in one controller (`ToolheadController`).
- The Monitor tab is reorganised into three panes — **Information**,
  **Printer status** and **Printer controls** — each with Cura-style
  accordion sections: an icon, a title hugging a chevron on the right,
  hover tinting and a divider that survives collapsing. Section state,
  pane collapse and the lock-all toggle persist across Cura restarts
  through a plugin-owned state file. Every pane collapses into a thin
  strip with a rotated title; clicking anywhere on a strip re-expands it.
- The accordion header is one shared `CollapsibleSectionHeader` QML type
  used by every pane; the section icons are Cura's own glyphs where they
  exist, and plugin-drawn SVGs (padlocks, power symbol) — theme-tinted
  through `UM.ColorImage` — where they don't.
- Lock-all now disables only the controls inside sections; the accordion
  stays navigable, and the lock state is shown by a closed/open padlock
  glyph in the pane title row (grey unlocked, Cura blue locked).
- Setup commands (Home, QGL, mesh calibration, Save) queue behind the
  in-flight command so they can be lined up in quick succession, and
  firmware/host restart actions join the System section. The emergency
  stop — two clicks, then a held third press of 0.6 seconds — now docks
  across the whole bottom of the window.
- The camera bar sits centred under the feed with a compact webcam
  selector and an icon-only refresh that restarts the stream. The
  readouts (print job, temperatures, fans, filament, objects, system,
  MCUs) share one labelled value column.
- Command replies get an honest timeout: a connection-level failure after
  acceptance reports "outcome unknown" instead of claiming the command
  was cancelled.
- The pre-release adversarial review fixed the rest: a rapid Preview ⇄
  Monitor switch (which can hang Cura and land its view restoration
  seconds late) no longer detaches the follower; the pane row compresses
  gracefully on narrow stages instead of sliding the Printer status pane
  under the controls; wrapped status sections keep their inter-section
  margins; the emergency stop cannot fire when a stolen mouse grab drops
  the release; the camera area distinguishes "not configured" from
  offline; and the toolhead guard releases after the queue settles.

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
