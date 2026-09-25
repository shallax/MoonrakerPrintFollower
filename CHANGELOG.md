# Changelog

Moonraker Print Follower is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`).

## 4.6.0

Version 4.6.0 is the plate release: the Monitor now draws your build
plate — every object where the slicer defined it — so a failure
mid-print points at a place on the plate instead of a name in a list.
The print follower lands beside it, drawing the print's own layers on
the same plate, and the prepared geometry gains a per-print home on
disk.

- **The plate map.** The object polygons Moonraker reports for the
  printer's `exclude_object` status, drawn to scale on the bed:
  included objects in the text colour, the object printing right now
  in the theme's accent, excluded objects red, and the objects the
  print has already visited green while the print's index is
  available. An object whose definition carries no usable polygon
  degrades to a centre point with a generous hit radius instead of
  vanishing. The section sits in the Information pane as a glance
  whose slot is always reserved — the plate arriving mid-print never
  reflows the pane — and a click on it opens the picker pop-over.
- **Triple-click excludes, and restores.** The gesture is the
  confirmation: excluding an object asks no question, and the old
  per-name action and its dialog are gone. The pop-over's permanent
  line counts the clicks ("Click again to exclude (2 of 3)") and
  names the direction while a command is in flight, and the outcome —
  a refusal included — lands in the status line rather than silently.
  A restore sends Klipper's own name-scoped reset
  (`EXCLUDE_OBJECT RESET=1 NAME=…`), never a bare reset that would
  clear the whole plate, and a second gesture on the same object
  while one is in flight says so instead of doubling the command.
- **The object list becomes the map.** The old list of names is
  removed: the picker is the control surface, and hovering an object
  reads its name and state ("part_A — current"). The hover readout,
  the click counter and the gesture hint share one permanent line, so
  the map never reflows under the pointer.
- **The print follower.** A second Information-pane section draws
  what the printer is actually doing: the previous and next layers
  ghosted, the current layer's printed portion filling in at the poll
  cadence, and the live toolhead on the plate. Feature colours come
  from the motion index's own per-motion types — walls, skin, infill
  and the rest, with a key under the map — and travel moves are one
  switch, off by default.
- **The follower is a tool, not a picture.** Zoom and pan the plate;
  the Layer slider seeks any layer and the seek is itself the detach;
  the Layer-progress slider plays the frozen layer through by hand;
  the line thickness scales from 0.5× to 2×; **Jump to toolhead** and
  **Keep toolhead centred** hold the live position at a zoom; and
  Detach/Attach is explicit. The view re-rasters once the interaction
  settles instead of on every tick.
- **The follower can schedule a pause.** Slide the pop-over's Layer
  slider to a layer and the button at the foot of the schedule offers
  the END of that layer: one press schedules it, and the same button
  becomes its removal. Beside it sits the schedule itself — every
  pause with its own ETA ("in 31m · ≈14:32"), a row's ✕ cancelling
  that one pause and **Clear** cancelling the rest of what you
  scheduled, with the countdown following the print as it runs. A
  pause the printer has already taken dims to "passed"; one whose
  moment went by untaken stays listed as "pause not taken" rather
  than vanishing, and a layer already printed or the print's final
  layer has nothing to offer. The plugin fires the pauses you
  schedule here, sending Klipper's `PAUSE` as the print crosses the
  layer, so the follower must be watching the print for one to land.
  A pause the gcode itself carries is listed too, marked "baked" and
  read-only — that one belongs to the slicer and cannot be cancelled
  from the list.
- **The follower tracks the print's real position.** The live layer's
  motion arrays now hydrate for every layer the follower serves — a
  cache-served layer used to keep them empty forever, and the live
  progress fell back to a byte-count estimate that drifted, stalled,
  jumped and overshot. The stall-and-snap cycles are gone: the fill
  tracks the toolhead on the real geometry.
- **The follower follows on cadences, not per poll.** While attached,
  the warm navigation raster bakes at most once every three seconds —
  stamped when the bake starts, so a slow render can never starve the
  schedule — and the printed prefix checkpoints every five seconds
  while the QML paints the tail between them. Attaching no longer
  re-renders the plate on every update.
- **Panning freezes the picture, not the toolhead.** While a drag is
  held, new lines stop painting and the view stays on the warm
  raster; the toolhead dot keeps moving — the one exception.
  Releasing the drag is the single resume trigger: the picture catches
  up with the print and the raster updates return. The lines printed
  since the raster's last bake ride the pan as a carried tail painted
  at the raster's own resolution, so the pan starts on the first
  movement with the complete current picture — no snap-back, no
  dead-start — and two quick pans in a row can no longer wedge the
  drags.
- **The prepared geometry gets its own home.** Each printer's
  prepared layers and G-code index share one folder per print under
  that machine's own cache namespace: a print reopened from the
  picker skips the whole preparation walk, a clean close checkpoints
  the part-prepared work for the next session, and eviction drops
  whole prints, least-recently-used first. The Diagnostics tab
  carries the per-printer size limit (512 MiB by default, 16-4096)
  beside the clear-cache button, which still clears every printer.
- **The preparation pass shows its progress.** The Print-job bar
  carries a teal sweep for the background optimisation's share,
  and it vanishes when the pass completes.
- **The background pass stops holding the interface.** The G-code
  parse and the preparation workers hand the interpreter back on a
  wall-clock gate instead of a line count, so a dense file no longer
  starves the UI thread for the length of the pass — what a frozen
  window measures is the time between hand-backs, and that no longer
  grows with the layer. A layer the follower demands is written to the
  prepared store by the worker that encoded it rather than by the
  interface thread, and a running pass publishes its progress at the
  rate the bar reads while the full refresh is kept for the phase, the
  completion and the failures.
- **The temperature chart's own clock.** The chart samples on a fixed
  one-second cadence instead of following the auxiliary delivery
  slider, whose fast settings used to cut the advertised 30-minute
  window down to minutes. A slower delivery holds the last value as a
  truthful step, the feed pauses while disconnected so a reconnect
  re-arms the window, and the Temps readouts still follow the slider —
  the chart can read fresher than the numbers above it.
- **Fixes:** rapid X/Y jog taps can no longer overshoot their maxima
  (the projections are per axis now), the settings dialog's sliders
  grab the handle from either side and keep the keyboard while a save
  applies, and zooming the follower no longer freezes its updates
  after the zoom settles — a wheel-only gesture had no release to
  resume the publications with.
- **Known limitations:** on macOS, a layer-boundary advance can show
  one bare frame in the plate interior — the frame where the retained
  prefix's own rule stands down because the canvas's texture has
  committed while its painted coverage is still zero. Documented and
  pinned by the gap census, not fixed: a fix needs the scene's
  committed frame, not a flag.

## 4.5.0

Version 4.5.0 is the persistence release: the plugin's settings leave
Cura's preference file for one MoonrakerPrintFollower folder beside
it, migrated automatically on the first start after the upgrade. The
release also swaps the webcam stream onto a plugin-owned renderer and
lands the performance pass across the Monitor page.

- **The persistence folder.** `MoonrakerPrintFollower/` holds
  `settings.json` (the connection, following, upload, camera and
  diagnostics configuration per printer), `state.json` (the pane
  layout and UI chrome) and one small `machines/` file per printer
  (the console transcript and history). Everything is pretty-printed
  and sorted, pleasant to read in an editor, and a failed migration
  flags itself: the dialog carries a notice with a rollback recipe
  and Cura's configuration is backed up to `cura.cfg.<timestamp>`
  before anything is removed.
- **The Status pane's Position row** reads in the axis colours,
  matching the collapsed readout and the Toolhead section.
- **Dark mode:** the emergency-stop label's idle text follows the
  theme, readable on the button's dark ground.
- **The camera cold start.** The webcam stream now comes up in
  milliseconds on the Monitor page: a refresh no longer restarts a
  connecting websocket (the startup aborted its own handshake), the
  first camera discovery applies the stream exactly once instead of
  twice, and the discovery retries coalesce.
- **The camera stream's own engine.** The webcam renders through a
  plugin-owned MJPEG renderer instead of Cura's built-in loader: a
  healthy stream stays connected indefinitely (the old loader
  restarted the stream whenever its buffer crossed 2 MB — about once
  a second on a 1080p camera), frames pace to a 30 fps ceiling with
  the newest frame winning, and memory stays bounded. A small chip
  overlays the live image with the stream's resolution and recent
  bandwidth, and the camera selector now sits above the image on the
  title row. A camera re-publish that changed only its URL's query
  no longer restarts the stream, and the camera timing trace no
  longer re-marks the periodic webcam poll.
- **The webcam keeps its room.** As the Monitor window narrows, the
  Information, Printer status and Printer controls panes fold
  themselves so the webcam pane never shrinks below its readable
  minimum — and expand themselves again when the window widens. The
  webcam pane itself never collapses.
- **The temperature chart scales.** The mini chart's payload is now a
  bounded reduction — a fixed budget of render points per series that
  keeps every spike — instead of the whole accumulated window; the
  full pop-over chart stays dormant until it is opened (and returns
  to dormancy when closed); the legends read live values from a tiny
  per-sensor projection instead of searching the payload; and the
  chart paints off Cura's main thread, with the hover cursor and
  markers rebuilt as lightweight scene-graph items that never
  repaint the chart. A full 30 minutes of history no longer weighs
  on the Monitor page.
- **The performance pass.** One monitor publish per heartbeat instead
  of three or four; no preference-file or state-file reads on the
  heartbeat (the config, the migration record and the console
  transcript hydrate once and cache); and the console's server
  chatter debounces into one write.
- **A straighter Z floor.** Stale telemetry can no longer re-arm the
  safety projection between a jog's dispatch and its reflection —
  the promised 0.00 floor stays promised.
- **The camera key stays home.** The Moonraker API key never rides to
  a foreign webcam origin, and a deposed camera retires its bridge.
- **The Cura floor.** Cura 5.7 / SDK 8.7 through Cura 5.13 / SDK
  8.12 is now the supported range — every minor in between verified
  end to end by the version sweep — with 5.11's preview limitations
  documented in the README, and the console's input row and Send
  button fixed on 5.11 and 5.12.
- **Fixes:** the console's notes no longer persist across restarts
  (they are session-transient by design), the status column's live
  values never wrap (the per-second polish-loop warning), the
  collapsed status strip's ETA readout no longer wraps, an empty
  Moonraker history no longer permanently refuses a file's metadata,
  the emergency label's colour answers the theme gate, and the
  chart's power-axis labels moved inside the plot's edge (their last
  glyph clipped at the pop-up's margin).

## 4.4.0

Version 4.4.0 is the configurable-sections release: every pane's
collapsed strip is a real readout, the sections are hideable and
reorderable, the next scheduled pause is visible ahead of time, and
the G-code index builds in one pass.

- **Configurable sections.** Each pane's configure popover lists
  its sections with a shared all/none selector, per-row toggles,
  drag handles and a reset-to-defaults label. The layout persists
  per pane — one pane's reset never touches another's.
- **Collapsed readouts.** The collapsed panes carry live readouts:
  the information strip shows the printer's hotend and bed
  temperatures; the status strip leads with the ETA and finish
  clock, the current/total layer count, one stacked progress bar
  (print on the bottom, layer on the top, touching) and the flow
  rate; the controls strip shows the X/Y/Z position in their axis
  colours and the Z offset. Unavailable values hide their glyphs
  whole, and groups de-render before the panes clip them.
- **The next pause.** A Next pause row under Finish gives the
  countdown and the wall-clock deadline (marked "(baked)" when the
  pause comes from the gcode), and both stacked bars gain a third
  fill in the mesh's neon orange — the print's progress toward the
  pause in time, measured from the last pause (the print's start
  before any), climbing smoothly and never exceeding 100% even when
  the ETA slips.
- **The theme document.** The plugin's colours — the axis identity
  colours, the neon orange, the console palette and the rest — live
  in one theme singleton instead of repeated hex literals.
- **The preview card.** The layer readout shows current/total, the
  strip gains the current height, the hourglass glyph mirrors the
  Monitor's improve-Eta action, the attach and pause controls hide
  without a toolpath (and the follower detaches for real when the
  toolpath goes away), the baked pauses appear through the light
  ETA-improve download alone, and every strip row carries a
  tooltip.
- **One-pass indexing.** The G-code index builds in a single pass
  (layers, marker values, block stats, motions and pause offsets
  together), the download requests identity encoding so the
  download and index phases read a real byte percentage, and the
  finish-time estimates use ≈ instead of ~.
- **The toolhead readout.** The Position row shows the three axes
  as fixed cells in their axis colours, and the absolute/relative
  toggle sits on its own Moves row underneath.
- **The load watchdog.** A Cura parse that runs long no longer
  trips the plugin's confirmation bound — it now waits five minutes
  before releasing a load Cura silently refused.

## 4.3.0

Version 4.3.0 is the QML componentisation release: every
collapsible section of the Monitor page is its own component, and
the Preview card gains a status strip with a single pause policy
behind it.

- **Every section its own component.** The Monitor page's panes
  are now thin shells. All twenty-two collapsible sections ride
  their own property-driven QML components — thirteen in the
  controls dashboard (Print, Setup, Toolhead, Macros, Profiles,
  Live tuning, Fans, LEDs, PWM, Power, System, Configuration
  changes, File manager) and nine in the monitor (Bed mesh,
  Temperature history, Print job, Temperatures, Fans, Filament
  sensors, Objects, System, MCUs). Each reads a printerModel
  property; the hosts keep the panes, the dialogs, the pop-over
  and the emergency dock. The slider freeze/refocus machinery
  stays single-owner on the host behind one interaction sink, and
  the pop-over and confirmation dialogs are requested through
  signals. The console remains a pane — its auto-collapse and
  resize are structurally the host's.
- **The Preview status strip.** Two fixed rows under the card
  title: Pause/Resume — status-only, driven by the permission
  policy's rows with the reason words as the tooltip — the hotend/
  bed pair, and a middle slot carrying the print ETA or the
  policy's refusal reason. The strip is fed by per-poll preview
  blocks from the monitor's data path with an explicit staleness
  clock; a stale strip says so instead of pretending.
- **One pause policy.** `can_pause`/`can_resume` join the policy
  table: busy is a row terminator, never pause-first; the
  assumed-stopped trap consumes its error; the verdicts publish
  with the reason words the buttons carry.
- **Print-start ownership and metadata adoption.** The print-start
  arm moves into its own owner with its timeout; the coordinator
  adopts Moonraker's metadata only when the job matches, with a
  bounded give-up per key.
- **The UI-state store.** Section expansion persists through the
  state file's second consumer with atomic merge writes (O_NOFOLLOW,
  0600, no NaN); the chart's flat-map migration merges and deletes
  only what it owns.
- **The slider track-click fix.** Clicking the slider track moves
  the handle AND commits the value — a track click used to move
  the handle while silently discarding the request.
- **The caption states.** The print-job caption names Disconnected,
  Printer state unknown and Locked — never a lying "Idle" while
  the socket is down, and a locked band keeps its context.
- **Harness evidence visibility.** Every resolving step records
  the element's geometry and the walk that resolved it, and the
  harness composites the outline onto each captured frame.
- **Baked pauses in the Preview list.** Pauses baked into the
  gcode (the PauseAtHeight `PAUSE`/`M0` block after a layer's
  elapsed marker) appear in the pause list as read-only rows,
  sorted with the manual schedule. A baked layer refuses a manual
  pause on top, a passed baked row dims as "baked · passed", and
  the rows never vanish mid-print.
- **The pause list rework.** Five rows visible with a scroll cap,
  up/down affordances in the whats-new idiom, a stable in-place
  model so the scroll never jumps on the refresh poll or on
  add/remove, wall-clock ETAs ("in 12m · ~14:32"), a red ✕
  remove glyph, and confirmed manual pauses that stay listed,
  dimmed "passed", still removable.
- **The M117 fix.** Moonraker pushes only the changed fields per
  notify, and the print's progress flood overwrote the one-shot
  message fragment before the drain — M117s vanished while
  printing. Fragments now merge per object in the socket
  accumulator, and the boot chain re-arms discovery within one
  aux tick so the first message never waits out a discovery
  interval.
- **The Windows fixes.** The O_NOFOLLOW state-store guard is now
  platform-aware (every write failed there before), the socket
  disconnects the previous cycle's handlers before reconnecting
  (the multiplied upgrade/sync lines), and the settings'
  diagnostics toggle mirrors into the leak instrument's
  preference key so the probe actually arms.
- **The leak instrument.** A gated 10-second sampler behind a
  Diagnostics-page toggle: the physical footprint (the axis
  Activity Monitor shows), the live print layer, camera-stream
  gauges, per-class QML diffs and plugin collection sizes, with
  the Python allocation trace as a separate opt-in;
  cross-platform, silent while disabled.
- **Preview card polish.** The arrow temperature pair, the black
  ETA and mesh labels, the status line above the strip, the
  bed-mesh button at the card's foot, the collapsing mesh legend
  (the card reflows instead of keeping a faded gap), the duplicate
  temperature entry labelled "(host)", and stable-height loading
  and prompt overlays that keep the layout free of polish loops.
- **The monitor loading prompt.** Connected-with-no-data shows a
  centred loading note instead of the empty pane.
- **The harness log scan.** Every UI run reads the Cura log and
  fails on plugin-originated warnings, errors and polish loops —
  no scenario needed to catch them.
- **The SDK floor.** Cura 5.11 / SDK 8.11 is now the minimum —
  the package and metadata declare exactly SDK 8.11 and 8.12, and
  Cura 4.x / SDK 7.x is not supported.
- **The native nozzle repair.** The 4.2.0 nozzle lifecycle repair
  is restored: a print loaded while Preview is already open keeps
  Cura's own nozzle visible, so the live indicator stays reliable.
- **The memory work, closed out.** The camera bridge's socket and
  reply objects are deleted on completion, the G-code hydration
  cache evicts beyond its bound, and a soak of the
  native follow path shows a bounded, oscillating current-RSS band
  that ends below its start. The experimental follow render pass
  (an alternative to Cura's own SimulationPass) was removed after
  live testing — following rides Cura's native renderer exactly as
  before.
- **A refused settings save is never silent.** The dialog shows a
  red notice naming the refusal, and the log records the reason.
- **Open Browser works.** The upload completion action's handler
  was held only by a weak reference (Uranium's message signals
  store plain functions weakly) and was collected before the
  click; it is now a bound method, and failures log the target.

## 4.2.0

Version 4.2.0 is the printer-state release: three live motion
readouts join the Monitor card, and every control's permission now
comes from one policy table that says why.

- **The motion cluster.** Velocity (Klipper's scalar `live_velocity`
  magnitude), Accel limit (the effective `toolhead.max_accel` — no
  instantaneous acceleration exists in Klipper) and Flow rate
  (COMMANDED volumetric flow: `live_extruder_velocity` × π·(d/2)²,
  signed — retractions read negative, tiny cancellation artifacts
  clamp to zero). The filament diameter reads per tool from the
  typed `configfile.settings` already resident in the snapshot;
  there is deliberately no override — a wrong reading is a wrong
  printer.cfg. The multiplier row is renamed "Speed factor" so the
  live row can take Velocity. Rows read 0 at idle; "—" means a
  row's sources have not reported (no `motion_report` object, or
  the diameter not resident yet for Flow rate — the discovery lane
  is its only supplier, the websocket push never carries settings).
  The trapq history staleness — up to 30 s of print time after the
  last extrusion, not just travel moves — is labelled in the
  tooltip.
- **One permission policy.** `MonitorPermissions`: pure functions
  over a frozen observation record (assembled once in `MonitorData`,
  with the tri-state connection — unknown/yes/no — as a client-layer
  prerequisite) ruling `can_jog`, per-device `can_power`,
  `can_restart`, `can_start_print`, `can_macro`, `can_exclude`,
  `can_z_offset` and `can_set_absolute`, each denial carrying a
  concise reason. Unknown fails closed with a reason; not-homed
  stays allowed for jog and print-start; observed error allows
  recovery moves; the e-stop assumption (`assumed_stopped`) rides
  the record while the shipped guard-release behaviour stands. The
  shipped `jogEnabled` stays as a projection of `can_jog`.
- **Disabled controls say why.** The toolhead status caption is the
  policy's short form (the reason, the pause-first warning, the
  paused note) with the full sentence in the tooltip; the restart
  buttons gate on `canRestart` and carry the reason.
- **Dispatch-time revalidation.** Queued one-shots carry their
  permission rule and re-check at dispatch — a restart clicked
  while idle can no longer fire up to 30 s later against a print
  another client started; denied entries drop with the reason.
  Print-start re-checks at confirm time (the dialog button keeps an
  honest live binding); the autonomous scheduled-pause re-checks an
  observed print before posting.
- **The state store.** `StateStore` owns the Monitor's persisted
  chrome: read-modify-write merges preserve the file's foreign keys
  for 4.3.0's UI-state store, the one-time chart migration keeps its
  deliberate replace, and persistence failures report once per
  session through the console instead of vanishing. The
  identity-neutral metadata-only fetch (`request_metadata_only`)
  ships; the print-start owner's extraction and the coordinator's
  adoption of the metadata service defer to 4.3.0 (recorded in the
  roadmap).
- **The harness grew the same teeth.** The simulator is
  field-faithful (object sets and field lists honoured on all three
  lanes); the fixtures carry the motion/toolhead/configfile shapes;
  the motion ticker arm drives the rows; the policy gates are pinned
  by model assertions and the rendered `item_disabled` step.
- **The bed-mesh range filter.** A dual-ended slider on BOTH the
  Information pop-over and the Preview card, one shared window in
  the model so the two stay synchronised: handle drags narrow it,
  dragging between the handles moves the whole window, a groove
  click jumps the nearest handle, a handle click never moves it,
  and the arrow keys nudge. The bar desaturates outside the window
  and the out-of-window cells grey on both surfaces (the shared
  grey), so peaks and troughs stand out. The window follows each new
  mesh until touched, then clamps into the new range — it is never
  persisted, by design.
- **Scale z-max.** The Preview's bed-mesh exaggeration adjusts from
  0 (flat) to 1000× and is remembered between sessions; the default
  stays the historical 20×.
- **Klipper-faithful bed-mesh visuals.** The maps and the Preview
  surface extend the probed bounds with Klipper's own clamp (the
  boundary values continue — no made-up slope), the orange outline
  marks the probed bounds on every surface, hovering the extended
  area reads the clamped value with an orange crosshair, and the
  map painting pre-mixes the fainter look so cell edges never
  double-paint into a grid.
- **Read-only firmware fans.** Fans Klipper regulates itself
  (controller_fan, temperature_fan, heater_fan) render their speed
  without a slider and the command lane refuses them fail-closed.
- **Independent LED channels and brightness.** The channel sliders
  hold your set percentages (seeded once from the first-seen
  colour); the brightness slider is the gain, composed into the
  send only — nudging one never moves the other, and the labels
  hold fixed widths.
- **Eager first connection.** Until a status has ever landed, the
  idle floor and the failure ladder do not gate the tick, and the
  connect transition re-broadcasts the admitted snapshot and fires
  every lane — the printer's data appears as soon as Moonraker
  answers.
- **Slider behaviours consolidated.** The plugin's OutlineSlider
  owns every interaction path (groove, handle, keyboard) through
  two semantic signals; keyboard nudges commit like releases, the
  fan/LED/PWM repeaters freeze during gestures, and focus re-grants
  to the exact slider after the submit's rebuild. The ETA
  hourglass's rotation resets through the idle state, so the
  download glyph never inherits the frozen angle.
- **Bumped.** The gcode deformation (vertex-style) defers to 4.3.0
  with a Snapshot-0 mock first, per the ruling.

## 4.1.0

Version 4.1.0 is the deep-harness-coverage release: the real-Cura
gate's coverage and proof machinery, the parallel local matrix, and
the review-driven product repairs, each shipped with its evidence.

- **The parallel local matrix.** `tools/harness_release.sh -j N` runs
  the gate's units in per-slot containers, each with its own working
  directory (the isolation ruling) — the local full matrix takes 532
  s against the 945 s serial baseline (a measured 1.8x). The serial
  run stays the default: one container and a shared scratch tree, so
  the second unit proves the first unit's debris does not break it.
- **The evidence spine, with teeth.** Every step records its op,
  target spec, verdict, capture, duration, the delivery record
  (which item accepted the press, which was under the aim) and a
  mechanism-derived class in `evidence.json`; a capture whose size
  does not match the declared geometry fails the step; the coverage
  EXECUTION check joins the surface map to the run's evidence, so a
  mapped surface whose scenario never ran — or whose mapped control
  no step addressed — fails the gate; and a run whose evidence never
  lands at the reported path fails whatever the verdict said (the
  4.0.2 bug shipped the exact failure this closes — its galleries
  are lost; 4.1.0's gate refuses to pass that way).
- **Real input, verified delivery.** The jog pad, the home row, the
  file-manager confirms, the console and the pause/resume buttons
  ride real press/release events whose acceptance is checked against
  the target's own item chain — a disabled control or a covering
  dimmer refuses the press and the step reads it (proven live by the
  z10/z11/z15 proof scenarios). The broken-start journey runs as a
  red scenario (z14): the failure verdict must fire, recorded as the
  expected red. The remaining direct-invocation steps are counted
  and classified, and the ratchet pins keep the balance from
  sliding back.
- **The file manager no longer re-sorts on a temperature tick.** The
  file-view projection now runs once per revision set: measured on a
  staged 400-file listing, ten unchanged publishes went from 90
  pipeline evaluations to zero, and publish latency from 4.76 ms to
  0.025 ms warm (4.86 → 1.57 ms cold), with byte-identical rows.
  Binds, deletes and renames invalidate the cache, and a refused
  directory listing now surfaces the walk-error banner.
- **Upload refusals say why.** A nested refusal body (Moonraker's
  newer error shape) now unwraps into the popup's status line
  instead of a generic transport error, and the simulator's upload
  lane answers the honest body — the scenario proves both the
  accepted and the refused verdicts.
- **A one-time "What's new" popup per version.** When Cura starts
  after an update, an overlay shows what changed in the new version,
  with previous releases in collapsed sections below, a link to the
  project's home, and dismissal by Esc, a press outside the card or
  the Close button — every dismissal records the seen version, so
  the popup stays gone until the next release.
- **The testing document describes the real harness.** TESTING.md's
  claims are reconciled section by section (struck claims are marked
  with their reasons, and a doc-pin test fails if one re-enters);
  the exclusion records carry their reason, evidence, date and
  re-check trigger.

The deferred remainder — the Information-pane round, the deferred
panel scenarios, the harness-level lifecycle scenarios — is recorded
in the roadmap with its evidence and its re-check triggers.

## 4.0.2

Version 4.0.2 is a correctness release: five repairs to the transfer
and print-identity paths, each shipped with its regression tests.

- **Downloads now retire cleanly.** Each download owns its queue,
  file and writer thread; cancelling (or switching printers)
  mid-download no longer leaves the old worker able to hijack the
  next download — the failure that could freeze Cura outright — or
  interleave writes into a file (silent corruption that passed the
  size check). Memory use is bounded: when the write queue is full,
  reading pauses and resumes when the writer drains, and the GUI
  never waits for the disk writer.
- **Progress and the size cap reset per attempt**, measured against
  the server-declared length rather than the cached metadata size
  (a stale cache could refuse a good file forever).
- **The file manager's Download button is honest about its outcome**:
  a download that finishes after a printer switch never loads into
  the wrong session; failures appear in the popup's status line; and
  if Cura never confirms a load (its silent refusal paths), the
  plugin's "loading" state no longer sticks forever.
- **Uploads leak nothing and report once.** Every terminal path
  disposes the reply; a second upload of the same file while one
  runs is refused with a message; "upload and print" says what
  actually happened (started, queued, or refused — the printer can
  answer 201 while declining the print), and both upload paths show
  the printer's own refusal words.
- **The previous print's metadata can never read as the new print's.**
  The fallback payload is tied to the print that fetched it; a failed
  fetch retries properly; same-name restarts refresh within the
  throttle; and metadata carrying a print id is cross-checked against
  the printer's history before it is used.

The simulated Moonraker in the test harness now speaks the real
protocol shapes (declared lengths, 404 metadata, print identities),
so the regressions exercise real semantics.

## 4.0.1

Version 4.0.1 is the harness release: no product changes — the plugin
behaves exactly as in 4.0.0. The test infrastructure around the release
gate hardened, and the documentation was cleaned up.

- **Harness output**: progress lines through the long phases (the
  image pull, each unit's start) and a heartbeat during the boot
  wait, so a watching terminal never looks hung.
- **Failure reports open with the signal**: the boot log tail in the
  failure report filters the known-benign upstream warnings (the
  ast.Str deprecation and kin).
- **Cura's "Make sure the g-code is suitable" message** is dismissed
  conditionally at boot — never waited for.
- **CI hardening**: workflow files are linted with actionlint (an
  invalid action input once failed ci.yml with zero jobs and no log
  to read); CodeQL ignores the harness tree via its config file; the
  smoke job pulls the published harness image from GHCR with the
  run-scoped token and re-tags it to the local name.
- **Documentation**: verbatim quotes and attributions removed from the
  roadmap, testing docs, instructions and code comments — decisions
  restated impersonally. The roadmap re-cut: 4.0.0 shipped, the
  Information-pane coverage moved to 4.1.0.

## 4.0.0

Version 4.0.0 is the websocket release: the Moonraker status transport
moves from per-request HTTP polling to Moonraker's websocket
subscription, with HTTP kept as a selectable, automatic-fallback mode.

### Highlights
- **Websocket subscription transport**: Klipper pushes object updates
  once per interval and Moonraker fans them out to every subscriber —
  one serialization shared by all clients instead of one full query per
  client per poll. The plugin's hand-rolled RFC 6455 client runs on
  Cura's bundled QtNetwork (no Cura release ships a QtWebSockets
  binding). The startup proof (real frames within a few seconds) and
  a steady-state silence watchdog degrade to HTTP polling with a
  visible reason rather than a dead UI.
- **Transport choice**: a WebSocket subscription / HTTP polling toggle
  on the Connection tab (websocket is the default), per printer, with
  a permanent reason line explaining the effective mode, and the
  connected status naming the live transport ("connected over
  websocket").
- **The monitor's remaining requests ride the socket RPC lane** in
  websocket mode (power, system, endstops, discovery, webcams,
  console), falling back to HTTP eagerly whenever the socket is down.
- **Delivery-cadence sliders**: the status update interval (log-spaced,
  250 ms to ~34 min), the auxiliary status cadence and the console
  cadence (both 250 ms–60 s) — per printer, with live value labels,
  a 250 ms floor, and helper text explaining that websocket data
  arrives at the printer's cadence regardless.
- **Camera bridge**: a camera behind a header-auth proxy cannot render
  through Cura's loader (NetworkMJPGImage sends no headers), so the
  plugin fetches the stream with the API key and republishes it on a
  keyless loopback endpoint for Cura's loader. Loopback-only, with the
  same same-origin redirect discipline as the shared transport.
- **Preview behaviour**: the current-layer info label fills while
  attached and collapses when there is nothing to say; a scroll or
  slider drag detaches immediately (a continuing drag is inspection —
  no snap-back — and the auto re-attach only covers a one-off
  restoration right after an attach); the empty "Load current print"
  card appears only when nothing is loaded, not during Cura's own
  busy/idle cycles.
- **Monitor completeness**: unchanged objects (a steady temperature)
  and devices switched on mid-print now appear without a reconnect.
- **Settings polish**: a wrong API key on Test connection reads "the
  API key was rejected (HTTP 401)".
- **The 4.0.1 scope folded in (the 2026-09-11 ruling)**:
  scroll-to-prompt (a successful console send returns the view to the
  prompt), the verified-pause-only list (an entry leaves only when
  the printer is observed paused; missed pauses stay listed in the
  error colour), the auto-improve-ETA opt-in (a Following-tab
  checkbox rescaling the remaining estimate by the learned
  per-layer drift), the webcam watchdog (a dead bridged stream bumps
  the loader's URL nonce and veils as "Camera recovering…"), and
  restart arming (a demonstrably lower print duration clears the
  e-stop assumption; a new print's start transition expires the
  previous print's tracked commands).

### Fixes
- The aux subscription deadlock (the wanted set never reached the
  socket until data arrived, which could never arrive), the
  subscribe-sync seeding gap, the slow-drag echo-window absorb loop,
  the watchdog snap-back mid-drag, and the wrong-card flip during
  Cura's activity cycles — all found and fixed in the live-test round.

## 3.6.0

Version 3.6.0 is the file-manager release, closed out with the
post-review sweep and the adversarial round's fixes.

### Highlights
- **File manager popup**: the Monitor tab's Files button opens the full
  store browser — search, filters, paging, per-column sort with
  resize/show-hide/order (titles never shrink below their own text,
  cells always elide), frozen left columns over a clipped horizontal
  scroll (whole-row content width; wheel scrolls over the list),
  folder chips with a context menu, and a search breadcrumb under the
  filename so same-named files in different folders stay tellable.
- **Mutations**: print with a confirmation dialog, download, upload
  with progress and guards (the printing file and disk shortfalls
  refuse), rename with live collision checks, delete for files and
  folders, create-folder, and thumbnail previews through a coalesced,
  bounded fetch queue.
- **Dialog discipline**: every dialog's Esc cancels its payload,
  dialogs close with the popup (nothing survives painted over the
  dashboard), the upload progress supersedes its confirmation, and
  cleared payloads can never crash a surviving button.
- **Print-start watchdog**: the verdict follows the printer's state
  change, not the filename alone — same-file re-prints are watched
  properly and heat-soak never reads as a failed start.
- **Reconnect**: a Reconnect button in the Printer controls' System
  section cycles the client and re-arms the monitor (works while
  disconnected).
- **Console**: the pane resizes by dragging its divider; the error
  bell stays fed by a slow poll while collapsed; the walk-error
  banner carries a dismiss.
- **Host load**: the auxiliary objects query steps down to one per
  2.5 s while printing — a first step toward the websocket transport.


Version 3.5.1 is the stability patch for 3.5.0: the Monitor tab never
shifts under the pointer, and it behaves sensibly while the printer is
disconnected.

### Highlights
- **No reflow, ever**: no control on the Monitor tab disappears any
  more. Every state-gated control (pause/resume/cancel, Exclude, the
  whole Configuration-changes section, the Preview follow/bed-mesh/
  pause-at-layer buttons, the console Clear button, the jog feedback
  row) renders permanently and simply disables; state-dependent status
  lines are permanent single-line slots whose text changes; and
  reserved space uses opacity (the Improve-ETA row and bar, the ETA
  glyph, the 90 px mesh-map slot, the layer-progress row). Nothing the
  printer does can move a button under the pointer mid-click — the UI
  only reflows on user action (expanding/collapsing, resizing).
- **Disconnected state**: with the printer disconnected every Monitor
  control disables — including the emergency stop — while the console
  stays readable: scroll, select and copy keep working in a greyed
  well, only input and Send/Clear disable. The camera dims and
  desaturates with a "Camera offline" caption while disconnected and
  carries a "Live" badge with a red dot while the stream is live. A
  connection dot sits before the Printer status title (green/red,
  tooltip), visible while the pane is collapsed too, and the console
  feed notes each connect and disconnect with the plugin's own `#`
  lines.
