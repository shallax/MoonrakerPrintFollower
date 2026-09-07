# Changelog

Moonraker Print Follower is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).

## 3.1.0

Version 3.1.0 is primarily an internal architecture and reliability release. It preserves the v3.0 user-facing workflow while reducing duplicated state/transport ownership and making the high-risk parts independently testable.

### Highlights
- Introduces a shared HTTP-only Moonraker session/state layer for the active Cura printer.
- Coalesces overlapping core refreshes and adapts polling cadence by printer state and request category.
- Removes Monitor's duplicate core-status fallback poller; Preview and Monitor consume the same generation-guarded core snapshot.
- Distinguishes command HTTP acceptance from observed printer-state confirmation for pause/resume/cancel.
- Extracts print-run identity, PAUSE scheduling, Preview expectation, cache identity and coordinator lifecycle into focused services.
- Adds an output-session adapter that reuses shared readiness before issuing redundant readiness requests.
- Adds deterministic architecture tests and a scripted fake-Moonraker harness for state transitions, restarts and acknowledgements.
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
