# TESTING.md — the real-Cura UI test harness

The 4.0.0 release gate: an automated suite that drives the REAL Cura
application with REAL clicks against the REAL UI, connected to a full
Moonraker simulator over websocket, and produces SCREENSHOTS of every
step as the proof artifact.

The author's ruling (2026-09-11, verbatim): "I don't want any fakery
here. I want you to be able to show me screenshots of the things you're
doing in the tests as PROOF that the things you've implemented work the
way you claim they work. We should use Cura 5.13, but it should be
possible to swap out the Cura version if we need."

## 1. What "real" means

The harness runs the actual Cura application process — the packaged
AppImage, the real QML engine, the real plugin — under a real window
server. The ONLY component that is not the production one is the
network peer: a Moonraker simulator. That substitution is legitimate
because Moonraker is what the PRINTER runs, not what Cura runs, and a
scriptable peer is the only way to make Klipper states (print start,
error, pause, camera streams) repeatable. Everything else — the
plugin's Python, its QML, Cura's stage machinery, the event loop, the
rendering — is the real thing.

Hard rules, in order:

1. No direct slot invocation. A test expresses intent as events
   (mouse press/release, drag, key) injected through the window's real
   input path. If a scenario can only be driven by calling a handler
   directly, the scenario is redesigned, not the rule relaxed.
2. No stubbing of the plugin's dependencies inside Cura. The model,
   the transport, the socket client, the bridge — all production code.
3. Screenshots are the deliverable. Every step (input, expected state,
   observed state) captures the window; every failed assertion keeps
   its capture. A run's gallery is what the author reviews.
4. Determinism by environment, not by luck: pinned container, pinned
   Cura build, pinned fonts, software GL, fixed window geometry.

## 2. Architecture

```
┌───────────────────────────── pinned container ─────────────────────────────┐
│  Xvfb :99 (fixed 1600x1000x24)                                             │
│   └─ real Cura AppImage (5.13.0 baseline; CURA_VERSION selects others)     │
│        └─ plugin (production code) ──┐                                     │
│        └─ HarnessDriver (test-only   │   websocket + HTTP (real protocol)  │
│           plugin, absent from        ├────────────►  Moonraker simulator    │
│           shipped packages)          │               (Tornado, scripted)   │
│   test runner (pytest + scenario     │               ┌─ ws subscribe/push  │
│   scripts, asserts via the driver)   │               ├─ HTTP endpoints     │
└───────────────────────────────────────────────────────┴─────────────────────┘
```

### 2.1 The driver: a test-only Cura plugin

`HarnessDriver` ships as a plugin in `tests/harness/` that the package
build EXCLUDES (verified by the package-parity gate, like the existing
test files). Running inside the real process gives the tests what a
sidecar cannot:

- `QApplication`, the QML engine, `Controller`, the scene and the
  plugin registry — the real objects, addressable directly for
  ASSERTIONS (reading state is not faking interaction).
- Item lookup by `objectName`, then by geometry fallback when a Cura
  version lacks an objectName.
- Event injection through the real path: `QTest.mouseClick/mouseMove/
  mousePress/mouseRelease/keyClick` targeted at the window or a
  located item. These synthesize real `QMouseEvent`s through the QPA
  input path — the same code path a human mouse uses once the X
  server delivers it.
- Screenshot capture: `window.grabWindow()` → PNG into the run
  directory, with the step manifest.

The driver exposes a small RPC surface (a loopback TCP listener on a
test-only port) so the OUT-OF-PROCESS runner drives scenarios and
reads results: `locate(name)`, `click(name|x,y)`, `drag(...)`,
`key(...)`, `wait(condition, timeout)`, `screenshot(label)`,
`property(name)`.

ObjectName coverage: every interactive item a scenario needs an
objectName on. The existing key items already carry them
(`moonrakerPreviewActionPanelControls`, `cameraControls`,
`consoleFlick`, …); the catalogue below drives the remaining additions
— an item that cannot be found reliably gets one, because the tests
are the consumers.

### 2.2 The Moonraker simulator

