# Moonraker Print Follower architecture

This is the current implementation contract, not a roadmap. Package identity,
version and SDK support are defined in `package.json`, `mpf/plugin.json` and
CI. Release history belongs in `CHANGELOG.md`. Change procedures, including the
version bump checklist, live in `INSTRUCTIONS.md`.

## 1. Design rules

- One active Cura/Moonraker binding, one core status poller and one active HTTP pool.
- Composition rather than a shared-`self` mixin runtime or a Monitor subclass stack.
- One owner per mutable domain. Components receive explicit capabilities, never
  the entire follower/model or an attribute-forwarding context.
- Physical printer state is separate from the user's Preview selection.
- Immutable observations and read-only query interfaces cross domain boundaries.
- Cancellation invalidates ownership before aborting work or changing credentials;
  a transfer operation owns its queue, target and worker — a stale writer can never
  adopt a later operation's state.
- QML and Cura adapters expose presentation and user intents, not network/index policy.
- One status feed per printer — a Moonraker websocket subscription (default) or the HTTP status poll
  (selectable, and the automatic fallback). The choice covers the status classes only: commands, the
  console store, uploads/downloads and thumbnails are HTTP in both modes, and the HTTP status path
  is never removed. The RFC 6455 client is hand-built on QtNetwork — no Qt module outside the Cura
  bundle's verified bindings may be imported. Candidate connection probes are isolated.
- No retired runtime implementations, compatibility aliases or dynamic `__getattr__`
  forwarding. Structural tests enforce these rules.

## 2. Composition roots and public APIs

`mpf/__init__.py` registers the extension, output-device plugin and Machine
Action. Its imports remain lazy so pure modules can be imported without Cura.

`MoonrakerPrintFollower.py` is the stable QObject/Extension facade. Its public
capabilities are `client`, `session`, `transport`, `print_state`, `bed_mesh`,
`current_printer_config()`, `current_printer_identity()`, `apply_printer_config()`,
the two Preview action slots and `deinitialize()`. It contains no following logic.

`FollowerRuntime.py` constructs and closes the follower's components. It implements
no domain algorithms. `PrintCoordinator.py` connects cross-domain events through
explicit constructor dependencies; components never call back into the coordinator
through a shared mutable follower object. Its refresh pass is a composition over
`NextPausePipeline.py` (the pause anchor and computation) and
`LoadStateTracker.py` (the load state and lease handoff). The pass itself is a
sequence of private phases — the toolpath sync, the frame's face (identity, stats
and metadata), the motion resolution, the running totals, the pause computation,
the plate payloads, the snapshot composition, the frame's observers, the Preview
projection and the publication — each reading the one frame the pass pinned and
handing the next a record, so no two values in a snapshot can describe two polls.

`MoonrakerOutputDevicePlugin.py` is the Monitor/output composition boundary. It
passes explicit client, configuration and immutable print-state capabilities into
the single `MoonrakerMonitorModel.py` and the output adapter. It does not expose
private follower state to either integration.

The output-device plugin retains one asynchronously compiled dashboard component
per Cura QML engine. It starts after the engine exists and a configured output
device is selected, creates no controls or camera requests, and releases the
component on stop or engine replacement. The Monitor shell still constructs its
dashboard synchronously when opened; the retained compilation removes the cold
compile wait without changing pane layout or initialization ordering.

### Package ownership

The tree is organised by domain and screen, not by programming language or
class suffix. QML and Python for a feature live together. A screen may host a
small wrapper for another feature (for example `monitor/FileManagerSection.qml`)
without becoming the owner of that feature's implementation.

| Package | Responsibility |
| --- | --- |
| `application/` | Cross-domain print orchestration: coordinator, refresh-side load tracking and next-pause pipeline. These intentionally compose domain and presentation capabilities. |
| `cura/` | Cura entry points, lifecycle, machine binding, output adapters and host-affine file preparation. |
| `settings/` | Configuration UI, schema, persistence, migration and shared migration-result presentation. The Machine Action remains a Cura adapter. |
| `printing/` | Physical print state, run identity, remote job observations, pause scheduling and print-start ownership; independent of the Preview and Monitor screens. |
| `preview/` | Cura Preview attachment/following, display smoothing, presentation and card hosts. |
| `gcode/` | Parsing, motion interpretation, indexing, cache lifecycle and immutable prepared geometry. `PlateProgress.py` prepares data; it is not the Qt renderer. |
| `geometry/` | Pure geometry shared by data processing and presentation. No screen or host dependencies. |
| `plate/` | Plate/follower rendering, GPU materials, colour projections and interaction. Consumes prepared G-code geometry, never owns parsing primitives. |
| `bedmesh/` | Mesh presentation and reusable views shared by Preview and Monitor. Screen-specific wrappers remain with the screen. |
| `monitor/` | Dashboard/model composition, observations and projections, with camera, console, controls, temperature, toolhead and layout subfeatures. |
| `detection/` | Consent-gated local model/runtime assets, single-worker CPU inference, freshness/evidence policy and the boot opt-in view. Never sends a printer command or opens a camera stream. |
| `files/browser/` | File browser QML, state, policy and stable file-row view model. |
| `files/transfers/` | Upload/download workflows, the upload dialog, streaming and remote-file leases. |
| `moonraker/` | Protocol, HTTP/websocket transport and status session; no screen dependencies. |
| `diagnostics/` | Opt-in instrumentation. Camera timing is also consumed by the connection layer, so it is not owned by the camera screen. |
| `whatsnew/` | Release-notice content, overlay and its lifecycle. |
| `widgets/` | Reusable UI primitives. |
| `resources/` | Shared theme, icons, shaders and plugin-root-relative resource paths. |

`FollowerRuntime.py` remains the composition root at the plugin root. The
`application/` workflows may compose screens; this is not permission for core
`printing/`, `gcode/`, `geometry/` or `settings/` code to import a screen.
`GCodeIndexService` uses `geometry/Polygons.py`, not Monitor formatting helpers.
Arc geometry, travel/retraction classification and motion-range validation live
in `gcode/`, so the index and prepared geometry no longer depend on `plate/`.

Python imports remain relative because Cura installs the package under its
plugin ID. There are no old-package forwarding aliases. Dynamic Python-loaded
QML and shaders use `resources/PluginPaths.plugin_path` with literal
plugin-root-relative paths; the configuration Machine Action retains the
relative `_qml_url` required by Cura. QML imports and URLs are relative to each
calling document, and `qmldir` entries point at the actual feature files.

`tests/test_domain_layout.py` checks qualified import resolution, core package
boundaries, co-location and non-vacuous nested dependency scanning.
`tests/test_resource_references.py` checks the Python entry points, QML resource
URLs, registration files and both built archive formats. Basename-based
`SourceRoot` remains useful for content tests; it is not used as evidence of
correct package ownership.

### Ownership map

