# Architecture

Moonraker Print Follower keeps Cura-facing UI, Moonraker transport, G-code indexing and Preview following separated so printer/API changes do not destabilise unrelated behaviour.

## v3.1 ownership model

`MoonrakerPrintFollower.py` is only the public Cura extension facade. `FollowerCoordinator.py` composes focused domain services while `FollowerRuntime.py` remains the compatibility boundary containing mature Cura scene/QML integration and established Preview/load mechanics inherited from 3.0.0.

Each mutable domain has one authoritative owner:

- `RemoteJobService.py` owns remote print-run identity and restart detection, using the pure `PrintTracker.py` algorithm.
- `RemoteFileService.py` owns remote-file identity plus downloaded G-code cache state, composing `GCodeRepository.py` for cache bookkeeping.
- `GCodeIndexService.py` owns active index, build and layer-hydration state. Pure G-code indexing logic remains in `GCodeIndex.py`.
- `PauseScheduleService.py` owns print-local end-of-layer PAUSE scheduling and consumption.
- `PreviewFollowerService.py` owns Preview attachment/detachment, the Preview position last written by the follower and manual-override classification.
- `CuraLifecycleBridge.py` owns Cura scene-generation tokens used to reject stale asynchronous callbacks.
- `FollowerTransport.py` owns follower-specific HTTP operations such as metadata, streamed G-code download and scheduled PAUSE, while reusing the shared Moonraker transport.
- `MoonrakerSession.py` owns the active printer's identity, shared state, request coalescing, adaptive polling policy, command acknowledgement and transport binding.
- `MoonrakerTransport.py` owns the reusable Qt HTTP connection pool, authentication, cancellation, request identity and transport telemetry.

There are intentionally no parallel legacy service classes for these domains. `PauseScheduler.py`, `PreviewController.py`, `FollowerStateBridge.py` and `FollowerSession.py` were transitional duplicates and are removed. Lower-level helpers such as `PrintTracker.py` and `GCodeRepository.py` remain because their higher-level services compose them rather than duplicate them.

This is a strangler refactor rather than a rewrite: established Cura-facing behaviour remains behind the compatibility runtime while state ownership and new policy move into the focused services. This keeps the 3.0 regression surface controlled while allowing later removal of now-overridden compatibility methods to be mechanical rather than behavioural.

## Moonraker HTTP/session

The plugin remains HTTP-only. WebSockets are intentionally not introduced.

`MoonrakerClient.py` is the single core-status poller for the active Cura printer and runs over one `MoonrakerSession`. The session owns one `MoonrakerHttpTransport` for that active binding. URL or API-key changes invalidate all transport owners and shared state in the same rebind transaction.

The transport supplies one Qt connection pool to Preview/follower operations, Monitor and output-device requests. Large G-code downloads and multipart uploads retain specialised reply lifecycles, but they use the same request builder and network manager.

A separate transport instance is used only for unsaved connection-test credentials so testing a candidate URL/API key cannot rebind the live printer session. It is still the same transport implementation rather than a second networking stack.

Core polling is generation-guarded, retains the 1/2/5/10/30 second failure backoff and coalesces overlapping forced refreshes into at most one queued follow-up.

Polling is category-aware:

- core status uses the configured interval while printing;
- core polling tightens to the precision interval around an imminent scheduled end-of-layer PAUSE;
- core polling relaxes while paused or idle;
- dynamic auxiliaries are fast while active and slower while idle;
- power, system and capability discovery use progressively slower lanes.

`MoonrakerMonitorSession.py` removes Monitor's duplicate core poller. Monitor consumes `MoonrakerClient` core state and routes peripheral JSON traffic through the same active-printer transport. Peripheral request cadence is driven by the shared polling policy.

`MoonrakerOutputSession.py` reuses both the shared transport and shared core readiness. Multipart upload remains a specialised streaming/write lifecycle but shares the active connection pool and request identity.

## Command acknowledgement

A successful HTTP response is not treated as proof that printer state changed. `CommandTracker` records command issue, Moonraker HTTP acceptance and the expected observable `print_stats.state`.

Pause, resume and cancel remain pending until the shared status stream confirms their expected state or the acknowledgement times out/fails. Scheduled end-of-layer PAUSE uses the same acknowledgement path: the G-code-script HTTP response means only that Moonraker accepted the request; completion is confirmed only when the shared session observes `paused`.

## Monitor layering

1. `MoonrakerMonitorModel.py` — generic Monitor field parsing, ETA, webcams, power and peripheral behaviour.
2. `MoonrakerMonitorSession.py` — shared core session, shared JSON transport and adaptive category cadence.
3. `MoonrakerMonitorRuntime.py` — follower-aware physical-layer resolution.
4. `MoonrakerMonitorControls.py` — printer actions and live tuning.
5. `MoonrakerMonitorTypedControls.py` — typed macro parameters, PWM, MCU stats, remembered webcams and bed-mesh Preview integration.

Core status is parsed once and extended through `_after_core_status`. Only the active Cura printer's Monitor may issue peripheral requests, and Monitor replies remain request-generation guarded.

`configfile.config` is static/heavy: the full configuration is refreshed with capability discovery while only SAVE_CONFIG volatile fields are included in the fast auxiliary lane.

The active QML chain is `MoonrakerMonitorBedMesh.qml` → `MoonrakerMonitorDashboard.qml` → `MoonrakerMonitor.qml`.

## G-code / Preview

- `GCodeIndex.py` remains pure indexing/timing/motion logic covered by file fixtures.
- `GCodeIndexService.py` owns the active index/build/hydration lifecycle.
- `RemoteFileService.py` owns remote metadata identity and local cached-file identity.
- `PreviewFollowerService.py` owns follower attachment state, follower-written Preview state and manual override detection.
- `FollowController.py` contains follow-mode decisions.
- `CuraAdapter.py` and `NativeNozzleFallback.py` isolate Cura compatibility.
- Large G-code streaming remains a specialised follower transport operation using the shared active-printer HTTP connection pool.

## Release invariants

- All QML must pass `tools/check_qml.py`; duplicate properties are a hard failure.
- Tests are discovered automatically with `unittest discover`.
- Python sources compile on 3.10, 3.11 and 3.12 in CI.
- The `.curapackage` must be an exact projection of the plugin source tree with no caches, editor backups or patch files.
- Marketplace package ID remains `Moonraker_Print_Follower`.
- v3.1 architecture changes must preserve v3.0 user-visible following, manual-override, upload and Monitor behaviour unless explicitly documented.

## Testing strategy

Pure policy/state has deterministic unit coverage, including a scripted fake Moonraker harness for status progression, command acknowledgement, request coalescing, scheduled-pause precision polling and print restart cases. Source-contract tests also enforce one authoritative service per extracted state domain so duplicate replacement classes cannot silently accumulate again. Cura/QML source contracts and G-code fixtures remain as regression guards around the compatibility boundary.