- **Readout restyle**: the MCUs-style two-column pattern (grey labels,
  black values) now applies across the Printer-controls sections; the
  status lines carry short forms with the full sentence in a tooltip;
  the Endstops block has its own bold title and only endstop content;
  the homed readout puts its values beside the label; and the
  extrusion presets show their mm unit.
- **Klipper restart** button in the System section, a larger Webcam
  pane title, and the free-text extrusion distance box is gone (the
  presets cover it).
- Tooling: zero-argument gate runs (`make gates`, `make all`) use a
  fresh container again — the warm-container reuse now applies only
  when a command is given.

## 3.5.0

Version 3.5.0 is the informational half of Mainsail parity: the Monitor
tab becomes the at-a-glance view of a running printer, with temperature
history, a bed-mesh map, a console with Klipper's live output, endstop
readouts and a better remaining-time estimate.

### Highlights
- **Temperature history chart** in the Information pane: a 30-minute
  rolling window at the 1 s auxiliary cadence, solid actuals in
  per-sensor colours, translucent target bands whose top edge is the
  setpoint marker, and heater power as a translucent 0-100% area on a
  second axis — all toggleable from a legend (per-sensor visibility,
  setpoints, power) that persists per printer, with the axis labelled
  in °C (the plugin follows Cura, which has no temperature-unit
  preference). Hovering snaps a cursor
  to the samples and the clock plus per-series readouts follow the
  mouse as a floating tooltip that never resizes the chart; a compact
  mini-chart of the primary sensors (extruders, bed, chamber heater)
  sits in the pane, and a click opens the full chart.
