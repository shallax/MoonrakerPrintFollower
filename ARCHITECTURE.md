# Moonraker Print Follower architecture

This document is the architectural source of truth for Moonraker Print Follower. It describes the current responsibility boundaries, mutable-state ownership, data flow, asynchronous validity rules, integration points and testing expectations.

It is not a release-history document. Release notes belong in `CHANGELOG.md`; exact package and Cura SDK compatibility metadata belong in `package.json`, `plugins/plugin.json` and CI tests. If this document and the implementation disagree, treat that disagreement as a defect.

---

## 1. Architectural goals

Moonraker Print Follower integrates three related capabilities into Cura:

- **Live Preview following** — Cura Preview follows the physical Klipper/Moonraker print.
- **Moonraker output** — Cura can upload generated G-code/UFP and optionally start the print.
- **Live Monitor** — Cura displays and controls printer state, temperatures, fans, sensors, macros, power, webcams, bed mesh and related Klipper state.

The architecture is built around these rules:

1. one live Moonraker session for the active Cura printer;
2. one authoritative owner for each mutable domain;
3. one production core-status poll path;
4. one active-printer HTTP connection pool;
5. HTTP only — no WebSocket transport;
6. category-aware adaptive polling instead of independent feature poll loops;
7. request coalescing and explicit replacement/cancellation semantics;
8. HTTP command acceptance is separate from observed command completion;
9. every asynchronous result is guarded against stale printer/Cura/job/file/index state;
10. Cura-facing runtime code is split into focused components rather than one extension monolith;
11. QML is presentation, not a second domain/state layer;
12. v3.0 user-visible behavior and configuration compatibility are preserved unless deliberately changed and tested.

---

## 2. Authoritative ownership

A consumer may read or invoke an authoritative owner. It must not mirror the same mutable state in another private field, convenience wrapper or compatibility object.

| Domain | Authoritative owner |
| --- | --- |
| live endpoint/API-key binding | `MoonrakerSession` + `MoonrakerHttpTransport` |
| merged core status, session generation, connection state, poll policy, request coalescing, command acknowledgements | `MoonrakerSessionState` |
| core poll lifecycle and retry/backoff | `MoonrakerClient` |
| active-printer request construction, authentication, ordinary JSON request lanes, cancellation and transport metrics | `MoonrakerHttpTransport` |
| remote print observation, current state/filename, print-run identity and same-file restart detection | `RemoteJobService` |
| remote file identity and cached local G-code identity | `RemoteFileService` |
| active G-code index, build generation, worker identity and hydration state | `GCodeIndexService` |
| Preview attachment, expected follower-written Preview position, path/ETA tracking and print-local Preview runtime state | `PreviewFollowerService` |
| scheduled end-of-layer PAUSE targets | `PauseScheduleService` |
| Cura scene/load lifecycle generation and stale-work token | `CuraLifecycleBridge` |
| current force-load/download/Cura-load/index operation phase | `OperationContext` |
| follower-specific metadata/status-probe/stream-download/PAUSE request orchestration | `FollowerTransportMixin` and `RemoteFileTransferMixin` |
| output upload/write lifecycle | `MoonrakerOutputDevice` + `MoonrakerOutputDeviceLifecycle` |

The source-contract tests intentionally reject old shadow fields and duplicate wrapper classes.

---

## 3. Top-level component model

```text
plugins/__init__.py
│
├─ MoonrakerPrintFollower                 public facade
│  └─ FollowerCoordinator                 cross-domain coordinator
│     ├─ RemoteJobService
│     ├─ RemoteFileService
│     ├─ GCodeIndexService
│     ├─ PreviewFollowerService
│     ├─ PauseScheduleService
│     ├─ CuraLifecycleBridge
│     ├─ FollowerTransportMixin
│     └─ FollowerRuntime                  focused Cura-facing composition
│        └─ MoonrakerClient
│           └─ MoonrakerSession
│              ├─ MoonrakerSessionState
│              └─ MoonrakerHttpTransport  active shared HTTP pool
│
├─ MoonrakerOutputDevicePlugin
│  ├─ MoonrakerOutputDeviceLifecycle
│  └─ Moonraker Monitor model chain
│
└─ MoonrakerFollowerMachineAction
   └─ isolated MoonrakerHttpTransport      unsaved connection-test identity only
```

