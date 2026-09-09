# Moonraker Print Follower roadmap

This is the direction of travel, not an implementation contract. `ARCHITECTURE.md`
stays the binding description of how the code is organised today; this file says
what the releases ahead aim to deliver and why they are ordered the way they are.
Version numbers and the release checklist live in `INSTRUCTIONS.md`. Items here
are proposals — each becomes binding only when its release branch exists.

Current release: **3.5.0** (Monitor awareness: temperature charts, bed-mesh
mini map, write-only console, endstops and the layer-anchored ETA).

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
- G-code console below the webcam: send arbitrary commands with
  scrollable history, up/down recall and a per-printer persisted
  history. Write-only by protocol — HTTP acknowledgement means queued
  at the Klipper boundary, never executed, and Klipper's replies are
  websocket-only (4.0.0 debt); the UI says exactly that. Deliberately
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

## 3.6.0 — File manager

The last large Mainsail parity piece: browse remote gcode, print and delete.
`RemoteFileService` and its `FileLease` lifetime model already provide the
safe download path; this release adds listing UI and the print/delete
command surface on top.

## 3.7.0 — Physical head in the Preview

What a web dashboard cannot do: show the real machine inside the slice.

- A live physical-position marker overlaid on the Preview scene. Moonraker
  reports `absolute_position` even while jogging, so the marker follows the
  real head around the build plate independently of the path follower.
- A floating jog pad in the Preview panel, so the head can be moved while
  looking at the actual toolpath.
- The bed-mesh presenter has already solved scene↔machine coordinate
  mapping, so the transform precedent exists.

## 4.0.0 — WebSockets, eventually

Still the right long-term foundation for high-rate data, but nothing before
this needs it: the poller already matches Mainsail's chart resolution, and
250 ms core polling has proven adequate for path following. Taking on the
socket migration before the parity surface exists would be paying a large
cost for no user-visible gain.

Known gaps the socket work should close: the 3.5.0 console is outbound-only
over HTTP — Klipper's script replies and errors (`notify_gcode_response`)
are websocket-only, so the console shows "sent" and cannot echo command
output or failures. An inbound response stream appended to the console
history is the natural first socket consumer. Also carried from the 3.5.0
panel (P21): push chart samples asynchronously at a steady cadence instead
of per aux-reply bursts, and build the coherent command/state push story
that lets tracked commands drop their polling confirmations.

## Explicitly out of scope

- **Multi-instance Monitor** — Cura's paradigm is one active printer at a
  time; per-printer Monitor instances do not fit.
- **Printer.cfg editing** — Cura machines are configured in Cura; a config
  editor belongs to Mainsail, not this plugin.