- **Bed-mesh mini map** in the Information pane; the enlarged detail
  view gains a crosshair that snaps to probe points with coordinates
  and Z offsets.
- **G-code console** below the webcam, a collapsing pane with the
  panes' uniform chevron toggle (the camera grows while it is
  collapsed): arbitrary commands, Enter-to-send, up/down recall and a
  per-printer persisted transcript (~50 lines of commands AND output
  plus the most recent commands survive across sessions, restored
  lines greyed). Sends return Klipper's execution verdict, and
  Klipper's replies stream in from Moonraker's command store —
  polled while the console is expanded (once a second while printing,
  an idle floor otherwise) — so the pane is a real terminal feed:
  typed lines carry the terminal `>` prompt and render blue until
  saved, then the verdict in muted hues (green ok / red failed);
  responses render in the bright hues (`!!` errors in red); a client
  timeout stays honest ("no response" — a blocking command may still
  be running); and no line claims an attribution the store cannot
  support (it pairs by recency, not per command). Deliberately
  unrestricted: typing a command is intent, so there is no
  command-safety table.
- **"Last action" row** in the Print job grid: transient "sent"
  receipts for macros and setup scripts age out to "—" under the
  permanent caption, a newer action always supersedes the old banner,
  and tracked commands show their confirmed outcome.