The follower instance is shared with Monitor and output. They consume its client/session/transport instead of constructing independent active-printer clients.

---

## 4. Startup and compatibility migration

Cura enters through `plugins/__init__.py`.

Startup creates one `MoonrakerPrintFollower`, passes it to `MoonrakerOutputDevicePlugin`, and passes both to `MoonrakerFollowerMachineAction`.

`PrinterConfigStore` retains two compatibility migrations because they are user-visible upgrade behavior, not architectural debt:

### Legacy follower preferences

Older global Moonraker Print Follower preferences are migrated once into the active Cura machine's per-printer configuration.

- Migration is deferred while Cura machine identity is `unknown`; this avoids permanently assigning real settings to a synthetic machine.
- Once migration succeeds, it is marked complete and is not replayed for later machines.

### Standalone Moonraker Connection settings

Settings from the previous standalone Moonraker Connection integration are imported once for all stored Cura printers.

- Existing Moonraker Print Follower URL/API-key values win when already configured.
- Equivalent upload, power, filename-translation and camera settings are imported.
- Old source preferences are left intact so rollback remains possible.
- Startup migration is non-fatal: malformed or inaccessible old preference data must never prevent Cura from loading the plugin.

Do not remove either migration merely because the old storage format is not used by new installations.

---

## 5. Active Cura printer ownership

Configuration is persisted per Cura machine ID. Many machines may be configured, but only the active Cura machine owns the live session.

Do not conflate:

- **Cura machine identity** — selects persisted `PrinterConfig`;
- **Moonraker connection identity** — `(base_url, api_key)` used by the shared session/transport.

Two Cura machines may point at the same Moonraker endpoint. A machine switch is still a full ownership transition.

Safe switch order in `FollowerConfigurationMixin` is:

1. detect changed Cura machine ID;
2. stop the old `MoonrakerClient`, which invalidates/reset its shared session;
3. invalidate Cura lifecycle work;
4. make the new Cura machine ID/name authoritative;
5. reset Preview print state, pause schedule, G-code index/cache, remote job and remote file identity;
6. configure the shared client/session from the new machine's settings;
7. restart polling if the URL is usable.

This ordering is the guard for the same-endpoint/different-Cura-profile case. `MoonrakerSession` therefore does not need to duplicate Cura machine identity internally.

---

## 6. Shared Moonraker session

### `MoonrakerSessionState`

Qt-independent state/policy core containing:

- `PollPolicy`;
- `RequestCoalescer`;
- `SessionSnapshot`;
- `CommandTracker`;
- session generation;
- base URL;
- connected state;
- scheduled-pause precision guard.

The pure state layer exists so important behavior can be tested deterministically without Cura or live networking.

### `MoonrakerSession`

Binds the state core to one active `MoonrakerHttpTransport` and API key.

Changing endpoint or credentials is a rebind. Rebind/reset clears old shared status, command tracking, coalescer state, pause guard and connection state, and advances validity generation.

### `MoonrakerClient`

The only production core-status poller. It:

- configures/starts/stops the session;
- sends the shared core status query;
- coalesces overlapping refresh requests;
- performs retry/backoff;
- merges successful status into the session snapshot;
- derives capabilities;
- emits status/connection/capability/command signals;
- adjusts core cadence through `PollPolicy`.

Preview and Monitor consume this same core status stream.

---

## 7. Polling policy

`RequestCategory` separates traffic by freshness requirement:

- `CORE`
- `AUXILIARY`
- `POWER`
- `SYSTEM`
- `DISCOVERY`
- `COMMAND`
- `STATIC`

Current policy values are defined in `PollPolicy`:

| Category/state | Current cadence policy |
| --- | --- |
| core, printing | configured follower interval |
| core, imminent scheduled PAUSE | min(configured interval, 250 ms) |
| core, paused | at least 1500 ms |
| core, idle/non-active | at least 5000 ms |
| auxiliary Monitor data, printing/paused | 1000 ms |
| auxiliary Monitor data, idle | 2500 ms |
| power | 5000 ms |
| system | 10000 ms |
| discovery | 30000 ms |
| command/static | event-driven |

