# Moonraker Print Follower architecture

This is the current implementation contract, not a roadmap. Package identity,
version and SDK support are defined in `package.json`, `plugins/plugin.json` and
CI. Release history belongs in `CHANGELOG.md`. Change procedures, including the
version bump checklist, live in `INSTRUCTIONS.md`.

## 1. Design rules

- One active Cura/Moonraker binding, one core status poller and one active HTTP pool.
- Composition rather than a shared-`self` mixin runtime or a Monitor subclass stack.
- One owner per mutable domain. Components receive explicit capabilities, never
  the entire follower/model or an attribute-forwarding context.
- Physical printer state is separate from the user's Preview selection.
- Immutable observations and read-only query interfaces cross domain boundaries.
- Cancellation invalidates ownership before aborting work or changing credentials.
- QML and Cura adapters expose presentation and user intents, not network/index policy.
- HTTP only — no WebSocket transport. Candidate connection probes are isolated.
- No retired runtime implementations, compatibility aliases or dynamic `__getattr__`
  forwarding. Structural tests enforce these rules.

## 2. Composition roots and public APIs

`plugins/__init__.py` registers the extension, output-device plugin and Machine
Action. Its imports remain lazy so pure modules can be imported without Cura.

`MoonrakerPrintFollower.py` is the stable QObject/Extension facade. Its public
capabilities are `client`, `session`, `transport`, `print_state`, `bed_mesh`,
`current_printer_config()`, `current_printer_identity()`, `apply_printer_config()`,
the two Preview action slots and `deinitialize()`. It contains no following logic.

`FollowerRuntime.py` constructs and closes the follower's components. It implements
no domain algorithms. `PrintCoordinator.py` connects cross-domain events through
explicit constructor dependencies; components never call back into the coordinator
through a shared mutable follower object.

`MoonrakerOutputDevicePlugin.py` is the Monitor/output composition boundary. It
passes explicit client, configuration and immutable print-state capabilities into
the single `MoonrakerMonitorModel.py` and the output adapter. It does not expose
private follower state to either integration.

### Ownership map

| Component | Owns | Does not own |
| --- | --- | --- |
| `PrinterBinding.py` | Per-printer configuration, migrations, active-machine transitions | Preview, files, uploads |
| `PrinterConfig.py` | Per-machine settings schema, URL normalisation, both migrations | Networking or Qt |
| `MoonrakerClient.py` | Core polling, retries, command deadline timer, Qt notifications | Cura lifecycle |
| `MoonrakerSession.py` | Binding state, merged core snapshot, polling policy, coalescer, command tracker | UI or G-code files |
| `MoonrakerTransport.py` | Request builder, credentials, HTTP pool, JSON lanes and metrics | Feature state |
| `MoonrakerProtocol.py` | Endpoint construction, file identity, coordinate conversion | Networking or UI |
| `RemoteJobService.py` | Print observation and same-filename run identity | Preview selection |
| `PrintState.py` | Immutable `PrintSnapshot`/`PhysicalLayer` and the single `LayerResolver` | QML/Cura writes |
| `RemoteFileService.py` | Metadata, streamed downloads, cached files and `FileLease` | Index algorithms or Cura loading |
| `DownloadStream.py` | Bounded streaming G-code downloads to disk | Networking policy or Cura |
| `GCodeIndexService.py` | Index lifecycle, bounded worker execution and `IndexView` | Networking or UI |
| `GCodeIndex.py` | Parsing, motion matching, compact hydration and cache serialization algorithms | Application orchestration |
| `FollowController.py` | Follow-mode decisions and state precedence | Preview writes or networking |
| `CuraIntegration.py` | Scene/view/file lifecycle, guarded callbacks and Preview API access | Printer protocol |
| `CuraAdapter.py` | Typed Cura view access, machine identity, Preview write decisions | Policy or networking |
| `CuraLifecycleBridge.py` | Cura-scene generation tokens for stale-work rejection | Scene contents |
| `NativeNozzleLifecycle.py` | Cura native-nozzle repair during exact following | Scene mesh semantics |
| `PreviewFollower.py` | Frozen `PreviewState`, attachment, expected positions, path progress and ETA | Downloads or workers |
| `PreviewSmoothing.py` | Pure display-path convergence policy (bounded, monotonic within a layer) | Qt or view writes |
| `PreviewMotion.py` | The Qt tick driver for the smoothed displayed path and its view writes | Physical observations |
| `PauseScheduleService.py` | Pure print-local target set and crossing policy | Network commands |
| `PauseController.py` | Scheduled PAUSE command and acknowledgement lifecycle | Preview rendering |
| `PreviewPresentation.py` | Preview QML objects, displayed values and user-intent signals | Following or scheduling policy |
| `BedMeshPresenter.py` | Active mesh overlay, visibility preference and Preview mesh controls | Macro execution |
| `BedMeshSceneNode.py` | Mesh surface/boundary geometry and shader rendering | Scene composition |
| `MonitorData.py` | Monitor request lifetime, category timers and frozen `MonitorSnapshot` | QML declarations |
| `MoonrakerMonitorModel.py` | The single Qt Monitor model: property declarations and projection merge | Domain policy or networking |
| `MoonrakerFollowerMachineAction.py` | Configuration QML properties, validation and the isolated probe transport | Live binding state |
| `MonitorCommands.py` | Monitor action acknowledgement and emergency-stop click sequence | Sliders or discovery |
| `MonitorTuning.py` | Debounce, pending tuning values, revision/confirmation timers | QML or printer discovery |
| `MonitorControls.py` | Macro, preset, fan/LED/PWM, setup and power/exclusion policy | Qt model inheritance |
| `MonitorFormatting.py` | Pure ETA, mesh, macro and peripheral projections/parsers | Mutable state or I/O |
| `PreviewFormatting.py` | Pure status, icon, ETA and pause-item projections for the Preview panel | Mutable state or I/O |
| `MonitorCamera.py` | Camera selection, transforms and per-printer selection persistence | Private configuration store |
| `CuraOutputWriter.py` | Cura-affine preparation of a temporary G-code/UFP file | HTTP upload |
| `UploadController.py` | One write operation: discovery, readiness, multipart stream and cancellation | Cura application or QML |
| `MoonrakerOutputDevice.py` | Cura output-device signals/dialog/message adapter | Upload state machine |