- **Filament used/remaining readouts** under the progress block:
  Klipper's real `filament_used` against the slicer's total — parsed
  from the downloaded file's own header so multi-extruder prints read
  correctly on every Moonraker version, with an honest "—" whenever
  the numbers disagree. The rows stay visible after the print
  completes until the next job starts.
- **Endstop readouts**: live pin states from a one-shot
  `printer/query_endstops/status` poll, with an explicit
  not-homed-yet state.
- **Layer-anchored ETA**: the remaining time prefers the estimate from
  the G-code index's per-layer timing scaled by the observed speed
  ratio (routed through the one PreviewFollower owner), falling back to
  the previous blend when the print was never downloaded and indexed.
  The blend itself uses the slicer's estimate from Moonraker's file
  metadata (a server-side header parse, not a download) when the print
  was never loaded, and the layer-height readout uses the same source.
  The ETA readout shows the active basis (colour and tooltip), and a
  small download glyph beside the estimate runs the download-and-index
  flow WITHOUT loading the preview (an optimisation: the
  render is only paid when the print is loaded in the Preview, which
  then reuses the already-downloaded file).
- One shared pop-over shell for the Information pane's glanceable
  widgets; each pop-over dismisses with its Close button, Escape, or
  an outside click, and no longer disturbs the pane layout.

