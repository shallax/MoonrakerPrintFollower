# TESTING.md — the real-Cura UI test harness

The 4.0.0 release gate: an automated suite that drives the REAL Cura
application with REAL clicks against the REAL UI, connected to a full
Moonraker simulator over the same websocket and HTTP transports a real
printer speaks, and produces SCREENSHOTS AND VIDEO of every step as
the proof artifact.

## The mandate (the author's gospel truths, 2026-09-11, verbatim)

1. "It must be REAL Cura, not just you instantiating QML and faking
   things. The plugin must be installed in Cura and live in the real
   runtime with a real UI being rendered, just like I'm doing with my
   live testing."
2. "You must be able to give me screenshots as evidence of tests
   passing, even better if they're video recordings (something like
   cypress tests or Browserstack does?)"
3. "It must be Cura 5.13 for now, but we should be able to specify the
   version."
4. "It must be open enough that we can test everything end-to-end
   (eventually)"
5. "I must be able to specify which test to run in case we want to
   iterate quickly on just one thing."
6. "It must be able to run in Docker because the host is headless
   anyway and I don't want you polluting the host."
7. "It must connect to a Moonraker simulator over both HTTP and
   Websocket transports. The simulator must behave as per Moonraker's
   API specs."
8. "If required, it must be possible to point the test framework at a
   real printer for when the simulator isn't accurate enough (such as
   dwell testing?)"
9. "You shouldn't have to modify the plugin or cura to facilitate
   testing (except maybe for orchestration - such as logging or
   performance counters), but you may if you absolutely have to."

Earlier rulings, still binding: screenshots are PROOF ("I don't want
any fakery here"); the mandate covers ALL functionality end-to-end
where feasible, starting with the recent failures to prove the
process; harness work lives on the `v4.0.0-harness` branch (no PRs, no
releases, sparse commits — work locally).

## 1. What "real" means

The harness runs the actual Cura application process — the packaged
AppImage, the real QML engine, the real plugin installed in it — under
a real X server inside a purpose-built Docker image. The ONLY
components that are not production ones are the network peer (the
Moonraker simulator) and the observation channel (a test-only driver
plugin). Both substitutions are legitimate: the peer replaces what the
PRINTER runs, never what Cura runs; the driver replaces a camera
pointed at the screen, never the screen.

Hard rules, in order:

1. No direct slot invocation. A test expresses intent as input events
   injected through the X server's XTEST extension — a real pointer,
   real focus, real X-level implicit grabs, entering Cura's xcb QPA
   exactly as a human mouse does.
2. No stubbing of the plugin's dependencies inside Cura. The model,
   the transport, the socket client, the bridge — all production code.
3. Screenshots and video are the deliverable. Every step captures;
   every scenario records; every failed assertion keeps its evidence.
4. Evidence must be independent of the claim. At least one assertion
   per scenario reads a peer-side fact (what the simulator received)
   or rendered pixels (what the X server drew), never only the
   plugin's own published values.
5. Determinism by environment, not by luck: pinned image, pinned Cura
   build with checksum, pinned fonts, software GL, fixed geometry,
   seeded configuration.

## 2. Architecture

```
┌──────────────────────────── harness image (docker) ───────────────────────────┐
│  Xvfb :99 (fixed 1600x1000x24) + xdotool (XTEST) + ffmpeg (x11grab)          │
│   └─ real Cura AppImage (5.13.0 baseline; CURA_VERSION selects others)       │
│        └─ plugin (production code) ──┐  real TCP: websocket + HTTP           │
│        └─ HarnessDriver (test-only   ├────────────►  Moonraker simulator     │
│           plugin, never shipped)     │               (Tornado, scripted)     │
│   test runner (out-of-process,        │         or  ┌─ real printer          │
│   pytest in the image)               ├────────────► │ (opt-in, read-only)    │
└───────────────────────────────────────────────────────┴───────────────────────┘
```

### 2.1 The driver: a test-only Cura plugin

`HarnessDriver` lives in `tests/harness/` and is staged into the Cura
plugin path at run time only; the package-parity gate and a new
allowlist pin over `plugins/` contents guarantee it never ships.

- **Assertions** read real objects (QML items, the model, the
  controller) — reading state is not faking interaction.
- **Addressing**: objectName-first on the plugin surface (new
  objectNames added only where a scenario needs them; inert in
  production), class/property/geometry discovery for Cura's own
  surface (Cura 5.13 has no objectNames). On the baseline version a
  geometry fallback hit FAILS the step — fallbacks are per-version,
  authorized by the version manifest, and every Cura-side click is
  verified after the fact against Cura's own state (the stage changed,
  the layer moved by the expected delta). The resolved address (parent
  chain, class, geometry, text) is recorded in the step manifest.
