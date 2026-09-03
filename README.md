# Moonraker Print Follower

A Cura 5.13 extension that keeps Cura Preview synchronised with a print running through Klipper/Moonraker.

- **Author:** shallax
- **Maintainer:** moonrakerprintfollower@maintain.contact
- **Project:** https://github.com/shallax/MoonrakerPrintFollower
- **Release:** 1.0.3
- **Target:** Cura 5.13 / SDK 8.12

## Features

- Follows Moonraker's current layer in Cura Preview.
- Follows progress through the current layer using Cura's horizontal toolpath slider.
- Uses `virtual_sdcard.file_position` as the reliable coarse position and, when available, refines it against `motion_report.live_position` so the Cura toolhead more closely follows the physical nozzle rather than Klipper's lookahead queue.
- Reads embedded `SET_PRINT_STATS_INFO CURRENT_LAYER=...` values so zero-based and one-based layer conventions are mapped from the actual G-code.
- **Load current print** explicitly downloads the active Moonraker G-code and replaces Cura's current contents after a native Yes/No confirmation. Nothing auto-loads merely because Cura is empty.
- **Pause following / Resume following** freezes or resumes Preview movement without stopping Moonraker polling.
- Manually moving Cura's layer or within-layer path slider pauses following automatically.
- Controls are Preview-only. **Load current print** remains available in an empty Preview; Pause/Resume appears only when Cura has toolpath data.
- Poll interval accepts any positive whole-number millisecond value with no snapping or plugin-side upper cap.
- Optional Moonraker API key, automatic Preview switching, and Z-height fallback.

## 1.0.3 hardening and performance

- G-code downloads are streamed directly to a temporary file with a bounded Qt read buffer instead of being accumulated in RAM.
- Temporary download files are flushed but not synchronously forced to storage before Cura reads them, avoiding unnecessary I/O stalls.
- G-code indexing is streaming and cancellable; it does not split or retain the whole G-code file as Python lines.
- Persistent path indexes are keyed by Moonraker file metadata (`uuid`, modification time and size when available), bounded by entry count and total cache size, and validated before use.
- `SceneNode.childrenChanged` is used for structural scene lifecycle changes instead of treating every Cura redraw/transform notification as a new scene.
- Following is suspended while CuraEngine slices and resumes at `BackendState.Done`.
- Delayed callbacks, HTTP work and index results are generation-guarded so stale work from a previous Cura scene or print run is discarded.
- SimulationView signal connections are rebound if Cura replaces the view object. Cura's layer/path change signals are used for manual-override detection where available, with the 75 ms watcher retained only as a compatibility fallback.
- Remote operations use an explicit resolving/downloading/Cura-loading/indexing/ready/error state model which also drives the compact Preview status text.
- Remote print identity combines per-run reset detection with Moonraker metadata so repeatedly printing or overwriting the same filename cannot silently reuse a stale index.
- Large-file worker cancellation and network reply cleanup are explicit during model changes, slicing, plugin shutdown and superseding jobs.
- The load path remains Cura's public `readLocalFile()` mechanism.
- High-risk logic is separated into `Core.py`, `MoonrakerProtocol.py`, `DownloadStream.py` and `GCodeIndex.py` so UI/lifecycle changes do not have to modify the G-code parser/cache implementation.

## Install

Drag `MoonrakerPrintFollower-v1.0.3.curapackage` onto Cura, accept the installation, and restart Cura. Configure it under **Extensions → Moonraker Print Follower → Configure…**.

## Development checks

The source archive includes a standard-library `unittest` regression suite under `tests/`. It covers layer-marker conventions, legacy byte/motion indexing semantics, streaming parsing, live-position refinement, persistent cache identity/round-tripping, protocol construction, operation state transitions, and static contracts for the known-good Cura loading/lifecycle paths.