`MoonrakerMonitorRuntime` consumes the same `PollPolicy` and reapplies Monitor timer intervals after core status changes. Do not reintroduce hard-coded independent active/idle policy in Monitor.

### Core coalescing

`RequestCoalescer` permits one in-flight core request and at most one pending forced follow-up. Repeated refresh requests collapse instead of creating a queue.

### Non-core lanes

`MoonrakerHttpTransport.send_json()` identifies ordinary JSON work by `(owner, channel)`. Callers explicitly choose whether a newer request replaces a running request.

---

## 8. HTTP transport and observability

`MoonrakerHttpTransport` is the only production module that constructs `QNetworkAccessManager`.

For the active printer it owns:

- the shared network manager/connection pool;
- endpoint/API key;
- request construction and standard headers;
- transfer timeout where supported;
- JSON encoding/decoding and Moonraker error conversion;
- owner/channel pending lanes;
- lane/owner/global cancellation;
- transport generation;
- request serials;
- category metrics and elapsed-time logging.

Representative lanes include:

```text
core::status
follower::metadata
follower::scheduled-pause
monitor::aux
monitor::power-list
output:<machine-id>::json
```

Per-category metrics record started/completed/failed counts and average elapsed time. Debug logs include request ID, category, lane, method, elapsed time and outcome.

### Allowed isolated transports

An isolated `MoonrakerHttpTransport` is allowed only for a deliberately separate identity that must not rebind the active session, for example:

- Machine Action testing unsaved candidate URL/API-key values;
- follower alternate/unsaved status probes.

These use the same transport implementation but are not the active-printer session.

### Streaming/multipart exception

Large G-code downloads and multipart uploads need direct `QNetworkReply` lifecycle control. They still:

- build requests through `transport.request()`;
- send through `transport.network`;
- use the same connection pool/authentication;
- maintain explicit lifecycle/job/device guards and cleanup.

They must not create their own network manager.

---

## 9. Command acknowledgement

Stateful commands distinguish HTTP acceptance from observed completion:

```text
issued
  -> HTTP accepted
      -> expected shared printer state observed -> confirmed
      -> timeout waiting for state              -> timed_out
  -> request/HTTP failure                       -> failed
```

`CommandTracker` owns this state. Examples include Monitor Pause/Resume/Cancel and scheduled follower PAUSE.

A caller may treat HTTP success as terminal only for an operation that genuinely has no meaningful later state to observe.

---

## 10. Public follower, coordinator and runtime composition

### `MoonrakerPrintFollower.py`

Tiny public facade over `FollowerCoordinator`. Feature implementation does not belong here.

### `FollowerCoordinator.py`

Creates the six requested domain services and coordinates events that genuinely cross domains. Examples:

- new print run -> reset print-local Preview state, index/cache and scheduled pauses;
- index invalidation -> reset only index-derived Preview tracking;
- manual Preview movement -> detach following;
- pause-target proximity -> enable/disable shared precision polling;
- cache adoption/discard -> filesystem cleanup;
- delayed Cura callbacks -> capture/check lifecycle generation.

The coordinator is not a second domain-state store.

### `FollowerRuntime.py`

A small multiple-inheritance composition root. It declares concrete signals/constants and assembles focused Cura-facing mixins. It is intentionally not a monolithic implementation class.

The runtime mixins must not define competing implementations of the same method and rely on MRO ordering to choose one.

---

## 11. Focused follower runtime modules

