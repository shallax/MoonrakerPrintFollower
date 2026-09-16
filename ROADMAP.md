# Moonraker Print Follower roadmap

This is the direction of travel, not an implementation contract. `ARCHITECTURE.md`
stays the binding description of how the code is organised today; this file says
what the releases ahead aim to deliver and why they are ordered the way they are.
Version numbers and the release checklist live in `INSTRUCTIONS.md`. Items here
are proposals — each becomes binding only when its release branch exists.

Current release: **4.0.2** — SHIPPED (2026-09-14): the transfer and
print-identity correctness release — five lifecycle repairs, each
with its regression, live-tested through the snapshot loop.

Next: **4.1.0** — deep harness coverage, extended by the round-1
critic and the architecture review (see the 4.1.0 section).

## Direction

The 3.2.0 debt payoff made structural change cheap again. The next stretch has one
theme: close the gap to Mainsail on the Monitor tab, then put physical printer
position to work in the Preview, where no web dashboard can go. (The socket
transport shipped in 4.0.0.)

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
  Chart spec (the rulings): a 30-minute rolling window;
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

## 3.6.0 — File manager and the small controls — SHIPPED (2026-09-11)

**Safety first — the jog reflow bug (live report, 2026-09-09):**
while hammering a toolhead move with some Printer-controls sections open,
other controls can disappear/reappear and REFLOW the whole pane — the nudge
button under the pointer can move mid-click. Incredibly dangerous during
nudges; the pane's content must stop shifting under the jog pad.

**The no-reflow rule (2026-09-10):** no controls disappear, ever —
only disablement/enablement. Nothing may cause the UI to reflow
unless the user explicitly asks for it (expanding/collapsing
sections, resizing, and so on).

