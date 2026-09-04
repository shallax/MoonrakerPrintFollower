# Moonraker Print Follower

A Cura 5.13 extension that keeps Cura Preview synchronised with a print running through Klipper/Moonraker.

- **Author:** shallax
- **Maintainer:** moonrakerprintfollower@maintain.contact
- **Project:** https://github.com/shallax/MoonrakerPrintFollower
- **Release:** 1.1.0
- **Target:** Cura 5.13 / SDK 8.12

## 1.1.0

The 1.0.x line is frozen. 1.1.0 is the first architectural release and keeps the proven 1.0.3 follower/load behavior while moving connection, machine configuration and follower policy behind explicit components.

### Per-printer configuration

Moonraker settings are stored against Cura's active machine instead of globally. Each Cura printer can have its own:

- enable/disable state
- Moonraker URL and optional API key
- HTTP polling interval
- layer-number fallback convention
- follow mode
- within-layer path following
- automatic Preview switching
- Z-height fallback and tolerance

On first 1.1.0 launch the legacy 1.0.x global settings are migrated once to the currently active Cura printer. Switching Cura machines stops the old Moonraker session, clears print-specific state and reconnects using the newly active machine's configuration.

Startup is deliberately passive: the plugin never forces Cura's lazily-created `MachineManager` into existence while plugins are loading. If Cura has not established a global machine stack yet, per-printer migration and connection setup wait for `globalContainerStackChanged`. This avoids interfering with Cura's own active-machine restoration sequence.

### Moonraker HTTP client

`MoonrakerClient` polls Moonraker's object-query API for `print_stats`, `gcode_move`, `virtual_sdcard` and `motion_report`. HTTP is the only live-status transport in this release.

- normal polling uses the per-printer configured interval
- failed requests back off automatically through 1s → 2s → 5s → 10s → 30s
- a successful response immediately restores the configured polling interval
- runtime capabilities are inferred from the objects Moonraker actually returns
- the Preview UI shows only compact follower state; transport/debug detail stays out of the action bar

### Follow modes

- **Exact current layer** — follows the currently printing layer and, when enabled, the within-layer toolpath position.
- **Last completed layer** — displays the previous completed layer.
- **Look ahead one layer** — displays the layer immediately after the current printer layer.
- **Window around current layer (±2)** — sets Cura's lower/upper layer handles to show a five-layer window around the live layer where possible.

Manual movement of either Cura layer handle or either within-layer path handle pauses following. **Resume** explicitly catches Preview back up without stopping Moonraker polling. Klipper pause/resume and Cura slicing suspension are represented as explicit follower states.

### Connection testing

**Test connection** checks `/server/info` and `/printer/objects/list`, reporting Moonraker/Klippy state, required print objects, and optional `motion_report`. Printer discovery is deliberately not attempted; each Cura printer uses the Moonraker URL configured for it.

### Compact Preview controls

The Preview controls are grouped in their own Cura-styled action-panel card, using the same theme background, border, radius and padding primitives as Cura's native Slice/Upload panel. This keeps Moonraker Print Follower visually separate from neighbouring `saveButton` extensions such as Post Processing.

- a native Cura **Nozzle** icon and bold **Moonraker Print Follower** title identify the card
- compact status (`Following`, `Paused`, `Printer paused`, `Connecting…`) occupies its own row with a native state icon
- status icons change with state (healthy/following, busy/connecting, disconnected/error, informational/paused)
- buttons are **Pause/Resume** and **Load print** on a fixed-width bottom row, so status changes do not resize the card
- an explicit Cura `default_margin` gutter separates the follower card from neighbouring `saveButton` extensions such as Post Processing
- connection/protocol diagnostics remain in the configuration dialog instead of the bottom action bar
- the empty-Preview overlay uses the same titled, fixed-width bordered-card treatment

### Large G-code and persistent indexes

The 1.0.3 streaming/cancellable indexer remains. 1.1.0 adds compact indexing for very large G-code files (128 MiB and above by default):

- the initial index stores layer byte ranges and layer-start positions without retaining every motion command
- byte-position following works immediately from the compact index
- motion/live-position data is hydrated lazily only for the layer being viewed
- hydrated data can be persisted back into the bounded on-disk index cache
- persistent indexes remain keyed and validated against Moonraker file identity metadata

Layer markers are recognised for Cura (`;LAYER:n`), PrusaSlicer/SuperSlicer (`;LAYER_CHANGE`), OrcaSlicer (`; layer num/total_layer_count: ...`) and `SET_PRINT_STATS_INFO CURRENT_LAYER=...` as a fallback/self-describing mapping.

### Internal boundaries

High-risk logic is separated into focused modules:

- `PrinterConfig.py` — per-Cura-machine persisted settings and 1.0.x migration
- `MoonrakerClient.py` — resilient HTTP polling, retry backoff and capability detection
- `FollowController.py` — follower state machine and follow-mode decisions
- `CuraAdapter.py` — Cura machine identity and Preview writes
- `GCodeIndex.py` — streaming/compact parsing, lazy layer hydration and persistent index cache
- `MoonrakerProtocol.py` — endpoint construction and coordinate conversion
- `DownloadStream.py` — bounded streaming G-code downloads
- `Core.py` — shared operation/identity/manual-override primitives

## Preserved behavior from 1.0.3

- explicit **Load current print** with native confirmation; nothing auto-loads merely because Preview is empty
- public Cura `readLocalFile()` load path
- manual upper/lower layer and path changes immediately suspend following
- generation guards prevent stale HTTP, download, index and lifecycle callbacks changing a later scene/job
- remote print identity distinguishes repeated or overwritten prints with the same filename
- streaming downloads avoid accumulating large G-code files in RAM
- live-position refinement uses `motion_report.live_position` when plausible, with `virtual_sdcard.file_position` as the coarse authoritative position

## Install

Drag `MoonrakerPrintFollower-v1.1.0.curapackage` onto Cura, accept the installation, and restart Cura. Configure it under **Extensions → Moonraker Print Follower → Configure…**.

If upgrading from 1.0.3, 1.1.0 performs the one-time per-printer settings migration automatically.

## Development checks

The source archive includes a standard-library `unittest` suite under `tests/`. It covers the frozen 1.0.3 behavior plus per-printer settings, follower state transitions/modes, HTTP/probe endpoint construction, Cura/Prusa/SuperSlicer/Orca layer-marker parsing, compact/lazy indexes, persistent-cache round trips, manual override semantics, streaming loading and static integration contracts.