| Module | Primary responsibility |
| --- | --- |
| `FollowerBootstrap.py` | QObject/Extension bootstrap, shared client/config creation, timers/signals, persistent-index cache and temp roots |
| `FollowerConfiguration.py` | active per-printer config, attach/detach, active-machine rebind, URL/status helpers |
| `CuraLifecycleRuntime.py` | Cura scene/slicing lifecycle consequences and invalidation orchestration |
| `CuraViewBridge.py` | SimulationView discovery/connection and Preview-side Cura bridging |
| `CuraFileLifecycle.py` | Cura file completion, shutdown/deinitialize and resource cleanup |
| `PreviewFollowerRuntime.py` | Preview-stage/native-nozzle/manual-view watcher integration |
| `PreviewStatus.py` | Preview status/control-model presentation state |
| `PreviewEta.py` | selected-layer ETA calculations/presentation |
| `PreviewControls.py` | Preview control creation, QML wiring and actions |
| `PreviewLoad.py` | explicit load-current-print confirmation/load orchestration |
| `PreviewFollowEngine.py` | shared Moonraker status -> Preview layer-follow orchestration |
| `PathFollowEngine.py` | within-layer path/nozzle progress calculation and application |
| `GCodeIndexRuntime.py` | index build/hydration/persistent-cache worker orchestration |
| `RemoteFileTransfer.py` | streamed remote G-code completion and Cura file-load lifecycle |

`FollowerTransportMixin` sits alongside this runtime composition and owns follower-specific Moonraker request orchestration.

---

## 12. Remote print-run identity

`RemoteJobService` owns the latest physical print observation and print-run key.

`PrintObservation` includes:

- printer state;
- filename;
- file size;
- file position;
- print duration.

A new print run is detected when an active print:

- has no existing run key;
- changes filename or file size;
- becomes active after a non-active observation;
- rewinds file position; or
- rewinds print duration.

The rewind rules are essential because printing the same file again must get a new run identity.

Runtime code must read current remote state/filename through `RemoteJobService`, not re-create `_last_remote_state`/`_last_remote_filename` shadow owners.

---

## 13. Remote file and G-code index ownership

### `RemoteFileService`

Owns:

- current `RemoteFileIdentity`;
- the job key for which metadata is valid;
- cached streamed filename/path/job key.

### `GCodeIndexService`

Owns:

- active index filename/job key/data;
- layer byte ranges;
- motion offsets;
- Moonraker-current-layer mapping;
- index generation;
- active build filename/job/cancel event/thread;
- in-progress hydration layers/threads.

### `GCodeIndexRuntimeMixin`

Owns worker orchestration, not index state. Build/hydration callbacks validate index generation plus relevant Cura lifecycle/job identity before installing results.

Persistent cache identity is based on remote file identity. A cache entry must never be silently reused for a different print/file identity.

---

## 14. Large G-code pipeline

When path following or explicit current-print loading needs remote G-code:

1. resolve/refresh remote metadata;
2. stream the remote file through the shared transport's request builder/network manager;
3. keep regular core polling responsive while streaming;
4. write to a temporary job-specific file;
5. build/restore a compact or full `LayerMotionIndex` as appropriate;
6. hydrate compact layers on demand;
7. persist reusable indexes when remote identity is strong enough;
8. reject stale download/index/hydration completion using lifecycle/job/index identity.

Cura `readLocalFile()` may continue asynchronously, so the cached file must outlive the call until Cura's file lifecycle confirms it is safe to clean up.

---

## 15. PreviewFollowerService state scopes

`PreviewFollowerService` is the authoritative Preview-follow state owner. It deliberately separates two reset scopes.

### `PreviewTrackingState`

Index/path/ETA-derived state that may be reset when the active G-code index changes:

- active path layer;
- monotonic path fraction;
- resolved remote layer used by Preview/ETA fallback;
- selected-layer ETA text;
- speed factor;
- ETA anchor layer;
- ETA anchor print duration;
- current print duration.

`reset_tracking()` clears this state and marks the toolhead path invalid, but does **not** pretend a new print run has begun.

### `PreviewRuntimeState`

Print-local state that survives ordinary index invalidation:

- physical observed remote layer;
- last extruder position;
- whether auto-Preview switching has occurred for the current job;
- whether the current toolhead path estimate is valid.

`reset_print_state()` resets both tracking and print-local runtime state. It is used for a new/inactive print binding or active-printer ownership change.

### Attachment/expected position

The same service also owns:

- whether following is detached/paused;
- the expected layer/minimum-layer/path/minimum-path written by the follower.

Expected position is used to distinguish plugin movement from direct user Preview-slider/path interaction.

Do not reintroduce raw follower fields for any of these values.

---

