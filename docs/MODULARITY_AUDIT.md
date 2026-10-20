# Modularity audit — 2026-10-20

Audited revision: `df228e51967bb0cdf9bd686c9e0c70c0ec2692dc` on
`release/v6.0.0`. This is an assessment of the current implementation, not a
claim that the changes below have been implemented or that the product is bug-free.

## Scope and method

The repository-wide structural inventory covered all 222 production Python
modules (64,949 lines), 124 QML documents, five JavaScript files, the standalone
library entry point, and 46 Python build/development tools. Python imports were
resolved with an AST scan including imports inside functions. Class and function
ownership, cross-domain dependencies, private imports, substantial identical
function bodies, QML imports, packaging manifests and architecture tests were
examined. Behaviour was traced through the principal owners in every runtime
domain, with deeper inspection of the coupling described below. This was not a
line-by-line correctness or performance certification of every statement.

No cycles were found in the resolved production Python module import graph,
including lazy imports. That does not establish that runtime callbacks have no
cycles. Existing architecture and library tests passed: 39 tests in four isolated
processes. Their passing result does not invalidate the findings below: several
tests explicitly permit the current coupling.

## Work needed to satisfy the broader modularity request

These are recommended boundaries, ordered approximately by their effect on
behavioural consistency and independent reuse. They are not all functional
release blockers, and they should not become one large simultaneous rewrite.

### 1. Make the recovery itinerary a single domain value

Evidence: `mpf/plate/RecoveryControls.qml:47` independently calculates capped
heights, priming positions, travel ordering, speeds, dwell and durations.
`mpf/printing/RecoveryHandover.py:137` separately validates and generates the
executable return. `RecoveryController.state` supplies scalar inputs to QML,
which reconstructs the itinerary. The diagrams consume QML's actions rather
than the executable plan.

Required boundary: a validated recovery plan containing ordered typed actions,
positions, feeds, dwell and restart boundary. Both command serialization and the
confirmation/diagram projection should consume those actions. QML should format
and display the plan. Fresh-status replanning must remain authoritative; a changed
itinerary must not silently execute under an obsolete confirmation.

Proof: compare displayed actions with serialized commands for paused/unloaded
recovery, prime on/off, high/low prime, travel caps, coordinate offsets and all
return-order branches. Current QML tests exercise expected sequences with a
synthetic plan, independently of the Python command producer.

### 2. Separate raster painting from Qt presentation and worker ownership

Evidence: `mpf/plate/PlateQt.py:86` contains cooperative scheduling, PNG file
publication, QRunnable work, QObject bridges, QML layer wrappers and checkpoint
storage; painting functions occupy the latter part of the module, including
`render_layer_prefix` at line 743. `mpf/plate/qt.py:9` eagerly imports both the
scene-graph renderer and these mixed facilities.

Required boundary: a raster module taking geometry/view values and returning
images; a separate QML asset adapter; separate worker/publication facilities.
Expose independently importable raster and scene-graph entry points. Keep one
authoritative source implementation in plugin and wheel.

`PlateRenderController` also owns asset references/file pruning, navigation
scheduling and prefix/checkpoint scheduling. Extract an asset-lifetime owner and
explicit render-job values while retaining one controller responsible for epoch
and generation admission. Do not duplicate that admission across new workers.

Proof: installed raster-only consumer with scene-graph imports unavailable;
existing pixel parity; cancellation, stale completion and retained-asset tests.

### 3. Separate index content from consumer retention and tracking policy

Evidence: `mpf/gcode/MotionIndex.py:206` stores `followed_layer` and
`manual_anchor` in `LayerMotionIndex`. `GCodeIndexService.set_followed_layer`
at line 794 writes them and manages a live/manual retention window. The same
service owns build/hydration scheduling, packed/decoded caching, GPU preparation,
motion refinement and object visitation, and imports `printing.PrintState`.
`IndexCache.py:29` depends on Moonraker's remote-file identity; it also imports
Uranium logging when available.

Required boundary: parsed index content and read-only queries; an explicit
hydration/residency session; consumer demands supplied as retention requirements;
and an application-facing tracking/presentation adapter. Cache persistence should
accept a neutral source identity/key and diagnostics capability, with Moonraker
identity translation performed by its caller.

