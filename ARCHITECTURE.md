# Architecture

Moonraker Print Follower keeps Cura-facing UI, Moonraker transport, G-code indexing and Preview following separated so printer/API changes do not destabilise unrelated behaviour.

## v3.1 ownership model

`MoonrakerPrintFollower.py` is now only the public Cura extension facade. `FollowerCoordinator.py` composes focused state/policy services while `FollowerRuntime.py` is the compatibility boundary containing the mature Cura scene/QML integration, streaming download/index workers and specialised Preview mechanics inherited from 3.0.0.

The extracted services are deliberately pure or nearly pure:

- `FollowerSession.py` owns active-machine/lifecycle coordination state.
- `PrintTracker.py` owns print-run identity and restart detection.
- `PauseScheduler.py` owns print-local end-of-layer PAUSE scheduling/consumption.
- `PreviewController.py` owns the Preview position last written by the follower.
- `GCodeRepository.py` owns downloaded G-code cache identity.
- `MoonrakerSession.py` owns shared Moonraker state, request coalescing, category-aware polling policy and command acknowledgement state.

This is a strangler refactor rather than a rewrite: established Cura-facing behaviour remains behind the compatibility runtime while new/changed policy belongs in the extracted services. That keeps the 3.0 regression surface intact and makes subsequent removals from `FollowerRuntime.py` mechanical rather than behavioural.

## Moonraker HTTP/session

The plugin remains HTTP-only. WebSockets are intentionally not introduced.

`MoonrakerClient.py` is the single core-status session for the active Cura printer. It generation-guards stale replies, keeps the existing 1/2/5/10/30 second failure backoff, coalesces overlapping refreshes into at most one queued refresh, and publishes a shared `SessionSnapshot` used by Preview and Monitor.

Polling is category-aware:

- core status uses the configured interval while printing, then relaxes while paused/idle;
- dynamic auxiliaries are fast while active and slower while idle;
- power, system and capability discovery use progressively slower lanes.

`MoonrakerMonitorSession.py` removes Monitor's duplicate core fallback poller. Monitor still owns capability-specific peripheral HTTP lanes because those queries vary by printer, but their cadence is driven by the shared policy and their core state always comes from `MoonrakerClient`.

`MoonrakerOutputSession.py` reuses shared core readiness before issuing an additional readiness query. Multipart upload remains in the output device because it is a specialised streaming/write lifecycle rather than shared status transport.

## Command acknowledgement

A successful HTTP response is not treated as proof that the printer changed state. `CommandTracker` records command issue, Moonraker HTTP acceptance and the expected observable `print_stats.state`. Pause/resume/cancel stay busy until the shared status stream confirms the state or the acknowledgement times out/fails.

## Monitor layering

1. `MoonrakerMonitorModel.py` — generic Monitor HTTP helpers, core field parsing, ETA, webcams, power and basic peripherals.
2. `MoonrakerMonitorSession.py` — shared core session and adaptive category cadence.
3. `MoonrakerMonitorRuntime.py` — follower-aware physical-layer resolution.
4. `MoonrakerMonitorControls.py` — printer actions and live tuning.
5. `MoonrakerMonitorTypedControls.py` — typed macro parameters, PWM, MCU stats, remembered webcams and bed-mesh Preview integration.

Core status is parsed once and extended through `_after_core_status`. Only the active Cura printer's Monitor may issue peripheral requests, and Monitor replies remain request-generation guarded.

`configfile.config` is static/heavy: the full configuration is refreshed with capability discovery while only SAVE_CONFIG volatile fields are included in the fast auxiliary lane.

The active QML chain is `MoonrakerMonitorBedMesh.qml` → `MoonrakerMonitorDashboard.qml` → `MoonrakerMonitor.qml`.

## G-code / Preview

- `GCodeIndex.py` remains pure indexing/timing/motion logic covered by file fixtures.
- `FollowController.py` contains follow-mode decisions.
- `CuraAdapter.py` and `NativeNozzleFallback.py` isolate Cura compatibility.
- Large G-code streaming and incremental hydration remain in `FollowerRuntime.py`; cache identity and print identity are now external services.

## Release invariants

- All QML must pass `tools/check_qml.py`; duplicate properties are a hard failure.
- Tests are discovered automatically with `unittest discover`.
- Python sources compile on 3.10, 3.11 and 3.12 in CI.
- The `.curapackage` must be an exact projection of the plugin source tree with no caches, editor backups or patch files.
- Marketplace package ID remains `Moonraker_Print_Follower`.
- v3.1 architecture changes must preserve v3.0 user-visible following, manual-override, upload and Monitor behaviour unless explicitly documented.

## Testing strategy

Pure policy/state has deterministic unit coverage, including a scripted fake Moonraker harness for status progression, command acknowledgement, request coalescing and print restart cases. Cura/QML source contracts and G-code fixtures remain as regression guards around the compatibility boundary.