## 16. Preview following and manual detach

Shared status flow is conceptually:

```text
MoonrakerClient shared status
 -> RemoteJobService observes run/state
 -> update Preview service speed/duration
 -> ensure metadata/index as needed
 -> resolve physical remote layer
 -> PreviewFollowerService.observe_remote_layer()
 -> scheduled PAUSE crossing check
 -> if detached: continue printer observation, stop Cura Preview writes
 -> otherwise calculate requested follow mode
 -> apply Preview layer decision
 -> optionally apply path progress
 -> remember follower-written Preview position
 -> update ETA/nozzle indicator/status
```

Physical printer observation deliberately continues while Preview is detached. This is required so scheduled PAUSE and ETA remain correct even when the user manually scrubs Preview.

Manual user movement is classified through `PreviewFollowerService.classify_manual_override()`. A detected layer/path override detaches following without disabling the saved follower setting.

---

## 17. Path following and layer resolution

Preferred physical layer source is Moonraker `print_stats.info.current_layer`.

When available, the active G-code index mapping translates slicer/Moonraker layer numbering into Cura index space. Configuration may also define whether raw Moonraker layer numbers are one-based.

Fallbacks include:

- file-position lookup against G-code layer ranges;
- configured Z-height fallback against Cura Preview geometry.

Within-layer path progress uses indexed motion data plus available Moonraker motion/file-position state. Service-owned path fraction is monotonic within a layer to prevent visible backward jumps caused by noisy/reordered observations.

Monitor uses the same follower/index interpretation for physical printer layer; it must not derive printer layer from the user's current Cura Preview slider position.

---

## 18. Selected-layer ETA

`PreviewEtaMixin` calculates ETA from slicer/index layer elapsed-time metadata plus live print progress.

Relevant state is service-owned:

- current physical observed/resolved layer;
- path fraction when available;
- speed factor;
- current print duration;
- per-layer print-duration anchor;
- index layer elapsed times.

Changing Preview selection must not mutate physical printer-layer observation.

---

## 19. Scheduled end-of-layer PAUSE

`PauseScheduleService` owns print-local zero-based target layers. Schedules are never persisted into `PrinterConfig`.

A pause for target layer `N` becomes due only after observation has advanced beyond it (`target < current`). This implements "pause at end of layer", not "pause when entering layer".

When a scheduled layer is current or one layer ahead, `FollowerCoordinator` enables the session pause guard. The existing core poll cadence tightens temporarily instead of creating a new timer/poller.

PAUSE is issued through the normal Klipper `PAUSE` G-code macro via Moonraker and the shared transport. HTTP acceptance is tracked separately from the later observed `paused` printer state.

The PAUSE reply is guarded by:

- scheduled-pause request generation;
- Cura lifecycle generation;
- remote print-run identity.

---

## 20. Cura lifecycle and stale work

`CuraLifecycleBridge` solely owns the Cura lifecycle generation/reason/token.

`CuraLifecycleRuntimeMixin` owns the Cura-side consequences of invalidation:

- mark toolhead path invalid;
- hide the nozzle indicator;
- clear expected Preview position;
- cancel index work;
- abort relevant follower requests when requested;
- reset `OperationContext`;
- clean deferred temporary data when safe.

Cura scene structure changes, slicing start/cancel/finish, file loading and active-machine changes are asynchronous ownership boundaries.

Any callback delayed with `QTimer` captures a lifecycle token and checks it before running. Worker/download callbacks additionally validate the identities relevant to their domain.

---

## 21. Concurrency model

### Cura/Qt UI thread

Owns:

- QML interaction;
- shared HTTP request/reply dispatch;
- follower/Monitor/output state transitions;
- Cura Preview writes;
- lifecycle signals.

It must never perform blocking network I/O, sleeps/busy waits or large G-code indexing work.

### Worker threads

Used for:

- G-code index building;
- compact-index layer hydration;
- persistent-index cache writes.

Worker results return through guarded Qt signals/callbacks and may be discarded when stale.

---

## 22. Monitor architecture

The active Monitor model is composed through:

