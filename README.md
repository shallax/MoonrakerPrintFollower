# Moonraker Print Follower

A Cura 5.x extension that follows the print currently running through Klipper/Moonraker.

## Features

- Follows Moonraker's current layer in Cura Preview.
- Follows progress through the current layer using Cura's horizontal toolpath slider.
- **Load current print** explicitly downloads the active Moonraker G-code and replaces the current Cura contents after a native Yes/No confirmation. There is no automatic empty-build-plate load.
- **Pause following / Resume following** freezes or resumes automatic Preview movement without stopping Moonraker polling.
- Manually moving Cura's layer or path slider pauses following automatically.
- Controls are Preview-only. **Load current print** remains available in an empty Preview; Pause/Resume appears only when Cura has toolpath data.
- Poll interval accepts any positive whole-number millisecond value with no snapping or plugin-side upper cap.
- Optional Moonraker API key, automatic Preview switching, layer-number fallback handling, and Z-height fallback.

## v1.0.2

- Scene invalidation reacts to structural node changes rather than every Cura `sceneChanged` notification.
- Slicing explicitly suspends Preview writes and resumes only after Cura has finished replacing layer data.
- Delayed callbacks, status replies, G-code downloads, and index results are generation-guarded so work from an old scene/job cannot mutate a new one.
- SimulationView connections are rebound when Cura replaces the view object. Layer/path change signals are used for manual-override detection when available, with the 75 ms watcher only as a compatibility fallback.
- The action-panel component is reused across main-window rebuilds and explicitly unregistered when destroyed or when the plugin shuts down.
- Aborted network replies are disconnected, aborted, and scheduled for deletion.
- The background G-code index worker supports cooperative cancellation and is joined during plugin shutdown.
- Remote job identity includes filename, reported file size, and run-reset detection, preventing stale indexes when the same filename is printed again.
- Cached G-code directories from previous jobs are cleaned up instead of accumulating for the whole Cura session. Cura's in-flight file path is tracked separately so cleanup cannot invalidate an asynchronous load.
- The G-code indexer no longer uses `splitlines()` over the complete file, avoiding the large temporary line-list allocation while preserving byte-offset semantics.
- No private SimulationPass/nozzle-renderer mutation is used.
- The explicit load path remains Cura's public `readLocalFile()` path.

## v1.0.1

Manual movement of either Cura Preview layer slider or within-layer path slider automatically pauses following. Plugin-driven movements are ignored, and Resume following catches back up to the live Moonraker position.

