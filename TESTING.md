# TESTING.md — the real-Cura UI test harness

> **Reconciliation status (2026-09-15, the 4.1.0 release):** this
> document describes the harness as it IS. Every section below
> carries its status inline; claims struck or amended in the 4.1.0
> reconciliation are marked **STRUCK**/**AMENDED** with the reason,
> and a doc-pin test fails if a struck phrase re-enters the text.
> The re-review's second pass (2026-09-15) struck the surviving
> false claims — the handshake and supervision claims, the
> transcript replay, the profile budgets, the red-gallery claims
> and the letter catalogue, among the rest named inline.
> The sections the release gate may CITE are: §1, §2.1's delivery
> and addressing rules, §2.3's run/artifact layout, §2.5, §3's
> catalogue, §4's flake policy, §6. Sections marked *planned* are
> design intent for later releases, never gate inputs.

The 4.0.0 release gate: an automated suite that drives the REAL Cura
application with REAL clicks against the REAL UI, connected to a full
Moonraker simulator over the same websocket and HTTP transports a real
printer speaks, and produces SCREENSHOTS AND VIDEO of every step as
the proof artifact.

## The mandate (2026-09-11)

1. REAL Cura, not instantiating QML and faking things: the plugin
   must be installed in Cura and live in the real runtime with a
   real UI being rendered, exactly as live testing does.
2. Screenshots as the evidence of tests passing — video recordings
   are even better (in the style of Cypress or Browserstack).
3. Cura 5.13 for now, with the version specifiable.
4. Open enough to test everything end-to-end, eventually.
5. Any single test must be runnable on its own, for fast iteration
   on one thing.
6. Run in Docker: the host is headless and must not be polluted.
7. Connect to a Moonraker simulator over both the HTTP and websocket
   transports; the simulator must behave per Moonraker's API specs.
8. Where the simulator is not accurate enough (dwell testing, say),
   it must be possible to point the framework at a real printer.
9. The plugin and Cura should not need modifying to facilitate
   testing (orchestration only — logging, performance counters —
   unless absolutely necessary).

Earlier rulings, still binding: screenshots are PROOF (no fakery);
the mandate covers ALL functionality end-to-end where feasible,
starting with the recent failures to prove the process; harness work
lives on the `v4.0.0-harness` branch (no PRs, no releases, sparse
commits — work locally).

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

1. Real input for the critical journeys. **AMENDED (2026-09-15):**
   the activation path is the driver's QTest-injected press/release —
   XTEST (`xdotool`) is never invoked anywhere, and the claim that
   it is was struck. A press is real when the delivery record shows
   it was ACCEPTED by the target's own item chain (an overlay or a
   disabled control fails the step by construction); the 4.1.0
   conversion moved the critical journeys onto this path, and the
   remaining direct-invocation steps carry their declared
   classification in the run's evidence record. Off-viewport
   controls are scrolled into the rendered viewport first (the
   containment is asserted).
2. No stubbing of the plugin's dependencies inside Cura. The model,
   the transport, the socket client, the bridge — all production code.
3. Screenshots and video are the deliverable. Every step captures;
   every scenario records; every failed assertion keeps its evidence.
4. Evidence must be independent of the claim. At least one assertion
   per scenario reads a peer-side fact (what the simulator received)
   or rendered pixels (what the X server drew), never only the
   plugin's own published values.
5. Determinism by environment, not by luck: pinned image, pinned Cura
   build with checksum, pinned fonts, software GL, fixed geometry
   (one display geometry: the 1920x1080 screen, the 1840x1040
   window, the pinned DPI), seeded configuration.

## 2. Architecture

```
┌──────────────────────────── harness image (docker) ───────────────────────────┐
│  Xvfb :99 (1920x1080, the one geometry) + ffmpeg (x11grab)                   │
│   └─ real Cura AppImage (5.13.0 baseline; CURA_VERSION selects others)       │
│        └─ plugin (production code) ──┐  real TCP: websocket + HTTP           │
│        └─ HarnessDriver (test-only   ├────────────►  Moonraker simulator     │
│           plugin, never shipped)     │               (Tornado, scripted)     │
│   test runner (out-of-process,        │         or  ┌─ real printer          │
│   python3 runner in the image)       ├────────────► │ (opt-in, read-only)    │
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
  geometry fallback hit FAILS the step — **AMENDED (2026-09-15):**
  the per-version manifest that would authorize fallbacks is
  *planned* (struck in §2.4); every Cura-side click is verified
  after the fact against Cura's own state (the stage changed, the
  layer moved by the expected delta). The resolved address (parent
  chain, class, geometry, text) lands in the step's delivery record.
- **Input (the 2026-09-11 ruling; the split validated in
  Phase A) — AMENDED (2026-09-15):** the claim that XTEST is the
  REAL-behaviour path was struck — Phase A found the environment's
  limit: under the WM-less Xvfb, XTEST hover and motion work but
  X-level button ACTIVATION does not (press/release never activate a
  QtQuick button; WM/no-WM/focus variants all tried). QTest is the
  CANONICAL activation path — an injected QtTest binding (PyQt6-Qt6
  pinned to the SAME version the bundle ships, staged on the
  interpreter's path at run time, never shipped) synthesizes events
  directly into Cura's window with exact button/timestamp control,
  for race windows, precise drag paths and delegate rows that shift
  coordinates. Phase A validated QTest end-to-end on Cura's OWN
  stage-header buttons (the injection that was challenged):
  synthesized press/release → DeliveryAgent → Button → clicked →
  handler, all three stages switching for real, on video. XTEST
  remains as the per-phase human-clickability realism control
  (screen-level pointer motion and pixel behaviour), never invoked
  by the suite's scenarios. **IMPLEMENTED (2026-09-15):** the
  delivery introspection the design promised — `deliver_click`/
  `click_text` record the window's mouse events, the item under the
  aim, and whether the press was ACCEPTED BY THE TARGET'S OWN CHAIN
  (a scroll container grabbing a disabled child's press reads as a
  refusal); a press that did not reach its intended item fails the
  step, and an overlay covering the target fails it by construction
  (proven live: the z15 dimmer refusal, and the harness catching
  itself when a leftover popup scrim refused the next scenario's
  clicks). Off-viewport controls are scrolled into the rendered
  viewport first, with the containment asserted.
- **The RPC surface** — **AMENDED (2026-09-15):** the driver
  exposes 47 verbs, not the ≤ a dozen this line once promised; the
  pinned structural test covers the runner's step-vocabulary (the
  census-pinned ratchet: real-input steps may only grow, direct
  invocation may only shrink), and the real-mode allowlists are
  pinned exactly (deny-by-default for input verbs against a live
  printer). The listener binds loopback port 0 on the GUI thread;
  `wait(condition, budget)` is a DEFERRED reply armed by a QTimer —
  the GUI thread never blocks. One in-flight request at a time.
- **Continuous recording** — **STRUCK (2026-09-15, *planned*):**
  the change recorders, the filmstrip and `watch(invariant,
  duration)` are design intent for a later release, not gate
  inputs. The per-step evidence record (`evidence.json`, schema 1)
  carries what the harness records today: op, spec, verdict,
  capture, duration, the delivery record and the mechanism-derived
  evidence class.
- **Screenshots and video** — **AMENDED (2026-09-15):** the
  canonical capture is `ffmpeg x11grab` (`xwd`/`QScreen.grabWindow`
  was struck as the claim); a capture is checked against the
  declared SIZE (a truncated or mis-sized frame fails the step), and
  the whole-scenario video records the same display. An assertion's
  capture must postdate the state change it claims.

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
  **STRUCK (2026-09-15, *planned*):** the transcript-replay claim —
  no live transcript is in the tree and no conformance scenario
  exists; the endpoint-generation audit is the fidelity oracle
  today.
  (b) The simulator is as STRICT as a real peer where the client's
  wire behaviour is at stake: unmasked client frames, RSV bits,
  unknown opcodes, control-frame length, non-minimal encodings, close
  handshake, per-message caps. Framing edge cases that Tornado cannot
  emit (fragmented server frames) stay in the pure unit suite
  (`tests/test_socket_framing.py`).
- **Cadence realism** — **AMENDED (2026-09-15):** pushes ride a
  fixed cadence (250 ms); the jitter/batch/skip distribution is
  *planned* — the fault arms that exist (dropped frames, the held
  upgrade, the subscribe refusal) cover the ordering risk today.
- **Capacity model (the dwell instrument)**: the simulator records a
  full request ledger `{ts, method, path, bytes, ms, in-flight}` with
  the aggregate stats, and the route-delay lane simulates a loaded
  peer per endpoint. Scenarios assert a request-rate budget over a
  steady window and the runner records the profile — the honest
  proxy for the printer-side dwell, which itself is only
  verifiable in real-printer mode (§2.5).
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
  group by name: smoke, connection, status, temperatures, console,
  webcams, files, motion, printing, settings, visual, preview,
  probe;
- `make ui_test CURA_VERSION=5.12.0 MODE=…` — any selection under any
  pinned Cura. A version is prepared once with
  `python3 tools/fetch_cura.py <version>`: it downloads the official
  AppImage, extracts it to
  `/tmp/mpf/cura_versions/<version>/root`, unpacks the matching
  PyQt6 + PyQt6-Qt6 wheels (read from the bundle, so they always
  match; the driver's QtTest import needs the full PyPI wheel) into
  `wheels/`, and records sources, sha256s and pins in
  `manifest.json` — the version-swap proof. The harness image is
  `tools/harness/Dockerfile`, run with `docker run --init`;
  `tools/harness_release.sh` runs the local matrix (the release
  WORKFLOW orchestrates the whole gate — §5).
- `make ui_test MODE=discover` — dump stage-menu coordinates;
- `make ui_test MODE=firstinstall` — the first-install leg: one clean
  profile booted twice (boot 1 proves the clean activation, boot 2
  the survival of what boot 1 saved — §3). `XDG_SEED=clean|full|keep`
  picks the fixture and is resolved once, like the run dir: `clean`
  is this mode's default (the committed fixture with the plugin's
  folder, its legacy blob and its cura.cfg section removed), `full`
  is the fixture as it ships (every other mode's default), `keep`
  boots the tree the previous run left behind.

Lifecycle and isolation (a scenario is a REBIND — the production
session boundary the plugin already supports): **AMENDED
(2026-09-15):** the scenarios in a group share ONE Cura boot (a
fresh boot per scenario was never the implementation — the claim is
corrected), and each scenario starts from the baseline geometry (a
suite-default pre-step, so a scenario's resize can never leak into
the next). Each unit runs in its own container and working
directory (the isolation ruling: per-slot containers with per-slot
`/tmp/mpf` under `harness_release.sh -j N`; the serial default keeps
the shared-boot debris proof). **STRUCK (2026-09-15):** the
handshake this line once promised was never built — the single-
instance trap is closed by the per-slot containers and work
directories themselves: each unit owns its port file (the boot wait
reads the run's OWN work dir, never the shared tree's copy), its
display, and its seeded tree.

The Cura profile is seeded, not produced by driving Cura's UI: a
pinned config directory checked into `tests/harness/config` (welcome
and What's-New dialogs suppressed, machine and printer record
present, window geometry pinned); a pre-scenario gate asserts the
expected stage is active and no overlay covers it.

Artifacts land in `/tmp/mpf/ui-artifacts/<run>/`: `index.html` (the
step gallery), `evidence.json` (the machine-readable per-step
record — op, spec, verdict, capture, duration, the delivery record
and the mechanism-derived evidence class, with the per-run
classification summary), the Cura log, the simulator's ledger and
the simulator's scenario log. **AMENDED (2026-09-15):** the
per-step address record is *planned*, not shipped — the evidence
record is what the gate cites today.

The runner supervises the app — **AMENDED (2026-09-15):** the
taxonomy strings and the wedge dump this line once promised were
struck as never built. What ships: Cura runs under an
in-container `timeout`, a failed run prints `ui_test: FAILED steps:
…` (the failing names land in the job log even when the gallery
upload dies) with the log tail and the last captures, and a wedged
boot gets the kernel's verdict — the process chain, each thread's
blocked syscall, then a live `strace` and `gdb` backtrace of the
loader (SYS_PTRACE is granted at container start for exactly this).
A failing run is evidence, not a mystery.

### 2.4 Cura version swap

`CURA_VERSION` selects a checksum-pinned AppImage from the cached
store (never downloaded at test time). The staging step generates the
driver's `plugin.json` for the target Cura's SDK; the pre-flight
asserts the plugin AND the driver actually loaded before scenario 1.
Per-version manifests — **STRUCK (2026-09-15, *planned*):** the SDK
verdicts, coordinate maps and theme-token manifests are design
intent for a later release; `HARNESS_COORDS` is exported and never
read today, and no manifest is written by anything. The version-swap
proof the gate cites is the smoke set running on the secondary
pinned Cura.

### 2.5 Real-printer mode (gospel truth #8)

`REAL_URL` (plus the key via env, never files) points the suite at a
real printer. Safety contract: only scenarios marked `real_safe` may
run — strictly read-only observation, no print start, no commands, no
restarts; a scenario that would mutate is refused. This is the mode
for dwell verification when the simulator's capacity model isn't
accurate enough.

Implemented as `make ui_test MODE=real` with `REAL_URL` +
`REAL_API_KEY` in the environment: the launcher rewrites the seeded
machine record at runtime (the host and key never touch the repo, the
logs or any committed file), the simulator is not started, and the
runner dispatches only the r-group (r1 status renders, r2
temperatures populate, r3 webcams stream, r4 the dwell profile, r5
the data-render census against the real printer).
Every step runs through a read-only allowlist — the ops are
observation/UI-navigation only, the slot allowlist is client-UI state
only, and the dwell GETs only `/printer/*` and `/server/*` routes.
Anything else — `sim_*`, file ops, jog/home/macros, print start —
is a hard refusal recorded in the gallery as a failed step, never a
command reaching the printer. A live print is observed, never
touched.

## 3. The scenario catalogue

Two layers. **The gates** are the release acceptance scenarios — each
must be green with its gallery, AND must have been demonstrably red
against the known-broken revision before it counts as evidence
(**AMENDED (2026-09-15):** nothing red is committed to git — the
red evidence is produced live per run: the z14 scenario runs the
broken-start journey against a printer that stays broken and
records the fired verdict as the expected red, and the z10/z11/z15
proof trio records the refused-press and overlay-refusal halves).
**The suite** is the full functional surface, built on the step
vocabulary the gates established.

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
   back re-attaches — THE ONLY automatic re-attach (the
   2026-09-11 ruling: "view-swap re-attach is the ONLY automatic
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

### The first-install leg (`MODE=firstinstall`)

The committed fixture is pre-migrated: `tests/harness/config` ships
a MoonrakerPrintFollower folder already holding a v2 document, so
every scenario boots a profile the plugin has migrated before. The
one path no scenario covered was the true first install — a machine
with no config folder at all — which is how a config-losing first
install shipped green past the gates. This leg is its proof, and it
is not a suite scenario: it boots twice.

One profile, two boots, one slot (`XDG_SEED=clean`, the mode's
default; the fixture is copied once, before boot 1, and never
re-seeded between them):

1. **Boot 1 — the activation.** With no config folder the plugin
   activates the v2 document directly: `configVersion` 2, no machine
   records, and no migration record in `global` (nothing was
   migrated, so there is nothing to record). The leg then configures
   the printer through the settings dialog's own save verb and reads
   the document back OFF DISK — a file edited behind the app would
   prove nothing about the plugin's write path. The app then closes
   itself, so boot 2 reads a tree the app closed rather than one the
   harness killed mid-write; the driver acks the close request
   before Cura closes, so an ack that never arrives is a failure, not
   a shutdown eating its own reply.
2. **Boot 2 — the survival.** The record boot 1 wrote is still
   there, field for field, and the document as a whole is untouched:
   the migration machinery left no record of its own in `global` and
   rebuilt nothing from a legacy blob. The red revision is the one
   this leg was written for: the boot that finds no migration record
   treated the live document as "needs migration" and replaced it —
   the second boot is where a first install lost its configuration.

Evidence is two galleries — the run's own `index.html` and
`boot2/index.html` beside it, each with its recording — and the clean
seed is fail-loud: the transform exits when there was nothing to
remove, so the leg cannot pass against a pre-migrated fixture.

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
behaviour get named variants; the letter catalogue this once used is
retired — the groups carry their own names): **connection** —
transport & connection (WS connect with the dot green only after the
first accepted snapshot, HTTP-mode full feed, mode switch
mid-session, silence proof → HTTP fallback, subscribe refusal →
HTTP fallback, 401 verdict, klippy ready re-subscribe, klippy lost,
reconnect, disconnected disables every control); **status** —
printer status (every print_stats state,
progress/layer/position/elapsed, filament totals, last-action row);
**temperatures** — temperatures/fans/sensors (targets, fan controls,
sensor hide/show, mini chart, runout, endstops); **console** —
console (send/response, error lines and the collapsed bell, clear,
scroll-to-prompt, recall, store backfill once, resize);
**webcams** — camera (first load, selector, rotation/flip
persistence, recovering wash, offline); **files** — files & print
start (browse/thumbnails, upload, delete, folder create/rename,
move, recents, print confirm → start verdict, the honest start
watchdog); **motion** — controls (jog pad with peer-verified `G1`s,
home/QGL/mesh calibrate/load/clear, abs/rel gating, extrude/retract,
macros refusing while printing, pause/resume/cancel, e-stop latch
and reconnect-after-emergency, lock toggle, power devices, bed-mesh
popover); **printing** — preview (card states, load end-to-end with
phases, attach/detach rules, scene-change/slicing/load detach, pause
schedule/remove/clear-all, bed-mesh show/hide with legend,
current-layer slot, ETA with drift-learning opt-in, the `</>`
alignment visual check); **settings** — settings & persistence
(transport radios with reason line, test connection, cadence sliders
bounded at 250 ms and persisted, camera config, ETA checkbox with
tooltip, relaunch keeps every preference); **soaks** — soaks &
faults (dwell profile, slow endpoint → degradation not hang,
dropped frames and socket close → recovery without data loss —
defined as a concrete set difference of received samples, a long
simulated print to completion with time-warped virtual_sdcard
progress; the soak group stays out of the release path).

**The visual group — visual fidelity.** Geometry pins (`assert_aligned`:
centre-line, containment, non-overlap; `assert_rect_change` for the
pane shrink/grow; `wait_rect` for rendered presence), rendered
follows-model checks (`wait_rendered`/`assert_rendered` read the
label's actual text after a push — the model being right is not
enough), and the UI exercise the ruling demands: pane
collapse/expand (info panel width, the temperatures section, the
status panel) and window resize to narrow/wide — the console's
width-driven auto-collapse and re-expand, with the panel layout
asserted intact at both extremes. The `</>` button is now pinned for
real: the preview's toolpath pipeline works end to end (the Voron
cube inserts through Cura's reader chain, the engine slices it — the
scenario pins the slice count at 39 — and the panel renders), and
the button appears once a
post-processing script is active — v1 activates PauseAtHeight
through the plugin's manager. The alignment question is
settled by a stock-Cura control boot: the button sits top-aligned
in Cura's native 60px action-panel row identically without the
plugin, so the apparent offset to our card's bottom line is Cura's
design — left alone, with the control's rects as evidence. The
matcher quirk this surfaced: custom Cura components layer labels
over clickable regions and repeater rows share objectNames —
rect/text lookups resolve the topmost visible instance, across
every visible window (the file manager is its own tree). One
limitation remains, documented with dump evidence: the file
manager's modal popups (the print-confirm dialog) open their dimmer
but expose no walkable content to the harness — their containment
stays covered by the model-level flows instead. The
margin-symmetry pin caught and closed a real asymmetry: the controls
pane reserved the scrollbar's width inside itself even when the
scrollbar was hidden, so the right gap read three margins wide
against the left pane's one (the doubled-edge class, the
right side this time). The reservation is gone (the scrollbar
overlays), and the pin now measures the panes' true outer edges:
11px vs 11px, green.

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

## 3b. The test model and the engine

`tests/harness/models/voron_cube.stl` is the suite's test model (the
2026-09-13 ruling): the Voron Design Cube v7, fetched from
a public mirror of the official STL and re-headed to a standard
binary STL header (the official export carries UltiMaker's "ATF"
header variant, which the reader chain silently rejects). The full
pipeline works: the cube loads and meshes (proven: 10,242
vertices), the engine slices it (the scenario pins the slice count
at 39), and the layer data reaches the SimulationView — the two
exec-level
blockers were the engine's relative ELF interpreter (resolved from
Cura's fakehome cwd, not the appdir — symlinked by ui_test.sh and
fetch_cura.py) and the layer job only feeding the view when the
SimulationView is the ACTIVE view (the Preview stage click).

## 4. Determinism and flake policy

- A dedicated harness image carries Xvfb, Mesa (llvmpipe via
  `LIBGL_ALWAYS_SOFTWARE=1`; never `QT_QUICK_BACKEND=software`),
  fontconfig, dbus, xdotool, ffmpeg and Tornado. **AMENDED
  (2026-09-15):** the claim that every package is pinned "by exact
  version" was struck — the Dockerfile is an unpinned base with
  unpinned apt (the render-stack pinning is a follow-up). The
  launcher exports `QT_QPA_PLATFORM=xcb` and `DISPLAY` explicitly;
  the boot gate pins the window to the one display geometry
  (1920x1080 screen, 1840x1040 window, pinned DPI) and FAILS unless
  the pin reports BOTH the wanted window size AND the wanted screen
  — a window larger than the screen used to pass by self-report
  alone. The container runs with `docker run --init` (docker-init as
  PID 1) so killed children are reaped instead of piling up as
  zombies, and the launcher (`tools/ui_test.sh`) holds an EXIT trap
  that kills Cura, the video ffmpeg and the simulator when the run
  ends — a finished run leaves no processes behind.
- **Timing budgets — AMENDED (2026-09-15):** the profile claim was
  struck — budgets are inline in the scenario steps (the
  hang detector treats a timeout as HANG, distinct from a red), and
  each latency assertion prints its observed value against its
  budget in the gallery.
- No retries. A flaky scenario is a failing scenario — with machinery
  so that is survivable: a measured baseline (5 clean runs on a green
  build before a scenario counts as a gate), INFRA vs PRODUCT failure
  tags and quarantine with teeth — **AMENDED (2026-09-15, all three
  *planned*):** the tags, the quarantine and the 5-clean-run
  baseline are design intent for a later release; what ships is the
  per-step evidence (the `evidence.json` record: op, spec, verdict,
  capture, duration, the delivery record and the mechanism-derived
  evidence class).
- The boot-time discovery intermittency (2026-09-13): the cold-boot
  race that kept the endstops (and sometimes webcams and
  temperatures) empty is closed on two fronts. The plugin now runs a
  discovery watchdog — three seconds after the connect, a chain that
  never armed (no objects list, no wanted object's data) is re-fired
  once through the same re-subscribe the Klippy-ready broadcast uses
  (ARCHITECTURE.md pins the contract; the Qt suite tests the heal).
  And the c-group's red turned out to be a miscalibrated
  expectation, not the race: the endstop summary only says "Not
  homed yet" when the items are EMPTY while connected, so the
  scenario now asserts the rendered items — the temperatures group runs green
  consistently.
- The red-run rule: a scenario that has never been observed failing
  is not evidence. **AMENDED (2026-09-15):** nothing red is
  committed to git — the red evidence is produced live per run by
  the z14 scenario (the broken start that stays broken, the fired
  verdict recorded as the expected red) and the z10/z11/z15 proof
  trio (the refused press and the overlay refusal).

## 5. Phasing (each phase ends with screenshots AND video for review)

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
- **Phase E — the version swap and CI (COMPLETE, 2026-09-13;
  AMENDED 2026-09-15):** `CURA_VERSION` picks any prepared Cura; the
  swap is proven — the smoke set runs green under both 5.13.0 (the
  primary pin) and 5.12.0 (the secondary). The release workflow runs
  its own matrix (smoke + the twelve suite groups on the primary,
  the smoke again on the secondary) — it does NOT call
  `tools/harness_release.sh`, the envelope and the retry policy this
  line once promised were struck, and the declared budgets are 15
  minutes per group and 20 for the smoke units with ONE attempt per
  unit (a timeout reports HANG, distinct from a red). Every unit's
  log records its wall clock (the performance record), and the gate's
  coverage EXECUTION check verifies the run's evidence: every mapped
  surface's scenario ran, and every objectName'd item mapped to a
  scenario is addressed by one of its steps. The local
  `harness_release.sh -j N` runs the same units in per-slot
  containers; the serial default keeps the shared-boot debris proof.
  **ADDED (2026-09-18):** the local matrix also carries the
  first-install leg (§3) as a 10-minute unit on the primary, and the
  release WORKFLOW's matrix runs it the same way — each unit takes
  its own `mode`, so the first-install step passes `MODE=firstinstall`
  (with no scenario group) instead of `MODE=suite`. The soak group
  stays out of the release path.

## 6. Boundaries

- Production-code surface (gospel truth #9): the harness adds NO
  production behaviour. Plugin-side changes are limited to
  objectNames on the interactive QML items the scenarios address
  (inert in production) and the orchestration logging already added
  for the 4.0.0 diagnostics. **AMENDED (2026-09-15):** 4.1.0 ships
  findings-driven product patches (the F06 projection repair, the
  naming passes) — those are the RELEASE's changes, each carrying
  its red-run evidence and its regression pins; the harness itself
  still adds no production behaviour. The driver, simulator, runner,
  scenarios and manifests live under `tests/` and never ship.
- Cura's own SimulationView internals are driven through Cura's own
  public APIs (the insert/slice exec chain), verified after the fact
  against Cura's own state — never by poking Cura-private state.
- The simulator never touches a real printer; its auth mode uses
  throwaway keys. Real-printer mode is read-only by construction.
- Reversal recorded: INSTRUCTIONS.md's earlier rejection of running
  real Cura under a virtual display is superseded by the gospel truths
  (real Cura under Docker is now the mandate); the doc and its pins
  are updated when the harness lands.