### Notes
- Chart history is session-scoped: a Cura restart starts the chart
  empty, and a monitor pause longer than half a minute starts a fresh
  window.
- The 1 s chart cadence applies while printing or paused; the idle
  auxiliary poll stays at 2.5 s.
- Chart colours, visibility and the console transcript (commands and
  Klipper's output) persist per printer; the pane chrome (collapsed
  sections, pane collapse, lock) stays global.
- Known issue — jog reflow (fixed in 3.5.1): with some Printer-controls
  sections open, expanding or collapsing another section mid-jog could
  reflow the controls, and the nudge button under the pointer could
  move mid-click. 3.5.1 makes every control permanent and
  disable-only, so nothing shifts under the pointer any more.

## 3.4.0

Version 3.4.0 is the first Monitor-parity release: manual control of the
physical toolhead from the Monitor tab, and a full overhaul of the Monitor
layout into Cura-style panes and collapsible sections.

### Highlights
- Adds a **Toolhead** section to the Monitor tab: an X/Y/Z compass with
  directional arrows, 0.1 / 0.5 / 1 / 5 / 10 / 25 / 50 / 100 / 125 mm
  distance presets plus free-text entry, per-axis home and home-all,
  motors off, centre-toolhead and Z-to-0 parking moves, and a readout of
  homed axes, absolute or relative move mode and the live position.