| Component | Owns | Does not own |
| --- | --- | --- |
| `Polygons.py` | Pure point/segment/polygon intersection and bounds shared by indexing and presentation | Qt, screen models, networking or mutable state |
| `MotionRanges.py` | Validation of the numerical motion ranges stored in G-code indexes and prepared data | Palette selection, rendering or Cura |
| `MigrationPresentation.py` | Shared migration-result text consumed by settings and Monitor | Either screen's model or runtime |
| `PluginPaths.py` | Installed-plugin-root-relative filenames for explicit QML and shader resources | Application state or resource-loading lifecycle |
| `PrinterBinding.py` | Per-printer configuration, migration triggers, active-machine transitions | Preview, files, uploads |
| `PrinterConfig.py` | Per-machine settings schema and coercion (the records, the bounds, the normalisers) | Networking, Qt or the settings file (the persistence facade owns the files) |
| `PluginPersistence.py` | The two plugin-owned stores: the settings document and the per-machine state shards, the typed key-scoped operations and the in-memory document — one instance per file per process, constructed at the composition root and handed down | Schema, coercion or networking |
| `PersistenceMigration.py` | The one-shot migration's control flow: the strict source read, the backup gate, the verify-by-re-read interlock and the clean-as-commit-point | Qt, Resources or the stores |
| `MigrationNotice.py` | The migration-failure surfaces' ordering: the once-per-failure toast after the What's-New sequence and the escape hatch | The surfaces themselves (the toast wiring is injected; the banner lives in the model's values) |
| `MoonrakerClient.py` | Core polling, retries, command deadline timer, Qt notifications | Cura lifecycle |
| `MoonrakerSession.py` | Binding state, merged core snapshot, polling policy, coalescer, command tracker | UI or G-code files |
| `MoonrakerTransport.py` | Request builder, credentials, HTTP pool, JSON lanes and metrics | Feature state |
| `MoonrakerProtocol.py` | Endpoint construction, file identity, coordinate conversion | Networking or UI |
| `MoonrakerSocket.py` | The websocket connection: handshake, the one merged subscription set, per-class raw-fragment accumulators, the keepalive round-trip and its own generation — never the HTTP pool | Status policy, timers beyond the keepalive, the UI |
| `NextPausePipeline.py` | The next scheduled pause: the time anchor, the job-boundary layer-index reset, the baked merge and the pause computation | Downloads or the monitor's verdicts |
| `SocketFraming.py` | Pure RFC 6455 framing: handshake build/verify, frame codec, extended lengths, size caps, close codes | Qt, sockets, policy |
| `RemoteJobService.py` | Print observation and same-filename run identity | Preview selection |
| `PrintState.py` | Immutable `PrintSnapshot`/`PhysicalLayer`/`MotionProgress` and the single `LayerResolver` | QML/Cura writes |
| `RemoteFileService.py` | Metadata, streamed downloads, cached files and `FileLease` — the identity-neutral `request_metadata_only` (4.2.0) included | Index algorithms or Cura loading |
| `DownloadStream.py` | Bounded streaming G-code downloads to disk and the `DownloadOperation` lifecycle | Networking policy or Cura |
| `GCodeIndexService.py` | Index lifecycle, bounded worker execution, `IndexView`, and the shared live-motion observation service | Networking or UI |
| `PlateSplitTracker.py` | Pure shared live-motion boundary policy: accepted floor, below-floor evidence, layer/print reset and adaptive compact search window | Qt, geometry matching, rendering |
| `LoadStateTracker.py` | The refresh-side load state: the pending flags and their age-out windows, the busy term, the monitor request's terminal conditions and the lease handoff | Snapshot semantics or Cura loading |
| `GCodeIndex.py` | One bounded G-code scan, delegating header parsing and completed-record assembly | Cache files, hydration or application orchestration |
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
| `PreviewToolheadPresentation.py` | Preview Toolhead host, readouts, local chrome and epoch-fenced selected-machine intents | G-code, polling or physical-motion policy |
| `ToolheadReadout.py` | Pure physical-position, Z offset and firmware-capability readouts | Qt, transport or movement permission |
| `PhysicalMotion.py` | Pure physical path/travel guard, G92/base-coordinate conversion, inclusive G-code zero bounds and validated manual-move scripts | Qt, telemetry acquisition or command transport |
| `PreviewPresentation.py` | Preview QML objects, displayed values and user-intent signals | Following or scheduling policy |
| `RenderTiming.py` | Opt-in bounded asynchronous GPU queries for plugin draw commands, never system-wide GPU utilisation | Profile settings writes, synchronous GPU waits or native timer ownership |
| `CameraProjection.py` | Camera-keyed banner projection matrices and hover rays; unchanged telemetry reuses camera-only work | Native camera mutation or settings |
| `ViewportHover.py` | Public Qt hover routing and visible control bounds prevent scene picking through UI panes | Input interception or native UI mutation |
| `ObjectNameProjection.py` | Screen-space object-name banner placement and collision avoidance | Cura camera access, printer commands or QML ownership |
| `ObjectWork.py` | Bounded per-object extrusion checkpoints, cache validation and finish-time projection | File reading, Qt or presentation |
| `BedMeshPresenter.py` | Active mesh overlay, visibility preference and Preview mesh controls | Macro execution |
| `ToolheadGeometry.py` | Immutable canonical Z-up millimetre triangles, lowest-surface bounds anchor and isolated projection/picking | Qt, I/O or printer state |
| `ToolheadMeshFormat.py` | Stdlib-only bounded versioned triangle-plane and CAD material/body metadata format, shared with the isolated worker | Qt, NumPy, settings or printer state |
| `ToolheadRotorAxis.py` | Conservative coaxial cylinder consensus in placed CAD coordinates | Qt, GPU or printer state |
| `ToolheadRotors.py` | Bounded visual rotor configuration, body axes, phases and read-only speed selection | Printer commands, Qt or persistence |
| `ToolheadFanReadings.py` | Raw fan-field observations, snapshot/delta semantics and freshness projection | Printer commands, Qt or networking |
| `ToolheadRotorRender.py` | Fixed shutter poses seeded from the same static colour/depth; original sample foreground precedes AA resolve and premultiplied averaging | Telemetry, scene discovery or static-head redraw |
| `ToolheadOpacity.py` | Bounded asset-bound body/face opacity precedence, immutable colour projection and edit-only selection ghosts | Printer commands, Qt, material inference or source mesh mutation |
| `ToolheadMaterials.py` | Conservative annotation-based roughness, metalness and generated plastic-detail presets | RGB inference, alpha changes or printer state |
| `ToolheadAssetStore.py` | Atomic content-addressed triangle/colour/material/body assets with bounded checksum-verified reads | Settings adoption or CAD conversion |
| `ToolheadLighting.py` | Bounded model-local emitters, outward normals, colour/brightness validation and light-origin offset | Qt, settings writes or scene objects |
| `ToolheadImport.py` | Local bounded STL parsing and isolated STEP process ownership, source snapshot, stage progress, cancellation and mesh validation | Cura scene or configuration writes |
| `StepWorker.py` | Stdlib-only isolated native STEP process, flattened millimetre triangles and colours | Cura, Qt, NumPy or parent-process configuration |
| `CadRuntime.py` | Optional platform-pinned CAD and CPython 3.12 helper downloads, safe extraction and verification of every cached runtime file against retained pinned archives | Printer credentials, model upload or pip |
| `ToolheadModels.py` | One CAD worker and per-active-printer settings draft; retired results never adopt after Cancel or rebind | Live scene rendering |
| `ToolheadModelPreview.py` | Preview camera, immutable background mesh packing, nearest-surface picking and software-only capture fallback | Printer scene or settings commit |
| `ToolheadPreviewGL.py` | Qt Quick framebuffer renderer, current-context shader/buffers and depth-tested CAD lighting; camera-only updates reuse uploaded geometry | Cura OpenGL singleton, settings draft writes or imports |
| `ToolheadInstancedShadow.py` | Fingerprint-guarded instanced native shadow tubes over cached vertex storage with owned bounded indices and context-safe GL cleanup | Current/fractional paths, visible travel, native shader mutation or telemetry |
| `ToolheadPathGeometry.py` | Stable owned indices and per-shader VAOs over native vertex buffers, compact boundary indices and conservative spatial rejection | Native mesh mutation or telemetry |
| `ToolheadDepthCache.py` | Private depth framebuffer retention and incremental completed-prefix updates, rebuilt on backward progress or scene/camera changes | Native framebuffer storage or colour composition |
| `ToolheadFrameCache.py` | Private premultiplied image retention for the shaded head and additive illumination; certified optional four-sample crops, shared crop camera and context-generation invalidation | Native shader mutation, telemetry or settings |
| `ToolheadTransparency.py` | Blend native transparent surfaces ahead of the cropped head per original depth sample before AA resolve, preserving its premultiplied colour and alpha | G-code rasterization or native shader changes |
| `ToolheadOcclusion.py` | Seed custom-head crops from certified original path depth replay or explicit path absence and public solid/bed batches; retain provenance and retry failed acquisition | Native private FBO access, duplicate G-code rasterization or settings |
| `ToolheadSurfaceCache.py` | Private visible-surface framebuffer, incremental completed paths and moving-light shading with bounded storage | Native framebuffer mutation or telemetry |
| `ToolheadSimulationPass.py` | Render-pass adapter for eligible previews with a visible custom head; retained native indices, guarded shadow instancing, read-only stock shader identity and certified original-shader four-sample depth replay | Native class patches or scene mutation |
| `ToolheadSimulationCache.py` | Retained native path colour/depth with completed-prefix append and bounded storage | Fractional paths, telemetry or native framebuffer mutation |
| `ToolheadCamera.py` | Parallel orthographic or perspective view rays from delivered camera matrices | Saved preferences or scene mutation |
| `ToolheadSceneLighting.py` | Owned additive bed/path illumination using light-independent surfaces; legacy/compatibility forward fallback | Host shader mutation, settings writes or telemetry |
| `ToolheadOpaqueShader.py` | Shared toolhead shader variant without alpha discard for the fully opaque single-pass path | Shader formula duplication, settings or telemetry |
| `ToolheadGLState.py` | Independent host graphics-state restoration around owned optional effects | Native framebuffer mutation or scene recursion |
| `ToolheadEnvironment.py` | Paired colour/depth cubemaps, frozen six-face capture, atomic probe publication and optional failure backoff | Scene discovery or printer state |
| `ToolheadAsyncEnvironment.py` | Main-context capture admission, pair-lifetime VBO wrapper leases, adoption and consumer retirement | Worker-context drawing or printer commands |
| `ToolheadEnvironmentWorker.py` | Private shared-context capture with bounded GPU submissions, optional complete-query cohorts, source reservations through main collection and verified pair retirement | Native renderer singletons or GUI mutation |
| `ToolheadEnvironmentMailbox.py` | Epoch/generation tickets for producer, consumer and pair retirement | Qt or graphics calls under its lock |
| `ToolheadCaptureValues.py` | Immutable mesh and uniform values and validated buffer descriptors | Native graphics ownership |
| `ToolheadCaptureRecipe.py` | Main-thread scene freezing and worker-local reconstruction | Mutable native scene objects crossing threads |
| `ToolheadCaptureGL.py` | Context-local graphics entry points and shader programs | Native shared shader state |
| `ToolheadCaptureBuffers.py` | Worker-owned buffers, textures and framebuffer resources | Host buffer ownership |
| `ToolheadCaptureDraw.py` | Private draw adapters and retained borrowed-buffer reads | Main-thread resource mutation |
| `ToolheadShadowValues.py` | Frozen existing world-space light delivery, complete depth-cube storage admission and projected depth values | Allocating maps, publishing shadow availability or inventing direct lights |
| `ToolheadSampleTarget.py` | Explicit owned four-sample RGBA8/Depth32F attachments, certified sample order and original per-sample depth readback; detached Qt texture and R32F staging also budgeted | Stock framebuffer access, depth resolves or synthesizing samples |
| `ToolheadEnvironmentScene.py` | Owned shaders reading platform/grid/visible bed-height meshes and the visible public LayerData prefix | Native program mutation or recursive renderer calls |
| `ToolheadSceneNode.py` | Non-selectable lit triangle node; private head depth above final scene composition, cleared before Qt controls; opaque single pass and completed-image opacity | Telemetry or nozzle suppression |
| `ToolheadPresenter.py` | Reported-position freshness/homing gate, estimated public LayerData adapter and one native-nozzle suppression owner | Print progress matching or native class patches |
| `BedMeshSceneNode.py` | Mesh surface/boundary geometry and shader rendering | Scene composition |
| `MonitorData.py` | Monitor request lifetime, category timers, the frozen `MonitorSnapshot` and the observation record's assembly (the tri-state connection, the two push-ins) | QML declarations |
| `MoonrakerMonitorModel.py` | The single Qt Monitor model: explicit property declarations, collaborator composition and the one publication transaction | Domain policy or networking |
| `MonitorPublication.py` | The committed Monitor value map, the per-value QVariant conversion cache and the notification-group table with its emit order | Value construction, signal emission or domain policy |
| `PlateSceneIdentity.py` | Immutable, named navigation scene keys and split-compatible identity projections used to reject obsolete raster work | Qt, rendering, scheduling |
| `MoonrakerFollowerMachineAction.py` | Configuration QML properties, validation and the isolated probe transport | Live binding state |
| `MonitorCommands.py` | Monitor action acknowledgement and emergency-stop click sequence | Sliders or discovery |
| `MonitorTuning.py` | Debounce, pending tuning values, revision/confirmation timers | QML or printer discovery |
| `MonitorControls.py` | Macro, preset, fan/LED/PWM, setup and power/exclusion dispatch (the restart guards included), each through the permission policy's `_allowed` gate | Qt model inheritance |
| `ToolheadPolicy.py` | Pure jog/home/extrude G-code, the DISPATCH-time jog gate and the stepwise jog queue — every command executes as its own move, never merged or cancelled (the 2026-09-17 ruling; the click-time gate is `MonitorPermissions.can_jog`) | Qt, timers or networking |
| `MonitorPermissions.py` | Pure permission policy: the frozen observation record and the action rulings table (can_jog, can_power, can_restart, can_start_print, can_pause, can_resume, …) with disabled reasons; `is_paused` (the authoritative paused bit) and `pause_resume_supported` (the capability signal) ride the observation | Qt, networking or mutable state |
| `StateStore.py` | The plugin JSON documents' file-semantics owner: the read-modify-write merge, the pretty atomic replace (the injected save primitive — Cura's SaveFile in production), the fsync, the optional cross-process lock and the rate-limited failure reporting | Qt, networking or value coercion |
| `ToolheadController.py` | Shared Monitor/Preview motion queue, fresh dispatch queries, timeout/session/revision fencing and pause-first sequencing | Model inheritance or mesh/coordinate mathematics |
| `PauseAtLayerPresentation.py` | The pause-at-layer block the popover reads: its candidate at the follower's own layer, and the button gates re-derived with the card's own helpers | The schedule itself, the coordinator's rows or the live print state |
| `MonitorFormatting.py` | Pure ETA, mesh, macro and peripheral projections/parsers | Mutable state or I/O |
| `PreviewFormatting.py` | Pure status, icon, ETA and pause-item projections for the Preview panel | Mutable state or I/O |
| `CameraSourceIdentity.py` | Pure stable feed identity retaining arbitrary camera selectors and ignoring known rotating transport parameters | Camera transport or credentials in diagnostics |
| `CameraRecovery.py` | The webcam stream's freshness policy: the reload nonce every recovery path moves, the recovery veil and the stream URL's transition rules | Publishing a frame, the bridge transport or the camera's own configuration |
| `MonitorCamera.py` | Camera selection, transforms, per-printer selection and FPS persistence; chooses the bridged MJPEG stream or snapshot URL at 5 FPS and below when supported | Private configuration store |
| `CameraControlBar.qml` | Webcam zoom/FPS faces and their shared dock/park rhythm; zoom width is fixed by a measured `800%`, while the FPS face fits its longer rate readout | Camera decoding or persistent configuration |
| `CameraBridge.py` | The key-carrying camera republisher: an ephemeral loopback listener for configured stream and snapshot requests with the X-Api-Key header, same-origin redirects only, per-connection upstreams | MoonrakerMonitorModel |
| `MoonrakerMJPGImage.py` | Latest-frame MJPEG presentation and bounded snapshot polling; one decode worker uses QImageReader.read, releasing Python's execution lock during native decoding; UI-owned receive, installation and painting | QML camera item |
| `DetectionAssets.py` | Pinned model and CPU runtime matrix, with host preflight | Network transfers or Qt presentation |
| `AssetInstaller.py` | Consented, integrity-checked asset downloads (HTTPS at every redirect, fixed size and SHA-256 regardless of CDN host) and guarded runtime extraction | User-interface state |
| `LocalFailureModel.py` | CPU-only model session and QImage-to-RGB tensor conversion | Camera transport or policy |
| `DetectionRegions.py` | Pure bounded normalized polygon validation and stable mask identity. |
| `DetectionMask.py` | Union clipping and source-pixel exclusion before model resizing. |
| `DetectionObservation.py` | Immutable sampled frame, capture timestamp, result and bounding boxes. |
| `DetectionDownloadTransport.py` | HTTPS socket abort during headers, chunk framing and body receipt. |
| `PrintRunIdentity.py` | Attests active Moonraker history identity independently of local follower serials. |
| `MonitorDetection.py` | Owns detection tuning, per-run mute and pause guard, history resolution and bounded evidence submission. |
| `DetectionPolicy.py` | Freshness, context reset and sustained evidence transitions | Qt, native inference or printer commands |
| `LocalDetectionService.py` | Single background setup, benchmark and inference lane; global consent, readiness and persisted enable state. Disabling retires queued/in-flight results while retaining per-printer settings | Camera transport or printer commands |
| `EvidenceStore.py` | The alert's bounded evidence on disk: the triggering frame and a per-print score timeline, pruned by count | Qt, policy or printer commands |
| `TemperaturePresentation.py` | Per-printer chart configuration, history ownership and independently cached mini/full/latest/legend projections | Monitor publication, networking or toolhead state |
| `MonitorTemperatureHistory.py` | Pure per-sensor temperature ring buffers and the chart payload projection | Qt or networking |
| `ConsolePolicy.py` | Pure console policy: history bounds, the empty-input guard, the shared-lane pending cap | Qt or networking |
| `ConsoleController.py` | Console state owner: the bounded per-printer history and the untracked send lane | Model inheritance or formatting |
| `CuraOutputWriter.py` | Cura-affine preparation of a temporary G-code/UFP file | HTTP upload |
| `UploadController.py` | The Preview upload's write operation: discovery, readiness, multipart stream and cancellation | Cura application or QML |
| `FileBrowserPresentation.py` | File-browser drafts, confirmation state and row presentation | Monitor model, printer dispatch policy or network ownership |
| `FileFormatting.py` | Pure file-table units and local-time labels | Qt, network or a screen model |
| `FileManager.py` | File-manager state owner: the resident walk, history window, view state, selection and the LOCAL-file upload (its own multipart path) | MoonrakerMonitorModel |
| `ThumbnailCache.py` | The listing thumbnails' whole lifecycle: the bounded fetch queue, the in-flight reply registry with its identity check, the generation that invalidates both together, the temp tree the bodies land in and the cache the listing rekeys on rename | Rows, the walk or a listing rebuild |
| `ReplyBodyReader.py` | The hard byte cap on the replies the file chrome reads itself: one drain per readyRead, overflow aborts the transfer and drops the bytes, one disposal per reply | Transfer workflows or reply policy beyond the cap |
| `FileManagerPolicy.py` | Pure file-listing projections: directory rows, the filter/search/sort/page pipeline, history joins, recents, selection states, filter-option counts | Qt, networking or mutable state |
| `SectionLayoutPolicy.py` | The static pane→section table (Controls 14, Information 4, Status 6), including the Failure Detection controls after Print, and the section-layout normaliser — unknown ids drop, missing ids fill, hidden ids dedupe and sort | Qt, the store, any id vocabulary outside the table |
| `FileDownload.py` | One-shot file streaming from the printer into Cura (the file manager's Download verb) | FollowerRuntime |
| `MoonrakerOutputDevice.py` | Cura output-device signals/dialog/message adapter (the upload-with-start print gate included) | Upload state machine |
| `WhatsNew.py` | The what's-new content: the curated per-release entries and the once-per-version marker gate | Qt, I/O or networking |
| `WhatsNewOverlay.py` | The overlay's window owner: the boot-wait offer, the main-window/monitor lookup and the Popup's creation on Cura's own engine | Monitor state or networking |

| `ArcGeometry.py` | Geometry and interpolation of one logical straight or arc motion | Qt or tracking policy |
| `CacheNamespaces.py` | Per-machine cache rebinding and namespace lifetime | Index algorithms |
| `CachePolicy.py` | Shared print-folder eviction, explicit recency and conservative temporary-writer liveness | Qt or file decoding |
| `RawSourceCache.py` | Durable, atomic size-checked raw G-code beside index/prepared entries, with metadata-gated restoration | Qt or remote fetching |
| `CameraTiming.py` | Opt-in cold-camera timing diagnostics | Camera lifecycle |
| `FilesViewModel.py` | Stable-identity Qt file-list projection | Networking or file operations |
| `FollowerColourScheme.py` | Guarded Cura colour-mode, material and theme integration | Geometry or tracking |
| `FollowerRuntime.py` | Dependency construction and signal wiring at the composition root | Domain policy |
| `GpuFollower.py` | Retained follower geometry, bounded asynchronous preparation and scene-graph presentation | Motion matching or printer commands |
| `PlateAxisGeometry.py` | Border-aligned open axis-arrow segments for GPU and warm raster rendering | QML and printer commands |
| `GpuObjectPicker.py` | Retained object outlines using the shared GPU stroke engine | Exclusion commands |
| `GpuStrokeMaterial.py` | Shader materials, stroke vertex layout and GPU uniform updates | Print state or networking |
| `LeakProbe.py` | Opt-in memory-growth diagnostics | Production lifecycle policy |
| `MoonrakerOutputDevicePlugin.py` | Cura output-device registration and adapter construction | Upload policy |
| `MoonrakerPrintFollower.py` | Stable Cura extension facade and runtime ownership | Domain algorithms |
| `PlateProgress.py` | Prepared layer geometry, motion ranges and display payload construction | Qt scene graph or commands |
| `PlateRenderController.py` | The native renderer: per-surface render contexts, bounded demand scheduling, raster workers and their tickets, cache assets and decoded pins | Print state, presentation settings or Qt publication |
| `PlateQt.py` | Qt-facing layer assets and preparation adapters | Printer tracking policy |
| `PreparedSession.py` | The prepared store's session for the file being served: the adopted table, the incremental writer and its retirement, the coverage census, the completeness/published latches and the identity strength gate | The store's format, the RAM tiers or worker scheduling |
| `PreparedStore.py` | Validated random-access prepared-layer files and resumable preparation | Networking or UI |
| `PreviewColours.py` | Pure print-wide ranges and Cura-compatible gradient projection | Cura API access |
| `PrintCoordinator.py` | Cross-domain orchestration over injected services and immutable observations | Protocol or geometry algorithms |
| `PrintIdentity.py` | Pure current-print identity checks | I/O or mutable lifecycle |
| `PrintStartOwner.py` | Print-start acknowledgement and operation lifetime | HTTP transport |
| `RenderSurface.py` | One surface's render record: view/plot context, retained layer wrappers, demand, navigation slot and generation | Scheduling, workers or resource lifetime |
| `TravelStates.py` | Per-tool retraction balance and travel classification | Rendering or networking |
| `UiStateStore.py` | Persistent section-layout UI state through the shared store | Monitor domain state |
| `FeatureTracker.py` | Slicer feature, extrusion and travel state while scanning a layer | Files, caches or scheduling |
| `GCodeParser.py` | G-code token/marker parsing and bounded fast-path recognition | Modal state, files or Qt |
| `IndexAssembly.py` | Projection of completed scan records into a motion index, pause/marker maps and metric ranges | File reads or scheduling |
| `IndexCache.py` | Persistent index cache files, atomic writes and eviction | Parsing or Qt |
| `IndexCodec.py` | Cache format, bounds and validation of event/feature/arc columns | File lifetime or scheduling |
| `IndexHeader.py` | Slicer marker sniffing and filament metadata | Motion state or the hot scan loop |
| `IndexHydrator.py` | Hydration of one compact layer from its saved modal seed | Index lifecycle or worker scheduling |
| `IndexLimits.py` | Input and allocation bounds shared by the scanner and hydrator | Mutable state |
| `IndexTasks.py` | Immutable submission records and worker-local hydration/preparation execution | Service lifecycle, signal publication or coordinator references |
| `IndexView.py` | Read-only query capability over one index and spiral-Z projections | Index lifecycle or workers |
| `IndexWork.py` | Cooperative cancellation/yield vocabulary and wall-clock gates | Worker pools or Qt |
| `LayerCache.py` | Byte-bounded layer LRU and explicit payload memory charging | Worker scheduling or store lifetime |
| `MotionIndex.py` | Layer/motion query data and physical position matching | File reading or persistence |
| `MotionRefinement.py` | Pure motion matching over prepared geometry | Mutable physical state or presentation |
| `ObjectVisitTracker.py` | Per-print object-visit replay state and bounded incremental polygon walks | Qt, index lifecycle or worker scheduling |

| `FrameDecoder.py` | Native JPEG decode worker and orphan lifetime protection | Qt scene mutation, networking or decode cadence |
| `CameraStatistics.py` | Per-camera lifetime counters and interval/rate projections | Qt, clocks or network requests |
| `MJPEGParser.py` | Incremental multipart/JPEG framing, bounded buffering and corruption recovery | Networking, decoding or painting |

## 3. Binding and migration

`PrinterBinding.start()` performs the legacy migrations before configuring the
first live connection. Legacy follower preferences migrate once into a real
Cura machine; when the initial identity is `unknown`, migration is retried when
the stack appears. Standalone Moonraker Connection settings are imported for
their stored machines. Migration failures are logged independently and must
not prevent plugin startup.

The 4.5.0 one-shot migration then moves the configuration into the plugin's
own stores. It runs from Cura's `initializationFinished` — never from plugin
construction, because Cura re-reads the preference file after plugins load
and resurrects a construction-time clean — with the machine-stack-change path
as the deferred-until-identity case (a stale `cura/active_machine` emits no
startup signal at all). The control flow is strict: the v1 blob is read so
absent and corrupt are distinguishable; a whole-file backup of cura.cfg is
written to `cura.cfg.<timestamp>`, fsynced and verified by content; the new
files are written and verified by re-read; and the clean is LAST — the commit
point — gated on "the source was understood" (never "records extracted": an
empty-but-present blob is healthy). The clean is an in-memory
return-to-default (`setValue`), never file surgery and never
`removePreference` — Uranium's writer re-emits any in-memory value that
differs from its registered default. The outcome persists as a tri-state
record in the settings document, so no later run can erase a failure; the v1
import stays idempotent because a Cura backup restore can re-introduce the
blob. On machine removal, Cura's `containerRemoved` drives a credential wipe:
the record's URL, API key and host-identifying fields (camera, frontend,
upload paths) are cleared and the live session stopped, while the harmless
rest survives for a same-named re-add — filtered on the machine type and
registry absence, idempotent, deferred off the removal stack, and recorded
with a `removed_at` reason, because Cura itself can remove machines without
the user.