- **Input (two complementary paths, the author's ruling
  2026-09-11; the split validated in Phase A):** XTEST is the
  REAL-behaviour path — every canonical click enters through the X
  server via `xdotool`, exactly as a human mouse. Phase A found the
  environment's limit: under the WM-less Xvfb, XTEST hover and motion
  work but X-level button ACTIVATION does not (press/release never
  activate a QtQuick button; WM/no-WM/focus variants all tried).
  QTest is the CHOREOGRAPHY path — an injected QtTest binding
  (PyQt6-Qt6 pinned to the SAME version the bundle ships, staged on
  the interpreter's path at run time, never shipped) synthesizes
  events directly into Cura's window with exact button/timestamp
  control, for race windows, precise drag paths and delegate rows
  that shift coordinates. Phase A validated QTest end-to-end on
  Cura's OWN stage-header buttons (the injection the author
  challenged): synthesized press/release → DeliveryAgent → Button →
  clicked → handler, all three stages switching for real, on video.
  QTest is therefore the canonical activation path in the harness;
  XTEST remains as the per-phase human-clickability realism control
  (screen-level pointer motion and pixel behaviour), not the
  activation mechanism. Both paths feed the same delivery
  introspection: the driver's event filter records the receiving
  item and whether the event was accepted, a press that did not
  reach its intended item fails the step, and the driver asserts no
  overlay covers the target's rect before injection.
- **The RPC surface** is pinned (a structural test; ≤ a dozen generic
  verbs — find / inspect / inject / capture / trace / relaunch /
  seed-state; no verb may name a plugin feature). The listener binds
  loopback port 0 on the GUI thread; `wait(condition, budget)` is a
  DEFERRED reply armed by a QTimer — the GUI thread never blocks. One
  in-flight request at a time.
- **Continuous recording**: for every tracked item property the driver
  installs change recorders (QML `Connections`/`onXChanged` probes)
  appending `(monotonic_ts, value)` into a ring buffer; a frame-indexed
  filmstrip capture fires on each change of the tracked set. Assertions
  run over the recorded series (`watch(invariant, duration)`), not
  over polls, and the gallery shows the timeline.
- **Screenshots and video**: the canonical capture is the X root
  window (`xwd -root`/`QScreen.grabWindow(0)` from a helper process),
  synchronized on a frame swap so it can never catch a half-painted
  frame; ffmpeg records the display for the whole scenario (the video
  requirement). Captures are frame-stamped; an assertion's capture
  must postdate the state change it claims.

### 2.2 The Moonraker simulator