Do not remove the lock or resident/hydrated state without replacing their
concurrency contract. The existing immutable `GCodeDocument` API is useful and
does not expose these fields through its normal entry points.

Proof: standalone compact hydration/cache consumer without Cura or Moonraker;
two independent consumer retention demands; eviction and stale-job regression
tests with identical results and memory bounds.

### 4. Finish extracting recovery and follower state from the Monitor facade

Evidence: `MoonrakerMonitorModel._pause_for_runout` at line 824 performs a fresh
query, validates sensor ownership/job identity and dispatches PAUSE. The facade
also owns attach/detach/scrub state at line 3028 onwards. `_compose` at line 1344
binds runout history and assembles recovery selection and rendering state as well
as merging presenters. `PrintCoordinator.refresh_recovery_files` at line 1161
implements a separate file-list request, filtering, sorting and selection flow.

Required boundary: a runout-pause workflow owner; a follower selection/presentation
owner; and a recovery-source selection workflow using explicit file-service
capabilities. Keep printer/job fences within those owners. The Monitor should
publish values and route intents; the coordinator should connect workflows.

Proof: test these workflows without constructing the Monitor's entire Qt property
surface, then retain real-QML enablement, selection and recovery integration tests.

### 5. Move settings validation out of the Cura MachineAction

Evidence: `MoonrakerFollowerMachineAction.saveConfig` at line 816 contains about
250 lines of conversion, ranges, cross-field rules, discovery checks, configuration
assembly, persistence and host refresh. `PrinterConfig.from_dict` at line 292
separately normalizes the same families of settings, including recovery ranges.

Required boundary: pure configuration/draft validation with structured field
errors, shared constraints and explicit discovery/context inputs. Distinguish
strict interactive validation from tolerant persisted-data loading; those need
different failure behaviour and must not be collapsed into silent normalization.
The Cura adapter should translate QVariant, call validation, apply the result and
refresh host UI. Feature-specific validation can be composed behind the existing
persisted schema without breaking saved settings.

Proof: equivalent validation outside Cura, per-field errors, missing/disconnected
discovery inputs, persistence refusal, and existing migration compatibility.

### 6. Decompose the follower QML by lifecycle, not by arbitrary line count

Evidence: `mpf/plate/PlateProgressFace.qml` has 2,958 lines. It owns motion
interpolation, layer handoff, asset-reference lifetime, exact-raster/Canvas
transactions, GPU/fallback selection, navigation gestures and the penguin overlay.
`PlateExactComposition.js` and `PlateViewPolicy.js` already extract useful policy,
but the surrounding owners remain interwoven.

Required boundary: explicit view/gesture state, presentation delivery state and
render surfaces, with one declared handoff protocol between them. Keep resource
release and Canvas receipt ownership explicit. The easter-egg overlay can be a
separate visual component. Merely moving functions into JavaScript that reads a
shared `root` object would preserve the coupling.

Proof: independent component tests plus the existing real-engine frame delivery,
gesture, retained-image and stale-receipt tests. Preserve the established no-reflow
and render-resource lifetime rules.

### 7. Separate toolhead import jobs from appearance-edit transactions

Evidence: `mpf/toolhead/ToolheadModels.py` combines model import/download consent,
thread creation/cancellation at line 773, active-printer fencing, asset publication,
appearance editing and QML properties. `beginEdit` at line 562 snapshots a long
positional tuple; `endEdit` restores it; reset/import/default paths each reset the
appearance families.

Required boundary: a model-import job/service with explicit immutable results;
an appearance draft/checkpoint value for materials, colours, opacity, lights,
rotors and tip; and a Qt presenter coordinating them. Preserve the current
transactional adoption point and printer identity checks. The geometry, material,
rotor and mesh-format primitives already have useful independent ownership.

Proof: draft accept/cancel/reset and all appearance fields, cancellation and
printer switches during import, unchanged asset publication behaviour.

### 8. Separate detection action policy from its Monitor/UI bridge

Evidence: `mpf/monitor/MonitorDetection.py` owns run-history attestation, camera
context, observation admission, persistence, region editing, alert suppression,
pause-attempt state and Uranium notifications. `_handle_detection_action` at
line 531 makes durable pause and alert decisions; `_notify_detection` at line 493
constructs the host message. `DetectionPolicy` already separates inference-score
policy, but not this second action state machine.