## 3. Binding and migration

`PrinterBinding.start()` performs both migrations before configuring the first live
connection. Legacy follower preferences migrate once into a real Cura machine;
when the initial identity is `unknown`, migration is retried when the stack appears.
Standalone Moonraker Connection settings are imported for their stored machines.
Migration failures are logged independently and must not prevent plugin startup.

Switching Cura machines invalidates even when both profiles use the same endpoint.
Changing URL/API key on the same machine also stops the old session before settings
are persisted/rebound. Other preference edits do not cancel a valid upload.

`MoonrakerClient.sessionInvalidated` is synchronous on the UI thread and occurs
before the transport changes identity. The coordinator clears print/file/index/
Preview/pause state, and output/Monitor owners deactivate old work. Rebinding never
means that an existing upload may silently move to another printer.

## 4. Shared networking and polling

`MoonrakerTransport.py` is the only production module constructing
`QNetworkAccessManager`. Ordinary JSON uses `(owner, channel)` lanes with explicit
replacement/cancellation. Request IDs, categories, latency and errors are logged
without credentials. Streaming downloads and multipart uploads use the same request
builder/pool but own their replies directly.

`SessionSnapshot` publishes fully detached status copies and stores defensive
copies of merged patches, so no consumer can mutate session internals through a
published snapshot.

The Machine Action may create an isolated instance of the same transport for
unsaved credentials; a probe must not reconfigure the live binding.

| Traffic | Policy |
| --- | --- |
| Core, printing | Configured interval |
| Core, imminent scheduled PAUSE | min(configured, 250 ms) |
| Core, paused | At least 1500 ms |
| Core, idle | At least 5000 ms |
| Monitor auxiliary, active/paused | 1000 ms |
| Monitor auxiliary, idle | 2500 ms |
| Power | 5000 ms |
| System | 10000 ms |
| Discovery/static configuration | 30000 ms or explicit refresh |

`MonitorData` alone applies Monitor timer policy. An unchanged interval is not
written back to an active QTimer, because that would restart it and starve slower
polls. Full Klipper configuration is discovered separately; auxiliary polling asks
only for volatile SAVE_CONFIG fields.