Shipped as 3.5.1 (the patch release, live-tested through
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

**Console sizing (direction):** the console's only expanded size is
~28% of the column — a drag handle to resize the pane lands with this
release, shaped by the new pro-user persona (a 3D-printer enthusiast
lens on feature value joins the review panel from this release).
Constraints (2026-09-10): no ridiculously large sizes — the drag
range is capped — and dragging small enough snaps the console into
its collapsed state.

**Layer numbering from the file's own comments:** DEFERRED at the
round-2 walk — the flip as ruled is a
silent no-op for `one_at_a_time` files (duplicate `;LAYER:0` discards
the layer map) and off-by-one for preprocessed files (comments are
0-based and signed; Cura's GUI and Klipper are 1-based; round-2 domain
D7). It did NOT land in 4.0.0 (the pack never formed); still deferred —
carried forward with its gate and base ruling.

The last large Mainsail parity piece: browse remote gcode, print and delete.
`RemoteFileService` and its `FileLease` lifetime model already provide the
safe download path; this release adds listing UI and the print/delete
command surface on top. Print-from-file is as powerful as the console (it
IS the console path plus print start): it needs the same ready-retry,
power-device gating and state-expectation machinery as UploadController,
delete of the currently-printing file must surface Moonraker's refusal
cleanly, and starting a print must trigger the existing job-observation →
load flow (stated so the wiring is tested).

**The file-manager vision (2026-09-10):** a popup opening mid-screen
from a File-manager button in the printer controls pane, mirroring
Mainsail's file manager. Columns: thumbnail (if available), name,
print attempts (count, last status success vs fail), last modified,
object height, layer height, estimated print time, last print time,
slicer, extruder temp, bed temp, filament usage estimate. Metadata
rule: the details must never require downloading the gcode —
anything that needs a download is too expensive to show. Functions:
delete (individually or bulk), rename (individually), print
(individually), download; upload external gcode via a file picker
(use the file's actual filename); upload the current slice, asking
for the filename like the Preview upload pane, and offer to print
when uploaded. Search: predominantly by name — searching anything
else would make results confusing. Filters (open for panel input):
slicer, date (earlier than X, later than X), layer height, file
size, duration — the set was deliberately left for the panel to
shape. Listing: paginated and loaded lazily so a huge amount of
data never arrives in one go; a user-picked page size; scrolling
between pages via advance/reverse buttons and possibly direct page
selection; sorting by any column, which requires downloading all
the metadata so sorts never work on a single page only. Free disk
space should be visible on the printer.

**Phase-0 rulings (2026-09-10):**
- The listing comes from `server/files/directory?extended=true`
  (round-1 critic: plain `server/files/list` carries no metadata, so the
  12-column table needs the extended form; the same response carries
  `disk_usage`). Round-2 change: subfolder navigation is in scope, so
  the grid lists ONE directory at a time with a breadcrumb — no
  full-tree flattening. Pagination, sorting and filtering are
  client-side (the whole listing downloads at once, and that is
  accepted). Lazy
  loading applies to thumbnails: fetch per visible row, cache.
- No requested column triggers a client gcode download — metadata is
  parsed host-side at upload; the contingency stays as the guardrail.
- Temps shown as the honest first-layer extr./bed values.
- Print attempts come from the `[history]` component; when history is
  unavailable the column is hidden, not blank.
- Upload overwrite is expected Moonraker behaviour; the client checks the
  loaded listing and asks "File already exists, are you sure?".
- Refresh: on open, after every action that makes the data stale, and a
  manual button. No timer.
- Search is name-only free text; everything structured (slicer, dates,
  layer height, size, duration — the exact set goes to the panel) is a
  filter.
- Print from a row opens a Mainsail-style confirmation: "Start print
  job?" with the filename, print/cancel.
- Disconnected: the File-manager button disables (the 3.5.1 rule).
- Sort/search/filter state is NOT persisted. Instead a **Recent prints**
  strip (new, the idea) LEADS the popup — the
  live-test reversal (Snapshot 0, 2026-09-10): "I've changed my opinion
  on the 'recent prints' bit. I think that does belong as the first
  thing in the window." It shows the top 5 distinct
  files most recently printed — "anything where Moonraker has started to
  print something counts" — sourced from Moonraker's `[history]` list
  (no local storage), hiding with the attempts column when history is
  unavailable. A file missing from the listing renders greyed out with a
  tooltip ("it shows that it _was_ there"): "No longer listed on the
  printer — deleted or moved". Clicking a live card opens the same
  "Start print job?" confirmation.
- Column order: drag-to-reorder, persisted, default = the
  sequence. Horizontal scroll for the wide table; per-column visibility
  is optional — the panel decides whether it earns its place.
- All timestamps render in the user's local time.
- Empty states: an emdash for a missing cell value; "No files available"
  overlay inside the grid with a refresh offer; the over-filtered variant
  says filters may be hiding results and never locks the controls
  (the camera-disconnected pattern).

**Round-1 critic (report in `review/3.6.0/round-1-general-critic.md`),
accepted adjustments:**
- Transport: file delete is Moonraker's HTTP
  `DELETE /server/files/{root}/{filename}` — verified against all 15
  documented file-management routes by the round-2 engineering panel
  (there is no POST delete; `server.files.delete_file` is Mainsail's
  websocket JSON-RPC route, which this release does not have). The
  transport gains a DELETE branch, and the correctness test is a
  real-socket verb test — a fake-based test would pin a fiction. (An
  interim "POST server/files/delete" correction turned out wrong; the
  panel caught it.) Scheme-aware `X-Api-Key` stripping for
  foreign-origin requests still lands (the webcam probe would leak the
  printer key to the camera host). Delete of the printing file surfaces
  Moonraker's 403 refusal.
- Rename uses the same "File already exists, are you sure?" collision
  prompt as upload (`server/files/move` silently overwrites).
- Greyed-out Recents use history's `exists` field, not a client-side
  listing diff.
- `printer/print/start` becomes a new tracked command family with an
  expected-state set (not fire-and-forget), and must inspect its result;
  the job-observation → load handoff is specified against that
  confirmation, not against raw optimism.
- Thumbnails: fetch only thumbnails the metadata lists as extracted;
  placeholder otherwise; serial fetch queue keyed by stable file
  identity, never a parse-from-gcode trigger.
- Watchdog auto-switch is transient (never rewrites the saved camera
  preference) — confirmation threshold to be ruled.
- Restart arming generalises the emergency pattern to per-button arming
  slots with per-button progress (model-owned state, surface lists).
- Console drag: the sizing model moves to model-owned persisted state;
  snap-to-collapse writes the section state. Cap values to be ruled.
- Print confirmation is an app-modal dialog (the sanctioned pattern),
  disables the grid rather than hiding anything.
- ETA feed-forward applies to the Monitor blend only (Preview already
  divides by speed factor); correction-vs-clamp placement to be
  specified.
- Verified-pause-only inverts ownership: the observation consumes the
  entry — the schedule no longer drops on due, the tracker references
  its entry.
- Column-order rehydration: stable merge (unknown appended in default
  order, removed retained), per the repo's existing rehydration rule.
- Empty-state overlay renders in a non-interactive region (banner +
  wash), never over the controls.
- Timestamps render as absolute local-time values — nothing
  relative-to-now (capture determinism); the formatter module joins the
  frozen-clock sources.
- Staleness: "listed X ago" label; no timer stands.
- Test surface: file endpoints land in `fake_moonraker` (keeps the
  determinism promise); QML surface checks must cover the popup's
  guarded branches.
- `INSTRUCTIONS.md`'s section-id recipe gains `console`.

**Round-1 rulings (2026-09-10):**
- Attempts column: the status word, colour-coded, never flattened to
  success/fail; the count carries a "within history retention" tooltip.
- Reflow: the no-reflow rule holds for the Monitor; the file-manager
  popup is exempt — reflowing the file manager is fine, there is
  nothing critical on it. The structural QML pin still sweeps the
  popup, so its `visible:` entries land in the allow list citing
  this ruling.
- Watchdog: auto-switch after 2 consecutive failed probes, transient
  only — "stream offline" badge + manual revert; the saved camera
  preference is never rewritten.
- Console drag: 15–50% of the column; below 15% snaps to the collapsed
  state.
- `;LAYER:n`: comments are authoritative only when consistent with the
  file's observed layer structure; Z-rise fallback otherwise.
- Pagination stays complete — page size, prev/next AND the direct
  page-selection widget (the critic's cut was rejected: pagination
  itself was never in question, only the skip widget). Per-column
  visibility: CUT (column order stays draggable and persisted).
- File-manager button: a permanent section at the top of the
  Printer-controls pane with a full-width "File manager" button.
- Recents dedup: top 5 distinct files by filename — a file printed
  three times counts once ("distinct files").

**Carried over from the 3.5.x decision log** — IN (all three):
- the private persist path (`binding.persist(config)`, R5-3 Option B)
- the client-side send queue (R5-8)
- the pane-readability INSTRUCTIONS note (the 3.6 docs pass)

**Round-2 panel outcomes (2026-09-10, walked through — full
disposition in `review/DECISIONS.md`, reports in `review/3.6.0/`):**
- Snapshot-sequenced build: **Snapshot 0** — the popup as a static
  mock-up (synthetic data, no network, no model) for the taste
  test — then 1 read-only manager → 2 print + download → 3 mutations;
  the Monitor tail on its own track.
- **Snapshot 1 (read-only manager) built (2026-09-10):** the pinned
  face now reads the live model — walk, recents, breadcrumb
  navigation, sortable headers, search, the four filters with
  multi-select menus and option counts, page size (25/50/100/all) and
  paging, three-state page checkbox, per-row selection, global clear,
  Load-all-history, per-row Scan metadata, empty states (loading / no
  files / over-filtered / walk error with retry), live disk readout.
  The mock rows remain the fallback when no printer is attached.
  Print + download (Snapshot 2) and the mutations (Snapshot 3) are
  still to come; the ⋮ menu carries their disabled placeholders.
- **Snapshot 1 live-test rulings (2026-09-10):** "Clear all" also
  clears the search and lights up while a search is active; the
  Delete verb and "N selected ✕" are GONE, not greyed, while no
  selection exists; the search field gets a circled ✕ clear button
  and a 250 ms settle before searching; a ⟳ refresh button sits
  beside "Last refreshed at"; no page chrome at all ("Showing…",
  "Page N of M", per-page, ‹ ›) while no data is on the page — no
  "Page 1 of 0"; and DIRECTORY BROWSING: folders never join the
  metadata list ("directories don't really fit in with the list
  model (they don't have any metadata about prints)") — a folder
  strip above the grid shows the current level's subdirectories as
  clickable chips, a click descends, the breadcrumb climbs, and the
  strip hides during a search (the scope is then the whole tree).
  Two live bugs fixed with regression tests: sorting by Status
  crashed the publish (the column key "status" has no FileRow field
  — it maps to last_status), and the view-mutating slots never
  re-published (filters appeared dead, the page carousel advanced
  one step then stopped).
- **Snapshot 1 live-test rulings, round 2 (2026-09-10):** "Load all
  history" sits with the refresh affordance, NOT the recents strip;
  the breadcrumb disappears while a search is active; the mock's
  decorative "⋮" in the actions column HEADER is gone (the per-row
  ⋮ stays); the sort arrow hugs its column title — at the column
  edge it read as belonging to the next header and overlapped the
  text on tight columns (Extruder widened 64→72); scratch files live
  in /tmp/mpf and the dev container bind-mounts it. Probe-proven
  engine behaviours: a Repeater with two bare children keeps only
  the LAST as its delegate (the breadcrumb's " / " separators never
  existed in the tree — fixed with one delegate per segment), and
  Component ids resolve through the SCOPE only — `root.x` is a
  property lookup and silently loads NOTHING in a Loader (the whole
  filter button row was empty; fixed with bare ids). Refresh now
  rebuilds the listing per walk and swaps it in on completion: new
  subdirectories appear, deleted files leave the rows.
- **Snapshot 1 live-test rulings, round 3 (2026-09-10):** Modified
  and Print time are RADIO menus (one at a time; clicking the active
  radio clears the filter) — Slicer stays checkboxes; a selection
  NEVER dismisses a filter menu (the user does); the menu opens
  BELOW its button, never over it (it parented to a zero-height
  Loader and opened at the button's top-left); both button faces
  coexist with visibility flips — a Loader swap on the active-state
  change destroyed the open menu. An up-directory chip ("..") leads
  the folder strip whenever a parent exists — the root view has
  none. A directory shows ITS OWN files, never a recursive subtree
  aggregate; only the search spans the whole tree. The page carousel
  was dead because `page_index()` passed the PAGE count into
  `clamp_page()`, whose `total` is the ROW count — it double-counted
  and clamped every listing over one page back to page 1 (bisected
  in the probe, fixed, end-to-end composed test with 30 resident
  files).
- **Snapshot 1 live-test rulings, round 4 (2026-09-10):** Esc on the
  Monitor page is a window-level Shortcut (a Keys handler dies with
  focus — a live report), gated off while the file-manager
  popup is open (two live shortcuts on one key would both fire; the
  first gate read the wrong parent and broke the popup's Esc). A
  refused command reads the SERVER's words ("Extrude refused:
  Extrude below minimum temp" — Moonraker's 400 body buries the
  message in the traceback tail) and is a refusal, not an outcome
  unknown. The console error bell gets its feed: while the console
  is collapsed a slow 5 s store poll keeps error lines flowing (the
  poll was expanded-only, so the bell could never ring — the
  author's own diagnosis). Jog/extrude selections persist in the
  plugin state file. Extrude distance/speed rows keep their
  selection highlighted via the button-face swap.
- **Snapshot 1 live-test rulings, rounds 5–8 (2026-09-10):** ONE
  window-level Esc shortcut owns the whole ladder (popup → popover →
  stage); the popup's open flag lives in the MODEL (the round-5 gate
  read the wrong parent and the popup's Esc died). Refusals read the
  server's one-line words across every shape (flat dict, nested
  "error" dict, message-only, traceback-only — the script endpoint
  answers HTTP 200 with the error DICT inside "error"); a rejected Z
  nudge explains itself in the jog status AND the console (once per
  burst); the Z floor is ZERO whatever position_min says, with a
  client-side Z estimate so rapid nudges can never outrun the poll;
  the abs/rel toggle is the mode TEXT itself with a poll latch; the
  Move distance combo restores the persisted selection; the breadcrumb
  root reads "<root>". Snapshot 2 (print + download) built: the pinned
  print confirmation (Esc cancels the TOP layer; other Print entries
  stand down), root-exclusive print/start with the print_stats
  transition as the only success and a 15 s watchdog that explains a
  start that never happens (an unhomed printer fails LOUDLY); Download
  streams through a one-shot lane and loads into Cura; double-click
  prints; the confirmation carries a themed background and a
  not-homed readiness line. The e-stop ASSUMES the print stopped at
  the CLIENT's observation layer — the emitted status reads as
  cancelled until the printer reports otherwise, so every consumer
  (guards, jog gate, the follower's coordinator) re-evaluates
  immediately and consistently; the earlier per-consumer latches were
  deleted (one point, no scattered conditionals — the guard
  sprawl; the permissions consolidation lives in the 4.2.0 roadmap).
- E-stop recovery (the ruling, 2026-09-10, live-proven on
  their printer, overriding the investigation's readiness-gating
  alternative): after the stop the host refuses commands until the
  connection is cycled — the plugin disconnects and reconnects ONCE,
  automatically (1.5 s after the stop, `MonitorCommands.
  RECONNECT_DELAY_MS`), re-arming the monitor; the console's own
  connect/disconnect notes render the cycle. The rejected alternative
  (gating motion/setup on `klippy_state` readiness) stays on the
  table for 4.2.0's permissions consolidation if the reconnect ever
  proves insufficient.
- Thumbnails (Snapshot 2): one-shot fetches into a session temp dir
  with loading/ready/failed states and the no-spin-forever fallback
  (the ruling). Iteration 1 shipped THREE stacked defects,
  all live-caught: a bare closure on the reply's finished signal
  crashed Cura on open (a PyQt use-after-free — SIGSEGV in
  `PyQtSlot::call`); the URL itself was wrong — the plain
  `server/files/gcodes/<file>.png` route 404s on a live Moonraker
  for every file, the thumbnail lives at the metadata's
  `relative_path` (`.thumbs/<name>-<size>.png`, largest entry,
  live-proven with curl); and `reply.error() != 0` is ALWAYS true
  (PyQt6 enums never equal plain ints), so every successful fetch
  still landed "failed" — the 404 masked it, and it masked nothing
  back (both are fixed; each is pinned). The lane
  now implements the roadmap's raw-reply owner rule — replies
  register per fetch, connect into a bound method, validate
  generation before any read, and abort on bind/unbind (the
  transport's own pattern; pinned by
  `test_network_replies_connect_into_bound_handlers_not_bare_closures`
  and a real-socket fetch test) — and rows without metadata
  thumbnails record "none" without ever fetching. Same pass: the row
  pointer surface moved OUT of the row layout (an anchored MouseArea
  inside a layout is undefined behaviour — Cura warned once per row
  on open) and the empty-state bindings gained the null-model guards
  the engine gate demands.
- The walker excludes all dot-entries (`.thumbs` included — "Don't show
  hidden directories in the browser at all"); Print/metadata gated on
  Moonraker's gcode extensions.
- `extended=true` reads Moonraker's metadata cache — a per-row "Scan
  metadata" (`metascan`) action covers legacy files (one-off host-side
  parse per file).
- Rows carry `(root, relpath, basename)`; Recents dedup on relpath;
  greyed wording "Not currently on the printer".
- `print/start` is root-exclusive vs delete/metadata root-inclusive —
  two path builders, real-socket tests; success = a `print_stats`
  transition with the expected filename, never the reply.
- Firmware restart routes to `printer/firmware_restart` (the
  gcode/script route reports failure on success).
- Transport first: DELETE branch, error-body reading (the 403 refusal
  surfaces), `SameOriginRedirectPolicy`, origin-triple key stripping,
  unknown-verb guard, raw-reply owner for thumbnails/probes.
- Filters per the UX adjudication, approved: Slicer (+Unknown bucket),
  Modified (window presets), Print time (buckets), Never printed; size,
  layer height and the date pair cut. The Size COLUMN joins (free);
  default sort newest-first; "Showing 1–50 of N"; page 1 reset on any
  change.
- Selection accumulates across pages, clears on sort/filter/search
  change; the header checkbox selects the whole page (Snapshot 0: the
  "Select all on this page" button and the bulk-bar Clear button were
  dropped — "can be done by double-toggling the select all checkbox").
- Recents dismissal persists (FIFO 50 name-keyed hide-list).
- Recents reversal (2026-09-10): the greyed-out "No
  longer listed on the printer — deleted or moved" card is GONE.
  The author: if recents were stored locally, a recently-deleted
  file could stay visible because the user could clean it out by
  hand — but recents are Moonraker's history (the source of truth),
  which cannot be cleaned client-side, so files the server no longer
  lists never render at all.
- Restart arming: click arms → separate press-and-hold (~800 ms) fires;
  one armed at a time; all three disabled mid-print.
- Column reorder via a transient [⇄ Columns] mode (drag handles, scroll
  suppressed, Done/Esc exits, order persisted).
- Print confirmation: filename, est. time, filament, target printer
  name, readiness line, verbs Start print/Cancel; other start paths
  disable while up; identity re-validated at the POST.
- Popup: stage-level panel above the e-stop dock (dock stays live), its
  own QML file; the no-reflow exemption covers the list region only.
- Console drag: fraction store (15–50%), handle only when expanded,
  live snap preview, double-click toggle, one write per drag.
- Watchdog: snapshot-URL probe (see above).
- `;LAYER:n` deferred (see above).

**Snapshot 0 live-test rulings (2026-09-10):**
- Filters stack UNDER the search bar on their own row ("Filter
  positioning is a bit weird. Perhaps they should be under the search
  bar") — refined live: ONE DROPDOWN PER FILTER
  CATEGORY, each self-contained with scrollable multi-select options;
  a category with an active filter turns SOLID BLUE as its active
  indicator. The active selections stay INSIDE each dropdown's menu —
  no chips band spilling into the form; the over-filtered empty state
  carries its own "Clear all filters" action.
- The grid is the flexible region: the popup must fit a small window
  without spilling over the emergency dock — the file list shrinks and
  scrolls (a dynamically shrinking file list was preferred).
- The camera control bar reflows (wraps) when the window is crushed
  horizontally — reflowing that control is fine.
- Snapshot 0 iterates until it looks right — no Snapshot 1 before
  the nod.
- Console: long Klipper lines wrap on word boundaries and the text
  stops short of the scrollbar; the console auto-collapses below
  350 px of width and auto-re-expands when widened (the persisted
  state is never touched).
- Camera bar: stays centred; the combo shrinks under crush; at the
  extreme crush the "Camera:" label moves above the dropdown — the
  wrap version was rejected for left-aligning it.
- The Recents strip gets a lining divider from the files list; the
  cards are tight (name and time stacked under the thumb, × in the
  top-right corner).
- Display names hide the ubiquitous `.gcode` suffix; the rarer
  extensions (.ufp, .nc, .gco, .g) stay — they signal file kind.
- The bulk-bar Delete is red, never primary-blue — blue reads as
  "the suggested next step" for a destructive action. It sits in the
  PAGINATION row, far left, nudging the page controls over; the verb
  and the count appear only while a selection exists.
- The column headers freeze during vertical scroll (the sticky
  name/thumb block stays; the trailing header follows the rows'
  horizontal scroll one-way).
- Narrow-window mode: below ~700 px width or ~500 px height the file
  area gives way to the recents strip and a resize hint ("Make the
  window larger to browse and manage files" — both dimensions can
  starve the grid, not just width).
- Column headers never wrap OR elide — the column widths are sized
  to fit their titles on one line.
- Column resize and reorder both live in the columns-arrangement
  mode (drag handles, scroll suppressed, Done/Esc exits, order and
  widths persist). Its trigger is the ⋮ beside the select-all
  checkbox in the first column's HEADER cell — not toolbar text
  (the live ruling).
- The sort slot shows the arrow only on the sorted column and nothing
  elsewhere — no "—" placeholder.
- Sort rule (the): exactly ONE column is sorted at a time;
  sorting another column cancels the current sort.
- The grid's two scrollers are axis-locked (vertical owns both
  columns; the horizontal one never intercepts a vertical wheel).

**Monitor width squeeze (the out-of-scope ruling,
2026-09-10):** when the stage is too narrow for the Webcam pane at
its minimum, the Information pane auto-collapses to make room. The
trigger is computed from FIXED constants (expanded Info width +
webcam minimum + status minimum) — never from the post-collapse
layout, so collapsing Information cannot move the goal post and no
hysteresis oscillation can form; re-expansion waits for the required
width plus a margin, so the boundary cannot jitter. While
auto-collapsed the toggle is inert with an explanatory tooltip.

**Snapshot 0 PINNED as the design (2026-09-10):** the mock iteration
had gone as far as it usefully could; this exact face is the design.
Snapshot 1 wires the real data behind it.

**Search/filter semantics (functional rulings, 2026-09-10):**
- Search narrows the results with every keystroke — live filtering
  over the resident data, debounced (~150 ms), cheap because the
  listing is fully client-side.
- Order of composition: FILTERS apply FIRST, then search searches
  WITHIN the remaining filtered subset — search never overrides or
  widens past the filters.
- Search is GLOBAL: it spans ALL files in the tree, suspending the
  current-folder view ("all bets are off with the breadcrumb/path");
  matches show their folder as a small second line in the name cell.
- Filter boolean logic: OR within a category (a file cannot be sliced
  by two slicers), AND across categories.
- Filter menus show a live per-option count — how many files would
  remain if that option were enabled.
- An active filter category renders blue with a count badge inside
  the button; no label summaries, no chips.
- Delete gets a confirmation for single AND bulk deletes (especially
  bulk).
- History: fetch the 200 most recent jobs per popup session (the
  author's ruling: snappy default, escape hatch complete); a count at
  the window edge says so in its tooltip, and a "Load all history"
  action fetches EVERYTHING remaining in one go (paging the list
  until exhausted) and recomputes every count — one click resolves
  even a file printed 1999 jobs ago. (Moonraker has no per-file
  history query — only the paged list and single-job-by-id — so the
  escape hatch extends the whole window, never a single file.)
- The "N selected" count carries a ✕ and IS the global selection
  clear (the ruling).
- Direct page selection is CUT (Mainsail has no such feature); "All"
  joins the page-size selector instead (25/50/100/All).
- The select-all header checkbox has three states: click on
  none/some selects the whole page; click on all deselects it; the
  some state shows a partial mark.
- The row Print trigger is BOTH: the ⋮ menu's first item AND
  double-clicking a row — both open the same "Start print job?"
  confirmation.
- Reopen while connected: always refetch; cached rows show until the
  new listing lands.
- Scan feedback: the thumbnail slot shows a spinning hourglass while
  a scan runs, then the values land.
- Esc closes only the top layer (the popup's shortcut stands down
  while a confirmation or mode owns the key).
- Ticked rows: white text everywhere, the status colour survives as a
  dot beside the word, and the printing accent bar turns white.
- The scroll chevrons stay centred over the data — the nudge into a
  gap was overruled (they would not be obvious enough).
- Recents cards shrink to a minimum useful width (112 px) and no
  further; five cards fit at Cura's minimum.
- Narrow mode hides the Files heading, breadcrumb and refreshed-at
  line too — the narrow face is exactly recents + hint.
- Tight iteration builds use `make snapshot_quick` (lint + run_tests
  + a verified package, no captures or determinism); `make all` stays
  the gate before commits and pushes.

Small controls the domain panel ranked as the real Mainsail gaps —
re-checked against the tree on 2026-09-10; two had shipped since the
panel's ranking (the speed/flow sliders and Z-babystepping). The rest
moved to a 3.6.1 follow-up by a ruling on 2026-09-11 (too late to
continue and too risky), then FOLDED INTO 4.0.0 the
same day: restart-button arming, the auto-improve-ETA opt-in, the
webcam liveness watchdog, ETA feed-forward, scroll-to-prompt and the
pause-list verified-pause-only semantics — all shipped in 4.0.0.

Snapshot 3 (2026-09-11): the mutations, uploads with progress and
verdicts, column config/resize/reorder with title floors, the
thumbnail queue with coalesced publishes and size-matched fetch, the
Esc/dialog state machine, and the panel re-review fixes (the print
watchdog's transition-is-success form, the directory delete route,
the clipped row viewport, the disconnected gates and the popup's own
note surface).

## 4.0.0 — Websocket transport (2026-09-11)

**RELEASE GATE (resolved):** the 2026-09-11 still-broken list (the
failure state persisting until a manual reconnect, the preview card
appearing only after a load, M117 missing the Print-job section) is
fixed and harness-verified — every item clicked through the real-Cura
suite with screenshots, and the full gate (smoke + all suite groups on
5.13.0, smoke on 5.12.0) ran green on 2026-09-13. The record and the
harness design are in the 4.1.0 section and `TESTING.md`.

3.6.0 ends the v3 line. The author's ruling (2026-09-11): the
per-request HTTP polling is unacceptable in production — prints audibly
dwell while the plugin is connected (live-proven on their Voron). The
author's correction (2026-09-11): the diagnosis is the CUMULATIVE
per-request polling load — the console poll was NOT singled out as the
cause; the console stays HTTP by ruling, and the measurement arms
record the console state as a covariate, not a suspect. 4.0.0 moves the transport to Moonraker's websocket subscription
model: Klipper pushes object updates to Moonraker once per interval and
Moonraker fans them out to subscribers — one serialization shared by
all clients instead of one per client query. Everything in 4.0.1 waits
on this.

**Scope rulings (2026-09-11):**

- v4.0.0 switches to websockets. It should be a feature-transparent
  change, so any feature that changes needs to be surfaced and
  approved before it is built — the transport swap is invisible to
  the user by design.
- There should be an option to enable websockets or stick to HTTP,
  even though HTTP has known performance issues — a user-facing
  transport-mode choice: websocket subscription or the HTTP poll,
  both first-class.

**Phase-0 rulings (2026-09-11, walked through):**

- The toggle switches the STATUS FEED only: the core and auxiliary
  status traffic follow the choice. Commands (gcode/script, print/start,
  macros, file operations), the console store feed, uploads/downloads
  and thumbnails stay HTTP in both modes — refusal-word surfacing and
  the e-stop cycle keep today's semantics.
- Default: websocket (new and upgraded installs), with HTTP selectable
  and the automatic fallback where the printer cannot subscribe.
- Guard polls: the 250 ms urgent polls (pause guard, toolhead tracking)
  stay HTTP — short-lived, only while a guard is latched — so pause
  confirmation latency and the jog readout do not coarsen.
- The setting is per-printer (the `PrinterConfig` record pattern, like
  the chart config): a fleet can mix modes per machine.
- Design intent for the panel (recommendation, not yet an author
  ruling): in websocket mode the socket's health is the liveness
  signal; a reconnect re-identifies, re-subscribes and re-syncs with
  one full HTTP objects query (no replay). The 3.5.1 disconnected UX
  renders identically in both modes.

**Compatibility baseline note (2026-09-11):** the panel had been
assessing against Cura 5.9.1; the current Cura is 5.13.x. Every
compatibility claim in the panel rounds is assessed against CURRENT
Cura (5.13.x) and its bundled
PyQt6. The 5.9.1 pin in the tree is the capture theme only
(`tests/theme_assets/`, extracted from the 5.9.1 AppImage) — capture
fidelity, never the runtime baseline. Cura 5.13's bundled PyQt6 is
stricter than the dev container's newer PyQt6 about re-exports (the
QHostAddress lesson, pinned by
`test_qt_imports_name_the_module_that_owns_the_class`): the websocket
module-availability question must be answered against the real 5.13
bundle, not the container.

**Substrate verification (2026-09-11, certainty demanded):**
the standing rule for this release — "We need to be absolutely
sure that if we implement websockets, this will work" (a previous
websocket attempt had failed — explained by the binding being absent).
Verified against the real Cura 5.13.0 AppImage (latest stable per
UltiMaker's own update feed; extracted, `X-AppImage-Version=5.13.0`):
the bundle ships Qt 6.6.0 and exactly 9 PyQt6 bindings (Core, DBus,
Gui, Network, OpenGL, Qml, Quick, Svg, Widgets) — **no
`QtWebSockets.abi3.so`**. Cura 5.9.1 ships a loose root-level
`QtWebSockets.abi3.so`, but its PyQt6 is a namespace package, so the
binding is unresolvable there too; Cura's own code imports websockets
in neither version, and no pure-Python websocket library ships as a
substitute. Confirmed in-process: a spike plugin inside the running
5.13.0 app recorded `ModuleNotFoundError` for `PyQt6.QtWebSockets` —
and the substrate spike PASSED: a hand-rolled RFC 6455 client over
`QTcpSocket` completed the upgrade handshake (Sec-WebSocket-Accept
verified), sent a masked text frame and read the echo, all inside the
real bundled runtime. **Ruling:** 4.0.0 builds the websocket client by
hand on `PyQt6.QtNetwork` (QTcpSocket/QSslSocket) — no new Qt module,
no new dependency, works on every Cura in the 5.0–5.13 line. Remaining
certainty gates: a TLS (wss) check and the live-Moonraker test on the
author's printer during the snapshot loop.

**Live-printer verification (2026-09-11, the Voron):** the
hand-rolled RFC 6455 client, running INSIDE the real Cura 5.13.0
bundle, completed the handshake against the live Moonraker
(v0.13.0-733), the subscribe returned all five objects, and 29 frames
/ 44.9 KB streamed in 6 s (~4.8 Hz — Klipper's 250 ms push cadence,
live-confirmed) with the predicted `notify_status_update` shape
([changed objects, eventtime]). The subscribe reply is the full
snapshot — the reconnect re-sync needs no separate HTTP query. Auth is
NOT enforced on this printer (any key or none works). Two probe bugs
died here and are part of the record: the RFC extended-length ENCODE
form (>125-byte payloads — Tornado silently drops the connection on a
corrupt header) and a probe recorder that swallowed the evidence. The
printer is plain http, so the live wss case remains open — the in-bundle
TLS spike (verified TLS 1.3 + certificate-verification parity with
today's HTTPS, measured byte-identical) covers the substrate half; a
live proxied-https test rides the snapshot loop.

**Phase-2 rulings (2026-09-11, walked through):**
- Subscription while idle: FULL-TIME (all five status objects
  whenever connected) with a delivery clock — the socket accumulates
  pushes and the existing `PollPolicy` delivers to consumers at
  today's cadences. Printer-side cost is measured live in the
  snapshot loop; per-policy narrowing is the measured fallback, not
  the day-one design.
- Mode switch on a live connection: REBIND — exactly like a URL/key
  change today (session reset, connection cycle). No new behaviour
  class to spec. The author's ruling (2026-09-11): the consequent
  upload abort is ACCEPTED and DOCUMENTED — flipping the toggle
  mid-upload cancels it, stated in ARCHITECTURE §3 and the toggle's
  helper line; no silent behaviour.
- The round-1 dispositions for C2/C4, H1–H8, M3–M6 and L1–L4 are
  recorded as PROPOSED (the nod comes at the round-2 walk);
  the three explicit rulings above and the substrate ruling are fixed.

**Phase-4 rulings (2026-09-11, walked through — the panel
walk):**
- Guards ride the push stream: in socket mode the 250 ms guard updates
  come from the delivery clock draining at the urgent interval (the
  printer already pushes at 250 ms — zero cost); the HTTP guard path
  remains for HTTP mode and as the socket's fallback. Worst-case
  readout jitter ~500 ms is a flagged transparency item for the
  snapshot loop.
- Data-proven connected: the dot goes green only after the first
  accepted snapshot; staying green requires a periodic cheap
  authenticated round-trip over the socket; `notify_klippy_ready`
  forces an instant re-subscribe (Moonraker wipes subscriptions on
  every Klippy restart). The socket's own state is NOT liveness.
- Fallback on silence: a startup proof (real frames within a few
  seconds of connecting, else bind HTTP and say why) plus a
  steady-state silence watchdog (silent-while-connected falls back to
  HTTP without a session reset, with a visible reason). The websocket
  default stands on this detection.
- Camera through the auth-enforcing proxy RULED (2026-09-11, the
  author): Cura's `NetworkMJPGImage` cannot send the API-key header, so
  the camera behind a header-auth proxy cannot render — 4.0.0 gains a
  camera BRIDGE: the plugin fetches the stream with the key and
  republishes it on a keyless local endpoint for Cura's loader. (Held
  for now; the camera-URL override field points Cura's loader at the
  printer's own keyless LAN webcam port, which unblocks live testing
  without the bridge.)
- Delivery-cadence sliders RULED IN for 4.0.0 (2026-09-11, the
  author): the push stream arrives at the printer's cadence and the
  delivery clock hands it to consumers on the client's — with sliders
  to adjust it. The core interval field is relabelled
  ("Status update interval") with a mode-aware helper line; the
  auxiliary and console cadences gain per-printer fields with bounds.
- UX adjudication RULED (2026-09-11): the transport-mode
  control is two `Cura.RadioButton`s — "WebSocket subscription" /
  "HTTP polling" — on the Connection tab with a permanent reason
  line, per the UX persona's recommendation. The approval-list strings
  (connection notes naming the transport, one-click revert, mode-aware
  Test-connection verdict, interval relabel, reason line, trace label)
  get the nod as they are built.

## 4.0.1 — Harness fast-follow (2026-09-13)

A fast follow after 4.0.0 ships; test-infrastructure only, plus the
docs cleanup folded in (2026-09-14):

- **Dismiss the G-code details warning**: Cura's "Make sure the g-code
  is suitable for your printer" dialog gets in the way of the UI
  tests. Dismiss it CONDITIONALLY at boot — never wait for it: tests
  that load no gcode never see it.
- **Harness verbosity (2026-09-13)**: the harness scripts
  sit silently through long phases (the docker build, the Cura fetch,
  the per-unit staging, the boot waits). Emit progress lines for each
  phase — what is being waited on and for how long — so a watching
  terminal never looks hung.
- **Filter the known-benign boot warnings (2026-09-13)**:
  every Cura boot prints upstream warnings — the ast.Str deprecation
  from UM/Settings/SettingFunction.py and its kin — that drown the
  failure report's log tail in boilerplate, and the same warnings
  bleed into the plugin's own test runs. Filter the known-benign
  lines out of the boot log before the failure report, and suppress
  DeprecationWarnings in the plugin's test output, so a failing
  unit's report opens with the signal.
- **Skip CodeQL for the test harness (2026-09-13)**: the
  simulator and harness code (tests/harness/**, the driver) is test
  infrastructure, not shipped code — CodeQL findings there are noise.
  Scope the analyze job's path filters to the shipped tree.
- **The docs cleanup (2026-09-14)**: every verbatim quote and
  attribution removed from the repo text — the roadmap, the testing
  docs, the instructions, the skill's capture step and the code
  comments — decisions restated impersonally.

The Information pane, the visible-interactions rule and the higher-
resolution harness moved to 4.1.0 (2026-09-14) — the pane's deep
coverage is a 4.1.0 round, and its companions ride with it.

### The original 4.0.1 — Printer resilience and console polish

FOLDED INTO 4.0.0 (ruling, 2026-09-11) — every item below shipped in
4.0.0. This list is now empty history.

- ~~**Restart arming**~~ — SHIPPED IN 4.0.0.
- ~~**Webcam watchdog**~~ — SHIPPED IN 4.0.0 (the bridged-stream
  restart with the veil; direct URLs remain out of scope).
- ~~**ETA feed-forward**~~ — STRUCK (2026-09-11): Klipper
  already feeds the live speed factor back (`gcode_move.speed_factor`)
  and the follower's ETA math already scales the slicer's per-layer
  times by it — there is no printer-side remaining-time signal to
  prefer.
- ~~**Auto-improve-ETA opt-in**~~ — SHIPPED IN 4.0.0.
- ~~**Scroll-to-prompt**~~ — SHIPPED IN 4.0.0.
- ~~**Pause-list verified-pause-only**~~ — SHIPPED IN 4.0.0.
- ~~**Poll-cadence sliders (2026-09-11)**~~ — SHIPPED IN
  4.0.0: the cadence sliders (status update, auxiliary, console) with
  the 250 ms floor landed as part of the websocket work.

## 4.0.2 — Transfer and print-identity correctness — SHIPPED (2026-09-14)

Proposed from the 2026-09-14 architecture review (record in
`review/4.0.2/`), shaped by the three-persona panel round (reports in
`review/4.0.2/round-2-*.md`). Five lifecycle repairs, each shipped
with its deterministic regression — ahead of the 4.1.0 test round so
the lifecycle-failure scenarios have fixed machinery to exercise.

- **F01/F02 — download operation ownership and per-attempt accounting
  (one commit).** Each download becomes a `DownloadOperation` in
  `DownloadStream.py` (the declared owner of bounded streaming
  downloads): operation-local queue, target, cancellation state, byte
  count and worker handle — the worker closure holds them and never
  re-reads a service attribute, so a stale worker can neither steal
  the next operation's sentinel (today: a permanent GUI-thread
  `join()` hang) nor interleave writes into its file (today: silent
  corruption that passes the byte-count check). Cancellation always
  signals the operation's own queue; the writer exits via a Qt
  signal — no GUI-thread join anywhere, and the writer owns the fd
  close. Buffering is bounded by high/low water marks: above the high
  mark the drain stops reading (bytes stay in Qt's buffer, throttling
  the socket) and a writer-emitted low-water signal resumes. The
  per-attempt byte counter is an operation field — progress and the
  2 GiB cap reset per transfer/retry, lifetime telemetry stays a
  service counter — and the response's Content-Length is the sole
  transfer-length authority for the cap, the progress denominator and
  the final check (the metadata-cache size is advisory: a stale value
  can refuse a good file forever).
- **F03 — one-off download lifetime.** A download captures (machine
  id, session generation, transport identity, relpath, load intent)
  at request time — the `UploadController` precedent — and cancels
  through an unconditional entry point keyed to `sessionInvalidated`
  (`bind`/`close` are no-ops while idle-browsing, exactly when
  one-shots run). `FollowerRuntime` closes `file_download` before
  `files`, so the temp root is never deleted under a live stream, and
  no terminal result arrives after the owner is closed. Cura's
  `fileCompleted` is NOT a terminal signal (six silent-return paths,
  the no-printer window included): the load lease is preflighted
  against the reachable refusals, a bounded watchdog releases it and
  clears `loading`, and `close()` releases rather than drops.
  Failures surface through a facade-relayed signal into the
  file-manager's existing note surface (no QML change); replies
  dispose on every path; exactly-once holds through constructor
  failure; the one-shot lane writes through the operation's writer,
  not the GUI thread.
- **F04 — local upload cleanup.** One disposal routine on every
  terminal path — success, failure, cancellation, stale completion —
  with the clear-before-abort ordering already documented for the
  Preview path (`abort()` can emit completion synchronously).
  Per-operation identity (operation-id keys, never the relpath); a
  second upload of the same name while one runs is REFUSED with a
  verdict. Terminal verdicts read the response body — Moonraker can
  answer 201 with `print_started: false`, and `print_queued` is a
  distinct outcome — and both upload paths extract the server's
  refusal words. Upstream residue recorded as known: aborted uploads
  leave a temp file on the printer and same-second uploads share one
  temp path — no client route cleans these.
- **F05 — print metadata identity.** Identity keys on
  `(size, modified)` — `filename` is an echo of the request and
  `uuid` is a fresh random per extraction, so `stable_key()`'s
  premise is fixed in the same pass. The per-print key is the
  metadata row's `job_id`, cross-checked against
  `server/history/list` (HTTP GET only — the websocket
  `history:history_changed` notification is the forbidden
  temptation; a null job id means "no job identity"; rowids can be
  reused). Request identity commits only when the send starts (a
  dropped send must not leave the latch satisfied forever); the
  latch keys on a completed fetch for `(filename, job)`; the
  throttle is job-keyed so same-name restarts stay valid; an
  in-flight flag stops stacking; a failed query can never serve the
  previous job's values (they feed the pause scheduler through
  `layer_height`).

Acceptance and test shapes: the review's five journeys (gated
slow-writer cancellation followed immediately by a new download;
sequential transfers and retries under an injected small cap; late
completion after a printer switch; repeated upload cleanup; print-A
metadata then a failing print-B query with a successful retry) — each
as a pure-deterministic regression: gated writers, ghost replies,
injected clocks, reply doubles whose `abort()` synchronously emits
`finished`. No sleeps; none wait for the 4.1.0 lifecycle machinery.
The fakes and simulator gain the real protocol shapes first
(Content-Length, the metadata 404, the identity fields) so the
regressions exercise real semantics. Structural pins retarget in the
same commits (the per-class policy in `review/DECISIONS.md`);
ARCHITECTURE §1/4/6/9/11 move with the code they describe.

## 4.1.0 — Deep harness coverage

**Scope (2026-09-14):** the remainder of the UI-driving suite's
mandate, now the harness itself has shipped in 4.0.0 — deeper
coverage of the functional surface — extended by the round-1 critic's
dispositions (the plan's premises corrected against the tree), the
architecture review's 4.1.0 findings (F06/F08/F09, the lifecycle
scenarios) and the parallel local matrix. Small product fixes land
only as findings-driven patches (see the rulings). Eight workstreams,
ordered by dependency:

- **The higher-resolution harness — one display geometry.** The
  suite moves to 1920x1080 GLOBALLY: the Xvfb screen (spawned by
  `ui_test.sh`'s geometry env at run time — the harness image
  carries no geometry of its own), the capture SIZE, the window pin
  and the driver's `resize` default move together, and a screen-fits
  assertion fails the run if the window cannot render on the screen
  (the round-1 catch: the pin only asks the window its own size, so
  a 1920x1080 window on a 1600x1000 screen passes silently while
  the Information and controls panes are never rendered).
  Calibration discipline: a per-scenario window-size assertion
  pre-step (no geometry leaking between scenarios inside a group's
  shared boot); the margin-symmetry pin is re-specified per pane
  state, not per resolution; the narrow/wide resize steps stay and
  are re-measured — 1920x1080 is the baseline and capture geometry
  only; one artifact-size measurement at the new geometry before
  the first gate run.
- **The visible-interactions rule and its machinery.** The rule
  needs capabilities that do not exist yet, so the capability list
  is a phase-0 deliverable with one end-to-end proof scenario
  before any conversion is promised: a scroll verb, a
  viewport-containment assertion, non-emit activation for
  label-layer buttons (the custom-button quirk), and walks that
  reach popup layers and separate windows. The FM confirm dialogs
  are probed as a click landing on a named verb INSIDE a Popup,
  verified by the driver's delivery introspection — a naming pass
  alone does not make them clickable. The canonical drag primitive
  is the explicit-buttons QMouseEvent path (`CONSOLE_RESIZE_CODE`
  is the working proof); `exec_console_resize` is wired or deleted;
  the consoleResize exclusion is re-probed in the same pass. The
  Esc ladder is probed with a popup open at the new geometry. The
  endstop readout reclassifies here — it lives in the scrolled
  controls pane, not the Information pane. Evidence classification
  (the review's F08): scenarios are labelled UI interaction /
  application integration / diagnostic probe / explicit exclusion,
  and each records its target, delivered input, peer-side effect
  and rendered outcome; the 34 `exec_slot` + 15 `exec_file_slot` +
  6 `emit_click` + 2 `click_text` + 4 inline emit steps convert to
  real input over the release, the critical user journeys first.
- **The Information pane round.** The chart mini widgets and their
  shared pop-over, the bed-mesh mini map and the endstop readout —
  the first beneficiary of the higher resolution. Chart assertions
  are model/peer-side series assertions plus captures (no "canvas
  is in the census" outcome): the simulator gains a
  temperature-history exposure, and a latch-precondition capture
  (infoPanel expanded at w=240) is pinned before any scenario
  promises the pane.
- **The deferred panel scenarios.** The disabled-while-printing
  family (enumerated with a stated rationale, and any patch found
  ships with its red-run evidence — pre-declared), the FM confirm
  dialogs, the temperature popover, the slider drags, the settings
  dialog, the pause timed_out arm. Budget policy comes AFTER
  measured per-group wall clock from the existing galleries; target
  counts and group assignment are fixed before budgets (a timeout
  must read as a budget overrun, not a product failure). Phase-0
  probes for timed_out and the settings dialog before promising
  their scenarios.
- **The coverage checklist enforced.** The gate becomes execution,
  not membership: an execution check (each mapped surface observed
  in a run's evidence), a widened extractor for unnamed pane items
  (`MonitorPopOver.qml`, `TemperatureChart.qml`,
  `CollapsibleSectionHeader.qml`, the configuration dialog — the
  round's own subject matter), an exclusion schema (reason,
  evidence, date, re-check trigger — the two stale entries are
  rewritten through it), and `report()` wired into the gate log or
  deleted. `REAL_SAFE_SLOTS` gets a pin.
- **TESTING.md reconciliation.** The document describes the real
  harness before the gate cites it — every unimplemented claim is
  implemented or struck (the pre-flight refusal, the
  overlay-coverage assertion, the ≤12-verb pin vs the real 45, the
  watch/recorder/filmstrip, the manifests, the flake policy). The
  round-6 deferral is re-ruled (2026-09-14): 4.1.0 makes the
  document's promise a gate, so the reconciliation moves in.
- **The parallel local matrix.** `-j N` parallelism in
  `tools/harness_release.sh` — opt-in, serial by default, the
  timing budgets documented as unloaded-machine assumptions.
  Rehearsed in the container before it is called fixed (the
  workflow-change rule).
- **The architecture review's 4.1.0 fold-ins.** The F06 measured
  projection repair (one cached file-view result recomputed on
  data/view/history revision with deliberate date-filter expiry;
  ~400-file baselines; a temperature tick or console append must
  not sort the file list — the structural completion rides the 4.3
  refactor); the reusable lifecycle-failure scenarios (injected
  barriers/clocks over the review's eight verification priorities —
  cancelled-and-replaced writers, independent transfer accounting,
  post-switch one-shots, upload disposal cycles, metadata failure
  after success); F09's pin discipline (behavioral tests replace
  comment/expression pins in the touched domains; the genuinely
  architectural pins keep their structural form); and the harness's
  own maintenance as 4.1 changes it (named step handlers, a
  validated scenario schema, observation probes separated from
  action injection).

**Phase-0 rulings (2026-09-14):**

- **The FM confirm dialogs are an addressability gap, not a
  visibility one.** The dialogs are plain popups inside the walked
  window (the dimmer in the dumps proves the walk ran while they
  were open); the verbs inside carry no objectNames, and the
  custom-button label quirk defeats text clicks — nothing was
  clickable. The fix is a naming pass on the dialogs' verbs PLUS
  the click machinery above — probed end-to-end before any scenario
  is promised.
- **Disabled-while-printing findings are patched small in 4.1.x as
  found** — two guards disagreeing about the same state is a bug
  today; the permissions consolidation itself stays 4.2.0. The
  family is enumerated with a stated rationale, and every patch
  ships with its red-run evidence.
- **The panel is the three-persona composition again** (architect,
  engineer, expert automated tester) — product/UX/security have
  little to adjudicate on scenario coverage; the pro-user persona
  feeds 4.2.0 planning only.
- **The phase-2 rulings (2026-09-14, the author):** the architecture
  review's re-sequencing adopted fully (the presentation refactor
  becomes 4.3.0, the physical head moves to 4.5.0); the TESTING.md
  reconciliation moves into 4.1.0 (re-ruling the round-6 deferral);
  draft PR #19 closes as superseded with nothing of the review lost
  and every finding dispositioned; 1920x1080 everywhere.

**Round-2 panel outcomes (2026-09-14, the full dispositions in
`review/DECISIONS.md`):** the plan survives the panel with four
re-shapes — the evidence spine (below), workstream 2 as machinery,
budgets as hang detectors, and TESTING.md as strike-and-mark.

- **The evidence spine (new workstream, built first).** The doubled
  evidence path — shipped in 4.0.2: fourteen green gate jobs, zero
  galleries uploaded — is fixed with tests (`94af08f`: one shared
  resolver for both sides, an EVIDENCE MISSING gate check). On top:
  the per-run per-step evidence record (op, target, resolved item,
  delivered event + accepted, peer effect, rendered outcome) and
  `shot()` refusing a bad capture. Every later workstream consumes
  it.
- **Workstream 2 is machinery, not conversion.** New driver verbs
  (key injection, popup walks, generalized delivery introspection);
  the coordinate path opens for clicked-bearing items and
  degradation gates the verdict; the four-part proof scenario (real
  delivery recorded; an overlay fails it; a broken binding fails
  it); the re-censused ~81 steps; `exec_code` closed as a hiding
  place; the real-mode allowlist ratchets (deny-by-default for
  mutating targets). F08's label is the minimum over a scenario's
  steps, computed from the evidence record, with state-setup and
  expected-red classes.
- **Workstream 1 geometry: one constant, pinned DPI.** The DPI is
  pinned (`-dpi`) and `screenScaleFactor` asserted unchanged; the
  window pins smaller than the screen; the screen-fits check lives
  in `shot()`/`ensure_ready()`; the per-scenario pre-step is a
  suite default. The calibration surface is banked: twelve numeric
  assertions and six resize steps.
- **Workstream 4 budgets are hang detectors.** Measured: the CI
  gate phase ran ~4m24s and the longest unit's run was 63s against
  a 900s budget — 14x headroom. A unit timeout reports HANG (exit
  124) distinct from a red; per-unit duration lines land in the
  unit log; the revisit happens after the conversion.
- **Workstream 3 chart evidence: push transcript + ticker.** The
  simulator records every pushed `notify_status_update` (frame,
  timestamp, topic, payload digest) and gains an autonomous
  temperature ticker; series assertions read the model against the
  peer transcript; captures are human-review evidence, never the
  assertion.
- **Workstream 6 TESTING.md: strike-and-mark.** ~40 claims
  reconciled — kept, implemented, or struck WITH status visible; a
  named citation set is what the gate may cite; INFRA/PRODUCT tags
  and quarantine are implemented (they serve workstreams 4/5), not
  struck; a doc-pin test stops the drift.
- **Workstream 7 `-j N`: the de-singletonization.** Per-slot port,
  display, token file, run dir and container; pkill scoped to the
  invocation's process group; refcounted teardown; serial default
  keeps the same-container debris proof; CI never uses `-j`.
- **Workstream 8 fold-ins.** F06's assertion is the deterministic
  call count (zero recomputes on an unrelated revision), latency
  recorded not gated, with the simulator files arm, revision
  counters and one injected-clock seam; the lifecycle set is built
  cheapest-first with priority 3 IN (the rebind + second machine
  record — the ruling); the runner/driver dispatch table moves the
  op-existence guard in the same commit.
- **Acceptance (the exit evidence).** Every mapped surface observed
  in a run's evidence record; every critical journey real input
  with red-run proof; the hang verdict distinct; the doc-pin green;
  all gates green with zero warnings; the measured performance
  comparison delivered (the serial-vs-parallel matrix timings and
  the F06 before/after numbers). The PR stops at ready — the
  merge, tag and release wait for the author's snapshot test, and
  the snapshot report names what to test.

**Reconciliation (2026-09-15, the phase-6 re-review + the adversarial
round):** the three personas re-verified the finished build against
their round-2 findings and the fresh critic attacked it; every
verdict and the fix record live in `review/DECISIONS.md`. The fixes
that landed: the serial gate's scratch-tree wipe (the default path
deleted `/tmp/mpf` itself); the slot boot wait reading the shared
tree's stale port file; capture errors now gate the verdict; the
evidence record's provenance fields now carry the real Cura and
plugin versions; the scenario rollup ignores the shared probe steps;
the delivery record names the item, not the class alone; the upload
lane in the simulator is live (the route prefix stripped it into
dead code — f2 now asserts both the honest success and the honest
refusal); the F06 cache invalidates on bind/unbind and on the
delete/rename publishes; the coverage gate gained its execution
half (every mapped surface's scenario must run in the evidence, and
every mapped control must be addressed by a step) with the
structured exclusion schema; the red-run and overlay-refusal proofs
landed as probe scenarios (z14/z15); the jog pad and the home row
press every button; the ratchet re-counted with exec_code and
confirm_box inside the direct set; TESTING.md's second pass struck
the surviving false claims (the handshake, the taxonomy, the
transcript replay, the profile budgets, the red-gallery claims, the
letter catalogue, the 1600x1000 diagram) and the doc pin covers
them. Recorded deferrals, with their evidence: the Information-pane
round, the deferred panel scenarios, the lifecycle priorities 5/7
(the simulator arms exist; priority 3 lives in the deterministic
suite), the popup chrome and cancel verbs, the emit-family
conversion. The performance record, measured on the final tree: 1056 s serial
against 415 s parallel (2.5x — the earlier 945 s/532 s were the
pre-reconciliation baselines), the per-unit wall clocks landing in
every unit log, and the F06 before/after on the 400-file listing (9→1 pipeline evaluations per publish, 90→0 per ten ticks,
4.86→1.57 ms cold / 4.76→0.025 ms warm publish latency).

### The suite's origin (the 4.0.0 record, kept for history)

**Pulled into scope (2026-09-11):** the 4.0.0 release is frozen on
this suite — no fakery: screenshots of what the tests actually do are
the PROOF that the implemented things work as claimed. Cura 5.13 is
the pinned version, with a swap path for other versions.
Non-negotiables: the REAL Cura
application, the REAL UI, REAL clicks, and screenshots as the proof
artifact; the only fake in the system is the network peer (a full
Moonraker simulator over websocket — Moonraker is what the PRINTER
runs, not what Cura runs). The design lives in `TESTING.md` and goes
to a three-persona panel — architect, engineer, and an expert
automated tester (the composition, with explicit go-ahead).

**The release gate (the final 4.0.0 live-test round,
2026-09-11):** the suite must click through, with screenshots:

- the failure state must clear without a manual reconnect (item 2:
  the error persists and requires a reconnect);
- the preview card must stay through the load AND after the render
  settles, without any interaction (item 4: the card only comes back
  after a load);
- M117 messages must reach the Print-job section;
- the earlier still-open items: the camera's first load without a
  refresh click, temperatures arriving within seconds of print start,
  controls unlocking after an out-of-range failure, and dwell
  (logging dwells by hand was declined — the harness captures it).

**Mandate expansion (2026-09-11):** the suite covers ALL of the
plugin's functionality end-to-end where feasible — not just the
regression list — connecting to a dummy simulated printer over the
same transport a real Moonraker printer uses. The catalogue in
`TESTING.md` is two layers: the release gates, then the full
functional surface — the suite (transport, status, temperatures,
console, camera, files, controls, preview, settings, soaks).
Phasing (2026-09-11): the gates first — start with the current
recent failures to prove the theory and the process — then the full
surface, so nothing needs re-testing by hand.

**Status (2026-09-13):** the suite SHIPPED, pulled into 4.0.0 — the
gates, all 13 groups, the smoke set, the simulator's real Moonraker
semantics, and the evidence layer (per-unit galleries, videos,
CI upload). 4.1.0's remainder is the deeper coverage, not the harness
itself:

- the deferred panel scenarios: the disabled-while-printing family,
  the FM confirm dialogs, the temperature popover, the slider drags,
  the settings dialog, the pause timed_out arm;
- the Information pane (also listed in the 4.0.1 fast-follow — it
  belongs here as the deep-coverage round);
- the visible-interactions rule and the higher-resolution harness
  (confirmed 2026-09-14 — the 4.0.1 fast-follow did not absorb them).

## 4.2.0 — State, permissions & operation boundaries

- **The motion cluster (planned 2026-09-15, round-2 rulings
  applied):** three new readout rows join Position in the Monitor
  card grid — Velocity, Accel limit, Flow rate — all from the
  polled snapshot, value-only rows reading 0.00 at idle (Klipper
  always sends the fields; "—" only when motion_report is absent),
  same no-reflow rule as the filament rows. The shipped multiplier
  row is renamed "Speed factor" (the author's ruling) so the new
  live row can take "Velocity" — four personas caught the
  duplicate-title collision independently. Verified against the
  live Voron mid-print (read-only queries, 2026-09-15):
  `motion_report.live_velocity` is a single scalar (20.0 mm/s
  observed) and no per-axis velocity exists, so the row is scalar
  (the author's ruling); Klipper publishes no instantaneous
  acceleration at all, so the accel row is the effective
  `toolhead.max_accel` limit (5000.0 observed). Row titles:
  Velocity (the value is an unsigned magnitude) and Accel limit
  (the value is the configured ceiling) — the titles say what the
  values are.
  Flow rate = `live_extruder_velocity` (scalar mm/s of filament) ×
  the filament cross-section π·(d/2)², the sign preserved so a
  retraction reads negative — a negative reading is correct
  behaviour, not a fault. Verified live mid-print: a retraction
  sampled −23.6 mm/s extruder velocity (≈ −56.8 mm³/s at 1.75 mm).
  It is the COMMANDED value — pressure advance is applied after
  the trapq and is not in this number — and the tooltip says so.
  Mid-travel the trapq serves a stale history value (the last E
  segment's terminal velocity), so the row can read nonzero while
  nothing extrudes; the ruling is to show it and let the live test
  decide whether travel-zeroing is worth its machinery. The
  staleness window is up to 30 s of print time after the last
  extrusion (MOVE_HISTORY_EXPIRE), not just the travel move. The
  travel-zeroing discriminator, if the live test demands it, is
  `live_position[3]` deltas (the extruder's E position from the
  same trapq — `motion_report.steppers` carries NAMES, not
  positions); flow magnitudes below the DISPLAY threshold (0.05
  mm³/s) clamp to zero so the %.1f format never renders "-0.0"
  (the re-review: a 1e-9 clamp sat far below where the rounding
  problem starts). Cadence:
  Velocity and Flow ride the core poll, Accel limit the aux poll —
  accepted, the limit only changes via SET_VELOCITY_LIMIT. The
  existing "Flow" row (extrude factor %) keeps its name and row;
  the volumetric value is a new key, not a takeover.
- **Filament diameter (2026-09-15, round-1 corrected):** no new
  lane — the plugin already fetches the whole configfile object on
  the discovery lane (every 30 s), and Moonraker itself prunes
  `config`/`settings` from its cache as "never change and can be
  quite large"; the read reuses what is already resident. NOTE:
  the websocket SUBSCRIPTION can never carry settings — the
  discovery query is the diameter's only supplier, and a printer
  whose discovery never lands reads "—" on the Flow row. Read the
  TYPED path: `settings.extruder.filament_diameter` is already a
  float with defaults applied (the `config` path is the raw
  string; settings reflects only options Klipper actually READ —
  filament_diameter is read unconditionally, so it is always
  present once the object arrives). Per-tool: every `[extruder*]`
  section is read and the ACTIVE tool's diameter used, so mixed
  1.75/2.85 machines read correctly per tool.
  `filament_diameter` is a REQUIRED Klipper option, so "not set"
  is unreachable; the reachable degradation is "—" (no numeric
  fallback — guessing 1.75 would contradict the no-override
  ruling). No manual override
  (the author's ruling): a wrong reading is a wrong printer.cfg,
  and an override would hide a config error that also breaks
  Klipper's own volumetric features. The harness simulator's
  configfile fixture (`simulator.py:97`, no config key) is
  extended FIRST so the auto-read has an honest test.
- **Permission rewiring order (2026-09-15, round-1 corrected):**
  the ledger and the fixture lead the build — the exact-set module
  list and ownership map, the exact persisted JSON, the exact
  `enabled:` strings, the hand-mapped `can*` surfaces, the frozen
  WHATS_NEW digest and the committed screenshots move in the same
  commits as the change (the pins rule). Then the visible controls
  (jog/extrude/position), then the restart and power locks — every
  control lands in this release working. New `can*` keys match no
  prefix rule and each hard-fails the surface coverage until
  mapped; the policy module, the state store and the print-start
  owner join the exact-set module list and ARCHITECTURE.md's
  ownership map when they land.
- **The Post-Processing button's vertical alignment (validated
  out, 2026-09-13):** the one-card refactor settled this — the card
  now lives inside Cura's own saveButton row between the `</>` button
  and the Slice panel, and the harness's v1 scenario asserts the
  no-overlap alignment directly. No longer a backlog item.

**State & permissions consolidation (2026-09-10):**
the question every guard answers — can this button be pressed while
printing, not homed, paused, or e-stopped — has grown scattered: the
toolhead gate, the model's publish projections, the QML's `enabled:`
bindings, the power locks and the restart guards each derive the same
states ad hoc. Not a Klipper problem — the plugin already polls the
full state (`print_stats.state`, `homed_axes`, `gcode_move`) and
Klipper/Moonraker have NO permission endpoint to query, so the table
has to live plugin-side. The fix: ONE pure policy module projecting
the snapshot into named permissions (can_jog, can_extrude,
can_restart, can_power, can_start_print, …) with the rulings as the
table — every consumer reads the same derivation, and the scattered
conditionals collapse into a single tested file. The e-stop
assumption stays special: the ONE case where the plugin must NOT
trust the last poll — it lives at the client's observation layer
(emitted status reads as cancelled until the printer says otherwise)
and is documented there.

**The policy input contract and the rulings (2026-09-15, round-1):**
the observation record carries everything its consumers read —
`data.active`, `connected` (three-valued: unknown/yes/no),
`controlsLocked`, `commands.busy`, `print_stats.state`,
`homed_axes`, `locked_while_printing`,
`configfile.save_config_pending` — or the one derivation is
unachievable. Rulings: unknown/disconnected → FAIL CLOSED
(disabled with a reason — the author's ruling; today two shipped
places read unknown as idle and are masked only by the QML section
gate: `can_toggle` and the restart guard's second clause);
not-homed → ALLOWED for jog and print-start (the shipped
decisions, now explicit rows); observed error → allowed (the
shipped toolhead ruling — ARCHITECTURE.md's "unknown/error"
wording is corrected to match); revalidation is PER ACTION —
some actions re-check the click-time predicate at dispatch,
others a different one (the pause-first jog deliberately
dispatches work that failed the click-time gate). `can_jog`
covers the jog/extrude block this release — no control
distinguishes them today, so no split. Each disabled state
carries its reason (F10).

**The architecture review's additions (2026-09-14):**

- **One action-availability owner (F10).** The consolidated policy
  projection becomes the review's shape: named action decisions each
  carrying a concise disabled reason, consumed by BOTH view models
  and command owners, revalidated when queued work is actually
  dispatched — a valid click can become invalid before execution.
  Unknown observations stay distinguishable from an observed idle
  state. The documented product decisions (disconnected-controls,
  e-stop behaviour, unrestricted console intent, the no-reflow
  rule) are preserved — changing policy is a separate decision from
  centralising it.
- **Typed operation boundaries and an explicit state store (F11).**
  Small `typing.Protocol` interfaces for the seams being changed
  (transport, printer session, file operations, index queries, Cura
  loading), checked by review — no type checker runs in any gate
  today, and adding one is a release-workflow change for a later
  version. The Monitor model's persistence moves into an explicit
  state-store owner with migration tests and rate-limited failure
  reporting through the Monitor model's status channel (once per
  session per failure class) — a selection that fails to survive
  restart must be explainable, and the swallowed persistence
  exceptions stop being silent. The owner created here is the one
  4.3.0's UI-state store consumes — one store, two releases'
  features, no second file.
- **A print-start operation owner** — pending/confirmed/failed
  print-start as one owned operation (the review's ownership
  table), consumed by every start path. Metadata-only requests
  are identity-neutral — neither set nor clear the parsed file
  identity (the 4.0.2 hazard: a failing metadata request
  overwriting the download path's identity); the model watchdog
  stays the print-start confirmer, never the HTTP result.
  4.2.0 ships: the identity-neutral metadata-only capability
  (RemoteFileService.request_metadata_only) and the dispatch-time
  gate (the policy's can_start_print at confirm). The OWNER
  extraction (queued outcome, the publish-driven tick, the
  watchdog move) and the upload path's adoption of it defer to
  4.3.0 — the upload path already reads its own queued verdict
  from its POST body, so the false-watchdog case only exists once
  both paths share the owner.
- **Consolidated metadata ownership** — one metadata service
  supporting metadata-only requests, consumed by the coordinator
  and the file flow (F05's follow-up; the coordinator stops
  implementing its own request/cache lifecycle). 4.2.0 ships the
  service-side capability; the coordinator's adoption of it (its
  own mr-metadata cache retires) defers to 4.3.0 — it rewires the
  preview's ETA/filament anchors, the riskiest area, and rides the
  same release as the print-start owner.

**Round-2 panel rulings (2026-09-15, all author-confirmed):**

- **Titles:** the live row is "Velocity" (Klipper's field name;
  the unit rides the value) and the shipped multiplier row is
  renamed "Speed factor" — pins, README and screenshots move with
  the rename. The Flow row keeps its name for now (flagged for the
  snapshot round — "Flow factor" is the symmetric candidate).
- **Placement and reasons (the UX adjudication, accepted as
  written):** the three rows land as one block after Position —
  Velocity, Flow rate, Accel limit; disabled reasons render in the
  existing per-section Status row (short form, ~30 chars) with
  tooltips as enrichment only — a dead pane must still say why.
  The reason copy is pinned in the same pass; the Flow rate
  tooltip names the diameter and the active tool.
- **Dispatch scope absorbed (the security audit's four):** the
  queued command lane revalidates at dispatch (all three restarts
  ride it — a clicked restart can otherwise fire up to 30 s later
  against a print another client started); print-start is gated at
  dispatch, not at dialog-open (the button is ungated today and
  FileManager bypasses the central gate); explicit per-action rows
  for G90/G91 and babystepping (SET_GCODE_OFFSET); the autonomous
  pause dispatch re-checks the print is still printable.
- **Policy shape:** pure functions over a frozen observation
  record that carries `assumed_stopped` (the e-stop rewrite the
  architecture blocker found — the policy must see the assumption,
  not just the rewritten state); the three-valued `connected`
  (unknown/yes/no) is built as a PREREQUISITE in the client layer
  — today it is a bool at every seam and unknown is
  unrepresentable; decisions carry a mode ("pause-first" is not a
  denial) and per-power-device rows; reason strings are
  module-level constants (value_property caches on value
  identity); `jogEnabled` stays as the published property, a
  projection of `can_jog` — ten shipped sites negate a two-valued
  print_active and fail open on unknown, so the derivation shape
  is a positive allow-list per permission, not site fixes.
- **Harness:** the `enabled:` substring pins are REPLACED by the
  already-built-but-unused `item_disabled` step (F09's rule), not
  retargeted; the simulator becomes field-faithful (it currently
  ignores object sets and field lists — the `settings`-survives-
  the-merge claim is untestable otherwise) and its fixtures gain
  the fields the rows read plus `toolhead.extruder`; the
  direct-invocation ratchet is saturated at 101/101 — any
  exec_code-style step needs a deliberate ceiling raise with a
  DECISIONS entry.
- **Klipper citation paths corrected** (klippy/configfile.py,
  klippy/webhooks.py, klippy/kinematics/extruder.py,
  klippy/chelper/kin_extruder.c — not klippy/extras/*).

## 4.3.0 — Monitor & file-manager presentation refactor

SHIPPED 2026-09-16 (branch release/v4.3.0, awaiting the author's
snapshot nod): the strip, the pause/resume policy rows, the
print-start owner, the metadata adoption, the UI-state store, the
caption states, the slider track-click fix, the harness evidence
layer, and the full per-section extraction (22 components). The
phase-6 re-review and a fresh adversarial round dispositioned in
`review/DECISIONS.md`; their blockers are fixed (the refocus walk's
cross-document ids, the misplaced System/MCUs instantiations, the
zero-width headers, the spacer gates, the pause_resume lane and
capability fail-closed, the metadata mismatch no-latch, the strip's
three absent states and the unlabelled temps pair). The strip's
staleness witness (a test that stops the feed) rides the
pre-release harness run.

The 2026-09-14 re-sequencing inserted this release: the presentation
debt gets a bounded delivery of its own instead of compounding under
the physical-head feature. The review's F07 plus F06's structural
completion, with fixed component scope and measurable exit criteria —
not a repository-wide redesign. The 2026-09-15 scoping ruling keeps
4.5.0 separate and folds every 4.2.0 deferral plus the round-2
pro-user planning input (2026-09-15) into this release. The round-1
critic (2026-09-15) corrected three false premises; the seven-persona
panel (2026-09-15) settled the strip's shape, lane and budget, and
the off-path readout was dropped by ruling. Every disposition is in
the decisions ledger.

- **Preview status strip (the pro-user's top want, ruled in
  2026-09-15).** Two fixed rows after the card's title, directly
  above the existing status row (re-ruled 2026-09-15 from the
  card's first row, so the two status lines read as one block):
  row 1 is one full-width control — "Pause print" / "Resume
  print" (the action word always visible; the policy's reason
  detail in the tooltip; the Detach/Attach tooltip reworded to
  read against a card that does pause); row 2 is the temps cell
  ("Hotend 205/210 °C · Bed 60/60 °C" — the Monitor's own labels
  and form) and a permanent middle slot whose text changes: the
  print ETA while a gate passes, the policy's refusal reason
  whenever one refuses. 61 px total; every cell explicitly
  width-bound — an implicit-width row paints past the card edge
  (the load-indicator precedent). Status-only (the 2026-09-15
  ruling): the factor sliders, the z-offset nudges and the
  extrude/retract controls stay in the Monitor — the Preview
  control dock is a 4.5.0 planning item with the jog pad. On the
  card, the strip rides its dual-host placement for free — no
  third host surface (re-ruled 2026-09-15 from a separate strip).
  If the control dock lands, the strip breaks out to its own
  surface then — the card rows are the simple form (the
  2026-09-15 ruling), built as a property-driven component with
  no card-internal state so the 4.5.0 host change is a re-home,
  not a rewrite. Snapshot-0 mock before wiring.

  The panel's rulings (2026-09-15, dispositions in the ledger):
  - The lane. The rows are `can_pause`/`can_resume` with reason
    constants — pause/resume is the last un-migrated command gate
    (two inline booleans today, no reasons). Both rows read
    `pause_resume.is_paused` where available (the object joins the
    queried set — the state proxy alone ships a live Resume on a
    print that can never resume) and consume `R_ESTOPPED`: the
    state table's assumed-stopped row reads "Stop issued — state
    unconfirmed" and disables both actions BY the assumption — the
    first consumer of the e-stop record. The mode mapping is
    allowed / disabled+reason, never pause-first. Busy is a term
    of the row ("A command is running", never a dead button). The
    lane re-derives from a fresh observation at dispatch (never
    the cached property), denies on a missing observation, reports
    the policy's words on every refusal, and the refusal lands on
    the middle slot — the strip's refusal surface. A "resume
    aborted" reply settles immediately as "nothing to resume" —
    never the 300 s window. The Dashboard's buttons migrate onto
    the rows and gain the reason words; the print-job caption
    reads a state property, not the permission boolean.
  - The seam. A slim per-poll Preview value block published by the
    aux accumulator (MonitorData's own 2.5 s clock), carried to
    the coordinator through a new read-only edge created at the
    output-device boundary — the only place both halves exist.
    The block carries the arrival stamp; the strip renders "—"
    per value when the feed is absent, stale, or the monitor is
    inactive (the three absent causes: poll failure, printer
    switch, never configured). Absence is an explicitly published
    sentinel — the sticky publish dict never removes a key, and a
    re-stamped block never goes stale. The block is generic (a
    "Preview value block per poll with a stated staleness rule")
    so the 4.5.0 camera thumbnail and marker readouts reuse it.
  - The temps. Reuse `chart_temperature_objects` — never a fresh
    classification (hotend and bed are "system" objects; a
    temperature-filter misses them). target 0.0 means no
    setpoint/off — the arrow is omitted; a 0.0 reading on a
    heater with no target renders "—" (Klipper's own
    not-measured convention); the fixed pair is hotend + bed —
    additional extruders stay pane-only in 4.3.0; per-heater
    staleness is not observable — the staleness rule is about the
    feed, and the plan says so.
  - The ETA. The middle slot carries the PRINT remaining/finish
    (`layer_eta`, already computed by the coordinator, new over
    the seam). The existing selected-layer slot stays untouched:
    its string is atomic, shows no time at all while following,
    and moving it asked for a value that does not exist on this
    host.
  - The state table. idle / printing / paused / disconnected /
    busy (printer command vs card load — two unrelated busies,
    both named) / error (download-index vs Klipper's, both named)
    / locked / assumed-stopped. One vocabulary, written once —
    the strip, the card's status row and the print-job caption
    all read from it.
  - The signals. `printPauseRequested` / `printResumeRequested`
    (never `pauseClicked` — that means Attach/Detach today); the
    presentation's `pauseRequested` renames to
    `pauseAtLayerRequested` in the same pass — three pause-named
    signals on one object is one too many.
  - The budget. A UX budget, not a harness one: the v1 assertion
    binds horizontally (22 px of X clearance to the save row) and
    there are 750 px of verified vertical headroom. The real
    constraint is the bottom-anchored card's overflow over Cura's
    viewport — the budget states its headroom rule (what happens
    when the 4.5.0 dock is added), not just a maximum.
- **Operation extraction — two slices, separately revertible
  (split after the round-1 critic).** Slice one: the print-start
  owner extraction completes — the queued outcome, the
  publish-driven tick and the watchdog move out of the model, and
  the upload path adopts the shared owner. Deterministic,
  unit-testable, no user-visible change; its own evidence. Slice
  two: the coordinator's metadata adoption. Before it, the
  consolidated service's contract is written down — fetch plus
  retained payload plus latch semantics plus job-identity
  discipline. The coordinator's mr-metadata cache is not a plain
  cache: its latch retired a ~1,200-identical-request retry ladder
  and its job-id cross-check against the newest
  `server/history/list` row prevents the wrong-job readout;
  adopting the bare fetch as-is reintroduces both. The contract
  keeps them; explicit unit tests (fake fetch, no latch on
  failure) move with the code. The rewrite of the preview's
  ETA/filament anchors rides this slice — the 4.2.0 record's
  riskiest area, kept apart from slice one so its regression
  signal stays clean.
- **The UI-state store (the 4.2.0 owner's second consumer).** New
  module `UiStateStore.py` owns section sizes, collapse state and
  their persisted schema, consuming the 4.2.0 `StateStore` — one
  store, two releases' features, no second file. The 4.2.0
  constraints carry over: top-level-only merge (named in
  ARCHITECTURE), the credential class stays out. The shape
  requirement is written down: the Monitor's save payload rewrites
  nine whole top-level keys per save, so the UI state lives as
  top-level siblings of those nine — never nested inside `sections`
  or `toolhead` — and the sizes sibling map is a tenth top-level
  key with the same protection argument. Collapse state stays
  booleans under the existing keys — a shape change or a renamed
  id silently re-expands every existing user's sections (the
  pro-user's reset vector, PUF2). The section-id pin is corrected
  and lands first (F11): six ids are already pinned by name; the
  pin becomes a literal set plus a length assertion so the console
  pane — whose id is not a `sectionId:` literal — cannot be
  missed.
- **View-model extraction.** A FilesViewModel owning the file
  projection as an INTERNAL collaborator: the model keeps the
  public property names (pinned by name AND by base class) and
  `fileManagerRows` stays a list-valued projection — the harness
  reads it as a value and the file-list delegates consume dict
  rows. The genuinely new part is the QAbstractListModel with
  stable row identities behind that surface; the revision-keyed
  cache and the date-filter expiry already ship (the F06
  `projection_count`-pinned cache). Then the focused view models
  where their update lifetimes differ: controls, console/camera,
  information/chart presentation. Each receives explicit model
  properties and emits intents — never the whole root object. The
  coverage gate's surface extractor reads one hard-coded file: it
  becomes a directory scan in the same commit as any
  `value_property` move, so a moved declaration cannot vanish from
  the matrix.
- **QML component extraction.** EVERY collapsible section becomes
  its own property-driven QML component (the author's ruling,
  2026-09-15: each section within a pane is its own file, not a
  huge block in the monitor QML) — the controls pane's twelve
  sections, the Information/Printer-status panes' nine, the console
  pane, the camera pane, the file table, the filter controls and
  the file confirmation dialogs. The panes become thin composition
  shells over the per-section components; the section-id and
  sectionIcon literals move WITH their components (the pinned-set
  test widens to scan the component files). Its required companion is the
  pin-retargeting pass, re-derived at the pass start with the
  counting method stated (the round-1 critic's counts: 339
  positive / 52 negative source-constant assertions, or 265
  `assertIn` sites / 406 executed assertions / 43 `assertNotIn`
  against QML text; the 27 `.index(` sites reproduce). Three
  families, three treatments: the string assertions retarget once
  the module-level source constants are widened (they read whole
  files); the count pins are arithmetic over a file's text and are
  re-derived against the new file set — a moved section otherwise
  reads as a lost section; the `.index()` anchors fail hard
  (ValueError) and are replaced with named structural markers that
  survive a move. The negative guards are the dangerous half —
  they pass vacuously once the code moves — and must follow the
  code, never deleted; the pass names how each guard follows. The
  pass runs AFTER `qmlformat` (the pre-commit gate formats staged
  QML); the qmldir entry and the two banned filenames are on the
  checklist.
- **Harness evidence visibility (bumped from 4.2.0, the author's
  2026-09-15 ruling).** The suite's inspections scroll their target
  into view before asserting, and the evidence captures show what
  the step actually saw — a recording that proves an inspection
  against an off-screen item proves nothing a user could do.
  Implemented as a new op or an opt-in driver flag, not a silent
  change to existing inspections: the scroll walk resolves its
  target through a different window set (visibility-filtered at
  depth 64 vs the click walk's unfiltered depth 96) and it mutates
  `contentY` — the four inspection ops sit in `REAL_SAFE_OPS`, the
  tuple between a scenario and a live printer, and it moves with
  the change in the same commit. The evidence entry gains the
  geometry field so the capture genuinely shows what the step saw.
- **The print-job caption's disconnected and locked states.** The
  premise is corrected (F18): the caption reads "Idle" while the
  socket is down — the surface that names disconnected today is
  `monitorState`. The caption gains both states, folded in with the
  strip's status vocabulary so one ruling covers both.
- **Exit criteria.** Object names and public surfaces used by the
  interaction tests are preserved; persistence migration and
  gesture ownership survive; resize, focus, Esc handling,
  confirm/cancel, printer switches and repeated slider grabs are
  re-verified after each extraction; NO REGRESSION against the
  4.1.0 baselines — the old wording asked to improve on numbers
  already banked post-improvement — via `projection_count` and the
  publish-latency probe; unchanged package identity. The
  no-oscillation invariant becomes testable: the three pane-width
  constants, the two thresholds and the release margin are pinned
  as unit-testable values BEFORE any pane slice moves — a pin
  change and the hysteresis re-derivation land in the same commit.
  The pin-retargeting pass costed and complete. Per-item criteria:
  the strip's is the state table, the signals, the content budget,
  the staleness witness (a test that stops the feed) and its own
  lane scenario (the refusal text via a model read and the
  absences via assert_model). Each extraction is a vertical slice
  — move
  one owner, redirect its callers, preserve observable behaviour,
  remove the obsolete path in the same change. A lines-per-file
  target is not an acceptance criterion; the test is that a feature
  change stays within its component.

## 4.4.0 — Configurable sections

A candidate from the author (2026-09-15): show/hide and re-order the
Monitor's collapsible sections — possibly whole panes — the way the
file manager's columns already work. The machinery it builds on is
mostly shipped or landing now: the 23 pinned section ids (4.3.0's
exact-set pin — any rename, re-order or visibility rule must respect
it), the collapse map as the persistence precedent, the file
manager's column UI (visibility + drag re-order) as the interaction
precedent, and the UI-state store's merge-write path as the persistence
home. Unplanned until the 4.5.0 physical-head marker ships.

## 4.5.0 — Physical head in the Preview (moved from 4.3.0)

What a web dashboard cannot do: show the real machine inside the slice.
The 4.3.0 presentation refactor lands first (the 2026-09-14
re-sequencing): the marker's interactive controls depend on the
common action policy and session ownership from 4.2.0, and its
lifecycle boundaries are proven by the 4.0.2 repairs and the 4.1.0
scenarios. The layer-hardening/foreign-heights pack becomes an
explicit gate for the resolver/coordinate work, scoped to the
behaviour the marker relies on; continuous-Z/vase support stays a
distinct capability and is not a requirement for every preceding
maintenance release. If feature value demands, the marker's
display-only slice may proceed after 4.3.0 while the presentation
refactor finishes.

- A live physical-position marker overlaid on the Preview scene. The
  position source is `motion_report.live_position` plus
  `gcode_move.homing_origin`/`axis_map` — already implemented as
  `live_position_in_gcode_space` (`MoonrakerProtocol.py`); the old
  "`absolute_position`" text named a field that does not exist. The hard
  cases are pause-park, start-gcode parking and homing, not jogging.
  Pro-user panel corrections (2026-09-10, verified against the tree):
  the marker must NEVER vanish by print state — solid while printing,
  dimmed idle, ghosted parked, one permanent line saying why; a drop
  line to the plate plus a readout; visually unmistakable from Cura's
  own nozzle (crosshair, not a dot); the same display-only motion
  smoothing the path already has (750 ms polls teleport a fast head);
  hidden only when unhomed or motors-off, where the frozen position
  lies. The extrusion guard answers "which layer", not "where the
  head" — drop it. Klipper reports the ACTIVE nozzle, but on
  single-nozzle machines that changes nothing — the real multi-extruder
  feature is an active-tool label and per-tool path colouring.
- A floating jog pad in the Preview panel, so the head can be moved while
  looking at the actual toolpath. The Preview control dock lands with
  it: the factor sliders ruled out of the 4.3.0 strip (2026-09-15)
  find their Preview home here; z-offset and extrude/retract stay
  Monitor-side unless 4.5.0's planning re-rules them. When the dock
  lands, the status strip breaks out of the card to its own surface
  (the 2026-09-15 ruling).
- A shared coordinate-transform module — corrected by the pro-user
  panel: the bed-mesh overlay does NOT use homing_origin/axis_map (its
  XY comes from Cura's build volume, its Z is a lifted deviation
  surface — `BedMeshSceneNode`). The marker needs two stages
  (machine→gcode, then gcode→Cura scene); the genuinely shareable
  piece is the scene-space mapping — name that, not the homing_origin
  transform.
- Macro surfacing: the macro LIST already ships (from
  `printer/objects/list`); only `description` is genuinely new.
  Typed parameter fields also already ship — `infer_macro_parameters`
  plus the Dashboard's typed inputs (README:255); "parameters cannot
  be introspected" is wrong and must not regress them. The gate is
  reframed (pro-user input): deny while actively printing; allow while
  PAUSED with a confirmation — the paused window is exactly the
  filament-swap/PARK moment. Today `run_macro` refuses in both states
  and macros fire immediately with no confirmation.

**Pro-user panel input (2026-09-10, planning only):** value ranking —
the marker is the must-have; the jog pad becomes a 1 only with Preview
pause/resume beside it; macro surfacing is a 2. Missing items ranked:
(1) a Preview status strip with pause/resume + temps + ETA — moved to
4.3.0 (2026-09-15); (2) the already-computed off-path distance in
plain words (`refined_fraction` is thrown away today — "Head is
12.4 mm off the toolpath"; commanded-not-sensed caveat: it cannot
detect skipped steps) — moved to 4.3.0, then dropped there
(2026-09-15); the marker may earn it back as a deviation callout
beside the actual head; (3) pause-at-Z-
height + one-click pause-at-next-layer + the layer-to-mm readout
(`PhysicalLayer.height` has no consumer); (4) filament/colour-change
waypoints (M600, `; filament change`) on the layer timeline with
time-to-go; (5) a camera thumbnail in the Preview (investigate, don't
assume); (6) active-tool label + per-extruder path colouring.
The ranking is stale and is re-derived at 4.5.0's planning round
(PUF4, 2026-09-15): pause-at-layer and the layer-to-mm readout
already ship, item (1) moved to 4.3.0 and item (2) was dropped
there — only pause-at-Z-height of the original three remains open.
Cross-cutting: the layer-hardening pack is the marker's prerequisite
(the marker inherits the resolver's numbers, and the `;LAYER:` flip
without the gate can increase wrong-layer risk); the version-drift
gate's user
value is its presentation (permanent disabled-with-reason states); the
deuteranopia cheap 80% is fixing the default red/green pair. Out-of-
scope revisit: folder trees — confirm before 3.6.0 ships that files in
subfolders are at least listable and printable, or the file manager is
weaker than Mainsail's for anyone with a library.

**Live-test fixes (2026-09-11, the snapshot round):**

- The author's live report: dragging the preview's layer-height slider
  no longer detaches the follower (the path progress bar still does),
  and the whitespace gap between the bed-mesh and pause buttons turned
  out to be the current-layer info label, which never fills while
  attached.
- Label: `update_eta` showed "current print layer" only while
  DETACHED, so the slot stayed blank while attached. It now fills in
  both states — the slot is the current-layer info label while
  attached; the ETA/already-printed text is unchanged. The QML slot
  also collapses when there is genuinely nothing to say (idle
  printer), instead of leaving a blank gap.
- Detach: `detect_override` passed silently whenever the follower was
  unarmed (view swap, dropped connection, absorbed echo) — a drag
  landing in that window was ignored until an observe happened to
  re-arm. It now adopts the view's position as the baseline, so the
  next deviation — a continuing drag — detaches. `reset_print` also
  preserves the armed view baseline — the print stopping does not
  move Cura's view, and wiping the baseline on every inactive
  observation left the window between observations permanently
  unarmed.
- **The missing Monitor cards (a live report): a
  subscription deadlock.** In websocket mode the auxiliary wanted set
  only reached the socket after the first aux fragment arrived — and
  Moonraker only pushes SUBSCRIBED objects, so the first fragment
  never came: temperatures, fans and sensors vanished (HTTP mode was
  unaffected — switching back to HTTP restored the cards). The wanted
  set now reaches the socket the moment the object list is known. The
  RPC lane itself was live-proven against the printer (3/3
  replies on all seven monitor methods, zero errors) before the
  deadlock was found.
- **The cadence controls are real sliders now** (the ruling
  and correction): aux + console are linear 250–60000 ms sliders with
  a 250 ms floor and live value labels; the status update interval is
  a log-spaced slider (250 ms to ~34 min, each step doubling) that
  never rewrites an untouched stored value.
- **The floating controls left of the preview card:** Cura's
  ActionPanelWidget row centres its components on the row's centre
  line, and our card's full height made the row centre far above the
  bottom — Cura's own Post Processing button floated there. The
  extension root now reports a short strip with the visible card
  anchored to its bottom (overflowing upward), so the row stays small
  and every component docks to the bottom line together.

**More live-test fixes (2026-09-11, the second round):**

- **Aux data that never changes never arrived:** Moonraker's subscribe
  response carries the full state ONCE, then pushes only CHANGES. The
  sync fed the core snapshot only, so a steady temperature never
  reached the Monitor's aux snapshot ("hitting Reconnect brought it
  all back" — the resubscribe re-sent the sync). The socket now seeds
  the aux accumulator from every subscribe response, so the next
  drain publishes unchanged objects too.
- **New objects join mid-print (the rule):** the aux merge
  accepted only names from the FIRST objects/list, so a device
  switched on mid-print was dropped even when its data arrived. The
  merge now accepts newly-seen names, and the subscription grows to
  include them.
- **The detach watchdog snapped back mid-drag:** the 3 s re-attach
  timer restarted only on a NEW detach — while detached it fired
  mid-inspection and re-attached under the user's pointer. The quiet
  window now restarts on every view movement while detached, so the
  re-attach comes 3 s after the user actually stops moving the view.
- **The preview's two card versions flipped during Cura's own
  busy/idle cycles:** the empty "Load current print" card gated on
  `!CuraApplication.platformActivity`, so it appeared as the "wrong"
  card whenever Cura flipped its activity flag (which also hides
  Cura's layer controls). The empty card now gates on toolpath/load
  state — it means "nothing is loaded", nothing else.
- **Test-connection 401 wording:** an auth gateway's HTML 401 body
  (the proxy) surfaced as Qt's raw error string. Non-JSON
  401s now read "the API key was rejected (HTTP 401)", matching the
  websocket path.
- **The connected state names the live transport** ("Moonraker
  connected over websocket" / "… over HTTP polling") — the
  ask for confidence that the websocket is genuinely in use.
- **The slow-drag detach delay (the third report):** the
  echo window refreshed on every unarmed→armed re-arm, and an absorbed
  drag deviation unarmed the follower — each observe then re-armed the
  window, absorbing a slow drag for the window's whole 3.5 s. The
  window now arms ONLY at attach(); a drag at any other moment
  detaches immediately. The re-attach watchdog also cancels outright
  on the first post-detach view movement — a continued drag is
  inspection, never a restoration echo.
- **The camera bridge lands (the 4.0.0 ruling):** `CameraBridge`
  fetches the configured stream WITH the X-Api-Key header and
  republishes it on an ephemeral keyless loopback port for Cura's
  loader; `MonitorCamera` rewrites the camera URL through the bridge
  whenever a key is set and the stream host is remote. Loopback-only
  listener, header-buffer cap, per-connection upstreams.


## 4.0.0 notes — WebSockets as a transport swap (panel history)

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
  faster than the 2.5 s aux poll: the socket buys event edges and lower
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

- **Gcode deformation (the vertex-style bed-mesh warp)** — permanently
  dropped (the author's ruling, 2026-09-15): the feasibility was
  never proven — the plugin has no vertex-level reach into Cura's
  layer geometry, and the 4.2.0 per-layer prototype did not produce
  the look — and the value does not repay the machinery.
- **The off-path distance readout** — dropped at planning (the
  author's ruling, 2026-09-15): the signal is mostly normal motion
  (travels, Z-hops and parks exceed the window by design), the
  anomaly cases are not visible through the commanded position,
  and the surviving value did not repay a five-owner plumbing
  change. A deviation callout beside the 4.5.0 physical-head
  marker is the one shape that might earn it back — with an actual
  head rendered, the number points at something on screen (the
  author, 2026-09-15).
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
  source; the 2.5 s aux poll (the 2026-09-11 step-down from 1 s)
sets the chart's shipped resolution.
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

## 3.6.0 candidate notes (settled 2026-09-10)

- **Console resize** — ruled in, see above.
- **Scroll-to-prompt after a send** — ruled IN for 3.6.0: after sending
  a command while scrolled up, the console returns to the prompt.
- **Pro-user persona round** — superseded: the pro-user persona is the
  permanent seventh panel member from this release (feature value for
  the NEXT release, not a gate on this one).
- **Pause-list semantics** — ruled 2026-09-10, verified-pause-only: an
  entry leaves the list only when the printer is OBSERVED paused at that
  layer. The verification window is 10 s (the existing command-track
  timeout); on timeout the entry stays, restyled missed/failed,
  dismissible by click, and all entries clear at print end. NO automatic
  PAUSE retry — a retry could double a command that was merely slow.