```text
MoonrakerMonitorModel.py
 -> MoonrakerMonitorRuntime.py
 -> MoonrakerMonitorControls.py
 -> MoonrakerMonitorTypedControls.py
```

`MoonrakerOutputDevicePlugin` instantiates the final typed model and packages the bed-mesh/dashboard QML chain.

Monitor core state comes from the follower's shared `MoonrakerClient`. It does not issue a second `status_endpoint` core poll.

Monitor-only traffic uses the follower's shared `MoonrakerHttpTransport` with owner `monitor` and category-specific lanes.

`MoonrakerMonitorRuntime` reapplies shared `PollPolicy` intervals as printer state changes, including the slower idle auxiliary cadence.

When a Monitor becomes inactive:

- timers stop;
- Monitor request generation advances;
- `monitor` transport lanes are cancelled;
- later stale callbacks/status application are ignored.

Monitor command UI waits for observed command confirmation where applicable.

---

## 23. Output and upload architecture

`MoonrakerOutputDevice` uses the same active transport and shared readiness status.

JSON calls use a machine-specific owner:

```text
output:<cura-machine-id>::json
```

Multipart upload sends through `transport.network` because Qt's multipart API owns its reply directly.

`MoonrakerOutputDeviceLifecycle` adds Cura/QML-safe write completion, deferred dialog acceptance/cancellation and remote upload-folder discovery.

### Device deactivation/rebind

When an output device loses active ownership because Cura switches printer, removes the stack, invalidates the URL, replaces the current device or stops the plugin, `MoonrakerOutputDevicePlugin` explicitly deactivates it before removal.

Deactivation:

1. stops that device's Monitor;
2. cancels pending machine-specific JSON lanes;
3. aborts a running multipart upload reply;
4. releases dialog/stream/power/readiness state;
5. completes an already-started Cura write lifecycle exactly once.

This is required because multipart replies are intentionally outside `MoonrakerHttpTransport.send_json()`'s pending-lane map.

---

## 24. Machine Action and connection testing

`MoonrakerFollowerMachineAction` edits the active Cura machine's `PrinterConfig`.

Its Test Connection flow may test unsaved URL/API-key values. Rebinding the live session for that test would disrupt an active print/Monitor/output session, so it uses a deliberately isolated `MoonrakerHttpTransport`.

The probe still uses the common transport implementation, timeout behavior and request categories. It checks server info and required printer objects and can be cancelled without affecting the active session.

---

## 25. Native nozzle lifecycle

`NativeNozzleLifecycle.py` owns the compatibility logic that keeps Cura's native SimulationView nozzle indicator visible where available.

It is defensive because Cura Preview object structure varies across supported versions. Failure to locate/update the native indicator must not break following.

The old `NativeNozzleFallback.py` name is retired; there is one implementation owner.

---

## 26. OperationContext

`OperationContext` is the authoritative state for a follower operation spanning remote resolution/download and Cura load/index work.

Phases are:

- `IDLE`
- `RESOLVING`
- `DOWNLOADING`
- `CURA_LOADING`
- `INDEXING`
- `READY`
- `ERROR`

Use it instead of parallel booleans for force-load/Cura-load state.

---

## 27. File/module ownership map

### Core/session/transport

| File | Responsibility |
| --- | --- |
| `Core.py` | pure operation/file/pause/manual-override helpers |
| `MoonrakerSession.py` | pure shared state, policy, coalescing and command tracking; session wrapper |
| `MoonrakerClient.py` | single core polling lifecycle |
| `MoonrakerTransport.py` | shared HTTP implementation and metrics |
| `MoonrakerProtocol.py` | endpoint/protocol helpers |

### Follower domains

| File | Responsibility |
| --- | --- |
| `MoonrakerPrintFollower.py` | public facade |
| `FollowerCoordinator.py` | cross-domain coordination |
| `FollowerRuntime.py` | runtime-mixin composition |
| `FollowerTransport.py` | follower-specific Moonraker I/O orchestration |
| `RemoteJobService.py` | print observation/run identity |
| `RemoteFileService.py` | remote file/cache identity |
| `GCodeIndexService.py` | active index/build/hydration state |
| `PreviewFollowerService.py` | attachment, expected position, tracking and print-local Preview state |
| `PauseScheduleService.py` | print-local pause schedule |
| `CuraLifecycleBridge.py` | Cura lifecycle generation |