- KlipperScreen-style extrusion controls: separate distance presets
  (5 / 10 / 15 / 25 / 75 / 100 mm) and speed presets (1 / 2 / 5 / 25 mm/s).
- Jogs are clamped to the axis limits and can never cross the axis
  minimum — a move that would end negative is forbidden outright.
- Moves are never force-executed mid-print: the jog controls are disabled
  while printing (pause first), and a tap that lands while the print is
  starting pauses the print through the same tracked Pause command as the
  pause button, with the queued moves running only once the printer
  reports paused. Queued moves are dropped if the pause is not confirmed
  within ten seconds or the print resumes mid-drain; rapid taps while
  paused coalesce into single relative moves. The live position tracks the
  head at the urgent poll floor while the queue drains.
- All motion G-code, the print-state safety gate and the queue coalescing
  rules are pure policy (`ToolheadPolicy`), fully unit-tested without Qt;
  the queue and pause sequencing live in one controller (`ToolheadController`).
- The Monitor tab is reorganised into three panes — **Information**,
  **Printer status** and **Printer controls** — each with Cura-style
  accordion sections: an icon, a title hugging a chevron on the right,
  hover tinting and a divider that survives collapsing. Section state,
  pane collapse and the lock-all toggle persist across Cura restarts
  through a plugin-owned state file. Every pane collapses into a thin
  strip with a rotated title; clicking anywhere on a strip re-expands it.