Switching Cura machines invalidates even when both profiles use the same endpoint.
Changing URL/API key on the same machine also stops the old session before settings
are persisted/rebound. Other preference edits do not cancel a valid upload.

`MoonrakerClient.sessionInvalidated` is synchronous on the UI thread and occurs
before the transport changes identity. The coordinator clears print/file/index/
Preview/pause state, and output/Monitor owners deactivate old work. Rebinding never
means that an existing upload may silently move to another printer.

The Monitor's data feed deactivates with the session and re-arms itself on the
next connection transition into connected — an automatic reconnect (a transport
handover, a socket recovery) must leave the discovery chain live exactly as the
manual reconnect does. A discovery watchdog holds the same line for the COLD
boot: three seconds after the connect, a chain that never armed (the objects
list empty or no wanted object's data arrived) is re-fired once, through the
same re-subscribe the Klippy-ready broadcast uses.

## 4. Shared networking and polling

`MoonrakerTransport.py` is the only production module constructing
`QNetworkAccessManager` — `CameraBridge.py` is the one sanctioned
exception: its upstream fetches are the camera republisher's own relay
lane, not a request path of the shared transport, and it keeps the
same same-origin redirect discipline so the key never travels to a
redirect target off the configured host. Ordinary JSON uses
`(owner, channel)` lanes with explicit replacement/cancellation.
Request IDs, categories, latency and errors are logged without
credentials. Streaming downloads and multipart uploads use the same
request builder/pool but own their replies directly — they are never
registered in the JSON lane registry, and `cancel_owner` stays
JSON-only. Each operation retires its reply on every terminal path.

`SessionSnapshot` copies incoming patches into a deeply immutable `FrozenStatus`.
Merges replace changed fields while sharing unchanged branches, including object
polygons. Owned consumers subscribe to `statusSnapshotReceived`; they retain the
same immutable data instead of copying and freezing the entire status on every
position update. The public `status` property and legacy `statusReceived` signal
still provide detached mutable copies. Legacy signal copies are constructed only
when that signal has subscribers. Session invalidation retires both publication
paths, including rebinds triggered synchronously by a subscriber.
Synchronous nested admissions are queued until the current frame's consumers
and command notifications finish. Connection republishing cannot replay an
older frame after a newer observation or an emergency-stop overlay.

The Machine Action may create an isolated instance of the same transport for
unsaved credentials; a probe must not reconfigure the live binding.

| Traffic | Policy |
| --- | --- |
| Core, printing | Configured interval |
| Core, imminent scheduled PAUSE | min(configured, 250 ms) |
| Core, paused | At least 1500 ms |
| Core, idle | At least 5000 ms |
| Monitor auxiliary, active/paused | 2500 ms |
| Monitor auxiliary, idle | 2500 ms |
| Power | 5000 ms |
| System | 10000 ms |
| Endstops (one-shot query_endstops) | 10000 ms |
| Discovery/static configuration | 30000 ms or explicit refresh |

`MonitorData` alone applies Monitor timer policy.

`PollPolicy` is the delivery policy in both modes: in HTTP mode a tick issues
the category's request; in websocket mode a tick drains that class's
accumulator (the socket is a source, not a clock), and the socket reconnects
on the same ladder. Every status write is admitted through one entry point
with an arrival stamp — a full re-sync applies whole or is dropped whole when
a newer write is already applied, and a fragment from a previous socket
generation is dropped. Socket liveness is active: a successful subscribe, an
admitted write, or a keepalive reply — socket state alone is not liveness.
A printer that cannot subscribe degrades the status feed to HTTP without a
session reset (the startup proof and the structured subscribe refusal are
the two triggers). An unchanged interval is not
written back to an active QTimer, because that would restart it and starve slower
polls. Full Klipper configuration is discovered separately; auxiliary polling asks
for the whole wanted objects — configfile narrowed to the volatile
SAVE_CONFIG fields — so `toolhead`'s accel ceiling rides the same lane
(4.2.0's Accel limit row).

Periodic core ticks skip overlapping requests. Forced refreshes coalesce into at
most one follow-up, but never bypass failure backoff. Queued refreshes and completion
handlers check generation, including after synchronous signal subscribers run.
A stale completion must not clear the new generation's coalescer slot.

Metrics cover ordinary JSON lanes, not all wire traffic: cancelled requests count
as started but not completed, and streaming/multipart bytes are outside those counters.

The file manager rides its own `file-manager` lane (never Monitor's `monitor`
lane): opening walks the directory tree breadth-first (bounded at 50
directories), the history window fetches once per open, and `Load all history`
pages until exhausted. A bind/deactivate bumps the service generation and
cancels the lane, so stale walk callbacks can never publish rows for the wrong
printer.

Thumbnail fetches (one-shot raw PNGs per visible row) follow the
METADATA's thumbnail `relative_path` (`.thumbs/<name>-<size>.png`,
largest entry) — the plain `<file>.png` sibling route 404s on a live
Moonraker; rows without metadata thumbnails record "none" and are
never fetched. The fetches own their replies directly under the
streaming rule above: each reply registers in the service, the signal
connects into a bound method, the handler validates its generation
before any read, and bind/unbind aborts and drains the registry. Bare
closures on network signals are banned — the live crash was a SIGSEGV in
`PyQtSlot::call` delivered from a QtNetwork signal right after the popup
opened (2026-09-10); `test_network_replies_connect_into_bound_handlers_not_bare_closures`
pins the pattern.

`CadRuntime.py` has a separate, narrowly scoped HTTPS downloader for optional
pinned PyPI CAD wheels and Astral's CPython 3.12.15 standalone helper release
20261003. The five supported platform pairs are macOS ARM64/x86-64, Linux glibc
ARM64/x86-64, and Windows x86-64. OCP always uses its cp312 ABI, independently
of Cura's Python version. The downloader carries no printer credentials,
rejects insecure redirects and verifies byte counts and SHA-256 before bounded
extraction. Cached runtime verification derives expected file hashes from
retained archives whose complete hashes match committed pins; a writable
installation marker never establishes trust. It does not create a second
Moonraker status feed. Downloads require explicit first-use consent and are
excluded from MPF release archives. Component licences, selected compatible
licence options and upstream source references are grouped in
`docs/THIRDPARTYSOFTWARE.md`, including Obico and the helper interpreter. Both
package formats include that document at the installed plugin root. Repackaging
the downloaded binaries would require meeting their additional redistribution obligations.

## 5. Physical state and Preview

The coordinator observes a print through `RemoteJobService`, then resolves its
physical layer through the one `LayerResolver`. Monitor reads `print_state`; it
does not run another resolver or advance Preview's extrusion state.

An observed pause preserves the last resolved layer. Parser lookahead and the
parked nozzle cannot reset its progress identity. After RESUME, an off-model
nozzle still holds that layer until its queued return reaches a resolvable
printing height. Resetting the resolver clears this print-local hold.

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

Selected-layer deadlines are formatted by the pure
`PreviewFormatting.layer_deadline_clock` projection. It uses one captured local
time for both the arrival clock and the calendar-day suffix (`+1`, `+2`, etc.),
matching the Monitor Finish convention without a second clock read at midnight.
`NextPausePipeline` uses the same projection for the Monitor Print Follower's
detached anchor ETA and scheduled pause deadlines.

The 5.3.0 Toolhead pane is attached in Preview by
`PreviewToolheadPresentation`. Typed intents route through the selected Monitor
owner; route epochs fence retired bindings. Jog/move-to require the shared jog
permission, home/calibration/motors require idle permission, and Z-offset has a
separate gate that permits printing. `ToolheadReadout` projects finite physical
`motion_report.live_position` readings, observed Z offset and firmware
capabilities from the Monitor's existing lanes. Missing or disconnected
readings remain em dashes. `MoonrakerOutputDevicePlugin` grants the readout
route to the selected machine only, revokes it before deactivation, and clears
readouts, target and distance drafts, motion focus and the open menu at a machine switch. The pane has
no transport, command owner or extra poller.

`PreviewToolheadHost.qml` anchors at the top left below Cura's actual stage
menu, reserving the lower-left perspective controls, job summary and object
selector through their QML context IDs. It
binds resizing and stage changes without reparenting on ordinary refreshes;
an unsupported host has no guessed placement. Collapse persists separately
from rendered-toolhead visibility. Its collapsed tab restores keyboard focus,
and a wrapping status strip stays outside the scroll area.
`PreviewJogDistance.qml` separates numeric drafts from the committed distance:
editing previews the interpolated slider position, while finishing the edit
commits a valid value. Owner reset/rebind discards unfinished edits and retires
keyboard focus to the non-motion header/tab, including disabled retained focus.
Values above the slider's 125 mm endpoint keep their
exact distance. `PreviewToolheadButton.qml` supplies compact two-line labels
and a theme-aware keyboard focus outline. The isolated
`tools/capture_preview_toolhead.py` scene uses real theme assets and synthetic
values; real-QML host tests prove placement, stage gating, ownership cleanup
and disabled controls without a printer. `ToolheadController` obtains a bounded
fresh status query before position-move dispatch through the existing command lane;
`PhysicalMotion` accepts calibrated G-code zero on every axis and rejects targets below zero.
Coordinate bases translate upper travel limits; firmware alone handles bed mesh. No inferred
bed-clearance floor may prevent reaching zero. The physical jog preflight checks
XY upper travel only; the fresh serialized dispatch query checks nominal Z travel
and enforces the inclusive G-code lower bound on each commanded axis. An untouched
axis already below zero does not block another axis returning to zero.
The guard supports Cartesian/CoreXY-family kinematics with reported XYZ limits;
it rejects unreported coordinate transforms and overridden movement commands.
The three-second status-query deadline, connection/session identity, command
revision and exact queue head must still match before dispatch. Each position
script saves firmware G-code state, restores it with `MOVE=0`, and never moves
back to the saved position. Offset commands bypass that query but serialize
through the same tracked command lane, including synchronous acknowledgements.
Z-offset nudges and resets are operator calibration controls: they bypass the
geometry guard and its status query, including homing and travel bounds.
They remain on the shared command lane with connection, ownership, control-lock
and input-validation checks, and remain available while printing. Firmware owns
whether a calibration command can execute; the client does not claim to know the
nozzle's physical clearance. Missing or unsupported transforms, malformed/stale
replies and changed ownership or command revision still fail closed for position
moves. The client cannot make its query/script atomic against
unrelated external G-code. Firmware owns its homing/probing paths.

The Preview object-name bar belongs to `PreviewPresentation`. Its banner anchors
use Cura's public `Camera.projectToViewport()` with the window's viewport rectangle
and device-pixel ratio. `ObjectNameProjection` places upright QML plates in screen
space and separates nearby labels while leaving leaders attached to their 3D
anchors. Crowded and edge positions keep their banners visible. The bar stores
banner visibility and hovered-only mode in global plugin state. Cura's
depth-tested `selection` pass selects the front-most scene model mesh under the
pointer. A G-code-only scene has no per-object toolpath IDs in that pass, so
hovered-only mode casts a camera ray into indexed object footprints and shows
every matching object; this is approximate because a footprint can include
empty space. Hovering fades every unselected banner. The banners use
Moonraker's object definitions only when the exact plugin-loaded file matches
the current print. The one-pass index records bounded per-object extrusion totals,
layer checkpoints, XY bounds and last-work offsets from `EXCLUDE_OBJECT_START/END`
or `;MESH:` markers. The version-15 cache persists these summaries and can supply
banner anchors when Moonraker object centres are missing. Unique case variants
of Moonraker and G-code names are joined without creating duplicate banners.
Matched objects show filament progress and, when a print-end ETA exists, a
projected object deadline scaled by slicer elapsed times. Unmatched objects
omit these estimates. The neighboring Preview card remembers its expanded
state and keeps its bottom edge aligned with the collapsible banner bar.

Toolhead-only motion requests cached native composition through a private pose;
it does not propagate native scene transformations. The head retains a cropped,
tile-sized colour/depth image and re-shades only when its pose, camera, model
or lights change. Global opacity fades this premultiplied image once at composition,
so coincident CAD faces do not accumulate alpha and opacity changes do not rerun
geometry. Opaque models use one shaded depth-tested draw at every global opacity;
models with intrinsically translucent materials draw all translucent colours first,
then write their owned depth before Cura's transparent bed overlay. This protects
completed head colours without an early transparent prepass hiding internal parts.

The View Options master lighting switch persists independently of the bed/model
choices. Disabling it bypasses scene illumination and displays the head's base
colours, including optional saved face paint, without perimeter lighting, attached
illumination or emission. The bed/model controls remain disabled until it is
enabled again. The model editor keeps its lighting available for configuration.

While showing a custom head, eligible normal-mode Preview rendering uses
an owned public simulation-pass adapter. Native shader resources and lower,
current and fractional draw order are preserved. Immutable indices stay in a GPU
buffer instead of serializing and uploading the entire print on each range draw.
Completed paths retain colour and depth; advancing progress appends only new
segments, with the fractional segment drawn into a separate working image.
Camera, filters, layer, minimum layer, shading mode or backwards progress rebuild
the cache. Compatibility mode and unsupported scene contents use the original
native pass; disabling the adapter restores that pass and requests a full frame.

The native simulation adapter may instance only the older-layer shadow range.
Admission requires the tested stock `layers3d_shadow.shader` content fingerprint,
desktop OpenGL 4.1, BACK/CCW culling, buffer-texture and instanced-draw entry points, native
contiguous float32 SOA attributes and nonnegative paired int32/uint32 indices.
Visible travel, unknown layouts/shaders and unsupported capabilities retain
the native geometry shader. The normal current and fractional ranges remain
on their existing shaders. Native shadow tubes retain helper/skin/infill and
extruder-opacity filters, including the native shadow prime-tower distinction.
The helper shares the public cached vertex buffer, owns at most 128 MiB of line
indices plus a 192-byte tube template, and restores program/VAO/array-buffer
and both buffer-texture bindings. A failed owned draw delegates the entire
frame to the original native pass. Admission logging remains opt-in through
the existing rendering diagnostic marker, once per context.

Scene illumination retains a full-viewport additive image independently of
native scene textures. Camera, filters, receiving mesh transforms, print prefix,
fractional motion and lighting changes invalidate it. Unrelated Qt composition
frames submit one image quad rather than re-extruding G-code. Stable index buffers
and per-shader VAOs reuse Cura's public vertex buffers. A compact boundary index
buffer lights older outer and hole walls; every selected top-layer category is
retained, subject to native visibility filters. Other older paths still contribute
to occlusion. Conservative light bounds reject distant blocks before vertex
submission, then the geometry shader rejects distant lines before tubular
extrusion. Static lower-layer depth is retained; completed top-layer depth appends
new segments while fractional segments remain transient. Backward scrubbing,
minimum-layer changes and camera/filter changes rebuild the appropriate depth.
On a camera rebuild, older non-receiving paths write depth first. Receiving paths
then write depth and surface attributes together with an inclusive depth test,
preserving receiver priority at coplanar boundaries while avoiding a second
extrusion of those paths.
When the owned simulation adapter has an equivalent completed depth image, a
guarded depth-only copy replaces the older non-receiver draw. Admission checks
the camera, transform, completed prefix, native visibility/shadow state, opaque
geometry and matching single-sample depth storage. Unsupported travel, support
or helper cases retain the partitioned draw. Only owned framebuffers participate;
the adapter never reads or changes native framebuffer storage. Profiling can log
the first successful admission in each graphics context.
Hosts unable to copy depth use the uncached depth path. Zero-opacity heads and
zero-energy lights submit no scene illumination.

Manual Preview changes are detected against remembered plugin-written values.
`CuraIntegration.writing_preview()` suppresses callbacks from plugin writes, while
user writes detach the follower. Selection tools also add/remove decorative
`ToolHandle` nodes: native SimulationView recomputes path limits and resets the
current path to its maximum synchronously. Integration identifies the exact
native scene-reset call chain, requires identical armed LayerData and unchanged
layer/minimum handles, restores the armed path through the public write guard,
and preserves attachment. Decorative ToolHandle root mutations also avoid scene
invalidation. Root topology signals
omit the changed child, so this host compatibility check uses bounded synchronous
frame inspection without retaining frames; unknown origins fail closed. Genuine
slider changes, including dragging to the maximum, still detach immediately.
Preview view reads and writes go through typed
`CuraAdapter` accessors rather than stringly-named view methods. Physical
observation and scheduled PAUSE continue while Preview is detached. ETA uses
slicer layer timing, speed, path progress and observed duration anchors—not
G-code byte percentage as time.

The index distinguishes continuously rising vase paths from flat layers.
`PrintState` uses the continuous-Z boundary for physical layer resolution,
and `GCodeIndexService` uses nozzle height to split spiral progress within
that layer. Flat layers continue to use ordinary path matching. The follower
can detach and scrub after indexing even while the print is waiting to reach
its first indexed layer.

Path smoothing is display-only: `PreviewMotion` animates the displayed path
toward the newest physical observation using the pure `PreviewSmoothing`
policy: the head cruises at the estimated physical velocity, never exceeds
the newest observation and never decreases during ordinary smoothing. The target itself
is reconstructed between consecutive observations by linear interpolation
over the measured poll interval, and the velocity window scales with that
interval, so the glide is continuous at any polling rate the poller actually
delivers (beyond ~5 s between polls the target saturates at the newest
observation until the next poll); the newest observation remains the hard
ceiling. A missing shared observation resets the driver so stale animation
cannot fight the next physical position. Layer transitions and an authoritative
backwards correction jump immediately, discarding the old ramp and velocity.
The physical `path_fraction` that ETA consumes comes from the shared boundary, and each
animated write re-remembers the plugin-written position so the override
detector cannot mistake the animation for a manual grab.

`GCodeIndexService.observe_motion()` owns live matching, independently of
prepared canvas availability. `PrintCoordinator` resolves the physical layer,
G-code-space toolhead position, attributed byte offset, pause state and extrusion
evidence once from a telemetry frame, calls the service once, and publishes its
immutable `MotionProgress` in `PrintSnapshot.motion_progress`. Monitor's live
payload uses that same split without matching again; Preview converts its
fraction to Cura's path units without another matcher or monotonic floor.
Both therefore share layer-entry suppression, pause/resume handling, ambiguity
holds and evidence-based backwards recovery. `IndexView.fraction()` remains a
stateless query, not a live tracking API. Manual Monitor scrubbing is separate
and cannot change the live floor; detaching either view does not stop tracking.
Hydrated motion arrays work before a renderer's geometry is ready, and a
compact layer may use the prepared geometry fallback while requesting its arrays.

Compact-layer refinement rejects segment motion ranges outside the search window
and edges whose bounding boxes cannot improve or tie the current nearest match.
Its spatial bound includes the existing candidate comparator's tolerance. Search
windows, travel acceptance and motion tie-breaking remain the same.

### Toolhead position and custom models (5.2.0)

The global View Options preferences select True position or Smooth path with either the native nozzle or a custom model. Smooth path enables smooth path progress in Preview and Monitor; True position resets the animation and uses discrete progress updates. The legacy per-printer path_smoothing field remains readable for compatibility but no longer controls production presentation. Hiding the custom toolhead restores the native nozzle, hides attached illumination and disables only custom appearance controls; position radios remain available and choices remain retained. Smooth path is disabled without a loaded toolpath. Named Toolhead, Object Name Banners and Bedmesh sections with dividers separate the position and appearance controls, independent object-name banners and bed-mesh display controls. View Options owns the mesh range filter, exaggeration scale and visibility button. Separate persisted Light bed/Light models switches govern attached illumination, and are disabled with the toolhead controls when the model is hidden.
Reported positioning observes the existing session feed independently of the
index and Preview attachment. `statusAdmitted` supplies admission provenance;
synthetic status re-emissions cannot refresh its age. XYZ homing and finite
`motion_report.live_position` are required. True position uses machine-space XYZ directly, with only Cura axis/bed-origin
conversion. G-code-origin corrections remain confined to path matching. Missing fields in a complete sync, a disconnect,
a rebind, or polling telemetry older than three effective core cadences (minimum
two seconds) hide the node with an explicit reason. A healthy changes-only
websocket retains stationary positions; socket keepalive owns its liveness.
Loaded-file provenance, path matching and Preview attachment never gate True
position; unrelated G-code cannot move or suppress the live indicator.

`ToolheadPresenter` suppresses the native nozzle through public parenting APIs
and `CuraIntegration.show_nozzle` respects its lease. The plugin uses an ordinary
SceneNode because SimulationPass rewrites native NozzleNode positions every
frame. Scene decoration restores public view activity and all four layer/path
handles under the Preview write guard after childrenChanged, so toggling the
indicator cannot be mistaken for a manual slider adjustment.
Estimated custom models use public LayerData interpolation and follow scrubbing.
Source mesh XYZ is Z-up millimetres; its anchor is subtracted before the single
Cura rotation (x,z,-y). Automatic alignment centres the XY bounds at minimum Z,
including an annular nozzle face; manual XYZ or surface picking overrides it.

STEP styles inherit through compound/solid/shell/face topology with instance placement, visibility and sRGB conversion. Four white sources on the top perimeter of the build volume aim 45° inward/down. View Options persists whole-model opacity as a global view preference; an independent scene-lighting uniform fades attached illumination with the same opacity without changing saved light intensities. Per-printer configuration persists up to eight attached emitters with model-local position, outward normal, RGB colour, brightness and range. Picking uses the visible surface normal and offsets the source outside the face; the shader excludes the inward hemisphere. Add/colour/brightness/remove edits share the settings Save/Cancel draft. An owned additive pass lights the native plate geometry and the visible G-code prefix using the installed SimulationView vertex/geometry sources and an owned lighting fragment. A depth prepass prevents bed illumination leaking through rendered paths. Its unchanged lower-layer depth is cached in an owned framebuffer and invalidated by camera, viewport, layer bounds, visibility filters or receiver changes; the current path prefix is drawn separately on every frame. Owned path indices are uploaded once, sharing Cura’s public cached vertex buffer. Conservative block bounds and a geometry-stage sphere check skip distant paths before tubular extrusion, and depth-only fragments skip lighting calculations. Toolhead-only position changes request cached composition without propagating scene-transform changes through Cura’s native G-code rendering. Zero opacity or disabling both receiving surfaces bypasses scene lighting entirely. Native receiver batches are retained across QtRenderer cached redraws, whose getBatches list is empty after endRendering; a full frame replaces them and scene/view/dimension changes invalidate them. Native shaders and classes are not patched. Brightness uses 0–100% UI values mapped to the existing 0–5 intensity; a renderer-only draft signal gives live updates without rebuilding the lights list. The custom head crop is seeded from the owned simulation pass’s complete visible depth, including fractional paths, plus public solid batches. Transparent bed and printer-frame geometry does not write that occlusion depth. Transparent model surfaces participate alongside the bed and frame; the head depth and coverage mask preserve opaque printed paths and prevent double blending outside the head. Geometry is not excluded merely because it belongs to a sliceable model. Its native shaders blend only foreground surfaces into a separate cropped image; a premultiplied resolve preserves the head’s coverage and opacity without double-drawing those surfaces outside it. Unchanged compositions reuse the result. Matching camera, viewport, source, path and depth-format checks prevent stale occluders; cache identity includes that depth revision. Warm/native fallback frames retain the previous overlay behaviour until compatible owned depth is available. Destination depth is cleared after composition so Qt controls stay above the head; line overlays do not occlude it. The transparency helper reuses native vertex buffers and never rerenders G-code. Native True-position nozzles still use the simpler native-mesh overlay without this custom-head depth seed.

Scene-light receivers use the native display material: older shadowed paths use
Cura’s grey and alpha, while current and unshadowed paths retain line colours.
The public simulation-mode capability invalidates retained materials on mode
changes; lighting never restores hidden line colours to greyed-out layers. An
unknown native mode remains a distinct cache identity but does not disable
model illumination. Selection handles retain the cached path pass and its
material observation; their small native overlay batch draws after paths.

Print-sized XYZ transforms in the depth-equivalence proof and light receiver
bounds use direct NumPy contraction (`einsum`, `optimize=False`). They do not
enter a BLAS worker pool: a large float32 transform stalled Cura's main thread
inside its bundled OpenBLAS `exec_blas` during a live large-print load. Proof
chunks remain bounded at 65,536 lines and unchanged geometry reuses the proof.

STL/STEP imports produce one immutable flattened mesh with per-triangle colours,
at most one million triangles and a 128 MiB source-file limit. Source deletion
after Save is harmless. Asset publication precedes the existing settings commit;
failed saves and cancelled drafts preserve the previously saved selection.
The MPFHEAD3 asset format also retains immutable material tables, per-triangle
material IDs, body-occurrence tables and separate per-triangle body IDs. Face IDs
continue to identify lighting and surface picks; names and geometry equality do
not identify bodies. Repeated or coincident STEP instances remain independent.
Physical material names/descriptions and meaningful component names retain their
provenance, without deriving material or transparency from RGB. STEP alpha remains
independent. Metadata is hash-covered, at most 1 MiB, with 4,096 entries per
table, 160-character plain names, exact array/table ranges and no duplicate JSON
keys or trailing bytes. Legacy MPFHEAD1/2 meshes retain their geometry and alpha
with unknown metadata; loading needs neither the original file nor the CAD runtime.
The worker resolves definitions with actual accumulated occurrence location
chains, applies each placement once, and carries assembly styles into descendants.
Absent native XCAF attributes are checked before invoking OCP output-handle APIs.
STEP external references are refused before native Transfer. A background worker
owns one disposable CPython 3.12 child process running `StepWorker.py`, with no
Cura, Qt or NumPy imports. It receives a private, bounded regular-file snapshot;
isolated Python mode (`-I -B`) and a sanitized environment prevent host Python
and loader settings from contaminating the helper. Conversion has no elapsed-time
cutoff. The parent terminates and waits for the child on cancellation or excessive
diagnostics/output, and validates the canonical mesh before adoption. The child
atomically replaces a small private progress file with stage names and actual
triangle counts during mesh construction; the parent reads at most 513 characters
at 4 Hz and publishes changed reports through the generation-guarded signal.
ToolheadModels owns a monotonic elapsed timer with a separate notification, so
ticks do not rebuild the model/light draft. Completion, reset and close stop it.
Opaque CAD stages show their names and elapsed time, not an invented percentage.
Native faults and calls holding the GIL stay in the child. Geometry/source limits
bound accepted data, not native allocations before conversion checks; the child
has no hard memory limit or filesystem sandbox. Demand-rendered QImages and
camera changes run off the UI thread; live rendering loads cached triangles
without CAD.

`tools/test_cad_runtime.py --coverage-worker` measures the isolated worker
against the actual pinned native libraries. A temporary stdlib `trace` bootstrap
records executed worker lines in the helper; the host coverage API checks its
statement coverage against the same 95% bar. No instrumentation is installed
into or written into the verified runtime. Mandatory CAD CI runs this check,
and the release verdict requires its success. Only `StepWorker.py` is exempt
from the ordinary host-process per-file report because its compiled helper ABI
and execution live in a separate process; it retains this dedicated threshold.

## 6. Remote files, leases and bounded indexing

The index implementation has three explicit boundaries. `GCodeIndex` owns the
one-pass modal scan; `IndexHeader`, `GCodeParser`, `FeatureTracker` and
`IndexAssembly` handle distinct input/projection stages. `MotionIndex` and
`IndexView` own queries, while `IndexCodec`, `IndexCache` and `IndexHydrator`
keep persistence and lazy hydration independent of the scanner.

`GCodeIndexService` remains the single scheduling/generation owner. Its worker
records in `IndexTasks` capture the index, lease, stores and cancellation events
at submission; a prepared reader cannot follow a later machine rebind. The
byte-bounded LRU, pure payload refinement and bounded object-visit replay live
in `LayerCache`, `MotionRefinement` and `ObjectVisitTracker`. No task receives a
service or coordinator object, and only the service publishes completions.


`RemoteFileService` binds metadata/cache identity to a job token. Same-filename
restarts invalidate old metadata and downloads. A streamed download is a
`DownloadOperation` (owned by `DownloadStream.py`): operation-local queue,
target, per-attempt byte counter and writer thread. The writer loop runs with
its operation bound — it never reads service fields, so a stale worker can
neither steal the next operation's sentinel nor write into its file — and it
exits via a Qt signal; the GUI thread never joins a writer or closes its file
(the writer closes its own fd). Buffering is bounded by high/low water marks:
above the high mark the drain stops reading, leaving bytes in the reply's
buffer (whose cap then throttles the socket). The pause flag and byte
counters share a lock: the writer wakes the drain exactly once when a paused
backlog falls below (not merely reaches) the low mark, including an exact
two-chunk threshold crossing. The response's Content-Length determines
progress and is checked against bytes written when present; the file listing
also checks the final size when known, and a separate 2 GiB cap limits bytes
regardless of either declaration. A transfer without Content-Length renders
indeterminate progress.

Metadata completeness is separate from download identity: a failed metadata
request installs a fallback identity so downloads proceed. File demand arms
one nonblocking backoff timer even after a temporary file is ready, independent
of the download's error latch. Verified metadata then retires that unverified
file and re-downloads before persistent publication; a deterministic download
refusal stays latched until `request_file(retry=True)` from the explicit Load
action; ordinary hydration demand (`request_file()`) cannot clear it. Only a successful
response marks the run's metadata complete.
The coordinator's Moonraker-metadata fallback carries the same discipline:
the payload latches only on a completed fetch keyed by `(filename, job)`
(request identity commits when the send starts, never before), a failed or
superseded fetch never serves the previous job's values, and a payload that
carries a `job_id` is cross-checked against the newest `server/history/list`
row over the HTTP lane before it latches. Content identity keys on
`(size, modified)` — Moonraker's `uuid` is a fresh random per extraction and
`filename` is an echo of the request, so neither is identity.
`RemoteFileService.request_metadata_only` is the service-side
identity-neutral fetch — the job lane's identity and cache stay untouched
on success AND failure (the 4.0.2 hazard). The coordinator adopts it
(4.3.0) through a bounded cross-check: the payload's job id must match
the newest history row, the give-up latches only the unattestable
causes, and a mismatched job id refuses without latching (the anchors
stay empty for that print). A job change clears the in-flight pending
flag — the lane never wedges across a print boundary.
Transient transport failures retry at most twice without further consumer
demand; size, encoding, cap and local write refusals do not automatically
re-download. A failed layer hydration is latched until a new file arrives
or the index is rebuilt, so a broken file is never re-read in full on every
poll. The latch is never silent: the payload carries the refusal, the
face names it instead of promising a load that is not coming, and an
explicit re-seek to that layer (a changed anchor, never a poll) clears
it and tries once more.

The file-manager's one-shot lane (`download_once`) runs on the same operation
machinery and captures the transport identity at request time. A mid-stream
printer/session switch cancels the stream — wired to the client's
session-invalidation signal — and a stale completion refuses to load; the
caller re-validates its captured machine/session identity at delivery. Cura's
`fileCompleted` is not a terminal signal: the load lease is preflighted against
the reachable refusals, and a bounded watchdog un-sticks `loading` when the
confirmation never arrives — the file is dropped, not deleted, and a late
completion is absorbed quietly.

A `FileLease` explicitly keeps that file alive for an index worker or Cura parse
job. Rebinding retires old files; deletion waits for all leases to close. An unrelated
Cura file completion cannot release the current remote file. The matching application
completion callback retains the lease even if the extension is deinitialized first.
The raw source is also persisted under the same per-machine, per-print folder as
its index and prepared layers. Restoration requires successful metadata with a
matching path, positive size and modified timestamp supplied by the remote
response itself (not a listing-size fallback), and a matching job size when
known. Filename, size and timestamp select the stable-key print folder; the
stored byte count is checked before use. Failed or incomplete metadata always
forces a fresh download. A separate temporary working file keeps active leases
safe through cache eviction or explicit clear: warm restoration runs off the UI
thread, hardlinking when possible and copying in bounded chunks with progress
when links are unavailable. A failed restore falls back to one fresh GET rather
than retrying the corrupt cache. Publication happens off the UI thread under a temporary name and an atomic
rename. Its worker remains alive through normal shutdown so a cross-volume
copy can finish before process exit. The unified per-machine LRU counts raw
bytes with index and prepared bytes. Live source pins prevent index/prepared
pruning from removing a print folder until its working file and leases retire;
stale pins are removed after a crash. Every eviction removes the entire print
folder, never just its raw source, index or prepared representation; a pinned
folder can temporarily exceed the budget even when the raw source alone exceeds
the limit. After its last active lease retires, pruning removes the entire
over-budget folder rather than stripping one representation.
The default 2048 MiB budget accommodates the verified 467,498,252-byte source
alongside its measured ~344 MiB prepared data and ~1 MiB index. Older unmarked
records containing 512 MiB upgrade to 2048 MiB:
the old serializer recorded the same 512 for inherited defaults and explicit
choices, so it cannot safely distinguish them. New explicit 512 MiB choices
carry a persisted marker and remain 512; all other saved bounds are preserved.

`GCodeIndexService` submits at most one worker job at a time. Requests coalesce into
desired state rather than an unbounded executor queue. Rebinding cancels the old
build and waits asynchronously for its completion before scheduling new work.
Persistent cache restoration, parsing, hydration and cache persistence all run off
the UI thread. Only generation-valid results are published on the Qt thread.

`IndexView` exposes immutable ranges/maps/timing and read-only query operations.
It does not expose mutable motion arrays or worker handles. Compact motion arrays
are used only after hydration has published them complete. Index algorithms and cache
format remain in `GCodeIndex.py`; they are not duplicated in runtime components.
Before raw arrays hydrate, prepared geometry can refine the follower's physical
split without a file lease. Live extrusion disambiguates travel crossings; a
cached index does not hide source-download percentage while the raw file is
still arriving.

One indexed motion is one G-code motion, and its physical geometry is one path:
a straight edge for G0/G1, and for G2/G3 the circular or helical path its centre
offsets describe. `ArcGeometry.py` owns that path — the tessellation the payload and
the printed-object walk read, and the live-position match — so no consumer branches
on the command word and no consumer re-derives a curve. `PlateProgress.motion_edges`
is the one expansion of a layer's indexed motions into physical edges: the payload
builders and the printed-object walk both read it, and a seek into the middle of a
layer yields the suffix of the very same walk (the travel state is the layer's
opening state with the boundaries before the seek applied in order). `refined_fraction`
does not go through that expansion: it searches the index's LOGICAL endpoints around
the parser position — the motion index is the unit the split, the payload and the
cache are all counted in — and hands every arc it meets to `ArcGeometry.closest`, so
the live toolhead is matched against the commanded curve rather than its chord. The
index carries the arcs sparsely (a descriptor per arc motion keyed by motion index,
plus the modal plane at each layer's start) and never pre-tessellates them. A
Klipper-invalid arc (R-form, G91, zero centre offsets) carries no descriptor and
draws as the straight edge it would have been, which keeps the index usable rather
than failing the file. The persistent cache is faithful or it is not written: an
index whose arc descriptors cannot fit the blob's entry budget is not published, and
a blob offering an over-budget arc column is refused rather than trusted, because a
cache that restores a commanded curve as a chord is geometry the file never had.

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

The camera receive pipeline is a composition, not a UI superclass stack.
`MJPEGParser` owns partial framing and snapshot accumulation; `FrameDecoder`
owns the native decode thread; `CameraStatistics` owns lifetime/interval
measurements. The `MoonrakerMJPGImage` Qt item retains requests, the single
decode deadline, stale-result rejection and image installation/painting. The
parser never reconnects and the decode worker never mutates a QML object.


There is one Qt Monitor model, directly derived from Cura's `PrinterOutputModel`.
Its declared Qt properties/slots retain the existing QML surface. `value_property`
is a declarative presentation binding—not dynamic attribute forwarding or a hidden
second model. Controller-produced UI values are copied into the model projection.

Monitor data is a deeply frozen snapshot. Controllers receive data/command/tuning
capabilities, not the model or follower. Tuning owns its revisions and debounce
lifetimes. Macro argument parsing is cached until static configuration changes.
Camera selection is persisted through the public configuration operation.

The native render path sits behind that model, not inside it.
`PlateRenderController` owns both surfaces' render records, their one-job-at-a-time
demand schedulers, the raster workers and the tickets that identify them, the
per-instance raster cache directory, the assets a live face still references and
the decoded payload pins the retained wrappers hold. The model keeps the Qt facade:
its properties and slots become delegates over the snapshots the controller returns,
and every renderer result enters the existing publication transaction rather than a
second stream of UI updates. The controller is handed narrow capabilities — an owner
for decoded pinning, callables for attach, the popover gate and the presentation
scene inputs, and the model's publication — and never reaches back into the model.

The Monitor's state lives in the plugin's own persistence (4.5.0): one
plugin-owned folder beside cura.cfg holding the settings document, the
state document and the per-machine shards.
The global chrome — the sections map, the section layout, the pane
collapses, the console height, the file-manager columns, the what's-new
marker and the toolhead jog selection — is one shared document; the
per-machine state (the console transcript, history and store-time) is one
small file per machine, so a console write touches only that printer's
record. The chart's visibility/colour block and the probe-point toggle stay
in the per-machine settings record, where the 4.3.0 migration placed them.
The stores pretty-print with sorted keys and write atomically through the
injected save primitive (Cura's SaveFile in production — fsync and flock);
the settings document and the global chrome take the cross-process lock,
while the per-machine shards have a single writer by construction.

Manual toolhead control is pure policy plus one queue owner: `ToolheadPolicy`
classifies the print state at DISPATCH
(disabled in unknown states, allowed while idle, paused or error — error
unlocks recovery moves) and retains each jog tap as its own move; the
CLICK-time gate is `MonitorPermissions.can_jog` (4.2.0), the same mapping
plus the fail-closed prelude. The motion controls are exposed only when
moves are immediately allowed — while printing the user must pause
explicitly first. `ToolheadController` owns the pending queue and the
pause-first safety net: anything queued while the state was allowed drops
if the print resumes mid-drain or the pause is not confirmed within ten
seconds. One-shot control scripts (Home, QGL, mesh calibration, Save,
Cooldown, macros, firmware/host restarts) queue behind the in-flight
command on the `monitor::control` lane so setup actions can be lined up in
succession — stateful commands never queue, and macros refuse while a
print is active — and jog scripts travel the same lane. Firing the
emergency stop clears the script queue, the jog queue and the busy gate
so power toggles and restart controls respond immediately. The
transport's replace lane is never used for motion because replacement
aborts an in-flight request whose G-code may or may not have executed.
`PhysicalMotion` validates and formats guarded jog/Move-to/centre/Z-zero moves
against one fresh firmware query per dispatch. `ToolheadPolicy` formats the
remaining homing/extrusion actions; firmware owns homing/probing paths.
Live Z-offset nudges and resets stay enabled during prints by design, without
client geometry or homing requirements. They retain the shared lane and
connection/ownership/control-lock/input gates.
The adjacent Apply control stages the current nonzero G-code Z offset for
the configured probe or mechanical Z endstop; it is disabled when the
reference cannot be identified unambiguously. It uses Klipper's
`Z_OFFSET_APPLY_PROBE` or `Z_OFFSET_APPLY_ENDSTOP`, never a
vendor-prefixed command or `SAVE_CONFIG` itself. Klipper exposes the
result as pending configuration; the existing Save configuration control
persists it after the print, restarting Klipper.

Two standing UI rules bound every Monitor control (both pinned in
`tests/test_monitor_qml_contracts.py`). **No reflow**: controls never disappear —
state gates disable, status lines are permanent single-line slots, and
reserved space uses opacity; nothing reflows unless the user acts
(expanding/collapsing, resizing). The reasoning is safety: a control
that vanishes mid-interaction moves under the pointer, which during
jog nudges is dangerous. **Disconnected state**: with the printer
disconnected every control disables (the emergency stop included);
the console keeps the transcript readable — scroll, select and copy
work in a greyed well, only input and Send/Clear disable; the camera
veils; and the connection dot plus the console's `#` notes mark the
transitions. The permission policy is one pure table:
`MonitorPermissions` rules every action over a frozen observation
record assembled once in `MonitorData` — the tri-state connection
(unknown/yes/no), the print state, homing, the controls lock, the
command lane's busy flag, `save_config_pending`, the e-stop
assumption `assumed_stopped` (the ONE case where the plugin must not
trust the last poll: the client rewrites the emitted state to
cancelled and the table sees the assumption itself), and — 4.3.0 —
the pause/resume pair: `is_paused` (the authoritative paused bit,
ridden on the CORE lane so it arrives with the state word) and
`pause_resume_supported` (the capability signal from the observed
object list — a printer without the module fails the rows closed
with R_UNSUPPORTED, never a fallthrough to the state word). Unknown
fails closed with a reason; the reason strings and the caption
sentences live in the policy, never in QML. The websocket carries
ZERO mutating RPCs — every mutation rides HTTP; the socket is an
observation feed in fact, not just by policy.

The Monitor's three panes and their accordion sections are presentation
owned by the model's published state: the expanded-section map, the pane
collapse flags and the lock toggle live in the model as VALUES, and the
FILE is owned by the `StateStore` (4.2.0) — a read-modify-write merge so
foreign keys survive (4.3.0's UI-state store consumes the same file),
with rate-limited failure notes through the console. QML binds to the
model through declared properties and setter slots. The temperature
history follows the same split: `MonitorTemperatureHistory` keeps the
bounded per-sensor ring buffers and chart projection pure, and the
model feeds them from the auxiliary poll — once per auxiliary reply,
never per publish. The chart config (sensor visibility, colours,
target/power toggles) persists per printer in the `PrinterConfig`
record because sensor names differ between machines; the plugin-owned
JSON state file keeps the chrome (expanded-section map, pane collapse,
controls lock, the console height, the what's-new marker, the file
manager's column config and the toolhead's jog/extrude selection), and
a legacy global chart block migrates into the per-printer record once
via the store's merge with a key delete — the deliberate
replace-write is gone (4.3.0), and the merge stays TOP-LEVEL only.
The print-start lifecycle's three homes were named here for the
4.3.0 owner extraction; they are now one owner
(`PrintStartOwner`): the POST stays in `FileManager.start_print`
and the upload path's POST-body verdict stays gated by the
device's `_print_verdict`.

**The section components (4.3.0).** The panes are thin shells:
every collapsible section is its own property-driven QML component
(thirteen in the controls dashboard, nine in the monitor), each
reading a `printerModel` property and never the host's ids. The
rule runs BOTH directions: a host never names a section's ids
either — it reaches a section through the instantiation id (the
refocus walk roots at `fansSection`/`ledsSection`/`pwmSection`;
`meshSection.refreshMap()` is the accessor the monitor's
typed-controls handler calls) or through a signal. The hosts keep
the panes, the dialogs, the pop-over and the emergency dock;
cross-surface requests cross the boundary as signals (pop-over
toggles, the power-off and exclude-object confirmations) or as a
single interaction sink — the sliders report their interaction
through `receiveSliderInteraction`, which snapshots the freeze
lists BEFORE the flag flips, re-arms a watchdog on every press (a
cancelled gesture can never latch the pane) and resets on printer
change. The capability gates ride the SECTION bodies (the section
hides or refuses while its data or permission is absent). Each
section's root is a `ColumnLayout` (`spacing: 0`) with the header on
`Layout.fillWidth` and the content carrying the gap as Layout
margins on its own gated visibility — probe-verified: a plain Column
root counts invisible children into its implicit height (a collapsed
section kept its hidden content in the pane's scroll length), and a
width binding on a layout-managed child breaks to zero. The
console remains a pane: its auto-collapse latch, camera-area
resize mapping and the host's printer-change resets are structural
entanglements, not section content.

**The theme singleton (4.4.0).** The plugin's colours live in one
singleton document (`mpf/resources/theme/MoonrakerTheme.qml`,
registered by the `qmldir` beside it and imported from a document
as `import "../resources/theme"`): the axis identity colours, the
pause orange, the console palette, the strip accents. No
document repeats a colour literal; `tests/test_theme.py` scans the
QML tree and fails any magic colour AND any cited token the
singleton does not declare (the two directions of the gate), and
the format/lint targets cover the theme directory like the rest of
`mpf/`.

**The Preview value-block seam (4.3.0).** The Monitor's data path
publishes one per-poll block — the strip's verdicts, the state
word, the temps pair and the aux-landing stamp — through
`MonitorData.previewBlockChanged` into the output-device edge and
`PrintCoordinator.receive_preview_block`, which deduplicates by the
block's own stamp (never re-stamped in transit). The staleness rule
reads the aux-landing stamp against `PREVIEW_BLOCK_STALE_S` AND the
client's connection truth — a dead feed publishes no events, so the
stamp alone can never age on the path the rule exists for. The
strip applies the rule in `MoonrakerPreviewCard`: never-arrived,
stale and not-following are three named states, never one
connection claim. The seam's ownership row: the strip owns its
cells and the refusal vocabulary (the policy's constants); the
coordinator owns the block, the staleness truth and the publishing
cadence; `MonitorData` owns the block's content and its landing
stamp.

The console sends on its own request path: `printer/gcode/script`
replies only after Klipper processes the script, and that reply's
result is the execution verdict — a client timeout is "no verdict",
because a blocking command may still be running. Klipper's output
streams back over HTTP from Moonraker's gcode store, polled only while
the console is expanded (an idle floor slows the cadence when no print
runs), so the pane is a terminal feed: typed lines as sent, output as
it arrives. `ConsolePolicy` owns the history bounds and input guards
(deliberately no command-safety table: the console is
unrestricted); `ConsoleController` owns the bounded per-printer
history, the send lane and the verdict pairing — each send closes over
its own entry, and no line claims an attribution the store cannot
support (it pairs by recency only). `CollapsibleSectionHeader` is
the single header implementation shared by every pane; pane chrome
(toggles, collapsed strips, plugin-drawn glyphs) is UI-only state in
the QML files and never mutates printer state directly. **The no-reflow
invariant (a ruling):** a control never disappears — every
state lives in `enabled`, never `visible`; state-dependent status lines
occupy permanent single-line slots whose text changes; the layout
reflows only for user-initiated actions (section collapse, resize).
The tests pin the banned patterns (`test_no_controls_disappear_controls_disable`). The recipes for
extending the panes are in `INSTRUCTIONS.md`, not here.

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
ownership before further I/O. Start-print power-on probes every configured
power device, never just the first. Cleanup clears reply ownership before
`abort()`, which may emit a completion synchronously; the same ordering and the
exactly-once terminal disposal rule apply to the file-manager's multipart
uploads, whose registry keys on operation identity, never the destination path.

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
- New controls: add policy to a focused controller and declare the Qt property/slot
  (toolhead control keeps script text pure in `ToolheadPolicy`; the click-time
  gate lives in `MonitorPermissions.can_jog` and the dispatch gate still reads
  `ToolheadPolicy.jog_gate`; the controller owns the queue and pause sequencing).
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
Monitor timing/tuning, the QML meta-object surface, upload terminal ordering, and
the download operation lifecycle — gated-writer retirement, per-attempt accounting,
stale-writer isolation and the no-GUI-join guarantee.

CI runs real-Qt regressions on Python 3.10–3.12 and native macOS/Windows
builds on Python 3.14. Cura provides Qt in production, so
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

### Software fallback: live plate camera gestures

The following raster, Canvas and prefix delivery contracts describe the
Diagnostics software fallback. The default GPU renderer retains geometry and
updates transforms, widths and progress through scene-graph state, as described
below; it does not request these raster or prefix producers.

The 4x warm raster is an entry-latched presentation buffer. Camera interaction
may defer new warm composites, but NEVER suppresses live split, layer, exact
native-checkpoint or render-result publications. After movement settles,
resume only the latest navigation demand. An exact scene rebuild is allowed
behind the warm picture throughout the gesture; the presentation controller
alone decides when a complete frame can replace it.

### Exact scene incarnation

The Monitor publishes a stable print/layer incarnation token alongside the
volatile printed-motion split. It changes when the print or surface layer
changes, not on every nozzle poll; consumers must distinguish the static
scene from within-layer progress. A raster or Canvas completion for a
previous incarnation must never claim ownership of the current scene.

### Exact-scene compositor

Canonical screenshot captures pin the amd64 container architecture as well
as the Mesa/EGL rasteriser for the toolhead showcase. `capture_toolhead.py`
loads checked-in, attributed Stealthburner/Cube geometry and the five-light
reference configuration, compiles the production head/scene-light GLSL and
captures fixed views. Cube contours are a documented partial-print illustration;
the receiver base palette is neutral. The normal capture and repeatability
entry points include both images; no live profile or CuraApplication is used.
Native smoke captures use host OpenGL (Mesa on headless Windows CI).
The opaque showcase bed writes its base-plane depth first. Its narrow grid
strips are a capture-only decal without depth testing/writes; paths and the
head then resume depth-tested rendering. This isolates grid sample coverage
from near-coplanar base depth without changing 4x MSAA or production Cura
rendering. Strict native and canonical comparisons remain required.

Canonical QML screenshot captures pin the amd64 container architecture as well
as Qt and fonts, and disable optional AVX/FMA raster paths for parity between
native CI and emulation on Apple Silicon. `tools/run_captures.sh` is the shared
entry point. Native test gates may use the host architecture; `make build`
regenerates canonical captures afterward rather than copying those test images.
Byte comparison and independent light/dark determinism checks remain strict.
The Preview Toolhead capture renders the real pane in four seeded states
(ready, collapsed, printing and disconnected), with no Monitor model or
transport. It participates in the same entry points and contrast checks;
the normal refresh also emits dark-suffixed illustrations for the README.
Its capture-only icon provider applies the production alpha-mask tint in
software; the generic offscreen stub's untinted black icons are not used in
these light/dark scenes. No production render path is altered.

`PlateExactComposition.js` is the single Qt-free owner of the asynchronous
Canvas delivery transaction, the attached/detached split acceptance rule, the
exact-picture readiness policy and the layer payload's own asset validity —
the raster, base and travel predicates those decisions are taken over. The QML
face now adapts this policy to actual Canvas/Image objects. Its software progress
surface has two bounded Canvas textures: a committed front and an offscreen
staging buffer. A staging upload can replace the front only when its receipt
matches the current demand; receipt, front texture and prefix ownership switch
together. Superseded uploads retain the committed image and coalesce a new
request. Each staging paint rebuilds only its eligible retained-prefix tail.
The native GPU renderer bypasses this software staging work.
Deferred presentation capture, background/progress paint and layer handoff
wakes belong to zero-delay, coalescing timers inside the face. Destroying its
Loader cancels those callbacks; no retained Qt.callLater method may evaluate
the retired QML context. Reopening creates independent delivery owners.

Two further Qt-free libraries sit beside it. `PlateViewPolicy.js` owns the
camera's arithmetic: the one printer-to-widget bed transform every consumer
resolves through (`PlateCanvas.plateToScene` included), the soft pan clamp, the
zoom's focal, eased and inverse terms, the scope's track-to-scale pair and its
graduations, and the physical stroke width the Canvas painters and the native
renderer's pen must agree on. `PlatePainter.js` owns the Canvas drawing: the
motion-edge rule and its two binary-search bounds, the batched per-class layer
walk, the travel families and the retraction glyphs. Each painter takes the
context, the payload, the boundaries and one explicit style record naming the
camera the pixels are baked at; `drawLayer` repeats the bed transform inline
because a call per vertex over hundreds of thousands of points was the
follower's dominant cost. The face keeps the camera STATE, the gestures that
write it, the canvases and the decision of which picture to draw.

`PlateToolheadDot.qml`, `PlateExtruderMarkers.qml` and `PlateZoomScope.qml` are
the face's visual leaves. Each takes what it draws as declared inputs. The
scope reports a requested scale through one signal and writes no camera state:
the view transform has exactly one writer. Printed ink is selected by one composition
decision, with retained prefix/full assets represented by immutable records
rather than visibility history or timed holds. The pixel-affecting world identity contains the print/layer
scene epoch and view. Native incremental prefixes and the 4x warm image remain
unchanged. Implicit Qt paints can be accepted only with consistent same-world
receipts; stale/mixed generations are rejected.

Delivered coverage is one immutable composition receipt; its QML coverage,
split, epoch and texture-readiness projections are read-only. Private paint
accumulators cannot certify presentation. A delivery notification without a
new bitmap preserves the standing receipt while any retry remains coalesced.
The receipt identifies the prefix URL whose interval the tail relies on,
as well as its boundary. Coalesced paints with different prefix assets are
ambiguous even when the boundary and split match. A partial printed picture
has one interval owner: a full-history Canvas, or a Ready prefix plus a
delivered tail for that same asset and boundary. A previously visible prefix
cannot overlap a full-history Canvas. A complete Canvas fallback can release
the warm-to-exact barrier while an optional prefix is still decoding.

### Presentation asset lifetimes

Every `PlateProgressFace` acquires an owner token from its model and replaces
its set of asset references atomically as Image sources and retained/held
sources change. The set includes assets loading behind the visible picture,
the retained prefix, the held full raster and the gesture's entry image.
Model switches and face destruction release the owner; released tokens cannot
be reused or resurrected. Independent faces may hold the same file.

Cache pruning protects the union of wrapper references, current navigation
assets and presentation references. Published navigation assets use this same
bounded retirement path instead of immediate unlinking on supersede. Released
files become eligible for the next normal prune; at most 64 unreferenced files
remain as retirement grace. Snapshot updates perform no directory scans,
image decoding or model publication in QML callbacks. Obsolete worker results
are removed on arrival, including completions for vanished surfaces, while
files still referenced by a presentation owner survive discard cleanup.

The presentation controller selects one printed composition: a complete native
class/travel pair, a delivered full-history Canvas, or a Ready immutable prefix
and its matching delivered Canvas tail. Prefix arrival alone cannot change
interval ownership. The previous full picture remains eligible during partial
entry until a complete replacement exists. No frame timer authorizes coverage.
Required base and ghost components have their own delivery transaction and join
the warm-to-exact barrier. Failed PNG decoding or publication invokes a complete
vector producer; ordinary full native scenes do not convert their geometry to
QVariant. The exact scene stays renderable beneath the opaque warm picture so
Qt can deliver its Canvas textures throughout camera gestures.

While attached, a navigation raster is reusable only when its scene matches and
its printed boundary does not exceed the live demand. A backward physical
correction retires future navigation ink and delivered Canvas/prefix compositions;
in-flight future work cannot promote. Detached scrubbing retains its previous
complete picture until replacement, preserving that interaction's continuity.

Camera decode uses QImageReader.read on its existing worker to release Python's
execution lock during native decoding. Periodic ticks and worker completions
share one dispatch deadline, with a single-shot wake for its remaining interval.
The mailbox still holds only the latest pending frame, the worker still permits
one in-flight decode, and stopping or replacing a stream cancels the extra wake.
The camera requests a framebuffer render target: Qt 6.0-6.8 ignores it, while
Qt 6.9+ can use accelerated OpenGL painting when the host supports it.

Partial-layer scrub geometry has a dedicated notification for each surface.
Each face retains it in a separate QML binding, so split advances and raster
delivery rebuild the small progress object without converting the full Python
geometry to JavaScript again. Geometry replacement still invalidates that binding.
An obsolete Canvas upload remains accounted for across a world change. Its
staging texture cannot replace the committed picture. The preparing composition
covers incompatible committed worlds while current work is queued. Staging
remains renderable outside the clip, avoiding a readiness cycle caused by hiding
the producer itself.

The split tracker separates search stalls from corrective physical evidence.
Only consecutive below-floor physical matches permit backward correction;
parser progress and missed matches cannot supply that evidence. At adjacent
layer entry, repeated XY geometry is withheld until distinct physical Z agrees
with the new layer. Equal-height geometry or missing height metadata remains
ambiguous and follows ordinary matching rather than claiming height evidence.

Real-engine pixel tests cover interval replacement, full/partial transitions,
failed assets, camera handover and scene changes. Dense native and publication
benchmarks retain the incremental rendering and 4x warm backing requirements.
Cross-platform native CI now builds and tests on macOS and Windows. Performance
inside a real Windows Cura session remains a separate validation requirement;
offscreen rendering does not certify it.

Backward seeks can restore the nearest earlier native prefix checkpoint from
the same immutable wrapper and render key, then extend only its remaining
interval. Each wrapper keeps at most four checkpoints and 16 MiB of cached
pixels; these bytes participate in the existing surface memory accounting.
View changes clear the checkpoints, scene changes replace the wrapper, and
checkpoint URLs join the asset reference set. A later checkpoint never seeds
a backward target. Once the normal current/ghost renders are hot, a lower
priority background worker writes independent prefixes at 5% intervals to PNG
files. This archive belongs only to the popover's current layer and is cleared
on layer, view, print and surface retirement. Checkpoints do not delay the
layer's normal render or publish QML updates as they are generated. Only one
checkpoint image is built at a time; the archive holds URLs rather than decoded
pixels. Reverse workers decode the nearest earlier file and extend at most 5%
of the layer, retaining the existing four-image/16 MiB decoded cache limit.
An unfinished archive or failed PNG falls back to ordinary native rendering.
Generation starts after a short idle delay, uses the pool's lower queue priority
and yields between geometry chunks. The worker's planned filenames are pinned
until completion so ordinary cache sweeps cannot delete an unfinished archive.

During split changes the last complete composition remains presented until its
replacement delivers, including backward scrubs. Intermediate prefix anchors
never present alone below the requested split. Static scene changes still
invalidate incompatible geometry. The standing grid rises above the preparation
cover while that geometry retires, keeping the grid visible through zero and
partial transitions. Both picker and follower draw red X-left and green Y-down
arrows along the top and right bed borders (15% of each edge). Their half-heads
remain inside the bed; the persisted, default-on "Axis arrows" checkbox
in Print Follower controls both surfaces without hiding the grid or print.
The Reset view label reserves its width while hidden so zoom never changes
the neighbouring checkbox Flow's available width or wraps its controls.
The grid paints them before object outlines or toolpaths
on Canvas, GPU and warm-raster paths, including the compact maps. Implicit Canvas
paints coalesce while an actual upload is
outstanding; a rejected delivery forces a fresh bitmap rather than a no-op retry.
While a native prefix worker or its Image decode is pending, QML keeps the
standing composition and coalesces progress instead of walking full history as
a temporary fallback. Completion wakes the painter; failed transport still
uses the complete vector recovery path.

Pause observations preserve the accepted split without collecting backward
correction evidence from the macro's parked head. Resuming keeps that floor
until geometry matches at or beyond it; parked telemetry after RESUME cannot
erase the layer's already printed history. Pause/resume parser-offset
rewinds keep the print identity; filename/size changes, an inactive boundary,
or a reset print duration still establish a new print.

Layer entry also considers `motion_report.live_extruder_velocity` when available.
A reported zero or negative velocity prevents an unconfirmed new layer from
accepting an apparent extrusion match during travel over excluded objects.
Positive extrusion releases the gate when the existing physical entry check
also succeeds. Missing velocity preserves the existing geometric policy;
ordinary travel after entry still advances progress. This does not assume that
the parsed file position identifies the currently executed move. Consistent
physical matches below an accepted floor retain the existing backward correction.

`GpuFollower` is the default OpenGL scene-graph renderer for both follower
faces. Worker threads prepare immutable, motion-sorted vertex buffers;
progress selects a prefix by binary search, while pan and zoom update one
retained transform shared with the themed 10/50 mm grid. Wide strokes use
constant-size quads with shader-expanded round caps and joins, independent of
driver line-width support. `GpuStrokeMaterial` owns the material and the packaged
Qt shader bundles; width and zoom change uniforms rather than rebuilding strokes.
The selected width is 1–8 logical pixels, independent of zoom. Previous-layer
ghosts retain class colours and solid strokes; next-layer ghosts use coloured,
translucent 0.5 mm dashes separated by 0.5 mm gaps. Flat dash ends preserve the gaps.
The opt-in antialiasing preference persists in the global follower view document
and selects analytic edge coverage in the fragment shader. It defaults to crisp
lines, without an offscreen multisampling pass. A layer change replaces the full
retained node tree, retiring Qt's old batches; ordinary updates retain that tree.
The toolhead dot stays above it. The persistent Keep centred preference follows
the attached toolhead when zoomed; manual panning switches it off, zooming does not.

Cold preparation stores immutable coordinate triples and polylines. Unlike nested
coordinate lists, CPython can remove these acyclic tuples from its cyclic-GC
traversal, avoiding long interpreter-wide pauses as the prepared cache grows.
Qt converts the tuples to the same QML arrays; this representation change alone
does not alter the binary coordinate layout. The current cache versions are
documented below. No process-wide GC policy is changed.
Prepared-file decoding uses immutable triples for GPU consumers too, with
1,024-point cancellation checkpoints. Software decoding retains the original
list shape, and software QML conversion is memoised per layer wrapper. GPU
publication does not convert the unused software vector payload.

GPU faces retire CPU raster, navigation and rewind-checkpoint demand. Shared
prepared geometry is limited to 48 MB, including conservative accounting for
source payloads retained by its identity keys. While GPU consumers exist, the
index service expands its decoded budget from 128 to 256 MB and speculatively
decodes existing local sources two to four layers beyond visible windows.
Foreground geometry and live motion-array debt outrank this prefetch. Decode
checkpoints yield the GIL and let newly selected layers interrupt speculation;
prefetch never requests a file download or hydrates motion arrays. Consumer
counts preserve the allowance until the last GPU face retires. Pinned active
layers retain the existing cache policy's explicit exception to the byte bound.
Geometry caching is per layer role and source payload, so a late ghost does not
rebuild the unchanged current layer. Source points are charged once per source;
synthetic dashed vertices retain their own byte charge. Loading an existing
prepared distant layer for GPU inspection does not also hydrate its raw motion
arrays. Followed-layer neighbours still hydrate for physical tracking, and
software selection keeps the original hydration policy.

Decoded geometry is presentation readiness, not physical-tracking readiness.
A speculatively decoded layer entering the live window must still request its
motion arrays, even though no geometry decode is needed. Otherwise compact
matching can select future geometry during a long excluded-object entry travel;
live traces captured motion 6,604 with coarse progress near 135, and motion
11,335 with coarse progress near 147. Extruder velocity below 1e-6 mm/s does
not confirm extrusion: the connected printer reported positive roundoff of
3.55e-15 mm/s during travel.

QML software fallback-vector reads and hidden Canvas paint demand are gated
off while the GPU renderer owns the face. The real-engine isolation regression
recorded 188 fallback reads before the gate and zero after it; selecting
Diagnostics software rendering restores those producers. During GPU buffer
preparation the last native frame may stand frozen until the new generation
lands. Its old progress remains frozen too: a new layer's split must never
paint the old buffers. Empty completions and explicit clears retire that frame;
retired worker generations cannot change it.

`GpuObjectPicker` shares the scene-graph stroke and grid primitives while
preserving the object picker palette, halo, hover widths, degraded centre
circles and pointer handling. It always uses four-sample antialiasing and adds
no controls. The Diagnostics preference `software_follower_renderer` applies
to both views; unsupported graphics backends also select the original software
renderers. Software selection resumes the original raster schedulers and
decoded-cache budget. The existing native/Canvas composition tests continue
covering the original software stroke contract, with additional coverage for
the new pixel-width control.

### Renderer measurements and limits

The retired `tools/spikes` prototype demonstrated scene-graph viability and is
not part of the maintained renderer. On Windows with Qt 6.6, a dense synthetic
layer measured median node updates of 0.193 ms (p95 0.275 ms) and frame swaps of
6.325 ms (p95 7.352 ms). Its four-sample antialiasing variant increased swap
median to 16.201 ms (p95 16.639 ms), motivating analytic follower antialiasing.
The production stroke shader, with a 42,000-edge synthetic layer over 200
width-change frames, measured median updates of 0.822 ms (p95 2.163 ms) and swaps
of 8.662 ms (p95 12.719 ms). These are isolated renderer measurements, not Cura
camera FPS or end-to-end interaction latency, and different runs are not a
controlled before/after comparison of the whole application.

Cold preparation of the first twelve layers in a captured large print improved
from 5.152 s to 3.954 s after immutable coordinate storage; the longest observed
generation-two collection fell from 311 ms to 26 ms and UI heartbeat delay from
314 ms to 31 ms. The source and captures are private investigation fixtures.
These measurements support avoiding cyclic-GC-heavy geometry and retaining GPU
buffers; no global GC tuning or offscreen follower composition is required.

The motion-line metadata shortcut reduced CPU time for a 20,971,515-byte scan
from a median 6.188 s to 5.547 s over three paired Python 3.10 runs, with matching
layer ranges, motion counts, types and travel boundaries. Encoding retains its
original loop after a proposed flattening shortcut measured slower. On eleven
captured prepared layers, three paired codec runs per layer measured median
decode time of 49.766 ms for lists and 46.895 ms for GPU tuples. Repeated
generation-two scans of those retained layers measured 121.858 ms versus
8.453 ms. These isolated Python 3.10 measurements identify overhead; they do
not predict Cura's Python 3.12 cold-index or distant-layer click latency.

Translucent previous, next and current-ghost strokes render opaquely into
separate viewport-sized GPU textures, then apply layer opacity once. Overlapping
round caps no longer compound the opacity of the stroke bodies. Anti-alias
coverage still blends only the one-pixel fringes. The printed prefix, travels,
glyphs and grid retain direct GPU rendering. GPU consumers share the main item's
immutable worker buffers through a typed QObject pointer; they submit no extra
preparation jobs and never activate software geometry. Progress-only changes
leave the translucent passes' settings and geometry unchanged. Each texture
pass uses its own item-sized shader viewport, preserving pixel widths under
zoom; the direct pass uses the window viewport. All passes carry the same
layer-generation frame hold. Software rendering keeps its existing compositor.


Attached GPU motion smoothing follows the global Smooth path mode.
True position disables that animation; the legacy `path_smoothing` value is
retained only for configuration compatibility.
The shared accepted motion record includes projection onto its single unfinished
motion; this never searches ahead or changes the accepted completed-motion floor.
A linear presentation animation trails observed progress, with immediate resets
for layer changes, backward corrections, detach and pause. The shader clips the
unfinished stroke using that animated motion fraction. Arc subdivisions carry
length-weighted fractional ranges within their motion, so they reveal sequentially.
The toolhead marker reads those same retained ranges by binary search and follows
the indexed path rather than interpolating XY telemetry chords across corners.
Missing geometry falls back to the reported toolhead position. This is a delayed
presentation of observed progress, not extrapolation of future printer movement.
Keep-centred animation changes only the displayed transform each frame; the model
receives its target view at telemetry cadence. Software rendering keeps its
existing discrete motion presentation.


A Windows Cura 5.13 live Preview-load capture (2026-09-27, 50 Hz,
50 seconds, py-spy GIL-owner sampling, 2,292 samples, zero sampling errors)
recorded 45.84 seconds of sampled GIL time: 44.00 seconds / 95.99% included
Cura's `GCodeReader`, versus 0.16 seconds / 0.35% including Print Follower.
The reader's dominant paths were `FlavorParser._createPolygon` and
`_calculateLineWidth`. Parsing on a JobQueue worker therefore still contends
with Python UI callbacks for the shared interpreter lock. Verbose diagnostics
were enabled; this capture does not support blaming them for the load stall.
A separate post-parse capture found substantial main-thread time under Cura's
SimulationPass / RenderBatch, including index-buffer conversion and upload.
Inclusive timings overlap and must not be added. These captures identify Cura's
reader and first 3D render as the dominant observed costs, not a measured
Windows-versus-macOS explanation. The plugin defers its native layer-height
cache requests and queued height batches until loading / slicing finishes;
Preview motion writes already suspend during loading.


### Extrusion widths and seam markers

The follower can estimate a separate bead width for each depositing motion.
The index retains signed E deltas as f32 values, filament diameter from slicer
metadata (1.75 mm when absent), and layer height from the first depositing XY
move in each layer. Modal E, units, G92 resets, compact hydration and persistent
index restoration use the same scanner state. Preparation estimates width as
filament volume divided by XY path length and layer height; arcs use their
subdivided path length rather than the endpoint chord. This is a rectangular
cross-section estimate, not a measurement of the printed bead. Missing or
implausible estimates use a 0.4 mm nominal width. Per-tool filament diameters are retained when metadata provides them.
Volumetric extrusion and live printer flow overrides are not inferred.

The packed GPU vertex is 40 bytes, including speed and tool ID. The signed end-corner X magnitude
carries millimetre width; its sign identifies the start/end corner. Pixel mode
uses that sign alone. True-thickness mode scales the magnitude through a
material uniform, so mode changes and zoom do not repack or upload geometry.
The saved 1–8 px override remains independent and an explicit width interaction
returns to it. Travels and the grid retain their existing widths. The software
fallback paints per-motion widths through its existing raster and Canvas paths.
The current index version is 15 and prepared-store version is 6; older
cache files are invalidated and rebuilt.

Retraction events are separate from travel boundaries: negative E or firmware
G10 retracts; a subsequent positive E or G11 unretracts. G92 never creates a
seam event. Independent persisted toggles show small hollow up/down arrows on
the current layer. Their screen footprint is 4 px at fit, bounded at 8 px when
zoomed, or 3 px in the mini pane. World-aligned screen cells retain at most one
of each kind per 12 px cell so dense seams do not obscure the full-bed view.
Neither marker size nor thinning changes with toolpath width.
Glyphs reveal only completed event prefixes, following the eased displayed
motion in GPU mode and withdrawing when scrubbing backwards. The glyph cache
uses event counts rather than fractional progress, so smoothing within one
motion does not rebuild marker geometry. Firmware events after the last indexed
motion are included at full playback.

### Shared Preview colour modes

`FollowerColourScheme` is the Cura boundary for the shared
`layerview/layer_view_type` preference, theme `layerview_*` colours and
active extruder material swatches. Either Preview or the follower dropdown
updates the same preference. Host model access starts after engine creation;
unavailable host colour interfaces retain the last successful palette (or the
default palette before the first successful read) without
preventing plugin registration. Theme and material signals refresh the snapshot.

Index cache version 15 records modal feedrate (mm/s) and tool ID per motion,
layer-start seeds and per-tool filament diameters. The compact indexing pass
aggregates whole-print speed, deposited layer-height, bead-width and flow
limits without retaining every layer's motion arrays. Prepared cache version 6
stores the profiles in binary columns beside geometry. Distant-layer hydration
reproduces the original tool and feedrate state; a tool switch is not a motion.

The GPU stroke vertex carries speed and tool ID alongside physical width.
Cura's Speed, Layer thickness, Line thickness and Flow gradient equations run
in the vertex shader. Switching mode, palette or range changes uniforms and
retains the geometry buffers. Material colour selects the motion's tool swatch.
Previous and next layers use the selected colours with reduced opacity; the
current layer ghost remains translucent grey. The software renderer uses the
same projections, with colour state included in asynchronous scene identity.
The legend shows material swatches, line-type keys or print-wide gradient bounds
with units. QML-facing profile arrays and range bounds are QVariant lists,
including freshly prepared data, so first-use legends cannot read `NaN`.

A successful Cura palette snapshot is persisted as a small preference. If the
host colour API is unavailable on a later launch, the last successfully read
Cura palette remains available rather than reverting to unrelated colours.
In True thickness mode travels have a fixed 1 logical-pixel stroke, independent
of zoom and the saved explicit extrusion width, on GPU and software paths.

The travel channel is split into Cura's Non retracted, Retracting, Retracted
and Priming categories. Each uses its corresponding host Preview theme colour
in every colour mode. The single Travels checkbox controls all four, and the
printed motion boundary clips them identically. Stationary retract/prime moves
remain glyphs rather than zero-length lines. Positive E that exceeds the
remaining retraction is deposition, not a priming-only travel. Per-tool filament
balances and firmware G10/G11 events cross layer boundaries and survive compact
index hydration; prepared travel classes have a binary TRCL extension. The
legacy combined travel channel remains available to existing consumers.

### RC review hardening

WebSocket data-message limits apply to the declared frame length before buffering
a body, including final unfragmented frames and accumulated continuations. The
initial upgraded connection has the same no-reply deadline as an established
connection. Camera bridge requests must remain within the configured HTTP origin
before credentials are attached. Thumbnail bodies are bounded to 16 MiB and
upload acknowledgements to 1 MiB, including responses without Content-Length.

Live motion remains physical telemetry. Paired `gcode_move.position` and
`gcode_move.gcode_position` supply only the XYZ origin offset (including G92);
the queued endpoint never replaces `motion_report.live_position`.

Prepared-cache recency advances only after validation. Dead index temporary
files are swept, while a folder containing an active writer or indeterminate process-liveness result
is protected from eviction; the cache may temporarily exceed its budget while
that writer is alive. Worker preparation failures publish a visible error and
a diagnostic instead of silently leaving a layer pending.

Native and Linux harnesses use `tests/harness/log_gate.py` to reject plugin
warnings, errors and QML binding/polish loops. Native first-install and migration
legs include both boots' logs. Missing evidence fails the gate. Deliberate fault-injection scenarios may declare exact expected log messages; only a fully passing scenario records that allowance, and the shared gate consumes its bounded message count. Repeated authentication-refusal warnings are permitted only inside the successful authentication-fault scenario's recorded time window. Other warnings still fail.

### Object-outline retention

The Exclude Object Picker retains the grid and each object's native outline
separately. Hover/current-state width changes rebuild only affected outlines;
colour changes update materials, and pan changes the parent transform. Object
removal releases its nodes, and geometry/scale changes invalidate the relevant
outline. The same capsule triangles, round caps, widths and palette are used.

A Windows CPU microbenchmark during RC review measured 49 rectangular objects
at roughly 7.4 ms to expand the complete bed, versus 1.1 ms for a retained hover
update. A synthetic stress case (256 objects with 64 edges each, not the user's
rectangular print) measured roughly 837 ms versus 9.3 ms. These are local CPU
measurements, not end-to-end FPS guarantees. A separate prefix-copy probe measured
about 0.15 ms for 10,000 segments and 2.15 ms for 100,000; it does not measure
Qt allocation or driver upload. Chunking that path remains a profiling-led
follow-up, not a demonstrated correctness defect or an RC architecture change.

### Settings page composition

`MoonrakerFollowerConfiguration.qml` owns navigation, the migration banner and
the save/cancel transaction. Its Connection, Following, Upload, Detection and Diagnostics
components own their editable fields and validation. Each publishes only its
configuration values and receives the settings manager explicitly; changing tabs
does not recreate a page or discard its draft. The shell merges the five value
blocks only when saving and preserves all public validation properties. The
Detection precedes Diagnostics (always the last tab). All five pages have a
themed scrollbar that remains visible whenever their content can scroll, with
space reserved beside the content. Detection settings owns shared consent-gated
model/runtime setup and prerequisites. `FailureDetectionSection.qml` owns the
active printer's enable checkbox, primary sensitivity slider (0.80–1.20×),
explicit Advanced lower/upper bounds, safe period, independent notification and
automatic-pause opt-ins, mute, acknowledgement and re-arm. Values remain visible
when disabled. All value-changing controls require a configured, enabled camera
and connected monitor; the secondary controls also require that printer's
analysis opt-in. Camera loss or disabling the feed suspends analysis and edits,
retires outstanding results and owned alerts, and requires fresh analysis on
return. Advanced and region editing are explicit user modes; neither changes
the shared collapse/lock contracts.

`MonitorDetection.py` owns policy, immutable observations, run attestation,
camera-local settings, alerts, evidence and automatic-pause guards. The Qt
Monitor facade forwards explicit properties/intents and composes that owner.
The 0.00–1.00 signal maps adaptive boundaries to the chosen Advanced bounds;
it is not a calibrated probability. Sensitivity scales the adaptive gap, with
1.00 preserving the original defaults. The long baseline belongs to the printer,
stable upstream camera identity, canonical region fingerprint and preprocessing
version. It survives stream/snapshot switching and ephemeral loopback-bridge
ports. A semantic source change, camera change, tuning change, re-arm, or
suspend/re-enable retires analysis through a monotonic epoch. Geometry is
validated once per settings revision rather than on every decoded frame.
First use and training resets enter a neutral six-frame Learning baseline
period; no score or alert is produced during it. The Webcam pane resets the
selected camera's training, while Detection settings resets training for every
camera on the selected printer. Both confirmed actions preserve regions and
tuning. Baselines persist across Cura restarts and prints; different print
footprints can call for manual retraining.

The selected camera's decoded frames feed a single bounded CPU worker at no
more than one inference every ten seconds. Acquisition age, context and ordering
are checked on delivery. The policy retains Obico's span-12 EWM, streaming short/
long means and relative escalation, with a configurable safe start. MPF adds
a 90-second acknowledgement cooldown. Shared weights/policy do not establish identical preprocessing or
accuracy: Qt resizing differs from upstream OpenCV and region masking changes
the input distribution. Model proposals are displayed as optional suspicious
boxes, clipped to monitored regions with their analysis age.

`DetectionOverlay.qml` shares the actual image's rotation, mirrors, zoom and
pan. The editor reserves a dock below the fitted image. New regions start as
rectangles; solid handles drag vertices, midpoint handles insert vertices,
and selected vertices/shapes can be deleted. Undo is bounded; invalid crossed
or degenerate shapes keep the last valid draft. Up to four polygons with
32 vertices each form a monitored union. Save applies that camera's complete
draft; Cancel/Escape discards it. Empty regions mean full frame. Source pixels
outside the union are blacked out and the smallest enclosing bounds are cropped
before resize. Model boxes are mapped back to full-frame coordinates, and outside-only proposals
are excluded before score aggregation. Insufficient mask resolution is reported
for that sample/camera without destroying the shared model session.
Disabling detection for a printer closes an active edit and hides its saved
region outlines on the webcam; the camera's saved geometry remains available
when detection is enabled again.

Durable action state uses Moonraker history's active job ID/start time, filename
and printer binding. Active history is re-attested every ten seconds even when
the local follower job serial stays unchanged. New automatic pause requires
proof no older than two seconds from request issuance; failed or negative
rechecks revoke that proof. A changed server run retires evidence and action
state. Mute suppresses notifications and automatic pauses while analysis and
boxes continue; it restores only for the same attested run and clears for the
next print. A possible saved mute is conservative while history is unavailable.

The pending pause guard is saved before dispatch. Every command has a unique
ID, and callbacks revalidate dispatch ownership both at entry and after
synchronous tracker signals. Confirmed pauses latch the run; pending/unknown
outcomes remain guarded across restarts. A refused command may retry on fresh
failure evidence; explicit re-arm clears the guard only after a successful save,
without issuing a command. Acknowledgement and mute are transactional too.
Owned Cura messages have a bounded lifetime and token, and are hidden on
retirement. Warning-to-failure escalation is immediate; repeats are bounded.

Evidence retains the exact analysed frame and timeline through a bounded
background I/O queue. State document writes and per-machine shard creation are
thread-safe. Evidence cleanup never follows links outside its own root.
Downloads abort live sockets during headers, proxy CONNECT and body framing.
Native inference uses cooperative ORT termination and a watchdog; shutdown
retires the Python mailbox promptly but cannot hard-abort native import/session
initialisation. Disabled startup avoids loading a session; setup releases its
benchmark session when disabled. Locked runtime deletion on Windows records a
durable cleanup intent and retries before the next import, with cleanup errors
visible and evidence removal independent of the loaded runtime.
Diagnostics
can rearm the global What's New and detection-offer markers for the next
launch without touching installed assets or printer settings. A separate
Diagnostics action removes the shared model/runtime and disables detection
and both automation opt-ins on every saved printer.

### File-browser presentation

`FileBrowserPresentation` owns popup drafts, confirmation state, upload progress,
and file-table projections. It receives a read-only snapshot capability and a
print-dispatch callback; it cannot reach the Monitor facade. The latter keeps
its established QML slots and the shared printer permission/start watchdog.
`FileFormatting` owns table units and labels without a Monitor dependency.
Thumbnail notifications remain separate from row publication; view changes stay
synchronous and the closed popup still avoids rebuilding its expensive rows.

### File-browser modal components

The browser shell opens and closes seven explicit dialogs; each dialog owns its
fields, keyboard handling and presentation helpers. They receive the file-model
capability explicitly, never the browser root. Print permission is a live boolean
input shared with the grid's action gate. Popup geometry remains anchored by the
shell, and stable object names and cancellation callbacks are unchanged.

### Console pane composition

`ConsolePane.qml` owns transcript rendering, selection/scroll preservation,
command drafts and history, collapse and resize interaction. It receives only
the printer-model capability and a stable resize coordinate frame. The Monitor
screen lays it out beside the camera and does not access the transcript's ids.
A printer change resets the transcript inside its owner; initial creation is
reconciled once after its children exist. The resize still measures against the
stationary containing frame, not the handle that moves during the drag.

### Toolhead materials, reflections and visual rotors (5.3.0)

PreviewPresentation persists previewReflectionsEnabled with the global display
preferences, publishes it to View Options, and uses sceneLightingRequested to
refresh the Presenter. ToolheadSceneNode gates capture and queued wakeups with
this flag; disabling closes the environment owner and invalidates the frame
cache without rebuilding mesh buffers. Re-enable creates a fresh capture owner.
Lighting off retains the separate reflection choice.
Disabling lighting retires its retained frame/surface owners and resets its
optional failure state; re-enabling allocates a fresh lighting owner lazily.
Reflection disable separately retires its fixed-size maps. Other simulation
owners may still require shared path buffers, and freeing owned allocations
does not require the driver's process footprint to shrink immediately.

MPFHEAD3 retains per-triangle material/body occurrence IDs and bounded immutable
annotation tables; MPFHEAD1/2 remain readable. STEP physical material labels and
clear part names choose conservative finishes. Unknown remains nonmetallic;
colour is never a metal detector. Intrinsic CAD alpha is retained. Exact-position
normal welding uses a conservative 192 MiB working budget (512 bytes per
triangle); larger meshes retain cached read-only flat normals without welding. Plastic detail
uses irregular gradient noise to perturb model-local normals and fades each
noise domain at its own pixel footprint, including anisotropic fibre grain; one saved strength is
live-previewed transactionally. Glass and metal do not gain generated grain.

The normal Preview renderer supplies a frozen visible source/prefix and cloned
public grid/platform/layer shaders to a 512-pixel colour/depth cube capture. Visible
BedMeshSceneNode surfaces retain their actual height and vertex colours. Plate
colours blend before their paired depth is written, including negative mesh heights.
A turn submits at most 64 preparation/draw/copy operations with a 2 ms CPU
budget; a single GPU draw or shader compilation is indivisible. Each path draw
has at most 32,768 indices, retaining owned VBOs and bounded EBO copies.
The owned vertex upload snapshots attribute metadata and supplies previous line
types before any draw. It shares immutable native arrays and the public colour
byte API; it never creates or borrows a native LayerData cached VBO. This prevents
Cura's late previous-type attribute from invalidating host storage. One weakly
retained upload per source/share group serves simulation, lighting and reflection.
Bounds reduction visits at most 65,536 vertices or 32,768 indexed path elements
per operation. Immutable full chunks are cached; only the current first/last
partial-prefix bounds are retained. Conservative transformed tube bounds reject
chunks outside one homogeneous cube-face clip plane; every 32 rejected chunks
provides a cooperative checkpoint. Native and attached-light passes use the same
admission, without decimation or changed pixel tolerances.
Snapshot-local visibility decisions retain their exact box/camera identities.
The additive pass also rejects padded chunks beyond every active light sphere;
the native colour/depth pass keeps the original admission. Both rejection tests
share one 32-candidate cooperative budget. Contiguous path draws share shader,
VAO and buffer bindings only within one capture turn; frozen uniforms upload
once, while element offsets and additive depth state remain per-command.
Every plate/face/shader transition and turn exit independently releases these
bindings before the host graphics guard restores its state. Procedure pointers
are weakly cached per native context generation and evicted on destruction.
Idle deadlines precede GL state handling. Declared CPU-only bounds preparation
also avoids that guard while retaining the same operation/time budget; snapshot
creation, drawing and resource creation remain guarded. Ordinary forward path
bounds are lazy; native/deferred draws do not scan them. Native int32 index arrays
can use an identical unsigned view without a whole-print conversion allocation.
Six complete colour and depth faces publish with their origin, near/far and
bounds atomically. Same-context slice, visibility, filter and source changes keep
the last complete map visible while its replacement builds; abandoned partial
faces never publish. Disable, context retirement and capture failure remove it.
On supported main-thread OpenGL contexts, an owned shared-context worker advances
capture without waiting for foreground render frames. It receives frozen arrays,
typed uniforms and shader recipes, and borrows validated existing path VBO names
while the main context retains their wrappers. Shaders, VAOs, chunk index buffers
and face targets belong to the worker. At most two submitted GPU turns remain
outstanding. The original synchronous owner handles unsupported capabilities and
the first missing native upload.

An epoch/generation mailbox owns the two physical map pairs. Producer poll tickets
and consumer read tickets prevent reuse during a pending fence poll or draw.
Hard source changes reject obsolete pending jobs; soft changes retain only the
latest pending request. Adoption preserves the complete colour/depth/descriptor
pair and a monotonic facade revision, including synchronous fallback publication.
Disable seals demand immediately; the worker drains producer and last-consumer
fences without requiring another ordinary frame. Fence failures request an
explicit cleanup frame in the original main context. Only that context's verified
completion may release retained read tickets. Context destruction withdraws
bindings and finishes its outstanding consumer work before retirement. The direct
Qt destruction signal may outlive its Python wrapper; a cached native pointer is
used only inside that signal, with a retired epoch rejecting later address reuse.
Late shader-finally callbacks do not invoke the retired context. If exact-main
activation or completion fails, the producer exits without returning unresolved
read tickets/input leases or deleting shared resources. One process-lifetime
quarantine retains that group's owner and blocks another async owner; the
synchronous backend remains available in a usable replacement context.

Optional mesh channels use native presence flags: an empty UV array on an
untextured 3MF platform is absent. Empty parsing snapshots do not request path
lighting shaders until path geometry exists. Native proofs cover empty-to-loaded
and loaded-to-empty transitions with the same worker owner.
Changed pose/path/light state coalesces with a 150 ms minimum interval after
publication; unchanged directional scenes sleep until their captured scene key changes. Legacy central probes retain their periodic refresh. Earlier wake
deadlines supersede old idle timers, and obsolete callbacks cannot request a
frame. Empty LayerData during active slice/layer production defers replacement
and retains the complete map; a stable empty bed remains capturable. The adapter
reads only the backend slicing/layer-job flags and public Preview busy state.
The opt-in rendering marker logs synchronous capture wall/submission time and
turn counts at most once per ten seconds, and completed worker capture wall time
and worker turns on adoption. Neither report claims whole-window frame latency.
After a successful stock draw, the simulation adapter can observe the identities
of the stock layer, shadow and current shaders to initialize stationary previews.
This narrow read-only host-layout dependency never changes native state; unknown
layouts retain transition-based observation. Unsupported compatibility mode
keeps ordinary shading. The map excludes the head. The live directional path
captures six exterior orthographic colour/depth views over scene bounds. The
shader visits every crossed depth texel, testing both opposite views together
in three shared axis walks. It retains the nearest admitted surface and uses
the colour mip chain for roughness, with independent face-edge clamping.
No sparse ray steps or repeated roughness depth searches are used. The legacy
central perspective path remains available to older fixtures. Finite-resolution
exterior depth views remain an approximation for arbitrary occluded geometry.
Captured-depth traversal publishes hit confidence independently of sampled RGB.
A central traversal miss leaves ordinary toolhead lighting unchanged; a valid
black captured surface retains the original reflection equation.
Directional capture cameras enclose the prepared scene bounds, so their CPU
chunk iteration omits redundant frustum classification while retaining exact
prefix ranges, raster clipping, light-range rejection and bounded checkpoints.
Perspective fixtures retain the original frustum test. Scene signatures take
one child snapshot and one layer-data lookup per child, retaining the same
visibility, material and geometry invalidation inputs.
Private capture programs cache uniforms by their exact delivered typed values;
rebinding does not force identical uploads. Relinking and bulk-array writes
invalidate the cache, including individually addressed array elements.
Idle workers wait for notifications rather than polling. A wake revision guards
the scan-to-sleep race, and scene-generation changes explicitly wake retirement
even when no successor job exists. GPU fence waits and shutdown retain their
bounded polling; no resource retirement depends on a later render frame.
Worker path captures retain one complete immutable index buffer per borrowed
vertex-buffer identity. Every bounded draw still validates its original element
range and publishes its original element start, using a byte offset into that
buffer. Later faces, passes and captures reuse the same upload; failed uploads
must succeed before any draw. Existing capture storage receipts include the
complete index buffer and temporary upload copies. A 200,000-path comparison
kept all six colour/depth faces byte-identical while eliminating 84 repeated
index uploads (9.6 MB) per warmed capture.
The default draw uses this directional map directly at the current head pose.
The unused geometry-query, receiver-layer and shadow-map prototypes are excluded
from the release; their source remains recoverable at commit `80c4b263`.
Optional injected worker factories retain their tested cohort lifetime hooks:
producer and last-consumer fences drain before cleanup and main source
acknowledgement. No such factory is installed by the ordinary renderer.
The current CaptureScene receipt survives main acknowledgement until cache
replacement, including cancelled captures. Receipts charge frozen array backing
allocations, borrowed VBOs, private uploads, derived receiver normals, textures
and upload copies. Disabled or ineligible bed-light receivers retire their
negative-identity buffers and CPU normals before replacement publication.

Attached-light receiver passes add colour with frozen emitter settings after
native geometry; alpha and the paired native depth remain unchanged. Their
light toggles and source settings participate in refresh invalidation.
Independent framebuffer,
VAO, texture, sampler, depth/blend and raster state is restored on failures too.
Owned shader release also unwinds interrupted Uranium batches.
Optional capture failures back off for five seconds. Platform image dimensions
and bytes are checked before decoding. Raw cube textures retire through their
own share group, with deferred render-thread deletion when no owning context is
current; context-group destruction releases the remaining names. Depth sampling
uses nearest filtering without comparison; owned sampler bindings clear and
restore host sampler objects. Refresh wake tokens belong to each capture owner,
so retired timers cannot stall a replacement. Switching to
the native nozzle releases capture, shutter and occlusion resources.

Rotor selection addresses whole body occurrences. Analytic coaxial cylinders
provide an axis; other bounds only propose an axis that the user confirms.
Centre/axis/direction and visual RPM are saved per printer. Rotor triangles are
packed separately once; only those meshes animate, while static head colour and
depth remain cached. Up to three shutter poses each copy the same static depth;
premultiplied samples are averaged before the existing whole-head opacity fade.
Opaque geometry writes depth first; translucent body groups composite back to
front for each pose, preserving glass over rotating parts. Both accumulation and
the final image choose additive blend equations independently of host state.
New shutter storage is capped at 128 MiB; allocation faults retain sharp rotation
and show a fallback status. Models exceeding 64 translucent bodies refuse fan
animation while keeping ordinary rendering available.
Conservative all-angle bounds prevent rotor clipping. A visible moving rotor
requests composition at 30 Hz; hidden/stopped rotors stop that timer.
Stopped poses reuse a completed shutter image keyed by camera, scene/depth,
materials, rotor geometry/configuration, phase and blur. Publication happens
after successful graphics-state restoration; faults cannot cache partial work.

The selected Monitor routes raw read-only fan observations. Full samples replace
old RPM fields; deltas retain unchanged values. Finite nonnegative measured RPM,
including zero, wins; otherwise normalized power scales a configured visual RPM
and is labelled estimated. Disconnected, missing and stale polling observations
stop bound animation. A healthy change-only socket retains unchanged values;
its session invalidation clears them. No animation owner can send fan or motion
commands. Printer controls are never exercised during development.


Toolhead appearance follows the delivered Cura camera projection, including
perspective/orthographic switches and retained-image crops. Perspective eye
vectors vary per fragment; orthographic vectors use the active camera world
back axis. Projection/view bytes invalidate cached shading. Bounded value-based
camera caches also fence dtype and in-place matrix changes; cached vectors are
copied before delivery. The editor uses
parallel orthographic rays, and translucent rotating bodies sort by view depth
in that mode. Cura's saved preference is not a rendering authority.

Opacity overrides are separate sparse body and face maps (2,048 entries each),
bound to the immutable asset. Face opacity overrides body opacity, then STEP
alpha; original RGB and metadata remain untouched. Effective colours determine
opaque/translucent batches, picks and the translucent-body animation limit.
Selection mode temporarily tints selected geometry cyan and ghosts invisible
geometry; those colours never reach normal rendering or persistence. Whole-body
edits clear descendant face opacity before applying explicitly selected faces.
Oversized edits fail atomically. Draft Cancel, asset replacement and printer
rebinding clear the editor selection and retain existing adoption fences.

Toolhead selected-part colour overrides are independent RGB-only sparse body/face maps. Face RGB overrides take precedence over body RGB; imported face resets bypass the body and use immutable source RGB. The shared opacity/colour resolver preserves alpha. The editor owns a lazy RGB dialog fenced by printer, asset, generation and selection; rejection or editor closure cancels pending colour edits. Both maps participate in preview cache keys and stationary presenter redraws.


`ToolheadShadowValues.py` freezes existing world-space light values used by
ordinary head lighting. Its pure depth/storage admission helpers remain tested,
but shadow-map storage, exchange and shader prototypes are excluded from this
release. Independent shadows remain deferred; source for the earlier experiments
is preserved at commit `80c4b263`.

Ray tracing and its Metal bridge, worker, tiled importer and packaged binaries
have been removed. Preview retains Enable reflections, which directly controls
the directional environment map. Obsolete ray/fallback preferences are ignored.
Renderer radio buttons, the fallback checkbox and the backend status label are
absent. FrameCache retains ordinary cropped rendering, coherent camera/depth
handoff, foreground composition, rotor animation and context retirement.
Generic explicit sample targets retain ordinary sample/foreground validation;
measured sample-pattern validation no longer imports a native ray bridge.