### G-code/index

| File | Responsibility |
| --- | --- |
| `GCodeIndex.py` | index data structures/build/hydration/persistent-cache algorithms |
| `GCodeIndexRuntime.py` | worker orchestration |
| `DownloadStream.py` | bounded streamed-file target helper |
| `RemoteFileTransfer.py` | streamed-reply completion and Cura load |

### Cura/runtime integration

The focused runtime modules listed in section 11 own their named Cura-facing responsibilities. Do not move domain state back into them for convenience.

### Monitor/output/settings

| File | Responsibility |
| --- | --- |
| `MoonrakerMonitorModel.py` | base shared-status Monitor model and peripheral requests |
| `MoonrakerMonitorRuntime.py` | follower layer interpretation + shared adaptive timer policy |
| `MoonrakerMonitorControls.py` | Klipper controls/macros/tuning |
| `MoonrakerMonitorTypedControls.py` | typed/specialized Monitor controls including bed mesh |
| `MoonrakerOutputDevice.py` | base shared-transport output/upload implementation |
| `MoonrakerOutputDeviceLifecycle.py` | re-entrancy-safe write/dialog/folder/deactivation lifecycle |
| `MoonrakerOutputDevicePlugin.py` | active output-device ownership and Monitor installation |
| `MoonrakerFollowerMachineAction.py` | per-printer settings and isolated candidate-connection probe |
| `PrinterConfig.py` | typed per-printer configuration + compatibility migrations |

---

## 28. Removed duplicate layers

These names are intentionally absent and must not be recreated as thin wrappers around current owners:

- `PauseScheduler.py`
- `PreviewController.py`
- `PrintTracker.py`
- `GCodeRepository.py`
- `FollowerSession.py`
- `FollowerStateBridge.py`
- `MoonrakerMonitorSession.py`
- `MoonrakerOutputSession.py`
- `NativeNozzleFallback.py`

If a new abstraction cannot state a distinct ownership/responsibility boundary, it probably should not exist.

---

## 29. Anti-patterns

Do not introduce:

- a second active-printer `MoonrakerClient`;
- a second core-status poll loop in Preview, Monitor or output;
- direct `QNetworkAccessManager` construction outside `MoonrakerTransport.py`;
- WebSockets;
- runtime fields mirroring service-owned job/file/index/Preview/pause/lifecycle state;
- compatibility properties whose only purpose is to keep duplicate owners alive;
- persistent scheduled-pause layer numbers;
- synchronous network calls or `sleep()` on Cura's UI thread;
- unguarded worker/network callbacks;
- QML-owned protocol/domain state;
- MRO-dependent duplicate runtime methods;
- a streaming/multipart reply without explicit cancellation/identity lifecycle;
- removal of legacy configuration migration merely to make the new model look cleaner.

---

## 30. Testing architecture

Tests are part of the architecture contract.

### Pure/deterministic tests

`tests/fake_moonraker.py` provides an in-process deterministic Moonraker with no sockets, threads or wall-clock dependency. Architecture tests cover:

- request coalescing;
- adaptive poll policy;
- endpoint/API-key rebind invalidation;
- HTTP acceptance versus observed command completion;
- deterministic command timeout;
- scheduled-pause precision and confirmation;
- same-filename print restart identity;
- long-print status progression;
- lifecycle/race guards.

### Source/ownership contracts

Source-contract tests enforce structural laws such as:

- public follower remains thin;
- exact services exist and retired wrappers do not;
- focused runtime contains no private HTTP stack;
- only `MoonrakerTransport.py` constructs network managers;
- no WebSocket path exists;
- legacy service-state aliases/shadow fields remain absent;
- Preview tracking/runtime state stays service-owned;
- Monitor consumes shared transport/session;
- output uses shared transport and explicit rebind cleanup.

### Behavioral regression tests

The suite retains v3.0 behavior contracts for, among other things:

- Cura/Orca/Prusa/variable-layer indexing;
- Preview/manual detach behavior;
- scheduled end-of-layer PAUSE;
- ETA;
- bed mesh;
- Monitor dashboard/controls;
- upload lifecycle and folder discovery;
- native nozzle lifecycle;
- per-printer configuration and both upgrade migrations;
- supported Cura SDK/QML surface.

Source-string tests should follow authoritative module ownership when code is moved; they must not force obsolete file placement or field names.

---

## 31. Release and package invariants

CI must verify at least:

- all discovered unit/regression/architecture tests;
- QML structural sanity;
- Python syntax for the supported CI interpreter matrix;
- deterministic package construction;
- exact source/package parity;
- Cura Marketplace source ZIP layout;
- canonical package ID and version metadata;
- absence of tracked Python cache artifacts.

`ARCHITECTURE.md` describes architecture, not exact supported SDK version numbers; those remain canonical in metadata/tests.

---

## 32. Change checklist

Before merging a change, ask:

1. Which domain owns the mutable state?
2. Is that state already owned somewhere else?
3. Does the feature need core status? If so, can it consume/extend the shared core snapshot rather than poll separately?
4. Is the request using the shared active transport unless it intentionally tests a separate identity?
5. Which `RequestCategory` applies?
6. Can refreshes overlap? What is the coalescing/replacement rule?
7. If a command is stateful, what observation confirms completion?
8. What invalidates this asynchronous work: session, Cura lifecycle, print run, file, index, request generation or output-device ownership?
9. Does a machine switch cancel/neutralize the old work even if both profiles use the same endpoint?
10. Does an index reset need `PreviewTrackingState` reset only, or is this actually a new print requiring `PreviewRuntimeState` reset too?
11. Does a streaming/multipart reply have explicit cancellation and cleanup?
12. Does the change preserve v3.0 behavior/configuration migration?
13. Are deterministic/behavioral tests present where practical?
14. Does package/source parity still pass?
15. Does this document still match the implementation?

---

## 33. Accepted architectural decisions

- **HTTP polling, not WebSockets.** Simpler dependency/compatibility model and sufficient precision with adaptive cadence.
- **One shared active session.** Preview, Monitor and output observe one current printer identity/state.
- **One active HTTP pool.** Streaming/multipart operations share the pool while owning reply lifecycle where required.
- **Focused runtime mixins.** Cura-facing concerns are separated physically without inventing duplicate domain owners.
- **Explicit services for mutable domains.** Job, file, index, Preview, pause and Cura lifecycle state each have one owner.
- **Two Preview reset scopes.** Index-derived tracking and print-local runtime state are deliberately distinct.
- **Observed command completion.** HTTP acceptance does not prove physical state transition.
- **Adaptive category polling.** Core and Monitor peripheral cadence respond to state/freshness needs.
- **Generation/identity guards.** Stale asynchronous work is discarded rather than allowed to mutate current state.
- **Per-printer configuration with retained migrations.** Architectural cleanup must not erase upgrade behavior.
- **Explicit output deactivation.** A Cura machine switch ends old Monitor/upload ownership before the new output device becomes active.
- **Production package-relative imports.** Tests adapt to production topology, not vice versa.

---

## 34. Summary

The central design is deliberately simple:

- Cura has one active Moonraker binding.
- `MoonrakerClient` supplies one shared core status stream.
- `MoonrakerSession`/`MoonrakerHttpTransport` provide shared state, policy and HTTP infrastructure.
- `FollowerCoordinator` composes exact domain services rather than storing duplicate state.
- `FollowerRuntime.py` is only a focused runtime composition boundary.
- `RemoteJobService`, `RemoteFileService`, `GCodeIndexService`, `PreviewFollowerService`, `PauseScheduleService` and `CuraLifecycleBridge` are authoritative owners.
- Preview path/ETA tracking and print-local Preview runtime state are separate typed scopes.
- Monitor/output reuse the active session/transport and have explicit lifecycle invalidation.
- Old configuration migrations remain supported.
- Every asynchronous path is expected to prove that its result still belongs to the current printer/Cura/job/file/index/device before applying it.

When extending the plugin, preserve these ownership and validity boundaries first. New functionality should plug into them, not create parallel sessions, pollers, caches or compatibility state.