- The accordion header is one shared `CollapsibleSectionHeader` QML type
  used by every pane; the section icons are Cura's own glyphs where they
  exist, and plugin-drawn SVGs (padlocks, power symbol) — theme-tinted
  through `UM.ColorImage` — where they don't.
- Lock-all now disables only the controls inside sections; the accordion
  stays navigable, and the lock state is shown by a closed/open padlock
  glyph in the pane title row (grey unlocked, Cura blue locked).
- Setup commands (Home, QGL, mesh calibration, Save) queue behind the
  in-flight command so they can be lined up in quick succession, and
  firmware/host restart actions join the System section. The emergency
  stop — two clicks, then a held third press of 0.6 seconds — now docks
  across the whole bottom of the window.
- The camera bar sits centred under the feed with a compact webcam
  selector and an icon-only refresh that restarts the stream. The
  readouts (print job, temperatures, fans, filament, objects, system,
  MCUs) share one labelled value column.
- Command replies get an honest timeout: a connection-level failure after
  acceptance reports "outcome unknown" instead of claiming the command
  was cancelled.
- The pre-release adversarial review fixed the rest: a rapid Preview ⇄
  Monitor switch (which can hang Cura and land its view restoration
  seconds late) no longer detaches the follower; the pane row compresses
  gracefully on narrow stages instead of sliding the Printer status pane
  under the controls; wrapped status sections keep their inter-section
  margins; the emergency stop cannot fire when a stolen mouse grab drops
  the release; the camera area distinguishes "not configured" from
  offline; and the toolhead guard releases after the queue settles.

## 3.3.1

Version 3.3.1 is an audit-driven hardening pass over 3.3.0: an adversarial
multi-agent review against the architecture contract confirmed and fixed the
following.

### Highlights
- The smoothing CSV trace is now opt-in via the
  `MOONRAKER_FOLLOWER_SMOOTHING_TRACE` environment variable (documented in
  `INSTRUCTIONS.md`) instead of writing to Cura's cache on every smoothed
  print.
- The display timer snaps to the target and stops when pure gap decay
  converges, instead of ticking at 30 Hz for the whole duration of a pause.
- Failed G-code downloads retry on a backoff ladder (2 s → 60 s) instead of
  wedging the file service for the rest of the print.
- Start-print power-on probes every configured power device; a powered
  socket can no longer mask a powered-down PSU.
- Lease lifetime was reviewed against the documented contract: an unrelated
  Cura file completion still must not release the current remote file
  (`ARCHITECTURE.md` section 6), so a superseded read intentionally retains
  its lease until shutdown.
- Failed layer hydration is latched until a new file or index arrives, so a
  broken file is not re-read in full on every poll.
- While a compact layer hydrates, the animation driver is reset so a stale
  target cannot fight the follower's own writes.
- The velocity window scales with the measured poll interval, keeping the
  glide honest at slow polling rates instead of degrading silently.
- Service failure messages are logged instead of being emitted with no
  listener.
- Corrected documentation that still described the removed lookahead and
  the old fallback behaviour, and added regression tests for every fix
  above.

## 3.3.0

Version 3.3.0 is the first feature release after the debt payoff: follower
quality.

### Highlights
- Smooths the Preview path head: the displayed position glides along the
  toolpath at the observed physical velocity, never gets ahead of the
  newest observation and never snaps back within a layer. Layer
  transitions are jumped, not animated.
- Reconstructs the physical trajectory between polls: the target ramps
  linearly over the measured poll interval, so the glide is equally smooth
  at any configured polling rate instead of stepping once per poll at slow
  rates.
- Fixes the physical observation itself for slow moves: the refinement
  search window is widened past Klipper's parser-chunk lead and the
  ambiguity fallback holds the last good value instead of jumping to the
  parser-position fraction, so the observed progress is a smooth
  sub-segment signal rather than a cm-apart staircase.
- The smoothing is display-only: the physical path fraction used for ETA is
  unchanged, and animated writes re-remember the plugin-written position so
  manual-override detection is unaffected.
- Adds a **Smooth path progress** option (enabled by default) in the
  Following tab.

## 3.2.0

Version 3.2.0 closes the remaining 3.1.0 architecture gaps and codifies how the repository changes. User-facing behaviour is unchanged.