Required boundary: explicit run-attestation/action state with decisions emitted
as effects, plus persistence/command/notification adapters and the QML presenter.
Do not merge runout pause, failure-detection pause and scheduled pause into one
generic policy: their evidence and acknowledgement rules differ.

Proof: acknowledged/repeated/escalated alerts, fresh/expired attestation, uncertain
pause outcomes, failed durable writes and printer/job changes without Uranium UI.

### 9. Consolidate shared transport/storage primitives and protocol knowledge

Evidence: `RemoteFileService.py:93` and `gcode/CachePolicy.py:59` contain identical
Windows process-liveness implementations. The transfer module explicitly says
the duplication exists to satisfy the allowed-import rule. FileManager's upload
at line 458 and `UploadController._upload` at line 261 independently construct
multipart requests and own streaming replies. `MonitorFormatting.py:71` owns
Klipper object-family classification used for discovery and command policy as
well as labels and presentation. RecoveryPlanning imports private parser tokens
and parsing helpers at lines 26–27 and embeds setup-pane navigation in refusal
messages.

Required boundary: one conservative process/filesystem primitive; one streaming
multipart operation with cancellation/progress/result contracts; shared printer
object classification in a protocol/domain module; explicit lexical parsing
capabilities and structured recovery refusals formatted by the UI layer.
Keep upload readiness/power/start-print policy in its existing workflow, and
keep recovery's strict unsupported-command policy separate from the indexer's
more tolerant geometry scan.

Proof: Windows ambiguous/dead/alive process cases; both upload consumers;
classification consistency; parser consumers and complete macro refusal lists.

## Useful further separation, without requiring more public packages

* `PreviewPresentation.py:338` combines scene enumeration/name matching and tag
  progress fallback with panel lifecycle and hover routing. A scene-read adapter
  and object-tag presenter would make those behaviours independently testable.
* `BedMeshSceneNode.py:140` holds bilinear sampling and geometry construction
  behind a Uranium SceneNode import. Extracting mesh sampling/build values would
  make the algorithm reusable; retain a small host mesh/draw adapter.
* The existing pure polygon, physical-motion, camera parsing, detection region,
  material and rotor components could gain documented public APIs/distributions
  if there is a consumer. They do not all need to become separately versioned
  packages to satisfy internal modularity.
* Python and JavaScript preview gradients implement the same equations. A shared
  specification/conformance fixture is appropriate across the language boundary;
  introducing per-segment Python calls into Canvas is not.

## Enforcement needed alongside the extractions

`tests/test_architecture.py` has useful exhaustive dependency inventories, but
allowing an edge does not prove the edge is desirable. Basename-based ownership
and source-token assertions should be supplemented with package-qualified layer
rules, import-cycle checks and actual independent consumer tests. Forbid core
imports of Cura/Monitor/application adapters; separately qualify core, raster and
scene-graph consumers. Retain lazy-import and package-resource checks.

The wheel manifest and installed-library checks already prove substantial source
and resource parity. Extend them for the narrower APIs rather than replacing
them. For recovery in particular, prove displayed itinerary versus executable
plan; testing both independently against handwritten expectations leaves room
for them to drift together or differently.

## Boundaries worth preserving

The composition root, shared Moonraker session/transport/framing, pure parser and
geometry primitives, immutable document snapshots, recovery execution owner,
file leases, state-store/migration writers, camera parser/decoder/statistics,
temperature presenter, and graphics context/mailbox/resource owners already
provide meaningful decomposition. There is no evidence that splitting each into
another package would improve them. Cura-specific scene and UI hosting should
remain adapters. Source moves must remain separate from policy/rendering changes.

## Recommended sequence

1. Canonical recovery itinerary and its display/execution equivalence tests.
2. Raster/public API separation; index data/residency and cache identity boundary.
3. Monitor recovery/follower owners and settings validation.
4. Follower QML delivery/gesture owners and toolhead editor/import separation.
5. Detection action state, shared transport/storage primitives and secondary
   Preview/bed-mesh extractions.

Boundary tests, documentation and independent-consumer proofs belong to each
step. This replaces the earlier assumption that PlateQt and index retention were
the only remaining modularity work. No runtime refactor was made for this audit.
