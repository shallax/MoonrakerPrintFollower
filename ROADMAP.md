# Moonraker Print Follower roadmap

This is the direction of travel, not an implementation contract. `ARCHITECTURE.md`
stays the binding description of how the code is organised today; this file says
what the releases ahead aim to deliver and why they are ordered the way they are.
Version numbers and the release checklist live in `INSTRUCTIONS.md`. Items here
are proposals — each becomes binding only when its release branch exists.

Current release: **3.5.1** (the stability patch over 3.5.0: the no-reflow
rule and the disconnected state, shipped from the `jog-reflow` branch).

## Direction

The 3.2.0 debt payoff made structural change cheap again. The next stretch has one
theme: close the gap to Mainsail on the Monitor tab, then put physical printer
position to work in the Preview, where no web dashboard can go. Socket transport
waits until the feature surface actually needs it.

## 3.4.0 — Toolhead control

Shipped in 3.4.0: a **Toolhead** section on the Monitor tab with an X/Y/Z
compass, distance presets from 0.1 to 125 mm plus free-text entry, per-axis
home and home-all, motors off, centre/Z-to-0 parking moves, KlipperScreen-
style extrusion controls and a homed-axes/move-mode/position readout. Moves
run while idle or paused; while printing, a jog request pauses first and
queued moves run only on the paused confirmation — never a force-move
mid-print, and never a move past the axis limits. Pure `ToolheadPolicy`
(scripts, safety gate, queue coalescing) plus the `ToolheadController`
queue owner; no transport changes.

The release also delivered the Monitor layout overhaul: three panes
(**Information**, **Printer status**, **Printer controls**) with
Cura-style collapsible accordion sections that persist across restarts,
collapsible panes with rotated titles, the lock-all padlock, the
full-width emergency-stop dock and the labelled readout columns —
procedures for extending all of it are in `INSTRUCTIONS.md`.

Original scope notes, all delivered:
- The follower already tolerates this: the refinement distance guard holds
  the Preview head when the physical head leaves the gcode path, and
  Klipper returns to the exact print position on resume.
- Transport and polling already exist (`printer/gcode/script`,
  `gcode_move`); this release is policy and UI.

## 3.5.0 — Monitor awareness

The informational half of Mainsail parity, plus the better ETA. The
Information pane becomes the at-a-glance pane (permanent, collapsible
sections); the camera column becomes live feed + action (console below
the stream).