### Highlights
- Reorganises the test suite by domain; version/regression-named test files are gone and coverage is unchanged.
- Codifies change procedures in `INSTRUCTIONS.md`, including the version bump checklist; the architecture document references it.
- Extracts pure `PreviewFormatting` (status text/icon, pause-item and ETA projections) out of `PrintCoordinator`, which now publishes a domain projection instead of formatting UI strings.
- Dissolves the mixed-domain `Core` module: `RemoteFileIdentity` joins `MoonrakerProtocol`, end-of-layer pause crossing joins `PauseScheduleService`, Preview override detection joins `PreviewFollower`, and the unused `OperationContext`/`OperationPhase` runtime is removed rather than relocated.
- Removes the unused `MoonrakerSessionState.rebind()` path; rebinding goes through the production `configure()`/`reset()` flow only.
- Extends the import/ownership contract tests to every domain module, including the new formatting module.
- Version metadata test now checks `package.json` and `plugin.json` stay in sync; the release workflow still validates both against the git tag.
- Publishes fully detached status snapshots from the session boundary; consumers can no longer mutate session internals through nested values.
- Separates metadata completeness from download identity: a failed metadata request installs a fallback identity so downloads proceed, then retries with backoff instead of permanently degrading the run.
- Moves Preview view reads/writes into typed `CuraAdapter` accessors; `PreviewFollower` no longer calls view methods by name.
- Consolidates URL normalisation into one `PrinterConfig.normalise_url` rule used by configuration parsing, binding and the Machine Action.
- Fixes the G-code layer map for files whose start-gcode emits `CURRENT_LAYER=0` before the first layer marker; scheduled pauses could previously fire one layer early.
- Keeps print run identity stable across transient `file_size` gaps (reconnect/restart), so a mid-print poll gap no longer restarts Preview tracking, pause schedules or downloads.
- Treats pre-print `current_layer=0` in one-based mode as no-layer rather than layer zero, and stops Z-fallback from jumping layers across pause/resume Z-lifts.
- Rejects non-finite or out-of-range Z tolerance values and caps the polling interval.
- Validates persistent index caches against size/modified time alongside the uuid, and recognises `G01`/`G00` motion forms.
- Guards the Monitor camera restore against webcam-set changes, prunes renamed/removed objects from the auxiliary snapshot, deactivates the outgoing Monitor before the incoming one activates, and removes a phantom signal key.
- Consolidates printer-object classification into one shared policy table, narrows Monitor controller capabilities instead of passing the whole client, and moves the bed-mesh observation to the print coordinator (Monitor can no longer write follower mesh state).
- Completes the `ARCHITECTURE.md` ownership map, overhauls the README (release header, feature and structure sections), and makes the tag-release CI run the full Qt suite; verifier tools share one checks implementation.
- Applies released Monitor slider values after a 250 ms unchanged window instead of 2 seconds.

## 3.1.0

Version 3.1.0 is primarily an internal architecture and reliability release. It preserves the v3.0 user-facing workflow while reducing duplicated state/transport ownership and making the high-risk parts independently testable.

### Highlights
- Introduces a shared HTTP-only Moonraker session/state layer for the active Cura printer.
- Coalesces overlapping core refreshes and adapts polling cadence by printer state and request category.
- Removes Monitor's duplicate core-status fallback poller; Preview and Monitor consume the same generation-guarded core snapshot.
- Distinguishes command HTTP acceptance from observed printer-state confirmation for pause/resume/cancel.
- Replaces the follower mixin runtime and Monitor inheritance chain with explicit composed components and immutable print/Preview observations.
- Shares one physical-layer resolver between Preview, Monitor and scheduled PAUSE; manual Preview selection cannot change printer observation.
- Moves index restoration, building, hydration and persistence to a bounded worker with explicit file leases and stale-result guards.
- Streams prepared G-code/UFP uploads from temporary files and gives each write one cancellation/terminal-signal owner.
- Preserves configuration migration before connection startup and invalidates old work on profile or credential changes.
- Adds real-Qt component and loopback HTTP tests, QML interface checks, and dependency contracts that reject retired implementations and private follower coupling.
- Keeps HTTP as the sole Moonraker transport; WebSockets are intentionally not introduced.

## 3.0.0

Version 3.0.0 turns Moonraker Print Follower into a much more complete Cura-side companion for Klipper/Moonraker while preserving the core live Preview follower.

### Highlights
- Integrates Moonraker connection/output functionality into one plugin, including G-code upload and printer-aware file handling.
- Adds a live printer dashboard with temperatures, print state, macros, power controls, Z offset, speed/flow tuning, fans, LEDs, PWM outputs and an emergency stop.
- Adds rich bed-mesh support including a 3D Preview overlay and mesh controls.
- Adds end-of-layer PAUSE scheduling directly from Cura Preview, including multiple scheduled pauses and ETA display.
- Restores and improves selected-layer ETA while inspecting future layers.
- Renames Preview following controls to **Detach / Attach** so they cannot be confused with pausing the printer.
- Improves multi-printer behaviour, large-print performance, polling efficiency and stale-response protection.

## 2.0.0

Version 2.0.0 makes Moonraker Print Follower feel like part of Cura rather than a separate utility.

### Highlights
- Configuration moves into **Settings → Printer → Manage Printers → Configure Moonraker Follower**.
- Full per-printer settings and single-active-printer behaviour.
- Targets Cura 5.x / SDK 8.x.
- Improved live nozzle handling in Preview using Cura's native nozzle model.
- Smoother monotonic within-layer following, avoiding visible rewind/retrace behaviour around ambiguous motion and layer changes.
- Retains multiple follow modes, resilient Moonraker polling and scalable large-G-code indexing.
- Existing 1.x settings are migrated automatically.

## 1.1.0

Version 1.1.0 moves Moonraker Print Follower from a single global setup to a proper per-printer Cura workflow.

### Highlights
- Separate Moonraker connection and following settings for each Cura printer.
- Automatic migration of existing 1.0.x settings.
- New follow modes: exact current layer, last completed layer, one-layer look-ahead and a layer window around the live layer.
- More resilient Moonraker polling with automatic retry backoff.
- Built-in connection testing and capability detection.
- Better handling of very large G-code files through compact indexing and on-demand detail loading.
- Refined Cura-styled Preview controls and clearer live status.

## 1.0.3

Version 1.0.3 is a performance and accuracy release aimed particularly at larger G-code files and long-running prints.

### Highlights
- Streams G-code downloads and indexing instead of holding the complete file in memory.
- Adds persistent, validated path indexes so repeated loads can be much faster.
- Uses Moonraker motion data when available to better match Cura's nozzle position to the physical printer.
- Improves layer mapping using information embedded in the G-code itself.
- Strengthens manual Preview override detection and stale-work protection.

## 1.0.2

Version 1.0.2 focuses on making following behave predictably while Cura is loading, slicing or changing scenes.

### Highlights
- Safer handling of Cura scene changes and slicing, reducing stale or out-of-order Preview updates.
- More reliable manual-override detection when Cura rebuilds its Preview components.
- Improved cleanup and cancellation of downloads, network requests and background indexing.
- Better protection against reusing stale data when the same G-code filename is printed again.
- Lower memory overhead while indexing large G-code files.

## 1.0.1

This release makes it much easier to inspect a print without fighting the follower.

### Highlights
- Moving Cura's layer or toolpath slider manually now suspends automatic following.
- Resuming following catches Preview back up to the live print.
- Plugin-driven Preview movement is distinguished from user interaction, avoiding false pauses.

## 1.0.0

Moonraker Print Follower brings a live Klipper/Moonraker print into Cura Preview.

### Highlights
- Follow the printer's current layer and progress through the active layer in Cura Preview.
- Load the G-code currently printing on Moonraker into Cura on demand.
- Pause and resume Preview following without pausing the printer itself.
- Configure Moonraker connection details, polling, layer handling and Preview behaviour.