Periodic core ticks skip overlapping requests. Forced refreshes coalesce into at
most one follow-up, but never bypass failure backoff. Queued refreshes and completion
handlers check generation, including after synchronous signal subscribers run.
A stale completion must not clear the new generation's coalescer slot.

Metrics cover ordinary JSON lanes, not all wire traffic: cancelled requests count
as started but not completed, and streaming/multipart bytes are outside those counters.

## 5. Physical state and Preview

The coordinator observes a print through `RemoteJobService`, then resolves its
physical layer through the one `LayerResolver`. Monitor reads `print_state`; it
does not run another resolver or advance Preview's extrusion state.

Resolution prefers exact G-code current-layer mapping, then reported layer numbering,
then indexed file position. Configured Z fallback requires extrusion advancement,
so a temporary Z-hop does not move the physical layer. Available metadata/geometry
can supply height/count information. A byte-progress percentage is not treated as
a reliable layer number. Local geometry never clamps the physical observation;
only Preview's requested display layer is clamped to the local view.

`PreviewFollower` receives immutable print observations and `IndexView` queries.
It alone changes `PreviewState` using replacement values. External code cannot
mutate path/ETA/attachment fields. `reset_tracking()` clears index-derived progress
and anchors while preserving print observation and attachment; `reset_print()`
clears print-local state without automatically reattaching a manually detached view.

Manual Preview changes are detected against remembered plugin-written values.
`CuraIntegration.writing_preview()` suppresses callbacks from plugin writes, while
user writes detach the follower. Preview view reads and writes go through typed
`CuraAdapter` accessors rather than stringly-named view methods. Physical
observation and scheduled PAUSE continue while Preview is detached. ETA uses
slicer layer timing, speed, path progress and observed duration anchors—not
G-code byte percentage as time.

Path smoothing is display-only: `PreviewMotion` animates the displayed path
toward the newest physical observation using the pure `PreviewSmoothing`
policy. Observations are stale samples of the true trajectory, so the head
may glide past the newest one by a bounded one-poll lookahead and never
decreases within a layer; if reality turns out to be behind the head, the
head waits rather than snapping back. Layer transitions are jumped, never
animated. The physical `path_fraction` that ETA consumes is unchanged, and
each animated write re-remembers the plugin-written position so the override
detector cannot mistake the animation for a manual grab.

## 6. Remote files, leases and bounded indexing

`RemoteFileService` binds metadata/cache identity to a job token. Same-filename
restarts invalidate old metadata and downloads. A streamed download uses a bounded
Qt read buffer, writes incrementally to a temporary file and verifies known file
size before publication.

Metadata completeness is separate from download identity: a failed metadata
request installs a fallback identity so downloads proceed, then retries with
backoff; only a successful response marks the run's metadata complete.

A `FileLease` explicitly keeps that file alive for an index worker or Cura parse
job. Rebinding retires old files; deletion waits for all leases to close. An unrelated
Cura file completion cannot release the current remote file. The matching application
completion callback retains the lease even if the extension is deinitialized first.

`GCodeIndexService` submits at most one worker job at a time. Requests coalesce into
desired state rather than an unbounded executor queue. Rebinding cancels the old
build and waits asynchronously for its completion before scheduling new work.
Persistent cache restoration, parsing, hydration and cache persistence all run off
the UI thread. Only generation-valid results are published on the Qt thread.

`IndexView` exposes immutable ranges/maps/timing and read-only query operations.
It does not expose mutable motion arrays or worker handles. Compact layers are used
only after hydration has published complete arrays. Index algorithms and cache
format remain in `GCodeIndex.py`; they are not duplicated in runtime components.

## 7. Commands and scheduled PAUSE

HTTP acceptance is distinct from observed printer-state confirmation. Only fresh
`print_stats.state` can confirm a stateful command; an unrelated patch or cached
snapshot cannot. The client-owned deadline timer runs while commands are pending,
including when status polls fail. Uncertain commands are not automatically replayed.

`PauseController` composes the pure schedule and shared command tracker. A target
becomes due only after the physical layer advances beyond it. Multiple crossed
targets coalesce into one PAUSE; an already-pending command retains its identity.
Schedules are never persisted into printer configuration.