A Tornado-based double (Tornado is Moonraker's own stack) speaking the
REAL protocols over real TCP, in both transports:

- **Websocket** (`/websocket`): subscribe reply carries the complete
  snapshot ONCE, then `notify_status_update` frames push only changes;
  `notify_klippy_ready` / `notify_klippy_disconnected` broadcasts; and
  — because Moonraker wipes every client subscription on a Klippy
  restart — the simulator models per-connection subscription state and
  DROPS it on `klippy_ready`, pushing nothing until a fresh subscribe
  arrives. RPC responses cover every method the plugin calls.
- **HTTP**: every endpoint the plugin actually calls, both lanes the
  plugin uses (commands, console store, files
  list/upload/download/move/delete/print/start, metascan, metadata,
  thumbnails, history/recents, database items, endstops, power,
  restarts, webcams) plus an MJPEG camera endpoint serving two
  dialects (with/without Content-Length, boundary variants, a slow
  first frame — the hard variant is the default).
- **Fidelity is audited, not assumed.** (a) The endpoint table is
  GENERATED from `MoonrakerProtocol.py`'s pure endpoint builders, and a
  unit test fails if a production endpoint has no simulator route.
  (b) The recorded live transcript from the author's Voron
  (Moonraker v0.13.0-733; 29 frames / 44.9 KB over 6 s) is kept in-tree
  and replayed verbatim; a conformance scenario asserts the scripted
  simulator diverges from the transcript nowhere the plugin relies on.
  (c) The simulator is as STRICT as a real peer where the client's
  wire behaviour is at stake: unmasked client frames, RSV bits,
  unknown opcodes, control-frame length, non-minimal encodings, close
  handshake, per-message caps. Framing edge cases that Tornado cannot
  emit (fragmented server frames) stay in the pure unit suite
  (`tests/test_socket_framing.py`); the end-to-end arm covers the
  extended-length encode path, which is the one that killed the live
  probe.
- **Cadence realism**: pushes jitter, batch, and occasionally skip a
  tick, from a seeded distribution — the metronome is NOT the default.
- **Capacity model (the dwell instrument)**: the simulator runs a
  bounded worker pool with measured service time, exposes queue depth,
  p95 latency and requests/s per endpoint, and records a full request
  ledger `(monotonic_ts, method, path, category, bytes, in-flight)`.
  Scenarios assert a request-rate budget over a steady window and the
  runner records the profile — the honest proxy for the author's
  printer-side dwell, which itself is only verifiable in real-printer
  mode (§2.5).
- **Fault injection**: dropped push frames, a stalled stream, refused
  subscriptions (both the unauthorized and the structured-refusal
  shapes), 401s, socket closes, a slow endpoint, and the
  slow-upgrade / swallowed first objects-list arm that reproduces the
  connect-time aux-subscription race.
- **The hermetic scenario boundary**: `/harness/reset` restores the
  kickoff state, clears every fault arm, the console lines and the
  request ledger between suite scenarios — one scenario's arms can
  never leak into the next (the group-a cascade that took down the
  first calibration sweep). `/harness/state` exposes the full state
  dict plus the counters, so `wait_sim` can assert any object. The
  print routes are faithful: pause/resume/cancel move `print_stats`,
  DELETE removes the file from the listing, and the API-key arm
  refuses the websocket upgrade itself (401), not just HTTP — the way
  the real host does.

### 2.3 Runner, selection and artifacts

`make ui_test` runs the skeleton demo scenario by default; the gates
run as `MODE=scenario1`..`scenario11`, and the suite as
`MODE=suite SCENARIO_GROUP=<group>`. Selection is modular (gospel
truth #5):

- `make ui_test MODE=suite SCENARIO_GROUP=webcams` — one surface
  group by name (or letter: `a`..`j`): connection, status,
  temperatures, console, webcams, files, motion, printing, settings,
  stress;
- `make ui_test CURA_VERSION=5.12.0 MODE=…` — any selection under any
  pinned Cura;
- `make ui_test MODE=discover` — dump stage-menu coordinates.

Lifecycle and isolation (a scenario is a REBIND — the production
session boundary the plugin already supports): each scenario gets its
own simulator instance and printer record, its own `HOME`/`XDG_*`
under the run directory, and its own Cura process; the runner kills by
process group and reaps before each launch. The single-instance trap
is closed by a handshake: the runner mints a run nonce, the driver's
first RPC reply carries `{pid, cura_version, platform_name, plugin_dir,
xdg_dirs, nonce}`, and the runner verifies it against the process it
spawned. A `relaunch()` primitive re-handshakes for the persistence
and reconnect groups.

The Cura profile is seeded, not produced by driving Cura's UI: a
pinned config directory checked into `tests/harness/config` (welcome
and What's-New dialogs suppressed, machine and printer record
present, window geometry pinned); a pre-scenario gate asserts the
expected stage is active and no overlay covers it.

Artifacts land in `/tmp/mpf/ui-artifacts/<run>/`: `index.html` (step
gallery: label, injected event with coordinates, condition expression,
the full poll/transition history, the asserted item's scene rect, an
annotated capture with pointer marker and red rect, the scenario
video, durations, latency observations with budgets), the Cura log,
the simulator's ledger and scenario log, and the resolved-address
manifest.

The runner supervises the app: wall-clock cap, liveness probe, and a
failure taxonomy — "scenario failed" / "app died" / "app never became
ready" — each with the log tail and the last capture. When the driver
is wedged (the dwell case), the runner grabs the X display
out-of-band, dumps all thread stacks via `faulthandler`, and escalates
SIGTERM → SIGKILL. A failing run is evidence, not a mystery.

### 2.4 Cura version swap

`CURA_VERSION` selects a checksum-pinned AppImage from the cached
store (never downloaded at test time). The staging step generates the
driver's `plugin.json` for the target Cura's SDK; the pre-flight
asserts the plugin AND the driver actually loaded before scenario 1.
Per-version manifests record SDK verdicts, coordinate maps (Cura's
sliders have no objectNames — their coordinates are version-pinned and
authorized per version), theme-token names and known UI deltas.
Manifests are generated from a live tree dump, so maintenance is
reviewing a diff.

### 2.5 Real-printer mode (gospel truth #8)

`REAL_MOONRAKER_URL` (plus the key via env, never files) points the
suite at a real printer. Safety contract: only scenarios marked
`real_safe` may run — strictly read-only observation, no print start,
no commands, no restarts; a scenario that would mutate is refused.
This is the mode for dwell verification when the simulator's capacity
model isn't accurate enough.

## 3. The scenario catalogue

Two layers. **The gates** are the release acceptance scenarios — each
must be green with its gallery, AND must have been demonstrably red
against the known-broken revision before it counts as evidence (the
red gallery is committed with the scenario). **The suite** is the
full functional surface, built on the step vocabulary the gates
established.

### The gates — release acceptance (recent failures, proving the process)

1. **Failure state clears itself** — the simulator errors the print
   (`Extrude below minimum temp`), and the START attempt of the NEXT
   print is armed; the scenario must let the 15 s verdict window fire
   against a printer that stays broken (red gallery: the verdict
   fires), then drive the recovery path: cold start → transient error
   → printing. Assertions: the Print job section shows the error; the
   jog pad UNLOCKS; the verdict does NOT fire on a transient error
   that recovers; the peer's ledger shows ONE connection throughout —
   the state cleared with no reconnect.
2. **Card stays through load and after render** — enter Preview with
   nothing loaded (the empty card), click "Load current print", and
   go HANDS-OFF. Assertions, per-card identity, on the recorded
   change trace: the ACTION card (the one with Detach) is visible
   continuously from the click to 30 s after settle; the EMPTY card
   never reappears after the load; a crop of each card's rect
   corroborates at three instants (clicked / mid-load / settled).
   The red gallery is the pre-fix revision's vanish.
3. **M117 in the Print-job section** — the slot label gets an
   objectName. Simulator pushes `display_status.message` A, then B:
   the RENDERED label shows B, then A is absent; then the message
   clears and the slot's previous content returns. Assertions read
   the rendered label text, not the model property.
4. **Camera first load** — the HARD ordering: the Monitor is entered
   while the webcam list is still pending, the list then arrives, and
   the stream must appear with no interaction. The simulator's MJPEG
   frames carry a changing, recognizable pattern (proving liveness
   in the capture); the first variant is the hard dialect with a
   slow first frame. Variants: keyless direct, auth-required bridged.
5. **Temperatures at print start** — the RACE form: the simulator
   holds the websocket upgrade past the bootstrap window while a
   print is already running (and the variant where Cura connects
   mid-print), then releases it; the first aux datum must arrive
   within 3 s of the sync snapshot, timestamped on the simulator's
   event clock. The red gallery is the swallowed-first-objects-list
   revision.
6. **Detach on any layer selection change** — while attached: a drag
   concurrent with a status push, a drag immediately after a view
   swap (the unarmed window — the one that must be red pre-fix), and
   a slow drag spanning several deliveries. Assertions run over the
   recorded `preview.state.attached` and `expected_layer` series:
   detach, stays detached until Attach. Variant: view-swap away and
   back re-attaches — THE ONLY automatic re-attach (the author's
   ruling, 2026-09-11: "view-swap re-attach is the ONLY automatic
   re-attach; any layer-selection change detaches and stays
   detached").
7. **Transport handover** — reopen the Monitor repeatedly in
   websocket mode. The peer's ledger asserts the core-category poll
   count (websocket mode must not fire the HTTP monitor lane beyond
   the bootstrap); the log assertions carry positive sentinel
   controls (the driver emits sentinel lines into the same stream,
   and the step fails if they are absent).
8. **Dwell profile** — a 10-minute soak, console expanded, against
   the capacity-limited simulator: assert the request-rate budget,
   peak in-flight and per-category rates; measure GUI responsiveness
   as scheduled-latency (a 100 ms QTimer's actual−scheduled deltas)
   plus the receipt canary (simulator event timestamp vs the model's
   apply time). The gallery shows the profile chart. Printer-side
   stutter itself: real-printer mode (§2.5).
9. **Pause list verified-only** — schedule an end-of-layer pause;
   drive the printer past the layer WITHOUT pausing; the entry stays
   listed, restyled as missed.
10. **Restart arming / e-stop latch** — print → error/cancel; the
    latch arms; a demonstrably fresh print clears it.
11. **Scroll-to-prompt** — flood the console; the view follows to the
    prompt on send; recall history works.

### The suite — the full functional surface, end-to-end

Coverage is machine-derived, not prose: a generator walks the code —
every `@pyqtSlot` on the QML-facing objects, every `objectName`'d
interactive item, every HTTP route from `MoonrakerProtocol.py`, every
published `MonitorFormatting` key — and produces a
surface→scenario matrix. A surface with no scenario entry FAILS the
suite (an explicit, justified exclusion is the only other option).
The selection rule for what merits a UI scenario: correctness that
depends on the real Cura/QML wiring, on real timing, on a reported
live failure, or on an addressable user intent; everything else stays
in the fast unit/Qt suites, and every scenario cites the ruling it
pins.

Groups (each feature gets a scenario; variants that carry distinct
behaviour get named variants): **A** transport & connection (WS
connect with the dot green only after the first accepted snapshot,
HTTP-mode full feed, mode switch mid-session, silence proof → HTTP
fallback, subscribe refusal → HTTP fallback, 401 verdict, klippy
ready re-subscribe, klippy lost, reconnect, disconnected disables
every control); **B** printer status (every print_stats state,
progress/layer/position/elapsed, filament totals, last-action row);
**C** temperatures/fans/sensors (targets, fan controls, sensor
hide/show, mini chart, runout, endstops); **D** console (send/response,
error lines and the collapsed bell, clear, scroll-to-prompt, recall,
store backfill once, resize); **E** camera (first load, selector,
rotation/flip persistence, recovering wash, offline); **F** files &
print start (browse/thumbnails, upload, delete, folder create/rename,
move, recents, print confirm → start verdict, the honest start
watchdog); **G** controls (jog pad with peer-verified `G1`s, home/QGL/
mesh calibrate/load/clear, abs/rel gating, extrude/retract, macros
refusing while printing, pause/resume/cancel, e-stop latch and
reconnect-after-emergency, lock toggle, power devices, bed-mesh
popover); **H** preview (card states, load end-to-end with phases,
attach/detach rules, scene-change/slicing/load detach, pause
schedule/remove/clear-all, bed-mesh show/hide with legend,
current-layer slot, ETA with drift-learning opt-in, the `</>`
alignment visual check); **I** settings & persistence (transport
radios with reason line, test connection, cadence sliders bounded at
250 ms and persisted, camera config, ETA checkbox with tooltip,
relaunch keeps every preference); **J** soaks & faults (dwell profile,
slow endpoint → degradation not hang, dropped frames and socket close
→ recovery without data loss — defined as a concrete set difference of
received samples, a long simulated print to completion with
time-warped virtual_sdcard progress).

The step vocabulary (`scenarios.py`, interpreted in
`runner.py`'s `suite_step`): `sim_set`/`sim_arm`/`sim_klippy`/
`sim_drop` drive the harness lane; `sim_ledger` asserts request
counts with an optional `method` filter; `exec_slot`/`exec_file_slot`
call a model slot with args, surfacing the driver's real error when
one raises (the harness's `slot(*)` bug hid exceptions for weeks —
the failure now names them); `exec_mode`/`assert_mode` apply and
verify the transport preference; `exec_validator` captures the
validator's answer for an `expect` comparison; `exec_test_connection`
runs the settings action's probe (the registry id is
`MoonrakerPrintFollowerConfigureAction`, not the plugin id);
`click_stage`/`click_text`/`click_item` drive the UI — `click_item`
emits the target's `clicked` signal when it has one, because custom
Cura components layer labels over their clickable region and a
coordinate click never reaches the handler; `wait_model`/
`assert_model`/`model_read`/`assert_changed` read published values;
`write_fixture` drops a local gcode for the upload flow; and
`exec_console`/`exec_console_resize`/`exec_stream_start`/`exec_upload`/
`exec_delete`/`exec_folder`/`exec_move`/`exec_extrude` drive the
remaining real paths. The boot gate now also waits for the model
itself (`activePrinter`) and re-emits Cura's
`globalContainerStackChanged` until it materializes — a quarter of
fresh boots restored the machine before the plugin's listener
existed and every later read saw `None`.

## 4. Determinism and flake policy

- A dedicated harness image pins Xvfb, Mesa (llvmpipe via
  `LIBGL_ALWAYS_SOFTWARE=1`; never `QT_QUICK_BACKEND=software`),
  fontconfig with an explicit fonts.conf, dbus, xdotool, ffmpeg,
  Tornado and pytest — by exact version, like the rest of the repo.
  The launcher exports `QT_QPA_PLATFORM=xcb` and `DISPLAY` explicitly;
  the driver's pre-flight refuses to run unless
  `platformName() == "xcb"`, the screen geometry matches the pin, and
  the window is exposed — recorded in the manifest. The container runs
  with `docker run --init` (docker-init as PID 1) so killed children
  are reaped instead of piling up as zombies, and the launcher
  (`tools/ui_test.sh`) holds an EXIT trap that kills Cura, the video
  ffmpeg and the simulator when the run ends — a finished run leaves
  no processes behind.
- Timing budgets live in a machine profile, not in scenario prose;
  each latency assertion prints its observed value against its budget
  in the gallery.
- No retries. A flaky scenario is a failing scenario — with machinery
  so that is survivable: a measured baseline (5 clean runs on a green
  build before a scenario counts as a gate), INFRA vs PRODUCT failure
  tags (both fail the run; the banner differs), quarantine with teeth
  (a dated entry in TESTING.md that still blocks the release; older
  than N days fails the suite), and per-step evidence (injected event,
  coordinates, condition, poll history, scene rect, peer-ledger
  slice).
- The red-run rule: a scenario that has never been observed failing
  is not evidence. Every gate scenario commits its red gallery from
  the known-broken revision.

## 5. Phasing (each phase ends with screenshots AND video shown to the
   author)

- **Phase A — the skeleton proof (COMPLETE, 2026-09-12):** boot
  real Cura under Xvfb in the harness image, click PREPARE →
  PREVIEW → MONITOR through the injected QTest path on Cura's OWN
  stage-header buttons, capture root-window frames and video, and
  run one deliberately failing scenario whose failure gallery ships
  with it. The QTest injection is PROVEN (the matching QtTest
  binding imported inside the real bundle interpreter), and the
  stage header renders on good boots — its QML StageModel Repeater
  delegates three buttons; a boot whose header buttons never appear
  is declared bad and retried under the flake policy, never worked
  around. This phase proves the honesty contract before any
  catalogue work.
- **Phase B — the simulator:** both transports, the endpoint table
  generated from the code, the transcript replay, the capacity model,
  the fault arms. Proof: a gallery of the Monitor rendering
  scripted data.
- **Phase C — the gates, first (the recent failures prove the
  process):** the 11 gate scenarios green with galleries AND red
  galleries against the known-broken revisions; the step vocabulary
  is a Phase-C deliverable — a suite scenario must be expressible as
  a short composition.
- **Phase D — the full surface:** the suite matrix lands feature-group
  by feature-group until the suite encompasses all testing
  end-to-end.
- **Phase E — the version swap and CI:** the suite under a second Cura
  version (swap manifest proven), then `make ui_test` in the release
  gates, with declared per-layer wall-clock budgets and the soak kept
  out of any PR-blocking path.

## 6. Boundaries

- Production-code surface (gospel truth #9): the harness adds NO
  production behaviour. Plugin-side changes are limited to
  objectNames on the interactive QML items the scenarios address
  (inert in production) and the orchestration logging already added
  for the 4.0.0 diagnostics. The driver, simulator, runner, scenarios
  and manifests live under `tests/` and never ship.
- Cura's own SimulationView internals are driven by XTEST events at
  authorized coordinates, verified after the fact against Cura's own
  state — never by poking Cura-private state.
- The simulator never touches a real printer; its auth mode uses
  throwaway keys. Real-printer mode is read-only by construction.
- Reversal recorded: INSTRUCTIONS.md's earlier rejection of running
  real Cura under a virtual display is superseded by the gospel truths
  (real Cura under Docker is now the mandate); the doc and its pins
  are updated when the harness lands.