A Tornado-based double (Tornado is Moonraker's own stack) speaking the
REAL protocols, both of them:

- **Websocket** (`/websocket`): the full subscribe/push contract the
  production client implements — subscribe reply carries the complete
  snapshot ONCE, then `notify_status_update` frames push only changes
  at a configurable cadence (250 ms default); `notify_klippy_ready` /
  `notify_klippy_disconnected` broadcasts; RPC responses for the
  socket lane (`printer.objects.list`, `server.info`, `printer.info`,
  `server.gcode_store`, `server.webcams.list`, …). This must exercise
  the hand-rolled RFC 6455 client's edges: frames >125 bytes, masked
  client frames, fragmented frames, and pings.
- **Same transport, no shortcuts:** the plugin under test reaches the
  simulator over REAL TCP — the same hand-rolled RFC 6455 client, the
  same HTTP transport, the same auth headers a real printer gets. The
  simulator is never imported in-process by the plugin, and no lane
  of the production transport is bypassed for test convenience.
- **HTTP** (`/server/...`, `/printer/...`, `/machine/...`): the lanes
  that stay HTTP in both modes — commands, the console store, files
  (list/upload/download/delete/print/start), thumbnails, endstops,
  power, webcams — plus an MJPEG camera endpoint the bridge can relay,
  and an optional auth mode (valid key / rejected key / keyless).

Scenario scripts drive a state machine: `idle → heating → printing
(layer N, progress) → paused → error("Extrude below minimum temp") →
recovered`, with fault injection: dropped push frames, a stalled
stream (no frames for X s), refused subscriptions, 401s, socket closes,
a slow endpoint (the dwell reproduction).

### 2.3 Runner and artifacts

`make ui_test` runs Tier 1 by default. The selection surface is
modular — a subset runs without the rest (the author's ruling,
2026-09-11: "run tests X, Y and Z rather than having to run everything
every time"):

- `make ui_test SCENARIOS=card-stays,m117,camera-first` — named
  scenarios, comma-separated;
- `make ui_test GROUP=tier1|tier2|console|camera|files|controls|
  preview|settings|soaks` — by surface group;
- `make ui_test CURA_VERSION=5.12.0 SCENARIOS=…` — any selection
  under any pinned Cura;
- `make ui_test --list` — the scenario/group index the selections
  refer to.

1. Boot Xvfb (fixed geometry, no WM needed — Cura's windows are
   undecorated under Xvfb, which also makes screenshots stable).
2. Extract the pinned AppImage (`--appimage-extract`, no FUSE) and
   launch the real binary with the driver plugin staged in.
3. The runner drives scenarios out-of-process over the driver RPC,
   with a per-step timeout and a `wait(condition)` model — NO bare
   sleeps; every wait is "condition became true or the step failed
   with its screenshot".
4. Artifacts land in `test-artifacts/<run>/`: `index.html` (step
   gallery: label, input, assertion, screenshot, duration), the
   screenshots, the Cura log, and the simulator's scenario log.
5. Failure mode: the assertion, the final screenshot, and the log are
   one self-contained page the author can open — a failing run is
   evidence, not a mystery.

Rendering determinism: software GL (Mesa llvmpipe) in the pinned
container, the pinned theme and fonts, fixed window size, and the
existing capture conventions. Absolute pixel-identity is NOT promised
(first paint may differ by frame); screenshots are proof of STATE
(the card is present, the message is in the slot, the button is
enabled), not of pixels.

### 2.4 Cura version swap

`CURA_VERSION` selects the AppImage (URL pattern parameterised;
5.13.0 is the baseline pin). A per-version manifest records known
deltas: objectName coverage, theme token names, window chrome. Item
addressing is objectName-first with a geometry fallback so a version
swap degrades to a manifest update, not a test rewrite. The simulator
is version-neutral (it speaks Moonraker's protocol, not Cura's).

## 3. The scenario catalogue

The mandate (the author, 2026-09-11, verbatim): "the mandate is not
JUST the problems we've faced in this release, it should be all of the
functionality, end-to-end where feasible, connecting to a dummy
simulated printer over the same transport method that a real Moonraker
printer uses."

So the catalogue is two tiers. Tier 1 is the release gate — every
scenario must pass with its gallery before 4.0.0 ships. Tier 2 is the
full functional surface, end-to-end through real clicks against the
simulated printer, built incrementally after Tier 1 proves the
honesty contract; each feature's scenario names the clicks, the
assertions, and the screenshots it owes.

### Tier 1 — the release gate

1. **Failure state clears itself** — drive the printer to
   `error("Extrude below minimum temp")` mid-print; assert the Print
   job section shows the error, the jog pad UNLOCKS, and starting the
   next print (simulator: cold start → transient error → printing)
   clears the state with no reconnect and no "Print start failed"
   verdict. Screenshots: the error state, the unlocked controls, the
   recovered print. (Guards the author's items 1–2.)
2. **Card stays through load and after render** — enter Preview with
   nothing loaded (empty card visible), click "Load current print",
   watch the card through download/parse/render, then go HANDS-OFF:
   assert the card is still visible 30 s after the render settles.
   Screenshots: before, mid-load, settled. (Guards item 4.)
3. **M117 in the Print-job section** — simulator pushes
   `display_status.message = "meow"`; assert the text appears in the
   Print job slot. (Guards the M117 item.)
4. **Camera first load** — a keyless LAN camera on the simulator's
   MJPEG endpoint renders on the FIRST load of the Monitor, no
   refresh click. Variants: keyless direct, auth-required bridged.
   (Guards the camera item.)
5. **Temperatures at print start** — connect in websocket mode; start
   a print; assert aux temperatures render within 3 s of the
   snapshot. (Guards the 30 s temps item.)
6. **Detach on any layer selection change** — while attached, drag
   the layer slider and click the layer-step arrows: the follower
   detaches and STAYS detached until the Attach button. Variants:
   view-swap away and back re-attaches (the ruling's exception).
   (Guards the detach saga.)
7. **Transport handover** — reopen the Monitor repeatedly; assert the
   Cura log shows no `Operation canceled` warnings and no layout
   polish loops from the plugin's QML. (Guards the log-noise items.)
8. **Dwell watchdog** — while the simulator streams at normal
   cadence, instrument the UI thread: measure frame/render intervals
   over a 10-minute soak with the console expanded; assert no
   multi-second stalls. The simulator's slow-endpoint injection arm
   reproduces the dwell and the test proves the plugin degrades
   (HTTP fallback) instead of hanging the UI.
9. **Pause list verified-only** — schedule an end-of-layer pause;
   drive the printer past the layer WITHOUT pausing; assert the entry
   stays listed, restyled as missed. (Guards the verified-pause
   ruling.)
10. **Restart arming / e-stop latch** — print → error/cancel; assert
    the e-stop latch arms and a demonstrably fresh print clears it.
11. **Scroll-to-prompt** — flood the console; assert the view follows
    to the prompt on send. (Guards the console item.)

### Tier 2 — the full functional surface, end-to-end

Grouped by surface. Every feature the plugin ships gets a scenario;
variants that carry distinct behaviour get named variants. The same
proof standard applies: real clicks, real assertions, screenshots.

**A. Transport & connection** — WS connect with the dot going green
only after the first accepted snapshot; HTTP-mode full feed; mode
switch mid-session (rebind, documented upload abort); silence proof →
HTTP fallback with its reason; subscribe refusal → HTTP fallback;
rejected API key (401) → clear verdict, no crash; `notify_klippy_ready`
→ instant re-subscribe, `notify_klippy_disconnected` → honest failure;
Reconnect cycles the client; while DISCONNECTED every Monitor control
disables.

**B. Monitor — printer status** — every print_stats state renders
(idle/printing/paused/complete/cancelled/error); progress, layer,
position and elapsed track the simulator's virtual_sdcard; filament
used/remaining; the Last-action row; M117 (Tier 1 #3).

**C. Monitor — temperatures, fans, sensors** — heater targets and
percent; fan controls; sensor hide/show and the mini chart; filament
sensor runout states; endstops after a homing.

**D. Console** — send a command and see the response; error lines red
and the collapsed bell rings; Clear; scroll-to-prompt (Tier 1 #11);
recall history up/down; the store backfills once per session, never
twice.

**E. Camera** — first load (Tier 1 #4); camera selector switches
streams; rotation/flip persist; stream failure → recovering wash →
recovered; offline state.

**F. File manager & print start** — browse dirs with thumbnails;
upload a file and see it listed; delete with confirm; print from file
through the confirm dialog to the success verdict (no false failure on
a cold-start error blip — Tier 1 #1's second half); the start
watchdog's honest failure on a start that never transitions, with the
printer's message; recents.

**G. Controls** — the jog pad (every axis and distance) reaches the
simulator as real `G1` moves; Home XYZ / QGL / calibrate mesh / load
and clear saved mesh; abs/rel toggle and its click gating while
locked; extrude/retract with distances and speeds; macros run, and
refuse while printing; pause/resume/cancel during a print; the
emergency stop's latch and reconnect-after-emergency; the controls
lock toggle disables everything; power devices on/off; the bed-mesh
popover.

**H. Preview** — the card in every state (Tier 1 #2); load current
print end-to-end with the progress phases; attach/detach and the
follower driving layer/view; detach rules (Tier 1 #6); scene
change/slicing/load detach; pause-at-layer schedule/remove/clear-all
and the verified-pause list (Tier 1 #9); bed-mesh show/hide with
legend; the current-layer slot, the ETA and the drift-learning opt-in;
the `</>` alignment visual check (backlogged, but capturable).

**I. Settings & persistence** — transport-mode radios with the reason
line and Test-connection; cadence sliders bounded (250 ms floor) and
persisted; camera config persisted; the ETA checkbox and its tooltip;
a full relaunch of Cura keeps every preference applied.

**J. Soaks & faults** — the dwell soak (Tier 1 #8); a slow endpoint →
degradation, not a hang; dropped push frames and a socket close →
recovery without data loss; a long simulated print (virtual_sdcard
progress → layer advance → ETA) run to completion.

## 4. Determinism and flake policy

- Fixed window geometry, software GL, pinned fonts, no WM. Any
  environment variable the plugin reads is set explicitly.
- `wait(condition)` everywhere; the condition polls the real QML
  object state. A step that needs wall-clock time declares its wait
  in the scenario, never an implicit sleep.
- A flaky scenario is a FAILING scenario until fixed — no retry
  allowances. The 4.0.0 live rounds proved timing races are exactly
  what this suite must catch, so masking them with retries defeats
  the gate.

## 5. Phasing (each phase ends with screenshots shown to the author)

- **Phase A — the skeleton proof:** boot real Cura under Xvfb, click
  PREPARE → PREVIEW → MONITOR through the real UI, screenshot each
  stage. Nothing about the printer yet. This phase proves the
  honesty contract (real app, real events, real captures) before any
  scenario work.
- **Phase B — the simulator:** the websocket + HTTP double with the
  scenario state machine; the production client connects to it
  in-process and the Monitor shows scripted data. Proof: a gallery of
  the Monitor rendering simulator-driven state.
- **Phase C — Tier 1 first (the author, 2026-09-11):** the recent
  failures prove the theory and the process — Tier 1 green with
  galleries before anything else grows.
- **Phase D — the full surface:** the Tier 2 catalogue lands
  feature-group by feature-group (transport, status, temperatures,
  console, camera, files, controls, preview, settings, soaks) until
  the suite encompasses all testing end-to-end — "I'm fed up of
  having to re-test everything myself."
- **Phase E — the version swap and CI:** run the suite under a second
  Cura version to prove the swap mechanism; wire `make ui_test` into
  the release gates so fix claims are machine-proven.

## 6. Boundaries

- Production-code surface (the author's constraint, 2026-09-11): the
  harness adds NO production behaviour. The only plugin-side changes
  are objectNames on the interactive QML items the scenarios address
  (inert in production) and the transition logging already added for
  the 4.0.0 diagnostics. The driver, the simulator, the runner and
  the scenarios all live under `tests/` and never ship.
- Cura's own SimulationView internals (its sliders' hit areas, its
  upload pane) are driven by events at known coordinates, never by
  poking Cura-private state. We test OUR surface; Cura's widgets are
  black boxes we click.
- The harness never ships: the package-parity gate keeps it out of
  `.curapackage` and the marketplace source.
- The simulator never touches a real printer; its auth mode uses
  throwaway keys.