This remains best-effort host scheduling, not firmware-exact boundary execution.
Polling latency, offline periods and short layers can delay observation. That is a
protocol limitation rather than unfinished component architecture.

## 8. Monitor and bed mesh

There is one Qt Monitor model, directly derived from Cura's `PrinterOutputModel`.
Its declared Qt properties/slots retain the existing QML surface. `value_property`
is a declarative presentation binding—not dynamic attribute forwarding or a hidden
second model. Controller-produced UI values are copied into the model projection.

Monitor data is a deeply frozen snapshot. Controllers receive data/command/tuning
capabilities, not the model or follower. Tuning owns its revisions and debounce
lifetimes. Macro argument parsing is cached until static configuration changes.
Camera selection is persisted through the public configuration operation.

`BedMeshPresenter` alone owns the active scene node and Preview mesh UI. It uses
`CuraIntegration.decorating_scene()` to distinguish its non-sliceable visual changes
from user scene changes. Inactive Monitor instances cannot overwrite the active mesh.
No Monitor code mutates private follower attributes or disconnects follower handlers.

## 9. File-backed upload lifecycle

The output adapter emits `writeStarted`, asks `CuraOutputWriter` to prepare a leased
temporary file, and hands it to `UploadController`. Cura writer invocation stays on
the UI thread because its API is Cura-affine; it writes to a file, not an in-memory
StringIO/BytesIO buffer. Multipart uses `QFile` and `QHttpPart.setBodyDevice()`.
The prepared file and Qt body device outlive the network operation.

Each operation captures its generation, session and active machine identity.
Folder scans, readiness retries, dialog accept/cancel and replies validate that
ownership before further I/O. Cleanup clears reply ownership before `abort()`, which
may emit a completion synchronously.

Dialog teardown and terminal delivery occur after the initiating QML handler returns.
The controller remains busy through success/error delivery; the adapter acknowledges
terminal delivery immediately before emitting `writeFinished`. Exactly one started
write is completed, including cancellation and rebind. A later write cannot receive
an earlier write's terminal notification.

## 10. Extending the architecture

- New high-frequency printer fields: extend the one core query and immutable print observation.
- New Monitor objects: add discovery/projection to `MonitorData` and pure formatting.
- New Preview projections: add pure formatting in `PreviewFormatting.py`.
- New display behaviour: keep policy pure (like `PreviewSmoothing`) and put
  the Qt tick/writes in `PreviewMotion`; never let displayed state feed back
  into physical observations.
- New controls: add policy to a focused controller and declare the Qt property/slot.
- New file operations: consume `FileLease`, never infer lifetime from Preview flags.
- New index work: use the bounded index owner and generation-valid publication.
- New Cura APIs: isolate them in Cura integration/presentation or the writer adapter.
- New state: identify the owner before adding a field; do not add a shared context.

Dependency tests reject upward imports, private follower access, cycles, runtime
mixins, forwarding magic and reintroduction of retired modules. Changes to a boundary
must update its behavioural tests, not just rename source strings until tests pass.

## 11. Verification and release gates

Pure tests cover parsing, layer resolution, immutable state, follow modes, ETA,
configuration, metadata identity and protocol policy. Real-Qt tests exercise actual
production components with minimal Cura host doubles, scripted/stale completions,
and loopback HTTP. They cover startup/migration, rebind, file leases, bounded workers,
Monitor timing/tuning, the QML meta-object surface and upload terminal ordering.

CI runs real-Qt regressions on Python 3.10–3.12; Cura provides Qt in production, so
no Qt wheel is bundled in the plugin. Stdlib-only local runs explicitly skip the
Qt suite and must not be presented as equivalent validation.

Release verification includes QML structural sanity, supported Python syntax,
package/source byte parity, reproducible archives and Marketplace layout. Production
contains no alternative legacy runtime. Framework inheritance is limited to Qt/Cura
adapters; domain behaviour is composed.

The harness is not Cura or printer firmware. Final release smoke tests must still
check real QML rendering, native nozzle/bed-mesh integration, Cura file-writer
compatibility, multi-printer interaction and large-file responsiveness. The architecture
removes the known shared-object migration debt; it cannot guarantee that future Cura
or Moonraker API changes will never require deliberate boundary changes.