- Temperature charts in the Information pane: ring buffers of the
  existing 1 s auxiliary poll feeding a small dependency-free QML chart
  component. 1 s matches Mainsail's resolution; no transport change.
  The Information pane's widgets share one interaction model: a small
  glanceable widget in the pane, and a click-to-enlarge pop-over for
  perusal and interaction (one shared pop-over shell, two contents).
  Chart spec (the author's rulings): a 30-minute rolling window;
  actuals are solid lines in a customisable, persisted colour per
  sensor; targets are translucent BANDS beneath the actuals whose top
  edge is the setpoint marker (dashes were rejected — the actual line
  chops them at steady state, and a <=0.1-alpha band beneath everything
  cannot occlude); heater power is a translucent 0-100% area on a
  second Y axis; everything toggleable from a Mainsail-style legend
  (sensor visibility, setpoints, power). Hovering shows a vertical
  cursor snapped to the 1 s samples with a clock and per-series
  readouts. The mini widget shows the primary sensors (extruders, bed,
  chamber heater) honouring the legend's visibility.
- Bed-mesh mini map, also permanent in the Information pane; the
  existing full-size pop-over becomes the click-to-enlarge detail view
  (dense meshes and exact values still need the big canvas), gaining a
  crosshair that snaps to probe points with coordinate and Z-offset
  readouts.
- G-code console below the webcam (a collapsing pane, so the camera
  grows while it is collapsed): send arbitrary commands with
  scrollable history, up/down recall and a per-printer persisted
  history. Sends return Klipper's execution verdict, and Klipper's
  output streams back over HTTP from the gcode store, polled only
  while the console is expanded — a real terminal feed. Deliberately
  unrestricted: typing a command is intent, so there is no
  command-safety table.
- Endstops readouts: the live pin states come from a one-shot
  `printer/query_endstops/status` poll on a slow cadence (they are not
  part of the objects query), with an explicit not-homed-yet state.
- Improved Monitor ETA: layer-anchored remaining time from the index's
  per-layer timing scaled by the observed speed ratio — the same
  information the Preview ETA already uses, instead of Moonraker's
  global estimate alone. Falls back to the current blend when the index
  is unavailable.

## 3.6.0 — File manager and the small controls

**Safety first — the jog reflow bug (the author's live report, 2026-09-09):**
while hammering a toolhead move with some Printer-controls sections open,
other controls can disappear/reappear and REFLOW the whole pane — the nudge
button under the pointer can move mid-click. Incredibly dangerous during
nudges; the pane's content must stop shifting under the jog pad.

**The author's rule (2026-09-10, verbatim):** "no controls disappear,
ever. It's only disablement/ enablement. The rule basically: nothing
should ever, EVER cause the UI to reflow unless it's explicitly done by
the user (expanding/ collapsing sections, resizing things, etc)."

Shipped as 3.5.1 (the patch release, live-tested by the author through
the snapshot loop): every state-gated control (pause/resume/cancel,
Exclude, the whole Configuration-changes section, the Preview
follow/bed-mesh/pause-at-layer buttons, the console Clear button, the
jog feedback row) renders permanently and DISABLES; state-dependent
status lines are permanent single-line slots whose text changes;
reserved space uses opacity (Improve-ETA row and bar, the ETA glyph,
the 90 px mesh-map slot, the layer-progress row, the endstop readout
moved below the jog pad). Capability-static gates (QGL, mesh
calibration, the mesh profile selector) keep `visible:` on the UX
panel's adjudication — they only change on printer switches, which are
user-initiated. The night's second ruling — disconnected disables every
Monitor control (the emergency stop included) while the console stays
readable — shipped with it, and both rules now live in
`INSTRUCTIONS.md` and `ARCHITECTURE.md`.

**Console sizing (the author's direction):** the console's only expanded
size is ~28% of the column — a drag handle to resize the pane lands with
this release, shaped by the new pro-user persona (a 3D-printer
enthusiast lens on feature value joins the review panel from this
release).

**Layer numbering from the file's own comments:** honour explicit
`;LAYER:n`-style comments for the layer index, demoting the Z-rise
heuristic to a comment-less fallback (the author's ruling: a future fix
— a resumed print showed layers 305..330 in Cura against the monitor's
25).

The last large Mainsail parity piece: browse remote gcode, print and delete.
`RemoteFileService` and its `FileLease` lifetime model already provide the
safe download path; this release adds listing UI and the print/delete
command surface on top. Print-from-file is as powerful as the console (it
IS the console path plus print start): it needs the same ready-retry,
power-device gating and state-expectation machinery as UploadController,
delete of the currently-printing file must surface Moonraker's refusal
cleanly, and starting a print must trigger the existing job-observation →
load flow (stated so the wiring is tested).

Small controls the domain panel ranked as the real Mainsail gaps:

- Live M220 speed / M221 flow sliders during printing, wired into the ETA
  as feed-forward (an empirical-only ETA misstates for minutes after a
  speed change).
- Z-babystepping (SET_GCODE_OFFSET Z±0.01…0.05 MOVE=1) — first-layer
  tuning is the most common live-print adjustment; reuses the
  homed-axes-gated offset code.
- Firmware-restart / restart actions with double-confirm beside the
  emergency stop (after printer.cfg edits the user restarts from the
  printer today).
- Per-printer opt-in "auto-improve monitor ETA when a print starts" —
  the no-silent-downloads ruling is respected by the explicit toggle.
- Webcam liveness watchdog: an HTTP probe on the stream URL → "offline"
  badge + auto-switch to the next configured camera (mjpeg-streamer dies
  silently in the field).

## 3.7.0 — Physical head in the Preview

What a web dashboard cannot do: show the real machine inside the slice.

- A live physical-position marker overlaid on the Preview scene. Moonraker
  reports `absolute_position` even while jogging, so the marker follows the
  real head around the build plate independently of the path follower.
  The hard cases are pause-park, start-gcode parking and homing, not
  jogging: the marker must hide/fade when not printing/paused, when Z is
  above the model with no extrusion (reuse the LayerResolver's extrusion
  guard), and when axes are unhomed. Klipper already reports the ACTIVE
  nozzle on multi-extruder machines, so no extra extruder math.
- A floating jog pad in the Preview panel, so the head can be moved while
  looking at the actual toolpath.
- A shared coordinate-transform module: the marker and the bed-mesh
  overlay both need the same homing_origin/axis_map conversion as
  `live_position_in_gcode_space` — name it once, use it everywhere.
- Macro surfacing from `configfile.settings` `[gcode_macro]` sections:
  names, descriptions and bodies are introspectable over HTTP; execution
  stays gcode/script with a confirmation and a hard gate — never while a
  print is active unless whitelisted (PRINT_START mid-print is a real
  hazard); parameters cannot be introspected, so a raw param string
  passes through like Mainsail.

## 4.0.0 — WebSockets as a transport swap

The socket remains the right long-term transport, but the domain panel
re-sequenced the plan on two facts:

- The console's echo no longer needs it: 3.5.0 ships the gcode-store feed
  over HTTP, and `notify_gcode_response` cannot do per-command attribution
  anyway (a broadcast with no correlation ids, doubled by every other
  connected client). The "first socket consumer" premise is retired.
- `notify_proc_stat_update` is HOST stats (cpu/memory/network), not
  printer data — the old "push chart samples" story conflated it with
  `notify_status_update` deltas of the heater/temperature objects. And
  heater readings update at Klippy's MCU sampling cadence, which is not
  faster than the 1 s aux poll: the socket buys event edges and lower
  polling load, not chart resolution. "High-rate data" is not a promise
  this roadmap makes.

The honest socket drivers are: immediate error/response streaming,
state-edge confirmation for tracked commands (reframed as "confirm via a
print_stats state subscription, HTTP poll as the post-reconnect
state-recovery path — notifications are diffs with no replay"), and lower
polling load. HTTP stays for uploads/downloads regardless — dual transport
is the destination, not a transitional wart. Reconnect semantics (re-identify,
re-subscribe, no replay) are pre-designed into the session-invalidation
machinery before the work starts.

Cross-cutting workstreams (land in whichever release touches their code
first): a version-drift capability gate (Moonraker has moved webcams,
history and notify names between minors — promote the existing
objects/list + server/info probes into feature flags before any socket
code), and the layer-hardening pack: full continuous-Z (vase) support and
the per-layer-heights rewrite WITH foreign-heights job gating as one work
item (the exact-match path is dead code on real Cura 5.x today; activating
it without the gate would claim wrong layers for the load-A-while-B-prints
workflow). A fixture-driven resolver test corpus from real trace-layer logs
(vase, multi-extruder, mesh-less, macro-less) backs the next
resolver-touching release. Far-future notes: full i18n via community
catalogs (the locale-driven spelling variants in 3.5.0 are the seed), and
per-series marker styles for deuteranopia (the palette already passes WCAG
contrast; pairwise hue separation is the residual debt).

## Explicitly out of scope

- **Multi-instance Monitor** — Cura's paradigm is one active printer at a
  time; per-printer Monitor instances do not fit.
- **Printer.cfg editing** — Cura machines are configured in Cura; a config
  editor belongs to Mainsail, not this plugin.
- **Per-command correlated console over the websocket** — no correlation
  ids exist in notify_gcode_response; the gcode-store feed is the
  attribution-free answer (Mainsail's own model).
- **Socket-only transport** — notifications have no replay; the HTTP
  objects query is the state-recovery path and upload/download is HTTP.
- **A Pi/system-health panel from notify_proc_stat_update** — host stats,
  not printer state; Mainsail/Fluidd already own that surface.
- **High-rate chart push (10 Hz+)** — MCU temperature sampling caps the
  source; the 1 s aux poll matches Mainsail's resolution.
- **Firmware-update management (Moonraker update_manager)** — dangerous,
  off-brand for a slicer plugin, and per-machine update state is a
  support sink.
- **Thumbnail galleries / folder trees in the file manager** — Cura's
  value is loading real G-code, not browsing it; deep management belongs
  in Mainsail.
- **Spoolman / filament inventory, job queues, OctoPrint-plugin API
  parity** — no Cura-side payoff; ecosystem features with their own
  frontends.
- **Network discovery of Moonraker instances** — a security surface for
  near-zero value; users configure one URL per machine.

## 3.6.0 candidate notes (for the author's review — not built)

Feature-shaped ideas surfaced during the no-reflow night, held back per
"improvements only — no new features; make a note for the roadmap":

- **Pause-list semantics**: the Preview card's scheduled-pause list
  collapses when a pause is consumed — including SILENT consumption
  (failed pause, layer skipped while a pause is in flight). A
  semantic ruling on when the list may collapse is needed before any
  further treatment.
- **Console resize** (already ruled): the drag handle for the console
  pane, shaped by the pro-user persona.
- **Scroll-to-prompt after a send** (deferred R5-10 #3): action-
  initiated follow after sending a command while scrolled up.
- **Pro-user persona round**: feature-value pass over the Monitor for
  3.6.0 planning (the author's ruling — joins from this release).
