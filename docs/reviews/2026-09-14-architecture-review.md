# Architecture and code-quality review

Reviewed on 14 September 2026 against main at [`f500f91df949d86c8350059bdd62189d5b40a5fc`](https://github.com/shallax/MoonrakerPrintFollower/commit/f500f91df949d86c8350059bdd62189d5b40a5fc), package version **4.0.1**.

This report is a proposed engineering plan. Its findings describe the reviewed commit; its recommendations have not been implemented by this documentation change. The corresponding release proposals are in [ROADMAP.md](../../ROADMAP.md).

## 1. Assessment

The project has a sound underlying architecture, but its ownership rules are being applied unevenly as functionality grows. The original separation between the Cura host, physical print observations, following policy, indexing, and networking is useful and visible in the code. The strongest next investment is to extend that discipline to the newer transfer paths and the Monitor/file-manager surface.

The highest-priority work is correctness in asynchronous file operations. This review found concrete source paths where cancellation does not retire all work, byte accounting crosses operations, and a previous print's metadata can become associated with a new print. These deserve a small maintenance release before a broad refactor.

The main maintainability issue is concentration of behavior in the Monitor model and its large QML documents. The model's name and architectural contract suggest a presentation adapter, but it also owns print-start decisions, file-operation confirmations, upload checks, persistence, camera recovery, and aggregation of almost every component's changes. That concentration increases the chance that an unrelated update interrupts a gesture or does expensive work.

Testing is substantial. The gap is the relationship between what a test exercises and what its result establishes. Real-Cura execution, direct Python slot invocation, a screenshot, a source-string assertion, and a scenario-map entry each provide different evidence. The roadmap should make those distinctions explicit and spend the next testing increment on meaningful lifecycle and interaction failures.

A replacement architecture is unnecessary. A sequence of small ownership repairs, stronger behavioral evidence, explicit action policy, and focused presentation extraction is sufficient.

## 2. Scope and evidence

The review traced the composition roots, printer/session transitions, shared transport, status observations, main and one-off downloads, indexing, both multipart upload paths, Monitor/file-manager projections, command/tuning boundaries, representative QML, source-contract tests, Qt integration tests, and the harness's scenario dispatch. It also examined the current roadmap and the completed Actions runs for the reviewed SHA.

This was a static source review supported by existing CI evidence. No new Python tests or live Cura/printer reproductions were executed in this review. The specific failure scenarios below are derived from the code; their proposed regression cases are the first implementation steps. Runtime frequency and performance impact have not been measured here.

Existing evidence at the reviewed SHA:

| Evidence | Observed result | What it establishes |
| --- | --- | --- |
| [CI run](https://github.com/shallax/MoonrakerPrintFollower/actions/runs/34857827194) | Successful | Existing checks passed for this exact source revision. |
| [Coverage job](https://github.com/shallax/MoonrakerPrintFollower/actions/runs/34857827194/job/104021849139) | 730 tests run; one skipped; 86% aggregate measured Python statement coverage | Broad execution of the Python implementation, with the usual limits of statement coverage and test doubles. |
| Python/Qt compatibility jobs | Successful on Python 3.10, 3.11 and 3.12 | The tested Qt-backed suite passes across those Python versions; this is separate from validation of every supported Cura release. |
| [Release run](https://github.com/shallax/MoonrakerPrintFollower/actions/runs/34857919811) | Release checks and all 14 harness gate jobs successful | Existing packaged-artifact and real-Cura scenarios passed, including the primary/secondary smoke jobs. |
| Source structure | Approximately 16,400 physical lines across 53 production Python files | The plugin now has enough surface area that explicit ownership and selective publication materially affect maintenance. |

Coverage in the logged run is uneven in ways that deserve targeted evidence, rather than an arbitrary higher global threshold: RemoteFileService is 86%, FileDownload 61%, PreviewPresentation 50%, and BedMeshSceneNode 2%. Those figures describe the Python coverage job; they do not incorporate all behavior exercised by the separate real-Cura harness.

## 3. Architecture worth preserving

**The composition roots are clear.** MoonrakerPrintFollower is a small host facade; FollowerRuntime constructs dependencies and closes them. This makes it possible to reason about the runtime without inheritance tricks or a mutable shared context. Preserve these boundaries. Sources: [plugins/MoonrakerPrintFollower.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerPrintFollower.py), [plugins/FollowerRuntime.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FollowerRuntime.py).

**Physical state and displayed Preview state are separated.** RemoteJobService, PrintSnapshot/LayerResolver, PreviewFollower and PreviewMotion distinguish what the printer has reported from what Cura currently displays. That is the right foundation for manual detachment, scheduled pauses and a future physical-position marker. Preserve the rule that display smoothing cannot become an input to physical state. Sources: [plugins/RemoteJobService.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteJobService.py), [plugins/PrintState.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PrintState.py), [plugins/PreviewFollower.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PreviewFollower.py), [plugins/PreviewMotion.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PreviewMotion.py).

**Index work has an explicit owner.** GCodeIndexService allows one submitted worker at a time, uses generations to reject stale publication, and retains file leases while work runs. The service is a useful model for repairing download-worker ownership. Source: [plugins/GCodeIndexService.py:45](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/GCodeIndexService.py#L45).

**The shared network boundary does useful work.** Request lanes, credential handling, cancellation, same-origin redirects and HTTP fallback are centralized. The custom WebSocket framing is isolated from Qt ownership, and its dependency constraint is documented: Cura's verified bundle does not supply arbitrary Qt modules. Preserve this constraint when considering libraries; a package change needs an actual compatibility benefit. Sources: [plugins/MoonrakerTransport.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerTransport.py), [plugins/MoonrakerSocket.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerSocket.py), [plugins/SocketFraming.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/SocketFraming.py), [ARCHITECTURE.md](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/ARCHITECTURE.md).

**Command confirmation is distinguished from request acceptance.** The session command tracker and the main upload controller already contain deliberate handling of pending operations, stale completions and terminal delivery. New workflows should reuse those principles. Sources: [plugins/MoonrakerSession.py:256](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerSession.py#L256), [plugins/UploadController.py:269](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/UploadController.py#L269).

**The verification infrastructure is valuable.** Real HTTP loopbacks, Qt-backed component tests, package parity, deterministic captures, and real-Cura runs provide a strong base. Improvements should sharpen their claims and cover missed transitions while retaining useful existing checks.

## 4. Correctness findings for 4.0.2

“High” means a plausible corruption, cross-session result, or application-stall path needing early correction. “Medium” means a concrete incorrect-result or resource-lifetime issue. These priorities express impact and source evidence, not an assertion that the failure was reproduced live.

### F01 — Download worker ownership and cancellation are incomplete

**Priority: High. Confidence: high in the ownership defect; the precise race outcome needs a controlled reproduction.**

The main download starts a daemon thread whose loop repeatedly reads `self._write_queue` and `self._target`. Cancellation clears/aborts the target but does not send the writer a termination signal or retain it until it exits. A subsequent bind clears the shared worker fields, and a later download replaces them with new queue/target objects. Sources: [plugins/RemoteFileService.py:282](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L282), [plugins/RemoteFileService.py:306](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L306), [plugins/RemoteFileService.py:377](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L377), [plugins/RemoteFileService.py:200](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L200).

A worker waiting on the previous queue can remain blocked indefinitely. A worker between iterations can instead pick up fields belonging to the next operation. Closing a target while its writer is active also races with the write. Under an unlucky interleaving, two workers can consume the replacement queue; sentinel consumption and completion are then no longer associated with one operation.

Two related responsiveness issues reinforce this finding. The Python queue is unbounded, so the Qt reply's 4 MiB read-buffer limit does not bound the application's queued bytes. Completion calls `self._writer.join()` on the Qt thread. If disk writing falls behind, the remaining backlog is paid as a UI stall.

**Change:** give each download an operation object owning its queue, target, cancellation state, byte count and worker handle. Pass those operation-local objects to the worker. Retire the operation before aborting the reply, signal the worker to stop, and release its file only after worker completion. Deliver completion back to Qt asynchronously. Introduce bounded byte buffering with backpressure that does not block the GUI thread; a blocking `put()` in `readyRead` would only move the stall.

**Acceptance:** use an injected gated/slow writer to cancel while blocked, rebind, and immediately start another download. Both workers must terminate, only the new operation may publish, no old bytes may reach the new target, the GUI heartbeat must continue, and queue growth must remain bounded. Inject a disk-write failure as well as network cancellation.

### F02 — Download progress and the size cap accumulate across transfers

**Priority: Medium. Confidence: high.**

`_download_received` is initialized in the service constructor and incremented as chunks arrive. Neither bind nor the start of a download resets it. Both the displayed progress and the 2 GiB cap use this counter. Source: [plugins/RemoteFileService.py:149](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L149), [plugins/RemoteFileService.py:316](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L316).

A second download can start with the first download's byte count already included, making progress jump or reach 100% prematurely. Enough cumulative traffic causes a later valid file to be rejected as oversized. Retries also inherit prior bytes.

**Change:** move the counter into the operation introduced for F01, initializing it on every attempt. Keep any lifetime telemetry in a different counter.

**Acceptance:** perform two sequential downloads and a failed-then-retried download with a deliberately small injected cap. Each valid operation must start at zero and be judged against its own bytes; a genuinely oversized operation must still fail.

### F03 — One-off file-manager downloads can outlive the printer/session that requested them

**Priority: High. Confidence: high in the missing lifecycle checks.**

`download_once()` tracks objects in `_one_shots`, but bind/close do not cancel that set. The one-off object has no captured session identity, its `_abort()` closes the target without aborting the network reply, and FileDownload's completion calls `CuraIntegration.load()` without checking that the original printer/request still owns the result. Sources: [plugins/RemoteFileService.py:31](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L31), [plugins/RemoteFileService.py:182](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L182), [plugins/RemoteFileService.py:415](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/RemoteFileService.py#L415), [plugins/FileDownload.py:21](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileDownload.py#L21).

The source permits this sequence: request a file from printer A, switch Cura to printer B, then receive A's late completion and load it into the current Cura session. This is a stale download/load issue; it is not evidence of a command being sent to printer B.

This path also writes chunks directly on the UI thread, drops errors silently at the FileDownload adapter, and does not retire replies on ordinary completion.

**Change:** reuse the stream-operation lifecycle from F01. Capture the printer/session identity and the load intent, cancel on session invalidation/shutdown, check ownership before loading, and surface failure through an existing status/console channel. Arbitrary browsing downloads should not be cancelled merely because the active print's job key changes; their owner is the requested file operation and printer session.

**Acceptance:** delay A's response, switch printer, and then complete it. Cura must not load A's file into B's session. Test shutdown, early construction failure, a size-cap failure, and two overlapping requests; verify exactly one terminal result per request and disposal of replies, workers and temporary files.

### F04 — Local-file uploads omit terminal reply cleanup

**Priority: Medium. Confidence: high.**

FileManager implements its own multipart upload. It parents the QFile and multipart to the reply, but `_upload_finished()` removes the reply from the registry and returns on success/error without scheduling deletion or closing the source. The shared network manager does not enable automatic reply deletion. Sources: [plugins/FileManager.py:435](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManager.py#L435), [plugins/FileManager.py:500](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManager.py#L500), [plugins/MoonrakerTransport.py:58](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerTransport.py#L58).

The ordinary completion paths therefore retain replies and their child resources under the manager. Removing the Python dictionary entry is insufficient. Qt explicitly requires disposal of completed replies unless automatic deletion has been enabled; its default is false. References: [Qt reply lifecycle](https://github.com/qt/qtbase/blob/v6.11.0/src/network/access/qnetworkaccessmanager.cpp#L200), [Qt default](https://github.com/qt/qtbase/blob/v6.11.0/src/network/access/qnetworkaccessmanager_p.h).

**Change:** put terminal cleanup in an unconditional path and invalidate registry ownership before an abort can synchronously emit completion. Give each upload an operation identity so an old result cannot complete a newer operation. Extract shared multipart/resource ownership from the two upload implementations when that can be done without changing their separate user workflows: Preview preparation/readiness/start-print and local-file upload remain different use cases.

**Acceptance:** run repeated success, error and cancellation cycles with a real Qt reply. Verify the reply is destroyed after deferred deletion, source files are closed, and each operation emits one result. Test cancel/rebind immediately followed by a new upload.

### F05 — The coordinator can relabel old metadata as belonging to the new print

**Priority: Medium. Confidence: high.**

RemoteFileService clears metadata when its job changes. The coordinator's separate `_mr_meta` cache only clears on a binding reset. `refresh()` uses that cache whenever the file service has no metadata. When a new metadata request starts, `_mr_meta_file` and `_mr_meta_job` are changed before a successful response replaces `_mr_meta`. Sources: [plugins/PrintCoordinator.py:97](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PrintCoordinator.py#L97), [plugins/PrintCoordinator.py:178](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PrintCoordinator.py#L178), [plugins/PrintCoordinator.py:277](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/PrintCoordinator.py#L277).

Suppose print A supplied metadata, then print B begins without being downloaded. B initially receives A's layer-height/estimated-time/filament metadata as the fallback. If B's request fails, the next call can satisfy the “metadata exists for this file/job” shortcut using A's old payload and B's new key. The old values can consequently persist and prevent the intended retry. The 30-second throttle also compares filename without the job key, delaying same-name restarts.

**Change:** invalidate the fallback payload on job change and distinguish requested identity, successful-result identity and retry state. Only mark metadata valid after a successful matching response. Subsequently consolidate metadata ownership into one service that supports metadata-only requests independently of downloading/indexing; the coordinator should not implement a second request/cache lifecycle.

**Acceptance:** load A metadata, start B, fail B's first metadata request, then allow a retry. A's values must never become B's values, and B must retry. Include a same-filename restart inside 30 seconds and a late response from the previous job.

## 5. Maintainability and performance findings

### F06 — File-list projections are recomputed by unrelated Monitor updates

**Priority: Medium. Schedule: a small measured repair in 4.1; structural completion in 4.3.**

The model connects data, commands, controls, camera, toolhead, console, files and mesh changes to the same `_publish()`. That method always asks for file-manager values. The projection repeatedly calls page/count helpers, each of which runs the full filter/search/sort pipeline. Sources: [plugins/MoonrakerMonitorModel.py:391](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L391), [plugins/MoonrakerMonitorModel.py:487](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L487), [plugins/MoonrakerMonitorModel.py:602](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L602), [plugins/FileManager.py:926](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManager.py#L926), [plugins/FileManagerPolicy.py:619](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManagerPolicy.py#L619).

For the normal open, numerically paginated path, there are nine pipeline evaluations inside `_file_manager_values()`; the closed path still does four to produce page information. Filter-option counts also rescan resident rows. These counts are derived from the call paths, not a timing benchmark.

Selective Qt notifications are already implemented and worth preserving. They avoid notifying unchanged values, but only after the values have been recomputed. Thumbnail publication has a separate path; extend that selective approach to the whole file projection.

**Change:** compute a single file-view result containing rows, total, page index/count, selection and empty state. Cache it by data/view/history revisions and any time boundary needed by date-based filters. Recompute only when those inputs change. A later FilesViewModel can own the publication and expose a QAbstractListModel with stable row identities and appropriate dataChanged notifications.

**Acceptance:** instrument projection calls and event-loop latency using realistic 400-file and larger listings. A temperature tick or console append must not sort the file list; a search/sort/navigation change should perform one projection. Use measured baselines to set timing budgets. Do not hide staleness behind a cache: time-dependent filters must expire deliberately.

### F07 — The Monitor model and QML documents have outgrown their responsibilities

**Priority: Medium. Schedule: prepare boundaries in 4.2, extract in 4.3.**

The largest presentation files at the reviewed revision are approximately:

| File | Physical lines | Main concern |
| --- | ---: | --- |
| [plugins/MoonrakerMonitorModel.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py) | 1,700 | Projection, persistence, confirmations, print-start supervision, upload decisions and component composition share one object. |
| [plugins/FileManager.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManager.py) | 1,100 | Resident browsing/history, mutations, uploads, thumbnails and column/view state share one owner. |
| [plugins/FileManager.qml](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/FileManager.qml) | 3,300 | Listing, menus, several dialogs, filtering, selection, keyboard behavior and layout share one document. |
| [plugins/MoonrakerMonitor.qml](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitor.qml) | 3,100 | Camera, console, status, information panes and popovers share one document. |
| [plugins/MoonrakerMonitorDashboard.qml](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorDashboard.qml) | 2,250 | Control sections, dialogs, emergency control and dashboard layout share one document. |

Line count is a locating aid. The stronger evidence is that `_publish()` both projects values and advances the file print-start watchdog, requests thumbnails and changes camera state. File-operation workflow policy also lives in model slots. A projection method that can trigger work is harder to invoke, test and optimize safely. Sources: [plugins/MoonrakerMonitorModel.py:637](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L637), [plugins/MoonrakerMonitorModel.py:650](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L650), [plugins/MoonrakerMonitorModel.py:1242](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L1242).

**Change:** retain Cura's required PrinterOutputModel adapter, then compose focused QObject view models with explicit properties and intents. Start with files because it has the clearest independent lifecycle and the most expensive projection. Extract a print-start operation owner and a small UI-state store. Follow with controls, console/camera and information/chart presentation where independent updates justify a boundary.

For QML, extract complete functional components: file table, filter controls, file confirmation dialogs, console pane, camera pane, toolhead section and peripheral controls. Each should receive explicit model properties and emit intents. Avoid passing the entire root object to every child, which would recreate the existing coupling under different filenames.

**Acceptance:** preserve object names used by interaction tests, existing public Cura/plugin surfaces, persistence migration and gesture ownership. Test resize, focus, Escape handling, confirm/cancel, printer switches and repeated slider grabs after each extraction. Stop when a feature change stays within its component; a lines-per-file target is not an acceptance criterion.

### F08 — UI coverage currently includes direct slot invocation

**Priority: High for interpreting test evidence. Schedule: 4.1.**

The harness runs real Cura, but several scenarios bypass the user's input path. `exec_slot` and `exec_file_slot` invoke model methods, while `emit_click` directly emits a clicked signal. For example, the pause/resume scenario invokes `pausePrint` and `resumePrint`, and file scenarios invoke upload, rename and confirmation slots. Sources: [tests/harness/runner.py:2051](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/harness/runner.py#L2051), [tests/harness/runner.py:2159](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/harness/runner.py#L2159), [tests/harness/scenarios.py:850](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/harness/scenarios.py#L850), [tests/harness/scenarios.py:930](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/harness/scenarios.py#L930).

These tests provide useful integration evidence: they exercise real application code and can verify the peer's requests. They do not establish that a control is reachable, enabled correctly, receives focus, handles a drag or survives an overlay. Screenshots taken after a direct slot call do not add that missing interaction coverage.

The surface-map check verifies that a name is mapped or excluded; prefix rules can cover a whole family. This is a useful inventory, but it does not prove that each mapped interaction was executed and asserted. Source: [tests/harness/surface_coverage.py:57](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/harness/surface_coverage.py#L57).

**Change:** classify scenarios as UI interaction, application integration, diagnostic probe or explicit exclusion. Convert the 4.1 critical user journeys to actual Qt mouse/keyboard events through the rendered controls. Keep slot-driven integration tests where they are useful, with accurate labeling. Record the intended target, delivered input, peer-side effect and rendered outcome. Make map validation distinguish inventory completeness from executed evidence.

**Acceptance:** deliberately break an enabled binding, cover a button with an overlay, and break a confirm handler. The corresponding UI test must fail even if the Python slot still works. Complete the roadmap's existing slider, confirmation-dialog, settings and Information-pane scenarios. No green “UI coverage” result may depend only on invoking the action under test directly.

### F09 — Source-string tests enforce incidental details and sometimes comments

**Priority: Medium. Schedule: 4.1, alongside the touched domains.**

Several architecture/Monitor tests assert exact implementation strings, file-local patterns and wording. One architecture test explicitly asserts a comment fragment in CuraIntegration. Another pins a specific thumbnail callback expression. Sources: [tests/test_architecture.py:81](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/test_architecture.py#L81), [tests/test_architecture.py:228](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/test_architecture.py#L228), [tests/test_monitor.py:130](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/tests/test_monitor.py#L130).

Such tests can reject an equivalent extraction while accepting code that contains the required phrase but violates the underlying lifetime rule. This matters directly to F01–F05: naming an ownership mechanism is weaker evidence than exercising cancellation and late completion.

**Change:** retain structural rules that are genuinely architectural—acyclic imports, package identity, forbidden dependencies, bundled-runtime compatibility—and implement them through AST or runtime inspection where appropriate. Replace comment/expression pins with observable behavior as those areas change. Keep UI geometry assertions, but anchor them to rendered outcomes and stable semantic selectors.

**Acceptance:** an equivalent helper extraction or comment rewrite should not fail behavior tests; deleting a required lifecycle guard or altering an action's result should fail. Add deterministic lifecycle cases for the transfer findings immediately in 4.0.2, then broaden the reusable harness support in 4.1.

### F10 — Action availability needs one owner, with execution-time checks

**Priority: Medium, with safety relevance. Schedule: the existing 4.2.**

The roadmap already identifies the scattered state guards correctly. Toolhead policy, command state, Monitor projections and QML independently derive permission-like decisions. This should be consolidated before adding more Preview controls. Sources: [ROADMAP.md:896](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/ROADMAP.md#L896), [plugins/MonitorCommands.py:93](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MonitorCommands.py#L93), [plugins/ToolheadPolicy.py](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/ToolheadPolicy.py), [plugins/MoonrakerMonitorModel.py:719](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L719).

**Change:** introduce one pure action-availability projection using current connection/state/capabilities, homing and peripheral constraints. Return named decisions and a concise disabled reason. Both view models and command owners consume it. Revalidate when queued work is actually dispatched: a valid click can become invalid before execution.

Preserve the documented product decisions during consolidation, including the disconnected-controls rule, the emergency-stop behavior, unrestricted console intent, and the no-reflow rule. Changing policy is a separate product decision from centralizing it.

**Acceptance:** use a table of disconnected, unknown, idle, printing, paused and error states, combined with relevant capabilities/homing/locks. Verify UI and dispatch agree and that a state change between enqueue and dispatch prevents an invalid operation. Unknown observations must remain distinguishable from an observed idle state.

### F11 — Interface contracts, persistence failures and documentation need tightening

**Priority: Low to medium. Schedule: typed/state boundaries in 4.2; incremental cleanup throughout.**

Components receive explicit collaborators, but many constructors leave those capability contracts untyped. Add small typing.Protocol interfaces for the seams being changed—transport, printer session, file operations, index queries and Cura loading—and type operation/result records. Start type checking with pure modules and these boundaries. Qt integration can retain narrow, documented Any usage where its bindings require it. Types complement lifecycle tests; they do not replace them.

Persistence reads and writes in the Monitor model swallow every exception. Defaults are reasonable for a missing first-run file; a permission/disk error while saving should produce a bounded diagnostic so a selection that fails to survive restart can be explained. Introduce an explicit state-store owner with migration tests and rate-limited failure reporting. Sources: [plugins/MoonrakerMonitorModel.py:111](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L111), [plugins/MoonrakerMonitorModel.py:161](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L161).

Comments often carry long historical explanations, panel references and attributions despite the repository's own concise-comment rule. Some contradict nearby current behavior: the print-start watchdog's introduction describes a progress test, while the implementation deliberately accepts the observed printing/paused state without one. Replace these with a short current invariant, preserving the rationale in design notes where needed. Sources: [plugins/MoonrakerMonitorModel.py:644](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/plugins/MoonrakerMonitorModel.py#L644), [INSTRUCTIONS.md:503](https://github.com/shallax/MoonrakerPrintFollower/blob/f500f91df949d86c8350059bdd62189d5b40a5fc/INSTRUCTIONS.md#L503).

The roadmap header still calls 4.0.0 current and 4.0.1 next despite the package/release being 4.0.1, and several historical forward references still mention 3.7.0. Correct the active sequence now. Keep shipped decisions available, but gradually move long historical discussion behind links so active work is visible. The implementation contract must only change when the implementation changes.

## 6. Proposed release sequence

| Release | Purpose and scope | Why here | Exit evidence |
| --- | --- | --- | --- |
| **4.0.2** | Repair F01–F05: transfer ownership, per-attempt byte accounting, stale one-off loads, upload cleanup, metadata identity. | Concrete correctness issues should not wait for UI refactoring. Keep changes narrowly scoped and review each operation lifecycle. | Deterministic cancellation/rebind/retry/cleanup tests plus the existing package and relevant real-Cura gates. |
| **4.1.0** | Extend the planned deep UI suite with F08/F09, reusable lifecycle failure scenarios, and the small F06 projection repair with measurements. | Establish dependable interaction and resource-lifetime evidence before structural change. The urgent cases already land with 4.0.2. | Real input for critical journeys, peer-side assertions, accurate evidence classification, projection/event-loop baselines. |
| **4.2.0** | Preserve state/permissions consolidation; add explicit action decisions, typed operation boundaries, a print-start owner and consolidated metadata ownership. Retain the planned volumetric-flow readout as a small vertical slice. | One authoritative action/state layer is needed before multiple UI surfaces expose the same commands. | Table-driven policy/dispatch agreement, stale-result tests, persistence compatibility, clean narrow interfaces. |
| **4.3.0 — added** | Extract Monitor/file-manager view models and complete QML components; finish selective projections and stable file-list model updates. | Give the presentation debt an explicit bounded delivery instead of burying a second major refactor in 4.2. | Preserved workflows and layout rules, stable gesture/focus behavior, measured projection improvements, unchanged package identity. |
| **4.4.0 — moved from 4.3.0** | Physical-position marker and the already-planned Preview capabilities. Start with the marker/status explanation and coordinate transforms; add jog/macro/other enhancements in independently reviewable slices. | Build new Preview commands on the common policy and proven lifecycle boundaries. Keep the existing feature ambition and make the infrastructure cost explicit. | Coordinate/unknown-position/off-path tests, Preview interaction evidence and the supported real-Cura matrix. |

The 4.3 insertion is the main scheduling tradeoff: it delays the physical-head feature by one release to make the growing Monitor surface cheaper to change. It should have a fixed component scope and measurable exit criteria, not expand into a repository-wide redesign. If feature value makes that delay unacceptable, the marker's display-only slice can proceed after 4.2 while 4.3 finishes; sharing the action policy and session ownership remains the dependency for its interactive controls.

The existing layer-hardening/foreign-heights work should become an explicit gate for the 4.4 resolver/coordinate work, scoped to the behavior the marker relies on. Continuous-Z/vase support is a distinct capability and should not silently become a requirement for every preceding maintenance release.

## 7. Implementation shape and sequencing within releases

The first transfer repair should keep the network policy in MoonrakerTransport and put per-operation resources in a small owned object. Upload and download operations need explicit start/cancel/complete states, one result identity and one cleanup route. They do not need a generic event bus, global context or a configurable workflow framework.

A useful split after 4.2–4.3 is:

| Responsibility | Proposed owner |
| --- | --- |
| HTTP credentials, request creation and request lanes | Existing MoonrakerTransport |
| Reply, source/target, queue, worker and terminal result | Transfer operation |
| Metadata-only lookup and successful metadata identity | One metadata service, consumed by the coordinator and file flow |
| Authoritative action availability | Pure action policy |
| Pending/confirmed/failed print-start operation | Print-start controller |
| File browser query, rows, selection and its UI intents | Files view model plus existing file policies/services |
| Connection/print summaries, charts, console and camera presentation | Focused view models where their update lifetimes differ |
| Cura host registration and public compatibility surface | Existing facade/output adapters |
| Section sizes, collapse state and their persisted schema | Small UI-state store |

Implement each extraction as a vertical slice: move one owner, redirect its callers, preserve observable behavior, and remove the obsolete path in the same change. Avoid a prolonged second runtime or forwarding shim layer. Preserve the package ID, Cura entry points and supported SDK declarations.

The test-harness runner and driver are themselves large—roughly 2,900 and 1,600 lines. As 4.1 changes their behavior, extract named step handlers and a validated scenario schema, with observation probes separated from action injection. This is maintenance of the existing harness, not a proposal to replace it.

Package-directory reorganization is lower priority than ownership. Existing structural tooling uses flat-file discovery, so moving files into subpackages has packaging/test-discovery consequences. Do that only after a stable boundary makes the move useful, with explicit package parity verification.

## 8. Verification priorities

The most valuable additional checks exercise transitions that normal short successful operations miss:

1. A slow writer is cancelled and immediately replaced; old work cannot publish or consume the new operation's resources.
2. Two valid transfers and a retry have independent accounting and cleanup.
3. A one-off download completes after switching printer or replacing the load intent.
4. Upload success, refusal, cancellation and rebind each release the Qt reply and source exactly once.
5. Print B's metadata fails after print A succeeded; no A values appear as B metadata, and retry still occurs.
6. Real control gestures remain stable while unrelated temperature, console and file updates arrive.
7. An action is queued while allowed and becomes disallowed before dispatch.
8. A large resident file listing stays responsive with the popup both open and closed.

Use injected barriers/clocks for race and timeout cases rather than relying on arbitrary sleeps. Use real Qt lifetime assertions where QObject ownership matters. Keep a small number of real-Cura journeys for the input/rendering boundary and peer effects. Retain the existing release gates; any later CI optimization should be based on measured duration and redundant evidence, with required-check behavior handled explicitly.

No higher coverage percentage or smaller-file target substitutes for these outcomes. The objective is that state ownership, user interactions and resource retirement remain correct as new functionality is added.
